import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from app.settings.config import settings
from app.dependencies import get_current_locale, get_current_organization, _locale_from_org
from app.models.organization import Organization

router = APIRouter()

@router.get("/settings", tags=["settings"])
async def get_frontend_settings():
    """Get frontend configuration settings"""
    is_testing = os.getenv("TESTING", "").lower() == "true"

    # SSO/auth config is resolved from the DB (falling back to bow-config file)
    # so the login page reflects UI-configured providers. This endpoint is public
    # and has no org context, so use a fresh session directly.
    from app.dependencies import async_session_maker
    from app.services.sso_config_service import SsoConfigService

    _sso = SsoConfigService()
    # First-run: server has zero users -> the login page shows a one-shot
    # "create your super-admin" form. FAIL-CLOSED: any error counting users
    # means False (normal login), never an open signup form.
    _needs_setup = False
    # Product branding (name, tagline, logo). Read here rather than from an
    # organization's settings because this endpoint — and the login page that
    # consumes it — has no organization context at all.
    from app.schemas.branding_schema import BrandingSchema
    from app.services.branding_service import BrandingService

    _branding = BrandingSchema()  # packaged defaults; overwritten below
    async with async_session_maker() as _db:
        _google = await _sso.resolve_google(_db)
        _auth_mode = await _sso.resolve_auth_mode(_db)
        _oidc_providers = await _sso.resolve_oidc_providers(_db)
        try:
            _branding = await BrandingService().get_branding(_db)
        except Exception:
            # Branding must never be able to take sign-in down. Falling back to
            # the packaged defaults renders exactly today's product.
            pass
        try:
            from sqlalchemy import select, func
            from app.models.user import User
            _user_count = (await _db.execute(select(func.count(User.id)))).scalar() or 0
            _needs_setup = _user_count == 0
        except Exception:
            _needs_setup = False

    # "configured" tells the login page whether an ENABLED provider is actually
    # usable yet. An enabled-but-unconfigured provider still surfaces (button
    # shown) but clicking it shows a friendly "ask your admin" message instead
    # of a broken redirect. Computed ONLY for the public feed — the real
    # authorize/callback resolvers (resolve_google/resolve_oidc_providers) keep
    # their existing behavior. google configured = client_id + secret present;
    # oidc configured = issuer + client_id present.
    _google_configured = bool(
        getattr(_google, "client_id", None) and getattr(_google, "client_secret", None)
    )

    def _oidc_configured(p) -> bool:
        # Secret folded in for non-PKCE providers: without it the authorize
        # route fails anyway, so the login page should pre-guard with the
        # friendly message instead of letting the click die mid-redirect. PKCE
        # public clients legitimately have no secret.
        base = bool(
            (getattr(p, "issuer", "") or "").strip()
            and (getattr(p, "client_id", "") or "").strip()
        )
        if not base:
            return False
        if getattr(p, "pkce", False):
            return True
        return bool((getattr(p, "client_secret", "") or "").strip())

    return JSONResponse({
        "needs_setup": _needs_setup,
        # ★ THIS ENDPOINT IS UNAUTHENTICATED BY DESIGN. Exactly the six public
        # branding keys go here and nothing else — never a secret, never an
        # upload path, never anything org-scoped. Always fully populated, so no
        # consumer has to handle a missing key.
        "branding": _branding.model_dump(),
        "google_oauth": {
            "enabled": _google.enabled,
            "configured": _google_configured,
        },
        "auth": {
            "mode": _auth_mode
        },
        "oidc_providers": [
            {
                "name": p.name,
                "enabled": p.enabled,
                "label": getattr(p, "label", None) or p.name,
                "configured": _oidc_configured(p),
            } for p in _oidc_providers or []
        ],
        "features": {
            "allow_uninvited_signups": settings.dash_config.features.allow_uninvited_signups,
            "allow_multiple_organizations": settings.dash_config.features.allow_multiple_organizations,
            "verify_emails": settings.dash_config.features.verify_emails,
            "instruction_improve": settings.instruction_improve,
            "per_user_instructions": settings.per_user_instructions,
            "per_user_table_select": settings.hybrid_per_user_table_select,
            "learn_progress": settings.hybrid_learn_progress,
            "app_analytics": settings.hybrid_app_analytics,
            "local_compute": settings.hybrid_local_compute,
            "local_runtime": settings.hybrid_local_runtime,
            "local_folder_attach": settings.hybrid_local_folder_attach,
        },
        "deployment": {
            "type": settings.dash_config.deployment.type if hasattr(settings.dash_config, 'deployment') else "development",
        },
        "base_url": settings.dash_config.base_url,
        "intercom": {
            "enabled": settings.dash_config.intercom.enabled and not is_testing,
        },
        "telemetry": {
            "enabled": settings.dash_config.telemetry.enabled and not is_testing,
        },
        "smtp_enabled": settings.dash_config.smtp_settings is not None,
        "version": settings.PROJECT_VERSION,
        "environment": settings.ENVIRONMENT,
        "i18n": {
            "default_locale": settings.dash_config.i18n.default_locale,
            "enabled_locales": settings.dash_config.i18n.enabled_locales,
            "fallback_locale": settings.dash_config.i18n.fallback_locale,
        },
    })


@router.get("/config/i18n", tags=["settings"])
async def get_i18n_config(request: Request):
    """Public i18n config: available locales and effective locale for this request.

    When an org header is present and valid, returns the org-overridden locale;
    otherwise returns the system default. X-Locale header (if in enabled list)
    takes highest priority.
    """
    i18n = settings.dash_config.i18n
    current_locale = await get_current_locale(request)

    org_locale = None
    org_id = request.headers.get("X-Organization-Id")
    if org_id:
        try:
            from app.dependencies import get_async_session
            from sqlalchemy import select
            async for db in get_async_session():
                org = (await db.execute(select(Organization).filter(Organization.id == org_id))).scalar_one_or_none()
                if org is not None:
                    org_locale = _locale_from_org(org)
                break
        except Exception:
            org_locale = None

    override = request.headers.get("X-Locale")
    if override and override in i18n.enabled_locales:
        current_locale = override
    elif org_locale:
        current_locale = org_locale

    return JSONResponse({
        "default_locale": i18n.default_locale,
        "enabled_locales": i18n.enabled_locales,
        "fallback_locale": i18n.fallback_locale,
        "current_locale": current_locale,
        "org_locale": org_locale,
    })
