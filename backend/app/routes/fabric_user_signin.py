"""Routes for the ``fabric_user`` connector — per-user Microsoft sign-in.

Each member connects with their OWN Microsoft email + password (ROPC), with an
automatic device-code fallback for MFA accounts, to a single Fabric Warehouse/
Lakehouse SQL endpoint the admin configured. Credentials persist per-user in
``UserDataSourceCredentials`` (the same store the generic ``/my-credentials``
routes use) — the password is NEVER stored, only the refresh_token.

This mirrors ``powerbi_user_signin`` but is SIMPLER: a Fabric connector points at
ONE SQL endpoint from its config, so there is no multi-tenant dataset scan, no
cross-tenant merge, and no select-tenant step. The member's refresh_token is
redeemed at query time for a fresh Fabric SQL token (see
``data_source_service.resolve_credentials`` ``fabric_user`` branch).

All endpoints require auth (organization + current_user) and operate on a
``data_source_id`` whose connection type is ``fabric_user`` (404 otherwise).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_async_db, get_current_organization
from app.core.auth import current_user
from app.core.permissions_decorator import requires_resource_permission
from app.models.user import User
from app.models.organization import Organization
from app.models.data_source import DataSource
from app.models.user_data_source_credentials import UserDataSourceCredentials

from app.services.fabric_user_signin import try_password_signin
from app.services.powerbi_device_code import (
    start_device_code,
    poll_device_code,
    decode_id_token,
    SCOPE_FABRIC,
)


router = APIRouter(tags=["data_sources"])

_AUTH_MODE = "user_login"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class ConnectBody(BaseModel):
    email: str
    password: str
    tenant_id: Optional[str] = None


class DeviceCodePollBody(BaseModel):
    device_code: str
    tenant_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _load_fabric_user_datasource(
    db: AsyncSession, organization: Organization, data_source_id: str
) -> DataSource:
    """Load a data source and assert its connection type is ``fabric_user``."""
    res = await db.execute(
        select(DataSource).where(
            DataSource.id == data_source_id,
            DataSource.organization_id == organization.id,
        )
    )
    ds = res.scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="Data source not found")
    conn = ds.connections[0] if ds.connections else None
    if not conn or conn.type != "fabric_user":
        raise HTTPException(status_code=404, detail="Not a Microsoft Fabric (User Sign-in) data source")
    return ds


def _default_tenant_id(ds: DataSource) -> Optional[str]:
    """The optional admin-configured default tenant from FabricUserConfig (if any)."""
    import json
    conn = ds.connections[0] if ds.connections else None
    if not conn:
        return None
    config = conn.config
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {}
    return (config or {}).get("default_tenant_id") or None


async def _persist_user_login_credentials(
    db: AsyncSession,
    data_source: DataSource,
    user: User,
    email: str,
    tenant_id: Optional[str],
    refresh_token: Optional[str],
) -> UserDataSourceCredentials:
    """Upsert the member's per-user credentials — password NEVER stored.

    Mirrors ``UserDataSourceCredentialsService.upsert_my_credentials``: find the
    active row, create/update, Fernet-encrypt the payload, enforce a single
    primary. Stored payload:
    ``{"auth_mode":"user_login","username":email,"tenant_id":...,"refresh_token":...}``.
    """
    stmt = (
        select(UserDataSourceCredentials)
        .where(
            UserDataSourceCredentials.data_source_id == data_source.id,
            UserDataSourceCredentials.user_id == user.id,
            UserDataSourceCredentials.is_active == True,  # noqa: E712
        )
        .order_by(
            UserDataSourceCredentials.is_primary.desc(),
            UserDataSourceCredentials.updated_at.desc(),
        )
    )
    row = (await db.execute(stmt)).scalars().first()

    if row is None:
        row = UserDataSourceCredentials(
            data_source_id=str(data_source.id),
            user_id=str(user.id),
            organization_id=str(data_source.organization_id),
            auth_mode=_AUTH_MODE,
            is_active=True,
            is_primary=True,
        )
    else:
        row.auth_mode = _AUTH_MODE
        row.is_active = True

    payload = {
        "auth_mode": _AUTH_MODE,
        "username": email,
        "tenant_id": tenant_id,
        "refresh_token": refresh_token,
    }
    row.encrypt_credentials(payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if row.is_primary:
        await db.execute(
            update(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.id != row.id,
            )
            .values(is_primary=False)
        )
        await db.commit()

    return row


# ★Strong references to in-flight sync tasks. asyncio holds only a WEAK
# reference to a running task, so without this a sync can be garbage-collected
# part-way through — no traceback, no error, just a member whose tables never
# arrive. Entries remove themselves in the task's done callback.
_SYNC_TASKS: set = set()


async def _run_federated_sync(data_source_id: str, user_id: str) -> None:
    """Background federated Fabric sync — opens its OWN DB session (the request's
    session is already closed by the time this runs) and drives the multi-endpoint
    overlay merge, recording progress in the shared registry throughout.

    Everything is swallowed: a failed sync must never crash the loop; it is
    recorded as ``error`` in the progress registry for the UI to surface.
    """
    from app.services import connection_sync_progress as prog
    from app.dependencies import async_session_maker
    from app.services.data_source_service import DataSourceService
    from sqlalchemy.orm import selectinload
    try:
        async with async_session_maker() as db:
            ds = (await db.execute(
                select(DataSource).where(DataSource.id == data_source_id)
                .options(selectinload(DataSource.connections))
                .execution_options(include_deleted=True)
            )).scalars().first()
            user = (await db.execute(
                select(User).where(User.id == user_id)
            )).scalars().first()
            if ds is None or user is None:
                await prog.fail(data_source_id, user_id, "data source or user not found")
                return
            svc = DataSourceService()
            tables = await svc.get_user_data_source_schema(
                db=db, data_source=ds, user=user
            )
            table_count = len(tables or [])

            # ★ORDER MATTERS, and it used to be the other way round.
            #
            # `prog.finish()` was called HERE, and the re-learn was scheduled on
            # the next line. Progress therefore reached a terminal state before
            # the learn began: the crawl is the fast half, the learn is the ~30
            # seconds that follow, and the member watching saw "done" over an
            # agent that could not answer a question yet. The screenshot that
            # started this work was taken five seconds before the learn began.
            #
            # Now: report the learning stage, AWAIT the learn, then finish. This
            # function is already a background task, so awaiting costs nothing —
            # the sign-in request returned long ago.
            await prog.learning(data_source_id, user_id, tables=table_count)
            try:
                await svc.relearn_overview_now(
                    data_source_id=str(ds.id),
                    user_id=str(user.id),
                    organization_id=str(ds.organization_id),
                )
            except Exception as _re:  # noqa: BLE001
                # relearn_overview_now swallows its own failures; this is the
                # belt for anything raised before it gets that far. A failed
                # overview must still leave the member with a synced agent.
                logging.getLogger(__name__).warning(
                    "fabric_user auto re-learn failed for ds=%s: %s", data_source_id, _re
                )
            await prog.finish(data_source_id, user_id, tables=table_count)

            # D.2 — the member has almost certainly switched tabs by now, and
            # the strip's own state expires after fifteen minutes. Read the
            # counts back off the progress row rather than recomputing them, so
            # the inbox and the strip cannot disagree about what happened.
            try:
                from app.services.sync_notifications import (
                    counts_from_progress, notify_sync_finished,
                )
                _state = await prog.get(data_source_id, user_id)
                await notify_sync_finished(
                    db,
                    organization_id=str(ds.organization_id),
                    user_id=str(user.id),
                    data_source_id=str(ds.id),
                    data_source_name=ds.name,
                    **counts_from_progress(_state),
                )
            except Exception as _nx:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "fabric_user sync notification failed for ds=%s: %s",
                    data_source_id, _nx,
                )
            # Attach the shipped Fabric skills. This is the point where a Fabric
            # agent first becomes real for an org that did not have one at
            # signup, so it is the hook that covers upgrades as well as fresh
            # installs. Idempotent; its wrapper swallows every error.
            try:
                from app.models.organization import Organization
                from app.services.builtin_skills_seeder import sync_builtin_skills_safe
                # Fetch the org explicitly — `ds.organization` is not eager-loaded
                # here and a lazy load inside async raises MissingGreenlet.
                _org = (await db.execute(
                    select(Organization).where(Organization.id == ds.organization_id)
                )).scalars().first()
                if _org is not None:
                    await sync_builtin_skills_safe(db, _org, user)
            except Exception as _bs:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "fabric_user builtin skills failed for ds=%s: %s", data_source_id, _bs
                )
    except Exception as e:  # noqa: BLE001
        # ★Classify before reporting. The failure that put this in the plan was
        # our own Postgres refusing a connection mid-crawl; reported as a bare
        # driver message it read as the member's Fabric credential being wrong,
        # and the agent then told them to "attach or refresh the lakehouse".
        from app.services.indexing_failures import classify_failure, describe_failure
        kind = classify_failure(e)
        sentence = describe_failure(e, kind)
        await prog.fail(data_source_id, user_id, sentence, error_kind=kind)

        # ★A fresh session, and the data source re-read. `db`/`ds` above belong
        # to an `async with` that has already exited by the time we get here —
        # touching them raises inside the handler and loses the failure we came
        # to report. The notification matters most in exactly this branch: the
        # member started a sync, walked away, and would otherwise come back to
        # an agent that quietly knows nothing.
        try:
            async with async_session_maker() as ndb:
                nds = (await ndb.execute(
                    select(DataSource).where(DataSource.id == data_source_id)
                    .execution_options(include_deleted=True)
                )).scalars().first()
                if nds is not None:
                    from app.services.sync_notifications import notify_sync_failed
                    await notify_sync_failed(
                        ndb,
                        organization_id=str(nds.organization_id),
                        user_id=str(user_id),
                        data_source_id=str(nds.id),
                        data_source_name=nds.name,
                        message=sentence,
                        error_kind=kind,
                    )
        except Exception as _nx:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "fabric_user failure notification failed for ds=%s: %s",
                data_source_id, _nx,
            )


def _kick_off_sync(data_source: DataSource, user: User) -> None:
    """Mark a sync started and schedule it on the event loop (fire-and-forget).

    Returns immediately so the sign-in request is not blocked on the ~20-30s
    multi-endpoint pull. The UI polls ``/fabric-signin/sync-status`` for progress.
    """
    import asyncio
    ds_id, uid = str(data_source.id), str(user.id)

    async def _tracked() -> None:
        # start() writes the progress row, so it belongs INSIDE the task: the
        # sign-in response must not wait on a database write, and the row is
        # visible to every worker the moment it commits.
        from app.services import connection_sync_progress as prog
        from app.services.sync_runs import TRIGGER_SIGNIN
        await prog.start(ds_id, uid, trigger=TRIGGER_SIGNIN)
        await _run_federated_sync(ds_id, uid)

    try:
        # ★asyncio keeps only a WEAK reference to a running task, so a bare
        # create_task() can be garbage-collected mid-sync — silently, with no
        # traceback. Hold the handle until it is done.
        loop = asyncio.get_running_loop()
        task = loop.create_task(_tracked())
        _SYNC_TASKS.add(task)
        task.add_done_callback(_SYNC_TASKS.discard)
    except RuntimeError:
        # No running loop (shouldn't happen inside a request) — run inline.
        asyncio.run(_tracked())


def _member_error(raw, fallback: str) -> str:
    """A Microsoft failure, said in a way a member can act on.

    ★The raw text is LOGGED, not shown. Microsoft's blob carries the tenant
    name, a trace id and a correlation id — none of which belong in a browser,
    and none of which tell somebody what to do next. `humanize_sentence` keeps
    the AADSTS code so support can still trace it.
    """
    import logging
    if raw:
        logging.getLogger(__name__).info("microsoft sign-in refused: %s", raw)
        from app.services.microsoft_error_text import humanize_sentence
        return humanize_sentence(raw)
    return fallback

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/data_sources/{data_source_id}/fabric-signin/connect")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_connect(
    data_source_id: str,
    payload: ConnectBody,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)
    tenant = (payload.tenant_id or _default_tenant_id(ds) or "organizations")

    result = await try_password_signin(payload.email, payload.password, tenant=tenant)

    if result.get("ok"):
        stored_tenant = payload.tenant_id or result.get("tenant_id") or _default_tenant_id(ds)
        await _persist_user_login_credentials(
            db, ds, current_user, payload.email, stored_tenant, result.get("refresh_token")
        )
        # Kick off the federated multi-endpoint sync in the background; the UI
        # polls sync-status. Returns immediately (no ~30s block).
        _kick_off_sync(ds, current_user)
        return {"status": "connected", "sync": "started"}

    if result.get("mfa_required"):
        dc = start_device_code(tenant or "organizations", scope=SCOPE_FABRIC)
        if not dc.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=_member_error(dc.get("error"), "Could not start the code sign-in."),
            )
        return {
            "status": "mfa_required",
            "user_code": dc.get("user_code"),
            "verification_uri": dc.get("verification_uri"),
            "device_code": dc.get("device_code"),
            "interval": dc.get("interval"),
            "expires_in": dc.get("expires_in"),
            "message": dc.get("message"),
        }

    raise HTTPException(
        status_code=400,
        detail=_member_error(result.get("error"), "Microsoft would not accept that sign-in."),
    )


@router.post("/data_sources/{data_source_id}/fabric-signin/device-code/poll")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_device_code_poll(
    data_source_id: str,
    payload: DeviceCodePollBody,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)
    tenant = (payload.tenant_id or _default_tenant_id(ds) or "organizations")

    res = poll_device_code(tenant, payload.device_code)
    status = res.get("status")

    if status == "pending":
        return {"status": "pending"}

    if status == "success":
        # The token's tid claim gives the concrete home tenant (no password here to
        # run tenant discovery, so we surface just the home tenant).
        claims = decode_id_token(res.get("id_token") or "")
        home_tid = claims.get("tid") or (payload.tenant_id or _default_tenant_id(ds))
        stored_tenant = payload.tenant_id or home_tid
        # We don't know the member's email in the MFA path unless the token carries
        # it — use preferred_username when present.
        email = claims.get("preferred_username") or claims.get("upn") or ""
        await _persist_user_login_credentials(
            db, ds, current_user, email, stored_tenant, res.get("refresh_token")
        )
        _kick_off_sync(ds, current_user)
        return {"status": "connected", "sync": "started"}

    raise HTTPException(
        status_code=400,
        detail=_member_error(res.get("error"), "The code sign-in did not complete."),
    )


@router.get("/data_sources/{data_source_id}/fabric-signin/sync-status")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_sync_status(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Live progress of the current user's background federated sync.

    Returns ``{status, phase, endpoints_total, endpoints_done, endpoints_failed,
    tables, detail[], error, elapsed_ms, last_done_at}``.

    ``status`` is one of ``idle | syncing | done | partial | error``. ``partial``
    means some workspaces answered and some did not — a working agent built on
    fewer workspaces, NOT a failure. ``detail`` names each workspace and what
    happened to it, so the UI can report progress in units the member recognises
    rather than as a percentage.
    """
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)
    from app.services import connection_sync_progress as prog
    # Always a full payload, never None — an absent row is reported as `idle`
    # by the registry itself so no caller has to interpret a missing value.
    return await prog.get(str(ds.id), str(current_user.id))


class WorkspaceSelection(BaseModel):
    """``None`` clears the selection back to "sync everything".

    ★An empty list is a real answer — "sync nothing" — and is stored as such.
    Collapsing it into None is the bug this endpoint's tests exist to catch.
    """
    selected: Optional[list[str]] = None


@router.get("/data_sources/{data_source_id}/fabric-signin/workspaces")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_workspaces(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Every workspace this member can reach, and which ones they sync.

    ``available`` comes from the last sync's endpoint list rather than a live
    discovery call: the picker must open instantly, and discovery costs a
    round trip to Microsoft. A member who has never synced gets an empty list
    and the UI tells them to sync once first — which is honest, and cheaper
    than a spinner on every visit to the settings page.

    ``selected`` is ``null`` when they have never chosen (everything syncs).
    """
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)
    from app.services import connection_sync_progress as prog
    from app.services.user_scope_service import get_selected_endpoints

    state = await prog.get(str(ds.id), str(current_user.id))
    available = [
        {"name": d.get("name"), "workspace": d.get("workspace"), "kind": d.get("kind")}
        for d in (state.get("detail") or [])
        if isinstance(d, dict) and d.get("name")
    ]
    selected = await get_selected_endpoints(db, str(ds.id), str(current_user.id))
    return {
        "available": available,
        "selected": selected,
        # So the UI can say "syncing all 20" rather than showing 20 unticked
        # boxes, which reads as "nothing is selected" — the opposite of true.
        "syncs_everything": selected is None,
    }


@router.put("/data_sources/{data_source_id}/fabric-signin/workspaces")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_set_workspaces(
    data_source_id: str,
    payload: WorkspaceSelection,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Save this member's workspace selection. Takes effect on the next sync.

    Deliberately does NOT kick off a sync. Saving a selection and paying for a
    crawl are separate decisions, and a picker that starts a twenty-minute job
    on every tick is unusable.
    """
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)
    from app.services.user_scope_service import set_selected_endpoints

    stored = await set_selected_endpoints(
        db, str(ds.id), str(current_user.id), payload.selected,
    )
    return {"selected": stored, "syncs_everything": stored is None}


@router.post("/data_sources/{data_source_id}/fabric-signin/resync")
@requires_resource_permission('data_source', 'view')
async def fabric_signin_resync(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Re-run the federated crawl for the signed-in member. No password needed.

    What "Try again" calls after a partial or failed sync. Deliberately not the
    generic ``/my-schema/refresh``: that crawls inside the request and returns a
    count, giving a long silent wait and no progress. This schedules the same
    background sync a sign-in does, so the same status endpoint reports it.

    409 when one is already running — two concurrent crawls would overwrite each
    other's progress and compete for the same Microsoft rate limit.
    """
    ds = await _load_fabric_user_datasource(db, organization, data_source_id)

    from app.core.progress_status import is_running
    from app.services import connection_sync_progress as prog
    current = await prog.get(str(ds.id), str(current_user.id))
    # ★`is_running`, not a literal. This read `== "syncing"`, which a sync in
    # its LEARNING stage does not match — so the 409 stopped guarding exactly
    # when the run was at its longest, and a second crawl could start on top of
    # the first. That is the double-crawl this check exists to prevent.
    if is_running(current.get("status")):
        raise HTTPException(status_code=409, detail="A sync is already running")

    # The crawl reads the stored refresh_token itself; this only confirms the
    # member has one, so a retry with no credential fails clearly instead of
    # scheduling a task that can only give up.
    row = (await db.execute(
        select(UserDataSourceCredentials).where(
            UserDataSourceCredentials.data_source_id == ds.id,
            UserDataSourceCredentials.user_id == current_user.id,
            UserDataSourceCredentials.is_active == True,  # noqa: E712
        )
    )).scalars().first()
    if row is None or not (row.decrypt_credentials() or {}).get("refresh_token"):
        raise HTTPException(status_code=403, detail="Connect your Microsoft account first")

    _kick_off_sync(ds, current_user)
    return {"status": "started"}
