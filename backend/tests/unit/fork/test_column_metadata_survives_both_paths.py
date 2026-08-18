"""Column `description`/`metadata` must survive whichever read path renders it.

A table reaches the agent through `DataSourceTable.to_prompt_table()`, which has
two branches: the NEW path delegates to `ConnectionTable.to_prompt_table()`, the
LEGACY path reads the JSON columns still stored on `DataSourceTable` itself. The
persist layer (`normalize_indexed_columns`) deliberately carries `description`
and `metadata` — metadata is what marks a column as a measure with its return
type, as hidden, or as a partition key — and the new path reads both back. The
legacy branch reduced every column to {name, dtype} and threw both away, so the
SAME stored row rendered differently depending only on whether it had been
migrated.

★This is the dangerous kind of defect: it loses nothing TODAY. Measured on the
live instance 2026-08-17, 69 of 106 tables are on the legacy path
(`connection_table_id IS NULL`) and they are Microsoft Fabric (63) and Power BI
(6) — the two connectors we most want to enrich next — and all of them carry
zero descriptions and zero metadata. The loss appears silently the moment a
client starts emitting either, with no test failing and nothing logged. So the
load-bearing assertion below is not "the legacy path keeps metadata" but
`test_both_paths_render_the_same_stored_columns_identically`: the two paths
handed the SAME input must produce EQUAL TableColumn objects, which is the only
form of the guard that stays true as either path changes.

★RED-PROOF: every test in `TestLegacyPathKeepsColumnMetadata` and
`TestBothPathsAgree` was run against the pre-fix tree first.
`test_a_measure_keeps_its_role_and_return_type`,
`test_a_hidden_join_key_stays_flagged_hidden`,
`test_a_column_description_survives` and
`test_both_paths_render_the_same_stored_columns_identically` FAIL there (4 of
9); the neither-key and pks cases pass on both, and are here to pin that the fix
did not buy its win by inventing empty dicts or by diverging the pks.
"""
from app.ai.prompt_formatters import TableColumn
from app.models.connection_table import ConnectionTable
from app.models.datasource_table import DataSourceTable


# One stored table shaped like a real indexed model: a hidden surrogate join
# key, a plain column, and a measure carrying its DAX expression.
STORED_COLUMNS = [
    {"name": "svc_vehicle_key", "dtype": "Integer",
     "metadata": {"role": "column", "hidden": True}},
    {"name": "parts_cost", "dtype": "Number",
     "description": "Cost of parts consumed",
     "metadata": {"role": "column"}},
    {"name": "Total Parts Cost", "dtype": "measure",
     "description": "SUM(fact_service_event[parts_cost])",
     "metadata": {"role": "measure", "returns": "Number",
                  "expression": "SUM(fact_service_event[parts_cost])"}},
]

STORED_PKS = [
    {"name": "svc_event_key", "dtype": "Integer",
     "description": "Surrogate key",
     "metadata": {"role": "column", "hidden": True}},
]

STORED_FKS = [{
    "column": {"name": "svc_vehicle_key", "dtype": "Integer"},
    "references_name": "dim_vehicle",
    "references_column": {"name": "vehicle_key", "dtype": "Integer"},
}]


def _legacy(columns=None, pks=None, fks=None) -> DataSourceTable:
    """A row that predates the ConnectionTable migration: no connection_table."""
    return DataSourceTable(
        name="fact_service_event",
        columns=STORED_COLUMNS if columns is None else columns,
        pks=STORED_PKS if pks is None else pks,
        fks=STORED_FKS if fks is None else fks,
    )


def _new(columns=None, pks=None, fks=None) -> DataSourceTable:
    """The same stored content, reached through a ConnectionTable."""
    dst = DataSourceTable(name="fact_service_event")
    dst.connection_table = ConnectionTable(
        name="fact_service_event",
        columns=STORED_COLUMNS if columns is None else columns,
        pks=STORED_PKS if pks is None else pks,
        fks=STORED_FKS if fks is None else fks,
        no_rows=0,
    )
    return dst


class TestLegacyPathKeepsColumnMetadata:
    def test_the_fixture_actually_takes_the_legacy_branch(self):
        """★Positive control. If `connection_table` were populated these tests
        would pass by exercising the path that was never broken."""
        assert _legacy().connection_table is None

    def test_a_measure_keeps_its_role_and_return_type(self):
        cols = {c.name: c for c in _legacy().to_prompt_table().columns}
        measure = cols["Total Parts Cost"]
        assert measure.dtype == "measure"
        assert measure.metadata == {
            "role": "measure", "returns": "Number",
            "expression": "SUM(fact_service_event[parts_cost])",
        }
        assert measure.description == "SUM(fact_service_event[parts_cost])"

    def test_a_hidden_join_key_stays_flagged_hidden(self):
        cols = {c.name: c for c in _legacy().to_prompt_table().columns}
        assert cols["svc_vehicle_key"].metadata == {"role": "column", "hidden": True}

    def test_a_column_description_survives(self):
        cols = {c.name: c for c in _legacy().to_prompt_table().columns}
        assert cols["parts_cost"].description == "Cost of parts consumed"

    def test_a_column_with_neither_key_still_renders(self):
        """`normalize_indexed_columns` OMITS both keys rather than storing None,
        so the common stored shape is exactly {name, dtype}. It must not raise,
        and it must not gain an empty dict — the renderer branches on presence,
        and {} reads as 'has metadata'."""
        table = _legacy(columns=[{"name": "plain", "dtype": "Text"}]).to_prompt_table()
        col = table.columns[0]
        assert (col.name, col.dtype) == ("plain", "Text")
        assert col.description is None
        assert col.metadata is None

    def test_relationships_are_undisturbed(self):
        """FKs were never the casualty — pin that the fix did not move them."""
        fks = _legacy().to_prompt_table().fks
        assert len(fks) == 1
        assert fks[0].column.name == "svc_vehicle_key"
        assert fks[0].references_name == "dim_vehicle"
        assert fks[0].references_column.name == "vehicle_key"


class TestBothPathsAgree:
    """★The assertion that actually pins the bug.

    Neither branch is the specification; their DISAGREEMENT was. These compare
    the two renderings of identical stored input, so they fail if either path
    starts carrying something the other does not.
    """

    def test_both_paths_render_the_same_stored_columns_identically(self):
        assert _new().to_prompt_table().columns == _legacy().to_prompt_table().columns

    def test_both_paths_render_pks_identically(self):
        """★Deliberately equal-and-lossy: NEITHER path carries description or
        metadata on pks. Matching the new path is the point — 'fixing' the
        legacy pks alone would recreate the divergence this file exists to
        close, just pointing the other way. A future change that enriches pks
        must change both sides, and this test is what forces that."""
        legacy_pks = _legacy().to_prompt_table().pks
        new_pks = _new().to_prompt_table().pks
        assert legacy_pks == new_pks
        assert legacy_pks == [TableColumn(name="svc_event_key", dtype="Integer")]
        assert legacy_pks[0].metadata is None
        assert legacy_pks[0].description is None

    def test_both_paths_agree_on_a_column_carrying_neither_key(self):
        bare = [{"name": "plain", "dtype": "Text"}]
        assert (_new(columns=bare).to_prompt_table().columns
                == _legacy(columns=bare).to_prompt_table().columns)

    def test_both_paths_render_fks_identically(self):
        assert _new().to_prompt_table().fks == _legacy().to_prompt_table().fks
