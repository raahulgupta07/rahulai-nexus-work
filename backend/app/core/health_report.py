"""What this server can and cannot do, in plain sentences.

One set of checks, read by three consumers so they can never disagree:

  * the startup banner in main.py
  * `python -m app.core.doctor`, which an operator runs in Docker
  * GET /health/detail

★Deliberately separate from GET /health. That endpoint is a LIVENESS probe —
docker's healthcheck, kubernetes and CI wait loops all restart or fail on it.
Making it report a degraded-but-working server would turn "the model provider
is not configured yet" into a container restart loop. Liveness answers "is this
process alive"; this module answers "is this server able to do its job".

Every check catches its own exceptions and reports FAIL with the reason. A
health report that can itself crash is worse than no health report — it fails
at exactly the moment somebody is trying to find out what is wrong.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

State = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    key: str
    state: State
    label: str
    detail: str = ""
    # Only set when there is something the operator can actually do.
    fix: str = ""

    def as_dict(self) -> dict:
        out = {"key": self.key, "state": self.state, "label": self.label}
        if self.detail:
            out["detail"] = self.detail
        if self.fix:
            out["fix"] = self.fix
        return out


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def worst(self) -> State:
        for state in ("fail", "warn"):
            if any(c.state == state for c in self.checks):
                return state  # type: ignore[return-value]
        return "ok"

    def as_dict(self) -> dict:
        return {"state": self.worst, "checks": [c.as_dict() for c in self.checks]}


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------
async def _check_database() -> Check:
    try:
        from sqlalchemy import text

        from app.dependencies import async_session_maker

        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        return Check("database", "ok", "database reachable")
    except Exception as e:
        return Check(
            "database", "fail", "database unreachable",
            detail=type(e).__name__,
            fix="check POSTGRES_PASSWORD in .env and that the database container is running",
        )


async def _check_schema() -> Check:
    """Compare the revision stamped in the database against the newest on disk.

    ★Reads the alembic files with ScriptDirectory rather than parsing them.
    `down_revision` is sometimes a tuple (merge migrations) and a regex over
    these files has already reported phantom heads on a tree alembic reads as
    one.
    """
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        from app.dependencies import async_session_maker

        root = Path(__file__).resolve().parents[2]
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        heads = ScriptDirectory.from_config(cfg).get_heads()

        async with async_session_maker() as db:
            result = await db.execute(text("SELECT version_num FROM alembic_version"))
            stamped = [row[0] for row in result.fetchall()]

        if len(heads) != 1:
            return Check(
                "schema", "fail", "migrations have more than one head",
                detail=", ".join(sorted(heads)),
                fix="alembic will refuse to run - the migration chain needs merging",
            )
        if stamped == list(heads):
            return Check("schema", "ok", "schema up to date", detail=heads[0])
        return Check(
            "schema", "fail", "schema is behind",
            detail=f"database at {stamped or ['nothing']}, code at {heads[0]}",
            fix="restart the app - migrations run automatically at startup",
        )
    except Exception as e:
        return Check("schema", "warn", "could not read schema version", detail=type(e).__name__)


def _check_encryption_key() -> Check:
    """★Reports only that the key is present and well-formed. The key itself,
    and any fingerprint of it, stay out of every log and response."""
    raw = os.environ.get("DASH_ENCRYPTION_KEY", "")
    if not raw:
        return Check(
            "encryption_key", "fail", "no encryption key",
            fix="set DASH_ENCRYPTION_KEY in .env - openssl rand -base64 32",
        )
    try:
        from cryptography.fernet import Fernet

        Fernet(raw.encode())
        return Check("encryption_key", "ok", "encryption key valid")
    except Exception:
        return Check(
            "encryption_key", "fail", "encryption key is not usable",
            fix="it must be 44 characters of url-safe base64",
        )


async def _check_model_provider() -> Check:
    """A server with no model can sign people in and show them an empty app.

    Warn, never fail: this is the expected state between first signup and the
    admin pasting a key, and a fresh install must not look broken.
    """
    try:
        from sqlalchemy import func, select

        from app.dependencies import async_session_maker
        from app.models.llm_provider import LLMProvider

        async with async_session_maker() as db:
            result = await db.execute(
                select(func.count()).select_from(LLMProvider).where(
                    LLMProvider.deleted_at.is_(None)
                )
            )
            count = result.scalar_one()
        if count:
            return Check("model_provider", "ok", "model provider configured",
                         detail=f"{count} configured")
        return Check(
            "model_provider", "warn", "no model provider yet",
            fix="add one in Settings after signing in - the agent cannot answer without it",
        )
    except Exception as e:
        return Check("model_provider", "warn", "could not check model provider",
                     detail=type(e).__name__)


def _check_frontend() -> Check:
    """The SPA is served from this process; a missing build is a blank site."""
    try:
        from pathlib import Path

        if os.environ.get("SERVE_FRONTEND", "").lower() not in ("1", "true", "yes"):
            return Check("frontend", "ok", "frontend served separately")
        dist = Path(os.environ.get("FRONTEND_DIST_DIR", "/app/frontend/dist"))
        if (dist / "index.html").is_file():
            return Check("frontend", "ok", "frontend build present")
        return Check(
            "frontend", "fail", "frontend build missing",
            detail=str(dist),
            fix="rebuild the image - did `nuxt generate` run?",
        )
    except Exception as e:
        return Check("frontend", "warn", "could not check frontend", detail=type(e).__name__)


def _check_debug() -> Check:
    """★Its own line because it was on in production from the day this shipped.
    A traceback returned to a browser is a real disclosure, and nothing said so."""
    from app.settings.config import settings

    if settings.ENVIRONMENT == "production" and settings.DEBUG:
        return Check(
            "debug", "warn", "debug mode is ON in production",
            detail="unhandled errors will return a traceback to the browser",
            fix="unset DEBUG in .env",
        )
    return Check("debug", "ok", f"debug off ({settings.ENVIRONMENT})")


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
async def collect() -> Report:
    checks = [_check_encryption_key()]
    checks.append(await _check_database())
    checks.append(await _check_schema())
    checks.append(await _check_model_provider())
    checks.append(_check_frontend())
    checks.append(_check_debug())
    return Report(checks)


_GLYPH = {"ok": "ok", "warn": "--", "fail": "XX"}


def render(report: Report, version: str, url: str = "") -> str:
    """Plain text, aligned, no colour. It is read in `docker logs` as often as
    in a terminal, and colour codes are noise there."""
    lines = [f"CityAgent Insights {version}", ""]
    for c in report.checks:
        line = f"  {_GLYPH[c.state]}  {c.label}"
        if c.detail:
            line += f" ({c.detail})"
        lines.append(line)
        if c.fix and c.state != "ok":
            lines.append(f"      -> {c.fix}")
    lines.append("")
    if report.worst == "fail":
        lines.append("  Something above needs attention before this server can be used.")
    elif url:
        lines.append(f"  Ready at {url}")
    return "\n".join(lines)
