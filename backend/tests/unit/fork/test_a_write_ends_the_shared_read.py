"""A reply computed before a write must never answer a read issued after it.

WHAT THIS COST
--------------
Reported 2026-08-20 on `0.0.543.9`: a member accepted a suggested change to an
instruction, and the "1 pending" badge, the row's "Pending review" chip and the
Pending-changes list all stayed until the page was reloaded.

The server was already right. Measured on the dev database at the time:

    instruction_builds   the accepted build recorded, approved_at set
    GET /instructions?pending_only=true   0 rows
    GET /instructions/counts              pending_total 0, ids []
    GET /instructions/{id}/review-hunks   0 suggestions

So the accept worked and only the screen disagreed — which is why a reload
"fixed" it.

THE CAUSE
---------
`frontend/composables/useMyFetch.ts` shares one network call between concurrent
identical GETs. Its own comment claimed that this can never serve stale data,
because an entry is dropped as soon as its response lands. That is true right up
until something is WRITTEN:

    t0  GET  /instructions/X/review-hunks    starts   -> answer: 1 pending
    t1  POST /instructions/X/resolve         accepted
    t2  GET  /instructions/X/review-hunks    JOINS t0 -> answer: 1 pending

The second read is answered from before the write, by a cache that believes it
is holding nothing. Nothing retries, because nothing failed.

★It is intermittent by nature — it needs a same-key GET already in flight when
the write lands — which is exactly why it presents as "sometimes I have to
refresh" rather than as a reproducible bug.

WHAT IS PINNED HERE
-------------------
  * the RULE, run both ways: the pre-fix rule still produces the stale answer,
    and the shipped rule does not (a red proof that runs every time, rather than
    one done once at a shell prompt)
  * ★a caller already waiting on the older request still receives its answer —
    it asked before the write, and cancelling it would break a read that was
    never wrong
  * the shipped file actually implements the fenced rule
  * the accept path awaits its refresh, so a refresh that fails is reported
    instead of leaving a resolved change on screen as pending

★These read files only — no schema, no database. See CLAUDE.md.
"""
import asyncio
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FETCH = REPO / "frontend" / "composables" / "useMyFetch.ts"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"


# --- the rule, in both versions -----------------------------------------------


class _Server:
    """Answers `review-hunks`. One pending suggestion until it is accepted."""

    def __init__(self):
        self.pending = 1
        self.release = asyncio.Event()

    async def review_hunks(self):
        # ★The answer is fixed when the request is HANDLED, and only delivered
        # later — that is what an in-flight request is, and modelling it the
        # other way round (state read at delivery) quietly removes the defect
        # from the simulation, because then every request sees the latest world
        # no matter who is sharing it.
        answer = {"suggestions": ["hunk"] * self.pending}
        await self.release.wait()
        return answer

    def accept(self):
        self.pending = 0


class _PreFix:
    """`useMyFetch` before this change: key -> in-flight promise, no fence."""

    def __init__(self, server):
        self.server, self.inflight = server, {}

    async def get(self, key):
        task = self.inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self.server.review_hunks())
            self.inflight[key] = task
            task.add_done_callback(lambda _t: self.inflight.pop(key, None))
        return await task

    def write(self):
        self.server.accept()


class _Shipped(_PreFix):
    """With the fence: a GET may only join an entry from the current epoch."""

    def __init__(self, server):
        super().__init__(server)
        self.epoch = 0

    async def get(self, key):
        entry = self.inflight.get(key)
        if entry is None or entry[0] != self.epoch:
            task = asyncio.ensure_future(self.server.review_hunks())
            epoch = self.epoch
            self.inflight[key] = (epoch, task)
            task.add_done_callback(
                lambda _t: self.inflight.pop(key, None)
                if self.inflight.get(key) == (epoch, task) else None
            )
            entry = (epoch, task)
        return await entry[1]

    def write(self):
        self.epoch += 1          # the fence goes up before the write is sent
        self.server.accept()


async def _accept_then_read(client_cls):
    """t0 read in flight, t1 write, t2 read. Returns (t0 answer, t2 answer)."""
    server = _Server()
    client = client_cls(server)

    async def settle():
        # ★More than one turn. The client coroutine has to run, create its
        # request, AND that request has to reach its first await before it is
        # genuinely "in flight" — with a single sleep(0) the first read had not
        # yet been handled when the write landed, so the window the defect needs
        # never opened and both rules looked identical.
        for _ in range(4):
            await asyncio.sleep(0)

    first = asyncio.ensure_future(client.get("review-hunks"))
    await settle()                            # t0 is handled, answer fixed
    client.write()                            # t1 the member accepts
    second = asyncio.ensure_future(client.get("review-hunks"))
    await settle()                            # t2 asks after the write
    server.release.set()                      # both requests may now answer
    return await first, await second


# --- the red proof, carried in the test ---------------------------------------


def test_the_old_rule_still_serves_the_pre_accept_answer():
    """★If this ever stops failing, the reconstruction has drifted and every
    other assertion in this file has stopped proving anything."""
    _first, second = asyncio.run(_accept_then_read(_PreFix))

    assert second["suggestions"], (
        "the pre-fix reconstruction no longer reproduces the defect — it is "
        "supposed to hand the post-accept read the pre-accept answer"
    )


def test_a_read_after_the_write_is_not_answered_from_before_it():
    _first, second = asyncio.run(_accept_then_read(_Shipped))

    assert second["suggestions"] == [], (
        "a read issued after the accept was answered from before it — this is "
        "the badge that will not clear until the page is reloaded"
    )


def test_a_reader_that_asked_first_still_gets_its_answer():
    """★The fence decides who may SHARE a request, not which requests survive.
    Cancelling the earlier one would break a read that was never wrong."""
    first, _second = asyncio.run(_accept_then_read(_Shipped))

    assert first["suggestions"], (
        "the read that started before the write was disturbed by it"
    )


# --- the shipped file implements that rule ------------------------------------


@pytest.fixture(scope="module")
def fetch_src() -> str:
    return FETCH.read_text(encoding="utf-8")


def test_the_shared_read_cache_is_fenced_by_writes(fetch_src: str):
    assert "writeEpoch" in fetch_src, "nothing separates reads across a write"
    # The fence must go up BEFORE the mutation is sent, not after it answers.
    assert re.search(r"if\s*\(method\s*!==\s*'GET'\)\s*writeEpoch\s*\+=\s*1", fetch_src), (
        "the epoch is not bumped on the mutating request itself"
    )
    assert re.search(r"entry\.epoch\s*!==\s*epoch", fetch_src), (
        "a GET can still join an entry created before a write"
    )


def test_a_streamed_write_raises_the_fence_too(fetch_src: str):
    """A chat turn is sent as a streamed POST. It writes like any other."""
    stream_block = fetch_src[fetch_src.index("if (opts.stream)"):]
    stream_block = stream_block[: stream_block.index("if (isClient)")]
    assert "writeEpoch += 1" in stream_block, (
        "a streamed mutation leaves the shared read cache unfenced"
    )


def test_an_entry_is_only_retracted_by_its_own_request(fetch_src: str):
    """★Two requests can share a key across an epoch boundary. Letting the older
    one delete the newer one's entry would leave a live request unshareable."""
    assert "inflightGets.get(key)?.promise === promise" in fetch_src, (
        "a settling request can delete an entry belonging to a later one"
    )


# --- and the accept path reports a refresh it could not do --------------------


def test_the_accept_path_awaits_its_own_refresh():
    """The badge, the dots and the Pending-changes list are all derived from
    what `refreshLists` fetches. Fire-and-forget meant a failure there left the
    pre-accept world on screen with nothing said and nothing retried."""
    source = EXPLORER.read_text(encoding="utf-8")
    code = "\n".join(l for l in source.splitlines() if not l.strip().startswith("//"))

    assert not re.search(r"^\s*refreshLists\(\)\s*$", code, re.M), (
        "an un-awaited refreshLists() is back — its failure cannot be seen"
    )
    assert code.count("await refreshLists()") >= 2, (
        "the two resolve paths must both await the refresh they depend on"
    )


def test_one_failing_group_cannot_cancel_the_pending_reload():
    """`refreshLists` awaits `fetchAll` before reloading the pending list, so a
    single rejected group used to take the badge down with it."""
    source = EXPLORER.read_text(encoding="utf-8")
    block = source[source.index("const fetchAll = async"):]
    block = block[: block.index("\n}")]
    assert "allSettled" in block, (
        "one group that fails to reload still aborts the whole refresh"
    )
