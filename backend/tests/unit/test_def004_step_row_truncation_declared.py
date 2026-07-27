"""DEF-004 — a dashboard's headline KPI was the total of a 1,000-row prefix.

Found by: E2E-P2.7 (Fabric dashboard — live render showed NET SALES $56,428,548,406
against a ground truth of $98,921,181,279, and covered 10 of 17 months)

``format_df_for_widget`` truncates at ``limit_row_count`` (default 1000) with
``df.head()`` — a PREFIX in the query's own ORDER BY, not a sample. The CFC query
was ordered by month, so the stored step held Jan 2025 → Oct 2025 and the last
seven months were simply gone. ``info.total_rows`` recorded the true 1,903, but
nothing declared the rows partial, and ``create_artifact`` then reported
``row_count = len(rows)`` — so the model was told 1,000 WAS the dataset (the same
lie DEF-002 fixed one layer up, arriving from a lower one).

Contract these tests pin:
  * truncation is declared where it happens (``rows_truncated`` / ``rows_total``)
  * ``row_count`` is the true dataset size, never the prefix length
  * the codegen profile carries an explicit completeness warning
  * an untruncated dataset gains no markers (no false alarms)
"""
import pandas as pd
import pytest

from app.ai.tools.implementations.create_artifact import CreateArtifactTool

TOOL = CreateArtifactTool()

# The shape that found it: month-ordered, so a prefix loses whole months.
FULL = pd.DataFrame(
    [
        {"month_label": f"2025-{m:02d}", "branch": f"B{b}", "net_sales": 1_000_000 + m}
        for m in range(1, 18)
        for b in range(112)
    ]
)


class _Executor:
    """Exercises format_df_for_widget without the executor's DB/LLM dependencies."""

    from app.ai.code_execution.code_execution import StreamingCodeExecutor as _S

    format_df_for_widget = _S.format_df_for_widget
    get_df_info = _S.get_df_info

    def __init__(self):
        self.organization_settings = None  # -> default cap of 1000


def _widget(df, max_rows=None):
    return _Executor().format_df_for_widget(df=df, max_rows=max_rows)


# --- the defect: declare truncation where it happens -------------------------


def test_def004_truncated_widget_declares_it():
    out = _widget(FULL)
    assert len(out["rows"]) == 1000
    assert out["rows_truncated"] is True
    assert out["rows_total"] == len(FULL)


def test_def004_true_total_is_reported_not_the_prefix_length():
    out = _widget(FULL)
    assert out["rows_total"] != len(out["rows"])
    assert out["info"]["total_rows"] == len(FULL)


def test_def004_untruncated_widget_carries_no_markers():
    out = _widget(FULL.head(50))
    assert "rows_truncated" not in out
    assert "rows_total" not in out


def test_def004_empty_frame_is_safe():
    out = _widget(FULL.iloc[0:0])
    assert out["rows"] == []
    assert "rows_truncated" not in out


def test_def004_explicit_max_rows_still_declares():
    out = _widget(FULL, max_rows=10)
    assert len(out["rows"]) == 10
    assert out["rows_truncated"] is True
    assert out["rows_total"] == len(FULL)


# --- the consequence: the prefix must not be reported as the dataset ---------


def _viz(rows_available, rows_total):
    """A visualization entry as create_artifact builds it from a truncated step."""
    return {
        "id": "viz-1",
        "title": "CFC Sales Master",
        "columns": [{"field": "net_sales"}, {"field": "month_label"}],
        "column_info": {},
        "row_count": rows_total,
        "rows_truncated": True,
        "rows_total": rows_total,
        "rows_available": rows_available,
        "rows": [{"net_sales": 1, "month_label": "2025-01"}] * min(rows_available, 120),
        "dataModel": {},
        "view": {},
    }


def test_def004_profile_row_count_is_the_dataset_not_the_prefix():
    p = TOOL._build_viz_profile(_viz(1000, 1903), allow_llm_see_data=True)
    assert p["row_count"] == 1903, "the model must not be told 1000 is the dataset"


def test_def004_profile_declares_what_is_actually_available():
    p = TOOL._build_viz_profile(_viz(1000, 1903), allow_llm_see_data=True)
    assert p["rows_truncated"] is True
    assert p["rows_available"] == 1000


def test_def004_profile_warning_states_the_correctness_constraint():
    """"rows_available < row_count" alone reads as trivia; spell out the consequence."""
    warning = TOOL._build_viz_profile(_viz(1000, 1903), allow_llm_see_data=True)[
        "data_completeness_warning"
    ]
    assert "PARTIAL" in warning
    assert "1000" in warning and "1903" in warning
    for phrase in ("do not label it a total", "full period"):
        assert phrase in warning


def test_def004_no_warning_when_nothing_was_truncated():
    clean = _viz(50, 50)
    clean.pop("rows_truncated")
    clean.pop("rows_total")
    clean.pop("rows_available")
    p = TOOL._build_viz_profile(clean, allow_llm_see_data=True)
    assert "data_completeness_warning" not in p
    assert "rows_truncated" not in p


def test_def004_warning_survives_privacy_mode():
    """Hiding rows must not hide the fact that the rows are incomplete."""
    p = TOOL._build_viz_profile(_viz(1000, 1903), allow_llm_see_data=False)
    assert "sample_rows" not in p
    assert p["rows_truncated"] is True
    assert "PARTIAL" in p["data_completeness_warning"]


@pytest.mark.parametrize("total", [1001, 5000, 250_000])
def test_def004_scales_to_any_overflow(total):
    p = TOOL._build_viz_profile(_viz(1000, total), allow_llm_see_data=True)
    assert p["row_count"] == total
    assert str(total) in p["data_completeness_warning"]
