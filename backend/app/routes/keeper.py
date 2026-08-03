"""The sync-history screen's endpoints.

★No permission decorator, and that is the deliberate choice
-----------------------------------------------------------
These return the CALLER'S OWN sync runs on agents the caller can already see.
Both halves of that are enforced inside `KeeperService` by construction: every
query starts from the member's visible data sources and is filtered to
``user_id == current_user.id``. A decorator here would gate a screen that shows
a member their own work — which is what `manage_settings` on the People page
correctly does and what this correctly does not.

An admin view over OTHER members' runs is a different endpoint with a real
permission on it, and is not built. A Fabric sync reports what one member's
Microsoft account could reach, so widening the scope is a decision about
disclosing colleagues' workspace names, not a display option.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.dependencies import get_async_db, get_current_organization
from app.models.organization import Organization
from app.models.user import User
from app.services.keeper_service import KeeperService

router = APIRouter(tags=["keeper"])


@router.get("/keeper")
async def keeper_overview(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """Everything the screen shows before anyone clicks anything.

    ``working_now`` — syncs in flight right now.
    ``today``       — counts since midnight UTC.
    ``agents``      — one entry per visible agent, newest run first, including
                      agents that have never synced (which is a fact worth
                      showing, not an empty row to hide).
    ``needs_a_person`` — only what a member can act on; our own outages are
                      excluded on purpose.
    ``recent``      — a preview; the activity endpoint is the full list.
    """
    return await KeeperService().overview(db, user, organization)


@router.get("/keeper/activity")
async def keeper_activity(
    data_source_id: Optional[str] = Query(None),
    problems_only: bool = Query(False),
    days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """The member's sync runs, newest first.

    ``problems_only`` keeps failures AND successes that missed a workspace — a
    sync that quietly returned four of five lakehouses is the case this filter
    exists for, and calling it a success would bury it.
    """
    return await KeeperService().activity(
        db, user, organization,
        data_source_id=data_source_id,
        problems_only=problems_only,
        days=days,
        limit=limit,
        offset=offset,
    )


@router.post("/keeper/sync-all")
async def keeper_sync_all(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """Queue a sync for every agent this member can sync, one at a time.

    ★Returns 200 with an empty queue when there is nothing to do, not an error.
    "Nothing was eligible" is a normal answer — every agent already syncing is
    the commonest case, and a 4xx there would put a red toast on a screen where
    the correct response is "they are all already running".

    The response describes the queue; progress is watched through `/api/keeper`,
    which is the one place that knows what is running.
    """
    from app.services import keeper_actions

    return await keeper_actions.sync_all(db, user, organization)


@router.get("/keeper/schedule")
async def keeper_schedule(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """What runs by itself, and what waits for the member to sign in.

    ★Declared BEFORE `/keeper/runs/{run_id}` matters not at all here (different
    prefixes), but the ordering is kept deliberate: a future `/keeper/{x}` route
    added above this one would swallow it.
    """
    return await KeeperService().schedule(db, user, organization)


@router.get("/keeper/runs/{run_id}")
async def keeper_run(
    run_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """One run: per-workspace breakdown and event log.

    404 covers "no such run", "not yours" and "agent you cannot see" alike —
    telling them apart would confirm a run exists to someone not entitled to
    know that.
    """
    payload = await KeeperService().run_detail(db, user, organization, run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return payload
