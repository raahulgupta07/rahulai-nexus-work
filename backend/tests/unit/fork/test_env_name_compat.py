"""The old environment names must keep working while the new ones take over.

The prefix is moving from the upstream project's initials (``BOW_``) to this
product's (``DASH_``). A straight swap would be unsafe, and uniquely so:

  ``config.py`` GENERATES a fresh encryption key when it cannot find one, with
  no error and no interruption to start-up. On an installation that already
  holds data, that silently makes every stored connector password, Microsoft
  token, directory bind and single-sign-on secret permanently unreadable, while
  the application looks perfectly healthy.

So the compatibility layer ships and is proven BEFORE any name is renamed. If
these tests pass, the worst realistic accident — new code deployed against an
old configuration file — is already known to be harmless.
"""
import os

import pytest

from app.settings import env_compat


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("DASH_", "BOW_")):
            monkeypatch.delenv(k, raising=False)
    env_compat._warned.clear()
    yield
    env_compat._warned.clear()


# ---------------------------------------------------------------------------
# The three ways a machine can be configured
# ---------------------------------------------------------------------------
def test_the_new_name_alone_works(monkeypatch):
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "new-value")
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") == "new-value"
    assert not env_compat.is_legacy_in_use(), "a fully migrated machine must not warn"


def test_the_old_name_alone_still_works(monkeypatch):
    """★The stranded-machine case. This is the one that matters: code renamed,
    configuration not. It must resolve, and it must say so."""
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "old-value")
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") == "old-value"
    assert env_compat.is_legacy_in_use()
    assert "DASH_ENCRYPTION_KEY" in env_compat.legacy_names_in_use()


def test_the_new_name_wins_when_both_are_set(monkeypatch):
    """★Precedence must be decided, not accidental — otherwise a half-migrated
    machine's behaviour depends on dictionary order."""
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "old-value")
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "new-value")
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") == "new-value"
    assert env_compat.env_get("BOW_ENCRYPTION_KEY") == "new-value", (
        "asking by the OLD name must still yield the NEW value"
    )


def test_neither_set_returns_the_default(monkeypatch):
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") is None
    assert env_compat.env_get("DASH_ENCRYPTION_KEY", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Direction, which is easy to get half-right
# ---------------------------------------------------------------------------
def test_it_resolves_in_both_directions(monkeypatch):
    """★The configuration file may still say ``${BOW_ENCRYPTION_KEY}`` while the
    environment supplies ``DASH_...``. Only handling old-name-in-env would leave
    that machine falling through to key generation."""
    monkeypatch.setenv("DASH_DATABASE_URL", "postgresql://x/y")
    assert env_compat.env_get("BOW_DATABASE_URL") == "postgresql://x/y"


def test_a_value_found_either_way_is_written_back_under_both(monkeypatch):
    """Anything later in start-up that reads os.environ directly must find it."""
    monkeypatch.setenv("BOW_LICENSE_KEY", "abc")
    env_compat.env_get("DASH_LICENSE_KEY")
    assert os.environ.get("DASH_LICENSE_KEY") == "abc"
    assert os.environ.get("BOW_LICENSE_KEY") == "abc"


def test_it_works_by_prefix_not_by_a_list(monkeypatch):
    """★58 names carry this prefix. A hand-maintained list would drift, and the
    drift would only show up as a missing value on some machine."""
    monkeypatch.setenv("BOW_SOMETHING_NOBODY_LISTED", "v")
    assert env_compat.env_get("DASH_SOMETHING_NOBODY_LISTED") == "v"


def test_an_unrelated_variable_is_untouched(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    assert env_compat.counterpart("POSTGRES_PASSWORD") is None
    assert env_compat.env_get("POSTGRES_PASSWORD") == "p"


def test_the_warning_is_emitted_once_per_name(monkeypatch, caplog):
    """A warning on every read would bury the log for a legitimately old
    machine; never warning would hide it."""
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "old")
    with caplog.at_level("WARNING"):
        for _ in range(5):
            env_compat.env_get("DASH_ENCRYPTION_KEY")
    hits = [r for r in caplog.records if "previous name" in r.message]
    assert len(hits) == 1, [r.message for r in hits]


# ---------------------------------------------------------------------------
# The generation path that makes all of this necessary
# ---------------------------------------------------------------------------
def test_generating_a_key_is_no_longer_silent():
    """★★★It cannot be removed — a genuinely new install needs it, and from
    inside that function a new install is indistinguishable from a broken one.
    So it must at least be impossible to miss in the output."""
    from pathlib import Path
    src = Path(env_compat.__file__).with_name("config.py").read_text(encoding="utf-8")
    assert "generate_fernet_key()" in src
    assert "can no longer be decrypted" in src, "the generation path must warn loudly"
    assert 'env_var_name.endswith("ENCRYPTION_KEY")' in src, (
        "the guard must match either prefix, or a renamed key falls through it"
    )


def test_the_config_loader_goes_through_the_shim():
    from pathlib import Path
    src = Path(env_compat.__file__).with_name("config.py").read_text(encoding="utf-8")
    assert "from .env_compat import env_get" in src
    assert "os.environ.get('BOW_CONFIG_PATH')" not in src, "a direct read bypasses the shim"
