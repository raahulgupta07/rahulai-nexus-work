"""`DEBUG` must be off in production.

`settings.DEBUG` is handed to `FastAPI(debug=...)` at main.py:133. With it on,
an unhandled exception returns a full traceback to the browser — file paths,
source lines, and whatever happened to be on the stack. The default was a
hardcoded `True`, so it was on in production from the day this shipped.

★Tested through `_debug_default()` rather than by instantiating `Settings`:
`Settings.__init__` opens `../VERSION`, loads `bow-config.yaml` and builds a
FastMail client, none of which belong in a fast unit test. The helper IS the
decision — the field default is nothing but a call to it.
"""
import os

import pytest

from app.settings.config import _debug_default


@pytest.fixture
def env(monkeypatch):
    """Give each case a clean ENVIRONMENT/DEBUG slate."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    return monkeypatch


def test_production_turns_debug_off(env):
    env.setenv("ENVIRONMENT", "production")
    assert _debug_default() is False


def test_development_leaves_debug_on(env):
    env.setenv("ENVIRONMENT", "development")
    assert _debug_default() is True


def test_staging_leaves_debug_on(env):
    """Staging is a place people debug. Only production is locked down."""
    env.setenv("ENVIRONMENT", "staging")
    assert _debug_default() is True


def test_unset_environment_leaves_debug_on(env):
    """A bare checkout with no env file is a developer's laptop, not a server."""
    assert _debug_default() is True


@pytest.mark.parametrize("raw", ["PRODUCTION", " production ", "Production"])
def test_production_is_recognised_regardless_of_case_or_padding(env, raw):
    """★A stray space or capital in a compose file must not silently re-enable
    tracebacks in production. That failure is invisible until it leaks."""
    env.setenv("ENVIRONMENT", raw)
    assert _debug_default() is False


def test_an_unknown_environment_name_does_not_read_as_production(env):
    """Fails toward debug-on for an unrecognised name, which is the honest
    default: we cannot prove it is a production box, and a developer who sees
    no traceback where they expected one loses time. Real deployments set
    ENVIRONMENT explicitly — compose pins it to `production`."""
    env.setenv("ENVIRONMENT", "prod")
    assert _debug_default() is True


def test_the_field_default_actually_calls_the_helper(env):
    """Guard the guard: every assertion above is worthless if the model field
    stopped using this function. Read the source rather than build a Settings
    instance, which would need VERSION, a config file and a mail client."""
    import inspect

    from app.settings import config as config_module

    src = inspect.getsource(config_module)
    assert "DEBUG: bool = _debug_default()" in src, (
        "Settings.DEBUG no longer derives from _debug_default() — production "
        "may be running with tracebacks exposed again"
    )


def test_compose_pins_production():
    """The whole scheme rests on compose setting ENVIRONMENT=production. If a
    future edit drops it, every deployment silently reverts to debug-on."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[4]
    for name in ("docker-compose.yaml", "docker-compose.dev.yaml"):
        path = repo / name
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        assert "ENVIRONMENT=production" in text or "ENVIRONMENT: production" in text, (
            f"{name} no longer pins ENVIRONMENT to production"
        )
