"""The planner resolved the table names, then the code generator guessed instead.

`inspect_data` was called with the names already resolved against the catalog:

    tables_by_source: [{"tables": ["LK_CFC_Sales.dbo.cfc_champion", ...]}]

and the generated SQL queried a DIFFERENT database:

    client.execute_query("SELECT TOP 5 * FROM CFC_Lakehouse.dbo.cfc_champion")

`[42S02] Invalid object name 'dbo.cfc_champion'` — 24 to 42 seconds at the
server, a visible failed step, then a self-corrected retry. Reproduced twice on
2026-08-17 against the live instance.

★★★It is a DISAMBIGUATION failure, not a hallucination. `CFC_Lakehouse` is a
real database on the same connection (it holds employee tables); `cfc_champion`
is a real table in `LK_CFC_Sales`. Both halves exist, only the pairing is wrong,
so nothing rejected it locally. The proof: ONE generated function used
`LK_CFC_Sales` for `Ref_BranchMaster` and `CFC_Lakehouse` for the other two
tables, and by the third attempt the model was looping over every database in a
try/except — one server round trip per guess.

Two independent fixes, one test file:

1. The resolved names now reach all THREE code generators. They were dropped by
   `generate_inspection_code`, and used by `generate_code` only as a key for
   retrieving old snippets — never as table identity. Fixing one generator alone
   would just move the guessing to another.
2. A pre-execution check compares generated references to `datasource_tables`
   and fails locally, in milliseconds, naming the correct qualified name.

★The check must FAIL OPEN. A false positive blocks a legitimate query, which is
far worse than the latency it saves — so it may only ever fire when the catalog
positively holds the same table under exactly ONE other prefix. "I cannot find
this table" must always allow: that is an unsynced table, not a mistake.

★The pattern the guard must recognise is `ds_clients.get(...)`, NOT
`ds_clients[...]`. Every generated sample recovered from the production database
uses `.get()`, usually with an `or` fallback. A first cut of this guard handled
only the subscript form, passed every fail-open case, and was completely inert
against the real bug.
"""

import pytest

from app.ai.agents.coder.coder import _resolved_table_names, _resolved_tables_section
from app.ai.code_execution.table_reference_check import (
    Catalog,
    CatalogEntry,
    check_table_references,
    split_qualified_name,
)


CLIENT = "Microsoft Fabric:fabric_user-1"
FALLBACK = "Microsoft Fabric"

# Verbatim casing and depth from `datasource_tables` on the live instance.
CATALOG_NAMES = (
    "LK_CFC_Sales.dbo.cfc_champion",
    "LK_CFC_Sales.dbo.cfc_accuracy_by_outlet",
    "LK_CFC_Sales.dbo.Ref_BranchMaster",
    "CFC_Lakehouse.dbo.cfc_employee_data",
)


def _catalog(client_key, *names):
    return Catalog(
        client_key=client_key,
        entries=[CatalogEntry(name=n, parts=split_qualified_name(n)) for n in names],
    )


def _catalogs(*names):
    return {CLIENT: _catalog(CLIENT, *(names or CATALOG_NAMES))}


def _code(sql):
    """The generated shape this actually ships as — `.get()` with an `or`."""
    return (
        'def generate_df(ds_clients, excel_files):\n'
        f'    client = ds_clients.get("{CLIENT}") or ds_clients.get("{FALLBACK}")\n'
        f'    return client.execute_query("{sql}")\n'
    )


# --------------------------------------------------------------------------
# 1. the resolved names reach the prompt
# --------------------------------------------------------------------------

def test_the_resolved_names_survive_the_shape_the_resolver_returns():
    """`_resolve_active_tables` returns groups of fully-qualified catalog strings."""
    resolved = [{"data_source_id": "ds-1", "tables": list(CATALOG_NAMES[:2])}]

    assert _resolved_table_names(resolved) == list(CATALOG_NAMES[:2])


def test_a_dict_shaped_table_is_not_silently_missed():
    """★`getattr` against a dict MISSES rather than raising. That exact bug
    already shipped once here, putting `{'name': 'Sales'}:None` into a prompt."""
    assert _resolved_table_names([{"tables": [{"name": "cat.sch.tbl"}]}]) == ["cat.sch.tbl"]


def test_an_object_shaped_table_still_works():
    class Table:
        name = "proj.ds.events"

    class Group:
        tables = [Table()]

    assert _resolved_table_names([Group()]) == ["proj.ds.events"]


def test_names_are_deduplicated_across_groups_in_order():
    resolved = [
        {"data_source_id": "a", "tables": ["db.s.t1", "db.s.t2"]},
        {"data_source_id": "b", "tables": ["db.s.t2", "other.s.t3"]},
    ]

    assert _resolved_table_names(resolved) == ["db.s.t1", "db.s.t2", "other.s.t3"]


@pytest.mark.parametrize(
    "empty",
    [None, [], [{"data_source_id": "x", "tables": []}], [{"tables": ["", "   "]}]],
)
def test_nothing_resolved_renders_nothing_at_all(empty):
    """A header with no names under it is an invitation to invent some."""
    assert _resolved_tables_section(empty) == ""


def test_the_section_carries_the_qualified_names_verbatim():
    section = _resolved_tables_section(
        [{"data_source_id": "ds-1", "tables": list(CATALOG_NAMES[:2])}]
    )

    for name in CATALOG_NAMES[:2]:
        assert name in section
    assert "<resolved_tables>" in section


def test_the_section_names_no_connector():
    """The bug is structural — any connector with multi-part names hits it the
    moment it is attached. Nothing here may be written for one of them."""
    section = _resolved_tables_section([{"tables": ["a.b.c"]}]).lower()

    for vendor in ("fabric", "lakehouse", "snowflake", "bigquery", "databricks"):
        assert vendor not in section


# --------------------------------------------------------------------------
# 2. the pre-execution check
# --------------------------------------------------------------------------

def test_the_original_failure_is_caught_before_it_leaves_the_process():
    message = check_table_references(
        _code("SELECT TOP 5 * FROM CFC_Lakehouse.dbo.cfc_champion"),
        _catalogs(),
        {CLIENT},
    )

    assert message is not None
    assert "LK_CFC_Sales.dbo.cfc_champion" in message


def test_a_client_reached_by_subscript_is_caught_too():
    code = (
        'def generate_df(ds_clients, excel_files):\n'
        f'    return ds_clients["{CLIENT}"].execute_query('
        '"SELECT * FROM CFC_Lakehouse.dbo.cfc_champion")\n'
    )

    assert check_table_references(code, _catalogs(), {CLIENT}) is not None


def test_the_correct_name_is_left_alone():
    assert check_table_references(
        _code("SELECT TOP 5 * FROM LK_CFC_Sales.dbo.cfc_champion"), _catalogs(), {CLIENT}
    ) is None


def test_a_table_the_catalog_has_never_seen_is_allowed():
    """★The fail-open case that matters most: an unsynced or brand-new table
    must run, not be blocked by a catalog that simply does not know it yet."""
    assert check_table_references(
        _code("SELECT * FROM LK_CFC_Sales.dbo.brand_new_table"), _catalogs(), {CLIENT}
    ) is None


def test_an_ambiguous_leaf_is_allowed():
    """Same table name under two databases — there is no single right answer."""
    catalogs = {CLIENT: _catalog(CLIENT, "A.dbo.orders", "B.dbo.orders")}

    assert check_table_references(
        _code("SELECT * FROM C.dbo.orders"), catalogs, {CLIENT}
    ) is None


def test_an_empty_catalog_is_allowed():
    assert check_table_references(
        _code("SELECT * FROM CFC_Lakehouse.dbo.cfc_champion"), {}, {CLIENT}
    ) is None


def test_sql_that_is_not_a_literal_is_allowed():
    """Dynamically built SQL yields no literal, so there is nothing to judge."""
    code = (
        'def generate_df(ds_clients, excel_files):\n'
        f'    c = ds_clients.get("{CLIENT}")\n'
        '    return c.execute_query(f"SELECT * FROM {db}.dbo.cfc_champion")\n'
    )

    assert check_table_references(code, _catalogs(), {CLIENT}) is None


def test_a_name_in_a_comment_or_a_print_is_not_a_reference():
    """Extraction walks the AST and reads only a client's query argument, so
    prose is excluded by construction rather than by filtering it back out."""
    code = (
        'def generate_df(ds_clients, excel_files):\n'
        f'    client = ds_clients.get("{CLIENT}")\n'
        '    # SELECT * FROM CFC_Lakehouse.dbo.cfc_champion\n'
        '    print("reading FROM CFC_Lakehouse.dbo.cfc_champion")\n'
        '    return client.execute_query('
        '"SELECT * FROM LK_CFC_Sales.dbo.cfc_champion")\n'
    )

    assert check_table_references(code, _catalogs(), {CLIENT}) is None


def test_an_unqualified_name_is_never_blocked():
    """A bare name is usually a CTE or an alias, and has no prefix to be wrong."""
    assert check_table_references(
        _code("SELECT * FROM cfc_champion"), _catalogs(), {CLIENT}
    ) is None


def test_a_shallower_qualification_still_matches_a_deeper_catalog_entry():
    """Catalogs record names at whatever depth the connector reported."""
    catalogs = {CLIENT: _catalog(CLIENT, "SalesDB.dbo.orders")}

    assert check_table_references(
        _code("SELECT * FROM dbo.orders"), catalogs, {CLIENT}
    ) is None


def test_an_unknown_client_is_allowed():
    code = _code("SELECT * FROM CFC_Lakehouse.dbo.cfc_champion").replace(
        CLIENT, "Some Other Source:pg-1"
    ).replace(FALLBACK, "Some Other Source")

    assert check_table_references(code, _catalogs(), {CLIENT}) is None


def test_a_dead_or_operand_does_not_veto_the_check():
    """★`ds_clients.get("a") or ds_clients.get("b")` where `b` is not a key at
    all: `.get` returns None, the `or` falls through, so `b` can never be the
    client at runtime and must not be read as an unknown that blocks judgement."""
    message = check_table_references(
        _code("SELECT * FROM CFC_Lakehouse.dbo.cfc_champion"), _catalogs(), {CLIENT}
    )

    assert message is not None


def test_two_live_operands_backed_by_different_sources_decline():
    """If the two candidates disagree, we cannot know which one runs."""
    catalogs = {
        CLIENT: _catalog(CLIENT, *CATALOG_NAMES),
        FALLBACK: _catalog(FALLBACK, "Elsewhere.dbo.nothing_alike"),
    }

    assert check_table_references(
        _code("SELECT * FROM CFC_Lakehouse.dbo.cfc_champion"),
        catalogs,
        {CLIENT, FALLBACK},
    ) is None


@pytest.mark.parametrize(
    "catalog_name, written",
    [
        ("proj.ds.events", "wrongproj.ds.events"),      # BigQuery shape
        ("public.orders", "sales.orders"),               # Postgres shape
        ("main.default.people", "other.default.people"),  # Databricks shape
    ],
)
def test_the_check_is_not_written_for_one_connector(catalog_name, written):
    """On this install only one source has multi-part names today, but the same
    failure appears the moment any multi-catalog connector is attached."""
    catalogs = {CLIENT: _catalog(CLIENT, catalog_name)}

    message = check_table_references(_code(f"SELECT * FROM {written}"), catalogs, {CLIENT})

    assert message is not None
    assert catalog_name in message


# --------------------------------------------------------------------------
# 5. the check has to work on EVERY connector, not only the one that failed
# --------------------------------------------------------------------------
# ★★★Measured 2026-08-17, after the check had already shipped and been called
# "connector-agnostic" in its own docstring: it CAUGHT Fabric and Databricks and
# ALLOWED Snowflake, BigQuery, Postgres and Trino. The docstring was describing
# the intent, not the behaviour.
#
# The reason is the catalog's DEPTH, not the connector. Fabric stores all three
# segments (`LK_CFC_Sales.dbo.cfc_champion`), so a wrong database makes the
# comparison an exact one and it fails. Snowflake stores two (`PUBLIC.ORDERS`)
# and Trino one (`orders`), so a wrong leading segment is simply surplus — and
# the old suffix-tolerant match accepted ANY value there. `WRONG_DB.PUBLIC.ORDERS`
# passed against a catalog holding `PUBLIC.ORDERS`.
#
# A surplus segment is therefore only judgeable against the container the client
# is actually bound to. That is read off the live client (`database` / `catalog`
# / `project_id`), and where it is unknown the surplus is allowed, exactly as
# before.

from app.ai.code_execution.table_reference_check import _container_name

KEY = "src:conn-1"


def _shaped(database, *names):
    return {
        KEY: Catalog(
            client_key=KEY,
            database=database,
            entries=[CatalogEntry(name=n, parts=split_qualified_name(n)) for n in names],
        )
    }


def _plain_code(sql):
    return (
        'def generate_df(ds_clients, excel_files):\n'
        f'    c = ds_clients.get("{KEY}")\n'
        f'    return c.execute_query("{sql}")\n'
    )


def _verdict(catalogs, written):
    return check_table_references(
        _plain_code(f"SELECT * FROM {written}"), catalogs, {KEY}
    )


@pytest.mark.parametrize(
    "label, database, entries, written",
    [
        # the shape that was already caught — kept so a fix here cannot lose it
        ("fabric/databricks", "lk_cfc_sales", ("RIGHT_DB.dbo.orders",), "WRONG_DB.dbo.orders"),
        # the four that were silently allowed
        ("snowflake", "RIGHT_DB", ("PUBLIC.ORDERS",), "WRONG_DB.PUBLIC.ORDERS"),
        ("bigquery", "rightproject", ("analytics.events",), "wrongproject.analytics.events"),
        ("postgres", "rightdb", ("public.orders",), "otherdb.public.orders"),
        ("trino/athena", "rightcat", ("orders",), "wrongcat.orders"),
    ],
)
def test_a_wrong_database_is_caught_whatever_depth_the_catalog_stores(
    label, database, entries, written
):
    message = _verdict(_shaped(database, *entries), written)
    assert message is not None, f"{label}: `{written}` was allowed"
    assert entries[0] in message, f"{label}: the repair message must name the real table"


@pytest.mark.parametrize(
    "label, database, entries, written",
    [
        ("the database spelled out correctly", "rightdb", ("public.orders",), "rightdb.public.orders"),
        ("case differs", "rightdb", ("public.orders",), "RIGHTDB.PUBLIC.ORDERS"),
        ("under-qualified, which is always legal", "rightdb", ("rightdb.public.orders",), "public.orders"),
        # ★the fail-open cases — each one is a thing we cannot judge, not a thing
        # we judged and approved
        ("no container name known", None, ("public.orders",), "otherdb.public.orders"),
        ("surplus of two segments", "rightcat", ("orders",), "a.b.orders"),
        ("leaf absent from the catalog", "rightdb", ("public.orders",), "otherdb.public.widgets"),
        ("leaf ambiguous", "rightdb", ("a.orders", "b.orders"), "otherdb.c.orders"),
    ],
)
def test_a_legitimate_or_unjudgeable_query_still_runs(label, database, entries, written):
    assert _verdict(_shaped(database, *entries), written) is None, label


def test_a_power_bi_slash_name_is_the_same_table_as_the_dotted_one():
    """Power BI and Analysis Services store `Dataset/Table`. Splitting on the dot
    alone leaves that as ONE segment, so its leaf never lines up with a dotted
    reference to the same table."""
    assert split_qualified_name("MyDataset/Sales") == ("mydataset", "sales")
    assert _verdict(_shaped(None, "MyDataset/Sales"), "MyDataset.Sales") is None


class _Client:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_the_container_name_is_read_from_whatever_the_connector_calls_it():
    assert _container_name(_Client(database="SalesDB")) == "salesdb"
    assert _container_name(_Client(catalog="main")) == "main"
    assert _container_name(_Client(project_id="my-project")) == "my-project"


def test_a_file_path_or_a_federated_client_yields_no_container_name():
    """★A DuckDB `database` is a path on disk and a Fabric federated client is
    bound to no single lakehouse. Either one taken as a container name would
    judge a correct query against a name the SQL could never have carried."""
    assert _container_name(_Client(database="/data/local.duckdb")) is None
    assert _container_name(_Client(database="")) is None
    assert _container_name(_Client()) is None
