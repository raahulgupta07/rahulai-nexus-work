"""No screen may tell a person to edit `bow-config.yaml`. It does not exist.

The config file was renamed to `dash-config.yaml` in `8531b382`. Four locale
strings and two hardcoded `.vue` strings went on naming the old one, so an
administrator following the LDAP or SMTP setup hint went looking for a file
that is not in the tree. That is not a branding blemish — it is a wrong
instruction, and the reader has no way to tell it is wrong except by failing.

★★★Why this test scans BOTH `locales/` and the `.vue` sources: the first sweep
looked only at `locales/*.json`, fixed four strings there, declared the job
done — and missed two more sitting hardcoded in `settings/smtp.vue`, which are
not translated at all. They surfaced only because the built bundle was grepped
afterwards. A guard pinned to the locale files would have gone on passing while
the screen kept lying.

★The BACKEND is deliberately out of scope. `settings/config.py` names both
spellings on purpose — `env_compat` resolves an old install that still ships a
`bow-config.yaml` on disk, and that fallback is why upgrading works at all.
Renaming it there would break the very installs it exists for. The rule is
about what a screen SAYS, not what the loader accepts.
"""
import re
from pathlib import Path

import pytest

# tests/unit/fork/<this file> → repo root
REPO = Path(__file__).resolve().parents[4]

OLD = re.compile(r"bow-config")

# ★A file that no longer exists cannot be named as a place to go. `dash-config`
# is the only correct spelling in anything a person reads.
SEARCH = [
    (REPO / "locales", "*.json"),
    (REPO / "frontend" / "pages", "*.vue"),
    (REPO / "frontend" / "components", "*.vue"),
]


def _offenders():
    out = []
    for root, glob in SEARCH:
        if not root.exists():
            pytest.skip(f"{root} not present — run from a full checkout, not the image")
        for path in root.rglob(glob):
            # ★`.bak-*` snapshots are frozen history, not shipped code.
            if ".bak" in path.name or ".output" in path.parts or ".nuxt" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD.search(line):
                    out.append(f"{path.relative_to(REPO)}:{i}: {line.strip()[:110]}")
    return out


def test_no_user_facing_string_names_the_old_config_file():
    bad = _offenders()
    assert not bad, (
        "these strings name `bow-config`, which was renamed to `dash-config.yaml` "
        "and no longer exists — anyone following them looks for a missing file:\n  "
        + "\n  ".join(bad)
    )


def test_the_guard_actually_looks_at_vue_files_too():
    """★The test's own coverage, asserted.

    The defect this file exists for was two hardcoded strings in a `.vue`
    template. A future edit that trims SEARCH back to `locales/` would leave
    every assertion above passing and the screens still wrong — so the scope is
    pinned here rather than left as a comment.
    """
    roots = {root.name for root, _ in SEARCH}
    assert "locales" in roots
    assert "pages" in roots, "the .vue templates are where this was missed once already"
