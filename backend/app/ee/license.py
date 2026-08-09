# Enterprise License Validation
# Licensed under the Business Source License 1.1
# See ENTERPRISE_LICENSE for details

import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Optional, List
from pydantic import BaseModel
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Features included in each tier
# When adding new enterprise features, add them here - no license regeneration needed
TIER_FEATURES = {
    "team": [
        "audit_logs",
    ],
    "enterprise": [
        "audit_logs",
        "step_retention_config",
        "scim",
        "custom_roles",
        "ldap",
        "domain_signup",
        "usage_limits",
        "scheduled_reindex",
        "cost_dashboard",
        "llm_access_control",
        "connection_rate_limit",
        "model_routing",
        "llm_fallback",
        "pii_protection",
        "rls",
    ],
}

# Data sources that require an enterprise license
ENTERPRISE_DATASOURCES = ["powerbi", "qvd", "sybase", "tableau", "zabbix", "splunk"]

class LicenseInfo(BaseModel):
    """Information about the current license"""
    licensed: bool = False
    tier: str = "community"
    org_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    features: List[str] = []
    license_id: Optional[str] = None
    # Per-organization quotas. -1 means "no limit" (the default when the license
    # omits them or the instance is unlicensed/expired). A value >= 0 is a hard cap
    # enforced on member invites and data source ("agent") creation.
    max_users: int = -1
    max_agents: int = -1


# An override for the standing enterprise grant. Read by get_license_info().
#
# ★NOTHING IN PRODUCTION WRITES THESE. The only assignment outside tests is
# clear_license_cache() setting them back to None/False, so on a running
# instance get_license_info() always falls through to the grant. They exist so
# the test suite can drive a scenario the product itself never enters — a seat
# cap, a narrowed feature list, community mode — and thereby keep the
# enforcement code (require_enterprise, is_datasource_allowed, the quota checks)
# genuinely covered.
#
# ★The name is a leftover: this was a CACHE, holding the decoded result of an
# RS256-signed licence key. That validator was deleted in 0.0.526.1 along with
# the key-parsing path, because this fork is permanently licensed and a stray or
# expired DASH_LICENSE_KEY must never be able to lock an installation. The
# globals kept their names only because eight test modules already write them.
_cached_license: Optional[LicenseInfo] = None
_cache_initialized: bool = False


def _apply_live_expiry(info: LicenseInfo) -> LicenseInfo:
    """
    Re-evaluate a cached LicenseInfo against the current time.

    Signature verification happens once and is cached, but a license can lapse while the
    process keeps running. If the cached info carries an expiry that is now in the past,
    downgrade it to the "expired" state on the fly — so enforcement no longer waits for a
    pod/container restart. Community/invalid licenses (no expiry) and already-expired ones
    pass through unchanged.
    """
    if (
        info.expires_at is not None
        and info.expires_at < datetime.now(timezone.utc)
        and (info.licensed or info.tier != "expired")
    ):
        return LicenseInfo(
            licensed=False,
            tier="expired",
            org_name=info.org_name,
            expires_at=info.expires_at,
            license_id=info.license_id,
        )
    return info


def get_license_info(force_refresh: bool = False) -> LicenseInfo:
    """
    Get current license information.

    CityAgent Insights ships as a self-contained product: every enterprise
    capability is unlocked by default, with no external license server, no
    signed key, and no expiry. This returns a permanent enterprise grant with
    all tier features enabled and unlimited seat/agent quotas, so all gates
    (has_feature, is_datasource_allowed, get_max_*, require_enterprise) pass.

    ★An explicitly injected ``_cached_license`` wins over that grant, and this
    is load-bearing for the test suite rather than a licensing feature. Tests
    set the global directly to drive a scenario — a seat cap
    (``test_seat_cap_autoprovision``), a restricted feature list
    (``tests/e2e/conftest.py``), community mode. Returning the grant
    unconditionally silently DISCARDED all of it, so seat-cap enforcement in
    the SCIM/OIDC/LDAP auto-provisioning paths had no coverage at all, and a
    fixture that believed it was granting 8 features was really granting 15.

    ★Production behaviour is unchanged, because production never writes this
    global. The only non-test assignment in the tree is ``clear_license_cache()``
    setting it to ``None`` — so outside tests the branch below cannot be taken
    and the grant is returned exactly as before. Verify with:
    ``grep -rn '_cached_license' app`` — the sole hits are this module's own.
    """
    if _cached_license is not None and not force_refresh:
        return _apply_live_expiry(_cached_license)

    return LicenseInfo(
        licensed=True,
        tier="enterprise",
        org_name="CityAgent Insights",
        expires_at=None,
        features=list(TIER_FEATURES["enterprise"]),
        license_id="cityagent-insights",
        max_users=-1,
        max_agents=-1,
    )


def is_enterprise_licensed() -> bool:
    """Check if the instance has an active enterprise license"""
    return get_license_info().licensed


def has_feature(feature: str) -> bool:
    """
    Check if a specific enterprise feature is enabled.

    Logic:
    - If license has explicit features list → use that (custom deals)
    - Otherwise → use tier defaults from TIER_FEATURES

    This allows adding new features to tiers without regenerating licenses.
    """
    license_info = get_license_info()
    if not license_info.licensed:
        return False

    # If explicit features in license, use those (for custom/restricted licenses)
    if license_info.features:
        return feature in license_info.features

    # Otherwise, use tier defaults
    tier_features = TIER_FEATURES.get(license_info.tier, [])
    return feature in tier_features


def is_datasource_allowed(ds_type: str) -> bool:
    """
    Check if a data source type is allowed under current license.

    Logic:
    - Non-enterprise data sources → always allowed
    - Enterprise data sources → require enterprise license
    - If license has explicit ds_ features → check that list
    - Otherwise enterprise tier → all enterprise DS allowed
    """
    if ds_type not in ENTERPRISE_DATASOURCES:
        return True

    license_info = get_license_info()
    if not license_info.licensed:
        return False

    # If license has explicit ds_ features, check that (for custom/restricted licenses)
    if license_info.features and any(f.startswith("ds_") for f in license_info.features):
        return f"ds_{ds_type}" in license_info.features

    # Only enterprise tier gets access to enterprise data sources
    return license_info.tier == "enterprise"


def get_max_users() -> int:
    """Max members (active + pending invites) allowed per organization.

    Returns -1 (unlimited) unless an *active* license sets an explicit cap.
    """
    return get_license_info().max_users


def get_max_agents() -> int:
    """Max data sources ("agents") allowed per organization.

    Returns -1 (unlimited) unless an *active* license sets an explicit cap.
    """
    return get_license_info().max_agents


def require_enterprise(feature: Optional[str] = None):
    """
    Decorator that requires an active enterprise license.
    Optionally checks for a specific feature.

    Usage:
    @require_enterprise()  # Requires any enterprise license
    @require_enterprise(feature="audit_logs")  # Requires audit_logs feature
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            license_info = get_license_info()

            if not license_info.licensed:
                if license_info.tier == "expired":
                    raise HTTPException(
                        status_code=402,
                        detail="Your enterprise license has expired. Please renew to access this feature."
                    )
                raise HTTPException(
                    status_code=402,
                    detail="This feature requires an enterprise license. Set DASH_LICENSE_KEY to enable."
                )

            if feature and not has_feature(feature):
                raise HTTPException(
                    status_code=402,
                    detail=f"This feature ({feature}) is not included in your license."
                )

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def clear_license_cache():
    """Clear the license cache (useful for testing or config reload)"""
    global _cached_license, _cache_initialized
    _cached_license = None
    _cache_initialized = False
