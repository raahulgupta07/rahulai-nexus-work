"""A Word document has to be readable past the first page of it.

Before this, it was not. A docx read came back with its text clipped at
`max_chars` and `truncated=true`; the tool then appended, for every file type,
"page the rest with windowed reads (offset/length)". For a docx that reached
the byte-window branch — which sits above the document branch — and returned
the raw bytes of a ZIP container. The other paging route, `page_range`, is
PDF-only and refused. The model, told twice by the tool to make the one call
that could not work, gave up on the document and answered without it.

Two things are pinned here: paging a document counts CHARACTERS of its
extracted text, and the advice a truncated read gives names a route that exists
for that file type.
"""

import pytest

from app.ai.tools.implementations.read_file import _content_details, _how_to_get_the_rest
from app.data_sources.clients.network_dir_client import NetworkDirClient


# ── the advice string ────────────────────────────────────────────────────────


def test_a_word_document_is_told_to_page_by_character():
    advice = _how_to_get_the_rest("CRM Agent Q&A Logic.docx")

    assert "offset/length" in advice
    assert "CHARACTERS" in advice
    assert "next_cursor" in advice


def test_a_pdf_is_told_about_page_range_first():
    """It has a better route than character offsets, so lead with it."""
    advice = _how_to_get_the_rest("contract.pdf")

    assert advice.index("page_range") < advice.index("offset/length")


def test_a_powerpoint_pages_by_character_too():
    assert "CHARACTERS" in _how_to_get_the_rest("deck.pptx")


def test_a_log_file_still_pages_by_byte():
    """Raw files are unchanged — the window into them IS the bytes."""
    advice = _how_to_get_the_rest("server.log")

    assert "bytes" in advice
    assert "CHARACTERS" not in advice


def test_a_file_with_no_extension_gets_the_byte_advice():
    """The safe default: byte paging works on anything that is not a container."""
    assert "bytes" in _how_to_get_the_rest("dump")
    assert "bytes" in _how_to_get_the_rest("")


def test_the_advice_reaches_the_model_through_the_details_trailer():
    """`_content_details` is what the planner actually sees."""
    output = {
        "text": "x" * 500,
        "truncated": True,
        "file_name": "CRM Agent Q&A Logic.docx",
    }

    details = _content_details(output, max_chars=100)

    assert "CHARACTERS" in details
    assert details.startswith("x" * 100)


def test_an_untruncated_read_gets_no_paging_advice():
    """Telling the model to page a file it has all of wastes a turn."""
    details = _content_details({"text": "short", "file_name": "a.docx"}, max_chars=100)

    assert details == "short"


# ── the document window ──────────────────────────────────────────────────────


class _FakeDoc:
    """A NetworkDirClient with the extractor swapped for a known string."""

    def __init__(self, monkeypatch, text, name="notes.docx"):
        import app.data_sources.clients.network_dir_client as mod

        self.client = NetworkDirClient(root_path="/tmp")
        self.name = name
        monkeypatch.setattr(
            mod, "extract_document_text",
            lambda path, fname=None, max_chars=None: text[:max_chars] if max_chars else text,
        )
        monkeypatch.setattr(mod, "doc_text_is_usable", lambda t, ext: bool(t))

        class _Stat:
            st_size = 1024

        class _Path:
            name = self.name

            def stat(self_inner):
                return _Stat()

        self.path = _Path()

    def window(self, offset, length=None):
        return self.client._read_document_window(self.path, offset, length)


def test_a_document_pages_from_start_to_eof(monkeypatch):
    """The whole point: read it all, in pages, and know when you are done."""
    doc = _FakeDoc(monkeypatch, "abcdefghij")

    first = doc.window(0, 4)
    assert first["content"] == "abcd"
    assert first["unit"] == "characters"
    assert first["eof"] is False
    assert first["next_cursor"] == 4

    second = doc.window(first["next_cursor"], 4)
    assert second["content"] == "efgh"
    assert second["next_cursor"] == 8

    last = doc.window(second["next_cursor"], 4)
    assert last["content"] == "ij"
    assert last["eof"] is True
    assert last["next_cursor"] is None
    assert last["total_size"] == 10


def test_the_total_is_not_claimed_before_the_end_is_reached(monkeypatch):
    """★Only one page is extracted per call, so the full length is genuinely
    unknown until eof. A guess would be read as fact by the model and by the
    progress line — and the extractor's own 200,000-char cap means a guess
    derived from "extract everything" would be wrong on exactly the long
    documents this feature is for."""
    doc = _FakeDoc(monkeypatch, "abcdefghij")

    assert doc.window(0, 4)["total_size"] is None
    assert doc.window(8, 4)["total_size"] == 10


def test_a_document_longer_than_the_extractor_cap_does_not_report_eof(monkeypatch):
    """The regression this method exists to avoid, one layer down: measuring
    the document by extracting all of it would hit DEFAULT_MAX_CHARS and call
    that the end."""
    doc = _FakeDoc(monkeypatch, "z" * 500_000)

    page = doc.window(199_000, 2_000)

    assert page["eof"] is False
    assert page["next_cursor"] == 201_000


def test_a_negative_offset_is_refused(monkeypatch):
    doc = _FakeDoc(monkeypatch, "abc")

    with pytest.raises(ValueError):
        doc.window(-1)


def test_a_scanned_document_says_so_instead_of_returning_nothing(monkeypatch):
    """An empty window reads as "done" to the model — it would summarise a
    document it never saw a word of."""
    import app.data_sources.clients.network_dir_client as mod

    doc = _FakeDoc(monkeypatch, "")
    monkeypatch.setattr(mod, "doc_text_is_usable", lambda t, ext: False)

    with pytest.raises(ValueError, match="scanned or"):
        doc.window(0)


def test_reading_past_the_end_returns_empty_and_eof(monkeypatch):
    """Not an error: a cursor that overshoots must terminate the loop."""
    doc = _FakeDoc(monkeypatch, "abc")

    page = doc.window(99, 10)

    assert page["content"] == ""
    assert page["eof"] is True
