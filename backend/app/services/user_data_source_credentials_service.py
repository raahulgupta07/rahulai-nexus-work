from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.user import User
from app.models.organization import Organization
from app.models.data_source import DataSource
from app.models.user_data_source_credentials import UserDataSourceCredentials
from app.schemas.user_data_source_credentials_schema import (
    UserDataSourceCredentialsCreate,
    UserDataSourceCredentialsUpdate,
    UserDataSourceCredentialsSchema,
)
from app.schemas.data_source_registry import get_entry
from fastapi import HTTPException
from app.schemas.data_source_schema import DataSourceUserStatus
from app.schemas.data_source_registry import resolve_client_class
import json
import inspect


class UserDataSourceCredentialsService:
    # Per-user sign-in connectors whose refresh token is a 90-day sliding window.
    _SLIDING_90D_TYPES = ("fabric_user", "powerbi_user")

    @staticmethod
    def _token_lifecycle(connection, row) -> dict:
        """Sign-in credential lifecycle for a per-user credential ``row``.

        Generalized from the fabric/pbi-only version so ANY per-user row surfaces
        its lifecycle (all additive optional DataSourceUserStatus fields):
          signed_in_at     : credential row created_at
          last_refreshed_at : updated_at -> last_used_at -> created_at
          credential_scope : "data_source" for the sign-in connectors
                             (fabric_user/powerbi_user, DS-scoped store),
                             "connection" for every other connector type.
          token_expires_at : the 90-day sliding-window expiry ONLY for the two
                             sign-in connectors; for other types, included only
                             when the row actually stores an expiry (never
                             fabricated), otherwise omitted.
        Empty dict when there is no credential row, so the fields stay None and
        no other connector's status changes.
        """
        try:
            if row is None:
                return {}
            from datetime import datetime as _dt, timedelta
            conn_type = getattr(connection, "type", None)
            is_signin = conn_type in UserDataSourceCredentialsService._SLIDING_90D_TYPES

            signed_in = getattr(row, "created_at", None)
            # updated_at advances every time the rotated refresh token is
            # re-encrypted+committed (see the sign-in _persist path); fall back
            # to last_used_at (query-time use) then to created_at.
            last_refreshed = (
                getattr(row, "updated_at", None)
                or getattr(row, "last_used_at", None)
                or signed_in
            )
            out = {
                "signed_in_at": signed_in,
                "last_refreshed_at": last_refreshed,
                "credential_scope": "data_source" if is_signin else "connection",
            }
            if is_signin:
                out["token_expires_at"] = (
                    (last_refreshed + timedelta(days=90)) if last_refreshed else None
                )
            else:
                # Other connector types: surface an expiry only when the row
                # actually stores one — never invent a sliding window.
                stored_exp = getattr(row, "expires_at", None)
                if stored_exp is None:
                    md = getattr(row, "metadata_json", None)
                    if isinstance(md, dict):
                        cand = md.get("expires_at") or md.get("token_expires_at")
                        if isinstance(cand, _dt):
                            stored_exp = cand
                if stored_exp is not None:
                    out["token_expires_at"] = stored_exp

            # Derive the two facts the UI actually needs from the expiry date.
            # ★Computed here rather than in the browser so every surface agrees:
            # a strip, a picker row and a roster row must never disagree about
            # whether somebody's sign-in still works.
            exp = out.get("token_expires_at")
            if exp is not None:
                remaining = exp - _dt.utcnow()
                days = int(remaining.total_seconds() // 86400)
                out["expired"] = remaining.total_seconds() <= 0
                out["expires_in_days"] = max(0, days)
                # A week's notice: long enough to act on at a convenient moment,
                # short enough that it is not background noise for three months.
                out["expiring_soon"] = (not out["expired"]) and days <= 7
            return out
        except Exception:
            return {}

    def _get_connection_info(self, data_source: DataSource) -> tuple:
        """
        Get connection info (type, config, auth_policy, allowed_user_auth_modes) from the first connection.
        Returns (type, config, auth_policy, allowed_user_auth_modes, connection) tuple.
        """
        conn = data_source.connections[0] if data_source.connections else None
        if not conn:
            return (None, {}, "system_only", None, None)
        
        config = conn.config
        if isinstance(config, str):
            config = json.loads(config)
        
        return (
            conn.type,
            config or {},
            conn.auth_policy or "system_only",
            conn.allowed_user_auth_modes,
            conn
        )

    async def get_primary_active_row(self, db: AsyncSession, data_source: DataSource, user: User) -> Optional[UserDataSourceCredentials]:
        stmt = (
            select(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.is_active == True,
            )
            .order_by(UserDataSourceCredentials.is_primary.desc(), UserDataSourceCredentials.updated_at.desc())
        )
        return (await db.execute(stmt)).scalars().first()

    async def get_my_credentials(self, db: AsyncSession, data_source: DataSource, user: User) -> Optional[UserDataSourceCredentialsSchema]:
        row = await self.get_primary_active_row(db, data_source, user)
        return UserDataSourceCredentialsSchema.from_orm(row) if row else None

    async def build_user_status(self, db: AsyncSession, data_source: DataSource, user: User, live_test: bool = False) -> DataSourceUserStatus:
        import logging
        logger = logging.getLogger(__name__)
        
        # Get connection info from the first connection
        ds_type, config, auth_policy, allowed_user_auth_modes, connection = self._get_connection_info(data_source)
        
        # Helper to get cached status from connection
        def get_cached_status():
            if connection and connection.last_connection_status:
                return connection.last_connection_status
            return "unknown"
        
        def get_last_checked_at():
            if connection and connection.last_connection_checked_at:
                return connection.last_connection_checked_at
            return None
        
        # For system-only data sources, report system connection status
        if auth_policy != "user_required":
            conn_status = "unknown"
            last_checked = None
            if live_test:
                try:
                    from app.services.data_source_service import DataSourceService
                    ds_service = DataSourceService()
                    client = await ds_service.construct_client(db=db, data_source=data_source, current_user=user)
                    ok = await client.atest_connection()
                    success = bool(ok.get("success")) if isinstance(ok, dict) else bool(ok)
                    conn_status = "success" if success else "not_connected"
                    logger.info(f"Connection test for {data_source.name}: {conn_status} (result={ok})")
                except Exception as e:
                    logger.error(f"Connection test failed for {data_source.name}: {e}")
                    conn_status = "not_connected"
            else:
                # Use cached status from connection
                conn_status = get_cached_status()
                last_checked = get_last_checked_at()
            return DataSourceUserStatus(
                has_user_credentials=False, 
                connection=conn_status, 
                effective_auth="system",
                last_checked_at=last_checked
            )

        # Delegated/OBO connections: status follows the admin query-identity toggle.
        if connection is not None:
            from app.services.connection_identity import supports_user_token, build_token_identity_status
            if supports_user_token(connection):
                return await build_token_identity_status(
                    db, connection, user, get_cached_status(), get_last_checked_at()
                )

        row = await self.get_primary_active_row(db, data_source, user)
        if not row:
            # Per-user sign-in connectors (fabric_user / powerbi_user) have NO
            # system credential — a missing per-user token means "not connected
            # yet", so even the owner/admin must sign in. Do NOT fall through to
            # the system-creds fallback below (there are none) — return
            # effective_auth="none" so the UI shows Connect. Once the user signs
            # in, get_primary_active_row finds the DS-scoped token and the row
            # path below returns effective_auth="user". (This also fixes the
            # earlier "no Connect button for admin" bug without misreading the
            # connection-scoped credential table.)
            if connection is not None and getattr(connection, "type", None) in ("fabric_user", "powerbi_user"):
                return DataSourceUserStatus(
                    has_user_credentials=False, connection="offline", effective_auth="none"
                )
            # Owner/admin fallback possible; owner/admin can use system creds or empty creds (e.g., SQLite)
            is_owner = str(getattr(data_source, "owner_user_id", "")) == str(getattr(user, "id", ""))
            
            # Admin-level access: full_admin or per-DS `manage` grant
            has_update_perm = False
            try:
                from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
                resolved = await resolve_permissions(
                    db, str(user.id), str(getattr(data_source, "organization_id", ""))
                )
                has_update_perm = (
                    FULL_ADMIN in resolved.org_permissions
                    or resolved.has_resource_permission("data_source", str(data_source.id), "manage")
                )
            except Exception:
                has_update_perm = False
            
            # Owner/admin can use the connection even without stored credentials (e.g., SQLite)
            if is_owner or has_update_perm:
                conn = "unknown"
                last_checked = None
                if live_test:
                    try:
                        # Attempt live test using system credentials
                        from app.services.data_source_service import DataSourceService
                        ds_service = DataSourceService()
                        client = await ds_service.construct_client(db=db, data_source=data_source, current_user=user)
                        ok = await client.atest_connection()
                        success = bool(ok.get("success")) if isinstance(ok, dict) else bool(ok)
                        conn = "success" if success else "not_connected"
                    except Exception:
                        conn = "not_connected"
                else:
                    # Use cached status
                    conn = get_cached_status()
                    last_checked = get_last_checked_at()
                return DataSourceUserStatus(
                    has_user_credentials=False, 
                    connection=conn, 
                    effective_auth="system", 
                    uses_fallback=True,
                    last_checked_at=last_checked
                )
            return DataSourceUserStatus(has_user_credentials=False, connection="offline", effective_auth="none")

        conn = "unknown"
        last_checked = None
        if live_test:
            try:
                # Local import to avoid circular
                from app.services.data_source_service import DataSourceService
                ds_service = DataSourceService()
                client = await ds_service.construct_client(db=db, data_source=data_source, current_user=user)
                ok = await client.atest_connection()
                success = bool(ok.get("success")) if isinstance(ok, dict) else bool(ok)
                conn = "success" if success else "not_connected"
            except Exception:
                conn = "not_connected"
        else:
            # Use cached status
            conn = get_cached_status()
            last_checked = get_last_checked_at()

        return DataSourceUserStatus(
            has_user_credentials=True,
            auth_mode=row.auth_mode,
            is_primary=row.is_primary,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            connection=conn,
            effective_auth="user",
            uses_fallback=False,
            credentials_id=str(getattr(row, "id", "")) if getattr(row, "id", None) else None,
            last_checked_at=last_checked,
            **self._token_lifecycle(connection, row),
        )

    async def build_user_status_for_connection(
        self,
        db: AsyncSession,
        connection,  # Connection model
        user: User,
        data_source: DataSource = None,
        live_test: bool = False,
        cred_index=None,  # connection_identity.UserCredentialIndex
    ) -> DataSourceUserStatus:
        """
        Build user status for a specific connection.
        Used for multi-connection support where each connection needs its own status.

        ``cred_index`` is the caller's credential rows, prefetched for a whole
        list of agents (see connection_identity.UserCredentialIndex). Without it
        this issues one query per connection plus one per data source, which is
        what makes the agent-list endpoints scale with the workspace's agent
        count instead of staying flat.
        """
        import logging
        logger = logging.getLogger(__name__)

        auth_policy = connection.auth_policy or "system_only"

        # Helper to get cached status from connection
        def get_cached_status():
            if connection and connection.last_connection_status:
                return connection.last_connection_status
            return "unknown"

        def get_last_checked_at():
            if connection and connection.last_connection_checked_at:
                return connection.last_connection_checked_at
            return None

        # For system-only connections, report system connection status
        if auth_policy != "user_required":
            conn_status = get_cached_status()
            last_checked = get_last_checked_at()
            return DataSourceUserStatus(
                has_user_credentials=False,
                connection=conn_status,
                effective_auth="system",
                last_checked_at=last_checked
            )

        # Delegated/OBO connections: status is driven by the admin query-identity
        # toggle (service account vs self) — handled in one place.
        from app.services.connection_identity import (
            supports_user_token, build_token_identity_status,
            supports_user_kerberos_sso, build_kerberos_sso_status,
        )
        if supports_user_token(connection):
            return await build_token_identity_status(
                db, connection, user, get_cached_status(), get_last_checked_at(),
                cred_index=cred_index,
            )

        # Kerberos SSO: no stored secret — access is derived from the member's AD
        # principal (login UPN or an explicit override), so a resolvable UPN is
        # itself "user" access. This is what lets their per-user overlay build.
        if supports_user_kerberos_sso(connection):
            return await build_kerberos_sso_status(
                db, connection, user, get_cached_status(), get_last_checked_at(),
                cred_index=cred_index,
            )

        # For user_required, check if user has credentials
        # First check data-source-level credentials, then connection-level credentials (OAuth)
        # Per-user sign-in connectors store their token DS-scoped; when a caller
        # (e.g. the /connections list) has no data_source in hand, resolve it
        # from the domain_connection link so the row lookup can still run.
        # ★FORK: a data source resolved HERE was never part of the caller's list,
        # so the prefetched index has no row for it — and the index cannot tell
        # "not prefetched" from "no credential". Reading it would report these
        # connectors as not-connected despite a valid token, which is the exact
        # missing-Connect-button bug this block was written to fix. Query directly
        # for a lazily-resolved data source.
        lazily_resolved_ds = False
        if data_source is None and getattr(connection, "type", None) in ("fabric_user", "powerbi_user"):
            try:
                from app.models.data_source import DataSource as _DSModel
                from app.models.domain_connection import domain_connection as _dc
                _ds_stmt = (
                    select(_DSModel)
                    .join(_dc, _dc.c.data_source_id == _DSModel.id)
                    .where(_dc.c.connection_id == str(connection.id))
                    .limit(1)
                )
                data_source = (await db.execute(_ds_stmt)).scalars().first()
                lazily_resolved_ds = data_source is not None
            except Exception:
                data_source = None

        row = None
        if data_source:
            row = (
                cred_index.data_source_row(data_source.id)
                if (cred_index is not None and not lazily_resolved_ds)
                else await self.get_primary_active_row(db, data_source, user)
            )

        if not row:
            # Check connection-level credentials (stored by OAuth flow)
            from app.models.user_connection_credentials import UserConnectionCredentials
            if cred_index is not None:
                conn_cred = cred_index.connection_row(connection.id)
            else:
                conn_cred_stmt = select(UserConnectionCredentials).where(
                    UserConnectionCredentials.connection_id == str(connection.id),
                    UserConnectionCredentials.user_id == str(user.id),
                    UserConnectionCredentials.is_active == True,
                )
                conn_cred = (await db.execute(conn_cred_stmt)).scalars().first()
            if conn_cred:
                # For user credentials, don't use system-level cached status —
                # it reflects the service principal test, not the user's OAuth token.
                # If user has last_used_at, they've successfully queried before.
                user_conn_status = "success" if conn_cred.last_used_at else "unknown"
                return DataSourceUserStatus(
                    has_user_credentials=True,
                    auth_mode=conn_cred.auth_mode,
                    is_primary=conn_cred.is_primary,
                    last_used_at=conn_cred.last_used_at,
                    expires_at=conn_cred.expires_at,
                    connection=user_conn_status,
                    effective_auth="user",
                    last_checked_at=conn_cred.last_used_at,
                    **self._token_lifecycle(connection, conn_cred),
                )

        if not row:
            # Per-user sign-in connectors (fabric_user / powerbi_user) have NO
            # system credential — a missing per-user token means "not connected
            # yet", so even the owner/admin must sign in. Do NOT fall through to
            # the owner/admin system fallback below (there are none) — return
            # effective_auth="none" so the UI shows "Sign in" instead of a
            # meaningless "Service account". Mirrors build_user_status.
            if getattr(connection, "type", None) in ("fabric_user", "powerbi_user"):
                return DataSourceUserStatus(
                    has_user_credentials=False, connection="offline", effective_auth="none"
                )
            # Owner/admin fallback: owner/admin can use system creds (e.g., SQLite)
            is_owner = False
            has_update_perm = False

            # Ownership: the given data_source, or — when only a connection is in
            # scope (the /connections list) — any data source linked to it.
            try:
                if data_source is not None:
                    owner_candidates = [data_source]
                else:
                    owner_candidates = list(getattr(connection, "data_sources", []) or [])
            except Exception:
                owner_candidates = [data_source] if data_source is not None else []
            for ds in owner_candidates:
                if ds is not None and str(getattr(ds, "owner_user_id", "")) == str(getattr(user, "id", "")):
                    is_owner = True
                    break

            # Admin-level access: org-wide full_admin / manage_connections (no
            # data_source needed) or a per-DS `manage` grant.
            try:
                from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
                org_id = getattr(data_source, "organization_id", None) or getattr(connection, "organization_id", None)
                resolved = await resolve_permissions(db, str(user.id), str(org_id))
                has_update_perm = (
                    FULL_ADMIN in resolved.org_permissions
                    or resolved.has_org_permission("manage_connections")
                    or (data_source is not None and resolved.has_resource_permission("data_source", str(data_source.id), "manage"))
                )
            except Exception:
                has_update_perm = False

            if is_owner or has_update_perm:
                return DataSourceUserStatus(
                    has_user_credentials=False,
                    connection=get_cached_status(),
                    effective_auth="system",
                    uses_fallback=True,
                    last_checked_at=get_last_checked_at()
                )

            # User has no credentials and no fallback permission
            return DataSourceUserStatus(
                has_user_credentials=False,
                connection="offline",
                effective_auth="none"
            )

        # User has stored credentials — use user-specific status, not system-level
        user_conn_status = "success" if row.last_used_at else "unknown"

        # Per-user sign-in (user_login, e.g. powerbi_user): surface the connected
        # account email + the tenants auto-merged after sign-in by decrypting the
        # stored payload. Null/empty for every other auth_mode → no behavior change.
        username = None
        tenants = []
        if row.auth_mode == "user_login":
            try:
                creds = row.decrypt_credentials() or {}
                username = creds.get("username") or None
                stored_tenants = creds.get("tenants") or []
                if isinstance(stored_tenants, list):
                    tenants = [
                        {"id": t.get("id"), "name": t.get("name")}
                        for t in stored_tenants
                        if isinstance(t, dict) and t.get("id")
                    ]
            except Exception:
                username = None
                tenants = []

        return DataSourceUserStatus(
            has_user_credentials=True,
            auth_mode=row.auth_mode,
            is_primary=row.is_primary,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            connection=user_conn_status,
            effective_auth="user",
            uses_fallback=False,
            credentials_id=str(getattr(row, "id", "")) if getattr(row, "id", None) else None,
            last_checked_at=row.last_used_at,
            username=username,
            tenants=tenants,
            **self._token_lifecycle(connection, row),
        )

    async def test_my_credentials(self, db: AsyncSession, data_source: DataSource, user: User, payload: UserDataSourceCredentialsCreate) -> dict:
        # Get connection info
        ds_type, config, auth_policy, allowed_user_auth_modes, connection = self._get_connection_info(data_source)
        
        if not ds_type:
            raise HTTPException(status_code=400, detail="Data source has no connection")
        
        # Validate against registry
        entry = get_entry(ds_type)
        variant = (entry.credentials_auth.by_auth or {}).get(payload.auth_mode)
        if not variant or ("user" not in (variant.scopes or [])):
            raise HTTPException(status_code=400, detail="Authentication mode is not allowed for user credentials")
        schema_cls = variant.schema
        try:
            schema_cls(**(payload.credentials or {}))
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid credentials: {e}")

        # Build client with provided creds without persisting. For an overlay
        # variant the user's payload is only their identity fields, so the
        # connection's system credentials (e.g. the Qlik client certificate)
        # go underneath — exactly as the runtime resolver will merge them.
        from app.schemas.data_source_registry import overlay_system_credentials
        creds = overlay_system_credentials(connection, payload.credentials or {}, payload.auth_mode)
        ClientClass = resolve_client_class(ds_type)
        params = {**(config or {}), **creds}
        # Strip meta keys
        meta_keys = {"auth_type", "auth_policy", "allowed_user_auth_modes"}
        params = {k: v for k, v in params.items() if v is not None and k not in meta_keys}
        # Filter by signature
        try:
            sig = inspect.signature(ClientClass.__init__)
            params = {k: v for k, v in params.items() if k in sig.parameters and k != "self"}
        except Exception:
            pass
        client = ClientClass(**params)
        try:
            res = await client.atest_connection()
            success = bool(res.get("success")) if isinstance(res, dict) else bool(res)
            return {"success": success, "message": (res.get("message") if isinstance(res, dict) else None)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def upsert_my_credentials(self, db: AsyncSession, data_source: DataSource, user: User, payload: UserDataSourceCredentialsCreate) -> UserDataSourceCredentialsSchema:
        # Get connection info
        ds_type, config, auth_policy, allowed_user_auth_modes, connection = self._get_connection_info(data_source)
        
        if not ds_type:
            raise HTTPException(status_code=400, detail="Data source has no connection")
        
        # Policy: ensure auth_mode allowed for user scope
        entry = get_entry(ds_type)
        variant = (entry.credentials_auth.by_auth or {}).get(payload.auth_mode)
        if not variant or ("user" not in (variant.scopes or [])):
            raise HTTPException(status_code=400, detail="Authentication mode is not allowed for user credentials")

        # If DS restricts allowed_user_auth_modes, enforce
        allowed = allowed_user_auth_modes or []
        if allowed and payload.auth_mode not in allowed:
            raise HTTPException(status_code=400, detail="Authentication mode not permitted by data source policy")

        # Validate credentials against registry schema
        schema_cls = variant.schema
        try:
            schema_cls(**(payload.credentials or {}))
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid credentials: {e}")

        # Kerberos SSO rows persist an explicit UPN: fill a blank principal from
        # the login identity at save time so resolvers never see an empty one.
        if payload.auth_mode == "kerberos_delegated":
            creds = dict(payload.credentials or {})
            if not (creds.get("kerberos_impersonate") or "").strip():
                email = (getattr(user, "email", None) or "").strip()
                if "@" not in email:
                    raise HTTPException(
                        status_code=422,
                        detail="Kerberos SSO requires an Active Directory principal (UPN); your login identity has none — provide one explicitly.",
                    )
                creds["kerberos_impersonate"] = email
            creds["use_kerberos"] = True
            payload.credentials = creds

        # Find existing (active) row
        stmt = (
            select(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.is_active == True,
            )
            .order_by(UserDataSourceCredentials.is_primary.desc(), UserDataSourceCredentials.updated_at.desc())
        )
        row = (await db.execute(stmt)).scalars().first()

        if row is None:
            row = UserDataSourceCredentials(
                data_source_id=str(data_source.id),
                user_id=str(user.id),
                organization_id=str(data_source.organization_id),
                auth_mode=payload.auth_mode,
                is_active=True,
                is_primary=bool(payload.is_primary if payload.is_primary is not None else True),
                expires_at=payload.expires_at,
                metadata_json=payload.metadata_json,
            )
        else:
            row.auth_mode = payload.auth_mode
            row.is_primary = bool(payload.is_primary if payload.is_primary is not None else row.is_primary)
            row.expires_at = payload.expires_at
            row.metadata_json = payload.metadata_json

        # Encrypt secret payload
        row.encrypt_credentials(payload.credentials or {})
        db.add(row)
        await db.commit()
        await db.refresh(row)

        # Enforce single primary per user+DS
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

        # Refresh per-user schema overlay (best-effort)
        try:
            from app.services.data_source_service import DataSourceService
            ds_service = DataSourceService()
            await ds_service.get_user_data_source_schema(db=db, data_source=data_source, user=user)
        except Exception:
            pass

        return UserDataSourceCredentialsSchema.from_orm(row)

    async def patch_my_credentials(self, db: AsyncSession, data_source: DataSource, user: User, payload: UserDataSourceCredentialsUpdate) -> UserDataSourceCredentialsSchema:
        # Get connection info
        ds_type, config, auth_policy, allowed_user_auth_modes, connection = self._get_connection_info(data_source)
        
        if not ds_type:
            raise HTTPException(status_code=400, detail="Data source has no connection")
        
        stmt = (
            select(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.is_active == True,
            )
            .order_by(UserDataSourceCredentials.is_primary.desc(), UserDataSourceCredentials.updated_at.desc())
        )
        row = (await db.execute(stmt)).scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="User credentials not found")

        # If auth_mode changes, require credentials
        if payload.auth_mode and not payload.credentials:
            raise HTTPException(status_code=400, detail="credentials are required when changing auth_mode")

        # Apply changes
        if payload.auth_mode:
            entry = get_entry(ds_type)
            variant = (entry.credentials_auth.by_auth or {}).get(payload.auth_mode)
            if not variant or ("user" not in (variant.scopes or [])):
                raise HTTPException(status_code=400, detail="Authentication mode is not allowed for user credentials")
            # Validate new credentials
            schema_cls = variant.schema
            try:
                schema_cls(**(payload.credentials or {}))
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Invalid credentials: {e}")
            row.auth_mode = payload.auth_mode
            row.encrypt_credentials(payload.credentials or {})

        if payload.credentials and not payload.auth_mode:
            # Validate against current auth_mode
            entry = get_entry(ds_type)
            variant = (entry.credentials_auth.by_auth or {}).get(row.auth_mode)
            schema_cls = variant.schema if variant else None
            if schema_cls is None:
                raise HTTPException(status_code=400, detail="Cannot validate credentials for current auth_mode")
            try:
                schema_cls(**payload.credentials)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Invalid credentials: {e}")
            row.encrypt_credentials(payload.credentials)

        if payload.is_active is not None:
            row.is_active = bool(payload.is_active)
        if payload.is_primary is not None:
            row.is_primary = bool(payload.is_primary)
        if payload.expires_at is not None:
            row.expires_at = payload.expires_at
        if payload.metadata_json is not None:
            row.metadata_json = payload.metadata_json

        db.add(row)
        await db.commit()
        await db.refresh(row)

        # Enforce single primary if set
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

        # Refresh per-user schema overlay (best-effort)
        try:
            from app.services.data_source_service import DataSourceService
            ds_service = DataSourceService()
            await ds_service.get_user_data_source_schema(db=db, data_source=data_source, user=user)
        except Exception:
            pass

        return UserDataSourceCredentialsSchema.from_orm(row)

    async def delete_my_credentials(self, db: AsyncSession, data_source: DataSource, user: User) -> None:
        stmt = (
            select(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
            )
            .order_by(UserDataSourceCredentials.is_primary.desc(), UserDataSourceCredentials.updated_at.desc())
        )
        row = (await db.execute(stmt)).scalars().first()
        if not row:
            return
        await db.delete(row)
        await db.commit()

        # Per-user sign-in connectors (fabric_user / powerbi_user) store their
        # per-user schema in the DS-scoped overlay (user_data_source_overlay).
        # Deleting the token alone leaves that overlay behind, so the member keeps
        # seeing (and the agent keeps listing) tables they can no longer reach
        # until the next sync — which can never run without a token. Revoke this
        # user's overlay rows for the DS now so the tables disappear immediately
        # on disconnect. Marked revoked (not hard-deleted) to preserve history;
        # read surfaces filter on is_accessible == True. Best-effort: overlay
        # cleanup must NEVER make the credential delete fail. Byte-identical for
        # every other connector type (guarded on connection type).
        try:
            conn0 = data_source.connections[0] if data_source.connections else None
            if conn0 is not None and getattr(conn0, "type", None) in ("fabric_user", "powerbi_user"):
                from sqlalchemy import update as _sa_update
                from app.models.user_data_source_overlay import (
                    UserDataSourceTable as _OverlayTable,
                    UserDataSourceColumn as _OverlayColumn,
                )
                await db.execute(
                    _sa_update(_OverlayTable)
                    .where(
                        _OverlayTable.data_source_id == str(data_source.id),
                        _OverlayTable.user_id == str(user.id),
                        _OverlayTable.is_accessible == True,  # noqa: E712
                    )
                    .values(is_accessible=False, status="revoked")
                )
                # Cascade to the column overlay: revoke columns whose parent table
                # row belongs to this (user, data_source).
                await db.execute(
                    _sa_update(_OverlayColumn)
                    .where(
                        _OverlayColumn.is_accessible == True,  # noqa: E712
                        _OverlayColumn.user_data_source_table_id.in_(
                            select(_OverlayTable.id).where(
                                _OverlayTable.data_source_id == str(data_source.id),
                                _OverlayTable.user_id == str(user.id),
                            )
                        ),
                    )
                    .values(is_accessible=False)
                )
                await db.commit()
        except Exception as _e:  # noqa: BLE001 — overlay revoke is best-effort
            import logging
            logging.getLogger(__name__).warning(
                "disconnect overlay-revoke failed for ds=%s user=%s: %s",
                getattr(data_source, "id", None), getattr(user, "id", None), _e,
            )


