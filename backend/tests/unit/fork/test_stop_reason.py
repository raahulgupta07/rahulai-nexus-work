"""A turn that stopped early must not look like one that finished.

A run ends at five places in the agent loop: the planner declaring itself done,
three circuit breakers, and an invalid-output ceiling. Four of those five used
to leave exactly the same row behind as a normal finish — a step count, a
duration, and no answer. The reason existed as a local variable and a log line,
so from the report, from the API, and from any later investigation, "the model
decided it was done" and "a breaker fired" were the same event.

That is why nobody could say which one ended a given run. These tests pin the
vocabulary and the one rule that matters: every early ending has words, and the
ordinary ending has none.
"""

import pytest

from app.ai.agent_v2 import (
    EARLY_STOPS,
    STOP_ARTIFACT_CAP,
    STOP_INVALID_OUTPUT,
    STOP_PLANNER_DONE,
    STOP_REASON_TEXT,
    STOP_REPEATED_CALLS,
    STOP_TOOL_FAILURES,
    stop_reason_text,
)
from app.ai.inspection_budget import InspectionBudget


ALL_REASONS = (
    STOP_PLANNER_DONE,
    STOP_TOOL_FAILURES,
    STOP_REPEATED_CALLS,
    STOP_ARTIFACT_CAP,
    STOP_INVALID_OUTPUT,
)


def test_there_is_a_reason_for_every_way_a_run_can_end():
    """Five termination sites, five names. A sixth site added without a name
    here is a run that ends silently again."""
    assert len(set(ALL_REASONS)) == 5


@pytest.mark.parametrize("reason", EARLY_STOPS)
def test_every_early_ending_has_words(reason):
    text = stop_reason_text(reason)

    assert text, f"{reason} would render as a blank explanation"
    assert text.startswith("Stopped early"), text


def test_the_ordinary_ending_says_nothing():
    """A banner on every successful answer is noise, and noise gets ignored —
    which is how the real ones would stop being read."""
    assert stop_reason_text(STOP_PLANNER_DONE) is None


def test_the_planner_finishing_is_not_an_early_stop():
    assert STOP_PLANNER_DONE not in EARLY_STOPS


def test_a_breaker_ending_is_an_early_stop():
    for reason in (STOP_TOOL_FAILURES, STOP_REPEATED_CALLS, STOP_ARTIFACT_CAP):
        assert reason in EARLY_STOPS


def test_the_invalid_output_ceiling_is_an_early_stop():
    """It also flips the completion to 'error', which is why the report page
    cannot gate the note on a successful status alone."""
    assert STOP_INVALID_OUTPUT in EARLY_STOPS


def test_no_reason_at_all_renders_nothing():
    """A run still in flight, or one that ended on a path with no name yet."""
    assert stop_reason_text(None) is None
    assert stop_reason_text("") is None


def test_an_unknown_reason_renders_nothing_rather_than_a_placeholder():
    """A label that says nothing is worse than no label — it occupies the spot
    where a real explanation would have gone."""
    assert stop_reason_text("something_new") is None


def test_the_text_is_written_for_a_person_not_a_log():
    """These strings go under the answer, where the reader is deciding whether
    to trust it."""
    for reason, text in STOP_REASON_TEXT.items():
        assert text.endswith("."), reason
        assert "_" not in text, f"{reason} leaks an identifier into the UI: {text}"
        assert len(text) < 90, f"{reason} is too long for a footer line"


# ── the inspection budget's own notice ───────────────────────────────────────


def test_the_budget_tells_the_reader_and_not_only_the_planner():
    """★The planner has always been told. The reader never was: the inspection
    tool simply stopped being offered, the planner answered with what it had,
    and the report showed an ordinary completed turn."""
    budget = InspectionBudget(budget_ms=180_000)
    budget.record("inspect_data", 190_000)

    assert budget.exhausted
    notice = budget.user_notice()

    assert "190s" in notice
    assert "answered with what had been gathered" in notice


def test_the_two_notices_are_written_for_different_readers():
    budget = InspectionBudget(budget_ms=1_000)
    budget.record("inspect_data", 2_000)

    planner_text = budget.notice()
    reader_text = budget.user_notice()

    assert planner_text != reader_text
    # The planner's copy carries instructions; the reader's must not.
    assert "answer from" in planner_text
    assert "answer from" not in reader_text
    assert "budget" not in reader_text.lower()


def test_an_unspent_budget_still_produces_a_sentence():
    """Called only on exhaustion today, but a notice that crashes when the
    numbers are small is a trap for the next caller."""
    assert InspectionBudget().user_notice()
