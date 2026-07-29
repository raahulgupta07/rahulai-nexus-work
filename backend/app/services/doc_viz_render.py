"""Resolve and render ``{{viz:<uuid>}}`` embeds of a `doc` artifact.

A doc's charts are a frontend-only render: the markdown carries nothing but a
uuid, and the live chart is drawn by ECharts in the browser. For a Word export
that is not enough — the file has to carry the picture itself.

This module closes that gap:
  1. `collect_doc_viz_assets` resolves each embedded uuid to its visualization
     and the default step's rows/columns/data_model (the same payload
     `ArtifactFrame.fetchData` builds for the viewer).
  2. Chart-type visualizations are drawn headlessly with the SAME vendored
     ECharts build the app ships and the SAME option code
     `artifact_codegen.generate_echart_option_code` produces for dashboards,
     then screenshotted to PNG.
  3. Table / metric visualizations come back as data instead of a picture, so
     the exporter can emit a native Word table.

Everything degrades: any failure returns fewer assets, never an exception, so
an export never fails because a chart could not be drawn.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai.tools.implementations._doc_markdown import extract_viz_placeholders
from app.services.artifact_codegen import generate_echart_option_code
from app.core.render_sandbox import block_external_requests, launch_chromium

logger = logging.getLogger(__name__)

# Mirrors DocVizEmbed.vue CHART_TYPES — what the viewer draws as a chart.
CHART_TYPES = {
    "pie_chart", "line_chart", "bar_chart", "area_chart", "heatmap",
    "scatter_plot", "map", "candlestick", "treemap", "radar_chart",
}

# Vendored libs are downloaded at image-build time; `dist` is what the running
# app actually serves, `public` is the dev tree. Checked in order.
_LIB_DIRS = [
    Path(__file__).parent.parent.parent.parent / "frontend" / "dist" / "libs",
    Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "libs",
    Path(__file__).parent.parent.parent.parent / "frontend" / ".output" / "public" / "libs",
]
_ECHARTS_FILENAME = "echarts-5.min.js"

_CHART_WIDTH = 900
_CHART_HEIGHT = 480
# Rows beyond this make a chart unreadable and the render slow; the app's own
# artifact row cap is the same order of magnitude.
_MAX_CHART_ROWS = 5000


def _find_echarts() -> Optional[Path]:
    for d in _LIB_DIRS:
        candidate = d / _ECHARTS_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _viz_type(view: Any, data_model: Dict[str, Any]) -> str:
    """Resolve a viz's render type the way DocVizEmbed.vue does."""
    t = None
    if isinstance(view, dict):
        inner = view.get("view")
        if isinstance(inner, dict):
            t = inner.get("type")
        t = t or view.get("type")
    t = t or (data_model or {}).get("type")
    return str(t or "table").lower()


async def collect_doc_viz_payloads(db, markdown_text: str, report_id: Optional[str]) -> List[Dict[str, Any]]:
    """Resolve embedded viz uuids to {id, title, rows, columns, data_model, view}."""
    viz_ids = extract_viz_placeholders(markdown_text or "")
    if not viz_ids:
        return []

    from app.models.query import Query
    from app.models.visualization import Visualization

    result = await db.execute(
        select(Visualization)
        .options(selectinload(Visualization.query).selectinload(Query.default_step))
        .where(Visualization.id.in_(viz_ids))
    )
    fetched = {str(v.id).lower(): v for v in result.scalars().all()}

    payloads: List[Dict[str, Any]] = []
    for viz_id in viz_ids:
        viz = fetched.get(viz_id.lower())
        if viz is None:
            continue
        if report_id and str(viz.report_id) != str(report_id):
            continue
        step = viz.query.default_step if viz.query else None
        if step is None:
            continue
        data = step.data or {}
        payloads.append({
            "id": str(viz.id).lower(),
            "title": viz.title or (viz.query.title if viz.query else None) or step.title or "",
            "rows": (data.get("rows") or [])[:_MAX_CHART_ROWS],
            "columns": data.get("columns") or [],
            "data_model": step.data_model or {},
            "view": viz.view or {},
        })
    return payloads


def _build_render_script(chart_specs: List[Dict[str, Any]]) -> str:
    """JS that evaluates each option expression and draws it into its own div.

    Each option expression is wrapped in its own try/catch: one bad data_model
    must not cost the other charts on the page.
    """
    viz_json = json.dumps(
        [{"rows": spec["rows"]} for spec in chart_specs], default=str
    )
    pushes = "\n".join(
        f"  try {{ opts.push({spec['option_code']}); }} catch (e) {{ opts.push(null); }}"
        for spec in chart_specs
    )
    # ★ `containLabel` is why the axis labels survive. ECharts sizes the plot
    # grid FIRST and draws axis labels outside it, so long rotated category
    # labels ("Ocean - Yangon 74") run past the container and are cut off by the
    # element screenshot — the picture came out reading "an - Yangon 74". In the
    # app this never shows, because the chart lives in a scrollable panel that
    # is wider than the export page. Set here rather than in
    # `generate_echart_option_code`, which dashboards share: this is a fact
    # about rendering to a fixed-size image, not about the chart.
    fit_labels = (
        "    var g = Object.assign({}, opt.grid || {});\n"
        "    g.containLabel = true;\n"
        "    opt.grid = g;\n"
    )
    return (
        "(function () {\n"
        f"  var viz = {viz_json};\n"
        "  var opts = [];\n"
        f"{pushes}\n"
        "  window.__chartOk = opts.map(function (opt, i) {\n"
        "    var el = document.getElementById('viz-' + i);\n"
        "    if (!opt || !el) return false;\n"
        "    try {\n"
        "      var chart = echarts.init(el, null, { renderer: 'canvas' });\n"
        "      opt.animation = false;\n"
        "      opt.backgroundColor = '#ffffff';\n"
        f"{fit_labels}"
        "      chart.setOption(opt);\n"
        "      return true;\n"
        "    } catch (e) { return false; }\n"
        "  });\n"
        "})();"
    )


async def _render_chart_pngs(chart_specs: List[Dict[str, Any]]) -> Dict[str, bytes]:
    """Draw each chart spec headlessly and screenshot it to PNG bytes."""
    echarts_path = _find_echarts()
    if echarts_path is None:
        logger.warning("doc viz render: vendored %s not found — charts skipped", _ECHARTS_FILENAME)
        return {}

    divs = "".join(
        f"<div id='viz-{i}' style='width:{_CHART_WIDTH}px;height:{_CHART_HEIGHT}px;background:#fff'></div>"
        for i in range(len(chart_specs))
    )
    page_html = (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body style='margin:0;background:#fff'>{divs}</body></html>"
    )

    from playwright.async_api import async_playwright

    images: Dict[str, bytes] = {}
    async with async_playwright() as p:
        browser = await launch_chromium(p, args=["--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(
                viewport={"width": _CHART_WIDTH + 40, "height": _CHART_HEIGHT + 40},
                device_scale_factor=2,
            )
            # ★No network from a page running model-written code — see
            # app/core/render_sandbox: this is the SSRF fix, and it means a
            # remote image or font simply does not appear in the output.
            await block_external_requests(page)
            await page.set_content(page_html)
            # ★ add_script_tag(path=...) inlines the file. A <script src="file://">
            # inside set_content is silently blocked by Chromium — the page loads,
            # `echarts` never defines, and every chart comes out missing.
            await page.add_script_tag(path=str(echarts_path))
            await page.add_script_tag(content=_build_render_script(chart_specs))
            ok_flags = await page.evaluate("window.__chartOk || []")
            for i, spec in enumerate(chart_specs):
                if not (i < len(ok_flags) and ok_flags[i]):
                    continue
                element = await page.query_selector(f"#viz-{i}")
                if element is None:
                    continue
                images[spec["id"]] = await element.screenshot(type="png")
        finally:
            await browser.close()
    return images


async def collect_doc_viz_assets(db, markdown_text: str, report_id: Optional[str]) -> Dict[str, Any]:
    """Resolve every ``{{viz:<uuid>}}`` embed to a docx-ready asset.

    Returns {viz_id: {"title", "image_png", "table"}}. Never raises.
    """
    try:
        payloads = await collect_doc_viz_payloads(db, markdown_text, report_id)
    except Exception:
        logger.exception("doc viz render: failed to resolve visualizations")
        return {}
    if not payloads:
        return {}

    chart_specs: List[Dict[str, Any]] = []
    assets: Dict[str, Any] = {}
    for payload in payloads:
        assets[payload["id"]] = {
            "title": payload["title"],
            "image_png": None,
            "table": {"columns": payload["columns"], "rows": payload["rows"]},
        }
        if _viz_type(payload["view"], payload["data_model"]) not in CHART_TYPES:
            continue
        if not payload["rows"]:
            continue
        try:
            option_code = generate_echart_option_code(payload["data_model"], len(chart_specs))
        except Exception:
            logger.exception("doc viz render: option codegen failed for %s", payload["id"])
            continue
        if not option_code or option_code.strip() == "{}":
            # Codegen could not read this data_model — the table asset stands in.
            continue
        chart_specs.append({
            "id": payload["id"],
            "rows": payload["rows"],
            "option_code": option_code,
        })

    if chart_specs:
        try:
            for viz_id, png in (await _render_chart_pngs(chart_specs)).items():
                assets[viz_id]["image_png"] = png
        except Exception:
            logger.exception("doc viz render: headless chart render failed")

    return assets
