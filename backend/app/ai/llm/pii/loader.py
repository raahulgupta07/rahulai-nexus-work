"""Loads and caches a per-organization :class:`PiiRedactor`.

The LLM chokepoint calls this to obtain the redactor for the model's
organization. Two hard guarantees live here:

* **Enterprise gate.** If the instance is not licensed for ``pii_protection``,
  this always returns ``None`` — no config value can turn redaction on in a
  community build.
* **Cheap steady state.** The compiled ruleset is cached per org with a short
  TTL so redaction doesn't add a DB round-trip to every LLM call. Settings
  writes call :func:`invalidate` for instant reflection.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy.future import select

from app.ee.license import has_feature
from .redactor import PiiRedactor, build_redactor

logger = logging.getLogger(__name__)

# Distinguishes "never successfully read" from "read, and the answer was None".
# `None` is a real policy state here — it means the org configured nothing — so
# it cannot double as the not-known marker.
_UNKNOWN = object()

# org_id -> (expires_at_monotonic, redactor_or_None)
_CACHE: Dict[str, Tuple[float, Optional[PiiRedactor]]] = {}
_CACHE_TTL_SECONDS = 10.0

# ★★★org_id -> the last value a SUCCESSFUL read produced (which may legitimately
# be None: "this org has no PII policy"). Not a cache — a memory of what the org
# actually configured, kept so a failed read cannot be mistaken for "no policy".
#
# Before this existed, any exception here set `redactor = None`, and None is the
# same value that means "nothing configured". A database outage was therefore
# indistinguishable from an org that had switched redaction off, and the
# unredacted prompt went to the third-party model with one WARNING line to show
# for it. Measured in production 2026-08-04: 9 such loads during a ~5h Postgres
# auth failure, while `llm._apply_pii` returned every prompt untouched.
_LAST_GOOD: Dict[str, Optional[PiiRedactor]] = {}

# How long a FAILED read is allowed to keep serving the remembered value before
# trying the database again. Short, because the point is to survive a blip, not
# to run indefinitely on a stale policy — but non-zero, so an outage does not
# turn every LLM call into its own failing connection attempt.
_FAILURE_BACKOFF_SECONDS = 5.0


def invalidate(organization_id: Optional[str] = None) -> None:
    """Drop cached redactors. Called after a settings write so a toggle takes
    effect immediately instead of waiting out the TTL.

    ★`_LAST_GOOD` is deliberately NOT cleared. It is the fallback for a failed
    read, and dropping it here would mean a settings write immediately followed
    by a database problem lands on "no policy" — the exact hole this closes. The
    cost is that an admin who turns redaction OFF and then loses the database
    keeps redacting until the next successful read. That is over-redaction, not
    under-redaction, and it is the direction to fail in.
    """
    if organization_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(organization_id), None)


async def load_redactor_for_org(
    organization_id: Optional[str],
    session_maker: Optional[Callable[[], "object"]],
) -> Optional[PiiRedactor]:
    """Return the redactor for ``organization_id`` or None.

    None means "do not redact" — because the feature is unlicensed, disabled,
    has no active rules, or the org/session is unavailable.

    ★It no longer also means "the read blew up". A failed read falls back to the
    last policy successfully loaded for this org and logs at ERROR; only a
    failure with no previously known policy still returns None, and that is
    stated in the log rather than passing as a normal answer.
    """
    # Enterprise gate first — never touches the DB on community instances.
    if not has_feature("pii_protection"):
        return None
    if not organization_id or session_maker is None:
        return None

    org_id = str(organization_id)
    now = time.monotonic()
    cached = _CACHE.get(org_id)
    if cached is not None and cached[0] > now:
        return cached[1]

    try:
        from app.models.organization_settings import OrganizationSettings

        async with session_maker() as session:
            result = await session.execute(
                select(OrganizationSettings).filter(
                    OrganizationSettings.organization_id == org_id
                )
            )
            settings = result.scalar_one_or_none()
            redactor: Optional[PiiRedactor] = None
            if settings and isinstance(settings.config, dict):
                pii_config = settings.config.get("pii_protection")
                redactor = build_redactor(pii_config)
    except Exception as exc:
        # ★★★A failed read is NOT "no policy". Fall back to what this org was
        # last known to have configured, so a transient database problem cannot
        # silently switch redaction off — including `block` mode, whose config
        # lives in the same row, so the org that chose the strictest setting was
        # previously the one handed the loosest.
        remembered = _LAST_GOOD.get(org_id, _UNKNOWN)
        if remembered is not _UNKNOWN:
            # ★ERROR, not WARNING. This is a privacy control running on a
            # remembered policy instead of the stored one; it needs to be
            # findable in a log, not one line among thousands of INFO.
            logger.error(
                "PII redactor load failed for org %s (%s: %s) — continuing on the "
                "last known policy (active=%s). Redaction is NOT reading current "
                "settings until the database recovers.",
                org_id, type(exc).__name__, exc,
                bool(remembered is not None and getattr(remembered, "active", False)),
            )
            redactor = remembered
        else:
            # ★★★Cold start during an outage: this process has never read this
            # org's settings, so there is nothing to fall back TO. We cannot
            # invent a policy, and refusing every prompt would turn a degraded
            # database into a total outage for orgs that have no policy at all —
            # they reach this same code path. So inference proceeds UNREDACTED,
            # and says so at ERROR. This is the one residual fail-open case.
            logger.error(
                "PII redactor load failed for org %s (%s: %s) and no previously "
                "loaded policy is known — prompts for this org are going to the "
                "model UNREDACTED until the database recovers.",
                org_id, type(exc).__name__, exc,
            )
            redactor = None
        # A shorter window than a good read gets: retry sooner, but not on
        # every single call while the database is down.
        _CACHE[org_id] = (now + _FAILURE_BACKOFF_SECONDS, redactor)
        return redactor

    _LAST_GOOD[org_id] = redactor
    _CACHE[org_id] = (now + _CACHE_TTL_SECONDS, redactor)
    return redactor
