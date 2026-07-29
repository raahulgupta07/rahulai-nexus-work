"""Data-quality signals: an aggregate that moves while its volume does not.

Found by: reading a published multi-page report. Its monthly trend chart showed
one period at roughly a sixth of both neighbours, while that same period carried
the highest unit count and the highest order-line count of the year. The report
charted it, wrote a narrative around it, and stated "confidence is high on
direction of monthly volume". Nothing was flagged. The cause turned out to be
benign and entirely visible in the source — most of that period's rows were
NULL in the column being summed — and asking the product about it directly got
an answer in 51 seconds. The capability was never missing; nothing prompted it
to look.

A second defect from the same body of work: for one concept, one turn summed one
column and a later turn summed a different, equally plausible column. Both
answers were defensible. Neither said which column it used. The totals differed
by about 4%.

Contract these tests pin:
  * A period whose value collapses or jumps by a large factor while another
    series in the same result holds steady is reported. The check is on the
    SHAPE of the result, not on any cause — a NULL-heavy column, a dropped join,
    a mid-series unit change and a partial load all produce this shape.
  * Ordinary seasonality is NOT reported. This is the load-bearing half. A
    signal that fires on every quiet February is noise, and noise is ignored,
    which would leave the product worse off than saying nothing.
  * A companion series that never varies is not evidence, and cannot be used to
    corroborate anything.
  * A truncated result is not analysed at all — its last period is an artefact
    of the row cap, not of the data.
  * When a signal fires, "confidence: high" is withdrawn: the signal carries a
    ceiling, and a finding asserting high confidence over it is dropped.
  * The column an answer aggregated is recorded from the SQL that actually ran,
    survives context compaction, and a later turn that switches to a different
    column for the same aggregation is flagged as drift.

Every frame below is synthetic. No table, column, connector, currency, period or
figure from the report that motivated this appears anywhere in this file.
"""
import pytest

from app.services import data_quality
from app.services.data_quality import (
    ROW_COUNT_SERIES,
    analyze_result,
    asserts_high_confidence,
    detect_discontinuities,
    detect_measure_drift,
    extract_measure_selection,
    reject_overconfident,
)


# --------------------------------------------------------------------------
# Frame builders. Generic names throughout — these must read as "any result".
# --------------------------------------------------------------------------

_PERIODS = [
    "2019-01", "2019-02", "2019-03", "2019-04", "2019-05", "2019-06",
    "2019-07", "2019-08", "2019-09", "2019-10", "2019-11", "2019-12",
]


def _frame(values, volumes=None, period_field="period", value_field="metric_a"):
    rows = []
    for i, value in enumerate(values):
        row = {period_field: _PERIODS[i], value_field: value}
        if volumes is not None:
            row["metric_b"] = volumes[i]
        rows.append(row)
    return rows


def _columns(signals):
    return {s["column"] for s in signals}


def _periods(signals):
    return {s["period"] for s in signals}


# --------------------------------------------------------------------------
# FIRES — the shape the product missed
# --------------------------------------------------------------------------


def test_value_collapses_while_a_companion_series_holds_steady_is_reported():
    """The exact shape: the measure falls off a cliff, the volume does not."""
    values = [600, 620, 590, 610, 100, 605, 615, 600]
    volumes = [50, 52, 49, 51, 55, 50, 51, 50]
    signals = detect_discontinuities(_frame(values, volumes))

    assert signals, "a 6x collapse against a flat companion must be reported"
    assert _periods(signals) == {"2019-05"}
    assert "metric_a" in _columns(signals)
    hit = next(s for s in signals if s["column"] == "metric_a")
    assert hit["kind"] == "co_movement_divergence"
    assert hit["steady_series"] in ("metric_b", ROW_COUNT_SERIES)
    assert hit["value"] == 100
    assert 550 < hit["expected_around"] < 650


def test_the_signal_is_cause_agnostic_a_jump_is_reported_as_readily_as_a_collapse():
    """A unit or currency change mid-series moves the value UP. Same shape."""
    values = [40, 42, 41, 43, 4200, 41, 42, 40]
    volumes = [10, 11, 10, 11, 10, 11, 10, 11]
    signals = detect_discontinuities(_frame(values, volumes))

    assert _periods(signals) == {"2019-05"}
    assert next(s for s in signals if s["column"] == "metric_a")["value"] == 4200


def test_a_zeroed_period_is_reported():
    """A partial load leaves a period at zero. Division must not swallow it."""
    values = [300, 310, 295, 305, 0, 300, 302, 298]
    volumes = [20, 21, 20, 21, 22, 20, 21, 20]
    signals = detect_discontinuities(_frame(values, volumes))

    assert _periods(signals) == {"2019-05"}
    assert next(s for s in signals)["move_factor"] is None  # unbounded, reported as such


def test_the_row_count_alone_can_be_the_steady_witness():
    """A grouped result carries its own volume: how many rows fell in each period.

    No second measure column here — the only companion available is the number
    of source rows per period, which is exactly the "volume" the check is named
    for.
    """
    per_period = [4, 5, 4, 5, 6, 5, 4, 5]
    rows = []
    for i, period in enumerate(_PERIODS[:8]):
        # The total tracks the row count everywhere except the suspect period,
        # where the count holds at its highest and the total collapses anyway.
        per_row = 5 if i == 4 else 100
        for n in range(per_period[i]):
            rows.append({"period": period, "segment": f"s{n}", "metric_a": per_row})
    signals = detect_discontinuities(rows)

    assert _periods(signals) == {"2019-05"}
    hit = next(s for s in signals)
    assert hit["kind"] == "co_movement_divergence"
    assert hit["steady_series"] == ROW_COUNT_SERIES


def test_an_isolated_collapse_is_reported_even_with_no_companion_series():
    """One number per period. Only the shape is available, so the bar is 5x."""
    values = [800, 810, 790, 805, 120, 800, 795, 810]
    signals = detect_discontinuities(_frame(values))

    assert _periods(signals) == {"2019-05"}
    assert next(s for s in signals)["kind"] == "isolated_discontinuity"


def test_a_partial_final_period_is_reported():
    """The commonest real fault there is: today's month is still loading."""
    values = [500, 510, 495, 505, 500, 498, 502, 40]
    signals = detect_discontinuities(_frame(values))

    assert _periods(signals) == {"2019-08"}


def test_a_result_grouped_by_period_and_something_else_is_analysed_on_its_totals():
    """A period x segment table is charted as period totals, so it is checked that way."""
    rows = []
    for i, period in enumerate(_PERIODS[:8]):
        for segment, share in (("north", 0.6), ("south", 0.4)):
            total = 30 if i == 5 else 900
            rows.append(
                {"period": period, "segment": segment, "metric_a": total * share,
                 "metric_b": 20}
            )
    signals = detect_discontinuities(rows)

    assert _periods(signals) == {"2019-06"}


# --------------------------------------------------------------------------
# DOES NOT FIRE — the half that decides whether anyone reads the other half
# --------------------------------------------------------------------------


def test_ordinary_seasonality_is_not_reported():
    """A quiet month and a peak month. Volume moves with value; nothing is broken."""
    values = [100, 90, 110, 130, 120, 140, 150, 130, 120, 160, 200, 260]
    volumes = [10, 9, 11, 13, 12, 14, 15, 13, 12, 16, 20, 26]
    assert detect_discontinuities(_frame(values, volumes)) == []


def test_a_seasonal_peak_of_nearly_three_times_is_not_reported():
    """December against November runs 2-3x in real retail. That is a business result."""
    values = [100, 95, 105, 100, 98, 102, 100, 99, 101, 100, 105, 290]
    assert detect_discontinuities(_frame(values)) == []


def test_a_deep_but_gradual_decline_is_not_reported():
    """A series that walks down 10x over a year has no discontinuity in it."""
    values = [1000, 800, 640, 512, 410, 328, 262, 210, 168, 134, 107, 86]
    assert detect_discontinuities(_frame(values)) == []


def test_a_step_change_that_persists_is_not_reported_as_a_discontinuity():
    """A permanent level change is a business event, not a hole in one period."""
    values = [1000, 1010, 990, 1005, 100, 105, 95, 102, 98, 101, 99, 100]
    assert detect_discontinuities(_frame(values)) == []


def test_strong_growth_is_not_reported():
    """A series that doubles every period is a trend, and every point fits it."""
    values = [10, 20, 40, 80, 160, 320, 640, 1280]
    assert detect_discontinuities(_frame(values)) == []


def test_a_short_series_is_not_analysed():
    """Three points can always be read as a trend. There is nothing to conclude."""
    rows = [
        {"period": "2019-01", "metric_a": 900},
        {"period": "2019-02", "metric_a": 10},
        {"period": "2019-03", "metric_a": 880},
    ]
    assert detect_discontinuities(rows) == []


def test_a_result_with_no_period_column_is_not_analysed():
    """Without a timeline there is no neighbourhood to be out of step with."""
    rows = [
        {"segment": "a", "metric_a": 500},
        {"segment": "b", "metric_a": 490},
        {"segment": "c", "metric_a": 5},
        {"segment": "d", "metric_a": 505},
        {"segment": "e", "metric_a": 495},
    ]
    assert detect_discontinuities(rows) == []


def test_a_constant_companion_cannot_corroborate_anything():
    """One row per period means a constant row count. "It didn't move" is vacuous.

    Without this guard the row count would witness every result, and the 5x bar
    that keeps seasonality out would silently drop to 3x for all of them.
    """
    values = [100, 95, 105, 100, 380, 98, 102, 100]  # 3.8x — over 3, under 5
    signals = detect_discontinuities(_frame(values))
    assert signals == [], "a series with no varying companion must clear the 5x bar"


def test_a_flat_column_that_does_not_track_the_measure_is_not_a_witness():
    """The false-positive path that a naive "any flat column" rule would open.

    A ratio-like column sits still through a seasonal peak that the volume fully
    explains. It is flat, but it never moved with the measure in the first
    place, so its stillness is not evidence of anything.
    """
    values = [100, 95, 200, 105, 330, 98, 210, 100]  # a bumpy series, peak 3.3x
    rows = [
        {"period": _PERIODS[i], "metric_a": v, "ratio_col": 7.0 + (i % 2) * 0.05}
        for i, v in enumerate(values)
    ]
    assert detect_discontinuities(rows) == []


def test_a_year_column_is_not_treated_as_a_measure():
    """Four-digit integers that are years must not be summed and trended."""
    rows = [
        {"period": p, "year": 2019, "metric_a": 100 + i}
        for i, p in enumerate(_PERIODS[:8])
    ]
    assert "year" not in _columns(detect_discontinuities(rows))


def test_a_truncated_result_is_not_analysed():
    """A prefix cut in the query's own sort order has a manufactured last period."""
    rows = _frame([500, 510, 495, 505, 500, 498, 502, 40])
    assert analyze_result(rows, truncated=False) is not None
    assert analyze_result(rows, truncated=True) is None


def test_analyze_result_returns_nothing_for_a_clean_series():
    values = [100, 90, 110, 130, 120, 140, 150, 130, 120, 160, 200, 260]
    volumes = [10, 9, 11, 13, 12, 14, 15, 13, 12, 16, 20, 26]
    assert analyze_result(_frame(values, volumes)) is None


# --------------------------------------------------------------------------
# The tie to confidence
# --------------------------------------------------------------------------


def test_a_signal_carries_a_confidence_ceiling_and_actionable_guidance():
    result = analyze_result(_frame([600, 620, 590, 610, 100, 605, 615, 600]))
    assert result["confidence_ceiling"] == "medium"
    assert result["discontinuities"]
    assert "high confidence" in result["guidance"] or "high confidence in it" in result["guidance"]


@pytest.mark.parametrize(
    "text",
    [
        "Confidence is high on the direction of this series.",
        "We have high confidence in the quarterly trend.",
        "confidence: HIGH",
        "I am highly confident the series is rising.",
        "The series is rising, with certainty.",
    ],
)
def test_high_confidence_phrasings_are_recognised(text):
    assert asserts_high_confidence(text)


@pytest.mark.parametrize(
    "text",
    [
        "Confidence is medium; one period looks incomplete.",
        "The series rose 12% over the window.",
        "Low confidence — the last period is still loading.",
        "There is a high value in the third period.",
    ],
)
def test_ordinary_prose_is_not_mistaken_for_a_confidence_claim(text):
    assert not asserts_high_confidence(text)


def test_a_finding_asserting_high_confidence_is_dropped_when_a_ceiling_is_in_force():
    findings = [
        {"text": "Confidence is high on the direction of the series."},
        {"text": "The series rose 12% across the window."},
    ]
    kept, rejected = reject_overconfident(findings, "medium")
    assert [f["text"] for f in kept] == ["The series rose 12% across the window."]
    assert len(rejected) == 1


def test_no_ceiling_means_no_findings_are_dropped():
    findings = [{"text": "Confidence is high on the direction of the series."}]
    kept, rejected = reject_overconfident(findings, None)
    assert kept == findings and rejected == []


def test_the_insight_gate_drops_an_overconfident_finding_over_a_broken_series():
    """End to end through the module that already drops ungrounded claims."""
    from app.services.artifact_insights import verify_findings

    rows = [
        {"period": p, "metric_a": v, "metric_b": 50}
        for p, v in zip(_PERIODS, [600, 620, 590, 610, 100, 605, 615, 600])
    ]
    visualizations = [{"id": "v1", "title": "Trend", "rows": rows, "row_count": 8}]
    findings = [
        {"text": "Confidence is high on the direction of this series.", "viz_id": "v1"},
        {"text": "The series sits around 600 for most periods.", "viz_id": "v1"},
    ]
    kept, rejected = verify_findings(findings, visualizations)

    assert all(not asserts_high_confidence(f["text"]) for f in kept)
    assert any("unexplained discontinuity" in r for r in rejected)


def test_the_insight_gate_leaves_a_clean_dashboard_alone():
    """The confidence gate must not become a tax on every well-founded summary."""
    from app.services.artifact_insights import verify_findings

    rows = [
        {"period": p, "metric_a": v, "metric_b": v / 10}
        for p, v in zip(_PERIODS, [100, 90, 110, 130, 120, 140, 150, 130, 120, 160, 200, 260])
    ]
    visualizations = [{"id": "v1", "title": "Trend", "rows": rows, "row_count": 12}]
    findings = [{"text": "Confidence is high on the direction of this series.", "viz_id": "v1"}]
    kept, rejected = verify_findings(findings, visualizations)

    assert len(kept) == 1 and rejected == []


# --------------------------------------------------------------------------
# P2 — which column answered the question
# --------------------------------------------------------------------------


def test_the_aggregated_column_is_read_off_the_sql_that_actually_ran():
    selection = extract_measure_selection(
        ["SELECT period, SUM(amount_x) AS total FROM t GROUP BY period"]
    )
    assert selection["columns"] == ["amount_x"]
    assert selection["aggregations"] == [{"function": "sum", "column": "amount_x"}]
    assert "which column" in selection["note"]


def test_qualified_and_quoted_column_references_resolve_to_the_column():
    selection = extract_measure_selection(
        ['SELECT SUM(t."amount_x"), AVG(other.[amount_y]) FROM t']
    )
    assert selection["columns"] == ["amount_x", "amount_y"]


def test_count_star_is_not_reported_as_a_column_choice():
    """There is no choice to disclose in COUNT(*)."""
    assert extract_measure_selection(["SELECT COUNT(*) FROM t"]) is None


def test_an_expression_is_not_reported_as_a_column_choice():
    """Naming an expression as "the column I chose" would be a lie."""
    assert extract_measure_selection(["SELECT SUM(a * b - c) FROM t"]) is None


def test_aggregation_synonyms_normalise_so_drift_compares_like_with_like():
    a = extract_measure_selection(["SELECT AVERAGE(amount_x) FROM t"])
    b = extract_measure_selection(["SELECT AVG(amount_x) FROM t"])
    assert a["aggregations"] == b["aggregations"]


def test_switching_the_summed_column_between_turns_is_reported_as_drift():
    first = extract_measure_selection(["SELECT SUM(amount_x) FROM t"])
    second = extract_measure_selection(["SELECT SUM(amount_y) FROM t"])
    drift = detect_measure_drift(first, second)

    assert drift is not None
    assert drift["changes"] == [
        {"function": "sum", "previous_columns": ["amount_x"], "current_columns": ["amount_y"]}
    ]
    assert "changed the basis" in drift["guidance"]


def test_reusing_the_same_column_is_not_drift():
    first = extract_measure_selection(["SELECT SUM(amount_x) FROM t GROUP BY period"])
    second = extract_measure_selection(["SELECT SUM(amount_x) FROM t GROUP BY segment"])
    assert detect_measure_drift(first, second) is None


def test_a_different_aggregation_on_a_different_column_is_not_drift():
    """Counting rows after summing an amount is a different question, not a switch."""
    first = extract_measure_selection(["SELECT SUM(amount_x) FROM t"])
    second = extract_measure_selection(["SELECT COUNT(id_col) FROM t"])
    assert detect_measure_drift(first, second) is None


def test_drift_needs_both_sides():
    selection = extract_measure_selection(["SELECT SUM(amount_x) FROM t"])
    assert detect_measure_drift(None, selection) is None
    assert detect_measure_drift(selection, None) is None


# --------------------------------------------------------------------------
# Wiring: the signal has to survive long enough to matter
# --------------------------------------------------------------------------


def test_the_signal_keys_survive_observation_compaction():
    """The chart is built one turn and the sentence claiming confidence another.

    A warning that expires after the keep-full window expires before the moment
    it exists for.
    """
    from app.ai.agents.planner.prompt_builder import _OBS_KEEP_KEYS, PromptBuilder

    assert {"data_quality", "measure_selection", "measure_drift"} <= _OBS_KEEP_KEYS

    observations = [
        {
            "tool_name": "create_data",
            "execution_number": i,
            "observation": {
                "summary": f"result {i}",
                "data_quality": {"confidence_ceiling": "medium"},
                "measure_selection": {"columns": ["amount_x"]},
                "code": "x" * 5000,
            },
        }
        for i in range(12)
    ]
    compacted = PromptBuilder._compact_past_observations(observations)

    oldest = compacted[0]
    assert oldest["data_quality"] == {"confidence_ceiling": "medium"}
    assert oldest["measure_selection"] == {"columns": ["amount_x"]}
    assert "code" not in oldest


def test_the_observation_builder_flags_drift_across_turns():
    from app.ai.context.builders.observation_context_builder import (
        ObservationContextBuilder,
    )

    builder = ObservationContextBuilder()
    builder.add_tool_observation(
        "create_data", {}, {"summary": "a",
                            "measure_selection": extract_measure_selection(
                                ["SELECT SUM(amount_x) FROM t"])},
        loop_index=1,
    )
    second = {"summary": "b",
              "measure_selection": extract_measure_selection(
                  ["SELECT SUM(amount_y) FROM t"])}
    builder.add_tool_observation("create_data", {}, second, loop_index=2)

    assert "measure_drift" in second
    assert second["measure_drift"]["changes"][0]["current_columns"] == ["amount_y"]


def test_the_observation_builder_is_silent_when_the_basis_is_unchanged():
    from app.ai.context.builders.observation_context_builder import (
        ObservationContextBuilder,
    )

    builder = ObservationContextBuilder()
    selection = extract_measure_selection(["SELECT SUM(amount_x) FROM t"])
    builder.add_tool_observation("create_data", {}, {"measure_selection": selection}, loop_index=1)
    second = {"measure_selection": selection}
    builder.add_tool_observation("create_data", {}, second, loop_index=2)

    assert "measure_drift" not in second


def test_the_planner_prompt_states_the_confidence_rule():
    """The computed ceiling is only binding if the model is told it is binding."""
    import inspect
    import re

    from app.ai.agents.planner import prompt_builder_v3

    source = inspect.getsource(prompt_builder_v3)
    source = re.sub(r"^\s*#.*$", "", source, flags=re.MULTILINE)

    assert "data_quality" in source
    assert "confidence_ceiling" in source
    assert "measure_selection" in source
    assert "measure_drift" in source


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------


class _Stub:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _swap_settings(monkeypatch, **kwargs):
    """`settings` is pydantic BaseSettings and rejects undeclared assignment.

    _flag re-imports the module on every call, so swapping the module attribute
    is both possible and sufficient.
    """
    from app.settings import config as config_module

    monkeypatch.setattr(config_module, "settings", _Stub(**kwargs))


def test_the_flags_default_on(monkeypatch):
    _swap_settings(monkeypatch)  # neither field declared at all
    assert data_quality.signals_enabled() is True
    assert data_quality.disclosure_enabled() is True


def test_the_flags_turn_the_behaviour_off(monkeypatch):
    _swap_settings(
        monkeypatch,
        hybrid_data_quality_signals=False,
        hybrid_measure_disclosure=False,
    )
    assert data_quality.signals_enabled() is False
    assert data_quality.disclosure_enabled() is False


@pytest.mark.parametrize("value,expected", [("off", False), ("on", True), ("false", False), ("true", True), (0, False)])
def test_a_string_flag_value_is_type_checked(monkeypatch, value, expected):
    """"off" is truthy in Python. A flag read with `if value:` allows what it denies."""
    _swap_settings(monkeypatch, hybrid_data_quality_signals=value)
    assert data_quality.signals_enabled() is expected
