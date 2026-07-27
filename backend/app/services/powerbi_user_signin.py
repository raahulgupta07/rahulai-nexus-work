"""Per-user Power BI sign-in orchestration for the ``powerbi_user`` connector.

A thin, DB-free layer over the two pure helper modules already in place:
``powerbi_device_code`` (ROPC + device-code + refresh grants) and
``powerbi_tenant_discovery`` (ARM ``/tenants`` cross-tenant discovery). Nothing
here touches FastAPI or the database — the route layer owns persistence.

Sign-in strategy (matches the sister app):
  1. Try ROPC (email + password) for the Power BI scope. Clean, no round-trip UI.
  2. On MFA / conditional-access / ROPC-blocked codes → tell the caller to fall
     back to the device-code flow (the route starts it).
  3. On a genuine bad password (AADSTS50126) → surface a real error.

The password is used exactly once (to mint tokens) and is NEVER persisted — only
the returned refresh_token is stored (encrypted) by the route layer.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

from app.services.powerbi_device_code import (
    ropc_token,
    refresh_to_access_token,
    decode_id_token,
    SCOPE_POWERBI,
)
from app.services.powerbi_tenant_discovery import discover_tenants


# AADSTS code that means "the password itself is wrong" — a genuine failure, never
# an MFA fallback. ropc_token already classifies MFA/legacy-block codes as mfa=True;
# everything else (including this) comes back as a plain error.
_BAD_CRED_CODE = "AADSTS50126"


async def try_password_signin(email: str, password: str, tenant: str = "organizations") -> Dict:
    """Attempt ROPC sign-in for the Power BI scope.

    Returns one of:
      ``{"ok": True, "refresh_token", "access_token", "tenant_id"|None}``
      ``{"ok": False, "mfa_required": True, "detail": str}``  (MFA / ROPC-blocked → device code)
      ``{"ok": False, "error": str}``                          (bad credentials / other)

    ``tenant`` defaults to the multi-tenant ``organizations`` authority so it works
    without a known tenant id. The blocking ``requests`` call runs in a thread so
    async routes never stall the event loop.
    """
    if not (email and password):
        return {"ok": False, "error": "email and password are required"}

    res = await asyncio.to_thread(
        ropc_token, tenant or "organizations", email, password, SCOPE_POWERBI
    )

    if res.get("ok"):
        # tid claim (home tenant) if the id_token carried one — handy so the
        # caller can persist a concrete tenant even from the multi-tenant authority.
        tid = decode_id_token(res.get("id_token") or "").get("tid")
        return {
            "ok": True,
            "refresh_token": res.get("refresh_token"),
            "access_token": res.get("access_token"),
            "tenant_id": tid,
        }

    # ropc_token flags MFA / conditional-access / legacy-auth-blocked codes as mfa=True.
    if res.get("mfa"):
        return {"ok": False, "mfa_required": True, "detail": res.get("error", "")}

    return {"ok": False, "error": res.get("error", "Sign-in failed")}


async def discover_user_tenants(email: str, password: str) -> Tuple[List[Dict], str]:
    """List every tenant the identity can reach — fail-soft.

    Returns ``(tenants, error)``: ``tenants`` is ``[{id, name, domain}]`` (possibly
    empty) and ``error`` is "" on success or the failure detail (e.g. an MFA/ROPC
    block) otherwise. Never raises — tenant discovery is a best-effort convenience.
    """
    if not (email and password):
        return [], "email and password are required"
    try:
        tenants = await asyncio.to_thread(discover_tenants, email, password)
        return (tenants or []), ""
    except Exception as e:  # noqa: BLE001
        return [], str(e)


async def mint_access_token(tenant_id: str, refresh_token: str) -> Dict:
    """Redeem a stored refresh_token for a fresh Power BI access_token.

    Wraps ``refresh_to_access_token``. Azure may rotate the refresh_token — the
    (possibly new) one is returned so the caller can persist it.

    Returns ``{"ok": True, "access_token", "refresh_token"|None, "expires_in"}`` or
    ``{"ok": False, "error": str}``.
    """
    if not (tenant_id and refresh_token):
        return {"ok": False, "error": "tenant_id and refresh_token are required"}
    return await asyncio.to_thread(
        refresh_to_access_token, tenant_id, refresh_token, SCOPE_POWERBI
    )
