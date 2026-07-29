"""Connector sync progress — the shared, cross-worker registry.

Why these tests exist
---------------------
The thing being replaced was an in-memory dict that LOOKED correct in every
single-process test and was wrong in production, where the app runs up to 4
uvicorn workers: the sync wrote progress in one worker and the poll read it from
another, so most polls reported `idle` while a sync was in flight. A test that
mocks away the store cannot catch that class of bug, so what is pinned here is
the CONTRACT that makes the fix work — the store is a DB row, the write opens
its own session, and every terminal shape is named.

No schema is needed: the session is faked, so this belongs in the fast fork
suite (see `tests/unit/fork/conftest.py`).
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.services import connection_sync_progress as prog
from app.models.connection_sync_progress import ConnectionSyncProgress


# ---------------------------------------------------------------------------
# A fake session that behaves enough like the real one for this module: it holds
# rows in a list, `execute(...).scalars().first()` filters them, and `commit` is
# a no-op. Deliberately NOT a MagicMock — a MagicMock fabricates every attribute
# and would make `hasattr` checks pass vacuously.
# ---------------------------------------------------------------------------
class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class FakeSession:
    def __init__(self, store):
        self.store = store
        self.commits = 0

    async def execute(self, stmt):
        # The only query this module issues is "the row for (ds, user)". Rather
        # than parse the statement, match on the criteria values it carries.
        wanted = [
            c.right.value
            for c in stmt.whereclause.get_children()
            if hasattr(c, "right") and hasattr(c.right, "value")
        ]
        rows = [
            r for r in self.store
            if r.data_source_id == wanted[0] and r.user_id == wanted[1]
        ] if len(wanted) >= 2 else []
        return _Result(rows)

    def add(self, row):
        self.store.append(row)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def store(monkeypatch):
    """Point the module's session factory at a fake, shared across calls."""
    rows = []

    class _Maker:
        def __call__(self):
            return FakeSession(rows)

    import app.dependencies as deps
    monkeypatch.setattr(deps, "async_session_maker", _Maker(), raising=False)
    return rows


def run(coro):
    """Drive one coroutine to completion on a loop of its own.

    ★Not `asyncio.get_event_loop()`. That passed when this file ran alone and
    failed on 15 tests inside the full suite: by then another test has closed
    the main thread's loop and `get_event_loop()` raises rather than making a
    new one. Nothing here is bound to a loop — the session is fake — so a fresh
    loop per call is both correct and cheaper to reason about.
    """
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. The store is a database row, not process memory
# ---------------------------------------------------------------------------
def test_progress_is_backed_by_a_database_table():
    """The whole point of the change. A module-level dict is invisible to the
    worker serving the poll; only a shared store is not."""
    assert ConnectionSyncProgress.__tablename__ == "connection_sync_progress"
    cols = {c.name for c in ConnectionSyncProgress.__table__.columns}
    for expected in (
        "data_source_id", "user_id", "status", "phase",
        "endpoints_total", "endpoints_done", "endpoints_failed",
        "tables", "detail", "error", "started_at", "last_done_at",
    ):
        assert expected in cols, expected


def test_module_holds_no_process_state():
    """No module-level container that could accumulate progress. That is the
    exact shape of the bug being fixed — a dict keyed by (ds, user), correct in
    one worker and empty in the other three.

    Dunders are excluded: `__builtins__` is a dict on every module and says
    nothing about this one.
    """
    offenders = [
        name for name, value in vars(prog).items()
        if not name.startswith("__")
        and isinstance(value, (dict, list, set))
    ]
    assert offenders == [], f"module-level mutable state: {offenders}"


def test_writes_open_their_own_session(store):
    """A progress write must not ride the caller's transaction — the callers are
    mid-crawl, and committing their session would commit half-built overlays."""
    import inspect
    for fn in (prog.start, prog.update, prog.finish, prog.fail, prog.get,
               prog.set_endpoints, prog.endpoint_done):
        params = inspect.signature(fn).parameters
        assert "db" not in params, f"{fn.__name__} takes a session"


# ---------------------------------------------------------------------------
# 2. Lifecycle
# ---------------------------------------------------------------------------
def test_start_then_get_reports_syncing(store):
    run(prog.start("ds1", "u1"))
    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "running"
    assert p["phase"] == "discovering"
    assert p["endpoints_total"] == 0


def test_get_with_no_row_is_idle_not_none(store):
    """Never None. An absent row means idle, and the registry decides that once
    rather than making every caller guess."""
    p = run(prog.get("nope", "nobody"))
    assert p["status"] == "idle"
    assert p["detail"] == []
    assert p["tables"] == 0


def test_set_endpoints_publishes_the_list_as_pending(store):
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [
        {"database": "DL_POC", "item_type": "Lakehouse", "workspace_name": "POC", "tenant_name": "City Mart"},
        {"database": "LK_CFC_Sales", "item_type": "Lakehouse"},
    ]))
    p = run(prog.get("ds1", "u1"))
    assert p["phase"] == "ingesting"
    assert p["endpoints_total"] == 2
    assert [d["name"] for d in p["detail"]] == ["DL_POC", "LK_CFC_Sales"]
    assert all(d["status"] == "pending" for d in p["detail"])
    assert p["detail"][0]["workspace"] == "POC"
    assert p["detail"][0]["tenant"] == "City Mart"


def test_endpoint_done_advances_counts_and_names_the_workspace(store):
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}, {"database": "B"}]))
    run(prog.endpoint_done("ds1", "u1", "A", tables=29))
    p = run(prog.get("ds1", "u1"))
    assert p["endpoints_done"] == 1
    assert p["endpoints_failed"] == 0
    a = next(d for d in p["detail"] if d["name"] == "A")
    assert a["status"] == "completed" and a["tables"] == 29
    assert next(d for d in p["detail"] if d["name"] == "B")["status"] == "pending"


def test_a_failed_endpoint_is_counted_separately(store):
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}, {"database": "B"}]))
    run(prog.endpoint_done("ds1", "u1", "A", tables=29))
    run(prog.endpoint_done("ds1", "u1", "B", error="timed out after 30s"))
    p = run(prog.get("ds1", "u1"))
    assert p["endpoints_done"] == 1
    assert p["endpoints_failed"] == 1
    b = next(d for d in p["detail"] if d["name"] == "B")
    assert b["status"] == "failed" and "timed out" in b["error"]


def test_an_undiscovered_endpoint_is_appended_not_dropped(store):
    """Discovery can under-report. What was actually read is the truth."""
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}]))
    run(prog.endpoint_done("ds1", "u1", "SURPRISE", tables=3))
    p = run(prog.get("ds1", "u1"))
    assert {d["name"] for d in p["detail"]} == {"A", "SURPRISE"}
    assert p["endpoints_total"] == 2


# ---------------------------------------------------------------------------
# 3. Terminal states — `partial` is the one that matters
# ---------------------------------------------------------------------------
def test_all_endpoints_ok_finishes_done(store):
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}]))
    run(prog.endpoint_done("ds1", "u1", "A", tables=29))
    run(prog.finish("ds1", "u1", tables=29))
    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "completed"
    assert p["tables"] == 29


def test_any_failed_endpoint_finishes_partial_not_error(store):
    """A member with three of four workspaces has a WORKING agent. Reporting
    that as an error would be false, and would hide the one real fact: which
    workspace is missing."""
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}, {"database": "B"}]))
    run(prog.endpoint_done("ds1", "u1", "A", tables=29))
    run(prog.endpoint_done("ds1", "u1", "B", error="unreachable"))
    run(prog.finish("ds1", "u1", tables=29))
    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "partial"
    assert p["error"] is None          # partial is not an error
    assert p["tables"] == 29
    assert p["endpoints_failed"] == 1


def test_fail_is_reserved_for_the_sync_itself_failing(store):
    run(prog.start("ds1", "u1"))
    run(prog.fail("ds1", "u1", "token mint failed"))
    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "failed"
    assert "token mint" in p["error"]


def test_fail_creates_a_row_even_if_start_never_ran(store):
    """A sync that dies before start() must still be reportable — otherwise the
    UI polls forever against `idle` and shows nothing at all."""
    run(prog.fail("ds9", "u9", "data source not found"))
    p = run(prog.get("ds9", "u9"))
    assert p["status"] == "failed"


# ---------------------------------------------------------------------------
# 4. Memory across runs
# ---------------------------------------------------------------------------
def test_last_done_at_survives_a_new_start(store):
    """"When did I last sync successfully" must not be erased by starting a run
    that may yet fail."""
    run(prog.start("ds1", "u1"))
    run(prog.finish("ds1", "u1", tables=5))
    first = run(prog.get("ds1", "u1"))["last_done_at"]
    assert first is not None

    run(prog.start("ds1", "u1"))
    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "running"
    assert p["last_done_at"] == first


def test_start_clears_the_previous_runs_counters(store):
    run(prog.start("ds1", "u1"))
    run(prog.set_endpoints("ds1", "u1", [{"database": "A"}]))
    run(prog.endpoint_done("ds1", "u1", "A", error="bad"))
    run(prog.finish("ds1", "u1", tables=0))

    run(prog.start("ds1", "u1"))
    p = run(prog.get("ds1", "u1"))
    assert p["endpoints_failed"] == 0
    assert p["detail"] == []
    assert p["error"] is None


def test_a_stale_terminal_row_reads_idle_but_keeps_last_done(store):
    run(prog.start("ds1", "u1"))
    run(prog.finish("ds1", "u1", tables=7))
    row = store[0]
    row.updated_at = datetime.utcnow() - timedelta(seconds=prog._TTL_SECONDS + 60)

    p = run(prog.get("ds1", "u1"))
    assert p["status"] == "idle"
    assert p["last_done_at"] is not None


# ---------------------------------------------------------------------------
# 5. Isolation — one row per (data source, member)
# ---------------------------------------------------------------------------
def test_two_members_do_not_see_each_others_sync(store):
    run(prog.start("ds1", "alice"))
    run(prog.set_endpoints("ds1", "alice", [{"database": "A"}]))
    run(prog.endpoint_done("ds1", "alice", "A", tables=11))
    run(prog.finish("ds1", "alice", tables=11))

    bob = run(prog.get("ds1", "bob"))
    assert bob["status"] == "idle"
    assert bob["tables"] == 0

    alice = run(prog.get("ds1", "alice"))
    assert alice["tables"] == 11


# ---------------------------------------------------------------------------
# 6. A tracker failure must never break the sync it describes
# ---------------------------------------------------------------------------
def test_a_broken_store_does_not_raise(monkeypatch):
    import app.dependencies as deps

    def _explode():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(deps, "async_session_maker", _explode, raising=False)

    # None of these may raise; the sync is more important than its narration.
    run(prog.start("ds1", "u1"))
    run(prog.update("ds1", "u1", phase="ingesting"))
    run(prog.endpoint_done("ds1", "u1", "A", tables=1))
    run(prog.finish("ds1", "u1", tables=1))
    run(prog.fail("ds1", "u1", "x"))
    assert run(prog.get("ds1", "u1"))["status"] == "idle"


# ---------------------------------------------------------------------------
# 7. The superseded module must break loudly, not silently
# ---------------------------------------------------------------------------
def test_the_old_in_memory_tracker_refuses_to_run():
    from app.services import fabric_sync_progress as old
    for fn in (old.start, old.update, old.finish, old.fail, old.get):
        with pytest.raises(RuntimeError, match="connection_sync_progress"):
            fn("ds", "u")
