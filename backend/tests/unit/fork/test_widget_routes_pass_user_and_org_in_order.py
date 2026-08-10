"""A service taking `(current_user, organization)` must be called that way.

Found live on 2026-08-09 by `scripts/chat-matrix.py` T4, not by any suite:
`GET /api/reports/{id}/widgets` answered **404 "Report not found" for every
report, to the report's own owner**, while the widgets sat in the database.

The cause is a POSITIONAL argument swap that upstream also ships:

    # service
    async def get_widgets_by_report(self, db_session, report_id, current_user, organization)
    # route
    widget_service.get_widgets_by_report(db, report_id, organization, current_user)

Upstream is unharmed by it because their body never reads `current_user` — the
only consequence there is that `str(organization.id)` silently keys PII display
redaction on the USER's id. Our 0.0.528.12 security pass added

    await assert_report_visible(db_session, report_id, current_user, organization)

into that body, and from that release on the visibility check received an
Organization where it expects a User and vice versa. Two READ routes died;
`update_widget` and `delete_widget` were untouched because they already passed
the pair in the declared order.

★The check is AST-based and general: for every call to a `widget_service`
method, compare the positional arguments against that method's own declared
parameter names. Hard-coding the two known-bad call sites would pass the moment
someone adds a third.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ROUTE = REPO / "backend" / "app" / "routes" / "widget.py"
SERVICE = REPO / "backend" / "app" / "services" / "widget_service.py"

# The two names that are interchangeable to the type checker, adjacent in every
# signature, and therefore the pair that actually gets swapped.
CONFUSABLE = ("current_user", "organization")


def _service_signatures(src: str) -> dict:
    """method name -> its positional parameter names, minus `self`."""
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            names = [a.arg for a in node.args.args]
            if names and names[0] == "self":
                names = names[1:]
            out[node.name] = names
    return out


def _calls(src: str):
    """Every `widget_service.<method>(...)` call, with its positional args."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        if not (isinstance(f.value, ast.Name) and f.value.id == "widget_service"):
            continue
        args = []
        for a in node.args:
            args.append(a.id if isinstance(a, ast.Name) else None)
        yield f.attr, args, node.lineno


def _mismatches(route_src: str, service_src: str) -> list:
    sigs = _service_signatures(service_src)
    bad = []
    for method, args, lineno in _calls(route_src):
        params = sigs.get(method)
        if not params:
            continue
        # The service's first parameter is the session; the route passes `db`.
        for i, passed in enumerate(args):
            if passed is None or i >= len(params):
                continue
            declared = params[i]
            if passed == declared:
                continue
            # Only the confusable pair matters: `db`/`db_session` and
            # `report_id`/`widget_uuid` are deliberate spelling differences.
            if passed in CONFUSABLE and declared in CONFUSABLE:
                bad.append(
                    f"{ROUTE.name}:{lineno} widget_service.{method}() passes "
                    f"`{passed}` in position {i} where the signature declares "
                    f"`{declared}`"
                )
    return bad


def test_widget_routes_pass_user_and_org_in_the_declared_order():
    bad = _mismatches(ROUTE.read_text(), SERVICE.read_text())
    assert not bad, (
        "a widget route hands current_user and organization to the service the "
        "wrong way round. Under 0.0.528.12's assert_report_visible that is a 404 "
        "on a report the caller owns:\n  " + "\n  ".join(bad)
    )


def test_the_original_defect_is_still_detected():
    """★The red proof, carried in the file rather than run once at a shell.

    Reconstruct the shipped call and require the checker to reject it. Without
    this, someone 'fixing' a future failure by relaxing the comparison leaves a
    test that agrees with whatever the tree says and detects nothing.
    """
    broken_route = (
        "async def get_widgets_by_report(report_id, current_user, organization, db):\n"
        "    return await widget_service.get_widgets_by_report("
        "db, report_id, organization, current_user)\n"
    )
    bad = _mismatches(broken_route, SERVICE.read_text())
    assert bad, (
        "the checker did not flag the exact call that shipped in 0.0.528.12 — "
        "it can no longer detect the bug it exists for"
    )


def test_a_correct_call_is_not_flagged():
    """The other half: the fix itself must read as clean, or the guard is noise."""
    good_route = (
        "async def get_widgets_by_report(report_id, current_user, organization, db):\n"
        "    return await widget_service.get_widgets_by_report("
        "db, report_id, current_user, organization)\n"
    )
    assert not _mismatches(good_route, SERVICE.read_text())
