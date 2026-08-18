"""The guard caught the wrong table in 6.2ms, and the step still failed.

Measured 2026-08-17 on the live instance, immediately after deploying the
table-reference check. The re-run went:

    inspect_data       -> succeeded (19.5s)
    generate_code      -> wrong database, guard blocked it in 6.2ms, naming all
                          three correct fully-qualified names
    step               -> FAILED

`limit_code_retries` defaults to **2**, and the block had spent the last one. So
the user got a red step out of a check whose entire purpose is to prevent red
steps — a worse outcome than before the guard existed, arrived at faster.

★A block is not a failed attempt. Nothing executed, no server was contacted, and
the message handed back carries the exact name to use. It is the cheapest
correction in the loop and was priced like the most expensive one.

★It cannot be free without limit, or a model that ignores the correction loops
forever. Past the cap the block is charged normally, so the loop still
terminates on its own budget.
"""

import ast
import inspect
from pathlib import Path

import pytest

from app.ai.code_execution.code_execution import (
    MAX_FREE_TABLE_REFERENCE_CORRECTIONS,
    table_reference_block_is_free,
)


def test_the_first_correction_is_free():
    """The measured failure: ONE block, and it consumed the whole budget."""
    assert table_reference_block_is_free(1) is True


def test_corrections_stay_free_up_to_the_cap():
    for n in range(1, MAX_FREE_TABLE_REFERENCE_CORRECTIONS + 1):
        assert table_reference_block_is_free(n) is True, n


def test_past_the_cap_a_block_is_charged_again():
    """★The termination half. A model that ignores the correction twice is not
    going to be talked round by a third copy of it, so the loop must be able to
    run out of budget."""
    assert table_reference_block_is_free(MAX_FREE_TABLE_REFERENCE_CORRECTIONS + 1) is False
    assert table_reference_block_is_free(99) is False


def test_the_cap_is_small_enough_that_a_stuck_run_still_ends():
    assert 1 <= MAX_FREE_TABLE_REFERENCE_CORRECTIONS <= 3


SOURCE = Path(
    inspect.getsourcefile(table_reference_block_is_free)
).read_text(encoding="utf-8")


def _guard_blocks():
    """Every `if table_ref_error:` body in the file, as source text.

    There are two retry loops — v1 (`create_widget`) and v2 — and they have
    drifted apart before. A rule applied to one of them is not applied.
    """
    tree = ast.parse(SOURCE)
    lines = SOURCE.splitlines()
    blocks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Name) and test.id == "table_ref_error":
            blocks.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
    return blocks


def test_both_retry_loops_still_run_the_check():
    assert len(_guard_blocks()) == 2, "a retry loop lost its table-reference check"


@pytest.mark.parametrize("index", (0, 1))
def test_neither_loop_charges_a_block_unconditionally(index):
    """★The bug was one line: a bare `retries += 1` inside the block. Both loops
    had it, and fixing only the one that was reproduced would leave
    `create_widget` still paying for its own corrections."""
    block = _guard_blocks()[index]
    assert "table_ref_corrections += 1" in block
    assert "table_reference_block_is_free(" in block
    for line in block.splitlines():
        stripped = line.strip()
        if stripped == "retries += 1":
            # legal only as the body of the cap check
            assert "not table_reference_block_is_free" in block, (
                "a block charges a retry with no cap check above it"
            )


def test_each_loop_starts_its_own_counter():
    """A counter shared across loops, or hoisted to the instance, would make one
    run's corrections spend the next run's budget."""
    assert SOURCE.count("table_ref_corrections = 0") == 2
    assert "self.table_ref_corrections" not in SOURCE
