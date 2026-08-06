from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Body, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_async_db, release_request_db
from typing import Optional, List, Union

from app.ee.audit.service import audit_service

from app.models.user import User
from app.core.auth import current_user
from app.models.organization import Organization
from app.dependencies import get_current_organization
from app.services.custom_query_service import is_accelerable_type
from app.services.data_source_service import DataSourceService
from app.schemas.data_source_schema import DataSourceCreate, DataSourceBase, DataSourceSchema, DataSourceUpdate, DataSourceMembershipCreate, DataSourceListItemSchema
from app.schemas.metadata_indexing_job_schema import MetadataIndexingJobSchema
from app.schemas.data_source_schema import DataSourceMembershipSchema
from app.schemas.datasource_table_schema import (
    DataSourceTableSchema,
    PaginatedTablesResponse,
    BulkUpdateTablesRequest,
    DeltaUpdateTablesRequest,
    DeltaUpdateTablesResponse,
)
from app.core.permissions_decorator import requires_permission, requires_resource_permission, check_resource_permissions
from app.models.data_source import DataSource

router = APIRouter(tags=["data_sources"])
data_source_service = DataSourceService()

@router.get("/available_data_sources", response_model=list[dict])
async def get_available_data_sources(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.get_available_data_sources(db, organization)


# --- Per-org connector enablement (in-app admin toggle) -------------------
# The Fabric (User Sign-in) connector is gated by BOTH the env master flag
# (HYBRID_FABRIC_USER) AND this per-org switch. Lets an admin turn the connector
# on/off in the UI after the env flag is set on the server (e.g. on AWS), with
# no redeploy. Default ON.
@router.get("/connector-toggles")
async def get_connector_toggles(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    from app.services.organization_settings_service import OrganizationSettingsService
    return await OrganizationSettingsService().get_connector_toggles(db, organization, current_user)


from pydantic import BaseModel as _PydBaseModel


class _ConnectorToggleBody(_PydBaseModel):
    fabric_user_enabled: bool


@router.put("/connector-toggles")
@requires_permission('manage_connections')
async def set_connector_toggles(
    body: _ConnectorToggleBody,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    from app.services.organization_settings_service import OrganizationSettingsService
    return await OrganizationSettingsService().set_connector_toggle(
        db, organization, current_user, "fabric_user_enabled", body.fabric_user_enabled
    )

@router.get("/connectors/catalog", response_model=list[dict])
async def get_connectors_catalog(
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Curated catalog of pre-built MCP integrations (Monday, Notion, …) —
    the named presets on the registry's `mcp` entry. Admins add them from the
    Add Connection catalog; the DCR ones need no setup."""
    from app.schemas.data_source_registry import mcp_presets
    return mcp_presets()

@router.get("/connectors/custom-api-presets", response_model=list[dict])
async def get_custom_api_presets(
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Curated Custom API presets (X Write…) — ready-to-connect REST endpoints
    exposed as tools. The connect form pre-fills base_url / endpoints / OAuth
    defaults; the admin supplies the OAuth client id/secret."""
    from app.schemas.data_source_registry import custom_api_presets
    return custom_api_presets()

@router.get("/data_sources", response_model=list[DataSourceListItemSchema])
async def get_data_sources(
    show_all: bool = Query(False, description="Admin 'show all' view: include every data source in the org (private ones too). Only honored for callers with org-wide data-source governance (full_admin_access / manage_connections); ignored otherwise."),
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    result = await data_source_service.get_data_sources(db, current_user, organization, show_all=show_all)
    await release_request_db(db)  # free the pooled connection before serialization (Cause A, Phase 1)
    return result

@router.get("/data_sources/active", response_model=list[DataSourceListItemSchema])
async def get_active_data_sources(
    include_unconnected: bool = Query(False, description="Include user_required data sources the user hasn't connected yet (returned with user_status so the client can offer a Connect action)"),
    show_all: bool = Query(False, description="Admin-only: include every agent in the org (not just the caller's memberships); admin-only entries are flagged with admin_only"),
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    result = await data_source_service.get_active_data_sources(db, organization, current_user, include_unconnected=include_unconnected, show_all=show_all)
    await release_request_db(db)  # free the pooled connection before serialization (Cause A, Phase 1)
    return result

@router.get("/data_sources/hidden", response_model=list[str])
async def get_hidden_data_sources(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """IDs of agents the CURRENT USER has hidden from their own chat picker.
    Personal scope only — does not disable the agent for anyone else."""
    return await data_source_service.list_hidden_data_source_ids(db, current_user, organization)


@router.post("/data_sources/{data_source_id}/hide", response_model=dict)
async def hide_data_source(
    data_source_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Hide an agent from the current user's chat picker (personal, reversible)."""
    return await data_source_service.hide_data_source(db, current_user, organization, data_source_id)


@router.delete("/data_sources/{data_source_id}/hide", response_model=dict)
async def unhide_data_source(
    data_source_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Un-hide (restore) an agent in the current user's chat picker."""
    return await data_source_service.unhide_data_source(db, current_user, organization, data_source_id)


@router.get("/data_sources/connected_channels", response_model=list[dict])
async def get_connected_channels(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    """List the org's channels (Slack/Teams/WhatsApp/email/MCP) annotated with
    whether each is connected. Drives the per-agent channel-availability toggles
    in the new-agent and agent-settings UI."""
    return await data_source_service.get_connected_channels(db, organization)

@router.get("/data_sources/{data_source_id}", response_model=DataSourceSchema)
@requires_resource_permission('data_source', 'view')
async def get_data_source(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.get_data_source(db, data_source_id, organization, current_user)


@router.get("/data_sources/{data_source_type}/fields", response_model=dict)
async def get_data_source_fields(
    data_source_type: str,
    auth_policy: str = None,
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
    db: AsyncSession = Depends(get_async_db)
):
    return await data_source_service.get_data_source_fields(db, data_source_type, organization, current_user, auth_policy=auth_policy)

@router.get("/data_sources/{data_source_type}/setup-doc.docx")
async def download_setup_doc(
    data_source_type: str,
    auth_policy: str = None,
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
    db: AsyncSession = Depends(get_async_db)
):
    from fastapi.responses import Response
    filename, data = await data_source_service.build_setup_docx(db, data_source_type, organization, current_user, auth_policy=auth_policy)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.post("/data_sources", response_model=DataSourceSchema)
async def create_data_source(
    data_source: DataSourceCreate,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    # Gate inside the handler so EITHER the full connector-create permission
    # (create_data_source / full admin) OR the restricted file-agent permission
    # (create_file_data_source) works. Members with only the file permission are
    # forced into "file_only" mode: upload-based CSV agents, no server paths.
    from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
    resolved = await resolve_permissions(db, str(current_user.id), str(organization.id))
    perms = resolved.org_permissions
    is_full = FULL_ADMIN in perms or "create_data_source" in perms
    if not (is_full or "create_file_data_source" in perms):
        raise HTTPException(status_code=403, detail="Permission denied")
    file_only = not is_full

    # Check resource-level permission on connection(s) being linked
    connection_ids = []
    if data_source.connection_ids:
        connection_ids = data_source.connection_ids
    elif data_source.connection_id:
        connection_ids = [data_source.connection_id]
    if connection_ids:
        # Building an agent on an existing connection requires per-connection
        # `create_data_sources` (connection admins & manage_connections pass via
        # implication). ALL-connections semantics: every attached connection
        # must permit it.
        await check_resource_permissions(
            db, str(current_user.id), str(organization.id),
            "connection", connection_ids, "create_data_sources",
        )
    return await data_source_service.create_data_source(db, organization, current_user, data_source, file_only=file_only)

@router.delete("/data_sources/{data_source_id}")
@requires_resource_permission('data_source', 'manage')
async def delete_data_source(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.delete_data_source(db, data_source_id, organization, current_user)

@router.get("/data_sources/{data_source_id}/test_connection", response_model=dict)
@requires_resource_permission('data_source', 'view')
async def test_data_source_connection(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.test_data_source_connection(db, data_source_id, organization, current_user)

@router.post("/data_sources/test_connection", response_model=dict)
@requires_permission('create_data_source')
async def test_new_data_source_connection(
    data_source: DataSourceCreate,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.test_new_data_source_connection(db=db, data=data_source, organization=organization, current_user=current_user)

@router.put("/data_sources/{data_source_id}", response_model=DataSourceSchema)
@requires_resource_permission('data_source', 'manage')
async def update_data_source(
    data_source_id: str,
    data_source: DataSourceUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization)
):
    return await data_source_service.update_data_source(db, data_source_id, organization, data_source, current_user)

@router.get("/data_sources/{data_source_id}/schema", response_model=list)
@requires_resource_permission('data_source', 'view')
async def get_data_source_schema(
    data_source_id: str,
    with_stats: bool = Query(False),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    return await data_source_service.get_data_source_schema(db, data_source_id, include_inactive=False, organization=organization, current_user=current_user, with_stats=with_stats)

@router.get("/data_sources/{data_source_id}/full_schema", response_model=Union[PaginatedTablesResponse, list])
@requires_resource_permission('data_source', 'view_schema')
async def get_data_source_full_schema(
    data_source_id: str,
    with_stats: bool = Query(False),
    # Pagination params (optional - if not provided, returns legacy list response)
    page: Optional[int] = Query(None, ge=1, description="Page number (1-indexed)"),
    page_size: Optional[int] = Query(None, ge=1, le=500, description="Items per page (max 500)"),
    schema_filter: Optional[str] = Query(None, description="Comma-separated schema names to filter"),
    connection_filter: Optional[str] = Query(None, description="Comma-separated connection IDs to filter"),
    search: Optional[str] = Query(None, description="Search tables by name"),
    sort_by: str = Query("name", description="Sort by: name, centrality_score, is_active, richness"),
    sort_dir: str = Query("asc", description="Sort direction: asc or desc"),
    selected_state: Optional[str] = Query(None, description="Filter by selection state: 'selected' or 'unselected'"),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    # If pagination params provided, use paginated response
    if page is not None or page_size is not None:
        # Default pagination values
        page = page or 1
        page_size = page_size or 100

        # Parse schema filter (comma-separated string to list)
        schema_filter_list = None
        if schema_filter:
            schema_filter_list = [s.strip() for s in schema_filter.split(",") if s.strip()]

        # Parse connection filter (comma-separated string to list)
        connection_filter_list = None
        if connection_filter:
            connection_filter_list = [c.strip() for c in connection_filter.split(",") if c.strip()]

        paginated = await data_source_service.get_data_source_schema_paginated(
            db=db,
            data_source_id=data_source_id,
            organization=organization,
            page=page,
            page_size=page_size,
            schema_filter=schema_filter_list,
            connection_filter=connection_filter_list,
            search=search,
            sort_by=sort_by,
            sort_dir=sort_dir,
            include_inactive=True,
            selected_state=selected_state,
            with_stats=with_stats,
            current_user=current_user,
            # File connections are surfaced as Files, not Tables — keep their
            # per-file catalog rows out of the tables selector.
            exclude_file_source_types=True,
        )
        await release_request_db(db)  # free the pooled connection before serialization (Cause A, Phase 1)
        return paginated

    # Legacy behavior: return full list
    legacy = await data_source_service.get_data_source_schema(db, data_source_id, include_inactive=True, organization=organization, current_user=current_user, with_stats=with_stats)
    await release_request_db(db)  # free the pooled connection before serialization (Cause A, Phase 1)
    return legacy

@router.put("/data_sources/{data_source_id}/update_schema", response_model=DataSourceSchema)
@requires_resource_permission('data_source', 'manage')
async def update_table_status_in_schema(
    data_source_id: str,
    tables: list[DataSourceTableSchema],
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    result = await data_source_service.update_table_status_in_schema(db, data_source_id, tables, organization)
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.tables_updated",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"table_count": len(tables or [])}, request=http_request,
        )
    except Exception:
        pass
    return result


@router.post("/data_sources/{data_source_id}/bulk_update_tables", response_model=DeltaUpdateTablesResponse)
@requires_resource_permission('data_source', 'manage')
async def bulk_update_tables(
    data_source_id: str,
    request: BulkUpdateTablesRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """
    Bulk activate/deactivate tables matching filter criteria.

    - action: "activate" or "deactivate"
    - filter: {"schema": ["schema1", "schema2"], "search": "pattern"}
    """
    result = await data_source_service.bulk_update_tables_status(
        db=db,
        data_source_id=data_source_id,
        organization=organization,
        action=request.action,
        filter_params=request.filter,
        current_user=current_user,
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.tables_bulk_updated",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"action": request.action, "filter": request.filter}, request=http_request,
        )
    except Exception:
        pass
    return result


@router.put("/data_sources/{data_source_id}/update_tables_status", response_model=DeltaUpdateTablesResponse)
@requires_resource_permission('data_source', 'manage')
async def update_tables_status_delta(
    data_source_id: str,
    request: DeltaUpdateTablesRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """
    Update table is_active status using delta (efficient for large table counts).

    - activate: list of table names to set is_active=True
    - deactivate: list of table names to set is_active=False
    """
    result = await data_source_service.update_tables_status_delta(
        db=db,
        data_source_id=data_source_id,
        organization=organization,
        activate=request.activate,
        deactivate=request.deactivate,
        current_user=current_user,
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.tables_updated",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"activated": len(request.activate or []),
                     "deactivated": len(request.deactivate or [])}, request=http_request,
        )
    except Exception:
        pass
    return result


@router.get("/data_sources/{data_source_id}/generate_items", response_model=dict)
@requires_resource_permission('data_source', 'manage')
async def generate_data_source_items(
    data_source_id: str,
    item: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    return await data_source_service.generate_data_source_items(db, item, data_source_id, organization, current_user)

@router.post("/data_sources/{data_source_id}/llm_sync", response_model=dict)
@requires_resource_permission('data_source', 'manage')
async def llm_sync(
    data_source_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    result = await data_source_service.llm_sync(db=db, data_source_id=data_source_id, organization=organization, current_user=current_user)
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.llm_synced",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            request=http_request,
        )
    except Exception:
        pass
    return result

@router.get("/auto-learn", response_model=dict)
# ★★★Was `view_data_sources`, a string that exists in no registry. An
# unregistered name is not inert: `has_org_permission` is a set membership
# test, so nothing but the `full_admin_access` wildcard could ever satisfy it
# and this route was admin-only by accident rather than by decision. The
# accident happened to be safe — the body returns EVERY agent in the org with
# no per-user filtering, so it should not be reachable by a plain member — but
# it was one seeded role away from being wrong in the other direction, and it
# read to anyone maintaining it as a view permission that members held.
# `manage_connections` is the org-level admin permission for this category and
# is what the route was already behaving as, minus the wildcard-only accident.
@requires_permission('manage_connections')
async def get_auto_learn_overview(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Every agent's freshness in one answer.

    The question worth asking is "is anything stale?", and that is about the SET
    of agents — today it can only be asked one agent at a time, which in practice
    means it is never asked.

    Cheap: each agent's state is a comparison of two values already stored. No
    model call, nothing crawled.
    """
    from datetime import datetime

    from sqlalchemy import select

    from app.models.datasource_table import DataSourceTable
    from app.services import auto_learn, training_drift

    agents = (await db.execute(
        select(DataSource).filter(
            DataSource.organization_id == organization.id,
            DataSource.deleted_at.is_(None),
        ).order_by(DataSource.name)
    )).scalars().all()

    now = datetime.utcnow()
    policy = await auto_learn.org_policy(db, organization)
    rows, stale, watched = [], 0, 0
    for a in agents:
        tables = (await db.execute(
            select(DataSourceTable).filter(DataSourceTable.datasource_id == a.id)
        )).scalars().all()
        status = training_drift.drift_for(a, tables)
        on = status["mode"] == training_drift.MODE_AUTO
        watched += 1 if on else 0
        stale += 1 if status["stale"] else 0
        rows.append({
            "id": str(a.id), "name": a.name,
            "auto": on,
            "stale": status["stale"], "known": status["known"],
            "summary": status["summary"],
            "trained_at": status["trained_at"],
            "active_tables": status["active_tables"],
        })

    return {
        "policy": policy,
        # Spent across every agent, because that total is the number anyone
        # actually cares about.
        "runs_today": await auto_learn.runs_today(db, organization, now),
        "watched": watched,
        "stale": stale,
        "agents": rows,
    }


@router.put("/auto-learn", response_model=dict)
@requires_permission('manage_settings')
async def update_auto_learn(
    payload: dict,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """The master switch and the budget.

    Goes through the settings service so the stored block is validated. ★Writing
    an org setting by hand has broken the whole settings surface here before:
    a partial object fails validation on every later read, and the page renders
    empty with "Failed to fetch settings".

    `manage_settings` because this decides what happens on everyone's behalf,
    including whether agents may spend model calls without being asked.
    """
    from app.schemas.organization_settings_schema import OrganizationSettingsUpdate
    from app.services.organization_settings_service import OrganizationSettingsService

    allowed = {"enabled", "quiet_minutes", "max_runs_per_day", "notify_on_train"}
    block = {k: v for k, v in (payload or {}).items() if k in allowed}
    if not block:
        raise HTTPException(
            status_code=400,
            detail="Nothing to update. Expected one of: " + ", ".join(sorted(allowed)),
        )
    updated = await OrganizationSettingsService().update_settings(
        db, organization, current_user,
        OrganizationSettingsUpdate(config={"auto_learn": block}),
    )
    cfg = (getattr(updated, "config", None) or {})
    return cfg.get("auto_learn", block)


@router.post("/auto-learn/check", response_model=dict)
@requires_permission('manage_settings')
async def run_auto_learn_now(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Run the check now instead of waiting for the next quarter-hour.

    Honours every guard the scheduled pass does — the master switch, the quiet
    period and the daily ceiling. "Check now" means "do the tick early", not
    "ignore the budget".
    """
    from app.services.auto_learn import sweep_auto_learn

    await sweep_auto_learn()
    return {"checked": True}


@router.get("/data_sources/{data_source_id}/training-status", response_model=dict)
@requires_resource_permission('data_source', 'view')
async def get_training_status(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Whether this agent's overview still describes its data.

    Cheap by construction: it compares a fingerprint recorded at training time
    against the tables as they stand now. No model call, nothing crawled — which
    is why noticing can be on by default while re-learning is not.

    `known: false` means the agent has never been trained by a version that
    recorded this. That is NOT "up to date", and the UI must not present it as
    such; every agent predating the feature is in that state.
    """
    # Imported here, not at module scope: this file does not import `select`
    # globally, and a route that assumed it does raised NameError at request
    # time — invisible to a test that reads the source rather than calls it.
    from sqlalchemy import select

    from app.models.datasource_table import DataSourceTable
    from app.services import training_drift

    ds = (await db.execute(
        select(DataSource).filter(
            DataSource.id == data_source_id,
            DataSource.organization_id == organization.id,
        )
    )).scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="Data source not found")

    tables = (await db.execute(
        select(DataSourceTable).filter(DataSourceTable.datasource_id == ds.id)
    )).scalars().all()
    return training_drift.drift_for(ds, tables)


@router.put("/data_sources/{data_source_id}/training-settings", response_model=dict)
@requires_resource_permission('data_source', 'manage')
async def update_training_settings(
    data_source_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Set how this agent keeps itself current: manual, notify, or auto.

    Requires `manage` rather than `view` — unlike training itself, this decides
    what happens on everyone's behalf, including whether the agent may spend
    model calls without being asked.
    """
    from sqlalchemy import select

    from app.services import training_drift

    mode = (payload or {}).get("mode")
    if mode not in training_drift.VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode {mode!r}. Expected one of: "
                   + ", ".join(training_drift.VALID_MODES),
        )

    ds = (await db.execute(
        select(DataSource).filter(
            DataSource.id == data_source_id,
            DataSource.organization_id == organization.id,
        )
    )).scalar_one_or_none()
    if not ds:
        raise HTTPException(status_code=404, detail="Data source not found")

    # Reassign rather than mutate — SQLAlchemy does not track in-place edits to
    # a JSON column and the write would be silently dropped.
    settings = dict(ds.training_settings or {})
    settings["mode"] = mode
    ds.training_settings = settings
    db.add(ds)
    await db.commit()
    return {"mode": mode}


@router.post("/data_sources/{data_source_id}/relearn", response_model=dict)
@requires_resource_permission('data_source', 'view')
async def relearn_data_source(
    data_source_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Regenerate the onboarding overview instruction grounded on the data
    source's NOW-real synced tables ("Use LLM to learn agent").

    Built for the per-user sign-in connectors (fabric_user / powerbi_user): a
    member signs in, tables auto-activate, then this regenerates the overview on
    the concrete schema. Forces LLM generation regardless of the agent's stored
    use_llm_sync preference. Any org member with access (view) to the DS may run
    it for the agent they can reach — the same members the auto-trigger fires for
    on sign-in.
    """
    result = await data_source_service.llm_sync(
        db=db, data_source_id=data_source_id, organization=organization,
        current_user=current_user, force_llm=True,
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.relearned",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            request=http_request,
        )
    except Exception:
        pass
    return result

@router.get("/data_sources/{data_source_id}/learn-status", response_model=dict)
@requires_resource_permission('data_source', 'view')
async def get_learn_status(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Live progress of the "Learn agent" (LLM overview regeneration) run for
    this data source and the calling user, so the UI can render the current
    stage instead of a bare spinner.

    Progress is tracked in-memory per (data_source_id, user_id) and stamped by
    data_source_service.llm_sync(force_llm=True) when settings.hybrid_learn_progress
    is on. Connector-agnostic. Returns an idle shape when no run is tracked.
    """
    from app.services import learn_progress
    return await learn_progress.get(db, str(data_source_id), str(current_user.id))

@router.get("/data_sources/{data_source_id}/onboarding_instruction", response_model=dict)
@requires_resource_permission('data_source', 'view')
async def get_onboarding_instruction(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Return the data source's onboarding overview instruction for the FE
    "Set context" step, so the wizard can display the just-learned draft (or the
    published overview) without hydrating the whole instruction list.

    Returns ``{id, title, text, status}`` of the newest non-deleted instruction
    with ``ai_source == "onboarding"`` for this data source, preferring the one
    the agent actually uses (its ``primary_instruction_id``) and then the
    published one, then newest. 404 when the agent has no onboarding overview
    yet (e.g. learning is still running in the background). No LLM calls.
    """
    from sqlalchemy import select, or_
    from app.models.instruction import Instruction, instruction_data_source_association

    ds = (await db.execute(
        select(DataSource).filter(
            DataSource.id == data_source_id,
            DataSource.organization_id == organization.id,
        )
    )).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="Data source not found")

    _primary_id = (
        str(ds.primary_instruction_id)
        if getattr(ds, "primary_instruction_id", None) else None
    )
    _order = []
    if _primary_id:
        _order.append((Instruction.id == _primary_id).desc())
    _order.append((Instruction.status == "published").desc())
    _order.append(Instruction.created_at.desc())

    instr = (await db.execute(
        select(Instruction).join(
            instruction_data_source_association,
            instruction_data_source_association.c.instruction_id == Instruction.id,
        ).filter(
            instruction_data_source_association.c.data_source_id == data_source_id,
            Instruction.ai_source == "onboarding",
            Instruction.deleted_at.is_(None),
            # On a per-user connector every member's Learn writes its own PRIVATE
            # overview. Without this scope the newest one wins regardless of who
            # owns it, so this endpoint would hand member B the text of member
            # A's private overview. Shared overviews stay visible to everyone.
            or_(
                Instruction.is_private.is_(False),
                Instruction.is_private.is_(None),
                Instruction.user_id == str(current_user.id),
            ),
        ).order_by(*_order).limit(1)
    )).scalar_one_or_none()

    if instr is None:
        raise HTTPException(status_code=404, detail="No onboarding instruction found")

    return {
        "id": str(instr.id),
        "title": instr.title,
        "text": instr.text,
        "status": instr.status,
    }

@router.get("/data_sources/{data_source_id}/refresh_schema", response_model=list)
@requires_resource_permission('data_source', 'view_schema')
async def refresh_data_source_schema(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    return await data_source_service.refresh_data_source_schema(db, data_source_id, organization, current_user)

@router.get("/data_sources/{data_source_id}/metadata_resources", response_model=MetadataIndexingJobSchema)
@requires_resource_permission('data_source', 'view')
async def get_metadata_resources(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    return await data_source_service.get_metadata_resources(db, data_source_id, organization, current_user)

@router.put("/data_sources/{data_source_id}/update_metadata_resources", response_model=MetadataIndexingJobSchema)
@requires_resource_permission('data_source', 'manage')
async def update_metadata_resources(
    data_source_id: str,
    http_request: Request,
    resources: list = Body(...),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Update the active status of metadata resources for a data source"""
    result = await data_source_service.update_resources_status(
        db=db,
        data_source_id=data_source_id,
        resources=resources,
        organization=organization,
        current_user=current_user
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.metadata_resources_updated",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"resource_count": len(resources or [])}, request=http_request,
        )
    except Exception:
        pass
    return result


@router.get("/data_sources/{data_source_id}/members", response_model=list[DataSourceMembershipSchema])
@requires_resource_permission('data_source', 'view')
async def get_data_source_members(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    return await data_source_service.get_data_source_members(db, data_source_id, organization, current_user)

@router.post("/data_sources/{data_source_id}/members", response_model=DataSourceMembershipSchema)
@requires_resource_permission('data_source', 'manage')
async def add_data_source_member(
    data_source_id: str,
    member: DataSourceMembershipCreate,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    result = await data_source_service.add_data_source_member(db, data_source_id, member, organization, current_user)
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.member_added",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"user_id": getattr(member, "user_id", None)}, request=http_request,
        )
    except Exception:
        pass
    return result

@router.delete("/data_sources/{data_source_id}/members/{user_id}", status_code=204)
@requires_resource_permission('data_source', 'manage')
async def remove_data_source_member(
    data_source_id: str,
    user_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    result = await data_source_service.remove_data_source_member(db, data_source_id, user_id, organization, current_user)
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.member_removed",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"user_id": str(user_id)}, request=http_request,
        )
    except Exception:
        pass
    return result


# ==================== Domain-Connection Routes ====================

@router.get("/data_sources/{data_source_id}/connections")
@requires_resource_permission('data_source', 'manage')
async def get_domain_connections(
    data_source_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Get all connections linked to an agent."""
    connections = await data_source_service.get_domain_connections(db, data_source_id, organization)
    # Expose the non-secret config (credentials live encrypted + separate) so
    # the agent UI can show a file connection's scope (path/prefix, globs,
    # indexing) without a second round-trip. This endpoint already requires the
    # 'manage' permission on the data source.
    def _safe_config(conn):
        cfg = conn.config
        if isinstance(cfg, str):
            try:
                import json as _json
                cfg = _json.loads(cfg)
            except Exception:
                cfg = {}
        return cfg if isinstance(cfg, dict) else {}
    # Include auth policy + the caller's per-connection auth status so the
    # tables selector can prompt "Connect your account" for delegated (OBO)
    # connections instead of rendering an unexplained empty list.
    from app.services.user_data_source_credentials_service import UserDataSourceCredentialsService
    _status_svc = UserDataSourceCredentialsService()

    async def _user_status(conn):
        if (conn.auth_policy or "system_only") != "user_required":
            return None
        try:
            status = await _status_svc.build_user_status_for_connection(
                db, conn, current_user, live_test=False
            )
            return status.model_dump()
        except Exception:
            return None

    return [
        {
            "id": str(conn.id),
            "name": conn.name,
            "type": conn.type,
            "is_active": conn.is_active,
            "config": _safe_config(conn),
            "auth_policy": conn.auth_policy,
            "allowed_user_auth_modes": conn.allowed_user_auth_modes,
            "user_status": await _user_status(conn),
            # Whether this connection can host BOW custom queries (accelerable
            # connector type + shared credentials). Drives the "Add Custom"
            # affordance in the tables selector.
            "custom_queries_supported": (
                is_accelerable_type(conn.type)
                and (conn.auth_policy or "system_only") == "system_only"
            ),
        }
        for conn in connections
    ]


@router.get("/data_sources/{data_source_id}/connections/{connection_id}/files")
@requires_resource_permission('data_source', 'view')
async def list_connection_files(
    data_source_id: str,
    connection_id: str,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
):
    """Browse a file connection's files — the SAME live path the agent's
    list_files tool uses (scoped by globs, bounded by the connection's cap).

    This is the single source of truth for "what files does this connection
    expose", so the UI browse never diverges from what the agent sees. It's
    live for cheap-to-list sources (network_dir/S3) and reflects the real
    source — so `none`-mode connections show their files (not an empty cache).
    """
    from app.models.connection import Connection
    from app.services.connection_service import ConnectionService
    conns = await data_source_service.get_domain_connections(db, data_source_id, organization)
    conn = next((c for c in conns if str(c.id) == str(connection_id)), None)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not attached to this agent")
    try:
        client = await ConnectionService().construct_client(db, conn, current_user)
        entries = await client.alist_files(recursive=True)
    except HTTPException as he:
        # Per-user OAuth sources raise 403 when the caller hasn't linked their
        # account. Surface that as a structured "connect required" state (not a
        # generic failure) so the UI can prompt sign-in instead of showing an
        # error. Any other HTTPException is a real error — re-raise it.
        detail = str(getattr(he, "detail", "") or "")
        if he.status_code == 403 and "connect" in detail.lower():
            return {
                "connection_id": str(connection_id),
                "connect_required": True,
                "reason": detail,
                "files": [], "total": 0, "offset": offset, "limit": limit, "has_more": False,
            }
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to list files: {e}")
    files = [e for e in (entries or []) if not e.get("is_folder")]
    total = len(files)
    page = files[max(0, offset): max(0, offset) + max(1, min(limit, 500))]
    return {
        "connection_id": str(connection_id),
        "files": [{"id": f.get("id"), "name": f.get("name"), "size": f.get("size"),
                   "modified_at": f.get("modified_at"), "mime_type": f.get("mime_type")} for f in page],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


@router.post("/data_sources/{data_source_id}/connections/{connection_id}")
@requires_resource_permission('data_source', 'manage')
async def add_connection_to_domain(
    data_source_id: str,
    connection_id: str,
    http_request: Request,
    sync_tables: bool = True,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Add a connection to an agent (M:N relationship).

    Two independent capabilities are required, matching the "build an agent on a
    connection" model:
      - The `data_source:manage` decorator proves the caller owns/manages this
        agent.
      - Per-connection `create_data_sources` proves they may build agents on the
        connection being attached — the SAME check `create_data_source` runs when
        an agent is created directly on a connection. Connection admins /
        `manage_connections` pass via implication.
    """
    await check_resource_permissions(
        db, str(current_user.id), str(organization.id),
        "connection", [connection_id], "create_data_sources",
    )

    result = await data_source_service.add_connection_to_domain(
        db=db,
        data_source_id=data_source_id,
        connection_id=connection_id,
        organization=organization,
        current_user=current_user,
        sync_tables=sync_tables,
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.connection_linked",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"connection_id": str(connection_id)}, request=http_request,
        )
    except Exception:
        pass
    return result


@router.delete("/data_sources/{data_source_id}/connections/{connection_id}")
@requires_resource_permission('data_source', 'manage')
async def remove_connection_from_domain(
    data_source_id: str,
    connection_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user)
):
    """Remove a connection from an agent."""
    result = await data_source_service.remove_connection_from_domain(
        db=db,
        data_source_id=data_source_id,
        connection_id=connection_id,
        organization=organization,
        current_user=current_user,
    )
    try:
        await audit_service.log(
            db=db, organization_id=organization.id, action="data_source.connection_unlinked",
            user_id=current_user.id, resource_type="data_source", resource_id=str(data_source_id),
            details={"connection_id": str(connection_id)}, request=http_request,
        )
    except Exception:
        pass
    return result