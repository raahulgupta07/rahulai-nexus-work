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

import base64
import html as _html
import re
from typing import Any, Dict, Optional

from markdown_it import MarkdownIt
from app.core.render_sandbox import block_external_requests, launch_chromium


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
/* Keep a data cell's text on ONE line. A wrapped cell still *renders*, but the
   PDF text layer then holds "Ocean - Yangon\\n74" instead of "Ocean - Yangon 74",
   so the value is no longer extractable (or copyable) as itself and the reading
   order interleaves columns. Headers may still wrap — they carry no data and
   wrapping them buys most of the width back. `_autofit_tables` below shrinks or,
   as a last resort, un-does the nowrap for any table that cannot fit. */
td { border-bottom: 1px solid #f3f4f6; padding: 0.45em 0.65em; vertical-align: top; white-space: nowrap; }
table.bow-wrap td { white-space: normal; }
tr:last-child td { border-bottom: none; }
h1, h2, h3, h4 { page-break-after: avoid; }
img { max-width: 100%; }
.viz-placeholder { border: 1px dashed #cbd5e1; border-radius: 8px; padding: 0.9em 1em; margin: 0.8em 0; color: #64748b; font-size: 11px; background: #f8fafc; }
table.bow-tight th, table.bow-tight td { padding: 0.3em 0.35em; }
/* Embedded visualizations. `break-inside: avoid` keeps a chart and its caption
   on one page — a chart split across a page break reads as two broken charts. */
figure.bow-viz { margin: 1.1em 0; page-break-inside: avoid; break-inside: avoid; text-align: center; }
figure.bow-viz img { max-width: 100%; height: auto; }
figure.bow-viz table { margin: 0 auto; }
.bow-viz-cap { font-size: 10px; font-style: italic; color: #64748b; margin-top: 0.4em; text-align: center; }
"""

# A4 (210mm) minus the left+right print margins declared in `_CSS` / `page.pdf`.
# Chromium lays the page out at this CSS-px width, so measuring the table at any
# other viewport width would fit it to the wrong box.
PAGE_CONTENT_WIDTH_PX = round((210 - 16 * 2) / 25.4 * 96, 2)  # 672.76

# Steps the auto-fit walks, largest first. 11px is the normal table size; below
# ~7px a table stops being readable, which is why the last resort is to wrap
# again rather than keep shrinking.
_TABLE_FONT_STEPS = (11, 10.5, 10, 9.5, 9, 8.5, 8, 7.5, 7)

_AUTOFIT_JS = """
(avail) => {
  const steps = %(steps)s;
  const results = [];
  document.querySelectorAll('table').forEach((t) => {
    const width = () => Math.max(t.scrollWidth, Math.ceil(t.getBoundingClientRect().width));
    let fitted = null;
    for (const tight of [false, true]) {
      t.classList.toggle('bow-tight', tight);
      for (const size of steps) {
        t.style.fontSize = size + 'px';
        if (width() <= avail + 0.5) { fitted = { size, tight }; break; }
      }
      if (fitted) break;
    }
    if (!fitted) {
      // Cannot fit on one line per cell at a readable size: restore wrapping
      // (exactly today's output) rather than clip the table off the page.
      t.style.fontSize = '';
      t.classList.remove('bow-tight');
      t.classList.add('bow-wrap');
      results.push({ fitted: false });
    } else {
      results.push({ fitted: true, size: fitted.size, tight: fitted.tight });
    }
  });
  return results;
}
""" % {"steps": list(_TABLE_FONT_STEPS)}


async def _autofit_tables(page) -> list:
    """Shrink each table until every data cell fits on one line.

    Returns the per-table outcome (also handy for tests/diagnostics). Never
    raises — a failed auto-fit must not cost the user their PDF.
    """
    try:
        await page.emulate_media(media="print")
        await page.set_viewport_size(
            {"width": int(PAGE_CONTENT_WIDTH_PX), "height": 1000}
        )
        return await page.evaluate(_AUTOFIT_JS, PAGE_CONTENT_WIDTH_PX)
    except Exception:  # pragma: no cover - defensive
        return []


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
# Same sentence, already rendered — used when stage two cannot resolve an asset
# and there is no markdown pass left to run.
_VIZ_PLACEHOLDER_HTML = (
    "<blockquote><p><em>[chart omitted — view in app or use browser print "
    "for the live chart]</em></p></blockquote>"
)


# Captures the uuid so an asset can be looked up. `_VIZ_EMBED` deliberately
# does not — it is the "throw it away" pattern and is still used when no assets
# were resolved.
_VIZ_EMBED_ID = re.compile(r"\{\{\s*(?:viz|chart|visualization)\s*:\s*([0-9a-fA-F-]{8,64})\s*\}\}")

_MAX_PDF_TABLE_ROWS = 12
_MAX_PDF_TABLE_COLS = 6


def _strip_viz(markdown_text: str) -> str:
    text = _VIZ_FENCE.sub(_VIZ_PLACEHOLDER, markdown_text)
    text = _VIZ_EMBED.sub(_VIZ_PLACEHOLDER, text)
    text = _VIZ_TAG.sub("", text)
    return text


def _asset_html(asset: Dict[str, Any]) -> str:
    """One resolved visualization as print-ready HTML, or '' if it has nothing."""
    title = _html.escape(str(asset.get("title") or ""))
    caption = f"<div class='bow-viz-cap'>{title}</div>" if title else ""

    png = asset.get("image_png")
    if png:
        b64 = base64.b64encode(png).decode("ascii")
        # Inline data URI, never a URL: the page is loaded with set_content and
        # has no origin, so an external reference would silently fail to load
        # and print as a broken image.
        return (
            f"<figure class='bow-viz'>"
            f"<img src='data:image/png;base64,{b64}' alt='{title}'/>"
            f"{caption}</figure>"
        )

    table = asset.get("table") or {}
    rows = table.get("rows") or []
    if not rows:
        return ""

    fields, headers = [], []
    for col in table.get("columns") or []:
        if isinstance(col, dict):
            field = col.get("field") or col.get("headerName")
            if not field:
                continue
            fields.append(str(field))
            headers.append(str(col.get("headerName") or field))
        elif col:
            fields.append(str(col))
            headers.append(str(col))
    if not fields:
        first = rows[0]
        if not isinstance(first, dict):
            return ""
        fields = [str(k) for k in first.keys()]
        headers = list(fields)

    total_rows, total_cols = len(rows), len(fields)
    body_rows = rows[:_MAX_PDF_TABLE_ROWS]

    # Same rule the Word export uses: a page holds few columns, so spend them on
    # columns that carry data for the rows actually shown.
    def _has_data(field: str) -> bool:
        return any(
            isinstance(r, dict) and r.get(field) is not None and str(r.get(field)).strip() != ""
            for r in body_rows
        )

    kept = [i for i, f in enumerate(fields) if _has_data(f)] or list(range(total_cols))
    shown_fields = [fields[i] for i in kept][:_MAX_PDF_TABLE_COLS]
    shown_headers = [headers[i] for i in kept][:_MAX_PDF_TABLE_COLS]

    head = "".join(f"<th>{_html.escape(h)}</th>" for h in shown_headers)
    body = "".join(
        "<tr>" + "".join(
            f"<td>{_html.escape('' if (v := (r.get(f) if isinstance(r, dict) else None)) is None else str(v))}</td>"
            for f in shown_fields
        ) + "</tr>"
        for r in body_rows
    )

    notes = []
    if total_rows > len(body_rows):
        notes.append(f"{len(body_rows)} of {total_rows} rows")
    if total_cols > len(shown_fields):
        notes.append(f"{len(shown_fields)} of {total_cols} columns")
    note = (
        f"<div class='bow-viz-cap'>Preview — showing {' and '.join(notes)}. "
        "Full data in the report.</div>"
        if notes else ""
    )
    return (
        f"<figure class='bow-viz'><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table>{caption}{note}</figure>"
    )


# ★ The markdown renderer runs with `html: False`, and that stays that way — a
# doc's markdown is model-written, and turning on raw HTML for everything to
# smuggle in a chart would let anything else through with it. So the embed is
# done in TWO stages: swap each placeholder for a plain-text sentinel that
# survives markdown untouched, render, then replace the rendered sentinel with
# our own generated HTML. The only HTML injected is the HTML this file built.
_VIZ_SENTINEL = "bowvizembedtoken"
_SENTINEL_TAG = re.compile(
    rf"<p>\s*{_VIZ_SENTINEL}([0-9a-f-]{{8,64}})\s*</p>", re.IGNORECASE
)


def _substitute_viz(markdown_text: str, viz_assets: Optional[Dict[str, Any]]) -> str:
    """Swap each ``{{viz:<uuid>}}`` for a sentinel the renderer leaves alone.

    With no assets this collapses to `_strip_viz`, so the classic path is
    byte-identical.
    """
    if not viz_assets:
        return _strip_viz(markdown_text)

    def repl(m):
        viz_id = (m.group(1) or "").lower()
        if viz_id not in viz_assets:
            return _VIZ_PLACEHOLDER
        # Own paragraph, or markdown folds it into the surrounding text and the
        # sentinel never appears as a standalone <p> for stage two to find.
        return f"\n\n{_VIZ_SENTINEL}{viz_id}\n\n"

    text = _VIZ_FENCE.sub(_VIZ_PLACEHOLDER, markdown_text or "")
    text = _VIZ_EMBED_ID.sub(repl, text)
    text = _VIZ_EMBED.sub(_VIZ_PLACEHOLDER, text)   # any embed the id form missed
    text = _VIZ_TAG.sub("", text)
    return text


def _inject_viz_html(body_html: str, viz_assets: Optional[Dict[str, Any]]) -> str:
    """Stage two: rendered sentinel -> the asset's own HTML."""
    if not viz_assets:
        return body_html

    def repl(m):
        html = _asset_html(viz_assets.get((m.group(1) or "").lower()) or {})
        return html or _VIZ_PLACEHOLDER_HTML

    return _SENTINEL_TAG.sub(repl, body_html)


def render_doc_html(
    markdown_text: str,
    title: str,
    viz_assets: Optional[Dict[str, Any]] = None,
) -> str:
    """Markdown -> standalone print-ready HTML string (also handy for tests)."""
    body = _build_md().render(_substitute_viz(markdown_text or "", viz_assets))
    body = _inject_viz_html(body, viz_assets)
    safe_title = _html.escape(title or "document")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title><style>{_CSS}</style></head>"
        f"<body><div class='doc'>{body}</div></body></html>"
    )


async def render_doc_pdf(
    markdown_text: str,
    title: str,
    viz_assets: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Render doc markdown to PDF bytes via Playwright's bundled Chromium.

    `viz_assets` is what `doc_viz_render.collect_doc_viz_assets` returns — the
    same resolved charts the Word export already embeds. Omit it and the PDF
    prints the historic "chart omitted" placeholder, unchanged.
    """
    page_html = render_doc_html(markdown_text, title, viz_assets=viz_assets)

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await launch_chromium(p, args=["--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(
                viewport={"width": int(PAGE_CONTENT_WIDTH_PX), "height": 1000}
            )
            # ★No network from a page running model-written code — see
            # app/core/render_sandbox: this is the SSRF fix, and it means a
            # remote image or font simply does not appear in the output.
            await block_external_requests(page)
            await page.set_content(page_html, wait_until="networkidle")
            await _autofit_tables(page)
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
            )
        finally:
            await browser.close()
    return pdf_bytes
