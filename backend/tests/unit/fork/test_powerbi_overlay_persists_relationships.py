"""A Power BI model that HAS relationships must still be storable.

`_normalize_tables` prepares every scanned Power BI table for
`_upsert_user_overlay`, which writes the result straight into JSON columns on
`datasource_tables`. `columns` and `pks` go through `normalize_indexed_columns`
— the one persist shape — but `fks` were passed through raw. The Power BI
client builds them as pydantic `ForeignKey` objects
(`powerbi_client.py`: `fks.append(fk if isinstance(fk, ForeignKey) else
ForeignKey(**fk))`), and a pydantic object cannot be serialized into a JSON
column:

    (builtins.TypeError) Object of type ForeignKey is not JSON serializable
    [SQL: INSERT INTO datasource_tables (name, ..., pks, fks, ...)]

The INSERT raises, the session is poisoned, and every later statement in the
same request fails with "This Session's transaction has been rolled back" —
including `fabric_user` federated sync, which shares the session and has
nothing to do with Power BI.

★The trigger is the DATA, not the code path: a model with no relationships
yields `fks == []`, which serializes fine. That is why this survived — the
local instance had not re-introspected Power BI since 2026-07-30 and carried
`fks = []` on all six stored tables, while dev introspected live, got
`6 table(s), 9 relationship(s)`, and could not store a single row.

★RED-PROOF: run against the pre-fix tree,
`test_a_model_with_relationships_is_json_serializable`,
`test_the_stored_relationship_keeps_its_column_and_target` and
`test_both_persist_paths_normalize_fks_the_same_way` FAIL with the TypeError
above. The dict-fks and empty-fks cases pass on both trees and are here as
positive controls — they pin that the fix was not bought by dropping fks on
the floor.
"""
import json

import pytest

from app.ai.prompt_formatters import ForeignKey, TableColumn
from app.services.powerbi_multitenant_scan import _normalize_tables


TENANT_ID = "9f8c1f2e-0000-4444-8888-cityholdings"
TENANT_NAME = "City Holdings Limited"


def _fk_objects():
    """Exactly what `PowerBIClient.get_schemas` hands the scanner for a model
    that declares a relationship — pydantic, not dicts."""
    return [ForeignKey(
        column=TableColumn(name="PromotionID", dtype="Int64"),
        references_name="Promotion_test/Promotion",
        references_column=TableColumn(name="ID", dtype="Int64"),
    )]


def _fk_dicts():
    return [{
        "column": {"name": "PromotionID", "dtype": "Int64"},
        "references_name": "Promotion_test/Promotion",
        "references_column": {"name": "ID", "dtype": "Int64"},
    }]


def _table(fks):
    return [{
        "name": "Promotion_test/Promo_Ben",
        "columns": [{"name": "PromotionID", "dtype": "Int64"},
                    {"name": "NORMAL_SALE_PRICE", "dtype": "Decimal"}],
        "pks": [{"name": "PromotionID", "dtype": "Int64"}],
        "fks": fks,
        "metadata_json": {"powerbi": {"datasetId": "abc", "tableName": "Promo_Ben"}},
    }]


def _normalized(fks):
    return _normalize_tables(_table(fks), TENANT_ID, TENANT_NAME)["Promotion_test/Promo_Ben"]


class TestRelationshipsCanBeStored:
    def test_a_model_with_no_relationships_is_json_serializable(self):
        """★Positive control. This is the shape local carried since 30 July, and
        it is why local never failed. It must keep passing."""
        json.dumps(_normalized([]))

    def test_a_model_with_dict_relationships_is_json_serializable(self):
        """★Positive control. A client that already emits dicts was never
        broken — pin that so a fix cannot claim a win by rejecting fks."""
        json.dumps(_normalized(_fk_dicts()))

    def test_a_model_with_relationships_is_json_serializable(self):
        """★The bug. Pydantic `ForeignKey` objects reach the JSON column."""
        json.dumps(_normalized(_fk_objects()))

    def test_the_stored_relationship_keeps_its_column_and_target(self):
        """Serializable is not enough — a relationship that survives as an
        empty dict is worse than a crash, because the agent then silently
        believes the tables cannot be joined."""
        fks = _normalized(_fk_objects())["fks"]
        assert len(fks) == 1
        fk = fks[0]
        assert fk["column"]["name"] == "PromotionID"
        assert fk["references_name"] == "Promotion_test/Promotion"
        assert fk["references_column"]["name"] == "ID"

    def test_pydantic_and_dict_relationships_agree_on_the_relationship(self):
        """★They do NOT store byte-identically, and that is pre-existing, not
        something this fix introduced: a dict is passed through untouched while
        a pydantic object dumps its full model, so the latter also carries
        `is_active`/`description`/`metadata: None` on each side of the join.
        Every SQL connector has stored the dumped shape through
        `refresh_schema` since long before Power BI did.

        So this pins the part that must agree — which column joins to which
        column on which table — and deliberately does not assert equality of
        the envelopes. Asserting that would either fail forever or push someone
        into rewriting the stored shape for every connector to satisfy a test.
        """
        def rel(fks):
            return [(fk["column"]["name"], fk["references_name"],
                     fk["references_column"]["name"]) for fk in fks]

        assert rel(_normalized(_fk_objects())["fks"]) == rel(_normalized(_fk_dicts())["fks"])

    def test_a_dumped_relationship_carries_no_surprises(self):
        """Pin the dumped envelope so a future pydantic change that adds a
        field to `TableColumn` shows up here, rather than silently widening
        every stored row."""
        fk = _normalized(_fk_objects())["fks"][0]
        assert set(fk) == {"column", "references_name", "references_column"}
        assert set(fk["column"]) == {"name", "dtype", "is_active", "description", "metadata"}

    def test_columns_and_pks_are_undisturbed(self):
        """Pin that fixing fks did not move the two fields that already
        worked."""
        out = _normalized(_fk_objects())
        assert out["columns"] == [{"name": "PromotionID", "dtype": "Int64"},
                                  {"name": "NORMAL_SALE_PRICE", "dtype": "Decimal"}]
        assert out["pks"] == [{"name": "PromotionID", "dtype": "Int64"}]

    def test_the_tenant_stamp_survives(self):
        meta = _normalized(_fk_objects())["metadata_json"]
        assert meta["tenant_id"] == TENANT_ID
        assert meta["tenant_name"] == TENANT_NAME
        assert meta["powerbi"]["datasetId"] == "abc"


class TestOnePersistShape:
    """★The assertion that keeps this closed.

    `connection_service.refresh_schema` already had a `normalize_fks` — as a
    local closure, invisible to this module, which was written from the same
    template and simply omitted it. Testing that Power BI now serializes would
    not stop the third copy from omitting it again. Testing that both paths
    call the SAME helper does.
    """

    def test_the_shared_helper_exists(self):
        from app.schemas.datasource_table_schema import normalize_fks  # noqa: F401

    def test_both_persist_paths_normalize_fks_the_same_way(self):
        from app.schemas.datasource_table_schema import normalize_fks
        assert _normalized(_fk_objects())["fks"] == normalize_fks(_fk_objects())

    def test_refresh_schema_uses_the_shared_helper_not_a_local_copy(self):
        """`refresh_schema` is a 400-line method; importing it is not practical
        here, so pin the source instead — a re-introduced local `def
        normalize_fks` inside it is exactly the regression this file exists to
        catch."""
        import inspect
        from app.services.connection_service import ConnectionService
        src = inspect.getsource(ConnectionService.refresh_schema)
        assert "def normalize_fks" not in src, (
            "refresh_schema defines its own normalize_fks again — use the "
            "shared helper in app.schemas.datasource_table_schema"
        )
        assert "normalize_fks" in src, "refresh_schema no longer normalizes fks at all"
