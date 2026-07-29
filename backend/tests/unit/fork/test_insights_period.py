"""The insight panel must look at the RIGHT END of a timeline.

A dashboard spanning 2023-Q1 through 2025-Q4 produced a headline and four
findings that all described 2023-Q1 vs 2023-Q2. Every figure was exact — the
grounding check had nothing to complain about. The fault was selection: the
summariser read the first rows of a chronologically ordered result.

These tests pin the selection, which is the part that can be proved without a
model: which rows reach the prompt, in what order, and whether the prompt says
where in time the data ends. What the model then WRITES is not provable here.
"""
import json

import pytest

from app.services.artifact_insights import (
    _MAX_PROMPT_ROWS,
    _period_column,
    _period_key,
    _recent_window,
    build_prompt,
)


def _quarters(start_year=2023, n=12):
    """A quarter-by-quarter series, oldest first — the shape that failed."""
    rows = []
    for i in range(n):
        year = start_year + i // 4
        q = i % 4 + 1
        rows.append({"quarter": f"{year}-Q{q}", "revenue": 1_000_000 + i})
    return rows


# ── period recognition ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "2025-Q3", "2025Q3", "Q3-2025", "Q3 2025", "q3 2025",
    "2025-01", "2025/01", "2025-01-31", "2025/01/31",
    "2025-01-31T09:00:00", "Jan 2025", "January 2025", "2025", 2025,
])
def test_recognises_period_shapes(value):
    assert _period_key(value) is not None


@pytest.mark.parametrize("value", [
    None, True, False, "", "Yangon", "City Express", "N/A",
    1_000_000,      # a measure, not a year
    12,             # a count
    "12.5%",
])
def test_rejects_non_periods(value):
    assert _period_key(value) is None


def test_periods_sort_chronologically():
    ordered = ["2023-Q1", "2023-Q4", "2024-Q1", "2025-Q4"]
    keys = [_period_key(v) for v in ordered]
    assert keys == sorted(keys)


def test_quarters_and_months_sort_together():
    # Q1 must sort before Feb of the same year, and after Dec of the previous.
    assert _period_key("2024-12") < _period_key("2025-Q1") < _period_key("2025-04")


# ── column detection ──────────────────────────────────────────────────────────

def test_finds_the_period_column():
    assert _period_column(_quarters()) == "quarter"


def test_no_period_column_when_there_is_no_timeline():
    rows = [{"banner": "City Mart", "stores": 24}, {"banner": "Marketplace", "stores": 5}]
    assert _period_column(rows) is None


def test_a_constant_period_is_a_label_not_a_timeline():
    rows = [{"year": "2025", "branch": "A", "sales": 1}, {"year": "2025", "branch": "B", "sales": 2}]
    assert _period_column(rows) is None


def test_one_stray_year_like_label_does_not_make_a_column_a_timeline():
    rows = [{"product": f"SKU-{i}", "sales": i} for i in range(9)]
    rows.append({"product": "2024", "sales": 9})
    assert _period_column(rows) != "product"


def test_year_stored_as_an_integer_is_a_period():
    rows = [{"year": 2023, "sales": 1}, {"year": 2024, "sales": 2}, {"year": 2025, "sales": 3}]
    assert _period_column(rows) == "year"


# ── the selection itself ──────────────────────────────────────────────────────

def test_window_is_ordered_oldest_first_and_ends_at_the_latest_period():
    window, earliest, latest = _recent_window(_quarters())
    assert [r["quarter"] for r in window] == [r["quarter"] for r in _quarters()]
    assert earliest == "2023-Q1"
    assert latest == "2025-Q4"


def test_shuffled_input_is_still_ordered():
    rows = _quarters()
    scrambled = [rows[7], rows[0], rows[11], rows[3]]
    window, earliest, latest = _recent_window(scrambled)
    assert [r["quarter"] for r in window] == ["2023-Q1", "2023-Q4", "2024-Q4", "2025-Q4"]
    assert (earliest, latest) == ("2023-Q1", "2025-Q4")


def test_oversized_series_keeps_the_MOST_RECENT_rows():
    """The regression, stated directly: truncation must cut the old end."""
    rows = [{"month": f"20{y:02d}-{m:02d}", "sales": y * 100 + m}
            for y in range(10, 30) for m in range(1, 13)]
    assert len(rows) > _MAX_PROMPT_ROWS
    window, earliest, latest = _recent_window(rows)

    assert len(window) == _MAX_PROMPT_ROWS
    assert window[-1]["month"] == "2029-12"
    assert latest == "2029-12"
    # The oldest rows are the ones dropped — this is the whole fix.
    assert window[0]["month"] != "2010-01"
    assert all(r["month"] >= window[0]["month"] for r in window)
    # earliest still reports the FULL span, not the window, so the prompt can
    # say what the dashboard covers even when the sample cannot show it.
    assert earliest == "2010-01"


def test_rows_with_an_unreadable_period_are_kept_not_dropped():
    rows = _quarters(n=4) + [{"quarter": "unknown", "revenue": 42}]
    window, _, latest = _recent_window(rows)
    assert len(window) == 5
    assert any(r["quarter"] == "unknown" for r in window)
    assert latest == "2023-Q4"


def test_without_a_timeline_behaviour_is_the_old_prefix():
    rows = [{"banner": f"B{i}", "stores": i} for i in range(100)]
    window, earliest, latest = _recent_window(rows)
    assert window == rows[:_MAX_PROMPT_ROWS]
    assert earliest is None and latest is None


def test_empty_and_junk_input_do_not_raise():
    assert _recent_window([]) == ([], None, None)
    assert _recent_window(None) == ([], None, None)
    assert _recent_window(["not a row", 7]) == ([], None, None)


# ── what actually reaches the model ───────────────────────────────────────────

def test_prompt_carries_the_span_and_the_recency_rule():
    prompt = build_prompt("Sales", [{"id": "v1", "title": "Revenue by quarter",
                                     "row_count": 12, "rows": _quarters()}])
    assert 'period_from="2023-Q1"' in prompt
    assert 'period_to="2025-Q4"' in prompt
    assert "OLDEST FIRST" in prompt
    assert "2025-Q4" in prompt.split("PERIOD")[1]
    assert "NAME the period" in prompt


def test_prompt_data_block_ends_on_the_latest_period():
    rows = [{"month": f"2025-{m:02d}", "sales": m} for m in range(1, 13)]
    prompt = build_prompt("Sales", [{"id": "v1", "title": "Monthly", "row_count": 12, "rows": rows}])
    block = prompt.split("<visualization")[1].split("</visualization>")[0]
    payload = json.loads(block[block.index("\n") + 1:].strip())
    assert payload[-1]["month"] == "2025-12"
    assert payload[0]["month"] == "2025-01"


def test_prompt_still_asks_for_the_period_when_there_is_no_timeline():
    prompt = build_prompt("Stores", [{"id": "v1", "title": "Stores by banner", "row_count": 2,
                                      "rows": [{"banner": "City Mart", "stores": 24}]}])
    assert "period_from" not in prompt
    assert "name that period" in prompt


def test_grounding_still_reads_the_full_dataset_not_the_window():
    """The window bounds the PROMPT. Verification must keep seeing everything.

    If the two ever shared a slice, a true finding about an early period would
    start being rejected as invented.
    """
    from app.services.artifact_insights import verify_findings

    rows = [{"month": f"20{y:02d}-{m:02d}", "sales": 1}
            for y in range(10, 30) for m in range(1, 13)]
    rows[0]["sales"] = 987654  # only present far outside the recent window
    kept, rejected = verify_findings(
        [{"text": "The first month recorded 987,654 in sales."}],
        [{"id": "v1", "rows": rows}],
    )
    assert len(kept) == 1 and rejected == []
