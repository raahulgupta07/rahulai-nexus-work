"""Read the new environment names, accept the old ones, say when you did.

The product's own prefix is being renamed from the upstream project's initials
(``BOW_``, for Bag of Words) to this product's (``DASH_``). Doing that as a
straight swap would be unsafe, because the failure mode is silent and permanent:

  ``config.py`` generates a brand-new encryption key when it cannot find one.
  It does not warn, and it does not stop. On an existing installation that means
  every stored connector password, Microsoft token, directory bind and
  single-sign-on secret becomes permanently unreadable, while the application
  starts up looking perfectly healthy.

So a machine that still carries the old names must keep working. This module
resolves either spelling, in both directions:

  * the new name wins when both are set;
  * an old name is accepted and logged, so a half-migrated machine is visible
    rather than merely lucky;
  * a value found under either name is written back under BOTH, so anything
    later in start-up that reads ``os.environ`` directly finds it.

The direction matters as much as the fallback. The configuration file may still
say ``${BOW_ENCRYPTION_KEY}`` while the environment supplies ``DASH_...``, or
the reverse, and both must resolve.

★This is deliberately shipped and proven BEFORE any name is renamed. Renaming
first and adding compatibility afterwards inverts the risk: there would be a
window in which one stale file silently destroys every credential on that host.

Once every environment is confirmed migrated, ``LEGACY_PREFIX`` can be dropped
and this module becomes a plain ``os.environ.get``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

PREFIX = "DASH_"
LEGACY_PREFIX = "BOW_"

# Names that were seen used at runtime, kept only so the warning can say which
# ones actually mattered. Resolution is by prefix and needs no list.
KNOWN_LIVE = (
    "ENCRYPTION_KEY",
    "DATABASE_URL",
    "CONFIG_PATH",
    "LICENSE_KEY",
)

_warned: set = set()


def counterpart(name: str) -> Optional[str]:
    """The same setting spelled the other way, or None if it is neither."""
    if name.startswith(PREFIX):
        return LEGACY_PREFIX + name[len(PREFIX):]
    if name.startswith(LEGACY_PREFIX):
        return PREFIX + name[len(LEGACY_PREFIX):]
    return None


def env_get(name: str, default=None):
    """Resolve ``name`` under either prefix. The new spelling wins.

    Whichever spelling supplies the value, both are written back, so code that
    reads ``os.environ`` directly later in start-up sees it under the name it
    expects.
    """
    other = counterpart(name)

    # Prefer the NEW name regardless of which spelling was asked for, so that
    # precedence does not depend on the caller.
    preferred = name if name.startswith(PREFIX) else (other or name)
    legacy = other if preferred is name else name

    value = os.environ.get(preferred)
    used_legacy = False
    if value is None and legacy:
        value = os.environ.get(legacy)
        used_legacy = value is not None

    if value is None:
        return default

    if used_legacy and preferred not in _warned:
        _warned.add(preferred)
        log.warning(
            "environment variable %s is set under its previous name %s; "
            "rename it to %s — the old name will stop being read in a future "
            "release", preferred, legacy, preferred,
        )

    # Normalise forward AND back: anything reading os.environ directly finds it.
    for spelling in (preferred, legacy):
        if spelling and os.environ.get(spelling) is None:
            os.environ[spelling] = value

    return value


def normalize_environment() -> list:
    """Mirror every old name onto its new one, and vice versa, once at start-up.

    ★★★This, not per-call lookups, is what makes the rename safe.

    There are ~292 reads of these variables across the code base, and most are
    plain ``os.getenv("DASH_X", <default>)``. A helper only protects the call
    sites that remember to use it — and the ones that forget do not fail loudly,
    they take their DEFAULT. That is how a renamed ``DASH_DATABASE_URL`` fell
    back to a local SQLite file: the application started, migrated the empty
    file, and served an installation with no data in it, reporting healthy
    throughout. The real database was untouched and simply not being used.

    Mirroring the whole environment before anything reads it means every
    existing call site keeps working with no edit, including ones nobody has
    found yet.

    Returns the list of names that were only present under the old spelling, so
    start-up can say so.
    """
    legacy_used = []
    for key in list(os.environ):
        other = counterpart(key)
        if not other or os.environ.get(other) is not None:
            continue
        os.environ[other] = os.environ[key]
        if key.startswith(LEGACY_PREFIX):
            legacy_used.append(key)
    if legacy_used:
        log.warning(
            "these environment variables are still set under their previous "
            "names and were mirrored onto the new ones: %s",
            ", ".join(sorted(legacy_used)),
        )
    return sorted(legacy_used)


def is_legacy_in_use() -> bool:
    """True when any value was resolved from an old name this process."""
    return bool(_warned)


def legacy_names_in_use() -> list:
    return sorted(_warned)
