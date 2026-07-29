"""The health report must be honest, safe, and unable to crash.

Three properties matter more than any individual check:

  1. It never raises. A health report that crashes fails at exactly the moment
     somebody is trying to find out what is wrong.
  2. It never carries a secret. It is printed to `docker logs` and served
     unauthenticated at /health/detail.
  3. /health stays a pure liveness probe. Docker's healthcheck and kubernetes
     restart on it, so a degraded-but-running server reporting unhealthy there
     turns "no model provider yet" into a restart loop.
"""
import asyncio
import re
from pathlib import Path

import pytest

from app.core import health_report as hr

REPO = Path(__file__).resolve().parents[4]
MAIN = REPO / "backend" / "main.py"


# ---------------------------------------------------------------------------
# encryption key: the one check that touches a secret
# ---------------------------------------------------------------------------
def test_a_missing_key_fails_with_the_command_to_generate_one(monkeypatch):
    monkeypatch.delenv("DASH_ENCRYPTION_KEY", raising=False)
    c = hr._check_encryption_key()
    assert c.state == "fail"
    assert "openssl rand -base64 32" in c.fix


def test_a_malformed_key_fails(monkeypatch):
    """★A truncated key is worse than a missing one: the app starts and every
    decrypt fails later, one feature at a time."""
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "too-short")
    assert hr._check_encryption_key().state == "fail"


def test_a_valid_key_passes(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("DASH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert hr._check_encryption_key().state == "ok"


@pytest.mark.parametrize(
    "value", ["", "too-short", None],
    ids=["missing", "malformed", "valid"],
)
def test_the_key_is_never_echoed(monkeypatch, value):
    """★The whole report is printed to docker logs and served unauthenticated.
    Not the key, not a prefix of it, not a hash — a fingerprint is still a
    fingerprint, and it is never needed to answer 'is a key present'."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode() if value is None else value
    if key:
        monkeypatch.setenv("DASH_ENCRYPTION_KEY", key)
    else:
        monkeypatch.delenv("DASH_ENCRYPTION_KEY", raising=False)

    blob = str(hr._check_encryption_key().as_dict())
    if key:
        assert key not in blob
        assert key[:8] not in blob


# ---------------------------------------------------------------------------
# debug: the fault this release exists to close
# ---------------------------------------------------------------------------
def test_debug_on_in_production_is_reported(monkeypatch):
    from app.settings.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEBUG", True)
    c = hr._check_debug()
    assert c.state == "warn"
    assert "traceback" in c.detail


def test_debug_off_in_production_is_fine(monkeypatch):
    from app.settings.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEBUG", False)
    assert hr._check_debug().state == "ok"


def test_debug_on_in_development_is_fine(monkeypatch):
    from app.settings.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DEBUG", True)
    assert hr._check_debug().state == "ok"


# ---------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------
def test_a_missing_frontend_build_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVE_FRONTEND", "1")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path))
    c = hr._check_frontend()
    assert c.state == "fail"
    assert str(tmp_path) in c.detail


def test_a_present_frontend_build_passes(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv("SERVE_FRONTEND", "1")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path))
    assert hr._check_frontend().state == "ok"


def test_a_separately_served_frontend_is_not_a_fault(monkeypatch):
    monkeypatch.delenv("SERVE_FRONTEND", raising=False)
    assert hr._check_frontend().state == "ok"


# ---------------------------------------------------------------------------
# the report as a whole
# ---------------------------------------------------------------------------
def test_a_database_that_cannot_be_reached_reports_rather_than_raises(monkeypatch):
    """★Every check swallows its own exception. Proven by making the session
    maker itself explode, which is the harshest realistic failure."""
    import app.dependencies as deps

    def boom(*a, **k):
        raise RuntimeError("no database today")

    monkeypatch.setattr(deps, "async_session_maker", boom)
    c = asyncio.run(hr._check_database())
    assert c.state == "fail"
    assert "no database today" not in str(c.as_dict()), (
        "raw exception text can carry a connection string - report the type only"
    )


def test_worst_state_is_the_worst_of_its_checks():
    mk = lambda s: hr.Check("k", s, "label")
    assert hr.Report([mk("ok"), mk("ok")]).worst == "ok"
    assert hr.Report([mk("ok"), mk("warn")]).worst == "warn"
    assert hr.Report([mk("warn"), mk("fail")]).worst == "fail"
    assert hr.Report([]).worst == "ok"


def test_render_shows_the_fix_only_for_problems():
    report = hr.Report([
        hr.Check("a", "ok", "all good", fix="never show me"),
        hr.Check("b", "fail", "broken", fix="do this thing"),
    ])
    out = hr.render(report, "1.2.3")
    assert "never show me" not in out
    assert "do this thing" in out
    assert "1.2.3" in out


def test_render_withholds_the_ready_line_when_something_failed():
    """Saying "you can now start using the app" over a failed check is how the
    old banner behaved, and it is worse than saying nothing."""
    report = hr.Report([hr.Check("a", "fail", "broken")])
    out = hr.render(report, "1.2.3", "http://example.test")
    assert "Ready at" not in out
    assert "needs attention" in out


# ---------------------------------------------------------------------------
# the liveness/readiness split
# ---------------------------------------------------------------------------
def test_plain_health_stays_a_dumb_liveness_probe():
    """★If /health ever starts reflecting the detailed report, docker's
    healthcheck will restart a server whose only problem is that nobody has
    pasted a model key yet."""
    src = MAIN.read_text(encoding="utf-8")
    body = re.search(
        r'@app\.get\("/health", include_in_schema=False\)\s*\nasync def health\(\):(.*?)\n@app\.',
        src, re.S,
    )
    assert body, "could not locate the /health handler"
    assert 'return {"status": "ok"}' in body.group(1)
    assert "collect" not in body.group(1), "/health must not run the health report"


def test_the_detailed_endpoint_exists():
    src = MAIN.read_text(encoding="utf-8")
    assert '@app.get("/health/detail", include_in_schema=False)' in src


def test_the_startup_banner_no_longer_prints_the_upstream_project_name():
    """★It shipped an ASCII logo spelling the upstream project's name — in a
    whitelabel, in the first thing an operator sees."""
    src = MAIN.read_text(encoding="utf-8")
    assert "\\__,_|\\__," not in src, "the upstream ASCII logo is back in the banner"


def test_the_doctor_entry_point_exists():
    doctor = REPO / "backend" / "app" / "core" / "doctor.py"
    assert doctor.exists()
    src = doctor.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src
    assert "import main" in src, (
        "doctor must import main first or the ORM registry is incomplete"
    )
