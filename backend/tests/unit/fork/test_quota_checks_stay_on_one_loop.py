"""A quota check must never touch the database from a throwaway event loop.

Heavy work runs the model and the code executor in worker threads
(`asyncio.to_thread`). Those threads call quota checks — `llm.py:891`,
`code_execution.py` three times — through `UsageLimitContext.run_blocking`,
which did this:

    if self.loop and self.loop.is_running():
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)      # ← a brand-new event loop

★And not one of the four places that construct the context ever passed
`loop`, so `self.loop` was always None and that fallback was the ONLY path a
worker thread ever took.

The coroutine then opens a session on the application's SHARED asyncpg pool.
That connection's protocol object is now bound to a loop that dies a moment
later, and the pool discovers it afterwards:

    RuntimeError: Task ... got Future ... attached to a different loop
    asyncpg.exceptions._base.InternalClientError: got result for unknown
        protocol state 3

Observed twice during a three-user test where each person ran a dashboard, a
chat and a slide deck at the same time. No request failed — the damage is to
pooled connections, which is why it never showed up in single-user work.

The same hazard is already half-known: `agent_v2.py` carries two comments
explaining that the Judge deliberately does NOT receive a usage context,
because routing its check through `run_blocking` raised "Lock is bound to a
different event loop". Those workarounds are a symptom of this bug.
"""
import asyncio
import inspect
import threading

import pytest

from app.services.usage_policy_service import UsageLimitContext


def _ctx(**kw):
    base = dict(organization_id="org", user_id="user", source="test")
    base.update(kw)
    return UsageLimitContext(**base)


# --- the loop is captured where the context is born ------------------------

def test_a_context_built_inside_the_app_remembers_that_loop():
    """★The fix. Every real construction happens in async code, so the loop is
    always there to be captured — nobody had to pass it, and nobody did."""
    async def build():
        return _ctx(), asyncio.get_running_loop()

    ctx, loop = asyncio.run(build())
    assert ctx.loop is loop


def test_an_explicit_loop_still_wins():
    """Passing one must keep working — `for_source` forwards the parent's."""
    loop = asyncio.new_event_loop()
    try:
        assert _ctx(loop=loop).loop is loop
    finally:
        loop.close()


def test_a_derived_context_inherits_the_loop():
    async def build():
        return _ctx().for_source("coder"), asyncio.get_running_loop()

    child, loop = asyncio.run(build())
    assert child.loop is loop


def test_a_context_built_with_no_loop_at_all_is_honest_about_it():
    """A plain synchronous caller — a script, a test — has no loop to capture
    and must not invent one."""
    assert _ctx().loop is None


# --- the dangerous path is gone -------------------------------------------

def test_a_worker_thread_runs_the_check_on_the_apps_own_loop():
    """★The heart of it. From a worker thread, the check must be handed BACK
    to the loop that owns the connection pool — never run on a new one."""
    seen = {}

    async def check():
        seen["ran_on"] = asyncio.get_running_loop()

    async def main():
        ctx = _ctx()
        app_loop = asyncio.get_running_loop()
        await asyncio.to_thread(ctx.run_blocking, check())
        return app_loop

    app_loop = asyncio.run(main())
    assert seen["ran_on"] is app_loop, "the check ran on a loop of its own"


def test_the_worker_thread_result_comes_back():
    """Not just correct — still useful. `run_blocking` must return the value."""
    async def check():
        return "quota-ok"

    async def main():
        return await asyncio.to_thread(_ctx().run_blocking, check())

    assert asyncio.run(main()) == "quota-ok"


def test_a_raise_inside_the_check_still_reaches_the_caller():
    """UsageLimitExceeded is the whole point of the call — it must propagate
    across the thread hand-off, not vanish."""
    class Boom(Exception):
        pass

    async def check():
        raise Boom("over limit")

    async def main():
        return await asyncio.to_thread(_ctx().run_blocking, check())

    with pytest.raises(Boom):
        asyncio.run(main())


def test_calling_it_from_the_owning_loop_is_refused_not_deadlocked():
    """★Handing a coroutine to the loop you are already sitting on schedules
    work you then block waiting for — a deadlock with no error, no timeout and
    no log entry.

    This branch was unreachable while `self.loop` was always None. Capturing
    the loop made it live, and this test HUNG the whole suite until the
    refusal was moved ahead of the hand-off. Hence the watchdog: a regression
    here must fail, not hang."""
    async def check():
        return 1

    result = {}

    def run():
        async def main():
            try:
                _ctx().run_blocking(check())
                result["outcome"] = "returned — no refusal"
            except RuntimeError:
                result["outcome"] = "refused"
        asyncio.run(main())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "run_blocking deadlocked on its own loop"
    assert result.get("outcome") == "refused"


def test_a_plain_synchronous_caller_still_works():
    """Scripts and tests have no app loop and no shared pool to corrupt, so
    running the coroutine directly is safe there and must keep working."""
    async def check():
        return "sync-ok"

    assert _ctx().run_blocking(check()) == "sync-ok"


# --- the shape ------------------------------------------------------------

def test_the_throwaway_loop_is_never_used_when_a_loop_is_bound():
    """★Guard against a future 'simplification' putting the fallback back in
    front of the hand-off."""
    src = inspect.getsource(UsageLimitContext.run_blocking)
    # Judge the code: the docstring names the call while explaining the order.
    body = src[src.index('"""', src.index('"""') + 3) + 3:]
    code = "\n".join(l.split("#", 1)[0] for l in body.splitlines())
    i_thread = code.index("run_coroutine_threadsafe")
    assert "asyncio.run(" not in code[:i_thread], (
        "the throwaway loop is reached before the hand-off"
    )


def test_the_capture_is_wired_into_construction():
    src = inspect.getsource(UsageLimitContext)
    assert "__post_init__" in src
    assert "get_running_loop" in src
