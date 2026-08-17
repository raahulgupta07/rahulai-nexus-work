"""The handover feature has to be reachable, not merely written.

This fork keeps shipping features that exist and cannot be found. App Analytics
sat complete in the image for eleven releases behind an env var nobody set. The
SSO logo picker saved an `icon` that four separate consumers dropped, so the
chosen mark never reached a pixel. `useInstanceFeatures` does not auto-fetch, so
a page that destructures its state and forgets the fetcher renders nothing, with
no error anywhere.

Every one of those passed its own unit tests. What they had in common is a chain
with a missing link somewhere between "the code is correct" and "a person can
use it". So this file walks the chain for the handover:

    a route exists  ->  a composable calls it  ->  a component calls the
    composable  ->  something fetches  ->  the tab is in the nav  ->  the
    strings exist in every language

★It is a TEXT scan, and that is a real boundary: it cannot mount a component,
evaluate declaration order or notice that a page throws. Those need the browser
suite. What it can do is fail fast and cheaply when a link is simply absent —
which is how every one of the failures above actually looked.
"""
from pathlib import Path

import json

import pytest

REPO = Path(__file__).resolve().parents[4]
FE = REPO / "frontend"
COMPOSABLE = FE / "composables" / "useOwnership.ts"
MODAL = FE / "components" / "TransferOwnershipModal.vue"
PROFILE = FE / "components" / "UserProfileModal.vue"
LOCALES = REPO / "locales"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── the chain, link by link ───────────────────────────────────────────────


def test_the_composable_talks_to_the_routes_that_exist():
    src = _src(COMPOSABLE)
    for endpoint in (
        "/api/me/content/summary",
        "/api/me/content",
        "/api/me/successor",
        "/api/me/content/transfer",
    ):
        assert endpoint in src, f"the composable never calls {endpoint}"


def test_the_composable_does_not_auto_fetch():
    """★Deliberate, and the reason the caller must be checked separately.

    `useAppSettings` fetches on creation; this one does not, so nothing costs a
    request for a person who never opens the tab. The trade is that a caller who
    forgets `fetchContent()` renders an empty panel in silence — which is
    exactly the failure the next test exists to catch.
    """
    src = _src(COMPOSABLE)
    body_after_return = src[src.rfind("return {"):]
    assert "fetchContent()" not in body_after_return


def test_something_actually_fetches():
    src = _src(PROFILE)
    assert "fetchContent()" in src, (
        "nothing calls fetchContent(), so the My content panel renders empty "
        "with no error anywhere — the exact shape of the instance-features bug"
    )
    assert "fetchSuccessor()" in src


def test_the_profile_modal_uses_the_shared_composable():
    src = _src(PROFILE)
    assert "useOwnership()" in src
    assert "/api/me/content" not in src, (
        "the profile modal calls the ownership endpoints directly instead of "
        "going through useOwnership — that is a second copy of the state, and "
        "one of the two copies eventually lies about what you own"
    )


def test_the_tab_is_in_the_nav_and_in_the_type():
    """A panel with no nav entry is unreachable; a nav entry missing from the
    activeTab union is a type error waiting for the next editor."""
    src = _src(PROFILE)
    assert "'myContent'" in src
    assert "key: 'myContent'" in src, "the tab is not in navItems, so nothing opens it"
    assert "activeTab === 'myContent'" in src, "the nav entry opens a panel that does not exist"


def test_the_modal_is_rendered_by_the_panel():
    src = _src(PROFILE)
    assert "<TransferOwnershipModal" in src, (
        "the transfer modal is never rendered, so the Hand over button opens "
        "nothing"
    )


# ── the two things a transfer must say out loud ───────────────────────────


def test_the_run_identity_warning_is_rendered():
    """★The consequence that is not a column change.

    A report with `shared_run_identity='creator'` queries its data AS its owner.
    The backend counts these separately for one reason: so the person choosing a
    recipient is told before they confirm. A modal that drops the warning turns
    a deliberate decision into a surprise.
    """
    src = _src(MODAL)
    assert "runIdentityWarning" in src
    assert "runs_as_owner" in src, (
        "nothing in the modal reads runs_as_owner, so the warning can never fire"
    )


def test_keep_access_defaults_to_on():
    """Figma's rule: handing over is not the same as walking away."""
    src = _src(MODAL)
    assert "const keepAccess = ref(true)" in src


def test_keep_access_is_declared_above_its_readers():
    """★The DataSourceSelector shape, which took this product down.

    A const declared below the computed that reads it survives only while the
    computed stays lazy. An `immediate` watcher — or a future refactor — reads
    it during setup() and hits the temporal dead zone.

    ★★★Scoped to the SCRIPT BLOCK, and that is the whole subtlety. In a `.vue`
    single-file component the template is written above the script, so a
    template binding is "before" every declaration by character index and a
    naive whole-file scan fails on correct code — which is exactly what the
    first version of this test did. Template bindings are resolved at RENDER
    time, long after setup() has run, so they cannot hit a dead zone. Only
    reads inside the script can.
    """
    src = _src(MODAL)
    start = src.find("<script")
    assert start != -1, "no script block"
    script = src[start:]

    declared = script.find("const keepAccess = ref(true)")
    assert declared != -1, "keepAccess is not declared in the script block"

    first_read = script.find("keepAccess.value")
    assert first_read == -1 or declared < first_read, (
        "keepAccess is read in the script before it is declared. Harmless while "
        "every reader stays lazy, and a ReferenceError the moment one does not."
    )


# ── every language, or the panel renders raw key paths ────────────────────


REQUIRED = [
    ("ownership", "myContent", "title"),
    ("ownership", "myContent", "handOverAll"),
    ("ownership", "transfer", "recipientLabel"),
    ("ownership", "transfer", "keepAccess"),
    ("ownership", "transfer", "runIdentityWarning"),
    ("ownership", "transfer", "undo"),
    ("ownership", "successor", "title"),
    ("profile", "nav", "myContent"),
]


def _locale_files():
    return sorted(p for p in LOCALES.glob("*.json") if ".bak-" not in p.name)


def test_every_locale_carries_every_new_string():
    missing = []
    for path in _locale_files():
        data = json.loads(_src(path))
        for chain in REQUIRED:
            node = data
            for key in chain:
                node = node.get(key, {}) if isinstance(node, dict) else {}
            if not isinstance(node, str) or not node.strip():
                missing.append(f"{path.name}: {'.'.join(chain)}")
    assert not missing, (
        "a missing key renders the raw dotted path on screen: " + ", ".join(missing)
    )


def test_english_json_still_ends_without_a_trailing_newline():
    """★Upstream's copy ends WITH one; ours does not. Adding one turns every
    future port of this file into a whole-file conflict — and a json.dump round
    trip does it silently."""
    raw = (LOCALES / "en.json").read_text(encoding="utf-8")
    assert not raw.endswith("\n")


# ── the red proof, run every time ─────────────────────────────────────────


CHECKS = {
    "fetches": lambda s: "fetchContent()" in s,
    "tab in nav": lambda s: "key: 'myContent'" in s,
    "panel exists": lambda s: "activeTab === 'myContent'" in s,
    "modal rendered": lambda s: "<TransferOwnershipModal" in s,
    "shared composable": lambda s: "useOwnership()" in s,
}


def test_the_original_absence_is_still_detected():
    """Reconstruct the pre-change profile modal and require every check to
    reject it. A guard whose assertions merely agree with today's file detects
    nothing."""
    src = _src(PROFILE)
    before = src
    for marker in (
        "fetchContent()",
        "key: 'myContent'",
        "activeTab === 'myContent'",
        "<TransferOwnershipModal",
        "useOwnership()",
    ):
        before = before.replace(marker, "")

    still_passing = [name for name, check in CHECKS.items() if check(before)]
    assert not still_passing, (
        "these checks pass on a file with the feature stripped out, so they are "
        f"not detecting its absence: {still_passing}"
    )


@pytest.mark.parametrize("name,check", list(CHECKS.items()))
def test_each_check_passes_on_the_real_file(name, check):
    """The positive half. Without it a check that can never pass would satisfy
    the red proof above and look like a working guard."""
    assert check(_src(PROFILE)), f"{name}: failed against the real file"


# ─────────────── the warning that is bound to a prop nobody passes ──────────


def test_every_caller_hands_the_modal_a_summary():
    """★★★A template that READS a value proves nothing when nothing WRITES it.

    The dialog's credentials warning is driven by `credential_bound_data_sources`,
    which lives on the SUMMARY, not on the report list the dialog is handed. The
    binding was added correctly and neither caller passed the prop, so the
    warning rendered on nobody's screen — a consequence of a transfer that the
    person confirming it would never have been told about.

    ★It shipped that way because the modal and its callers were written from
    different file lists. Disjoint files prevent conflicts, not integration
    gaps, and this is the shape that already cost this fork four releases when a
    saved logo reached four consumers that each dropped it. So the check is on
    the CALLERS, deliberately: asserting the modal READS the prop is the
    assertion that was already true while the feature did nothing.
    """
    members = (FE / "components" / "MembersComponent.vue").read_text(encoding="utf-8")
    profile = PROFILE.read_text(encoding="utf-8")

    for name, text in (("MembersComponent.vue", members), ("UserProfileModal.vue", profile)):
        start = text.index("<TransferOwnershipModal")
        tag = text[start : text.index("/>", start)]
        assert ":summary=" in tag, (
            f"{name} renders TransferOwnershipModal without :summary, so the "
            "credentials warning inside it can never fire"
        )


def test_the_credentials_warning_says_both_halves():
    """★The consequence splits two ways and a warning naming only one is worse
    than none: for Fabric and Power BI agents the recipient CANNOT run them
    until they sign in themselves, and for the other per-user connections they
    silently GAIN the organization's shared sign-in. Somebody told only the
    first half would approve the second without knowing."""
    import json

    en = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
    copy = en["ownership"]["transfer"]["credentialsWarning"].lower()
    assert "cannot run" in copy, "the warning omits that the recipient may be locked out"
    assert "shared sign-in" in copy or "shared sign in" in copy, (
        "the warning omits that the recipient gains the shared credentials"
    )
    for forbidden in ("auth_policy", "user_required", "data_source"):
        assert forbidden not in copy, (
            f"{forbidden!r} is a column name, not something to show a user"
        )


def test_the_modal_reads_the_count_off_the_summary_not_the_items():
    """★The item list is REPORTS; this count is AGENTS. Deriving it from
    `movingItems` would give zero on every screen and read as 'no agents are
    affected' rather than 'nobody asked'."""
    src = MODAL.read_text(encoding="utf-8")
    body = src[src.index("const credentialBoundCount") :][:400]
    assert "summary" in body and "movingItems" not in body
