"""A skill's catalog line must survive the catalog it is written for.

A skill is advertised to the agent as ONE line — its description — and that line
is the only thing read when deciding whether to pull the full text with
`read_instruction`. Longer than `SKILL_DESCRIPTION_MAX` and the builder trims it
with an ellipsis.

★ This is not hypothetical. All three shipped skills were over the limit for a
week (198, 216 and 181 characters against a 160 cap) and every one was cut
mid-word inside the "Read before ..." clause that says when to open it:

    "...proof that two columns join. Read befor…"
    "...refuses. Read before using recursive CT…"
    "...ming a peak or latest period from a GROU…"

Nothing raised, nothing logged, and no test in this repository touched skills at
all — which is exactly why it survived. This is that missing check.

★ It imports SKILL_DESCRIPTION_MAX rather than restating 160. A guard that
hard-codes the number it is guarding stops guarding the moment someone changes
the rule, and reports success while doing it.

★ It asserts on `get_builtin_skills()` — the values Python produces after the
implicit string concatenation — not on a regex over the source. The literals are
split across several lines, so a source-level check measures one fragment and
passes a description that is twice the limit.
"""
import pytest

from app.ai.context.sections.instructions_section import SKILL_DESCRIPTION_MAX
from app.ai.context.builders.instruction_context_builder import InstructionContextBuilder
from app.services.builtin_skills import get_builtin_skills


def _catalog_line(description: str) -> str:
    """The exact string the agent is shown, via the product's own function."""
    class _Row:
        pass

    row = _Row()
    row.description = description
    row.structured_data = None
    row.text = ""
    return InstructionContextBuilder._skill_description(row) or ""


@pytest.mark.parametrize("skill", get_builtin_skills(), ids=lambda s: s["slug"])
def test_builtin_skill_description_fits_the_catalog(skill):
    """Every shipped skill must be advertised in full, not trimmed."""
    desc = skill["description"]
    assert len(desc) <= SKILL_DESCRIPTION_MAX, (
        f"{skill['slug']}: description is {len(desc)} chars, over the "
        f"{SKILL_DESCRIPTION_MAX} the catalog allows. The agent would be shown "
        f"\"...{desc[:SKILL_DESCRIPTION_MAX - 3][-30:]}…\" — cut inside the "
        f"sentence that tells it when to use this skill. Shorten it."
    )


@pytest.mark.parametrize("skill", get_builtin_skills(), ids=lambda s: s["slug"])
def test_builtin_skill_line_is_not_trimmed_by_the_builder(skill):
    """Belt and braces: run the real builder, not just the arithmetic.

    The length assertion above could pass while the builder still trimmed, if
    the two ever disagreed about what counts (whitespace, the ellipsis budget).
    This asserts on the string the agent actually receives.
    """
    line = _catalog_line(skill["description"])
    assert not line.endswith("…"), (
        f"{skill['slug']}: the catalog line is trimmed — {line!r}"
    )
    assert line == skill["description"], (
        f"{skill['slug']}: the advertised line differs from the authored one"
    )


def test_the_guard_itself_catches_an_over_long_description():
    """The guard must fail on a bad description, or it is guarding nothing.

    A test that has only ever been seen passing proves the code is quiet, not
    that the check works. This pins the failure it exists to catch.
    """
    too_long = "x" * (SKILL_DESCRIPTION_MAX + 1)
    line = _catalog_line(too_long)
    assert line.endswith("…"), "the builder no longer trims — this guard is dead"
    # Trimmed output is 157 chars + a single-character ellipsis = 158. The cap
    # is a ceiling, not a target: asserting equality with SKILL_DESCRIPTION_MAX
    # fails, which is exactly what this test caught on its first run — the
    # ellipsis "…" is ONE character, not the three that the -3 budget implies.
    assert len(line) <= SKILL_DESCRIPTION_MAX, (
        f"trimmed line is {len(line)} chars, over the {SKILL_DESCRIPTION_MAX} cap"
    )
    assert len(line) < len(too_long), "trimming did not shorten anything"


def test_every_builtin_says_when_to_use_it():
    """The line has to carry its trigger, not just fit.

    Fitting is necessary, not sufficient: a description shortened by deleting
    the "Read before ..." clause would pass the length check and still leave the
    planner with nothing to match a question against. That clause is the reason
    the truncation mattered in the first place.
    """
    missing = [s["slug"] for s in get_builtin_skills()
               if "read before" not in (s["description"] or "").lower()]
    assert not missing, (
        f"these skills no longer say when to use them: {missing}. A catalog line "
        f"that only describes the topic gives the planner nothing to trigger on."
    )
