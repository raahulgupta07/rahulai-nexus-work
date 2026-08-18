"""A deck can be delivered as a PDF, and says why when it cannot.

THE DEFECT
----------
`GET /artifacts/{id}/export/pdf` answered 400 for every deck, and because the
toolbar renders from the same rule the button was never drawn at all. There was
no way to get a deck out of the product as a PDF.

WHY IT MATTERS
--------------
A .pptx does not carry its fonts — python-pptx has no mechanism for embedding
them. A deck built on one of our design systems and opened by a recipient who
does not have the typeface is silently re-set in a substitute face, which is
the whole visual identity gone. PDF is the only format that embeds what it
draws with, so it is the fidelity escape hatch, and it did not exist.

WHAT IS LOCKED HERE
-------------------
1. A finished deck is offered PDF, and the offer is honest — a failed deck, or
   one that never saved a .pptx, is refused with a sentence that says what to
   do about it.
2. `doc` and `page` behaviour is UNCHANGED. This is the important guard: the
   change widens one modes tuple that three artifact types read, and a
   regression there is invisible until someone tries to export a document.
3. The route actually branches on mode — a deck goes to the LibreOffice path
   and a document does not.

LibreOffice is never invoked: subprocess.run is stubbed throughout.
"""

import asyncio
import subprocess

import pytest
from fastapi import HTTPException

from app.services import deck_pdf_service
from app.services.artifact_exports import (
    EXPORT_FORMATS,
    assert_export_supported,
    export_unavailable_reason,
    is_export_supported,
    supported_exports,
)
from app.services.deck_pdf_service import (
    DeckPdfError,
    DeckPdfRendererUnavailable,
    render_deck_pdf,
)


class FakeArtifact:
    def __init__(self, mode, content=None, status="completed", pptx_path=None, id="a1"):
        self.id = id
        self.mode = mode
        self.content = content if content is not None else {}
        self.status = status
        self.pptx_path = pptx_path
        self.title = "Q4 review"
        self.report_id = "r1"


def _formats(artifact):
    return {e["format"] for e in supported_exports(artifact)}


def DECK(**kw):
    kw.setdefault("pptx_path", "uploads/pptx/a.pptx")
    return FakeArtifact("slides", {"code": "<section></section>"}, **kw)


DOC = lambda: FakeArtifact("doc", {"markdown": "# Title"})
DASHBOARD = lambda: FakeArtifact("page", {"code": "function App(){}"})


# --- 1. the deck is offered PDF, honestly ----------------------------------

def test_a_finished_deck_is_offered_pdf():
    assert is_export_supported(DECK(), "pdf")
    assert export_unavailable_reason(DECK(), "pdf") is None


def test_the_toolbar_offers_a_deck_both_powerpoint_and_pdf():
    """supported_exports() is what the UI renders its buttons from, so this is
    the assertion that the button appears at all."""
    assert _formats(DECK()) == {"pptx", "pdf"}
    entry = next(e for e in supported_exports(DECK()) if e["format"] == "pdf")
    assert entry["url"] == "/artifacts/a1/export/pdf"
    assert entry["media_type"] == "application/pdf"
    assert entry["label"] == "PDF"


def test_a_failed_deck_is_not_offered_pdf_and_says_why():
    deck = DECK(status="failed")
    reason = export_unavailable_reason(deck, "pdf")
    assert reason is not None
    assert "regenerate" in reason.lower()
    assert "pdf" not in _formats(deck)


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_deck_with_no_pptx_file_is_not_offered_pdf_and_says_why(missing):
    """The PDF is converted from the saved .pptx. A deck without one has
    nothing to convert, and the reason must point at the deck rather than at
    'no content' — the legacy slides/code sources are still present, so a
    generic message would send the user looking in the wrong place."""
    deck = FakeArtifact(
        "slides",
        {"code": "<section/>", "slides": [{"t": 1}]},
        pptx_path=missing,
    )
    reason = export_unavailable_reason(deck, "pdf")
    assert reason is not None
    assert "powerpoint" in reason.lower()
    assert "pdf" not in _formats(deck)
    # ...while PPTX itself is still offered from those legacy sources, so this
    # is a PDF-specific refusal and not the deck going dark.
    assert "pptx" in _formats(deck)


def test_the_deck_refusal_is_the_same_string_the_route_returns():
    deck = DECK(status="failed")
    with pytest.raises(HTTPException) as exc:
        assert_export_supported(deck, "pdf")
    assert exc.value.status_code == 400
    assert exc.value.detail == export_unavailable_reason(deck, "pdf")


# --- 2. the regression guard: doc and page are untouched -------------------

def test_a_document_still_offers_exactly_pdf_and_word():
    assert _formats(DOC()) == {"pdf", "docx"}


def test_a_dashboard_still_offers_exactly_pdf():
    assert _formats(DASHBOARD()) == {"pdf"}


def test_a_document_pdf_still_depends_on_markdown_not_on_a_pptx_path():
    """The deck branch keys on pptx_path. If it leaked into the doc/page path,
    a document would start being refused for having no PowerPoint file."""
    assert _formats(FakeArtifact("doc", {})) == set()
    assert _formats(FakeArtifact("doc", {"markdown": "\n \n"})) == set()
    assert is_export_supported(FakeArtifact("doc", {"markdown": "x"}, pptx_path=None), "pdf")


def test_a_dashboard_pdf_still_depends_on_code():
    assert _formats(FakeArtifact("page", {})) == set()
    assert _formats(FakeArtifact("page", {"code": "   "})) == set()


def test_a_failed_document_or_dashboard_is_judged_the_way_it_always_was():
    """Only decks read `status`; the deck rule must not have spread."""
    assert _formats(FakeArtifact("doc", {"markdown": "# t"}, status="failed")) == {"pdf", "docx"}
    assert _formats(FakeArtifact("page", {"code": "x"}, status="failed")) == {"pdf"}


def test_an_unknown_mode_is_still_offered_nothing():
    assert _formats(FakeArtifact("something-new", {"code": "x"}, pptx_path="uploads/a.pptx")) == set()


@pytest.mark.parametrize("mode", ["page", "doc", "slides", "unknown"])
@pytest.mark.parametrize("status", ["completed", "failed", "pending"])
def test_offered_iff_the_route_gate_accepts(mode, status):
    """The invariant that keeps the button and the gate in step."""
    artifact = FakeArtifact(
        mode,
        {"code": "x", "markdown": "y", "slides": [{"t": 1}]},
        status=status,
        pptx_path="uploads/pptx/a.pptx",
    )
    offered = _formats(artifact)
    for fmt in EXPORT_FORMATS:
        if fmt in offered:
            assert_export_supported(artifact, fmt)  # must not raise
        else:
            with pytest.raises(HTTPException) as exc:
                assert_export_supported(artifact, fmt)
            assert exc.value.status_code == 400


# --- 3. the converter ------------------------------------------------------

def _stub_soffice(monkeypatch, *, returncode=0, stderr="", write_pdf=b"%PDF-1.4 deck", raises=None):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if raises is not None:
            raise raises
        outdir = cmd[cmd.index("--outdir") + 1]
        if write_pdf is not None:
            from pathlib import Path

            (Path(outdir) / "deck.pdf").write_bytes(write_pdf)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(deck_pdf_service.subprocess, "run", fake_run)
    return calls


@pytest.fixture()
def saved_pptx(tmp_path):
    p = tmp_path / "deck.pptx"
    p.write_bytes(b"PK\x03\x04not-really-a-deck")
    return p


def test_a_saved_deck_converts_to_pdf_bytes(monkeypatch, saved_pptx):
    calls = _stub_soffice(monkeypatch)
    assert render_deck_pdf(saved_pptx) == b"%PDF-1.4 deck"

    cmd, kwargs = calls[0]
    assert cmd[:4] == ["soffice", "--headless", "--convert-to", "pdf"]
    assert cmd[-1] == str(saved_pptx)
    assert kwargs["capture_output"] and kwargs["timeout"]


def test_the_temp_directory_does_not_survive_the_call(monkeypatch, saved_pptx):
    seen = {}

    def fake_run(cmd, **kwargs):
        from pathlib import Path

        outdir = Path(cmd[cmd.index("--outdir") + 1])
        seen["outdir"] = outdir
        (outdir / "deck.pdf").write_bytes(b"%PDF-1.4 deck")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(deck_pdf_service.subprocess, "run", fake_run)
    render_deck_pdf(saved_pptx)
    assert not seen["outdir"].exists()


def test_a_missing_converter_is_its_own_error(monkeypatch, saved_pptx):
    """The operator has to install something; the user did nothing wrong and
    regenerating the deck will not help. The route needs to tell those apart."""
    _stub_soffice(monkeypatch, raises=FileNotFoundError("soffice"))
    with pytest.raises(DeckPdfRendererUnavailable) as exc:
        render_deck_pdf(saved_pptx)
    assert "powerpoint" in exc.value.message.lower()  # names the fallback
    assert "libreoffice" in (exc.value.detail or "").lower()


def test_a_failed_conversion_is_reported_without_leaking_stderr(monkeypatch, saved_pptx):
    _stub_soffice(monkeypatch, returncode=1, stderr="Fatal: /root/.config denied")
    with pytest.raises(DeckPdfError) as exc:
        render_deck_pdf(saved_pptx)
    assert "/root/.config" not in exc.value.message
    assert "/root/.config" in exc.value.detail


def test_a_conversion_that_wrote_nothing_is_a_failure_not_empty_bytes(monkeypatch, saved_pptx):
    _stub_soffice(monkeypatch, returncode=0, write_pdf=None)
    with pytest.raises(DeckPdfError):
        render_deck_pdf(saved_pptx)


def test_an_empty_pdf_is_a_failure(monkeypatch, saved_pptx):
    _stub_soffice(monkeypatch, returncode=0, write_pdf=b"")
    with pytest.raises(DeckPdfError):
        render_deck_pdf(saved_pptx)


def test_a_timeout_is_a_typed_error(monkeypatch, saved_pptx):
    _stub_soffice(monkeypatch, raises=subprocess.TimeoutExpired("soffice", 120))
    with pytest.raises(DeckPdfError):
        render_deck_pdf(saved_pptx)


def test_a_missing_file_never_reaches_the_converter(monkeypatch, tmp_path):
    calls = _stub_soffice(monkeypatch)
    with pytest.raises(DeckPdfError):
        render_deck_pdf(tmp_path / "gone.pptx")
    assert calls == []


# --- 4. the route branches on mode -----------------------------------------

def _pdf_route():
    from app.routes import artifact as artifact_routes

    # Unwrap the permission decorator: this test is about which renderer the
    # body picks, and the gate itself is covered by the route-gating suite.
    return artifact_routes, artifact_routes.export_artifact_pdf.__wrapped__


class _FakeUser:
    id = "u1"
    is_verified = True


class _FakeOrg:
    id = "o1"


def _run_route(monkeypatch, artifact, deck_result=b"%PDF-1.4 deck"):
    """Call export_artifact_pdf's body and record which renderer it chose."""
    routes, route = _pdf_route()
    chosen = []

    async def fake_get(db, artifact_id):
        return artifact

    async def fake_deck(a):
        chosen.append("deck")
        if isinstance(deck_result, Exception):
            raise deck_result
        return deck_result

    async def fake_doc(markdown, title, viz_assets=None):
        chosen.append("doc")
        return b"%PDF-1.4 doc"

    async def fake_guard(db, a, user):
        chosen.append("guard")

    async def fake_assets(db, md, report_id):
        return {}

    monkeypatch.setattr(routes.service, "get", fake_get)
    monkeypatch.setattr(routes, "_render_deck_pdf_from_artifact", fake_deck)
    monkeypatch.setattr(routes, "_guard_rendered_artifact_for_viewer", fake_guard)

    import app.services.doc_viz_render as doc_viz_render
    import app.services.pdf_export_service as pdf_export_service

    monkeypatch.setattr(pdf_export_service, "render_doc_pdf", fake_doc)
    monkeypatch.setattr(doc_viz_render, "collect_doc_viz_assets", fake_assets)

    from app.ee.audit.service import audit_service

    async def fake_log(**kwargs):
        return None

    monkeypatch.setattr(audit_service, "log", fake_log)

    response = asyncio.run(
        route(
            artifact_id="a1",
            request=None,
            current_user=_FakeUser(),
            organization=_FakeOrg(),
            db=object(),
        )
    )
    return chosen, response


def test_a_deck_goes_to_the_libreoffice_path(monkeypatch):
    chosen, response = _run_route(monkeypatch, DECK())
    assert "deck" in chosen and "doc" not in chosen
    assert response.media_type == "application/pdf"


def test_a_deck_pdf_inherits_the_viewer_gate_the_pptx_download_has(monkeypatch):
    """The PDF carries the same baked snapshot as the .pptx, so it must not be
    a way around the gate that protects it."""
    chosen, _ = _run_route(monkeypatch, DECK())
    assert chosen.index("guard") < chosen.index("deck")


def test_a_document_still_goes_to_the_document_renderer(monkeypatch):
    chosen, _ = _run_route(monkeypatch, DOC())
    assert chosen == ["doc"], "the doc path must not touch the deck path or the deck guard"


def test_a_failed_deck_conversion_reaches_the_user_as_its_own_message(monkeypatch):
    err = DeckPdfError("This deck could not be converted to PDF.", detail="soffice exit 1")
    with pytest.raises(HTTPException) as exc:
        _run_route(monkeypatch, DECK(), deck_result=err)
    assert exc.value.status_code == 500
    assert exc.value.detail == "This deck could not be converted to PDF."
    assert "soffice" not in exc.value.detail


# --- 5. the toolbar actually offers it -------------------------------------
#
# ★ These read ArtifactFrame.vue as TEXT. They cannot mount the component and
# they cannot prove a button renders in a browser — they prove the binding is
# present and correctly conditioned. The rendered control is verified by hand
# in the running app.
#
# They exist because the backend half of this change is otherwise a value the
# API serves that no consumer reads: `supported_exports()` returning "pdf" for
# a deck does nothing on its own, because this toolbar gates each PDF button on
# a hardcoded mode check IN ADDITION to canExport(). Without a slides branch
# here, the export is reachable only by typing the URL.

from tests.unit.fork.vue_source import read_source

ARTIFACT_FRAME = "components/dashboard/ArtifactFrame.vue"


@pytest.fixture(scope="module")
def artifact_frame() -> str:
    return read_source(ARTIFACT_FRAME)


def test_the_toolbar_has_a_slides_pdf_button(artifact_frame):
    assert "selectedArtifact?.mode === 'slides' && canExport('pdf')" in artifact_frame, (
        "no slides PDF control in the toolbar — the backend offers pdf for a "
        "deck and nothing renders it"
    )


def test_the_slides_pdf_button_calls_the_pdf_export(artifact_frame):
    """Guard the guard: a condition with no handler behind it is still a dead
    control. The block must reach exportDocPdf, which is the function that hits
    /artifacts/{id}/export/pdf."""
    start = artifact_frame.index("selectedArtifact?.mode === 'slides' && canExport('pdf')")
    block = artifact_frame[start:start + 800]
    end = block.index("</UTooltip>")
    assert '@click="exportDocPdf"' in block[:end]


def test_no_pdf_button_is_guarded_by_canexport_alone(artifact_frame):
    """canExport() returns true when the /exports fetch failed — by design, so
    a transient error does not hide a working control. That makes it a veto,
    never a source, so every PDF button must carry a mode condition as well.

    The doc button takes its mode condition from the <template> that wraps it
    rather than from its own v-if, so this enumerates the three PDF controls
    explicitly and then pins the count: a fourth one added without a mode guard
    fails here rather than slipping through a looser pattern.
    """
    same_attribute = [
        "selectedArtifact?.mode === 'page' && canExport('pdf')",
        "selectedArtifact?.mode === 'slides' && canExport('pdf')",
    ]
    for condition in same_attribute:
        assert condition in artifact_frame

    # The doc control: wrapped in the isDocMode template, with no other
    # canExport('pdf') between the wrapper and the button.
    wrapper = artifact_frame.index('<template v-if="isDocMode && !isEditingDoc">')
    doc_block = artifact_frame[wrapper:artifact_frame.index("</template>", wrapper)]
    assert doc_block.count("canExport('pdf')") == 1

    assert artifact_frame.count("canExport('pdf')") == 3, (
        "a PDF control was added or removed — check that the new one is "
        "guarded on mode and not on canExport alone"
    )


def test_the_existing_dashboard_and_pptx_buttons_are_unchanged(artifact_frame):
    assert "selectedArtifact?.mode === 'page' && canExport('pdf')" in artifact_frame
    assert "selectedArtifact?.mode === 'slides' && canExport('pptx')" in artifact_frame


def test_the_servers_reason_reaches_the_user_on_a_deck(artifact_frame):
    """The route returns a written sentence (converter missing vs conversion
    failed). A deck has no browser print fallback, so if the toolbar replaced
    that with a generic message the typed errors would be unreadable."""
    assert "serverDetail" in artifact_frame
    assert "selectedArtifact.value?.mode === 'slides' && typeof error?.serverDetail" in artifact_frame


def test_the_docx_export_was_not_touched(artifact_frame):
    """The PDF and Word downloads share their shape; a careless edit to one
    lands in the other. Word must still throw the plain error it always did."""
    docx_start = artifact_frame.index("/export/docx")
    docx_block = artifact_frame[docx_start:docx_start + 900]
    assert "serverDetail" not in docx_block
    assert "if (!response.ok) throw new Error(" in docx_block
