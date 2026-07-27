"""Federated Microsoft Fabric query client (Phase 5 of the `fabric_user`
connector).

The federated sync (Phases 2-3) merges tables from MANY Fabric SQL endpoints
(one per lakehouse/warehouse across every workspace/tenant) into ONE overlay,
naming each table ``{database}.{schema}.{table}`` and stamping its owning
endpoint into ``metadata_json['fabric']``. A single Fabric SQL connection can
only reach ONE database, so at query time each query must be routed to the right
endpoint.

This client is the router. The agent addresses ONE client key and writes SQL
using the exact namespaced names it sees in context, e.g.::

    ds_clients["MSFB"].execute_query(
        "SELECT COUNT(*) FROM DL_POC.dbo.CMO_SalesDetails"
    )

`execute_query` detects the ``DL_POC.`` prefix, strips it to the endpoint-local
``dbo.CMO_SalesDetails``, mints (and caches) a SQL token for that endpoint's
OWNING tenant, and runs the query against that one lakehouse.

A query naming SEVERAL lakehouses is routed by workspace, not refused outright.
Fabric supports cross-database queries with three-part names "to warehouses and
databases in the current active workspace"
(learn.microsoft.com/fabric/data-warehouse/query-warehouse), and a SQL endpoint
hostname is per-workspace — so lakehouses sharing a host share a workspace and
the engine can do the join itself. Only a query spanning two workspaces raises
the instructive error telling the agent to query each separately and combine the
DataFrames in pandas.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

import pandas as pd

from app.data_sources.clients.ms_fabric_client import MsFabricClient

logger = logging.getLogger(__name__)


class MsFabricFederatedClient:
    def __init__(self, endpoints: List[Dict], refresh_token: str):
        """`endpoints`: ``[{database, host, tenant_id}]`` (from the overlay's
        ``metadata_json['fabric']``). `refresh_token`: the user's stored FOCI
        multi-resource token, redeemed per tenant for a SQL access token."""
        # Keyed by database name (case-insensitive lookup map kept separately).
        self._endpoints: Dict[str, Dict] = {}
        for e in endpoints or []:
            dbname = e.get("database")
            host = e.get("host")
            if dbname and host:
                self._endpoints[dbname] = {
                    "database": dbname,
                    "host": host,
                    "tenant_id": e.get("tenant_id") or "organizations",
                }
        self._by_lower = {k.lower(): k for k in self._endpoints}
        self._refresh_token = refresh_token
        self._token_by_tenant: Dict[str, Optional[str]] = {}
        self._client_by_db: Dict[str, MsFabricClient] = {}

    # -- token + per-endpoint client caches ---------------------------------
    def _sql_token(self, tenant_id: str) -> Optional[str]:
        if tenant_id in self._token_by_tenant:
            return self._token_by_tenant[tenant_id]
        from app.services.powerbi_device_code import refresh_to_access_token, SCOPE_FABRIC
        res = refresh_to_access_token(tenant_id, self._refresh_token, SCOPE_FABRIC)
        token = res.get("access_token") if res.get("ok") else None
        if not token:
            logger.warning("fabric federated: SQL token mint failed for tenant %s: %s",
                           tenant_id, res.get("error"))
        self._token_by_tenant[tenant_id] = token
        return token

    def _client_for(self, database: str) -> MsFabricClient:
        if database in self._client_by_db:
            return self._client_by_db[database]
        ep = self._endpoints[database]
        token = self._sql_token(ep["tenant_id"])
        if not token:
            raise RuntimeError(
                f"Could not obtain a Fabric SQL token for '{database}' "
                f"(tenant {ep['tenant_id']}). Re-connect the Fabric sign-in."
            )
        client = MsFabricClient(server_hostname=ep["host"], database=database, access_token=token)
        self._client_by_db[database] = client
        return client

    # -- routing ------------------------------------------------------------
    def _databases_referenced(self, sql: str) -> List[str]:
        """Return the known endpoint databases whose ``{db}.`` prefix appears in
        the SQL, matching both plain (``DL_POC.``) and bracketed (``[DL_POC].``)
        forms, case-insensitively. Order-preserving, de-duplicated."""
        found: List[str] = []
        for lower, real in self._by_lower.items():
            esc = re.escape(real)
            # word-boundary db name, optional brackets, followed by a dot
            pattern = rf"(?<![\w.])\[?{esc}\]?\s*\."
            if re.search(pattern, sql, flags=re.IGNORECASE):
                if real not in found:
                    found.append(real)
        return found

    def _strip_db_prefix(self, sql: str, database: str) -> str:
        esc = re.escape(database)
        # Replace `DB.` / `[DB].` (case-insensitive) with nothing, leaving
        # `schema.table`. The negative-lookbehind keeps it from eating a `.DB.`
        # that is actually the middle of a longer qualified name.
        pattern = rf"(?<![\w.])\[?{esc}\]?\s*\."
        return re.sub(pattern, "", sql, flags=re.IGNORECASE)

    def _route(self, sql: str) -> tuple[str, str]:
        dbs = self._databases_referenced(sql)
        if len(dbs) == 1:
            database = dbs[0]
            return database, self._strip_db_prefix(sql, database)
        if len(dbs) > 1:
            # Fabric DOES support cross-database queries with three-part names,
            # but only "to warehouses and databases in the current active
            # workspace" (learn.microsoft.com/fabric/data-warehouse/query-warehouse).
            # A SQL endpoint hostname is per-workspace, so same host == same
            # workspace == the engine can do the join itself. Only a genuinely
            # cross-WORKSPACE query has to fall back to pandas.
            hosts = {self._endpoints[d]["host"] for d in dbs}
            if len(hosts) == 1:
                # Keep the three-part names intact — they are what makes the
                # cross-database reference resolve. Connect to the first one.
                return dbs[0], sql
            by_host: Dict[str, List[str]] = {}
            for d in dbs:
                by_host.setdefault(self._endpoints[d]["host"], []).append(d)
            groups = " | ".join(", ".join(sorted(v)) for v in by_host.values())
            raise ValueError(
                "This query references Fabric lakehouses in DIFFERENT workspaces "
                f"({groups}). Fabric can only join across databases inside one "
                "workspace, so this join cannot run in SQL — query each workspace "
                "separately (e.g. ds_clients[key].execute_query with tables from "
                "ONE workspace) and combine the results with pandas.concat / merge. "
                "Lakehouses listed together above ARE joinable in a single query."
            )
        # No namespaced prefix found.
        if len(self._endpoints) == 1:
            only = next(iter(self._endpoints))
            return only, sql
        raise ValueError(
            "Could not tell which Fabric lakehouse this query targets. Prefix each "
            "table with its lakehouse name as shown in the schema, e.g. "
            "'FROM DL_POC.dbo.CMO_SalesDetails'. Available lakehouses: "
            + ", ".join(sorted(self._endpoints))
        )

    # -- public API (mirrors MsFabricClient for the executor) ---------------
    def execute_query(self, sql: str, database: Optional[str] = None) -> pd.DataFrame:
        """Route `sql` to the right Fabric endpoint and run it. Pass `database`
        to force a target and skip prefix detection."""
        if database:
            real = self._by_lower.get(database.lower(), database)
            if real not in self._endpoints:
                raise ValueError(
                    f"Unknown Fabric lakehouse '{database}'. Available: "
                    + ", ".join(sorted(self._endpoints))
                )
            target, q = real, self._strip_db_prefix(sql, real)
        else:
            target, q = self._route(sql)
        return self._client_for(target).execute_query(q)

    # `query` alias — some code paths call `.query`.
    def query(self, sql: str, *args, **kwargs) -> pd.DataFrame:
        return self.execute_query(sql, database=kwargs.get("database"))

    def get_schemas(self) -> List:
        """No-op: the schema is served from the pre-synced per-user overlay, not
        a live crawl. Returning empty keeps the generic sync path from double
        work if something calls it."""
        return []

    def get_tables(self) -> List:
        return []

    def test_connection(self) -> dict:
        """Healthy if at least one endpoint answers SELECT 1."""
        if not self._endpoints:
            return {"success": False, "message": "No Fabric endpoints discovered yet — sign in to sync."}
        errors = []
        for dbname in self._endpoints:
            try:
                self._client_for(dbname).execute_query("SELECT 1")
                return {"success": True, "message": f"Connected to Fabric ({dbname})."}
            except Exception as e:  # noqa: BLE001
                errors.append(f"{dbname}: {e}")
        return {"success": False, "message": "; ".join(errors)[:400]}

    def _workspace_groups(self) -> List[List[str]]:
        """Lakehouses grouped by workspace (one group per SQL endpoint host)."""
        by_host: Dict[str, List[str]] = {}
        for name, ep in self._endpoints.items():
            by_host.setdefault(ep["host"], []).append(name)
        return [sorted(v) for v in by_host.values()]

    @property
    def description(self) -> str:
        groups = self._workspace_groups()
        if not groups:
            return (
                "Federated Microsoft Fabric (per-user sign-in). No lakehouses "
                "discovered yet — sign in to sync."
            )
        listed = " | ".join(", ".join(g) for g in groups)
        multi = any(len(g) > 1 for g in groups)
        join_rule = (
            "Lakehouses listed together above sit in ONE workspace and CAN be "
            "joined in a single query using their full {lakehouse}.{schema}.{table} "
            "names. Lakehouses in different groups cannot — query each separately "
            "and combine in pandas."
            if multi else
            "Each lakehouse sits in its own workspace, so a single query cannot "
            "join across them — query each separately and combine in pandas."
        )
        return (
            "Federated Microsoft Fabric (per-user sign-in). Tables are named "
            "{lakehouse}.{schema}.{table} and queries route to the right endpoint "
            f"automatically. Lakehouses by workspace: {listed}. {join_rule}"
        )
