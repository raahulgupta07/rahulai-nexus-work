"""One clarifying question must render as one form with one Submit.

The incident showed the same clarify block twice — near-identical wording, two
Submit buttons, one turn. Three candidates were on the table: a double emit
from the tool, a frontend render, or a genuine second planner step.

★Diagnosed from the run rather than guessed. In the live database that run
(`agent_execution bdd8cc87-…`) has exactly **one** `clarify` row in
`tool_executions` and exactly **one** `completion_blocks` row pointing at it —
so the loop ran once, persisted once, and the duplicate never survived a
reload. It was a live-stream render.

The cause is in the kickoff paint. The planner emits `action.name` on
ToolUseStart, ~1s before `tool.started`, so the client paints a placeholder
`tool_execution` to render the widget immediately — and a *second*
`decision.partial` follows after ToolUseComplete carrying the full arguments.
The client addressed both by taking the last block in the list, and "the last
block" is not the same block at both moments: any `block.upsert` in between
moves it. Two blocks then each held a `clarify` placeholder, and `clarify`
renders as a form, so the user was asked the same thing twice.

The block id is known on the server the whole time — `current_block_id` is
generated before the planner runs, and its `block.upsert` is emitted from it.
It simply was never put in the decision payload. Both halves are pinned here:
the server names the block, the client addresses the one it names.
"""
import ast
import inspect
import pathlib
import re

import pytest

import app.ai.agent_v2 as agent_v2


REPO = pathlib.Path(__file__).resolve().parents[4]
REPORT_PAGE = REPO / "frontend" / "pages" / "reports" / "[id]" / "index.vue"


def _sse_payloads():
    """Every `data={...}` literal handed to an SSEEvent in agent_v2, keyed by
    the event name in the same call.

    Read from the AST rather than by regex: these are 40-line calls inside a
    deeply nested loop, and a text search across them cannot tell which `data`
    belongs to which `event`.
    """
    tree = ast.parse(inspect.getsource(agent_v2))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != "SSEEvent":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        event = kwargs.get("event")
        data = kwargs.get("data")
        if not isinstance(event, ast.Constant) or not isinstance(data, ast.Dict):
            continue
        keys = {k.value for k in data.keys if isinstance(k, ast.Constant)}
        found.setdefault(event.value, []).append(keys)
    return found


PAYLOADS = _sse_payloads()


# --- the server names the block --------------------------------------------

@pytest.mark.parametrize("event", ["decision.partial", "decision.final"])
def test_the_decision_event_carries_its_block_id(event):
    """★Without this the client has nothing to address and must guess."""
    assert event in PAYLOADS, f"no SSEEvent(event={event!r}) found at all"
    for keys in PAYLOADS[event]:
        assert "block_id" in keys, (
            f"{event} is emitted without block_id — the client falls back to "
            "'the last block', which is what asked the question twice"
        )


def test_the_block_upsert_still_carries_the_same_id():
    """Guard the pairing: the id the decision names has to be an id the client
    has already seen a block for."""
    source = inspect.getsource(agent_v2)
    assert "_pre_block_id = str(_uuid_mod.uuid4())" in source
    assert "current_block_id = _pre_block_id" in source


# --- the client addresses it ------------------------------------------------

@pytest.fixture(scope="module")
def report_page():
    if not REPORT_PAGE.exists():  # pragma: no cover - see the /src runner note
        pytest.skip("frontend source not present in this environment")
    return REPORT_PAGE.read_text(encoding="utf-8")


def _decision_handler(source: str) -> str:
    """The body of the `decision.partial` / `decision.final` case."""
    start = source.index("case 'decision.partial':")
    end = source.index("case 'context.compacted':", start)
    return source[start:end]


def test_the_decision_handler_does_not_take_the_last_block(report_page):
    """★The defect in one line. `completion_blocks[length - 1]` is a guess, and
    it was wrong precisely when two events described one decision."""
    handler = _decision_handler(report_page)
    assert "completion_blocks.length - 1" not in handler, (
        "the decision handler still addresses 'the last block'"
    )


def test_the_decision_handler_resolves_the_named_block(report_page):
    handler = _decision_handler(report_page)
    assert handler.count("resolveToolEventBlock(sysMessage, payload)") >= 2, (
        "both the plan_decision update and the tool kickoff must resolve the "
        "block the payload names"
    )


def test_the_resolver_prefers_the_named_block(report_page):
    """`resolveToolEventBlock` is only a fix if block_id wins over the fallback."""
    body = report_page[report_page.index("function resolveToolEventBlock"):]
    body = body[: body.index("\n}\n")]
    assert body.index("payload?.block_id") < body.index("blocks[blocks.length - 1]")


def test_a_finished_block_is_never_given_a_tool_placeholder(report_page):
    """Second line of defence, kept deliberately: against an older server that
    sends no block_id, this degrades to a placeholder missing for ~1s instead
    of to a second question."""
    handler = _decision_handler(report_page)
    assert "alreadyDone" in handler
    assert re.search(r"if \(!\w+\.tool_execution && !alreadyDone\)", handler), (
        "the kickoff paint no longer checks whether the block is already finished"
    )


# --- what must NOT change ---------------------------------------------------

def test_the_clarify_text_is_still_written_for_non_ui_readers(report_page):
    """The tool's `final_answer` is a flattened copy of its own question, and
    that is deliberate — Slack, the shared view and the console render text,
    not the form. The report page must keep suppressing it, since there it
    would be the duplicate."""
    from app.ai.tools.implementations.clarify import ClarifyTool  # noqa: F401
    src = inspect.getsource(inspect.getmodule(ClarifyTool))
    assert '" / ".join(q.options)' in src, "the plain-text fallback was removed"
    assert "block.tool_execution?.tool_name !== 'clarify'" in report_page, (
        "the report page must not render the text copy beside the form"
    )
