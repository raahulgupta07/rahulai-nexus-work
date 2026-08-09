"""Accept/reject must apply exactly the hunks the reviewer looked at.

Every review action now carries the snapshot it was decided against — the main
build id and the instruction's version id — and the accept-all / reject-all
passes carry the explicit list of hunks the screen was showing. Before that:

- ``against_main_version_id`` defaulted to ``None``, so a client that omitted it
  compared against an unknown baseline instead of being refused. ``None`` is a
  legitimate value (an org with no main build, an instruction absent from main),
  which is exactly why it cannot double as "not supplied";
- accept-all / reject-all took a whole-body default (``AcceptAllRequest()``),
  so a bare POST applied *everything currently live* — including hunks that
  appeared after the reviewer loaded the screen;
- ``_claim_main`` re-read main under a lock and then wrote regardless of what it
  found, so a build promoted between the read and the write was silently
  overwritten rather than reported as a conflict.

These are AST checks, not text scans: a field's presence is a fact about the
class body, and a docstring or comment mentioning ``against_main_build_id``
must not be able to satisfy them.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
ROUTES = REPO / "backend" / "app" / "routes" / "instruction.py"
BUILD_SERVICE = REPO / "backend" / "app" / "services" / "build_service.py"
SERVICE = REPO / "backend" / "app" / "services" / "instruction_service.py"

SNAPSHOT_FIELDS = ("against_main_build_id", "against_main_version_id")
REVIEW_MODELS = ("AcceptHunkRequest", "RejectHunkRequest",
                 "AcceptAllRequest", "RejectAllRequest")
BULK_MODELS = ("AcceptAllRequest", "RejectAllRequest")


@pytest.fixture(scope="module")
def routes_tree() -> ast.Module:
    return ast.parse(ROUTES.read_text(encoding="utf-8"))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone from routes/instruction.py")


def _annotated_fields(cls: ast.ClassDef) -> dict:
    return {
        stmt.target.id: stmt
        for stmt in cls.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }


@pytest.mark.parametrize("model", REVIEW_MODELS)
def test_every_review_action_carries_the_snapshot_it_decided_against(routes_tree, model):
    fields = _annotated_fields(_class(routes_tree, model))
    for name in SNAPSHOT_FIELDS:
        assert name in fields, f"{model} does not carry {name}"


@pytest.mark.parametrize("model", REVIEW_MODELS)
def test_omitting_the_snapshot_fails_closed_rather_than_defaulting(routes_tree, model):
    """``None`` means "there was no main build". It cannot also mean "the client
    didn't say", or an unknown baseline passes for a checked one."""
    fields = _annotated_fields(_class(routes_tree, model))
    for name in SNAPSHOT_FIELDS:
        assert fields[name].value is None, (
            f"{model}.{name} has a default, so a request that omits it is "
            "accepted and compared against a baseline nobody asserted"
        )


@pytest.mark.parametrize("model", BULK_MODELS)
def test_a_bulk_pass_names_its_hunks_and_cannot_be_empty(routes_tree, model):
    cls = _class(routes_tree, model)
    fields = _annotated_fields(cls)
    assert "hunks" in fields, (
        f"{model} does not name the hunks to apply — the pass falls back to "
        "'everything live', which is not what the reviewer saw"
    )
    src = ast.unparse(fields["hunks"])
    assert "min_length=1" in src, (
        f"{model}.hunks accepts an empty list: {src}"
    )
    assert "build_id" not in fields, (
        f"{model} still takes a bare build_id — a whole suggestion applied by "
        "id is the imprecise pass this change replaced"
    )


@pytest.mark.parametrize("handler", ["accept_all_instruction_hunks",
                                     "reject_all_instruction_hunks"])
def test_the_bulk_endpoints_require_a_body(routes_tree, handler):
    """``body: AcceptAllRequest = AcceptAllRequest()`` let a bare POST through."""
    fn = next(
        n for n in ast.walk(routes_tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == handler
    )
    args = {a.arg: i for i, a in enumerate(fn.args.args)}
    assert "body" in args, f"{handler} takes no body"
    # Defaults align to the TAIL of the argument list.
    offset = len(fn.args.args) - len(fn.args.defaults)
    default = (fn.args.defaults[args["body"] - offset]
               if args["body"] >= offset else None)
    assert default is None, (
        f"{handler} still constructs a default request body, so a POST with no "
        "hunks named applies whatever happens to be live"
    )


@pytest.mark.parametrize("handler", ["accept_all_instruction_hunks",
                                     "reject_all_instruction_hunks",
                                     "reject_instruction_hunk"])
def test_a_moved_baseline_is_a_conflict_not_a_silent_apply(routes_tree, handler):
    fn = next(
        n for n in ast.walk(routes_tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == handler
    )
    src = ast.unparse(fn)
    assert 'status == "conflict"' in src or "status == 'conflict'" in src, (
        f"{handler} does not translate a moved baseline into a refusal"
    )
    assert "RESOURCE_CONFLICT" in src, f"{handler} raises no conflict error"


def test_claiming_main_notices_that_main_moved():
    """The lock made the read-then-write atomic; nothing checked what was read."""
    tree = ast.parse(BUILD_SERVICE.read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "MainBuildChangedError" in names, (
        "build_service has no way to report that main moved under a review action"
    )
    claim = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "_claim_main"
    )
    kwonly = {a.arg for a in claim.args.kwonlyargs}
    assert {"expected_main_build_id", "enforce_expected_main"} <= kwonly, (
        f"_claim_main cannot be told which main the caller decided against: {kwonly}"
    )
    body = ast.unparse(claim)
    assert "MainBuildChangedError" in body, (
        "_claim_main takes the expected main and never checks it — a parameter "
        "that is read by nothing is worse than none, because it reads as a guard"
    )


def test_the_single_hunk_actions_go_through_the_exact_pass():
    """accept_hunk/reject_hunk are the one-element case of the same pass, so a
    fix to the pass cannot miss them."""
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    for name, delegate in (("accept_hunk", "accept_all_hunks"),
                           ("reject_hunk", "reject_all_hunks")):
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
        )
        src = ast.unparse(fn)
        assert f"self.{delegate}(" in src, f"{name} no longer delegates to {delegate}"
        assert "selected_hunks=" in src, (
            f"{name} does not name the one hunk it is applying"
        )
