"""One reader for on/off feature switches stored in org settings.

Why this exists: ``FeatureConfig.value`` is ``Optional[Any]`` and
``OrganizationSettingsService.update_settings`` applies no coercion to boolean
keys, so ``PUT /organization_settings`` with ``{"enable_web_fetch": {"value":
"off"}}`` stores the STRING ``"off"`` verbatim. Every gate that read the switch
as ``bool(cfg.value)`` then saw ``True``, because ``bool("off") is True`` — an
admin who spelled "off" got the capability switched ON. This codebase has
shipped that bug once already, on a three-state access setting read with
``if not feature.value:``.

``access_state()`` in ``schemas/organization_settings_schema`` already
normalises these spellings, but it answers a three-state question
(on / coming_soon / off). These switches are binary, so they get their own
reader with the same discipline: **anything unreadable or unrecognised as
"on" leaves the capability off** — an unparseable setting is not consent.

Use ``setting_enabled(org_settings, "enable_web_fetch")`` at a gate site, or
``flag_enabled(cfg)`` when the config object is already in hand.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Spellings an admin actually types into a text field. Compared after
# strip/lower/underscore-normalisation.
FALSE_STRINGS = frozenset({
    "off", "false", "no", "disabled", "0",
    # A three-state access setting parked mid-rollout is not released. Kept in
    # step with ACCESS_COMING_SOON so the two readers cannot disagree.
    "coming_soon", "comingsoon", "soon",
})
TRUE_STRINGS = frozenset({"on", "true", "yes", "enabled", "1"})

# (setting name, raw value) pairs already reported. A gate can run on every
# turn of every run; the operator needs to see the misconfiguration once, not
# once per LLM call.
_warned: set = set()


def _warn_once(key: Optional[str], value: Any) -> None:
    marker = (key, repr(value))
    if marker in _warned:
        return
    _warned.add(marker)
    logger.warning(
        "feature flag %s has unrecognised value %r; treating as ENABLED. "
        "Use true/false (or on/off) — a misspelled value here decides whether "
        "a capability exists.",
        key or "<unnamed>",
        value,
    )


def flag_enabled(config: Any, key: Optional[str] = None, default: bool = False) -> bool:
    """Whether a stored on/off switch is on.

    Accepts a ``FeatureConfig``, anything else carrying ``.value``, a plain
    dict as stored in the org's JSON config, or a bare value.

      - ``None`` / no stored config -> ``default`` (False unless the switch
        genuinely ships on, e.g. ``allow_llm_see_data``)
      - real booleans               -> themselves
      - ``""``, ``0``               -> False
      - "off"/"false"/"no"/"disabled"/"0" (case-insensitive) -> False
      - "on"/"true"/"yes"/"enabled"/"1"                      -> True
      - anything else truthy        -> True, logged once at WARNING

    ``key`` is only used to name the setting in that warning.
    """
    if config is None:
        return default

    value = config
    if isinstance(value, dict):
        value = value.get("value")
    elif not isinstance(value, (bool, int, float, str)):
        # FeatureConfig and the various stand-ins for it.
        value = getattr(value, "value", None)

    if value is None:
        # Stored but unset. Same meaning as no stored config at all.
        return default
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        v = value.strip().lower().replace("-", "_").replace(" ", "_")
        if v == "" or v in FALSE_STRINGS:
            return False
        if v in TRUE_STRINGS:
            return True
        _warn_once(key, value)
        return True

    return bool(value)


def setting_enabled(organization_settings: Any, key: str, default: bool = False) -> bool:
    """Read ``key`` off an org settings object and decide the gate.

    Falls back to ``default`` when there is no settings object and when the
    read raises — an unreadable setting is not permission to enable anything,
    so ``default`` should stay False for every capability the org has to opt
    into. Pass True only for a switch that genuinely ships on.
    """
    if organization_settings is None:
        return default
    try:
        cfg = organization_settings.get_config(key)
    except Exception:
        return default
    return flag_enabled(cfg, key, default)
