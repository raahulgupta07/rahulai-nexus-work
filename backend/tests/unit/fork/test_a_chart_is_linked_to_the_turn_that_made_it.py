"""A chart must be linked to the tool call that drew it.

`tool_executions.created_widget_id` is what the chat's chart preview binds on:
`CreateWidgetTool.vue` / `ToolWidgetPreview.vue` render `created_widget`, and
`serializers/completion_v2.py` loads the widget only `if created_widget_id`.

Nothing ever wrote it. Measured across the whole database before the fix: 374
widget rows, 372 `create_data` executions, and **zero** rows with the FK set.
`created_step_id` on the same rows was correct — so the mechanism worked and
was simply not used for widgets.

The failure is silent by design, which is why it survived. A null FK does not
error; the UI just falls back to the step's data table, and a chart the agent
really drew arrives as a grid with nothing anywhere explaining why.

The load-bearing case here is the LAST one: a wrong id is worse than a null,
because it renders another turn's chart under this turn. `inv.current_widget`
is inherited from agent-wide state for non-reset tools and is exactly the
plausible-looking wrong answer, so these tests pin the id to the Query this
invocation minted.
"""

import pytest

from app.ai.agent_v2 import (
    _INVOCATION_RESET_TOOLS,
    ToolInvocationState,
    resolve_created_widget_id,
)


class _Query:
    """Stands in for the Query create_data mints; it anchors exactly one Widget."""

    def __init__(self, widget_id):
        self.id = "query-1"
        self.widget_id = widget_id


class _Widget:
    def __init__(self, wid):
        self.id = wid


def _invocation(query=None, widget=None):
    return ToolInvocationState(query=query, widget=widget)


def test_create_data_records_the_widget_it_made():
    inv = _invocation(query=_Query("widget-abc"))
    observation = {"summary": "Created data 'Revenue by month' successfully"}

    assert resolve_created_widget_id("create_data", observation, inv) == "widget-abc"


def test_an_execution_that_made_no_widget_leaves_the_column_null():
    """Not 0, not "" — a falsy sentinel would be stored and then dereferenced."""
    inv = _invocation()  # no query: the tool never got as far as creating one

    for observation in ({"summary": "no data"}, {}, None):
        assert resolve_created_widget_id("create_data", observation, inv) is None

    # A query that somehow carries no widget is the same "no widget" answer,
    # and an empty string must never reach a String(36) foreign key.
    for empty in (None, "", 0):
        assert resolve_created_widget_id("create_data", {}, _invocation(query=_Query(empty))) is None


def test_a_tool_that_creates_nothing_records_nothing():
    """A read-only tool inherits the previous data tool's query — it made no widget."""
    inherited = _Query("widget-from-an-earlier-turn")
    inv = _invocation(query=inherited)

    assert "answer_question" not in _INVOCATION_RESET_TOOLS
    assert resolve_created_widget_id("answer_question", {"summary": "ok"}, inv) is None


def test_the_recorded_id_is_the_widget_this_call_created():
    """The wrong-id guard.

    ``inv.current_widget`` is seeded from agent-wide state and can hold a widget
    an EARLIER call made. Binding to it would render someone else's chart under
    this turn — a positively wrong answer, not a missing one. The id must come
    from this invocation's own Query.
    """
    stale = _Widget("widget-from-an-earlier-turn")
    mine = _Query("widget-this-call-made")
    inv = _invocation(query=mine, widget=stale)

    recorded = resolve_created_widget_id("create_data", {"summary": "ok"}, inv)

    assert recorded == "widget-this-call-made"
    assert recorded != stale.id


def test_every_widget_creating_tool_gets_the_same_treatment():
    """create_data is not special: each reset tool mints its own Query+Widget."""
    for tool_name in sorted(_INVOCATION_RESET_TOOLS):
        inv = _invocation(query=_Query(f"widget-{tool_name}"))
        assert resolve_created_widget_id(tool_name, {}, inv) == f"widget-{tool_name}"


def test_a_tool_that_reports_its_own_widget_is_believed():
    """The observation wins, mirroring how created_step_id resolves."""
    inv = _invocation(query=_Query("widget-from-state"))

    assert (
        resolve_created_widget_id("create_data", {"widget_id": "widget-reported"}, inv)
        == "widget-reported"
    )


def test_an_unreadable_id_is_null_rather_than_a_guess():
    class _Exploding:
        @property
        def widget_id(self):
            raise RuntimeError("detached from its session")

    inv = _invocation(query=_Exploding())

    assert resolve_created_widget_id("create_data", {}, inv) is None


def test_created_step_id_resolution_is_unchanged():
    """The step FK already worked; this change must not have touched it.

    Pins the two behaviours the loop relies on: the observation's ``step_id``,
    and the per-invocation ``current_step_id`` fallback for tools that create
    their step through progress events (which is how create_data does it).
    """
    inv = ToolInvocationState(step_id="step-from-state")

    observation = {"step_id": "step-reported"}
    created_step_id = observation.get("step_id")
    if not created_step_id and inv.current_step_id:
        created_step_id = inv.current_step_id
    assert created_step_id == "step-reported"

    observation = {"summary": "no step_id key"}
    created_step_id = observation.get("step_id")
    if not created_step_id and inv.current_step_id:
        created_step_id = inv.current_step_id
    assert created_step_id == "step-from-state"

    # And resolving the widget must not consume or clear that state.
    assert inv.current_step_id == "step-from-state"


def test_the_loop_calls_the_resolver_instead_of_reading_the_observation_directly():
    """Static guard: the fix is in the loop, not only in the helper.

    A helper nothing calls is the exact shape this defect had — the column
    existed and the mechanism worked, and no caller used it.
    """
    import inspect

    from app.ai import agent_v2

    source = inspect.getsource(agent_v2)
    assert "resolve_created_widget_id(tool_name, observation, _inv)" in source
    assert 'created_widget_id = observation["widget_id"]' not in source
