"""A second spelling of the period is not a measure.

★ Found live, not by review. A result grouped by month AND branch carried both
a date column and a plain `month` 1-12. The date won the period axis; `month`
stayed behind in the measure pool, got summed, and the December-to-January
wrap (12 -> 1) read as a 12x collapse with a flat row count beside it — which
is precisely the corroborated-discontinuity shape this module hunts. The
product warned about `month = 1` in January and the written answer had to talk
the reader out of it:

    "A platform data-quality flag on `month` = 1 in January is a false
     positive: `month` is a calendar label (1...12), not a sales measure"

A warning that is wrong is worse than no warning, because it teaches people to
skip the real ones.

★ These fixtures are deliberately anonymous — `value`, `units`, `group_a`. The
rule may not know anything about any particular dataset, currency, or industry;
it decides from the SHAPE of whatever result it is handed. A fixture that named
a real column would let a dataset-specific rule pass these tests.
"""
from app.services import data_quality


def _rows_grouped_by_period_and_group(measures):
    """A result grouped by period AND by something else, carrying the period twice.

    `period` is the axis. `period_index` is the same information written again
    as a small integer that cycles — the shape that caused the false positive.
    Every group repeats every period, so each measure genuinely varies within a
    period while the two period columns do not.
    """
    rows = []
    for i, (label, index, total) in enumerate(measures):
        for g, share in enumerate((0.5, 0.3, 0.2)):
            rows.append({
                "period": label,
                "period_index": index,
                "group_a": f"g{g}",
                "value": total * share,
                "units": (10 + i) * share,
            })
    return rows


# A full year plus the wrap into the next January: the index runs 1..12 then
# restarts at 1 while the axis keeps moving forward.
_TWO_YEAR_WRAP = [
    ("2025-01", 1, 1000.0), ("2025-02", 2, 1020.0), ("2025-03", 3, 1010.0),
    ("2025-04", 4, 1030.0), ("2025-05", 5, 1025.0), ("2025-06", 6, 1040.0),
    ("2025-07", 7, 1035.0), ("2025-08", 8, 1050.0), ("2025-09", 9, 1045.0),
    ("2025-10", 10, 1060.0), ("2025-11", 11, 1055.0), ("2025-12", 12, 1070.0),
    ("2026-01", 1, 1065.0), ("2026-02", 2, 1080.0),
]


def test_a_repeated_period_column_is_not_reported_as_a_collapse():
    """The exact live failure: the axis written twice, one of them cycling."""
    rows = _rows_grouped_by_period_and_group(_TWO_YEAR_WRAP)
    result = data_quality.analyze_result(rows)
    assert result is None, (
        "a column that merely spells the period again was reported as a "
        f"discontinuity: {result}"
    )


def test_the_repeated_period_column_is_identified_as_a_label():
    """State the rule directly, not only through its effect."""
    rows = _rows_grouped_by_period_and_group(_TWO_YEAR_WRAP)
    _, _, series = data_quality._build_period_series(rows)
    assert "period_index" not in series, "the second spelling of the axis survived"
    assert "value" in series, "a real measure was dropped with it"
    assert "units" in series


def test_a_real_collapse_in_a_real_measure_is_still_reported():
    """★The fix must not buy silence. The whole point of the module still works.

    One period's value falls to a sixth of its neighbours while the row count
    and the companion series hold steady — the corroborated shape.
    """
    measures = list(_TWO_YEAR_WRAP)
    label, index, total = measures[6]
    measures[6] = (label, index, total / 6.0)
    rows = _rows_grouped_by_period_and_group(measures)

    result = data_quality.analyze_result(rows)
    assert result is not None, "the real collapse went unreported"
    columns = {d["column"] for d in result["discontinuities"]}
    assert "value" in columns, f"reported something, but not the measure: {columns}"
    assert "period_index" not in columns, "still reporting the label"


def test_one_row_per_period_keeps_its_measures():
    """★The rule is only meaningful when the result GROUPS.

    With a single row per period every column is trivially constant inside its
    period, so an unguarded version of this rule would classify the measures
    themselves as labels and go permanently silent. That is the dangerous
    failure — a check that never fires looks exactly like a clean dataset.
    """
    rows = [
        {"period": label, "value": total}
        for label, _index, total in _TWO_YEAR_WRAP
    ]
    _, _, series = data_quality._build_period_series(rows)
    assert "value" in series, (
        "an ungrouped result lost its measure — the check has gone blind"
    )


def test_one_row_per_period_still_reports_a_real_collapse():
    """The end-to-end form of the guard above."""
    measures = list(_TWO_YEAR_WRAP)
    label, index, total = measures[6]
    measures[6] = (label, index, total / 6.0)
    rows = [{"period": lb, "value": tot} for lb, _i, tot in measures]

    result = data_quality.analyze_result(rows)
    assert result is not None, "an ungrouped real collapse went unreported"


def test_a_constant_measure_is_not_mistaken_for_a_label():
    """Constant inside a period is not enough — it must also be DISTINCT across
    periods. A quantity that is flat within each period and repeats the same
    value in several of them is still a measure, and dropping it would hide a
    genuine later move in it."""
    measures = [(lb, idx, tot) for lb, idx, tot in _TWO_YEAR_WRAP]
    rows = []
    for i, (label, index, total) in enumerate(measures):
        for g, share in enumerate((0.5, 0.3, 0.2)):
            rows.append({
                "period": label,
                "period_index": index,
                "group_a": f"g{g}",
                "value": total * share,
                # constant inside each period, and REPEATS across periods
                "headcount": 7.0,
            })
    _, _, series = data_quality._build_period_series(rows)
    assert "headcount" in series, (
        "a flat measure was discarded as a label; only a value that is distinct "
        "per period is functionally determined by the axis"
    )
    assert "period_index" not in series


def test_every_component_of_the_axis_is_recognised():
    """A result may spell the period several ways at once — month, quarter and
    year. Each is derivable from the axis, so each is a label."""
    rows = []
    for label, index, total in _TWO_YEAR_WRAP:
        year = int(label[:4])
        for g, share in enumerate((0.5, 0.3, 0.2)):
            rows.append({
                "period": label,
                "period_index": index,
                "quarter_number": (index - 1) // 3 + 1,
                "year_number": year,
                "group_a": f"g{g}",
                "value": total * share,
            })
    _, _, series = data_quality._build_period_series(rows)
    assert "period_index" not in series
    assert "quarter_number" not in series
    assert "year_number" not in series
    assert "value" in series, "the measure was dropped along with the labels"


def test_a_running_counter_is_left_alone_and_is_harmless():
    """★An honest boundary. A 1..N run counter is NOT derivable from the axis —
    it depends on where the result happens to start — so this rule leaves it in
    the measure pool rather than guessing.

    That is safe, and the test says why rather than merely asserting it: a
    monotonically rising counter has no collapse in it, so it cannot produce the
    discontinuity shape. Silence here is earned, not assumed.
    """
    rows = []
    for i, (label, index, total) in enumerate(_TWO_YEAR_WRAP):
        for g, share in enumerate((0.5, 0.3, 0.2)):
            rows.append({
                "period": label,
                "row_number": i + 1,
                "group_a": f"g{g}",
                "value": total * share,
            })
    _, _, series = data_quality._build_period_series(rows)
    assert "row_number" in series, "a counter this rule cannot justify was still dropped"
    assert data_quality.analyze_result(rows) is None, (
        "a monotonic counter produced a discontinuity"
    )
