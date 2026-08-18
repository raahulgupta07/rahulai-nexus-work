"""The "What this means" section, rendered into an artifact document.

★This exists once. The same section has to appear in every document a dashboard
becomes — the in-app frame, the shared report page, the PDF export and the card
thumbnail — and those are assembled in six different places. Two of them are
Python. If each wrote its own copy they would drift, exactly as the library
lists did before frontend/public/libs/manifest.json: pdf.min.js was loaded by
one renderer out of six and nothing ever errored, the pages simply came out
different.

The counterpart is `insightsSection()` in frontend/utils/artifactIframe.ts.
The two must produce the same markup and the same CSS; that agreement is
asserted by tests/unit/fork/test_every_artifact_renderer_agrees.py.

★The narrative is composed here rather than written into the artifact's own
generated code, for two reasons. Dashboards created before insights existed
still get one, because the document is rebuilt on every render. And the figures
have already been verified server-side — `rejected_count` counts the findings
dropped for citing a number absent from the data — so handing them back to a
model to restate would only give them a chance to go wrong again.
"""
from __future__ import annotations

import html
from typing import Any, Optional

# ★Scoped hard, every rule prefixed. The dashboard above this section is
# model-written Tailwind and will happily restyle a bare <section>.
#
# ★Two blocks, because print and screen legitimately need different layouts.
#
# On screen #root is left alone entirely: it grows with its content and the
# narrative follows after all of it, in normal flow. Three attempts got here:
#   * body as a flex column with #root{flex:1} gave #root 753px and the section
#     207px — numbers that add up, and a dashboard that permanently lost 207px
#     of height and had to scroll inside itself.
#   * #root{height:100vh} pinned the box to one viewport while the content
#     OVERFLOWED it, so the section landed on top of the overflow. Comparing
#     bounding rects reported no overlap, because a box does not include what
#     spills out of it. Only the screenshot showed the collision.
#   * #root{min-height:100vh} broke nothing, and still had to go. It padded
#     short dashboards out to a full viewport, which opened a white hole
#     BETWEEN the dashboard and the narrative — 532px on the shortest of them,
#     and 392px on one carrying no narrative at all, where the padding bought
#     nothing whatsoever.
# ★All 19 stored `page` artifacts were rendered both with the rule and without
# it. 15 came out pixel-identical; the 4 that differed were the short ones, and
# every one of them read better without it. The worry that motivated the rule —
# that a bare-auto #root would collapse inner panels sized with h-full or
# height:100% — was measured and did not happen: zero collapsed elements and
# zero zero-sized canvases across all 19, in both modes.
#
# So this block sets the document reset and nothing else. margin:0 on body is
# load-bearing (the UA default 8px shows as a border around the dashboard);
# #root gets no height rule at all.
#
# On paper the document must be free to run as long as its content, which is
# why report_pdf_service pins min-height rather than height and releases it
# again under @media print. Forcing the flex column there would undo that, so
# the PDF renderer takes SECTION css only and lets the section follow #root in
# normal flow. Anything that needs both uses INSIGHTS_CSS.
INSIGHTS_LAYOUT_CSS = """
    html { height: 100%; }
    body { min-height: 100%; margin: 0; padding: 0; }"""

INSIGHTS_SECTION_CSS = """
    #artifact-insights {
      border-top: 1px solid #e5e7eb;
      background: #ffffff;
      padding: 16px 20px 20px;
      font-family: system-ui, -apple-system, sans-serif;
      color: #111827;
    }
    #artifact-insights .bow-insight-label {
      font-size: 10px; font-weight: 700; letter-spacing: .12em;
      text-transform: uppercase; color: #6b7280;
    }
    #artifact-insights .bow-insight-headline {
      margin: 6px 0 0; font-size: 15px; font-weight: 600; line-height: 1.45;
    }
    #artifact-insights .bow-insight-list {
      margin: 10px 0 0; padding-left: 18px;
      display: flex; flex-direction: column; gap: 5px;
    }
    #artifact-insights .bow-insight-list li {
      font-size: 12.5px; line-height: 1.55; color: #4b5563;
    }
    #artifact-insights .bow-insight-src {
      margin-left: 6px; font-size: 10px; color: #9ca3af; white-space: nowrap;
    }
    #artifact-insights .bow-insight-rejected {
      margin: 10px 0 0; font-size: 10px; color: #b45309;
    }"""

INSIGHTS_CSS = INSIGHTS_LAYOUT_CSS + INSIGHTS_SECTION_CSS


def insights_section_html(
    insights: Optional[dict[str, Any]],
    visualizations: Optional[list[Any]] = None,
) -> str:
    """Return the section markup, or '' when there is nothing to say.

    An empty shell under a dashboard is worse than no section, and every
    artifact generated before this feature carries no payload at all — so the
    absent case must render nothing rather than a heading with no content.
    """
    if not isinstance(insights, dict):
        return ""

    headline = str(insights.get("headline") or "").strip()
    raw_findings = insights.get("findings")
    findings = [
        f for f in (raw_findings if isinstance(raw_findings, list) else [])
        if isinstance(f, dict) and str(f.get("text") or "").strip()
    ]
    if not headline and not findings:
        return ""

    by_id: dict[str, str] = {}
    for viz in visualizations or []:
        if isinstance(viz, dict) and viz.get("id"):
            title = viz.get("title") or viz.get("name")
            if isinstance(title, str) and title.strip():
                by_id[str(viz["id"])] = title.strip()

    bullets = []
    for f in findings:
        # ★Escaped, not trusted. This text is model-written and lands in a
        # document that also runs the model's own code; an unescaped finding
        # could close the section and open a script tag.
        text = html.escape(str(f.get("text") or "").strip())
        source = by_id.get(str(f.get("viz_id") or ""), "")
        cite = f'<span class="bow-insight-src">{html.escape(source)}</span>' if source else ""
        bullets.append(f"<li>{text}{cite}</li>")

    # ★Dropped findings are stated, not hidden. A narrative that lost four of
    # five points is not a short one, it is a warning, and a reader who cannot
    # see that has no way to tell a thin section from a verified one.
    rejected_count = insights.get("rejected_count") or 0
    rejected = (
        f'<p class="bow-insight-rejected">{int(rejected_count)} finding(s) were '
        "dropped for citing a figure that is not in the data.</p>"
        if isinstance(rejected_count, int) and rejected_count > 0
        else ""
    )

    head = (
        f'<p class="bow-insight-headline">{html.escape(headline)}</p>' if headline else ""
    )
    lst = f'<ul class="bow-insight-list">{"".join(bullets)}</ul>' if bullets else ""

    return (
        '\n  <section id="artifact-insights" data-polish-ignore="true">\n'
        '    <div class="bow-insight-label">What this means</div>\n'
        f"    {head}\n"
        f"    {lst}\n"
        f"    {rejected}\n"
        "  </section>"
    )
