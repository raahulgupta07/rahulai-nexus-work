from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_async_db, get_current_organization
from app.core.auth import current_user
from app.core.permissions_decorator import requires_permission
from app.schemas.organization_settings_schema import SsoConfigSchema, SsoConfigUpdate
from app.services.sso_config_service import SsoConfigService
from app.models.user import User
from app.models.organization import Organization

router = APIRouter(prefix="/enterprise/sso", tags=["sso"])


@router.get("/config", response_model=SsoConfigSchema)
@requires_permission("manage_identity_providers")
async def get_sso_config(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Return the effective SSO config (OIDC providers, Google, auth mode)."""
    return await SsoConfigService().get_config(db)


@router.put("/config", response_model=SsoConfigSchema)
@requires_permission("manage_identity_providers")
async def update_sso_config(
    payload: SsoConfigUpdate,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Save the SSO config; client secrets are encrypted at rest."""
    return await SsoConfigService().update_config(db, payload, current_user)
