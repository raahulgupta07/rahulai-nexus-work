from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_gate import require_access
from app.dependencies import get_async_db, get_current_organization
from app.core.auth import current_user, forbid_service_account_principal
from app.models.user import User
from app.models.organization import Organization
from app.schemas.api_key_schema import ApiKeyCreate, ApiKeyResponse, ApiKeyCreated
from app.services.api_key_service import ApiKeyService
from app.ee.audit.service import audit_service

# A leaked service-account key must not be able to mint more keys.
router = APIRouter(prefix="/api_keys", tags=["api_keys"], dependencies=[Depends(forbid_service_account_principal)])
api_key_service = ApiKeyService()


@router.post("", response_model=ApiKeyCreated)
async def create_api_key(
    data: ApiKeyCreate,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Create a new API key for the current user within the current organization.

    The full key is only returned once upon creation. Store it securely.
    """
    require_access(organization, "api_keys", "API keys")
    result = await api_key_service.create_api_key(db, data, user, organization)
    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="api_key.created",
            user_id=user.id,
            resource_type="api_key",
            resource_id=result.id,
            details={"name": data.name},
            request=request,
        )
    except Exception:
        pass
    return result


@router.get("", response_model=List[ApiKeyResponse])
async def list_api_keys(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """List all API keys for the current user."""
    require_access(organization, "api_keys", "API keys")
    return await api_key_service.list_api_keys(db, user)


@router.delete("/{key_id}")
async def delete_api_key(
    key_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Revoke an API key.

    Deliberately NOT gated. Revoking is the safe direction: if an admin
    switches API keys off while a member holds a key they want to destroy,
    blocking the revoke would keep a live credential alive with no way to kill
    it. Creating and listing are gated; taking a key away never is.
    """
    await api_key_service.delete_api_key(db, key_id, user)
    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="api_key.revoked",
            user_id=user.id,
            resource_type="api_key",
            resource_id=key_id,
            request=request,
        )
    except Exception:
        pass
    return {"message": "API key revoked"}


