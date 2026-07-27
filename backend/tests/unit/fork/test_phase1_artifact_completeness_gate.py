"""PHASE 1 — a dashboard may not be built from a truncated dataset.

Found by: the same live runs that produced DEF-004. Query results are capped at
``limit_row_count`` (default 1000) and only the capped copy is persisted, so a
dashboard built from that prefix reported NET SALES 56.4B against a true 98.9B
and covered 10 of 17 months with no indication anywhere on the page.

DEF-004 made the truncation VISIBLE — the step declares ``rows_truncated`` and
the codegen profile carries a completeness warning. Visibility was not enough.
The model read the warning, built the dashboard anyway, rendered it, noticed the
totals were partial, discarded it and rebuilt with compact aggregates. That is
the right outcome reached the expensive way, and it happened on all three live
runs; any run where the model did NOT notice shipped the wrong number. So
``create_artifact`` now REFUSES, before a generation is spent, and tells the
agent to pre-aggregate instead.

Two things are testable in isolation and both are pinned here:

  * the flag. ``_completeness_gate_enabled()`` defaults ON, and — this is the
    part that has bitten this codebase three times — the STRING "off" must not
    enable it by truthiness. ``_read_bool_setting`` exists for exactly that and
    carries a comment saying so.
  * the truncation determination. ``step_truncated`` is now
    ``rows_total > len(rows)``: it deliberately does NOT trust the step's own
    ``rows_truncated`` flag, because that flag describes the PREVIEW copy and is
    stale once the artifact-width copy is in use. Trusting it would make the
    gate refuse a dataset that is complete.

The gate itself lives inside ``CreateArtifactTool.run_stream``, a long async
generator that needs a DB session, a report and an LLM, so it is not driven end
to end here. Its wiring is pinned by reading the source instead — stated plainly
rather than dressed up as a behavioral test.

Contract these tests pin:
  * the completeness gate is ON by default and OFF only for a real deny value
  * the string "off" (and "false"/"no"/"0"/"") never enables it
  * an unreadable flag value falls back to the default, never to truthiness
  * truncation is decided by the rows IN HAND vs the true total, nothing else
  * a step whose stored flag says truncated, but whose artifact rows are
    complete, is NOT treated as truncated
  * the refusal is a failure (``success: False``) carrying
    ``truncated_visualizations`` and error type ``incomplete_visualization_data``
"""
import inspect

import pytest

from app.ai.tools.implementations.create_artifact import CreateArtifactTool

# The fix under test adds these names. Imported defensively ONLY so this suite
# FAILS on pre-fix code instead of erroring at collection — a collection error
# proves nothing about behavior.
try:
    from app.ai.tools.implementations.create_artifact import (
        _completeness_gate_enabled,
        _read_bool_setting,
    )

    FIX_PRESENT = True
except ImportError:  # pre-fix code path
    FIX_PRESENT = False
    _completeness_gate_enabled = _read_bool_setting = None

needs_fix = pytest.mark.skipif(
    not FIX_PRESENT, reason="symbol does not exist pre-fix (see module docstring)"
)

FLAG = "hybrid_artifact_completeness_gate"

# Values that are all TRUTHY in Python and every one of which means "off".
OFF_STRINGS = ["off", "OFF", " Off ", "false", "FALSE", " False ", "no", "0", ""]
ON_STRINGS = ["on", "ON", " On ", "true", "TRUE", " True ", "yes", "1"]
# Neither a bool nor a string: unreadable, so the default must win.
JUNK_VALUES = [None, 0, 1, 3.5, object(), [], {}, ("on",)]


# ── how these tests inject a setting value ──────────────────────────────────
# `settings` is a pydantic BaseSettings instance, so it REJECTS assignment to a
# field it does not declare — `monkeypatch.setattr(settings, "probe", ...)`
# raises, and junk values fail field validation even on real fields. But
# `_read_bool_setting` re-imports the module and does getattr() on every call,
# so swapping the module attribute for a plain stub injects any value we like,
# including the malformed ones this is meant to cover.
def _stub_settings(monkeypatch, name=None, value=None, present=True):
    import app.settings.config as _cfg

    class _Stub:
        pass

    stub = _Stub()
    if present and name is not None:
        setattr(stub, name, value)
    monkeypatch.setattr(_cfg, "settings", stub)
    return stub


@pytest.fixture
def gate_flag(monkeypatch):
    def _set(value):
        _stub_settings(monkeypatch, FLAG, value)

    return _set


# --- 1. the gate flag ---------------------------------------------------------


@needs_fix
def test_phase1_gate_is_on_when_the_setting_is_missing(monkeypatch):
    """Fail safe: an absent flag protects the numbers, it does not open the gate."""
    _stub_settings(monkeypatch, present=False)
    assert _completeness_gate_enabled() is True


@needs_fix
def test_phase1_gate_on_for_boolean_true(gate_flag):
    gate_flag(True)
    assert _completeness_gate_enabled() is True


@needs_fix
def test_phase1_gate_off_for_boolean_false(gate_flag):
    """The only intended way to turn it off."""
    gate_flag(False)
    assert _completeness_gate_enabled() is False


@needs_fix
@pytest.mark.parametrize("value", OFF_STRINGS)
def test_phase1_string_off_does_not_enable_the_gate(gate_flag, value):
    """`if value:` is True for the string "off". Three past defects, one rule."""
    gate_flag(value)
    assert _completeness_gate_enabled() is False


@needs_fix
@pytest.mark.parametrize("value", ON_STRINGS)
def test_phase1_string_on_enables_the_gate(gate_flag, value):
    gate_flag(value)
    assert _completeness_gate_enabled() is True


@needs_fix
@pytest.mark.parametrize("value", JUNK_VALUES)
def test_phase1_unreadable_flag_value_defaults_the_gate_on(gate_flag, value):
    """Note 0 and 1 are here on purpose: an int is not a declared answer."""
    gate_flag(value)
    assert _completeness_gate_enabled() is True


# --- 2. _read_bool_setting, directly -----------------------------------------


@needs_fix
@pytest.mark.parametrize("stored", [True, False])
@pytest.mark.parametrize("default", [True, False])
def test_phase1_bool_value_passes_through_whatever_the_default(
    monkeypatch, stored, default
):
    _stub_settings(monkeypatch, "phase1_probe_flag", stored)
    assert _read_bool_setting("phase1_probe_flag", default) is stored


@needs_fix
@pytest.mark.parametrize("value", ON_STRINGS)
def test_phase1_read_bool_setting_on_forms(monkeypatch, value):
    _stub_settings(monkeypatch, "phase1_probe_flag", value)
    # The default is deliberately the OPPOSITE of the expected answer, so a
    # fall-through to the default cannot pass this test by accident.
    assert _read_bool_setting("phase1_probe_flag", False) is True


@needs_fix
@pytest.mark.parametrize("value", OFF_STRINGS)
def test_phase1_read_bool_setting_off_forms(monkeypatch, value):
    _stub_settings(monkeypatch, "phase1_probe_flag", value)
    assert _read_bool_setting("phase1_probe_flag", True) is False


@needs_fix
@pytest.mark.parametrize("value", JUNK_VALUES)
@pytest.mark.parametrize("default", [True, False])
def test_phase1_junk_value_returns_the_default(monkeypatch, value, default):
    _stub_settings(monkeypatch, "phase1_probe_flag", value)
    assert _read_bool_setting("phase1_probe_flag", default) is default


@needs_fix
@pytest.mark.parametrize("default", [True, False])
def test_phase1_missing_name_returns_the_default(monkeypatch, default):
    _stub_settings(monkeypatch, present=False)
    assert _read_bool_setting("phase1_probe_flag", default) is default


@needs_fix
def test_phase1_read_bool_setting_defaults_to_true():
    """The signature's own default — the gate relies on it."""
    assert inspect.signature(_read_bool_setting).parameters["default"].default is True


# --- 3. the truncation determination -----------------------------------------
#
# `run_stream` needs a DB session, a report and an LLM, so the determination is
# exercised through a mirror of the block that computes it
# (create_artifact.py, "Get data directly from step" -> `step_truncated`).
# The mirror is only worth anything because the source-anchored tests in
# section 4 below assert the real code still computes it the same way; read the
# two sections together.


def _resolve_rows_and_truncation(step_data):
    """Mirror of create_artifact.run_stream's row selection + truncation test.

    Returns (rows, rows_total, step_truncated).
    """
    rows = []
    if step_data:
        wide_rows = step_data.get("rows_artifact")
        if isinstance(wide_rows, list) and len(wide_rows) > len(step_data.get("rows") or []):
            rows = wide_rows
        else:
            rows = step_data.get("rows") or []
    step_info = (step_data.get("info") or {}) if step_data else {}
    step_total_rows = step_info.get("total_rows")
    rows_total = (
        int(step_total_rows)
        if isinstance(step_total_rows, (int, float)) and int(step_total_rows) >= len(rows)
        else len(rows)
    )
    return rows, rows_total, rows_total > len(rows)


def _step(preview, total, artifact=None, stored_flag=None):
    """A persisted step's `data` blob, as create_data writes it."""
    data = {
        "rows": [{"period": f"P{i:03d}", "amount": i} for i in range(preview)],
        "columns": [{"field": "period"}, {"field": "amount"}],
        "info": {"total_rows": total, "column_info": {}},
    }
    if artifact is not None:
        data["rows_artifact"] = [
            {"period": f"P{i:03d}", "amount": i} for i in range(artifact)
        ]
    if stored_flag is not None:
        data["rows_truncated"] = stored_flag
    return data


def test_phase1_preview_prefix_of_a_bigger_dataset_is_truncated():
    """The shape that shipped the wrong KPI: 1,000 rows of a 1,903-row dataset."""
    rows, total, truncated = _resolve_rows_and_truncation(_step(1000, 1903))
    assert len(rows) == 1000
    assert total == 1903
    assert truncated is True


def test_phase1_complete_dataset_is_not_truncated():
    rows, total, truncated = _resolve_rows_and_truncation(_step(420, 420))
    assert (len(rows), total, truncated) == (420, 420, False)


def test_phase1_artifact_width_rows_are_preferred_over_the_preview():
    """Phase 2 stores a wider copy; the gate must judge THAT copy."""
    rows, total, truncated = _resolve_rows_and_truncation(
        _step(1000, 6400, artifact=6400)
    )
    assert len(rows) == 6400
    assert truncated is False


def test_phase1_stale_stored_flag_does_not_make_a_complete_dataset_truncated():
    """THE regression this section exists for.

    `rows_truncated` on the step describes the PREVIEW copy. Once the
    artifact-width copy is in use it says nothing about the rows in hand, and
    trusting it would refuse a dashboard whose data is complete.
    """
    _, _, truncated = _resolve_rows_and_truncation(
        _step(1000, 6400, artifact=6400, stored_flag=True)
    )
    assert truncated is False


def test_phase1_stale_stored_flag_does_not_hide_a_real_truncation():
    """The mirror image: a False stored flag must not suppress the refusal."""
    _, _, truncated = _resolve_rows_and_truncation(
        _step(1000, 6400, artifact=4000, stored_flag=False)
    )
    assert truncated is True


def test_phase1_artifact_copy_still_short_of_the_total_is_truncated():
    rows, total, truncated = _resolve_rows_and_truncation(
        _step(1000, 52_000, artifact=10_000)
    )
    assert (len(rows), total, truncated) == (10_000, 52_000, True)


def test_phase1_missing_total_rows_is_not_truncation():
    """No true count recorded — assume what we hold IS the dataset, don't refuse."""
    data = _step(300, 300)
    data["info"].pop("total_rows")
    rows, total, truncated = _resolve_rows_and_truncation(data)
    assert (total, truncated) == (300, False)


# NaN is deliberately absent: `int(float("nan"))` raises, in the real code as
# much as in this mirror, so pinning it here would pin a crash rather than a
# contract.
@pytest.mark.parametrize("bogus", [None, "1903", {}, [], -5])
def test_phase1_unusable_total_rows_never_manufactures_truncation(bogus):
    data = _step(300, 300)
    data["info"]["total_rows"] = bogus
    _, total, truncated = _resolve_rows_and_truncation(data)
    assert total == 300
    assert truncated is False


def test_phase1_total_below_the_rows_in_hand_is_ignored():
    """A stale, smaller total cannot mean "we hold more than everything"."""
    _, total, truncated = _resolve_rows_and_truncation(_step(500, 120))
    assert (total, truncated) == (500, False)


def test_phase1_float_total_rows_is_accepted():
    _, total, truncated = _resolve_rows_and_truncation(_step(100, 250.0))
    assert (total, truncated) == (250, True)


@pytest.mark.parametrize("empty", [None, {}, {"rows": []}, {"rows": None}])
def test_phase1_empty_step_data_is_safe(empty):
    rows, total, truncated = _resolve_rows_and_truncation(empty)
    assert rows == []
    assert (total, truncated) == (0, False)


def test_phase1_artifact_key_that_is_not_a_list_is_ignored():
    rows, _, _ = _resolve_rows_and_truncation(
        {"rows": [{"a": 1}], "rows_artifact": "not-a-list", "info": {"total_rows": 1}}
    )
    assert rows == [{"a": 1}]


def test_phase1_shorter_artifact_copy_never_replaces_the_preview():
    """`rows_artifact` is only ever an improvement; a smaller one is nonsense."""
    rows, _, _ = _resolve_rows_and_truncation(_step(900, 900, artifact=100))
    assert len(rows) == 900


# --- 4. the gate's wiring inside run_stream ----------------------------------
#
# These read source text. They are here because the gate sits inside an async
# generator that cannot be driven without a DB, a report and an LLM, and a
# contract nobody checks is a contract that quietly disappears in the next
# refactor. They pin WIRING, not behavior — the behavior above is what matters.
#
# Deliberately NOT marked `needs_fix`: they need no new symbol, so on pre-fix
# code they FAIL rather than skip, which is the whole point of them.

_RUN_STREAM_SRC = inspect.getsource(CreateArtifactTool.run_stream)


def test_phase1_run_stream_consults_the_gate_flag():
    assert "_completeness_gate_enabled()" in _RUN_STREAM_SRC


def test_phase1_truncation_is_derived_from_the_rows_in_hand():
    assert "step_truncated = rows_total > len(rows)" in _RUN_STREAM_SRC


def test_phase1_the_steps_own_truncation_flag_is_never_consulted():
    """It describes the PREVIEW copy. Reading it back is the stale-flag defect."""
    for stale_read in (
        'step_data.get("rows_truncated")',
        "step_data.get('rows_truncated')",
        'step_info.get("rows_truncated")',
        "step_info.get('rows_truncated')",
    ):
        assert stale_read not in _RUN_STREAM_SRC


def test_phase1_refusal_is_a_failure_not_a_warning():
    """A warning is what DEF-004 already shipped, and the model built anyway."""
    assert '"incomplete_visualization_data"' in _RUN_STREAM_SRC
    assert '"truncated_visualizations"' in _RUN_STREAM_SRC
    assert '"success": False' in _RUN_STREAM_SRC


def test_phase1_refusal_tells_the_agent_what_to_do_instead():
    """Refusing without a next move just costs the same generation elsewhere."""
    src = _RUN_STREAM_SRC
    assert "PRE-AGGREGATED" in src
    assert "create_data" in src
    assert "NOT retry unchanged" in src
