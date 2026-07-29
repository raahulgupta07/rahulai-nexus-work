"""The settings screen that says how people get in.

It used to be "Domain-based signup" — a list of email domains kept by hand. That
list only ever existed because nothing else could admit anybody: signing in
proved who you were and never decided whether you got an account. Three things
can now, and a hand-kept domain list is a second copy of what the identity
provider already knows, in a place that can disagree with it.

So the screen states the three real ways in, shows which of them create accounts,
owns the role those accounts arrive with, and keeps the domain list under
Advanced for installs still leaning on it.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PAGE = REPO / "frontend" / "components" / "SignupPolicyManager.vue"
MEMBERS = REPO / "frontend" / "pages" / "settings" / "members.vue"
EN = REPO / "locales" / "en.json"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _en() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


def _js_fn(name: str) -> str:
    """One top-level function from the component's <script setup>.

    Needed because a substring check over the whole file is satisfied by
    unrelated code that happens to contain the same token — which is exactly
    how two planted faults slipped past the first version of these tests.
    """
    src = _src()
    start = src.index(f"function {name}(")
    rest = src[start:]
    nxt = re.search(r"\n(?:async )?function \w+\(", rest[1:])
    body = rest[: nxt.start() + 1] if nxt else rest
    assert "useMyFetch" in body or len(body) > 40, f"{name} looks empty"
    return body


# ---------------------------------------------------------------------------
# The three doors
# ---------------------------------------------------------------------------
def test_all_three_ways_in_are_named():
    """★Asserts the exact CALL, not the bare key name. A plant that renamed the
    key to `waysIn.ldapTitleX` passed the substring version — the mangled name
    still contains the original."""
    src = _src()
    for key in ("waysIn.adminTitle", "waysIn.ssoTitle", "waysIn.ldapTitle"):
        assert f"$t('{key}')" in src, f"{key} — a way in that the screen never mentions"


def test_each_door_says_whether_it_creates_accounts():
    """★The whole failure was that signing in did not mean getting an account.
    An admin looking at this screen has to be able to see which doors do."""
    src = _src()
    assert "badgeLabel(ssoDoor)" in src
    assert "badgeLabel(ldapDoor)" in src
    assert "waysIn.badgeCreates" in src and "waysIn.badgeInviteOnly" in src


def test_the_door_states_are_the_three_that_exist():
    src = _src()
    assert "type DoorState = 'creates' | 'invite' | 'off'" in src


def test_a_provider_that_cannot_create_reads_as_invite_only():
    """Enabled is not the same as admitting. A provider that authenticates but
    does not create must not look like a way in for new people."""
    src = _src()
    assert "list.some((p) => p.creates) ? 'creates' : 'invite'" in src
    assert "cfg.auto_provision_users ? 'creates' : 'invite'" in src


def test_a_directory_that_is_off_reads_as_off_regardless():
    src = _src()
    assert "if (!cfg?.enabled) ldapDoor.value = 'off'" in src


def test_the_switches_are_shown_here_and_set_elsewhere():
    """★★Deliberately read-only. Editing the same switch in two screens is the
    two-places-to-disagree problem this page exists to remove — and it is the
    reason the domain list was demoted in the first place."""
    src = _src()
    assert "auto_provision" not in src.split("<script")[0], (
        "the provider switch is editable in the template — it belongs to the "
        "Identity Providers page alone"
    )
    assert src.count("/settings/identity-provider") >= 2, (
        "both provider doors must point at where they are actually configured"
    )


def test_the_status_reads_fail_quiet():
    """An admin without the enterprise directory feature, or an install with no
    providers, must still see a usable page — not an error."""
    src = _src()
    doors = src[src.index("async function loadDoors()"):]
    doors = doors[: doors.index("function normalizeDomain")]
    assert doors.count("catch") == 2
    assert "ssoDoor.value = 'off'" in doors and "ldapDoor.value = 'off'" in doors


# ---------------------------------------------------------------------------
# The role
# ---------------------------------------------------------------------------
def test_the_page_owns_the_auto_provision_role():
    """★Scoped to the two functions that do the work.

    The first version asserted the endpoint and `method: 'PUT'` appeared
    ANYWHERE in the file — and both plants passed, because the domain-policy
    save right below also PUTs. A presence check over a whole file proves
    nothing about the code you meant to check.
    """
    read = _js_fn("loadAutoRole")
    assert "'/organization/auto-provision'" in read, "the role is never read"

    write = _js_fn("saveRole")
    assert "'/organization/auto-provision'" in write, "the role is never saved"
    assert "method: 'PUT'" in write, "the role save is not a write"


def test_the_role_control_is_only_claimed_to_matter_when_it_does():
    """★If nothing creates accounts, the setting has no effect. Say so rather
    than showing a control that silently does nothing."""
    src = _src()
    assert "anyDoorCreates" in src
    assert "waysIn.roleUnusedNote" in src


def test_a_failed_role_save_snaps_back():
    """★Otherwise the selector keeps showing the rejected value and the admin
    believes a role is set that the server refused."""
    src = _src()
    save = src[src.index("async function saveRole()"):]
    save = save[: save.index("async function loadDoors")]
    assert "autoRole.value = originalAutoRole.value" in save.split("catch")[1]


def test_the_role_and_the_domain_role_stay_separate():
    """The domain list has its own `auto_invite_role` and keeps it. They are
    different questions — one is 'anyone from this domain', the other is
    'anyone the provider vouches for'."""
    src = _src()
    assert "form.auto_invite_role" in src
    assert "autoRole" in src
    assert "form.auto_invite_role = autoRole" not in src


# ---------------------------------------------------------------------------
# The domain list, demoted but not removed
# ---------------------------------------------------------------------------
def test_the_domain_list_is_behind_advanced():
    src = _src()
    assert "showAdvanced" in src
    assert "waysIn.advancedTitle" in src
    adv = src.index("waysIn.advancedTitle")
    assert src.index("signupPolicy.allowedDomains") > adv, (
        "the domain list is still above the fold"
    )


def test_an_install_already_using_domains_still_finds_them():
    """★Demoting a feature must not hide it from the people relying on it."""
    src = _src()
    assert "if (p.enabled) showAdvanced.value = true" in src


def test_the_domain_list_still_saves_the_way_it_did():
    src = _src()
    assert "'/organization/signup-policy'" in src
    assert "signupPolicy.toastSaved" in src
    assert "form.enabled && form.allowed_domains.length === 0" in src, (
        "the enable-without-domains guard was dropped"
    )


def test_the_domain_section_hides_without_its_licence():
    """A 402 means the control could never be saved. Hide it rather than
    offering a button that always fails."""
    src = _src()
    assert "domainSignupAvailable" in src
    assert "statusCode === 402" in src


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------
def test_the_tab_is_no_longer_gated_on_the_domain_licence():
    """★★The tab is not the domain list any more. Gating it on `domain_signup`
    hid the three doors AND the role from every install that never used
    domains — which is most of them."""
    src = MEMBERS.read_text(encoding="utf-8")
    line = [l for l in src.splitlines() if "key: 'signup'" in l]
    assert len(line) == 1, line
    assert "domain_signup" not in line[0], line[0]
    assert "full_admin_access" in line[0], "the tab must stay admin-only"


def test_the_tab_is_named_for_what_it_does():
    assert _en()["settings"]["membersTabs"]["signup"] == "How people get in"


# ---------------------------------------------------------------------------
# ★vue-i18n renders the KEY on a miss — a typo ships as visible gibberish
# ---------------------------------------------------------------------------
def test_every_string_the_page_asks_for_exists():
    """★`$t('a.b') || 'Fallback'` does NOT fall back — vue-i18n returns the
    truthy key. So a missing key is not a blank, it is `waysIn.roleTitle`
    rendered on the screen. This has happened three times in this codebase.
    """
    src = _src()
    en = _en()
    keys = set(re.findall(r"\$?t\(\s*'([a-zA-Z0-9_.]+)'\s*\)", src))
    assert keys, "no translated strings found — did the template change shape?"

    missing = []
    for k in sorted(keys):
        node = en
        for part in k.split("."):
            if not isinstance(node, dict) or part not in node:
                missing.append(k)
                break
            node = node[part]
        else:
            if not isinstance(node, str) or not node.strip():
                missing.append(k)
    assert not missing, f"keys that would render as raw text: {missing}"


def test_the_guard_above_is_actually_looking_at_something():
    """Guard the guard: a template rewrite that stops matching the regex would
    make the test above pass by checking nothing."""
    keys = set(re.findall(r"\$?t\(\s*'([a-zA-Z0-9_.]+)'\s*\)", _src()))
    assert len([k for k in keys if k.startswith("waysIn.")]) >= 15, sorted(keys)


def test_the_locale_file_kept_its_shape():
    """★`json.dumps(indent=4)` on this 2-space file reformats all 4000 lines,
    and it has NO trailing newline. Locale edits must be surgical."""
    raw = EN.read_text(encoding="utf-8")
    assert not raw.endswith("\n")
    assert '\n  "waysIn": {\n' in raw
    assert '\n    "title": "How people get in",\n' in raw
