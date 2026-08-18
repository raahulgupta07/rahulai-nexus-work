"""Three lookups answered an ambiguous name by taking whichever row came first.

Each one produced a REAL result from the WRONG object, which is the failure
shape a user cannot detect — no error, no warning, correct-looking numbers.

  * `load_entity("revenue")` fell through to a `%revenue%` substring search with
    `.limit(1)`, so it silently chose between `Revenue by Region`,
    `Revenue Forecast` and `Net Revenue`.
  * `InstructionReferenceService` resolved a table name with `.first()` — and
    then REWROTE `ref.object_id` to that row, so the arbitrary pick became the
    instruction's permanent target. A rule written about `sales.orders` ends up
    governing `staging.orders`, shaping every later answer.
  * Power BI and SSAS did the same for semantic models and catalogs; those live
    in `test_a_source_that_cannot_answer_says_so.py`.

★The fix is never "ask more often". Exact tiers — an id, a slug, a full title, a
fully-qualified table name — still resolve silently, because one candidate is
not a choice. Only a genuinely plural match speaks up, and when it does it NAMES
the candidates, so the next attempt can be exact instead of another guess.

★load_step is deliberately NOT changed. Its `by_title` collision rule keeps the
most recent step, scoped to a single report — two steps with one title there are
the same analysis re-run, so recency is the answer rather than a guess.
"""

import ast
import inspect
from pathlib import Path

import app.ai.code_execution.loadables as loadables
import app.services.instruction_reference_service as instruction_refs

LOADABLES = Path(inspect.getsourcefile(loadables)).read_text(encoding="utf-8")
INSTRUCTIONS = Path(inspect.getsourcefile(instruction_refs)).read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found")


# --------------------------------------------------------------------------
# load_entity
# --------------------------------------------------------------------------

RESOLVE_ENTITY = _function_source(LOADABLES, "_resolve_entity")


def test_the_fuzzy_tier_no_longer_takes_the_first_row():
    assert ".limit(1)" not in RESOLVE_ENTITY.split("Entity.title.ilike(f\"%")[-1], (
        "the substring search still bounds itself to one row, which IS the guess"
    )
    assert "_q_many(" in RESOLVE_ENTITY


def test_an_ambiguous_entity_name_names_its_candidates():
    assert "matches {len(fuzzy)} published" in RESOLVE_ENTITY
    assert "there is no default" in RESOLVE_ENTITY
    assert '", ".join(names)' in RESOLVE_ENTITY, (
        "an ambiguity error that does not list the options just costs a turn"
    )


def test_the_exact_tiers_still_resolve_without_asking():
    """★The regression guard. Making every lookup ask would be worse than the
    bug — an id, a slug and a full title each identify one entity."""
    assert "await _q(Entity.id == ref)" in RESOLVE_ENTITY
    assert "await _q(Entity.slug.ilike(ref))" in RESOLVE_ENTITY
    assert "await _q(Entity.title.ilike(ref))" in RESOLVE_ENTITY


def test_a_single_fuzzy_match_is_still_used_silently():
    assert "fuzzy[0] if fuzzy else None" in RESOLVE_ENTITY


def test_a_miss_is_still_a_miss_and_not_an_ambiguity():
    assert "no matching published entity found" in RESOLVE_ENTITY


# --------------------------------------------------------------------------
# instruction -> datasource_table
# --------------------------------------------------------------------------

def test_a_table_reference_no_longer_binds_to_an_arbitrary_row():
    assert ".scalars().first()" not in INSTRUCTIONS, (
        "the rewrite below makes an arbitrary pick permanent"
    )
    # ★the literal wraps across two f-string lines — match the half that
    # cannot be produced by anything else in this file
    assert "ambiguous — {len(matches)} tables match" in INSTRUCTIONS
    assert "raise ValueError" in INSTRUCTIONS


def test_a_qualified_name_is_tried_before_its_bare_leaf():
    """Folding `sales.orders` and `orders` into one IN() made them equal-weight,
    so a fully-qualified reference could be answered by a bare table in another
    schema."""
    assert "await _by_name(qualified) or await _by_name(reduced)" in INSTRUCTIONS


def test_the_ambiguity_error_says_which_data_source_each_match_is_in():
    assert "data source {m.datasource_id}" in INSTRUCTIONS


def test_one_match_still_rewrites_the_reference_as_before():
    assert "obj = matches[0] if matches else None" in INSTRUCTIONS
    assert "ref.object_id = obj.id" in INSTRUCTIONS


# --------------------------------------------------------------------------
# the deliberate non-change
# --------------------------------------------------------------------------

def test_load_step_still_prefers_the_most_recent_step_of_a_title():
    """Recorded so a future sweep for `first match` does not "fix" a rule that
    is correct: two steps titled the same INSIDE ONE REPORT are one analysis
    re-run."""
    assert "On title collision the most recent step wins." in LOADABLES
