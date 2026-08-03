import asyncio
import logging
import time

from app.models.user import User

logger = logging.getLogger(__name__)

# Server-side dedup for background overview re-learns. schedule_overview_relearn
# adds a data_source_id here before spawning the background task and the task
# clears it in its finally block, so a second schedule for the same DS while one
# is still pending is a cheap no-op. Protects against a double-fire regardless of
# how many upload requests (or how the frontend batches) hit the schedule path.
_RELEARN_INFLIGHT: set[str] = set()

from app.models.organization import Organization
from app.models.data_source import DataSource
from app.schemas.data_source_registry import (
    list_available_data_sources,
    config_schema_for,
    default_credentials_schema_for,
    resolve_client_class,
    tool_provider_types,
    is_per_user_connector,
)


def _ds_is_connector(d) -> bool:
    """True when every connection on a data source is a tool provider
    (mcp / custom_api, data_shape="tools"). Such a data source is a
    "connector": a lightweight, tools-only source surfaced in /agents."""
    tps = tool_provider_types()
    conns = getattr(d, "connections", None) or []
    return bool(conns) and all(getattr(c, "type", None) in tps for c in conns)


def _conn_connector_key(conn):
    """The preset key (e.g. 'notion', 'monday') for a single connection so the UI
    can render the provider's icon (even though the connection type is just
    'mcp'). Read from config.catalog_key, else matched by server_url against the
    mcp presets. None if not a known preset connector."""
    import json as _json
    try:
        from app.schemas.data_source_registry import mcp_presets
        by_url = {p["server_url"]: p["key"] for p in mcp_presets() if p.get("server_url")}
    except Exception:
        by_url = {}
    cfg = getattr(conn, "config", None)
    if isinstance(cfg, str):
        try:
            cfg = _json.loads(cfg)
        except Exception:
            cfg = {}
    cfg = cfg or {}
    if cfg.get("catalog_key"):
        return cfg["catalog_key"]
    if cfg.get("server_url") in by_url:
        return by_url[cfg["server_url"]]
    return None


def _ds_connector_key(d):
    """The preset key for a data source — the first connection that resolves to a
    known connector. None if none do."""
    for c in (getattr(d, "connections", None) or []):
        key = _conn_connector_key(c)
        if key:
            return key
    return None
from app.models.user_data_source_credentials import UserDataSourceCredentials
from app.models.data_source_membership import DataSourceMembership, PRINCIPAL_TYPE_USER
from app.models.metadata_resource import MetadataResource
from app.models.metadata_indexing_job import MetadataIndexingJob, IndexingJobStatus
from app.models.git_repository import GitRepository

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.schemas.data_source_schema import (
    DataSourceCreate, DataSourceBase, DataSourceSchema, DataSourceUpdate,
    DataSourceMembershipSchema, DataSourceMembershipCreate, DataSourceUserStatus,
    DataSourceListItemSchema, ConnectionEmbedded,
)
from app.schemas.metadata_resource_schema import MetadataResourceSchema

from pydantic import BaseModel
from app.ai.agents.data_source.data_source import DataSourceAgent
from fastapi import HTTPException

import uuid
from uuid import UUID
import json
from datetime import datetime, timezone

from sqlalchemy import insert, delete, or_, and_, func, exists
from sqlalchemy.exc import IntegrityError
from app.schemas.datasource_table_schema import DataSourceTableSchema
from app.models.datasource_table import DataSourceTable  # Add this import at the top of the file
from app.models.user_data_source_overlay import UserDataSourceTable as UserOverlayTable, UserDataSourceColumn as UserOverlayColumn
from app.models.webhook_data_source_association import webhook_data_source_association

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import selectinload, lazyload
from app.services.instruction_service import InstructionService
from app.schemas.instruction_schema import InstructionCreate
from app.core.telemetry import telemetry
from app.ee.audit.service import audit_service

class DataSourceService:

    def __init__(self):
        pass

    async def _bulk_connection_aux(
        self,
        db: AsyncSession,
        data_sources: list,
        *,
        defer_indexing_events: bool = False,
        include_table_counts: bool = True,
    ):
        """Precompute the per-connection indexing rows and table counts for MANY
        data sources in a handful of grouped queries.

        The list endpoints (e.g. the agents sidebar, especially the admin
        "show all" view) build a ConnectionEmbedded for every connection of
        every data source. Done naively that issues one latest-indexing query
        per data source plus one (or two) table-count queries per connection —
        an N+1 whose cost grows with the number of agents, which is exactly what
        makes "show all" slow. This batches all of it into three queries and
        returns maps keyed by (data source id, connection id) / data source id
        for ``_build_connections_list`` to read.

        ``defer_indexing_events`` skips loading each indexing row's
        ``events_json`` (up to 200 log entries per connection) — pass it from
        list callers that also pass ``include_indexing_events=False`` to
        ``_build_connections_list``, so the event logs are neither fetched nor
        serialized.

        ``include_table_counts=False`` skips the two count queries entirely.
        They are aggregates over ``datasource_tables`` — the org's whole
        catalog, which is the largest table in a connection-heavy workspace —
        so they cost one full scan per request no matter how few agents come
        back. Callers whose response nobody reads a count from (the agent
        picker in the prompt box, the mention menu) should skip them; the
        connections then report ``table_count = None``, which is honestly "not
        counted" rather than a zero that reads as an empty catalog.
        """
        from sqlalchemy.orm import defer
        from app.models.connection_indexing import ConnectionIndexing
        from app.models.connection_table import ConnectionTable

        conn_ids = [str(c.id) for d in data_sources for c in (getattr(d, "connections", None) or [])]
        ds_ids = [str(d.id) for d in data_sources]
        indexing_by_conn: dict = {}
        table_count_by_conn: dict = {}
        legacy_count_by_ds: dict = {}
        if not conn_ids:
            return indexing_by_conn, table_count_by_conn, legacy_count_by_ds

        # Latest indexing row per connection (portable MAX(created_at) join).
        # Restricted to the ORG-shared run (`user_id IS NULL`): per-user catalog
        # syncs also live in this table, and one member's OneDrive sync is not
        # the data source's state — nor anyone else's business.
        try:
            latest_subq = (
                select(
                    ConnectionIndexing.connection_id,
                    func.max(ConnectionIndexing.created_at).label("max_created"),
                )
                .where(
                    ConnectionIndexing.connection_id.in_(conn_ids),
                    ConnectionIndexing.user_id.is_(None),
                )
                .group_by(ConnectionIndexing.connection_id)
                .subquery()
            )
            latest_stmt = select(ConnectionIndexing).where(
                ConnectionIndexing.user_id.is_(None)
            ).join(
                latest_subq,
                (ConnectionIndexing.connection_id == latest_subq.c.connection_id)
                & (ConnectionIndexing.created_at == latest_subq.c.max_created),
            )
            if defer_indexing_events:
                latest_stmt = latest_stmt.options(defer(ConnectionIndexing.events_json))
            rows = await db.execute(latest_stmt)
            for idx in rows.scalars().all():
                indexing_by_conn[str(idx.connection_id)] = idx
        except Exception:
            logger.exception("indexing.bulk_lookup_failed_multi")

        if not include_table_counts:
            return indexing_by_conn, None, None

        # Active table count grouped by (data source, connection) — one query
        # for all connections. Keyed by the pair so a connection shared by two
        # selected agents doesn't report the sum of both agents' tables.
        try:
            count_rows = await db.execute(
                select(
                    DataSourceTable.datasource_id,
                    ConnectionTable.connection_id,
                    func.count(DataSourceTable.id),
                )
                .select_from(DataSourceTable)
                .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                .where(
                    DataSourceTable.datasource_id.in_(ds_ids),
                    DataSourceTable.is_active == True,
                    ConnectionTable.connection_id.in_(conn_ids),
                )
                .group_by(DataSourceTable.datasource_id, ConnectionTable.connection_id)
            )
            for dsid, cid, cnt in count_rows.all():
                table_count_by_conn[(str(dsid), str(cid))] = cnt or 0
        except Exception:
            logger.exception("table_count.bulk_lookup_failed")

        # Legacy fallback: active tables with no connection_table link, per data source.
        try:
            legacy_rows = await db.execute(
                select(DataSourceTable.datasource_id, func.count(DataSourceTable.id))
                .where(
                    DataSourceTable.datasource_id.in_(ds_ids),
                    DataSourceTable.is_active == True,
                    DataSourceTable.connection_table_id == None,
                )
                .group_by(DataSourceTable.datasource_id)
            )
            for dsid, cnt in legacy_rows.all():
                legacy_count_by_ds[str(dsid)] = cnt or 0
        except Exception:
            logger.exception("legacy_table_count.bulk_lookup_failed")

        return indexing_by_conn, table_count_by_conn, legacy_count_by_ds

    async def _build_connections_list(
        self,
        db: AsyncSession,
        data_source: DataSource,
        current_user: User = None,
        live_test: bool = False,
        indexing_by_conn: dict | None = None,
        table_count_by_conn: dict | None = None,
        legacy_count_by_ds: dict | None = None,
        include_indexing_events: bool = True,
        include_table_counts: bool = True,
        cred_index=None,  # connection_identity.UserCredentialIndex
    ) -> List[ConnectionEmbedded]:
        """
        Build list of ConnectionEmbedded from all connections of a DataSource.
        Includes user_status if current_user is provided.

        ``indexing_by_conn`` / ``table_count_by_conn`` / ``legacy_count_by_ds``
        are optional precomputed maps. List endpoints that build many data
        sources at once (e.g. the agents sidebar "show all" view) pass these in
        so the latest-indexing and table-count lookups are batched once across
        the whole list instead of run per-connection — avoiding an N+1 that
        scales with the agent count. Single-data-source callers omit them and
        keep the original per-call queries.

        ``include_indexing_events=False`` drops the indexing event log (up to
        200 entries per connection) from the payload. List endpoints pass it:
        nothing on the /agents page renders the log from a list response, and
        with ~50 connections the logs alone are megabytes per request. The
        single-data-source detail keeps events (the connections modal shows
        live logs from it while indexing runs).

        ``include_table_counts=False`` reports ``table_count = None`` instead of
        counting. Pair it with the same flag on ``_bulk_connection_aux`` — that
        is where the catalog-wide aggregate is actually skipped; this flag only
        keeps the per-connection fallback from running in its place.

        ``cred_index`` is the same idea for ``user_status``: list callers build
        one (connection_identity.UserCredentialIndex) across every agent they
        are about to return, so the per-user credential lookups don't repeat
        per connection.
        """
        from app.schemas.data_source_registry import data_shape_for
        if not data_source.connections:
            return []

        from app.services.user_data_source_credentials_service import UserDataSourceCredentialsService
        from app.models.connection_indexing import ConnectionIndexing
        from app.models.connection_table import ConnectionTable
        from app.models.connection_tool import ConnectionTool

        # One query for all connections' latest indexings — avoids N+1 on
        # GET /data_sources/{id} which is polled every ~2s while indexing runs.
        # Postgres has DISTINCT ON; we use a portable correlated subquery with
        # MAX(created_at) so SQLite + Postgres + others all behave the same.
        # Skipped entirely when the caller supplied a batched map.
        connection_ids = [str(c.id) for c in data_source.connections]
        if indexing_by_conn is None:
            indexing_by_conn = {}
            if connection_ids:
                try:
                    # ORG-shared runs only — see `_bulk_connection_aux`: per-user
                    # catalog syncs share this table and must not surface as the
                    # data source's indexing state.
                    latest_subq = (
                        select(
                            ConnectionIndexing.connection_id,
                            func.max(ConnectionIndexing.created_at).label("max_created"),
                        )
                        .where(
                            ConnectionIndexing.connection_id.in_(connection_ids),
                            ConnectionIndexing.user_id.is_(None),
                        )
                        .group_by(ConnectionIndexing.connection_id)
                        .subquery()
                    )
                    rows = await db.execute(
                        select(ConnectionIndexing)
                        .where(ConnectionIndexing.user_id.is_(None))
                        .join(
                            latest_subq,
                            (ConnectionIndexing.connection_id == latest_subq.c.connection_id)
                            & (ConnectionIndexing.created_at == latest_subq.c.max_created),
                        )
                    )
                    for idx in rows.scalars().all():
                        indexing_by_conn[str(idx.connection_id)] = idx
                except Exception:
                    logger.exception(
                        "indexing.bulk_lookup_failed",
                        extra={"data_source_id": str(data_source.id)},
                    )

        # Tool counts for tool-provider connections, in one grouped query. The
        # catalog of an MCP / Custom API connection is its tool list, so this is
        # the "N items" the UI reports for them — `table_count` is always 0.
        tool_count_by_conn: dict = {}
        if connection_ids:
            tool_rows = await db.execute(
                select(ConnectionTool.connection_id, func.count(ConnectionTool.id))
                .where(ConnectionTool.connection_id.in_(connection_ids))
                .group_by(ConnectionTool.connection_id)
            )
            tool_count_by_conn = {str(cid): (n or 0) for cid, n in tool_rows.all()}

        connections_list = []

        for conn in data_source.connections:
            # Build user status for the connection
            user_status = None
            if current_user:
                u_svc = UserDataSourceCredentialsService()
                try:
                    user_status = await u_svc.build_user_status_for_connection(
                        db=db,
                        connection=conn,
                        user=current_user,
                        data_source=data_source,
                        live_test=live_test,
                        cred_index=cred_index,
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to build user_status for connection {conn.name}: {e}")

            # Get table count for this specific connection
            # Count DataSourceTables that reference ConnectionTables belonging to this connection.
            # When the caller supplied batched maps, read the counts from them
            # instead of issuing per-connection queries (avoids the N+1).
            if not include_table_counts:
                # Not counted rather than counted-as-zero — see
                # _bulk_connection_aux(include_table_counts=False).
                table_count = None
            elif table_count_by_conn is not None:
                table_count = table_count_by_conn.get(
                    (str(data_source.id), str(conn.id)), 0
                )
                # Fallback to legacy (connection_table_id IS NULL) tables, mirroring
                # the per-connection path below.
                if table_count == 0 and legacy_count_by_ds is not None:
                    table_count = legacy_count_by_ds.get(str(data_source.id), 0)
            else:
                table_count_result = await db.execute(
                    select(func.count(DataSourceTable.id))
                    .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                    .where(
                        DataSourceTable.datasource_id == str(data_source.id),
                        DataSourceTable.is_active == True,
                        ConnectionTable.connection_id == str(conn.id)
                    )
                )
                table_count = table_count_result.scalar() or 0

                # Fallback: count legacy tables without connection_table_id
                # This handles data sources created before the ConnectionTable architecture
                if table_count == 0:
                    legacy_count_result = await db.execute(
                        select(func.count(DataSourceTable.id))
                        .where(
                            DataSourceTable.datasource_id == str(data_source.id),
                            DataSourceTable.is_active == True,
                            DataSourceTable.connection_table_id == None
                        )
                    )
                    table_count = legacy_count_result.scalar() or 0

            # User-scoped count: for a user_required connection, the count the UI
            # shows should reflect what THIS user can actually see — their per-user
            # overlay — not the org catalog. Mirror SchemaContextBuilder's
            # effective_auth resolution (which already drives the schema served):
            #   'user'   → count the user's accessible overlay tables
            #   'none'   → 0 (no proven access; don't advertise the catalog)
            #   'system' / non-user_required → keep the canonical catalog count
            # (admins/service-account see everything).
            #
            # ★FORK: skipped when the caller isn't counting. This block is ours
            # and runs AFTER the branch above, so without this guard it put the
            # per-connection COUNT straight back for every user_required
            # connection — exactly the N+1 include_table_counts=False exists to
            # remove, and on a delegated-auth workspace that is EVERY connection.
            # No consumer of the picker list renders a count (they read it from
            # /data_sources, /connections and /data_sources/{id}), so computing
            # it there is pure cost.
            eff_auth = getattr(user_status, "effective_auth", None) if user_status is not None else None
            if include_table_counts and (conn.auth_policy or "system_only") == "user_required" and current_user and eff_auth:
                if eff_auth == "none":
                    table_count = 0
                elif eff_auth == "user":
                    from app.models.user_data_source_overlay import UserDataSourceTable
                    # Scope to this connection via the table link when present.
                    per_conn_result = await db.execute(
                        select(func.count(func.distinct(UserDataSourceTable.table_name)))
                        .join(DataSourceTable, UserDataSourceTable.data_source_table_id == DataSourceTable.id)
                        .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                        .where(
                            UserDataSourceTable.data_source_id == str(data_source.id),
                            UserDataSourceTable.user_id == str(current_user.id),
                            UserDataSourceTable.is_accessible == True,
                            ConnectionTable.connection_id == str(conn.id),
                        )
                    )
                    user_count = per_conn_result.scalar() or 0
                    # Fallback for single-connection sources whose overlay rows
                    # aren't cleanly linked to a connection_table: count the
                    # user's accessible tables at the data-source level.
                    if user_count == 0 and len(data_source.connections) == 1:
                        ds_level_result = await db.execute(
                            select(func.count(func.distinct(UserDataSourceTable.table_name)))
                            .where(
                                UserDataSourceTable.data_source_id == str(data_source.id),
                                UserDataSourceTable.user_id == str(current_user.id),
                                UserDataSourceTable.is_accessible == True,
                            )
                        )
                        user_count = ds_level_result.scalar() or 0
                    table_count = user_count

            # Inline latest indexing row (for UI polling / status badge).
            indexing_row = indexing_by_conn.get(str(conn.id))
            indexing_payload = None
            if indexing_row is not None:
                indexing_payload = {
                    "id": str(indexing_row.id),
                    "status": indexing_row.status,
                    "phase": indexing_row.phase,
                    "current_item": indexing_row.current_item,
                    "progress_done": indexing_row.progress_done or 0,
                    "progress_total": indexing_row.progress_total or 0,
                    "started_at": indexing_row.started_at.isoformat() if indexing_row.started_at else None,
                    "finished_at": indexing_row.finished_at.isoformat() if indexing_row.finished_at else None,
                    "error": indexing_row.error,
                    "stats": indexing_row.stats_json,
                    "events": (indexing_row.events_json or []) if include_indexing_events else [],
                }

            connections_list.append(ConnectionEmbedded(
                id=str(conn.id),
                data_source_id=str(data_source.id),
                name=conn.name,
                type=conn.type,
                auth_policy=conn.auth_policy,
                allowed_user_auth_modes=conn.allowed_user_auth_modes,
                config=conn.config if isinstance(conn.config, dict) else json.loads(conn.config) if conn.config else {},
                is_active=conn.is_active,
                last_synced_at=conn.last_synced_at,
                user_status=user_status,
                table_count=table_count,
                tool_count=tool_count_by_conn.get(str(conn.id), 0),
                indexing=indexing_payload,
                connector_key=_conn_connector_key(conn),
                data_shape=data_shape_for(conn.type),
            ))

        return connections_list

    async def _create_memberships(self, db: AsyncSession, data_source: DataSource, user_ids: List[str], permissions: Optional[List[str]] = None):
        """
        Create memberships for a list of user IDs.

        Writes to both DataSourceMembership (legacy) and ResourceGrant (RBAC).
        `permissions` controls the RBAC grant; defaults to ["view", "view_schema"]
        to match legacy DSM semantics. Pass ["manage"] for the owner.
        """
        if not user_ids:
            return

        from app.models.resource_grant import ResourceGrant
        grant_perms = list(permissions) if permissions is not None else []

        data_source_memberships = [
            DataSourceMembership(
                data_source_id=data_source.id,
                principal_type=PRINCIPAL_TYPE_USER,
                principal_id=user_id,
            )
            for user_id in user_ids
        ]
        db.add_all(data_source_memberships)

        # Mirror into resource_grants (RBAC). Skip if a grant already exists.
        for user_id in user_ids:
            existing = await db.execute(
                select(ResourceGrant).where(
                    ResourceGrant.resource_type == "data_source",
                    ResourceGrant.resource_id == str(data_source.id),
                    ResourceGrant.principal_type == PRINCIPAL_TYPE_USER,
                    ResourceGrant.principal_id == str(user_id),
                    ResourceGrant.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                continue
            db.add(ResourceGrant(
                organization_id=str(data_source.organization_id),
                resource_type="data_source",
                resource_id=str(data_source.id),
                principal_type=PRINCIPAL_TYPE_USER,
                principal_id=str(user_id),
                permissions=grant_perms,
            ))

        await db.commit()

    async def create_data_source(self, db: AsyncSession, organization: Organization, current_user: User, data_source: DataSourceCreate, file_only: bool = False):
        # Convert Pydantic model to dict
        data_source_dict = data_source.dict()

        # file_only: caller holds ONLY create_file_data_source (not full admin /
        # create_data_source). Restrict to upload-based CSV agents. A member must
        # never be able to set config.file_paths (arbitrary server paths read via
        # glob) or link an existing connection (which could point anywhere), so we
        # force empty file_paths + private here — tables arrive later via upload.
        FILE_AGENT_TYPES = {"csv"}
        if file_only:
            req_type = (data_source_dict.get("type") or "").lower()
            if req_type not in FILE_AGENT_TYPES:
                raise HTTPException(status_code=403, detail="You can only create file (CSV upload) agents.")
            # security lock: members may NOT point at server paths — force empty file_paths, force private
            cfg = data_source_dict.get("config") or {}
            if isinstance(cfg, dict):
                cfg = {k: v for k, v in cfg.items() if k not in ("file_paths",)}
                cfg["file_paths"] = ""
                data_source_dict["config"] = cfg
            data_source_dict["is_public"] = False
            # reject linking to existing connections (that could point anywhere)
            if data_source_dict.get("connection_id") or data_source_dict.get("connection_ids"):
                raise HTTPException(status_code=403, detail="File agents cannot link existing connections.")

        if data_source_dict['name'] == '':
            raise HTTPException(status_code=400, detail="Data source name is required")

        # Enforce per-organization agent (data source) cap from the enterprise license.
        # No-op when unlicensed/unset (max_agents == -1 → unlimited).
        from app.ee.license import get_max_agents
        max_agents = get_max_agents()
        if max_agents >= 0:
            count_result = await db.execute(
                select(func.count(DataSource.id)).filter(
                    DataSource.organization_id == organization.id
                )
            )
            current_agents = count_result.scalar() or 0
            if current_agents >= max_agents:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"Agent limit reached for your license ({max_agents}). "
                        "Contact sales to increase your agent count."
                    ),
                )

        # Remove legacy generation flags (generation now deferred to llm_sync after table selection)
        data_source_dict.pop("generate_summary", None)
        data_source_dict.pop("generate_conversation_starters", None)
        data_source_dict.pop("generate_ai_rules", None)
        
        # Extract credentials, config, and membership info
        credentials = data_source_dict.pop("credentials", None)
        config = data_source_dict.pop("config", None)
        is_public = data_source_dict.pop("is_public", False)
        use_llm_sync = data_source_dict.pop("use_llm_sync", False)
        channel_availability = data_source_dict.pop("channel_availability", None)
        member_user_ids = data_source_dict.pop("member_user_ids", [])
        auth_policy = data_source_dict.get("auth_policy", "system_only")
        
        # Check if linking to existing connection(s)
        connection_id = data_source_dict.pop("connection_id", None)
        connection_ids = data_source_dict.pop("connection_ids", None)
        from app.models.connection import Connection

        # Normalize to list of connection IDs
        existing_connection_ids = []
        if connection_ids and len(connection_ids) > 0:
            existing_connection_ids = connection_ids
        elif connection_id:
            existing_connection_ids = [connection_id]

        # Track connections for linking
        connections_to_link: List[Connection] = []

        if existing_connection_ids:
            # === Mode 2: Link to existing connection(s) ===
            from app.models.connection_table import ConnectionTable
            from app.services.connection_service import ConnectionService

            for conn_id in existing_connection_ids:
                conn_result = await db.execute(
                    select(Connection).filter(
                        Connection.id == conn_id,
                        Connection.organization_id == organization.id
                    )
                )
                conn = conn_result.scalar_one_or_none()
                if not conn:
                    raise HTTPException(status_code=404, detail=f"Connection {conn_id} not found")

                connections_to_link.append(conn)

                # Ensure ConnectionTable is populated (may be empty for legacy connections)
                conn_tables_result = await db.execute(
                    select(func.count(ConnectionTable.id)).filter(ConnectionTable.connection_id == conn_id)
                )
                conn_table_count = conn_tables_result.scalar() or 0

                if conn_table_count == 0 and conn.auth_policy == "system_only":
                    # Kick off background indexing — the runner populates
                    # ConnectionTable and then syncs DataSourceTable for every
                    # linked data source. The create call returns without
                    # waiting. The domain starts with zero tables; the UI
                    # polls the indexing status and updates when ready.
                    from app.services.connection_indexing_service import (
                        ConnectionIndexingService,
                    )
                    await ConnectionIndexingService().start(db=db, connection=conn)

            # Use first connection's auth_policy for downstream logic
            auth_policy = connections_to_link[0].auth_policy
            ds_type = connections_to_link[0].type

            # Check enterprise license for ALL restricted data sources
            from app.ee.license import is_datasource_allowed
            for conn in connections_to_link:
                if not is_datasource_allowed(conn.type):
                    raise HTTPException(
                        status_code=402,
                        detail=f"The {conn.type} connector requires an enterprise license."
                    )

            # Extract remaining connection fields that won't be used
            data_source_dict.pop("type", None)
            data_source_dict.pop("allowed_user_auth_modes", None)
        else:
            # === Mode 1: Create new connection ===
            # Validate connection and schema access BEFORE saving (for system_only auth)
            # Skip validation for a connectionless CSV agent with no file_paths yet:
            # it has nothing to read (files/tables arrive later via upload), so a
            # connection test would spuriously 400 on an empty path glob.
            _cfg = config if isinstance(config, dict) else {}
            _is_empty_csv = (
                (data_source_dict.get("type") or "").lower() == "csv"
                and not _cfg.get("file_paths")
            )
            if auth_policy == "system_only" and not _is_empty_csv:
                validation_result = await self.test_new_data_source_connection(
                    db=db, data=data_source, organization=organization, current_user=current_user
                )
                if not validation_result.get("success"):
                    raise HTTPException(
                        status_code=400,
                        detail=validation_result.get("message", "Connection validation failed")
                    )
            
            # Extract connection-related fields
            ds_type = data_source_dict.pop("type", None)
            allowed_user_auth_modes = data_source_dict.pop("allowed_user_auth_modes", None)

            # Default allowed_user_auth_modes for user_required connections —
            # same rule as ConnectionService.create_connection. Without this,
            # a connection created through the data-source form has no modes,
            # which silently disables the /oauth/authorize route.
            if auth_policy == "user_required" and not allowed_user_auth_modes:
                from app.services.connection_service import default_user_auth_modes
                allowed_user_auth_modes = default_user_auth_modes(ds_type, config, credentials)

            # Check enterprise license for restricted data sources
            from app.ee.license import is_datasource_allowed
            if ds_type and not is_datasource_allowed(ds_type):
                raise HTTPException(
                    status_code=402,
                    detail=f"The {ds_type} connector requires an enterprise license."
                )

            # Auto-generate connection name as type-NUMBER (e.g., postgresql-1)
            from sqlalchemy import func as sql_func
            count_result = await db.execute(
                select(sql_func.count(Connection.id)).filter(
                    Connection.organization_id == organization.id,
                    Connection.type == ds_type
                )
            )
            existing_count = count_result.scalar() or 0
            connection_name = f"{ds_type}-{existing_count + 1}"
            
            # Create the Connection
            new_connection = Connection(
                name=connection_name,
                type=ds_type,
                config=json.dumps(config) if config else "{}",
                organization_id=str(organization.id),
                is_active=True,
                auth_policy=auth_policy,
                allowed_user_auth_modes=allowed_user_auth_modes,
            )
            
            # Encrypt and store credentials on connection
            if credentials:
                new_connection.encrypt_credentials(credentials)
            
            db.add(new_connection)
            await db.flush()  # Get the connection ID
        
        # Create base data source dict (without connection-related fields)
        ds_create_dict = {
            "name": data_source_dict["name"],
            "organization_id": organization.id,
            "is_public": is_public,
            "use_llm_sync": use_llm_sync,
            "channel_availability": channel_availability,
            "owner_user_id": current_user.id
        }
        
        # Create the data source instance
        new_data_source = DataSource(**ds_create_dict)

        # Associate with connection(s)
        if connections_to_link:
            # Mode 2: Link to existing connections
            for conn in connections_to_link:
                new_data_source.connections.append(conn)
        else:
            # Mode 1: New connection created above
            new_data_source.connections.append(new_connection)
        
        db.add(new_data_source)
        try:
            await db.commit()
            await db.refresh(new_data_source)
        except IntegrityError as e:
            # Roll back and surface a friendly conflict error for duplicate names per organization
            await db.rollback()
            name = data_source_dict.get("name") or "data source"
            # SQLite message includes "UNIQUE constraint failed: data_sources.organization_id, data_sources.name"
            # Normalize to a clear API error
            raise HTTPException(
                status_code=409,
                detail=f"A data source named '{name}' already exists in this organization. Please choose a different name."
            )

        # Mode 1 created a new connection inline → grant the creator ownership of
        # it too, so they can manage it and build further agents on it.
        if not connections_to_link:
            from app.services.connection_service import grant_connection_owner
            await grant_connection_owner(
                db, str(organization.id), str(new_connection.id), str(current_user.id)
            )

        # Telemetry: data source created (minimal fields only)
        try:
            await telemetry.capture(
                "data_source_created",
                {
                    "data_source_id": str(new_data_source.id),
                    "type": ds_type,
                    "is_public": bool(is_public),
                    "auth_policy": auth_policy,
                    "use_llm_sync": bool(use_llm_sync),
                    "from_existing_connection": bool(existing_connection_ids),
                    "connection_count": len(connections_to_link) if connections_to_link else 1,
                },
                user_id=current_user.id,
                org_id=organization.id,
            )
        except Exception:
            pass

        # Audit log
        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="data_source.created",
                user_id=str(current_user.id),
                resource_type="data_source",
                resource_id=str(new_data_source.id),
                details={"name": new_data_source.name, "type": ds_type, "is_public": bool(is_public), "auth_policy": auth_policy},
            )
        except Exception:
            pass

        # Always add the creator as a member (regardless of public/private status)
        await self._create_memberships(db, new_data_source, [current_user.id], permissions=["manage"])
        
        # Create memberships for additional specified users (only for private data sources)
        if member_user_ids and not is_public:
            # Filter out the creator ID to avoid duplicates
            additional_user_ids = [uid for uid in member_user_ids if uid != current_user.id]
            if additional_user_ids:
                await self._create_memberships(db, new_data_source, additional_user_ids)
                # Notify each newly added member (delayed; only if SMTP configured).
                try:
                    from app.services.data_source_member_email import schedule_member_added_email
                    for uid in additional_user_ids:
                        schedule_member_added_email(
                            data_source_id=str(new_data_source.id),
                            user_id=str(uid),
                            added_by_user_id=str(current_user.id),
                            organization_id=str(organization.id),
                        )
                except Exception as e:
                    logger.warning("Could not schedule member-added emails on create: %s", e)

        # Save tables (validation already passed above)
        # Note: Description, conversation starters, and instructions are generated
        # later via llm_sync (after user selects tables) to use the correct schema
        if connections_to_link:
            # Mode 2: Link to existing connection(s). Seed DataSourceTable from
            # each connection's already-discovered ConnectionTable catalog so the
            # new agent shows tables immediately — for user_required too, not just
            # system_only. The admin/service-principal indexing already populated
            # the shared catalog; per-user accessibility is layered via the
            # overlay at read time. This is a local DB copy (no live fetch / creds):
            # a connection whose catalog is still empty (e.g. delegated-only OBO
            # before anyone signs in) just syncs zero rows and fills in later.
            for conn in connections_to_link:
                await self.sync_domain_tables_from_connection(
                    db, new_data_source, conn,
                    max_auto_select=self.ONBOARDING_MAX_TABLES
                )
            await db.commit()
            await db.refresh(new_data_source)
        elif auth_policy == "system_only":
            # Mode 1: New connection - schema discovery runs in the
            # background. The indexing runner populates ConnectionTable
            # and then syncs DataSourceTable for this data source
            # (and any others linked to the connection).
            from app.services.connection_indexing_service import (
                ConnectionIndexingService,
            )
            logger.info(
                f"create_data_source: Mode 1 - kicking off background indexing "
                f"for new connection {new_connection.id}"
            )
            await ConnectionIndexingService().start(db=db, connection=new_connection)
            await db.commit()
            await db.refresh(new_data_source)

        # Tool-provider connections (mcp / custom_api) carry no schema to index;
        # instead discover their tools now so the connector is immediately usable
        # by the agent (execute_mcp gates on ConnectionTool rows). Members can't
        # call the connection refresh-tools route, so we do it here on create.
        try:
            tps = tool_provider_types()
            conns_for_tools = connections_to_link if connections_to_link else [new_connection]
            tool_conns = [c for c in conns_for_tools if getattr(c, "type", None) in tps]
            if tool_conns:
                from app.services.connection_service import ConnectionService
                _csvc = ConnectionService()
                for c in tool_conns:
                    try:
                        await _csvc.refresh_tools(db, c, current_user)
                    except Exception as _te:
                        logger.warning(f"create_data_source: tool discovery failed for connection {getattr(c,'id',None)}: {_te}")
        except Exception as _te:
            logger.warning(f"create_data_source: tool-provider refresh skipped: {_te}")

        # Reload the data source with relationships to avoid serialization issues
        stmt = (
            select(DataSource)
            .options(
                selectinload(DataSource.data_source_memberships),
                selectinload(DataSource.connections),
                selectinload(DataSource.tables),
            )
            .where(DataSource.id == new_data_source.id)
        )
        result = await db.execute(stmt)
        final_data_source = result.scalar_one()
        
        # Build connections list
        connections_list = await self._build_connections_list(
            db=db,
            data_source=final_data_source,
            current_user=current_user,
            live_test=False
        )

        # Get first connection for legacy fields
        conn = final_data_source.connections[0] if final_data_source.connections else None
        conn_config = None
        if conn and conn.config:
            conn_config = json.loads(conn.config) if isinstance(conn.config, str) else conn.config

        return DataSourceSchema(
            id=str(final_data_source.id),
            organization_id=str(final_data_source.organization_id),
            name=final_data_source.name,
            created_at=final_data_source.created_at,
            updated_at=final_data_source.updated_at,
            context=final_data_source.context,
            description=final_data_source.description,
            summary=final_data_source.summary,
            conversation_starters=final_data_source.conversation_starters,
            is_active=final_data_source.is_active,
            is_public=final_data_source.is_public,
            publish_status=getattr(final_data_source, "publish_status", "published") or "published",
            reliability_status=getattr(final_data_source, "reliability_status", "training") or "training",
            icon=getattr(final_data_source, "icon", None),
            use_llm_sync=final_data_source.use_llm_sync,
            channel_availability=getattr(final_data_source, "channel_availability", None),
            owner_user_id=str(final_data_source.owner_user_id) if final_data_source.owner_user_id else None,
            git_repository=final_data_source.git_repository,
            memberships=final_data_source.data_source_memberships,
            connections=connections_list,
            # Legacy fields from first connection for backward compatibility
            type=conn.type if conn else None,
            config=conn_config,
            auth_policy=conn.auth_policy if conn else None,
            allowed_user_auth_modes=conn.allowed_user_auth_modes if conn else None,
            user_status=connections_list[0].user_status if connections_list else None,
        )

    async def generate_data_source_items(self, db: AsyncSession, item: str, data_source_id: str, organization: Organization, current_user: User):
        # get data source by id
        result = await db.execute(select(DataSource).filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id))
        data_source = result.scalar_one_or_none()

        model = await organization.get_default_llm_model(db)
        if not model:
            raise HTTPException(status_code=400, detail="No default LLM model found")

        schema = await self._get_prompt_schema(db=db, data_source=data_source, organization=organization, current_user=current_user)

        data_source_agent = DataSourceAgent(data_source=data_source, schema=schema, model=model)
        response = {}
        # Each `generate_*` calls sync `LLM.inference` which can't run
        # its pre-call usage-limit check from an active event loop with
        # no `loop` set; offload to a worker thread.
        if item == "summary":
            response["summary"] = await asyncio.to_thread(data_source_agent.generate_summary)
        elif item == "conversation_starters":
            response["conversation_starters"] = await asyncio.to_thread(data_source_agent.generate_conversation_starters)
        elif item == "description":
            response["description"] = await asyncio.to_thread(data_source_agent.generate_description)

        return response

    async def _annotate_disproved_joins(
        self, db: AsyncSession, data_source: DataSource, user: User | None, text: str
    ) -> str:
        """Annotate `A <-> B` join claims in a generated overview that have zero
        overlapping values in the real data.

        Returns `text` unchanged whenever anything is missing or fails: no user,
        no client, no schema, a query error. The only edit this makes is adding
        a note next to a claim measured to be impossible.
        """
        if not text or user is None:
            return text

        from sqlalchemy import text as _sql
        from app.services.join_key_validation import validate_join_claims

        rows = (await db.execute(_sql(
            "SELECT name, columns FROM datasource_tables "
            "WHERE datasource_id = :ds AND deleted_at IS NULL"
        ), {"ds": str(data_source.id)})).all()
        if not rows:
            return text

        columns_by_table: dict = {}
        for name, cols in rows:
            if isinstance(cols, str):
                try:
                    cols = json.loads(cols or "[]")
                except Exception:  # noqa: BLE001
                    cols = []
            columns_by_table[name] = [
                c.get("name") for c in (cols or []) if isinstance(c, dict) and c.get("name")
            ]

        client = await self._build_fabric_federated_client(
            db=db, data_source=data_source, user=user
        )
        if client is None:
            return text

        # The whole validation runs in one worker thread, so the blocking ODBC
        # call inside it needs no further hop.
        fixed, findings = await asyncio.to_thread(
            validate_join_claims, text, columns_by_table, client.execute_query
        )
        for f in findings:
            logger.info(
                "join claim %s <-> %s overlap=%s", f["left"], f["right"], f["overlap"]
            )
        return fixed

    async def llm_sync(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User | None = None, force_llm: bool = False) -> dict:
        """Run LLM onboarding generators for a data source.
        Returns a dict of generated fields.

        ``force_llm=True`` bypasses the ``use_llm_sync`` opt-out guard — used by
        the per-user sign-in "re-learn" path (fabric_user/powerbi_user), which
        must regenerate the overview on the now-real synced schema regardless of
        the agent's stored preference. Default False keeps every existing caller
        byte-identical.
        """
        result: dict = {}

        model = await organization.get_default_llm_model(db)

        # Load the data source model instance for context and schema sync
        ds_q = await db.execute(
            select(DataSource).filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = ds_q.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")

        # Respect the use_llm_sync flag - if disabled, skip all LLM generation.
        # Fallback primary promotion still runs: promoting an EXISTING instruction
        # (e.g. an uploaded Definitions -> Data dictionary) needs no LLM, so a
        # file agent with LLM-learning off is never left with an empty primary.
        if not getattr(data_source, "use_llm_sync", True) and not force_llm:
            await self._maybe_promote_fallback_primary(
            db, data_source, data_source_id, result, current_user=current_user
        )
            result.update({"skipped": True, "reason": "LLM sync disabled for this data source"})
            return result

        # Live "Learn agent" progress stamping (best-effort, gated behind
        # settings.hybrid_learn_progress, and only for the force_llm "Learn"
        # runs — the ones the UI shows a spinner for). Every stamp swallows its
        # own exceptions so a tracker failure can NEVER affect the actual learn.
        # Connector-agnostic: keyed on (data_source_id, user_id).
        from app.settings.config import settings as _lp_settings
        _lp_on = bool(force_llm and getattr(_lp_settings, "hybrid_learn_progress", False))
        _lp_dsid = str(data_source_id)
        _lp_uid = str(current_user.id) if getattr(current_user, "id", None) else None

        async def _lp_start(_tables: int = 0, _columns: int = 0):
            if not _lp_on:
                return
            try:
                from app.services import learn_progress
                await learn_progress.start(db, _lp_dsid, _lp_uid, tables=_tables, columns=_columns)
            except Exception:
                pass

        async def _lp_stage(_stage: str, _step: int):
            if not _lp_on:
                return
            try:
                from app.services import learn_progress
                await learn_progress.set_stage(db, _lp_dsid, _lp_uid, _stage, _step)
            except Exception:
                pass

        async def _lp_done():
            if not _lp_on:
                return
            try:
                from app.services import learn_progress
                await learn_progress.done(db, _lp_dsid, _lp_uid)
            except Exception:
                pass

        async def _lp_error(_message: str):
            if not _lp_on:
                return
            try:
                from app.services import learn_progress
                await learn_progress.error(db, _lp_dsid, _lp_uid, _message)
            except Exception:
                pass

        # Ensure the table schema is reflected BEFORE the LLM generators run.
        # For file/CSV/Excel agents the schema is built asynchronously after
        # upload, so llm_sync (invoked from the create-agent wizard's Set-Context
        # step) can otherwise see an empty schema -> a blank/weak overview -> no
        # primary instruction gets set. refresh_data_source_schema awaits any
        # in-flight indexing (wait_for_active) and reflects tables deterministically.
        # For DB agents whose schema is already synced this returns fast and is a
        # no-op; failures are non-fatal (generators fall back to _get_prompt_schema).
        try:
            await self.refresh_data_source_schema(
                db=db, data_source_id=data_source_id,
                organization=organization, current_user=current_user or User(),
            )
            # Reload the instance after the refresh commit so downstream
            # generators read fresh table state.
            await db.refresh(data_source)
        except Exception as e:
            logger.warning(f"llm_sync: pre-generation schema refresh failed for {data_source_id}: {e}")

        # Learn progress: stage 1 (reading_tables). Best-effort table/column
        # counts from the now-reflected schema — a count failure never blocks.
        if _lp_on:
            _lp_tables, _lp_columns = 0, 0
            try:
                _lp_rows = (await db.execute(
                    select(DataSourceTable).filter(
                        DataSourceTable.datasource_id == data_source_id
                    )
                )).scalars().all()
                _lp_tables = len(_lp_rows)
                for _lp_t in _lp_rows:
                    _lp_columns += len(getattr(_lp_t, "columns", None) or [])
            except Exception:
                pass
            await _lp_start(_lp_tables, _lp_columns)

        try:
            summary = await self.generate_data_source_items(db=db, item="summary", data_source_id=data_source_id, organization=organization, current_user=current_user or User())
            result.update(summary)
            if isinstance(summary, dict) and summary.get("summary"):
                data_source.description = summary.get("summary")
                await db.commit()
                await db.refresh(data_source)
        except Exception:
            pass

        try:
            starters = await self.generate_data_source_items(db=db, item="conversation_starters", data_source_id=data_source_id, organization=organization, current_user=current_user or User())
            result.update(starters)
            if isinstance(starters, dict) and starters.get("conversation_starters") is not None:
                # Dedupe (case-insensitive, order-preserving) and cap: repeated
                # learns were stacking near-identical starters onto the agent.
                _raw_starters = starters.get("conversation_starters") or []
                _seen_keys = set()
                _unique = []
                for _s in _raw_starters:
                    _k = str(_s.get("title") if isinstance(_s, dict) else _s).strip().lower()
                    if _k and _k not in _seen_keys:
                        _seen_keys.add(_k)
                        _unique.append(_s)
                data_source.conversation_starters = _unique[:6]
                await db.commit()
                await db.refresh(data_source)
                # Also materialize the generated starters as agent-scoped Prompts.
                try:
                    from app.services.prompt_service import prompt_service
                    await prompt_service.materialize_starters_for_data_source(db, data_source)
                except Exception:
                    logger.warning("Failed to materialize starter prompts for %s", data_source_id, exc_info=True)
        except Exception:
            pass

        # Generate and save a single overview instruction draft for the onboarding UI
        try:
            # Learn progress: stage 2 (analyzing) — building schema/context.
            await _lp_stage("analyzing", 2)
            from app.ai.context.builders.schema_context_builder import SchemaContextBuilder
            schema_ctx = await SchemaContextBuilder(
                db=db, data_sources=[data_source], organization=organization, report=None
            ).build(with_stats=False)
            schema = schema_ctx.render() if schema_ctx else await self._get_prompt_schema(db=db, data_source=data_source, organization=organization, current_user=current_user or User())
            from app.ai.agents.data_source.data_source import DataSourceAgent
            agent = DataSourceAgent(data_source=data_source, schema=schema, model=model)

            # Gather knowledge the agent already holds beyond the raw schema —
            # chiefly a data-dictionary/glossary instruction ingested from an
            # uploaded Definitions file — so the overview folds it in instead of
            # ignoring it. Excludes any prior onboarding draft (avoids feeding the
            # overview its own previous output).
            extra_context = ""
            try:
                from app.models.instruction import Instruction, instruction_data_source_association
                existing_instr_q = await db.execute(
                    select(Instruction).join(
                        instruction_data_source_association,
                        instruction_data_source_association.c.instruction_id == Instruction.id,
                    ).filter(
                        instruction_data_source_association.c.data_source_id == data_source_id,
                        Instruction.deleted_at.is_(None),
                        Instruction.ai_source != "onboarding",
                    ).order_by(
                        (Instruction.category == "data_modeling").desc(),
                        Instruction.created_at.desc(),
                    ).limit(5)
                )
                existing_instrs = existing_instr_q.scalars().all()
                if existing_instrs:
                    parts = []
                    for ins in existing_instrs:
                        t = (ins.title or ins.category or "note").strip()
                        body = (ins.text or "").strip()
                        if body:
                            parts.append(f"[{t}]\n{body}")
                    extra_context = "\n\n".join(parts)[:6000]
            except Exception as e:
                logger.warning(f"llm_sync: failed to gather existing instructions for {data_source_id}: {e}")

            # Learn progress: stage 3 (generating_overview) — the LLM call.
            await _lp_stage("generating_overview", 3)
            # Offload — sync `generate_datasource_instruction` calls
            # `LLM.inference` whose pre-call usage-limit check can't run
            # from an active event loop without `loop` set.
            instruction_data_raw = await asyncio.to_thread(agent.generate_datasource_instruction, extra_context)

            text = (instruction_data_raw or {}).get("text", "").strip()
            title = (instruction_data_raw or {}).get("title", "").strip()
            category = (instruction_data_raw or {}).get("category", "general")
            load_mode = (instruction_data_raw or {}).get("load_mode", "always")

            # JSON-blob guard: a generator can occasionally return the whole
            # structured payload as a JSON string in `text` (seen on re-learn —
            # the stored draft was a raw `{"title":..,"text":..}` blob). Unwrap it
            # so the stored instruction is prose, not JSON. Acts only when `text`
            # is actually a JSON object carrying a "text" key; safe for both the
            # force_llm and normal paths.
            if text.startswith("{"):
                import re as _re
                _blob = None
                # A trailing markdown fence defeats json.loads ("Extra data:
                # line 1 column N") and used to drop the whole raw blob into the
                # always-loaded overview — observed live on MICROSOFT_FABRIC_OVERVIEW,
                # which shipped `{"title": ..., "confidence": 0.95}```" as its text.
                # Strip fences on BOTH ends before parsing.
                _unfenced = _re.sub(r"\s*```\s*$", "", _re.sub(r"^```(?:json)?\s*", "", text)).strip()
                # Models emit invalid escapes like `*\_id` inside the JSON string
                # (markdown-escaped underscores), which json.loads rejects even
                # with strict=False — retry with those escapes doubled.
                for _candidate in (text, _unfenced,
                                   _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', _unfenced),
                                   _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)):
                    try:
                        _blob = json.loads(_candidate, strict=False)
                        break
                    except Exception:
                        continue
                if isinstance(_blob, dict) and _blob.get("text"):
                    text = str(_blob.get("text")).strip()
                    if _blob.get("title"):
                        title = str(_blob.get("title")).strip()

            # Check the join keys the model just asserted against the real data
            # before this text becomes an always-loaded instruction. The Fabric
            # overview shipped `ARTICLE_CODE ... <-> ProductCode` for weeks; the
            # two columns share zero values. Only a measured zero annotates the
            # claim — every error path leaves the text exactly as generated.
            try:
                text = await self._annotate_disproved_joins(
                    db=db, data_source=data_source, user=current_user, text=text
                )
            except Exception as _jv:  # noqa: BLE001
                logger.info("llm_sync: join-key validation skipped: %s", _jv)

            if text and title:
                # Learn progress: stage 4 (grounding_publishing) — ground
                # @mentions and create/update the overview instruction.
                await _lp_stage("grounding_publishing", 4)
                instruction_service = InstructionService()
                from app.models.instruction import Instruction, instruction_data_source_association

                # Per-user training (Fabric + Power BI): when both the table-select
                # flag and per_user_instructions are on, each signed-in member's
                # Learn produces THEIR OWN private overview (user_id=caller,
                # is_private=true) grounded on their active tables. The lookup and
                # create below are scoped to the caller so users never overwrite
                # each other's overview. Needs per_user_instructions for retrieval
                # isolation, so require both flags.
                from app.settings.config import settings as _settings
                _pu_train = bool(
                    self._per_user_table_select_active(data_source, current_user)
                    and getattr(_settings, "per_user_instructions", False)
                    and getattr(current_user, "id", None)
                )
                _pu_train_filter = (
                    [Instruction.user_id == str(current_user.id)] if _pu_train else []
                )

                # Reuse existing onboarding draft if present (avoids FK cascade issues from old builds)
                if force_llm:
                    # Re-learn path: at agent creation the overview is PUBLISHED
                    # and set as the agent's primary_instruction_id — the
                    # draft-only reuse below would miss it and write a shadow
                    # draft nobody sees, leaving the published (stale) overview on
                    # the agent page. Widen to published+draft and prefer the
                    # instruction the agent actually shows: current primary first,
                    # then any published, then newest. Update it in place (its
                    # status is preserved by the update block, so a published
                    # overview stays published).
                    _primary_id = (
                        str(data_source.primary_instruction_id)
                        if getattr(data_source, "primary_instruction_id", None)
                        else None
                    )
                    _order = []
                    if _primary_id:
                        _order.append((Instruction.id == _primary_id).desc())
                    _order.append((Instruction.status == "published").desc())
                    _order.append(Instruction.created_at.desc())
                    existing_q = await db.execute(
                        select(Instruction).join(
                            instruction_data_source_association,
                            instruction_data_source_association.c.instruction_id == Instruction.id,
                        ).filter(
                            instruction_data_source_association.c.data_source_id == data_source_id,
                            Instruction.ai_source == "onboarding",
                            Instruction.status.in_(["draft", "published"]),
                            Instruction.deleted_at.is_(None),
                            *_pu_train_filter,
                        ).order_by(*_order).limit(1)
                    )
                else:
                    existing_q = await db.execute(
                        select(Instruction).join(
                            instruction_data_source_association,
                            instruction_data_source_association.c.instruction_id == Instruction.id,
                        ).filter(
                            instruction_data_source_association.c.data_source_id == data_source_id,
                            Instruction.ai_source == "onboarding",
                            Instruction.status == "draft",
                            *_pu_train_filter,
                        ).limit(1)
                    )
                existing = existing_q.scalar_one_or_none()

                # Ground any @Token mentions in the generated overview to real
                # DataSourceTable rows so the instruction carries datasource_table
                # references (parity with connector agents). Runs for ALL agent
                # types; safe no-op when there are no mentions or no matches, and
                # any failure falls back to an instruction with NO references.
                overview_refs = []
                try:
                    import re as _ref_re
                    tokens = _ref_re.findall(r"@([A-Za-z_][A-Za-z0-9_./]*)", text or "")
                    if tokens:
                        from app.schemas.instruction_reference_schema import InstructionReferenceCreate

                        def _strip_uuid_prefix(n: str) -> str:
                            return _ref_re.sub(
                                r"^(?:[0-9a-fA-F]{8}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}"
                                r"[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{12}|[0-9a-fA-F]{32})_",
                                "",
                                n or "",
                            )

                        tbl_q = await db.execute(
                            select(DataSourceTable).filter(
                                DataSourceTable.datasource_id == data_source_id
                            )
                        )
                        ds_tables = tbl_q.scalars().all()
                        # Index tables by exact-lower name, uuid-stripped base name,
                        # and (tolerating schema.table) the bare table name.
                        by_name = {}
                        for t in ds_tables:
                            nm = t.name or ""
                            by_name.setdefault(nm.lower(), t)
                            by_name.setdefault(_strip_uuid_prefix(nm).lower(), t)
                            if "." in nm:
                                by_name.setdefault(nm.rsplit(".", 1)[-1].lower(), t)
                        seen_ids = set()
                        for tok in tokens:
                            cand = tok.lower()
                            match = by_name.get(cand) or by_name.get(_strip_uuid_prefix(cand).lower())
                            if match is not None and match.id not in seen_ids:
                                seen_ids.add(match.id)
                                overview_refs.append(InstructionReferenceCreate(
                                    object_type="datasource_table",
                                    object_id=str(match.id),
                                    relation_type="scope",
                                    display_text=tok,
                                ))
                except Exception as _ref_e:
                    overview_refs = []
                    logger.warning(
                        f"llm_sync: failed to ground overview @mentions for {data_source_id}: {_ref_e}"
                    )

                if existing:
                    existing.text = text
                    existing.title = title
                    existing.category = category
                    existing.load_mode = load_mode
                    await db.commit()
                    await db.refresh(existing)
                    result["onboarding_instruction"] = {"id": str(existing.id), "title": title}
                    logger.info(f"Updated onboarding draft instruction {existing.id} for data source {data_source_id}")

                    # Heal a missing primary: this branch REFRESHES an overview
                    # that already existed, and it never pointed the agent at it.
                    # An agent whose primary was cleared (or never set, e.g.
                    # created through the API rather than the wizard) would stay
                    # "No primary instruction" forever, no matter how many times
                    # it was re-learned. Only fills a NULL — an admin's explicit
                    # choice is never overwritten — and never a private overview
                    # (that is per-viewer, resolved in get_data_source).
                    if (
                        force_llm
                        and not _pu_train
                        and not getattr(existing, "is_private", False)
                        and existing.status == "published"
                        and not data_source.primary_instruction_id
                    ):
                        try:
                            data_source.primary_instruction_id = existing.id
                            db.add(data_source)
                            await db.commit()
                            result["primary_instruction_id"] = str(existing.id)
                            result["primary_source"] = "overview_refresh"
                        except Exception as _pe:  # noqa: BLE001
                            await db.rollback()
                            logger.warning(
                                f"llm_sync: could not heal primary instruction for {data_source_id}: {_pe}"
                            )

                    # Re-learn cleanup: if we refreshed the PUBLISHED overview,
                    # soft-delete any leftover onboarding DRAFT for the same DS so
                    # the agent page shows a single instruction (the older code
                    # could leave a stale shadow draft behind). force_llm-only —
                    # the normal draft-reuse path is untouched.
                    if force_llm and existing.status == "published":
                        try:
                            from datetime import datetime as _dt
                            stray_q = await db.execute(
                                select(Instruction).join(
                                    instruction_data_source_association,
                                    instruction_data_source_association.c.instruction_id == Instruction.id,
                                ).filter(
                                    instruction_data_source_association.c.data_source_id == data_source_id,
                                    Instruction.ai_source == "onboarding",
                                    Instruction.status == "draft",
                                    Instruction.deleted_at.is_(None),
                                    Instruction.id != existing.id,
                                )
                            )
                            _strays = stray_q.scalars().all()
                            for _stray in _strays:
                                _stray.deleted_at = _dt.utcnow()
                            if _strays:
                                await db.commit()
                                logger.info(
                                    f"Re-learn: soft-deleted {len(_strays)} stray onboarding "
                                    f"draft(s) for data source {data_source_id}"
                                )
                        except Exception as _stray_e:
                            logger.warning(
                                f"llm_sync: stray-draft cleanup failed for {data_source_id}: {_stray_e}"
                            )
                else:
                    # An explicit re-learn (force_llm) with no prior overview must
                    # produce a LIVE instruction: nothing downstream ever publishes
                    # it (that step is the creation wizard's Finish, which seeded /
                    # sign-in agents never pass through), so a draft here is
                    # invisible forever. The classic wizard path keeps draft.
                    _new_status = "published" if force_llm else "draft"
                    create_payload = InstructionCreate(
                        text=text,
                        title=title,
                        category=category,
                        load_mode=load_mode,
                        ai_source="onboarding",
                        data_source_ids=[data_source_id],
                        status=_new_status,
                        references=overview_refs,
                        # Per-user training → this member's private overview.
                        is_private=bool(_pu_train),
                    )
                    created = await instruction_service.create_instruction(
                        db=db,
                        instruction_data=create_payload,
                        current_user=current_user or User(),
                        organization=organization,
                        # A private per-user overview is NOT a shared/global rule.
                        force_global=not _pu_train,
                        # An explicit learn must go LIVE, not sit in a pending
                        # build ("Pending review") nobody ever approves — the
                        # wizard batching that wanted deferred finalize only uses
                        # the draft (non-force) path. Requires a real user: a
                        # blank User() can't resolve permissions and a failed
                        # finalize would soft-delete the fresh instruction.
                        auto_finalize=bool(force_llm and getattr(current_user, "id", None)),
                    )
                    result["onboarding_instruction"] = {"id": str(created.id), "title": title}
                    logger.info(f"Created onboarding {_new_status} instruction {created.id} for data source {data_source_id}")
                    # Re-learn regenerates the agent's overview — make it the
                    # face of the agent. This intentionally replaces a primary
                    # that the seed-time fallback promoted (a random teaching
                    # rule) or a stale auto-pick; an admin can re-Change it.
                    # NEVER for a per-user PRIVATE overview: primary_instruction_id
                    # is the shared, agent-level face — pointing it at one member's
                    # private overview would leak it to everyone. Private overviews
                    # load per-user via private-instruction retrieval instead.
                    if force_llm and not _pu_train:
                        try:
                            data_source.primary_instruction_id = created.id
                            db.add(data_source)
                            await db.commit()
                        except Exception as _pe:  # noqa: BLE001
                            await db.rollback()
                            logger.warning(f"llm_sync: could not set primary instruction for {data_source_id}: {_pe}")
        except Exception as e:
            logger.warning(f"Failed to generate onboarding instruction: {e}")
            # Learn progress: record the failure (best-effort). done() below
            # will not clobber this error status.
            await _lp_error(str(e))

        # Fallback promote: if the LLM produced no overview draft and this agent
        # still has no primary instruction, promote an existing agent-scoped
        # instruction (e.g. a "Data dictionary" from an uploaded Definitions file).
        await self._maybe_promote_fallback_primary(
            db, data_source, data_source_id, result, current_user=current_user
        )

        # Learn progress: mark this Learn run done (no-op if it recorded an
        # error; done() preserves an error status).
        # Remember what this training actually read, so a later change to the
        # tables can be noticed. Only on a real learn: the draft path does not
        # publish an overview, so nothing has yet described this schema.
        if force_llm:
            try:
                from app.services import training_drift as _td
                _tbls = (await db.execute(
                    select(DataSourceTable).filter(DataSourceTable.datasource_id == data_source.id)
                )).scalars().all()
                await _td.record_trained(db, data_source, _tbls)
            except Exception as _tderr:  # noqa: BLE001
                logger.warning(f"llm_sync: could not record trained schema: {_tderr}")

        await _lp_done()

        return result

    async def _maybe_promote_fallback_primary(
        self, db, data_source, data_source_id, result, current_user=None
    ) -> None:
        """Promote an existing agent-scoped instruction to primary when the agent
        has no primary set and the LLM produced no overview draft.

        Needs no LLM — pure DB — so it is safe to call whether or not LLM sync is
        enabled. Only fires when primary_instruction_id is currently NULL, so it
        never overwrites a primary the user (or an overview draft) already chose.
        """
        try:
            if result.get("onboarding_instruction") or data_source.primary_instruction_id:
                return
            # Per-user connectors (Fabric / Power BI sign-in) resolve their
            # primary PER VIEWER — each member's own private overview, surfaced
            # in get_data_source. Writing a shared instruction into the column
            # here would win over that and show everyone the same generic rule
            # instead of their own learned overview.
            if self._per_user_table_select_active(data_source, current_user):
                return
            from app.models.instruction import Instruction, instruction_data_source_association
            fallback_q = await db.execute(
                select(Instruction).join(
                    instruction_data_source_association,
                    instruction_data_source_association.c.instruction_id == Instruction.id,
                ).filter(
                    instruction_data_source_association.c.data_source_id == data_source_id,
                    Instruction.status == "published",
                    Instruction.deleted_at.is_(None),
                    # ★ Never promote a PRIVATE instruction. primary_instruction_id
                    # is the shared, agent-level face — pointing it at one member's
                    # private overview publishes that member's text to the whole
                    # org. A private overview is load_mode='always', which is
                    # exactly what the ordering below prefers, so without this
                    # filter it is the most likely row to be picked.
                    or_(
                        Instruction.is_private.is_(False),
                        Instruction.is_private.is_(None),
                    ),
                    # Built-in skills ship to every agent of a connector type and
                    # say nothing about THIS agent's data — a poor "face of the
                    # agent", and promoting one would also suppress the real
                    # overview once it arrives.
                    or_(
                        Instruction.ai_source.is_(None),
                        ~Instruction.ai_source.like("builtin:%"),
                    ),
                ).order_by(
                    # Prefer a real overview, then always-on, then the newest.
                    (Instruction.ai_source == "onboarding").desc(),
                    (Instruction.load_mode == "always").desc(),
                    Instruction.created_at.desc(),
                ).limit(1)
            )
            fallback = fallback_q.scalar_one_or_none()
            if fallback:
                data_source.primary_instruction_id = str(fallback.id)
                await db.commit()
                await db.refresh(data_source)
                result["primary_instruction_id"] = str(fallback.id)
                result["primary_source"] = "fallback_existing"
                logger.info(
                    f"llm_sync: promoted existing instruction {fallback.id} to primary "
                    f"for data source {data_source_id} (no overview draft produced)"
                )
        except Exception as e:
            logger.warning(f"llm_sync: fallback primary promotion failed for {data_source_id}: {e}")

    async def get_data_source(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User = None) -> DataSourceSchema:
        # lazyload("*") suppresses the model-level lazy="selectin" cascade
        # (reports → widgets/queries/completions/…). The detail schema does
        # surface git_repository and memberships, so keep those eager. We
        # also suppress the onward cascade on Connection (data_sources →
        # cycle back to DataSource).
        from app.models.instruction import Instruction as InstructionModel
        query = (
            select(DataSource)
            .options(
                lazyload("*"),
                selectinload(DataSource.git_repository),
                selectinload(DataSource.data_source_memberships),
                selectinload(DataSource.connections).options(lazyload("*")),
                selectinload(DataSource.primary_instruction).selectinload(InstructionModel.references),
            )
            .filter(DataSource.id == data_source_id)
            .filter(DataSource.organization_id == organization.id)
        )
        result = await db.execute(query)
        data_source = result.scalar_one_or_none()
        
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")

        # Build connections list from CACHED status only (live_test=False).
        # This endpoint used to live-test every connection whose cached status
        # was older than 5 minutes — sequentially, inside the request, with no
        # connect timeout — so an agent with 50 connections stalled for minutes
        # (and pinned its pooled DB connection the whole time) every TTL window.
        # Freshness is now owned by the background sweeper
        # (connection_status_sweep.sweep_stale_connection_status); reads never
        # dial a warehouse. Batch the indexing + table-count lookups: this
        # endpoint is polled every ~2s while an indexing run is live, and
        # per-connection COUNTs made each poll issue 2×N queries for agents
        # with many connections. Events stay included — the connections modal
        # renders the live indexing log from this payload.
        indexing_by_conn, table_count_by_conn, legacy_count_by_ds = (
            await self._bulk_connection_aux(db, [data_source])
        )
        connections_list = await self._build_connections_list(
            db=db,
            data_source=data_source,
            current_user=current_user,
            live_test=False,
            indexing_by_conn=indexing_by_conn,
            table_count_by_conn=table_count_by_conn,
            legacy_count_by_ds=legacy_count_by_ds,
        )

        # Get first connection for legacy fields
        conn = data_source.connections[0] if data_source.connections else None

        # Parse config from connection (may be stored as JSON string)
        conn_config = None
        if conn and conn.config:
            conn_config = json.loads(conn.config) if isinstance(conn.config, str) else conn.config

        # Resolve the instruction to show as this agent's primary.
        #
        # Normally that is the shared `primary_instruction_id` column. Per-user
        # connectors (Fabric / Power BI sign-in) can never fill it: each member's
        # Learn produces a PRIVATE overview owned by that member, and pointing a
        # shared column at it would publish one person's overview to the whole
        # org. So those agents resolve their primary per VIEWER — the caller's
        # own private overview — which is also the instruction the agent actually
        # loads for them at query time. Nothing is written; this is a read-time
        # resolution only.
        pi = data_source.primary_instruction if data_source.primary_instruction_id else None
        primary_scope = "shared" if pi is not None else None
        if pi is None and current_user is not None:
            try:
                from app.models.instruction import (
                    Instruction as _Instr,
                    instruction_data_source_association as _idsa,
                )
                pi = (await db.execute(
                    select(_Instr).join(
                        _idsa, _idsa.c.instruction_id == _Instr.id,
                    ).options(selectinload(_Instr.references)).filter(
                        _idsa.c.data_source_id == str(data_source.id),
                        _Instr.ai_source == "onboarding",
                        _Instr.status == "published",
                        _Instr.deleted_at.is_(None),
                        _Instr.is_private.is_(True),
                        _Instr.user_id == str(current_user.id),
                    ).order_by(_Instr.created_at.desc()).limit(1)
                )).scalars().first()
                if pi is not None:
                    primary_scope = "personal"
            except Exception as e:  # noqa: BLE001 — never break the agent page
                logger.warning("Failed to resolve personal primary instruction: %s", e)
                pi = None

        primary_instruction_data = None
        if pi is not None:
            try:
                refs = []
                for r in (pi.references or []):
                    refs.append({
                        "id": str(r.id),
                        "object_type": r.object_type,
                        "object_id": str(r.object_id),
                        "column_name": r.column_name,
                        "relation_type": r.relation_type,
                        "display_text": r.display_text,
                    })
                primary_instruction_data = {
                    "id": str(pi.id),
                    "text": pi.text or "",
                    "status": pi.status,
                    "category": pi.category,
                    "source_type": pi.source_type or "user",
                    "load_mode": pi.load_mode or "always",
                    "title": pi.title,
                    "organization_id": str(pi.organization_id),
                    "references": refs,
                    # True once "Improve overview" has been applied (backup snapshot
                    # present) → drives the FE Undo button visibility. Additive, cheap.
                    "improved": bool(
                        (getattr(pi, "structured_data", None) or {}).get("improve_backup")
                    ),
                    # "shared" = the agent-level column every member sees.
                    # "personal" = this viewer's own private overview, resolved at
                    # read time on a per-user connector. The FE labels the card so
                    # nobody mistakes a personal overview for an org-wide one.
                    "scope": primary_scope,
                }
            except Exception as e:
                logger.warning("Failed to serialize primary_instruction: %s", e)

        schema = DataSourceSchema(
            id=str(data_source.id),
            organization_id=str(data_source.organization_id),
            name=data_source.name,
            created_at=data_source.created_at,
            updated_at=data_source.updated_at,
            context=data_source.context,
            description=data_source.description,
            summary=data_source.summary,
            conversation_starters=data_source.conversation_starters,
            is_active=data_source.is_active,
            is_public=data_source.is_public,
            publish_status=getattr(data_source, "publish_status", "published") or "published",
            reliability_status=getattr(data_source, "reliability_status", "training") or "training",
            icon=getattr(data_source, "icon", None),
            use_llm_sync=data_source.use_llm_sync,
            channel_availability=getattr(data_source, "channel_availability", None),
            owner_user_id=data_source.owner_user_id,
            git_repository=data_source.git_repository,
            memberships=data_source.data_source_memberships,
            connections=connections_list,
            # Legacy fields from first connection for backward compatibility
            type=conn.type if conn else None,
            config=conn_config,
            auth_policy=conn.auth_policy if conn else None,
            allowed_user_auth_modes=conn.allowed_user_auth_modes if conn else None,
            user_status=connections_list[0].user_status if connections_list else None,
            primary_instruction_id=data_source.primary_instruction_id,
            primary_instruction=primary_instruction_data,
        )

        return schema


    async def get_available_data_sources(self, db: AsyncSession, organization: Organization):
        items = list_available_data_sources()
        # In-app admin toggle (AND-ed with the env master gate already applied in
        # the registry): hide fabric_user from the catalog if this org turned it
        # off in Settings. Default ON, fail-open (any error → leave list as-is).
        try:
            from app.models.organization_settings import OrganizationSettings
            row = (await db.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == organization.id
                )
            )).scalar_one_or_none()
            cfg = (row.config if row and isinstance(row.config, dict) else {}) or {}
            fabric_enabled = ((cfg.get("connectors") or {}).get("fabric_user_enabled", True))
            if not fabric_enabled:
                items = [x for x in items if x.get("type") != "fabric_user"]
        except Exception:
            pass
        return items

    async def _publish_visibility(self, db: AsyncSession, current_user: User, organization: Organization):
        """Returns (is_governance, manageable_ds_ids, resolved).

        Used to decide whether a non-published agent (draft/disabled) is visible
        to the caller. Managers — org-wide governance (full_admin_access /
        manage_connections) or a per-DS ``manage`` grant — can see their drafts;
        everyone else only sees ``published`` agents. ``resolved`` is returned so
        callers can also gate ``development`` agents on ``manage_evals`` (agent
        admin) without resolving permissions twice. It may be ``None`` when there
        is no user or resolution fails.
        """
        if current_user is None:
            return False, set(), None
        try:
            from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
            resolved = await resolve_permissions(
                db, str(current_user.id), str(organization.id)
            )
            is_gov = (
                FULL_ADMIN in resolved.org_permissions
                or resolved.has_org_permission("manage_connections")
            )
            manage_ids = {
                str(rid)
                for (rtype, rid), perms in resolved.resource_permissions.items()
                if rtype == "data_source" and "manage" in perms
            }
            return is_gov, manage_ids, resolved
        except Exception:
            return False, set(), None

    @staticmethod
    def _development_hidden(reliability_status, ds_id, is_gov, resolved) -> bool:
        """Is this ``development`` agent hidden from the current caller?

        ``development`` agents are pulled from regular users entirely — they only
        stay visible to agent admins (``manage_evals`` on this agent, which
        org-level manage_evals / full_admin imply) and to org governance.
        """
        if (reliability_status or "training") != "development":
            return False
        if is_gov:
            return False
        return not (
            resolved is not None
            and resolved.has_resource_permission("data_source", str(ds_id), "manage_evals")
        )

    async def get_data_sources(self, db: AsyncSession, current_user: User, organization: Organization, show_all: bool = False) -> List[DataSourceListItemSchema]:
        # Query for data sources the user has access to
        # NOTE: Do NOT use selectinload(DataSource.tables) here - it loads ALL tables into memory
        # For data sources with 25K+ tables, this causes severe performance issues
        # Table count is fetched separately via COUNT query in _build_connections_list
        # NOTE: by default we scope this to *explicit* memberships even for
        # admins so the list isn't flooded with every DS in the org. Admins
        # keep capability bypass and can still open any DS via direct URL.
        #
        # When ``show_all`` is requested AND the caller holds org-wide
        # data-source governance (full_admin_access / manage_connections), we
        # drop the membership filter and return every DS in the org. This is
        # the admin "show all" view on the agents page. Per-DS ``manage`` does
        # NOT unlock this (see can_view_all_data_sources).
        from app.core.permission_resolver import (
            get_member_data_source_ids,
            can_view_all_data_sources,
        )
        member_ids = await get_member_data_source_ids(
            db, str(current_user.id), str(organization.id)
        )
        member_id_set = {str(m) for m in member_ids}

        show_all_effective = False
        if show_all:
            show_all_effective = await can_view_all_data_sources(
                db, str(current_user.id), str(organization.id)
            )

        # lazyload("*") suppresses the model-level lazy="selectin" cascade
        # (reports, instructions, entities, files, …); without it, listing
        # data sources triggers ~20 SELECTs hauling in the full report/
        # widget/query graph that this endpoint never returns. We only need
        # connections, and we suppress the cascade on the loaded Connection
        # objects too (Connection.data_sources is also lazy="selectin").
        query = (
            select(DataSource)
            .options(
                lazyload("*"),
                selectinload(DataSource.connections).options(lazyload("*")),
            )
            .filter(DataSource.organization_id == organization.id)
        )
        if not show_all_effective:
            clauses = [DataSource.is_public == True]
            if member_ids:
                clauses.append(DataSource.id.in_(member_ids))
            query = query.filter(or_(*clauses))
        result = await db.execute(query)
        data_sources = result.scalars().all()
        # Non-published agents (draft/disabled) are only visible to managers.
        is_gov, manage_ids, resolved = await self._publish_visibility(db, current_user, organization)
        # Batch the per-connection indexing + table-count lookups across the
        # whole list (same as get_active_data_sources) — without this, an org
        # with many connections per agent pays one COUNT round-trip per
        # connection per agent, which is what made GET /data_sources take tens
        # of seconds on large deployments.
        indexing_by_conn, table_count_by_conn, legacy_count_by_ds = (
            await self._bulk_connection_aux(db, data_sources, defer_indexing_events=True)
        )
        cached_by_ds = await self._cached_table_names_by_ds(db, data_sources)
        # user_status's per-user credential lookups, batched the same way. This
        # list keeps its table counts (its consumers render them), but the
        # credential N+1 has nothing to do with counting — see
        # connection_identity.UserCredentialIndex.
        from app.services.connection_identity import UserCredentialIndex
        cred_index = await UserCredentialIndex.build(
            db, current_user,
            connection_ids=[str(c.id) for d in data_sources for c in (d.connections or [])],
            data_source_ids=[str(d.id) for d in data_sources],
        )
        # Build list with connection info (no live test for list to keep it fast)
        schemas: list[DataSourceListItemSchema] = []
        for d in data_sources:
            publish_status = getattr(d, "publish_status", "published") or "published"
            if publish_status != "published" and not (is_gov or str(d.id) in manage_ids):
                continue
            # `development` agents are hidden from everyone but agent admins.
            if self._development_hidden(
                getattr(d, "reliability_status", "training"), d.id, is_gov, resolved
            ):
                continue
            # Build connections list
            connections_list = await self._build_connections_list(
                db=db,
                data_source=d,
                current_user=current_user,
                live_test=False,
                indexing_by_conn=indexing_by_conn,
                table_count_by_conn=table_count_by_conn,
                legacy_count_by_ds=legacy_count_by_ds,
                include_indexing_events=False,
                cred_index=cred_index,
            )
            conn = d.connections[0] if d.connections else None

            s = DataSourceListItemSchema(
                id=str(d.id),
                name=d.name,
                conversation_starters=getattr(d, "conversation_starters", None),
                description=getattr(d, "description", None),
                created_at=d.created_at,
                status=("active" if bool(d.is_active) else "inactive"),
                is_public=bool(d.is_public),
                publish_status=publish_status,
                reliability_status=getattr(d, "reliability_status", "training") or "training",
                icon=getattr(d, "icon", None),
                connections=connections_list,
                cached_tables=cached_by_ds.get(str(d.id), []),
                is_connector=_ds_is_connector(d),
                connector_key=_ds_connector_key(d),
                # Legacy fields from first connection for backward compatibility
                type=conn.type if conn else None,
                auth_policy=conn.auth_policy if conn else None,
                user_status=connections_list[0].user_status if connections_list else None,
                # Flag entries surfaced only by the admin "show all" view:
                # private and not an explicit membership of the caller.
                admin_only=(
                    show_all_effective
                    and not bool(d.is_public)
                    and str(d.id) not in member_id_set
                ),
            )
            schemas.append(s)
        return schemas

    # ------------------------------------------------------------------
    # Personal "hide from my chat picker" (per-user, reversible).
    # Distinct from the org-wide publish_status='disabled' control: hiding here
    # only removes the agent from THIS user's composer picker. It never disables
    # the agent for others and never touches the AI context.
    # ------------------------------------------------------------------
    async def list_hidden_data_source_ids(self, db: AsyncSession, current_user: User, organization: Organization) -> List[str]:
        from app.models.user_hidden_data_source import UserHiddenDataSource
        rows = (await db.execute(
            select(UserHiddenDataSource.data_source_id).where(
                UserHiddenDataSource.user_id == str(current_user.id),
                UserHiddenDataSource.organization_id == str(organization.id),
                UserHiddenDataSource.deleted_at.is_(None),
            )
        )).scalars().all()
        return [str(r) for r in rows]

    async def hide_data_source(self, db: AsyncSession, current_user: User, organization: Organization, data_source_id: str) -> dict:
        from app.models.user_hidden_data_source import UserHiddenDataSource
        # Idempotent: reuse an existing (possibly soft-deleted) row.
        existing = (await db.execute(
            select(UserHiddenDataSource).where(
                UserHiddenDataSource.user_id == str(current_user.id),
                UserHiddenDataSource.data_source_id == str(data_source_id),
            )
        )).scalars().first()
        if existing is not None:
            existing.deleted_at = None
            existing.organization_id = str(organization.id)
        else:
            db.add(UserHiddenDataSource(
                user_id=str(current_user.id),
                data_source_id=str(data_source_id),
                organization_id=str(organization.id),
            ))
        await db.commit()
        return {"hidden": True, "data_source_id": str(data_source_id)}

    async def unhide_data_source(self, db: AsyncSession, current_user: User, organization: Organization, data_source_id: str) -> dict:
        from app.models.user_hidden_data_source import UserHiddenDataSource
        existing = (await db.execute(
            select(UserHiddenDataSource).where(
                UserHiddenDataSource.user_id == str(current_user.id),
                UserHiddenDataSource.data_source_id == str(data_source_id),
                UserHiddenDataSource.deleted_at.is_(None),
            )
        )).scalars().first()
        if existing is not None:
            existing.deleted_at = datetime.utcnow()
            await db.commit()
        return {"hidden": False, "data_source_id": str(data_source_id)}

    async def _cached_table_names_by_ds(self, db: AsyncSession, data_sources) -> dict:
        """{data_source_id: [names]} of ACTIVATED BOW custom queries.

        One grouped query for the whole list — a per-agent lookup here would add
        a round trip per row to every agent-list render.
        """
        from app.models.connection_table import ConnectionTable, KIND_BOW

        ds_ids = [str(d.id) for d in (data_sources or [])]
        if not ds_ids:
            return {}
        try:
            rows = (await db.execute(
                select(DataSourceTable.datasource_id, ConnectionTable.name)
                .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                .where(
                    DataSourceTable.datasource_id.in_(ds_ids),
                    DataSourceTable.is_active.is_(True),
                    ConnectionTable.kind == KIND_BOW,
                    ConnectionTable.deleted_at.is_(None),
                )
            )).all()
        except Exception as e:
            logger.error(f"_cached_table_names_by_ds failed: {e}")
            return {}
        out: dict = {}
        for ds_id, name in rows:
            out.setdefault(str(ds_id), []).append(name)
        return out

    async def _last_used_at_by_ds(self, db: AsyncSession, organization: Organization, current_user: User, data_sources) -> dict:
        """{data_source_id: when this user last conversed with that agent}.

        Attachment alone is the wrong signal: a fresh report attaches every
        agent the user can see, so association would stamp the whole list as
        "just used" every time someone opens a blank report. Only reports that
        actually got a turn count, which is what makes the ordering mean
        anything. One grouped query for the whole list.
        """
        from app.models.report import Report
        from app.models.completion import Completion
        from app.models.report_data_source_association import (
            report_data_source_association as assoc,
        )

        ds_ids = [str(d.id) for d in (data_sources or [])]
        if not ds_ids or not current_user:
            return {}
        try:
            rows = (await db.execute(
                select(assoc.c.data_source_id, func.max(Report.last_activity_at))
                .select_from(assoc.join(Report, assoc.c.report_id == Report.id))
                .where(
                    Report.organization_id == str(organization.id),
                    Report.user_id == str(current_user.id),
                    assoc.c.data_source_id.in_(ds_ids),
                    exists().where(Completion.report_id == Report.id),
                )
                .group_by(assoc.c.data_source_id)
            )).all()
        except Exception as e:
            logger.error(f"_last_used_at_by_ds failed: {e}")
            return {}
        return {str(ds_id): last for ds_id, last in rows if last is not None}

    async def get_active_data_sources(self, db: AsyncSession, organization: Organization, current_user: User = None, include_unconnected: bool = False, show_all: bool = False, channel: str | None = None) -> List[DataSourceListItemSchema]:
        """Get all active data sources for an organization that the user has access to, compact list shape.

        When ``channel`` is provided (an external channel type such as ``"slack"``,
        ``"teams"`` or ``"mcp"``), agents that have been configured as unavailable
        in that channel are excluded. ``None`` (internal/web) applies no channel
        gating.

        When ``show_all`` is requested AND the caller holds org-wide data-source
        governance, the membership filter is dropped (admin "show all" view) and
        entries the caller isn't a member of are flagged ``admin_only``.
        """
        # See get_data_sources above for the lazyload("*") rationale — same
        # cascade applies here. The list schema doesn't expose
        # data_source_memberships, so we don't eager-load it.
        stmt = (
            select(DataSource)
            .options(
                lazyload("*"),
                selectinload(DataSource.connections).options(lazyload("*")),
            )
            .where(
                DataSource.organization_id == organization.id,
                DataSource.is_active == True
            )
        )
        
        # Apply access control if user is provided (same logic as get_data_sources).
        # When ``show_all`` is requested AND the caller holds org-wide data-source
        # governance, drop the membership filter and return every DS in the org —
        # the admin "show all" view. Entries the admin isn't a member of are
        # flagged ``admin_only`` below.
        member_id_set: set = set()
        show_all_effective = False
        if current_user:
            from app.core.permission_resolver import (
                get_member_data_source_ids,
                can_view_all_data_sources,
            )
            member_ids = await get_member_data_source_ids(
                db, str(current_user.id), str(organization.id)
            )
            member_id_set = {str(m) for m in member_ids}
            if show_all:
                show_all_effective = await can_view_all_data_sources(
                    db, str(current_user.id), str(organization.id)
                )
            if not show_all_effective:
                clauses = [DataSource.is_public == True]
                if member_ids:
                    clauses.append(DataSource.id.in_(member_ids))
                stmt = stmt.filter(or_(*clauses))

        result = await db.execute(stmt)
        data_sources = result.scalars().all()

        # Batch the per-connection indexing + table-count lookups across the
        # whole list so building N agents doesn't issue N×(queries) — the N+1
        # that made the admin "show all" view slow as the org's agent count grew.
        # Table counts are skipped outright: this list feeds the agent pickers
        # (prompt box, mention menu, /agents tree), none of which render a
        # catalog count, and counting means an aggregate over every row in
        # datasource_tables on every call.
        indexing_by_conn, table_count_by_conn, legacy_count_by_ds = (
            await self._bulk_connection_aux(
                db, data_sources, defer_indexing_events=True, include_table_counts=False
            )
        )
        cached_by_ds = await self._cached_table_names_by_ds(db, data_sources)

        # Same treatment for the per-user credential lookups behind user_status:
        # two queries for the whole list instead of two per connection.
        from app.services.connection_identity import UserCredentialIndex
        cred_index = await UserCredentialIndex.build(
            db, current_user,
            connection_ids=[str(c.id) for d in data_sources for c in (d.connections or [])],
            data_source_ids=[str(d.id) for d in data_sources],
        )
        # ★★★A three-way merge resolves this hunk to NOTHING, and that is wrong.
        # Our side is empty only because `cached_by_ds` moved twelve lines up
        # (positional drift), so `git` sees "they added, we deleted" and keeps
        # the deletion — silently dropping upstream's feature rather than
        # reporting a conflict anyone would look at. The `cached_by_ds` line
        # upstream re-adds here IS the one already computed above; only
        # `last_used_by_ds` is new, and it is read at the `last_used_at=` field
        # below, so losing it would leave every agent's "last used" empty.
        last_used_by_ds = await self._last_used_at_by_ds(
            db, organization, current_user, data_sources
        )

        # Compute once whether the current user has admin-level access to data sources
        # (full_admin_access or org-level create_data_source).
        has_update_perm = False
        is_gov = False
        manage_ids: set = set()
        resolved = None
        if current_user:
            try:
                from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
                resolved = await resolve_permissions(
                    db, str(current_user.id), str(organization.id)
                )
                has_update_perm = (
                    FULL_ADMIN in resolved.org_permissions
                    or resolved.has_org_permission("create_data_source")
                )
                # Managers (governance or per-DS `manage`) may also see/use their
                # own draft agents in the selector; everyone else gets published.
                is_gov = (
                    FULL_ADMIN in resolved.org_permissions
                    or resolved.has_org_permission("manage_connections")
                )
                manage_ids = {
                    str(rid)
                    for (rtype, rid), perms in resolved.resource_permissions.items()
                    if rtype == "data_source" and "manage" in perms
                }
            except Exception:
                has_update_perm = False
                resolved = None

        # Batch-resolve owner (creator) identities for display / user-filter.
        owner_map: dict[str, tuple] = {}
        owner_ids = {str(d.owner_user_id) for d in data_sources if getattr(d, "owner_user_id", None)}
        if owner_ids:
            from app.models.user import User as _OwnerUser
            _owners = (await db.execute(select(_OwnerUser).where(_OwnerUser.id.in_(owner_ids)))).scalars().all()
            for _u in _owners:
                owner_map[str(_u.id)] = (getattr(_u, "email", None), getattr(_u, "name", None))

        items: list[DataSourceListItemSchema] = []
        for d in data_sources:
            # Publishing-lifecycle visibility:
            #   disabled → not usable by anyone / excluded from AI, but still
            #              surfaced (greyed) to its managers so they can find and
            #              re-enable it — in the agents list AND the chat picker,
            #              without needing "show all". Same manager rule as draft.
            #   draft    → only managers (governance / per-DS manage)
            #   published → everyone with access
            publish_status = getattr(d, "publish_status", "published") or "published"
            if publish_status == "disabled" and not (show_all_effective or is_gov or str(d.id) in manage_ids):
                continue
            if publish_status == "draft" and not (is_gov or str(d.id) in manage_ids):
                continue
            # `development` agents are hidden from the selector for everyone but
            # agent admins (manage_evals on this agent).
            if self._development_hidden(
                getattr(d, "reliability_status", "training"), d.id, is_gov, resolved
            ):
                continue
            # Channel availability gating (external channels only).
            if not d.is_available_in(channel):
                continue
            # Build connections list (indexing read from the batched map above
            # instead of per-connection queries; table counts not computed —
            # see the _bulk_connection_aux call).
            connections_list = await self._build_connections_list(
                db=db,
                data_source=d,
                current_user=current_user,
                live_test=False,
                indexing_by_conn=indexing_by_conn,
                table_count_by_conn=table_count_by_conn,
                legacy_count_by_ds=legacy_count_by_ds,
                include_indexing_events=False,
                include_table_counts=False,
                cred_index=cred_index,
            )
            conn = d.connections[0] if d.connections else None

            s = DataSourceListItemSchema(
                id=str(d.id),
                name=d.name,
                conversation_starters=getattr(d, "conversation_starters", None),
                description=getattr(d, "description", None),
                created_at=d.created_at,
                status=("active" if bool(d.is_active) else "inactive"),
                is_public=bool(d.is_public),
                publish_status=publish_status,
                reliability_status=getattr(d, "reliability_status", "training") or "training",
                icon=getattr(d, "icon", None),
                last_used_at=last_used_by_ds.get(str(d.id)),
                connections=connections_list,
                cached_tables=cached_by_ds.get(str(d.id), []),
                is_connector=_ds_is_connector(d),
                connector_key=_ds_connector_key(d),
                # Legacy fields from first connection for backward compatibility
                type=conn.type if conn else None,
                auth_policy=conn.auth_policy if conn else None,
                user_status=connections_list[0].user_status if connections_list else None,
                # Flag entries surfaced only by the admin "show all" view:
                # private and not an explicit membership of the caller.
                admin_only=(
                    show_all_effective
                    and not bool(d.is_public)
                    and str(d.id) not in member_id_set
                ),
                owner_user_id=str(d.owner_user_id) if getattr(d, "owner_user_id", None) else None,
                owner_email=owner_map.get(str(getattr(d, "owner_user_id", "")), (None, None))[0],
                owner_name=owner_map.get(str(getattr(d, "owner_user_id", "")), (None, None))[1],
            )

            # Exclude user_required data sources lacking user credentials,
            # unless the user has permission to update data sources (admin/editor)
            # or the caller explicitly opted in via include_unconnected (so the
            # client can surface a "Connect" action for them).
            auth_policy = conn.auth_policy if conn else "system_only"
            if auth_policy == "user_required" and current_user:
                try:
                    has_user_creds = getattr(s.user_status, "has_user_credentials", False)
                except Exception:
                    has_user_creds = False
                if not has_user_creds and not has_update_perm and not include_unconnected:
                    continue
            items.append(s)
        return items

    async def get_public_data_sources(self, db: AsyncSession, organization: Organization, channel: str | None = None) -> List[DataSourceListItemSchema]:
        """
        Get only public active data sources with system_only auth for an organization.
        Used for Slack channel mentions where we can't rely on individual user credentials.
        Only includes data sources that use system-level credentials (auth_policy="system_only").

        When ``channel`` is provided, agents configured as unavailable in that
        channel are excluded.
        """
        stmt = (
            select(DataSource)
            .options(
                lazyload("*"),
                selectinload(DataSource.connections).options(lazyload("*")),
            )
            .where(
                DataSource.organization_id == organization.id,
                DataSource.is_active == True,
                DataSource.is_public == True,  # Only public data sources
                # Slack channel mentions have no manager context — only ever
                # expose published agents (never draft/disabled).
                DataSource.publish_status == "published",
            )
        )

        result = await db.execute(stmt)
        data_sources = result.scalars().all()

        # Batch per-connection indexing + table-count lookups (avoid N+1).
        indexing_by_conn, table_count_by_conn, legacy_count_by_ds = (
            await self._bulk_connection_aux(db, data_sources, defer_indexing_events=True)
        )
        cached_by_ds = await self._cached_table_names_by_ds(db, data_sources)

        items: list[DataSourceListItemSchema] = []
        for d in data_sources:
            # Channel availability gating (external channels only).
            if not d.is_available_in(channel):
                continue
            conn = d.connections[0] if d.connections else None
            # Only include data sources with system_only auth policy
            # Skip user_required data sources since channel mentions can't use individual user credentials
            auth_policy = conn.auth_policy if conn else "system_only"
            if auth_policy == "user_required":
                continue

            connections_list = await self._build_connections_list(
                db=db,
                data_source=d,
                current_user=None,
                live_test=False,
                indexing_by_conn=indexing_by_conn,
                table_count_by_conn=table_count_by_conn,
                legacy_count_by_ds=legacy_count_by_ds,
                include_indexing_events=False,
            )

            s = DataSourceListItemSchema(
                id=str(d.id),
                name=d.name,
                conversation_starters=getattr(d, "conversation_starters", None),
                description=getattr(d, "description", None),
                created_at=d.created_at,
                status=("active" if bool(d.is_active) else "inactive"),
                publish_status=getattr(d, "publish_status", "published") or "published",
                reliability_status=getattr(d, "reliability_status", "training") or "training",
                icon=getattr(d, "icon", None),
                connections=connections_list,
                cached_tables=cached_by_ds.get(str(d.id), []),
                is_connector=_ds_is_connector(d),
                connector_key=_ds_connector_key(d),
                type=conn.type if conn else None,
                auth_policy=auth_policy,
                user_status=connections_list[0].user_status if connections_list else None,
            )
            items.append(s)
        return items

    # Channels an agent can be made available in. Keeping this catalog here (vs.
    # hard-coded in the route/UI) keeps the backend the single source of truth.
    CHANNEL_CATALOG = [
        {"key": "slack", "name": "Slack"},
        {"key": "teams", "name": "Microsoft Teams"},
        {"key": "whatsapp", "name": "WhatsApp"},
        {"key": "google_chat", "name": "Google Chat"},
        {"key": "email", "name": "Email"},
        {"key": "mcp", "name": "MCP"},
    ]

    async def get_connected_channels(self, db: AsyncSession, organization: Organization) -> List[dict]:
        """Return the channel catalog annotated with whether each channel is
        connected for this organization.

        A platform channel (Slack/Teams/WhatsApp/email) is "connected" when an
        active ``ExternalPlatform`` row exists; MCP is "connected" when the
        ``mcp_enabled`` org setting is on. The new-agent UI uses this to render a
        channel-availability toggle for each connected channel.
        """
        from app.models.external_platform import ExternalPlatform

        result = await db.execute(
            select(ExternalPlatform.platform_type).where(
                ExternalPlatform.organization_id == organization.id,
                ExternalPlatform.is_active == True,
            )
        )
        active_types = {row for row in result.scalars().all()}

        mcp_enabled = False
        try:
            if organization.settings:
                cfg = organization.settings.get_config("mcp_enabled")
                mcp_enabled = bool(cfg and getattr(cfg, "value", False))
        except Exception:
            mcp_enabled = False

        channels = []
        for c in self.CHANNEL_CATALOG:
            connected = (c["key"] == "mcp" and mcp_enabled) or (c["key"] in active_types)
            channels.append({**c, "connected": connected})
        return channels

    async def get_data_source_fields(self, db: AsyncSession, data_source_type: str, organization: Organization, current_user: User, auth_type: str | None = None, auth_policy: str | None = None):
        try:
            # Resolve schemas via registry
            config_schema = config_schema_for(data_source_type)
            from app.schemas.data_source_registry import credentials_schema_for, get_entry
            entry = get_entry(data_source_type)
            # Filter auth variants by policy if provided (system_only vs user_required)
            def allowed(mode: str) -> bool:
                try:
                    scopes = (entry.credentials_auth.by_auth.get(mode) or {}).scopes or []
                except Exception:
                    scopes = []
                if not auth_policy or auth_policy == "system_only":
                    return "system" in scopes
                if auth_policy == "user_required":
                    return "user" in scopes
                return True
            # Build config fields
            config_fields = self._extract_fields_from_schema(schema=config_schema)
            # Build credentials fields for default and for all auth modes
            # If a policy is specified and the chosen auth_type is not allowed, drop it so default applies
            if auth_type and not allowed(auth_type):
                auth_type = None
            # When NO auth variant is allowed under this policy — e.g. a pure
            # user-sign-in connector (powerbi_user) queried with system_only,
            # whose only variant is user-scoped — there are no system
            # credentials for the admin to collect (email/password live in the
            # per-user sign-in modal). Return empty so the admin form shows
            # config only, instead of leaking the user-scoped variant's fields
            # into the "System Credentials" box.
            any_allowed = any(allowed(m) for m in (entry.credentials_auth.by_auth or {}))
            if not any_allowed:
                credentials_fields = []
            else:
                default_credentials_schema = credentials_schema_for(data_source_type, auth_type)
                credentials_fields = self._extract_fields_from_schema(schema=default_credentials_schema)
            credentials_by_auth: dict[str, dict] = {}
            for mode, variant in (entry.credentials_auth.by_auth or {}).items():
                if not allowed(mode):
                    continue
                try:
                    credentials_by_auth[mode] = self._extract_fields_from_schema(schema=variant.schema)
                except Exception:
                    continue
            # Get titles/descriptions and auth metadata
            catalog = {d.get("type"): d for d in list_available_data_sources()}
            meta = catalog.get(data_source_type) or {}
            # Setup help ("How to get each value") — curated + generic fallback,
            # so EVERY connector gets a docs block for the right-side panel.
            try:
                from app.data_sources.connector_docs import build_connector_docs
                docs = build_connector_docs(
                    data_source_type,
                    config_fields=config_fields,
                    credentials_fields=credentials_fields,
                    credentials_by_auth=credentials_by_auth,
                    meta=meta,
                )
            except Exception:
                docs = None
            return {
                "config": config_fields,
                "credentials": credentials_fields,
                "credentials_by_auth": credentials_by_auth,
                "type": data_source_type,
                "title": meta.get("title"),
                "description": meta.get("description"),
                "docs": docs,
                # Surface the registry axes so frontend forms / sign-in modals
                # can render the right UX without hardcoding type lists.
                "data_shape": entry.data_shape,
                "catalog_ownership": entry.catalog_ownership,
                "ui_form": entry.ui_form,
                "auth": {
                    "default": entry.credentials_auth.default,
                    "by_auth": {k: {"title": v.title} for k, v in (entry.credentials_auth.by_auth or {}).items() if allowed(k)},
                    "policy": auth_policy or "system_only",
                },
            }
        except Exception as e:
            raise ValueError(f"Schema not found for {data_source_type}: {str(e)}")

    async def build_setup_docx(self, db: AsyncSession, data_source_type: str, organization: Organization, current_user: User, auth_policy: str | None = None):
        """Return (filename, docx_bytes) for a connector's setup worksheet."""
        from app.data_sources.connector_docs import build_connector_docs, render_setup_docx
        payload = await self.get_data_source_fields(db, data_source_type, organization, current_user, auth_policy=auth_policy)
        docs = payload.get("docs") or build_connector_docs(
            data_source_type,
            config_fields=payload.get("config"),
            credentials_fields=payload.get("credentials"),
            credentials_by_auth=payload.get("credentials_by_auth"),
            meta={"title": payload.get("title")},
        )
        data = render_setup_docx(docs)
        safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(data_source_type)) or "connector"
        return f"{safe}-setup-worksheet.docx", data

    async def delete_data_source(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User):
        result = await db.execute(select(DataSource).filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id))
        data_source = result.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")

        # Capture details before deletion for audit
        data_source_name = data_source.name

        # 1) Delete per-user overlay columns and tables (they hard-FK the data source)
        #    Delete columns via subquery of overlay table ids, then overlay tables.
        overlay_ids_subq = select(UserOverlayTable.id).where(UserOverlayTable.data_source_id == data_source_id)
        await db.execute(
            delete(UserOverlayColumn).where(
                UserOverlayColumn.user_data_source_table_id.in_(overlay_ids_subq)
            )
        )
        await db.execute(
            delete(UserOverlayTable).where(UserOverlayTable.data_source_id == data_source_id)
        )

        # 2) Remove direct child rows managed by ORM on update but not guaranteed by DB cascades
        await db.execute(
            delete(DataSourceMembership).where(DataSourceMembership.data_source_id == data_source_id)
        )
        await db.execute(
            delete(UserDataSourceCredentials).where(UserDataSourceCredentials.data_source_id == data_source_id)
        )

        # 2b) Detach this agent from any trigger webhooks. The M2M is declared
        #     only on the Webhook side (Webhook.data_sources), so the ORM has no
        #     idea the secondary table points at us and leaves the rows behind —
        #     Postgres then rejects the parent DELETE with
        #     webhook_data_source_association_data_source_id_fkey. SQLite never
        #     enforced the FK, which is why this only ever bit in production.
        await db.execute(
            delete(webhook_data_source_association).where(
                webhook_data_source_association.c.data_source_id == data_source_id
            )
        )

        # 3) Delete dependent metadata resources first (they FK both data source and jobs)
        resources_q = await db.execute(
            select(MetadataResource).where(MetadataResource.data_source_id == data_source_id)
        )
        for resource in resources_q.scalars().all():
            await db.delete(resource)

        # 4) Delete metadata indexing jobs for this data source
        jobs_q = await db.execute(
            select(MetadataIndexingJob).where(MetadataIndexingJob.data_source_id == data_source_id)
        )
        for job in jobs_q.scalars().all():
            await db.delete(job)

        # 5) Delete any linked git repository for this data source
        repo_q = await db.execute(
            select(GitRepository).where(
                GitRepository.data_source_id == data_source_id,
                GitRepository.organization_id == organization.id,
            )
        )
        repo = repo_q.scalar_one_or_none()
        if repo:
            await db.delete(repo)

        # Apply deletions before removing the data source to avoid NULLing non-nullable FKs
        await db.commit()

        # 6) Delete schema tables and the data source, retrying if a concurrent
        #    connection-indexing job re-creates datasource_tables rows in between.
        #    Creating a domain from an existing connection can start background
        #    indexing that syncs DataSourceTable for every linked data source, so
        #    rows may reappear after we clear them but before the data source is
        #    removed, causing a foreign-key violation. Re-clear and retry until
        #    the indexer stops producing rows.
        max_attempts = 8
        for attempt in range(max_attempts):
            # Delete (possibly re-created) schema tables for this data source
            await self.delete_data_source_tables(db=db, data_source_id=data_source_id, organization=organization, current_user=current_user)
            try:
                await db.delete(data_source)
                await db.commit()
                break
            except IntegrityError:
                await db.rollback()
                if attempt == max_attempts - 1:
                    raise
                # The data source object is expired after rollback; re-fetch it.
                result = await db.execute(select(DataSource).filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id))
                data_source = result.scalar_one_or_none()
                if not data_source:
                    # Already removed elsewhere; nothing left to delete.
                    break
                await asyncio.sleep(min(0.2 * (attempt + 1), 1.0))

        # Audit log
        try:
            await audit_service.log(
                db=db,
                organization_id=str(organization.id),
                action="data_source.deleted",
                user_id=str(current_user.id),
                resource_type="data_source",
                resource_id=str(data_source_id),
                details={"name": data_source_name},
            )
        except Exception:
            pass

        return {"message": "Data source deleted successfully"}

    async def delete_data_source_tables(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User):
        result = await db.execute(select(DataSourceTable).filter(DataSourceTable.datasource_id == data_source_id))
        tables = result.scalars().all()
        for table in tables:
            await db.delete(table)
        await db.commit()
        return {"message": "Data source tables deleted successfully"}
    
    async def test_data_source_connection(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User):
        from datetime import datetime, timezone
        from sqlalchemy.orm import selectinload
        from app.services.connection_service import ConnectionService

        try:
            # Find the data source with connections eager-loaded
            result = await db.execute(
                select(DataSource)
                .options(selectinload(DataSource.connections))
                .filter(
                    DataSource.id == data_source_id,
                    DataSource.organization_id == organization.id
                )
            )
            data_source = result.scalar_one_or_none()
            if not data_source:
                raise ValueError(f"Data source not found: {data_source_id}")

            if not data_source.connections:
                return {"success": False, "message": "Data source has no connections"}

            # Test all connections using ConnectionService (which caches results)
            conn_service = ConnectionService()
            all_success = True
            last_status = None
            for conn in data_source.connections:
                try:
                    last_status = await conn_service.test_connection(
                        db=db,
                        connection_id=str(conn.id),
                        organization=organization,
                        current_user=current_user,
                    )
                    success = bool(last_status.get("success")) if isinstance(last_status, dict) else bool(last_status)
                    if not success:
                        all_success = False
                except Exception as e:
                    all_success = False
                    last_status = {"success": False, "message": str(e)}

            # Reflect connectivity on org-wide flag only for system creds
            if getattr(data_source, "auth_policy", "system_only") == "system_only":
                if not all_success:
                    data_source.is_active = False
                elif data_source.is_active == False:
                    data_source.is_active = True
                await db.commit()

            await db.refresh(data_source)
            connection_status = last_status or {"success": all_success}

        except Exception as e:
            connection_status = {
                "success": False,
                "message": str(e)
            }

        return connection_status
    
    async def test_new_data_source_connection(self, db: AsyncSession, data: DataSourceCreate, organization: Organization, current_user: User):
        """Test connection for a new (unsaved) data source using DataSourceCreate payload.
        Validates both basic connectivity AND schema access (get_tables).
        Does not persist anything to the database.
        """
        try:
            payload = data.dict()
            data_source_type = payload.get("type")
            config = payload.get("config") or {}
            credentials = payload.get("credentials") or {}

            # Instantiate client by type using same naming convention as DataSource.get_client
            client = self._resolve_client_by_type(
                data_source_type=data_source_type,
                config=config,
                credentials=credentials,
            )

            # Step 1: Test basic connectivity
            connection_status = await client.atest_connection()
            if not connection_status.get("success"):
                return connection_status

            # Tool-provider connectors (mcp / custom_api) expose tools, not a
            # tabular schema — connectivity (which lists tools) is the only
            # meaningful validation. Skip schema introspection for them.
            if data_source_type in tool_provider_types():
                return {
                    "success": True,
                    "message": connection_status.get("message", "Connected"),
                    "connectivity": True,
                    "schema_access": True,
                    "table_count": 0,
                }

            # Step 2: Validate schema access by attempting to get tables
            schema_status = await self._avalidate_schema_access(client)
            
            # Combine results
            if not schema_status.get("success"):
                return {
                    "success": False,
                    "message": schema_status.get("message", "Schema validation failed"),
                    "connectivity": True,
                    "schema_access": False,
                    "table_count": 0,
                }
            
            table_count = schema_status.get("table_count", 0)
            from app.services.connection_service import _connected_message
            message = _connected_message(
                data_source_type, table_count,
                approximate=bool(schema_status.get("table_count_approximate")),
            )
            return {
                "success": True,
                "message": message,
                "connectivity": True,
                "schema_access": True,
                "table_count": table_count,
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "connectivity": False,
                "schema_access": False,
            }

    async def _avalidate_schema_access(self, client) -> dict:
        """Validate that we can read schema metadata and find tables (async, offloads to thread).
        Returns a dict with success status, table count, and optional error message.
        """
        try:
            # File sources: count via a metadata-only listing instead of
            # get_schemas(), which would content-extract every PDF/Office doc
            # just to be len()'d here (real indexing re-runs on save), and
            # bounded by VALIDATION_FILE_CAP so testing a large SharePoint
            # library / OneDrive doesn't walk it folder by folder.
            from app.services.connection_service import (
                VALIDATION_FILE_CAP,
                _acount_files_for_validation,
            )
            file_count = await _acount_files_for_validation(
                client, limit=VALIDATION_FILE_CAP
            )
            if file_count is not None:
                return {
                    "success": True,
                    "table_count": file_count,
                    "table_count_approximate": file_count >= VALIDATION_FILE_CAP,
                }

            # Try aget_schemas first (most clients), fall back to get_tables
            tables = None
            if hasattr(client, "aget_schemas"):
                tables = await client.aget_schemas()
            elif hasattr(client, "get_tables"):
                import asyncio
                tables = await asyncio.to_thread(client.get_tables)

            if tables is None:
                return {
                    "success": False,
                    "message": "Client does not support schema introspection",
                    "table_count": 0,
                }

            table_count = len(tables) if tables else 0

            # Note: Empty databases are allowed - schema can be refreshed later when tables are added
            return {
                "success": True,
                "table_count": table_count,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Connected but cannot read schema: {str(e)}",
                "table_count": 0,
            }

    async def resolve_credentials(self, db: AsyncSession, data_source: DataSource, current_user: User | None) -> dict:
        # Get connection from data source
        conn = data_source.connections[0] if data_source.connections else None
        if not conn:
            return {}
        
        # system_only → use stored system credentials
        if conn.auth_policy == "system_only":
            try:
                return conn.decrypt_credentials() or {}
            except Exception:
                return {}
        
        # user_required → require per-user credentials
        if not current_user:
            raise HTTPException(status_code=403, detail="User credentials required")
        row = await db.execute(
            select(UserDataSourceCredentials)
            .where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == current_user.id,
                UserDataSourceCredentials.is_active == True,
            )
            .order_by(UserDataSourceCredentials.is_primary.desc(), UserDataSourceCredentials.updated_at.desc())
        )
        row = row.scalars().first()
        if not row:
            # --- powerbi_mt: multi-tenant delegated routing --------------------
            # The user signed in once; the OAuth callback fanned that token out
            # per tenant and stored a `tenant_tokens = {tenant_id: refresh_token}`
            # map on the connection-level UserConnectionCredentials. If we know
            # which tenant the target table belongs to we can mint a fresh
            # tenant-scoped access token from that tenant's refresh_token and hand
            # PowerBIClient the exact `{access_token, tenant_id}` shape it wants.
            #
            # ★ Per-table routing reachability: resolve_credentials only receives
            #   (data_source, user) — NOT the specific table being queried — so at
            #   THIS layer we cannot pick a tenant per table. The per-table tenant
            #   lives in each overlay row's metadata_json (`tenant_id`), which the
            #   QUERY layer (client construction for a given table) would need to
            #   thread down for true per-table routing. Until that's threaded we
            #   fall back to the home-tenant delegated token below (the connection
            #   resolver's refreshed OAuth token), which serves home-tenant tables
            #   correctly; guest-tenant tables need the query-layer tenant hint.
            if conn.type == "powerbi_mt":
                try:
                    from app.services.connection_service import ConnectionService
                    home_creds = await ConnectionService().resolve_credentials(db, conn, current_user)
                    if isinstance(home_creds, dict) and home_creds.get("access_token"):
                        # Reshape to the delegated shape PowerBIClient expects,
                        # dropping refresh/expiry keys the client ignores.
                        return {
                            "access_token": home_creds.get("access_token"),
                            "tenant_id": home_creds.get("tenant_id"),
                        }
                    return home_creds
                except HTTPException:
                    raise
                except Exception:
                    # Fall through to the generic resolver on any unexpected error.
                    pass
            # No data-source-level creds — delegate to the connection-level resolver,
            # the single source of truth for delegated/OBO tokens, the admin
            # query-identity toggle, OAuth refresh, the legacy owner/admin system
            # fallback, and the "connect required" 403.
            from app.services.connection_service import ConnectionService
            return await ConnectionService().resolve_credentials(db, conn, current_user)

        stored = row.decrypt_credentials() or {}

        # --- powerbi_user: mint a fresh delegated access_token from the stored
        # refresh_token, persist the rotated refresh_token (Azure rotates on every
        # redeem), and return the shape PowerBIClient expects for delegated auth
        # (access_token + tenant_id). Guarded on connection type so every other
        # type stays byte-identical.
        if conn.type == "powerbi_user" and stored.get("auth_mode") == "user_login" and stored.get("refresh_token"):
            tenant_id = stored.get("tenant_id") or "organizations"
            try:
                from app.services.powerbi_user_signin import mint_access_token
                minted = await mint_access_token(tenant_id, stored.get("refresh_token"))
            except Exception:
                minted = {"ok": False}
            if minted.get("ok"):
                # Persist a rotated refresh_token when Azure returns a new one.
                new_rt = minted.get("refresh_token")
                if new_rt and new_rt != stored.get("refresh_token"):
                    try:
                        updated = dict(stored)
                        updated["refresh_token"] = new_rt
                        row.encrypt_credentials(updated)
                        db.add(row)
                        await db.commit()
                    except Exception:
                        await db.rollback()
                return {
                    "access_token": minted.get("access_token"),
                    "tenant_id": stored.get("tenant_id") or None,
                }
            # Mint failed (expired/revoked refresh_token) → force a reconnect.
            raise HTTPException(
                status_code=403,
                detail="Your Power BI sign-in has expired. Please reconnect your Microsoft account.",
            )

        # --- fabric_user: mint a fresh Fabric SQL access_token from the stored
        # refresh_token, persist the rotated refresh_token (Azure rotates on every
        # redeem), and return the shape MsFabricClient expects for delegated auth
        # (access_token). server_hostname/database/schema come from config and are
        # merged by construct_client — do NOT return them here. Guarded on
        # connection type so every other type stays byte-identical.
        if conn.type == "fabric_user" and stored.get("auth_mode") == "user_login" and stored.get("refresh_token"):
            # Mint against the tenant that OWNS the Fabric endpoint. A Fabric SQL
            # endpoint only accepts a token issued by its own tenant — a token from
            # the user's home tenant is rejected with SQL error 18456 when the
            # endpoint lives in a different tenant (guest access). Prefer the
            # admin-configured tenant_id (FabricUserConfig), then the sign-in's home
            # tenant, then the multi-tenant 'organizations' authority.
            _fab_cfg = conn.config
            if isinstance(_fab_cfg, str):
                try:
                    import json as _json
                    _fab_cfg = _json.loads(_fab_cfg or "{}")
                except Exception:
                    _fab_cfg = {}
            config_tenant = (_fab_cfg or {}).get("tenant_id")
            # No admin-configured tenant (device-code connections ship with a
            # blank config): the sync already discovered which tenant OWNS each
            # endpoint and stamped it on the overlay rows (metadata_json.fabric
            # .tenant_id) — prefer that over the sign-in's home tenant, which
            # the endpoint rejects with 18456 for guest access.
            overlay_tenant = None
            if not config_tenant:
                try:
                    from app.models.user_data_source_overlay import UserDataSourceTable as _UDT
                    _md_rows = (await db.execute(
                        select(_UDT.metadata_json).where(
                            _UDT.data_source_id == str(data_source.id),
                            _UDT.user_id == str(current_user.id),
                            _UDT.is_accessible == True,  # noqa: E712
                        ).limit(20)
                    )).scalars().all()
                    for _md in _md_rows:
                        _t = ((_md or {}).get("fabric") or {}).get("tenant_id")
                        if _t:
                            overlay_tenant = _t
                            break
                except Exception:
                    overlay_tenant = None
            tenant_id = config_tenant or overlay_tenant or stored.get("tenant_id") or "organizations"
            try:
                from app.services.fabric_user_signin import mint_access_token
                minted = await mint_access_token(tenant_id, stored.get("refresh_token"))
            except Exception:
                minted = {"ok": False}
            if minted.get("ok"):
                # Persist a rotated refresh_token when Azure returns a new one.
                new_rt = minted.get("refresh_token")
                if new_rt and new_rt != stored.get("refresh_token"):
                    try:
                        updated = dict(stored)
                        updated["refresh_token"] = new_rt
                        row.encrypt_credentials(updated)
                        db.add(row)
                        await db.commit()
                    except Exception:
                        await db.rollback()
                return {
                    "access_token": minted.get("access_token"),
                }
            # Mint failed (expired/revoked refresh_token) → force a reconnect.
            raise HTTPException(
                status_code=403,
                detail="Your Microsoft sign-in has expired. Please reconnect your account.",
            )

        return stored

    async def construct_client(self, db: AsyncSession, data_source: DataSource, current_user: User | None):
        """
        Construct a single client for the first connection.
        DEPRECATED: Use construct_clients() for multi-connection support.
        """
        # Get connection from data source
        if not data_source.connections:
            raise HTTPException(status_code=400, detail="Data source has no associated connection")

        conn = data_source.connections[0]

        # Resolve client class from registry (no model dependency)
        ClientClass = resolve_client_class(conn.type)
        # Merge config and creds
        config = json.loads(conn.config) if isinstance(conn.config, str) else (conn.config or {})
        creds = await self.resolve_credentials(db=db, data_source=data_source, current_user=current_user)
        params = {**(config or {}), **(creds or {})}
        # Strip meta keys and oauth override keys
        meta_keys = {"auth_type", "auth_policy", "allowed_user_auth_modes"}
        params = {k: v for k, v in (params or {}).items() if v is not None and k not in meta_keys and not k.startswith("oauth_")}
        # Narrow to constructor signature — same VAR_KEYWORD-aware logic as
        # ConnectionService.construct_client. Forwarder subclasses (e.g.
        # `class OnedriveClient(GraphDriveClient): def __init__(self, **kw): super().__init__(**kw)`)
        # only expose `self` + `kwargs` to inspect; narrowing on that would
        # strip every real arg (access_token, tenant_id, …). When the ctor
        # accepts **kwargs, pass everything through.
        try:
            import inspect
            sig = inspect.signature(ClientClass.__init__)
            accepts_var_kwargs = any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
            if accepts_var_kwargs:
                allowed = params
            else:
                allowed = {k: v for k, v in params.items() if k in sig.parameters and k != "self"}
        except Exception:
            allowed = params
        return ClientClass(**allowed)

    @staticmethod
    def is_execution_live(ds) -> bool:
        """User-independent lifecycle check for run-time consumers.

        A report's attached data sources are a snapshot taken at creation;
        an agent disabled (or deactivated) afterwards stays on the snapshot.
        Execution paths — client construction, AI context — must skip those:
        ``disabled`` means "excluded from normal use", not just hidden in
        pickers. Draft/development agents remain runnable (their visibility
        is a per-user concern handled by filter_live_data_sources).
        """
        if not getattr(ds, "is_active", True):
            return False
        return (getattr(ds, "publish_status", "published") or "published") != "disabled"

    async def filter_live_data_sources(
        self,
        db: AsyncSession,
        data_sources: list,
        current_user: User | None,
        organization: Organization | None,
        visibility: tuple | None = None,
    ) -> list:
        """Keep only data sources that are live for this caller.

        Mirrors the lifecycle rules of get_active_data_sources so a report's
        attached data sources (a creation-time snapshot, serialized raw
        otherwise) show the same set the selector would offer:
          - inactive / ``disabled`` → dropped for everyone
          - ``draft``               → managers only (governance / per-DS manage)
          - ``development``         → agent admins only (manage_evals)

        ``visibility`` optionally takes a precomputed ``_publish_visibility``
        result so list endpoints can resolve permissions once across many
        reports. With no current_user (system/scheduled contexts) only the
        user-independent checks apply.
        """
        live = [ds for ds in (data_sources or []) if self.is_execution_live(ds)]
        if current_user is None or organization is None:
            return live
        if visibility is None:
            visibility = await self._publish_visibility(db, current_user, organization)
        is_gov, manage_ids, resolved = visibility
        out = []
        for ds in live:
            publish_status = getattr(ds, "publish_status", "published") or "published"
            if publish_status == "draft" and not (is_gov or str(ds.id) in manage_ids):
                continue
            if self._development_hidden(
                getattr(ds, "reliability_status", "training"), ds.id, is_gov, resolved
            ):
                continue
            out.append(ds)
        return out

    async def filter_user_visible_data_sources(
        self,
        db: AsyncSession,
        data_sources: list,
        current_user: User | None,
        organization: Organization,
    ) -> list:
        """Keep only data sources the user is allowed to SEE.

        Visibility (public OR explicit member/grant OR org-wide DS governance)
        is distinct from usability (credentials, handled by
        filter_user_usable_data_sources). A report's attached data sources are
        trusted input from whoever created it, so when a *different* user reads
        that report's context over MCP we must re-filter by their visibility —
        otherwise a private data source's schema leaks to a non-member.

        With no current_user (system/scheduled contexts) nothing is filtered.
        """
        if current_user is None:
            return list(data_sources or [])

        from app.core.permission_resolver import (
            get_member_data_source_ids,
            can_view_all_data_sources,
        )

        if await can_view_all_data_sources(
            db, str(current_user.id), str(organization.id)
        ):
            return list(data_sources or [])

        member_ids = set(
            await get_member_data_source_ids(
                db, str(current_user.id), str(organization.id)
            )
        )
        return [
            ds for ds in (data_sources or [])
            if getattr(ds, "is_public", False) or str(ds.id) in member_ids
        ]

    async def filter_user_usable_data_sources(
        self,
        db: AsyncSession,
        data_sources: list,
        current_user: User | None,
    ) -> tuple[list, list[str]]:
        """Split data sources into (usable, skipped_names) for the given user.

        A user_required data source is NOT usable when the user has neither
        personal credentials nor a system/service-account fallback
        (effective_auth == "none"). Such sources 403 inside construct_clients and
        break create/inspect-data tools mid-run — so callers building agent
        context or clients should exclude them up front rather than attach them
        and fail. With no current_user (system/scheduled contexts) nothing is
        filtered.

        Note: connections must be loaded on each data source (eager-load
        DataSource.connections) — this does not lazy-load in async contexts.
        """
        if not current_user:
            return list(data_sources or []), []

        from app.services.user_data_source_credentials_service import UserDataSourceCredentialsService
        status_svc = UserDataSourceCredentialsService()

        usable: list = []
        skipped: list[str] = []
        for ds in (data_sources or []):
            can_use = True
            for conn in (getattr(ds, "connections", None) or []):
                if (getattr(conn, "auth_policy", None) or "system_only") != "user_required":
                    continue
                try:
                    status = await status_svc.build_user_status_for_connection(
                        db=db, connection=conn, user=current_user, data_source=ds, live_test=False
                    )
                    if getattr(status, "effective_auth", "none") == "none":
                        can_use = False
                        break
                except Exception:
                    # If status can't be determined, exclude — never attach a
                    # source that will 403 at query time.
                    can_use = False
                    break
            if can_use:
                usable.append(ds)
            else:
                skipped.append(getattr(ds, "name", str(getattr(ds, "id", "?"))))
        return usable, skipped

    async def construct_clients(self, db: AsyncSession, data_source: DataSource, current_user: User | None) -> Dict[str, Any]:
        """
        Construct clients for ALL connections in the domain.

        Returns:
            Dict keyed by "{domain_name}:{connection_name}" -> client

        For backward compatibility with legacy code, also adds aliases:
        - "{domain_name}" (only if single connection, for legacy ds_clients.get("name") pattern)
        """
        import inspect
        from typing import Dict, Any

        # Access backstop: never build a credentialed client for a data source
        # the requesting user can't access. This is the deepest chokepoint — every
        # path (main agent, MCP create/inspect_data, file tools) builds clients
        # here, so gating here makes execute_query unreachable for unauthorized
        # sources regardless of what a (possibly stale or hand-crafted) report
        # snapshot claims. `current_user is None` means a trusted system/scheduled
        # context, which is not filtered (mirrors filter_user_*_data_sources).
        if current_user is not None:
            from app.core.permission_resolver import user_can_access_data_source
            if not await user_can_access_data_source(
                db, str(current_user.id), str(data_source.organization_id), data_source
            ):
                raise HTTPException(
                    status_code=403,
                    detail=f"You do not have access to data source '{data_source.name}'",
                )

        if not data_source.connections:
            raise HTTPException(status_code=400, detail="Data source has no associated connections")

        # Skip connections flagged unhealthy. Connection.is_active is a cached
        # reachability flag, not a config toggle: a failed system_only connection
        # test sets it False (ConnectionService.test_connection) and a later
        # success flips it back. Building a client for a known-dead connection
        # lets generated code "try each client" and fail on every run — e.g. a
        # stale `SBODemoIL:SBODemoIL` whose login no longer works. Dropping it
        # here (paired with the schema builder's matching connection filter)
        # keeps both the client set and the model's context off dead
        # connections. If every connection is inactive we return no clients;
        # the data source then drops out downstream (AgentV2 `_has_client`).
        active_connections = [
            conn for conn in data_source.connections
            if getattr(conn, "is_active", True)
        ]

        clients: Dict[str, Any] = {}
        meta_keys = {"auth_type", "auth_policy", "allowed_user_auth_modes"}

        for conn in active_connections:
            key = f"{data_source.name}:{conn.name}"

            # --- fabric_user: federated routing client (Phase 5) ---------------
            # A fabric_user connection spans many SQL endpoints (one per
            # lakehouse). Build ONE routing client that dispatches each query to
            # the right endpoint by the table's `{database}.` prefix, minting a
            # per-tenant SQL token on demand. Works whether the fabric_user
            # connection is alone or alongside other connections in the domain.
            # Falls through to the generic build on any failure.
            if current_user is not None and getattr(conn, "type", None) == "fabric_user":
                try:
                    fed = await self._build_fabric_federated_client(db, data_source, current_user)
                    if fed is not None:
                        self._attach_client_quota_metadata(fed, data_source, conn, key)
                        clients[key] = fed
                        continue
                except Exception as e:  # noqa: BLE001 — never block query on routing build
                    logger.warning("fabric_user federated client build failed: %s", e)

            # A fabric_user connection is CONFIG-LESS (no server_hostname/database
            # — those come from the per-user overlay). If the federated client
            # couldn't be built above (user not signed in, no endpoints synced,
            # or current_user is None), do NOT fall through to the generic build:
            # the plain MsFabricClient requires server_hostname+database and would
            # raise "missing 2 required positional arguments", crashing the whole
            # query. Skip instead — the agent simply has no Fabric client until
            # the user signs in, which the planner handles as "no data".
            if getattr(conn, "type", None) == "fabric_user":
                logger.info("fabric_user '%s': no federated client (user not signed in?) — skipping connection", key)
                continue

            # Resolve client class from registry
            ClientClass = resolve_client_class(conn.type)

            # Merge config and creds
            config = json.loads(conn.config) if isinstance(conn.config, str) else (conn.config or {})

            # Resolve credentials for this specific connection
            creds = await self.resolve_credentials_for_connection(
                db=db,
                connection=conn,
                data_source=data_source,
                current_user=current_user
            )

            params = {**(config or {}), **(creds or {})}
            params = {k: v for k, v in params.items() if v is not None and k not in meta_keys}

            # Narrow to constructor signature (VAR_KEYWORD-aware; see
            # ConnectionService.construct_client for the reasoning).
            try:
                sig = inspect.signature(ClientClass.__init__)
                accepts_var_kwargs = any(
                    p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if accepts_var_kwargs:
                    allowed = params
                else:
                    allowed = {k: v for k, v in params.items() if k in sig.parameters and k != "self"}
            except Exception:
                allowed = params

            client = ClientClass(**allowed)
            self._attach_client_quota_metadata(client, data_source, conn, key)
            await self._attach_stored_table_metadata(db, client, data_source, conn)
            clients[key] = client

            # Accelerated (FAST) relations for this connection, exposed as a
            # sibling client speaking DuckDB SQL. Only relations this agent has
            # ACTIVATED are attached — that filtering is the authorization
            # boundary and it is structural, since a relation absent from the
            # DuckDB catalog cannot be named at all.
            fast_client = await self._construct_fast_client(
                db, data_source, conn, current_user=current_user
            )
            if fast_client is not None:
                fast_key = f"{key}::fast"
                self._attach_client_quota_metadata(fast_client, data_source, conn, fast_key)
                clients[fast_key] = fast_client

        # Backward compatibility: add legacy key aliases for single-connection domains.
        # Guard on a non-empty dict — a connection can be intentionally skipped
        # (e.g. a config-less fabric_user with no federated client because the
        # user hasn't signed in), leaving `clients` empty; `next(iter(...))` on an
        # empty dict raises StopIteration (→ RuntimeError in async).
        if len(active_connections) == 1 and clients:
            first_key = next(iter(clients.keys()))
            first_client = clients[first_key]
            clients[data_source.name] = first_client

        return clients

    async def _construct_fast_client(self, db: AsyncSession, data_source: DataSource,
                                     connection, current_user: User | None = None):
        """Build the FastQueryClient for the custom queries this agent activated.

        Returns None when the agent has activated none — most agents, most of the
        time — so no extra client appears in the common case.

        `current_user` decides which ROWS come back from any relation carrying
        an RLS policy. It has no permissive default: None resolves to an
        anonymous identity, which sees nothing from a protected relation. A
        background path that legitimately needs rows must name a real user.
        """
        from app.models.connection_table import ConnectionTable, KIND_BOW
        from app.services.custom_query_service import CustomQueryService

        try:
            rows = (await db.execute(
                select(ConnectionTable)
                .join(
                    DataSourceTable,
                    DataSourceTable.connection_table_id == ConnectionTable.id,
                )
                .where(
                    ConnectionTable.connection_id == str(connection.id),
                    ConnectionTable.kind == KIND_BOW,
                    ConnectionTable.deleted_at.is_(None),
                    DataSourceTable.datasource_id == str(data_source.id),
                    DataSourceTable.is_active.is_(True),
                )
            )).scalars().unique().all()
        except Exception as e:
            logger.error(f"_construct_fast_client: lookup failed: {e}")
            return None

        if not rows:
            return None

        identity = None
        if any(getattr(r, "rls_enabled", False) for r in rows):
            # Only pay for identity resolution when something actually needs it.
            from app.services.rls_identity_service import resolve_identity
            identity = await resolve_identity(
                db, current_user, str(data_source.organization_id)
            )
        return CustomQueryService.build_fast_client(
            list(rows), connection_name=connection.name, identity=identity
        )

    async def _attach_stored_table_metadata(self, db: AsyncSession, client, data_source: DataSource, connection) -> None:
        """Inject the persisted (indexed) table metadata into clients that
        resolve query targets from it.

        Some connectors address queries by opaque IDs rather than names — e.g.
        Power BI's executeQueries endpoint needs the dataset GUID. Those GUIDs
        are captured at indexing time in DataSourceTable.metadata_json, but the
        client itself is constructed from connection config/credentials only and
        has no DB access at query time. Without this hook its only fallback is a
        live catalog re-crawl on every query. Clients opt in by exposing
        `attach_table_metadata(tables)`.
        """
        if not hasattr(client, "attach_table_metadata"):
            return
        try:
            from app.models.datasource_table import DataSourceTable
            from app.models.connection_table import ConnectionTable

            # Rows linked to THIS connection, plus every unlinked row. Unlinked
            # covers two cases that both need query-time metadata:
            #   - legacy rows indexed before the connection_table link existed
            #   - user-contributed rows: a semantic model the service principal
            #     cannot see (every RLS model — SPs get 401 on those) enters the
            #     catalog through a user's own discovery and never gets a
            #     ConnectionTable link. An inner join dropped them, so the client
            #     could not resolve their dataset GUID and every query against
            #     them failed with "Could not resolve Power BI dataset".
            # Attaching an unlinked row to a sibling connection's client is
            # harmless — resolution is by name, and a name it does not own simply
            # will not match (this is what the old no-rows fallback already did).
            rows = (await db.execute(
                select(DataSourceTable.name, DataSourceTable.metadata_json)
                .outerjoin(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                .where(
                    DataSourceTable.datasource_id == str(data_source.id),
                    DataSourceTable.is_active == True,
                    or_(
                        ConnectionTable.connection_id == str(connection.id),
                        DataSourceTable.connection_table_id.is_(None),
                    ),
                )
            )).all()
            client.attach_table_metadata(
                [{"name": name, "metadata_json": metadata_json} for name, metadata_json in rows]
            )
        except Exception:
            # Non-fatal: the client falls back to live discovery.
            logger.debug("attach_stored_table_metadata failed", exc_info=True)

    def _attach_client_quota_metadata(self, client, data_source: DataSource, connection, client_key: str) -> None:
        try:
            setattr(client, "_bow_connection_id", str(connection.id))
            setattr(client, "_bow_connection_name", connection.name)
            setattr(client, "_bow_data_source_id", str(data_source.id))
            setattr(client, "_bow_data_source_name", data_source.name)
            setattr(client, "_bow_client_key", client_key)
            # Per-connection query timeout override (read by the code-execution
            # wrapper). Stored on the client so the wrapper does not need DB
            # access to resolve it.
            try:
                conn_config = (
                    json.loads(connection.config)
                    if isinstance(connection.config, str)
                    else (connection.config or {})
                )
                conn_timeout = conn_config.get("query_timeout_seconds") if isinstance(conn_config, dict) else None
                if isinstance(conn_timeout, (int, float)) and conn_timeout > 0:
                    setattr(client, "_bow_connection_query_timeout", int(conn_timeout))
                # Same shape for the per-connection concurrency cap: a fragile
                # source can be held to fewer parallel queries than the org
                # default without a schema change.
                conn_conc = conn_config.get("max_concurrent_queries") if isinstance(conn_config, dict) else None
                if isinstance(conn_conc, (int, float)) and conn_conc > 0:
                    setattr(client, "_bow_connection_max_concurrent_queries", int(conn_conc))
            except Exception:
                pass
        except Exception:
            pass

    async def resolve_credentials_for_connection(
        self,
        db: AsyncSession,
        connection,  # Connection model
        data_source: DataSource,
        current_user: User | None
    ) -> dict:
        """
        Resolve credentials for a specific connection.
        Falls back to system credentials stored on the connection.
        """
        auth_policy = connection.auth_policy or "system_only"

        # For user_required, resolve per-user credentials.
        if auth_policy == "user_required" and current_user:
            # Data-source-level per-user creds (legacy user/pass keyed on the DS).
            #
            # NOT for the per-user sign-in connectors. Their DS-scoped row stores a
            # *refresh_token*, never an access_token, so returning the raw blob here
            # hands PowerBIClient a param set with no delegated token — connect()
            # then falls through to the client_credentials grant and dies on
            # AADSTS7000216 ("client_secret is required"), making every Power BI
            # query fail even though sign-in succeeded and the catalog loaded.
            # Those types skip this short-circuit and fall through to
            # ConnectionService.resolve_credentials below, which mints a fresh
            # access_token from the stored refresh_token (silent refresh) and
            # returns the shape the client actually wants. Guarded on connection
            # type so every other connector stays byte-identical.
            if getattr(connection, "type", None) not in ("fabric_user", "powerbi_user"):
                from app.services.user_data_source_credentials_service import UserDataSourceCredentialsService
                u_svc = UserDataSourceCredentialsService()
                try:
                    row = await u_svc.get_primary_active_row(db, data_source, current_user)
                    if row:
                        return row.decrypt_credentials() or {}
                except Exception:
                    pass

            # Connection-level resolution. ConnectionService.resolve_credentials is the
            # single source of truth: it handles delegated/OBO tokens, the admin
            # query-identity toggle (service account vs self), OAuth refresh, the legacy
            # owner/admin system fallback for non-delegated connections, and the
            # "connect required" 403 for a self-identity user with no token.
            from app.services.connection_service import ConnectionService
            return await ConnectionService().resolve_credentials(db, connection, current_user)

        # For system_only or if no user, use system credentials
        return connection.get_credentials() if hasattr(connection, 'get_credentials') else {}

    def _resolve_client_by_type(self, data_source_type: str, config: dict, credentials: dict):
        """Dynamically import and construct the client for a given data source type.
        Mirrors the naming convention used in DataSource.get_client().
        """
        if not data_source_type:
            raise ValueError("Data source type is required")
        try:
            ClientClass = resolve_client_class(data_source_type)

            client_params = (config or {}).copy()
            if credentials:
                client_params.update(credentials)

            # Strip meta keys, empty values, and oauth override keys (not part of client signatures)
            meta_keys = {"auth_type", "auth_policy", "allowed_user_auth_modes"}
            client_params = {k: v for k, v in (client_params or {}).items() if v is not None and v != "" and k not in meta_keys and not k.startswith("oauth_")}

            return ClientClass(**client_params)
        except (ImportError, AttributeError) as e:
            raise ValueError(f"Unable to load data source client for {data_source_type}: {str(e)}")
    
    async def update_data_source(self, db: AsyncSession, data_source_id: str, organization: Organization, data_source: DataSourceUpdate, current_user: User):
        result = await db.execute(
            select(DataSource)
            .options(
                selectinload(DataSource.data_source_memberships),
                selectinload(DataSource.connections)
            )
            .filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id)
        )
        data_source_db = result.scalar_one_or_none()
        
        if not data_source_db:
            raise HTTPException(status_code=404, detail="Data source not found")

        # Extract the update data
        update_data = data_source.dict(exclude_unset=True)
        
        # Detect if connection-relevant fields are being changed
        connection_fields = {'config', 'credentials', 'auth_policy'}
        connection_updates = {k: update_data.pop(k) for k in list(update_data.keys()) if k in connection_fields}
        connection_changed = bool(connection_updates)
        
        # Handle membership updates
        newly_added_member_ids: List[str] = []
        if 'member_user_ids' in update_data:
            member_user_ids = update_data.pop('member_user_ids')
            if member_user_ids is not None:
                # Capture the current member set first so we can tell which of the
                # incoming ids are genuinely *new* (this path replaces the whole
                # membership list, so we must not re-notify existing members).
                existing_result = await db.execute(
                    select(DataSourceMembership.principal_id).where(
                        DataSourceMembership.data_source_id == data_source_id,
                        DataSourceMembership.principal_type == PRINCIPAL_TYPE_USER,
                    )
                )
                existing_member_ids = {str(r) for r in existing_result.scalars().all()}
                newly_added_member_ids = [
                    str(uid) for uid in member_user_ids
                    if str(uid) not in existing_member_ids and str(uid) != str(current_user.id)
                ]
                # Delete existing data_source_memberships
                await db.execute(
                    delete(DataSourceMembership).where(
                        DataSourceMembership.data_source_id == data_source_id
                    )
                )
                # Create new data_source_memberships
                if member_user_ids:
                    await self._create_memberships(db, data_source_db, member_user_ids)
        
        # Handle primary_instruction_id explicitly (allow None to clear it)
        if 'primary_instruction_id' in update_data:
            _new_primary = update_data.pop('primary_instruction_id')
            # ★ A private instruction must never become the shared primary: this
            # column is read by every member of the agent, so pointing it at one
            # person's private overview publishes their text org-wide. The picker
            # only lists instructions the caller can see — which INCLUDES their
            # own private ones — so the refusal has to live here, at the write.
            if _new_primary:
                from app.models.instruction import Instruction as _PInstr
                _pi = (await db.execute(
                    select(_PInstr).filter(_PInstr.id == str(_new_primary))
                )).scalars().first()
                if _pi is not None and getattr(_pi, "is_private", False):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "A private instruction cannot be the agent's primary. "
                            "It is already loaded for you automatically."
                        ),
                    )
            data_source_db.primary_instruction_id = _new_primary

        # Handle icon explicitly so an explicit null clears the custom-icon
        # override (the generic loop below skips None values).
        if 'icon' in update_data:
            data_source_db.icon = update_data.pop('icon')

        # Update remaining domain-specific fields on DataSource
        for field, value in update_data.items():
            if value is not None:
                setattr(data_source_db, field, value)
        
        # Delegate connection-relevant field updates to Connection
        if connection_changed and data_source_db.connections:
            from app.services.connection_service import ConnectionService
            conn_svc = ConnectionService()
            conn = data_source_db.connections[0]
            
            await conn_svc.update_connection(
                db=db,
                connection_id=str(conn.id),
                organization=organization,
                current_user=current_user,
                **connection_updates
            )
        
        try:
            await db.commit()

            # Notify users newly added to this data source (delayed; SMTP-gated).
            if newly_added_member_ids:
                try:
                    from app.services.data_source_member_email import schedule_member_added_email
                    for uid in newly_added_member_ids:
                        schedule_member_added_email(
                            data_source_id=str(data_source_id),
                            user_id=str(uid),
                            added_by_user_id=str(current_user.id),
                            organization_id=str(organization.id),
                        )
                except Exception as e:
                    logger.warning("Could not schedule member-added emails on update: %s", e)

            # Refresh tables if connection fields changed
            if connection_changed and data_source_db.connections:
                conn = data_source_db.connections[0]
                if conn.auth_policy == "system_only":
                    try:
                        from app.services.connection_service import ConnectionService
                        conn_svc = ConnectionService()
                        await conn_svc.refresh_schema(db, conn, current_user)
                    except Exception:
                        # Non-fatal: tables refresh can fail without blocking update
                        pass
            
            # Reload the data source with relationships to avoid serialization issues
            stmt = (
                select(DataSource)
                .options(
                    selectinload(DataSource.data_source_memberships),
                    selectinload(DataSource.connections),
                    selectinload(DataSource.git_repository)
                )
                .where(DataSource.id == data_source_db.id)
            )
            result = await db.execute(stmt)
            final_data_source = result.scalar_one()

            # Audit log
            try:
                await audit_service.log(
                    db=db,
                    organization_id=str(organization.id),
                    action="data_source.updated",
                    user_id=str(current_user.id),
                    resource_type="data_source",
                    resource_id=str(final_data_source.id),
                    details={"name": final_data_source.name},
                )
            except Exception:
                pass

            # Return schema with connection info
            return await self.get_data_source(db, str(final_data_source.id), organization, current_user)
        except IntegrityError as e:
            await db.rollback()
            # Conflict on unique constraint (likely name within organization)
            raise HTTPException(
                status_code=409,
                detail="Another data source with this name already exists in this organization."
            )
        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update data source: {str(e)}")

    def _extract_fields_from_schema(self, schema: BaseModel):
        main_model_schema = schema.model_json_schema()  # (1)!
        
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Extracted schema: {main_model_schema}")

        return main_model_schema

    async def get_data_source_fresh_schema(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User = None):
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id)
        )
        data_source = result.scalar_one_or_none()

        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")


        client = await self.construct_client(db=db, data_source=data_source, current_user=current_user)
        try:
            schema = await client.aget_schemas()
            # Empty list is valid (e.g., empty database) - only None indicates an error
            if schema is None:
                raise HTTPException(status_code=500, detail="No schema returned from data source")
            return schema
        except HTTPException:
            raise
        except Exception as e:
            print(f"Error getting data source schema: {e}")
            raise HTTPException(status_code=500, detail=f"Error getting data source schema: {e}")
    
    async def get_data_source_schema(self, db: AsyncSession, data_source_id: str, include_inactive: bool = False, organization: Organization = None, current_user: User = None, with_stats: bool = False):
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id)
        )
        data_source = result.scalar_one_or_none()
        
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        
        # Get auth_policy from the first connection (auth_policy is now on Connection, not DataSource)
        auth_policy = "system_only"
        if data_source.connections:
            auth_policy = data_source.connections[0].auth_policy or "system_only"
            
        # For user_required policy, read from the persisted user overlay first.
        # Cache-first keeps page renders fast and avoids hammering Drive APIs on
        # every UI navigation.
        #
        # On a cache miss (no overlay rows yet) fall back to the live per-user
        # fetch, which resolves credentials with the owner/admin system-creds
        # fallback and persists the overlay (warming the cache for next time).
        # This is the populate-on-first-read path; it also restores the owner
        # fallback on shared-catalog user_required sources (e.g. SQLite), where
        # an owner refresh stores tables as inactive canonical rows that a
        # cache-only read would miss. If the live fetch can't run (no creds yet,
        # e.g. OneDrive before OAuth) it raises and we drop to the canonical
        # schema below — typically empty for per-user catalogs.
        if auth_policy == "user_required" and current_user is not None:
            # Gate on the user's CURRENT access, not just the (possibly stale)
            # overlay. The overlay's is_accessible flag tracks the last sync, not
            # live credential validity — a disconnected user's rows can linger as
            # accessible. Classify access fresh and serve accordingly.
            effective_auth = await self._resolve_effective_auth(db, data_source, current_user)
            if effective_auth == "user":
                # User has their own creds → their overlay/live catalog only.
                # Never fall through to the canonical (admin) catalog: for shared
                # user_required sources (e.g. Fabric) that would leak tables the
                # user can't actually query.
                try:
                    overlay = await self.read_user_data_source_schema(db=db, data_source=data_source, user=current_user)
                    if overlay:
                        return overlay
                    live = await self.get_user_data_source_schema(db=db, data_source=data_source, user=current_user)
                    return live or []
                except Exception:
                    return []
            elif effective_auth == "none":
                # No proven access (disconnected, expired, revoked) → no tables
                # for a plain member; do NOT leak the canonical catalog to them.
                # Owner/admin fall through to the canonical catalog below — they
                # already see it via connection management endpoints, and hiding
                # it here only breaks agent configuration before first sign-in.
                if not await self._admin_catalog_access(db, data_source, current_user):
                    return []
            # effective_auth == "system" → owner/admin via service account:
            # fall through to the canonical full catalog below.

        schemas = await data_source.get_schemas(db=db, include_inactive=include_inactive, with_stats=with_stats)

        return schemas

    async def _admin_catalog_access(self, db: AsyncSession, data_source: DataSource, current_user: User) -> bool:
        """May this not-yet-connected caller see the CANONICAL catalog for
        configuration purposes?

        True for the data source's owner and for org admins / manage_connections
        holders — the same audience `connection_identity.is_admin_or_owner`
        grants the query-identity toggle to, and the audience that already sees
        the canonical table list via connection management endpoints. This is a
        DISPLAY fallback only: query execution still resolves per-user
        credentials and fails closed ("Connect required")."""
        try:
            if str(getattr(data_source, "owner_user_id", "")) == str(getattr(current_user, "id", "")):
                return True
        except Exception:
            pass
        try:
            from app.core.permission_resolver import FULL_ADMIN, resolve_permissions
            resolved = await resolve_permissions(
                db, str(current_user.id), str(data_source.organization_id)
            )
            return (
                FULL_ADMIN in resolved.org_permissions
                or resolved.has_org_permission("manage_connections")
            )
        except Exception:
            return False

    async def _resolve_effective_auth(self, db: AsyncSession, data_source: DataSource, current_user: User) -> str:
        """Classify a user's CURRENT access to a (user_required) data source.

        Returns one of:
          'user'   — the user has their own active credentials (use their overlay)
          'system' — owner/admin using the service-account fallback (full catalog)
          'none'   — no proven access (use nothing)

        Fails closed to 'none' so a stale overlay can't keep serving tables after
        access is lost. Owner/admin reliably classify as 'system', so the closed
        default never hides the canonical catalog from them.
        """
        try:
            conn = data_source.connections[0] if getattr(data_source, "connections", None) else None
            if conn is None:
                return "none"
            from app.services.user_data_source_credentials_service import UserDataSourceCredentialsService
            status = await UserDataSourceCredentialsService().build_user_status_for_connection(
                db, conn, current_user, data_source=data_source, live_test=False
            )
            return status.effective_auth or "none"
        except Exception:
            return "none"

    async def get_data_source_schema_paginated(
        self,
        db: AsyncSession,
        data_source_id: str,
        organization: Organization,
        page: int = 1,
        page_size: int = 100,
        schema_filter: List[str] = None,
        connection_filter: List[str] = None,
        search: str = None,
        sort_by: str = "is_active",
        sort_dir: str = "desc",
        include_inactive: bool = True,
        selected_state: str = None,  # 'selected', 'unselected', or None for all
        with_stats: bool = False,
        current_user: User = None,
        exclude_file_source_types: bool = False,  # hide file-connection catalog rows (they're Files, not Tables)
    ):
        """
        Get paginated tables for a data source with filtering and sorting.
        Returns PaginatedTablesResponse with tables, counts, and metadata.
        """
        from app.schemas.datasource_table_schema import PaginatedTablesResponse, DataSourceTableSchema, ConnectionInfo
        from app.models.connection_table import ConnectionTable
        from app.models.connection import Connection
        from sqlalchemy import func, case, and_
        import math
        
        # Verify data source exists
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = result.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")

        # Identity-aware scoping: for a user_required (delegated) source, the tables
        # selector must show what the CURRENT effective identity can see — the same
        # rule the agent's schema context and query execution follow:
        #   'user'   (toggle = Me, has token) → only the user's overlay tables
        #   'none'   (Me, not connected)      → nothing
        #   'system' (toggle = Service account / admin SP) → full catalog
        # `overlay_table_ids is None` means "no restriction" (full catalog).
        overlay_table_ids = None
        # For per-user connectors with the flag on, the CHECKBOX state must reflect
        # the caller's OWN overlay is_active (not the shared catalog). Map keyed by
        # data_source_table_id, applied at serialization below.
        overlay_active_by_dstid = None
        _per_user_active = self._per_user_table_select_active(data_source, current_user)
        conn0 = data_source.connections[0] if getattr(data_source, "connections", None) else None
        if current_user is not None and conn0 is not None and (conn0.auth_policy or "system_only") == "user_required":
            eff_auth = await self._resolve_effective_auth(db, data_source, current_user)
            if eff_auth == "user":
                ov = await db.execute(
                    select(UserOverlayTable.data_source_table_id, UserOverlayTable.is_active).where(
                        UserOverlayTable.data_source_id == str(data_source_id),
                        UserOverlayTable.user_id == str(current_user.id),
                        UserOverlayTable.is_accessible == True,
                        UserOverlayTable.data_source_table_id.isnot(None),
                    )
                )
                _ov_rows = ov.all()
                overlay_table_ids = [r[0] for r in _ov_rows]
                if _per_user_active:
                    overlay_active_by_dstid = {r[0]: bool(r[1]) for r in _ov_rows}
            elif eff_auth == "none":
                # Not connected yet. For a plain member this fails closed
                # (nothing). An owner/admin, however, already sees the canonical
                # catalog through connection management (the Add Connection
                # modal's "Discovered N tables", GET /connections/{id}/tables) —
                # hiding the same names here only breaks agent configuration, so
                # show them the full catalog. Query time stays fail-closed via
                # resolve_credentials.
                if not await self._admin_catalog_access(db, data_source, current_user):
                    overlay_table_ids = []

        def _scope(q):
            return q if overlay_table_ids is None else q.where(DataSourceTable.id.in_(overlay_table_ids))

        # Exclude file-source catalog rows from the Tables view: a file connection
        # (network_dir / s3 / SharePoint / OneDrive / Drive) is surfaced as Files,
        # NOT as selectable tables — its per-file catalog rows must not leak here.
        # Applied via a subquery on connection_table_id (no extra joins to clash
        # with the conditional joins below). NULL connection_table_id (legacy
        # tables) is kept.
        _FILE_SOURCE_TYPES = [
            "network_dir", "s3", "sharepoint", "onedrive", "google_drive",
            "outlook_mail", "gmail_mail",
        ]
        _file_ct_subq = None
        # Per-user file catalogs (OneDrive, Outlook, personal Drive) have no
        # shared ConnectionTable, so their DataSourceTable rows carry NO
        # connection link. The exclusion below keeps unlinked rows on purpose —
        # legacy name-keyed rows from the old save_or_update_tables path are
        # genuine tables — but that allowance also let every per-user FILE row
        # through, so a OneDrive agent listed its documents under "Tables"
        # (SharePoint, whose rows ARE linked, was correctly hidden).
        #
        # When every connection on this data source is a file source, nothing it
        # owns can be a table, so unlinked rows are excluded too. Mixed agents
        # keep the legacy allowance.
        _all_conns_are_file_sources = False
        if exclude_file_source_types:
            _conn_types = [
                (getattr(c, "type", None) or "") for c in (data_source.connections or [])
            ]
            _all_conns_are_file_sources = bool(_conn_types) and all(
                t in _FILE_SOURCE_TYPES for t in _conn_types
            )
            _file_ct_subq = (
                select(ConnectionTable.id)
                .join(Connection, ConnectionTable.connection_id == Connection.id)
                .where(Connection.type.in_(_FILE_SOURCE_TYPES))
            ).scalar_subquery()

        def _excl(q):
            if _file_ct_subq is None:
                return q
            # Local bind: a later `from sqlalchemy import or_` in this method makes
            # `or_` a function-local name, so reference it locally here.
            from sqlalchemy import or_ as _or
            if _all_conns_are_file_sources:
                # Explicit "must be linked, and not to a file connection".
                # Relying on `NULL NOT IN (subquery)` would be accidental: that
                # is NULL (row dropped) only while the subquery has rows, and
                # TRUE (row kept) when it is empty — so the behaviour would flip
                # depending on whether any file catalog existed elsewhere in the
                # org.
                return q.where(
                    DataSourceTable.connection_table_id.isnot(None),
                    DataSourceTable.connection_table_id.notin_(_file_ct_subq),
                )
            return q.where(_or(
                DataSourceTable.connection_table_id.is_(None),
                DataSourceTable.connection_table_id.notin_(_file_ct_subq),
            ))

        # Get total_tables count first (no filters - for display purposes)
        total_tables_result = await db.execute(
            _excl(_scope(select(func.count(DataSourceTable.id)).where(DataSourceTable.datasource_id == data_source_id)))
        )
        total_tables = total_tables_result.scalar() or 0

        # Build base query
        base_query = _excl(_scope(select(DataSourceTable).where(DataSourceTable.datasource_id == data_source_id)))
        count_query = _excl(_scope(select(func.count(DataSourceTable.id)).where(DataSourceTable.datasource_id == data_source_id)))
        
        # Apply selected_state filter (takes precedence over include_inactive)
        if selected_state == 'selected':
            base_query = base_query.where(DataSourceTable.is_active == True)
            count_query = count_query.where(DataSourceTable.is_active == True)
        elif selected_state == 'unselected':
            base_query = base_query.where(DataSourceTable.is_active == False)
            count_query = count_query.where(DataSourceTable.is_active == False)
        elif not include_inactive:
            # Only apply include_inactive if selected_state is not set
            base_query = base_query.where(DataSourceTable.is_active == True)
            count_query = count_query.where(DataSourceTable.is_active == True)
        
        # Helper for cross-database JSON schema extraction
        # SQLite uses json_extract, PostgreSQL uses ->> operator
        def get_schema_expr():
            bind = db.get_bind()
            dialect_name = bind.dialect.name if bind else "sqlite"
            if dialect_name == "postgresql":
                # PostgreSQL: use ->> operator for JSON text extraction
                return DataSourceTable.metadata_json.op('->>')('schema')
            else:
                # SQLite: use json_extract
                return func.json_extract(DataSourceTable.metadata_json, '$.schema')
        
        # Apply schema filter (from metadata_json->>'schema')
        # Supports prefixed format "connection_name:schema" for multi-connection
        if schema_filter and len(schema_filter) > 0:
            from sqlalchemy import or_
            schema_expr = get_schema_expr()
            # Check if any filter values use the "conn_name:schema" prefix format
            prefixed = [s for s in schema_filter if ':' in s]
            plain = [s for s in schema_filter if ':' not in s]

            schema_conditions = []
            if plain:
                schema_conditions.extend([schema_expr == s for s in plain])
            if prefixed:
                # Need to join connection to filter by both connection name and schema
                if not connection_filter:
                    # Only join if not already joined by connection_filter below
                    base_query = base_query.join(
                        ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id, isouter=False
                    ).join(
                        Connection, ConnectionTable.connection_id == Connection.id, isouter=False
                    )
                    count_query = count_query.join(
                        ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id, isouter=False
                    ).join(
                        Connection, ConnectionTable.connection_id == Connection.id, isouter=False
                    )
                for pf in prefixed:
                    conn_name, schema_name = pf.split(':', 1)
                    schema_conditions.append(
                        and_(Connection.name == conn_name, schema_expr == schema_name)
                    )
            if schema_conditions:
                base_query = base_query.where(or_(*schema_conditions))
                count_query = count_query.where(or_(*schema_conditions))

        # Apply connection filter (via connection_table -> connection relationship)
        if connection_filter and len(connection_filter) > 0:
            # Join with ConnectionTable to filter by connection_id
            base_query = base_query.join(
                ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id
            ).where(ConnectionTable.connection_id.in_(connection_filter))
            count_query = count_query.join(
                ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id
            ).where(ConnectionTable.connection_id.in_(connection_filter))

        # Apply search filter
        if search and search.strip():
            search_pattern = f"%{search.strip().lower()}%"
            base_query = base_query.where(func.lower(DataSourceTable.name).like(search_pattern))
            count_query = count_query.where(func.lower(DataSourceTable.name).like(search_pattern))
        
        # Get total count matching filter
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0
        
        # Get total selected count (across ALL tables, not just filtered)
        selected_count_result = await db.execute(
            _excl(select(func.count(DataSourceTable.id)).where(
                DataSourceTable.datasource_id == data_source_id,
                DataSourceTable.is_active == True
            ))
        )
        selected_count = selected_count_result.scalar() or 0

        # Get distinct connections for filter dropdown (exclude file sources when
        # they're hidden from the Tables view, so the dropdown matches the rows).
        connections_query = (
            select(Connection.id, Connection.name, Connection.type)
            .select_from(DataSourceTable)
            .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
            .join(Connection, ConnectionTable.connection_id == Connection.id)
            .where(DataSourceTable.datasource_id == data_source_id)
            .distinct()
        )
        if exclude_file_source_types:
            connections_query = connections_query.where(Connection.type.notin_(_FILE_SOURCE_TYPES))
        connections_result = await db.execute(connections_query)
        distinct_connections = [
            ConnectionInfo(id=str(row[0]), name=row[1], type=row[2])
            for row in connections_result.fetchall()
        ]
        has_multi_connection = len(distinct_connections) > 1

        # Get distinct schemas for filter dropdown (database-agnostic)
        # When multiple connections exist, prefix schema with connection name
        schema_expr = get_schema_expr()
        if has_multi_connection:
            schemas_result = await db.execute(
                select(schema_expr, Connection.name)
                .select_from(DataSourceTable)
                .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                .join(Connection, ConnectionTable.connection_id == Connection.id)
                .where(DataSourceTable.datasource_id == data_source_id)
                .where(schema_expr.isnot(None))
                .distinct()
            )
            distinct_schemas = [
                f"{row[1]}:{row[0]}" for row in schemas_result.fetchall() if row[0]
            ]
        else:
            schemas_result = await db.execute(
                select(func.distinct(schema_expr))
                .where(DataSourceTable.datasource_id == data_source_id)
                .where(schema_expr.isnot(None))
            )
            distinct_schemas = [row[0] for row in schemas_result.fetchall() if row[0]]

        # Apply sorting
        sort_column = DataSourceTable.name  # default
        if sort_by == "centrality_score":
            sort_column = DataSourceTable.centrality_score
        elif sort_by == "is_active":
            sort_column = DataSourceTable.is_active
        elif sort_by == "richness":
            sort_column = DataSourceTable.richness
        
        if sort_dir.lower() == "desc":
            base_query = base_query.order_by(sort_column.desc().nullslast())
        else:
            base_query = base_query.order_by(sort_column.asc().nullsfirst())
        
        # Apply pagination
        offset = (page - 1) * page_size
        base_query = base_query.offset(offset).limit(page_size)

        # Add selectinload for connection info
        base_query = base_query.options(
            selectinload(DataSourceTable.connection_table).selectinload(ConnectionTable.connection)
        )

        # Execute query
        tables_result = await db.execute(base_query)
        table_rows = tables_result.scalars().all()
        
        # Fetch stats if requested
        # Stats are matched by row id where the stats row records one, and only
        # fall back to the lowercased name where it doesn't. Name alone is not
        # an identity: a custom query named `album` and a source table named
        # `Album` are different relations that collided into one bucket, so the
        # new relation displayed the other one's usage count. The same applies
        # to two connections on one agent that both have an `orders`.
        stats_by_id = {}
        stats_by_name = {}
        if with_stats:
            from app.models.table_stats import TableStats
            stats_result = await db.execute(
                select(TableStats).where(
                    TableStats.report_id == None,
                    TableStats.data_source_id == data_source_id,
                )
            )
            for s in stats_result.scalars().all():
                if s.datasource_table_id:
                    stats_by_id[str(s.datasource_table_id)] = s
                else:
                    stats_by_name[(s.table_fqn or '').lower()] = s

        # Convert to schema objects
        tables = []
        for table in table_rows:
            # Get stats for this table
            stats = None
            if with_stats:
                stats = stats_by_id.get(str(table.id))
                if stats is None and str(table.id) not in stats_by_id:
                    # Legacy rows written before datasource_table_id existed.
                    # Ambiguous by construction, so only used when nothing
                    # better exists for this relation.
                    stats = stats_by_name.get((table.name or '').lower())

            # Extract connection info from relationship
            conn_id = None
            conn_name = None
            conn_type = None
            if table.connection_table and table.connection_table.connection:
                conn = table.connection_table.connection
                conn_id = str(conn.id)
                conn_name = conn.name
                conn_type = conn.type

            # Per-user connectors: show the caller's overlay is_active, not shared.
            _row_active = table.is_active
            if overlay_active_by_dstid is not None:
                _row_active = overlay_active_by_dstid.get(str(table.id), table.is_active)

            table_schema = DataSourceTableSchema(
                id=str(table.id),
                name=table.name,
                columns=table.columns or [],
                no_rows=table.no_rows or 0,
                datasource_id=str(table.datasource_id),
                pks=table.pks or [],
                fks=table.fks or [],
                is_active=_row_active,
                metadata_json=table.metadata_json,
                # Connection info
                connection_id=conn_id,
                connection_name=conn_name,
                connection_type=conn_type,
                # Metrics
                centrality_score=table.centrality_score,
                richness=table.richness,
                degree_in=table.degree_in,
                degree_out=table.degree_out,
                entity_like=table.entity_like,
                metrics_computed_at=table.metrics_computed_at.isoformat() if table.metrics_computed_at else None,
                # Stats fields
                usage_count=int(stats.usage_count or 0) if stats else None,
                success_count=int(stats.success_count or 0) if stats else None,
                failure_count=int(stats.failure_count or 0) if stats else None,
                pos_feedback_count=int(stats.pos_feedback_count or 0) if stats else None,
                neg_feedback_count=int(stats.neg_feedback_count or 0) if stats else None,
            )
            tables.append(table_schema)
        
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0
        
        return PaginatedTablesResponse(
            tables=tables,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            schemas=sorted(distinct_schemas),
            connections=distinct_connections,
            selected_count=selected_count,
            total_tables=total_tables,
            has_more=page < total_pages,
        )

    def _per_user_table_select_active(self, data_source, current_user) -> bool:
        """True when per-user table selection should apply to this write: the
        flag is on, it's a per-user connector, and there is a signed-in caller.
        Off → callers fall through to the shared-catalog path (unchanged)."""
        from app.settings.config import settings
        return bool(
            current_user is not None
            and getattr(settings, "hybrid_per_user_table_select", False)
            and is_per_user_connector(data_source)
        )

    async def _set_user_overlay_active(
        self, db, data_source, user, activate_names: List[str], deactivate_names: List[str]
    ) -> tuple[int, int]:
        """Flip is_active on the caller's own overlay rows. The FE keys tables by
        `id || name`, so each item is EITHER a canonical DataSourceTable id (UUID)
        or a table_name — match on whichever it looks like. Only touches rows for
        (data_source, this user); activation additionally requires the table be
        accessible (an inaccessible table can never become active). Returns
        (activated, deactivated) row counts. Caller commits."""
        from sqlalchemy import update as _update
        from app.models.user_data_source_overlay import UserDataSourceTable as _UDT
        import re as _re
        _uuid_re = _re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', _re.I)

        def _match_col(items):
            # UUIDs → match on data_source_table_id; else on table_name.
            use_ids = bool(items) and all(_uuid_re.match(str(i)) for i in items[:3])
            return (_UDT.data_source_table_id if use_ids else _UDT.table_name)

        a = d = 0
        if activate_names:
            res = await db.execute(
                _update(_UDT).where(
                    _UDT.data_source_id == str(data_source.id),
                    _UDT.user_id == str(user.id),
                    _UDT.is_accessible.is_(True),
                    _match_col(activate_names).in_([str(i) for i in activate_names]),
                ).values(is_active=True)
            )
            a = res.rowcount or 0
        if deactivate_names:
            res = await db.execute(
                _update(_UDT).where(
                    _UDT.data_source_id == str(data_source.id),
                    _UDT.user_id == str(user.id),
                    _match_col(deactivate_names).in_([str(i) for i in deactivate_names]),
                ).values(is_active=False)
            )
            d = res.rowcount or 0
        return a, d

    async def _user_overlay_active_count(self, db, data_source, user) -> int:
        """Count of the caller's currently-active accessible overlay tables."""
        from sqlalchemy import func as _func
        from app.models.user_data_source_overlay import UserDataSourceTable as _UDT
        return (await db.execute(
            select(_func.count(_UDT.id)).where(
                _UDT.data_source_id == str(data_source.id),
                _UDT.user_id == str(user.id),
                _UDT.is_accessible.is_(True),
                _UDT.is_active.is_(True),
            )
        )).scalar() or 0

    async def _user_overlay_names_matching(
        self, db, data_source, user, filter_params: dict
    ) -> List[str]:
        """Names of the caller's accessible overlay tables matching a bulk filter
        (search substring; schema prefix). Empty filter → all accessible."""
        from app.models.user_data_source_overlay import UserDataSourceTable as _UDT
        rows = (await db.execute(
            select(_UDT.table_name).where(
                _UDT.data_source_id == str(data_source.id),
                _UDT.user_id == str(user.id),
                _UDT.is_accessible.is_(True),
            )
        )).scalars().all()
        search = (filter_params.get("search") or "").strip().lower()
        schema_filter = filter_params.get("schema") or filter_params.get("schemas")
        if isinstance(schema_filter, str):
            schema_filter = [schema_filter]
        out = []
        for name in rows:
            if search and search not in name.lower():
                continue
            if schema_filter:
                # overlay table_name is fully-qualified (e.g. DB.schema.table);
                # match any requested schema token as a substring.
                if not any((s or "").lower() in name.lower() for s in schema_filter):
                    continue
            out.append(name)
        return out

    async def bulk_update_tables_status(
        self,
        db: AsyncSession,
        data_source_id: str,
        organization: Organization,
        action: str,
        filter_params: dict = None,
        current_user: User = None,
    ):
        """
        Bulk update is_active status for tables matching filter.
        action: "activate" or "deactivate"
        filter_params: {"schema": ["schema1", "schema2"], "search": "..."}
        """
        from sqlalchemy import update, func
        from app.schemas.datasource_table_schema import DeltaUpdateTablesResponse
        
        # Verify data source exists
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = result.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        
        if action not in ("activate", "deactivate"):
            raise HTTPException(status_code=400, detail="Action must be 'activate' or 'deactivate'")

        new_status = action == "activate"

        # Per-user connectors (Fabric + Power BI): Select-all / Deselect-all from a
        # signed-in member applies to THEIR overlay only. Resolve which of the
        # caller's accessible tables match the filter, then flip their is_active.
        if self._per_user_table_select_active(data_source, current_user):
            from app.schemas.datasource_table_schema import DeltaUpdateTablesResponse as _Resp
            names = await self._user_overlay_names_matching(
                db, data_source, current_user, filter_params or {}
            )
            if new_status:
                a, d = await self._set_user_overlay_active(db, data_source, current_user, activate_names=names, deactivate_names=[])
            else:
                a, d = await self._set_user_overlay_active(db, data_source, current_user, activate_names=[], deactivate_names=names)
            await db.commit()
            total = await self._user_overlay_active_count(db, data_source, current_user)
            return _Resp(activated_count=a, deactivated_count=d, total_selected=total)

        # Build update query with filters
        update_query = (
            update(DataSourceTable)
            .where(DataSourceTable.datasource_id == data_source_id)
        )
        
        filter_params = filter_params or {}
        
        # Apply schema filter (database-agnostic JSON extraction)
        # Supports prefixed "conn_name:schema" format for multi-connection
        schema_filter = filter_params.get("schema") or filter_params.get("schemas")
        if schema_filter:
            if isinstance(schema_filter, str):
                schema_filter = [schema_filter]
            if len(schema_filter) > 0:
                from sqlalchemy import or_, and_
                from app.models.connection_table import ConnectionTable
                from app.models.connection import Connection
                # Detect dialect for cross-database JSON extraction
                bind = db.get_bind()
                dialect_name = bind.dialect.name if bind else "sqlite"
                if dialect_name == "postgresql":
                    schema_expr = DataSourceTable.metadata_json.op('->>')('schema')
                else:
                    schema_expr = func.json_extract(DataSourceTable.metadata_json, '$.schema')

                prefixed = [s for s in schema_filter if ':' in s]
                plain = [s for s in schema_filter if ':' not in s]
                schema_conditions = [schema_expr == s for s in plain]

                if prefixed:
                    # Join to connection for prefixed schema filters
                    update_query = update_query.where(
                        DataSourceTable.id.in_(
                            select(DataSourceTable.id)
                            .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
                            .join(Connection, ConnectionTable.connection_id == Connection.id)
                            .where(
                                DataSourceTable.datasource_id == data_source_id,
                                or_(*[
                                    and_(Connection.name == pf.split(':', 1)[0], schema_expr == pf.split(':', 1)[1])
                                    for pf in prefixed
                                ] + schema_conditions)
                            )
                        )
                    )
                elif schema_conditions:
                    update_query = update_query.where(or_(*schema_conditions))
        
        # Apply connection filter
        connection_filter = filter_params.get("connection")
        if connection_filter:
            if isinstance(connection_filter, str):
                connection_filter = [connection_filter]
            if len(connection_filter) > 0:
                from app.models.connection_table import ConnectionTable as CT2
                update_query = update_query.where(
                    DataSourceTable.id.in_(
                        select(DataSourceTable.id)
                        .join(CT2, DataSourceTable.connection_table_id == CT2.id)
                        .where(
                            DataSourceTable.datasource_id == data_source_id,
                            CT2.connection_id.in_(connection_filter)
                        )
                    )
                )

        # Apply search filter
        search = filter_params.get("search")
        if search and search.strip():
            search_pattern = f"%{search.strip().lower()}%"
            update_query = update_query.where(func.lower(DataSourceTable.name).like(search_pattern))

        # Execute update
        update_query = update_query.values(is_active=new_status)
        result = await db.execute(update_query)
        await db.commit()
        
        affected_count = result.rowcount
        
        # Get new total selected count
        selected_count_result = await db.execute(
            select(func.count(DataSourceTable.id)).where(
                DataSourceTable.datasource_id == data_source_id,
                DataSourceTable.is_active == True
            )
        )
        total_selected = selected_count_result.scalar() or 0
        
        return DeltaUpdateTablesResponse(
            activated_count=affected_count if new_status else 0,
            deactivated_count=affected_count if not new_status else 0,
            total_selected=total_selected,
        )

    async def update_tables_status_delta(
        self,
        db: AsyncSession,
        data_source_id: str,
        organization: Organization,
        activate: List[str] = None,
        deactivate: List[str] = None,
        current_user: User = None,
    ):
        """
        Update table is_active status using delta (lists of table names to activate/deactivate).
        More efficient than sending all tables.
        """
        from sqlalchemy import update, func
        from app.schemas.datasource_table_schema import DeltaUpdateTablesResponse
        
        # Verify data source exists
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = result.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        
        activate = activate or []
        deactivate = deactivate or []

        # Per-user connectors (Fabric + Power BI): a delta from a signed-in member
        # targets THEIR overlay's is_active, never the shared catalog. Gated.
        if self._per_user_table_select_active(data_source, current_user):
            from app.schemas.datasource_table_schema import DeltaUpdateTablesResponse as _Resp
            a, d = await self._set_user_overlay_active(
                db, data_source, current_user, activate_names=activate, deactivate_names=deactivate
            )
            await db.commit()
            total = await self._user_overlay_active_count(db, data_source, current_user)
            return _Resp(activated_count=a, deactivated_count=d, total_selected=total)

        activated_count = 0
        deactivated_count = 0

        # Detect whether caller sent table IDs (UUIDs) or legacy table names.
        # IDs are unique per row; names may collide across connections.
        def _looks_like_ids(items: List[str]) -> bool:
            if not items:
                return False
            import re
            uuid_re = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
            return all(uuid_re.match(i) for i in items[:3])

        use_ids = _looks_like_ids(activate) or _looks_like_ids(deactivate)

        # Activate tables
        if activate:
            if use_ids:
                activate_result = await db.execute(
                    update(DataSourceTable)
                    .where(
                        DataSourceTable.datasource_id == data_source_id,
                        DataSourceTable.id.in_(activate)
                    )
                    .values(is_active=True)
                )
            else:
                activate_result = await db.execute(
                    update(DataSourceTable)
                    .where(
                        DataSourceTable.datasource_id == data_source_id,
                        DataSourceTable.name.in_(activate)
                    )
                    .values(is_active=True)
                )
            activated_count = activate_result.rowcount

        # Deactivate tables
        if deactivate:
            if use_ids:
                deactivate_result = await db.execute(
                    update(DataSourceTable)
                    .where(
                        DataSourceTable.datasource_id == data_source_id,
                        DataSourceTable.id.in_(deactivate)
                    )
                    .values(is_active=False)
                )
            else:
                deactivate_result = await db.execute(
                    update(DataSourceTable)
                    .where(
                        DataSourceTable.datasource_id == data_source_id,
                        DataSourceTable.name.in_(deactivate)
                    )
                    .values(is_active=False)
                )
            deactivated_count = deactivate_result.rowcount
        
        await db.commit()
        
        # Get new total selected count
        selected_count_result = await db.execute(
            select(func.count(DataSourceTable.id)).where(
                DataSourceTable.datasource_id == data_source_id,
                DataSourceTable.is_active == True
            )
        )
        total_selected = selected_count_result.scalar() or 0

        # A newly-activated table changes what the agent can use → re-measure.
        # Only fire on activation (newly-in-scope tables); deactivation only
        # shrinks scope and won't introduce new failures worth a loop.
        if activated_count:
            try:
                from app.services.agent_reliability_service import AgentReliabilityService
                from app.models.agent_automation_run import TRIGGER_TABLE_CHANGE
                AgentReliabilityService().schedule(
                    organization_id=str(organization.id),
                    data_source_id=str(data_source_id),
                    trigger=TRIGGER_TABLE_CHANGE,
                    changed_hint=f"{activated_count} table(s) activated",
                )
                # NOTE: intentionally NO "Connection schema changed" review/notification
                # here. Activating tables is a deliberate user action in the table
                # selector — not a backend schema change — so notifying about it is
                # noise. The schema-changed alert is reserved for genuine upstream
                # structural drift detected during a schema sync (see
                # sync_domain_tables, which emits emit_schema_changed on a real
                # table/column structure change).
            except Exception:
                logger.debug("update_tables_status_delta: reliability trigger skipped", exc_info=True)

        return DeltaUpdateTablesResponse(
            activated_count=activated_count,
            deactivated_count=deactivated_count,
            total_selected=total_selected,
        )

    async def read_user_data_source_schema(self, db: AsyncSession, data_source: DataSource, user: User):
        """Return the user's catalog from persisted UserOverlayTable rows.

        Cache-first — does NOT touch the live source. Use this for every
        read-shaped surface: /mentions, prompt builds, /full_schema, list
        renderers. Refreshes happen explicitly via `get_user_data_source_schema`
        (post-OAuth, manual refresh, OBO auto-provision).

        Empty list when the overlay hasn't been populated yet (first sign-in
        before the post-OAuth refresh completes). Callers can decide whether
        to trigger a fresh fetch or surface a "still loading" state.
        """
        from app.ai.prompt_formatters import Table, TableColumn
        from sqlalchemy.orm import selectinload
        from app.settings.config import settings

        # Per-user table selection (Fabric + Power BI): when the flag is on and
        # this is a per-user connector, the agent uses only the tables THIS user
        # marked active (is_active) among the ones their token can reach. Off, or
        # any shared connector → unchanged: every accessible table is used.
        _per_user_select = (
            getattr(settings, "hybrid_per_user_table_select", False)
            and is_per_user_connector(data_source)
        )
        _where = [
            UserOverlayTable.data_source_id == str(data_source.id),
            UserOverlayTable.user_id == str(user.id),
            UserOverlayTable.deleted_at.is_(None),
            UserOverlayTable.is_accessible.is_(True),
        ]
        if _per_user_select:
            _where.append(UserOverlayTable.is_active.is_(True))

        rows_q = await db.execute(
            select(UserOverlayTable)
            .options(selectinload(UserOverlayTable.data_source))
            .where(*_where)
        )
        overlay_rows = rows_q.scalars().all()
        if not overlay_rows:
            return []

        # Load all columns for these overlay rows in one query.
        col_q = await db.execute(
            select(UserOverlayColumn).where(
                UserOverlayColumn.user_data_source_table_id.in_([str(r.id) for r in overlay_rows]),
                UserOverlayColumn.is_accessible.is_(True),
            )
        )
        cols_by_table: dict[str, list] = {}
        for c in col_q.scalars().all():
            cols_by_table.setdefault(str(c.user_data_source_table_id), []).append(c)

        tables: list[Table] = []
        for row in overlay_rows:
            tables.append(Table(
                name=row.table_name,
                columns=[
                    TableColumn(name=c.column_name, dtype=c.data_type)
                    for c in cols_by_table.get(str(row.id), [])
                ],
                pks=[],
                fks=[],
                metadata_json=row.metadata_json,
            ))
        return tables

    def schedule_overview_relearn(self, data_source_id: str, user_id: str | None, organization_id: str) -> None:
        """Fire-and-forget background re-learn of the onboarding overview after a
        per-user sign-in sync (fabric_user/powerbi_user). Returns immediately so a
        sign-in is never blocked on the ~LLM overview regen. Never raises — a
        failed re-learn must not break the sync it followed.
        """
        import asyncio
        _ds_key = str(data_source_id)
        # Server-side dedup: if a re-learn for this DS is already pending, skip
        # scheduling a second one (the running one will regenerate on the latest
        # schema anyway). Cleared in _relearn_overview_bg's finally.
        if _ds_key in _RELEARN_INFLIGHT:
            logger.info("schedule_overview_relearn: re-learn already pending for ds=%s, skipping", _ds_key)
            return
        try:
            asyncio.get_running_loop().create_task(
                self._relearn_overview_bg(_ds_key, str(user_id) if user_id else None, str(organization_id))
            )
            _RELEARN_INFLIGHT.add(_ds_key)
        except RuntimeError:
            # No running loop (shouldn't happen inside a request) — skip; the
            # overview can still be regenerated via the /relearn route.
            logger.warning("schedule_overview_relearn: no running loop, skipping ds=%s", data_source_id)

    async def relearn_overview_now(self, data_source_id: str, user_id: str | None, organization_id: str) -> None:
        """Await the overview re-learn instead of firing it into the background.

        ★Exists so a caller that is ALREADY a background task can report the
        learn as a stage. `schedule_overview_relearn` returns immediately, which
        is right inside a request — but the per-user sign-in sync is itself a
        background task, so firing a second task from it only meant nothing
        could observe when the learn finished. Progress therefore had to be
        marked done before it started.

        Same body, same swallow-everything contract, same in-flight dedup — the
        only difference is that this one can be awaited. Never raises.
        """
        _ds_key = str(data_source_id)
        if _ds_key in _RELEARN_INFLIGHT:
            logger.info("relearn_overview_now: re-learn already pending for ds=%s, skipping", _ds_key)
            return
        _RELEARN_INFLIGHT.add(_ds_key)
        # _relearn_overview_bg clears the marker in its own finally, and swallows
        # every exception, so no try/finally is needed here.
        await self._relearn_overview_bg(
            _ds_key, str(user_id) if user_id else None, str(organization_id)
        )

    async def _relearn_overview_bg(self, data_source_id: str, user_id: str | None, organization_id: str) -> None:
        """Background body for schedule_overview_relearn — opens its OWN session
        (the request's is closed by now) and regenerates the overview on the real
        synced schema. Everything is swallowed."""
        from app.dependencies import async_session_maker
        from app.models.organization import Organization as _Org
        from app.models.user import User as _User
        try:
            async with async_session_maker() as _db:
                org = (await _db.execute(select(_Org).where(_Org.id == organization_id))).scalars().first()
                if org is None:
                    return
                usr = None
                if user_id:
                    usr = (await _db.execute(select(_User).where(_User.id == user_id))).scalars().first()
                await self.llm_sync(
                    db=_db, data_source_id=data_source_id, organization=org,
                    current_user=usr, force_llm=True,
                )
        except Exception as e:  # noqa: BLE001 — auto re-learn is best-effort
            logger.warning("auto re-learn after sync failed for ds=%s: %s", data_source_id, e)
        finally:
            # Clear the in-flight marker so a future upload can schedule again.
            _RELEARN_INFLIGHT.discard(str(data_source_id))

    async def get_user_data_source_schema(
        self,
        db: AsyncSession,
        data_source: DataSource,
        user: User,
        prefetched_tables: Optional[list] = None,
        progress_callback=None,
    ):
        """Fetch live schema with user creds, persist overlay rows, and return a user-scoped Table list.

        EXPENSIVE — hits the upstream source (Drive walk, SQL describe, etc.).
        Call only when a refresh is intended: post-OAuth, manual /refresh_schema,
        OBO auto-provision. Read-shaped surfaces should call
        `read_user_data_source_schema` instead.

        `prefetched_tables`, when not None, is a schema list already fetched
        with THIS user's credentials in the same request (e.g. by the shared
        catalog refresh that runs just before the overlay sync on a manual
        Reload). It skips the live re-fetch — on tabular OBO sources like
        Power BI that fetch is a full tenant crawl, and doing it twice per
        Reload doubled the wait.

        `progress_callback` is forwarded to the client's discovery (clients that
        accept it) so a background per-user sync can report where it is — and so
        the indexing runner's cancel check reaches inside the fetch.
        """
        # --- fabric_user: federated multi-endpoint sync (Phase 3) --------------
        # A single Fabric sign-in reaches MANY SQL endpoints (one per
        # lakehouse/warehouse across every workspace/tenant). Instead of the
        # single host/db the admin typed, auto-discover them all and merge every
        # endpoint's tables into one overlay. Falls back to the generic
        # single-client path on any failure (or when discovery finds nothing).
        # Runs BEFORE the prefetch shortcut: the federated merge is its own
        # multi-endpoint walk and must not be replaced by a single-connection
        # catalog.
        try:
            conn0 = data_source.connections[0] if data_source.connections else None
            if conn0 is not None and getattr(conn0, "type", None) == "fabric_user":
                merged = await self._merge_all_fabric_endpoints(db=db, data_source=data_source, user=user)
                if merged is not None:
                    return merged
        except Exception as e:  # noqa: BLE001 — never block on the federated path
            logger.warning("fabric_user federated sync failed, falling back to single client: %s", e)

        logger.info(
            f"get_user_data_source_schema: overlay sync for data source {data_source.id} "
            f"user {user.id} (reusing prefetched catalog: {prefetched_tables is not None})"
        )
        if prefetched_tables is not None:
            fresh = prefetched_tables
        else:
            # Live fetch with the user's own credentials. Hand the canonical
            # catalog to the client as `prior_tables` so catalog-crawling
            # sources (Power BI) only introspect datasets not already indexed:
            # the identity-scoped dataset listing alone determines which of the
            # known tables this user can see. Turns the post-sign-in overlay
            # sync from minutes into seconds on large tenants.
            prior_tables = None
            try:
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
            except Exception:
                prior_tables = None
            client = await self.construct_client(db=db, data_source=data_source, current_user=user)
            from app.data_sources.clients.base import _accepts_kwarg
            # Only pass what the client actually accepts, and only when there is
            # something to pass: a bare `aget_schemas(self)` — every stub client
            # in the test suite, and any custom client that overrides the base
            # wrapper — raises TypeError on an unexpected kwarg, which would fail
            # the sync and leave the user's overlay empty. Callers with no
            # callback (every path except the tracked background job) get exactly
            # the call they made before.
            kwargs = {}
            if prior_tables and _accepts_kwarg(client.aget_schemas, "prior_tables"):
                kwargs["prior_tables"] = prior_tables
            if progress_callback is not None and _accepts_kwarg(
                client.aget_schemas, "progress_callback"
            ):
                kwargs["progress_callback"] = progress_callback
            fresh = await client.aget_schemas(**kwargs)
        if not fresh:
            return []

        # Normalize
        from app.schemas.datasource_table_schema import normalize_indexed_columns as normalize_columns

        normalized: dict[str, dict] = {}
        for t in fresh:
            if isinstance(t, dict):
                name = t.get("name")
                if not name:
                    continue
                normalized[name] = {
                    "columns": normalize_columns(t.get("columns", [])),
                    "pks": normalize_columns(t.get("pks", [])),
                    "fks": t.get("fks", []) or [],
                    "metadata_json": t.get("metadata_json"),
                }
            else:
                name = getattr(t, "name", None)
                if not name:
                    continue
                normalized[name] = {
                    "columns": normalize_columns(getattr(t, "columns", [])),
                    "pks": normalize_columns(getattr(t, "pks", [])),
                    "fks": getattr(t, "fks", []) or [],
                    "metadata_json": getattr(t, "metadata_json", None),
                }

        # Persist overlays
        await self._upsert_user_overlay(db=db, data_source=data_source, user=user, normalized=normalized)

        # Build Table models compatible with prompt formatters
        from app.ai.prompt_formatters import Table, TableColumn, ForeignKey as PromptForeignKey
        tables: list[Table] = []
        for name, payload in normalized.items():
            columns = [TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("columns") or [])]
            pks = [TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("pks") or [])]
            fks = []
            for fk in (payload.get("fks") or []):
                try:
                    fks.append(
                        PromptForeignKey(
                            column=TableColumn(name=fk["column"]["name"], dtype=fk["column"].get("dtype")),
                            references_name=fk["references_name"],
                            references_column=TableColumn(name=fk["references_column"]["name"], dtype=fk["references_column"].get("dtype")),
                        )
                    )
                except Exception:
                    continue
            tables.append(Table(name=name, columns=columns, pks=pks, fks=fks, metadata_json=payload.get("metadata_json")))

        return tables

    # Fabric dataflow scratch endpoints — auto-generated staging, not user data.
    _FABRIC_STAGING_PREFIXES = (
        "staginglakehousefordataflows",
        "stagingwarehousefordataflows",
    )

    @staticmethod
    def _is_fabric_staging_db(db_name: str | None) -> bool:
        n = (db_name or "").strip().lower()
        return any(n.startswith(p) for p in DataSourceService._FABRIC_STAGING_PREFIXES)

    async def _build_fabric_federated_client(self, db: AsyncSession, data_source: DataSource, user: User):
        """Build a `MsFabricFederatedClient` from the user's stored refresh_token
        and the endpoints recorded in their overlay (`metadata_json['fabric']`).

        Returns None (→ generic single-client fallback) when there is no stored
        user token or no discovered endpoints in the overlay yet.
        """
        # Isolation guard (defense-in-depth): a per-user Fabric client must NEVER
        # be built without an explicit, concrete user. Every read below is keyed
        # to `user.id`, so a missing/blank user could only ever be a caller bug —
        # refuse loudly instead of risking building a client from the wrong (or
        # first-found) credential.
        if user is None or not getattr(user, "id", None):
            logger.warning("fabric federated client: refused — no explicit user (isolation guard)")
            return None

        # Stored refresh_token (per-user).
        row = (await db.execute(
            select(UserDataSourceCredentials).where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.is_active == True,  # noqa: E712
            ).order_by(
                UserDataSourceCredentials.is_primary.desc(),
                UserDataSourceCredentials.updated_at.desc(),
            )
        )).scalars().first()
        if row is None:
            return None
        stored = row.decrypt_credentials() or {}
        refresh_token = stored.get("refresh_token")
        if not (stored.get("auth_mode") == "user_login" and refresh_token):
            return None

        # Endpoints from the overlay's routing metadata (no network — the sync
        # already recorded host/database/tenant per table).
        from app.models.user_data_source_overlay import UserDataSourceTable
        rows = (await db.execute(
            select(UserDataSourceTable.metadata_json).where(
                UserDataSourceTable.data_source_id == str(data_source.id),
                UserDataSourceTable.user_id == str(user.id),
                UserDataSourceTable.is_accessible == True,  # noqa: E712
            )
        )).scalars().all()
        endpoints: dict[tuple, dict] = {}
        for meta in rows:
            fab = (meta or {}).get("fabric") if isinstance(meta, dict) else None
            if not fab:
                continue
            host, database = fab.get("host"), fab.get("database")
            if not (host and database):
                continue
            endpoints[(host, database)] = {
                "host": host,
                "database": database,
                "tenant_id": fab.get("tenant_id") or "organizations",
            }
        if not endpoints:
            return None

        # Audit: the client is built from THIS user's token + overlay only.
        logger.info(
            "fabric federated client built: user=%s ds=%s endpoints=%d (per-user isolated)",
            user.id, data_source.id, len(endpoints),
        )
        from app.data_sources.clients.ms_fabric_federated_client import MsFabricFederatedClient
        return MsFabricFederatedClient(endpoints=list(endpoints.values()), refresh_token=refresh_token)

    async def _merge_all_fabric_endpoints(self, db: AsyncSession, data_source: DataSource, user: User):
        """Federated Fabric sync: discover every reachable SQL endpoint from the
        user's ONE stored refresh_token, pull each endpoint's tables, and merge
        them into a single per-user overlay.

        Returns a `Table` list on success, or ``None`` to signal the caller to
        fall back to the generic single-client path (no stored token, discovery
        empty, or nothing ingested). Never raises for a per-endpoint failure —
        one bad endpoint is skipped, the rest still sync (fail-soft).

        Each merged table is:
          - keyed/renamed ``{database}.{schema}.{table}`` so tables with the same
            name in different lakehouses don't collide, and the agent can see
            which lakehouse a table lives in; and
          - stamped ``metadata_json['fabric'] = {host, database, tenant_id,
            workspace, table}`` — the routing key Phase 5 uses to send a query to
            the right endpoint.
        """
        conn = data_source.connections[0] if data_source.connections else None
        if conn is None:
            return None

        # 1) The user's stored refresh_token (per-user credential).
        row = (await db.execute(
            select(UserDataSourceCredentials).where(
                UserDataSourceCredentials.data_source_id == data_source.id,
                UserDataSourceCredentials.user_id == user.id,
                UserDataSourceCredentials.is_active == True,  # noqa: E712
            ).order_by(
                UserDataSourceCredentials.is_primary.desc(),
                UserDataSourceCredentials.updated_at.desc(),
            )
        )).scalars().first()
        if row is None:
            return None
        stored = row.decrypt_credentials() or {}
        refresh_token = stored.get("refresh_token")
        if not (stored.get("auth_mode") == "user_login" and refresh_token):
            return None

        # Progress registry — a no-op when no sync was started (a plain query
        # rebuilds this client too, and must not look like a sync).
        from app.services import connection_sync_progress as _prog
        _ds_id, _uid = str(data_source.id), str(user.id)

        # 2) Discover every endpoint (config-less; Phase 2).
        await _prog.update(_ds_id, _uid, phase="discovering")
        from app.services.fabric_discovery import discover_endpoints
        endpoints = await asyncio.to_thread(discover_endpoints, refresh_token)
        # Drop dataflow staging scratch DBs — never business data.
        endpoints = [e for e in endpoints if not self._is_fabric_staging_db(e.get("database"))]
        if not endpoints:
            return None

        # C.3 — the member's workspace selection, applied after discovery and
        # before any crawling. Discovery itself is one cheap call against the
        # Fabric API; the cost this removes is the per-endpoint SQL crawl below.
        from app.services.endpoint_selection import select_endpoints, unmatched_selection
        from app.services.user_scope_service import get_selected_endpoints

        selected = await get_selected_endpoints(db, _ds_id, _uid)
        missing = unmatched_selection(endpoints, selected)
        discovered_total = len(endpoints)
        endpoints = select_endpoints(endpoints, selected)
        if missing:
            # A renamed workspace, or access that was revoked. Without this the
            # member sees "0 of 3" and no reason — indistinguishable from a
            # sync that ran and found nothing.
            await _prog.update(
                _ds_id, _uid,
                error=(
                    "Selected but not found: " + ", ".join(missing) +
                    " — they may have been renamed, or your access changed. "
                    "Update your workspace selection."
                ),
            )
        if not endpoints:
            # ★An explicit empty selection is honoured, not silently widened to
            # "everything". Returning None here would fall through to the
            # generic single-client path; the caller needs a terminal, truthful
            # result instead.
            await _prog.set_endpoints(_ds_id, _uid, [])
            await _prog.finish(_ds_id, _uid, tables=0)
            logger.info(
                "fabric_user.selection_empty",
                extra={"data_source_id": _ds_id, "discovered": discovered_total},
            )
            return []

        # Publish the SELECTED workspace list up front, every entry pending. The
        # UI can then name what it is waiting for instead of showing a bare
        # count, and a member can check the list against the access they know
        # they have.
        await _prog.set_endpoints(_ds_id, _uid, endpoints)

        # 3) One SQL token per tenant (a database.windows.net token is
        #    tenant-scoped, not db-scoped → serves every endpoint in that tenant).
        from app.services.powerbi_device_code import refresh_to_access_token, SCOPE_FABRIC
        from app.data_sources.clients.ms_fabric_client import MsFabricClient

        tenant_tokens: dict[str, str] = {}
        latest_refresh = refresh_token

        def _sql_token_for(tenant_id: str) -> str | None:
            if tenant_id in tenant_tokens:
                return tenant_tokens[tenant_id]
            res = refresh_to_access_token(tenant_id, latest_refresh, SCOPE_FABRIC)
            if not res.get("ok") or not res.get("access_token"):
                logger.warning("fabric_user: SQL token mint failed for tenant %s: %s",
                               tenant_id, res.get("error"))
                tenant_tokens[tenant_id] = None
                return None
            tenant_tokens[tenant_id] = res["access_token"]
            return res["access_token"]

        # One lock per tenant. The crawl below runs endpoints concurrently, and
        # several endpoints usually share a tenant — without this they would all
        # miss the cache at once and mint the same token in parallel. Locking per
        # TENANT rather than globally keeps different tenants minting in
        # parallel. Locks are created up front so no two coroutines can race to
        # create the same lock.
        _token_locks: dict[str, asyncio.Lock] = {
            (ep.get("tenant_id") or "organizations"): asyncio.Lock() for ep in endpoints
        }

        async def _sql_token_for_async(tenant_id: str) -> str | None:
            async with _token_locks[tenant_id]:
                if tenant_id in tenant_tokens:
                    return tenant_tokens[tenant_id]
                return await asyncio.to_thread(_sql_token_for, tenant_id)

        # 4) Per endpoint: fetch schemas, namespace + stamp routing metadata.
        normalized: dict[str, dict] = {}
        ok_endpoints = 0
        # Count endpoints that FAILED to enumerate (bad metadata, token mint
        # failure, or schema-fetch error). Used by the post-merge stale sweep
        # below: only sweep when the sync fully enumerated every endpoint, so a
        # transient per-endpoint failure never mass-stales a user's tables.
        failed_endpoints = 0
        # (host, database) of every endpoint that ANSWERED — including one that
        # answered with no tables, which is a real "we looked and it's empty".
        # Only these are eligible to have their absent tables revoked.
        ok_scope: set = set()

        # Endpoints are crawled CONCURRENTLY. Each one is an independent ODBC
        # round trip to a different Fabric SQL endpoint, and the old serial loop
        # paid the full latency of every lakehouse in sequence — the dominant
        # cost of a sign-in on a multi-workspace tenant. Bounded so a user with
        # many lakehouses cannot open an unbounded number of ODBC connections at
        # once; the failure contract per endpoint is unchanged (log, count, skip).
        _FABRIC_CRAWL_CONCURRENCY = 6
        _sem = asyncio.Semaphore(min(_FABRIC_CRAWL_CONCURRENCY, max(1, len(endpoints))))
        _done_count = 0

        async def _crawl(ep: dict):
            """Fetch one endpoint. Returns (ep, fresh) or None if it failed.

            Never raises: a failed endpoint must not cancel its siblings.
            """
            nonlocal _done_count
            tenant_id = ep.get("tenant_id") or "organizations"
            host, database = ep.get("host"), ep.get("database")
            if not (host and database):
                return None
            async with _sem:
                token = await _sql_token_for_async(tenant_id)
                if not token:
                    # A tenant whose token could not be minted is a REPORTED
                    # failure, not a silent skip — otherwise the member sees a
                    # sync that "finished" without the workspace they wanted.
                    await _prog.endpoint_done(
                        _ds_id, _uid, database,
                        error="could not get a Microsoft token for this tenant",
                    )
                    return None
                try:
                    client = MsFabricClient(
                        server_hostname=host, database=database, access_token=token
                    )
                    fresh = await client.aget_schemas()
                except Exception as e:  # noqa: BLE001 — skip a bad endpoint, keep going
                    logger.warning("fabric_user: endpoint %s/%s schema fetch failed (soft): %s",
                                   database, host[:30], e)
                    await _prog.endpoint_done(_ds_id, _uid, database, error=str(e))
                    return None
            _done_count += 1
            await _prog.endpoint_done(
                _ds_id, _uid, database, tables=len(fresh or []),
            )
            return (ep, fresh)

        # return_exceptions: a bug in _crawl must degrade to "this endpoint
        # failed", never take down the whole sign-in.
        _crawl_started = time.monotonic()
        _results = await asyncio.gather(
            *(_crawl(ep) for ep in endpoints), return_exceptions=True
        )
        _crawl_seconds = time.monotonic() - _crawl_started

        # Merge SEQUENTIALLY in the original endpoint order. Two lakehouses can
        # expose the same `database.table` display name, and the serial loop let
        # the later endpoint win; preserving input order keeps that outcome
        # deterministic and identical to before.
        for ep, res in zip(endpoints, _results):
            if isinstance(res, BaseException):
                logger.warning("fabric_user: endpoint crawl raised (soft): %s", res)
                failed_endpoints += 1
                continue
            if res is None:
                failed_endpoints += 1
                continue
            _ep, fresh = res
            tenant_id = ep.get("tenant_id") or "organizations"
            host = ep.get("host")
            database = ep.get("database")
            ok_scope.add((host, database))
            if not fresh:
                continue
            ok_endpoints += 1
            for t in (fresh or []):
                tname = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
                if not tname:
                    continue
                cols = getattr(t, "columns", None)
                if cols is None and isinstance(t, dict):
                    cols = t.get("columns", [])
                base_meta = getattr(t, "metadata_json", None)
                if base_meta is None and isinstance(t, dict):
                    base_meta = t.get("metadata_json")
                meta = dict(base_meta) if isinstance(base_meta, dict) else {}
                # Routing key for Phase 5 — the ONLY authoritative source of the
                # real endpoint + real table name behind the namespaced display.
                meta["fabric"] = {
                    "host": host,
                    "database": database,
                    "tenant_id": tenant_id,
                    "workspace": ep.get("workspace_name"),
                    "table": tname,  # the endpoint-local schema.table
                }
                display = f"{database}.{tname}"
                normalized[display] = {
                    "columns": [
                        {"name": (c.name if hasattr(c, "name") else c.get("name")),
                         "dtype": (c.dtype if hasattr(c, "dtype") else c.get("dtype"))}
                        for c in (cols or [])
                    ],
                    "pks": [],
                    "fks": [],
                    "metadata_json": meta,
                }

        if not normalized:
            return None

        # 5) Merge everything into one overlay (single call — per-endpoint calls
        #    would revoke each other's rows; _upsert revokes anything absent).
        #    On a PARTIAL crawl the revoke is scoped to the endpoints that
        #    answered, so a lakehouse that timed out keeps its tables instead of
        #    disappearing from the user's agent (see _upsert_user_overlay).
        await self._upsert_user_overlay(
            db=db, data_source=data_source, user=user, normalized=normalized,
            revoke_scope=None if failed_endpoints == 0 else ok_scope,
        )

        # 5b) Stale-endpoint sweep -------------------------------------------
        # After a SUCCESSFUL, COMPLETE federated merge (every discovered endpoint
        # enumerated — failed_endpoints == 0), mark any of THIS user's overlay
        # rows whose table_name was not seen in this sync as status='stale',
        # is_accessible=false. _upsert_user_overlay already revokes not-seen rows;
        # this refines the label to 'stale' so a table that vanished because its
        # Fabric endpoint dropped out of discovery is distinguishable from a
        # permission revocation. Guarded on failed_endpoints == 0 so a transient
        # per-endpoint failure (partial sync) never mass-stales the user's tables.
        # Best-effort: never let the sweep break a sync.
        try:
            if failed_endpoints == 0:
                _seen_names = list(normalized.keys())
                from sqlalchemy import update as _sa_update
                _stale_stmt = (
                    _sa_update(UserOverlayTable)
                    .where(
                        UserOverlayTable.data_source_id == str(data_source.id),
                        UserOverlayTable.user_id == str(user.id),
                        UserOverlayTable.deleted_at.is_(None),
                        UserOverlayTable.table_name.notin_(_seen_names),
                        UserOverlayTable.status != "stale",
                    )
                    .values(is_accessible=False, status="stale")
                )
                await db.execute(_stale_stmt)
                # Cascade columns of newly-stale tables to inaccessible (optional,
                # keeps both layers consistent). Scoped to this user's stale tables.
                _stale_ids_subq = (
                    select(UserOverlayTable.id).where(
                        UserOverlayTable.data_source_id == str(data_source.id),
                        UserOverlayTable.user_id == str(user.id),
                        UserOverlayTable.status == "stale",
                    )
                )
                await db.execute(
                    _sa_update(UserOverlayColumn)
                    .where(
                        UserOverlayColumn.user_data_source_table_id.in_(_stale_ids_subq),
                        UserOverlayColumn.is_accessible == True,  # noqa: E712
                    )
                    .values(is_accessible=False)
                )
                await db.commit()
        except Exception as _stale_err:  # noqa: BLE001 — stale sweep is best-effort
            logger.warning(
                "fabric_user stale-endpoint sweep failed for ds=%s user=%s: %s",
                data_source.id, user.id, _stale_err,
            )

        await _prog.update(_ds_id, _uid, tables=len(normalized))
        logger.info(
            "fabric_user federated sync: %s table(s) from %s/%s endpoint(s), "
            "%s tenant(s), crawl %.1fs",
            len(normalized), ok_endpoints, len(endpoints), len(tenant_tokens),
            _crawl_seconds,
        )

        from app.ai.prompt_formatters import Table, TableColumn
        tables: list[Table] = []
        for name, payload in normalized.items():
            tables.append(Table(
                name=name,
                columns=[TableColumn(name=c["name"], dtype=c.get("dtype")) for c in (payload.get("columns") or [])],
                pks=[], fks=[], metadata_json=payload.get("metadata_json"),
            ))
        return tables

    @staticmethod
    def _row_in_revoke_scope(t_row, revoke_scope: set) -> bool:
        """Is this overlay row's source endpoint one we successfully read?

        The routing key written by `_merge_all_fabric_endpoints` is the only
        authoritative record of which endpoint a row came from. A row with no
        such key predates the federated sync (or came from another connector)
        and cannot be attributed — treat it as in scope so the historical
        revoke behaviour is preserved for it rather than leaving it to linger
        forever.
        """
        try:
            fab = (t_row.metadata_json or {}).get("fabric")
            if not isinstance(fab, dict):
                return True
            host, database = fab.get("host"), fab.get("database")
            if not host or not database:
                return True
            return (host, database) in revoke_scope
        except Exception:  # noqa: BLE001 — never let attribution break a sync
            return True

    async def _upsert_user_overlay(
        self,
        db: AsyncSession,
        data_source: DataSource,
        user: User,
        normalized: dict[str, dict],
        revoke_scope: Optional[set] = None,
    ):
        """Upsert per-user table/column overlay based on normalized schema.

        Tables/columns present in `normalized` are marked accessible. Any rows
        that existed before but are no longer returned are marked
        `is_accessible=False, status='revoked'` so consumers (LLM schema context,
        UI) stop surfacing them when the user loses permissions upstream. Rows
        are kept (not hard-deleted) so audit history survives across syncs.

        `revoke_scope` — absence of a table only means "revoked" if we actually
        LOOKED there. A Fabric sync crawls several SQL endpoints and skips any
        that errors (see `_merge_all_fabric_endpoints`), so on a partial sync an
        entire lakehouse is missing from `normalized` through no fault of the
        user's permissions. Revoking it would silently empty their agent until
        the next fully-clean sync. When a caller passes a set of
        `(host, database)` pairs it successfully enumerated, only prior rows
        belonging to those endpoints are eligible for revocation; rows from an
        endpoint that failed are left exactly as they were.

        None (the default, and every non-Fabric caller) keeps the original
        behaviour: anything not returned is revoked.
        """
        now = datetime.now(timezone.utc)
        # Load canonical mapping to link if present
        existing_q = await db.execute(select(DataSourceTable).where(DataSourceTable.datasource_id == data_source.id))
        existing_canonical = list(existing_q.scalars().all())
        canonical_by_name = {row.name: row for row in existing_canonical}

        def _dataset_table_key(meta) -> tuple | None:
            """Stable identity for a Power BI table independent of display name:
            (datasetId, tableName). Lets a user's row match an existing canonical
            row even if the dataset was renamed or two datasets share a name."""
            try:
                pbi = (meta or {}).get("powerbi") if isinstance(meta, dict) else None
                if pbi and pbi.get("datasetId") and pbi.get("tableName"):
                    return (str(pbi["datasetId"]), str(pbi["tableName"]))
            except Exception:
                pass
            return None

        canonical_by_dataset_table = {}
        for row in existing_canonical:
            k = _dataset_table_key(getattr(row, "metadata_json", None))
            if k is not None:
                canonical_by_dataset_table.setdefault(k, row)

        # Decide whether this connection's catalog should be UNIONED with the
        # user's own discovery (create canonical rows on demand from the user's
        # sync) rather than intersected with the admin/SP catalog:
        #
        #   - per_user catalogs (OneDrive, personal Drive): there is no admin
        #     sync at all — the canonical rows would never exist otherwise.
        #   - user_required (delegated/OBO) shared catalogs (Power BI, Fabric):
        #     a model the user's own token can see but the service principal
        #     cannot must still be selectable. Without the on-demand row the
        #     overlay link stays NULL and the table is filtered out of the
        #     selector — the reported "missing semantic models" bug.
        #
        # Either way one org-level canonical row per table is created (keyed by
        # dataset/table identity), so usage/instructions aggregate across users;
        # per-user visibility stays enforced by UserOverlayTable.
        union_user_discovery = False
        is_per_user_catalog = False
        try:
            from app.schemas.data_source_registry import get_entry
            conn = (data_source.connections or [None])[0]
            if conn is not None:
                is_per_user_catalog = get_entry(conn.type).catalog_ownership == "per_user"
                is_delegated = (conn.auth_policy or "system_only") == "user_required"
                union_user_discovery = is_per_user_catalog or is_delegated
        except Exception:
            pass
        if union_user_discovery:
            # per_user catalogs (OneDrive/Drive) auto-activate the user's fetched
            # files, as before. Delegated shared catalogs (Power BI/Fabric) create
            # the row inactive — the user selects it in the wizard, matching how
            # SP-discovered tables start.
            new_row_active = is_per_user_catalog
            for table_name, payload in normalized.items():
                meta = payload.get("metadata_json")
                dt_key = _dataset_table_key(meta)
                # Already have a canonical row for this table (by dataset/table
                # identity first, then by display name)? Reuse it — no dup.
                if dt_key is not None and dt_key in canonical_by_dataset_table:
                    existing_row = canonical_by_dataset_table[dt_key]
                    # A dataset/table rename keeps the same (datasetId, tableName)
                    # identity but a new display name. Refresh the display name so
                    # the selector (which renders DataSourceTable.name) shows the
                    # current name instead of the stale one — but only for
                    # user-discovered (unlinked) rows; an SP-linked row's name is
                    # owned by the service-principal crawl.
                    if (
                        existing_row.connection_table_id is None
                        and table_name
                        and existing_row.name != table_name
                    ):
                        existing_row.name = table_name
                        db.add(existing_row)
                    canonical_by_name.setdefault(table_name, existing_row)
                    continue
                if table_name in canonical_by_name:
                    continue
                # Tag provenance so the SP's background re-index (which prunes by
                # ConnectionTable membership) leaves this unlinked, user-sourced
                # row alone; it survives until no user can access it.
                row_meta = dict(meta) if isinstance(meta, dict) else {}
                row_meta.setdefault("discovered_by", "user")
                row = DataSourceTable(
                    # Client-side id: the overlay rows below reference it, so
                    # generating it here avoids a flush() per contributed table
                    # (a user granted thousands of tables the service account
                    # cannot see would otherwise pay thousands of round trips).
                    id=str(uuid.uuid4()),
                    datasource_id=str(data_source.id),
                    name=table_name,
                    columns=payload.get("columns") or [],
                    pks=payload.get("pks") or [],
                    fks=payload.get("fks") or [],
                    metadata_json=row_meta,
                    is_active=new_row_active,
                )
                db.add(row)
                canonical_by_name[table_name] = row
                if dt_key is not None:
                    canonical_by_dataset_table[dt_key] = row

        # Load all prior overlay rows for (data_source, user). We need them both to
        # update matches AND to detect tables that disappeared from the latest sync.
        all_prior_q = await db.execute(
            select(UserOverlayTable).where(
                UserOverlayTable.data_source_id == data_source.id,
                UserOverlayTable.user_id == user.id,
                UserOverlayTable.deleted_at.is_(None),
            )
        )
        prior_by_name = {row.table_name: row for row in all_prior_q.scalars().all()}
        new_table_names = set(normalized.keys())

        # Batch-load every prior column overlay in ONE pass instead of querying
        # per table inside the loop. On a 5k-table warehouse that per-table query
        # was ~2 000 of the ~2 060 statements this sync issued (measured: 1.33s of
        # 1.6s in-SQL). Chunked to stay under driver bind-parameter limits.
        cols_by_table: dict[str, dict[str, UserOverlayColumn]] = {}
        prior_ids = [str(r.id) for r in prior_by_name.values()]
        for i in range(0, len(prior_ids), 500):
            chunk = prior_ids[i:i + 500]
            chunk_q = await db.execute(
                select(UserOverlayColumn).where(
                    UserOverlayColumn.user_data_source_table_id.in_(chunk)
                )
            )
            for c in chunk_q.scalars().all():
                cols_by_table.setdefault(str(c.user_data_source_table_id), {})[c.column_name] = c

        # Two passes: every overlay TABLE row first, then the column rows that
        # reference them. The column rows carry the parent id as a plain FK
        # column (there is no `relationship()` between the two mappers), so the
        # unit of work has no dependency edge to order the two INSERT batches —
        # it falls back to sorting mappers by name, and
        # "UserDataSourceColumn" sorts before "UserDataSourceTable". On Postgres
        # that emitted the children first and the commit died with
        # `user_data_source_columns_user_data_source_table_id_fkey` violated
        # (SQLite doesn't enforce FKs by default, so it only ever failed on PG).
        # One flush between the passes fixes the order without giving back the
        # per-table round trips this loop was rewritten to avoid.
        rows_by_name: dict[str, UserOverlayTable] = {}
        for table_name, payload in normalized.items():
            t_row = prior_by_name.get(table_name)
            if t_row is None:
                t_row = UserOverlayTable(
                    # Assign the id up front: the column rows below need it as an
                    # FK, and generating it here removes a per-table `flush()`
                    # round trip (thousands of them on a large catalog).
                    id=str(uuid.uuid4()),
                    data_source_id=str(data_source.id),
                    user_id=str(user.id),
                    table_name=table_name,
                    data_source_table_id=str(canonical_by_name.get(table_name).id) if canonical_by_name.get(table_name) else None,
                    is_accessible=True,
                    status="accessible",
                    metadata_json=payload.get("metadata_json"),
                )
                db.add(t_row)
            else:
                t_row.metadata_json = payload.get("metadata_json")
                # (Re)link to the CURRENT canonical row for this name. Repairing a
                # STALE link matters as much as filling a missing one: when a
                # canonical DataSourceTable is dropped and later recreated (e.g. a
                # catalog prune followed by a re-index), the overlay keeps pointing
                # at the old id. Reads that scope by
                # `DataSourceTable.id IN (overlay ids)` then silently hide a table
                # the user can actually query, and re-syncing never healed it
                # because the link was non-NULL. Match on identity, not on
                # NULL-ness.
                canonical_row = canonical_by_name.get(table_name)
                if canonical_row is not None and str(t_row.data_source_table_id or "") != str(canonical_row.id):
                    t_row.data_source_table_id = str(canonical_row.id)
                # Re-grant access if this table had been marked revoked on a prior sync
                if not t_row.is_accessible or t_row.status != "accessible":
                    t_row.is_accessible = True
                    t_row.status = "accessible"
                db.add(t_row)
            rows_by_name[table_name] = t_row

        # Parents on disk before any child INSERT is emitted. This also lands the
        # user-discovered canonical `DataSourceTable` rows created above, which
        # the overlay's `data_source_table_id` points at.
        await db.flush()

        for table_name, payload in normalized.items():
            t_row = rows_by_name[table_name]
            # Upsert column overlays for this table (from the batch-loaded map;
            # a freshly created table has none).
            existing_cols = cols_by_table.get(str(t_row.id), {})
            new_col_names = set()
            for col in (payload.get("columns") or []):
                col_name = col.get("name")
                if not col_name:
                    continue
                new_col_names.add(col_name)
                c_row = existing_cols.get(col_name)
                if c_row is None:
                    c_row = UserOverlayColumn(
                        user_data_source_table_id=str(t_row.id),
                        column_name=col_name,
                        is_accessible=True,
                        is_masked=False,
                        data_type=col.get("dtype"),
                    )
                else:
                    c_row.data_type = col.get("dtype")
                    # Re-grant if previously revoked
                    if not c_row.is_accessible:
                        c_row.is_accessible = True
                db.add(c_row)
            # Revoke columns no longer returned for this table
            for existing_name, c_row in existing_cols.items():
                if existing_name not in new_col_names and c_row.is_accessible:
                    c_row.is_accessible = False
                    db.add(c_row)

        # Revoke tables that existed before but are no longer returned. The user
        # has lost access upstream (e.g., SQL GRANT revoked, PowerBI dataset
        # permission removed) and should stop seeing them in LLM context / UI.
        skipped_out_of_scope = 0
        for existing_name, t_row in prior_by_name.items():
            if existing_name in new_table_names:
                continue
            if revoke_scope is not None and not self._row_in_revoke_scope(t_row, revoke_scope):
                # This row's endpoint was not successfully enumerated in this
                # sync, so its absence proves nothing. Leave it alone.
                skipped_out_of_scope += 1
                continue
            if not t_row.is_accessible and t_row.status == "revoked":
                continue  # already revoked, no change
            t_row.is_accessible = False
            t_row.status = "revoked"
            db.add(t_row)
            # Cascade to columns so both layers reflect the revocation (served
            # from the same batch-loaded map — no per-table query).
            for c in cols_by_table.get(str(t_row.id), {}).values():
                if c.is_accessible:
                    c.is_accessible = False
                    db.add(c)

        if skipped_out_of_scope:
            logger.info(
                "partial sync for ds=%s user=%s: kept %d table(s) whose endpoint "
                "was not reachable this run (not revoked)",
                data_source.id, user.id, skipped_out_of_scope,
            )

        # AUTO-ACTIVATE (fabric_user / powerbi_user only) ----------------------
        # These per-user sign-in connectors have no admin "Select Tables" step —
        # a member signs in and expects to query immediately. For delegated
        # shared catalogs the canonical DataSourceTable rows are created inactive
        # (is_active=False, the union_user_discovery default above), so the UI
        # would show "0 active tables" until someone manually activated them. For
        # these two connector types ONLY, flip every just-synced table active so
        # the agent can use them right away. All other connector types keep the
        # manual-select behavior byte-for-byte.
        try:
            _conn0 = (data_source.connections or [None])[0]
            if (
                _conn0 is not None
                and getattr(_conn0, "type", None) in ("fabric_user", "powerbi_user")
                and new_table_names
            ):
                from sqlalchemy import update as _sa_update
                await db.execute(
                    _sa_update(DataSourceTable)
                    .where(
                        DataSourceTable.datasource_id == str(data_source.id),
                        DataSourceTable.name.in_(list(new_table_names)),
                        DataSourceTable.is_active == False,  # noqa: E712
                    )
                    .values(is_active=True)
                )
        except Exception as _e:  # noqa: BLE001 — activation is best-effort
            logger.warning("auto-activate tables failed for ds=%s: %s", data_source.id, _e)

        await db.commit()

    async def update_table_status_in_schema(self, db: AsyncSession, data_source_id: str, tables: list[DataSourceTableSchema], organization: Organization):
        data_source = await self.get_data_source(db=db, data_source_id=data_source_id, organization=organization)
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        
        for table in tables:
            table_object = await db.execute(select(DataSourceTable).filter(DataSourceTable.datasource_id == data_source_id, DataSourceTable.name == table.name))
            table_object = table_object.scalar_one_or_none()
            if table_object:
                table_object.is_active = table.is_active
                await db.commit()
                await db.refresh(table_object)
        
        return data_source
    
    # Maximum tables to set as active when auto-selecting
    MAX_ACTIVE_TABLES = 500
    
    # Onboarding: auto-select a focused set of tables
    ONBOARDING_MAX_TABLES = 0

    async def save_or_update_tables(self, db: AsyncSession, data_source: DataSource, organization: Organization = None, should_set_active: bool = True, current_user: User | None = None, force_all_active: bool = False):
        """Diff-based upsert of datasource tables.
        - Insert new tables
        - Update changed tables
        - Deactivate missing tables (keep history)
        - If should_set_active and > ONBOARDING_MAX_TABLES, auto-select top tables via SQL
        - If force_all_active=True, bypass smart selection and activate all tables (for demos)
        """
        from sqlalchemy import text, update
        import json as json_module
        
        try:
            fresh_tables = await self.get_data_source_fresh_schema(db=db, data_source_id=data_source.id, organization=organization, current_user=current_user)
            if not fresh_tables:
                return

            # Map incoming by name
            from app.schemas.datasource_table_schema import normalize_indexed_columns as normalize_columns

            incoming = {}
            for t in fresh_tables:
                if isinstance(t, dict):
                    name = t.get("name")
                    if not name:
                        continue
                    incoming[name] = {
                        "columns": normalize_columns(t.get("columns", [])),
                        "pks": normalize_columns(t.get("pks", [])),
                        "fks": t.get("fks", []),
                        "metadata_json": t.get("metadata_json")
                    }
                else:
                    name = getattr(t, "name", None)
                    if not name:
                        continue
                    incoming[name] = {
                        "columns": normalize_columns(getattr(t, "columns", [])),
                        "pks": normalize_columns(getattr(t, "pks", [])),
                        "fks": getattr(t, "fks", []) or [],
                        "metadata_json": getattr(t, "metadata_json", None)
                    }

            total_tables = len(incoming)
            # Skip smart selection if force_all_active (e.g., demo data sources)
            needs_smart_selection = should_set_active and total_tables > self.ONBOARDING_MAX_TABLES and not force_all_active

            # Load existing table names only (not full objects for efficiency)
            existing_q = await db.execute(
                select(DataSourceTable.id, DataSourceTable.name)
                .where(DataSourceTable.datasource_id == data_source.id)
            )
            existing_names = {row.name: row.id for row in existing_q.fetchall()}

            # Prepare bulk insert for new tables
            new_tables = []
            for name, payload in incoming.items():
                if name not in existing_names:
                    new_tables.append({
                        "name": name,
                        "columns": json_module.dumps(payload["columns"]),
                        "pks": json_module.dumps(payload["pks"]),
                        "fks": json_module.dumps(payload["fks"]),
                        "datasource_id": str(data_source.id),
                        "is_active": False if needs_smart_selection else bool(should_set_active),
                        "metadata_json": json_module.dumps(payload.get("metadata_json")) if payload.get("metadata_json") else None,
                        "no_rows": 0,
                    })

            # Bulk insert new tables using ORM (database-agnostic)
            if new_tables:
                for table_data in new_tables:
                    db.add(DataSourceTable(
                        name=table_data["name"],
                        columns=json_module.loads(table_data["columns"]),
                        pks=json_module.loads(table_data["pks"]),
                        fks=json_module.loads(table_data["fks"]),
                        datasource_id=table_data["datasource_id"],  # Already a string
                        is_active=table_data["is_active"],
                        metadata_json=json_module.loads(table_data["metadata_json"]) if table_data["metadata_json"] else None,
                        no_rows=table_data["no_rows"],
                    ))
                await db.commit()

            # Update existing tables with new column data
            for name, payload in incoming.items():
                if name in existing_names:
                    table_id = existing_names[name]
                    await db.execute(
                        update(DataSourceTable)
                        .where(DataSourceTable.id == table_id)
                        .values(
                            columns=payload["columns"],
                            pks=payload["pks"],
                            fks=payload["fks"],
                            metadata_json=payload.get("metadata_json"),
                        )
                    )
            
            # Deactivate tables that no longer exist in fresh schema
            missing_tables = set(existing_names.keys()) - set(incoming.keys())
            if missing_tables:
                for table_name in missing_tables:
                    table_id = existing_names[table_name]
                    await db.execute(
                        update(DataSourceTable)
                        .where(DataSourceTable.id == table_id)
                        .values(is_active=False)
                    )
            
            await db.commit()

            # If smart selection needed, use SQL to select top tables (onboarding limit)
            if needs_smart_selection:
                await self._select_active_tables_sql(db, str(data_source.id), self.ONBOARDING_MAX_TABLES)

        except Exception as e:
            print(f"Error saving tables: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save database tables: {e}")

        # Return full schema including inactive for downstream context
        schemas = await data_source.get_schemas(db=db, include_inactive=True)
        return schemas

    async def _select_active_tables_sql(self, db: AsyncSession, datasource_id: str, max_active: int):
        """
        Select top tables based on:
        1. Schema distribution (spread across schemas proportionally)
        2. Column count (tables with more columns ranked higher)
        
        Uses efficient SQL with dialect-specific functions for PostgreSQL/SQLite.
        """
        from sqlalchemy import text
        
        # Detect database dialect
        bind = db.get_bind()
        dialect_name = bind.dialect.name if bind else "sqlite"
        is_postgres = dialect_name == "postgresql"
        
        # First, deactivate all tables for this datasource
        await db.execute(
            text("UPDATE datasource_tables SET is_active = :false_val WHERE datasource_id = :ds_id"),
            {"ds_id": datasource_id, "false_val": False}
        )
        
        # Build dialect-specific SQL for table selection
        if is_postgres:
            # PostgreSQL syntax
            json_schema_extract = "COALESCE(metadata_json->>'schema', CASE WHEN position('.' in name) > 0 THEN split_part(name, '.', 1) ELSE '__default__' END)"
            json_array_len = "COALESCE(jsonb_array_length(columns::jsonb), 0)"
            greatest_expr = "GREATEST(1, CAST(ROUND(1.0 * table_count / total_tables * :max_active) AS INTEGER))"
        else:
            # SQLite syntax (no GREATEST function, use MAX or CASE)
            json_schema_extract = "COALESCE(json_extract(metadata_json, '$.schema'), CASE WHEN instr(name, '.') > 0 THEN substr(name, 1, instr(name, '.') - 1) ELSE '__default__' END)"
            json_array_len = "COALESCE(json_array_length(columns), 0)"
            greatest_expr = "MAX(1, CAST(ROUND(1.0 * table_count / total_tables * :max_active) AS INTEGER))"
        
        # SQL to select top tables with proportional schema distribution
        # Uses window functions (standard SQL) to rank tables within each schema
        select_sql = text(f"""
            WITH table_stats AS (
                SELECT 
                    id,
                    name,
                    {json_schema_extract} as schema_name,
                    {json_array_len} as col_count
                FROM datasource_tables
                WHERE datasource_id = :ds_id
            ),
            schema_counts AS (
                SELECT 
                    schema_name,
                    COUNT(*) as table_count,
                    SUM(COUNT(*)) OVER () as total_tables
                FROM table_stats
                GROUP BY schema_name
            ),
            schema_allocations AS (
                SELECT 
                    schema_name,
                    -- Proportional allocation with minimum of 1
                    {greatest_expr} as allocation
                FROM schema_counts
            ),
            ranked_tables AS (
                SELECT 
                    t.id,
                    t.name,
                    t.schema_name,
                    t.col_count,
                    ROW_NUMBER() OVER (PARTITION BY t.schema_name ORDER BY t.col_count DESC, t.name) as rank_in_schema,
                    a.allocation
                FROM table_stats t
                JOIN schema_allocations a ON t.schema_name = a.schema_name
            ),
            selected_by_schema AS (
                SELECT id, col_count, rank_in_schema
                FROM ranked_tables
                WHERE rank_in_schema <= allocation
            )
            SELECT id FROM selected_by_schema
            ORDER BY col_count DESC
            LIMIT :max_active
        """)
        
        result = await db.execute(select_sql, {"ds_id": datasource_id, "max_active": max_active})
        selected_ids = [row[0] for row in result.fetchall()]
        
        # Activate selected tables
        if selected_ids:
            # Build placeholders for IN clause
            placeholders = ", ".join([f":id{i}" for i in range(len(selected_ids))])
            params = {f"id{i}": id_val for i, id_val in enumerate(selected_ids)}
            params["ds_id"] = datasource_id
            params["true_val"] = True
            
            await db.execute(
                text(f"UPDATE datasource_tables SET is_active = :true_val WHERE datasource_id = :ds_id AND id IN ({placeholders})"),
                params
            )
        
        await db.commit()
        
    
    async def refresh_data_source_schema(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User):
        # Get the DataSource model instance with connections eagerly loaded
        result = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id)
        )
        data_source = result.scalar_one_or_none()

        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")

        # First, refresh ConnectionTable from the database (for all linked connections).
        # If a background indexing job is currently in flight for any connection,
        # await it first — both to avoid duplicate work and to ensure deterministic
        # state for the synchronous sync that follows.
        if data_source.connections:
            from app.services.connection_service import ConnectionService
            from app.services.connection_indexing_service import ConnectionIndexingService
            from app.schemas.data_source_registry import get_entry
            connection_service = ConnectionService()
            indexing_service = ConnectionIndexingService()
            logger.info(f"refresh_data_source_schema: Found {len(data_source.connections)} connections for data_source {data_source_id}")

            # Classify by catalog ownership, NOT by auth_policy. Any SHARED catalog
            # — system_only AND user_required (e.g. Fabric) — has an admin/system
            # catalog that should be refreshed via the connection's creds and synced
            # into DataSourceTable LINKED to ConnectionTable. Routing user_required
            # through this path (instead of the old save_or_update_tables fallback)
            # keeps exactly one canonical row per table and never creates name-keyed
            # orphans. Per-user catalogs (OneDrive, personal Drive) have no shared
            # catalog and are fetched per user below.
            # Tool providers (MCP / Custom API) carry no schema at all — their
            # catalog is a tool list, discovered by refresh_tools. Routing them
            # through refresh_schema below reached `McpClient.aget_schemas`,
            # which does not exist, so an agent-level Reload raised
            # AttributeError and the user's only recovery action did nothing.
            from app.schemas.data_source_registry import tool_provider_types
            _tool_types = tool_provider_types()

            shared_conns, per_user_conns, tool_conns = [], [], []
            for conn in (data_source.connections or []):
                if conn.type in _tool_types:
                    tool_conns.append(conn)
                    continue
                ownership = "shared"
                try:
                    ownership = get_entry(conn.type).catalog_ownership
                except Exception:
                    ownership = "shared"
                (per_user_conns if ownership == "per_user" else shared_conns).append(conn)

            # Discover with the CALLER's credentials: for a per-user OAuth
            # connector that is the only identity that has a token at all.
            for conn in tool_conns:
                try:
                    await connection_service.refresh_tools(
                        db=db, connection=conn, current_user=current_user
                    )
                except Exception as e:
                    logger.warning(
                        f"refresh_data_source_schema: tool refresh failed for connection {conn.id}: {e}"
                    )

            # Tool-only agent: there is no schema to return, and no legacy
            # fallback to fall through to (save_or_update_tables would land back
            # on the same missing aget_schemas).
            if tool_conns and not shared_conns and not per_user_conns:
                return []

            if shared_conns:
                # When every shared connection's refresh below runs with the
                # CALLER's own credentials, the fetched catalog is exactly what
                # the per-user overlay sync would re-fetch — collect it so the
                # overlay refresh can reuse it instead of crawling the source a
                # second time in the same request (on Power BI/Fabric OBO each
                # crawl is a full tenant walk; the duplicate doubled Reload time).
                caller_id = str(current_user.id) if current_user is not None else None
                caller_fetched_tables: list = []
                all_fetched_as_caller = caller_id is not None

                for conn in shared_conns:
                    # Wait for any active indexing run before refreshing synchronously.
                    try:
                        await indexing_service.wait_for_active(db, str(conn.id))
                    except TimeoutError as exc:
                        raise HTTPException(status_code=504, detail=str(exc)) from exc
                    logger.info(f"refresh_data_source_schema: refresh_schema for connection {conn.id} (auth_policy={conn.auth_policy})")
                    # Interactive reload: only introspect NEW datasets; known
                    # ones are rebuilt from the indexed catalog (column-level
                    # drift is picked up by scheduled/background reindexing,
                    # which runs with the default full introspection).
                    await connection_service.refresh_schema(
                        db=db, connection=conn, current_user=current_user,
                        introspection="incremental",
                    )
                    fetched = getattr(connection_service, "last_refresh_fresh_tables", None)
                    fetched_as = getattr(connection_service, "last_refresh_identity_user_id", None)
                    if fetched is not None and fetched_as is not None and fetched_as == caller_id:
                        caller_fetched_tables.extend(fetched)
                    else:
                        all_fetched_as_caller = False

                prefetched = caller_fetched_tables if all_fetched_as_caller else None

                # Sync ConnectionTable -> DataSourceTable (linked). Reconciles/heals
                # any legacy unlinked orphan rows; keep existing is_active state.
                for conn in shared_conns:
                    await self.sync_domain_tables_from_connection(
                        db, data_source, conn, max_auto_select=None
                    )
                if not per_user_conns:
                    user_scoped = await self._refresh_shared_user_overlay(
                        db, data_source, current_user, prefetched_tables=prefetched
                    )
                    if user_scoped is not None:
                        return user_scoped
                    schemas = await data_source.get_schemas(db=db, include_inactive=True)
                    return schemas

            # Per-user catalogs: fetch the caller's own catalog against their creds.
            if per_user_conns and current_user is not None:
                schemas = await self.get_user_data_source_schema(db=db, data_source=data_source, user=current_user)
                return schemas or []

            # Mixed (shared + per-user) already refreshed the shared side above.
            if shared_conns:
                user_scoped = await self._refresh_shared_user_overlay(
                    db, data_source, current_user, prefetched_tables=prefetched
                )
                if user_scoped is not None:
                    return user_scoped
                schemas = await data_source.get_schemas(db=db, include_inactive=True)
                return schemas

        # No connections at all: legacy direct fetch (nothing to link against).
        schemas = await self.save_or_update_tables(db=db, data_source=data_source, organization=organization, should_set_active=False, current_user=current_user)
        return schemas or []

    async def _refresh_shared_user_overlay(
        self,
        db: AsyncSession,
        data_source: DataSource,
        current_user: User,
        prefetched_tables: Optional[list] = None,
    ):
        """On an explicit reload of a SHARED-catalog, user_required (delegated/OBO,
        e.g. Fabric/PowerBI) source, also refresh the CALLER's per-user overlay.

        The shared-catalog refresh above only updates the canonical catalog
        (ConnectionTable -> DataSourceTable). But the tables selector is
        overlay-scoped for a caller running with their own delegated token
        (effective_auth == "user"), so without this the caller sees ZERO tables
        right after reloading and only sees them later, once an unrelated path
        (sign-in, OAuth connect, credential upsert) lazily warms the overlay.

        Returns the caller's user-scoped schema list when the overlay applies, or
        None to signal the caller should get the full canonical catalog (admin via
        service account, or a non-delegated source).
        """
        conns = getattr(data_source, "connections", None) or []
        auth_policy = (conns[0].auth_policy if conns else "system_only") or "system_only"
        if auth_policy != "user_required" or current_user is None:
            return None
        effective_auth = await self._resolve_effective_auth(db, data_source, current_user)
        if effective_auth == "user":
            # Caller runs with their own token: populate + return their overlay so
            # the reload reflects exactly the tables they can query.
            try:
                schemas = await self.get_user_data_source_schema(
                    db=db, data_source=data_source, user=current_user,
                    prefetched_tables=prefetched_tables,
                )
                return schemas or []
            except Exception as e:
                # Degrading to "no tables" is deliberate (a live fetch against the
                # user's token can fail for reasons we can't fix here), but stay
                # loud about it: a swallowed DB error here reads downstream as an
                # empty overlay, which is indistinguishable from "user sees
                # nothing" and cost real debugging time once already.
                logger.warning(
                    "Per-user overlay refresh failed for data source %s / user %s: %s",
                    data_source.id, getattr(current_user, "id", None), e, exc_info=True,
                )
                return []
        if effective_auth == "none":
            # No proven access (disconnected/expired) → nothing to show for a
            # plain member. Owner/admin get the canonical catalog (display
            # fallback, same rule as get_data_source_schema_paginated).
            if await self._admin_catalog_access(db, data_source, current_user):
                return None
            return []
        # effective_auth == "system": admin via service account → full catalog.
        return None

    async def get_metadata_resources(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User = None):
        result = await db.execute(select(DataSource).filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id))
        data_source = result.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        metadata_indexing_job = await db.execute(
            select(MetadataIndexingJob)
            .filter(
                MetadataIndexingJob.data_source_id == data_source_id,
                MetadataIndexingJob.status == IndexingJobStatus.COMPLETED.value,
                MetadataIndexingJob.is_active == True
            )
            .order_by(MetadataIndexingJob.created_at.desc())
            .limit(1)
        )
        metadata_indexing_job = metadata_indexing_job.scalar_one_or_none()
        
        if not metadata_indexing_job:
            raise HTTPException(status_code=404, detail="Metadata indexing job not found")
        
        resources = await db.execute(select(MetadataResource).filter(MetadataResource.data_source_id == data_source_id))
        resources = resources.scalars().all()
        
        # Import the schema
        from app.schemas.metadata_indexing_job_schema import MetadataIndexingJobSchema, JobStatus
        
        # Create a dict with all the job attributes
        job_data = {
            "id": metadata_indexing_job.id,
            "name": f"Indexing job for {data_source.name}",
            "description": f"Metadata indexing job for data source {data_source.name}",
            "job_type": "dbt",
            "status": JobStatus(metadata_indexing_job.status),
            "error_message": metadata_indexing_job.error_message,
            "resources_processed": metadata_indexing_job.processed_resources or 0,
            "resources_failed": 0,
            "started_at": metadata_indexing_job.started_at,
            "completed_at": metadata_indexing_job.completed_at,
            "data_source_id": metadata_indexing_job.data_source_id,
            "created_at": metadata_indexing_job.created_at,
            "updated_at": metadata_indexing_job.updated_at,
            "resources": [MetadataResourceSchema.from_orm(resource) for resource in resources],
            "config": {}
        }
        
        return MetadataIndexingJobSchema(**job_data)
    
    async def update_resources_status(self, db: AsyncSession, data_source_id: str, resources: list, organization: Organization, current_user: User = None):
        """Update the active status of DBT resources for a data source"""
        result = await db.execute(select(DataSource).filter(DataSource.id == data_source_id, DataSource.organization_id == organization.id))
        data_source = result.scalar_one_or_none()
        
        if not data_source:
            raise HTTPException(status_code=404, detail="Data source not found")
        
        for resource in resources:
            resource_object = await db.execute(
                select(MetadataResource).filter(
                    MetadataResource.id == resource.get('id'),
                    MetadataResource.data_source_id == data_source_id
                )
            )
            resource_object = resource_object.scalar_one_or_none()
            
            if resource_object:
                resource_object.is_active = resource.get('is_active', True)
                await db.commit()
                await db.refresh(resource_object)
        
        # Return updated resources
        resources = await db.execute(select(MetadataResource).filter(MetadataResource.data_source_id == data_source_id))
        resources = resources.scalars().all()

        # Get the metadata indexing job

        metadata_indexing_job = await self.get_metadata_resources(db=db, data_source_id=data_source_id, organization=organization, current_user=current_user)

        return metadata_indexing_job

    async def add_data_source_member(self, db: AsyncSession, data_source_id: str, member: DataSourceMembershipCreate, organization: Organization, current_user: User):
        """Add a user to data source membership.
        Writes to both DataSourceMembership (legacy) and ResourceGrant (RBAC).
        """
        # Get data source to verify it exists
        data_source = await self.get_data_source(db, data_source_id, organization)

        # Check if membership already exists (legacy table)
        existing = await db.execute(
            select(DataSourceMembership).filter(
                DataSourceMembership.data_source_id == data_source_id,
                DataSourceMembership.principal_type == member.principal_type,
                DataSourceMembership.principal_id == member.principal_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="User is already a member")

        # Create legacy membership
        membership = DataSourceMembership(
            data_source_id=data_source_id,
            principal_type=member.principal_type,
            principal_id=member.principal_id,
            config=member.config
        )
        db.add(membership)

        # Also create resource_grant (RBAC path)
        try:
            from app.models.resource_grant import ResourceGrant
            existing_grant = await db.execute(
                select(ResourceGrant).where(
                    ResourceGrant.resource_type == "data_source",
                    ResourceGrant.resource_id == data_source_id,
                    ResourceGrant.principal_type == member.principal_type,
                    ResourceGrant.principal_id == member.principal_id,
                    ResourceGrant.deleted_at.is_(None),
                )
            )
            if not existing_grant.scalar_one_or_none():
                grant = ResourceGrant(
                    organization_id=str(organization.id),
                    resource_type="data_source",
                    resource_id=data_source_id,
                    principal_type=member.principal_type,
                    principal_id=member.principal_id,
                    permissions=[],
                )
                db.add(grant)
        except Exception:
            pass  # Don't break if resource_grants table doesn't exist yet

        await db.commit()
        await db.refresh(membership)

        # Notify the newly added user (delayed; only if SMTP is configured).
        if member.principal_type == PRINCIPAL_TYPE_USER:
            try:
                from app.services.data_source_member_email import schedule_member_added_email
                schedule_member_added_email(
                    data_source_id=str(data_source_id),
                    user_id=str(member.principal_id),
                    added_by_user_id=str(current_user.id),
                    organization_id=str(organization.id),
                )
            except Exception as e:
                logger.warning("Could not schedule member-added email: %s", e)

        return DataSourceMembershipSchema.from_orm(membership)

    async def remove_data_source_member(self, db: AsyncSession, data_source_id: str, user_id: str, organization: Organization, current_user: User):
        """Remove a user from data source membership.
        Deletes from both DataSourceMembership (legacy) and ResourceGrant (RBAC).
        """
        # Get data source to verify it exists
        data_source = await self.get_data_source(db, data_source_id, organization)

        # Find and delete legacy membership
        result = await db.execute(
            select(DataSourceMembership).filter(
                DataSourceMembership.data_source_id == data_source_id,
                DataSourceMembership.principal_type == PRINCIPAL_TYPE_USER,
                DataSourceMembership.principal_id == user_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=404, detail="Membership not found")

        await db.delete(membership)

        # Also delete resource_grant (RBAC path)
        try:
            from app.models.resource_grant import ResourceGrant
            grant_result = await db.execute(
                select(ResourceGrant).where(
                    ResourceGrant.resource_type == "data_source",
                    ResourceGrant.resource_id == data_source_id,
                    ResourceGrant.principal_type == PRINCIPAL_TYPE_USER,
                    ResourceGrant.principal_id == user_id,
                    ResourceGrant.deleted_at.is_(None),
                )
            )
            grant = grant_result.scalar_one_or_none()
            if grant:
                await db.delete(grant)
        except Exception:
            pass  # Don't break if resource_grants table doesn't exist yet

        await db.commit()
        return {"message": "Member removed successfully"}

    async def get_data_source_members(self, db: AsyncSession, data_source_id: str, organization: Organization, current_user: User):
        """Get all members of a data source.
        Reads from resource_grants (RBAC) with fallback to DataSourceMembership (legacy).
        """
        # Get data source to verify it exists
        data_source = await self.get_data_source(db, data_source_id, organization, current_user)

        # Try RBAC path first (resource_grants)
        try:
            from app.models.resource_grant import ResourceGrant
            result = await db.execute(
                select(ResourceGrant).where(
                    ResourceGrant.resource_type == "data_source",
                    ResourceGrant.resource_id == data_source_id,
                    ResourceGrant.organization_id == str(organization.id),
                    ResourceGrant.deleted_at.is_(None),
                )
            )
            grants = result.scalars().all()
            if grants:
                # Resolve principal names
                user_ids = [g.principal_id for g in grants if g.principal_type == "user"]
                group_ids = [g.principal_id for g in grants if g.principal_type == "group"]
                role_ids = [g.principal_id for g in grants if g.principal_type == "role"]

                user_names = {}
                if user_ids:
                    from app.models.user import User
                    user_result = await db.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
                    for uid, name, email in user_result.all():
                        user_names[uid] = name or email or uid

                group_names = {}
                if group_ids:
                    from app.models.group import Group
                    group_result = await db.execute(select(Group.id, Group.name).where(Group.id.in_(group_ids)))
                    for gid, name in group_result.all():
                        group_names[gid] = name

                role_names = {}
                if role_ids:
                    from app.models.role import Role
                    role_result = await db.execute(select(Role.id, Role.name).where(Role.id.in_(role_ids)))
                    for rid, name in role_result.all():
                        role_names[rid] = name

                def _resolve_name(g):
                    if g.principal_type == "group":
                        return group_names.get(g.principal_id)
                    if g.principal_type == "role":
                        return role_names.get(g.principal_id)
                    return user_names.get(g.principal_id)

                return [
                    DataSourceMembershipSchema(
                        id=g.id,
                        data_source_id=data_source_id,
                        principal_type=g.principal_type,
                        principal_id=g.principal_id,
                        principal_name=_resolve_name(g),
                        permissions=g.permissions if isinstance(g.permissions, list) else [],
                        config=None,
                        created_at=g.created_at,
                        updated_at=g.updated_at,
                    )
                    for g in grants
                ]
        except Exception:
            pass  # Fall through to legacy path

        # Fallback: legacy DataSourceMembership
        result = await db.execute(
            select(DataSourceMembership).filter(
                DataSourceMembership.data_source_id == data_source_id
            )
        )
        data_source_memberships = result.scalars().all()
        return [DataSourceMembershipSchema.from_orm(m) for m in data_source_memberships]

    async def _get_prompt_schema(self, db: AsyncSession, data_source: DataSource, organization: Organization, current_user: User | None) -> str:
        """Resolve a prompt-ready schema string for this data source.
        - For system_only: use canonical via DataSource.prompt_schema
        - For user_required with user: use per-user overlay tables and TableFormatter
        """
        # User-required path uses per-user overlays — cache-first read, no
        # live walk on every prompt build.
        if getattr(data_source, "auth_policy", "system_only") == "user_required" and current_user is not None:
            tables = await self.read_user_data_source_schema(db=db, data_source=data_source, user=current_user)
            try:
                from app.ai.prompt_formatters import TableFormatter
                return TableFormatter(tables).table_str
            except Exception:
                # Fallback to no-stats canonical prompt schema
                return await data_source.prompt_schema(db=db, with_stats=False)
        # System path: canonical tables
        return await data_source.prompt_schema(db=db, with_stats=False)

    # ==================== Domain-Connection Architecture Methods ====================

    async def create_domain_with_connection(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        data_source_create: DataSourceCreate,
    ):
        """
        Create a DataSource (Domain) along with its Connection.
        This is the new architecture method that creates both in a single transaction.
        Maintains backward compatibility with existing create_data_source.
        """
        from app.services.connection_service import ConnectionService
        from app.models.connection import Connection
        from app.models.domain_connection import domain_connection
        
        connection_service = ConnectionService()
        data_source_dict = data_source_create.dict()
        
        # Extract connection-specific fields
        ds_type = data_source_dict.get("type")
        config = data_source_dict.pop("config", {})
        credentials = data_source_dict.pop("credentials", {})
        auth_policy = data_source_dict.get("auth_policy", "system_only")
        allowed_user_auth_modes = data_source_dict.pop("allowed_user_auth_modes", None)
        
        # Extract domain-specific fields
        name = data_source_dict.get("name")
        is_public = data_source_dict.get("is_public", False)
        member_user_ids = data_source_dict.pop("member_user_ids", [])
        generate_summary = data_source_dict.pop("generate_summary", False)
        generate_conversation_starters = data_source_dict.pop("generate_conversation_starters", False)
        generate_ai_rules = data_source_dict.pop("generate_ai_rules", False)
        use_llm_sync = data_source_dict.pop("use_llm_sync", False)
        
        # Create the Connection first
        connection = await connection_service.create_connection(
            db=db,
            organization=organization,
            current_user=current_user,
            name=name,
            type=ds_type,
            config=config,
            credentials=credentials,
            auth_policy=auth_policy,
            allowed_user_auth_modes=allowed_user_auth_modes,
        )
        
        # Create the DataSource (Domain) - connection fields are now on Connection model
        new_data_source = DataSource(
            name=name,
            organization_id=organization.id,
            is_public=is_public,
            use_llm_sync=use_llm_sync,
            owner_user_id=current_user.id,
        )
        
        db.add(new_data_source)
        await db.flush()
        
        # Link domain to connection via junction table
        await db.execute(
            domain_connection.insert().values(
                data_source_id=new_data_source.id,
                connection_id=connection.id
            )
        )
        
        await db.commit()
        await db.refresh(new_data_source)
        
        # Create memberships
        await self._create_memberships(db, new_data_source, [current_user.id], permissions=["manage"])
        if member_user_ids and not is_public:
            additional_user_ids = [uid for uid in member_user_ids if uid != current_user.id]
            if additional_user_ids:
                await self._create_memberships(db, new_data_source, additional_user_ids)
                # Notify each newly added member (delayed; only if SMTP configured).
                try:
                    from app.services.data_source_member_email import schedule_member_added_email
                    for uid in additional_user_ids:
                        schedule_member_added_email(
                            data_source_id=str(new_data_source.id),
                            user_id=str(uid),
                            added_by_user_id=str(current_user.id),
                            organization_id=str(organization.id),
                        )
                except Exception as e:
                    logger.warning("Could not schedule member-added emails on create: %s", e)

        # Sync domain tables from connection tables (onboarding: auto-select up to 20)
        await self.sync_domain_tables_from_connection(
            db, new_data_source, connection, 
            max_auto_select=self.ONBOARDING_MAX_TABLES
        )
        
        # Generate AI content if requested
        if auth_policy == "system_only":
            if generate_summary:
                response = await self.generate_data_source_items(db=db, item="summary", data_source_id=new_data_source.id, organization=organization, current_user=current_user)
                new_data_source.description = response.get("summary")
            if generate_conversation_starters:
                response = await self.generate_data_source_items(db=db, item="conversation_starters", data_source_id=new_data_source.id, organization=organization, current_user=current_user)
                new_data_source.conversation_starters = response.get("conversation_starters")
            await db.commit()
            await db.refresh(new_data_source)

        # user_required sources (OneDrive, GDrive, etc.) skip LLM summary gen
        # because their schema is per-user — so the description stays empty.
        # Fall back to the registry entry's static description so the field
        # isn't blank in lists/cards.
        if not new_data_source.description:
            try:
                from app.schemas.data_source_registry import get_entry
                entry = get_entry(ds_type)
                if entry and entry.description:
                    new_data_source.description = entry.description
                    await db.commit()
                    await db.refresh(new_data_source)
            except Exception:
                pass
        
        # Reload with relationships
        stmt = (
            select(DataSource)
            .options(
                selectinload(DataSource.data_source_memberships),
                selectinload(DataSource.connections)
            )
            .where(DataSource.id == new_data_source.id)
        )
        result = await db.execute(stmt)
        return result.scalar_one()

    async def add_connection_to_domain(
        self,
        db: AsyncSession,
        data_source_id: str,
        connection_id: str,
        organization: Organization,
        current_user: User,
        sync_tables: bool = True,
    ):
        """
        Add a connection to an existing domain (M:N relationship).
        """
        from app.models.connection import Connection
        from app.models.domain_connection import domain_connection
        
        # Verify domain exists
        data_source = await db.execute(
            select(DataSource).filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = data_source.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Verify connection exists
        connection = await db.execute(
            select(Connection).filter(
                Connection.id == connection_id,
                Connection.organization_id == organization.id
            )
        )
        connection = connection.scalar_one_or_none()
        if not connection:
            raise HTTPException(status_code=404, detail="Connection not found")
        
        # Check if already linked
        existing = await db.execute(
            domain_connection.select().where(
                domain_connection.c.data_source_id == data_source_id,
                domain_connection.c.connection_id == connection_id
            )
        )
        if existing.first():
            raise HTTPException(status_code=400, detail="Connection already linked to this agent")
        
        # Create link
        await db.execute(
            domain_connection.insert().values(
                data_source_id=data_source_id,
                connection_id=connection_id
            )
        )
        await db.commit()
        
        # Sync domain tables from this connection (no auto-select for existing domains)
        if sync_tables:
            await self.sync_domain_tables_from_connection(
                db, data_source, connection,
                max_auto_select=None  # User must manually select tables
            )
        
        return {"message": "Connection added to agent"}

    async def remove_connection_from_domain(
        self,
        db: AsyncSession,
        data_source_id: str,
        connection_id: str,
        organization: Organization,
        current_user: User,
    ):
        """
        Remove a connection from an agent.
        """
        from app.models.domain_connection import domain_connection
        
        # Verify domain exists
        data_source = await db.execute(
            select(DataSource).options(selectinload(DataSource.connections)).filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = data_source.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Check if this is the last connection
        if len(data_source.connections) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last connection from an agent")
        
        # Remove link
        await db.execute(
            domain_connection.delete().where(
                domain_connection.c.data_source_id == data_source_id,
                domain_connection.c.connection_id == connection_id
            )
        )
        
        # Remove domain tables that reference this connection's tables
        from app.models.connection_table import ConnectionTable
        await db.execute(
            delete(DataSourceTable).where(
                DataSourceTable.datasource_id == data_source_id,
                DataSourceTable.connection_table_id.in_(
                    select(ConnectionTable.id).where(ConnectionTable.connection_id == connection_id)
                )
            )
        )
        
        await db.commit()
        return {"message": "Connection removed from agent"}

    async def sync_domain_tables_from_connection(
        self,
        db: AsyncSession,
        data_source: DataSource,
        connection,
        max_auto_select: int | None = None,
    ):
        """
        Create DataSourceTable (DomainTable) entries from ConnectionTable entries.
        Links domain tables to connection tables for schema access.
        
        Args:
            max_auto_select: Maximum tables to auto-select.
                - None: No auto-selection, all tables start inactive (for new domains from existing connections)
                - int: Auto-select up to this many tables (for onboarding, use ONBOARDING_MAX_TABLES=20)
        """
        from app.models.connection_table import ConnectionTable

        # Get connection tables - ensure connection_id is string
        connection_id_str = str(connection.id)
        conn_tables = await db.execute(
            select(ConnectionTable).filter(ConnectionTable.connection_id == connection_id_str)
        )
        conn_tables = conn_tables.scalars().all()

        logger.info(f"sync_domain_tables_from_connection: Found {len(conn_tables)} ConnectionTable records for connection {connection_id_str}")

        if not conn_tables:
            logger.warning(f"sync_domain_tables_from_connection: No ConnectionTable records found, cannot sync")
            return
        
        # Get existing domain tables keyed by connection_table_id
        # This allows the same table name from different connections to coexist
        existing = await db.execute(
            select(DataSourceTable).filter(DataSourceTable.datasource_id == data_source.id)
        )
        existing_rows = existing.scalars().all()
        existing_by_conn_table_id = {t.connection_table_id: t for t in existing_rows if t.connection_table_id}

        # Snapshot the per-table column structure BEFORE we overwrite it below,
        # so we can detect a real structural change (added/removed column or a
        # dtype change) after the refresh and trigger the agent-reliability
        # loop. Keyed by table name; value is a stable signature of the columns.
        # Captured for active tables only — the agent only "uses" active tables,
        # so an inactive table's schema drift isn't worth a re-eval.
        pre_schema_sig = {
            t.name: self._column_signature(t.columns)
            for t in existing_rows
            if getattr(t, "is_active", False)
        }
        # Unlinked rows (connection_table_id is NULL) keyed by name. These are
        # legacy name-keyed rows from the old save_or_update_tables path. We adopt
        # them below instead of creating a duplicate linked row, which both
        # prevents new duplicates and heals existing orphans on the next sync.
        unlinked_by_name: dict[str, list] = {}
        for t in existing_rows:
            if not t.connection_table_id:
                unlinked_by_name.setdefault(t.name, []).append(t)

        total_tables = len(conn_tables)

        # Determine initial activation:
        # - If max_auto_select is None: all tables start inactive (user must select)
        # - If max_auto_select is set and total <= limit: activate all
        # - If max_auto_select is set and total > limit: start inactive, then smart-select
        if max_auto_select is None:
            should_activate = False
            needs_smart_selection = False
        else:
            should_activate = total_tables <= max_auto_select
            needs_smart_selection = total_tables > max_auto_select

        for conn_table in conn_tables:
            if conn_table.id in existing_by_conn_table_id:
                # Update existing - refresh schema data (preserves is_active)
                domain_table = existing_by_conn_table_id[conn_table.id]
                domain_table.columns = conn_table.columns
                domain_table.pks = conn_table.pks
                domain_table.fks = conn_table.fks
                domain_table.no_rows = conn_table.no_rows
                domain_table.metadata_json = conn_table.metadata_json
            else:
                # Reconcile first: if a legacy unlinked (connection_table_id=NULL)
                # row of the same name exists, ADOPT it — link it to this conn_table
                # rather than inserting a duplicate. Preserves its is_active (it may
                # be the row users currently see/select) and its per-user overlay
                # links (UserDataSourceTable.data_source_table_id points at it).
                pool = unlinked_by_name.get(conn_table.name)
                if pool:
                    domain_table = pool.pop(0)
                    domain_table.connection_table_id = conn_table.id
                    domain_table.columns = conn_table.columns
                    domain_table.pks = conn_table.pks
                    domain_table.fks = conn_table.fks
                    domain_table.no_rows = conn_table.no_rows
                    domain_table.metadata_json = conn_table.metadata_json
                    domain_table.centrality_score = conn_table.centrality_score
                    domain_table.richness = conn_table.richness
                    domain_table.degree_in = conn_table.degree_in
                    domain_table.degree_out = conn_table.degree_out
                    domain_table.entity_like = conn_table.entity_like
                    domain_table.metrics_computed_at = conn_table.metrics_computed_at
                    db.add(domain_table)
                else:
                    # Create new domain table linked to connection table
                    domain_table = DataSourceTable(
                        name=conn_table.name,
                        datasource_id=data_source.id,
                        connection_table_id=conn_table.id,
                        # A BOW custom query always starts inactive on a new
                        # agent: it is an admin's curated relation for a specific
                        # purpose, not part of the source catalog the auto-select
                        # rule is reasoning about, and enabling it silently would
                        # widen what a brand-new agent can query.
                        is_active=(
                            False
                            if getattr(conn_table, "kind", None) == "bow"
                            else should_activate
                        ),
                        # Copy legacy fields for backward compatibility
                        columns=conn_table.columns,
                        pks=conn_table.pks,
                        fks=conn_table.fks,
                        no_rows=conn_table.no_rows,
                        metadata_json=conn_table.metadata_json,
                        centrality_score=conn_table.centrality_score,
                        richness=conn_table.richness,
                        degree_in=conn_table.degree_in,
                        degree_out=conn_table.degree_out,
                        entity_like=conn_table.entity_like,
                        metrics_computed_at=conn_table.metrics_computed_at,
                    )
                    db.add(domain_table)

        # Heal pre-existing duplicates: when a name already had BOTH a linked row
        # (matched by connection_table_id above) AND a leftover unlinked orphan,
        # the conn_table matched the linked row so the orphan was never adopted.
        # Re-point any per-user overlays from the orphan to the linked row, carry
        # over the orphan's active state, then delete the orphan — leaving exactly
        # one canonical row per (data_source, name) for this connection.
        await db.flush()
        from sqlalchemy import update as _sql_update, delete as _sql_delete
        from app.models.user_data_source_overlay import UserDataSourceTable as _UDT
        from app.models.table_stats import TableStats as _TStats
        from app.models.table_usage_event import TableUsageEvent as _TUsage
        from app.models.table_feedback_event import TableFeedbackEvent as _TFeedback
        this_conn_linked = (await db.execute(
            select(DataSourceTable)
            .join(ConnectionTable, DataSourceTable.connection_table_id == ConnectionTable.id)
            .where(
                DataSourceTable.datasource_id == data_source.id,
                ConnectionTable.connection_id == connection_id_str,
            )
        )).scalars().all()
        linked_by_name = {}
        for t in this_conn_linked:
            linked_by_name.setdefault(t.name, t)
        if linked_by_name:
            orphan_rows = (await db.execute(
                select(DataSourceTable).where(
                    DataSourceTable.datasource_id == data_source.id,
                    DataSourceTable.connection_table_id.is_(None),
                    DataSourceTable.name.in_(list(linked_by_name.keys())),
                )
            )).scalars().all()
            for orphan in orphan_rows:
                target = linked_by_name.get(orphan.name)
                if target is None or str(target.id) == str(orphan.id):
                    continue
                oid, tid = str(orphan.id), str(target.id)
                # Re-point everything that referenced the orphan onto the canonical
                # linked row before deleting it (these FKs have no ON DELETE rule,
                # so a bare delete would FK-violate). Stats uniqueness is by
                # table_fqn/scope, not table_id, so re-pointing is safe.
                await db.execute(_sql_update(_UDT).where(_UDT.data_source_table_id == oid).values(data_source_table_id=tid))
                await db.execute(_sql_update(_TStats).where(_TStats.datasource_table_id == oid).values(datasource_table_id=tid))
                await db.execute(_sql_update(_TUsage).where(_TUsage.datasource_table_id == oid).values(datasource_table_id=tid))
                await db.execute(_sql_update(_TFeedback).where(_TFeedback.datasource_table_id == oid).values(datasource_table_id=tid))
                if orphan.is_active and not target.is_active:
                    target.is_active = True
                    db.add(target)
                await db.execute(_sql_delete(DataSourceTable).where(DataSourceTable.id == oid))

        # Deactivate domain tables that no longer exist in the connection
        # (table was deleted from the database)
        # IMPORTANT: Only check tables that belong to THIS connection, not all domain tables
        conn_table_ids = {t.id for t in conn_tables}

        # Domain tables linked to THIS connection. `this_conn_linked` above ran
        # exactly this query a few lines earlier and nothing between them inserts
        # DataSourceTable rows (the orphan heal only re-points and deletes), so
        # reuse it instead of re-materializing the whole set — on a 5k-table
        # source that second pass was pure ORM overhead.
        existing_for_this_conn = this_conn_linked

        missing_tables = [t for t in existing_for_this_conn if t.connection_table_id not in conn_table_ids]
        if missing_tables:
            from sqlalchemy import update
            for domain_table in missing_tables:
                await db.execute(
                    update(DataSourceTable)
                    .where(DataSourceTable.id == domain_table.id)
                    .values(is_active=False)
                )

        await db.commit()

        # AUTO-ACTIVATE (csv file-agents only) --------------------------------
        # File agents (type='csv' — uploaded CSV/Excel) have no admin "Select
        # Tables" step in the normal upload path: a member uploads files and
        # expects every reflected table to be queryable immediately. This reflect
        # path is called with max_auto_select=None, so new rows are created
        # is_active=False and the wizard's Review step showed "0/1 active". For
        # csv connections ONLY, flip every just-synced table active. All other
        # connector types keep the manual-select behavior byte-for-byte (guarded
        # by the connection.type == 'csv' check). Mirrors the pt3 fabric/powerbi
        # auto-activate pattern: UPDATE by synced-name list, best-effort.
        try:
            if getattr(connection, "type", None) == "csv":
                _synced_names = [t.name for t in conn_tables if getattr(t, "name", None)]
                if _synced_names:
                    from sqlalchemy import update as _sa_update
                    await db.execute(
                        _sa_update(DataSourceTable)
                        .where(
                            DataSourceTable.datasource_id == data_source.id,
                            DataSourceTable.name.in_(_synced_names),
                            DataSourceTable.is_active == False,  # noqa: E712
                        )
                        .values(is_active=True)
                    )
                    await db.commit()
        except Exception as _act_err:  # noqa: BLE001 — activation is best-effort
            logger.warning(
                "auto-activate csv tables failed for ds=%s: %s", data_source.id, _act_err
            )

        # If too many tables for auto-select, use smart selection algorithm
        if needs_smart_selection and max_auto_select:
            await self._select_active_tables_sql(db, str(data_source.id), max_auto_select)

        # Detect a structural change vs. the pre-refresh snapshot and fire the
        # agent-reliability loop (debounced to one schedule per sync). Skipped on
        # the initial population (empty pre-snapshot) so onboarding doesn't storm
        # the loop. Best-effort: never let trigger wiring break a schema sync.
        try:
            post_rows = (await db.execute(
                select(DataSourceTable).where(
                    DataSourceTable.datasource_id == data_source.id,
                    DataSourceTable.is_active == True,  # noqa: E712
                )
            )).scalars().all()
            post_schema_sig = {t.name: self._column_signature(t.columns) for t in post_rows}
            if pre_schema_sig:
                changed = sorted({
                    name for name in (set(pre_schema_sig) | set(post_schema_sig))
                    if pre_schema_sig.get(name) != post_schema_sig.get(name)
                })
                if changed:
                    from app.services.agent_reliability_service import AgentReliabilityService
                    from app.models.agent_automation_run import TRIGGER_TABLE_CHANGE
                    hint = "Table/column structure changed: " + ", ".join(changed[:8])
                    AgentReliabilityService().schedule(
                        organization_id=str(data_source.organization_id),
                        data_source_id=str(data_source.id),
                        trigger=TRIGGER_TABLE_CHANGE,
                        changed_hint=hint,
                    )
                    from app.services.review_producers import emit_schema_changed
                    await emit_schema_changed(
                        db, str(data_source.organization_id), str(data_source.id), summary=hint,
                    )
        except Exception:
            logger.debug("sync_domain_tables: reliability trigger skipped", exc_info=True)

    @staticmethod
    def _column_signature(columns) -> tuple:
        """Stable, order-independent signature of a table's columns for change
        detection: a sorted tuple of (name, dtype). Tolerates missing/None
        column lists and varied dict shapes."""
        sig = []
        for col in (columns or []):
            if isinstance(col, dict):
                sig.append((str(col.get("name", "")), str(col.get("dtype", "") or col.get("type", ""))))
            else:
                sig.append((str(col), ""))
        return tuple(sorted(sig))

    async def get_domain_connections(
        self,
        db: AsyncSession,
        data_source_id: str,
        organization: Organization,
    ):
        """Get all connections linked to an agent."""
        data_source = await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .filter(
                DataSource.id == data_source_id,
                DataSource.organization_id == organization.id
            )
        )
        data_source = data_source.scalar_one_or_none()
        if not data_source:
            raise HTTPException(status_code=404, detail="Agent not found")

        return data_source.connections
