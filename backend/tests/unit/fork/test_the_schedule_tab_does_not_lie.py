"""The Schedule tab may only claim a thing runs by itself when it does.

The tab exists to answer one question — what runs without me? — and it was
answering it wrong for the most common case. `runs_when` was:

    "signin" if ds_id in per_user_ds else "auto_learn"

so every agent that is not a per-user Microsoft connector was labelled
"Auto learn", whether or not Auto learn was switched on for the organisation and
whatever that agent's own mode was set to. `auto_learn.py:213` sweeps only
agents whose `training_drift.mode_of(a) == MODE_AUTO`, and `:240` bails when the
org switch is off. So a member with the switch off, or an agent left on
`manual`, was told something would run overnight that never would.

★A schedule screen that is wrong about what is scheduled is worse than no
schedule screen: the member stops checking, and finds out when the data is
stale. The whole reason the per-user case is spelled out here is that people had
already been burned assuming a sync happened on its own.

★Read-only, no schema — `tests/unit/fork`. See CLAUDE.md.
"""
import inspect
import json
import re
from pathlib import Path

from app.services import keeper_service, training_drift

REPO = Path(__file__).resolve().parents[4]
EN = REPO / "locales" / "en.json"
COMPOSABLE = REPO / "frontend" / "composables" / "useKeeper.ts"
SCREEN = REPO / "frontend" / "components" / "KeeperScreen.vue"

SRC = inspect.getsource(keeper_service.KeeperService.schedule)

# Every value the backend can emit. The screen renders `keeper.runsWhen.<value>`
# directly, so a value with no key renders the key itself to the member.
STATES = ("signin", "auto_learn", "manual")


def _locale():
    return json.loads(EN.read_text(encoding="utf-8"))


def test_auto_learn_is_claimed_only_when_both_switches_are_on():
    """The org switch AND the agent's own mode. Either one off means it does not
    run by itself, and the tab must not say otherwise."""
    assert 'policy.get("enabled")' in SRC, (
        "the org Auto learn switch is not consulted — the tab will claim a "
        "schedule for an organisation that has the sweep turned off"
    )
    assert "MODE_AUTO" in SRC, (
        "the agent's own training mode is not consulted — an agent left on "
        "manual or notify will be reported as Auto learn"
    )


def test_the_old_unconditional_label_is_gone():
    """★The exact expression that caused this. It is short, it reads as
    reasonable, and it is the obvious thing to write again."""
    collapsed = re.sub(r"\s+", " ", SRC)
    assert 'else "auto_learn"' not in collapsed, (
        "runs_when falls back to auto_learn unconditionally again"
    )


def test_a_per_user_agent_is_still_never_called_scheduled():
    """The fact the tab was built for. A per-user connector has no timer at all
    — it runs against the member's own Microsoft token, and there is nobody to
    borrow that from at 3am."""
    assert '"signin"' in SRC
    assert "PER_USER_TOKEN_TYPES" in SRC


def test_every_state_the_backend_emits_has_a_string():
    """★vue-i18n renders the KEY when it is missing, so a member would read
    `keeper.runsWhen.manual` on the screen. Checked mechanically for that."""
    runs_when = _locale()["keeper"]["runsWhen"]
    for state in STATES:
        assert isinstance(runs_when.get(state), str) and runs_when[state].strip(), (
            f"keeper.runsWhen.{state} is missing — the member sees the key"
        )
    assert set(runs_when) == set(STATES), (
        f"locale and backend disagree on the state set: {sorted(runs_when)} vs "
        f"{sorted(STATES)}"
    )


def test_the_frontend_type_admits_every_state():
    """A narrower union than the backend emits is a silent type lie — the value
    still arrives, nothing catches it, and only the rendering is wrong."""
    ts = COMPOSABLE.read_text(encoding="utf-8")
    line = next(l for l in ts.splitlines() if "runs_when:" in l)
    for state in STATES:
        assert f"'{state}'" in line, f"{state} missing from the runs_when type"


def test_the_dead_see_all_key_stays_dead():
    """★Removed 2026-08-03 with the popover it belonged to. The button opens the
    history in one click now, so nothing links to it. An unused locale string is
    a translator's time spent on a screen nobody will see."""
    assert "seeAll" not in _locale()["keeper"], "the orphan key is back"
    for path in (SCREEN, COMPOSABLE, REPO / "frontend" / "components" / "KeeperButton.vue"):
        assert "keeper.seeAll" not in path.read_text(encoding="utf-8")


def test_the_modes_this_depends_on_still_exist():
    """If `training_drift` renames its modes, the checks above pass while the
    comparison silently stops matching anything."""
    assert training_drift.MODE_AUTO in training_drift.VALID_MODES
    assert training_drift.mode_of(object()) == training_drift.DEFAULT_MODE
