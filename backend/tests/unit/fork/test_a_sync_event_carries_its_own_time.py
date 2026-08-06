"""A sync event says WHEN it happened, and an old one still says nothing.

Every event on `GET /api/keeper/runs/{id}` came back `ts: null`. The run knew
its own `started_at`/`finished_at`, so the run was timed and the events inside
it were not — the screen could show "this sync took 86s" and not a single fact
about where in those 86 seconds anything happened.

The cause was that the event log is BUILT AT THE END of the crawl, from the
progress tracker's final detail list, and the tracker kept no per-workspace
time to build it from. The fix stamps the time in `endpoint_done`, the one
moment the fact is true; `sync_runs` carries it rather than taking its own.

★The other half of this test is that history stays silent. A row written before
the tracker stamped anything has no time to recover, and a backfill would put a
plausible invented number where a member could read it as measurement.

No schema here on purpose: everything under test is either a pure function or a
tracker write driven through a fake session, so this belongs in `fork/`.
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from app.services import connection_sync_progress as progress
from app.services import sync_runs
from app.services.keeper_service import _event_out


# Naive UTC isoformat — the same shape `started_at`/`finished_at` are serialised
# in. The screen subtracts one from the other; a `Z` on one side only would be
# wrong by the viewer's offset and would still parse.
_NAIVE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _detail(**over):
    entry = {"name": "DL_POC", "status": "ok", "tables": 29, "error": None}
    entry.update(over)
    return entry


# ───────────────────── the log carries what the tracker stamped ─────────────

def test_an_event_carries_the_time_the_workspace_finished():
    stamped = "2026-08-04T14:49:41.123456"
    events = sync_runs._events_from_detail([_detail(ts=stamped)], total=1)
    assert [e["ts"] for e in events] == [stamped]


def test_each_workspace_keeps_its_own_time_not_the_run_s():
    """The whole point: N events, N different times.

    Stamping in `_events_from_detail` would have been the easy fix and would
    have produced N copies of the finish time — which looks like data and
    answers nothing.
    """
    detail = [
        _detail(name="DL_POC", ts="2026-08-04T14:49:41"),
        _detail(name="CFC_Lakehouse", ts="2026-08-04T14:50:12"),
        _detail(name="LK_CFC_Sales", ts="2026-08-04T14:50:52"),
    ]
    events = sync_runs._events_from_detail(detail, total=3)
    assert [e["ts"] for e in events] == [
        "2026-08-04T14:49:41", "2026-08-04T14:50:12", "2026-08-04T14:50:52",
    ]


def test_a_failed_workspace_is_timed_too():
    events = sync_runs._events_from_detail(
        [_detail(status="failed", error="403", ts="2026-08-04T14:49:41")], total=1
    )
    assert events[0]["level"] == "warning"
    assert events[0]["ts"] == "2026-08-04T14:49:41"


def test_the_event_shape_is_otherwise_unchanged():
    """`ts` was added; nothing else moved. The screen reads these keys."""
    events = sync_runs._events_from_detail([_detail(ts="2026-08-04T14:49:41")], total=4)
    assert set(events[0]) == {"ts", "level", "phase", "message", "done", "total"}
    assert events[0]["message"] == "DL_POC: 29 table(s)"
    assert (events[0]["phase"], events[0]["done"], events[0]["total"]) == ("ingesting", 1, 4)


# ───────────────────────── history stays silent ──────────────────────────────

def test_an_entry_with_no_time_stays_null():
    events = sync_runs._events_from_detail([_detail()], total=1)
    assert events[0]["ts"] is None


@pytest.mark.parametrize("bad", [None, "", 0, 1754318981, {"when": "now"}, []])
def test_a_time_that_is_not_a_string_is_refused_not_coerced(bad):
    """A `datetime` would not survive the JSON column, and a number is somebody
    else's convention. Either way the honest answer is "no time recorded"."""
    events = sync_runs._events_from_detail([_detail(ts=bad)], total=1)
    assert events[0]["ts"] is None


def test_a_stored_event_from_before_this_change_still_serialises():
    """Historical `events_json` rows have no `ts` KEY at all in the oldest
    shapes. The detail endpoint must hand back `null`, never raise."""
    old = {"level": "info", "phase": "ingesting", "message": "DL_POC: 29 table(s)",
           "done": 1, "total": 4}
    out = _event_out(old)
    assert out["ts"] is None
    assert out["message"] == "DL_POC: 29 table(s)"
    # ★And the stored value is not touched: `events_json` is the ORM's own JSON
    # and mutating it on a read would dirty the row on a GET.
    assert "ts" not in old


def test_an_explicit_null_is_left_exactly_as_it_is():
    stored = {"ts": None, "level": "info", "message": "x"}
    assert _event_out(stored)["ts"] is None


def test_a_junk_event_is_passed_through_rather_than_crashing():
    assert _event_out("not a dict") == "not a dict"


# ─────────────────── the tracker stamps at the right moment ──────────────────

class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj


class _FakeSession:
    """Just enough session for `connection_sync_progress` — no schema, no I/O."""

    def __init__(self, row):
        self._row = row

    async def execute(self, _stmt):
        return _FakeResult(self._row)

    async def commit(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


@pytest.mark.asyncio
async def test_endpoint_done_stamps_the_entry_it_completes(monkeypatch):
    from app.models.connection_sync_progress import ConnectionSyncProgress
    from app import dependencies

    row = ConnectionSyncProgress(data_source_id="ds", user_id="u")
    row.detail = [
        {"name": "DL_POC", "status": "pending", "tables": 0, "error": None, "ts": None},
        {"name": "CFC_Lakehouse", "status": "pending", "tables": 0, "error": None, "ts": None},
    ]
    row.endpoints_total = 2
    monkeypatch.setattr(dependencies, "async_session_maker", lambda: _FakeSession(row))

    before = datetime.utcnow()
    await progress.endpoint_done("ds", "u", "DL_POC", tables=29)
    after = datetime.utcnow()

    done = next(d for d in row.detail if d["name"] == "DL_POC")
    assert _NAIVE_ISO.match(done["ts"]), done["ts"]
    assert before <= datetime.fromisoformat(done["ts"]) <= after

    # ★The workspace still running is NOT stamped. A time on a pending entry
    # would be read as "finished at", and the log built from this list would
    # then time an event that had not happened.
    pending = next(d for d in row.detail if d["name"] == "CFC_Lakehouse")
    assert pending["ts"] is None


@pytest.mark.asyncio
async def test_a_workspace_discovery_never_invents_a_time(monkeypatch):
    """`set_endpoints` publishes the pending list; nothing there has run yet."""
    captured = {}

    async def _fake_update(_ds, _user, **fields):
        captured.update(fields)

    monkeypatch.setattr(progress, "update", _fake_update)
    await progress.set_endpoints("ds", "u", [{"database": "DL_POC"}, {"name": "X"}])

    assert [d["ts"] for d in captured["detail"]] == [None, None]
