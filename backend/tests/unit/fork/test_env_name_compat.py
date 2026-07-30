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


# ---------------------------------------------------------------------------
# ★★★An empty value is not a value
#
# These lock the single defect that made every test above true and the product
# still lose the key. The compatibility layer was correct; the COMPOSE FILE
# defeated it. Both compose files carried
#
#     - DASH_ENCRYPTION_KEY=${DASH_ENCRYPTION_KEY:-}
#
# so a container whose .env supplies only the OLD name received the NEW name
# set to an empty string. "" is not None, the new name was judged present, the
# old name was never consulted, and config.py generated a fresh key — the exact
# silent, permanent credential loss this module exists to prevent.
#
# Measured against the built image before the fix:
#
#     compose sets DASH_ = ''   ->  mirrored: []                  old key LOST
#     DASH_ genuinely absent    ->  mirrored: [BOW_...]           old key KEPT
#
# The compose lines are gone AND empty is treated as absent, because either one
# alone leaves the trap one edit away from returning.
# ---------------------------------------------------------------------------
def test_an_empty_new_name_does_not_shadow_a_real_old_one(monkeypatch):
    """The exact shape a pre-rename .env takes under the shipped compose file."""
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "old-value")
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "")
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") == "old-value"
    assert env_compat.is_legacy_in_use(), "falling back to the old name must still be visible"


def test_normalize_mirrors_past_an_empty_new_name(monkeypatch):
    """normalize_environment is what protects the ~292 plain os.getenv call
    sites. It skipped when the counterpart was 'set' — including set to ''."""
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "old-value")
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "")
    legacy = env_compat.normalize_environment()
    assert "BOW_ENCRYPTION_KEY" in legacy
    assert os.environ["DASH_ENCRYPTION_KEY"] == "old-value"


def test_an_empty_old_name_is_not_mirrored_onto_a_real_new_one(monkeypatch):
    """The mirror must not run backwards either — an empty old name must never
    overwrite a good new one.

    ★Unlike the three around it, this one also passed BEFORE the fix (checked
    by rerunning it against the old semantics). It is a property worth pinning
    down, not evidence of the defect — the fix made empty values invisible in
    both directions and this asserts the direction that was already safe.
    """
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "")
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "new-value")
    env_compat.normalize_environment()
    assert os.environ["DASH_ENCRYPTION_KEY"] == "new-value"
    assert env_compat.env_get("DASH_ENCRYPTION_KEY") == "new-value"


def test_both_empty_falls_through_to_the_default(monkeypatch):
    monkeypatch.setenv("BOW_ENCRYPTION_KEY", "")
    monkeypatch.setenv("DASH_ENCRYPTION_KEY", "")
    assert env_compat.env_get("DASH_ENCRYPTION_KEY", "fallback") == "fallback"


def test_compose_files_do_not_hand_out_empty_prefixed_variables():
    """★The guard that keeps the two fixes from drifting apart.

    Treating empty as absent makes the fallback survive these lines. Removing
    them means it never has to. This asserts the second, so a future edit that
    reinstates `DASH_FOO=${DASH_FOO:-}` fails here rather than on someone's
    server months later.

    DASH_DATABASE_URL is exempt: it is COMPOSED from POSTGRES_* rather than
    passed through, and is never empty when those are set.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    offenders = []
    for name in ("docker-compose.yaml", "docker-compose.dev.yaml"):
        path = root / name
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("- ") or stripped.startswith("- #"):
                continue
            m = re.match(r"- ((?:DASH|BOW)_\w+)=\$\{\1:-\}$", stripped)
            if m and m.group(1) != "DASH_DATABASE_URL":
                offenders.append(f"{name}:{n}  {stripped}")
    assert not offenders, (
        "these lines pass an EMPTY value for a variable that may be carried "
        "under its other prefix in .env:\n  " + "\n  ".join(offenders)
    )
