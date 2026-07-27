"""Config-less Microsoft Fabric endpoint discovery (Phase 2 of the federated
`fabric_user` connector).

Given ONE stored refresh_token (FOCI multi-resource, from the user's device-code
sign-in), enumerate every SQL endpoint the identity can reach WITHOUT the admin
pre-typing a host/database:

    refresh_token
      → ARM /tenants                      (every tenant the identity is in)
      → per tenant: Fabric REST token      (api.fabric.microsoft.com/.default)
      → GET /v1/workspaces                 (every workspace in that tenant)
      → per workspace: lakehouses + warehouses
      → each item → (tenant, host, database)   ← a queryable SQL endpoint

The output is a flat, de-duplicated catalog of endpoints; Phase 3 ingests each
one and Phase 5 routes queries to it. Every network call is FAIL-SOFT — a tenant
or workspace that errors is skipped, never raises, never logs a token.

Cross-tenant note: a Fabric SQL (`database.windows.net`) token must be minted by
the tenant that OWNS the endpoint (home-tenant token → SQL 18456). Discovery
therefore records each endpoint's owning `tenant_id` so Phase 3/5 mint against it.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# FOCI public client — same one the device-code flow uses; a refresh_token it
# issued can be redeemed for any Microsoft resource in any tenant the user is in.
_PUBLIC_CLIENT = "1950a258-227b-4e31-a9cf-717495945fc2"

_ORG_TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
_TENANT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

_ARM_SCOPE = "https://management.azure.com/.default offline_access"
_TENANTS_URL = "https://management.azure.com/tenants?api-version=2020-01-01"

# Fabric REST (metadata plane). Distinct from the SQL data plane
# (database.windows.net) used to actually query — this only lists the catalog.
_FABRIC_REST_SCOPE = "https://api.fabric.microsoft.com/.default offline_access"
_FABRIC_REST = "https://api.fabric.microsoft.com/v1"


def _client_creds(client_id: Optional[str], client_secret: Optional[str]) -> Dict:
    """Public-client by default; only send confidential creds if BOTH provided."""
    if client_id and client_secret:
        return {"client_id": client_id, "client_secret": client_secret}
    return {"client_id": client_id or _PUBLIC_CLIENT}


def discover_tenants(
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> List[Dict]:
    """Return ``[{id, name, domain}]`` for every tenant this identity can reach.

    Redeems ``refresh_token`` for an ARM token at the ``organizations`` authority
    then lists tenants. Fail-soft → ``[]``. (Mirrors powerbi_multitenant_scan so
    both connectors discover tenants the same way.)
    """
    if not refresh_token:
        return []
    try:
        resp = requests.post(
            _ORG_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": _ARM_SCOPE,
                **_client_creds(client_id, client_secret),
            },
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.warning(
                "fabric_discovery: ARM token redeem failed HTTP %s %s",
                resp.status_code, resp.text[:200],
            )
            return []
        token = (resp.json() or {}).get("access_token")
        if not token:
            return []
        tr = requests.get(
            _TENANTS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        if tr.status_code >= 300:
            logger.warning(
                "fabric_discovery: /tenants failed HTTP %s %s",
                tr.status_code, tr.text[:200],
            )
            return []
        out: List[Dict] = []
        for t in (tr.json() or {}).get("value", []):
            tid = t.get("tenantId")
            if not tid:
                continue
            out.append({
                "id": tid,
                "name": t.get("displayName") or "(tenant)",
                "domain": (t.get("domains") or [None])[0],
            })
        return out
    except Exception as e:  # noqa: BLE001 — fail-soft, never raise
        logger.warning("fabric_discovery.discover_tenants raised (soft): %s", e)
        return []


def _fabric_rest_token(
    refresh_token: str,
    tenant_id: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> Optional[str]:
    """Redeem ``refresh_token`` for a Fabric-REST access token in ONE tenant.

    Must be minted against that tenant's own token endpoint so the workspace
    listing reflects that tenant. Fail-soft → ``None``.
    """
    if not (refresh_token and tenant_id):
        return None
    try:
        resp = requests.post(
            _TENANT_TOKEN_URL.format(tenant=tenant_id),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": _FABRIC_REST_SCOPE,
                **_client_creds(client_id, client_secret),
            },
            timeout=30,
        )
        if resp.status_code >= 300:
            logger.warning(
                "fabric_discovery: Fabric-REST token for tenant %s failed HTTP %s %s",
                tenant_id, resp.status_code, resp.text[:200],
            )
            return None
        return (resp.json() or {}).get("access_token")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "fabric_discovery._fabric_rest_token tenant %s raised (soft): %s",
            tenant_id, e,
        )
        return None


def _get_paged(url: str, token: str) -> List[Dict]:
    """GET a Fabric REST collection, following ``continuationUri`` paging.
    Fail-soft → whatever was collected before the error (possibly ``[]``)."""
    out: List[Dict] = []
    next_url: Optional[str] = url
    guard = 0
    try:
        while next_url and guard < 50:
            guard += 1
            r = requests.get(
                next_url, headers={"Authorization": f"Bearer {token}"}, timeout=40
            )
            if r.status_code >= 300:
                logger.warning(
                    "fabric_discovery: GET %s failed HTTP %s %s",
                    next_url.split("?")[0], r.status_code, r.text[:160],
                )
                break
            j = r.json() or {}
            out.extend(j.get("value", []) or [])
            next_url = j.get("continuationUri")
    except Exception as e:  # noqa: BLE001
        logger.warning("fabric_discovery._get_paged raised (soft): %s", e)
    return out


def _host_from_connection_string(cs: Optional[str]) -> Optional[str]:
    """Fabric returns a bare host in ``connectionString`` (no ``tcp:``/port), but
    tolerate a full ADO form too. Returns the hostname or ``None``."""
    if not cs or not isinstance(cs, str):
        return None
    host = cs.strip()
    if host.lower().startswith("tcp:"):
        host = host[4:]
    host = host.split(",")[0].split(";")[0].strip()
    return host or None


def _endpoints_from_items(
    items: List[Dict],
    item_type: str,
    tenant_id: str,
    tenant_name: str,
    workspace_id: str,
    workspace_name: str,
) -> List[Dict]:
    """Extract ``(host, database)`` endpoints from a lakehouse/warehouse listing.

    Lakehouse: ``properties.sqlEndpointProperties.connectionString`` + display
    name is the SQL database. Warehouse: ``properties.connectionString`` + display
    name. Items still provisioning (no connectionString yet) are skipped.
    """
    out: List[Dict] = []
    for it in items or []:
        props = it.get("properties") or {}
        if item_type == "Lakehouse":
            cs = (props.get("sqlEndpointProperties") or {}).get("connectionString")
        else:  # Warehouse
            cs = props.get("connectionString")
        host = _host_from_connection_string(cs)
        # The SQL database name for both lakehouse-SQL-endpoint and warehouse is
        # the item's display name.
        db = it.get("displayName") or it.get("name")
        if not (host and db):
            continue
        out.append({
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "item_type": item_type,
            "item_id": it.get("id"),
            "host": host,
            "database": db,
        })
    return out


def discover_endpoints(
    refresh_token: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    tenant_ids: Optional[List[str]] = None,
) -> List[Dict]:
    """The Phase 2 entry point: return every reachable Fabric SQL endpoint.

    Each item: ``{tenant_id, tenant_name, workspace_id, workspace_name,
    item_type, item_id, host, database}``. De-duplicated on ``(host, database)``.

    ``tenant_ids`` optionally restricts discovery to a known tenant set (skips the
    ARM /tenants call). Fully fail-soft — returns whatever it could reach.
    """
    if not refresh_token:
        return []

    # 1) Which tenants to scan.
    if tenant_ids:
        tenants = [{"id": t, "name": "(tenant)", "domain": None} for t in tenant_ids]
    else:
        tenants = discover_tenants(refresh_token, client_id, client_secret)
        if not tenants:
            # Even if ARM listing failed, try the home tenant via "organizations".
            tenants = [{"id": "organizations", "name": "(home)", "domain": None}]

    seen: set = set()
    catalog: List[Dict] = []
    for ten in tenants:
        tid = ten["id"]
        tname = ten.get("name") or "(tenant)"
        token = _fabric_rest_token(refresh_token, tid, client_id, client_secret)
        if not token:
            continue
        workspaces = _get_paged(f"{_FABRIC_REST}/workspaces", token)
        for ws in workspaces:
            wid = ws.get("id")
            wname = ws.get("displayName") or ws.get("name") or "(workspace)"
            if not wid:
                continue
            lakehouses = _get_paged(f"{_FABRIC_REST}/workspaces/{wid}/lakehouses", token)
            warehouses = _get_paged(f"{_FABRIC_REST}/workspaces/{wid}/warehouses", token)
            eps = (
                _endpoints_from_items(lakehouses, "Lakehouse", tid, tname, wid, wname)
                + _endpoints_from_items(warehouses, "Warehouse", tid, tname, wid, wname)
            )
            for ep in eps:
                key = (ep["host"].lower(), ep["database"].lower())
                if key in seen:
                    continue
                seen.add(key)
                catalog.append(ep)
    logger.info(
        "fabric_discovery: %s endpoint(s) across %s tenant(s)",
        len(catalog), len(tenants),
    )
    return catalog
