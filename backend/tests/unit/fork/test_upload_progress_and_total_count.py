"""Two screens that could not tell the user a number they already had.

1. THE UPLOAD CHIP COULD ONLY SPIN.
   Not a design choice — `useMyFetch` goes through the fetch API, which emits no
   upload-progress event of any kind. A 40 MB file on a slow line rendered the
   same pixels as a stalled request. XMLHttpRequest still exposes
   `upload.onprogress`, so that is what the new composable uses.

   ★ The percentage covers BYTES SENT and nothing else. When the last byte
   lands, the server parses the file, may split a workbook into one table per
   sheet, and may re-learn the agent — none of which reports progress. A bar
   that fills to 100% and then sits there for another thirty seconds is the same
   lie as a spinner, told with more confidence. So the composable exposes a
   `stage`, the bar stops at 99 until the response arrives, and the UI switches
   to a NAMED indeterminate state rather than inventing a number.

2. TWO DIFFERENT TOTALS ON ONE SCREEN.
   The header read the server's `counts.total`, a deduped union. The modal it
   opens computed `rows.length + notLiveRows.length` — a plain sum. An
   instruction that is pending AND not carried by the live build is in both
   arrays, so it was counted twice: 21 in the modal against 16 in the header,
   both visible at once. The server had already fixed exactly this
   (instruction_service.py: "UNION, not a sum … 220 against 139 real ones");
   the client reproduced it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
COMP = REPO / "frontend" / "composables" / "useUploadWithProgress.ts"
CHIP = REPO / "frontend" / "components" / "FileUploadComponent.vue"
PANEL = REPO / "frontend" / "components" / "datasources" / "AgentFilesPanel.vue"
MODAL = REPO / "frontend" / "components" / "instructions" / "AllInstructionsModal.vue"
SERVICE = REPO / "backend" / "app" / "services" / "instruction_service.py"
EN = REPO / "locales" / "en.json"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── 1. upload progress ───────────────────────────────────────────────────────

def test_the_composable_uses_xhr_because_fetch_cannot():
    src = _src(COMP)
    assert "new XMLHttpRequest()" in src
    assert "xhr.upload.onprogress" in src, (
        "without the upload progress event this is just fetch with extra steps"
    )


def test_auth_matches_usemyfetch_exactly():
    """Diverging here is a 401 that happens only on uploads."""
    src = _src(COMP)
    assert "setRequestHeader('Authorization'" in src
    assert "setRequestHeader('X-Organization-Id'" in src
    assert "ensureOrganization()" in src, (
        "useMyFetch awaits the org before every call; skipping it races the "
        "org load and sends uploads with no org header"
    )


def test_the_bar_does_not_claim_completion_before_the_response():
    src = _src(COMP)
    assert "Math.min(99," in src, (
        "reaching 100% while the server is still parsing is what makes an "
        "upload look hung"
    )


def test_there_is_a_stage_for_the_part_that_has_no_percentage():
    src = _src(COMP)
    assert "UploadStage" in src and "'processing'" in src
    assert "stage.value = 'processing'" in src, (
        "nothing marks the seam between bytes-sent and server-side work"
    )


def test_a_failed_upload_is_not_reported_as_finished():
    src = _src(COMP)
    for handler in ("xhr.onerror", "xhr.onabort"):
        assert handler in src, f"{handler} unhandled — the promise would never settle"
    assert "stage.value = 'error'" in src


def test_both_upload_paths_use_it():
    for p, what in [(CHIP, "the composer's paperclip"), (PANEL, "the agent Files panel")]:
        src = _src(p)
        assert "useUploadWithProgress()" in src, f"{what} still has no progress"
        assert "uploader(" in src


def test_the_chip_shows_bytes_while_uploading_and_a_name_after():
    src = _src(CHIP)
    assert "file.upload_percent" in src and "formatBytes(file.upload_loaded)" in src
    assert "files.uploadProcessing" in src, (
        "after the last byte the chip must name what is happening rather than "
        "show a number it does not have"
    )


def test_the_panel_progress_is_not_inside_the_disabled_button():
    """The upload button is disabled mid-upload; a disabled control is the last
    place to put the only sign of life. Same class of mistake as the convert
    progress that lived inside a hover-only element."""
    src = _src(PANEL)
    i = src.index('<div v-if="uploading" class="mt-2 mb-1">')
    block = src[i:i + 1200]
    assert "uploadPercent" in block
    assert ":disabled" not in block


def test_the_panel_resets_between_uploads():
    src = _src(PANEL)
    i = src.index("} finally {")
    block = src[i:i + 400]
    assert "uploadPercent.value = 0" in block, (
        "the next upload would open showing the previous one's percentage"
    )


def test_the_copy_exists():
    import json
    assert json.loads(_src(EN))["files"]["uploadProcessing"]


# ── 2. the total ─────────────────────────────────────────────────────────────

def test_the_modal_total_is_a_union_not_a_sum():
    src = _src(MODAL)
    i = src.index("const totalCount = computed(")
    block = src[i:i + 400]
    assert "new Set" in block, "still adding two overlapping arrays"
    assert "rows.value.length + notLiveRows.value.length" not in src


def test_the_state_chips_still_overlap_on_purpose():
    """A single instruction can legitimately be both pending and not-live. Only
    the TOTAL must not double count; deduping the chips would be a new bug."""
    src = _src(MODAL)
    assert "const notLiveCount = computed(() => notLiveRows.value.length)" in src


def test_the_server_side_precedent_is_still_there():
    """The header's number comes from here. If this stops being a union, the
    two numbers disagree again — from the other side."""
    src = _src(SERVICE)
    assert "UNION, not a sum" in src
    assert "total = len({str(i) for i in live_ids} | not_live_id_set)" in src
