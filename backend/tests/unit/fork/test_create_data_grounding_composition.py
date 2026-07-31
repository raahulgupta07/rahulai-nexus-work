"""`create_data` must ground the coder on BOTH source files and local folders.

Two independent pieces of grounding are folded into the same
`build_codegen_context(...)` call, from two different origins:

  * `source_directive` — upstream v0.0.494. Names the specific files a step was
    told to read, appended to the prompt so the coder cannot quietly widen the
    question to everything it can reach.
  * `_local_folders_ctx` — this fork's local-runtime work. Describes tables that
    exist ONLY on the user's own machine, appended to the schema excerpt. Without
    it the coder cannot see their columns, so it invents them or reaches for a
    warehouse table instead.

They arrived on opposite sides of a merge conflict during the v0.0.494 port, and
they touch DIFFERENT arguments of the same call — so the conflict looks like a
choice and is not one. Resolving it by taking either hunk alone produces code
that compiles, tests that pass, and a coder that is silently blind to one half
of what it was told. The failure surfaces much later as a wrong answer rather
than an error: invented columns, or a step that reads files it was not given.

This file asserts the composition, argument by argument, so a future port that
resolves the same conflict has to keep both. It deliberately does NOT assert how
either string is built — only that each still reaches the argument it grounds.
"""
import ast
import inspect
import textwrap

import pytest

import app.ai.tools.implementations.create_data as create_data_module


def _codegen_context_call() -> ast.Call:
    """The single `build_codegen_context(...)` call, as a parse tree.

    Parsed rather than grepped because what matters is WHICH ARGUMENT each
    piece of grounding lands in. Both names appear in the surrounding prose and
    in nearby unrelated code, so a text search proves only that the words are
    still in the file — which is exactly what a half-resolved conflict leaves
    behind.
    """
    src = textwrap.dedent(inspect.getsource(create_data_module))
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_codegen_context"
    ]
    assert len(calls) == 1, (
        f"expected exactly one build_codegen_context call in create_data, found {len(calls)}. "
        "If the tool now builds context in more than one place, every one of them "
        "needs both halves of the grounding below."
    )
    return calls[0]


def _kwarg(call: ast.Call, name: str) -> str:
    for kw in call.keywords:
        if kw.arg == name:
            return ast.unparse(kw.value)
    pytest.fail(f"build_codegen_context is no longer passed {name!r}")


# ── upstream's half: the file directive reaches the prompt ──────────────────

def test_the_source_directive_is_appended_to_the_user_prompt():
    """Without it the coder is free to read whatever it can reach, and a step
    scoped to two files quietly answers from everything."""
    assert "source_directive" in _kwarg(_codegen_context_call(), "user_prompt")


def test_the_source_directive_also_reaches_the_interpreted_prompt():
    """Both prompts are grounding inputs; the interpreted one is what the coder
    actually plans against when the planner rewrote the question."""
    assert "source_directive" in _kwarg(_codegen_context_call(), "interpreted_prompt")


# ── this fork's half: local folder schemas reach the excerpt ────────────────

def test_the_local_folder_schemas_are_appended_to_the_schema_excerpt():
    """These tables exist only on the user's machine. Dropped from here, the
    coder has no way to learn their columns and will invent them."""
    assert "_local_folders_ctx" in _kwarg(_codegen_context_call(), "schemas_excerpt")


def test_the_schema_excerpt_still_carries_the_ordinary_schemas_too():
    """The folder context is APPENDED, not substituted. A resolution that
    replaced the excerpt would ground the coder on local folders alone and blind
    it to every connected data source."""
    assert "schemas_excerpt" in _kwarg(_codegen_context_call(), "schemas_excerpt")


# ── the two halves are genuinely independent ────────────────────────────────

def test_the_two_halves_land_in_different_arguments():
    """Why the conflict is not a choice: they do not overlap, so there is no
    version of 'pick one' that is correct. Stated as an assertion so that if
    they ever DO converge on one argument, this file is re-read rather than
    trusted."""
    call = _codegen_context_call()
    prompt_args = _kwarg(call, "user_prompt") + _kwarg(call, "interpreted_prompt")
    schema_arg = _kwarg(call, "schemas_excerpt")
    assert "_local_folders_ctx" not in prompt_args
    assert "source_directive" not in schema_arg


def test_the_folder_context_degrades_to_empty_rather_than_raising():
    """It reads the device's published schema, which can fail for ordinary
    reasons — flag off, nothing attached, helper offline. A raise here would
    take down every create_data run, including the overwhelming majority that
    never touch a local folder."""
    src = inspect.getsource(create_data_module)
    start = src.index("_local_folders_ctx = \"\"")
    region = src[start:src.index("codegen_context = await build_codegen_context")]
    assert "except Exception:" in region, (
        "the local-folder lookup is no longer guarded; a device that cannot be "
        "reached would now fail the whole step"
    )
    assert region.count('_local_folders_ctx = ""') >= 2, (
        "the failure path no longer resets the context to empty"
    )


def test_the_empty_case_adds_no_separator():
    """When nothing is attached the excerpt must be byte-identical to what it
    was before this feature existed — an unconditional join would prepend blank
    lines to every run's grounding and change the model's input for everyone."""
    arg = _kwarg(_codegen_context_call(), "schemas_excerpt")
    assert "if _local_folders_ctx" in arg, (
        "the folder context is concatenated unconditionally"
    )
