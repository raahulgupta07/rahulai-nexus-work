"""The permission gates on the conversation routes exist AND are reachable.

Three separate ways a gate on these routes has silently done nothing:

1. **Decorator order.** `@requires_permission` written ABOVE `@router.get` is
   dead code. `router.get` registers whatever object it is handed, so the
   *unwrapped* function goes into the route table and the wrapper is only ever
   bound to the module-level name nobody calls. Six routes in this tree were
   written that way — the file reads as gated and the endpoint is open to any
   authenticated caller with an org header.

2. **A missing `owner_only`.** `requires_permission(..., model=Report)` without
   `owner_only=True` runs the org-membership half of the object gate and then
   skips ownership entirely, so any org member holding `view_reports` could read
   any colleague's transcript and any member holding `create_reports` could post
   a turn into it.

3. **An unscoped service lookup.** `select(Report).filter(Report.id == ...)`
   resolves from the whole table, so the route decorator was the only thing
   between a cross-org report id and a completion.

These are source-shape checks by necessity — the behaviour they protect needs a
database, and it is covered by `tests/e2e/test_conversation_access.py`. They are
here because they are instant and because all three failures are invisible in a
passing behavioural suite that never happens to try the wrong user.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "backend"
ROUTES = BACKEND / "app" / "routes"

COMPLETION_ROUTES = ROUTES / "completion.py"
ORGANIZATION_ROUTES = ROUTES / "organization.py"
COMPLETION_SERVICE = BACKEND / "app" / "services" / "completion_service.py"
REPORT_SERVICE = BACKEND / "app" / "services" / "report_service.py"
INBOX_SERVICE = BACKEND / "app" / "services" / "inbox_service.py"


# --------------------------------------------------------------------------
# helpers — shared with the "prove it fails" driver, so keep them source-in
# --------------------------------------------------------------------------

def _decorator_name(node) -> str:
    """Dotted name of a decorator expression, call or not: 'router.get',
    'requires_permission', 'staticmethod'."""
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def misordered_gates(source: str):
    """(function name, ...) for every route whose permission decorator is
    written above its @router.* decorator and therefore never runs."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = [_decorator_name(d) for d in node.decorator_list]
        route_idx = next(
            (i for i, n in enumerate(names)
             if n.startswith("router.") or n.endswith(".router")), None)
        perm_idx = next(
            (i for i, n in enumerate(names) if n == "requires_permission"), None)
        if route_idx is None or perm_idx is None:
            continue
        # index 0 is the OUTERMOST decorator. The router must be outermost, so
        # that it registers the already-wrapped function.
        if perm_idx < route_idx:
            bad.append(node.name)
    return bad


def permission_call(source: str, func_name: str):
    """(positional args, kwargs) of requires_permission on `func_name`, both
    unparsed to source text — or None when the function has no permission
    decorator at all."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and _decorator_name(dec) == "requires_permission":
                return (
                    [ast.unparse(a) for a in dec.args],
                    {kw.arg: ast.unparse(kw.value) for kw in dec.keywords},
                )
        return None
    raise AssertionError(f"no function named {func_name!r} in the source")


def permission_kwargs(source: str, func_name: str):
    call = permission_call(source, func_name)
    return None if call is None else call[1]


def function_source(source: str, func_name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"no function named {func_name!r} in the source")


# --------------------------------------------------------------------------
# 1. every gate in every route module is reachable
# --------------------------------------------------------------------------

def test_no_permission_decorator_is_stranded_above_its_route():
    """Repo-wide, not just the two files that were broken: this mistake is
    invisible on review and produces an open endpoint, so it is worth catching
    the next one anywhere in routes/."""
    offenders = {}
    for path in sorted(ROUTES.glob("*.py")):
        bad = misordered_gates(path.read_text(encoding="utf-8"))
        if bad:
            offenders[path.name] = bad
    assert offenders == {}, (
        "@requires_permission is written above @router.* on these routes, so it "
        "never runs and the endpoint is open: " + repr(offenders)
    )


def test_the_two_repaired_modules_are_specifically_clean():
    """Named so a regression in either file says which one."""
    assert misordered_gates(COMPLETION_ROUTES.read_text(encoding="utf-8")) == []
    assert misordered_gates(ORGANIZATION_ROUTES.read_text(encoding="utf-8")) == []


# --------------------------------------------------------------------------
# 1a. the scanner still recognises the shape it was written for
# --------------------------------------------------------------------------
#
# ★`misordered_gates` matches ONE literal identifier, `requires_permission`.
# The scan above is a pure absence check over `routes/`, so the day that
# decorator is renamed — or the gate moves into a dependency — it stops
# matching anything, reports `offenders == {}`, and goes green on a tree where
# every route is open. Nothing else in this file would notice.
#
# Modelled on `test_an_immediate_watch_cannot_read_a_dead_const.py`, which
# pins its scanner against synthetic sources for the same reason. These
# fixtures are deliberately written out in full rather than read from disk:
# the point is to state the shape independently of whatever `routes/` happens
# to contain today.

_MISORDERED = '''
from app.core.permissions_decorator import requires_permission

@requires_permission("view_members")
@router.get("/organizations/{organization_id}/members")
async def list_members(organization_id: str):
    return []
'''

_CORRECT = '''
from app.core.permissions_decorator import requires_permission

@router.get("/organizations/{organization_id}/members")
@requires_permission("view_members")
async def list_members(organization_id: str):
    return []
'''

_UNGATED = '''
@router.get("/health")
async def health():
    return {"ok": True}
'''


def test_the_scanner_still_detects_a_stranded_decorator():
    """The defect itself, reconstructed. If this fails, the scan above is
    asleep — read `misordered_gates` before touching anything in `routes/`."""
    assert misordered_gates(_MISORDERED) == ["list_members"], (
        "misordered_gates no longer recognises a permission decorator written "
        "above @router.*. It matches the literal name `requires_permission`; if "
        "the decorator was renamed, update the scanner — the repo-wide check "
        "above is reporting a clean tree because it is matching NOTHING."
    )


def test_the_scanner_accepts_the_correct_order():
    """Guard the guard, the other way: a scanner that flags everything would
    also 'detect' the bug, and would be just as useless."""
    assert misordered_gates(_CORRECT) == []


def test_a_route_with_no_gate_is_not_reported_as_misordered():
    """An ungated route is a different question, deliberately not this one —
    plenty of routes here are public on purpose."""
    assert misordered_gates(_UNGATED) == []


def test_the_scanner_has_something_to_scan():
    """★An empty or moved `routes/` makes the repo-wide check above vacuous in
    the most literal way. Inside `dash-app` several of these paths do not
    resolve at all (see CLAUDE.md on the `/src` runner)."""
    modules = sorted(ROUTES.glob("*.py"))
    assert len(modules) > 20, f"only {len(modules)} route modules found at {ROUTES}"
    gated = [p.name for p in modules if "requires_permission" in p.read_text(encoding="utf-8")]
    assert len(gated) > 10, (
        f"only {len(gated)} route modules mention `requires_permission` — either "
        "the gates were removed wholesale or the decorator was renamed, and the "
        "repo-wide scan above can no longer fail: " + repr(gated)
    )


def test_the_member_directory_is_gated_at_a_permission_members_hold():
    """The route was ungated (decorator stranded above). It is now gated at
    `view_members`, the org-member baseline — deliberately NOT `manage_members`,
    because this is the directory the share and mention pickers read.
    """
    call = permission_call(
        ORGANIZATION_ROUTES.read_text(encoding="utf-8"), "get_organization_members")
    assert call is not None, "get_organization_members lost its gate"
    args, _kwargs = call
    assert args and args[0] == "'view_members'", (
        "get_organization_members should require 'view_members'. 'manage_members' "
        "would lock every non-admin out of the share and mention pickers "
        "(PromptBoxV2, NotifyRecipientPicker, the dashboard share modal, "
        f"KnowledgeExplorer); no gate at all leaves it open. Got {args!r}"
    )


# --------------------------------------------------------------------------
# 2. the report-scoped conversation routes enforce ownership
# --------------------------------------------------------------------------

# Every route in completion.py that takes a report_id. The transcript is
# owner-only: reads additionally admit full admins and project collaborators
# through the decorator's read-only bypasses, writes stay with the owner.
CONVERSATION_ROUTES = [
    "estimate_completion_tokens",
    "compact_report_context",
    "create_completion",
    "watch_completion_stream",
    "get_completions",
    "get_completions_v2",
]


@pytest.mark.parametrize("func_name", CONVERSATION_ROUTES)
def test_a_report_scoped_route_enforces_ownership(func_name):
    kwargs_present = permission_kwargs(
        COMPLETION_ROUTES.read_text(encoding="utf-8"), func_name)
    assert kwargs_present is not None, f"{func_name} has no permission decorator"
    assert kwargs_present.get("model") == "Report", (
        f"{func_name} passes no model=Report, so requires_permission skips its "
        f"whole object gate (see permissions_decorator: `if model and object_id "
        f"is not None`) — got {kwargs_present!r}"
    )
    assert kwargs_present.get("owner_only") == "True", (
        f"{func_name} passes model=Report without owner_only=True: the decorator "
        f"then checks org membership and returns without ever comparing user_id, "
        f"so any org member with the role reads or writes this conversation — "
        f"got {kwargs_present!r}"
    )


def test_every_report_scoped_completion_route_is_in_the_list_above():
    """The list is hand-maintained; this fails when a new report_id route lands
    so it gets classified rather than silently escaping the check."""
    src = COMPLETION_ROUTES.read_text(encoding="utf-8")
    found = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "report_id" in args and any(
            _decorator_name(d).startswith("router.") for d in node.decorator_list
        ):
            found.add(node.name)
    assert found == set(CONVERSATION_ROUTES), (
        "a report-scoped route appeared or vanished in completion.py; decide "
        f"whether it is owner-only and update the list. Unlisted: {found - set(CONVERSATION_ROUTES)}, "
        f"missing: {set(CONVERSATION_ROUTES) - found}"
    )


# --------------------------------------------------------------------------
# 3. the service lookups are org-scoped
# --------------------------------------------------------------------------

def test_the_completion_service_never_looks_a_report_up_unscoped():
    """All of them go through `_report_stmt`, which adds the org filter."""
    src = COMPLETION_SERVICE.read_text(encoding="utf-8")
    bare = src.count("select(Report).filter(Report.id == report_id)")
    # exactly one: the statement built inside _report_stmt itself, which then
    # appends the organization filter.
    assert bare == 1, (
        f"{bare} unscoped `select(Report).filter(Report.id == report_id)` in "
        "completion_service.py; route decorators are then the only thing "
        "standing between a cross-org report id and a turn. Use _report_stmt."
    )
    helper = function_source(src, "_report_stmt")
    assert "Report.organization_id == organization.id" in helper


def test_report_service_get_report_is_org_scoped():
    src = REPORT_SERVICE.read_text(encoding="utf-8")
    body = function_source(src, "get_report")
    assert "Report.organization_id == organization.id" in body, (
        "report_service.get_report resolves a report from the whole table; it "
        "backs the workspace read and every conversation list, so it must fail "
        "closed on a cross-org id even if a route forgets its gate."
    )


# --------------------------------------------------------------------------
# 4. a share notification links to the surface it granted
# --------------------------------------------------------------------------

def test_share_notifications_point_at_the_shared_surface():
    """/r/{id} for a dashboard, /c/{token} for a conversation — never
    /reports/{id}, the authoring workspace, whose transcript is owner-only and
    now 403s for the recipient."""
    body = function_source(INBOX_SERVICE.read_text(encoding="utf-8"), "notify_share")
    assert 'f"/r/{report.id}"' in body, "artifact shares must link to /r/{id}"
    assert 'f"/c/{token}"' in body, "conversation shares must link to /c/{token}"
    assert 'link=f"/reports/{report.id}"' not in body, (
        "notify_share still links straight at the authoring workspace"
    )
