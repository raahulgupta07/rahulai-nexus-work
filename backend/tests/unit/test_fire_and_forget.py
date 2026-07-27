"""`spawn()` must hold a strong reference to a background task until it ends.

The bug being guarded: the event loop holds only a WEAK reference to a task, so
a bare `asyncio.create_task(...)` whose handle is discarded can be collected
while suspended at an await point — it stops silently, no exception, no log.
Our ORM event hooks (websocket broadcasts, Slack senders) are exactly that
shape, and the longer they await the likelier they vanish.

NOTE ON WHAT IS *NOT* TESTED HERE. There is no "task survives gc.collect()"
test, deliberately. Such a test passes with a bare `create_task` too: an
`asyncio.sleep` registers a timer callback that itself strongly references the
task, so a sleeping task is never collectable. Reproducing the real window —
suspended on a future nothing else holds — is not something a unit test can
force. A test that passes either way is worse than no test, so these assert the
reference contract instead, which is what `spawn` actually promises.
"""

import asyncio

import pytest

from app.core.fire_and_forget import spawn, _pending_tasks


@pytest.mark.asyncio
async def test_reference_is_held_after_the_handle_is_dropped():
    """The caller discards the handle — `_pending_tasks` must be the ref."""
    release = asyncio.Event()
    started = asyncio.Event()

    async def waiter():
        started.set()
        await release.wait()

    spawn(waiter())                 # handle deliberately not kept
    await started.wait()

    live = [t for t in _pending_tasks if not t.done()]
    assert live, "spawn kept no strong reference to a pending task"

    release.set()
    await asyncio.gather(*live)
    await asyncio.sleep(0)          # let the done-callback fire
    for t in live:
        assert t not in _pending_tasks


@pytest.mark.asyncio
async def test_pending_set_releases_on_completion():
    """Held while running, dropped when done — the set must not grow."""
    release = asyncio.Event()
    started = asyncio.Event()

    async def waiter():
        started.set()
        await release.wait()

    before = len(_pending_tasks)
    task = spawn(waiter())
    await started.wait()
    assert task in _pending_tasks

    release.set()
    await task
    await asyncio.sleep(0)

    assert task not in _pending_tasks
    assert len(_pending_tasks) == before


@pytest.mark.asyncio
async def test_failing_task_still_releases_its_reference():
    """A raising task must not pin its reference forever (slow leak)."""
    async def boom():
        raise RuntimeError("expected")

    task = spawn(boom())
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert task not in _pending_tasks


@pytest.mark.asyncio
async def test_spawn_returns_a_real_task():
    """Call sites that do keep the handle must still get a Task back."""
    async def value():
        return 42

    task = spawn(value())
    assert isinstance(task, asyncio.Task)
    assert await task == 42
