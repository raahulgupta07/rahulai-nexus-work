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
        # Auto re-learn the overview on the now-real merged schema, in the
        # BACKGROUND (fire-and-forget) so sign-in returns immediately. force_llm
        # regenerates even when the agent has use_llm_sync off. Replaces the prior
        # synchronous llm_sync call. Never raises.
        try:
            from app.services.data_source_service import DataSourceService
            DataSourceService().schedule_overview_relearn(
                data_source_id=str(data_source.id),
                user_id=str(user.id),
                organization_id=str(organization.id),
            )
        except Exception:
            pass
        return merged_tenants

    # Merge discovered no tables — keep the classic single-tenant overlay refresh
    # so a lone-tenant member still gets their tables, and surface whatever tenant
    # list we already have.
    await _refresh_user_overlay(db, data_source, user)
    try:
        from app.services.data_source_service import DataSourceService
        DataSourceService().schedule_overview_relearn(
            data_source_id=str(data_source.id),
            user_id=str(user.id),
            organization_id=str(organization.id),
        )
    except Exception:
        pass
    if merged_tenants:
        await _persist_uds_tenant_data(db, data_source, user, tenant_tokens, merged_tenants)
        return merged_tenants
    return fallback_tenants or []


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
        # AUTO-MERGE every reachable tenant's tables — NO tenant picking. Always
        # returns the full merged tenant list; never requires select-tenant.
        tenants = await _merge_all_tenants(
            db, ds, current_user, organization, result.get("refresh_token"),
            fallback_tenants=fallback_tenants,
        )
        return {"status": "connected", "tenants": tenants}

    if result.get("mfa_required"):
        dc = start_device_code(tenant or "organizations")
        if not dc.get("ok"):
            raise HTTPException(status_code=400, detail=dc.get("error", "Could not start device-code sign-in"))
        return {
            "status": "mfa_required",
            "user_code": dc.get("user_code"),
            "verification_uri": dc.get("verification_uri"),
            "device_code": dc.get("device_code"),
            "interval": dc.get("interval"),
            "expires_in": dc.get("expires_in"),
            "message": dc.get("message"),
        }

    raise HTTPException(status_code=400, detail=result.get("error", "Sign-in failed"))


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
        # AUTO-MERGE every reachable tenant's tables — NO tenant picking.
        tenants = await _merge_all_tenants(
            db, ds, current_user, organization, res.get("refresh_token"),
            fallback_tenants=fallback_tenants,
        )
        return {"status": "connected", "tenants": tenants}

    raise HTTPException(status_code=400, detail=res.get("error", "Device-code sign-in failed"))


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
    # Re-learn the overview on the (re)synced schema in the background — never blocks.
    try:
        from app.services.data_source_service import DataSourceService
        DataSourceService().schedule_overview_relearn(
            data_source_id=str(ds.id),
            user_id=str(current_user.id),
            organization_id=str(organization.id),
        )
    except Exception:
        pass
    return {"status": "ok", "tenant_id": payload.tenant_id}
