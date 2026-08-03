"""Replay the 2026-08-03 outage end to end, against a real database.

The incident, from `insights-2026-08-3-logs.txt` (3,236 records, prod):

    05:31:46 → 06:37:52   `password authentication failed for user "dash"` × 88
    05:46:05              `indexing.run.crash`, last frame `InvalidPasswordError`
    thereafter            `indexing.wait_for_active.timeout` × 4, 600s each

Our own Postgres refused new connections for about an hour — only new ones, so
busy minutes (warm pooled connections) were clean and idle minutes were not.
A Fabric sync ticked into that window and died. The handler that records a
crash then opened *another* new connection on the same failing engine, failed
the same way, and discarded the failure through `except Exception: pass`. The
row stayed `running` forever. Four later callers each blocked the full 600s on
it. The member saw a spinner that never finished and an agent that could not
see their tables — and was told to "attach or refresh the lakehouse".

The fork-suite tests for phases B/D/F check each part in isolation, without a
schema. This file is the other thing: one continuous scenario, on a live
database, that fails if any link in the chain regresses. The parts can all pass
individually while the chain is broken — that is precisely what happened in
production, where every component was doing what it was written to do.

★These need a schema, so they live here and NOT in `tests/unit/fork` — see
CLAUDE.md. They pay the ~0.9s per-test migration cost deliberately.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.dependencies import async_session_maker
from app.models.connection import Connection
from app.models.connection_indexing import (
    ConnectionIndexing,
    ConnectionIndexingStatus,
)
from app.models.connection_sync_progress import ConnectionSyncProgress
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.user import User
from app.services.connection_indexing_service import (
    ConnectionIndexingService,
    _write_terminal_failure,
)
from app.services.indexing_failures import FAILURE_INFRASTRUCTURE


# ─────────────────────────── the outage itself ───────────────────────────


class InvalidPasswordError(Exception):
    """The real asyncpg exception, reproduced by module and message.

    Classification reads `type(exc).__module__`, so the stand-in has to live
    where the real one lives. Importing asyncpg's own class would work too, but
    then the test would silently start passing for the wrong reason if the
    discriminator ever changed to something asyncpg-specific.
    """
    __module__ = "asyncpg.exceptions"


def _outage() -> Exception:
    return InvalidPasswordError('password authentication failed for user "dash"')


class _DeadDatabase:
    """A session factory that refuses the first N times, like the real one did.

    ★This is the part that cannot be faked with a mock that always succeeds.
    The bug was that recording the failure needed a NEW connection from the
    same engine that had just refused one — so the recovery path failed for
    exactly the reason it was invoked. A factory that works on the first call
    tests a situation that never occurred.
    """

    def __init__(self, fail_first: int, real_factory):
        self.fail_first = fail_first
        self.calls = 0
        self._real = real_factory

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise _outage()
        return self._real()


async def _seed(db):
    org = Organization(name="Outage Org")
    db.add(org)
    await db.flush()
    user = User(
        name="Member",
        email="outage-scenario@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    conn = Connection(
        organization_id=str(org.id),
        name="Fabric",
        type="fabric_user",
        config={},  # NOT NULL on the table
    )
    db.add(conn)
    await db.flush()
    return org, user, conn


async def _running_row(db, conn, user=None) -> ConnectionIndexing:
    row = ConnectionIndexing(
        connection_id=str(conn.id),
        user_id=str(user.id) if user else None,
        status=ConnectionIndexingStatus.RUNNING.value,
        progress_done=3,
        progress_total=20,
        started_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()
    await db.commit()
    return row


# ─────────── 1. the crash lands even though the DB is refusing ───────────


@pytest.mark.asyncio
async def test_the_failure_is_recorded_despite_the_outage_that_caused_it():
    """★The whole incident in one assertion.

    Two connection attempts are refused before one succeeds — the same shape as
    production, where rejections were intermittent. Before this fix the single
    attempt hit the first refusal and the failure was silently dropped.
    """
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id = str(row.id)

    factory = _DeadDatabase(fail_first=2, real_factory=async_session_maker)
    landed = await _write_terminal_failure(factory, indexing_id, _outage())

    assert landed is True
    assert factory.calls == 3, "it must retry, not give up on the first refusal"

    async with async_session_maker() as db:
        fresh = await db.get(ConnectionIndexing, indexing_id)
        assert fresh.status == ConnectionIndexingStatus.FAILED.value
        assert fresh.finished_at is not None


@pytest.mark.asyncio
async def test_the_row_does_not_stay_running_forever():
    """The observable symptom: a spinner that never stops, and four callers
    each blocked for the full 600s of `wait_for_active`."""
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id = str(row.id)

    await _write_terminal_failure(
        _DeadDatabase(fail_first=1, real_factory=async_session_maker),
        indexing_id, _outage(),
    )

    async with async_session_maker() as db:
        svc = ConnectionIndexingService()
        assert await svc.get_active(db, str(conn.id)) is None, (
            "a terminal row must not read as work in flight"
        )


@pytest.mark.asyncio
async def test_an_outage_that_never_lifts_gives_up_loudly_not_silently():
    """If every attempt fails there is nothing more to be done — but the run
    must not vanish. `_write_terminal_failure` reports False so the caller
    knows, and logs at ERROR so the row's absence is explainable later."""
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id = str(row.id)

    landed = await _write_terminal_failure(
        _DeadDatabase(fail_first=99, real_factory=async_session_maker),
        indexing_id, _outage(),
    )
    assert landed is False


# ─────────── 2. it is recorded as OURS, and retried accordingly ───────────


@pytest.mark.asyncio
async def test_the_outage_is_classified_as_ours_not_the_members_credential():
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id, conn_id = str(row.id), str(conn.id)

    await _write_terminal_failure(async_session_maker, indexing_id, _outage())

    async with async_session_maker() as db:
        fresh = await db.get(ConnectionIndexing, indexing_id)
        assert (fresh.stats_json or {}).get("error_kind") == FAILURE_INFRASTRUCTURE
        # And the sentence the member reads blames nobody.
        assert "our own service" in (fresh.error or "")
        assert "credentials" not in (fresh.error or "")


@pytest.mark.asyncio
async def test_our_own_outage_schedules_a_retry_within_minutes():
    """A source refusing us waits for the full interval — a human has to act.
    An outage of ours passes on its own, so waiting hours is pure downtime."""
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id, conn_id = str(row.id), str(conn.id)

    before = datetime.utcnow()
    await _write_terminal_failure(async_session_maker, indexing_id, _outage())

    async with async_session_maker() as db:
        fresh_conn = await db.get(Connection, conn_id)
        assert fresh_conn.next_retry_at is not None
        gap = fresh_conn.next_retry_at - before
        assert timedelta(minutes=1) < gap < timedelta(minutes=15)


@pytest.mark.asyncio
async def test_a_source_refusal_does_not_schedule_an_automatic_retry():
    """★The counterpart, and the reason classification is worth having at all.
    Retrying a genuinely wrong credential every five minutes is how a service
    account gets locked out."""
    class PyodbcError(Exception):
        __module__ = "pyodbc"

    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        indexing_id, conn_id = str(row.id), str(conn.id)

    await _write_terminal_failure(
        async_session_maker, indexing_id,
        PyodbcError("Login failed for user 'fabric-svc'"),
    )

    async with async_session_maker() as db:
        fresh_conn = await db.get(Connection, conn_id)
        assert fresh_conn.next_retry_at is None
        assert "credentials" in (fresh_conn.last_reindex_error or "")


@pytest.mark.asyncio
async def test_a_repeating_outage_backs_off_instead_of_hammering():
    """Five minutes is right for a blip. Repeating it for an hour means every
    connection in the org retrying into a database already refusing them."""
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        conn_id = str(conn.id)
        first = await _running_row(db, conn)
        first_id = str(first.id)

    await _write_terminal_failure(async_session_maker, first_id, _outage())
    async with async_session_maker() as db:
        gap_one = (await db.get(Connection, conn_id)).next_retry_at - datetime.utcnow()
        conn_row = await db.get(Connection, conn_id)
        second = await _running_row(db, conn_row)
        second_id = str(second.id)

    await _write_terminal_failure(async_session_maker, second_id, _outage())
    async with async_session_maker() as db:
        gap_two = (await db.get(Connection, conn_id)).next_retry_at - datetime.utcnow()

    assert gap_two > gap_one, "the second consecutive outage must wait longer"


# ─────────── 3. a process that dies outright still unblocks ───────────


@pytest.mark.asyncio
async def test_a_killed_process_does_not_block_the_connection_forever():
    """★No handler runs at all on an OOM kill or a pod eviction, so nothing
    above can help. `start()` returns any non-terminal row instead of kicking
    off work — so without a reaper the sync can never be started again, by
    anyone, ever.
    """
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        row = await _running_row(db, conn)
        # Backdate past the abandon threshold: the runner is gone and has
        # written nothing for far longer than any quiet phase lasts.
        row.updated_at = datetime.utcnow() - timedelta(hours=3)
        await db.commit()
        conn_id, indexing_id = str(conn.id), str(row.id)

    async with async_session_maker() as db:
        svc = ConnectionIndexingService()
        assert await svc.get_active(db, conn_id) is None

    async with async_session_maker() as db:
        fresh = await db.get(ConnectionIndexing, indexing_id)
        assert fresh.status == ConnectionIndexingStatus.FAILED.value
        assert (fresh.stats_json or {}).get("abandoned") is True


@pytest.mark.asyncio
async def test_a_live_run_is_never_reaped():
    """Reporting a failure that did not happen is worse than the spinner."""
    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        await _running_row(db, conn)
        conn_id = str(conn.id)

    async with async_session_maker() as db:
        svc = ConnectionIndexingService()
        assert await svc.get_active(db, conn_id) is not None


# ─────────── 4. the member is told, and told the truth ───────────


@pytest.mark.asyncio
async def test_the_member_gets_a_notification_that_does_not_blame_them():
    from app.services.indexing_failures import classify_failure, describe_failure
    from app.services.sync_notifications import notify_sync_failed

    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        await db.commit()
        org_id, user_id = str(org.id), str(user.id)

        exc = _outage()
        kind = classify_failure(exc)
        await notify_sync_failed(
            db,
            organization_id=org_id,
            user_id=user_id,
            data_source_id="ds-1",
            data_source_name="Fabric",
            message=describe_failure(exc, kind),
            error_kind=kind,
        )

    async with async_session_maker() as db:
        from sqlalchemy import select

        rows = (await db.execute(
            select(Notification).where(Notification.user_id == user_id)
        )).scalars().all()
        assert len(rows) == 1
        note = rows[0]
        assert note.type == "sync_failed"
        # Amber, not red: there is nothing here for the member to act on.
        assert note.severity == "warning"
        assert "interrupted" in note.title
        assert "our own service" in (note.body or "")
        assert "credential" not in (note.body or "").lower()


@pytest.mark.asyncio
async def test_the_sync_tracker_records_the_cause_for_the_strip():
    """The strip the member is actually looking at reads this row, and words
    an outage of ours differently from a source refusing them."""
    from app.services import connection_sync_progress as prog
    from app.services.indexing_failures import classify_failure, describe_failure

    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        await db.commit()
        user_id = str(user.id)

    exc = _outage()
    kind = classify_failure(exc)
    await prog.fail("ds-1", user_id, describe_failure(exc, kind), error_kind=kind)

    state = await prog.get("ds-1", user_id)
    assert state["status"] == "failed"
    assert state["error_kind"] == FAILURE_INFRASTRUCTURE
    assert "our own service" in (state["error"] or "")


# ─────────── 5. and the agent stops inventing a cause ───────────


@pytest.mark.asyncio
async def test_the_agent_is_told_the_sync_failed_instead_of_seeing_nothing():
    """★The last link, and the one that produced the screenshot.

    With an empty catalog and no explanation the model is not wrong to guess —
    it has nothing else. This asserts the context it receives now contains the
    real cause and an explicit instruction not to send the member off to fix
    something that was never broken.
    """
    from app.ai.context.sections.tables_schema_section import TablesSchemaContext
    from app.schemas.data_source_schema import DataSourceSummarySchema
    from app.services import connection_sync_progress as prog
    from app.services.indexing_failures import classify_failure, describe_failure

    async with async_session_maker() as db:
        org, user, conn = await _seed(db)
        await db.commit()
        user_id = str(user.id)

    # ★`start()` first. `set_endpoints`/`endpoint_done` update an existing row
    # and silently no-op without one — in production `_kick_off_sync` always
    # starts the run before the crawl publishes anything. Skipping it here
    # produced an empty `detail` and a test that asserted nothing.
    await prog.start("ds-1", user_id)

    # The endpoints that answered before the crash — F.2's "where we looked".
    await prog.set_endpoints("ds-1", user_id, [
        {"database": "DL_POC"}, {"database": "Sales_LH"},
    ])
    await prog.endpoint_done("ds-1", user_id, "DL_POC", tables=12)
    exc = _outage()
    kind = classify_failure(exc)
    await prog.fail("ds-1", user_id, describe_failure(exc, kind), error_kind=kind)

    row = (await prog.get("ds-1", user_id))
    searched = [
        d.get("name") for d in (row.get("detail") or [])
        if isinstance(d, dict) and d.get("status") in ("ok", "completed")
    ]

    section = TablesSchemaContext.DataSource(
        info=DataSourceSummarySchema(
            id="ds-1", name="Fabric", type="fabric_user", is_active=True,
        ),
        tables=[],
        sync_failure={
            "kind": row["error_kind"],
            "message": row["error"],
            "searched": searched,
            "when": None,
        },
    )
    rendered = TablesSchemaContext(data_sources=[section]).render_combined()

    # The source is still there at all — it used to be dropped entirely.
    assert "Fabric" in rendered
    assert "OUR service" in rendered
    assert "Do NOT tell the user to attach, refresh, reconnect" in rendered
    # And it can say where it looked, so "not found" is checkable.
    assert "DL_POC" in rendered
