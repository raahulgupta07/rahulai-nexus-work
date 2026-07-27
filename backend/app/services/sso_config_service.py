"""Instance-global SSO configuration service.

Unlike LDAP/SMTP (which are per-organization, on ``OrganizationSettings.config``),
single sign-on governs how *any* user of the instance authenticates, so it lives
on the ``InstanceSettings`` singleton (JSON ``config`` column). This service is
the single read/write/resolve surface for it, mirroring the
``get_ldap`` / ``update_ldap`` / ``resolve_ldap_config`` trio on
``OrganizationSettingsService``.

Client secrets are Fernet-encrypted at rest as ``client_secret_enc`` (same
``BOW_ENCRYPTION_KEY`` as SMTP/LDAP) and are NEVER returned to the client — the
read schema exposes only ``client_secret_set: bool``. When the singleton has no
saved SSO block, everything falls back to the file config
(``settings.bow_config``) so existing ``bow-config.yaml`` setups keep working;
the ``source_db`` flag tells the UI which is in effect.
"""
from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from fastapi import HTTPException

from app.models.instance_settings import InstanceSettings
from app.services.email.secrets import encrypt_secret, decrypt_secret
from app.settings.config import settings
from app.schemas.organization_settings_schema import (
    SsoConfigSchema,
    SsoConfigUpdate,
    SsoGoogleRead,
    SsoProviderRead,
)


# Provider names double as URL slugs in the OIDC login/callback routes, so keep
# them url-safe and unique.
_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


class SsoConfigService:
    """Read/write/resolve the instance-global SSO config."""

    def __init__(self):
        pass

    # --- read -------------------------------------------------------------
    async def get_config(self, db: AsyncSession) -> SsoConfigSchema:
        """Return the instance SSO config (client secrets redacted).

        When the singleton has a saved block it is returned with
        ``source_db=True``; otherwise the file config is reflected with
        ``source_db=False`` so the form always shows whatever is in effect.
        """
        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})

        has_db_block = any(
            k in cfg for k in ("auth_mode", "google", "oidc_providers")
        )
        if has_db_block:
            g = cfg.get("google") or {}
            google = SsoGoogleRead(
                enabled=bool(g.get("enabled", False)),
                client_id=g.get("client_id"),
                client_secret_set=bool(g.get("client_secret_enc")),
            )
            providers = [
                SsoProviderRead(
                    name=p.get("name", ""),
                    enabled=bool(p.get("enabled", False)),
                    issuer=p.get("issuer") or "",
                    client_id=p.get("client_id"),
                    client_secret_set=bool(p.get("client_secret_enc")),
                    scopes=list(p.get("scopes") or ["openid", "profile", "email"]),
                    label=p.get("label"),
                    icon=p.get("icon"),
                    pkce=bool(p.get("pkce", True)),
                    discovery=bool(p.get("discovery", True)),
                    uid_claim=p.get("uid_claim") or "sub",
                    sync_groups=bool(p.get("sync_groups", False)),
                    group_claim=p.get("group_claim") or "groups",
                    resolve_group_names=bool(p.get("resolve_group_names", False)),
                )
                for p in (cfg.get("oidc_providers") or [])
            ]
            return SsoConfigSchema(
                auth_mode=cfg.get("auth_mode") or "hybrid",
                google=google,
                providers=providers,
                source_db=True,
            )

        # File fallback — reflect bow-config.yaml (never exposes secrets).
        fc = settings.bow_config
        fg = fc.google_oauth
        google = SsoGoogleRead(
            enabled=bool(fg.enabled),
            client_id=fg.client_id,
            client_secret_set=bool(fg.client_secret),
        )
        providers = [
            SsoProviderRead(
                name=p.name,
                enabled=bool(p.enabled),
                issuer=p.issuer or "",
                client_id=p.client_id,
                client_secret_set=bool(p.client_secret),
                scopes=list(p.scopes or ["openid", "profile", "email"]),
                label=p.label,
                icon=p.icon,
                pkce=bool(p.pkce),
                discovery=bool(p.discovery),
                uid_claim=p.uid_claim or "sub",
                sync_groups=bool(p.sync_groups),
                group_claim=p.group_claim or "groups",
                resolve_group_names=bool(p.resolve_group_names),
            )
            for p in (fc.oidc_providers or [])
        ]
        return SsoConfigSchema(
            auth_mode=getattr(fc.auth, "mode", "hybrid") or "hybrid",
            google=google,
            providers=providers,
            source_db=False,
        )

    # --- write ------------------------------------------------------------
    async def update_config(
        self,
        db: AsyncSession,
        payload: SsoConfigUpdate,
        current_user,
    ) -> SsoConfigSchema:
        """Merge ``payload`` into the singleton config and persist.

        Existing ``client_secret_enc`` values are kept unless the payload
        supplies a new plaintext ``client_secret`` (which is then encrypted).
        Provider names must be non-empty url-safe slugs and unique. Issuer /
        client_id are NOT required to *enable* a provider (an enabled-but-blank
        entry surfaces on the login page as "not configured"); they are only
        format-validated when a value is present. Secrets stay encrypted.
        """
        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})

        # auth_mode
        if payload.auth_mode is not None:
            mode = (payload.auth_mode or "").strip() or "hybrid"
            if mode not in ("local_only", "sso_only", "hybrid"):
                raise HTTPException(
                    status_code=400,
                    detail="auth_mode must be one of local_only, sso_only, hybrid.",
                )
            cfg["auth_mode"] = mode

        # google
        if payload.google is not None:
            g = payload.google
            existing_g = cfg.get("google") or {}
            client_id = (g.client_id or "").strip() or None
            # client_id is NOT required to enable Google — an enabled-but-blank
            # entry shows on the login page as "not configured" (and the
            # authorize route returns a clean 400 if actually hit).
            new_g = {
                "enabled": bool(g.enabled),
                "client_id": client_id,
                # Keep the existing encrypted secret unless a new one is supplied.
                "client_secret_enc": existing_g.get("client_secret_enc"),
            }
            if g.client_secret:
                new_g["client_secret_enc"] = encrypt_secret(g.client_secret)
            cfg["google"] = new_g

        # oidc providers
        if payload.providers is not None:
            existing_by_name = {
                p.get("name"): p for p in (cfg.get("oidc_providers") or [])
            }
            seen: set[str] = set()
            new_providers = []
            for p in payload.providers:
                name = (p.name or "").strip().lower()
                if not name or not _PROVIDER_NAME_RE.match(name):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Provider name '{p.name}' is invalid — use only "
                            "lowercase letters, digits, '-' and '_'."
                        ),
                    )
                if name in seen:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Duplicate provider name '{name}'.",
                    )
                seen.add(name)

                issuer = (p.issuer or "").strip()
                # Issuer is NOT required to enable a provider (enabled-but-blank
                # surfaces as "not configured" on the login page). Only
                # format-validate it when a value is actually present.
                if issuer and not (
                    issuer.startswith("http://") or issuer.startswith("https://")
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Provider '{name}' issuer must be an http(s) URL "
                            "when set."
                        ),
                    )

                prev = existing_by_name.get(name) or {}
                entry = {
                    "name": name,
                    "enabled": bool(p.enabled),
                    "issuer": issuer,
                    "client_id": (p.client_id or "").strip() or None,
                    # Keep the existing encrypted secret unless a new one is supplied.
                    "client_secret_enc": prev.get("client_secret_enc"),
                    "scopes": list(p.scopes or ["openid", "profile", "email"]),
                    "label": p.label,
                    "icon": p.icon,
                    "pkce": bool(p.pkce),
                    "discovery": bool(p.discovery),
                    "uid_claim": p.uid_claim or "sub",
                    "sync_groups": bool(p.sync_groups),
                    "group_claim": p.group_claim or "groups",
                    "resolve_group_names": bool(p.resolve_group_names),
                }
                if p.client_secret:
                    entry["client_secret_enc"] = encrypt_secret(p.client_secret)
                new_providers.append(entry)
            cfg["oidc_providers"] = new_providers

        inst.config = cfg
        flag_modified(inst, "config")
        db.add(inst)
        await db.commit()
        await db.refresh(inst)

        # Best-effort audit log (mirrors OrganizationSettingsService).
        try:
            from app.ee.audit.service import audit_service
            await audit_service.log(
                db=db,
                organization_id=None,
                action="settings.sso_config_updated",
                user_id=str(current_user.id) if current_user else None,
                resource_type="instance_settings",
                resource_id=str(inst.id),
                details={
                    "auth_mode": cfg.get("auth_mode"),
                    "google_enabled": bool((cfg.get("google") or {}).get("enabled")),
                    "providers": [p.get("name") for p in (cfg.get("oidc_providers") or [])],
                },
            )
        except Exception:
            pass

        return await self.get_config(db)

    # --- resolve (runtime config objects) ---------------------------------
    async def resolve_oidc_providers(self, db: AsyncSession) -> list:
        """Build runtime ``OIDCProvider`` objects for the instance.

        Uses the singleton's saved providers (decrypting each client secret)
        when present; otherwise falls back to the file config.
        """
        from app.settings.bow_config import OIDCProvider

        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})
        raw = cfg.get("oidc_providers")
        if not raw:
            return settings.bow_config.oidc_providers  # file fallback

        providers = []
        for p in raw:
            providers.append(
                OIDCProvider(
                    name=p.get("name", ""),
                    enabled=bool(p.get("enabled", False)),
                    issuer=p.get("issuer") or "",
                    client_id=p.get("client_id"),
                    client_secret=decrypt_secret(p.get("client_secret_enc")),
                    scopes=list(p.get("scopes") or ["openid", "profile", "email"]),
                    label=p.get("label"),
                    icon=p.get("icon"),
                    pkce=bool(p.get("pkce", True)),
                    discovery=bool(p.get("discovery", True)),
                    uid_claim=p.get("uid_claim") or "sub",
                    sync_groups=bool(p.get("sync_groups", False)),
                    group_claim=p.get("group_claim") or "groups",
                    resolve_group_names=bool(p.get("resolve_group_names", False)),
                )
            )
        return providers

    async def resolve_google(self, db: AsyncSession):
        """Build a runtime ``GoogleOAuth`` for the instance.

        Uses the singleton's saved google block (decrypting the secret) when
        present; otherwise falls back to the file config.
        """
        from app.settings.bow_config import GoogleOAuth

        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})
        g = cfg.get("google")
        if not g:
            return settings.bow_config.google_oauth  # file fallback
        return GoogleOAuth(
            enabled=bool(g.get("enabled", False)),
            client_id=g.get("client_id"),
            client_secret=decrypt_secret(g.get("client_secret_enc")),
        )

    async def resolve_auth_mode(self, db: AsyncSession) -> str:
        """Return the effective auth mode (DB override else file, default hybrid)."""
        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})
        mode = cfg.get("auth_mode")
        if mode:
            return mode
        return getattr(getattr(settings.bow_config, "auth", None), "mode", "hybrid") or "hybrid"
