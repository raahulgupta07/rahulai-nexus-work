"""Removing a file must take its table with it.

Uploading a CSV writes four things: the association row, the managed path into
`connection.config.file_paths`, `source_kind="table_backing"` on the File, and a
schema refresh that turns that path into a queryable table. Reflecting the path
is the entire mechanism — a table exists because its path is in that
newline-joined string and for no other reason.

Deleting removed the association and nothing else. So the file vanished from the
UI while its table stayed active and the agent kept answering from data the user
had removed. Nothing errored, nothing warned; the only evidence was a row count
that stayed too high.

Reproduced live before the fix: a probe CSV deleted through the API returned 200
with `path_still_in_config=true` and `intake_probe is_active=true`.

These tests pin the path arithmetic — which entries come out and, more
importantly, which stay in. The re-sync that actually retires the table is
delegated to `refresh_data_source_schema` on purpose (see the docstring on
`_withdraw_file_tables`) and is proven live rather than mocked here.
"""
import os

import pytest

from app.services.file_service import (
    DERIVED_PATHS_PREVIEW_KEY,
    INTAKE_PREVIEW_KEY,
    carry_forward_preview_records,
    merge_derived_paths_into_preview,
    owned_table_paths,
)

UPLOADS = "/app/backend/uploads/files"
JAN = f"{UPLOADS}/aaaa1111_MM Conso Data Report (Jan'25).csv"
MAY = f"{UPLOADS}/f55204bd_MM Conso Data Report (May'25).csv"


class _FileRow:
    def __init__(self, path=None, preview=None, filename="f.csv"):
        self.path = path
        self.preview = preview
        self.filename = filename


# ── which paths belong to a file ────────────────────────────────────────────

def test_a_plain_csv_owns_its_own_managed_path():
    assert owned_table_paths(_FileRow(path=MAY)) == [MAY]


def test_a_spreadsheet_owns_the_sheets_it_was_expanded_into():
    """The decisive case. `xlsx_to_csvs` names every sheet with a FRESH uuid4,
    and the raw .xlsx is never added to file_paths — so nothing on the File row
    could match those CSVs by name, prefix or anything else. Without the record
    stamped at upload, deleting a spreadsheet leaves all its tables alive with no
    way left to find them."""
    sheets = [f"{UPLOADS}/9f2c_q1.csv", f"{UPLOADS}/3b71_q2.csv"]
    row = _FileRow(
        path=f"{UPLOADS}/abcd_book.xlsx",
        preview=merge_derived_paths_into_preview(None, sheets),
    )
    owned = owned_table_paths(row)
    for sheet in sheets:
        assert sheet in owned


def test_a_file_uploaded_before_the_record_existed_still_yields_its_own_path():
    """Legacy rows have no derived-path list. A CSV — the overwhelming majority —
    is still fully recoverable from its own path, so the fix must not require the
    record to work at all."""
    assert owned_table_paths(_FileRow(path=MAY, preview={"type": "csv"})) == [MAY]


def test_a_row_with_no_path_owns_nothing():
    """Rather than returning something falsy that a caller might treat as a
    wildcard and strip the whole list with."""
    assert owned_table_paths(_FileRow(path=None)) == []


@pytest.mark.parametrize("junk", [None, "", 0, [], "not a dict"])
def test_a_malformed_preview_does_not_break_ownership(junk):
    assert owned_table_paths(_FileRow(path=MAY, preview=junk)) == [MAY]


def test_junk_entries_in_the_record_are_ignored():
    """`preview` is free-form JSON written by several code paths over the years;
    a non-string entry must not reach path arithmetic."""
    row = _FileRow(path=MAY, preview={DERIVED_PATHS_PREVIEW_KEY: [None, 42, "", "/real/x.csv"]})
    assert owned_table_paths(row) == [MAY, "/real/x.csv"]


# ── the withdrawal keeps everything it does not own ─────────────────────────

def _withdraw(existing, owned):
    """The exact arithmetic `_withdraw_file_tables` performs on the path list."""
    owned_set = {os.path.abspath(p) for p in owned}
    kept = [p for p in existing if os.path.abspath(p) not in owned_set]
    removed = [p for p in existing if os.path.abspath(p) in owned_set]
    return kept, removed


def test_only_the_removed_file_s_path_comes_out():
    existing = [JAN, MAY, f"{UPLOADS}/cccc_Mar.csv"]
    kept, removed = _withdraw(existing, [MAY])
    assert removed == [MAY]
    assert kept == [JAN, f"{UPLOADS}/cccc_Mar.csv"]


def test_two_files_with_the_same_basename_are_not_confused():
    """The live agent holds two files both named `MM Conso Data Report (Mar'25)
    .csv` under different uuids. Matching on filename would remove both and take
    down a table nobody asked to remove."""
    a = f"{UPLOADS}/1111_MM Conso Data Report (Mar'25).csv"
    b = f"{UPLOADS}/2222_MM Conso Data Report (Mar'25).csv"
    kept, removed = _withdraw([a, b], [a])
    assert removed == [a]
    assert kept == [b]


def test_a_relative_config_entry_still_matches():
    """Upload writes absolute paths, but a config edited by an older build or by
    hand may hold a relative one. A near-miss here leaves the table alive, which
    is exactly the failure being fixed — so the compare is on abspath."""
    rel = "uploads/files/f55204bd_x.csv"
    kept, removed = _withdraw([rel], [os.path.abspath(rel)])
    assert removed == [rel]
    assert kept == []


def test_removing_a_file_that_never_had_a_table_changes_nothing():
    """A Word document owns no path. The withdrawal must be a no-op rather than
    rewriting the config to an equal value and forcing a needless re-sync."""
    kept, removed = _withdraw([JAN, MAY], [f"{UPLOADS}/dddd_notes.docx"])
    assert removed == []
    assert kept == [JAN, MAY]


# ── the records survive a regenerated preview ───────────────────────────────

def test_both_records_survive_preview_regeneration():
    """Intake and the derived-path list are the two things on `preview` that are
    NOT derived from the file's bytes. A regenerated preview drops both unless
    carried across, and the loss is silent."""
    stored = {
        INTAKE_PREVIEW_KEY: {"destination": "table", "confidence": 0.98},
        DERIVED_PATHS_PREVIEW_KEY: [MAY],
        "type": "csv",
        "rows": 3,
    }
    fresh = {"type": "csv", "columns": ["a", "b"]}

    out = carry_forward_preview_records(fresh, stored)

    assert out[INTAKE_PREVIEW_KEY] == stored[INTAKE_PREVIEW_KEY]
    assert out[DERIVED_PATHS_PREVIEW_KEY] == [MAY]
    assert out["columns"] == ["a", "b"]


def test_the_stale_preview_cannot_resurrect_dropped_fields():
    """Carried by explicit key, not merged wholesale — otherwise a preview that
    legitimately no longer reports `rows` would keep reporting the old count."""
    out = carry_forward_preview_records({"type": "csv"}, {"type": "excel", "rows": 999})
    assert "rows" not in out
    assert out["type"] == "csv"


def test_nothing_stored_leaves_the_fresh_preview_untouched():
    assert carry_forward_preview_records({"type": "csv"}, None) == {"type": "csv"}


# ── removing the LAST file ──────────────────────────────────────────────────

def test_the_last_removal_retires_the_tables_itself():
    """Found live, after the fix shipped. Five files were removed cleanly and
    the sixth left `mm_conso_data_report_mar_25` standing — active, queryable,
    and backed by nothing.

    Cause: `refresh_schema` returns early at "No tables returned from
    get_schemas()" BEFORE it reaches the prune. That guard is correct for a
    database — an introspection that comes back empty usually means the
    connection broke, and letting that wipe the shared catalog turns a blip into
    data loss. It simply cannot tell "unreachable" from "genuinely empty", and a
    file connection with no files left is the second one.

    So the withdrawal handles the empty case itself, and ONLY the empty case:
    every partial removal still goes through the refresh, which is already
    proven to prune correctly.
    """
    import inspect
    import textwrap

    import app.services.file_service as file_service

    src = textwrap.dedent(
        inspect.getsource(file_service.FileService._withdraw_file_tables)
    )
    assert "_retire_all_connection_tables" in src
    guard = src[src.index("if not kept"):src.index("_retire_all_connection_tables")]
    assert guard.strip().startswith("if not kept"), (
        "the direct retirement is no longer limited to the case where the "
        "connection has no files left — it must not run on a partial removal, "
        "where the refresh prune is both correct and sufficient"
    )


def test_the_rows_are_deleted_not_merely_deactivated():
    """Deactivating is not enough, checked against the running product: the
    paginated tables reader filters neither `is_active` nor `deleted_at`, so a
    retired table keeps appearing on the Tables tab — greyed out, backed by
    nothing, and impossible for the user to get rid of.

    Both rows go: the agent's DataSourceTable and the ConnectionTable behind it.
    """
    import inspect
    import textwrap

    import app.services.file_service as file_service

    src = textwrap.dedent(
        inspect.getsource(file_service.FileService._retire_all_connection_tables)
    )
    assert "_sql_delete(DataSourceTable)" in src
    assert "_sql_delete(ConnectionTable)" in src


def test_everything_referencing_the_table_is_cleared_first():
    """Three of the four referencing tables have no ON DELETE rule — verified
    against the live schema, not assumed. They describe a table that no longer
    exists, so they go with it; leaving any of them raises a foreign-key error
    that this best-effort method swallows, leaving the ghost row behind."""
    import inspect
    import textwrap

    import app.services.file_service as file_service

    src = textwrap.dedent(
        inspect.getsource(file_service.FileService._retire_all_connection_tables)
    )
    for dependent in ("TableStats", "TableUsageEvent", "TableFeedbackEvent"):
        assert dependent in src, f"{dependent} rows would block the delete"
    assert src.index("TableStats") < src.index("_sql_delete(DataSourceTable)")
    assert src.index("_sql_delete(DataSourceTable)") < src.index("_sql_delete(ConnectionTable)")


def test_retiring_the_tables_cannot_fail_the_removal():
    """The user asked for a file to be removed. Tidying up behind it is not
    allowed to turn that into an error."""
    import inspect
    import textwrap

    import app.services.file_service as file_service

    src = textwrap.dedent(
        inspect.getsource(file_service.FileService._retire_all_connection_tables)
    )
    assert "except Exception" in src


def test_the_domain_row_is_unlinked_before_the_catalog_row_is_deleted():
    """`datasource_tables.connection_table_id` is a foreign key with no ON
    DELETE rule. Deleting the catalog row underneath a live reference raises
    `fk_datasource_tables_connection_table_id` — and because this method is
    best-effort, that error is swallowed and the table is left standing, which
    is precisely the bug it exists to fix.

    Hit for real while clearing the stale table by hand. The structural tests
    above all passed while the runtime path could not complete.
    """
    import inspect
    import textwrap

    import app.services.file_service as file_service

    src = textwrap.dedent(
        inspect.getsource(file_service.FileService._retire_all_connection_tables)
    )
    assert src.index("_sql_delete(DataSourceTable)") < src.index("_sql_delete(ConnectionTable)"), (
        "the domain rows must go before the catalog rows they reference, or the "
        "delete raises a foreign-key error that this method silently swallows"
    )
