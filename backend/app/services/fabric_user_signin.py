"""Per-user Microsoft Fabric sign-in orchestration for the ``fabric_user`` connector.

A thin, DB-free layer over the pure helper module already in place
(``powerbi_device_code`` — ROPC + device-code + refresh grants). Nothing here
touches FastAPI or the database — the route layer owns persistence.

This mirrors ``powerbi_user_signin`` exactly, but mints tokens for the Fabric SQL
data-plane scope (``SCOPE_FABRIC`` = ``https://database.windows.net/.default``)
instead of the Power BI scope, so the resulting access_token is the one the ODBC
driver / ``MsFabricClient`` needs to query a Fabric Warehouse/Lakehouse SQL
endpoint. Because the device-code flow uses a FOCI public client, a refresh_token
issued here can be redeemed for a fresh Fabric SQL token at query time.

Sign-in strategy (matches the sister Power BI connector):
  1. Try ROPC (email + password) for the Fabric scope. Clean, no round-trip UI.
  2. On MFA / conditional-access / ROPC-blocked codes → tell the caller to fall
     back to the device-code flow (the route starts it).
  3. On a genuine bad password (AADSTS50126) → surface a real error.

The password is used exactly once (to mint tokens) and is NEVER persisted — only
the returned refresh_token is stored (encrypted) by the route layer.
"""
from __future__ import annotations

import asyncio
from typing import Dict

from app.services.powerbi_device_code import (
    ropc_token,
    refresh_to_access_token,
    decode_id_token,
    SCOPE_FABRIC,
)


async def try_password_signin(email: str, password: str, tenant: str = "organizations") -> Dict:
    """Attempt ROPC sign-in for the Fabric SQL scope.

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
        ropc_token, tenant or "organizations", email, password, SCOPE_FABRIC
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


async def mint_access_token(tenant_id: str, refresh_token: str) -> Dict:
    """Redeem a stored refresh_token for a fresh Fabric SQL access_token.

    Wraps ``refresh_to_access_token`` at ``SCOPE_FABRIC``. Azure may rotate the
    refresh_token — the (possibly new) one is returned so the caller can persist it.

    Returns ``{"ok": True, "access_token", "refresh_token"|None, "expires_in"}`` or
    ``{"ok": False, "error": str}``.
    """
    if not (tenant_id and refresh_token):
        return {"ok": False, "error": "tenant_id and refresh_token are required"}
    return await asyncio.to_thread(
        refresh_to_access_token, tenant_id, refresh_token, SCOPE_FABRIC
    )
