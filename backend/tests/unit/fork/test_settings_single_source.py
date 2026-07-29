"""One source of truth for every setting, and no shipped secret.

Three claims this file enforces mechanically, because each has already been
false at some point and nothing said so:

  1. A default is declared in ONE place. When compose repeated a default that
     config.py also declared, compose won silently - and two of the sixteen
     disagreed. `HYBRID_FABRIC_USER` was `true` in code and `false` in compose,
     so a fresh install seeded a Microsoft Fabric agent and then left the
     Fabric connector out of the catalogue.

  2. No credential has a working default. The database password fell back to
     the literal string `bowpassword`, published in this repository, on a port
     mapped to the host.

  3. start.sh refuses rather than invents. It used to generate a throwaway
     encryption key and warn, which destroyed stored credentials one restart
     at a time.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "backend" / "app" / "settings" / "config.py"
START = REPO / "start.sh"
ENV_EXAMPLE = REPO / ".env.example"
COMPOSE = [REPO / "docker-compose.yaml", REPO / "docker-compose.dev.yaml"]

# `- NAME=${NAME:-value}` in a compose environment list
COMPOSE_DEFAULT_RE = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]+)=\$\{\1:-([^}]*)\}\s*$", re.M)
# `os.environ.get("NAME", "value")`, which config.py wraps across lines
CODE_DEFAULT_RE = re.compile(r'os\.environ\.get\(\s*"([A-Z][A-Z0-9_]+)",\s*"([^"]*)"', re.S)

# Plumbing that legitimately lives in compose: these describe how the
# containers are wired to each other, not how the application behaves, and
# config.py declares no default for any of them.
COMPOSE_OWNED = {"DASH_ENCRYPTION_KEY", "DASH_LICENSE_KEY", "DOMAIN", "POSTGRES_PASSWORD"}


def _code_defaults() -> dict:
    return {
        name: value.strip().lower()
        for name, value in CODE_DEFAULT_RE.findall(CONFIG.read_text(encoding="utf-8"))
    }


def _compose_defaults(path: Path) -> dict:
    return {
        name: value.strip().lower()
        for name, value in COMPOSE_DEFAULT_RE.findall(path.read_text(encoding="utf-8"))
    }


# ---------------------------------------------------------------------------
# 1. one source of truth
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_compose_never_repeats_a_default_the_code_already_declares(path):
    """★The failure this prevents is silent in BOTH directions: agreeing
    duplicates drift apart later, and disagreeing ones make the code's default
    dead without anything reporting it."""
    code = _code_defaults()
    duplicated = sorted(set(_compose_defaults(path)) & set(code) - COMPOSE_OWNED)
    assert duplicated == [], (
        f"{path.name} repeats defaults that config.py already owns: {duplicated}. "
        "Delete them from compose - env_file already carries operator overrides."
    )


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_every_compose_variable_is_either_code_owned_or_plumbing(path):
    """Catches a NEW variable being added to compose with its own default,
    which is how the situation above arose in the first place."""
    code = _code_defaults()
    unknown = sorted(set(_compose_defaults(path)) - set(code) - COMPOSE_OWNED)
    assert unknown == [], (
        f"{path.name} defines {unknown} with a compose-side default. Either give "
        f"it a default in config.py, or add it to COMPOSE_OWNED with a reason."
    )


def test_the_code_actually_declares_defaults():
    """Guard the guard. Every assertion above passes vacuously if the regex
    stops matching - a formatting change to config.py would do it."""
    assert len(_code_defaults()) > 15


# ---------------------------------------------------------------------------
# 2. no credential has a working default
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_no_compose_file_ships_a_default_password(path):
    text = path.read_text(encoding="utf-8")
    # Comments explaining the removal are fine; a live substitution is not.
    live = [
        line for line in text.splitlines()
        if "bowpassword" in line and not line.strip().startswith("#")
    ]
    assert live == [], f"{path.name} still ships a default password: {live}"


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_secrets_have_no_fallback_value(path):
    """`${POSTGRES_PASSWORD:-something}` is the shape that shipped the default.
    Empty (`:-`) is fine: postgres then refuses to initialise and says why."""
    text = path.read_text(encoding="utf-8")
    for secret in ("POSTGRES_PASSWORD", "DASH_ENCRYPTION_KEY"):
        for match in re.finditer(rf"\$\{{{secret}:-([^}}]*)\}}", text):
            assert match.group(1) == "", (
                f"{path.name}: {secret} falls back to {match.group(1)!r}"
            )


def test_env_example_leaves_both_secrets_blank():
    """A filled-in example is a shipped credential, whatever the file is called."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for secret in ("DASH_ENCRYPTION_KEY", "POSTGRES_PASSWORD"):
        line = next(
            (l for l in text.splitlines() if l.startswith(f"{secret}=")), None
        )
        assert line is not None, f"{secret} missing from .env.example"
        assert line == f"{secret}=", f".env.example ships a value for {secret}"


# ---------------------------------------------------------------------------
# 3. start.sh refuses, and never invents
# ---------------------------------------------------------------------------
def test_start_script_does_not_generate_an_encryption_key():
    """★The single most destructive line this project has had. It generated a
    Fernet key when none was supplied, kept it in memory, and warned - so every
    restart orphaned the previous run's encrypted data with no error."""
    text = START.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "Fernet.generate_key" not in code, (
        "start.sh generates an encryption key again - it must refuse instead"
    )


def test_start_script_exits_when_the_key_is_missing():
    text = START.read_text(encoding="utf-8")
    assert 'if [ -z "$DASH_ENCRYPTION_KEY" ]; then' in text
    assert "fail_missing" in text
    assert "exit 1" in text


def test_start_script_validates_the_key_it_was_given():
    """A truncated key is worse than a missing one: the app starts and every
    decrypt fails later, one feature at a time."""
    assert "Fernet(os.environ['DASH_ENCRYPTION_KEY']" in START.read_text(encoding="utf-8")


def test_start_script_rejects_the_old_shipped_password():
    text = START.read_text(encoding="utf-8")
    assert "*:bowpassword@*" in text, (
        "start.sh no longer catches an install that copied the old default password"
    )


def test_the_refusal_messages_say_what_to_do():
    """A refusal that does not name the fix is just an outage."""
    text = START.read_text(encoding="utf-8")
    assert "openssl rand -base64 32" in text
    assert "openssl rand -base64 24" in text


# ---------------------------------------------------------------------------
# 4. .env reaches the application
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_compose_passes_the_env_file_through(path):
    """Without this, a key the operator sets in .env is silently ignored - which
    was true of the model provider, email, Entra sign-on and the AWS database
    options, all of which the code reads and none of which compose forwarded."""
    text = path.read_text(encoding="utf-8")
    assert "env_file:" in text, f"{path.name} does not pass .env through"
    assert "required: false" in text, (
        f"{path.name} would fail `docker compose down` on a host without .env"
    )
