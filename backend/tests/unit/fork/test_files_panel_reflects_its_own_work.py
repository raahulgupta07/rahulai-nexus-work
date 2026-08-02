"""The files panel did work and then showed the user something else.

TWO FAULTS, ONE PANEL. Both were reported as "the upload failed" and neither
was an upload failure — every request returned 200.

  1. AFTER AN UPLOAD, THE ROW WAS RENDERED FROM THE WRONG RECORD.
     `fate` and `intake` are DERIVED, not columns. file_schema.py says it
     outright: "Populated by get_files_by_data_source; None on endpoints that
     don't compute it." The upload endpoint is one of those. The panel pushed
     the upload response straight into its list, so a freshly uploaded file
     rendered through the `default` branch of fateOf() — "Not ingested … the
     agent cannot use it until re-ingested" — with a Re-ingest button offering
     to redo work that had already finished. Reload the page and the same row
     read "In table · 68%". One saved HTTP request, in exchange for telling the
     user their upload had not worked.

  2. THE PANEL TOLD NOBODY WHAT IT HAD DONE.
     It declared `defineEmits(['edit-connection'])` and nothing else. The tree,
     the `Files` count and the `Tables` count belong to KnowledgeExplorer, whose
     `loadAgentMeta` short-circuits on `agentLoaded`. So a file uploaded here
     never reached the tree at all — not stale-looking, absent — until a reload.

  3. (found while reading the same function) The panel sent no `learn`
     parameter, which defaults to True, so a six-file batch kicked off six full
     re-learns. The route's own comment documents the intended behaviour: the
     frontend sends learn=false for every file except the last.

AND THE CONVERT PROGRESS. There WAS a progress indicator, and it was correct —
it just lived inside a control styled `opacity-0 group-hover:opacity-100`.
Choosing a destination closes the popover; the pointer leaves; the one element
carrying the state becomes invisible. The request is synchronous and re-reads
the whole document, so the row sat there identical to its idle self, and the
natural response was to click Convert again and start a second conversion.

These are text assertions over .vue files. They prove the wiring is written
correctly; they do NOT prove the browser renders it. That needs a live check.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PANEL = REPO / "frontend" / "components" / "datasources" / "AgentFilesPanel.vue"
TABS = REPO / "frontend" / "components" / "datasources" / "AgentKnowledgeTabs.vue"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
SCHEMA = REPO / "backend" / "app" / "schemas" / "file_schema.py"
ROUTE = REPO / "backend" / "app" / "routes" / "file.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fn(src: str, header: str) -> str:
    """A JS function body, from its declaration to the next top-level one."""
    i = src.index(header)
    rest = src[i:]
    nxt = re.search(r"\n(?:async function |function |const [A-Za-z_])", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── 1. the upload must be re-read, not pushed ────────────────────────────────

def test_upload_does_not_push_the_post_response():
    body = _fn(_src(PANEL), "async function onFileInput")
    assert "files.value.push" not in body, (
        "The upload response has no `fate`/`intake` — they are computed by the "
        "list endpoint only. Pushing it renders the file as 'Not ingested' when "
        "it has in fact been ingested."
    )


def test_upload_refetches_and_announces():
    body = _fn(_src(PANEL), "async function onFileInput")
    assert "await loadAll()" in body, "no refetch after upload"
    assert "emit('files-changed')" in body, "the tree is still not told"


def test_the_derived_field_contract_that_makes_this_necessary():
    """If the backend ever starts computing these on POST, revisit the fix."""
    s = _src(SCHEMA)
    assert "fate: Optional[str] = None" in s
    assert "intake: Optional[dict] = None" in s
    assert "None on endpoints that don't compute it" in s, (
        "the comment naming this contract is gone — either it moved or the "
        "behaviour changed; do not assume this test still means what it meant"
    )


def test_batch_learns_once_not_once_per_file():
    body = _fn(_src(PANEL), "async function onFileInput")
    assert "learn=" in body, "no learn parameter — defaults to true, one re-learn PER FILE"
    assert re.search(r"const last = i === list\.length - 1", body), \
        "the last-file computation is gone, so learn is no longer batch-aware"
    assert "?learn=${last}" in body


def test_the_route_still_documents_that_contract():
    """The comment is wrapped across lines with `# ` prefixes, so match it
    against the collapsed text rather than the raw source — the first version of
    this test failed purely on a line break, which is a false alarm, and a test
    that cries wolf gets deleted."""
    flat = re.sub(r"\s*#\s*", " ", _src(ROUTE))
    flat = re.sub(r"\s+", " ", flat)
    assert "learn=false for every file of a multi-file batch EXCEPT the last one" in flat, (
        "the route comment this behaviour was derived from has changed"
    )


# ── 2. every mutation announces itself ───────────────────────────────────────

def test_panel_declares_the_event():
    assert re.search(r"defineEmits\(\[[^\]]*'files-changed'", _src(PANEL)), \
        "files-changed is not declared, so Vue will warn and the listener is a no-op"


def test_convert_reingest_and_remove_all_announce():
    src = _src(PANEL)
    for fn_header, what in [
        ("async function convertFile", "a conversion changes the instruction/knowledge counts"),
        ("async function reingestFile", "a re-ingest can create a table"),
        ("async function removeFile", "a removal can withdraw a table"),
    ]:
        assert "emit('files-changed')" in _fn(src, fn_header), f"{fn_header}: {what}"


def test_the_host_listens_and_actually_refetches():
    src = _src(EXPLORER)
    assert '@files-changed="refreshAgentMeta(panelView.agentId)"' in src, \
        "KnowledgeExplorer does not listen for the event"
    body = _fn(src, "const refreshAgentMeta")
    assert "agentLoaded.value.delete" in body, (
        "refreshAgentMeta must clear the guard first — loadAgentMeta returns "
        "immediately for an agent already in `agentLoaded`, so without this the "
        "listener is wired up and does nothing at all"
    )
    assert "loadAgentMeta(id)" in body


def test_the_intermediate_component_forwards_rather_than_swallows():
    src = _src(TABS)
    assert "'files-changed'" in src and '@files-changed="$emit(\'files-changed\')"' in src, \
        "AgentKnowledgeTabs swallows the event — the same fault, one level up"


# ── 3. convert progress must not be hover-gated ──────────────────────────────

def test_progress_is_on_the_row_not_only_the_hover_button():
    src = _src(PANEL)
    m = re.search(r'<li v-for="f in files".*?</li>', src, re.S)
    assert m, "the file row changed shape — re-read this test"
    row = m.group(0)
    assert 'v-if="converting[f.id]"' in row, "no row-level converting branch"
    assert "converting →" in row, "the row does not say where the file is going"
    assert "animate-spin" in row or "animate-pulse" in row, "no visible motion"
    # The progress branch must not itself be hover-gated.
    busy = row[row.index('v-if="converting[f.id]"'):row.index('v-else')]
    assert "opacity-0" not in busy and "group-hover" not in busy, (
        "the progress indicator is inside a hover-gated element again — that is "
        "the original bug: the state was tracked correctly and rendered where "
        "nobody could see it"
    )


def test_actions_are_withdrawn_while_a_row_is_converting():
    row = re.search(r'<li v-for="f in files".*?</li>', _src(PANEL), re.S).group(0)
    assert 'v-if="!converting[f.id]"' in row, (
        "the action cluster still renders mid-conversion, so a second Convert "
        "can be started on a file that is already being converted"
    )


def test_the_destination_is_remembered_for_the_duration():
    src = _src(PANEL)
    assert "convertTarget" in src, "nothing records where an in-flight conversion is going"
    body = _fn(src, "async function convertFile")
    assert "convertTarget.value = { ...convertTarget.value, [f.id]: destination }" in body
    # and cleared, or the badge lies on the next conversion
    assert "convertTarget.value = restT" in body, "convertTarget is never cleaned up"


def test_progress_text_covers_every_offered_destination():
    """A destination with no copy would render the bare fallback."""
    src = _src(PANEL)
    offered = set(re.findall(r"\{ key: '([a-z_]+)', label:", src))
    described = set(re.findall(r"^  ([a-z_]+): '", src, re.M))
    missing = offered - described
    assert not missing, f"no progress line for: {sorted(missing)}"
