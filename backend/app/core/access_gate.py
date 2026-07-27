"""Server-side enforcement for the three-state member-access settings.

Hiding a tab is not access control. A member who cannot see the API Keys panel
can still POST to ``/api/api_keys``, so every feature an admin can switch off in
Settings → Access is ALSO refused here, at the route. The UI decides what you
see; this decides what you can do.

Why a helper instead of `if setting.value:` at each call site
-------------------------------------------------------------
The settings are strings now (``on`` / ``coming_soon`` / ``off``), and the
string ``"off"`` is TRUTHY in Python. Every truthiness check written against
the old boolean would therefore silently allow access on exactly the state
meant to deny it — the MCP gate had this shape before this module existed.
Routing every read through :func:`access_state` makes that mistake impossible.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from app.models.organization import Organization
from app.schemas.organization_settings_schema import (
    ACCESS_COMING_SOON,
    ACCESS_OFF,
    ACCESS_ON,
    access_state,
)

logger = logging.getLogger(__name__)

# Shown to a member who reaches a gated endpoint directly. Deliberately says
# who can change it, so the message is actionable rather than a dead end.
_COMING_SOON_DETAIL = "{label} is not available yet. Ask an administrator to enable it."
_OFF_DETAIL = "{label} is not enabled for this organization."


def get_access_state(organization: Optional[Organization], key: str) -> str:
    """Current state of one access setting, defaulting closed.

    An organisation with no settings row, an unreadable config, or an
    unrecognised value resolves to ``off``. Failing closed matters more than
    convenience here: the alternative is handing out access because a lookup
    broke.
    """
    try:
        settings = getattr(organization, "settings", None)
        if not settings:
            return ACCESS_OFF
        return access_state(settings.get_config(key))
    except Exception:  # noqa: BLE001 — a broken read must not grant access
        logger.warning("access gate: could not read %r; treating as off", key, exc_info=True)
        return ACCESS_OFF


def is_access_allowed(organization: Optional[Organization], key: str) -> bool:
    return get_access_state(organization, key) == ACCESS_ON


def require_access(organization: Optional[Organization], key: str, label: str) -> None:
    """Raise 403 unless the feature is fully released to members.

    ``coming_soon`` and ``off`` both refuse; only the wording differs, so a
    member who somehow reaches the endpoint is told which situation they are in.
    """
    state = get_access_state(organization, key)
    if state == ACCESS_ON:
        return
    detail = (_COMING_SOON_DETAIL if state == ACCESS_COMING_SOON else _OFF_DETAIL).format(label=label)
    raise HTTPException(status_code=403, detail=detail)
