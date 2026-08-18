"""A Power BI column must carry HOW it is meant to be used, not just its type.

Upstream 0.0.542 taught the schema section to render a flat set of column
properties — `_COLUMN_META_KEYS` in
`app/ai/context/sections/tables_schema_section.py` — for EVERY connector.
Power BI models are Tabular models, so those properties exist at the source;
they were simply never read.

`summarize_by` is the one that changes answers. Without it the agent guesses
whether a numeric column should be summed or averaged, and a ratio, a rate or
a closing balance summed is wrong in a way that looks like a working report.

The rule these guards exist to hold: **a key appears only when the model
answered for it.** A fabricated `summarize_by` is worse than a missing one,
because the agent trusts it. So every test below pairs "the value arrives" with
"the key is absent when the source is silent" — a mapping that defaults would
pass the first half and fail the second.

The wider `INFO.VIEW.COLUMNS` projection is deployment-dependent, exactly like
the DAX INFO functions it extends. `TestTheRichProjectionIsOptional` pins the
fallback: an endpoint that rejects it must lose the semantics and NOTHING else
— not the data types, not the measures, not the relationships.
"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.data_sources.clients.powerbi_client import PowerBIClient


WS = {"id": "ws1", "name": "Ops"}
DS = {"id": "ds1", "name": "FleetOps"}


def _rich_row(name, **over):
    """One INFO.VIEW.COLUMNS row in the widened projection's shape."""
    row = {
        "Kind": "C", "Tbl": "fact_service", "Name": name,
        "Info1": "Double", "Info2": None, "Flag": False,
        "Fmt": None, "Summarize": None, "SortBy": None, "Folder": None,
    }
    row.update(over)
    return row


def _narrow_row(name, **over):
    """One row in the pre-existing projection — no semantic aliases at all."""
    row = {
        "Kind": "C", "Tbl": "fact_service", "Name": name,
        "Info1": "Double", "Info2": None, "Flag": False,
    }
    row.update(over)
    return row


def _client(rows, *, rich_rows=None, rich_error=None, admin_scan=None):
    """A client whose whole tenant is one workspace holding one dataset.

    `rows` answers the narrow projection; `rich_rows` (defaulting to `rows`)
    answers the widened one, and `rich_error` makes the widened one fail so the
    fallback can be exercised.
    """
    c = PowerBIClient(tenant_id="t", client_id="c", client_secret="s", access_token="tok")
    c._http = MagicMock()
    c.connect = lambda: None
    c.refresh_user_permissions = lambda: None
    c.list_workspaces = lambda: [WS]
    c.list_datasets = lambda ws_id: [DS]
    c.list_reports = lambda ws_id: []
    c._batch_admin_scan = lambda ws_ids: (admin_scan or {})
    c.executed = []

    def _dax(workspace_id, dataset_id, dax, max_rows=None):
        c.executed.append(dax)
        if "SummarizeBy" in dax:
            if rich_error is not None:
                raise rich_error
            return pd.DataFrame(rich_rows if rich_rows is not None else rows)
        return pd.DataFrame(rows)

    c._execute_dax_internal = _dax
    return c


def _columns(client):
    tables = client.get_schemas()
    assert [t.name for t in tables] == ["FleetOps/fact_service"]
    return {col.name: col for col in tables[0].columns}


class TestSemanticsReachTheColumn:
    """A source that answers for these properties produces them on the column."""

    def test_every_mapped_key_arrives_under_its_contract_name(self):
        cols = _columns(_client([
            _rich_row(
                "parts_cost",
                Info2="Currency", Fmt="#,0.00", Summarize="Sum",
                SortBy="cost_rank", Folder="Costs",
            ),
        ]))
        assert cols["parts_cost"].metadata == {
            "role": "column",
            "data_category": "Currency",
            "format_string": "#,0.00",
            "summarize_by": "sum",
            "sort_by_column": "cost_rank",
            "display_folder": "Costs",
        }

    def test_summarize_by_none_is_a_value_not_an_absence(self):
        """`None` is the model saying "never aggregate this" — a margin
        percentage, an index, an account number. Filtering it out with the
        blanks would delete the single most useful thing it says."""
        cols = _columns(_client([_rich_row("margin_pct", Summarize="None")]))
        assert cols["margin_pct"].metadata["summarize_by"] == "none"

    def test_summarize_by_is_lowercased_for_the_agent_contract(self):
        cols = _columns(_client([_rich_row("trips", Summarize="Average")]))
        assert cols["trips"].metadata["summarize_by"] == "average"

    def test_a_format_string_keeps_the_authors_own_casing(self):
        """Only `summarize_by` is an enum. A format string is a literal — case
        is meaning ("0.0%" vs "General"), so normalizing it would corrupt it."""
        cols = _columns(_client([_rich_row("share", Fmt="0.0%;-0.0%;General")]))
        assert cols["share"].metadata["format_string"] == "0.0%;-0.0%;General"

    def test_semantics_sit_beside_the_flags_that_were_already_there(self):
        cols = _columns(_client([
            _rich_row("vehicle_key", Info1="Int64", Flag=True, Folder="Keys"),
        ]))
        assert cols["vehicle_key"].metadata == {
            "role": "column", "hidden": True, "display_folder": "Keys",
        }
        assert cols["vehicle_key"].dtype == "Int64"


class TestSilenceIsNeverInvented:
    def test_a_property_the_model_did_not_set_is_an_absent_key(self):
        """Not None, not "" — absent. The persist layer stores whatever dict it
        is handed, and an empty attribute renders in the prompt as a property
        the model claims to have set."""
        cols = _columns(_client([_rich_row("plain", Summarize="Sum")]))
        meta = cols["plain"].metadata
        assert meta == {"role": "column", "summarize_by": "sum"}
        for key in ("format_string", "sort_by_column", "display_folder", "data_category"):
            assert key not in meta

    @pytest.mark.parametrize("blank", [None, "", "   ", float("nan")])
    def test_blank_cells_do_not_become_attributes(self, blank):
        """executeQueries answers with includeNulls, and an all-null column
        arrives through pandas as float nan — three shapes of "unset"."""
        cols = _columns(_client([
            _rich_row("plain", Fmt=blank, Summarize=blank, SortBy=blank, Folder=blank),
        ]))
        assert cols["plain"].metadata == {"role": "column"}

    def test_a_model_with_no_semantics_at_all_indexes_cleanly(self):
        cols = _columns(_client([_narrow_row("plain")]))
        assert cols["plain"].metadata == {"role": "column"}
        assert cols["plain"].dtype == "Double"


class TestHiddenStaysABool:
    """`hidden` is the one non-string in the contract, and executeQueries
    serializes booleans inconsistently across models — the string "True" stored
    raw would render as a value rather than a flag."""

    @pytest.mark.parametrize("flag", [True, "True", "true", 1])
    def test_truthy_serializations_all_become_true(self, flag):
        cols = _columns(_client([_rich_row("k", Flag=flag)]))
        assert cols["k"].metadata["hidden"] is True

    @pytest.mark.parametrize("flag", [False, "False", "false", 0, ""])
    def test_falsy_serializations_omit_the_key_entirely(self, flag):
        cols = _columns(_client([_rich_row("k", Flag=flag)]))
        assert "hidden" not in cols["k"].metadata


class TestTheRichProjectionIsOptional:
    """An endpoint that refuses the widened query must lose the semantics and
    nothing else. This is the load-bearing half of the change: the narrow query
    carries the data types, the measures and the relationships, and trading
    those for `summarize_by` would be a bad bargain on every model."""

    def test_a_rejected_projection_falls_back_and_keeps_types_and_measures(self):
        c = _client(
            [
                _narrow_row("parts_cost"),
                {"Kind": "M", "Tbl": "fact_service", "Name": "Total Cost",
                 "Info1": "Double", "Info2": "", "Flag": False},
            ],
            rich_error=RuntimeError("DAX query failed: HTTP 400 unknown column [SummarizeBy]"),
        )
        cols = _columns(c)
        assert cols["parts_cost"].dtype == "Double"
        assert cols["parts_cost"].metadata == {"role": "column"}
        assert cols["Total Cost"].metadata == {"role": "measure", "expression": "",
                                               "returns": "Double"}
        assert any("SummarizeBy" in q for q in c.executed), "the wide query was never tried"
        assert any("SummarizeBy" not in q for q in c.executed), "no fallback was attempted"

    def test_a_rejection_does_not_disable_dax_info_functions(self):
        """★These are two different capabilities. Pinning
        `_info_functions_supported` here would cost every later dataset in the
        crawl its types, measures AND relationships over an enrichment."""
        c = _client([_narrow_row("plain")], rich_error=RuntimeError("HTTP 400 bad request"))
        _columns(c)
        assert c._column_semantics_supported is False
        assert c._info_functions_supported is True

    def test_a_per_dataset_refusal_does_not_pin_the_flag_for_the_tenant(self):
        """★A 401/403/404 is about THIS model — RLS, no Build permission — not
        about the projection. One unreadable model must not strip the semantics
        off every model discovered after it."""
        c = _client([], rich_error=RuntimeError("DAX query failed: HTTP 401 unauthorized"))
        c._get_tables_via_column_stats_with_reason = lambda ws, ds: ([], [], "no access")
        c._http.get.return_value = MagicMock(status_code=401, json=lambda: {})
        c.get_schemas()
        assert c._column_semantics_supported is None

    def test_the_wide_query_is_tried_once_then_dropped_for_the_crawl(self):
        c = _client([_narrow_row("plain")], rich_error=RuntimeError("HTTP 400 bad request"))
        c.list_datasets = lambda ws_id: [DS, {"id": "ds2", "name": "Fleet2"}]
        c.get_schemas()
        assert sum("SummarizeBy" in q for q in c.executed) == 1
        assert sum("SummarizeBy" not in q for q in c.executed) == 2


class TestTheAdminScanPathCopiesWhatArrives:
    """The scanner speaks the same camelCase names as the discovery shape, so a
    semantic property is carried through when the payload has one. Whether a
    given tenant's scan emits them is not asserted here — only that a payload
    holding one is not thrown away, and one without does not gain a key."""

    @staticmethod
    def _scan(col):
        return {"tables": [{"name": "fact_service", "columns": [col], "measures": []}]}

    def test_a_scanned_column_carrying_semantics_keeps_them(self):
        tables, _ = _client([])._parse_admin_scan_tables(self._scan({
            "name": "parts_cost", "dataType": "Double",
            "summarizeBy": "Sum", "formatString": "#,0.00", "displayFolder": "Costs",
        }))
        assert tables[0]["columns"][0]["summarizeBy"] == "Sum"
        assert tables[0]["columns"][0]["formatString"] == "#,0.00"
        assert tables[0]["columns"][0]["displayFolder"] == "Costs"

    def test_a_scan_without_them_produces_the_shape_it_always_did(self):
        tables, _ = _client([])._parse_admin_scan_tables(self._scan({
            "name": "parts_cost", "dataType": "Double", "isHidden": True,
        }))
        assert tables[0]["columns"][0] == {
            "name": "parts_cost", "dataType": "Double", "isHidden": True,
        }


class TestTheGuardWouldHaveFailed:
    """Prove these assert the change, not merely today's behaviour: the old
    mapping built `col_meta` from `isHidden`/`isRelationshipKey` alone, so every
    semantic key was dropped no matter what the model answered."""

    @staticmethod
    def _old_col_meta(col):
        meta = {"role": "column"}
        if col.get("isHidden"):
            meta["hidden"] = True
        if col.get("isRelationshipKey"):
            meta["relationship_key"] = True
        return meta

    def test_the_prefix_mapping_dropped_every_semantic_key(self):
        col = {
            "name": "parts_cost", "dataType": "Double", "isHidden": False,
            "summarizeBy": "Sum", "formatString": "#,0.00",
            "sortByColumn": "cost_rank", "dataCategory": "Currency",
            "displayFolder": "Costs",
        }
        assert self._old_col_meta(col) == {"role": "column"}
        assert PowerBIClient._column_semantics(col) == {
            "summarize_by": "sum", "format_string": "#,0.00",
            "sort_by_column": "cost_rank", "data_category": "Currency",
            "display_folder": "Costs",
        }
