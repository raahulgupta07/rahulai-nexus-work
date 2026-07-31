"""`trim_after_final_df_return` must stay the AST trim, not upstream's regex.

Both this fork and upstream v0.0.494 fixed the same regression — the codegen
post-processor that ran ``re.sub(r'(?s)return\\s+df.*$', 'return df', code)``,
a *replacement* rather than a trim, which rewrote `return df_aggregated` to
`return df` and shipped the pre-aggregation frame as the answer.

The two fixes are not equivalent, and the difference is invisible to upstream's
own suite:

  * upstream anchors a greedy regex on the LAST ``return df…`` in the text
  * we parse and cut at the end of the first top-level function

Every one of upstream's five test cases passes against BOTH. So if a later port
replaces our implementation with theirs, `tests/unit/test_coder_return_trim.py`
stays green and the regression returns silently — which is exactly how the
original bug survived: it was a coin flip on whether the model happened to name
a helper's return value `df`.

The case that separates them is the one the bug was reported as: a function
ending `return x` that contains a nested helper ending `return df`. The greedy
match anchors on the helper and deletes the queries and the real return; the
run then fails "returned None or an empty DataFrame" with no hint that the code
was truncated after generation.

These tests pin the behaviour, not the implementation — nothing here asserts
which parser is used, only that the shipped function survives the shapes a
regex cannot. A future port is free to change how it works and will be told if
it changes what it does.
"""
import inspect

import pytest

from app.ai.agents.coder.coder import (
    _legacy_trim_after_return_df,
    trim_after_final_df_return,
)

trim = trim_after_final_df_return

FN = "def generate_df(ds_clients, excel_files):\n"


# ── the shape upstream's regex cannot survive ───────────────────────────────

def test_a_nested_helper_returning_df_does_not_truncate_the_function():
    """The reported bug, verbatim. A greedy anchor on the last `return df…`
    lands on the helper, because the real return names something else."""
    code = (
        FN
        + "    def clean(df):\n"
        + "        return df\n"
        + "    rows = ds_clients['c'].execute_query('SELECT 1')\n"
        + "    out = clean(rows)\n"
        + "    return out\n"
    )
    trimmed = trim(code)
    assert "execute_query" in trimmed, "the query was deleted — this is the bug"
    assert trimmed.rstrip().endswith("return out")


def test_the_helper_case_is_genuinely_a_divergence_not_a_shared_win():
    """Guard the guard. If the legacy trim ever stopped mangling this input,
    the test above would pass for both implementations and would no longer be
    protecting anything — so assert the failure mode still exists."""
    code = (
        FN
        + "    def clean(df):\n"
        + "        return df\n"
        + "    rows = ds_clients['c'].execute_query('SELECT 1')\n"
        + "    return rows\n"
    )
    assert "execute_query" not in _legacy_trim_after_return_df(code), (
        "the legacy trim no longer truncates this; re-derive what separates "
        "the AST trim from a text trim before trusting this file"
    )


@pytest.mark.parametrize("final_return", ["return out", "return result", "return summary"])
def test_a_function_not_returning_a_df_name_still_gets_trimmed(final_return):
    """A text trim keyed on `return df` has nothing to anchor on here, so it
    leaves the trailing chatter in place and the chatter reaches the executor."""
    code = FN + f"    {final_return}\n\n# usage\ngenerate_df(a, b)\n"
    trimmed = trim(code)
    assert trimmed.rstrip().endswith(final_return)
    assert "# usage" not in trimmed


def test_a_return_df_inside_a_string_literal_is_not_an_anchor():
    """SQL and prompt text in the generated code can contain the phrase. A
    parser cannot be fooled by it; a regex over raw text can."""
    code = (
        FN
        + '    note = "the previous attempt ended: return df_totals"\n'
        + "    rows = ds_clients['c'].execute_query('SELECT 1')\n"
        + "    return rows\n"
    )
    trimmed = trim(code)
    assert "execute_query" in trimmed
    assert trimmed.rstrip().endswith("return rows")


# ── upstream's cases must keep passing (composition, not replacement) ───────

def test_the_returned_name_is_preserved():
    code = FN + "    df_aggregated = load().groupby('p').sum()\n    return df_aggregated\n"
    assert trim(code).rstrip().endswith("return df_aggregated")


def test_an_early_return_does_not_truncate_the_function():
    code = FN + "    if not ds_clients:\n        return df_empty\n    df_final = build()\n    return df_final\n"
    trimmed = trim(code)
    assert "df_final = build()" in trimmed
    assert trimmed.rstrip().endswith("return df_final")


def test_nothing_to_trim_returns_the_input_unchanged():
    """A no-op must be byte-identical, trailing newline included — otherwise
    every generation looks edited to anything diffing before against after."""
    code = FN + "    return build()\n"
    assert trim(code) == code


def test_trailing_prose_is_dropped_even_when_it_breaks_the_parse():
    """The case the prefix-retry exists for: chatter after the function makes
    the WHOLE output unparseable, so the cut is found by retrying prefixes."""
    code = FN + "    return build()\n\nThat should give you the totals you wanted.\n"
    trimmed = trim(code)
    assert "totals you wanted" not in trimmed
    assert trimmed.rstrip().endswith("return build()")


def test_prose_BEFORE_the_function_is_left_in_place():
    """Characterization of a real gap, not an endorsement of it.

    The retry only considers prefixes, so nothing can cut a preamble off the
    front — every candidate prefix still starts with the prose and still fails
    to parse, and the legacy text trim it falls through to has no concept of a
    preamble either. The output then reaches the executor with a sentence on
    line 1 and fails as a SyntaxError rather than as truncation.

    In practice the leading markdown fence and a bare `python`/`json` line ARE
    stripped by the caller before this runs, which covers the common shape; a
    free-text preamble is not. Pre-existing and out of scope for the v0.0.494
    port — pinned here so it is visible and so a fix has something to flip.
    """
    code = "Here is the function you asked for:\n" + FN + "    return build()\n"
    assert trim(code).startswith("Here is the function")


# ── the fork marker itself ──────────────────────────────────────────────────

def test_the_legacy_text_trim_is_reachable_only_as_a_fallback():
    """`_legacy_trim_after_return_df` is the mangling implementation, kept for
    output that cannot be parsed at all. If it ever becomes the primary path
    again the bug is back for every generation, not just unparseable ones."""
    # The docstring quotes the old regex verbatim to explain what was wrong
    # with it, so check the CODE — searching the whole source matches the
    # explanation and reports the bug as present in the fix for it. String
    # subtraction is not enough either: the docstring escapes the backslash,
    # so the two spellings differ. Drop the docstring node instead.
    import ast as _ast
    import textwrap

    tree = _ast.parse(textwrap.dedent(inspect.getsource(trim_after_final_df_return)))
    fn = tree.body[0]
    body = fn.body[1:] if _ast.get_docstring(fn) is not None else fn.body
    code = "\n".join(_ast.unparse(node) for node in body)

    assert "_legacy_trim_after_return_df" not in code, (
        "the shipped trim now calls the legacy text trim directly"
    )
    assert "'return df'" not in code, "the shipped trim rewrites the returned name"


def test_every_codegen_path_goes_through_the_one_helper():
    """Three methods post-process generated code. A copy that skipped the
    shared helper would quietly reintroduce the rewrite on its own path — which
    is how this survived a first fix: the regex lived in two places and a grep
    found only the one with a comment above it."""
    from app.ai.agents.coder.coder import Coder

    for method in (Coder.generate_code, Coder.data_model_to_code, Coder.generate_transform_code):
        src = inspect.getsource(method)
        assert "trim_after_final_df_return" in src, f"{method.__name__} does not trim"
        assert "re.sub(r'(?s)return" not in src, f"{method.__name__} trims inline"
