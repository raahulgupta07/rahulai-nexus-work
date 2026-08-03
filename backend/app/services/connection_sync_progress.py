"""Shared progress registry for per-user connector syncs (fabric_user, powerbi_user).

A member signs in with their own Microsoft account; we then crawl every
workspace that account can reach and merge the result into their private
overlay. That takes tens of seconds. This module records how far it has got so
a status endpoint can report it and the UI can show named progress instead of a
spinner that ends in a window closing.

★Backed by the ``connection_sync_progress`` TABLE, not a dict
-------------------------------------------------------------
``start.sh`` runs uvicorn with up to 4 workers. The sync runs in the worker that
served the sign-in; the browser's poll round-robins across all of them. A
module-level dict is therefore invisible to three polls in four — they get
``idle``, and the UI concludes nothing is running. That is what the previous
``fabric_sync_progress`` module did.

★Every write opens its OWN short-lived session
----------------------------------------------
Deliberate, and the reason this module takes no ``db`` argument. The callers are
mid-crawl, inside a transaction that is building the member's table overlay. If
progress were written on that session, committing it would also commit whatever
half-finished overlay work was pending. A separate session makes a progress
write incapable of affecting the sync it is describing. The cost is about six
short connections per sync, which is nothing next to a multi-workspace crawl.

Every function is best-effort: it swallows its own exceptions and returns
quietly. A tracker failure must never fail a sync that is otherwise working.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.progress_status import normalize as normalize_status, is_terminal
from app.models.connection_sync_progress import ConnectionSyncProgress

logger = logging.getLogger(__name__)

# How long a finished row keeps reporting its result before it reads as idle.
# Long enough that a member who steps away still sees what happened when they
# come back; short enough that last week's sync is not presented as news.
_TTL_SECONDS = 900


def _idle() -> Dict[str, Any]:
    return {
        "status": "idle",
        "phase": None,
        "endpoints_total": 0,
        "endpoints_done": 0,
        "endpoints_failed": 0,
        "tables": 0,
        "detail": [],
        "error": None,
        "error_kind": None,
        "elapsed_ms": 0,
        "last_done_at": None,
    }


def _normalize_detail(entry: Any) -> Any:
    """Canonicalise one endpoint's status without touching anything else."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    if "status" in out:
        out["status"] = normalize_status(out.get("status"))
    return out


def _serialize(row: ConnectionSyncProgress) -> Dict[str, Any]:
    elapsed = 0
    if row.started_at:
        end = row.updated_at or datetime.utcnow()
        elapsed = max(0, int((end - row.started_at).total_seconds() * 1000))
    return {
        # ★Normalised on the way OUT. The stored value keeps whatever this
        # tracker has always written ("syncing", "done", "error"), so no row and
        # no previous version is disturbed; what leaves here is the one
        # vocabulary every tracker now speaks. See app/core/progress_status.py.
        "status": normalize_status(row.status),
        "phase": row.phase,
        "endpoints_total": int(row.endpoints_total or 0),
        "endpoints_done": int(row.endpoints_done or 0),
        "endpoints_failed": int(row.endpoints_failed or 0),
        "tables": int(row.tables or 0),
        # ★Same treatment for the endpoints inside. This payload used to say
        # "done" at the top and "ok" per workspace — two spellings of one word,
        # side by side, in a single response.
        "detail": [_normalize_detail(d) for d in (row.detail or [])],
        "error": row.error,
        "error_kind": getattr(row, "error_kind", None),
        "elapsed_ms": elapsed,
        "last_done_at": row.last_done_at.isoformat() if row.last_done_at else None,
    }


async def _with_session(fn):
    """Run `fn(db)` on a fresh session, committing once. Never raises."""
    try:
        from app.dependencies import async_session_maker
        async with async_session_maker() as db:
            result = await fn(db)
            await db.commit()
            return result
    except Exception as e:  # noqa: BLE001
        logger.warning("connection_sync_progress: %s", e)
        return None


async def _row(db, data_source_id: str, user_id: str) -> Optional[ConnectionSyncProgress]:
    return (await db.execute(
        select(ConnectionSyncProgress).where(
            ConnectionSyncProgress.data_source_id == str(data_source_id),
            ConnectionSyncProgress.user_id == str(user_id or ""),
        )
    )).scalars().first()


async def start(data_source_id: str, user_id: str, trigger: Optional[str] = None) -> None:
    """Begin (or restart) tracking. Counters zeroed, `last_done_at` preserved.

    Also opens a durable run row (``app.services.sync_runs``). The archive is
    hooked HERE rather than at the call sites because this module is already the
    one thing every per-user connector agrees to call: fabric_user and
    powerbi_user both drive start/learning/finish/fail, so history arrives for
    both — and for the next connector — with no third place to remember.
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            row = ConnectionSyncProgress(
                data_source_id=str(data_source_id),
                user_id=str(user_id or ""),
            )
            db.add(row)
        row.status = "syncing"
        row.phase = "discovering"
        row.endpoints_total = 0
        row.endpoints_done = 0
        row.endpoints_failed = 0
        row.tables = 0
        row.detail = []
        row.error = None
        row.started_at = datetime.utcnow()
        # last_done_at deliberately untouched — "when did I last sync
        # successfully" must survive the start of a run that may yet fail.
    await _with_session(_do)
    from app.services import sync_runs
    await sync_runs.begin(data_source_id, user_id, trigger=trigger)


async def update(data_source_id: str, user_id: str, **fields) -> None:
    """Patch fields on an in-flight row. No-op when nothing is being tracked."""
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            return
        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        # SQLAlchemy does not see an in-place mutation of a JSON column, so a
        # caller appending to `detail` must reassign it. Reassigning here makes
        # that impossible to get wrong.
        if "detail" in fields:
            row.detail = [dict(d) if isinstance(d, dict) else d for d in (fields["detail"] or [])]
            flag_modified(row, "detail")
        row.updated_at = datetime.utcnow()
    await _with_session(_do)


async def set_endpoints(
    data_source_id: str, user_id: str, endpoints: List[Dict[str, Any]]
) -> None:
    """Publish the discovered workspace list, all pending, and switch to ingesting."""
    # Keys follow what `fabric_discovery.discover_endpoints` emits; the
    # `.get(... ) or .get(...)` chains let the Power BI scan pass its own shape
    # (name/kind) without a translation layer in the caller.
    detail = [
        {
            "name": e.get("database") or e.get("name") or "workspace",
            "kind": e.get("item_type") or e.get("kind") or "lakehouse",
            "workspace": e.get("workspace_name"),
            "tenant": e.get("tenant_name") or e.get("tenant_id"),
            "status": "pending",
            "tables": 0,
            "error": None,
        }
        for e in (endpoints or [])
    ]
    await update(
        data_source_id, user_id,
        phase="ingesting", endpoints_total=len(detail), detail=detail,
    )


async def endpoint_done(
    data_source_id: str,
    user_id: str,
    name: str,
    tables: int = 0,
    error: Optional[str] = None,
) -> None:
    """Mark one workspace finished — ok or failed — and advance the counters.

    Matched on name. An endpoint that is not in the published list is appended
    rather than dropped, so a discovery that under-reported still shows the
    truth about what was actually read.
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            return
        # ★★★`[dict(d) ...]`, not `list(...)`. A shallow copy of the list keeps
        # the SAME dict objects SQLAlchemy loaded, so mutating one below also
        # mutates the value the ORM compares against — it sees no change and
        # never writes the column. `endpoints_done` is a scalar and DID persist,
        # so the counters moved while the per-workspace list stayed "pending"
        # forever. The docstring on `update()` warns about in-place JSON
        # mutation and the reassignment it prescribes is not enough on its own:
        # reassigning an object you have already mutated changes nothing.
        detail = [dict(d) if isinstance(d, dict) else d for d in (row.detail or [])]
        hit = next((d for d in detail if d.get("name") == name), None)
        if hit is None:
            hit = {"name": name, "kind": None, "tenant": None}
            detail.append(hit)
        hit["status"] = "failed" if error else "ok"
        hit["tables"] = int(tables or 0)
        hit["error"] = str(error)[:200] if error else None

        row.detail = detail
        # Explicit, because a JSON column's change detection is exactly what
        # failed here. Cheap, and it cannot be wrong.
        flag_modified(row, "detail")
        row.endpoints_done = sum(1 for d in detail if d.get("status") == "ok")
        row.endpoints_failed = sum(1 for d in detail if d.get("status") == "failed")
        if row.endpoints_total < len(detail):
            row.endpoints_total = len(detail)
        row.updated_at = datetime.utcnow()
    await _with_session(_do)


async def learning(data_source_id: str, user_id: str, tables: int) -> None:
    """Reading is done; the agent is now writing its overview.

    ★This stage existed and was invisible. `finish()` used to be called the
    moment the crawl ended, four lines before the learn was even scheduled — so
    progress reached a terminal state while the longest and most interesting
    part of connecting was still running, and a member watching saw "done" over
    an agent that could not yet answer a question.

    Deliberately NOT terminal: `last_done_at` is untouched, so nothing treats
    this as a finished sync. `finish()` is still the only terminal success.
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            return
        row.status = "learning"
        row.phase = "learning"
        row.tables = int(tables or 0)
        row.error = None
        row.updated_at = datetime.utcnow()
    await _with_session(_do)


async def finish(data_source_id: str, user_id: str, tables: int) -> None:
    """Terminal success. Becomes `partial` when any workspace failed to answer.

    ★`partial` is a success, not a failure: the member has a working agent built
    on the workspaces that answered. It is a distinct status only so the UI can
    name the ones it could not read, instead of implying coverage it does not
    have.
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            return
        row.status = "partial" if int(row.endpoints_failed or 0) > 0 else "done"
        row.phase = "done"
        row.tables = int(tables or 0)
        row.error = None
        row.last_done_at = datetime.utcnow()
        row.updated_at = row.last_done_at
    await _with_session(_do)
    # Archive from the row we just wrote, not from the caller's arguments, so
    # history and the strip can never disagree about what this run found.
    from app.services import sync_runs
    state = await get(data_source_id, user_id)
    await sync_runs.complete(data_source_id, user_id, state)
    await sync_runs.prune(data_source_id, user_id)


async def fail(
    data_source_id: str,
    user_id: str,
    error: str,
    error_kind: Optional[str] = None,
) -> None:
    """Terminal failure — the sync itself could not run.

    ``error_kind`` says which side failed, so the strip can word our own outage
    as ours rather than telling the member to check a credential that is fine.
    Optional: callers that genuinely cannot tell leave it unset rather than
    guessing, and NULL means "no claim about cause".
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            row = ConnectionSyncProgress(
                data_source_id=str(data_source_id),
                user_id=str(user_id or ""),
                started_at=datetime.utcnow(),
            )
            db.add(row)
        row.status = "error"
        row.phase = "error"
        row.error = str(error)[:300]
        row.error_kind = error_kind
        row.updated_at = datetime.utcnow()
    await _with_session(_do)
    from app.services import sync_runs
    state = await get(data_source_id, user_id)
    await sync_runs.fail(data_source_id, user_id, state, error=str(error), error_kind=error_kind)


async def get(data_source_id: str, user_id: str) -> Dict[str, Any]:
    """Current progress, or an idle payload.

    Always returns a full dict — never None — so no caller has to decide what an
    absent row means. A terminal row older than the TTL reads as idle but keeps
    `last_done_at`, which is the one fact still worth showing.
    """
    async def _do(db):
        row = await _row(db, data_source_id, user_id)
        if row is None:
            return _idle()
        payload = _serialize(row)
        # ★Reads the CANONICAL value, because `payload` is already normalised.
        # A literal list here would have to be kept in step with the writers by
        # hand, which is the coupling this whole change removes.
        if is_terminal(payload["status"]):
            ref = row.updated_at or row.started_at
            if ref and (datetime.utcnow() - ref).total_seconds() > _TTL_SECONDS:
                idle = _idle()
                idle["last_done_at"] = payload["last_done_at"]
                return idle
        return payload

    result = await _with_session(_do)
    return result if result is not None else _idle()
