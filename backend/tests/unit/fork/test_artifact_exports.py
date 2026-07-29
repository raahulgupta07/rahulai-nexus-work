"""Export availability is one rule, and the UI renders from it (defect 2).

A control must never be offered when its only outcome is an error. These lock
the rule itself and the invariant that binds it to the routes: the list the UI
renders and the gate the route enforces are the same function.
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


# --- the reported defect ---------------------------------------------------

def test_a_deck_is_never_offered_pdf():
    assert "pdf" not in _formats(DECK())
    assert not is_export_supported(DECK(), "pdf")


def test_a_deck_is_refused_pdf_on_its_mode_not_on_missing_content():
    # A deck that happens to carry every kind of content still gets no PDF.
    # Without this the rule is only guarded by the content check, and widening
    # the mode list would silently put the dead button back.
    rich_deck = FakeArtifact(
        "slides",
        {"code": "<section/>", "markdown": "# notes", "slides": [{"t": 1}]},
        pptx_path="uploads/pptx/a.pptx",
    )
    assert _formats(rich_deck) == {"pptx"}
    assert export_unavailable_reason(rich_deck, "pdf") is not None


def test_a_deck_is_offered_the_export_it_actually_has():
    assert _formats(DECK()) == {"pptx"}


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
    assert _formats(FakeArtifact("slides", {}, pptx_path="uploads/pptx/a.pptx")) == {"pptx"}
    assert _formats(FakeArtifact("slides", {"slides": [{"title": "x"}]})) == {"pptx"}
    assert _formats(FakeArtifact("slides", {"code": "<section/>"})) == {"pptx"}
    assert _formats(FakeArtifact("slides", {})) == set()


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
    reason = export_unavailable_reason(DECK(), "pdf")
    assert reason and "PDF" in reason
    assert export_unavailable_reason(DASHBOARD(), "pdf") is None


def test_unknown_format_is_refused_rather_than_silently_allowed():
    assert not is_export_supported(DASHBOARD(), "csv")
    with pytest.raises(HTTPException):
        assert_export_supported(DASHBOARD(), "csv")


def test_offered_entries_carry_a_usable_download_url():
    entry = next(e for e in supported_exports(DECK()) if e["format"] == "pptx")
    assert entry["url"] == "/artifacts/a1/export/pptx"
    assert entry["label"]
    assert entry["media_type"]
