"""Instance-wide feature switches a super admin can flip without a redeploy.

The `features` block on ``GET /api/settings`` is served from environment
variables read once at import (``settings.hybrid_*``). That makes every switch a
deploy — and worse, an *invisible* one: a feature can ship complete, sit in the
image for eleven releases, and never surface because nobody set a variable
nobody knew about. Measured 2026-08-03 on a live deployment, where the App
Analytics page was fully built, fully routed, and unreachable.

This module gives those switches a second, higher-authority home:
``instance_settings.config["features"]`` — the same singleton the SSO config
already uses. No migration; the row and its JSON column exist.

★Three states, not two
----------------------
    stored True   → on,  because a super admin said so
    stored False  → off, because a super admin said so
    not stored    → whatever the environment default says

The third is what makes the env variable still mean something. Collapsing it to
a bare boolean would silently freeze every deployment at whatever the default
happened to be on the day this shipped, and an operator setting the env var
would find it ignored with no way to tell why. ``source`` is returned alongside
the value for exactly that reason: the UI can say "using the default" rather
than implying someone chose it.

★Instance-wide, so the gate is `is_superuser`
---------------------------------------------
Not ``manage_settings``. An organization admin administers their organization; a
switch here changes the product for every organization on the deployment. Those
are different powers and the codebase already distinguishes them —
``is_superuser`` is described as instance-wide at routes/user_password.py:92.

★General by construction, one switch exposed
--------------------------------------------
The registry below is a dict, and adding a switch is a line in it. Only
``app_analytics`` is wired to a UI today. The other seven `hybrid_*` flags are
listed as UNEXPOSED on purpose: several of them gate half-finished work, and
handing a super admin a switch for a feature that is not ready is worse than no
switch at all. Promote one by moving it up when its feature is done.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.instance_settings import InstanceSettings
from app.settings.config import settings

logger = logging.getLogger(__name__)

# The key under which every switch lives on the singleton's JSON config.
_FEATURES_KEY = "features"


# name → the `settings` attribute holding its environment default.
# Only names in here can be read or written; anything else is rejected rather
# than quietly stored, so a typo in a PUT cannot create a switch that nothing
# reads and that looks, in the database, exactly like a real one.
TOGGLEABLE: Dict[str, str] = {
    "app_analytics": "hybrid_app_analytics",
}

# Deliberately NOT toggleable yet. Kept here so the next person can see that
# their omission is a decision rather than an oversight, and can promote one by
# moving its line into TOGGLEABLE above.
#
#   per_user_table_select  hybrid_per_user_table_select
#   learn_progress         hybrid_learn_progress
#   local_compute          hybrid_local_compute
#   local_runtime          hybrid_local_runtime
#   local_folder_attach    hybrid_local_folder_attach
#   instruction_improve    instruction_improve
#   per_user_instructions  per_user_instructions


def env_default(name: str) -> bool:
    """The environment-derived default for one switch."""
    attr = TOGGLEABLE.get(name)
    if attr is None:
        return False
    return bool(getattr(settings, attr, False))


async def _stored(db: AsyncSession) -> Dict[str, Any]:
    inst = await InstanceSettings.get_or_create(db)
    block = (inst.config or {}).get(_FEATURES_KEY)
    return dict(block) if isinstance(block, dict) else {}


async def resolve(db: AsyncSession, name: str) -> bool:
    """Whether one switch is on, honouring the stored override.

    Never raises: a switch whose value cannot be read falls back to the
    environment default. A settings lookup must not be able to take down the
    endpoint that reports settings.
    """
    if name not in TOGGLEABLE:
        return False
    try:
        block = await _stored(db)
        value = block.get(name)
        if isinstance(value, bool):
            return value
    except Exception as e:  # noqa: BLE001
        logger.warning("instance_features.resolve(%s) fell back to default: %s", name, e)
    return env_default(name)


async def resolve_with_source(db: AsyncSession, name: str) -> Tuple[bool, str]:
    """``(value, "db" | "default")`` — what is in effect, and who decided it."""
    if name not in TOGGLEABLE:
        return False, "default"
    try:
        block = await _stored(db)
        value = block.get(name)
        if isinstance(value, bool):
            return value, "db"
    except Exception as e:  # noqa: BLE001
        logger.warning("instance_features.resolve_with_source(%s): %s", name, e)
    return env_default(name), "default"


async def read_all(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    """Every toggleable switch: ``{name: {value, source, default}}``.

    ``default`` is returned as well as ``value`` so the UI can offer "reset to
    the default" without a second round trip, and so an operator can see what
    the environment would say if the override were removed.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name in TOGGLEABLE:
        value, source = await resolve_with_source(db, name)
        out[name] = {"value": value, "source": source, "default": env_default(name)}
    return out


async def set_feature(db: AsyncSession, name: str, value: Optional[bool]) -> Dict[str, Any]:
    """Store an override, or clear it back to the environment default.

    ``value=None`` REMOVES the stored key rather than writing False. "Off" and
    "not chosen" are different states — writing False for a reset would pin the
    switch off forever and make the env default unreachable, which is the bug
    this tri-state exists to avoid.
    """
    if name not in TOGGLEABLE:
        raise ValueError(f"unknown feature: {name}")

    inst = await InstanceSettings.get_or_create(db)
    config = dict(inst.config or {})
    block = dict(config.get(_FEATURES_KEY) or {})

    if value is None:
        block.pop(name, None)
    else:
        block[name] = bool(value)

    config[_FEATURES_KEY] = block
    inst.config = config
    # The column is `json`, and SQLAlchemy does not see an in-place mutation of
    # a JSON value. Reassigning above is necessary but not sufficient when the
    # nested dict came from the loaded object — flag it explicitly. This exact
    # failure has already cost this codebase a debugging session on
    # connection_sync_progress.detail.
    flag_modified(inst, "config")
    await db.commit()

    resolved, source = await resolve_with_source(db, name)
    return {"value": resolved, "source": source, "default": env_default(name)}
