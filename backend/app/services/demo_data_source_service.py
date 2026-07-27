"""
Demo Data Source Service

Handles listing and installing demo data sources.
"""

from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_

from app.models.data_source import DataSource
from app.models.user import User
from app.models.organization import Organization
from app.schemas.demo_data_source_schema import (
    DEMO_DATA_SOURCES,
    DemoDataSourceDefinition,
    DemoDataSourceListItem,
    DemoDataSourceInstallResponse,
    get_demo_data_source,
    list_demo_data_sources as list_demo_definitions,
)

import json
import logging

from app.core.telemetry import telemetry

logger = logging.getLogger(__name__)


# Marker key stored in config to identify demo data sources
DEMO_ID_KEY = "demo_id"


class DemoDataSourceService:

    async def list_demo_data_sources(
        self,
        db: AsyncSession,
        organization: Organization,
    ) -> List[DemoDataSourceListItem]:
        """
        List all available demo data sources with their installation status.
        """
        # Get all demo definitions
        demos = list_demo_definitions()

        # Check which ones are already installed for this organization
        installed_demos = await self._get_installed_demos(db, organization.id)

        result = []
        for demo in demos:
            installed_ds = installed_demos.get(demo.id)
            result.append(
                DemoDataSourceListItem(
                    id=demo.id,
                    name=demo.name,
                    description=demo.description,
                    type=demo.type,
                    installed=installed_ds is not None,
                    installed_data_source_id=installed_ds.id if installed_ds else None,
                )
            )

        return result

    async def install_demo_data_source(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        demo_id: str,
    ) -> DemoDataSourceInstallResponse:
        """
        Install a demo data source for the organization.
        
        If already installed, returns the existing data source.
        """
        # Get demo definition
        demo = get_demo_data_source(demo_id)
        if not demo:
            return DemoDataSourceInstallResponse(
                success=False,
                message=f"Demo data source '{demo_id}' not found",
            )

        # Check if already installed (the demo's database/connection exists)
        existing = await self._get_installed_demo(db, organization.id, demo_id)
        if existing:
            return DemoDataSourceInstallResponse(
                success=True,
                message=f"Sample database '{demo.name}' is already added",
                data_source_id=existing.id,  # connection id
                already_installed=True,
            )

        # Register the sample database (connection-only — no agent).
        try:
            connection = await self._create_demo_data_source(
                db=db,
                organization=organization,
                current_user=current_user,
                demo=demo,
            )

            # Telemetry: track sample database registration.
            try:
                await telemetry.capture(
                    "connection_created",
                    {
                        "connection_id": str(connection.id),
                        "type": f"{demo.type}-demo",
                        "auth_policy": "system_only",
                        "from_demo": True,
                    },
                    user_id=current_user.id,
                    org_id=organization.id,
                )
            except Exception:
                pass  # Never fail the request due to telemetry

            return DemoDataSourceInstallResponse(
                success=True,
                message=f"Added sample database '{demo.name}'. Create an agent on it from the wizard.",
                data_source_id=connection.id,  # connection id (no agent is created)
                already_installed=False,
            )

        except Exception as e:
            logger.error(f"Failed to add sample database {demo_id}: {e}")
            return DemoDataSourceInstallResponse(
                success=False,
                message=f"Failed to add sample database: {str(e)}",
            )

    async def _create_demo_data_source(
        self,
        db: AsyncSession,
        organization: Organization,
        current_user: User,
        demo: DemoDataSourceDefinition,
    ) -> DataSource:
        """
        Create a data source from a demo definition.
        
        This bypasses the normal registry dev_only check since demos
        are explicitly whitelisted.
        """
        # Build config with demo_id marker
        config = demo.config.copy()
        config[DEMO_ID_KEY] = demo.id

        # Create the Connection first — but a prior install of this demo whose
        # data source was deleted can leave the connection behind (delete does
        # not cascade it), and connection names are unique per org. Reuse an
        # existing connection with the same name instead of colliding on the
        # `uq_connections_org_name` constraint (which made re-install silently
        # fail with "duplicate key"). Refresh its config to the demo's.
        from sqlalchemy import select as _sel
        from app.models.connection import Connection
        conn_name = demo.connection_name or demo.name
        # Match by name INCLUDING soft-deleted rows: `uq_connections_org_name`
        # is not filtered by deleted_at, so a soft-deleted connection with this
        # name still owns the unique slot. Resurrect it (clear deleted_at)
        # instead of INSERTing a colliding row.
        existing_conn = (await db.execute(_sel(Connection).where(
            Connection.organization_id == str(organization.id),
            Connection.name == conn_name,
        ).order_by(Connection.deleted_at.is_(None).desc()))).scalars().first()
        if existing_conn is not None:
            connection = existing_conn
            connection.type = demo.type
            connection.config = json.dumps(config)
            connection.is_active = True
            connection.deleted_at = None  # resurrect if it was soft-deleted
            if demo.credentials:
                connection.encrypt_credentials(demo.credentials)
        else:
            connection = Connection(
                name=conn_name,
                type=demo.type,
                config=json.dumps(config),
                organization_id=str(organization.id),
                is_active=True,
                auth_policy="system_only",
            )
            # Encrypt credentials on connection (even if empty, for consistency)
            if demo.credentials:
                connection.encrypt_credentials(demo.credentials)
            db.add(connection)
        await db.flush()

        # Database-only: a sample install registers just the CONNECTION (the
        # database). It does NOT create an agent — the user builds the agent
        # themselves via the Create Data Agent wizard, picking this connection.
        # We still populate the connection's schema (ConnectionTable records) so
        # the wizard shows the tables ("N tables") straight away.
        await db.commit()

        try:
            from app.services.connection_service import ConnectionService
            await ConnectionService().refresh_schema(
                db=db, connection=connection, current_user=current_user,
            )
        except Exception as e:
            # Non-fatal: the connection still exists; schema can be refreshed later.
            logger.warning(f"Demo '{demo.name}': schema refresh failed: {e}")

        logger.info(
            f"Installed demo database (connection-only): {connection.name} "
            f"(id={connection.id}) for org={organization.id}"
        )
        return connection

    async def _test_connection(
        self,
        db: AsyncSession,
        data_source: DataSource,
        organization: Organization,
        current_user: User,
    ):
        """
        Test the connection to ensure proper status is set.
        """
        from app.services.data_source_service import DataSourceService
        
        try:
            ds_service = DataSourceService()
            result = await ds_service.test_data_source_connection(
                db=db,
                data_source_id=data_source.id,
                organization=organization,
                current_user=current_user,
            )
            logger.info(f"Connection test for demo data source {data_source.name}: {result}")
        except Exception as e:
            logger.warning(f"Failed to test connection for demo data source {data_source.name}: {e}")

    async def _create_instructions(
        self,
        db: AsyncSession,
        data_source: DataSource,
        organization: Organization,
        current_user: User,
        demo: DemoDataSourceDefinition,
    ):
        """
        Create instructions for the demo data source.
        """
        if not demo.instructions:
            return

        from app.services.instruction_service import InstructionService
        from app.schemas.instruction_schema import InstructionCreate

        try:
            instruction_service = InstructionService()
            
            # === Build System Integration ===
            # Create a single build for all demo instructions
            demo_build = None
            try:
                from app.services.build_service import BuildService
                build_service = BuildService()
                demo_build = await build_service.get_or_create_draft_build(
                    db,
                    organization.id,
                    source='user',
                    user_id=current_user.id
                )
                logger.debug(f"Created demo build {demo_build.id} for data source {data_source.name}")
            except Exception as build_error:
                logger.warning(f"Failed to create demo build: {build_error}")
            
            created_count = 0
            for instruction_text in demo.instructions:
                # Scope each demo instruction to THIS newly created data source.
                # (Do NOT pass force_global — these must stay associated with the
                # demo agent, not leak in as global/all-agent instructions.)
                instruction_data = InstructionCreate(
                    text=instruction_text,
                    data_source_ids=[data_source.id],
                    category="general",
                )

                created = await instruction_service.create_instruction(
                    db=db,
                    instruction_data=instruction_data,
                    current_user=current_user,
                    organization=organization,
                    build=demo_build,  # Use shared build
                    auto_finalize=False,  # Don't finalize yet
                )

                # Safety net: guarantee the association exists even if a future
                # build/version flow change ever drops it.
                try:
                    if created is not None and not any(str(ds.id) == str(data_source.id) for ds in (created.data_sources or [])):
                        await instruction_service._associate_data_sources(db, created, [data_source.id])
                except Exception as assoc_error:
                    logger.warning(f"Failed to ensure demo instruction association: {assoc_error}")
                created_count += 1
            
            # === Finalize Build ===
            if demo_build and created_count > 0:
                try:
                    await build_service.submit_build(db, demo_build.id)
                    await build_service.approve_build(db, demo_build.id, approved_by_user_id=current_user.id)
                    await build_service.promote_build(db, demo_build.id)
                    logger.info(f"Finalized demo build {demo_build.id} with {created_count} instructions")
                except Exception as finalize_error:
                    logger.warning(f"Failed to finalize demo build: {finalize_error}")
            
            logger.info(f"Created {len(demo.instructions)} instructions for demo data source: {data_source.name}")
        except Exception as e:
            # Log but don't fail - the data source is still created
            logger.warning(f"Failed to create instructions for demo data source {data_source.name}: {e}")

    async def _load_tables(
        self,
        db: AsyncSession,
        data_source: DataSource,
        organization: Organization,
        current_user: User,
    ):
        """
        Load tables from the demo data source connection.

        Uses ConnectionService.refresh_schema to create ConnectionTable records,
        then syncs them to DataSourceTable records. This ensures demo connections
        can be linked to other domains later.
        """
        from app.services.connection_service import ConnectionService
        from app.services.data_source_service import DataSourceService

        try:
            if not data_source.connections:
                logger.warning(f"Demo data source {data_source.name} has no connections")
                return

            connection = data_source.connections[0]

            # Step 1: Refresh schema to create ConnectionTable records
            conn_service = ConnectionService()
            await conn_service.refresh_schema(
                db=db,
                connection=connection,
                current_user=current_user,
            )
            logger.info(f"Created ConnectionTable records for demo connection: {connection.name}")

            # Step 2: Sync ConnectionTable records to DataSourceTable records
            ds_service = DataSourceService()
            await ds_service.sync_domain_tables_from_connection(
                db=db,
                data_source=data_source,
                connection=connection,
                max_auto_select=9999,  # Activate all tables for demos
            )
            logger.info(f"Loaded tables for demo data source: {data_source.name}")
        except Exception as e:
            # Log but don't fail - the data source is still created
            logger.warning(f"Failed to load tables for demo data source {data_source.name}: {e}")

    async def _get_installed_demos(
        self,
        db: AsyncSession,
        organization_id: str,
    ) -> dict:
        """
        Get all installed demo databases for an organization.

        Demos are database-only now: installing a sample registers a
        Connection (the database), NOT an agent. Installed status is therefore
        keyed on the connection carrying the demo_id marker. Returns a dict
        mapping demo_id -> Connection.
        """
        from app.models.connection import Connection

        stmt = select(Connection).where(
            Connection.organization_id == organization_id,
            Connection.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        connections = result.scalars().all()

        installed = {}
        for conn in connections:
            config = conn.config if isinstance(conn.config, dict) else json.loads(conn.config or "{}")
            demo_id = config.get(DEMO_ID_KEY)
            if demo_id and demo_id in DEMO_DATA_SOURCES:
                installed[demo_id] = conn
        return installed

    async def _get_installed_demo(
        self,
        db: AsyncSession,
        organization_id: str,
        demo_id: str,
    ):
        """Check if a specific demo database is already installed (Connection)."""
        installed = await self._get_installed_demos(db, organization_id)
        return installed.get(demo_id)
