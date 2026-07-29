"""Routes for the ``powerbi_user`` connector — per-user Microsoft sign-in.

Each member connects with their OWN Microsoft email + password (ROPC), with an
automatic device-code fallback for MFA accounts, plus best-effort cross-tenant
discovery. Credentials persist per-user in ``UserDataSourceCredentials`` (the same
store the generic ``/my-credentials`` routes use) — the password is NEVER stored,
only the refresh_token.

All endpoints require auth (organization + current_user) and operate on a
``data_source_id`` whose connection type is ``powerbi_user`` (404 otherwise).
"""
from __future__ import annotations

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

from app.services.powerbi_user_signin import (
    try_password_signin,
    discover_user_tenants,
)
from app.services.powerbi_device_code import (
    start_device_code,
    poll_device_code,
    decode_id_token,
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


class SelectTenantBody(BaseModel):
    tenant_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _load_powerbi_user_datasource(
    db: AsyncSession, organization: Organization, data_source_id: str
) -> DataSource:
    """Load a data source and assert its connection type is ``powerbi_user``."""
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
    if not conn or conn.type != "powerbi_user":
        raise HTTPException(status_code=404, detail="Not a Power BI (User Sign-in) data source")
    return ds


def _default_tenant_id(ds: DataSource) -> Optional[str]:
    """The optional admin-configured default tenant from PowerBIUserConfig."""
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


async def _refresh_user_overlay(db: AsyncSession, data_source: DataSource, user: User) -> None:
    """Best-effort per-user schema overlay refresh (same call the OAuth path uses).

    Sign-in success must NOT fail if this errors, so everything is swallowed.
    """
    try:
        from app.services.data_source_service import DataSourceService
        await DataSourceService().get_user_data_source_schema(
            db=db, data_source=data_source, user=user
        )
    except Exception:
        pass


def _workspaces_config(ds: DataSource) -> Optional[str]:
    """Optional admin-configured workspace filter from PowerBIUserConfig."""
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
    return (config or {}).get("workspaces") or None


async def _persist_uds_tenant_data(
    db: AsyncSession,
    data_source: DataSource,
    user: User,
    tenant_tokens: dict,
    tenants: list,
) -> None:
    """Merge the per-tenant refresh_token map + discovered tenant list onto the
    member's ``UserDataSourceCredentials`` payload (where the powerbi_user
    refresh_token already lives), so query-time routing can mint a fresh
    tenant-scoped token and status can surface the reachable tenants.

    Best-effort: never raises.
    """
    try:
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
            return
        creds = row.decrypt_credentials() or {}
        if tenant_tokens:
            existing = creds.get("tenant_tokens") or {}
            existing.update(tenant_tokens)
            creds["tenant_tokens"] = existing
        if tenants:
            # Store a lean [{id,name}] list for status surfacing.
            creds["tenants"] = [
                {"id": t.get("id"), "name": t.get("name")}
                for t in tenants if t.get("id")
            ]
        row.encrypt_credentials(creds)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    except Exception:
        pass


async def _merge_all_tenants(
    db: AsyncSession,
    data_source: DataSource,
    user: User,
    organization: Organization,
    refresh_token: Optional[str],
    fallback_tenants: Optional[list] = None,
    on_discovered=None,
    on_tenant=None,
) -> list:
    """AUTO-MERGE tables from EVERY tenant the member can reach (no tenant picking).

    Reuses the ``powerbi_multitenant_scan`` engine as a PUBLIC (FOCI) client — no
    app-registration secret exists for the ``powerbi_user`` connector, so we pass
    the FOCI public client id and ``client_secret=None`` (the engine then omits
    the secret and redeems tokens exactly like ``mint_access_token`` does).

    Everything is best-effort: sign-in must NEVER fail if the merge errors. On
    success the per-tenant refresh_token map is persisted onto the member's
    UserDataSourceCredentials, the merged tenants are recorded for status, and
    the datasource overview/primary instruction is regenerated (best-effort).

    Returns the list of merged tenants ``[{id,name,...}]`` — or ``fallback_tenants``
    (then a single-tenant overlay refresh) when the merge yields nothing.
    """
    from app.services import powerbi_multitenant_scan as mt_scan

    if not refresh_token:
        await _refresh_user_overlay(db, data_source, user)
        return fallback_tenants or []

    try:
        scan = await mt_scan.scan_all_tenants(
            db=db,
            data_source=data_source,
            user=user,
            home_refresh_token=refresh_token,
            client_id=mt_scan._PUBLIC_CLIENT,  # FOCI public client — no secret
            client_secret=None,                # public client redemption
            workspaces=_workspaces_config(data_source),
            persist_tokens=False,              # store on UDS creds, not conn creds
            on_discovered=on_discovered,       # live progress; None = unchanged
            on_tenant=on_tenant,
        )
    except Exception:
        # Engine never raises, but belt-and-suspenders: fall back cleanly.
        await _refresh_user_overlay(db, data_source, user)
        return fallback_tenants or []

    merged_tenants = scan.get("tenants") or []
    tenant_tokens = scan.get("tenant_tokens") or {}

    if scan.get("tables_merged"):
        # Persist per-tenant tokens + tenant list onto the member's UDS creds.
        await _persist_uds_tenant_data(db, data_source, user, tenant_tokens, merged_tenants)
        # ★The overview re-learn used to be fired from HERE, into the background,
        # on both of this function's exits. That made it invisible: the caller had
        # no way to await it, so it marked the sync finished while the longest
        # stage had not started — the same fault Fabric had. The learn now belongs
        # to `_run_tenant_merge`, which owns the progress tracker and can report it.
        return merged_tenants

    # Merge discovered no tables — keep the classic single-tenant overlay refresh
    # so a lone-tenant member still gets their tables, and surface whatever tenant
    # list we already have.
    await _refresh_user_overlay(db, data_source, user)
    if merged_tenants:
        await _persist_uds_tenant_data(db, data_source, user, tenant_tokens, merged_tenants)
        return merged_tenants
    return fallback_tenants or []


# ---------------------------------------------------------------------------
# Background tenant merge
#
# ★The whole reason this exists. `_merge_all_tenants` crawls every Microsoft
# tenant the member can reach, and it used to run INSIDE the sign-in request —
# so the browser sat on a spinning button for the length of a full multi-tenant
# scan, and the reply carried no progress marker, so the sign-in window closed
# the instant it returned. From the member's side: a long unexplained wait, then
# the screen vanishes. Nothing was broken; there was simply nothing left to look
# at, and the overview re-learn that follows had no surface at all.
#
# Now the request returns as soon as the credential is stored, and the crawl runs
# here, reporting into the shared progress registry that both connectors use.
# This mirrors what `fabric_user` already did.
# ---------------------------------------------------------------------------
# Strong references to in-flight merges. asyncio holds only a WEAK reference to
# a running task, so without this the merge can be garbage-collected part-way
# through — silently, no traceback, member's tables never appear.
_SYNC_TASKS: set = set()


async def _run_tenant_merge(
    data_source_id: str,
    user_id: str,
    organization_id: str,
    refresh_token: Optional[str],
    fallback_tenants: Optional[list],
) -> None:
    """Crawl and merge every reachable tenant, on its own DB session.

    The request's session is closed by the time this runs, so it opens its own
    and reloads the three objects it needs. Everything is swallowed: a failed
    merge is recorded as an error for the UI to surface, never a crashed loop.
    """
    import logging
    from sqlalchemy.orm import selectinload
    from app.dependencies import async_session_maker
    from app.services import connection_sync_progress as prog
    from app.services.data_source_service import DataSourceService

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
            org = (await db.execute(
                select(Organization).where(Organization.id == organization_id)
            )).scalars().first()
            if ds is None or user is None or org is None:
                await prog.fail(data_source_id, user_id, "data source, user or organization not found")
                return

            await prog.update(data_source_id, user_id, phase="ingesting")

            # Live progress. The scan is one long await, so without these hooks
            # the member would see nothing until every tenant had been crawled —
            # which on a multi-tenant account is the whole wait.
            async def _discovered(tenants: list) -> None:
                await prog.set_endpoints(
                    data_source_id, user_id,
                    [
                        {"name": t.get("name") or t.get("id"), "kind": "tenant"}
                        for t in (tenants or [])
                    ],
                )

            async def _tenant_done(name: str, tables: int, error) -> None:
                await prog.endpoint_done(
                    data_source_id, user_id, name, tables=tables, error=error,
                )

            tenants = await _merge_all_tenants(
                db, ds, user, org, refresh_token,
                fallback_tenants=fallback_tenants,
                on_discovered=_discovered,
                on_tenant=_tenant_done,
            )

            total = sum(int(t.get("tables") or 0) for t in (tenants or []))

            # ★ORDER MATTERS. Reading the tenants is the fast half; writing the
            # agent's overview is the ~30 seconds that follow, and it used to run
            # unreported in the background while `finish()` had already been
            # called. Report the stage, await the learn, THEN finish.
            await prog.learning(data_source_id, user_id, tables=total)
            try:
                await DataSourceService().relearn_overview_now(
                    data_source_id=str(ds.id),
                    user_id=str(user.id),
                    organization_id=str(org.id),
                )
            except Exception as _re:  # noqa: BLE001
                # A failed learn must not fail the sync. A member with tables and
                # no overview has a working agent.
                logging.getLogger(__name__).warning(
                    "powerbi_user auto re-learn failed for ds=%s: %s", data_source_id, _re
                )
            await prog.finish(data_source_id, user_id, tables=total)
    except Exception as e:  # noqa: BLE001
        import logging as _l
        _l.getLogger(__name__).warning(
            "powerbi_user tenant merge failed for ds=%s: %s", data_source_id, e
        )
        await prog.fail(data_source_id, user_id, str(e))


def _kick_off_merge(
    data_source: DataSource,
    user: User,
    organization: Organization,
    refresh_token: Optional[str],
    fallback_tenants: Optional[list],
) -> None:
    """Schedule the merge and return immediately. Never blocks the sign-in."""
    import asyncio
    ds_id, uid, org_id = str(data_source.id), str(user.id), str(organization.id)

    async def _tracked() -> None:
        from app.services import connection_sync_progress as prog
        # start() writes a row, so it belongs inside the task — the sign-in
        # response must not wait on a database write.
        await prog.start(ds_id, uid)
        await _run_tenant_merge(ds_id, uid, org_id, refresh_token, fallback_tenants)

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_tracked())
        _SYNC_TASKS.add(task)
        task.add_done_callback(_SYNC_TASKS.discard)
    except RuntimeError:
        # No running loop (should not happen inside a request) — run inline.
        asyncio.run(_tracked())


def _kick_off_tracked_learn(
    data_source: DataSource,
    user: User,
    organization: Organization,
) -> None:
    """Re-learn the overview in the background, but SAY SO while it runs.

    ★For request paths that cannot await a ~30-second learn. The plain
    fire-and-forget `schedule_overview_relearn` leaves the screen with nothing to
    show; this reports `learning` first and finishes the tracker after, so the
    same background work is watchable.

    Reads the table count off the existing progress row rather than re-counting —
    the crawl that produced it has already finished by the time anything calls
    this, and an invented number would be worse than the real one.
    """
    import asyncio
    ds_id, uid, org_id = str(data_source.id), str(user.id), str(organization.id)

    async def _tracked() -> None:
        import logging
        from app.services import connection_sync_progress as prog
        from app.services.data_source_service import DataSourceService

        tables = 0
        try:
            snap = await prog.get(ds_id, uid)
            tables = int((snap or {}).get("tables") or 0)
        except Exception:  # noqa: BLE001
            pass

        await prog.learning(ds_id, uid, tables=tables)
        try:
            await DataSourceService().relearn_overview_now(
                data_source_id=ds_id, user_id=uid, organization_id=org_id,
            )
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "powerbi_user tracked re-learn failed for ds=%s: %s", ds_id, e
            )
        await prog.finish(ds_id, uid, tables=tables)

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_tracked())
        # ★Strong reference — asyncio holds only a weak one, so without this the
        # learn can be collected part-way through, silently.
        _SYNC_TASKS.add(task)
        task.add_done_callback(_SYNC_TASKS.discard)
    except RuntimeError:
        pass


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
@router.post("/data_sources/{data_source_id}/user-signin/connect")
@requires_resource_permission('data_source', 'view')
async def user_signin_connect(
    data_source_id: str,
    payload: ConnectBody,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    ds = await _load_powerbi_user_datasource(db, organization, data_source_id)
    tenant = (payload.tenant_id or _default_tenant_id(ds) or "organizations")

    result = await try_password_signin(payload.email, payload.password, tenant=tenant)

    if result.get("ok"):
        stored_tenant = payload.tenant_id or result.get("tenant_id") or _default_tenant_id(ds)
        await _persist_user_login_credentials(
            db, ds, current_user, payload.email, stored_tenant, result.get("refresh_token")
        )
        # Best-effort cross-tenant discovery (password still in hand here only) —
        # used only as a fallback tenant list if the token-driven merge finds none.
        fallback_tenants, _err = await discover_user_tenants(payload.email, payload.password)
        # AUTO-MERGE every reachable tenant's tables in the BACKGROUND — no tenant
        # picking, and no blocking. The UI polls `/user-signin/sync-status`.
        _kick_off_merge(ds, current_user, organization, result.get("refresh_token"), fallback_tenants)
        return {"status": "connected", "sync": "started"}

    if result.get("mfa_required"):
        dc = start_device_code(tenant or "organizations")
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


@router.post("/data_sources/{data_source_id}/user-signin/device-code/poll")
@requires_resource_permission('data_source', 'view')
async def user_signin_device_code_poll(
    data_source_id: str,
    payload: DeviceCodePollBody,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    ds = await _load_powerbi_user_datasource(db, organization, data_source_id)
    tenant = (payload.tenant_id or _default_tenant_id(ds) or "organizations")

    res = poll_device_code(tenant, payload.device_code)
    status = res.get("status")

    if status == "pending":
        return {"status": "pending"}

    if status == "success":
        # The token's tid claim gives the concrete home tenant (no password here to
        # run ARM tenant discovery, so we surface just the home tenant).
        claims = decode_id_token(res.get("id_token") or "")
        home_tid = claims.get("tid") or (payload.tenant_id or _default_tenant_id(ds))
        stored_tenant = payload.tenant_id or home_tid
        # We don't know the member's email in the MFA path unless the token carries
        # it — use preferred_username when present.
        email = claims.get("preferred_username") or claims.get("upn") or ""
        await _persist_user_login_credentials(
            db, ds, current_user, email, stored_tenant, res.get("refresh_token")
        )
        # Fallback single-tenant list from the id_token, used only if the
        # token-driven cross-tenant merge discovers nothing.
        fallback_tenants = []
        if home_tid:
            fallback_tenants = [{
                "id": home_tid,
                "name": claims.get("tenant_display_name") or "(home tenant)",
                "domain": (email.split("@", 1)[1] if "@" in email else None),
            }]
        # AUTO-MERGE every reachable tenant's tables in the BACKGROUND.
        _kick_off_merge(ds, current_user, organization, res.get("refresh_token"), fallback_tenants)
        return {"status": "connected", "sync": "started"}

    raise HTTPException(
        status_code=400,
        detail=_member_error(res.get("error"), "The code sign-in did not complete."),
    )


@router.post("/data_sources/{data_source_id}/user-signin/select-tenant")
@requires_resource_permission('data_source', 'view')
async def user_signin_select_tenant(
    data_source_id: str,
    payload: SelectTenantBody,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    ds = await _load_powerbi_user_datasource(db, organization, data_source_id)

    stmt = (
        select(UserDataSourceCredentials)
        .where(
            UserDataSourceCredentials.data_source_id == ds.id,
            UserDataSourceCredentials.user_id == current_user.id,
            UserDataSourceCredentials.is_active == True,  # noqa: E712
        )
        .order_by(
            UserDataSourceCredentials.is_primary.desc(),
            UserDataSourceCredentials.updated_at.desc(),
        )
    )
    row = (await db.execute(stmt)).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="No stored credentials to update")

    creds = row.decrypt_credentials() or {}
    creds["tenant_id"] = payload.tenant_id
    row.encrypt_credentials(creds)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await _refresh_user_overlay(db, ds, current_user)
    # ★Reported, not silent. This is a request path, so the learn cannot be
    # awaited here — but it can still be WATCHED. `_kick_off_tracked_learn`
    # marks the learning stage before it starts and finishes the tracker after,
    # so the strip says "Learning" for the whole ~30 seconds instead of jumping
    # to a settled state with the overview still unwritten.
    _kick_off_tracked_learn(ds, current_user, organization)
    return {"status": "ok", "tenant_id": payload.tenant_id}


@router.get("/data_sources/{data_source_id}/user-signin/sync-status")
@requires_resource_permission('data_source', 'view')
async def user_signin_sync_status(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Live progress of the current user's background tenant merge.

    Same payload and same vocabulary as the Fabric equivalent, deliberately:
    the UI polls one shape regardless of which Microsoft connector it is looking
    at. Returns ``{status, phase, endpoints_total, endpoints_done,
    endpoints_failed, tables, detail[], error, elapsed_ms, last_done_at}`` where
    ``status`` is ``idle | syncing | done | partial | error``.

    Per-user by construction — the row is keyed on (data_source, member), so one
    member polling can never see another's sync.
    """
    ds = await _load_powerbi_user_datasource(db, organization, data_source_id)
    from app.services import connection_sync_progress as prog
    return await prog.get(str(ds.id), str(current_user.id))


@router.post("/data_sources/{data_source_id}/user-signin/resync")
@requires_resource_permission('data_source', 'view')
async def user_signin_resync(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Re-run the tenant crawl for the signed-in member. No password needed.

    This is what the "Try again" action calls after a partial or failed sync.
    It deliberately does NOT reuse the generic ``/my-schema/refresh`` route:
    that one crawls inside the request and returns a count, so a member would
    get a long silent wait and no progress — the exact shape being fixed. This
    schedules the same background merge a sign-in does, so the same status
    endpoint reports it.

    409 when a sync is already running: firing a second crawl over the first
    would have them overwrite each other's progress and compete for the same
    Microsoft rate limit.
    """
    ds = await _load_powerbi_user_datasource(db, organization, data_source_id)

    from app.core.progress_status import is_running
    from app.services import connection_sync_progress as prog
    current = await prog.get(str(ds.id), str(current_user.id))
    # ★`is_running`, not a literal. This read `== "syncing"`, which a sync in
    # its LEARNING stage does not match — so the 409 stopped guarding exactly
    # when the run was at its longest, and a second crawl could start on top of
    # the first. That is the double-crawl this check exists to prevent.
    if is_running(current.get("status")):
        raise HTTPException(status_code=409, detail="A sync is already running")

    row = (await db.execute(
        select(UserDataSourceCredentials).where(
            UserDataSourceCredentials.data_source_id == ds.id,
            UserDataSourceCredentials.user_id == current_user.id,
            UserDataSourceCredentials.is_active == True,  # noqa: E712
        ).order_by(
            UserDataSourceCredentials.is_primary.desc(),
            UserDataSourceCredentials.updated_at.desc(),
        )
    )).scalars().first()
    if row is None:
        raise HTTPException(status_code=403, detail="Connect your Microsoft account first")

    refresh_token = (row.decrypt_credentials() or {}).get("refresh_token")
    if not refresh_token:
        # The stored credential cannot mint a token, so a retry would fail the
        # same way. Say what actually fixes it rather than looping.
        raise HTTPException(
            status_code=400,
            detail="Your stored sign-in cannot be refreshed. Reconnect your Microsoft account.",
        )

    _kick_off_merge(ds, current_user, organization, refresh_token, None)
    return {"status": "started"}
