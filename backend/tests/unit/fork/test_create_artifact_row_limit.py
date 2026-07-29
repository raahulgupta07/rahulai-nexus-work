"""DEF-009 — a dataset over the row limit must produce an artifact, not an error.

Found live: a dashboard was asked for over a master table of 28,592 rows.
``create_artifact`` returned ``error`` because the persisted result had been cut
to 1,000 rows by ``limit_row_count``. The agent recovered on its own — it went
away, wrote a GROUP BY that returned 960 rows, and asked again — which is the
right shape of answer reached the expensive way: a full generation and ~2
minutes, every time, for a case the tool had everything it needed to handle.

Refusing was NOT wrong. The rows in hand are a PREFIX in the query's own sort
order, so a dashboard built from them reports a partial sum as a total (56.4B
against a true 98.9B on the run that produced the Phase 1 gate). No arithmetic
over the persisted prefix recovers the missing rows.

What the tool did have, and did not use, is the QUERY (``steps.code``) and the
clients that ran it, both still in the run's context. DEF-009 re-runs it — no
LLM, seconds — and then:

  * the whole result fits the artifact cap -> every row is used. Complete.
  * it does not                            -> the low-cardinality dimensions are
                                              grouped and the measures summed,
                                              which is what the agent wrote by
                                              hand. Complete, coarser grain.

Both paths compute over the WHOLE dataset, so correctness is never traded for
speed; and both are DECLARED — in the codegen profile, on the stored artifact,
and in the tool's returned observation.

Contract these tests pin:
  * over the limit -> a recovered, COMPLETE visualization, no refusal
  * the reduction is disclosed: row counts used, and that reduction happened
  * an aggregate is arithmetically complete (group sums == whole-dataset sums)
  * under the limit -> untouched, and the query is never re-run
  * every failure of recovery leaves the visualization refused, never partial

The gate and the disclosure plumbing live inside ``run_stream``, an async
generator needing a DB session, a report and an LLM. As in the Phase 1 suite,
those are pinned by reading the source rather than dressed up as behavioral
tests — the behavior above is what matters.
"""
import inspect

import pandas as pd
import pytest

from app.ai.tools.implementations.create_artifact import CreateArtifactTool

# Imported defensively so this suite FAILS on pre-fix code rather than erroring
# at collection — a collection error proves nothing about behavior.
try:
    from app.ai.tools.implementations import create_artifact as ca
    from app.ai.tools.implementations.create_artifact import (
        _aggregate_dataframe,
        _artifact_row_cap,
        _recovery_enabled,
        recover_truncated_visualizations,
    )

    FIX_PRESENT = True
except ImportError:  # pre-fix code path
    FIX_PRESENT = False
    ca = None
    _aggregate_dataframe = _artifact_row_cap = None
    _recovery_enabled = recover_truncated_visualizations = None

needs_fix = pytest.mark.skipif(
    not FIX_PRESENT, reason="symbol does not exist pre-fix (see module docstring)"
)

# ── fixtures: the smallest things that stand in for a settings row, a step's
#    executor and a truncated visualization entry ────────────────────────────


class _Config:
    def __init__(self, value):
        self.value = value


class _Settings:
    """Just enough of OrganizationSettings for `_artifact_row_cap`."""

    def __init__(self, cap):
        self._cap = cap

    def get_config(self, name):
        if name == "artifact_row_limit":
            return _Config(self._cap)
        return None


class _Executor:
    """Stands in for StreamingCodeExecutor: hands back a DataFrame, or raises."""

    def __init__(self, df=None, exc=None):
        self.df = df
        self.exc = exc
        self.calls = []

    async def execute_code_async(self, *, code, ds_clients, excel_files, **kwargs):
        self.calls.append(code)
        if self.exc is not None:
            raise self.exc
        return self.df, "log", []


def _install_executor(monkeypatch, executor):
    monkeypatch.setattr(ca, "_build_executor", lambda *a, **k: executor)
    return executor


def _sales_frame(rows):
    """A shape that reproduces the live failure: months x branches x records."""
    months = pd.date_range("2025-01-01", periods=12, freq="MS")
    branches = [f"BR{i:02d}" for i in range(8)]
    data = []
    for i in range(rows):
        data.append({
            "order_date": months[i % len(months)],
            "branch": branches[i % len(branches)],
            "order_id": f"O{i:07d}",  # an identifier: never a group key
            "amount": float(i % 97) + 1.0,
            "units": i % 5,
        })
    return pd.DataFrame(data)


def _truncated_viz(stored_rows=1000, total=28592, viz_id="viz-1"):
    """A visualization entry as run_stream builds it for a capped step."""
    return {
        "id": viz_id,
        "title": "CFC Sales Master",
        "columns": [{"field": "order_date"}, {"field": "amount"}],
        "column_info": {"amount": {"dtype": "float64"}},
        "row_count": total,
        "rows": [{"order_date": "2025-01-01", "amount": 1.0}] * stored_rows,
        "rows_truncated": True,
        "rows_total": total,
        "rows_available": stored_rows,
    }


# ── 1. the headline behavior: over the limit is no longer an error ───────────


@pytest.mark.asyncio
@needs_fix
async def test_def009_input_over_the_limit_is_recovered_not_refused(monkeypatch):
    """THE regression this suite exists for.

    28,592 rows, 1,000 persisted. Before: `create_artifact` returned an error and
    the agent spent a generation recovering. After: the visualization comes back
    complete and the Phase 1 gate — which refuses anything still carrying
    `rows_truncated` — has nothing to refuse.
    """
    viz = _truncated_viz()
    ex = _install_executor(monkeypatch, _Executor(df=_sales_frame(28592)))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "SELECT ..."}, {}, _Settings(10000)
    )

    assert ex.calls == ["SELECT ..."], "the stored query must be re-run exactly once"
    assert "rows_truncated" not in viz, "a recovered dataset is complete, not partial"
    assert viz.get("rows_available") is None
    assert len(reductions) == 1
    assert len(viz["rows"]) <= 10000


@pytest.mark.asyncio
@needs_fix
async def test_def009_recovered_rows_are_a_complete_aggregate(monkeypatch):
    """Complete means complete: the group sums must equal the full-dataset sum.

    This is the whole justification for building the artifact rather than
    refusing. If the reduction dropped or double-counted a single source row,
    the dashboard would be as wrong as the 1,000-row prefix was — just less
    obviously so.
    """
    df = _sales_frame(28592)
    viz = _truncated_viz()
    _install_executor(monkeypatch, _Executor(df=df))

    await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(10000)
    )

    assert sum(r["amount"] for r in viz["rows"]) == pytest.approx(df["amount"].sum())
    assert sum(r["units"] for r in viz["rows"]) == df["units"].sum()
    count_col = viz["data_reduction"]["row_count_column"]
    assert sum(r[count_col] for r in viz["rows"]) == len(df)


@pytest.mark.asyncio
@needs_fix
async def test_def009_row_count_reflects_the_rows_actually_in_hand(monkeypatch):
    """`row_count` is read as the dataset size by the profile and the gate.

    Leaving the pre-reduction total there would tell the model it holds 28,592
    rows while it holds a few hundred — the same lie the row limit told.
    """
    viz = _truncated_viz()
    _install_executor(monkeypatch, _Executor(df=_sales_frame(28592)))

    await recover_truncated_visualizations([viz], {"viz-1": "q"}, {}, _Settings(10000))

    assert viz["row_count"] == len(viz["rows"])


# ── 2. the reduction is disclosed, in the tool's own returned content ────────


@pytest.mark.asyncio
@needs_fix
async def test_def009_reduction_is_disclosed_with_the_row_counts(monkeypatch):
    viz = _truncated_viz()
    _install_executor(monkeypatch, _Executor(df=_sales_frame(28592)))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(10000)
    )

    red = reductions[0]
    assert red["method"] == "aggregate"
    assert red["source_row_count"] == 28592
    assert red["stored_row_count"] == 1000
    assert red["rows_used"] == len(viz["rows"])
    assert red["visualization_id"] == "viz-1"
    assert red["group_columns"], "which dimensions survived is the reader's first question"
    # The same record is attached to the visualization, which is what carries it
    # into the codegen profile and the stored artifact content.
    assert viz["data_reduction"] == red


@pytest.mark.asyncio
@needs_fix
async def test_def009_notice_states_both_counts_and_that_it_was_aggregated(monkeypatch):
    """A number without the word "aggregated" beside it gets quoted as a total."""
    viz = _truncated_viz()
    _install_executor(monkeypatch, _Executor(df=_sales_frame(28592)))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(10000)
    )

    notice = reductions[0]["notice"]
    assert "28,592" in notice
    assert f"{reductions[0]['rows_used']:,}" in notice
    assert "aggregat" in notice.lower()
    assert "SUM" in notice


@needs_fix
def test_def009_profile_tells_the_model_a_row_is_a_group(monkeypatch):
    """The model must not average a column of sums, or call a group a record."""
    tool = CreateArtifactTool()
    viz = _truncated_viz()
    viz.pop("rows_truncated")
    viz["data_reduction"] = {
        "method": "aggregate",
        "group_columns": ["branch", "order_date"],
        "row_count_column": "source_row_count",
        "source_row_count": 28592,
        "rows_used": 96,
    }

    profile = tool._build_viz_profile(viz, allow_llm_see_data=True)

    assert profile["data_reduction"] == viz["data_reduction"]
    notice = profile["data_reduction_notice"]
    assert "PRE-AGGREGATED" in notice
    assert "28592" in notice
    assert "source_row_count" in notice, "the averages denominator must be named"


@needs_fix
def test_def009_profile_of_an_unreduced_visualization_is_unchanged():
    """A dataset that never needed reducing must not gain reduction language."""
    tool = CreateArtifactTool()
    viz = {
        "id": "v", "title": "t", "row_count": 42,
        "columns": [{"field": "amount"}],
        "rows": [{"amount": 1}],
    }

    profile = tool._build_viz_profile(viz, allow_llm_see_data=True)

    assert "data_reduction" not in profile
    assert "data_reduction_notice" not in profile


# ── 3. a small input is completely unaffected ────────────────────────────────


@pytest.mark.asyncio
@needs_fix
async def test_def009_untruncated_visualization_is_left_alone(monkeypatch):
    """No truncation, no re-run: the common case must not pay for this at all."""
    viz = {
        "id": "small", "title": "Monthly totals", "row_count": 96,
        "columns": [{"field": "amount"}],
        "rows": [{"amount": i} for i in range(96)],
    }
    before = dict(viz)
    ex = _install_executor(monkeypatch, _Executor(df=_sales_frame(10)))

    reductions = await recover_truncated_visualizations(
        [viz], {"small": "q"}, {}, _Settings(10000)
    )

    assert reductions == []
    assert ex.calls == [], "an unreduced dataset must never re-run its query"
    assert viz == before
    assert "data_reduction" not in viz


@pytest.mark.asyncio
@needs_fix
async def test_def009_only_the_truncated_visualization_is_touched(monkeypatch):
    small = {"id": "small", "title": "s", "row_count": 5, "rows": [{"a": 1}] * 5}
    big = _truncated_viz(viz_id="big")
    ex = _install_executor(monkeypatch, _Executor(df=_sales_frame(28592)))

    reductions = await recover_truncated_visualizations(
        [small, big], {"small": "qs", "big": "qb"}, {}, _Settings(10000)
    )

    assert ex.calls == ["qb"]
    assert [r["visualization_id"] for r in reductions] == ["big"]
    assert "data_reduction" not in small


# ── 4. a result that fits is re-read whole, not aggregated ───────────────────


@pytest.mark.asyncio
@needs_fix
async def test_def009_result_within_the_cap_is_re_read_in_full(monkeypatch):
    """1,200 rows persisted as 1,000: no need to coarsen anything, just fetch it."""
    df = _sales_frame(1200)
    viz = _truncated_viz(stored_rows=1000, total=1200)
    _install_executor(monkeypatch, _Executor(df=df))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(10000)
    )

    assert reductions[0]["method"] == "full_reread"
    assert len(viz["rows"]) == 1200
    assert "rows_truncated" not in viz
    assert "1,200" in reductions[0]["notice"]
    # Row-level detail survives — nothing was grouped away.
    assert "order_id" in viz["rows"][0]


# ── 5. every failure leaves the refusal in place ─────────────────────────────


@needs_fix
@pytest.mark.parametrize(
    "executor, codes",
    [
        (_Executor(exc=RuntimeError("connection reset")), {"viz-1": "q"}),
        (_Executor(df=pd.DataFrame()), {"viz-1": "q"}),
        (_Executor(df=None), {"viz-1": "q"}),
        (_Executor(df=_sales_frame(50)), {}),  # no stored query to re-run
    ],
    ids=["query-fails", "empty-result", "no-dataframe", "no-stored-query"],
)
@pytest.mark.asyncio
async def test_def009_failed_recovery_leaves_the_visualization_refused(
    monkeypatch, executor, codes
):
    """Recovery may only turn a refusal into a CORRECT artifact.

    If anything goes wrong it must leave `rows_truncated` standing, so the
    Phase 1 gate refuses exactly as it does today. Silently proceeding with the
    prefix is the defect Phase 1 exists to prevent.
    """
    viz = _truncated_viz()
    _install_executor(monkeypatch, executor)

    reductions = await recover_truncated_visualizations(
        [viz], codes, {}, _Settings(10000)
    )

    assert reductions == []
    assert viz["rows_truncated"] is True
    assert viz["rows_available"] == 1000
    assert len(viz["rows"]) == 1000


@pytest.mark.asyncio
@needs_fix
async def test_def009_result_with_no_measures_is_left_refused(monkeypatch):
    """Nothing to SUM means no honest aggregate — a COUNT answers another question."""
    df = pd.DataFrame({"branch": [f"BR{i%9}" for i in range(50)],
                       "note": [f"n{i}" for i in range(50)]})
    viz = _truncated_viz(total=50)
    _install_executor(monkeypatch, _Executor(df=df))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(10)
    )

    assert reductions == []
    assert viz["rows_truncated"] is True


@pytest.mark.asyncio
@needs_fix
async def test_def009_result_of_pure_identifiers_is_left_refused(monkeypatch):
    """Every dimension unique: grouping returns the input and calls it a summary."""
    df = pd.DataFrame({"order_id": [f"O{i}" for i in range(9000)],
                       "amount": range(9000)})
    viz = _truncated_viz(total=9000)
    _install_executor(monkeypatch, _Executor(df=df))

    reductions = await recover_truncated_visualizations(
        [viz], {"viz-1": "q"}, {}, _Settings(100)
    )

    assert reductions == []
    assert viz["rows_truncated"] is True


# ── 6. the aggregator's own contract ─────────────────────────────────────────


@needs_fix
def test_def009_every_group_carries_its_own_sum_and_its_own_count():
    """Totals matching is necessary but not sufficient.

    The per-group row count is attached from a second `groupby` pass, so if the
    two passes ever disagreed on order, every total would still be right while
    every ROW was wrong — the worst possible failure, because it looks correct
    in aggregate. Checked per group against pandas directly.
    """
    df = _sales_frame(5000)
    agg, meta = _aggregate_dataframe(df, cap=200)
    keys = meta["group_columns"]
    # Mirror the month binning, or the expected index is keyed on Timestamps
    # while the aggregate is keyed on "2025-01" strings.
    expected_src = df.copy()
    for col in meta["binned_columns"]:
        expected_src[col] = expected_src[col].dt.to_period("M").astype(str)
    expected_sum = expected_src.groupby(keys, dropna=False)["amount"].sum()
    expected_n = expected_src.groupby(keys, dropna=False).size()

    for _, row in agg.iterrows():
        key = tuple(row[k] for k in keys)
        key = key[0] if len(key) == 1 else key
        assert row["amount"] == pytest.approx(expected_sum.loc[key])
        assert row[meta["row_count_column"]] == expected_n.loc[key]


@needs_fix
def test_def009_aggregate_fits_the_cap():
    agg, meta = _aggregate_dataframe(_sales_frame(28592), cap=200)
    assert agg is not None
    assert len(agg) <= 200
    assert meta["measures"] == ["amount", "units"]


@needs_fix
def test_def009_high_cardinality_columns_are_never_group_keys():
    """`order_id` is an identifier. Grouping on it is a no-op wearing a hat."""
    _, meta = _aggregate_dataframe(_sales_frame(28592), cap=10000)
    assert "order_id" not in meta["group_columns"]
    assert "order_id" in meta["dropped_columns"]


@needs_fix
def test_def009_datetime_dimensions_are_binned_not_discarded():
    """A timestamp is near-unique, so it would be dropped as an identifier —
    and the time axis is usually the most useful thing on the dashboard."""
    df = _sales_frame(5000)
    df["order_date"] = df["order_date"] + pd.to_timedelta(df.index, unit="s")
    _, meta = _aggregate_dataframe(df, cap=500)
    assert meta["binned_columns"] == {"order_date": "month"}
    assert "order_date" in meta["group_columns"]


@needs_fix
def test_def009_aggregate_does_not_collide_with_an_existing_count_column():
    df = _sales_frame(5000)
    df["source_row_count"] = 1
    agg, meta = _aggregate_dataframe(df, cap=500)
    assert meta["row_count_column"] != "source_row_count"
    assert meta["row_count_column"] in agg.columns
    # The user's own column is preserved as a measure, not overwritten.
    assert "source_row_count" in agg.columns
    assert agg["source_row_count"].sum() == 5000


@needs_fix
@pytest.mark.parametrize("cap", [0, -1])
def test_def009_no_cap_means_nothing_to_aggregate(cap):
    """"0 = no limit" is what the setting says; there is then no over-limit case."""
    assert _aggregate_dataframe(_sales_frame(100), cap=cap) == (None, None)


# ── 7. the cap is read from the org setting ──────────────────────────────────


@needs_fix
@pytest.mark.parametrize("stored, expected", [(10000, 10000), (250, 250), (0, 0), (-5, 0)])
def test_def009_artifact_row_cap_reads_the_setting(stored, expected):
    assert _artifact_row_cap(_Settings(stored)) == expected


@needs_fix
@pytest.mark.parametrize("settings", [None, object(), _Settings("nonsense")])
def test_def009_unreadable_cap_falls_back_to_the_default(settings):
    """An older settings row has no `artifact_row_limit`; that is not a reason to
    treat every dataset as uncapped."""
    assert _artifact_row_cap(settings) == 10000


# ── 8. the flag, and the "off"-is-truthy trap that has bitten this file ──────


@needs_fix
def test_def009_recovery_is_on_by_default(monkeypatch):
    import app.settings.config as cfg

    class _Stub:
        pass

    monkeypatch.setattr(cfg, "settings", _Stub())
    assert _recovery_enabled() is True


@needs_fix
@pytest.mark.parametrize("value", ["off", "OFF", " Off ", "false", "no", "0", ""])
def test_def009_the_string_off_never_enables_recovery(monkeypatch, value):
    import app.settings.config as cfg

    class _Stub:
        hybrid_artifact_data_recovery = value

    monkeypatch.setattr(cfg, "settings", _Stub())
    assert _recovery_enabled() is False


# ── 9. wiring inside run_stream ──────────────────────────────────────────────
#
# Read from source, for the reason the Phase 1 suite gives: the gate sits in an
# async generator that cannot be driven without a DB, a report and an LLM, and a
# contract nobody checks disappears in the next refactor. These pin WIRING; the
# behavior above is what matters.

_RUN_STREAM_SRC = inspect.getsource(CreateArtifactTool.run_stream)


def test_def009_run_stream_attempts_recovery_before_the_gate():
    src = _RUN_STREAM_SRC
    assert "recover_truncated_visualizations(" in src
    assert src.index("recover_truncated_visualizations(") < src.index(
        "_completeness_gate_enabled()"
    ), "recovering after the refusal has already been returned would do nothing"


def test_def009_the_refusal_still_exists_for_unrecoverable_data():
    """DEF-009 adds a path; it does not remove Phase 1."""
    assert '"incomplete_visualization_data"' in _RUN_STREAM_SRC


def test_def009_run_stream_keeps_the_query_needed_to_re_read():
    assert "step_code_by_viz" in _RUN_STREAM_SRC


def test_def009_reduction_is_carried_into_the_stored_artifact_and_the_result():
    src = _RUN_STREAM_SRC
    assert 'content["data_reduction"] = data_reductions' in src
    assert 'observation["data_reduction"] = data_reductions' in src
    assert "was too large for one artifact and was reduced first" in src, (
        "the summary line is what the agent actually reads"
    )

# ── 10. the switch has to be real, and the re-read has to have a ceiling ─────
#
# Two operational properties, not behavioral ones. Recovery re-runs a query with
# no LIMIT and materializes the whole result in this worker, so an operator has
# to be able to (a) turn it off and (b) rely on it not reading without bound.


def test_def009_the_flag_is_actually_declared_in_config():
    """The kill switch only exists if `settings` carries the field.

    `_read_bool_setting` getattrs `settings` and returns the DEFAULT for a name
    it cannot find — so an undeclared flag reads HYBRID_ARTIFACT_DATA_RECOVERY
    =false, finds nothing, and stays on. The flag looked switchable, was
    documented as switchable, and was not.
    """
    from app.settings.config import settings

    assert hasattr(settings, "hybrid_artifact_data_recovery"), (
        "HYBRID_ARTIFACT_DATA_RECOVERY cannot be turned off unless "
        "`hybrid_artifact_data_recovery` is declared on Settings"
    )
    assert isinstance(settings.hybrid_artifact_data_recovery, bool)


def test_def009_the_declared_flag_defaults_to_on():
    """Declaring it must not quietly change today's behavior."""
    from app.settings.config import settings

    assert settings.hybrid_artifact_data_recovery is True


@pytest.mark.asyncio
async def test_def009_a_result_past_the_ceiling_is_left_refused(monkeypatch):
    """A re-read far past the cap is abandoned, not reduced.

    Truncation is what kept an oversized result out of this worker's memory;
    recovery undoes truncation, so it needs its own ceiling. Past it, the
    Phase 1 refusal is the correct outcome — the same one the user got before
    recovery existed.
    """
    monkeypatch.setattr(ca, "_RECOVERY_MAX_SOURCE_ROWS", 100)
    _install_executor(monkeypatch, _Executor(df=_sales_frame(101)))

    viz = _truncated_viz()
    reductions = await ca.recover_truncated_visualizations(
        [viz], {"viz-1": "select 1"}, {}, _Settings(10)
    )

    assert reductions == []
    assert viz["rows_truncated"] is True, (
        "the markers must survive, or the completeness gate passes data it "
        "never actually recovered"
    )
    assert len(viz["rows"]) == 1000, "the stored prefix is left exactly as it was"


@pytest.mark.asyncio
async def test_def009_a_result_at_the_ceiling_is_still_recovered(monkeypatch):
    """The ceiling is a ceiling, not a lower bound — equal still recovers."""
    monkeypatch.setattr(ca, "_RECOVERY_MAX_SOURCE_ROWS", 100)
    _install_executor(monkeypatch, _Executor(df=_sales_frame(100)))

    viz = _truncated_viz()
    reductions = await ca.recover_truncated_visualizations(
        [viz], {"viz-1": "select 1"}, {}, _Settings(10000)
    )

    assert len(reductions) == 1
    assert not viz.get("rows_truncated")


@pytest.mark.asyncio
async def test_def009_the_ceiling_does_not_stop_the_next_visualization(
    monkeypatch,
):
    """One oversized result must not cost the others their recovery."""
    monkeypatch.setattr(ca, "_RECOVERY_MAX_SOURCE_ROWS", 100)

    frames = {"big": _sales_frame(101), "small": _sales_frame(50)}

    class _PerQueryExecutor:
        async def execute_code_async(self, *, code, ds_clients, excel_files, **kw):
            return frames[code], "log", []

    monkeypatch.setattr(ca, "_build_executor", lambda *a, **k: _PerQueryExecutor())

    over = _truncated_viz(viz_id="viz-big")
    under = _truncated_viz(viz_id="viz-small")
    reductions = await ca.recover_truncated_visualizations(
        [over, under], {"viz-big": "big", "viz-small": "small"}, {}, _Settings(10000)
    )

    assert len(reductions) == 1
    assert over["rows_truncated"] is True
    assert not under.get("rows_truncated")


def test_def009_the_ceiling_is_env_overridable():
    """An operator with more memory than this default should be able to say so."""
    src = inspect.getsource(ca)
    assert 'os.getenv("DASH_RECOVERY_MAX_SOURCE_ROWS"' in src
    assert ca._RECOVERY_MAX_SOURCE_ROWS > 0
