# LDAP Background Sync Jobs
# Licensed under the Business Source License 1.1
# See ENTERPRISE_LICENSE for details

import logging

from app.dependencies import async_session_maker
from app.settings.config import settings
from app.ee.ldap.sync_service import LDAPGroupSyncService
from app.models.organization import Organization
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def ldap_sync_all_organizations():
    """Background job: sync LDAP groups for all organizations."""
    import asyncio
    from app.core.scheduler import claim_scheduled_run
    # Fires in every worker (shared job store); claim so only one syncs.
    if not await asyncio.to_thread(claim_scheduled_run, "ldap_group_sync"):
        return

    # Resolve LDAP config per organization: each org may have saved its own block
    # via the UI (config.ldap), otherwise it falls back to the global bow-config
    # file section. Orgs with LDAP disabled are skipped.
    from app.services.organization_settings_service import OrganizationSettingsService
    settings_service = OrganizationSettingsService()

    async with async_session_maker() as db:
        orgs = (await db.execute(select(Organization))).scalars().all()
        for org in orgs:
            try:
                org_config = await settings_service.resolve_ldap_config(db, org)
                if not org_config.enabled:
                    continue
                sync_service = LDAPGroupSyncService(org_config)
                result = await sync_service.sync_groups(db, str(org.id))
                if result.errors:
                    logger.warning(f"LDAP sync for org {org.id} completed with errors: {result.errors}")
            except Exception as e:
                logger.error(f"LDAP sync failed for org {org.id}: {e}")
