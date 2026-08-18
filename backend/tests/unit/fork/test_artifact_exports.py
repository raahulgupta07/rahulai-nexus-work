"""Export availability is one rule, and the UI renders from it (defect 2).

A control must never be offered when its only outcome is an error. These lock
the rule itself and the invariant that binds it to the routes: the list the UI
renders and the gate the route enforces are the same function.

★ A DECK NOW HAS A PDF. Several tests below were written when "slides" was not
a PDF mode at all, and their original subject — the refusal itself — no longer
exists. They have been rewritten to guard the rule that replaced it rather than
deleted, and each says so in its own docstring. The deck's PDF is converted
from the saved .pptx (app.services.deck_pdf_service), so what decides it is
whether that FILE exists, not what content the artifact happens to carry.
"""

import pytest
from fastapi import HTTPException

from app.services.artifact_exports import (
    EXPORT_FORMATS,
    assert_export_supported,
    export_unavailable_reason,
    is_export_supported,
    supported_exports,
)


class FakeArtifact:
    def __init__(self, mode, content=None, status="completed", pptx_path=None, id="a1"):
        self.id = id
        self.mode = mode
        self.content = content if content is not None else {}
        self.status = status
        self.pptx_path = pptx_path


def _formats(artifact):
    return {e["format"] for e in supported_exports(artifact)}


DASHBOARD = lambda: FakeArtifact("page", {"code": "function App(){}"})
DOC = lambda: FakeArtifact("doc", {"markdown": "# Title"})
DECK = lambda: FakeArtifact("slides", {"code": "<section></section>"}, pptx_path="uploads/pptx/a.pptx")
# A deck that was never saved as a .pptx: still a deck, still carries slide
# content, but there is no file for the converter to read.
DECK_NO_FILE = lambda: FakeArtifact("slides", {"code": "<section></section>"}, pptx_path=None)


# --- the reported defect ---------------------------------------------------

def test_a_deck_with_a_saved_file_is_offered_pdf():
    """Was ``test_a_deck_is_never_offered_pdf``. That rule is gone: a .pptx
    cannot embed its fonts, so PDF is the only way to hand someone a deck that
    still looks like the deck. This is the positive control for the pair below
    — if the deck path stops working entirely, this fails first and the
    refusal tests cannot pass by accident."""
    assert "pdf" in _formats(DECK())
    assert is_export_supported(DECK(), "pdf")
    assert export_unavailable_reason(DECK(), "pdf") is None


def test_a_deck_is_refused_pdf_on_its_missing_file_not_on_its_content():
    """Was ``..._on_its_mode_not_on_missing_content``. The mode refusal it
    guarded no longer exists; this guards the rule that took its place, in the
    same shape — a deck carrying every kind of content is still refused when
    the one thing that matters, the saved .pptx, is absent.

    Without this the deck rule could decay into "has any content at all",
    which would offer PDF on a deck LibreOffice has nothing to open."""
    rich_but_fileless = FakeArtifact(
        "slides",
        {"code": "<section/>", "markdown": "# notes", "slides": [{"t": 1}]},
        pptx_path=None,
    )
    assert _formats(rich_but_fileless) == {"pptx"}
    reason = export_unavailable_reason(rich_but_fileless, "pdf")
    assert reason is not None
    # The sentence has to send the user to the right place: the deck has plenty
    # of content, so "no content yet" would be a lie they cannot act on.
    assert "powerpoint" in reason.lower()
    assert "regenerate" in reason.lower()


def test_a_failed_deck_is_refused_pdf_and_told_to_regenerate():
    """The other half of the refusal. A deck whose generation failed has no
    trustworthy render to convert, whatever its pptx_path says."""
    failed = FakeArtifact("slides", {"code": "<section/>"}, status="failed",
                          pptx_path="uploads/pptx/a.pptx")
    reason = export_unavailable_reason(failed, "pdf")
    assert reason is not None
    assert "regenerate" in reason.lower()
    assert "pdf" not in _formats(failed)


def test_a_deck_is_offered_the_exports_it_actually_has():
    """Shape change only: the set grew by one. Still an equality assertion, so
    an unrelated format leaking into the deck's list still fails."""
    assert _formats(DECK()) == {"pptx", "pdf"}
    assert _formats(DECK_NO_FILE()) == {"pptx"}


# --- the rest of the matrix ------------------------------------------------

def test_a_dashboard_is_offered_pdf_only():
    assert _formats(DASHBOARD()) == {"pdf"}


def test_a_document_is_offered_pdf_and_word():
    assert _formats(DOC()) == {"pdf", "docx"}


def test_an_unknown_mode_is_offered_nothing_rather_than_a_dead_button():
    assert _formats(FakeArtifact("something-new", {"code": "x"})) == set()


# --- state, not just mode --------------------------------------------------

def test_a_deck_whose_generation_failed_is_offered_nothing():
    deck = FakeArtifact("slides", {"code": "<section></section>"}, status="failed")
    assert _formats(deck) == set()


def test_a_dashboard_with_no_code_yet_is_offered_nothing():
    assert _formats(FakeArtifact("page", {})) == set()
    assert _formats(FakeArtifact("page", {"code": "   "})) == set()


def test_a_document_with_no_text_yet_is_offered_nothing():
    assert _formats(FakeArtifact("doc", {})) == set()
    assert _formats(FakeArtifact("doc", {"markdown": "\n  \n"})) == set()


def test_a_deck_is_offered_pptx_from_any_of_its_three_sources():
    """Unchanged intent, one line's shape adjusted: PPTX can still be built
    from a saved file, from stored slides, or from slide markup. PDF cannot —
    it is converted from the file, so only the first source produces it, and
    asserting the exact set on each line keeps that distinction guarded."""
    assert _formats(FakeArtifact("slides", {}, pptx_path="uploads/pptx/a.pptx")) == {"pptx", "pdf"}
    assert _formats(FakeArtifact("slides", {"slides": [{"title": "x"}]})) == {"pptx"}
    assert _formats(FakeArtifact("slides", {"code": "<section/>"})) == {"pptx"}
    assert _formats(FakeArtifact("slides", {})) == set()


def test_the_deck_rule_did_not_leak_into_documents_or_dashboards():
    """The deck branch keys on ``pptx_path`` and on ``status``. Neither may
    reach the doc/page path: a document with no PowerPoint file must still
    export PDF, and a document whose status is "failed" must still be judged
    on its markdown the way it always was."""
    assert is_export_supported(FakeArtifact("doc", {"markdown": "# t"}, pptx_path=None), "pdf")
    assert is_export_supported(FakeArtifact("page", {"code": "x"}, pptx_path=None), "pdf")
    assert _formats(FakeArtifact("doc", {"markdown": "# t"}, status="failed")) == {"pdf", "docx"}
    assert _formats(FakeArtifact("page", {"code": "x"}, status="failed")) == {"pdf"}
    # ...and the content checks that DO decide them are untouched.
    assert _formats(FakeArtifact("doc", {}, pptx_path="uploads/pptx/a.pptx")) == set()
    assert _formats(FakeArtifact("page", {}, pptx_path="uploads/pptx/a.pptx")) == set()


def test_non_dict_content_is_treated_as_empty_not_as_a_crash():
    assert _formats(FakeArtifact("doc", content=None)) == set()
    assert _formats(FakeArtifact("page", content="not-a-dict")) == set()


# --- the invariant that keeps UI and route in step -------------------------

@pytest.mark.parametrize("mode", ["page", "doc", "slides", "unknown"])
@pytest.mark.parametrize("status", ["completed", "failed", "pending"])
def test_offered_iff_the_route_gate_accepts(mode, status):
    artifact = FakeArtifact(
        mode,
        {"code": "x", "markdown": "y", "slides": [{"t": 1}]},
        status=status,
    )
    offered = _formats(artifact)
    for fmt in EXPORT_FORMATS:
        if fmt in offered:
            assert_export_supported(artifact, fmt)  # must not raise
        else:
            with pytest.raises(HTTPException) as exc:
                assert_export_supported(artifact, fmt)
            assert exc.value.status_code == 400


def test_refusal_explains_itself():
    """Same intent, new subject: DECK() used to be the refused case and is now
    a supported one, so the refusal half moves to a deck with no saved file.
    Both halves are kept — a reason when refused, None when not — for every
    mode, so this cannot pass by the rule collapsing in either direction."""
    reason = export_unavailable_reason(DECK_NO_FILE(), "pdf")
    assert reason and reason.strip().endswith(".")
    assert "PDF" in reason

    docx_reason = export_unavailable_reason(DASHBOARD(), "docx")
    assert docx_reason and "Word" in docx_reason

    assert export_unavailable_reason(DASHBOARD(), "pdf") is None
    assert export_unavailable_reason(DOC(), "pdf") is None
    assert export_unavailable_reason(DECK(), "pdf") is None


def test_unknown_format_is_refused_rather_than_silently_allowed():
    assert not is_export_supported(DASHBOARD(), "csv")
    with pytest.raises(HTTPException):
        assert_export_supported(DASHBOARD(), "csv")


def test_offered_entries_carry_a_usable_download_url():
    entry = next(e for e in supported_exports(DECK()) if e["format"] == "pptx")
    assert entry["url"] == "/artifacts/a1/export/pptx"
    assert entry["label"]
    assert entry["media_type"]
