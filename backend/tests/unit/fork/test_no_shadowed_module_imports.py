"""No function may shadow a module-level import with a bare re-import.

THE BUG THIS EXISTS FOR. `file_service.upload_file` used `os.makedirs(...)` near
the top, and ~70 lines further down, inside a defensive `try:`, had a bare
`import os`. Python decides a name is local at COMPILE time: a binding anywhere
in a function body makes that name local for the WHOLE body. So the earlier use
compiled to LOAD_FAST_CHECK and raised

    UnboundLocalError: cannot access local variable 'os'
                       where it is not associated with a value

on every single call — before touching the disk, before any error the code was
defending against. Every file upload returned 500 on both the cloud install and
the local one, for two days, with the traceback pointing at a line that was
correct.

Nothing caught it: the module imports fine, the file compiles, every other test
passes, and the inner import looks harmless in review. It only fails when the
function actually runs — and the one code path that runs it was the one nobody
had a test for.

Note the same file already used ``import os as _os`` in three other functions,
precisely to avoid this. One site did not, and that was enough.

This test does not look for `os`. It compiles every function in the app and asks
Python itself which names are local, then flags any that are also module-level
imports — so it catches the next one regardless of which module it involves.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[4] / "backend" / "app"


def _module_imports(tree: ast.Module) -> set[str]:
    """Names bound by imports at MODULE level (not inside any function)."""
    names: set[str] = set()
    for node in tree.body:  # top level only
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def _function_import_bindings(fn: ast.AST) -> dict[str, int]:
    """Names bound by an import INSIDE this function, to the line that binds them.

    Nested functions are skipped: they have their own scope, so a re-import
    there shadows nothing in the parent.
    """
    found: dict[str, int] = {}
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # separate scope
        if isinstance(node, ast.Import):
            for a in node.names:
                found.setdefault(a.asname or a.name.split(".")[0], node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                found.setdefault(a.asname or a.name, node.lineno)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _first_use_line(fn: ast.AST, name: str) -> int | None:
    """Earliest line where `name` is READ in this function's BODY.

    ★The signature is deliberately excluded. Argument and return annotations on
    the `def` line are evaluated at definition time in the ENCLOSING scope, so
    they resolve to the module-level import and are unaffected by anything the
    body re-imports. Counting them produced three false positives on the first
    run of this test — `def connect(self) -> Generator[psycopg2...]`,
    `-> DataSource`, `-> Dict[str, Any]` — all perfectly safe. A checker that
    cries wolf gets switched off, so it only looks at statements.
    """
    lines: list[int] = []
    for stmt in fn.body:  # body only — never fn.args, never fn.returns
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load):
                lines.append(n.lineno)
    return min(lines) if lines else None


def _offences() -> list[str]:
    out: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        s = str(path)
        if "__pycache__" in s or ".bak-" in s:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue  # compilation is verify.sh's job, not this test's
        top = _module_imports(tree)
        if not top:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for name, import_line in _function_import_bindings(fn).items():
                if name not in top:
                    continue  # a genuinely new local name — fine
                use = _first_use_line(fn, name)
                if use is not None and use < import_line:
                    out.append(
                        f"{path.relative_to(APP.parent.parent)}:{use} uses `{name}` "
                        f"before re-importing it at line {import_line} "
                        f"(in `{fn.name}`) — `{name}` is a module-level import, so "
                        f"the inner import makes it LOCAL for the whole function "
                        f"and the earlier use raises UnboundLocalError at runtime. "
                        f"Drop the inner import, or alias it (`import {name} as _{name}`)."
                    )
    return out


def test_no_function_shadows_a_module_import_before_using_it():
    offences = _offences()
    assert not offences, (
        "Function-local re-import shadows a module-level import that the same "
        "function uses EARLIER. This raises UnboundLocalError on every call and "
        "no other check catches it:\n  " + "\n  ".join(offences)
    )


def test_the_detector_actually_detects(tmp_path):
    """A checker that never fires proves nothing. Feed it the real bug."""
    bad = tmp_path / "app" / "boom.py"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "import os\n"
        "\n"
        "def upload():\n"
        "    os.makedirs('x', exist_ok=True)\n"   # use
        "    try:\n"
        "        import os\n"                      # shadow, later
        "        return os.path.join('a', 'b')\n"
        "    except Exception:\n"
        "        return None\n"
    )
    tree = ast.parse(bad.read_text())
    top = _module_imports(tree)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    binds = _function_import_bindings(fn)
    assert "os" in top and "os" in binds, "fixture is not shaped like the bug"
    assert _first_use_line(fn, "os") < binds["os"], "use must precede the shadowing import"


def test_an_aliased_reimport_is_not_flagged(tmp_path):
    """`import os as _os` binds a DIFFERENT name, so it shadows nothing.

    Three functions in file_service.py already do this deliberately. If this
    test ever fails, the detector has become noisy and people will disable it.
    """
    src = (
        "import os\n"
        "\n"
        "def fine():\n"
        "    os.makedirs('x')\n"
        "    import os as _os\n"
        "    return _os.sep\n"
    )
    tree = ast.parse(src)
    top = _module_imports(tree)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    binds = _function_import_bindings(fn)
    assert "_os" in binds and "_os" not in top
    assert "os" not in binds, "an aliased import must not register as shadowing `os`"


@pytest.mark.parametrize("name", ["os", "json", "asyncio", "logging", "re"])
def test_common_stdlib_names_are_clean(name):
    """Named separately so a failure says WHICH module, not just 'something'."""
    hits = [o for o in _offences() if f"`{name}`" in o]
    assert not hits, "\n  ".join(hits)
