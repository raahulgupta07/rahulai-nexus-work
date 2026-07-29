"""PDF export regression tests.

The defect these lock down: a wide markdown table in a `doc` artifact rendered
its data cells wrapped, so a store name like "Ocean - Yangon 74" ended up in the
PDF's text layer as "Ocean - Yangon\\n74". It was visible on the page, so a
screenshot looked correct — but the value was no longer extractable, copyable or
searchable as itself, while the unwrapped number beside it survived. That
asymmetry (revenue extractable, name not) is exactly what a text-layer assertion
catches and an eyeball does not.

These tests are pure rendering: no DB, no app boot. Playwright + Chromium and
pdfminer already ship in the image (doc PDF export and PDF ingestion use them).
"""
import asyncio
import io

import pytest

from app.services.pdf_export_service import (
    PAGE_CONTENT_WIDTH_PX,
    render_doc_html,
    render_doc_pdf,
)


# Same shape as the artifact that produced the bug: 8 columns, a multi-word
# name column, and grouped numbers wide enough to squeeze it.
WIDE_TABLE_MD = """# Q4 review

## Top 10 stores — Q4 2025

| Rank | Store | Banner | City | Region | Q4 2025 net revenue (MMK) | Q4 2024 net revenue (MMK) | YoY growth |
|---|---|---|---|---|---|---|---|
| 1 | Ocean - Yangon 74 | Ocean | Yangon | Lower Myanmar | 17,726,384 | 16,886,812 | +5.0% |
| 2 | Ocean - Yangon 72 | Ocean | Yangon | Lower Myanmar | 17,599,257 | 16,777,192 | +4.9% |
| 6 | City Mart - Yangon 63 | City Mart | Yangon | Lower Myanmar | 10,500,000 | 8,000,000 | +32.0% |
| 9 | City Mart - Monywa 51 | City Mart | Monywa | Upper Myanmar | 9,800,000 | 10,300,000 | -5.0% |
"""

STORE_NAMES = [
    "Ocean - Yangon 74",
    "Ocean - Yangon 72",
    "City Mart - Yangon 63",
    "City Mart - Monywa 51",
]


def _pdf_text(pdf_bytes: bytes) -> str:
    from pdfminer.high_level import extract_text

    return extract_text(io.BytesIO(pdf_bytes))


@pytest.fixture(scope="module")
def wide_table_pdf_text() -> str:
    pdf = asyncio.run(render_doc_pdf(WIDE_TABLE_MD, "Q4 review"))
    assert pdf[:5] == b"%PDF-", "render_doc_pdf did not return a PDF"
    return _pdf_text(pdf)


@pytest.mark.parametrize("name", STORE_NAMES)
def test_store_name_survives_as_one_string(wide_table_pdf_text, name):
    """A data cell must not be broken across lines in the PDF text layer."""
    assert name in wide_table_pdf_text, (
        f"{name!r} is not extractable from the exported PDF. It is probably "
        f"wrapped mid-cell (e.g. 'Ocean - Yangon\\n74'), which renders but "
        f"destroys the value in the text layer."
    )


def test_numbers_still_extractable(wide_table_pdf_text):
    """Guard the guard: if the numbers vanished too, the render broke entirely
    and the name assertions above would be meaningless."""
    for number in ("17,726,384", "16,886,812", "10,500,000"):
        assert number in wide_table_pdf_text


def test_every_row_is_present(wide_table_pdf_text):
    """The column must not be dropped — the other failure mode for the same
    symptom is a cell that renders blank."""
    assert wide_table_pdf_text.count("Lower Myanmar") >= 3
    assert "Upper Myanmar" in wide_table_pdf_text


def test_data_cells_declare_nowrap_and_headers_do_not():
    """The CSS contract the auto-fit builds on: data cells hold one line;
    headers may wrap (they carry no value and wrapping them buys the width)."""
    html = render_doc_html(WIDE_TABLE_MD, "t")
    assert "td { border-bottom" in html and "white-space: nowrap" in html
    th_rule = html.split("th { text-align: left;")[1].split("}")[0]
    assert "nowrap" not in th_rule


def test_page_content_width_matches_a4_margins():
    """The auto-fit measures at the width Chromium prints at. If the print
    margins in _CSS/page.pdf change and this does not, tables are fitted to the
    wrong box and silently wrap again."""
    assert 670 < PAGE_CONTENT_WIDTH_PX < 676


def _vendored_libs_available() -> bool:
    """The artifact sandbox libs are downloaded during the Docker build and are
    gitignored, so a bare checkout does not have them."""
    from app.services import artifact_libs

    d = artifact_libs._find_libs_dir()
    return bool(d and (d / "tailwindcss-3.4.16.js").is_file())


DASHBOARD_CODE = """
function App() {
  return <div className="p-8"><h1 className="text-2xl">Ocean - Yangon 74</h1>
    <p>Revenue 17,726,384</p></div>;
}
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
"""


def test_dashboard_page_geometry_is_consistent():
    """Dashboards print scaled, so the sandbox must be laid out at
    paper-size/scale — not at the paper size. If these drift apart, ECharts
    canvases are sized for a width the PDF never uses."""
    from app.services import dashboard_pdf_export_service as d

    assert 0 < d.PDF_SCALE <= 1
    printed_width_px = d.PAGE_CONTENT_WIDTH_PX * d.PDF_SCALE
    assert 1058 < printed_width_px < 1066  # A4 landscape minus 8mm margins
    assert d.PAGE_CONTENT_WIDTH_PX >= 1024  # desktop breakpoints must apply


@pytest.mark.skipif(
    not _vendored_libs_available(),
    reason="vendored artifact sandbox libs are build-time artifacts",
)
def test_dashboard_renders_to_pdf():
    """A `page` artifact must export to a real PDF — it used to 400 with
    'Only document artifacts can be exported to PDF'."""
    from app.services.dashboard_pdf_export_service import (
        build_dashboard_html,
        render_dashboard_pdf,
    )

    data = {"report": {"id": "r1", "title": "T"}, "visualizations": []}
    html = build_dashboard_html(data, DASHBOARD_CODE)
    assert "ARTIFACT_DATA" in html and "Ocean - Yangon 74" in html

    pdf = asyncio.run(render_dashboard_pdf(data, DASHBOARD_CODE, "dash"))
    assert pdf[:5] == b"%PDF-"
    text = _pdf_text(pdf)
    assert "Ocean - Yangon 74" in text
    assert "17,726,384" in text


def test_narrow_table_is_untouched():
    """A table that already fits must not be shrunk or re-wrapped."""
    md = "| A | B |\n|---|---|\n| one | two |\n"
    pdf = asyncio.run(render_doc_pdf(md, "narrow"))
    text = _pdf_text(pdf)
    assert "one" in text and "two" in text


def test_uncroppable_table_falls_back_to_wrapping():
    """A table that cannot fit one-line cells at a readable size must degrade to
    wrapping (the old behaviour) and still render every column — never clip the
    table off the page edge. Contiguous cell text is deliberately NOT asserted
    here: giving it up is the trade the fallback makes."""
    n = 12
    cells = " | ".join(f"Very Long Value Number {i}" for i in range(n))
    header = " | ".join(f"Column Heading {i}" for i in range(n))
    sep = "|".join("---" for _ in range(n))
    md = f"| {header} |\n|{sep}|\n| {cells} |\n"
    text = _pdf_text(asyncio.run(render_doc_pdf(md, "huge")))
    assert text.count("Value") == n, "a column was clipped off the page"
    assert text.count("Heading") == n
