"""The figure check ran against a field that is empty on 96% of turns.

`_ground_final_answer` read and wrote `decision.final_answer` and returned at
its first line when that was blank. Measured on live Postgres, `plan_decisions`
joined to `completion_blocks` with `analysis_complete = true`:

  * 406 completed decisions had a rendered block
  * 390 of them had `final_answer IS NULL`
  * in ALL 390, `completion_blocks.content` was BYTE-EQUAL to
    `plan_decisions.assistant`

`project_manager.upsert_block_for_decision` says why in one line —
`content = plan_decision.final_answer or plan_decision.assistant` — so the text
the user reads is `assistant_message` almost every time. The verifier was
looking at the other field, finding nothing, and returning silently. It had
never removed a sentence in production and nothing said so.

Two further gaps went with it:

  * the evidence pool was steps reached through a widget on the report, so a
    turn that answers with numbers without creating a widget had NOTHING to
    check against. 169 of 405 completed turns (42%) resolved no dataset at all,
    and 46 of those stated a four-or-more-digit figure. The tool mix on exactly
    those turns was `inspect_data` (21) and `read_file` (5) — exploration,
    which is where the observed fabrication came from.
  * neither early return logged anything, so "checked and clean" and "never
    ran" were indistinguishable in production.

What these tests pin:
  * the `assistant`-carried answer is grounded — and the PRE-FIX shape,
    reconstructed here, is required to FAIL that, so the guard cannot pass by
    agreeing with whatever the file happens to say
  * a grounded answer survives BYTE-IDENTICAL (positive control for every
    refusal below — a removal-only test passes on a totally broken feature)
  * `reasoning_message` is deliberately NOT edited
  * the transcript's memory of the last thing said follows the edit
  * numbers printed by a tool result become evidence; the model's own generated
    code does not; a tool result's numeric METADATA does not
  * exactly one summary line per turn, whether or not anything was dropped

No database. Every fixture is invented, and nothing here knows a column, a
table, a currency or a domain.
"""
import asyncio
import inspect
import logging
import types

import pytest

from app.ai.agent_v2 import AgentV2
from app.services import figure_grounding


# Column total 10,000; mean 5,000; group totals 4,000 and 6,000.
SIMPLE = [{"rows": [
    {"label": "alpha", "value": 4000},
    {"label": "beta", "value": 6000},
]}]

GROUNDED = "The total across all groups is 10,000."
FABRICATED = "The overall figure reached 12,345."


def _agent_stub(datasets=SIMPLE, last_assistant_text=None):
    """The smallest thing `_ground_final_answer` needs to run.

    It takes `self` only for `_run_datasets` and `_last_assistant_text`; the
    field list is read off the class precisely so a stub like this one cannot
    silently reduce the method to a no-op.
    """
    stub = types.SimpleNamespace(_last_assistant_text=last_assistant_text)

    async def _run_datasets():
        return datasets

    stub._run_datasets = _run_datasets
    return stub


def _ground(decision, datasets=SIMPLE, stub=None):
    asyncio.run(AgentV2._ground_final_answer(stub or _agent_stub(datasets), decision))


# --- the defect, with its own red proof -------------------------------------


def test_the_answer_carried_by_the_assistant_field_is_grounded():
    """THE load-bearing case: 390 of 406 live turns look exactly like this."""
    decision = types.SimpleNamespace(
        final_answer=None,
        assistant_message=f"{GROUNDED} {FABRICATED}",
        reasoning_message=None,
    )

    _ground(decision)

    assert "12,345" not in decision.assistant_message, (
        "the field the user actually reads was persisted with a figure the "
        "run's data cannot justify"
    )
    assert "10,000" in decision.assistant_message, "the grounded sentence was collateral damage"


def test_the_original_defect_is_still_detected():
    """Carry the red proof INSIDE the guard.

    Reconstruct the pre-fix shape — grounding `final_answer` and nothing else —
    and require it to LEAVE the fabrication in `assistant_message`. Without
    this, the test above passes for as long as the tuple happens to name the
    right field and stops meaning anything the moment someone edits the tuple
    to match a rename. A proof done once at a shell prompt rots into a comment;
    one that runs every time cannot.
    """
    async def _pre_fix(self, decision):
        text = getattr(decision, "final_answer", None)
        if not text or not str(text).strip():
            return
        verdict = figure_grounding.verify_narrative(str(text), await self._run_datasets())
        if verdict.changed:
            decision.final_answer = verdict.text

    decision = types.SimpleNamespace(
        final_answer=None,
        assistant_message=f"{GROUNDED} {FABRICATED}",
    )
    asyncio.run(_pre_fix(_agent_stub(), decision))

    assert "12,345" in decision.assistant_message, (
        "the reconstructed pre-fix shape removed the fabrication, so this "
        "guard can no longer tell the fix from the bug — re-derive it before "
        "changing it"
    )

    assert "assistant_message" in AgentV2._USER_VISIBLE_PROSE_FIELDS
    assert "final_answer" in AgentV2._USER_VISIBLE_PROSE_FIELDS


def test_the_final_answer_field_is_still_grounded():
    """The 16-of-406 case. The fix must widen the check, not move it."""
    decision = types.SimpleNamespace(
        final_answer=f"{GROUNDED} {FABRICATED}",
        assistant_message=None,
    )
    _ground(decision)

    assert "12,345" not in decision.final_answer
    assert "10,000" in decision.final_answer


# --- positive controls -------------------------------------------------------


def test_a_grounded_answer_survives_byte_identical():
    """★Without this every assertion above is satisfied by deleting the text."""
    text = "The total across all groups is 10,000.\nThe leading group contributes 6,000."
    decision = types.SimpleNamespace(final_answer=None, assistant_message=text)

    _ground(decision)

    assert decision.assistant_message == text, "a correct answer was edited"


def test_nothing_is_touched_when_the_run_data_cannot_be_resolved():
    """Fail OPEN. No datasets is indistinguishable from a broken lookup."""
    decision = types.SimpleNamespace(final_answer=None, assistant_message=FABRICATED)

    _ground(decision, datasets=[])

    assert decision.assistant_message == FABRICATED


def test_a_broken_lookup_never_costs_the_user_an_answer():
    stub = types.SimpleNamespace()

    async def _boom():
        raise RuntimeError("no db")

    stub._run_datasets = _boom
    decision = types.SimpleNamespace(final_answer=None, assistant_message=FABRICATED)

    _ground(decision, stub=stub)

    assert decision.assistant_message == FABRICATED


# --- what is deliberately NOT grounded ---------------------------------------


def test_the_reasoning_transcript_is_left_alone():
    """A deliberate product decision, pinned so a later change is a decision.

    `reasoning_message` is user-facing, but it is presented as PROCESS. It is
    also the only record of how a figure was arrived at — the evidence someone
    would need to diagnose the very fabrication the answer-side check removed.
    Editing it would doctor the transcript of the model's thinking.
    """
    reasoning = f"I computed this from the exploration output. {FABRICATED}"
    decision = types.SimpleNamespace(
        final_answer=None,
        assistant_message=GROUNDED,
        reasoning_message=reasoning,
    )

    _ground(decision)

    assert decision.reasoning_message == reasoning
    assert "reasoning_message" not in AgentV2._USER_VISIBLE_PROSE_FIELDS


# --- the conversation, not just the answer -----------------------------------


def test_the_transcript_memory_follows_the_edit():
    """The agent keeps the last thing it told the user, captured DURING
    streaming from the unedited text. Left alone, the model reads back the
    sentence that was removed and restates the fabricated figure next turn: the
    answer clean, the conversation not."""
    text = f"{GROUNDED} {FABRICATED}"
    stub = _agent_stub(last_assistant_text=text)
    decision = types.SimpleNamespace(final_answer=None, assistant_message=text)

    _ground(decision, stub=stub)

    assert stub._last_assistant_text == decision.assistant_message
    assert "12,345" not in stub._last_assistant_text


# --- the evidence pool -------------------------------------------------------


def test_a_number_printed_by_a_tool_becomes_evidence():
    """The coverage fix. 46 live turns stated a four-digit figure with no step
    to check it against, and their tools were `inspect_data` and `read_file` —
    whose output is printed text, not a stored dataset."""
    payloads = [{
        "success": True,
        "execution_log": "channel  net_amount\nalpha    87,412\nbeta     15,900\n",
    }]
    dataset = AgentV2._tool_result_dataset(payloads)

    assert dataset, "the printed output of an exploration tool yielded no evidence"
    verdict = figure_grounding.verify_narrative(
        "The leading channel reached 87,412.", [dataset]
    )
    assert verdict.checked is True, "the check still could not run"
    assert "87,412" in verdict.text, "a figure the tool printed was called invented"

    fabricated = figure_grounding.verify_narrative(
        "The leading channel reached 99,999.", [dataset]
    )
    assert "99,999" not in fabricated.text, (
        "the harvested pool grounds anything — the check has gone vacuous"
    )


def test_the_models_own_generated_code_is_not_evidence():
    """Evidence is what came BACK, never what went out. A literal the model
    chose to write in a WHERE clause must not ground a figure it then states."""
    payloads = [{"code": "df = client.execute_query('SELECT * FROM t WHERE amount > 987654')"}]

    assert AgentV2._tool_result_dataset(payloads) is None
    assert "code" in AgentV2._NON_EVIDENCE_RESULT_KEYS


def test_result_metadata_is_not_evidence():
    """Every numeric scalar in a tool-result envelope is metadata — durations,
    ids, byte counts. Measured live, `inspect_data` carries `execution_ms`,
    `codegen_ms` and `query_timings`. Admitting those would ground a four-figure
    fabrication with a millisecond count: the check would report `checked=True`
    and be worse than not running."""
    payloads = [{"success": True, "execution_ms": 87412, "total_chars": 99999}]

    assert AgentV2._tool_result_dataset(payloads) is None


def test_nested_text_is_reached_and_the_walk_is_bounded():
    out = []
    AgentV2._evidence_strings({"a": {"b": ["deep 4,242"]}}, out)
    assert any("4,242" in s for s in out)

    out = []
    AgentV2._evidence_strings({"logs": ["x"] * 10_000}, out)
    assert len(out) <= AgentV2._TOOL_RESULT_STRING_LIMIT


def test_the_evidence_query_stays_inside_this_report():
    """Scope is the whole point. Widen it past the report and the pool grows
    until every figure finds a match, and the check reports `checked=True`
    while accepting anything."""
    source = inspect.getsource(AgentV2._run_datasets)

    assert source.count("AgentExecution.report_id == report_id") == 2, (
        "a run-scoped query lost its report predicate"
    )
    assert "Widget.report_id == report_id" in source
    assert "seen_step_ids" in source, "a step reachable two ways is counted twice"


# --- silence must stop being ambiguous ---------------------------------------


def _summary_lines(records):
    return [r for r in records if "narrative grounding: checked=" in r.getMessage()]


def test_one_line_is_logged_when_nothing_was_dropped(caplog):
    """Zero log lines over 55,028 lines of container output was equally
    consistent with "every answer was clean" and "the check has never run"."""
    decision = types.SimpleNamespace(final_answer=None, assistant_message=GROUNDED)

    with caplog.at_level(logging.INFO, logger="app.ai.agent_v2"):
        _ground(decision)

    lines = _summary_lines(caplog.records)
    assert len(lines) == 1, f"expected exactly one summary line, got {len(lines)}"
    assert "checked=True" in lines[0].getMessage()
    assert "dropped=0" in lines[0].getMessage()
    assert lines[0].levelno == logging.INFO


def test_the_line_is_a_warning_when_a_sentence_was_dropped(caplog):
    decision = types.SimpleNamespace(
        final_answer=None, assistant_message=f"{GROUNDED} {FABRICATED}"
    )

    with caplog.at_level(logging.INFO, logger="app.ai.agent_v2"):
        _ground(decision)

    lines = _summary_lines(caplog.records)
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert "dropped=1" in lines[0].getMessage()


def test_a_turn_that_could_not_be_checked_says_so(caplog):
    """The case that hid the defect for its whole life."""
    decision = types.SimpleNamespace(final_answer=None, assistant_message=FABRICATED)

    with caplog.at_level(logging.INFO, logger="app.ai.agent_v2"):
        _ground(decision, datasets=[])

    lines = _summary_lines(caplog.records)
    assert len(lines) == 1
    assert "checked=False" in lines[0].getMessage()
    assert "datasets=0" in lines[0].getMessage()


def test_no_line_is_logged_for_a_turn_with_no_prose():
    """A tool-only decision writes nothing a user reads; one line per COMPLETED
    turn is the contract, not one line per planner loop."""
    decision = types.SimpleNamespace(final_answer=None, assistant_message=None)
    stub = types.SimpleNamespace()

    async def _never():
        raise AssertionError("the run's data was resolved for a decision with no prose")

    stub._run_datasets = _never
    _ground(decision, stub=stub)


# --- the seam ----------------------------------------------------------------


def test_the_check_is_still_wired_into_the_decision_final_path():
    from app.ai import agent_v2

    source = inspect.getsource(agent_v2)
    assert source.count("await self._ground_final_answer(decision)") == 1, (
        "the grounding call is scattered or gone; it belongs at the ONE point "
        "where the decision's text is finalised, before the SSE and before "
        "persistence"
    )
