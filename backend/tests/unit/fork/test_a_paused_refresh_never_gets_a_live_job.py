"""A paused report refresh must never be handed a live scheduler job.

Pausing a refresh used to be expressed by writing ``Report.cron_schedule =
NULL``, which DESTROYS the configured time — turning a refresh off and on again
meant setting it up from scratch. ``Report.cron_is_active`` separates WHEN from
WHETHER, and that split only means anything if every place that registers a job
consults it.

★There are TWO registration sites and they are far apart in the file:

  * ``ReportService.set_report_schedule`` — the API path, what the modal calls;
  * ``ReportService._reregister_report_cron`` — called in a loop by
    ``organization_settings_service`` when the org timezone changes, rebuilding
    every job in the organisation.

★★★Gating only the first one is the failure this guard exists for, and it is
invisible in every obvious test. The pause takes effect, the row says paused,
the tab renders "Paused" — and then somebody changes the org timezone, or the
process restarts, and the rebuild loop hands every paused report a live job
again. The refresh silently resumes, subscriber emails go back out, and nothing
anywhere records that it happened. The rebuild loop selects on
``cron_schedule.isnot(None)``, which is TRUE of a paused report, so the caller
cannot be the thing that filters them out.

★The check walks the AST rather than grepping. A text scan for
``cron_is_active`` near ``add_job`` is satisfied by the word appearing in a
comment, in a docstring, or in an unrelated line — and both of these functions
have long docstrings that discuss the flag by name. What has to be true is that
the ``add_job`` call is REACHABLE only through a condition that depends on the
flag, and only the tree can say that.

★A negative control is carried in the test rather than done once at a shell
prompt (``test_the_pre_fix_shape_is_still_rejected``): a red proof performed by
hand rots into a comment, and this file's whole value is that it goes red.

★Read-only, no schema — ``tests/unit/fork``. See CLAUDE.md.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
REPORT_SERVICE = REPO / "backend" / "app" / "services" / "report_service.py"
ORG_SETTINGS = REPO / "backend" / "app" / "services" / "organization_settings_service.py"

FLAG = "cron_is_active"
# The job id every report refresh is registered under. A scheduled PROMPT uses a
# different prefix and a different flag of its own, and must not be swept in.
JOB_ID_PREFIX = "report_"


def _parents(tree):
    table = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            table[child] = node
    return table


def _is_report_job_registration(call: ast.Call) -> bool:
    """``scheduler.add_job(..., id=f"report_{…}", ...)``.

    Matched on the job id, not on the surrounding function name: a third
    registration site added later under any name is caught the day it lands,
    which is the whole point of asking the tree instead of a list of names.
    """
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "add_job"):
        return False
    for kw in call.keywords:
        if kw.arg != "id":
            continue
        value = kw.value
        if isinstance(value, ast.JoinedStr):
            first = value.values[0] if value.values else None
            if isinstance(first, ast.Constant) and str(first.value).startswith(JOB_ID_PREFIX):
                return True
        if isinstance(value, ast.Constant) and str(value.value).startswith(JOB_ID_PREFIX):
            return True
    return False


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _statement_in(body, node, parents):
    """The element of ``body`` that contains ``node``, or None."""
    seen = set()
    cur = node
    while cur is not None:
        seen.add(cur)
        cur = parents.get(cur)
    for stmt in body:
        if stmt in seen:
            return stmt
    return None


def _local_assignments(fn, source):
    """name -> the source text of everything ever assigned to it in ``fn``.

    Deliberately accumulative and flow-insensitive: this is used to follow a
    gate through one hop of indirection (``should_register = bool(getattr(report,
    'cron_is_active', True))``), not to prove anything about ordering.
    """
    table = {}
    for node in ast.walk(fn):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                table.setdefault(target.id, []).append(
                    ast.get_source_segment(source, value) or ""
                )
    return table


def _expand(text, assignments, hops=3):
    """Inline the source of any local name the expression mentions."""
    seen = text
    for _ in range(hops):
        grown = seen
        for name, values in assignments.items():
            if name in seen:
                grown += " " + " ".join(values)
        if grown == seen:
            break
        seen = grown
    return seen


def _guard_expressions(call, fn, source, parents):
    """Every condition that stands between the function's entry and this call.

    Two shapes count, because both are used in this file:
      * the call sits inside ``if <test>:`` — an ancestor If;
      * an earlier ``if <test>: return`` bails out before reaching it.
    """
    expressions = []

    node = call
    while node is not fn and node in parents:
        parent = parents[node]
        if isinstance(parent, ast.If) and node is not parent.test:
            # only the branch bodies gate the call, not the test itself
            expressions.append(ast.get_source_segment(source, parent.test) or "")
        node = parent

    owner = _statement_in(fn.body, call, parents)
    for stmt in fn.body:
        if stmt is owner:
            break
        if isinstance(stmt, ast.If) and all(
            isinstance(inner, (ast.Return, ast.Raise)) for inner in stmt.body
        ):
            expressions.append(ast.get_source_segment(source, stmt.test) or "")

    return expressions


def _ungated_registrations(source: str):
    """Report-refresh ``add_job`` sites not gated on the pause flag.

    Returns ``[(function name, line), …]`` — a list rather than a count, so a
    failure says WHICH site is open instead of only how many.
    """
    tree = ast.parse(source)
    parents = _parents(tree)
    open_sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_report_job_registration(node)):
            continue
        fn = _enclosing_function(node, parents)
        if fn is None:
            open_sites.append(("<module>", node.lineno))
            continue
        assignments = _local_assignments(fn, source)
        gates = _guard_expressions(node, fn, source, parents)
        if not any(FLAG in _expand(gate, assignments) for gate in gates):
            open_sites.append((fn.name, node.lineno))
    return open_sites


def _registration_sites(source: str):
    tree = ast.parse(source)
    parents = _parents(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_report_job_registration(node):
            fn = _enclosing_function(node, parents)
            found.append(fn.name if fn else "<module>")
    return sorted(found)


@pytest.fixture(scope="module")
def source():
    assert REPORT_SERVICE.exists(), REPORT_SERVICE
    return REPORT_SERVICE.read_text(encoding="utf-8")


def test_every_registration_site_is_gated_on_the_pause_flag(source):
    """The defect this file exists for. One unguarded site is enough."""
    open_sites = _ungated_registrations(source)
    assert open_sites == [], (
        "these register a report refresh job without consulting "
        f"{FLAG} — a paused refresh keeps firing through them: {open_sites}"
    )


def test_both_known_registration_sites_are_still_present(source):
    """★A positive control. ``test_every_registration_site_is_gated`` is equally
    satisfied by deleting the registrations altogether, or by renaming the job
    id so nothing matches — either of which would leave a green suite over a
    product that no longer schedules anything."""
    sites = _registration_sites(source)
    assert sites == ["_reregister_report_cron", "set_report_schedule"], (
        "the set of report-refresh registration sites changed; a new one must "
        f"carry the {FLAG} gate too. Found: {sites}"
    )


def test_the_rebuild_loop_cannot_be_the_thing_that_filters(source):
    """★Why the gate lives at the registration site and not in the caller.

    ``organization_settings_service`` rebuilds every job in an org after a
    timezone change, selecting reports on ``cron_schedule.isnot(None)`` — which
    is TRUE of a paused report, because pausing deliberately keeps the time.
    If that query ever became the filter, the gate would look redundant and the
    next reader would remove it from the service. It is asserted here so that
    change fails loudly instead."""
    org = ORG_SETTINGS.read_text(encoding="utf-8")
    assert "_reregister_report_cron(r)" in org, (
        "the timezone rebuild no longer re-registers report refreshes"
    )
    assert "Report.cron_schedule.isnot(None)" in org, (
        "the rebuild loop's report query changed — re-read whether the pause "
        "gate at the registration site is still the only thing filtering "
        "paused reports out"
    )


def test_the_listing_is_deliberately_not_filtered_on_the_flag(source):
    """★★★The flag gates SCHEDULING, never LISTING, and the difference matters.

    ``get_report_refreshes`` feeds the Scheduled tab and selects on
    ``cron_schedule.isnot(None)`` alone. A paused refresh is precisely what that
    page has to show — hiding it is the old null-the-cron behaviour wearing a
    new column, where turning a refresh off made it vanish with no way back to
    it. So this guard must never be read as "consult the flag everywhere".

    ★It is asserted rather than left implicit because the tempting "fix" for a
    reviewer who has read the rest of this file is to add the flag to this
    query, and that change is silent: the tab simply stops listing paused rows,
    and the pause control the user just pressed makes the row disappear.
    """
    tree = ast.parse(source)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "get_report_refreshes"
    )
    conditions = next(
        node for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "conditions" for t in node.targets)
    )
    # ★★★Read the NODES, not ``get_source_segment``. The segment for this list
    # spans the comment that explains the exemption, and that comment names the
    # flag — so a text check fails against its own documentation. That is the
    # exact mistake CLAUDE.md records three guards making in one day, and it
    # was made here too, on the first run of this test.
    referenced = {
        node.attr for node in ast.walk(conditions.value)
        if isinstance(node, ast.Attribute)
    }
    assert FLAG not in referenced, (
        "the refresh listing now filters on the pause flag — a paused refresh "
        "would vanish from the Scheduled tab, which is the defect the column "
        "was added to fix, not a tightening of it"
    )
    # And the row still has to CARRY the flag, or the tab cannot render "Paused".
    body = ast.get_source_segment(source, fn) or ""
    assert FLAG in body, (
        "the listing no longer reports the pause state, so every row renders as "
        "though it will run"
    )


def test_unscheduling_is_not_expressible_as_a_pause(source):
    """★The bug in the ORIGINAL design, pinned so it cannot come back.

    Turning a refresh off used to null the cron string, destroying the
    configured time. Pausing must move the flag; the string is what the owner
    typed and is theirs to keep."""
    tree = ast.parse(source)
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "set_report_schedule"
    )
    body = ast.get_source_segment(source, fn) or ""
    assert FLAG in body, "set_report_schedule no longer knows about pausing"
    # The one legitimate `cron_schedule = None` is the explicit unschedule,
    # which is driven by the caller's cron_expression, never by the flag.
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Attribute) and target.attr == "cron_schedule"):
            continue
        assigned = ast.get_source_segment(source, node.value) or ""
        assert FLAG not in assigned, (
            f"cron_schedule is being written from {FLAG} at line {node.lineno} — "
            "pausing must not touch the configured time"
        )


PRE_FIX_SHAPE = '''
class ReportService:
    def set_report_schedule(self, report_id, cron_expression):
        """Docstring naming cron_is_active, which is how a text scan passes."""
        # cron_is_active is mentioned in this comment too.
        cron_expression_parsed = self._parse_cron_expression(cron_expression)
        if cron_expression_parsed is not None:
            scheduler.add_job(
                func=self.scheduled_rerun_report_steps,
                trigger="cron",
                id=f"report_{report_id}",
                replace_existing=True,
                **cron_expression_parsed
            )
'''

HALF_FIXED_SHAPE = '''
class ReportService:
    def _reregister_report_cron(self, report):
        if not getattr(report, "cron_is_active", True):
            return False
        scheduler.add_job(trigger="cron", id=f"report_{report.id}")
        return True

    def set_report_schedule(self, report_id, cron_expression):
        parsed = self._parse_cron_expression(cron_expression)
        if parsed is not None:
            scheduler.add_job(trigger="cron", id=f"report_{report_id}", **parsed)
'''


def test_the_pre_fix_shape_is_still_rejected():
    """★★★The red proof, carried in the test.

    Both fixtures below are what the file looked like before this work. The
    first mentions the flag in a docstring and a comment and nowhere else —
    exactly the code a text-matching guard calls fixed. The second is the
    half-fix the docstring warns about: one site gated, one not, which behaves
    correctly right up until a restart or a timezone change.
    """
    assert _ungated_registrations(PRE_FIX_SHAPE) == [("set_report_schedule", 8)], (
        "the checker no longer detects a completely ungated registration — it "
        "has stopped being able to fail"
    )
    assert _ungated_registrations(HALF_FIXED_SHAPE) == [("set_report_schedule", 12)], (
        "the checker accepts the half-fix: gating the rehydration loop alone "
        "leaves a paused schedule firing through the API path"
    )


def test_a_gate_reached_through_a_local_variable_still_counts():
    """The shape the real fix uses — the flag is read into a name, and the name
    is what the `if` tests. A checker that only looked at the test expression
    itself would report the real code as ungated and be deleted as noise."""
    indirect = '''
def set_report_schedule(report, cron_expression):
    parsed = _parse(cron_expression)
    should_register = bool(getattr(report, "cron_is_active", True))
    if parsed is not None and should_register:
        scheduler.add_job(trigger="cron", id=f"report_{report.id}", **parsed)
'''
    assert _ungated_registrations(indirect) == []
