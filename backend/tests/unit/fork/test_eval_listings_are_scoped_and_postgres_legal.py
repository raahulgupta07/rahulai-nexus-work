"""The two eval listings must be legal on Postgres and narrowed in SQL.

Both properties are invisible to the rest of the suite, for the same reason:
the test database is SQLite and the product database is Postgres.

**Postgres legality.** ``GET /api/tests/runs`` and the ``get_eval_runs`` agent
tool paginate by taking a DISTINCT over run ids and ordering by ``created_at``.
Postgres refuses that outright -- for ``SELECT DISTINCT`` every ORDER BY
expression must appear in the select list (42P10), rejected at parse time, so
an empty table does not save you. SQLite accepts it happily, which is why the
whole suite was green while the live endpoint 500'd. Measured against the live
Postgres on 2026-08-08::

    SELECT DISTINCT test_runs.id FROM test_runs JOIN ... ORDER BY test_runs.created_at DESC
    ERROR:  for SELECT DISTINCT, ORDER BY expressions must appear in select list

The fix selects ``created_at`` alongside the id. It is functionally determined
by the id, so the pair is still one row per run and the page is unchanged.

So this guard does not run SQL. It compiles the statement the production code
actually builds, using the **Postgres** dialect, and applies the same rule the
server applies. Compiling the real statement is the point: asserting on a
statement the test itself assembles would only prove the test can spell it.

**SQL narrowing.** The per-agent panels used to fetch a page of ORG-WIDE rows
and filter it in the browser, so an agent whose runs sat past the org's 100
newest showed "no runs". The narrowing has to be in the WHERE clause, before
LIMIT, or the cap bounds the organization's history instead of the agent's.
"""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

BACKEND = Path(__file__).resolve().parents[3]
REPO = BACKEND.parent

RUN_SERVICE = BACKEND / "app" / "services" / "test_run_service.py"
EVAL_TOOL = BACKEND / "app" / "ai" / "tools" / "implementations" / "get_eval_runs.py"
CASE_SERVICE = BACKEND / "app" / "services" / "test_case_service.py"
PANEL = REPO / "frontend" / "components" / "AgentEvalsPanel.vue"


# --- the Postgres rule, spelled out once -------------------------------------

def order_by_terms_missing_from_select_list(sql: str) -> list:
    """Return the ORDER BY expressions a Postgres SELECT DISTINCT would reject.

    This is 42P10 and nothing else: with DISTINCT in play, every ORDER BY
    expression must also be selected. A non-empty return value is the exact
    shape psql rejects.
    """
    flat = " ".join(sql.split())
    m = re.search(r"SELECT\s+DISTINCT\s+(.*?)\s+FROM\s", flat, re.IGNORECASE)
    if not m:
        return []  # no DISTINCT: the rule does not apply
    select_list = m.group(1)
    o = re.search(r"\sORDER\s+BY\s+(.*?)(?:\s+LIMIT\s|\s+OFFSET\s|$)", flat, re.IGNORECASE)
    if not o:
        return []
    missing = []
    for term in o.group(1).split(","):
        expr = re.sub(r"\s+(ASC|DESC)$", "", term.strip(), flags=re.IGNORECASE)
        if expr not in select_list:
            missing.append(expr)
    return missing


def test_the_rule_itself_can_fail():
    """The checker above is worthless if it cannot answer 'no'."""
    bad = "SELECT DISTINCT test_runs.id FROM test_runs ORDER BY test_runs.created_at DESC"
    good = "SELECT DISTINCT test_runs.id, test_runs.created_at FROM test_runs ORDER BY test_runs.created_at DESC"
    assert order_by_terms_missing_from_select_list(bad) == ["test_runs.created_at"]
    assert order_by_terms_missing_from_select_list(good) == []


# --- the real statement, compiled for Postgres -------------------------------

class _CapturingResult:
    def all(self):
        return []

    def scalars(self):
        return self

    def unique(self):
        return self


class _CapturingSession:
    """Stands in for AsyncSession: records what was executed, returns nothing.

    An empty first result makes ``list_runs`` return early, so nothing past the
    id query is exercised -- which is all this guard is about.
    """

    def __init__(self):
        self.statements = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        return _CapturingResult()


async def _capture_list_runs(**kwargs):
    from app.services.test_run_service import TestRunService

    db = _CapturingSession()
    out = await TestRunService().list_runs(db, "org-1", None, **kwargs)
    assert out == []
    assert db.statements, "list_runs issued no query at all"
    return db.statements[0]


def _pg(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_the_runs_listing_compiles_to_legal_postgres():
    sql = _pg(await _capture_list_runs(limit=20))
    assert "DISTINCT" in sql.upper(), "the pagination query stopped using DISTINCT — re-read this guard"
    missing = order_by_terms_missing_from_select_list(sql)
    assert not missing, (
        "Postgres would reject this with 42P10; ORDER BY expressions absent from "
        f"the SELECT DISTINCT list: {missing}\n{sql}"
    )


@pytest.mark.asyncio
async def test_the_runs_listing_narrows_to_one_agent_in_sql():
    """The agent filter must reach the WHERE clause, not the browser."""
    wide = _pg(await _capture_list_runs(limit=20))
    narrow = _pg(await _capture_list_runs(limit=20, data_source_id="ds-42"))
    assert "data_source_ids_json" not in wide, (
        "an unnarrowed listing must stay org-wide")
    assert "data_source_ids_json" in narrow, (
        "data_source_id did not reach SQL — the panel is filtering a page of "
        "org-wide rows again")
    assert '"ds-42"' in narrow, "the agent id is not in the predicate"
    # Before LIMIT, or the cap still bounds the organization and not the agent.
    assert narrow.index("data_source_ids_json") < narrow.upper().rindex("LIMIT")

    glob = _pg(await _capture_list_runs(limit=20, scope="global"))
    assert "data_source_ids_json" in glob and '"ds-42"' not in glob


# --- the same defect, statically, everywhere it can occur --------------------

def _distinct_chains_with_bad_order_by(source: str) -> list:
    """Find ``.with_only_columns(...).order_by(X).distinct()`` chains whose
    ORDER BY names a column the chain does not select.

    Static because the agent tool's statement sits behind permission resolution
    and an async generator; driving it would mock more than it measures.
    """
    tree = ast.parse(source)
    findings = {}

    def attr_name(node):
        # TestRun.created_at.desc() -> "TestRun.created_at"
        while isinstance(node, ast.Call):
            node = node.func
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        parts.reverse()
        if parts and parts[-1] in ("desc", "asc"):
            parts.pop()
        return ".".join(parts)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Walk one chain from this call down to its with_only_columns. Keyed on
        # that call's line so the nested Call nodes ast.walk also visits report
        # the same chain once, not once per link.
        selected, ordered, saw_distinct, anchor = None, [], False, None
        cur = node
        while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            name = cur.func.attr
            if name == "distinct":
                saw_distinct = True
            elif name == "order_by":
                ordered = [attr_name(a) for a in cur.args]
            elif name == "with_only_columns":
                anchor = cur.lineno
                selected = {attr_name(a) for a in cur.args}
            cur = cur.func.value
        if saw_distinct and anchor is not None and ordered:
            missing = [o for o in ordered if o and o not in (selected or set())]
            if missing:
                findings[anchor] = missing
    return sorted(findings.items())


@pytest.mark.parametrize("path", [RUN_SERVICE, EVAL_TOOL], ids=lambda p: p.name)
def test_no_distinct_orders_by_a_column_it_does_not_select(path):
    bad = _distinct_chains_with_bad_order_by(path.read_text())
    assert not bad, (
        f"{path.name} builds a SELECT DISTINCT that Postgres rejects (42P10) at "
        f"line(s) {bad}. Select the ORDER BY column too."
    )


def test_the_static_scan_can_fail():
    """A scan that cannot match real code is a comment with a test's salary."""
    broken = (
        "id_stmt = (\n"
        "    stmt.with_only_columns(TestRun.id, maintain_column_froms=True)\n"
        "    .order_by(TestRun.created_at.desc())\n"
        "    .distinct()\n"
        "    .limit(5)\n"
        ")\n"
    )
    assert _distinct_chains_with_bad_order_by(broken) == [(2, ["TestRun.created_at"])]
    fixed = broken.replace("TestRun.id,", "TestRun.id, TestRun.created_at,")
    assert _distinct_chains_with_bad_order_by(fixed) == []


# --- the case listing, and the panel that consumes both ----------------------

def test_the_case_listing_takes_the_agent_narrowing():
    src = CASE_SERVICE.read_text()
    assert "agent_scope_clause" in src, (
        "list_cases_multi no longer narrows in SQL")
    where = src.index("agent_scope_clause(TestCase.data_source_ids_json")
    assert where < src.index(".limit(limit)"), (
        "the agent filter must be applied before LIMIT, or the page is still "
        "the organization's newest cases")


def test_the_panel_asks_the_server_for_one_agent():
    src = PANEL.read_text()
    assert "scope=global" in src and "data_source_id=${encodeURIComponent" in src, (
        "AgentEvalsPanel no longer sends a scope with its listings")
    for endpoint in ("/api/tests/cases?limit=500", "/api/tests/runs?limit=100"):
        line = next(l for l in src.splitlines() if endpoint in l)
        assert "scopeQuery" in line, f"{endpoint} is still fetched org-wide: {line.strip()}"
    # The client-side intersection is what produced "no runs" for an agent with
    # plenty; it must be gone, not merely bypassed.
    assert "runResultsCaseIds" not in src


def test_a_new_test_case_defaults_to_this_agents_own_suite():
    """Filing defaulted to whichever suite sorted first in the ORGANIZATION."""
    src = PANEL.read_text()
    body = src[src.index("function addNewTest"):]
    body = body[:body.index("\n}")]
    assert "ownSuiteIds" in body, (
        "addNewTest still defaults to an org-wide suite map — it can file a "
        "case onto another agent's shelf")
    assert "Object.keys(suitesById" not in body
