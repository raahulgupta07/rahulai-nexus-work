"""Prose written before the code fence must never reach `exec()`.

Measured incident, 2026-08-03: a folder holding one `.docx`, the prompt
"summaries data for me". Seventy seconds, two issues raised, no answer — the
user saw only:

    CSV generation failed — Execution error: invalid syntax (<string>, line 1)

Line 1 was `Looking at this request, I need to:`.

The cause is the strip in `coder.py`, repeated verbatim at four call sites:

    result = re.sub(r'^\\s*```(?:[A-Za-z0-9_\\-]+)?\\s*\\r?\\n', '', result.strip(), ...)

`^` after `.strip()` anchors to the very start of the reply, so a fence is
removed only when the model emits nothing before it. Anything written first
survives and is handed to Python.

The tell was already in the file: the next line calls
`trim_after_final_df_return`, which carefully removes everything *after* the
function. Nothing removed anything *before* it.

★The trigger is a cheap model, not a broken one. The coder prompt is
self-contradictory — `coder.py` "produce ONLY the Python function code … no
markdown, no text, no anything" against `_file_access_rules` "`.docx` → NOT
readable from generated code at all … the planner must use the `read_file`
tool". Handed a document the model must violate one of them, and a small model
resolves that out loud. A strong model silently obeys, which is why this stayed
latent. Removing the contradiction is separate work; this file only makes the
failure survivable.
"""
import ast
import inspect
import re
from pathlib import Path

import pytest

from app.ai.agents.coder.coder import Coder, extract_generated_code

FIXTURES = Path(__file__).parent / "fixtures"
PROSE_BEFORE_FENCE = (FIXTURES / "codegen_prose_before_fence.txt").read_text()

# The exact strip these four sites used before this fix.
_OLD_STRIP = r'^\s*```(?:[A-Za-z0-9_\-]+)?\s*\r?\n'


def _compiles(code: str) -> bool:
    try:
        compile(code, "<string>", "exec")
    except SyntaxError:
        return False
    return True


# --- the premise -----------------------------------------------------------

def test_the_old_strip_really_did_ship_prose_to_python():
    """★Guard the guard. If this ever stops reproducing, the fix below is
    pointless and this test says so instead of passing quietly."""
    survived = re.sub(_OLD_STRIP, "", PROSE_BEFORE_FENCE.strip(), flags=re.IGNORECASE)
    assert survived.startswith("Looking at this request"), (
        "the fixture no longer opens with prose — the premise of this file is gone"
    )
    with pytest.raises(SyntaxError):
        compile(survived, "<string>", "exec")


# --- the fix ---------------------------------------------------------------

def test_prose_before_the_fence_is_discarded():
    """The measured incident, end to end."""
    code = extract_generated_code(PROSE_BEFORE_FENCE)
    assert "Looking at this request" not in code
    assert "However, the task requires" not in code
    assert code.lstrip().startswith("def generate_df")
    assert _compiles(code)


def test_a_fence_at_the_very_start_still_works():
    """The common case. It worked before and must keep working."""
    raw = "```python\ndef generate_df(ds_clients, excel_files):\n    return df\n```"
    code = extract_generated_code(raw)
    assert code.strip() == "def generate_df(ds_clients, excel_files):\n    return df"


def test_bare_code_with_no_fence_is_left_alone():
    raw = "import pandas as pd\n\ndef generate_df(ds_clients, excel_files):\n    return df\n"
    code = extract_generated_code(raw)
    assert code.strip().startswith("import pandas as pd")
    assert _compiles(code)


def test_prose_then_bare_code_with_no_fence_still_yields_code():
    """A model that narrates and then emits unfenced code — the same failure
    wearing different clothes."""
    raw = (
        "Here is my plan:\n\n"
        "1. Load the file\n2. Aggregate\n\n"
        "import pandas as pd\n\n"
        "def generate_df(ds_clients, excel_files):\n    return df\n"
    )
    code = extract_generated_code(raw)
    assert "Here is my plan" not in code
    assert _compiles(code)


def test_the_last_fence_wins_when_the_model_shows_an_example_first():
    """Prose, a throwaway example, then the real answer. Taking the FIRST fence
    ships the example as the answer — a wrong result, not an error, which is
    worse than the crash this file started from."""
    raw = (
        "For example, you might write:\n\n"
        "```python\ndef example():\n    pass\n```\n\n"
        "But for your data the correct function is:\n\n"
        "```python\ndef generate_df(ds_clients, excel_files):\n    return df\n```\n"
    )
    code = extract_generated_code(raw)
    assert "def generate_df" in code
    assert "def example" not in code


def test_a_language_tag_on_its_own_line_is_not_left_behind():
    raw = "```\npython\ndef generate_df(ds_clients, excel_files):\n    return df\n```"
    code = extract_generated_code(raw)
    assert _compiles(code)


def test_closing_fences_never_survive():
    code = extract_generated_code(PROSE_BEFORE_FENCE)
    assert "```" not in code


def test_output_that_cannot_compile_raises_instead_of_being_executed():
    """Extraction is not a guess. If what comes out is not Python, the caller
    must get an exception it can retry on — never a string handed to exec()."""
    with pytest.raises(SyntaxError):
        extract_generated_code("I cannot help with that request.")


def test_an_empty_reply_raises():
    with pytest.raises(SyntaxError):
        extract_generated_code("")


def test_the_failure_message_tells_the_model_what_to_do_differently():
    """★The message is fed straight back as the retry's error feedback. A model
    that just explained itself cannot act on "invalid syntax"; it can act on
    "return the function only"."""
    with pytest.raises(SyntaxError) as excinfo:
        extract_generated_code("I cannot help with that request.")
    message = str(excinfo.value)
    assert "function definition only" in message
    assert "no explanation before it" in message


# --- a failed extraction is a retry, never a user-facing failure ------------

@pytest.mark.parametrize(
    "func_name", ["generate_and_execute_stream", "generate_and_execute_stream_v2"]
)
def test_codegen_failure_is_caught_and_retried(func_name):
    """`extract_generated_code` raises where the old strip silently returned
    prose. That is only an improvement if the retry loop absorbs it — otherwise
    the same incident returns wearing a different exception.

    Checked at source level on purpose: driving the real streamer needs a
    database schema, and `tests/unit/fork/` deliberately has none (a
    schema-needing test here fails "no such table" and reads as a product bug).
    """
    from app.ai.code_execution import code_execution

    func = getattr(code_execution.StreamingCodeExecutor, func_name)
    body = inspect.getsource(func)
    assert "code_generator_fn(" in body
    i_call = body.index("code_generator_fn(")
    tail = body[i_call:]
    assert "except Exception" in tail, (
        f"{func_name} does not catch a failed generation — a SyntaxError from "
        "extract_generated_code would reach the user instead of retrying"
    )
    assert "retries += 1" in tail


# --- it is actually wired in ------------------------------------------------

_CODEGEN_METHODS = (
    "data_model_to_code",
    "generate_code",
    "generate_inspection_code",
    "generate_transform_code",
)


@pytest.mark.parametrize("method_name", _CODEGEN_METHODS)
def test_every_codegen_path_uses_the_shared_helper(method_name):
    """★A helper nothing calls fixes nothing — the recurring shape in this
    codebase. The strip was duplicated verbatim four times, so a fix applied to
    one site left three live."""
    body = inspect.getsource(getattr(Coder, method_name))
    assert "extract_generated_code(" in body, (
        f"{method_name} still cleans its own output"
    )


def test_no_fence_stripping_regex_survives_anywhere_in_the_module():
    """Stops a fifth copy appearing later. The regex is the defect; its absence
    is the invariant.

    ★Matched against the parsed tree, not the file text — the first version of
    this test searched the source for the pattern and tripped over the pattern
    quoted in `extract_generated_code`'s own docstring, which documents the bug
    it fixes. A guard that cannot tell code from prose about code is not a guard.
    """
    tree = ast.parse(inspect.getsource(inspect.getmodule(Coder)))
    # extract_generated_code legitimately clears leftover fence lines, so its
    # own body is the one place the pattern belongs. Everywhere else it is the
    # bug coming back.
    elsewhere = [
        node for node in tree.body
        if not (isinstance(node, ast.FunctionDef) and node.name == "extract_generated_code")
    ]
    offenders = [
        node.args[0].value
        for top in elsewhere
        for node in ast.walk(top)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) == "sub"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and "```" in node.args[0].value
    ]
    assert not offenders, (
        f"a hand-rolled fence strip is back in coder.py: {offenders} — "
        "use extract_generated_code"
    )
