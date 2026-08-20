"""The Licence screen is removed, and the licence MECHANISM is not.

WHAT THIS COST
--------------
This fork unlocked enterprise permanently: `backend/app/ee/license.py` returns a
standing grant with no key, no expiry and no external server. The Settings ▸
Licence page therefore showed a form for a thing nobody can enter, on an
installation where entering it would change nothing — and the licence-expiry
banner in `layouts/default.vue` offered a "View licence" link straight into it.
A page that answers no question is a page every admin has to read once to learn
it answers no question.

WHAT IS PINNED HERE
-------------------
  * the page file is gone, both nav entries with it, and nothing in `frontend/`
    still routes to `settings/license`
  * ★the expiry BANNER survives. Deleting the page must not take the warning
    with it — the banner is the only thing that ever told anyone a licence was
    running out, and it is rendered from `layouts/default.vue`, the same file
    the dead link is being cut from. A removal that overshoots by two lines is
    indistinguishable from a clean one until a licence expires silently.
  * ★★the licence MECHANISM is untouched. `useEnterprise` still drives the
    banner in `layouts/default.vue`, and `hasFeature` still gates other tabs in
    `layouts/settings.vue`. This is the assertion that keeps the change a PAGE
    REMOVAL rather than a FEATURE-GATE removal: without it, ripping out every
    licence-shaped identifier in both layouts would satisfy every other check
    in this file while quietly unlocking or breaking the `requiredFeature`
    tabs (`pii`) that read the same composable.

★These read files only, so they belong in `tests/unit/fork` — no schema is
touched. See CLAUDE.md.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"
PAGE = FRONTEND / "pages" / "settings" / "license.vue"
DEFAULT_LAYOUT = FRONTEND / "layouts" / "default.vue"
SETTINGS_LAYOUT = FRONTEND / "layouts" / "settings.vue"

# ★★★A source-scanning test matches its OWN comments. Every negative assertion
# below runs on comment-stripped source, because the comment explaining why the
# licence tab was removed is very likely to contain the word `license` — and a
# naive substring search cannot tell an explanation of a rule from a violation
# of it. Third-time-in-this-repo mistake; see CLAUDE.md.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _code(source):
    """The source with its prose removed: HTML comments, /* */ blocks, and
    whole-line `//` comments (both .vue script blocks and .ts use them)."""
    stripped = _BLOCK_COMMENT.sub(" ", _HTML_COMMENT.sub(" ", source))
    return "\n".join(
        line for line in stripped.splitlines() if not line.strip().startswith("//")
    )


@pytest.fixture(scope="module")
def default_layout():
    return DEFAULT_LAYOUT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def settings_layout():
    return SETTINGS_LAYOUT.read_text(encoding="utf-8")


# --- the page and its two nav entries -----------------------------------------


def test_the_licence_page_file_is_gone():
    assert not PAGE.exists(), (
        "pages/settings/license.vue is still on disk — Nuxt routes by file, so "
        "the page is still reachable at /settings/license no matter what the "
        "nav lists"
    )


def test_no_licence_tab_in_the_settings_layout(settings_layout):
    code = _code(settings_layout)
    assert not re.search(r"name:\s*['\"]license['\"]", code), (
        "the Licence tab is back in layouts/settings.vue's tab list"
    )


def test_no_licence_entry_in_the_sidebar_permission_map(default_layout):
    """`settingsTabPermissions` decides where the sidebar's Settings link deep-
    links to. An entry for a page that no longer exists sends an admin to a 404
    as the FIRST reachable tab."""
    code = _code(default_layout)
    # ★The slice boundary IS the guard. `code.index("]")` from the identifier
    # lands on the `[]` of the TYPE ANNOTATION
    # (`settingsTabPermissions: { … }[] = [`), so the window closes before the
    # array literal even opens and the check reads an empty string — green with
    # a `license` entry sitting three lines further down. Measured: that form
    # passed the mutation that puts the entry back. Open the window at the
    # assignment instead.
    block = code[code.index("settingsTabPermissions"):]
    block = block[block.index("= ["):]
    block = block[: block.index("]")]
    assert "name: 'members'" in block, (
        "the tab-list window no longer contains the tab list — this check "
        "cannot fail until the slice is repaired"
    )
    assert not re.search(r"name:\s*['\"]license['\"]", block), (
        "layouts/default.vue still lists a 'license' settings tab — the sidebar "
        "can deep-link to a page that is gone"
    )


def test_nothing_in_the_frontend_routes_to_the_licence_page():
    """★`.bak-*` files are excluded deliberately: this tree carries over a
    thousand of them, none is shipped, and letting them into the scan makes an
    honest removal impossible to prove."""
    offenders = []
    for path in FRONTEND.rglob("*"):
        if path.suffix not in (".vue", ".ts") or not path.is_file():
            continue
        parts = set(path.parts)
        if parts & {"node_modules", ".nuxt", ".output"}:
            continue
        if ".bak-" in path.name:
            continue
        if "settings/license" in _code(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(FRONTEND)))
    assert not offenders, (
        "these still navigate to the removed page: {}".format(offenders)
    )


# --- ★the warning that told anyone, and the mechanism behind it ---------------


def test_the_expiry_banner_still_warns(default_layout):
    """★The banner is the only thing that ever said a licence was expiring, and
    it lives in the same file as the link being cut. Removing the destination
    must not remove the message."""
    code = _code(default_layout)
    assert "licenseExpired" in code, (
        "the expiry banner's own state is gone from layouts/default.vue — "
        "nothing now distinguishes an expired licence from an expiring one"
    )
    assert "showLicenseBanner" in code, "the banner no longer renders at all"
    for key in (
        "settings.licensePage.banner.expired",
        "settings.licensePage.banner.expiring",
    ):
        assert key in code, (
            "banner text key {} is gone — the licence can now expire with "
            "nothing said".format(key)
        )


def test_the_dead_link_out_of_the_banner_is_gone(default_layout):
    """The banner stays; the thing it pointed at does not."""
    code = _code(default_layout)
    assert "settings.licensePage.banner.viewLicense" not in code, (
        "the 'View licence' link is back and it goes nowhere"
    )


def test_the_licence_mechanism_is_untouched(default_layout, settings_layout):
    """★★This is a PAGE removal, not a feature-gate removal.

    `useEnterprise` is what the banner reads, and `hasFeature` is what gates the
    `requiredFeature` tabs (`pii`). Both live in the two files this change
    edits, so both are one careless sweep away from going with the page — and
    every other assertion in this file passes if they do.
    """
    default_code = _code(default_layout)
    assert "useEnterprise" in default_code, (
        "layouts/default.vue no longer reads useEnterprise — the licence page "
        "removal took the expiry mechanism with it"
    )
    settings_code = _code(settings_layout)
    assert "hasFeature" in settings_code, (
        "layouts/settings.vue no longer calls hasFeature — every requiredFeature "
        "tab gate has been removed along with the licence page"
    )
    assert "requiredFeature" in settings_code, (
        "the requiredFeature gate itself is gone from the tab list"
    )
