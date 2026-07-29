"""Generated code is checked before it runs — on the server AND on a laptop.

`validate_python_code` is the AST gate: forbidden imports, forbidden calls,
forbidden attributes, and SQL string literals that write. It ran inside
`execute_code`, which is the SERVER path.

`execute_code_async` dispatches to the user's paired helper first when local
runtime is on, and returns that result directly — so on a machine with a helper
connected, unvalidated code was shipped to somebody's laptop and executed
there. The gate was in the fallback, not on the way in.

The docstring made the opposite claim ("Validates Python code via AST analysis
before execution"), which is the recurring shape in this codebase: a promise
with nothing enforcing it.
"""
import ast
import inspect
import re

import pytest

from app.ai.code_execution.code_execution import (
    StreamingCodeExecutor,
    validate_python_code,
)


def _dispatch_source() -> str:
    return inspect.getsource(StreamingCodeExecutor.execute_code_async)


def _dispatch_code() -> str:
    """The dispatch body with docstring and comments removed."""
    import textwrap
    src = textwrap.dedent(_dispatch_source())
    tree = ast.parse(src)
    fn = tree.body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    return "\n".join(ast.unparse(n) for n in body)


# --- the ordering ----------------------------------------------------------

def test_the_gate_runs_before_anything_is_dispatched():
    """★The defect. The check has to happen on the way IN, not in the branch
    that happens to run when the laptop is unavailable."""
    code = _dispatch_code()
    assert "validate_python_code" in code, "the dispatch never validates"
    i_validate = code.index("validate_python_code")
    for later in ("_try_run_remote", "_run_server_async"):
        assert i_validate < code.index(later), (
            f"{later} is reached before the code is validated"
        )


def test_the_gate_is_not_inside_the_local_runtime_branch():
    """Validating only when the flag is on would leave the server path relying
    on a second check further down — two gates that can drift apart."""
    code = _dispatch_code()
    i_validate = code.index("validate_python_code")
    head = code[:i_validate]
    assert "hybrid_local_runtime" not in head, (
        "the check sits inside the feature-flag branch"
    )


def test_the_server_path_still_checks_too():
    """★Belt and braces on purpose. `execute_code` is called directly from
    other places, so removing its own gate to avoid double work would open a
    different door. Double validation of the same string is cheap."""
    body = inspect.getsource(StreamingCodeExecutor.execute_code)
    assert "validate_python_code(code)" in body


def test_the_docstring_no_longer_claims_something_nothing_did():
    """The comment above the dispatch must describe what the code does. A
    security claim that is only aspirational is worse than none."""
    src = _dispatch_source()
    doc = inspect.getdoc(StreamingCodeExecutor.execute_code_async) or ""
    assert "validat" in doc.lower(), "the dispatch does not say that it validates"


# --- the gate itself, unchanged -------------------------------------------

def test_an_ordinary_analysis_still_passes():
    """If the gate rejects real generated code, it gets removed."""
    validate_python_code(
        "import pandas as pd\n"
        "def generate_df(ds_clients, excel_files):\n"
        "    df = ds_clients['db'].execute_query('SELECT a, b FROM t')\n"
        "    return df\n"
    )


@pytest.mark.parametrize("snippet", [
    "import os\nos.system('id')\n",
    "df = ds_clients['db'].execute_query('DROP TABLE users')\n",
    "df = ds_clients['db'].execute_query('DELETE FROM users')\n",
])
def test_the_gate_still_refuses_what_it_always_refused(snippet):
    with pytest.raises(Exception):
        validate_python_code(snippet)
