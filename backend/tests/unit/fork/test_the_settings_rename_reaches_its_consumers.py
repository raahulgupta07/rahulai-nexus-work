"""The `bow_config` → `dash_config` rename must hold in the code that USES it.

Commit `8531b382` renamed the settings attribute along with the `BOW_*` env
vars. It renamed the file that DEFINES the attribute. It missed three files that
read it, and they stayed missed for three releases:

    app/data_sources/fast/artifacts.py:39        Fernet(settings.bow_config.encryption_key)
    app/ee/oidc/google_profile_service.py:90     settings.bow_config.google_oauth
    app/services/automation_alerts.py:143        getattr(settings.bow_config, "base_url", …)

There is no `bow_config` attribute any more, so the first two raise
``AttributeError: 'Production' object has no attribute 'bow_config'`` the moment
they run — fast-path artifact encryption and Google profile sync both dead.

★The third is the instructive one. It sat inside ``try: … except Exception:
pass``, so it did not raise: it silently fell back to ``http://localhost:3000``
and every automation-failure alert went out carrying a link to localhost. A
feature that looks like it works is how a rename survives three releases.

``test_env_name_compat`` and ``test_install_defaults_agree`` already guard the
env-var and config-file halves of that commit. This is the third half — the
Python attribute — and its absence is why the miss was found by a red test in a
one-hour full-suite run rather than at the point of the rename.

★Scans SOURCE, not the settings module. `env_compat.py` and `start.sh`
deliberately still resolve both spellings so an existing install can be upgraded;
that compat layer is not a second supported spelling for new code to reach for.
"""
from __future__ import annotations

import re
from pathlib import Path

# fork → unit → tests → backend. The sibling fork tests use parents[4] for the
# repo root; this one stops one level short because it only reads Python source.
BACKEND = Path(__file__).resolve().parents[3]
APP = BACKEND / "app"

# `settings.bow_config`, `_settings.bow_config`, `cfg.bow_config` — any attribute
# access, not just the one spelling that happened to be wrong here.
_ATTR = re.compile(r"\b\w*settings\s*\.\s*bow_config\b|\.\s*bow_config\b")

# The compat layer's whole job is to answer to both names while an existing
# install is upgraded. Excluded by path so the exemption is visible rather than
# silently absorbed by a cleverer regex.
_ALLOWED = {
    APP / "settings" / "env_compat.py",
}


def _python_sources():
    for path in sorted(APP.rglob("*.py")):
        # `.bak-*` snapshots are frozen copies of old work, kept on purpose.
        if ".bak-" in path.name or "__pycache__" in path.parts:
            continue
        if path in _ALLOWED:
            continue
        yield path


def test_no_source_file_reads_the_old_settings_attribute():
    """★The guard that should have shipped with `8531b382`.

    A single hit is a live `AttributeError` waiting for the code path to run —
    or, worse, a silent fallback that makes the failure look like a feature.
    """
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if _ATTR.search(line):
                offenders.append(f"{path.relative_to(BACKEND)}:{n}  {line.strip()}")

    assert not offenders, (
        "`settings.bow_config` no longer exists — it is `settings.dash_config` "
        "(renamed in 8531b382). Every line below raises AttributeError when its "
        "code path runs, unless it is wrapped in a bare `except`, in which case "
        "it fails silently instead:\n  " + "\n  ".join(offenders)
    )


def test_the_settings_object_answers_to_the_new_name_only():
    """The other direction: `dash_config` resolves, `bow_config` does not.

    Without this the scan above could pass while the attribute itself had been
    renamed back, or aliased — either of which would make the scan meaningless.
    """
    from app.settings.config import settings

    assert hasattr(settings, "dash_config")
    assert not hasattr(settings, "bow_config"), (
        "re-adding a `bow_config` alias would make the scan above pass while "
        "leaving two spellings of one attribute in the codebase — which is the "
        "state the rename existed to end"
    )


def test_the_comments_do_not_teach_the_old_name():
    """Docstrings and comments are where the next person looks for the idiom.

    Two of them named `settings.bow_config.encryption_key` as the thing that
    "already backs SMTP and external-platform secrets" — an instruction to use a
    name that raises.
    """
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if "bow_config" in line:
                offenders.append(f"{path.relative_to(BACKEND)}:{n}  {line.strip()}")

    assert not offenders, (
        "these mention `bow_config`, which no longer exists:\n  "
        + "\n  ".join(offenders)
    )
