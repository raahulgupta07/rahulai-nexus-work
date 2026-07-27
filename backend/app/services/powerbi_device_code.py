"""OAuth 2.0 device-code sign-in for the Power BI user connector (MFA-safe).

ROPC (email + password) dies the moment an account has MFA enabled
(``AADSTS50076``/``50079``) or the tenant blocks legacy auth (``AADSTS7000218``).
The device-code flow is the self-serve alternative: the app shows a short
``user_code`` + a verification URL; the user approves on any device (MFA/2FA
happens there natively) and the app polls until a token — plus a refresh token —
comes back.

Two pure functions (mirroring ``powerbi_tenant_discovery.py`` — no FastAPI, no DB):
``start_device_code`` kicks off the flow; ``poll_device_code`` does ONE poll (the
caller loops). ``offline_access`` is in the scope so we get a refresh token, which
we persist (encrypted) so future scans don't need a re-login.

Never raises; network/HTTP failures come back as ``{"ok": False, ...}`` /
``{"status": "error", ...}``. Never logs tokens.
"""
from __future__ import annotations

import base64
import json

import requests

_PUBLIC_CLIENT = "1950a258-227b-4e31-a9cf-717495945fc2"  # MS FOCI public client (no secret)
_DEVICECODE_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
# openid profile = get an id_token back (carries the signed-in MS account
# identity: preferred_username / name / tid); offline_access = REQUIRED to
# receive a refresh_token back.
_SCOPE = "https://analysis.windows.net/powerbi/api/.default offline_access openid profile"

# Per-connector device-code scopes. All use the SAME FOCI public client above —
# a refresh_token issued for one family member (e.g. Power BI) can be redeemed for
# a token to another (Fabric SQL / Graph) via the refresh-grant helper below.
# offline_access on every scope so we always get a refresh_token back;
# openid profile so the token response also carries an id_token (MS identity).
SCOPE_POWERBI = _SCOPE
SCOPE_FABRIC = "https://database.windows.net/.default offline_access openid profile"
SCOPE_GRAPH = "https://graph.microsoft.com/.default offline_access openid profile"
# Fabric REST API (control plane) — list workspaces/warehouses/lakehouses to
# DISCOVER a warehouse SQL endpoint hostname. Distinct from SCOPE_FABRIC, which
# is the SQL data-plane token (database.windows.net) fed to ODBC. Both redeem
# from the same FOCI refresh token.
SCOPE_FABRIC_REST = "https://api.fabric.microsoft.com/.default offline_access openid profile"
# Fabric SQL endpoint token audience (used when minting an access token to feed
# the ODBC driver via attrs_before={1256: ...}).
FABRIC_TOKEN_SCOPE = "https://database.windows.net/.default"


def _err_detail(resp: requests.Response) -> str:
    try:
        j = resp.json()
        return f"{j.get('error')}: {j.get('error_description', '')[:300]}"
    except Exception:  # noqa: BLE001
        return resp.text[:300]


def decode_id_token(id_token: str) -> dict:
    """Decode an OIDC id_token's payload (claims) WITHOUT verifying the signature.

    We only read display-identity claims (``preferred_username`` = the MS account
    email/UPN, ``name`` = display name, ``tid`` = tenant id) that we already trust
    because the token came straight from the Microsoft token endpoint over TLS —
    no signature check needed for a display label. Fail-soft: returns ``{}`` on any
    error. Never logs the token or the claims.
    """
    if not id_token:
        return {}
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
        return json.loads(base64.urlsafe_b64decode(payload_b64)) or {}
    except Exception:  # noqa: BLE001
        return {}


def start_device_code(tenant_id: str, client_id: str | None = None, scope: str | None = None) -> dict:
    """Begin the device-code flow. Returns the user_code + verification URL to show.

    ``tenant_id`` = a concrete tenant GUID or the multi-tenant word ``organizations``.
    ``scope`` defaults to the Power BI scope; pass ``SCOPE_FABRIC``/``SCOPE_GRAPH``
    for a Fabric SQL / Graph token (same FOCI public client, different resource).
    """
    if not tenant_id:
        return {"ok": False, "error": "tenant_id is required"}
    try:
        resp = requests.post(
            _DEVICECODE_URL.format(tenant=tenant_id),
            data={"client_id": client_id or _PUBLIC_CLIENT, "scope": scope or _SCOPE},
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if resp.status_code >= 300:
        return {"ok": False, "error": _err_detail(resp)}
    j = resp.json() or {}
    return {
        "ok": True,
        "device_code": j.get("device_code"),
        "user_code": j.get("user_code"),
        "verification_uri": j.get("verification_uri") or j.get("verification_url"),
        "expires_in": j.get("expires_in"),
        "interval": j.get("interval") or 5,
        "message": j.get("message"),
    }


def poll_device_code(tenant_id: str, device_code: str, client_id: str | None = None) -> dict:
    """Poll ONCE for the device-code token. Caller loops on ``status == 'pending'``.

    Returns one of:
      ``{"status": "success", "access_token", "refresh_token", "expires_in"}``
      ``{"status": "pending"}``            (optionally ``"slow_down": True``)
      ``{"status": "error", "error": str}``
    """
    if not (tenant_id and device_code):
        return {"status": "error", "error": "tenant_id and device_code are required"}
    try:
        resp = requests.post(
            _TOKEN_URL.format(tenant=tenant_id),
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id or _PUBLIC_CLIENT,
                "device_code": device_code,
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

    if resp.status_code < 300:
        j = resp.json() or {}
        return {
            "status": "success",
            "access_token": j.get("access_token"),
            "refresh_token": j.get("refresh_token"),
            "expires_in": j.get("expires_in"),
            # id_token carries the signed-in MS account identity (decode with
            # decode_id_token → preferred_username / name / tid).
            "id_token": j.get("id_token"),
        }

    # HTTP 400 carries the flow state in `error`.
    err = ""
    try:
        err = (resp.json() or {}).get("error", "")
    except Exception:  # noqa: BLE001
        err = ""
    if err == "authorization_pending":
        return {"status": "pending"}
    if err == "slow_down":
        return {"status": "pending", "slow_down": True}
    return {"status": "error", "error": _err_detail(resp)}


# AADSTS codes that mean "password alone can't finish — MFA / conditional access /
# legacy-auth blocked" → the caller should fall back to the device-code flow.
_MFA_FALLBACK_CODES = (
    "AADSTS50076",   # MFA required this session
    "AADSTS50079",   # MFA enrollment required
    "AADSTS50158",   # external security challenge (conditional access)
    "AADSTS50072",   # MFA enrollment required (variant)
    "AADSTS53000",   # device not compliant (conditional access)
    "AADSTS7000218", # tenant blocks ROPC / legacy auth
)


def ropc_token(
    tenant_id: str,
    username: str,
    password: str,
    scope: str,
    client_id: str | None = None,
) -> dict:
    """Resource-Owner-Password (ROPC) grant — email + password → token.

    The ``scope`` already carries ``offline_access`` so a success also yields a
    refresh_token (which the caller stores, exactly like the device-code path).

    Returns one of:
      ``{"ok": True, "access_token", "refresh_token", "expires_in"}``  (no MFA)
      ``{"ok": False, "mfa": True, "error": str}``   (MFA / policy → device-code fallback)
      ``{"ok": False, "error": str}``                (bad password / other — real failure)
    """
    if not (tenant_id and username and password and scope):
        return {"ok": False, "error": "tenant_id, username, password and scope are required"}
    try:
        resp = requests.post(
            _TOKEN_URL.format(tenant=tenant_id),
            data={
                "grant_type": "password",
                "client_id": client_id or _PUBLIC_CLIENT,
                "username": username,
                "password": password,
                "scope": scope,
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if resp.status_code < 300:
        j = resp.json() or {}
        return {
            "ok": True,
            "access_token": j.get("access_token"),
            "refresh_token": j.get("refresh_token"),
            "expires_in": j.get("expires_in"),
            # id_token carries the signed-in MS account identity (decode with
            # decode_id_token → preferred_username / name / tid).
            "id_token": j.get("id_token"),
        }
    detail = _err_detail(resp)
    if any(code in detail for code in _MFA_FALLBACK_CODES):
        return {"ok": False, "mfa": True, "error": detail}
    return {"ok": False, "error": detail}


def refresh_to_access_token(
    tenant_id: str,
    refresh_token: str,
    scope: str,
    client_id: str | None = None,
) -> dict:
    """Redeem a stored refresh_token for a fresh access_token at ``scope``.

    Because the device-code flow uses a FOCI public client, a refresh_token
    obtained for one Microsoft resource can be exchanged for a token to another
    (e.g. a Power BI refresh_token → a Fabric ``database.windows.net`` SQL token).
    Azure may rotate the refresh_token — the new one (when present) is returned so
    the caller can persist it. Never raises; never logs the token.

    Returns:
      ``{"ok": True, "access_token", "refresh_token"|None, "expires_in"}`` or
      ``{"ok": False, "error": str}``.
    """
    if not (tenant_id and refresh_token and scope):
        return {"ok": False, "error": "tenant_id, refresh_token and scope are required"}
    try:
        resp = requests.post(
            _TOKEN_URL.format(tenant=tenant_id),
            data={
                "grant_type": "refresh_token",
                "client_id": client_id or _PUBLIC_CLIENT,
                "refresh_token": refresh_token,
                "scope": scope,
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if resp.status_code >= 300:
        return {"ok": False, "error": _err_detail(resp)}
    j = resp.json() or {}
    return {
        "ok": True,
        "access_token": j.get("access_token"),
        # Azure returns a rotated refresh_token on some tenants; keep the old one if absent.
        "refresh_token": j.get("refresh_token"),
        "expires_in": j.get("expires_in"),
        # id_token carries the signed-in MS account identity (decode with
        # decode_id_token → preferred_username / name / tid).
        "id_token": j.get("id_token"),
    }
