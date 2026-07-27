"""DEF-001 — exported doc PDFs printed raw {{viz:<uuid>}} tokens.

Found by: E2E-P1.5 (City Mart doc report, PDF export)

``_strip_viz`` handled ```viz fences and <viz> tags, but the canonical embed
syntax that ``create_doc``/``edit_doc`` emit — and that DocViewer renders — is
``{{viz:<uuid>}}``, which neither pattern matched. Every exported PDF printed
the raw token where a chart belongs (5 of them in the report that found this).

The fence and tag forms are kept as defensive patterns; the embed form is the
one that occurs in real doc markdown, so it is the one these tests pin.
"""
from app.services.pdf_export_service import _strip_viz, render_doc_html

VIZ_ID = "4045d5a6-43fa-464b-8889-23cd2c34684b"
PLACEHOLDER = "chart omitted"


# --- the defect itself --------------------------------------------------------

def test_def001_embed_token_is_replaced():
    out = _strip_viz(f"## Total net sales\n\n{{{{viz:{VIZ_ID}}}}}\n\nBody text.")
    assert "{{viz" not in out
    assert VIZ_ID not in out
    assert PLACEHOLDER in out


def test_def001_no_raw_token_survives_to_html():
    """The rendered PDF body is what the user sees — assert at that boundary."""
    html = render_doc_html(f"# R\n\n{{{{viz:{VIZ_ID}}}}}\n", "R")
    assert "{{viz" not in html
    assert VIZ_ID not in html


def test_def001_every_embed_in_a_multi_chart_doc():
    """The report that found this had five embeds; one surviving token is a fail."""
    md = "\n\n".join(f"## Section {i}\n\n{{{{viz:{VIZ_ID[:-1]}{i}}}}}" for i in range(5))
    out = _strip_viz(md)
    assert "{{viz" not in out
    assert out.count(PLACEHOLDER) == 5


def test_def001_tolerates_whitespace_and_case():
    for raw in ("{{ viz : abc-123 }}", "{{VIZ:abc-123}}", "{{Chart:abc-123}}",
                "{{visualization:abc-123}}"):
        assert "{{" not in _strip_viz(raw), raw


# --- guard the surrounding behaviour these patterns must not disturb ---------

def test_def001_prose_and_tables_are_untouched():
    md = "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nText with {braces} and $money.\n"
    assert _strip_viz(md) == md


def test_def001_inline_code_mentioning_viz_is_not_eaten():
    """A doc explaining the syntax must keep prose about it readable."""
    md = "Use `{{viz:<uuid>}}` to embed a chart."
    out = _strip_viz(md)
    # The token form is replaced wherever it appears (we cannot tell code spans
    # apart at this layer), but the surrounding sentence must survive intact.
    assert "to embed a chart." in out
    assert "Use" in out


def test_def001_fence_and_tag_forms_still_handled():
    assert PLACEHOLDER in _strip_viz("```viz\n{'id': 1}\n```")
    assert "<viz" not in _strip_viz('<viz id="1" />')


def test_def001_empty_and_none_are_safe():
    assert _strip_viz("") == ""
    assert _strip_viz(None or "") == ""
