"""A per-user sync must leave a durable record of what it did.

Measured against the live database on 2026-08-03: `connection_indexings` held 49
rows and every one of them was an org-scope sweep reporting 0/0 tables. The
per-user Fabric connector — the one members actually sync — wrote only to
`connection_sync_progress`, which keeps ONE row per (data_source, user) and
overwrites it on every run. So the second sync destroyed the evidence of the
first, and there was no way to answer "why did today find fewer tables than
yesterday" or even "did it run at all last week".

These tests hold the run store to the contract that makes that answerable:

  1. a sync leaves exactly one row, and a second sync leaves a second row;
  2. the per-workspace breakdown survives — which lakehouse gave which tables,
     and which one failed and why;
  3. a failure keeps its cause AND its `error_kind`, so history can still say
     whose fault it was after the strip's fifteen-minute window has expired;
  4. runs stay in the PER-USER scope, so a member's crawl can never block the
     org-level callers that wait on `user_id IS NULL`;
  5. a run whose worker died is closed, not left reading `running` forever.

★These need a schema, so they live here and NOT in `tests/unit/fork` — see
CLAUDE.md. They pay the ~0.9s per-test migration cost deliberately.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert, select

from app.dependencies import async_session_maker
from app.models.connection import Connection
from app.models.connection_indexing import (
    ConnectionIndexing,
    ConnectionIndexingStatus,
)
from app.models.data_source import DataSource
from app.models.domain_connection import domain_connection
from app.models.organization import Organization
from app.models.user import User
from app.services import connection_sync_progress as prog
from app.services import sync_runs


_SEQ = {"n": 0}


async def _seed(db):
    """An org, a member, and a fabric_user data source wired to a connection."""
    _SEQ["n"] += 1
    n = _SEQ["n"]
    org = Organization(name=f"Run Store Org {n}")
    db.add(org)
    await db.flush()
    user = User(
        name="Member",
        email=f"run-store-{n}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    conn = Connection(
        organization_id=str(org.id),
        name=f"Fabric {n}",
        type="fabric_user",
        config={},  # NOT NULL on the table
    )
    db.add(conn)
    ds = DataSource(name=f"Fabric Agent {n}", organization_id=str(org.id))
    db.add(ds)
    await db.flush()
    # The M:N junction is what `sync_runs` resolves data_source → connection
    # through; without it a run has nowhere to be recorded.
    await db.execute(insert(domain_connection).values(
        data_source_id=str(ds.id), connection_id=str(conn.id),
    ))
    await db.commit()
    return org, user, conn, ds


async def _runs_for(conn, user):
    async with async_session_maker() as db:
        return (await db.execute(
            select(ConnectionIndexing)
            .where(
                ConnectionIndexing.connection_id == str(conn.id),
                ConnectionIndexing.user_id == str(user.id),
            )
            .order_by(ConnectionIndexing.created_at.asc())
        )).scalars().all()


async def _one_sync(ds, user, *, endpoints, results, tables, trigger="signin"):
    """Drive a whole sync through the tracker, exactly as the routes do."""
    ds_id, uid = str(ds.id), str(user.id)
    await prog.start(ds_id, uid, trigger=trigger)
    await prog.set_endpoints(ds_id, uid, endpoints)
    for name, count, error in results:
        await prog.endpoint_done(ds_id, uid, name, tables=count, error=error)
    await prog.finish(ds_id, uid, tables=tables)


# ───────────────────────── 1. a run is recorded at all ─────────────────────


@pytest.mark.asyncio
async def test_a_sync_leaves_one_run_behind():
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales", "workspace_name": "Finance"}],
        results=[("Sales", 12, None)],
        tables=12,
    )

    runs = await _runs_for(conn, user)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == ConnectionIndexingStatus.COMPLETED.value
    assert run.finished_at is not None
    assert run.trigger == "signin"
    assert run.stats_json["tables"] == 12


@pytest.mark.asyncio
async def test_a_second_sync_does_not_erase_the_first():
    """★The whole point. The live tracker overwrites; this store must not."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales"}],
        results=[("Sales", 12, None)],
        tables=12,
    )
    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales"}],
        results=[("Sales", 4, None)],
        tables=4,
    )

    runs = await _runs_for(conn, user)
    assert len(runs) == 2, "the second sync must not overwrite the first"
    assert [r.stats_json["tables"] for r in runs] == [12, 4], (
        "yesterday's count must still be readable next to today's — that "
        "comparison is the reason history exists"
    )


# ──────────────────── 2. the per-workspace breakdown survives ──────────────


@pytest.mark.asyncio
async def test_the_run_says_which_workspace_gave_which_tables():
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[
            {"database": "Sales", "workspace_name": "Finance"},
            {"database": "Ops", "workspace_name": "Operations"},
            {"database": "HR", "workspace_name": "People"},
        ],
        results=[
            ("Sales", 12, None),
            ("Ops", 0, None),
            ("HR", 0, "login timeout expired"),
        ],
        tables=12,
    )

    run = (await _runs_for(conn, user))[0]
    by_name = {w["name"]: w for w in run.stats_json["workspaces"]}
    assert by_name["Sales"]["tables"] == 12
    assert by_name["Sales"]["workspace"] == "Finance"
    # ★"completed", not the "ok" the tracker stores. The archive is built from
    # `prog.get()`, which normalises on the way out (app/core/progress_status.py),
    # so history speaks the same one vocabulary as every other consumer instead
    # of preserving the tracker's private spelling.
    assert by_name["Ops"]["status"] == "completed" and by_name["Ops"]["tables"] == 0, (
        "a workspace that answered with nothing is a real answer, not a failure"
    )
    assert by_name["HR"]["status"] == "failed"
    assert "timeout" in by_name["HR"]["error"]


@pytest.mark.asyncio
async def test_a_partial_sync_is_not_recorded_as_a_plain_success():
    """`partial` is a success — the member has a working agent — but history
    must keep the distinction, or a run that missed two of five workspaces is
    indistinguishable from one that read all five."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales"}, {"database": "HR"}],
        results=[("Sales", 12, None), ("HR", 0, "no token for this tenant")],
        tables=12,
    )

    run = (await _runs_for(conn, user))[0]
    assert run.status == ConnectionIndexingStatus.COMPLETED.value
    assert run.stats_json["result"] == "partial"
    assert run.stats_json["endpoints_failed"] == 1


@pytest.mark.asyncio
async def test_the_event_log_names_the_workspace_that_failed():
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales"}, {"database": "HR"}],
        results=[("Sales", 12, None), ("HR", 0, "login timeout expired")],
        tables=12,
    )

    run = (await _runs_for(conn, user))[0]
    warnings = [e for e in run.events_json if e["level"] == "warning"]
    assert len(warnings) == 1
    assert "HR" in warnings[0]["message"]
    assert "timeout" in warnings[0]["message"]
    assert warnings[0]["total"] == 2


# ─────────────────────── 3. a failure keeps its cause ──────────────────────


@pytest.mark.asyncio
async def test_a_failed_sync_keeps_whose_fault_it_was():
    """★The strip forgets after fifteen minutes (`_TTL_SECONDS`). The incident
    that started this work was diagnosed a day later; if the run store dropped
    `error_kind` too, the same investigation would still be guessing."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    ds_id, uid = str(ds.id), str(user.id)
    await prog.start(ds_id, uid, trigger="signin")
    await prog.fail(
        ds_id, uid,
        "We could not reach our own database while syncing.",
        error_kind="infrastructure",
    )

    run = (await _runs_for(conn, user))[0]
    assert run.status == ConnectionIndexingStatus.FAILED.value
    assert run.finished_at is not None
    assert "our own database" in run.error
    assert run.stats_json["error_kind"] == "infrastructure", (
        "without this, history can only say the sync failed, not whether the "
        "member should do anything about it"
    )


# ──────────────────────── 4. scope isolation ───────────────────────────────


@pytest.mark.asyncio
async def test_a_members_run_never_lands_in_the_org_scope():
    """★What makes this safe to add. Every existing blocking caller
    (`wait_for_active`, `get_active`) reads the org scope — `user_id IS NULL`.
    A per-user run leaking into it would block org reindexes and the queries
    that wait on them, which is exactly the 600s stall this feature was born
    from."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await _one_sync(
        ds, user,
        endpoints=[{"database": "Sales"}],
        results=[("Sales", 3, None)],
        tables=3,
    )

    async with async_session_maker() as db:
        org_rows = (await db.execute(
            select(ConnectionIndexing).where(
                ConnectionIndexing.connection_id == str(conn.id),
                ConnectionIndexing.user_id.is_(None),
            )
        )).scalars().all()
    assert org_rows == []

    from app.services.connection_indexing_service import ConnectionIndexingService
    async with async_session_maker() as db:
        assert await ConnectionIndexingService().get_active(db, str(conn.id)) is None


@pytest.mark.asyncio
async def test_a_sync_with_no_user_is_refused_rather_than_widened():
    """An empty user_id would write `user_id = NULL`, i.e. the org scope. Better
    to record nothing than to record it in the scope other callers block on."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await sync_runs.begin(str(ds.id), "", trigger="signin")

    async with async_session_maker() as db:
        rows = (await db.execute(
            select(ConnectionIndexing).where(
                ConnectionIndexing.connection_id == str(conn.id)
            )
        )).scalars().all()
    assert rows == []


# ────────────────── 5. a run whose worker died gets closed ─────────────────


@pytest.mark.asyncio
async def test_a_restart_mid_crawl_does_not_leave_a_sync_running_forever():
    """The tracker writes are driven by the task doing the crawl. Kill the
    worker and nothing is left to finish the row — it reads `running` until
    something sweeps it."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await prog.start(str(ds.id), str(user.id), trigger="signin")
    # Backdate past the abandonment window, as a run orphaned by a deploy would be.
    async with async_session_maker() as db:
        row = (await db.execute(
            select(ConnectionIndexing).where(
                ConnectionIndexing.connection_id == str(conn.id),
                ConnectionIndexing.user_id == str(user.id),
            )
        )).scalars().first()
        row.started_at = datetime.utcnow() - timedelta(minutes=90)
        await db.commit()

    closed = await sync_runs.sweep_abandoned()
    assert closed >= 1

    run = (await _runs_for(conn, user))[0]
    assert run.status == ConnectionIndexingStatus.FAILED.value
    assert run.stats_json["abandoned"] is True
    assert run.stats_json["error_kind"] == "infrastructure", (
        "a worker we replaced is our doing — the member must not be sent to "
        "check a credential that is fine"
    )
    assert "Nothing on your side is wrong" in run.error


@pytest.mark.asyncio
async def test_a_re_sync_supersedes_a_stuck_run_instead_of_being_blocked_by_it():
    """Only one non-terminal row per scope is allowed. A member re-syncing while
    an earlier crawl is wedged must get a new run, not an exception."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await prog.start(str(ds.id), str(user.id), trigger="signin")
    await prog.start(str(ds.id), str(user.id), trigger="manual")

    runs = await _runs_for(conn, user)
    assert len(runs) == 2
    assert runs[0].status == ConnectionIndexingStatus.CANCELLED.value
    assert runs[0].stats_json["superseded"] is True
    assert runs[1].status == ConnectionIndexingStatus.RUNNING.value
    assert runs[1].trigger == "manual"


# ─────────────── 6. a plain client rebuild is not a sync ───────────────────


@pytest.mark.asyncio
async def test_a_finish_with_no_run_open_invents_nothing():
    """`_merge_all_fabric_endpoints` runs on a plain query too, and its progress
    calls are deliberate no-ops when no sync was started. The run store must
    behave the same way — a query must not appear in the sync history."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    await prog.finish(str(ds.id), str(user.id), tables=7)

    assert await _runs_for(conn, user) == []


@pytest.mark.asyncio
async def test_history_is_bounded():
    """Runs are pruned to a fixed depth so a member who syncs on every sign-in
    cannot grow the table without limit."""
    async with async_session_maker() as db:
        org, user, conn, ds = await _seed(db)

    keep = sync_runs._KEEP_RUNS_PER_SCOPE
    for _ in range(keep + 5):
        await _one_sync(
            ds, user,
            endpoints=[{"database": "Sales"}],
            results=[("Sales", 1, None)],
            tables=1,
        )

    runs = await _runs_for(conn, user)
    assert len(runs) <= keep
