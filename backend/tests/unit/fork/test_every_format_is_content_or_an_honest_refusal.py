"""Every file format ends in content or in a refusal that says so.

The two outcomes this forbids are the two that cost real money:

  SILENT GARBAGE  something comes back that is not the file's content. Measured
                  2026-08-03: `pd.read_csv` on a real `.rtf` returned a 157-row
                  frame of control words, with no exception, and generated code
                  had been handed that file with no reader named.
  CRASH           an exception escapes a reader and reaches the user as a stack
                  trace instead of a sentence.

The corpus in `tests/fixtures/formats/` is 32 REAL files — LibreOffice and the
app's own libraries produced them, so `.doc` is a binary Word document and
`.xls` an OLE2 BIFF8 workbook. A renamed `.txt` would only ever measure the
extension check, which is the thing most likely to be wrong.

Four fixtures are adversarial, each defeating a different guard: an image-only
PDF, a 10-character docx (under `MIN_USABLE_DOC_CHARS`), a PDF with every
ToUnicode CMap stripped, and a truncated docx.

★Deliberately does NOT render anything. `render_file_images` shells out to
LibreOffice for Office formats — seconds per file — and this suite runs 2000+
tests in under a minute. Rendering behaviour is covered where that cost is
affordable; what belongs here is the classification, which is where the defect
was.

★Read-only, no schema — `tests/unit/fork`. See CLAUDE.md.
"""
from pathlib import Path

import pytest

from app.data_sources.clients._document_text import (
    doc_text_is_usable,
    doc_text_looks_garbled,
    extract_document_text,
)
from app.services.file_formats import (
    loadable_in_code,
    readable_by_read_file,
    reader_for,
    refusal_for,
    refused_in_code,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "formats"

# The string every text-bearing fixture carries. An extractor that returns
# *something* without this is producing garbage, not content — which is the
# distinction the whole file is about.
MARK = "ALPHA-7731"

ALL_FIXTURES = sorted(p.name for p in FIXTURES.iterdir() if p.is_file())


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def test_the_corpus_is_present_and_complete():
    """A silently empty fixture directory would turn every test below into a
    no-op that passes."""
    assert len(ALL_FIXTURES) == 32, f"corpus is {len(ALL_FIXTURES)} files, expected 32"
    for name in ("sample.rtf", "sample.doc", "sample.xls", "sample.parquet",
                 "adv_corrupt.docx", "adv_garbled.pdf", "adv_imageonly.pdf",
                 "adv_oneline.docx"):
        assert name in ALL_FIXTURES, f"{name} is missing — it covers a specific defect"
        assert (FIXTURES / name).stat().st_size > 0


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_format_is_offered_to_generated_code_without_a_reader(name):
    """★The defect itself. Every fixture must either name one concrete reader
    or be refused — the state in between is where the model guessed."""
    ext = _ext(name)
    if loadable_in_code(ext):
        call = reader_for(ext, 0)
        assert call and "excel_files[0]" in call
    else:
        assert refused_in_code(ext), (
            f".{ext} has no reader and is not refused — generated code will be "
            f"handed {name} with nothing telling it what to do"
        )
        assert refusal_for(ext, "abc")


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_a_refusal_points_somewhere_true(name):
    """A refusal that names `read_file` for a file `read_file` cannot open is
    worse than no advice: it costs a turn and ends in a second failure."""
    ext = _ext(name)
    if not refused_in_code(ext):
        return
    msg = refusal_for(ext, "abc")
    if readable_by_read_file(ext):
        assert "read_file" in msg
    else:
        assert "read_file" not in msg, f"{name} sent to a tool that also cannot open it"


@pytest.mark.parametrize("name", ["sample.pdf", "sample.docx", "sample.pptx"])
def test_the_document_formats_return_their_actual_content(name):
    """Not merely "some text" — the marker. An extractor returning the file's
    XML scaffolding would pass a length check and fail this."""
    text = extract_document_text(str(FIXTURES / name), name)
    assert MARK in text, f"{name} extracted {len(text)} chars without the marker"
    assert doc_text_is_usable(text, _ext(name))
    assert not doc_text_looks_garbled(text)
    for tag in ("<w:", "<a:", "<p:"):
        assert tag not in text, f"{name} leaked {tag} markup into its text"


def test_a_short_document_is_not_mistaken_for_a_failed_read():
    """★`MIN_USABLE_DOC_CHARS` is 16 and exists for scanned PDFs. OOXML
    extraction is exact, so a short result means a short document — applying the
    floor there discarded a correct read of a one-line memo and handed the
    caller bytes it had no way to recover from."""
    text = extract_document_text(str(FIXTURES / "adv_oneline.docx"), "adv_oneline.docx")
    assert 0 < len(text.strip()) < 16
    assert doc_text_is_usable(text, "docx"), "the OOXML carve-out is gone"
    # Without the extension the conservative PDF-style floor still applies.
    assert not doc_text_is_usable(text)


def test_glyph_soup_is_detected_rather_than_reported_as_text():
    """A subset-font PDF with no ToUnicode map renders perfectly and extracts as
    symbol salad. Length cannot catch it — hundreds of chars come back."""
    text = extract_document_text(str(FIXTURES / "adv_garbled.pdf"), "adv_garbled.pdf")
    assert text, "nothing extracted — this fixture must produce text to be a garble test"
    assert doc_text_looks_garbled(text), (
        "garbled text is being reported as a faithful read"
    )


def test_a_scanned_page_extracts_nothing_and_says_nothing_untrue():
    """An image-only PDF has no text layer. The honest answer is empty, not a
    stray glyph presented as the document."""
    text = extract_document_text(str(FIXTURES / "adv_imageonly.pdf"), "adv_imageonly.pdf")
    assert not doc_text_is_usable(text, "pdf")


def test_a_corrupt_document_returns_empty_instead_of_raising():
    """★The extractor is called while scanning whole directories, so one bad
    file must not end the scan. It returns "" — and because "" is
    indistinguishable from an unsupported format, `read_file` now says which it
    is rather than reporting a successful read of nothing."""
    text = extract_document_text(str(FIXTURES / "adv_corrupt.docx"), "adv_corrupt.docx")
    assert text == ""


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_no_fixture_makes_the_extractor_raise(name):
    """Every format, including the ones it does not handle. `extract_document_text`
    is the chokepoint for search over mixed directories."""
    assert isinstance(extract_document_text(str(FIXTURES / name), name), str)


@pytest.mark.parametrize("name", ["sample.rtf", "sample.eml", "sample.yaml",
                                  "sample.html", "sample.xml"])
def test_the_five_that_returned_a_frame_of_nonsense(name):
    """Named individually so a regression reports which one came back.

    Four are plain text and now read as text. `.rtf` is markup LibreOffice can
    lay out, so it goes to `read_file` — giving it `read_text` would hand the
    model control words, the same defect in a politer form.
    """
    ext = _ext(name)
    if ext == "rtf":
        assert refused_in_code(ext) and readable_by_read_file(ext)
    else:
        call = reader_for(ext, 0)
        assert call and call.startswith("read_text("), (
            f".{ext} is read with `{call}` — a DataFrame here is the defect back"
        )
