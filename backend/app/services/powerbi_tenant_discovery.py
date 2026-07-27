"""Cross-tenant discovery for Power BI user sign-in.

A user's Office-365 identity may live in one Entra tenant while the Power BI /
Fabric workspaces they need live in ANOTHER tenant where they're a B2B guest. A
token minted against the home tenant can't see the guest tenant's workspaces, so
the user must supply the *guest* tenant id. Most users don't know that GUID.

This helper takes the user's email + password and lists EVERY tenant the identity
can reach (home + guest) via the Azure Resource Manager ``/tenants`` endpoint —
the same identity, a management-scoped token. Read-only, no secret stored.

ROPC caveat (same as PowerBIUserClient): fails on MFA-enabled accounts. On any
auth failure the raw AADSTS code is surfaced so the caller can fall back to
device-code (P3) or ask the tenant id manually.
"""
from __future__ import annotations

from typing import List, Dict

import requests

_PUBLIC_CLIENT = "1950a258-227b-4e31-a9cf-717495945fc2"  # MS FOCI public client, permits ROPC
_ARM_SCOPE = "https://management.azure.com/.default"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_TENANTS_URL = "https://management.azure.com/tenants?api-version=2020-01-01"


def discover_tenants(username: str, password: str, home_tenant: str = "organizations") -> List[Dict]:
    """Return ``[{id, name, domain}]`` for every tenant this identity can access.

    ``home_tenant`` defaults to the multi-tenant ``organizations`` authority so the
    call works without knowing any tenant id up front. Raises ``RuntimeError`` with
    the AADSTS detail on auth failure (MFA / ROPC blocked).
    """
    if not (username and password):
        raise RuntimeError("email and password are required to discover tenants")

    resp = requests.post(
        _TOKEN_URL.format(tenant=home_tenant or "organizations"),
        data={
            "client_id": _PUBLIC_CLIENT,
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": _ARM_SCOPE,
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        detail = ""
        try:
            j = resp.json()
            detail = f"{j.get('error')}: {j.get('error_description', '')[:300]}"
        except Exception:  # noqa: BLE001
            detail = resp.text[:300]
        hint = ""
        if "AADSTS50076" in detail or "AADSTS50079" in detail:
            hint = " (MFA required — use device-code sign-in or enter the tenant id manually.)"
        elif "AADSTS7000218" in detail or "AADSTS65001" in detail:
            hint = " (Tenant blocks ROPC — enter the tenant id manually.)"
        raise RuntimeError(f"Tenant discovery sign-in failed: {detail}{hint}")

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Tenant discovery did not return an access token.")

    tr = requests.get(_TENANTS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if tr.status_code >= 300:
        raise RuntimeError(f"Failed to list tenants: HTTP {tr.status_code} {tr.text[:200]}")

    out: List[Dict] = []
    for t in (tr.json() or {}).get("value", []):
        out.append({
            "id": t.get("tenantId"),
            "name": t.get("displayName") or "(tenant)",
            "domain": (t.get("domains") or [None])[0],
        })
    return out
