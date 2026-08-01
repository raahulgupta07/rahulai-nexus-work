"""Multi-tenant Power BI fan-out for the ``powerbi_mt`` connector.

One delegated OAuth sign-in (minted against the multi-tenant ``organizations``
authority) reaches every Entra tenant the user belongs to — their home tenant
plus any tenant where they're a B2B guest. This module takes the home
refresh_token from that single sign-in and:

  1. discovers EVERY tenant the identity can reach (token-driven, via the Azure
     Resource Manager ``/tenants`` endpoint — no password, unlike the ROPC
     ``powerbi_tenant_discovery.discover_tenants``);
  2. redeems the refresh_token for a Power BI access token in EACH tenant;
  3. runs Power BI schema discovery per tenant with that tenant-scoped token;
  4. MERGES every tenant's tables into one per-user overlay (reusing
     ``DataSourceService._upsert_user_overlay``), stamping each table's
     ``metadata_json`` with ``tenant_id`` / ``tenant_name`` so the merged agent
     knows which tenant a table came from;
  5. persists a per-tenant refresh_token map (``tenant_tokens``) onto the user's
     stored connection credentials so query-time routing can mint a fresh
     tenant-scoped token on demand.

Everything is fail-soft: a failing tenant never kills the others, and the whole
scan never raises (the caller wraps it best-effort so it can never break the
OAuth sign-in). Guarded entirely by connection type ``powerbi_mt`` at the call
sites — no other connector reaches this code.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import requests

from app.settings.logging_config import get_logger

logger = get_logger(__name__)

# MS FOCI public client — used as a fallback when the connection's OAuth app has
# no client_secret (public client). A refresh_token issued to a FOCI client can
# be redeemed for other resources/tenants. Mirrors powerbi_tenant_discovery.
_PUBLIC_CLIENT = "1950a258-227b-4e31-a9cf-717495945fc2"
_ORG_TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
_TENANT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_TENANTS_URL = "https://management.azure.com/tenants?api-version=2020-01-01"
_ARM_SCOPE = "https://management.azure.com/.default offline_access"
_PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default offline_access"


def _client_creds(client_id: Optional[str], client_secret: Optional[str]) -> Dict[str, str]:
    """Build the client half of a token request body.

    Uses the connection's own OAuth app when a client_id is present; falls back
    to the MS FOCI public client when there is no client_id at all. A public
    client (no secret) simply omits ``client_secret``.
    """
    cid = client_id or _PUBLIC_CLIENT
    body = {"client_id": cid}
    if client_secret:
        body["client_secret"] = client_secret
    return body


def discover_tenants_from_refresh(
    refresh_token: str,
    client_id: Optional[str],
    client_secret: Optional[str] = None,
) -> List[Dict]:
    """Return ``[{id, name, domain}]`` for every tenant this identity can reach.

    Token-driven (no password): redeems ``refresh_token`` for an Azure Resource
    Manager token against the ``organizations`` authority, then lists tenants via
    the ARM ``/tenants`` endpoint. Fail-soft → ``[]`` on any error.
    """
    if not refresh_token:
        return []
    try:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": _ARM_SCOPE,
            **_client_creds(client_id, client_secret),
        }
        resp = requests.post(_ORG_TOKEN_URL, data=data, timeout=30)
        if resp.status_code >= 300:
            logger.warning(
                "powerbi_mt tenant discovery: ARM token redeem failed HTTP %s %s",
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
                "powerbi_mt tenant discovery: /tenants failed HTTP %s %s",
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
        logger.warning("powerbi_mt tenant discovery raised (soft): %s", e)
        return []


def redeem_for_tenant(
    refresh_token: str,
    tenant_id: str,
    client_id: Optional[str],
    client_secret: Optional[str] = None,
) -> Dict:
    """Redeem ``refresh_token`` for a Power BI access token in a SPECIFIC tenant.

    Refresh-token grant against that tenant's own token endpoint with the Power
    BI delegated scope. Returns ``{access_token, refresh_token}`` — the second is
    the possibly-rotated per-tenant refresh_token (Azure rotates on redeem).
    Fail-soft → ``{}`` on any error (that tenant is skipped).
    """
    if not (refresh_token and tenant_id):
        return {}
    try:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": _PBI_SCOPE,
            **_client_creds(client_id, client_secret),
        }
        resp = requests.post(
            _TENANT_TOKEN_URL.format(tenant=tenant_id), data=data, timeout=30
        )
        if resp.status_code >= 300:
            logger.warning(
                "powerbi_mt: redeem for tenant %s failed HTTP %s %s",
                tenant_id, resp.status_code, resp.text[:200],
            )
            return {}
        j = resp.json() or {}
        access_token = j.get("access_token")
        if not access_token:
            return {}
        return {
            "access_token": access_token,
            # Azure returns a new refresh_token per tenant/resource — keep it so
            # the per-tenant map holds a working token for query-time routing.
            "refresh_token": j.get("refresh_token") or refresh_token,
        }
    except Exception as e:  # noqa: BLE001 — fail-soft, never raise
        logger.warning("powerbi_mt redeem_for_tenant %s raised (soft): %s", tenant_id, e)
        return {}


def _normalize_columns(cols) -> List[Dict]:
    out = []
    for c in cols or []:
        name = c.name if hasattr(c, "name") else c.get("name")
        dtype = c.dtype if hasattr(c, "dtype") else c.get("dtype")
        out.append({"name": name, "dtype": dtype})
    return out


def _merge_tenant_tables(
    per_tenant: List[Tuple[str, str, Dict[str, Dict]]],
) -> Tuple[Dict[str, Dict], List[str]]:
    """Merge every tenant's normalized tables into ONE dict, keeping tables that
    share a display name across tenants.

    The merge used to be a plain ``combined.update(normalized)`` keyed on the
    bare ``{Dataset}/{Table}`` name, which carries no tenant. Two tenants holding
    a model of the same name therefore collided and the later tenant silently
    won — the earlier tenant's table was gone before the overlay write ever saw
    it. The identity check that keeps tenants apart lives in
    ``_upsert_user_overlay``, which only ever receives what survived this dict.
    Not hypothetical: ``Usage Metrics Report`` is a built-in semantic model in
    every Power BI tenant, so any identity signed into two tenants hit it.

    A name claimed by more than one tenant is qualified for EVERY claimant —
    including the first — so the outcome does not depend on tenant iteration
    order. A name claimed by one tenant is left exactly as it was, so existing
    overlays are not renamed (a rename revokes the old row and creates a new
    one, which would churn every user's catalog on upgrade).

    Returns ``(combined, collided_names)``.
    """
    owners: Dict[str, set] = {}
    for tid, _tname, normalized in per_tenant:
        for name in normalized:
            owners.setdefault(name, set()).add(tid)

    collided = sorted(n for n, tids in owners.items() if len(tids) > 1)
    combined: Dict[str, Dict] = {}
    for tid, tname, normalized in per_tenant:
        for name, entry in normalized.items():
            if len(owners.get(name) or ()) <= 1:
                combined[name] = entry
                continue
            key = f"{name} ({tname})"
            # Two tenants can carry the same display name. Fall back to the
            # tenant id, which is unique by construction.
            if key in combined:
                key = f"{name} ({tname} · {tid})"
            combined[key] = entry
    return combined, collided


def _normalize_tables(fresh, tenant_id: str, tenant_name: str) -> Dict[str, Dict]:
    """Normalize a client's schema list into the ``_upsert_user_overlay`` shape,
    stamping every table's metadata_json with the owning tenant."""
    normalized: Dict[str, Dict] = {}
    for t in fresh or []:
        if isinstance(t, dict):
            name = t.get("name")
            cols = t.get("columns", [])
            pks = t.get("pks", [])
            fks = t.get("fks", []) or []
            meta = t.get("metadata_json")
        else:
            name = getattr(t, "name", None)
            cols = getattr(t, "columns", [])
            pks = getattr(t, "pks", [])
            fks = getattr(t, "fks", []) or []
            meta = getattr(t, "metadata_json", None)
        if not name:
            continue
        # Stamp the tenant tag at the top level of metadata_json so the merged
        # overlay/agent can attribute each table to its tenant. Left the existing
        # `powerbi` sub-dict (datasetId/tableName) untouched — that stays the
        # stable identity key `_upsert_user_overlay` de-dups on.
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["tenant_id"] = tenant_id
        meta["tenant_name"] = tenant_name
        normalized[name] = {
            "columns": _normalize_columns(cols),
            "pks": _normalize_columns(pks),
            "fks": fks,
            "metadata_json": meta,
        }
    return normalized


async def scan_all_tenants(
    db,
    data_source,
    user,
    home_refresh_token: str,
    client_id: Optional[str],
    client_secret: Optional[str] = None,
    workspaces: Optional[str] = None,
    shared_dataset_ids: Optional[str] = None,
    persist_tokens: bool = True,
    on_discovered: Optional[Callable[[List[Dict]], Awaitable[None]]] = None,
    on_tenant: Optional[Callable[..., Awaitable[None]]] = None,
) -> Dict:
    """Discover every tenant, scan each, MERGE all tables into one user overlay.

    Reuses ``DataSourceService._upsert_user_overlay`` with the UNION of every
    tenant's tables in a single call — calling it per-tenant would revoke the
    other tenants' rows (it revokes anything absent from the passed set). Also
    persists a ``tenant_tokens`` map onto the user's connection credentials.

    ``client_secret`` is OPTIONAL: when falsy every token redemption runs as a
    PUBLIC client (``_client_creds`` omits the secret — the FOCI public-client
    path the ``powerbi_user`` connector uses). When a secret IS supplied the
    behavior is byte-identical to the confidential ``powerbi_mt`` path.

    ``persist_tokens`` (default True) keeps the original ``powerbi_mt`` behavior
    of writing the per-tenant refresh_token map onto ``UserConnectionCredentials``.
    The ``powerbi_user`` connector stores its refresh_token on
    ``UserDataSourceCredentials`` instead, so it passes ``persist_tokens=False``
    and persists the returned ``tenant_tokens`` map itself.

    ``on_discovered`` / ``on_tenant`` are optional async progress hooks. They
    exist so a caller can report a multi-tenant crawl AS IT HAPPENS rather than
    only on return — the whole scan is one long await, and a member watching it
    should see each tenant land. Both default to ``None``, which makes this
    function behave exactly as before for the ``powerbi_mt`` OAuth path.
    A hook that raises is logged and ignored: narration must never break the
    crawl it is narrating.

    Never raises. Returns
    ``{tenants, tables_merged, tenant_tokens, errors, collided_names}``.
    """
    async def _notify(cb, *args) -> None:
        if cb is None:
            return
        try:
            await cb(*args)
        except Exception as e:  # noqa: BLE001 — a progress hook is never load-bearing
            logger.warning("powerbi scan progress hook failed (soft): %s", e)

    result = {
        "tenants": [], "tables_merged": 0, "tenant_tokens": {}, "errors": [],
        # Display names that more than one tenant claimed; each was qualified
        # with its tenant name rather than one tenant overwriting the other.
        "collided_names": [],
        # Models found but not readable, each carrying the category that says
        # what would fix it (needs_build / wrong_connector / ...). This is what
        # the Access panel renders; without it a blocked dashboard and a
        # non-existent one look identical to the member.
        "access": [],
    }
    if not home_refresh_token:
        result["errors"].append("no_home_refresh_token")
        return result

    try:
        tenants = await asyncio.to_thread(
            discover_tenants_from_refresh, home_refresh_token, client_id, client_secret
        )
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"discovery:{e}")
        tenants = []

    if not tenants:
        result["errors"].append("no_tenants_discovered")
        return result

    # Publish the list before crawling it, so the UI can name what it is waiting
    # for instead of counting up to a total nobody has seen.
    await _notify(on_discovered, tenants)

    from app.data_sources.clients.powerbi_client import PowerBIClient
    from app.services.data_source_service import DataSourceService

    # (tenant_id, tenant_name, normalized) per tenant. Collected rather than
    # merged in the loop so _merge_tenant_tables can see EVERY tenant's names
    # before deciding which need qualifying — a collision cannot be detected
    # from one tenant alone, and resolving it in loop order would make the
    # result depend on the order ARM happened to return tenants in.
    per_tenant: List[Tuple[str, str, Dict[str, Dict]]] = []
    tenant_tokens: Dict[str, str] = {}

    # Hand the data source's canonical catalog to every per-tenant client as
    # `prior_tables` so already-indexed datasets are rebuilt from the stored
    # definition instead of re-introspected (per-dataset COLUMNSTATISTICS is the
    # rate-limited, minutes-scale part of a Power BI crawl). The canonical rows
    # exist even on a user_required connector because the union-mode overlay sync
    # writes them back from user discovery.
    #
    # Identity-safe: the per-tenant DATASET LISTING still runs with the
    # signing-in user's own token, so only datasets that user can currently see
    # are rebuilt — a prior row nobody can reach is simply never re-emitted. And
    # priors can't false-match across tenants because matching keys on
    # metadata_json.powerbi.datasetId, which is unique per tenant.
    prior_tables: Optional[Dict[str, Dict]] = None
    try:
        from sqlalchemy import select
        from app.models.datasource_table import DataSourceTable

        rows = (await db.execute(
            select(DataSourceTable).where(DataSourceTable.datasource_id == data_source.id)
        )).scalars().all()
        prior_tables = {
            r.name: {
                "columns": r.columns or [],
                "pks": r.pks or [],
                "fks": r.fks or [],
                "metadata_json": r.metadata_json,
            }
            for r in rows if r.metadata_json
        } or None
    except Exception as e:  # noqa: BLE001 — fail open to a full crawl
        logger.warning("powerbi_mt scan: prior_tables lookup failed (soft): %s", e)
        prior_tables = None

    if prior_tables:
        logger.info(
            "powerbi_mt scan: %d prior table(s) available for incremental discovery",
            len(prior_tables),
        )

    from app.data_sources.clients.base import _accepts_kwarg

    for t in tenants:
        tid = t.get("id")
        tname = t.get("name") or "(tenant)"
        if not tid:
            continue
        try:
            minted = await asyncio.to_thread(
                redeem_for_tenant, home_refresh_token, tid, client_id, client_secret
            )
            if not minted.get("access_token"):
                result["errors"].append(f"redeem_failed:{tid}")
                await _notify(
                    on_tenant, tname, 0,
                    "could not get a Microsoft token for this tenant",
                )
                continue
            tenant_tokens[tid] = minted.get("refresh_token") or home_refresh_token

            client = PowerBIClient(
                access_token=minted["access_token"],
                tenant_id=tid,
                workspaces=workspaces or None,
                # Item-shared models are tried in EVERY tenant: a dataset id is
                # unique, so a probe in the wrong tenant simply 404s and is
                # dropped, and we cannot know up front which tenant owns one.
                shared_dataset_ids=shared_dataset_ids or None,
            )
            if prior_tables and _accepts_kwarg(client.get_schemas, "prior_tables"):
                fresh = await client.aget_schemas(prior_tables=prior_tables)
            else:
                fresh = await client.aget_schemas()
            normalized = _normalize_tables(fresh, tid, tname)
            per_tenant.append((tid, tname, normalized))
            # Models this identity FOUND in this tenant and could not read,
            # already classified by what would resolve each one. Stamped with
            # the tenant so a member with the same model name in two tenants
            # can tell which one is blocked.
            for d in (getattr(client, "discovery_diagnostics", None) or []):
                result["access"].append({**d, "tenantId": tid, "tenantName": tname})
            result["tenants"].append({"id": tid, "name": tname, "tables": len(normalized)})
            await _notify(on_tenant, tname, len(normalized), None)
        except Exception as e:  # noqa: BLE001 — one tenant failing never kills others
            logger.warning("powerbi_mt scan of tenant %s failed (soft): %s", tid, e)
            result["errors"].append(f"scan:{tid}:{e}")
            await _notify(on_tenant, tname, 0, str(e))
            continue

    combined, collided = _merge_tenant_tables(per_tenant)

    # ★A model lives in exactly ONE tenant, but every tenant gets probed with
    # the same candidate ids (we cannot know up front which tenant owns one).
    # So a model that reads perfectly in tenant A also produces a 404 in tenant
    # B — and reporting that as "ask an admin for Build permission" sends the
    # member to the wrong admin about a model that is already working. Drop any
    # finding for a dataset that proved readable ANYWHERE in this scan.
    readable_ids = set()
    for entry in combined.values():
        pbi = ((entry.get("metadata_json") or {}).get("powerbi") or {})
        ds_id = pbi.get("datasetId")
        if ds_id:
            readable_ids.add(str(ds_id))
    if readable_ids:
        before = len(result["access"])
        result["access"] = [
            a for a in result["access"] if str(a.get("datasetId")) not in readable_ids
        ]
        dropped = before - len(result["access"])
        if dropped:
            logger.info(
                "powerbi scan: dropped %d refusal(s) for models that are readable in "
                "another tenant — probing every tenant for the same id always 404s "
                "in the ones that do not own it", dropped,
            )
    if collided:
        # Report it: a qualified name is a visible change to the member's
        # catalog, and the alternative (silently keeping one tenant's copy) is
        # the bug this replaces.
        logger.info(
            "powerbi scan: %d table name(s) claimed by more than one tenant, "
            "qualified with the tenant name: %s",
            len(collided), ", ".join(collided[:10]),
        )
        result["collided_names"] = collided

    # Merge every tenant's tables into the user's overlay in ONE call.
    if combined:
        try:
            await DataSourceService()._upsert_user_overlay(
                db=db, data_source=data_source, user=user, normalized=combined
            )
            result["tables_merged"] = len(combined)
        except Exception as e:  # noqa: BLE001
            logger.warning("powerbi_mt overlay merge failed (soft): %s", e)
            result["errors"].append(f"overlay:{e}")

    # Always surface the per-tenant refresh_token map so a caller that stores its
    # credentials elsewhere (powerbi_user → UserDataSourceCredentials) can persist
    # it itself.
    result["tenant_tokens"] = tenant_tokens

    # Persist the per-tenant refresh_token map so query-time routing can mint a
    # fresh tenant-scoped access token on demand (encrypted at rest by the model).
    # Only the connection-credential store (powerbi_mt) — gated by persist_tokens.
    if tenant_tokens and persist_tokens:
        try:
            await _persist_tenant_tokens(db, data_source, user, tenant_tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("powerbi_mt tenant_tokens persist failed (soft): %s", e)
            result["errors"].append(f"tenant_tokens:{e}")

    return result


async def _persist_tenant_tokens(db, data_source, user, tenant_tokens: Dict[str, str]) -> None:
    """Store ``tenant_tokens`` onto the user's connection-level OAuth credentials.

    The home OAuth token lives in ``UserConnectionCredentials`` (written by the
    OAuth callback); the per-tenant refresh_token map rides alongside it in the
    same encrypted blob so query-time routing (resolve_credentials) can find it.
    """
    from sqlalchemy import select
    from app.models.user_connection_credentials import UserConnectionCredentials

    conn = (data_source.connections or [None])[0]
    if conn is None:
        return
    row = (
        await db.execute(
            select(UserConnectionCredentials).where(
                UserConnectionCredentials.connection_id == conn.id,
                UserConnectionCredentials.user_id == str(user.id),
                UserConnectionCredentials.is_active == True,  # noqa: E712
            ).order_by(
                UserConnectionCredentials.is_primary.desc(),
                UserConnectionCredentials.updated_at.desc(),
            )
        )
    ).scalars().first()
    if row is None:
        return
    try:
        creds = row.decrypt_credentials() or {}
    except Exception:
        creds = {}
    existing = creds.get("tenant_tokens") or {}
    existing.update(tenant_tokens)
    creds["tenant_tokens"] = existing
    row.encrypt_credentials(creds)
    db.add(row)
    await db.commit()
