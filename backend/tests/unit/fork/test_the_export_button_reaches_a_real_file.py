"""The download chain — 0.0.531.7.

Same four hops as every other surface in this feature (route → composable →
component → locale), with one extra thing to pin that none of the others have:

★★★**`responseType: 'blob'`.** Without it the zip is decoded as text on the way
through, and what lands on disk is a corrupt archive that opens nowhere. There
is no error, no console line, and no failing request — the download *succeeds*.
Nobody finds out until the day they open the backup, which is by definition the
worst day to find out. It is one option in one object and it is the single most
load-bearing character sequence in this release.

★A .vue template always precedes its script by character index, so ordering
checks run over a whole file report every template binding as a use-before-
declare. Anything positional here reads the script block only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
COMPOSABLE = REPO / "frontend" / "composables" / "useOwnership.ts"
PROFILE = REPO / "frontend" / "components" / "UserProfileModal.vue"
REMOVE = REPO / "frontend" / "components" / "RemoveMemberModal.vue"
ROUTES = REPO / "backend" / "app" / "routes" / "ownership.py"
LOCALES = REPO / "locales"


def _script(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find("<script setup")
    assert start != -1, f"{path.name} has no <script setup> block"
    return text[start:]


# ─────────────────────────── hop 1: the routes ─────────────────────────────


def test_both_export_routes_exist():
    src = ROUTES.read_text(encoding="utf-8")
    assert '"/me/content/export"' in src, "the self-export route is gone"
    assert "/content-export" in src, "the admin export route is gone"


# ──────────────────────── hop 2: the composable ────────────────────────────


def test_the_download_asks_for_a_blob():
    """★★★The whole release turns on this.

    Fetched as text, the zip is silently corrupted and the download still
    reports success. Nothing else in the stack can detect it.
    """
    src = COMPOSABLE.read_text(encoding="utf-8")
    body = src[src.index("const downloadBundle = ") :]
    body = body[: body.index("\n  const exportMyContent")]
    assert "responseType: 'blob'" in body, (
        "the bundle is fetched without responseType: 'blob', so the zip is "
        "decoded as text and the file that downloads is corrupt — with no "
        "error anywhere and the download reporting success"
    )


def test_the_object_url_is_revoked():
    """A blob URL held after the click pins the whole archive in memory for the
    life of the tab. Harmless once; this is a modal somebody may use repeatedly
    while offboarding a team."""
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert "revokeObjectURL" in src


def test_both_exports_are_defined_and_returned():
    src = COMPOSABLE.read_text(encoding="utf-8")
    for name in ("exportMyContent", "exportMemberContent"):
        assert f"const {name} = " in src, f"{name} is not defined"
    returned = src[src.rindex("  return {") :]
    for name in ("exportMyContent", "exportMemberContent"):
        assert f"{name}," in returned, (
            f"{name} is defined and never returned, so no component can reach "
            "it — the button is inert with nothing logged"
        )


def test_a_failed_export_does_not_throw_into_the_page():
    src = COMPOSABLE.read_text(encoding="utf-8")
    body = src[src.index("const downloadBundle = ") :][:1600]
    assert "catch" in body and "return false" in body, (
        "a failed export can throw into the dialog it was launched from"
    )


# ───────────────────────── hop 3: the components ───────────────────────────


def test_the_profile_modal_offers_the_download_and_wires_it():
    text = PROFILE.read_text(encoding="utf-8")
    script = _script(PROFILE)
    assert 'data-testid="export-my-content"' in text, "the download button is gone"
    assert "exportMyContent()" in text, "the button is not wired to the fetcher"
    assert "exportMyContent," in script, (
        "exportMyContent is used in the template and never destructured from "
        "the composable, so the click throws"
    )


def test_the_download_is_not_disabled_on_an_empty_list():
    """★Somebody who owns nothing still gets a manifest saying so, which is a
    real answer to "did my work survive?" — better than a dead button."""
    text = PROFILE.read_text(encoding="utf-8")
    idx = text.index('data-testid="export-my-content"')
    # The button's own attributes, back to the opening tag.
    start = text.rindex("<UButton", 0, idx)
    block = text[start : idx + 200]
    assert ":disabled" not in block, (
        "the export button is disabled, so the one person who most needs to "
        "confirm their work is gone cannot ask"
    )


def test_the_remove_dialog_offers_a_copy_before_the_irreversible_button():
    """★Placement is the point. Transferring keeps the work inside this install;
    the bundle survives the install. Offering it after the removal would be
    offering it too late."""
    text = REMOVE.read_text(encoding="utf-8")
    export_at = text.index('data-testid="remove-export"')
    confirm_at = text.index('data-testid="remove-confirm"')
    assert export_at < confirm_at, (
        "the export offer sits below the Remove button in the dialog"
    )
    assert "exportMemberContent" in _script(REMOVE)


def test_exporting_does_not_freeze_the_dialog():
    """★A shared busy flag would disable Cancel and Remove during a download, so
    an admin who exports first appears to have jammed the dialog."""
    script = _script(REMOVE)
    assert "const exporting = ref(false)" in script, (
        "the export shares `busy` with the removal"
    )
    decl = script.index("const exporting = ref(false)")
    first_read = script.index("exporting.value")
    assert decl < first_read, "exporting is read before it is declared"


def test_a_modal_opened_by_v_if_still_loads_its_data():
    """★★★A real bug, found by the browser and by nothing else.

    Both dialogs are rendered by their parent under a `v-if`, and the parent
    sets the target and the open flag in the SAME tick. So by the time the
    child's `watch(open, …)` is created, `open` is already `true` — and a plain
    watcher only fires on a CHANGE. It never ran.

    The failure is quiet and specific. The dialog opens, the title is correct,
    the buttons work; only the section that makes it useful is missing — the
    owned counts on the removal dialog, and the RECIPIENT PICKER on the
    transfer dialog, which is the one control that dialog exists to provide. No
    request is made and nothing is logged, so it reads as an empty state rather
    than a defect. I first attributed it to the endpoint 404ing against the old
    backend; nothing had been asked.

    ★`{ immediate: true }` is safe in both files ONLY because every ref and
    function the callback touches is declared above the watcher. That is the
    exact shape that cost this fork the 0.0.518.1 release, so anything moved
    below one of these watchers must be moved back.
    """
    for path in (REMOVE, REPO / "frontend" / "components" / "TransferOwnershipModal.vue"):
        script = _script(path)
        idx = script.index("watch(open")
        # The watcher call, up to the end of its options object.
        tail = script[idx : idx + 1400]
        assert "{ immediate: true }" in tail, (
            f"{path.name}: watch(open, …) is not immediate. The component is "
            "mounted with open already true, so this watcher never fires and "
            "the dialog renders without the data it was opened to show."
        )


# ────────────────────────── hop 4: the locales ─────────────────────────────


def test_every_language_carries_the_export_strings():
    """★vue-i18n renders the key path itself when a key is missing, so an
    untranslated button reads `ownership.remove.download` to that user and
    nothing anywhere records it."""
    missing: list[str] = []
    for path in sorted(LOCALES.glob("*.json")):
        own = json.loads(path.read_text(encoding="utf-8")).get("ownership", {})
        if "download" not in own.get("myContent", {}):
            missing.append(f"{path.name}: myContent.download")
        for key in ("download", "downloadDone"):
            if key not in own.get("remove", {}):
                missing.append(f"{path.name}: remove.{key}")
    assert not missing, "untranslated: " + ", ".join(missing)


def test_no_raw_key_paths_in_the_export_controls():
    for path in (PROFILE, REMOVE):
        text = path.read_text(encoding="utf-8")
        for match in re.findall(r"\$t\('(ownership\.[a-zA-Z.]+)'", text):
            assert not match.endswith("."), f"{path.name}: malformed key {match}"


def test_en_json_still_has_no_trailing_newline():
    assert not LOCALES.joinpath("en.json").read_bytes().endswith(b"\n")
