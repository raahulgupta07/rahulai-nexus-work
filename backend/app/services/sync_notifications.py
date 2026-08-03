"""Tell the member how their sync ended.

A federated Fabric sync takes minutes. Nobody watches a progress strip for
minutes — they start it, switch tabs, and come back later to an agent that
either knows about their tables or does not, with no record of which happened or
when. The sync strip's own state expires after fifteen minutes
(`connection_sync_progress._TTL_SECONDS`), so a member who steps away for lunch
returns to a screen that says nothing at all.

★No new delivery channel. The per-user inbox (`InboxService`) already exists,
already has an unread badge, already has read/dismiss state, and is already
polled by the front end. Adding an SSE stream beside it would give two places a
sync result can live and one of them would drift.

★Never raises. A notification is an account of what happened; failing to file it
must not change what happened. Every entry point here swallows.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SOURCE_SYNC,
)
from app.services.inbox_service import inbox_service


logger = logging.getLogger(__name__)


# Below this, a sync finished fast enough that the member was probably still
# looking at the strip. Notifying anyway turns the inbox into a log.
NOTIFY_ABOVE_SECONDS = 30


def _duration(seconds: Optional[float]) -> str:
    if not seconds or seconds < 1:
        return ""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m{total % 60:02d}s"


async def _link_for(data_source_id: str, user_id: str, *, to_the_run: bool) -> str:
    """Where this notification should land.

    ★A clean success and a problem want DIFFERENT destinations. "Your agent is
    ready" is an invitation to go and use the data, so it opens the agent. "Two
    workspaces did not answer" is a question, and the answer — which two, and
    with what error — is on the run itself. Sending both to the agent page is
    what made the old body say "open the agent to see which" about a breakdown
    that was not on the agent page.

    Falls back to the agent page whenever the run cannot be identified. A link
    that is merely less specific beats a link to a run that is not there.
    """
    if to_the_run:
        try:
            from app.services.sync_runs import latest_run_id
            run_id = await latest_run_id(str(data_source_id), str(user_id))
            if run_id:
                return f"/agents?keeper=activity&run={run_id}"
        except Exception:  # noqa: BLE001
            logger.warning("sync_notify.link_lookup_failed", exc_info=True)
    return f"/agents/{data_source_id}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


async def notify_sync_finished(
    db: AsyncSession,
    *,
    organization_id: str,
    user_id: str,
    data_source_id: str,
    data_source_name: str,
    tables: int,
    workspaces_done: int = 0,
    workspaces_failed: int = 0,
    elapsed_seconds: Optional[float] = None,
    force: bool = False,
) -> None:
    """A sync that reached a result. Partial counts as finished, with a warning.

    `force` bypasses the duration floor — used by a member-initiated retry,
    where the whole point was to find out whether it worked this time.
    """
    try:
        if not force and (elapsed_seconds or 0) < NOTIFY_ABOVE_SECONDS:
            return

        partial = workspaces_failed > 0
        took = _duration(elapsed_seconds)
        detail = _plural(tables, "table", "tables")
        if workspaces_done:
            detail += f" from {_plural(workspaces_done, 'workspace', 'workspaces')}"
        if took:
            detail += f" in {took}"

        if partial:
            title = f"{data_source_name} synced with {_plural(workspaces_failed, 'gap', 'gaps')}"
            body = (
                f"{detail}. {_plural(workspaces_failed, 'workspace', 'workspaces')} "
                "did not answer — open this sync to see which."
            )
            severity = SEVERITY_WARNING
        elif tables == 0:
            # ★Not silence. Zero tables after a "successful" sync is the most
            # confusing possible outcome, and it is exactly what an empty
            # workspace selection produces — a member who deselected everything
            # last week and forgot deserves to be told why their agent is blank.
            title = f"{data_source_name} synced, but found no tables"
            body = (
                "Nothing was returned. Check which workspaces are selected for "
                "this agent."
            )
            severity = SEVERITY_WARNING
        else:
            title = f"{data_source_name} is ready"
            body = detail
            severity = SEVERITY_INFO

        await inbox_service.notify_users(
            db,
            organization_id=str(organization_id),
            user_ids=[str(user_id)],
            source=SOURCE_SYNC,
            type="sync_finished",
            title=title,
            body=body,
            severity=severity,
            # Partial and zero-table results are questions; a clean success is
            # an invitation. See `_link_for`.
            link=await _link_for(
                data_source_id, user_id, to_the_run=(partial or tables == 0)
            ),
            subject={
                "data_source_id": str(data_source_id),
                "tables": tables,
                "workspaces_done": workspaces_done,
                "workspaces_failed": workspaces_failed,
            },
            source_id=str(data_source_id),
            # One row per agent per outcome — a member who retries four times
            # gets one refreshed notification, not four.
            group_key=f"sync:{data_source_id}:finished",
        )
    except Exception:  # noqa: BLE001
        logger.warning("sync_notify.finished_failed", exc_info=True)


async def notify_sync_failed(
    db: AsyncSession,
    *,
    organization_id: str,
    user_id: str,
    data_source_id: str,
    data_source_name: str,
    message: str,
    error_kind: Optional[str] = None,
) -> None:
    """A sync that could not produce a result.

    ★Always notified, regardless of how fast it failed. The duration floor above
    exists because a quick success is unremarkable; a quick failure is not — and
    the fastest failures are the infrastructure ones, which are precisely the
    ones a member would otherwise never learn about.
    """
    try:
        if error_kind == "infrastructure":
            title = f"{data_source_name} sync was interrupted"
            severity = SEVERITY_WARNING
        else:
            title = f"{data_source_name} sync failed"
            severity = SEVERITY_ERROR

        await inbox_service.notify_users(
            db,
            organization_id=str(organization_id),
            user_ids=[str(user_id)],
            source=SOURCE_SYNC,
            type="sync_failed",
            title=title,
            # The classified sentence, already written for a person by
            # `indexing_failures.describe_failure`. Not re-worded here — two
            # phrasings of one failure is how the inbox and the sync strip end
            # up disagreeing on screen.
            body=message,
            severity=severity,
            # Always the run. A failure is a question by definition, and the
            # answer — the error, the phase it died in, which workspaces had
            # already answered — is on the run and nowhere else.
            link=await _link_for(data_source_id, user_id, to_the_run=True),
            subject={
                "data_source_id": str(data_source_id),
                "error_kind": error_kind,
            },
            source_id=str(data_source_id),
            group_key=f"sync:{data_source_id}:failed",
        )
    except Exception:  # noqa: BLE001
        logger.warning("sync_notify.failed_failed", exc_info=True)


def counts_from_progress(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the numbers a notification needs out of a progress payload."""
    detail: List[Dict[str, Any]] = [
        d for d in (state.get("detail") or []) if isinstance(d, dict)
    ]
    return {
        "tables": int(state.get("tables") or 0),
        "workspaces_done": sum(1 for d in detail if d.get("status") in ("ok", "completed")),
        "workspaces_failed": sum(1 for d in detail if d.get("status") == "failed"),
        "elapsed_seconds": (int(state.get("elapsed_ms") or 0) / 1000.0) or None,
    }
