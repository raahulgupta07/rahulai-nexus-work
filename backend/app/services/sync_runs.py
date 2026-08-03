"""Durable per-attempt history for per-user connector syncs.

``connection_sync_progress`` answers "what is happening right now" — ONE row per
(data_source, user), overwritten on every run. That is the correct shape for a
live strip and the wrong shape for history: the moment a member syncs again,
what happened last time is gone. Measured 2026-08-03 on the live database, the
per-user Fabric connector had **no run history at all** — 49 ``connection_indexings``
rows existed and every one of them was an org-scope sweep reporting 0/0.

This module writes the missing half.

★No new table
-------------
``connection_indexings`` already models exactly this: one row per refresh
attempt, ``events_json``/``stats_json`` for the detail, and a nullable
``user_id`` whose docstring already describes the per-user scope. The OAuth
catalog path (``connection_oauth.py``) has written per-user rows there since it
was built. The federated Fabric/Power BI crawl simply never created one. So this
is a wiring change, not a schema invention — and it ends the state where two
stores described the same sync and could disagree.

★Scope isolation is what makes this safe
----------------------------------------
Rows written here always carry a ``user_id``. Every existing blocking caller
(``wait_for_active``/``get_active`` in connection_service, data_source_service,
routes/connection) reads the ORG scope — ``user_id IS NULL`` — so a member's
Fabric crawl can never block an org-level reindex or a query that waits on one.

★Best-effort, own session, like the tracker it rides on
-------------------------------------------------------
Same contract as ``connection_sync_progress``: every function swallows its own
exceptions and opens its own short-lived session. History is worth having and
never worth failing a working sync for.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete

from app.models.connection_indexing import (
    ConnectionIndexing,
    ConnectionIndexingStatus,
    TERMINAL_INDEXING_STATUSES,
)
from app.models.domain_connection import domain_connection

logger = logging.getLogger(__name__)

# What started a run. Stored on the row so the activity list can say "you asked
# for this" versus "we retried it for you" without inferring from timestamps.
TRIGGER_SIGNIN = "signin"
TRIGGER_MANUAL = "manual"
TRIGGER_RETRY = "retry"
TRIGGER_SCHEDULE = "schedule"
TRIGGER_UPLOAD = "upload"
TRIGGERS = (TRIGGER_SIGNIN, TRIGGER_MANUAL, TRIGGER_RETRY, TRIGGER_SCHEDULE, TRIGGER_UPLOAD)

# A run still `running` this long after its last touch did not finish — the
# worker died, or the process was replaced mid-crawl. Matches the reaper window
# already used by ConnectionIndexingService so the two agree about what "stuck"
# means.
_ABANDONED_AFTER_MINUTES = 30

# How much history one (connection, user) pair keeps. A member who syncs on
# every sign-in generates a row a day; this is months of it, and it bounds the
# table without a policy anyone has to think about.
_KEEP_RUNS_PER_SCOPE = 50


async def _with_session(fn):
    """Run `fn(db)` on a fresh session, committing once. Never raises."""
    try:
        from app.dependencies import async_session_maker
        async with async_session_maker() as db:
            result = await fn(db)
            await db.commit()
            return result
    except Exception as e:  # noqa: BLE001
        logger.warning("sync_runs: %s", e)
        return None


async def _connection_id_for(db, data_source_id: str) -> Optional[str]:
    """The data source's connection, via the M:N junction.

    A per-user connector (fabric_user, powerbi_user) has exactly one. Reading the
    junction directly rather than the relationship keeps this usable from a bare
    session with nothing eager-loaded.
    """
    row = (await db.execute(
        select(domain_connection.c.connection_id).where(
            domain_connection.c.data_source_id == str(data_source_id)
        ).limit(1)
    )).first()
    return str(row[0]) if row else None


async def _open_run(db, connection_id: str, user_id: str) -> Optional[ConnectionIndexing]:
    """The newest non-terminal run for this exact (connection, user) scope."""
    return (await db.execute(
        select(ConnectionIndexing)
        .where(
            ConnectionIndexing.connection_id == str(connection_id),
            ConnectionIndexing.user_id == str(user_id),
            ConnectionIndexing.status.notin_(list(TERMINAL_INDEXING_STATUSES)),
        )
        .order_by(ConnectionIndexing.created_at.desc())
        .limit(1)
    )).scalars().first()


def _events_from_detail(detail: List[Any], *, total: int) -> List[Dict[str, Any]]:
    """One event per workspace, from the progress row's final detail list.

    ★Built at the END of the run, not appended during it. The live view is
    already served by ``connection_sync_progress``, which is written per
    workspace as the crawl walks; duplicating those writes here would double the
    tracker's cost to record something nobody reads until the run is over. The
    terminal snapshot carries the same facts.
    """
    events: List[Dict[str, Any]] = []
    done = 0
    for entry in (detail or []):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        failed = status == "failed"
        done += 1
        name = entry.get("name") or "workspace"
        if failed:
            message = f"{name} could not be read: {entry.get('error') or 'no reason given'}"
        else:
            message = f"{name}: {int(entry.get('tables') or 0)} table(s)"
        events.append({
            "ts": None,  # the tracker keeps no per-workspace timestamp
            "level": "warning" if failed else "info",
            "phase": "ingesting",
            "message": message,
            "done": done,
            "total": total,
        })
    return events


def _stats_from_detail(detail: List[Any]) -> Dict[str, Any]:
    """Per-workspace breakdown, kept verbatim.

    This is the fact the overwrite-in-place tracker destroys: which lakehouse
    contributed which tables, and which one failed and why. Without it a member
    asking "why did today's sync find fewer tables than yesterday's" has nothing
    to compare.
    """
    workspaces = []
    for entry in (detail or []):
        if not isinstance(entry, dict):
            continue
        workspaces.append({
            "name": entry.get("name"),
            "workspace": entry.get("workspace"),
            "kind": entry.get("kind"),
            "tenant": entry.get("tenant"),
            "status": entry.get("status"),
            "tables": int(entry.get("tables") or 0),
            "error": entry.get("error"),
        })
    return {"workspaces": workspaces}


async def begin(data_source_id: str, user_id: str, trigger: Optional[str] = None) -> None:
    """Open a run row for this sync attempt.

    Any run left open for the same scope is closed as ``cancelled`` first —
    ``ConnectionIndexingService`` allows only one non-terminal row per scope, and
    a member who re-syncs while a crawl is stuck must not be blocked by it.
    """
    if not user_id:
        # A per-user run with no user is not a per-user run; writing it with
        # user_id NULL would put it in the ORG scope, where blocking callers
        # live. Refuse rather than corrupt the scope.
        return

    async def _do(db):
        connection_id = await _connection_id_for(db, data_source_id)
        if connection_id is None:
            return
        stale = await _open_run(db, connection_id, user_id)
        if stale is not None:
            stale.mark_cancelled()
            stats = dict(stale.stats_json or {})
            stats["superseded"] = True
            stale.stats_json = stats
        row = ConnectionIndexing(
            connection_id=connection_id,
            user_id=str(user_id),
            status=ConnectionIndexingStatus.RUNNING.value,
            phase="discovering",
            started_at=datetime.utcnow(),
            trigger=(trigger if trigger in TRIGGERS else None),
            stats_json={"data_source_id": str(data_source_id)},
        )
        db.add(row)

    await _with_session(_do)


async def _close(
    data_source_id: str,
    user_id: str,
    *,
    status: str,
    state: Optional[Dict[str, Any]],
    error: Optional[str] = None,
    error_kind: Optional[str] = None,
) -> None:
    """Finalize the open run from the tracker's terminal state."""
    if not user_id:
        return

    async def _do(db):
        connection_id = await _connection_id_for(db, data_source_id)
        if connection_id is None:
            return
        row = await _open_run(db, connection_id, user_id)
        if row is None:
            # No run was opened — a plain client rebuild, or a failure that
            # happened before begin(). Nothing to close; inventing a row here
            # would make history claim a sync that never started.
            return
        payload = state or {}
        total = int(payload.get("endpoints_total") or 0)
        detail = payload.get("detail") or []

        row.status = status
        row.phase = payload.get("phase") or ("error" if error else "done")
        row.finished_at = datetime.utcnow()
        row.progress_done = int(payload.get("endpoints_done") or 0)
        row.progress_total = total
        row.error = (str(error)[:4000] if error else None)
        row.events_json = _events_from_detail(detail, total=total)

        stats = dict(row.stats_json or {})
        stats.update(_stats_from_detail(detail))
        stats["tables"] = int(payload.get("tables") or 0)
        stats["endpoints_failed"] = int(payload.get("endpoints_failed") or 0)
        # The tracker's own status is kept alongside ours: it distinguishes
        # `partial` from `done`, and ConnectionIndexingStatus has no such value.
        # Flattening the two would lose the fact that some workspaces failed on a
        # run we are otherwise calling completed.
        if payload.get("status"):
            stats["result"] = payload["status"]
        if error_kind:
            stats["error_kind"] = error_kind
        row.stats_json = stats

    await _with_session(_do)


async def complete(data_source_id: str, user_id: str, state: Optional[Dict[str, Any]]) -> None:
    """Terminal success — including a partial one (see `result` in stats)."""
    await _close(
        data_source_id, user_id,
        status=ConnectionIndexingStatus.COMPLETED.value,
        state=state,
    )


async def fail(
    data_source_id: str,
    user_id: str,
    state: Optional[Dict[str, Any]],
    error: str,
    error_kind: Optional[str] = None,
) -> None:
    """Terminal failure — the sync itself could not run."""
    await _close(
        data_source_id, user_id,
        status=ConnectionIndexingStatus.FAILED.value,
        state=state,
        error=error,
        error_kind=error_kind,
    )


async def sweep_abandoned() -> int:
    """Close per-user runs whose worker died mid-crawl. Returns how many.

    Without this a killed worker leaves a row reading `running` forever, and the
    activity list shows a sync that has been in progress since last Tuesday.
    Scoped to rows carrying a user_id — the org scope has its own reaper on
    ConnectionIndexingService and the two must not fight over the same rows.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=_ABANDONED_AFTER_MINUTES)

    async def _do(db):
        rows = (await db.execute(
            select(ConnectionIndexing).where(
                ConnectionIndexing.user_id.isnot(None),
                ConnectionIndexing.status.notin_(list(TERMINAL_INDEXING_STATUSES)),
                ConnectionIndexing.started_at.isnot(None),
                ConnectionIndexing.started_at < cutoff,
            )
        )).scalars().all()
        for row in rows:
            row.mark_failed(
                "This sync stopped before it finished — the server was restarted "
                "or the worker running it was replaced. Nothing on your side is wrong."
            )
            stats = dict(row.stats_json or {})
            stats["abandoned"] = True
            stats["error_kind"] = "infrastructure"
            row.stats_json = stats
        return len(rows)

    return await _with_session(_do) or 0


async def latest_run_id(data_source_id: str, user_id: str) -> Optional[str]:
    """The newest run in this member's scope, whatever its state.

    ★For deep-linking a notification at the run it is about. Both connectors
    close the run (`prog.finish` / `prog.fail`) BEFORE they write the
    notification, so "newest" is the run being reported — reading it here rather
    than threading an id through two route files keeps the callers unchanged and
    keeps this the only place that knows how a run is identified.

    Returns None on any failure. A notification with a slightly less specific
    link is a far better outcome than a notification that was not sent.
    """
    if not user_id:
        return None

    async def _do(db):
        connection_id = await _connection_id_for(db, data_source_id)
        if connection_id is None:
            return None
        row = (await db.execute(
            select(ConnectionIndexing.id)
            .where(
                ConnectionIndexing.connection_id == connection_id,
                ConnectionIndexing.user_id == str(user_id),
            )
            .order_by(ConnectionIndexing.created_at.desc())
            .limit(1)
        )).first()
        return str(row[0]) if row else None

    return await _with_session(_do)


async def prune(data_source_id: str, user_id: str) -> int:
    """Keep the newest `_KEEP_RUNS_PER_SCOPE` runs for this scope, drop the rest."""
    if not user_id:
        return 0

    async def _do(db):
        connection_id = await _connection_id_for(db, data_source_id)
        if connection_id is None:
            return 0
        keep = (await db.execute(
            select(ConnectionIndexing.id)
            .where(
                ConnectionIndexing.connection_id == connection_id,
                ConnectionIndexing.user_id == str(user_id),
            )
            .order_by(ConnectionIndexing.created_at.desc())
            .limit(_KEEP_RUNS_PER_SCOPE)
        )).scalars().all()
        if len(keep) < _KEEP_RUNS_PER_SCOPE:
            return 0
        result = await db.execute(
            delete(ConnectionIndexing).where(
                ConnectionIndexing.connection_id == connection_id,
                ConnectionIndexing.user_id == str(user_id),
                ConnectionIndexing.id.notin_(list(keep)),
            )
        )
        return int(result.rowcount or 0)

    return await _with_session(_do) or 0
