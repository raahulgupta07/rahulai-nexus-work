"""PDF export for `doc` artifacts.

Renders the doc's markdown to a clean, print-ready HTML page (typography
mirroring the frontend DocViewer) and prints it to PDF with the headless
Chromium that ships in the image via Playwright. No external dependency is
added — `markdown-it-py` and `playwright` are already installed.

Fidelity note: text, headings, bold/italic, lists, blockquotes, code and
tables render faithfully. Embedded live visualizations (chart blocks) are a
frontend-only render; they are shown as a labelled placeholder here. For a
chart-perfect capture, the browser print path (Ctrl-P / the repaired print
stylesheet) is still available.
"""
from __future__ import annotations

import html as _html
import re

from markdown_it import MarkdownIt


# Typography deliberately mirrors DocViewer.vue `.bow-doc-md` so the PDF looks
# like the on-screen document (compact, document-scale, neutral palette).
_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  font-size: 12px; line-height: 1.6; color: #1f2937; -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.doc { max-width: 720px; margin: 0 auto; }
h1 { font-size: 22px; font-weight: 700; margin: 0 0 0.5em; color: #111827; }
h2 { font-size: 17px; font-weight: 700; margin: 1.4em 0 0.4em; color: #111827; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.2em; }
h3 { font-size: 14px; font-weight: 700; margin: 1.1em 0 0.3em; color: #111827; }
h4, h5, h6 { font-size: 12.5px; font-weight: 700; margin: 1em 0 0.3em; }
p { margin: 0 0 0.7em; }
strong { font-weight: 700; color: #111827; }
ul, ol { margin: 0 0 0.8em; padding-left: 1.4em; }
li { margin: 0.15em 0; }
a { color: #2563eb; text-decoration: none; border-bottom: 1px solid #bfdbfe; }
blockquote { margin: 0.6em 0; padding: 0.2em 0.9em; border-left: 3px solid #d1d5db; color: #4b5563; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; background: #f3f4f6; padding: 0.1em 0.35em; border-radius: 4px; }
pre { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 0.8em 1em; overflow-x: auto; }
pre code { background: none; padding: 0; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 1.2em 0; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0 1em; font-size: 11px; page-break-inside: avoid; }
th { text-align: left; font-weight: 600; color: #374151; background: #f9fafb; border-bottom: 2px solid #e5e7eb; padding: 0.45em 0.65em; }
td { border-bottom: 1px solid #f3f4f6; padding: 0.45em 0.65em; vertical-align: top; }
tr:last-child td { border-bottom: none; }
h1, h2, h3, h4 { page-break-after: avoid; }
img { max-width: 100%; }
.viz-placeholder { border: 1px dashed #cbd5e1; border-radius: 8px; padding: 0.9em 1em; margin: 0.8em 0; color: #64748b; font-size: 11px; background: #f8fafc; }
"""


def _build_md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "linkify": True}).enable("table")


# Chart/visualization blocks in doc markdown are frontend-only. Replace every
# form with a labelled placeholder so the PDF reads cleanly instead of dumping
# raw markers.
#
# _VIZ_EMBED is the one that actually matters: {{viz:<uuid>}} is the canonical
# embed syntax that create_doc/edit_doc emit and DocViewer renders. Without it
# every exported PDF printed the raw token (DEF-001) — the fence and tag forms
# below are defensive, and neither of them ever matched real doc markdown.
_VIZ_EMBED = re.compile(r"\{\{\s*(?:viz|chart|visualization)\s*:[^}]*\}\}", re.IGNORECASE)
_VIZ_FENCE = re.compile(r"```(?:viz|chart|visualization)[^\n]*\n.*?```", re.DOTALL | re.IGNORECASE)
_VIZ_TAG = re.compile(r"<\s*(?:viz|visualization|chart)\b[^>]*>", re.IGNORECASE)

_VIZ_PLACEHOLDER = "\n\n> _[chart omitted — view in app or use browser print for the live chart]_\n\n"


def _strip_viz(markdown_text: str) -> str:
    text = _VIZ_FENCE.sub(_VIZ_PLACEHOLDER, markdown_text)
    text = _VIZ_EMBED.sub(_VIZ_PLACEHOLDER, text)
    text = _VIZ_TAG.sub("", text)
    return text


def render_doc_html(markdown_text: str, title: str) -> str:
    """Markdown -> standalone print-ready HTML string (also handy for tests)."""
    body = _build_md().render(_strip_viz(markdown_text or ""))
    safe_title = _html.escape(title or "document")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title><style>{_CSS}</style></head>"
        f"<body><div class='doc'>{body}</div></body></html>"
    )


async def render_doc_pdf(markdown_text: str, title: str) -> bytes:
    """Render doc markdown to PDF bytes via Playwright's bundled Chromium."""
    page_html = render_doc_html(markdown_text, title)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            page = await browser.new_page()
            await page.set_content(page_html, wait_until="networkidle")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            )
        finally:
            await browser.close()
    return pdf_bytes
