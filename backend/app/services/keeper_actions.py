"""Sync every agent the member can, one at a time.

★A QUEUE, not a fan-out
-----------------------
The obvious implementation starts every sync at once. That is precisely wrong
here: each of these crawls hundreds of Microsoft endpoints on ONE member's
token, against a per-user rate limit they all share. Firing five in parallel
does not finish five times sooner — it makes all five slower, and the throttled
ones fail in a way that reads as "the sync is broken" rather than "we asked too
fast". So they run strictly in sequence.

★Nothing here decides what a member may sync
--------------------------------------------
The agent list comes from `KeeperService._visible_scope`, the same scope every
read on the sync-history screen already goes through. This module adds an
action; it must not become a second, weaker answer to "which agents are yours".

★Skips are reported, never silent
---------------------------------
An agent with no stored Microsoft credential, or one already mid-sync, is not an
error and must not fail the whole request — but "queued 2 of 5" with no
explanation is the shape that gets reported as data loss. Every skip carries a
reason, and the caller renders it.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Hold task handles: asyncio keeps only a weak reference to a running task, so a
# bare create_task can be collected mid-run — silently, with no traceback. The
# same trap the per-connector kickoffs document.
_QUEUE_TASKS: set = set()

# How long one agent may hold the queue before the next one starts anyway. A
# crawl that has genuinely wedged must not park every other agent behind it
# forever; the abandoned-run sweep will close its record out.
_PER_AGENT_TIMEOUT_S = 20 * 60

# How often the queue checks whether the agent in front of it has finished.
_POLL_S = 5


async def _eligible(
    db: AsyncSession, user, organization
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(runnable, skipped) over the member's visible per-user agents."""
    from app.core.progress_status import is_running
    from app.models.connection import Connection
    from app.models.data_source import DataSource
    from app.models.user_data_source_credentials import UserDataSourceCredentials
    from app.schemas.data_source_registry import PER_USER_TOKEN_TYPES
    from app.services import connection_sync_progress as prog
    from app.services.keeper_service import KeeperService

    conn_to_ds, names = await KeeperService()._visible_scope(db, user, organization)
    if not conn_to_ds:
        return [], []

    rows = (await db.execute(
        select(Connection.id, Connection.type).where(
            Connection.id.in_(list(conn_to_ds.keys()))
        )
    )).all()
    type_by_ds: Dict[str, str] = {}
    for conn_id, conn_type in rows:
        if conn_type in PER_USER_TOKEN_TYPES:
            type_by_ds.setdefault(conn_to_ds[str(conn_id)], conn_type)

    runnable: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for ds_id, conn_type in type_by_ds.items():
        entry = {"data_source_id": ds_id, "name": names.get(ds_id, "Unknown agent")}

        state = await prog.get(ds_id, str(user.id))
        if is_running(state.get("status")):
            skipped.append({**entry, "reason": "already_running"})
            continue

        cred = (await db.execute(
            select(UserDataSourceCredentials).where(
                UserDataSourceCredentials.data_source_id == ds_id,
                UserDataSourceCredentials.user_id == str(user.id),
                UserDataSourceCredentials.is_active == True,  # noqa: E712
            ).order_by(
                UserDataSourceCredentials.is_primary.desc(),
                UserDataSourceCredentials.updated_at.desc(),
            )
        )).scalars().first()
        if cred is None or not (cred.decrypt_credentials() or {}).get("refresh_token"):
            # Not an error: the member simply has not connected this one. Saying
            # so is what turns "nothing happened" into an action they can take.
            skipped.append({**entry, "reason": "not_connected"})
            continue

        runnable.append({**entry, "type": conn_type, "refresh_token": (
            cred.decrypt_credentials() or {}
        ).get("refresh_token")})

    runnable.sort(key=lambda a: a["name"].lower())
    skipped.sort(key=lambda a: a["name"].lower())
    return runnable, skipped


async def _run_one(agent: Dict[str, Any], user_id: str, organization_id: str) -> None:
    """Start one sync and wait for it to settle. Never raises."""
    from app.core.progress_status import is_running
    from app.services import connection_sync_progress as prog
    from app.services.sync_runs import TRIGGER_MANUAL

    ds_id = agent["data_source_id"]
    try:
        # ★TRIGGER_MANUAL, not TRIGGER_SIGNIN. The per-connector kickoffs
        # hardcode "signin" because that is genuinely what calls them; a run
        # started from this button is a different thing, and the history screen
        # is only able to say which because the distinction is written here.
        await prog.start(ds_id, user_id, trigger=TRIGGER_MANUAL)

        if agent["type"] == "fabric_user":
            from app.routes.fabric_user_signin import _run_federated_sync
            await _run_federated_sync(ds_id, user_id)
        else:
            from app.routes.powerbi_user_signin import _run_tenant_merge
            await _run_tenant_merge(
                ds_id, user_id, organization_id, agent.get("refresh_token"), None,
            )
    except Exception as err:
        # One agent failing must not take the rest of the queue with it. The
        # per-connector crawl already records its own failure through `prog`; if
        # it died before doing so, close the record here rather than leave a run
        # open for the abandoned sweep to find half an hour later.
        logger.warning(f"keeper sync-all: {ds_id} failed: {err}")
        try:
            state = await prog.get(ds_id, user_id)
            if is_running(state.get("status")):
                await prog.fail(ds_id, user_id, str(err))
        except Exception:
            pass


async def _drain(agents: List[Dict[str, Any]], user_id: str, organization_id: str) -> None:
    """Run the queue. Strictly one at a time — see the module docstring."""
    from app.core.progress_status import is_running
    from app.services import connection_sync_progress as prog

    for agent in agents:
        await _run_one(agent, user_id, organization_id)

        # `_run_one` awaits the crawl, so this is normally already settled. It
        # is kept because a connector that internally schedules its own task
        # would otherwise let the next agent start on top of this one, which is
        # the exact parallelism this queue exists to prevent.
        waited = 0
        while waited < _PER_AGENT_TIMEOUT_S:
            state = await prog.get(agent["data_source_id"], user_id)
            if not is_running(state.get("status")):
                break
            await asyncio.sleep(_POLL_S)
            waited += _POLL_S
        else:
            logger.warning(
                f"keeper sync-all: {agent['data_source_id']} still running after "
                f"{_PER_AGENT_TIMEOUT_S}s — moving on so the queue is not parked"
            )


async def sync_all(db: AsyncSession, user, organization) -> Dict[str, Any]:
    """Queue a sync for every eligible agent and return immediately.

    The response describes the QUEUE, not the result — the syncs themselves are
    watched through the same `/api/keeper` feed everything else on this screen
    reads, so there is one place that knows what is running.
    """
    runnable, skipped = await _eligible(db, user, organization)

    if runnable:
        agents = [
            {k: a[k] for k in ("data_source_id", "name", "type", "refresh_token")}
            for a in runnable
        ]
        uid, org_id = str(user.id), str(organization.id)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_drain(agents, uid, org_id))
            _QUEUE_TASKS.add(task)
            task.add_done_callback(_QUEUE_TASKS.discard)
        except RuntimeError:
            # No running loop — should not happen inside a request.
            await _drain(agents, uid, org_id)

    return {
        "queued": [
            {"data_source_id": a["data_source_id"], "name": a["name"]} for a in runnable
        ],
        "skipped": skipped,
    }
