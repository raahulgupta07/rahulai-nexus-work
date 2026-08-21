"""A test that never looked at the catalog must say so, not report emptiness.

WHY THIS FILE EXISTS
--------------------
`POST /connections/{id}/test` answered `schema_access: false, table_count: 0`
on a healthy DuckDB connection holding **eleven** real tables — in the same
second `GET /connections/{id}` said 11. Two faults, and each alone was enough:

  * `DuckDBClient.test_connection` ran the counting query and THREW THE RESULT
    AWAY, so the client reported nothing about the catalog it had just read;
  * the route filled that silence with invented defaults —
    `result.get("table_count", 0)` and `result.get("schema_access", False)` —
    so "the test did not look" and "the test looked and found nothing" arrived
    at the modal spelled identically.

This is the same false-fact class as `ConnectionTable.no_rows`, whose
`NOT NULL DEFAULT 0` made "never measured" and "genuinely empty" indistinguish-
able. The fix is `Optional` all the way through: **None means nobody measured**.

HOW THIS IS MEASURED, AND WHY
-----------------------------
The client is driven against a REAL DuckDB file — `citymart_retail.duckdb`,
the demo data source shipped in this repo — because the defect was that a query
ran and its answer was discarded. A stubbed client proves the plumbing accepts
a number and says nothing about whether one is produced.

★The source scan strips comments and docstrings first. It has to: the fix's own
comments QUOTE the broken expression ("★No invented defaults… `result.get(...)`"
in the route, and the schema's docstring spells out the false `schema_access:
false, table_count: 0`). This repo has shipped a guard that matched its own
explanation four separate times; a plain grep here would fail against the
CORRECT file and read as the fix not having landed.
"""
import ast
from pathlib import Path

import pytest

from app.data_sources.clients.duckdb_client import DuckDBClient
from app.routes import connection as connection_routes
from app.schemas.connection_schema import ConnectionTestResult

#: The demo DuckDB database that is part of this repo. Eleven tables in `main`.
DEMO_DB = Path(__file__).resolve().parents[3] / "demo-datasources" / "citymart_retail.duckdb"
EXPECTED_TABLES = 11

# The two invented defaults, verbatim as `ast.unparse` renders them — string
# literals normalise to single quotes, so the double-quoted spelling in the file
# would never match and the scan would be an assertion that cannot fail.
INVENTED_TABLE_COUNT = "result.get('table_count', 0)"
INVENTED_SCHEMA_ACCESS = "result.get('schema_access', False)"
HONEST_TABLE_COUNT = "result.get('table_count')"
HONEST_SCHEMA_ACCESS = "result.get('schema_access')"


def executable_source(module) -> str:
    """A module's source with every comment and docstring removed.

    Comments go because `ast.parse` never keeps them; docstrings are dropped
    node by node. What remains is only text that runs — which is the only text
    a guard about behaviour has any business reading.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


@pytest.fixture(scope="module")
def route_code() -> str:
    return executable_source(connection_routes)


# ═══════════════════════════════════════════════════════════════════════════
# The schema can hold "nobody measured"
# ═══════════════════════════════════════════════════════════════════════════


def test_a_test_that_never_counted_tables_answers_none():
    """None is a legal, meaningful answer — not a validation error."""
    result = ConnectionTestResult(success=True, message="connected")

    assert result.table_count is None
    assert result.schema_access is None


def test_none_can_be_said_explicitly_too():
    """A client that KNOWS it did not look can say so out loud.

    The route passes `result.get(...)` straight through, so the field has to
    accept an explicit None as well as an omitted one — otherwise the route's
    honest answer becomes a 500.
    """
    result = ConnectionTestResult(
        success=True, message="connected", table_count=None, schema_access=None
    )

    assert result.table_count is None
    assert result.schema_access is None


def test_a_genuine_zero_is_still_a_zero():
    """★THE POSITIVE CONTROL. An empty catalog is a MEASUREMENT.

    Making everything optional is only half a fix. A connection that really
    exposes no tables must keep saying 0 — a change that collapsed 0 to None
    would satisfy the absence cases above and lose the very distinction this
    file is about, from the other side.
    """
    result = ConnectionTestResult(
        success=True, message="connected", table_count=0, schema_access=False
    )

    assert result.table_count == 0
    assert result.table_count is not None
    assert result.schema_access is False
    assert result.schema_access is not None


# ═══════════════════════════════════════════════════════════════════════════
# The route stopped inventing the answer
# ═══════════════════════════════════════════════════════════════════════════


def test_the_route_no_longer_invents_a_table_count(route_code):
    assert INVENTED_TABLE_COUNT not in route_code


def test_the_route_no_longer_invents_schema_access(route_code):
    assert INVENTED_SCHEMA_ACCESS not in route_code


def test_the_route_still_passes_both_values_on(route_code):
    """★THE POSITIVE CONTROL for the two scans above.

    Deleting the fields from the route entirely satisfies both absence
    assertions — and would leave the modal reading None on a connection with
    eleven tables, which is a different wrong answer, not a fix. This is also
    what proves the scan can SEE the real code: if the stripper were eating
    string literals (a mistake made in this repo before), this assertion fails
    and the two above become vacuous.
    """
    assert HONEST_TABLE_COUNT in route_code
    assert HONEST_SCHEMA_ACCESS in route_code


def test_the_comment_that_explains_the_fix_is_not_what_is_being_scanned():
    """★The trap, pinned so nobody rediscovers it by losing an afternoon.

    The route's comments quote the broken form on purpose, to say why it is
    gone. A raw read of the file therefore CONTAINS the string this file
    asserts is absent, and a guard written without the stripper fails against
    the correct product. Fourth time in this repo; last time it cost a day.
    """
    raw = Path(connection_routes.__file__).read_text()
    stripped = executable_source(connection_routes)

    if "invented defaults" not in raw:
        pytest.skip(
            "the explanatory comment is gone, so a raw scan is no longer "
            "misleading here. Nothing is broken — read this file's module "
            "docstring before removing the stripper anyway."
        )
    assert len(stripped) < len(raw)


# ═══════════════════════════════════════════════════════════════════════════
# The client answers with what it actually counted
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def demo_database() -> Path:
    if not DEMO_DB.exists():
        pytest.skip(
            f"{DEMO_DB} is not present. This is a FILE read of a demo data "
            "source shipped in the repo; it is absent inside the application "
            "image, which has no repo tree. Run this file from a checkout."
        )
    return DEMO_DB


def test_a_duckdb_database_reports_the_tables_it_has(demo_database):
    """Eleven tables, and the client says eleven.

    The counting query ran here for years and its result was thrown away, so
    this is the assertion the whole defect turned on.
    """
    result = DuckDBClient(database=str(demo_database)).test_connection()

    assert result["success"] is True
    assert result["table_count"] == EXPECTED_TABLES


def test_a_duckdb_database_reports_that_it_read_the_catalog(demo_database):
    """It listed the tables, so catalog access is CONFIRMED, not assumed."""
    result = DuckDBClient(database=str(demo_database)).test_connection()

    assert result["schema_access"] is True


def test_the_clients_answer_survives_the_schema_unchanged(demo_database):
    """End to end, minus the HTTP: what the client counted is what is served.

    The two halves of this defect were independent — a route inventing zeros
    over a client that had counted correctly would look exactly the same to a
    user — so the seam between them is asserted rather than assumed.
    """
    result = DuckDBClient(database=str(demo_database)).test_connection()

    served = ConnectionTestResult(
        success=result.get("success", False),
        message=result.get("message", ""),
        schema_access=result.get("schema_access"),
        table_count=result.get("table_count"),
    )

    assert served.table_count == EXPECTED_TABLES
    assert served.schema_access is True
