"""Column `description`/`metadata` must survive the last hop to the agent.

`normalize_indexed_columns` is the ONE persist shape and it deliberately carries
both (read its docstring). The renderer at
`app/ai/context/sections/tables_schema_section.py` (`_COLUMN_META_KEYS`) reads
`summarize_by`, `format_string`, `sort_by_column`, `data_category`,
`display_folder`, `hidden` and `unique_name` off the column object for EVERY
connector. Between those two, three places rebuilt each column as {name, dtype}
and threw the rest away:

  1. `powerbi_multitenant_scan._normalize_columns` — a LOCAL copy of the
     normalizer, on the Power BI user sign-in and OAuth callback paths. What it
     returned went straight into the user's overlay via `_upsert_user_overlay`,
     so the loss was at the last hop before STORAGE.
  2. `data_source_service` — the `{name, dtype}` dict comprehension in
     `_merge_all_fabric_endpoints` (persist), and the two `TableColumn(...)`
     rebuilds that hand the freshly-synced tables back to the caller, in
     `get_user_data_source_schema` and the Fabric federated return.
  3. `DataSource.get_schemas()` — reading an ALREADY-RICH stored row and
     dropping both. LLM-facing: it feeds `prompt_schema()` → `llm_sync` and
     `generate_data_source_items`, the agent's own learning/onboarding overview.

★This is the dangerous kind of defect: it loses nothing until a client starts
emitting something, and then it loses it silently, with nothing failing and
nothing logged. A measure arrives as an untyped column; a currency column
arrives with no `format_string`, so the agent reformats numbers the customer's
own reports render differently.

★RED-PROOF — measured 2026-08-17 by reverting all three sites to their pre-fix
bodies and running this file: **9 failed, 7 passed**; with the fix, 16 passed.
The 9 are every case in `TestPowerBiMultitenantScan` except the bare-column one
(4 of 5), the two enrichment cases in `TestFabricAndUserSchemaSites` (2 of 3),
and every case in `TestGetSchemas` except the bare-column one (3 of 4).
The bare-column cases pass on both trees on purpose: they pin that the fix did
not buy its win by inventing `None`s or empty dicts, which the renderer would
read as "has metadata". `TestTheGuardsWouldHaveFailed` carries that proof INSIDE
the suite — a red proof done once at a shell prompt rots into a comment.
"""
import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource
from app.models.datasource_table import DataSourceTable
from app.services.powerbi_multitenant_scan import _normalize_tables

SERVICE_PATH = Path(__file__).parents[3] / "app" / "services" / "data_source_service.py"


# One indexed model shaped like real Power BI output: a hidden surrogate join
# key, a currency column carrying the formatting keys the renderer reads, and a
# measure carrying its DAX expression.
RICH_COLUMNS = [
    {"name": "svc_vehicle_key", "dtype": "Integer",
     "metadata": {"role": "column", "hidden": True, "unique_name": "[svc_vehicle_key]"}},
    {"name": "parts_cost", "dtype": "Number",
     "description": "Cost of parts consumed",
     "metadata": {"role": "column", "summarize_by": "sum",
                  "format_string": "\\$#,0.00;(\\$#,0.00)",
                  "sort_by_column": "parts_cost_rank",
                  "data_category": "Currency",
                  "display_folder": "Costs"}},
    {"name": "Total Parts Cost", "dtype": "measure",
     "description": "SUM(fact_service_event[parts_cost])",
     "metadata": {"role": "measure", "returns": "Number", "summarize_by": "none",
                  "format_string": "\\$#,0.00"}},
]

RICH_PKS = [
    {"name": "svc_event_key", "dtype": "Integer",
     "description": "Surrogate key",
     "metadata": {"role": "column", "hidden": True}},
]

BARE_COLUMNS = [{"name": "plain", "dtype": "Text"}]


def _meta_and_desc(cols):
    """(metadata entries, descriptions) surviving, over dicts or TableColumns."""
    def _get(c, k):
        return c.get(k) if isinstance(c, dict) else getattr(c, k, None)
    meta = sum(len(_get(c, "metadata") or {}) for c in cols)
    desc = sum(1 for c in cols if _get(c, "description"))
    return meta, desc


class TestPowerBiMultitenantScan:
    """Site 1 — `_normalize_tables`, live on Power BI user sign-in and OAuth."""

    @staticmethod
    def _normalize(columns=None, pks=None):
        out = _normalize_tables(
            [{"name": "fact_service_event",
              "columns": RICH_COLUMNS if columns is None else columns,
              "pks": RICH_PKS if pks is None else pks,
              "fks": [],
              "metadata_json": {"powerbi": {"datasetId": "ds1",
                                            "tableName": "fact_service_event"}}}],
            tenant_id="t1", tenant_name="Contoso",
        )
        return out["fact_service_event"]

    def test_nothing_is_lost_across_the_whole_table(self):
        """The headline number: count what went in and what came out."""
        entry = self._normalize()
        assert _meta_and_desc(RICH_COLUMNS + RICH_PKS) == (15, 3), "fixture drifted"
        assert _meta_and_desc(entry["columns"] + entry["pks"]) == (15, 3)

    def test_the_powerbi_formatting_keys_survive_by_name(self):
        """★The keys the Power BI client work depends on, asserted individually
        rather than by a count — a count passes if the right number of wrong
        keys arrives."""
        cols = {c["name"]: c for c in self._normalize()["columns"]}
        cost = cols["parts_cost"]["metadata"]
        assert cost["summarize_by"] == "sum"
        assert cost["format_string"] == "\\$#,0.00;(\\$#,0.00)"
        assert cost["sort_by_column"] == "parts_cost_rank"
        assert cost["data_category"] == "Currency"
        assert cost["display_folder"] == "Costs"
        assert cols["parts_cost"]["description"] == "Cost of parts consumed"
        assert cols["Total Parts Cost"]["metadata"]["summarize_by"] == "none"

    def test_pks_are_normalized_by_the_same_helper(self):
        """★The old local helper was called for BOTH columns and pks, so giving
        pks a lossier shape here would invent a difference this layer never had.
        `normalize_indexed_columns` carries pks through; match it."""
        pk = self._normalize()["pks"][0]
        assert pk["metadata"] == {"role": "column", "hidden": True}
        assert pk["description"] == "Surrogate key"

    def test_a_bare_column_gains_no_empty_keys(self):
        """★Passes on both trees, deliberately. The renderer branches on
        PRESENCE, so a stored `"metadata": {}` or `"description": None` reads as
        'has metadata'. Keys must be OMITTED."""
        col = self._normalize(columns=BARE_COLUMNS, pks=[])["columns"][0]
        assert col == {"name": "plain", "dtype": "Text"}

    def test_the_local_normalizer_is_gone(self):
        """★Not decoration: a second extension of the persist shape is how the
        first one silently stops being canonical. Three modules kept private
        block-lists of loadable file formats for exactly this reason."""
        import app.services.powerbi_multitenant_scan as mod
        assert not hasattr(mod, "_normalize_columns")


# --- Site 2 — the three rebuilds in data_source_service --------------------
#
# These live deep inside async methods that discover Fabric endpoints and write
# overlays, so they cannot be called from `tests/unit/fork` (no schema, no
# session). The checks below read the SOURCE instead, and every one of them is
# run against a reconstruction of the pre-fix code in
# `TestTheGuardsWouldHaveFailed` so they are shown to reject the bug rather than
# merely to accept the fix.

def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} no longer exists — re-target this guard")


def _column_comprehensions(fn):
    """Every list comprehension in `fn` that iterates a COLUMN list.

    Keyed on the iterable, not on the element, so it finds both the
    `TableColumn(...)` rebuilds and any hand-rolled `{name, dtype}` dict.
    """
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.ListComp) and "columns" in ast.unparse(n.generators[0].iter)]


def _persisted_columns_values(fn):
    """The value assigned to a `"columns"` key of a dict literal in `fn` —
    i.e. what gets written into the overlay payload."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "columns":
                    out.append(v)
    return out


def _check_column_comprehensions_carry_both(fn):
    """Failures, as sentences. A comprehension over a column list must either
    delegate to the lossless normalizer or name both fields itself."""
    problems = []
    for comp in _column_comprehensions(fn):
        src = ast.unparse(comp)
        if "normalize_indexed_columns" in src or "normalize_columns" in src:
            continue
        if not ("description=" in src or "'description'" in src or '"description"' in src):
            problems.append(f"{fn.name}: drops description -> {src}")
        if not ("metadata=" in src or "'metadata'" in src or '"metadata"' in src):
            problems.append(f"{fn.name}: drops metadata -> {src}")
    return problems


def _check_persisted_columns_use_the_normalizer(fn):
    problems = []
    for value in _persisted_columns_values(fn):
        src = ast.unparse(value)
        if "normalize_indexed_columns" in src or "normalize_columns" in src:
            continue
        problems.append(f"{fn.name}: persists a hand-rolled column shape -> {src}")
    return problems


@pytest.fixture(scope="module")
def service_tree():
    return ast.parse(SERVICE_PATH.read_text())


class TestFabricAndUserSchemaSites:
    def test_every_column_rebuild_carries_both_fields(self, service_tree):
        """`get_user_data_source_schema` and the Fabric federated return both
        rebuilt the prompt column from {name, dtype} ONE LINE after the lossless
        normalizer had carried the rest into the payload."""
        problems = []
        for name in ("get_user_data_source_schema", "_merge_all_fabric_endpoints"):
            problems += _check_column_comprehensions_carry_both(_fn(service_tree, name))
        assert problems == []

    def test_the_fabric_overlay_is_persisted_through_the_normalizer(self, service_tree):
        """★The persist half. `_merge_all_fabric_endpoints` writes `normalized`
        straight into the user's overlay, so a `{name, dtype}` literal here
        discards the metadata before anything can read it back."""
        assert _check_persisted_columns_use_the_normalizer(
            _fn(service_tree, "_merge_all_fabric_endpoints")) == []

    def test_the_pks_rebuild_is_still_deliberately_bare(self, service_tree):
        """★Positive control on the ONE site that must NOT change.
        `ConnectionTable.to_prompt_table` and the legacy `DataSourceTable` path
        both render pks as {name, dtype}, and
        `test_column_metadata_survives_both_paths` pins that they AGREE.
        Enriching pks here alone recreates that divergence pointing the other
        way — so this guard fails a well-meant 'fix everything' change and says
        why."""
        fn = _fn(service_tree, "get_user_data_source_schema")
        pk_comps = [n for n in ast.walk(fn)
                    if isinstance(n, ast.ListComp) and "pks" in ast.unparse(n.generators[0].iter)]
        assert len(pk_comps) == 1
        src = ast.unparse(pk_comps[0])
        assert "description" not in src and "metadata" not in src


class TestGetSchemas:
    """Site 3 — `DataSource.get_schemas()`, the LLM-facing one.

    It needs an AsyncSession only to re-load itself; the session is stubbed so
    the column rebuild can be exercised without a schema (this directory's
    `run_migrations` is a no-op, so a schema-needing test cannot live here).
    """

    @staticmethod
    async def _schemas(columns):
        table = DataSourceTable(
            name="fact_service_event",
            columns=columns,
            pks=[{"name": "svc_event_key", "dtype": "Integer"}],
            fks=[],
            is_active=True,
            metadata_json=None,
        )
        table.id = "t-1"
        table.connection_table = None
        ds = DataSource(name="FleetOps")
        ds.id = "ds-1"
        ds.tables = [table]

        session = AsyncSession()
        result = type("_R", (), {"scalar_one": lambda _self: ds})()
        session.execute = AsyncMock(return_value=result)
        try:
            return await ds.get_schemas(db=session)
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_nothing_is_lost_across_the_stored_row(self):
        tables = await self._schemas(RICH_COLUMNS)
        assert _meta_and_desc(tables[0].columns) == (13, 2)

    @pytest.mark.asyncio
    async def test_a_measure_keeps_its_role_and_return_type(self):
        cols = {c.name: c for c in (await self._schemas(RICH_COLUMNS))[0].columns}
        measure = cols["Total Parts Cost"]
        assert measure.dtype == "measure"
        assert measure.metadata["role"] == "measure"
        assert measure.metadata["returns"] == "Number"
        assert measure.description == "SUM(fact_service_event[parts_cost])"

    @pytest.mark.asyncio
    async def test_the_powerbi_formatting_keys_survive_by_name(self):
        cols = {c.name: c for c in (await self._schemas(RICH_COLUMNS))[0].columns}
        cost = cols["parts_cost"]
        assert cost.metadata["summarize_by"] == "sum"
        assert cost.metadata["format_string"] == "\\$#,0.00;(\\$#,0.00)"
        assert cost.description == "Cost of parts consumed"
        assert cols["svc_vehicle_key"].metadata["hidden"] is True

    @pytest.mark.asyncio
    async def test_a_bare_column_gains_no_empty_keys(self):
        """★Passes on both trees, deliberately — see the note on the sibling
        case above. Also pins that the untyped default is untouched: `dtype`
        falls back to "unknown", it does not become None."""
        col = (await self._schemas([{"name": "plain"}]))[0].columns[0]
        assert (col.name, col.dtype) == ("plain", "unknown")
        assert col.description is None
        assert col.metadata is None


class TestTheGuardsWouldHaveFailed:
    """Prove the guards fail on the bug, not merely pass on the fix.

    The pre-fix bodies are reproduced verbatim below. Running the real checkers
    over them shows what the fix undoes — and, unlike a shell-prompt red proof,
    this one runs on every future change.
    """

    PRE_FIX_SERVICE = '''
async def get_user_data_source_schema(self, db, data_source, user):
    for name, payload in normalized.items():
        columns = [TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("columns") or [])]
        pks = [TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("pks") or [])]

async def _merge_all_fabric_endpoints(self, db, data_source, user):
    normalized[display] = {
        "columns": [
            {"name": (c.name if hasattr(c, "name") else c.get("name")),
             "dtype": (c.dtype if hasattr(c, "dtype") else c.get("dtype"))}
            for c in (cols or [])
        ],
        "pks": [],
        "fks": [],
        "metadata_json": meta,
    }
    for name, payload in normalized.items():
        tables.append(Table(
            name=name,
            columns=[TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("columns") or [])],
            pks=[], fks=[], metadata_json=payload.get("metadata_json"),
        ))
'''

    @staticmethod
    def _old_normalize_columns(cols):
        """`powerbi_multitenant_scan._normalize_columns`, verbatim."""
        out = []
        for c in cols or []:
            name = c.name if hasattr(c, "name") else c.get("name")
            dtype = c.dtype if hasattr(c, "dtype") else c.get("dtype")
            out.append({"name": name, "dtype": dtype})
        return out

    def test_the_old_powerbi_helper_lost_all_of_it(self):
        old = (self._old_normalize_columns(RICH_COLUMNS)
               + self._old_normalize_columns(RICH_PKS))
        assert _meta_and_desc(old) == (0, 0), "fixture no longer exercises the bug"

    def test_the_old_get_schemas_rebuild_lost_all_of_it(self):
        from app.ai.prompt_formatters import TableColumn
        old = [TableColumn(name=c["name"], dtype=c.get("dtype", "unknown"))
               for c in RICH_COLUMNS]
        assert _meta_and_desc(old) == (0, 0), "fixture no longer exercises the bug"

    def test_the_checkers_reject_the_pre_fix_service_source(self):
        tree = ast.parse(self.PRE_FIX_SERVICE)
        user_fn = _fn(tree, "get_user_data_source_schema")
        fabric_fn = _fn(tree, "_merge_all_fabric_endpoints")
        assert _check_column_comprehensions_carry_both(user_fn), \
            "the comprehension checker cannot see the defect it exists for"
        assert _check_column_comprehensions_carry_both(fabric_fn)
        assert _check_persisted_columns_use_the_normalizer(fabric_fn), \
            "the persist checker cannot see the defect it exists for"

    def test_the_pks_control_passes_on_the_pre_fix_source_too(self):
        """★The pks case must NOT be a way for the other guards to go green.
        It was already correct before the fix and stays correct after."""
        tree = ast.parse(self.PRE_FIX_SERVICE)
        fn = _fn(tree, "get_user_data_source_schema")
        pk_comps = [n for n in ast.walk(fn)
                    if isinstance(n, ast.ListComp) and "pks" in ast.unparse(n.generators[0].iter)]
        assert len(pk_comps) == 1
        assert "metadata" not in ast.unparse(pk_comps[0])
