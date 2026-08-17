"""The Needs-an-owner banner, end to end through the four hops.

The 0.0.531.2–.5 releases cost four attempts at the SSO logo picker because a
value was saved correctly and every consumer dropped it. The lesson written into
this fork is that a feature is not a route — it is a chain, and every link is a
place a field vanishes silently. So this walks all of them:

  route → composable → component → locale

★A template that READS a value proves nothing when nothing WRITES it. The
strongest check here is the pair: `loadOrphans` is DEFINED and also CALLED on
mount. A banner wired to a fetcher nobody invokes renders nothing forever, with
no error anywhere — the exact failure mode of `initAgentPreference` during the
531 port, where server-side persistence was completely inert and localStorage
made it look like it worked on the machine that was tested.

★These read files as TEXT. They cannot mount a component or evaluate ordering —
that is the boundary of a Python suite, and the reason a Playwright case exists
alongside them. They still catch the whole class of "one hop was missed".

★★★**Red proof, by mutation of the shipped files** — each dropped hop took down
exactly one test and left the other 10 green:

  * deleting `loadOrphans()` from `onMounted` → only
    `test_the_banner_is_rendered_and_its_loader_is_actually_called`
  * deleting `fetchOrphans,` from the composable's return → only
    `test_the_composable_fetches_and_exports_it`

Both are silent failures in the product: the banner never appears, no request
is made, and nothing is logged anywhere.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MEMBERS = REPO / "frontend" / "components" / "MembersComponent.vue"
COMPOSABLE = REPO / "frontend" / "composables" / "useOwnership.ts"
ROUTE = REPO / "backend" / "app" / "routes" / "ownership.py"
LOCALES = REPO / "locales"

ORPHAN_KEYS = {"title", "body", "owns", "staleSuccessor", "reassign"}


def _script(path: Path) -> str:
    """Only the <script setup> block.

    ★A .vue template always precedes its script by character index, so any
    ordering check run over the whole file reports every template binding as a
    use-before-declare. That mistake produced a wrong guard in 0.0.531.4.
    """
    text = path.read_text(encoding="utf-8")
    start = text.find("<script setup")
    assert start != -1, "MembersComponent has no <script setup> block"
    return text[start:]


# ─────────────────────────── hop 1: the route ──────────────────────────────


def test_the_route_exists_and_is_gated():
    src = ROUTE.read_text(encoding="utf-8")
    idx = src.find('"/organizations/{organization_id}/orphaned-content"')
    assert idx != -1, "the Needs-an-owner route is gone"

    window = src[idx : idx + 400]
    assert "@requires_permission('manage_settings')" in window, (
        "the orphan listing is ungated or gated on something else. It names "
        "people and counts their work; it is not a public read."
    )


# ──────────────────────── hop 2: the composable ────────────────────────────


def test_the_composable_fetches_and_exports_it():
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert "const fetchOrphans = async" in src, "fetchOrphans is not defined"
    assert "orphaned-content" in src, "fetchOrphans does not call the route"

    returned = src[src.rindex("  return {") :]
    assert "fetchOrphans," in returned, (
        "fetchOrphans is defined and never returned, so no component can reach "
        "it — the whole feature is dead with nothing logged"
    )


def test_a_failed_fetch_degrades_to_empty_not_to_a_broken_page():
    """★This banner sits on top of a working members list. A throw here would
    take down the screen an administrator uses to fix the problem."""
    src = COMPOSABLE.read_text(encoding="utf-8")
    body = src[src.index("const fetchOrphans = async") :]
    body = body[: body.index("\n  const ")]
    assert "catch" in body and "return []" in body, (
        "fetchOrphans can throw into the members page"
    )


# ───────────────────────── hop 3: the component ────────────────────────────


def test_the_banner_is_rendered_and_its_loader_is_actually_called():
    """★★★The load-bearing check.

    Defining `loadOrphans` and never calling it leaves `orphans` empty forever.
    The banner is `v-if="orphans.length"`, so it simply never appears — no
    error, no request, nothing to notice. `initAgentPreference` failed exactly
    this way during the 531 port.
    """
    text = MEMBERS.read_text(encoding="utf-8")
    script = _script(MEMBERS)

    assert 'data-testid="needs-an-owner"' in text, "the banner is gone"
    assert 'v-if="orphans.length"' in text, (
        "the banner is not conditional, so an organization with nothing "
        "stranded gets a permanent empty panel — one people stop seeing"
    )
    assert "const loadOrphans = async" in script, "loadOrphans is not defined"

    # Calls, excluding the definition itself.
    calls = len(re.findall(r"(?<!const )loadOrphans\(\)", script))
    assert calls >= 1, (
        "loadOrphans is defined and never called. The banner is bound to a ref "
        "that nothing ever fills, so it renders nothing forever."
    )

    mounted = script[script.index("onMounted(") :]
    assert "loadOrphans()" in mounted[:1200], (
        "loadOrphans is not called on mount, so the banner only appears after "
        "some other action happens to refresh it"
    )


def test_reassigning_an_orphan_opens_the_transfer_modal():
    script = _script(MEMBERS)
    assert "const openOrphanTransfer = " in script
    # ★The window ends at the function's own closing brace, never at a fixed
    # character count. A fixed slice measures the slice: adding a comment inside
    # this function pushed the `open` assignment past a 400-char cut and the
    # guard failed against a file that was completely correct. That reads as a
    # broken feature and costs somebody an afternoon.
    tail = script[script.index("const openOrphanTransfer = ") :]
    body = tail[: tail.index("\n}") + 2]
    assert "membership_id" in body, (
        "the orphan's membership id is not passed, so the modal would act on "
        "whoever was selected last"
    )
    assert "transferModalOpen.value = true" in body
    assert "transferSummary.value = orphan.summary" in body, (
        "the orphan row's summary is not handed to the modal, so the dialog's "
        "credentials warning is bound to a prop nothing writes and renders for "
        "nobody"
    )


def test_the_transfer_modal_is_driven_by_the_shared_target():
    """★An orphan is not in `members` — their account is deactivated and they
    come from a different endpoint. Binding the modal to a Member row means the
    Reassign button silently does nothing for exactly the people it exists for.
    """
    text = MEMBERS.read_text(encoding="utf-8")
    idx = text.index("<TransferOwnershipModal")
    block = text[idx : idx + 420]
    assert 'v-if="transferTarget"' in block, (
        "the transfer modal still guards on a Member row, so it cannot open "
        "for an orphan"
    )
    assert 'transferTarget.membershipId' in block


def test_the_orphan_target_is_declared_above_its_readers():
    """The temporal-dead-zone shape that cost this fork a release."""
    script = _script(MEMBERS)
    decl = script.index("const transferTarget = ")
    first_read = script.index("transferTarget.value", decl - 4000 if decl > 4000 else 0)
    assert decl < first_read, (
        "transferTarget is read before it is declared; an eager evaluation "
        "during setup() would throw"
    )


def test_no_raw_key_paths_are_rendered():
    """Every string on the banner goes through $t. A literal `ownership.orphans.
    title` on screen is what a missing key looks like to a user."""
    text = MEMBERS.read_text(encoding="utf-8")
    idx = text.index('data-testid="needs-an-owner"')
    block = text[idx : idx + 2600]
    for key in ORPHAN_KEYS:
        assert f"ownership.orphans.{key}" in block, f"the banner never uses {key}"
    assert "{{ 'ownership.orphans" not in block


# ────────────────────────── hop 4: the locales ─────────────────────────────


def test_every_language_carries_every_orphan_key():
    """★A missing key does not fail loudly — vue-i18n renders the path itself,
    so the banner shows `ownership.orphans.body` to a French user and nothing
    anywhere records it."""
    missing: list[str] = []
    for path in sorted(LOCALES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        block = data.get("ownership", {}).get("orphans")
        if not isinstance(block, dict):
            missing.append(f"{path.name}: no ownership.orphans block")
            continue
        for key in sorted(ORPHAN_KEYS - set(block)):
            missing.append(f"{path.name}: {key}")
    assert not missing, "untranslated: " + ", ".join(missing)


def test_the_interpolations_survived_translation():
    """★A translator dropping `{count}` produces a sentence that is grammatical
    and wrong — "people left work behind" with no number. Nothing else checks
    this, because the string still renders."""
    required = {"title": "{count}", "owns": "{count}", "staleSuccessor": "{name}"}
    broken: list[str] = []
    for path in sorted(LOCALES.glob("*.json")):
        block = json.loads(path.read_text(encoding="utf-8")).get("ownership", {}).get(
            "orphans", {}
        )
        for key, token in required.items():
            value = block.get(key)
            if isinstance(value, str) and token not in value:
                broken.append(f"{path.name}: {key} lost {token}")
    assert not broken, "; ".join(broken)


def test_en_json_still_has_no_trailing_newline():
    """★Upstream's copy ends without one. Adding it makes every future port of
    this file a whole-file conflict."""
    assert not LOCALES.joinpath("en.json").read_bytes().endswith(b"\n")
