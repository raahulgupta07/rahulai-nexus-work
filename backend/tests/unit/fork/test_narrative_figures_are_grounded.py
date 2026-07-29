"""The chat narrative stated figures that appear nowhere in the run's data.

Found by: reading the prose the agent wrote beside a dashboard and then
independently summing every dataset the run had stored. The narrative claimed a
total, an order count and an average. None of the three matched anything — not
the preview copy of the dataset, not the artifact-width copy, not the aggregate
the dashboard was built on. The model wrote them from its own working memory
during exploration and never re-derived them.

The insight panel directly ABOVE that same dashboard was correct, because
`artifact_insights.verify_findings` checks every figure against the artifact's
own data and drops the ones it cannot justify. Two surfaces, one run, one set of
numbers, and only one of them was being held to it. That asymmetry was the whole
defect, so the check now lives in `figure_grounding` and both surfaces import it.

Contract these tests pin:
  * a figure the run's data cannot justify is REMOVED from the narrative
  * a sentence whose figures are all grounded survives BYTE-IDENTICAL
  * a percentage passes — it is computed FROM values and will not appear in them
  * years and year-months are not magnitude claims and never sink a sentence
  * rounding is legitimate reporting: a figure written to three significant
    figures is judged at three significant figures
  * no datasets -> the narrative passes through untouched (fail OPEN)
  * the verifier raising -> the narrative passes through untouched (fail OPEN)
  * the flag off -> the narrative passes through untouched
  * a narrative with nothing left says so, rather than being blank
  * `artifact_insights` and the agent both use the ONE definition — a future
    copy-paste of this logic into a second file fails here

Every fixture is synthetic. The check knows nothing about any column, currency,
table, magnitude or domain, and neither does anything below.
"""
import types

import pytest

from app.services import figure_grounding


# --- fixtures: invented data, in the shape the check takes ------------------
#
# A dataset is `{"rows": [...]}` — the same shape a dashboard's visualizations
# arrive in, which is why one verifier serves both surfaces.


def _dataset(rows):
    return [{"rows": rows}]


# Column total 10,000; mean 5,000; group totals 4,000 and 6,000.
SIMPLE = _dataset([
    {"label": "alpha", "value": 4000},
    {"label": "beta", "value": 6000},
])


# --- the defect -------------------------------------------------------------


def test_a_fabricated_figure_is_removed_from_the_narrative():
    narrative = (
        "The total across all groups is 10,000. "
        "The overall figure reached 12,345."
    )
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert verdict.checked is True
    assert "12,345" not in verdict.text, (
        "a figure the run's data cannot justify survived into the narrative"
    )
    assert "10,000" in verdict.text, "the grounded sentence was collateral damage"
    assert verdict.dropped, "the removal was not recorded for the log"


def test_the_precision_of_the_writing_is_the_tolerance():
    """A flat percentage tolerance provably cannot separate these two.

    8,653 against a true 8,642.31 is 0.12% out — a fabrication. "8.64K" against
    the same value is 0.03% out — correct reporting. Any flat tolerance either
    admits the first or rejects the second. What separates them is how precisely
    each was WRITTEN: "8,653" claims the unit and misses by twenty units;
    "8.64K" claims a hundredth of a thousand and lands inside it.
    """
    data = _dataset([{"label": "alpha", "value": 8642.31}])

    fabricated = figure_grounding.verify_narrative("The value is 8,653.", data)
    assert "8,653" not in fabricated.text, (
        "a figure 0.12% off the truth was accepted — the tolerance has gone flat"
    )

    rounded = figure_grounding.verify_narrative("The value is 8.64K.", data)
    assert rounded.text == "The value is 8.64K.", (
        "legitimate rounding was called a fabrication"
    )


# --- the allowances, pinned as hard as the rejections ------------------------


def test_a_grounded_narrative_is_returned_untouched():
    narrative = (
        "The total across all groups is 10,000.\n"
        "The leading group contributes 6,000, the other 4,000."
    )
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert verdict.text == narrative, "a correct narrative was edited"
    assert verdict.dropped == []
    assert verdict.changed is False


def test_a_percentage_passes():
    """Percentages are computed FROM the values and never appear among them."""
    narrative = "The leading group holds 60.0% of the total, up 12.5%."
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert verdict.text == narrative, "a derived percentage was treated as a claim"


def test_years_and_periods_are_not_magnitude_claims():
    """"between 2023 and 2025" holds two number-like tokens and no claims."""
    narrative = "Between 2023 and 2025 the total was 10,000."
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)
    assert verdict.text == narrative, "a year was checked as if it were a measure"

    dated = "In 2024-06 the leading group contributed 6,000."
    assert figure_grounding.verify_narrative(dated, SIMPLE).text == dated


def test_small_structural_integers_survive():
    """"the top 3 of 8 groups" describes shape, not a measured quantity."""
    narrative = "The top 3 of 8 groups account for 10,000."
    assert figure_grounding.verify_narrative(narrative, SIMPLE).text == narrative


# --- failing open -----------------------------------------------------------


def test_fails_open_when_the_run_data_cannot_be_resolved():
    """No datasets is indistinguishable from a broken lookup. Say nothing."""
    narrative = "The overall figure reached 12,345."

    assert figure_grounding.verify_narrative(narrative, []).text == narrative
    assert figure_grounding.verify_narrative(narrative, None).text == narrative
    assert figure_grounding.verify_narrative(narrative, [{"rows": []}]).text == narrative
    assert figure_grounding.verify_narrative(narrative, []).checked is False


def test_fails_open_when_the_check_itself_throws(monkeypatch):
    """A broken verifier must never blank an answer."""
    def _explode(_datasets):
        raise RuntimeError("verifier is broken")

    monkeypatch.setattr(figure_grounding, "data_magnitudes", _explode)

    narrative = "The overall figure reached 12,345."
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert verdict.text == narrative, "a throwing verifier ate the answer"
    assert verdict.checked is False


def test_the_flag_turns_it_off(monkeypatch):
    monkeypatch.setattr(figure_grounding, "enabled", lambda: False)

    narrative = "The overall figure reached 12,345."
    assert figure_grounding.verify_narrative(narrative, SIMPLE).text == narrative


def test_the_flag_is_declared_so_it_can_be_switched():
    """★ `_read_bool_setting` returns its default for a name `settings` does not
    carry, so an undeclared flag is read, found missing, and silently ignored."""
    from app.settings.config import settings

    assert hasattr(settings, "hybrid_narrative_grounding")
    assert settings.hybrid_narrative_grounding is True, "the default is ON"


def test_the_flag_reader_does_not_trust_truthiness():
    """★ The string "off" is TRUTHY in Python. This has bitten the codebase
    three times, so the reader type-checks rather than reading truthiness."""
    from app.ai.tools.implementations.create_artifact import _read_bool_setting

    class _Fake:
        hybrid_narrative_grounding = "off"

    import app.settings.config as config_module
    real = config_module.settings
    config_module.settings = _Fake()
    try:
        assert _read_bool_setting("hybrid_narrative_grounding", True) is False
    finally:
        config_module.settings = real


# --- what "removed" means ----------------------------------------------------


def test_an_emptied_narrative_says_so_rather_than_going_blank():
    """An empty answer reads as a crash, and inventing a replacement summary is
    the very thing this check exists to prevent. So: state the situation, with
    no figure in it."""
    narrative = "The overall figure reached 12,345. A second total came to 98,765."
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert verdict.text.strip(), "the answer was blanked"
    assert "12,345" not in verdict.text and "98,765" not in verdict.text
    assert verdict.text == figure_grounding.EMPTY_NARRATIVE_NOTICE
    assert not figure_grounding.numbers_in(figure_grounding.EMPTY_NARRATIVE_NOTICE), (
        "the fallback notice must not itself state a figure"
    )


def test_an_emptied_bullet_does_not_survive_as_a_dangling_marker():
    narrative = (
        "- The total across all groups is 10,000.\n"
        "- The overall figure reached 12,345."
    )
    verdict = figure_grounding.verify_narrative(narrative, SIMPLE)

    assert "10,000" in verdict.text
    assert "12,345" not in verdict.text
    for line in verdict.text.split("\n"):
        assert line.strip() not in ("-", "*", "+"), f"dangling marker left: {line!r}"


def test_a_decimal_does_not_split_a_sentence():
    """`1.5` has no space after its point, so sentence splitting cannot cut it."""
    data = _dataset([{"label": "alpha", "value": 1.5}])
    narrative = "The value is 1.5 in this run."
    assert figure_grounding.verify_narrative(narrative, data).text == narrative


# --- ONE definition ----------------------------------------------------------


def test_artifact_insights_uses_the_shared_definition():
    """Not "behaves the same" — IS the same object. A future copy-paste of this
    logic into `artifact_insights` fails here rather than drifting quietly."""
    from app.services import artifact_insights

    assert artifact_insights._is_grounded is figure_grounding.is_grounded
    assert artifact_insights._numbers_in is figure_grounding.numbers_in
    assert artifact_insights._canonical is figure_grounding.canonical
    assert artifact_insights._data_magnitudes is figure_grounding.data_magnitudes
    assert artifact_insights._write_precision_slack is figure_grounding.write_precision_slack


def test_artifact_insights_behaviour_is_unchanged():
    """The insight panel's own contract, re-pinned from the far side of the move."""
    from app.services.artifact_insights import verify_findings

    findings = [
        {"text": "The total across all groups is 10,000."},
        {"text": "The overall figure reached 12,345."},
    ]
    kept, rejected = verify_findings(findings, SIMPLE)

    assert [f["text"] for f in kept] == ["The total across all groups is 10,000."]
    assert len(rejected) == 1


def test_the_agent_grounds_the_answer_it_is_about_to_persist():
    """The seam: `_ground_final_answer` edits `decision.final_answer` in place,
    which is what the decision.final SSE, the saved PlanDecision and the
    completion block all read."""
    import asyncio

    from app.ai.agent_v2 import AgentV2

    decision = types.SimpleNamespace(
        final_answer="The total across all groups is 10,000. The overall figure reached 12,345."
    )
    stub = types.SimpleNamespace()

    async def _run_datasets():
        return SIMPLE

    stub._run_datasets = _run_datasets

    asyncio.run(AgentV2._ground_final_answer(stub, decision))

    assert "12,345" not in decision.final_answer, (
        "the agent persisted a figure the run's data cannot justify"
    )
    assert "10,000" in decision.final_answer


def test_the_agent_fails_open_when_the_run_data_cannot_be_resolved():
    import asyncio

    from app.ai.agent_v2 import AgentV2

    original = "The overall figure reached 12,345."
    decision = types.SimpleNamespace(final_answer=original)
    stub = types.SimpleNamespace()

    async def _run_datasets():
        raise RuntimeError("no db")

    stub._run_datasets = _run_datasets

    asyncio.run(AgentV2._ground_final_answer(stub, decision))
    assert decision.final_answer == original, "a broken lookup ate the answer"


def test_the_narrative_check_is_wired_into_the_final_answer_path():
    """One seam, not scattered: the call sits in the planner's decision.final
    branch, before the SSE emit and before persistence."""
    import inspect

    from app.ai import agent_v2

    source = inspect.getsource(agent_v2)
    assert "await self._ground_final_answer(decision)" in source
    assert "figure_grounding" in source, (
        "the agent must use the shared definition, not a local copy"
    )
