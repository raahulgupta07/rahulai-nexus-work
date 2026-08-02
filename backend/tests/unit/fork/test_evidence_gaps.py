"""An answer built on less than it needed has to say so.

Three things can quietly remove evidence from a turn — a query abandoned at the
hard limit, an exhausted inspection budget, an unresolvable file — and in every
case the planner carried on and answered with what remained. That part is
correct. Presenting the remainder as the answer is not.

"H1 completed calls: 7,412" over four of six months is not a smaller answer. It
is the wrong one, and on screen it is indistinguishable from the right one.
"""

from app.ai.evidence_gaps import (
    GAP_FILE_UNRESOLVED,
    GAP_INSPECTION_BUDGET,
    GAP_QUERY_TIMEOUT,
    EvidenceGap,
    as_dicts,
    gaps_from_query_timings,
    has_data_gap,
    planner_notice,
    reader_notice,
    record_gap,
)


def _ctx():
    return {"evidence_gaps": []}


# ── recording ────────────────────────────────────────────────────────────────


def test_a_gap_is_recorded_with_the_subject_a_reader_recognises():
    """An id is not a subject. "May'25" is; "d203" is not."""
    ctx = _ctx()
    record_gap(ctx, GAP_QUERY_TIMEOUT, "MM Conso Data Report (May'25)", "exceeded 900s")

    assert as_dicts(ctx["evidence_gaps"]) == [
        {
            "kind": "query_timeout",
            "subject": "MM Conso Data Report (May'25)",
            "detail": "exceeded 900s",
        }
    ]


def test_the_same_subject_is_not_recorded_twice():
    """A retried query that times out again is one missing source, not two."""
    ctx = _ctx()
    record_gap(ctx, GAP_QUERY_TIMEOUT, "sales.orders")
    record_gap(ctx, GAP_QUERY_TIMEOUT, "sales.orders")

    assert len(ctx["evidence_gaps"]) == 1


def test_the_same_subject_missing_for_two_reasons_is_two_gaps():
    ctx = _ctx()
    record_gap(ctx, GAP_QUERY_TIMEOUT, "sales.orders")
    record_gap(ctx, GAP_FILE_UNRESOLVED, "sales.orders")

    assert len(ctx["evidence_gaps"]) == 2


def test_recording_without_a_context_does_not_explode():
    """Tools run in paths that never built one. Losing the note is bad; taking
    the turn down with it is worse."""
    record_gap(None, GAP_QUERY_TIMEOUT, "x")
    record_gap({}, GAP_QUERY_TIMEOUT, "x")


def test_an_empty_subject_still_reads_as_a_sentence():
    ctx = _ctx()
    record_gap(ctx, GAP_QUERY_TIMEOUT, "   ")

    assert ctx["evidence_gaps"][0].subject == "an unnamed source"


# ── from query timings ───────────────────────────────────────────────────────


def test_a_timed_out_query_becomes_a_gap():
    """The timing entry is the only place a timeout survives — the exception is
    caught and turned into a message the planner may or may not act on."""
    ctx = _ctx()
    gaps_from_query_timings(ctx, [
        {"error_type": "timeout", "sql": "SELECT * FROM may_2025", "timeout_seconds": 900},
    ])

    assert len(ctx["evidence_gaps"]) == 1
    assert "may_2025" in ctx["evidence_gaps"][0].subject
    assert "900" in ctx["evidence_gaps"][0].detail


def test_an_ordinary_query_error_is_not_a_gap():
    """A syntax error is a step that failed and will be retried. A timeout is
    data that is not coming."""
    ctx = _ctx()
    gaps_from_query_timings(ctx, [{"error": "syntax error", "sql": "SELEC 1"}])

    assert ctx["evidence_gaps"] == []


def test_a_successful_query_is_not_a_gap():
    ctx = _ctx()
    gaps_from_query_timings(ctx, [{"query_ms": 12.0, "rows": 5}])

    assert ctx["evidence_gaps"] == []


# ── what the planner is told ─────────────────────────────────────────────────


def test_no_gaps_means_no_instruction():
    """A warning on every answer is noise, and noise gets ignored."""
    assert planner_notice([]) == ""
    assert planner_notice(None) == ""


def test_the_planner_is_forbidden_from_presenting_it_as_complete():
    notice = planner_notice([EvidenceGap(GAP_QUERY_TIMEOUT, "May'25")])

    assert "MUST NOT present your result as complete" in notice
    assert "May'25" in notice


def test_missing_data_forbids_totalling_the_rest():
    """★The rule that matters. Four months of a six-month range must not be
    summed and called the range."""
    notice = planner_notice([EvidenceGap(GAP_QUERY_TIMEOUT, "May'25")])

    assert "do NOT total or average the rest" in notice
    assert "A wrong total is worse than an incomplete one" in notice


def test_a_curtailed_inspection_does_not_forbid_totalling():
    """Thinner context is not missing data — the numbers it did fetch are whole.
    Over-warning here would make the real warning unremarkable."""
    notice = planner_notice([EvidenceGap(GAP_INSPECTION_BUDGET, "further data inspection")])

    assert "MUST NOT present your result as complete" in notice
    assert "do NOT total" not in notice


def test_only_data_gaps_count_as_missing_data():
    assert has_data_gap([EvidenceGap(GAP_QUERY_TIMEOUT, "x")])
    assert has_data_gap([EvidenceGap(GAP_FILE_UNRESOLVED, "x")])
    assert not has_data_gap([EvidenceGap(GAP_INSPECTION_BUDGET, "x")])
    assert not has_data_gap([])


# ── what the reader is told ──────────────────────────────────────────────────


def test_the_reader_is_told_what_is_missing_not_that_something_is():
    notice = reader_notice([
        EvidenceGap(GAP_QUERY_TIMEOUT, "May'25"),
        EvidenceGap(GAP_QUERY_TIMEOUT, "Jun'25"),
    ])

    assert notice == "Incomplete — could not reach: May'25, Jun'25."


def test_a_long_list_says_how_many_it_left_out():
    """A truncation that does not admit it is the same lie in miniature."""
    notice = reader_notice([EvidenceGap(GAP_QUERY_TIMEOUT, f"s{i}") for i in range(6)])

    assert "and 3 more" in notice


def test_nothing_missing_means_no_banner():
    assert reader_notice([]) == ""


def test_the_two_notices_speak_to_different_readers():
    gaps = [EvidenceGap(GAP_QUERY_TIMEOUT, "May'25")]

    planner = planner_notice(gaps)
    reader = reader_notice(gaps)

    assert "MUST NOT" in planner
    assert "MUST NOT" not in reader
    assert len(reader) < 120, "this one sits above the answer; it has to be short"
