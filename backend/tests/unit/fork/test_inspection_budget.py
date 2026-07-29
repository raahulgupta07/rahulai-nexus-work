"""A question cannot spend unbounded time exploring (defect 3).

The bound is on cumulative inspection wall time, not on a call count, and it
sits above the worst observed *good* run on purpose — see the module docstring
of app/ai/inspection_budget.py.
"""

import pytest

from app.ai.inspection_budget import (
    DEFAULT_INSPECTION_BUDGET_MS,
    InspectionBudget,
    resolve_budget_ms,
)


def test_a_fresh_run_has_its_whole_budget():
    b = InspectionBudget()
    assert not b.exhausted
    assert b.spent_ms == 0
    assert b.remaining_ms == b.budget_ms


def test_only_inspection_tools_are_charged():
    b = InspectionBudget(1000)
    b.record("create_data", 999_999)
    b.record("describe_tables", 999_999)
    b.record("read_instruction", 999_999)
    assert b.spent_ms == 0
    assert b.calls == 0
    assert not b.exhausted


def test_inspection_time_accumulates_across_calls():
    b = InspectionBudget(10_000)
    b.record("inspect_data", 4_000)
    b.record("inspect_data", 3_000)
    assert b.spent_ms == 7_000
    assert b.calls == 2
    assert not b.exhausted


def test_the_budget_trips_once_the_time_is_spent():
    b = InspectionBudget(10_000)
    b.record("inspect_data", 6_000)
    assert not b.exhausted
    b.record("inspect_data", 6_000)
    assert b.exhausted
    assert b.remaining_ms == 0


def test_exactly_at_the_limit_counts_as_spent():
    b = InspectionBudget(5_000)
    b.record("inspect_data", 5_000)
    assert b.exhausted


def test_many_cheap_inspections_are_not_punished():
    # A count-based cap would stop this run; a time budget correctly does not.
    b = InspectionBudget(180_000)
    for _ in range(20):
        b.record("inspect_data", 2_000)
    assert b.calls == 20
    assert not b.exhausted


def test_two_expensive_inspections_can_still_be_afforded():
    # The 277s cross-source run spent ~140s over two calls and answered well.
    # Capping it would trade a large quality loss for a small latency win.
    b = InspectionBudget()
    b.record("inspect_data", 68_437)
    b.record("inspect_data", 71_927)
    assert not b.exhausted, "the default bound must not truncate a converging run"


def test_runaway_exploration_is_stopped():
    b = InspectionBudget()
    for _ in range(10):
        b.record("inspect_data", 70_000)
    assert b.exhausted


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan")])
def test_an_unusable_duration_never_crashes_the_run(bad):
    b = InspectionBudget(10_000)
    b.record("inspect_data", bad)
    assert b.calls == 1  # the call still happened
    assert not b.exhausted


def test_a_negative_duration_is_ignored():
    b = InspectionBudget(10_000)
    b.record("inspect_data", -5_000)
    assert b.spent_ms == 0


def test_a_non_positive_budget_cannot_disable_inspection_outright():
    for value in (0, -1):
        b = InspectionBudget(value)
        assert b.budget_ms >= 1
        assert not b.exhausted


def test_a_junk_budget_falls_back_to_the_default():
    assert InspectionBudget("nonsense").budget_ms == DEFAULT_INSPECTION_BUDGET_MS
    assert InspectionBudget(None).budget_ms == DEFAULT_INSPECTION_BUDGET_MS


def test_tracks_identifies_the_inspection_tools():
    b = InspectionBudget()
    assert b.tracks("inspect_data")
    assert not b.tracks("create_artifact")
    assert not b.tracks(None)


def test_the_notice_tells_the_planner_what_to_do_next():
    b = InspectionBudget(10_000)
    b.record("inspect_data", 12_000)
    notice = b.notice()
    assert "12s" in notice
    assert "1 inspection call" in notice
    assert "answer" in notice.lower()


def test_as_dict_reports_the_run_state():
    b = InspectionBudget(10_000)
    b.record("inspect_data", 4_000)
    assert b.as_dict() == {
        "spent_ms": 4_000,
        "budget_ms": 10_000,
        "calls": 1,
        "exhausted": False,
    }


# --- org override ----------------------------------------------------------

class FakeCfg:
    def __init__(self, value):
        self.value = value


class FakeSettings:
    def __init__(self, value, raises=False):
        self._value = value
        self._raises = raises

    def get_config(self, name):
        if self._raises:
            raise RuntimeError("settings unavailable")
        return FakeCfg(self._value)


def test_no_settings_means_the_default():
    assert resolve_budget_ms(None) == DEFAULT_INSPECTION_BUDGET_MS


def test_settings_failure_means_the_default():
    assert resolve_budget_ms(FakeSettings(None, raises=True)) == DEFAULT_INSPECTION_BUDGET_MS


def test_an_org_can_tune_the_bound():
    assert resolve_budget_ms(FakeSettings(240_000)) == 240_000


def test_a_stored_value_can_neither_disable_nor_unbound_inspection():
    assert resolve_budget_ms(FakeSettings(1)) == 30_000
    assert resolve_budget_ms(FakeSettings(10_000_000)) == 900_000


@pytest.mark.parametrize("bad", [None, "", "abc", [], {}])
def test_a_junk_setting_falls_back_to_the_default(bad):
    assert resolve_budget_ms(FakeSettings(bad)) == DEFAULT_INSPECTION_BUDGET_MS


@pytest.mark.parametrize("flag", [True, False])
def test_a_boolean_setting_is_not_read_as_a_duration(flag):
    # "off" and True are both truthy-adjacent traps; a flag is not a budget.
    assert resolve_budget_ms(FakeSettings(flag)) == DEFAULT_INSPECTION_BUDGET_MS


# ---------------------------------------------------------------------------
# ★ The bound is only tunable if the settings schema DECLARES it.
#
# `resolve_budget_ms` has always read `agent_inspection_budget_ms`, and its
# docstring said the bound is "tunable without a code change". It was not: the
# key was absent from OrganizationSettingsConfig, so the validating settings API
# would not carry it, and the only remaining route — raw SQL into the settings
# blob — omits FeatureConfig's required `description` and makes every later
# settings read return 500. A setting nobody can set is not a setting.
# ---------------------------------------------------------------------------


def test_the_budget_is_a_declared_setting():
    from app.schemas.organization_settings_schema import OrganizationSettingsConfig

    cfg = OrganizationSettingsConfig()
    entry = getattr(cfg, "agent_inspection_budget_ms", None)
    assert entry is not None, (
        "the budget is read from settings but not declared, so it can only be "
        "written by raw SQL — which breaks the whole settings surface"
    )
    assert entry.description, "FeatureConfig requires a description or reads 500"
    assert entry.editable is True, "declared but not editable is still untunable"


def test_the_declared_default_matches_the_code_default():
    """Two defaults that can drift are a defect waiting to happen: the settings
    page would advertise one bound while the agent enforced another."""
    from app.schemas.organization_settings_schema import OrganizationSettingsConfig
    from app.ai.inspection_budget import DEFAULT_INSPECTION_BUDGET_MS

    declared = OrganizationSettingsConfig().agent_inspection_budget_ms.value
    assert int(declared) == DEFAULT_INSPECTION_BUDGET_MS


def test_the_declared_default_survives_a_round_trip_through_resolve():
    """The declared entry must be readable by the resolver that consumes it —
    the two have to agree on the shape, not only the name."""
    from app.schemas.organization_settings_schema import OrganizationSettingsConfig
    from app.ai.inspection_budget import resolve_budget_ms, DEFAULT_INSPECTION_BUDGET_MS

    cfg = OrganizationSettingsConfig()

    class _Settings:
        def get_config(self, key):
            return getattr(cfg, key, None)

    assert resolve_budget_ms(_Settings()) == DEFAULT_INSPECTION_BUDGET_MS
