"""/dashboards showed one card for three dashboards.

The page fetched `/reports?has_artifacts=yes` and rendered one card per REPORT.
A report holding a deep-analytics doc, a CEO deck and a key-insights dashboard
produced a single card, wearing the first badge that matched in a
slides → page → doc chain, and the other two artifacts were reachable only from
the picker inside the report. The All / Dashboards / Docs chips filtered
reports, so that one report matched all three and every chip returned the same
card — which reads as a broken filter.

Now the page lists artifacts. Three things have to hold for that to be honest:

  1. The list is visibility-scoped through the REPORT, using the same predicate
     every other read path uses. An artifact list that filtered on
     `organization_id` alone would publish every colleague's private report.
  2. The card links to its OWN artifact, and the report page honours that link.
     Without the deep link the CEO-deck card opens whatever artifact happens to
     be newest, which looks like the wrong card was clicked.
  3. The chip counts are computed before the mode filter is applied, or
     selecting "Docs" would make every chip report the doc count.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SERVICE = REPO / "backend" / "app" / "services" / "report_service.py"
ROUTE = REPO / "backend" / "app" / "routes" / "artifact.py"
SCHEMA = REPO / "backend" / "app" / "schemas" / "artifact_schema.py"
PAGE = REPO / "frontend" / "pages" / "dashboards" / "index.vue"
CARD = REPO / "frontend" / "components" / "home" / "ArtifactCard.vue"
REPORT_CARD = REPO / "frontend" / "components" / "home" / "RecentReportCard.vue"
PUBLIC_REPORT = REPO / "frontend" / "pages" / "r" / "[id]" / "index.vue"
EN = REPO / "locales" / "en.json"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fn(src: str, header: str) -> str:
    i = src.index(header)
    rest = src[i:]
    nxt = re.search(r"\n    async def |\n    def ", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── backend ──────────────────────────────────────────────────────────────────

def test_the_service_method_exists():
    assert "async def get_artifacts(" in _src(SERVICE)


def test_visibility_is_delegated_not_reinvented():
    body = _fn(_src(SERVICE), "async def get_artifacts(")
    assert "_report_visibility_terms" in body and "visible_reports_predicate" in body, (
        "the artifact list must reuse the one definition of report visibility; "
        "a local rule here is how a surface starts authorizing on org alone"
    )
    assert "Artifact.report_id.in_(visible_report_ids)" in body, (
        "artifacts are not scoped to the reports the caller may see"
    )


def test_soft_deleted_artifacts_are_excluded():
    body = _fn(_src(SERVICE), "async def get_artifacts(")
    assert "Artifact.deleted_at.is_(None)" in body


def test_chip_counts_are_computed_before_the_mode_filter():
    """Otherwise picking one chip rewrites all four numbers."""
    body = _fn(_src(SERVICE), "async def get_artifacts(")
    counts_at = body.index("mode_counts")
    filter_at = body.index('if mode in ("page", "doc", "slides")')
    assert counts_at < filter_at, (
        "the per-mode counts are being taken after the mode filter narrowed "
        "the set, so every chip would report the selected mode's count"
    )


def test_the_route_is_registered_and_permission_gated():
    src = _src(ROUTE)
    i = src.index('@router.get("", response_model=ArtifactBrowseResponse)')
    block = src[i:i + 400]
    assert "@requires_permission('view_reports')" in block


def test_fastapi_query_does_not_shadow_the_query_model():
    """`Query` in this module is app.models.query.Query."""
    src = _src(ROUTE)
    assert "from fastapi import Query as FQuery" in src
    assert not re.search(r"^from fastapi import .*\bQuery\b(?!\s+as)", src, re.M), (
        "importing fastapi's Query under its own name shadows the ORM model "
        "that the export routes use"
    )


def test_the_browse_schema_carries_where_the_artifact_came_from():
    src = _src(SCHEMA)
    i = src.index("class ArtifactBrowseSchema")
    block = src[i:i + 1400]
    for field in ("report_id", "report_title", "mode", "thumbnail_url"):
        assert f"{field}:" in block, f"{field} missing — the card cannot render without it"


# ── frontend ─────────────────────────────────────────────────────────────────

def test_the_page_fetches_artifacts_not_reports():
    src = _src(PAGE)
    assert "useMyFetch('/artifacts'" in src
    assert "useMyFetch('/reports'" not in src, "still listing reports"
    assert "has_artifacts" not in src, "leftover report-list parameter"


def test_the_grid_renders_one_card_per_artifact():
    src = _src(PAGE)
    assert 'v-for="artifact in artifacts"' in src
    # The comment above the grid names the old component on purpose — match the
    # tag and the import, not the word, or the test fires on its own history.
    assert "<RecentReportCard" not in src and "import RecentReportCard" not in src, (
        "the report-grained card is what collapsed three artifacts into one"
    )


def test_slides_is_offered_as_a_filter():
    """'page' and 'doc' were filterable; slides — the mode of the deck that
    triggered this — was not."""
    src = _src(PAGE)
    assert "'slides' as const" in src
    assert "dashboards.typeSlides" in src


def test_the_card_deep_links_to_its_own_artifact():
    body = _src(CARD)
    assert "?artifact=${props.artifact.id}" in body, "the card links to the report, not the artifact"


def test_the_report_page_honours_the_deep_link():
    src = _src(PUBLIC_REPORT)
    assert "route.query.artifact" in src, (
        "without this the deep link is decoration — every card opens the newest "
        "artifact and looks like it opened the wrong one"
    )
    assert "data.value[0].id" in src, "the fallback to newest must survive"


def test_the_card_badge_names_its_own_mode():
    body = _src(CARD)
    for mode in ("slides", "doc"):
        assert f"props.artifact.mode === '{mode}'" in body


def test_the_report_card_badge_stopped_being_first_wins():
    """The home page card stays report-grained, but it must not label a report
    holding three kinds of artifact as though it held one."""
    body = _src(REPORT_CARD)
    i = body.index("const badgeStyle")
    block = body[i:]
    assert "parts.push('Dashboard')" in block and "parts.push('Doc')" in block and "parts.push('Slides')" in block
    assert "parts.join(' · ')" in block
    assert "label: 'Slides'" not in block, "a hardcoded single label is the old behaviour"


def test_the_copy_exists():
    import json
    d = json.loads(_src(EN))["dashboards"]
    for k in ("typeSlides", "badgeDashboard", "badgeDoc", "badgeSlides",
              "untitledArtifact", "untitledReport"):
        assert d.get(k), f"missing dashboards.{k}"
