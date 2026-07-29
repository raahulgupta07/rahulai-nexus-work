import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.permissions_decorator import requires_permission
from app.dependencies import get_async_db, get_current_organization
from app.models.organization import Organization
from app.models.user import User
from app.schemas.branding_schema import BrandingSchema, BrandingUpdate
from app.services.branding_service import BrandingService

router = APIRouter(tags=["settings"])

branding_service = BrandingService()

# Single-segment basename: no path separators, no traversal.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,200}$")


# ── Instance branding (admin) ──────────────────────────────────────────────
#
# Product name / tagline / logo, configurable at runtime. Stored on the
# InstanceSettings singleton, NOT on OrganizationSettings: the sign-in page
# renders before any organization is selected, so org-scoped branding would be
# unreachable on the one screen that matters most.
#
# Gated on `manage_settings` — the same permission the rest of the settings
# pages use (see routes/organization_settings.py). The decorator resolves that
# against the caller's organization, so these are admin routes even though what
# they configure is instance-wide.
#
# The public, unauthenticated read lives on `/api/settings`
# (routes/dash_settings.py) — that is what the login page consumes.


@router.get("/instance/branding", response_model=BrandingSchema)
@requires_permission("manage_settings")
async def get_instance_branding(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """The current branding block, with defaults filled for anything unset."""
    return await branding_service.get_branding(db)


@router.put("/instance/branding", response_model=BrandingSchema)
@requires_permission("manage_settings")
async def update_instance_branding(
    payload: BrandingUpdate,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Partially update branding. Omitted fields keep their stored values."""
    return await branding_service.update_branding(db, payload, current_user)


@router.post("/instance/branding/logo", response_model=BrandingSchema)
@requires_permission("manage_settings")
async def upload_instance_logo(
    logo: UploadFile = File(...),
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Upload a product logo. Stored in `uploads/branding`, served by the icon
    route below; only the generated key is written to `logo_key`."""
    return await branding_service.set_logo(db, logo, current_user)


@router.post("/instance/branding/favicon", response_model=BrandingSchema)
@requires_permission("manage_settings")
async def upload_instance_favicon(
    favicon: UploadFile = File(...),
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Upload a browser-tab icon. Same storage and serving path as the logo."""
    return await branding_service.set_favicon(db, favicon, current_user)


@router.get("/general/icon/{icon_key}")
async def get_general_icon(icon_key: str):
    # Inline path-traversal guard at the sink (Snyk python/PT).
    if ".." in icon_key or "/" in icon_key or "\\" in icon_key:
        raise HTTPException(status_code=404, detail="Icon not found")
    if not _SAFE_NAME_RE.fullmatch(icon_key):
        raise HTTPException(status_code=404, detail="Icon not found")

    base_dir = os.path.realpath(os.path.join(os.getcwd(), "uploads", "branding"))
    resolved = os.path.realpath(os.path.join(base_dir, icon_key))
    if not resolved.startswith(base_dir + os.sep):
        raise HTTPException(status_code=404, detail="Icon not found")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(resolved)


@router.get("/users/avatar/{avatar_key}")
async def get_user_avatar(avatar_key: str):
    """Serve an uploaded user avatar. Public (like the org icon): avatars are
    non-sensitive and need to render in <img> tags without auth headers."""
    if ".." in avatar_key or "/" in avatar_key or "\\" in avatar_key:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if not _SAFE_NAME_RE.fullmatch(avatar_key):
        raise HTTPException(status_code=404, detail="Avatar not found")

    base_dir = os.path.realpath(os.path.join(os.getcwd(), "uploads", "avatars"))
    resolved = os.path.realpath(os.path.join(base_dir, avatar_key))
    if not resolved.startswith(base_dir + os.sep):
        raise HTTPException(status_code=404, detail="Avatar not found")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Avatar not found")
    return FileResponse(resolved, media_type="image/png")


@router.get("/thumbnails/{filename}")
async def get_thumbnail(filename: str):
    """Serve thumbnail images for artifacts/reports."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    if not _SAFE_NAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    base_dir = os.path.realpath(os.path.join(os.getcwd(), "uploads", "thumbnails"))
    resolved = os.path.realpath(os.path.join(base_dir, filename))
    if not resolved.startswith(base_dir + os.sep):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(resolved, media_type="image/png")
