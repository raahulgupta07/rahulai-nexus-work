"""Instance-wide product branding (name, tagline, logo, accent colour).

WHY THIS IS NOT AN ORGANIZATION SETTING
---------------------------------------
The sign-in page renders before anyone has logged in and therefore before any
organization has been selected. Branding stored on ``OrganizationSettings``
would be unreachable on the one screen where it matters most — the first thing
a customer ever sees. So it lives on the ``InstanceSettings`` singleton, for
exactly the same reason SSO provider configuration does.

THE STORED SHAPE IS A CONTRACT
------------------------------
``instance_settings.config["branding"]`` holds these six keys and no others,
and the public ``/api/settings`` feed republishes them as a top-level
``branding`` object. Consumers — the login page, the app shell, the browser
tab — must never have to handle a missing key, so every read fills defaults.

THE DEFAULTS ARE TODAY'S VISIBLE STRINGS
----------------------------------------
An installation that never opens the branding screen must look byte-identical
to one that predates this feature. That is the whole test for whether the
defaults below are right: change one and you have silently rebranded every
existing customer.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Defaults ───────────────────────────────────────────────────────────────
# ★ These must equal the strings the product shows today. See module docstring.
DEFAULT_PRODUCT_NAME = "CityAgent Insights"
DEFAULT_TAGLINE = "Your AI analyst for data"
DEFAULT_FOOTER_TEXT = ""
DEFAULT_ACCENT_COLOR = "#2563eb"

#: The exact key set of the stored block and of the public feed's `branding`
#: object. Anything not in here does not belong in either place — the feed is
#: unauthenticated, so a stray key is a disclosure, not a convenience.
BRANDING_KEYS = (
    "product_name",
    "tagline",
    "footer_text",
    "accent_color",
    "logo_key",
    "favicon_key",
)

DEFAULT_BRANDING: dict = {
    "product_name": DEFAULT_PRODUCT_NAME,
    "tagline": DEFAULT_TAGLINE,
    "footer_text": DEFAULT_FOOTER_TEXT,
    "accent_color": DEFAULT_ACCENT_COLOR,
    "logo_key": None,
    "favicon_key": None,
}

# Six-digit hex only. Three-digit shorthand, `rgb()` and named colours are all
# valid CSS, but the frontend derives hover/border shades arithmetically from
# this value, so one canonical form keeps that derivation total.
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Upload keys are generated server-side (see BrandingService) and only ever
# read back out of the JSON blob. Validated on the way in anyway: the value is
# interpolated into a URL the browser fetches, and the file route resolves it
# against a directory.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,200}$")

MAX_PRODUCT_NAME = 120
MAX_TAGLINE = 200
MAX_FOOTER_TEXT = 300


def _validate_hex(value: str, field: str) -> str:
    v = (value or "").strip()
    if not _HEX_COLOR_RE.match(v):
        raise ValueError(
            f"{field} must be a 6-digit hex colour like #2563eb (got {value!r})."
        )
    return v.lower()


def _validate_key(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if not _SAFE_KEY_RE.match(v):
        raise ValueError(f"{field} is not a valid upload key.")
    return v


class BrandingSchema(BaseModel):
    """The full, always-populated branding block. This is what a read returns."""

    product_name: str = Field(default=DEFAULT_PRODUCT_NAME, max_length=MAX_PRODUCT_NAME)
    tagline: str = Field(default=DEFAULT_TAGLINE, max_length=MAX_TAGLINE)
    footer_text: str = Field(default=DEFAULT_FOOTER_TEXT, max_length=MAX_FOOTER_TEXT)
    accent_color: str = Field(default=DEFAULT_ACCENT_COLOR)
    logo_key: Optional[str] = None
    favicon_key: Optional[str] = None

    @field_validator("product_name", "tagline")
    @classmethod
    def _non_empty(cls, v: str, info) -> str:
        # A blank product name would render as an empty page title and an empty
        # sign-in heading — visibly broken, not "unbranded". Refuse it.
        stripped = (v or "").strip()
        if not stripped:
            raise ValueError(f"{info.field_name} cannot be empty.")
        return stripped

    @field_validator("footer_text")
    @classmethod
    def _strip_footer(cls, v: str) -> str:
        # Empty IS meaningful here: it is the default, and it means "no footer".
        return (v or "").strip()

    @field_validator("accent_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _validate_hex(v, "accent_color")

    @field_validator("logo_key", "favicon_key")
    @classmethod
    def _key(cls, v: Optional[str], info) -> Optional[str]:
        return _validate_key(v, info.field_name)


class BrandingUpdate(BaseModel):
    """A PARTIAL update. Every field is optional and ``None`` means "leave it".

    ★ This is why the service merges rather than replaces: a PUT carrying only
    ``product_name`` must not blank the tagline. ``footer_text: ""`` is a real
    value (clear the footer), which is exactly why the "unset" signal has to be
    ``None`` and not falsiness.

    ``logo_key``/``favicon_key`` are settable so an admin can clear an uploaded
    image (send ``null``); the keys themselves are minted by the upload
    endpoints, never typed in by hand.
    """

    product_name: Optional[str] = Field(default=None, max_length=MAX_PRODUCT_NAME)
    tagline: Optional[str] = Field(default=None, max_length=MAX_TAGLINE)
    footer_text: Optional[str] = Field(default=None, max_length=MAX_FOOTER_TEXT)
    accent_color: Optional[str] = None
    logo_key: Optional[str] = None
    favicon_key: Optional[str] = None

    @field_validator("product_name", "tagline")
    @classmethod
    def _non_empty_if_given(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError(f"{info.field_name} cannot be empty.")
        return stripped

    @field_validator("footer_text")
    @classmethod
    def _strip_footer(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else v.strip()

    @field_validator("accent_color")
    @classmethod
    def _hex_if_given(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_hex(v, "accent_color")

    @field_validator("logo_key", "favicon_key")
    @classmethod
    def _key_if_given(cls, v: Optional[str], info) -> Optional[str]:
        return _validate_key(v, info.field_name)


def merge_branding(stored: Optional[dict], update: BrandingUpdate) -> dict:
    """Fold a partial update onto the stored block, filling defaults for gaps.

    Returns a plain dict carrying exactly ``BRANDING_KEYS``. Unknown keys in
    ``stored`` are dropped: the block is a contract, and the public feed
    republishes it.
    """
    merged = dict(DEFAULT_BRANDING)
    for key in BRANDING_KEYS:
        if stored and key in stored and stored[key] is not None:
            merged[key] = stored[key]

    supplied = update.model_dump(exclude_unset=True)
    for key in BRANDING_KEYS:
        if key in supplied and supplied[key] is not None:
            merged[key] = supplied[key]
        elif key in supplied and supplied[key] is None and key in ("logo_key", "favicon_key"):
            # An explicit null on an image key means "remove the image". For
            # the text fields null means "not supplied" (see class docstring),
            # so only the two nullable keys honour it.
            merged[key] = None

    # Round-trip through the read schema so a hand-written stored blob (or a
    # value that predates a tightened rule) cannot escape validation.
    return BrandingSchema(**merged).model_dump()


def branding_from_stored(stored: Optional[dict]) -> BrandingSchema:
    """Read path: stored block (possibly absent, partial or corrupt) -> full schema.

    ★ Never raises. Branding is read by the unauthenticated settings feed that
    the login page depends on; a bad stored value must degrade to the defaults,
    not take sign-in down.
    """
    merged = dict(DEFAULT_BRANDING)
    if isinstance(stored, dict):
        for key in BRANDING_KEYS:
            value = stored.get(key)
            if value is not None:
                merged[key] = value
    try:
        return BrandingSchema(**merged)
    except Exception:  # noqa: BLE001
        return BrandingSchema()
