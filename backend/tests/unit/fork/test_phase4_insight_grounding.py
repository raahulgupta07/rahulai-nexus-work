"""PHASE 4 — a dashboard summary reported a number that came from nowhere.

Found by: reading the summary panel next to the tiles it describes. The panel
reported an average order value of **11,499**. The true figure was
**11,488.57**. The tile was right; the sentence beside it was invented.

The value appeared in no query result, no generated code and no tool output,
and it matched no plausible alternative formula — not the weighted mean, not
the unweighted mean of the monthly values, not the median, not the last month,
not the max month. Small (0.09%), confident, and completely untraceable. That
combination is the dangerous one: nothing about the sentence looks wrong, so
nobody checks it.

So every figure a finding claims is checked against the dashboard's own data
before anything is stored. This turns "the model usually gets it right" into
"a wrong number cannot be published", which is a different guarantee.

Two allowances are deliberate and are pinned here as hard as the rejections,
because a grounding check that drops correct findings gets switched off:

  * **Rounding is legitimate reporting.** "5.39B" for 5,386,520,580 is how a
    person would write it.
  * **Derived values are legitimate.** Percentages and growth rates are
    computed FROM the data and will never appear in it.

★ And dates are not magnitude claims. "rose from 5.39B in Jan 2025 to 8.02B in
May 2026" holds four number-like tokens, two of which are YEARS — they say
WHEN, not HOW MUCH. Left in, they failed grounding and sank every correctly
grounded finding along with them. This was a real bug, not a hypothetical, so
`2025`, `2025-01` and `Q3` forms are each pinned.

★★★ ONE TEST FAILS ON THE CODE AS WRITTEN, AND THE CONTRACT IS RIGHT
`test_phase4_the_real_fabrication_is_dropped` fails today.
`_is_grounded` compares proportionally at a flat 2% tolerance, and the
fabrication is 0.09% off the truth — well inside it. The check therefore
accepts the exact number it was built to reject.

A wider or narrower flat tolerance cannot fix this: the fabrication is 0.091%
off (11,499 vs 11,488.57) and the legitimate rounding is 0.065% off (5.39B vs
5,386,520,580). They overlap. What separates them is PRECISION, not distance —
"5.39B" is written to three significant figures and claims nothing finer than
±0.005B, which 5,386,520,580 satisfies; "11,499" is written to the unit and
claims ±0.5, which 11,488.57 misses by twenty times. So the tolerance has to
come from how the figure was WRITTEN — half a unit in its last written place —
not from a fixed percentage. Every other test in this file passes as-is.

Contract these tests pin:
  * the real fabrication (11,499 against data holding 11,488.57) is DROPPED
  * a rounded figure ("5.39B" for 5,386,520,580) is KEPT
  * an exact figure present in the data is KEPT
  * a derived percentage ("grew 48.8%") is KEPT
  * an invented magnitude ("reached 87.2B") is DROPPED
  * year / year-month / quarter tokens are stripped before checking, and a
    finding that only "fails" on its dates is KEPT
  * small structural integers ("the top 10 of 80 branches") are not magnitude
    claims and are not rejected
  * `parse_response` reads bare JSON, fenced JSON and JSON with chatter around
    it; returns None for garbage and for a reply with no headline; coerces a
    non-list `findings` to []
  * `build_prompt` shows the visualization data and states the grounding rule
  * empty findings and empty visualizations do not crash
"""
import json

import pytest

from app.services.artifact_insights import (
    _canonical,
    _is_grounded,
    _numbers_in,
    build_prompt,
    parse_response,
    verify_findings,
)


# --- fixtures: invented data, in the real visualization shape ----------------
#
# The story numbers (11,499 / 11,488.57) appear because they ARE the defect.
# Everything else is made up.


def _viz(vid, title, rows):
    return {"id": vid, "title": title, "row_count": len(rows), "rows": rows}


# The one that was fabricated against. Deliberately narrow, so a drop can only
# be attributed to the AOV token itself.
AOV_VIZ = [
    _viz(
        "v_order_value",
        "Average order value by month",
        [
            {"month": "2026-01", "orders": 38104, "avg_order_value": 11402.19},
            {"month": "2026-02", "orders": 40551, "avg_order_value": 11488.57},
            {"month": "2026-03", "orders": 41822, "avg_order_value": 11310.06},
        ],
    )
]

REVENUE_VIZ = [
    _viz(
        "v_revenue",
        "Revenue by month",
        [
            {"month": "2025-01", "revenue": 5386520580, "orders": 9120492},
            {"month": "2026-05", "revenue": 8019430000, "orders": 9884301},
        ],
    ),
    _viz(
        "v_branches",
        "Revenue by branch",
        [
            {"branch": "North", "revenue": 812440300},
            {"branch": "South", "revenue": 604118920},
        ],
    ),
]


def _finding(text, viz_id="v_revenue"):
    return {"text": text, "viz_id": viz_id}


def _kept_texts(findings, visualizations):
    kept, _ = verify_findings(findings, visualizations)
    return [f["text"] for f in kept]


# =============================================================================
# 1. the fabrication
# =============================================================================


def test_phase4_the_real_fabrication_is_dropped():
    """★ The defect, verbatim: 11,499 claimed against data holding 11,488.57.

    Nothing else in the sentence carries a figure, so the drop can only be the
    fabricated one.

    ★ FAILS on the current implementation — the flat 2% tolerance swallows a
    0.09% fabrication. See the module docstring for why a precision-derived
    tolerance separates this from legitimate rounding and a flat one cannot.
    """
    f = _finding(
        "The average order value across the period was 11,499.", "v_order_value"
    )

    kept, rejected = verify_findings([f], AOV_VIZ)

    assert kept == []
    assert len(rejected) == 1
    assert "11,499" in rejected[0]


def test_phase4_the_true_figure_is_kept():
    """The control for the case above: the real number must survive."""
    f = _finding(
        "The average order value in February was 11,488.57.", "v_order_value"
    )

    assert _kept_texts([f], AOV_VIZ) == [f["text"]]


def test_phase4_an_invented_magnitude_is_dropped():
    f = _finding("Revenue reached 87.2B over the period.")

    kept, rejected = verify_findings([f], REVENUE_VIZ)

    assert kept == []
    assert "87.2B" in rejected[0]


def test_phase4_one_bad_figure_drops_only_its_own_finding():
    """A fabrication must not take the correct findings down with it."""
    good = _finding("Revenue in the first month was 5.39B.")
    bad = _finding("Revenue reached 87.2B over the period.")

    kept, rejected = verify_findings([good, bad], REVENUE_VIZ)

    assert [f["text"] for f in kept] == [good["text"]]
    assert len(rejected) == 1


# =============================================================================
# 2. the allowances — rounding, exactness, derived values
# =============================================================================


def test_phase4_rounded_reporting_is_kept():
    """"5.39B" for 5,386,520,580 is correct reporting, not a fabrication."""
    f = _finding("Revenue in the first month was 5.39B.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_an_exact_value_from_the_data_is_kept():
    f = _finding("9,120,492 orders were placed in the first month.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_derived_percentage_is_kept():
    """48.8% is computed FROM the data and will never appear IN it."""
    f = _finding("Revenue grew 48.8% across the period.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_percentage_is_grounded_without_any_data():
    """The rule is about the KIND of figure, not about what happens to match."""
    assert _is_grounded("48.8%", []) is True


def test_phase4_small_structural_integers_are_not_magnitude_claims():
    """Rejecting "top 10" as ungrounded would be absurd."""
    f = _finding("The top 10 branches of 80 accounted for most of the revenue.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_finding_with_no_figures_at_all_is_kept():
    f = _finding("Revenue rose steadily across the period.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


# =============================================================================
# 3. ★ dates are not magnitude claims
# =============================================================================


def test_phase4_years_are_stripped_before_checking():
    assert _numbers_in("rose from 5.39B in Jan 2025 to 8.02B in May 2026") == [
        "5.39B",
        "8.02B",
    ]


def test_phase4_year_month_dates_are_stripped_before_checking():
    assert _numbers_in("revenue in 2025-01 was 5.39B") == ["5.39B"]


def test_phase4_full_iso_dates_are_stripped_before_checking():
    assert _numbers_in("on 2025-01-31 revenue was 5.39B") == ["5.39B"]


def test_phase4_quarters_are_stripped_before_checking():
    assert _numbers_in("Q3 revenue reached 8.02B") == ["8.02B"]
    assert _numbers_in("q3 revenue reached 8.02B") == ["8.02B"]


def test_phase4_a_dated_finding_is_kept():
    """★ The real bug: the years failed grounding and sank the whole finding."""
    f = _finding("Revenue rose from 5.39B in Jan 2025 to 8.02B in May 2026.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_year_month_dated_finding_is_kept():
    f = _finding("Revenue in 2025-01 was 5.39B, rising to 8.02B by 2026-05.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_quarter_dated_finding_is_kept():
    f = _finding("Q1 revenue was 5.39B and Q4 revenue reached 8.02B.")

    assert _kept_texts([f], REVENUE_VIZ) == [f["text"]]


def test_phase4_a_bare_year_would_not_survive_the_grounding_check():
    """Why the stripping is load-bearing: a year is not near any measure."""
    assert _is_grounded("2025", [5386520580.0, 8019430000.0]) is False


# =============================================================================
# 4. the small pieces
# =============================================================================


@pytest.mark.parametrize(
    "token,expected",
    [
        ("11,499", 11499.0),
        ("11,488.57", 11488.57),
        ("5.39B", 5.39e9),
        ("104.8M", 1.048e8),
        ("9.5K", 9500.0),
        ("48.8%", 48.8),
        ("0", 0.0),
    ],
)
def test_phase4_canonical_reads_written_figures(token, expected):
    assert _canonical(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["", "   ", "B", "abc", "%"])
def test_phase4_canonical_returns_none_for_non_figures(token):
    assert _canonical(token) is None


def test_phase4_numbers_in_handles_empty_text():
    assert _numbers_in("") == []


def test_phase4_is_grounded_rejects_an_unmatched_magnitude():
    assert _is_grounded("87.2B", [5386520580.0]) is False


def test_phase4_is_grounded_accepts_an_exact_match():
    assert _is_grounded("5,386,520,580", [5386520580.0]) is True


# =============================================================================
# 5. parse_response
# =============================================================================

_GOOD = {
    "headline": "Revenue nearly doubled across the period.",
    "findings": [{"text": "Revenue rose to 8.02B.", "viz_id": "v_revenue"}],
}


def test_phase4_parse_response_reads_bare_json():
    out = parse_response(json.dumps(_GOOD))

    assert out["headline"] == _GOOD["headline"]
    assert out["findings"] == _GOOD["findings"]


def test_phase4_parse_response_reads_a_json_fence():
    raw = "```json\n" + json.dumps(_GOOD) + "\n```"

    assert parse_response(raw)["headline"] == _GOOD["headline"]


def test_phase4_parse_response_reads_an_unlabelled_fence():
    raw = "```\n" + json.dumps(_GOOD) + "\n```"

    assert parse_response(raw)["headline"] == _GOOD["headline"]


def test_phase4_parse_response_reads_json_surrounded_by_chatter():
    raw = (
        "Sure — here is the summary panel you asked for:\n"
        + json.dumps(_GOOD)
        + "\nLet me know if you would like it shorter."
    )

    assert parse_response(raw)["headline"] == _GOOD["headline"]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "I could not summarise this dashboard.",
        "{not json at all}",
        "}{",
    ],
)
def test_phase4_parse_response_returns_none_for_garbage(raw):
    assert parse_response(raw) is None


@pytest.mark.parametrize(
    "obj",
    [
        {"findings": []},
        {"headline": "", "findings": []},
        {"headline": None, "findings": []},
    ],
)
def test_phase4_parse_response_returns_none_without_a_headline(obj):
    """No headline, no panel — there is nothing to render."""
    assert parse_response(json.dumps(obj)) is None


@pytest.mark.parametrize(
    "findings",
    ["not a list", 5, {"text": "one finding, wrong shape"}, None],
)
def test_phase4_parse_response_coerces_non_list_findings_to_empty(findings):
    out = parse_response(json.dumps({"headline": "H", "findings": findings}))

    assert out is not None
    assert out["findings"] == []


def test_phase4_parse_response_missing_findings_key_is_a_list():
    out = parse_response(json.dumps({"headline": "H"}))

    assert out["findings"] == []


# =============================================================================
# 6. build_prompt
# =============================================================================


def test_phase4_build_prompt_includes_the_visualization_data():
    prompt = build_prompt("Sales overview", REVENUE_VIZ)

    assert "5386520580" in prompt
    assert "v_revenue" in prompt
    assert "Revenue by month" in prompt


def test_phase4_build_prompt_includes_every_visualization():
    prompt = build_prompt("Sales overview", REVENUE_VIZ)

    assert "v_branches" in prompt
    assert "812440300" in prompt


def test_phase4_build_prompt_names_the_dashboard():
    assert "Sales overview" in build_prompt("Sales overview", REVENUE_VIZ)


def test_phase4_build_prompt_states_the_grounding_rule():
    prompt = build_prompt("Sales overview", REVENUE_VIZ)

    assert "come from the data" in prompt
    assert "rejected" in prompt


def test_phase4_build_prompt_asks_for_the_json_shape_it_parses():
    prompt = build_prompt("Sales overview", REVENUE_VIZ)

    assert "headline" in prompt
    assert "findings" in prompt


def test_phase4_build_prompt_survives_no_visualizations():
    prompt = build_prompt("Empty dashboard", [])

    assert "Empty dashboard" in prompt
    assert "come from the data" in prompt


# =============================================================================
# 7. the empty and malformed edges
# =============================================================================


@pytest.mark.parametrize("findings", [[], None])
def test_phase4_no_findings_is_not_a_crash(findings):
    assert verify_findings(findings, REVENUE_VIZ) == ([], [])


@pytest.mark.parametrize("visualizations", [[], None])
def test_phase4_no_visualizations_is_not_a_crash(visualizations):
    kept, rejected = verify_findings([_finding("Revenue reached 8.02B.")], visualizations)

    assert kept == []
    assert len(rejected) == 1


def test_phase4_a_figureless_finding_survives_empty_visualizations():
    f = _finding("Revenue rose steadily.")

    assert _kept_texts([f], []) == [f["text"]]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_phase4_a_blank_finding_is_neither_kept_nor_reported(text):
    """Nothing to publish and nothing to complain about."""
    assert verify_findings([{"text": text, "viz_id": "v_revenue"}], REVENUE_VIZ) == ([], [])


def test_phase4_a_malformed_finding_entry_does_not_crash():
    kept, rejected = verify_findings([None, {}, _finding("Revenue rose steadily.")], REVENUE_VIZ)

    assert [f["text"] for f in kept] == ["Revenue rose steadily."]


def test_phase4_rows_that_are_not_dicts_are_ignored():
    viz = [{"id": "v", "title": "odd", "row_count": 2, "rows": ["not a row", 42]}]

    kept, rejected = verify_findings([_finding("Revenue reached 8.02B.")], viz)

    assert kept == []


def test_phase4_booleans_are_not_collected_as_magnitudes():
    """`True` is 1 in Python — a flag column must not become a figure."""
    from app.services.artifact_insights import _data_magnitudes

    viz = [
        {
            "id": "v",
            "title": "flags",
            "row_count": 1,
            "rows": [{"is_member": True, "is_online": False, "orders": 42}],
        }
    ]

    mags = _data_magnitudes(viz)
    # The pool deliberately holds more than the raw cells — column totals and
    # means, and per-group totals — because a truthful finding may cite a sum
    # that appears in no single cell. So assert the CONTRACT, not the exact
    # list: the boolean columns contributed nothing, and the real number did.
    assert 42.0 in mags
    assert all(m == 42.0 for m in mags), f"boolean columns leaked into {mags}"
    assert 1.0 not in mags and 0.0 not in mags
