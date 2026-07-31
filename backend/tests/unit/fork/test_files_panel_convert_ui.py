"""The Files panel must offer the conversion, and must not offer it wrongly.

The backend now records why each file was filed and accepts a forced
destination, but until the panel surfaces both, the state of the product is
unchanged: a badge with no explanation and no way to disagree with it. These
tests read the shipped component source, because the frontend has no test runner
here and an unreferenced backend feature is indistinguishable from an absent one.

Two things are asserted beyond "the button exists":

  * a table-backing file is NOT offered conversion. Its rows are already
    materialized, and `reingest_file` refuses it — so offering the button would
    make the refusal something the user discovers by clicking.
  * confidence is shown for a machine's verdict and NOT for the user's own
    choice. "100% confident" is a lie about who decided.
"""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[4] / "frontend"
PANEL = FRONTEND / "components" / "datasources" / "AgentFilesPanel.vue"
EXPLORER = FRONTEND / "components" / "KnowledgeExplorer.vue"


@pytest.fixture(scope="module")
def panel() -> str:
    if not PANEL.exists():
        pytest.skip(f"component not present at {PANEL}")
    return PANEL.read_text()


@pytest.fixture(scope="module")
def explorer() -> str:
    if not EXPLORER.exists():
        pytest.skip(f"component not present at {EXPLORER}")
    return EXPLORER.read_text()


# ── the reason reaches the screen ───────────────────────────────────────────

def test_the_panel_renders_the_stored_reason(panel):
    assert "intakeReason(f)" in panel, (
        "the librarian's reason is recorded and served but never rendered — the "
        "badge is still unaccountable"
    )


def test_a_file_without_a_record_shows_no_reason_line(panel):
    """Files ingested before the record existed have none. Rendering an empty
    line for them would put a blank gap under half the list."""
    assert re.search(r'v-if="intakeReason\(f\)"', panel)


def test_confidence_is_hidden_for_a_choice_the_user_made(panel):
    """A conversion is not a confident guess, it is an instruction. Showing
    "100%" beside it misattributes the decision to the model."""
    assert "decided_by === 'user'" in panel
    assert "isUserChoice" in panel


# ── the convert control ─────────────────────────────────────────────────────

def test_the_panel_offers_conversion(panel):
    assert "convertFile" in panel
    assert "reingest?" in panel or "destination=" in panel


def test_the_request_carries_a_destination(panel):
    assert "destination=${destination}" in panel


def test_the_keep_existing_option_is_wired(panel):
    """Replacement is the default because correcting a misfiling is the common
    case — but a Q&A whose definitions belong in an instruction may still want
    its full text searchable, and that must be reachable from the UI."""
    assert "keep_existing=true" in panel
    assert "keepExisting" in panel


def test_a_table_backing_file_is_not_offered_conversion(panel):
    """Its data is already materialized and the backend refuses the request.
    An offered button that always fails is worse than no button."""
    assert "canConvert" in panel
    match = re.search(r"const canConvert = \(f: any\) => (.+)", panel)
    assert match, "canConvert is not defined"
    assert "table_backing" in match.group(1)


def test_table_is_only_offered_for_tabular_files(panel):
    """Routing a .docx to a table produces nothing — DuckDB cannot read it. The
    option must not appear where it cannot work."""
    assert re.search(r"csv\|xlsx", panel)
    assert "isTabular" in panel


def test_every_destination_is_described_by_when_the_agent_sees_it(panel):
    """The words "instruction", "skill" and "knowledge" are meaningless to
    someone choosing between them. What separates the options is WHEN the agent
    reads the file, so that is what each row has to say."""
    for phrase in ("whenever it might be relevant", "when a question matches", "decides to look"):
        assert phrase in panel, f"destination descriptions no longer explain reach: {phrase!r}"


def test_the_convert_toast_reports_what_was_written(panel):
    """A conversion that reports only success cannot be told apart from one that
    quietly produced nothing."""
    assert "created" in panel


# ── delete now has a bigger consequence, and says so ────────────────────────

def test_removing_a_file_reports_that_its_table_went_too(panel):
    """Deleting a file now withdraws the table built from it. "File removed" no
    longer covers what happened."""
    assert "removed_paths" in panel


# ── the document preview ────────────────────────────────────────────────────

def test_the_preview_falls_back_to_extracted_text(explorer):
    """A .docx is not text, not an image and not a PDF, so every branch fell
    through to "No inline preview for this file type" — while the same text was
    already extracted and stored as the chunks the agent reads."""
    assert "/text" in explorer
    assert "isDocument" in explorer


def test_the_text_request_is_limited_to_formats_that_can_yield_text(explorer):
    """Not "anything that isn't an image": asking for the text of a .zip spends
    a request on every click to be told there is nothing."""
    match = re.search(r"const isDocument = \(f: any\) => (.+)", explorer)
    assert match, "isDocument is not defined"
    assert "docx" in match.group(1)


def test_an_unreadable_document_does_not_render_a_blank_panel(explorer):
    """`extractable` is what separates "we cannot read this format" from "this
    document is empty". Ignoring it shows an empty box that reads as a bug."""
    assert "extractable" in explorer


# ── the delete must ask ─────────────────────────────────────────────────────

def test_removing_a_file_asks_first(panel):
    """Regression, from real damage. This delete used to detach a file and leave
    everything it had produced in place, so a single unconfirmed click was
    survivable. Once it also withdrew the table built from the file, six clicks
    roughly a third of a second apart destroyed every file on a live agent — and
    the tables they took with them live on a different tab, so nothing on screen
    showed what had just happened.

    The confirm is what makes the new consequence visible at the moment it is
    incurred. It is not optional and has no "don't ask again".
    """
    assert "window.confirm" in panel, (
        "the Files panel deletes on a single click again — the table built from "
        "the file goes with it and nothing on this screen would show that"
    )
    assert "removalConsequence" in panel


def test_the_confirmation_names_what_is_lost(panel):
    """"Are you sure?" is not a warning. What makes this one work is naming the
    table — the thing that disappears, on a tab the user is not looking at."""
    assert "table built from this file is removed too" in panel


def test_the_wording_follows_what_the_file_actually_produced(panel):
    """A document that became knowledge has no table to lose, and telling the
    user it does would teach them the warning is boilerplate."""
    for fate in ("table_backing", "knowledge", "instruction"):
        assert fate in panel


def test_a_conversion_that_replaces_asks_first(panel):
    """Converting retires what the file produced last time. That is the right
    default — a conversion is normally a correction — but it destroys the
    passages or rules the agent is currently using, and neither is visible from
    the row being clicked. Same shape as the delete fault, so the same answer."""
    assert "conversionConsequence" in panel
    assert "currently reads from this document are replaced" in panel


def test_keeping_both_filings_does_not_warn(panel):
    """With "keep the current filing" ticked nothing is replaced, so a warning
    would be false — and a warning that fires when nothing is at stake is how
    people learn to dismiss the ones that matter."""
    src = panel[panel.index("function conversionConsequence"):]
    body = src[:src.index("async function convertFile")]
    assert "if (keepExisting.value) return null" in body


def test_a_file_with_nothing_to_replace_does_not_warn(panel):
    """A file that was never ingested has produced nothing, so converting it
    takes nothing away."""
    src = panel[panel.index("function conversionConsequence"):]
    body = src[:src.index("async function convertFile")]
    # The fall-through, for a file whose fate matched no branch above.
    assert "return null  // nothing was produced from it yet" in body
