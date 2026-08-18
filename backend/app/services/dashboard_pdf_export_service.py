"""PDF export for `page` (dashboard) artifacts.

A dashboard is React/JSX rendered in the artifact sandbox against
`window.ARTIFACT_DATA`. There is no second pipeline here: this reuses the exact
HTML that `create_artifact` already builds for its headless preview/thumbnail
render (`CreateArtifactTool._build_thumbnail_html`, which inlines the vendored
React/Babel/ECharts/Tailwind bundles), renders it in the Playwright Chromium
that already ships in the image for the doc PDF export, and prints it.

Because it is the same HTML the thumbnail render uses, a dashboard that renders
in the app renders here — including ECharts canvases, which are captured as
drawn rather than re-created for print.

Fidelity note: the export is a snapshot of the dashboard's *initial* state.
Interactive filters are rendered at their default selection.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.artifact import Artifact
from app.models.query import Query
from app.core.render_sandbox import block_external_requests, launch_chromium
from app.services.artifact_data import resolve_artifact_rows

logger = logging.getLogger(__name__)


_MARGIN_MM = 8

# Printed at 75%: a dashboard laid out at ~1400px is a desktop-width layout (so
# the model's `lg:`/`xl:` grid breakpoints apply, as they do in the app) and a
# 400px-tall chart card no longer eats a whole A4 page.
PDF_SCALE = 0.75

# A4 landscape content box, in the CSS pixels the page is laid out in. Chromium
# lays the printed page out at paper-size/scale, NOT at the browser viewport, so
# the viewport has to be set to exactly this or ECharts sizes its canvases for a
# width the PDF never uses (chart resize is driven by a ResizeObserver, which
# does not run again during PDF capture).
PAGE_CONTENT_WIDTH_PX = int(round((297 - _MARGIN_MM * 2) / 25.4 * 96 / PDF_SCALE))   # 1416
PAGE_CONTENT_HEIGHT_PX = int(round((210 - _MARGIN_MM * 2) / 25.4 * 96 / PDF_SCALE))  # 977
_VIEWPORT_HEIGHT_PX = 1400

_RENDER_TIMEOUT_MS = 20000

_PRINT_CSS = """
  @page { size: A4 landscape; margin: %(m)dmm; }
  html, body { height: auto !important; background: #fff !important; }
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
  .bow-pdf-keep { break-inside: avoid; page-break-inside: avoid; }
""" % {"m": _MARGIN_MM}

# Keep a card whole across a page break — but only the *innermost* cards, and
# only ones short enough that honouring it is cheap. Marking every rounded
# container (including the section wrapper around a row of cards) leaves most of
# each page blank, because the wrapper is too tall to fit in what is left.
_KEEP_TOGETHER_JS = """
(maxHeight) => {
  const sel = '.rounded-2xl, .rounded-xl, .rounded-lg';
  let kept = 0;
  document.querySelectorAll(sel).forEach((el) => {
    if (el.querySelector(sel)) return;            // not innermost
    if (el.offsetHeight > maxHeight) return;      // cannot fit on a page anyway
    el.classList.add('bow-pdf-keep');
    kept++;
  });
  return kept;
}
"""


async def build_dashboard_artifact_data(
    db: AsyncSession,
    artifact: Artifact,
) -> Dict[str, Any]:
    """Assemble the `window.ARTIFACT_DATA` payload for a stored artifact.

    Same shape the live frontend posts into the sandbox iframe (and the same
    shape `create_artifact` injects for its headless render): report meta plus
    one entry per visualization with its rows/columns.
    """
    # Fetch the report explicitly rather than touching `artifact.report`: the
    # artifact handed to us may come from a session where that relationship was
    # not eager-loaded, and a lazy load on an async session raises.
    from app.models.report import Report

    report = (
        await db.execute(select(Report).where(Report.id == str(artifact.report_id)))
    ).scalar_one_or_none()
    content = artifact.content or {}
    viz_ids = [str(v) for v in (content.get("visualization_ids") or [])]

    result = await db.execute(
        select(Query)
        .options(
            selectinload(Query.default_step),
            selectinload(Query.steps),
            selectinload(Query.visualizations),
        )
        .where(Query.report_id == str(artifact.report_id))
        .execution_options(populate_existing=True)
    )

    visualizations: List[Dict[str, Any]] = []
    for query in result.scalars().all():
        step = query.default_step
        if not step and query.steps:
            step = query.steps[-1]
        step_data = (step.data or {}) if step else {}
        # DEF-010: resolved through the SAME accessor the build and the browser
        # use. This used to be its own `rows_artifact or rows` expression, and an
        # artifact printed here summed 1,200 rows while the same artifact on
        # screen summed a 1,000-row prefix of them.
        resolved = resolve_artifact_rows(step_data)
        rows = resolved.rows

        for viz in (query.visualizations or []):
            if viz_ids and str(viz.id) not in viz_ids:
                continue
            visualizations.append({
                "id": str(viz.id),
                "title": viz.title or query.title or "Untitled",
                "view": viz.view or {},
                "rows": rows,
                "columns": resolved.columns,
                # Any cap or aggregation applied to what is printed, in words.
                **({"dataNotice": resolved.notice()} if resolved.notice() else {}),
                "dataModel": (step.data_model or {}) if step else {},
                "stepStatus": step.status if step else None,
            })

    data: Dict[str, Any] = {
        "report": {
            "id": str(report.id) if report else str(artifact.report_id),
            "title": getattr(report, "title", None) if report else None,
            "theme": getattr(report, "theme", None) if report else None,
        },
        "visualizations": visualizations,
    }

    # Embedded images/PDFs shown via <BowFile>. Best-effort: a dashboard without
    # them is the common case, and a file that will not resolve must not cost
    # the user the export.
    stored_files = content.get("files")
    if stored_files:
        try:
            from app.ai.tools.implementations.create_artifact import CreateArtifactTool
            data["files"] = await CreateArtifactTool()._build_file_datauris(db, stored_files)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Dashboard PDF: could not inline embedded files: {e}")

    return data


def build_dashboard_html(
    artifact_data: Dict[str, Any],
    code: str,
    insights: Optional[Dict[str, Any]] = None,
) -> str:
    """Standalone sandbox HTML for the dashboard (reuses create_artifact's builder).

    ★`insights` is threaded through because THIS is the path a dashboard PDF
    actually takes — export/pdf -> render_dashboard_pdf -> here -> the document
    in create_artifact.py. Wiring the narrative into report_pdf_service and
    thumbnail_service was not enough: the exported PDF still came out with the
    dashboard's conclusion missing, and nothing errored.
    """
    from app.ai.tools.implementations.create_artifact import CreateArtifactTool

    return CreateArtifactTool()._build_thumbnail_html(
        artifact_data, code, mode="page", insights=insights
    )


async def render_dashboard_pdf(
    artifact_data: Dict[str, Any],
    code: str,
    title: Optional[str] = None,
    insights: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Render a dashboard artifact to PDF bytes via Playwright's Chromium."""
    html = build_dashboard_html(artifact_data, code, insights=insights)

    from playwright.async_api import async_playwright

    tmp = tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    )
    tmp.write(html)
    tmp.close()

    try:
        async with async_playwright() as p:
            browser = await launch_chromium(p, args=["--disable-dev-shm-usage"])
            try:
                page = await browser.new_page(
                    viewport={
                        "width": PAGE_CONTENT_WIDTH_PX,
                        "height": _VIEWPORT_HEIGHT_PX,
                    }
                )
                # ★No network from a page running model-written code — see
                # app/core/render_sandbox: this is the SSRF fix, and it means a
                # remote image or font simply does not appear in the output.
                await block_external_requests(page)
                # file:// (not set_content): the vendored Tailwind runtime uses
                # document.write(), which fails on the about:blank page
                # set_content() creates. Same reason create_artifact does this.
                await page.goto(f"file://{tmp.name}", wait_until="networkidle")
                try:
                    await page.wait_for_function(
                        "window.__ARTIFACT_RENDER_COMPLETE__ === true",
                        timeout=_RENDER_TIMEOUT_MS,
                    )
                except Exception:
                    logger.warning(
                        "Dashboard PDF: render-complete signal timed out; "
                        "printing what has rendered"
                    )

                await page.add_style_tag(content=_PRINT_CSS)
                try:
                    await page.evaluate(
                        _KEEP_TOGETHER_JS, int(PAGE_CONTENT_HEIGHT_PX * 0.7)
                    )
                except Exception:  # pragma: no cover - cosmetic only
                    pass
                # Charts size themselves from the container; nudge them once the
                # print stylesheet has been applied.
                try:
                    await page.evaluate(
                        "() => { window.resizeAllCharts && window.resizeAllCharts(); }"
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(400)

                return await page.pdf(
                    format="A4",
                    landscape=True,
                    scale=PDF_SCALE,
                    print_background=True,
                    margin={
                        "top": f"{_MARGIN_MM}mm",
                        "bottom": f"{_MARGIN_MM}mm",
                        "left": f"{_MARGIN_MM}mm",
                        "right": f"{_MARGIN_MM}mm",
                    },
                )
            finally:
                await browser.close()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:  # pragma: no cover
            pass
