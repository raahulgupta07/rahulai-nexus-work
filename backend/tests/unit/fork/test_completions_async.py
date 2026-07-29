"""The completions endpoint's async (background) mode, and the thing that
makes it trustworthy.

``POST /api/reports/{id}/completions`` runs the whole agent turn inside the HTTP
request by default. That default is deliberate — API consumers read the finished
turn out of the response body — so the escape hatch for long turns is the
already-existing ``?background=true``, which returns as soon as the completion
rows exist and lets the client poll.

Background mode is only worth recommending if a detached run cannot vanish.
``asyncio`` keeps only a *weak* reference to a running task, so a
``create_task()`` whose handle nobody holds may be collected mid-run — silently,
with no traceback, leaving its Completion row ``in_progress`` forever. There is
no HTTP request left to notice. ``_spawn_background`` holds the reference until
the task finishes.

Pure logic: no database, no app boot. See this directory's conftest.
"""

import asyncio
import gc

import pytest

from app.services import completion_service as cs


@pytest.mark.asyncio
async def test_spawn_background_holds_a_reference_while_running():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    cs._spawn_background(work())  # handle deliberately not kept by the caller
    await started.wait()

    assert len(cs._BACKGROUND_TASKS) == 1

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_spawn_background_releases_the_reference_when_done():
    async def work():
        return "done"

    task = cs._spawn_background(work())
    assert task in cs._BACKGROUND_TASKS

    assert await task == "done"
    await asyncio.sleep(0)

    # The set must not grow without bound — every completed run drops out.
    assert task not in cs._BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_detached_task_survives_a_collection_mid_run():
    """The actual failure this guards: caller drops the handle, GC runs, and the
    agent run must still finish. This is what leaves a completion stuck
    'in_progress' when it goes wrong."""
    finished = asyncio.Event()

    async def work():
        await asyncio.sleep(0.01)
        finished.set()

    cs._spawn_background(work())
    gc.collect()

    await asyncio.wait_for(finished.wait(), timeout=2)


@pytest.mark.asyncio
async def test_a_failing_background_task_still_releases_its_reference():
    async def boom():
        raise RuntimeError("agent blew up")

    task = cs._spawn_background(boom())
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert task not in cs._BACKGROUND_TASKS


def test_no_bare_create_task_left_in_the_service():
    """Every detached spawn in this service must go through the helper. A new
    bare ``asyncio.create_task(...)`` re-introduces the collectable-task bug in
    exactly the place it is hardest to notice."""
    import inspect

    source = inspect.getsource(cs)
    # The helper's own line is the single legitimate occurrence.
    occurrences = source.count("asyncio.create_task(")
    assert occurrences == 1, (
        f"expected only the create_task() inside _spawn_background, found {occurrences}"
    )
    assert "task = asyncio.create_task(coro)" in source


def test_background_is_opt_in_and_default_stays_synchronous():
    """The route must not flip to async by default: the response body of the
    default path is what existing API consumers read."""
    import inspect

    from app.routes import completion as route

    source = inspect.getsource(route.create_completion)
    assert 'request.query_params.get("background", "false")' in source, (
        "background must default to false — flipping it silently breaks every "
        "client that reads the finished turn out of the response body"
    )
    assert "background=background" in source
