"""`TimeoutPolicy.hard_timeout_s` has to actually end a run.

★Inherited from upstream, not introduced here — the same code is in
`origin/main` at the time of writing, so this is a fix to report back rather
than a fork regression.

`ToolRunner.run` sets the watchdog up like this:

    async def hard_timeout():
        await asyncio.sleep(...)
        raise asyncio.TimeoutError("hard timeout")

    hard_timer = asyncio.create_task(hard_timeout())

and never awaits `hard_timer`. A raise inside an un-awaited task is *stored on
the task*, not propagated to the code that created it, so the exception goes
nowhere — the only observable effect is a possible "Task exception was never
retrieved" warning when it is garbage collected. The ceiling has never fired.

It matters in one specific shape: a tool that keeps emitting events forever.
The idle timeout only fires on silence, so a chatty tool that never finishes is
bounded by nothing at all.
"""
import asyncio
import inspect

import pytest

from app.ai.runner.policies import RetryPolicy, TimeoutPolicy
from app.ai.runner.tool_runner import ToolRunner


class _ChattyTool:
    """Never finishes, and is never silent — so only the hard limit can stop it."""

    name = "chatty"
    input_model = None
    output_model = None
    spec = None
    metadata = None

    def __init__(self):
        self.events = 0

    async def run_stream(self, arguments, runtime_ctx):
        while True:
            self.events += 1
            yield {"type": "tool.progress", "payload": {"stage": "working"}}
            await asyncio.sleep(0.01)


def _runner(hard):
    return ToolRunner(
        retry=RetryPolicy(max_attempts=1),
        timeout=TimeoutPolicy(start_timeout_s=1, idle_timeout_s=1, hard_timeout_s=hard),
    )


@pytest.mark.asyncio
async def test_a_tool_that_never_stops_talking_is_still_ended():
    """★The case the ceiling exists for. Without it this call never returns."""
    tool = _ChattyTool()
    runner = _runner(hard=1)

    async def emit(_event):
        return None

    result = await asyncio.wait_for(
        runner.run(tool, {}, {"mode": "chat"}, emit), timeout=10
    )

    assert "error" in result
    assert result["error"]["type"] == "timeout_error"
    assert tool.events > 1, "the tool should have been talking, not silent"


@pytest.mark.asyncio
async def test_the_hard_limit_is_not_reached_by_a_tool_that_finishes():
    """The ceiling must not clip a normal run."""

    class _Quick:
        name = "quick"
        input_model = output_model = spec = metadata = None

        async def run_stream(self, arguments, runtime_ctx):
            yield {"type": "tool.end", "payload": {"observation": {"summary": "done"}}}

    async def emit(_event):
        return None

    result = await _runner(hard=5).run(_Quick(), {}, {"mode": "chat"}, emit)
    assert result["observation"]["summary"] == "done"


def test_the_watchdog_result_is_actually_consumed():
    """★The defect in one line: `create_task` on its own proves nothing. A task
    that raises into the void is indistinguishable from no watchdog at all, so
    the run loop has to look at it."""
    body = inspect.getsource(ToolRunner.run)
    assert "hard_timer" in body
    assert "hard_timer.done()" in body or "hard_timer.result()" in body, (
        "the hard-timeout task is created but its outcome is never observed — "
        "the ceiling cannot fire"
    )
