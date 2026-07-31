"""One switch above all the agents, and one budget shared between them.

Per-agent Auto learn answered "should this agent keep itself current?". It did
not answer the two questions that follow immediately:

* **is anything stale?** — which is about the SET of agents, and today can only
  be asked one agent at a time, so in practice is never asked at all;
* **what is this costing?** — twelve agents at four runs each is forty-eight
  model calls a day that nobody agreed to. A per-agent ceiling looks tidier and
  hides exactly the number a person cares about.

So the organisation gets a master switch and one shared ceiling, and the agent
keeps its opt-out. The master switch wins: turning it off has to stop everything
at once, without visiting each agent in turn.
"""
import inspect
import textwrap

import pytest

from app.services import auto_learn


def _src(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


# ── the master switch ───────────────────────────────────────────────────────

def test_the_organisation_switch_is_off_by_default():
    """Nothing should begin spending because an installation was upgraded."""
    from app.schemas.organization_settings_schema import OrganizationSettingsConfig

    assert OrganizationSettingsConfig().auto_learn.enabled is False


def test_an_unreadable_policy_leaves_it_switched_off():
    """Failing open here would mean a settings row that cannot be read results
    in unbudgeted model calls. Off is the only safe direction."""
    src = _src(auto_learn.org_policy)
    tail = src[src.rindex("return"):]
    assert '"enabled": False' in tail


def test_the_switch_is_checked_before_any_agent_runs():
    """An agent may be opted in individually and still not run."""
    src = _src(auto_learn.sweep_auto_learn)
    assert 'policy["enabled"]' in src
    assert src.index('policy["enabled"]') < src.index("auto_learn_agent(")


def test_the_policy_is_read_once_per_organisation():
    """A sweep over many agents must not re-read the same settings row for each
    of them."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "policies" in src and "if oid not in policies" in src


# ── the shared ceiling ──────────────────────────────────────────────────────

def test_the_ceiling_counts_every_agent_together():
    """The total is the number that matters. Counting per agent hides it."""
    src = _src(auto_learn.runs_today)
    assert "organization_id" in src
    assert "auto_runs" in src


def test_reaching_the_ceiling_is_announced():
    """A silent stop and a broken sweep look identical from outside — the
    failure shape this whole area kept producing."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "daily limit reached" in src


def test_a_run_is_counted_as_it_happens():
    """Counting only at the start of the sweep would let a single pass spend
    well past the ceiling before anything noticed."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "spent[oid] += 1" in src


def test_the_default_ceiling_is_a_day_not_a_tick():
    from app.schemas.organization_settings_schema import OrganizationSettingsConfig

    cfg = OrganizationSettingsConfig().auto_learn
    assert cfg.max_runs_per_day >= 1
    assert cfg.quiet_minutes >= 1


# ── the overview ────────────────────────────────────────────────────────────

def test_the_overview_answers_the_set_question():
    """"Is anything stale?" has to be answerable in one request, or it is not
    answerable in practice."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.get_auto_learn_overview)
    for field in ('"stale"', '"watched"', '"runs_today"', '"agents"', '"policy"'):
        assert field in src


def test_the_overview_needs_only_view():
    """Anyone who can see the agents can see whether they are current. Reading
    freshness is not an administrative act."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.get_auto_learn_overview)
    assert "view_data_sources" in src


def test_changing_the_policy_needs_manage_settings():
    """It decides what happens on everyone's behalf, including whether agents
    may spend model calls unasked."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_auto_learn)
    assert "manage_settings" in src


def test_the_policy_write_goes_through_the_settings_service():
    """★Writing an org setting by hand has broken the entire settings surface in
    this codebase: a partial object fails validation on every later read, and
    the page renders empty with "Failed to fetch settings"."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_auto_learn)
    assert "OrganizationSettingsService" in src
    assert "update_settings" in src


def test_unknown_fields_are_refused():
    """So a typo silently writes nothing rather than appearing to work."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_auto_learn)
    assert "allowed" in src
    assert "status_code=400" in src


def test_check_now_still_honours_the_budget():
    """"Check now" means run the tick early, not ignore the ceiling — otherwise
    the button is a way around the limit that exists to make this safe."""
    import app.routes.data_source as routes

    src = inspect.getsource(routes.run_auto_learn_now)
    assert "sweep_auto_learn" in src
    assert "auto_learn_agent" not in src, (
        "check-now calls the per-agent worker directly, bypassing the master "
        "switch and the daily ceiling that the sweep enforces"
    )
