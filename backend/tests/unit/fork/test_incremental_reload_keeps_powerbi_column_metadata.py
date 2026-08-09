"""An incremental Power BI reload must not downgrade what a full index captured.

`refresh_data_source_schema` runs the interactive Reload with
introspection="incremental", which rebuilds every already-known dataset from the
stored catalog via `_tables_from_prior` instead of re-introspecting it. That
rebuild used to reduce each column to {name, dtype}, discarding the `description`
and `metadata` that `normalize_indexed_columns` deliberately persists — so one
click of Reload stripped `role=measure`/`returns` off every measure and `hidden`
off every join key, and the stripped form was written straight back to the
catalog.

That matters because the connector's own system prompt tells the agent to invoke
a measure BY NAME rather than re-deriving it with SUM: a hand-rolled equivalent
does not reproduce the measure's filter context and disagrees with the customer's
own Power BI reports. An untyped column carries no such signal.

Ported by hand from upstream 6a1f0b1d — this fork's powerbi_client.py has
diverged (per-user Power BI/Fabric auth), so only the `_cols` hunk was taken.

★Measured on this tree with the fork's own catalog fixture below:
   HEAD (pre-fix): 0 of 8 metadata entries + 0 of 2 descriptions survived.
   Fixed:          8 of 8 metadata entries + 2 of 2 descriptions survived.
See `test_the_prefix_tree_actually_lost_this_metadata`, which runs the OLD
`_cols` body from `git show HEAD:` against the same fixture, so the guard is
shown to fail on the bug rather than merely to pass on the fix.
"""
from unittest.mock import MagicMock

import pytest

from app.ai.prompt_formatters import TableColumn
from app.data_sources.clients.powerbi_client import PowerBIClient


def _mk_client() -> PowerBIClient:
    c = PowerBIClient(
        tenant_id="t", client_id="c", client_secret="s", access_token="tok",
    )
    c._http = MagicMock()
    return c


# A prior catalog entry shaped like a real indexed model: a fact table with a
# hidden surrogate join key, a plain column, and a DAX measure.
PRIOR = {
    "FleetOps/fact_service_event": {
        "columns": [
            {"name": "svc_vehicle_key", "dtype": "Integer",
             "metadata": {"role": "column", "hidden": True}},
            {"name": "parts_cost", "dtype": "Number",
             "metadata": {"role": "column"}},
            {"name": "Total Parts Cost", "dtype": "measure",
             "description": "SUM(fact_service_event[parts_cost])",
             "metadata": {"role": "measure", "returns": "Number",
                          "expression": "SUM(fact_service_event[parts_cost])"}},
        ],
        "pks": [
            {"name": "svc_event_key", "dtype": "Integer",
             "description": "Surrogate key",
             "metadata": {"role": "column", "hidden": True}},
        ],
        "fks": [{
            "column": {"name": "svc_vehicle_key", "dtype": "unknown"},
            "references_name": "FleetOps/dim_vehicle",
            "references_column": {"name": "vehicle_key", "dtype": "unknown"},
        }],
        "metadata_json": {"powerbi": {
            "datasetId": "ds1", "tableName": "fact_service_event",
        }},
    },
}

DS = {"id": "ds1", "name": "FleetOps"}


def _rebuild(prior=None):
    tables = _mk_client()._tables_from_prior(
        list((prior or PRIOR).items()), DS, "ws1", "Ops", [],
    )
    assert len(tables) == 1
    return tables[0]


def _survival(table):
    """(metadata entries, descriptions) surviving the rebuild."""
    cols = list(table.columns or []) + list(table.pks or [])
    meta = sum(len(c.metadata or {}) for c in cols)
    desc = sum(1 for c in cols if c.description)
    return meta, desc


class TestIncrementalReloadPreservesColumnMetadata:
    def test_measure_keeps_role_and_return_type(self):
        cols = {c.name: c for c in _rebuild().columns}
        measure = cols["Total Parts Cost"]
        assert measure.dtype == "measure"
        assert measure.metadata == {
            "role": "measure", "returns": "Number",
            "expression": "SUM(fact_service_event[parts_cost])",
        }
        assert measure.description == "SUM(fact_service_event[parts_cost])"

    def test_hidden_join_key_stays_flagged_hidden(self):
        cols = {c.name: c for c in _rebuild().columns}
        assert cols["svc_vehicle_key"].metadata == {"role": "column", "hidden": True}

    def test_pks_are_rebuilt_by_the_same_helper_and_keep_theirs(self):
        pk = _rebuild().pks[0]
        assert pk.metadata == {"role": "column", "hidden": True}
        assert pk.description == "Surrogate key"

    def test_nothing_is_lost_across_the_whole_entry(self):
        """The headline number: count what went in and what came out."""
        meta, desc = _survival(_rebuild())
        assert (meta, desc) == (8, 2)

    def test_relationships_still_survive_the_rebuild(self):
        """FKs were never the casualty here — pin that the fix did not disturb
        the one thing the reload already got right."""
        fks = _rebuild().fks
        assert len(fks) == 1
        assert fks[0].column.name == "svc_vehicle_key"
        assert fks[0].references_name == "FleetOps/dim_vehicle"

    def test_column_without_metadata_stays_clean(self):
        """A source supplying no metadata must not gain an empty dict — the
        renderer branches on presence, and {} would read as 'has metadata'."""
        prior = {"FleetOps/t": {
            "columns": [{"name": "plain", "dtype": "Text"}],
            "pks": [], "fks": [],
            "metadata_json": {"powerbi": {"datasetId": "ds1", "tableName": "t"}},
        }}
        col = _rebuild(prior).columns[0]
        assert col.metadata is None
        assert col.description is None

    def test_an_orm_style_column_does_not_abort_the_rebuild(self):
        """On a SQLAlchemy instance `.metadata` is the declarative MetaData
        registry, not a dict. Taken raw it fails TableColumn validation and
        kills the whole rebuild — every table for that dataset vanishes."""
        class _Ormish:
            name = "svc_vehicle_key"
            dtype = "Integer"
            description = None
            metadata = object()  # not a dict

        prior = {"FleetOps/t": {
            "columns": [_Ormish()], "pks": [], "fks": [],
            "metadata_json": {"powerbi": {"datasetId": "ds1", "tableName": "t"}},
        }}
        col = _rebuild(prior).columns[0]
        assert col.name == "svc_vehicle_key"
        assert col.metadata is None


class TestTheGuardWouldHaveFailed:
    """Prove the guard fails on the bug, not merely passes on the fix.

    The old `_cols` body is reproduced here verbatim from the pre-fix file
    (`git show HEAD:backend/app/data_sources/clients/powerbi_client.py`, the
    `_tables_from_prior` inner helper). Running it against the SAME fixture
    shows the loss the fix undoes.
    """

    @staticmethod
    def _old_cols(items):
        cols = []
        for c in items or []:
            name = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
            if not name:
                continue
            dtype = c.get("dtype") if isinstance(c, dict) else getattr(c, "dtype", None)
            cols.append(TableColumn(name=name, dtype=dtype or "unknown"))
        return cols

    def test_the_prefix_tree_actually_lost_this_metadata(self):
        entry = PRIOR["FleetOps/fact_service_event"]
        old = self._old_cols(entry["columns"]) + self._old_cols(entry["pks"])
        old_meta = sum(len(c.metadata or {}) for c in old)
        old_desc = sum(1 for c in old if c.description)
        assert (old_meta, old_desc) == (0, 0), "fixture no longer exercises the bug"

        new_meta, new_desc = _survival(_rebuild())
        assert (new_meta, new_desc) == (8, 2)

    def test_the_old_body_reduced_a_measure_to_an_untyped_column(self):
        measure = [c for c in self._old_cols(
            PRIOR["FleetOps/fact_service_event"]["columns"]
        ) if c.name == "Total Parts Cost"][0]
        assert measure.metadata is None
        assert measure.description is None
