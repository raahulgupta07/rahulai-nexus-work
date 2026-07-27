"""DEF-002 — dashboard preview was built from a silently truncated 100-row slice.

Found by: E2E-P1.7 (City Mart dashboard, stored preview vs live browser render)

``create_artifact`` truncated each visualization's rows with ``rows[:100]`` at
fetch time, then reported ``row_count: len(rows)`` from the truncated list. So a
360-row dataset was described to the model as 100 rows, under a prompt that says
"(Full sample data included above)" and instructs it to rely on ``row_count``.

The same truncated list was injected as ``window.ARTIFACT_DATA`` for the preview
screenshot and the stored thumbnail, so the preview rendered the first 10 of 36
months: KPI tiles read 1.4B / 36K where the live dashboard read 5.1B / 130K.
That image is not shown in the UI, but ``read_artifact`` feeds it to the model as
a vision image and the self-heal path feeds it as "the broken render" — so the
agent could read back wrong figures, or repair a dashboard that was never broken.

Contract these tests pin:
  * rows are carried in FULL on the visualization entry
  * ``row_count`` is the true count
  * the prompt's stats are a sample, and say so (``stats_from_sample``)
  * the render payload matches what the live frontend would receive
  * if the render is ever capped, the payload declares it
"""
import pytest

from app.ai.tools.implementations.create_artifact import (
    CreateArtifactTool,
    _PROMPT_STATS_ROWS,
    _RENDER_ROW_LIMIT,
)

TOOL = CreateArtifactTool()

# The shape that found the defect: 36 months x 5 banners x 2 member types.
FULL_ROWS = [
    {"year": 2023 + (i // 120), "month": (i // 10) % 12 + 1,
     "banner": f"B{i % 5}", "member_type": "Member" if i % 2 else "Non-member",
     "net_sales": 1_000_000 + i}
    for i in range(360)
]


def _viz(rows, **extra):
    v = {
        "id": "viz-1",
        "title": "Commercial sales master",
        "columns": [{"field": "net_sales"}, {"field": "banner"}],
        "column_info": {},
        "row_count": len(rows),
        "rows": rows,
        "dataModel": {},
        "view": {},
    }
    v.update(extra)
    return v


# --- the defect: honest row_count --------------------------------------------

def test_def002_row_count_is_the_true_count_not_the_sample_size():
    profile = TOOL._build_viz_profile(_viz(FULL_ROWS), allow_llm_see_data=True)
    assert profile["row_count"] == 360, "the model must not be told 100"
    assert profile["row_count"] != _PROMPT_STATS_ROWS


def test_def002_sampled_stats_are_declared_as_sampled():
    profile = TOOL._build_viz_profile(_viz(FULL_ROWS), allow_llm_see_data=True)
    assert profile["stats_from_sample"] == _PROMPT_STATS_ROWS


def test_def002_no_sample_marker_when_nothing_was_sampled():
    small = FULL_ROWS[:20]
    profile = TOOL._build_viz_profile(_viz(small), allow_llm_see_data=True)
    assert "stats_from_sample" not in profile
    assert profile["row_count"] == 20


def test_def002_prompt_sample_rows_stay_small():
    """Prompt size is still bounded — the fix must not inline 360 rows."""
    profile = TOOL._build_viz_profile(_viz(FULL_ROWS), allow_llm_see_data=True)
    assert len(profile["sample_rows"]) == 5


# --- the defect: the render must see what the user sees -----------------------

def test_def002_render_payload_carries_every_row():
    out = TOOL._render_visualizations([_viz(FULL_ROWS)])
    assert len(out[0]["rows"]) == 360, "preview must not render a 100-row prefix"
    assert not out[0].get("rows_truncated")


def test_def002_render_totals_match_the_full_dataset():
    """The actual failure was arithmetic: tiles summed a prefix of the data."""
    out = TOOL._render_visualizations([_viz(FULL_ROWS)])
    rendered_total = sum(r["net_sales"] for r in out[0]["rows"])
    true_total = sum(r["net_sales"] for r in FULL_ROWS)
    assert rendered_total == true_total


def test_def002_render_months_are_not_clipped():
    """The tell was a 'monthly trend' that stopped at month 10 of year 1."""
    out = TOOL._render_visualizations([_viz(FULL_ROWS)])
    years = {r["year"] for r in out[0]["rows"]}
    assert years == {2023, 2024, 2025}


def test_def002_oversized_render_is_capped_but_says_so():
    big = [{"net_sales": 1} for _ in range(_RENDER_ROW_LIMIT + 500)]
    out = TOOL._render_visualizations([_viz(big)])[0]
    assert len(out["rows"]) == _RENDER_ROW_LIMIT
    assert out["rows_truncated"] is True
    assert out["rows_total"] == _RENDER_ROW_LIMIT + 500


def test_def002_capping_does_not_mutate_the_source_entry():
    big = [{"net_sales": 1} for _ in range(_RENDER_ROW_LIMIT + 5)]
    src = _viz(big)
    TOOL._render_visualizations([src])
    assert len(src["rows"]) == _RENDER_ROW_LIMIT + 5
    assert "rows_truncated" not in src


def test_def002_multiple_visualizations_each_handled():
    out = TOOL._render_visualizations([_viz(FULL_ROWS), _viz(FULL_ROWS[:25])])
    assert [len(v["rows"]) for v in out] == [360, 25]


@pytest.mark.parametrize("rows", [[], None])
def test_def002_empty_rows_are_safe(rows):
    v = _viz([])
    v["rows"] = rows
    out = TOOL._render_visualizations([v])
    assert out[0]["rows"] in ([], None)


def test_def002_privacy_mode_still_hides_rows_but_reports_true_count():
    """allow_llm_see_data=False must keep hiding data — and still not lie."""
    profile = TOOL._build_viz_profile(_viz(FULL_ROWS), allow_llm_see_data=False)
    assert "sample_rows" not in profile
    assert "column_stats" not in profile
    assert profile["row_count"] == 360
