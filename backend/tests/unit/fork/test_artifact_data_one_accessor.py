"""DEF-010 — one artifact, one dataset.

A dashboard reported a total ~16% below the truth because the figures were the
sum of a 1,000-row prefix, and the SAME artifact exported to PDF reported a
different (larger) number because the export preferred a wider copy of the same
result that the browser could not see. Two readers, one artifact, two answers,
neither saying anything.

These tests are entirely synthetic: rows are generated, columns are named `c0`,
`c1`, and no connector, table, currency or figure from any real deployment
appears. The properties hold for any source that can produce more rows than a
cap allows.
"""
from __future__ import annotations

import pytest

from app.services.artifact_data import (
    ARTIFACT_COLUMNS_KEY,
    ARTIFACT_REDUCTION_KEY,
    ARTIFACT_ROWS_KEY,
    apply_to_step_payload,
    attach_artifact_rows,
    resolve_artifact_rows,
    store_artifact_dataset,
)

DISPLAY_CAP = 1000
ARTIFACT_CAP = 10000


def _rows(n: int, offset: int = 0):
    """A synthetic result: one measure and one dimension, n rows."""
    return [{"c0": i + offset, "c1": f"g{(i + offset) % 7}"} for i in range(n)]


def _columns(*names):
    return [{"headerName": n, "field": n} for n in names]


def _step_blob(display_n: int, total_n: int, *, wide_n: int | None = None, reduction=None):
    """A step's stored `data`, shaped exactly as the writers shape it."""
    blob = {
        "rows": _rows(display_n),
        "columns": _columns("c0", "c1"),
        "info": {"total_rows": total_n},
    }
    if total_n > display_n:
        # This is what the formatter stamps: it describes the DISPLAY copy.
        blob["rows_truncated"] = True
        blob["rows_total"] = total_n
    if wide_n is not None:
        blob[ARTIFACT_ROWS_KEY] = _rows(wide_n)
        blob["rows_artifact_total"] = wide_n
    if reduction is not None:
        blob[ARTIFACT_REDUCTION_KEY] = reduction
    return blob


class _FakeExecutor:
    """Stands in for StreamingCodeExecutor's two formatting caps."""

    def __init__(self, display_cap=DISPLAY_CAP, artifact_cap=ARTIFACT_CAP, raises=False):
        self.display_cap = display_cap
        self.artifact_cap = artifact_cap
        self.raises = raises
        self.calls = []

    def format_df_for_widget(self, df, max_rows=None, for_artifact=False):
        self.calls.append(for_artifact)
        if self.raises:
            raise RuntimeError("formatter unavailable")
        cap = self.artifact_cap if for_artifact else self.display_cap
        payload = {"rows": list(df[:cap]), "columns": _columns("c0", "c1")}
        if len(df) > cap:
            payload["rows_truncated"] = True
            payload["rows_total"] = len(df)
        return payload


# ─────────────────────────────────────────────────────────────────────────────
# The accessor itself
# ─────────────────────────────────────────────────────────────────────────────

def test_a_prefix_is_reported_as_incomplete():
    """The whole point of the gate: rows in hand < the result -> truncated."""
    resolved = resolve_artifact_rows(_step_blob(DISPLAY_CAP, 215089))
    assert resolved.rows_used == DISPLAY_CAP
    assert resolved.rows_total == 215089
    assert resolved.truncated is True
    assert "215,089" in (resolved.notice() or "")


def test_the_wide_copy_wins_and_makes_the_result_complete():
    resolved = resolve_artifact_rows(_step_blob(DISPLAY_CAP, 1200, wide_n=1200))
    assert resolved.rows_used == 1200
    assert resolved.source == ARTIFACT_ROWS_KEY
    assert resolved.truncated is False
    assert resolved.notice() is None


def test_the_stale_truncation_flag_does_not_decide_completeness():
    """`rows_truncated` describes the DISPLAY copy and stays True forever.

    Reading it as the answer made the gate refuse data that was complete.
    """
    blob = _step_blob(DISPLAY_CAP, 1200, wide_n=1200)
    assert blob["rows_truncated"] is True          # the stored flag says partial
    assert resolve_artifact_rows(blob).truncated is False   # the data says otherwise


def test_a_step_without_a_wide_copy_is_untouched():
    blob = _step_blob(400, 400)
    resolved = resolve_artifact_rows(blob)
    assert resolved.rows == blob["rows"]
    assert resolved.columns == blob["columns"]
    assert resolved.source == "rows"
    assert resolved.truncated is False


def test_junk_in_the_wide_slot_is_ignored():
    blob = _step_blob(DISPLAY_CAP, 1200)
    blob[ARTIFACT_ROWS_KEY] = "not-a-list"
    resolved = resolve_artifact_rows(blob)
    assert resolved.source == "rows"
    assert resolved.rows_used == DISPLAY_CAP


def test_an_aggregate_is_complete_even_though_it_is_smaller():
    """A reduction covers every source row, so it is not a prefix."""
    reduction = {"method": "aggregate", "notice": "re-read in full and pre-aggregated"}
    blob = _step_blob(DISPLAY_CAP, 215089, wide_n=None, reduction=reduction)
    blob[ARTIFACT_ROWS_KEY] = _rows(292)
    blob[ARTIFACT_COLUMNS_KEY] = _columns("c1", "c0", "source_row_count")
    resolved = resolve_artifact_rows(blob)
    assert resolved.rows_used == 292
    assert resolved.truncated is False
    assert resolved.columns == blob[ARTIFACT_COLUMNS_KEY]
    assert resolved.notice() == reduction["notice"]


def test_a_shorter_wide_copy_with_no_reduction_recorded_is_not_trusted():
    blob = _step_blob(DISPLAY_CAP, 215089)
    blob[ARTIFACT_ROWS_KEY] = _rows(12)     # leftover, nothing says why
    resolved = resolve_artifact_rows(blob)
    assert resolved.source == "rows"
    assert resolved.rows_used == DISPLAY_CAP


def test_no_step_data_is_survivable():
    for junk in (None, "", 5, []):
        resolved = resolve_artifact_rows(junk)
        assert resolved.rows == []
        assert resolved.truncated is False


# ─────────────────────────────────────────────────────────────────────────────
# The property that failed live: two render paths, one answer
# ─────────────────────────────────────────────────────────────────────────────

def _rows_the_export_renders(step_data):
    """Exactly what app/services/dashboard_pdf_export_service.py does."""
    from app.services.dashboard_pdf_export_service import resolve_artifact_rows as export_resolver

    r = export_resolver(step_data)
    return r.rows, r.columns


def _rows_the_browser_renders(step_data):
    """Exactly what the browser gets: `step.data.rows` off the step API."""
    served = apply_to_step_payload(step_data)
    return served.get("rows"), served.get("columns")


def _rows_the_build_used(step_data):
    """Exactly what create_artifact builds and gates on."""
    from app.ai.tools.implementations.create_artifact import resolve_artifact_rows as build_resolver

    r = build_resolver(step_data)
    return r.rows, r.columns


@pytest.mark.parametrize(
    "blob",
    [
        _step_blob(400, 400),                                    # nothing capped
        _step_blob(DISPLAY_CAP, 1200, wide_n=1200),              # wide copy in play
        _step_blob(DISPLAY_CAP, 215089),                         # genuinely partial
    ],
    ids=["complete", "wide_copy", "partial"],
)
def test_every_render_path_reads_the_same_rows(blob):
    """The bug, as a test: the page and the export must not disagree.

    Live, one artifact showed a total over 1,000 rows on screen and a total over
    1,200 rows in its own PDF export.
    """
    assert _rows_the_browser_renders(blob) == _rows_the_export_renders(blob)
    assert _rows_the_browser_renders(blob) == _rows_the_build_used(blob)


def test_the_render_paths_share_one_implementation():
    """Not merely equal today — the same function object."""
    from app.ai.tools.implementations import create_artifact as build_mod
    from app.services import artifact_data
    from app.services import dashboard_pdf_export_service as export_mod

    assert export_mod.resolve_artifact_rows is artifact_data.resolve_artifact_rows
    assert build_mod.resolve_artifact_rows is artifact_data.resolve_artifact_rows


def test_what_the_browser_is_served_declares_the_cap():
    served = apply_to_step_payload(_step_blob(DISPLAY_CAP, 1200, wide_n=1200))
    assert served["rows_source"] == ARTIFACT_ROWS_KEY
    assert served["rows_total"] == 1200
    assert served["rows_truncated"] is False


def test_serving_a_step_never_mutates_the_stored_blob():
    blob = _step_blob(DISPLAY_CAP, 1200, wide_n=1200)
    before = len(blob["rows"])
    apply_to_step_payload(blob)
    assert len(blob["rows"]) == before


# ─────────────────────────────────────────────────────────────────────────────
# The producers: every writer attaches the same copy, so a refresh cannot shrink
# a dashboard behind the user's back
# ─────────────────────────────────────────────────────────────────────────────

def test_a_writer_attaches_the_wide_copy_when_the_display_cap_cut_something():
    df = _rows(1200)
    ex = _FakeExecutor()
    formatted = ex.format_df_for_widget(df)
    attach_artifact_rows(ex, df, formatted)
    assert len(formatted[ARTIFACT_ROWS_KEY]) == 1200
    assert formatted["rows_artifact_total"] == 1200
    assert formatted[ARTIFACT_COLUMNS_KEY]
    assert len(formatted["rows"]) == DISPLAY_CAP     # display copy untouched


def test_a_writer_stores_nothing_wider_when_the_artifact_cap_also_cuts():
    """A bigger prefix is still a prefix — this must stay refusable."""
    df = _rows(215089)
    ex = _FakeExecutor()
    formatted = ex.format_df_for_widget(df)
    attach_artifact_rows(ex, df, formatted)
    assert ARTIFACT_ROWS_KEY not in formatted
    assert resolve_artifact_rows({**formatted, "info": {"total_rows": 215089}}).truncated is True


def test_a_complete_result_gets_no_second_copy():
    df = _rows(50)
    ex = _FakeExecutor()
    formatted = ex.format_df_for_widget(df)
    attach_artifact_rows(ex, df, formatted)
    assert ARTIFACT_ROWS_KEY not in formatted
    assert ex.calls == [False]        # no needless second formatting pass


def test_a_writer_that_cannot_build_the_wide_copy_still_returns_the_step():
    df = _rows(1200)
    ex = _FakeExecutor()
    formatted = ex.format_df_for_widget(df)
    broken = _FakeExecutor(raises=True)
    out = attach_artifact_rows(broken, df, formatted)
    assert out is formatted
    assert ARTIFACT_ROWS_KEY not in out
    # ...and the result is then correctly reported as incomplete.
    assert resolve_artifact_rows({**out, "info": {"total_rows": 1200}}).truncated is True


def test_a_rerun_keeps_the_dashboard_the_same_size():
    """A refresh formats the result again. If it only writes the display copy,
    the dashboard built on the wide one silently loses rows."""
    df = _rows(1200)
    ex = _FakeExecutor()

    first = ex.format_df_for_widget(df)
    attach_artifact_rows(ex, df, first)
    first["info"] = {"total_rows": 1200}

    rerun = ex.format_df_for_widget(df)
    attach_artifact_rows(ex, df, rerun)
    rerun["info"] = {"total_rows": 1200}

    assert resolve_artifact_rows(rerun).rows_used == resolve_artifact_rows(first).rows_used


def test_every_writer_uses_the_same_attach_helper():
    import app.ai.tools.implementations.create_data as create_data_mod
    import app.ai.tools.mcp.create_data as mcp_create_data_mod
    import app.services.query_service as query_service_mod
    import app.services.step_service as step_service_mod
    from app.services.artifact_data import attach_artifact_rows as shared

    for mod in (create_data_mod, mcp_create_data_mod, query_service_mod, step_service_mod):
        src = "".join(
            line for line in open(mod.__file__).read().splitlines(keepends=True)
            if not line.lstrip().startswith("#")
        )
        assert "attach_artifact_rows" in src, f"{mod.__name__} writes a step without the wide copy"
    assert shared.__module__ == "app.services.artifact_data"


# ─────────────────────────────────────────────────────────────────────────────
# Writing back what an artifact was actually built on
# ─────────────────────────────────────────────────────────────────────────────

def test_a_recovered_dataset_becomes_what_every_path_renders():
    """create_artifact may re-read and aggregate a truncated result. Before this,
    that dataset lived only in the tool's memory: the dashboard was built on an
    aggregate and rendered from a prefix with different columns."""
    stored = _step_blob(DISPLAY_CAP, 215089)
    aggregate = _rows(292)
    agg_columns = _columns("c1", "source_row_count")
    reduction = {"method": "aggregate", "notice": "aggregated over every source row"}

    written = store_artifact_dataset(stored, aggregate, agg_columns, reduction)

    assert written is not stored                     # reassignment, or the ORM misses it
    assert stored["rows"] == written["rows"]         # display copy left alone
    for reader in (_rows_the_browser_renders, _rows_the_export_renders, _rows_the_build_used):
        rows, columns = reader(written)
        assert rows == aggregate
        assert columns == agg_columns
    assert resolve_artifact_rows(written).truncated is False
    assert resolve_artifact_rows(written).notice() == reduction["notice"]


def test_writing_back_a_full_reread_clears_any_earlier_reduction():
    stored = store_artifact_dataset(
        _step_blob(DISPLAY_CAP, 215089), _rows(292), _columns("c1"), {"notice": "old"}
    )
    rewritten = store_artifact_dataset(stored, _rows(1200), _columns("c0", "c1"), None)
    assert ARTIFACT_REDUCTION_KEY not in rewritten
    assert resolve_artifact_rows(rewritten).rows_used == 1200


def test_the_build_can_write_back_and_disclose():
    """The two helpers create_artifact needs are the shared ones."""
    from app.ai.tools.implementations import create_artifact as build_mod
    from app.services import artifact_data

    assert build_mod.store_artifact_dataset is artifact_data.store_artifact_dataset


# ─────────────────────────────────────────────────────────────────────────────
# A cap must be stated, and a flag must be read as a flag
# ─────────────────────────────────────────────────────────────────────────────

def test_a_capped_dataset_carries_a_sentence_saying_so():
    notice = resolve_artifact_rows(_step_blob(DISPLAY_CAP, 4321)).notice()
    assert notice and "1,000" in notice and "4,321" in notice
    assert "PARTIAL" in notice


def test_the_preview_render_declares_its_own_cap():
    from app.ai.tools.implementations.create_artifact import CreateArtifactTool, _RENDER_ROW_LIMIT

    big = [{"c0": i} for i in range(_RENDER_ROW_LIMIT + 5)]
    out = CreateArtifactTool()._render_visualizations([{"id": "v", "rows": big}])
    assert out[0]["rows_truncated"] is True
    assert "dataNotice" in out[0]


def test_a_reduction_reaches_the_rendered_payload():
    from app.ai.tools.implementations.create_artifact import CreateArtifactTool

    out = CreateArtifactTool()._render_visualizations([
        {"id": "v", "rows": _rows(10), "data_reduction": {"notice": "aggregated over everything"}}
    ])
    assert out[0]["dataNotice"] == "aggregated over everything"


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("off", False), ("on", True), ("false", False), (None, True)],
)
def test_the_completeness_gate_flag_is_type_checked(monkeypatch, value, expected):
    """`"off"` is TRUTHY in Python. A gate read by truthiness is not a gate."""
    import app.settings.config as config_mod
    from app.ai.tools.implementations.create_artifact import _completeness_gate_enabled

    class _Stub:
        pass

    stub = _Stub()
    if value is not None:
        stub.hybrid_artifact_completeness_gate = value
    # `settings` is a pydantic BaseSettings instance and rejects assignment of
    # fields it does not declare — swap the module attribute instead.
    monkeypatch.setattr(config_mod, "settings", stub)
    assert _completeness_gate_enabled() is expected
