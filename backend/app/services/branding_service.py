"""Instance-wide product branding: read, partial update, and image upload.

Mirrors ``sso_config_service.py`` in shape and for the same reason — both
configure something that must be readable *before* login, so both live on the
``InstanceSettings`` singleton rather than on a per-organization settings row.

Storage is the JSON ``config`` column, under the single key ``branding``. That
is a JSON column, so a new key needs no migration.

Uploaded images reuse the mechanism the organization icon already uses: files
land in ``<cwd>/uploads/branding`` (a mounted volume, so they survive a
rebuild) and are served by ``GET /api/general/icon/{icon_key}`` in
``app/routes/branding.py``. Only the returned key is stored.
"""
from __future__ import annotations

import hashlib
import os
from io import BytesIO
from typing import Optional

from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.instance_settings import InstanceSettings
from app.schemas.branding_schema import (
    DEFAULT_PRODUCT_NAME,
    BrandingSchema,
    BrandingUpdate,
    branding_from_stored,
    merge_branding,
)

BRANDING_CONFIG_KEY = "branding"

#: Same 512 KB ceiling and same directory as the organization icon upload
#: (`OrganizationSettingsService.set_general_icon`). Deliberately identical:
#: two upload paths writing the same directory with different limits is how a
#: "why did this file work over there" bug gets written.
MAX_UPLOAD_BYTES = 512 * 1024

# Raster images are normalised to PNG so the served bytes are predictable.
# SVG and ICO are stored verbatim — SVG is not a raster format at all, and
# re-encoding an ICO throws away the multi-resolution frames that are the
# entire point of the format for a favicon.
_RASTER_TYPES = {"image/png", "image/jpeg", "image/jpg"}
_VERBATIM_TYPES = {
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}

LOGO_CONTENT_TYPES = _RASTER_TYPES | {"image/svg+xml"}
FAVICON_CONTENT_TYPES = _RASTER_TYPES | set(_VERBATIM_TYPES)

# Logos render in a header strip; favicons in a 16-32px tab. Bounds mirror the
# org icon's (512x256) for the logo and keep a favicon square.
_LOGO_BOUNDS = (512, 256)
_FAVICON_BOUNDS = (256, 256)


# ── Process-level cache for synchronous callers ────────────────────────────
#
# ★ The problem this solves: `settings.PROJECT_NAME` is a pydantic Settings
# field evaluated at import, so it cannot read the database — and several
# places that surface the product name to a real person are synchronous
# functions with no session in reach (an email subject builder, a pydantic
# field default, the OpenAPI title callback).
#
# So the name is resolved at *request* time from a value this module keeps
# fresh: every `get_branding()` refreshes it, and `get_branding()` is what the
# public `/api/settings` feed calls on every load of the login page and every
# boot of the frontend. Before the first read — and if anything at all goes
# wrong — the cache holds the packaged default, which is today's string. So
# the worst case is the behaviour that exists today.
_cached_product_name: str = DEFAULT_PRODUCT_NAME


def product_name() -> str:
    """The configured product name, for synchronous callers.

    Never raises, never blocks, never touches the database. See the note above
    for what "fresh" means here and why this is not simply a DB read.
    """
    return _cached_product_name or DEFAULT_PRODUCT_NAME


def _remember_product_name(name: Optional[str]) -> None:
    global _cached_product_name
    if name and name.strip():
        _cached_product_name = name.strip()


class BrandingService:
    """Read/write the instance-global branding block."""

    # --- read -------------------------------------------------------------
    async def get_branding(self, db: AsyncSession) -> BrandingSchema:
        """Return the full branding block, defaults filled for anything unset.

        ★ Always returns all six keys. Callers — including the unauthenticated
        settings feed — must never have to handle a missing one.
        """
        inst = await InstanceSettings.get_or_create(db)
        stored = (inst.config or {}).get(BRANDING_CONFIG_KEY)
        branding = branding_from_stored(stored)
        _remember_product_name(branding.product_name)
        return branding

    # --- write ------------------------------------------------------------
    async def update_branding(
        self,
        db: AsyncSession,
        payload: BrandingUpdate,
        current_user=None,
    ) -> BrandingSchema:
        """Merge a PARTIAL update into the stored block and persist it.

        ★ Merge, not replace. A PUT carrying only ``product_name`` leaves the
        tagline, the colour and both image keys exactly as they were.
        """
        inst = await InstanceSettings.get_or_create(db)
        cfg = dict(inst.config or {})
        merged = merge_branding(cfg.get(BRANDING_CONFIG_KEY), payload)

        cfg[BRANDING_CONFIG_KEY] = merged
        inst.config = cfg
        flag_modified(inst, "config")
        db.add(inst)
        await db.commit()
        await db.refresh(inst)

        # Best-effort audit log, matching how SsoConfigService records a change
        # to the same singleton. Never let logging fail the write.
        try:
            from app.ee.audit.service import audit_service

            await audit_service.log(
                db=db,
                organization_id=None,
                action="settings.branding_updated",
                user_id=str(current_user.id) if current_user else None,
                resource_type="instance_settings",
                resource_id=str(inst.id),
                details={"changed_keys": sorted(payload.model_dump(exclude_unset=True).keys())},
            )
        except Exception:  # noqa: BLE001
            pass

        return await self.get_branding(db)

    # --- image upload -----------------------------------------------------
    async def set_logo(
        self, db: AsyncSession, file: UploadFile, current_user=None
    ) -> BrandingSchema:
        key = await self._store_image(
            file,
            allowed=LOGO_CONTENT_TYPES,
            bounds=_LOGO_BOUNDS,
            prefix="instance-logo",
        )
        return await self.update_branding(
            db, BrandingUpdate(logo_key=key), current_user=current_user
        )

    async def set_favicon(
        self, db: AsyncSession, file: UploadFile, current_user=None
    ) -> BrandingSchema:
        key = await self._store_image(
            file,
            allowed=FAVICON_CONTENT_TYPES,
            bounds=_FAVICON_BOUNDS,
            prefix="instance-favicon",
        )
        return await self.update_branding(
            db, BrandingUpdate(favicon_key=key), current_user=current_user
        )

    async def _store_image(
        self,
        file: UploadFile,
        *,
        allowed: set,
        bounds: tuple,
        prefix: str,
    ) -> str:
        """Validate, normalise and write an upload; return its storage key.

        Validation mirrors ``OrganizationSettingsService.set_general_icon``:
        content-type allowlist first, then a 512 KB ceiling, then an actual
        decode — a declared content type is a claim by the client, not a fact
        about the bytes.
        """
        content_type = (file.content_type or "").split(";")[0].strip().lower()
        if content_type not in allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Unsupported image type. Allowed: "
                    + ", ".join(sorted(allowed))
                ),
            )

        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file")
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Image too large. Max 512KB")

        base_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads", "branding"))
        os.makedirs(base_dir, exist_ok=True)
        digest = hashlib.sha256(raw).hexdigest()[:16]

        if content_type in _VERBATIM_TYPES:
            suffix = _VERBATIM_TYPES[content_type]
            if suffix == ".svg":
                self._reject_unsafe_svg(raw)
            filename = f"{prefix}-{digest}{suffix}"
            with open(os.path.join(base_dir, filename), "wb") as fh:
                fh.write(raw)
            return filename

        try:
            image = Image.open(BytesIO(raw))
            image = image.convert("RGBA")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid image file")

        width, height = image.size
        max_width, max_height = bounds
        scale = min(max_width / width, max_height / height, 1.0)  # never upscale
        if scale < 1.0:
            image = image.resize(
                (int(width * scale), int(height * scale)), Image.Resampling.LANCZOS
            )

        filename = f"{prefix}-{digest}.png"
        buf = BytesIO()
        image.save(buf, format="PNG")
        with open(os.path.join(base_dir, filename), "wb") as fh:
            fh.write(buf.getvalue())
        return filename

    @staticmethod
    def _reject_unsafe_svg(raw: bytes) -> None:
        """Refuse anything in an SVG that is not drawing.

        ★ An SVG is a document, not a picture. It is served from our own origin
        to an authenticated browser, and the auth cookie is not httpOnly, so
        anything executable inside one runs with the app's origin and can read
        that cookie. The logo endpoint is the only place in this product that
        accepts one, so the check lives here rather than in a shared helper.

        ★★This used to be five substrings scanned over the FIRST 200,000 bytes
        of a file allowed to be 512 KB. Both halves failed:

          * a blocklist only knows the attacks somebody thought of —
            `onmouseover`, `<use href=…>`, `<animate attributeName="href">`,
            `<set>` and an entity declaration are none of the five;
          * 200 KB of window on a 512 KB limit meant the last 312 KB was never
            read. Pad, then attack.

        Now an ALLOW-list over the parsed document: known drawing elements,
        known presentational attributes, and nothing else. Unknown tag, unknown
        attribute, or a file that will not parse — refused. Refusing an unusual
        logo is a support ticket; accepting one executable logo is every
        session on the instance.
        """
        import xml.etree.ElementTree as ET

        def _refuse(reason: str):
            raise HTTPException(
                status_code=400,
                detail=f"SVG rejected: {reason}.",
            )

        if not raw or not raw.strip():
            _refuse("empty file")

        # ★Before any parser touches it. ElementTree resolves internal entities,
        # which is both an XXE and a billion-laughs route, and a DOCTYPE has no
        # business in a logo.
        head = raw[:4096].lower()
        if b"<!doctype" in head or b"<!entity" in raw.lower():
            _refuse("document type or entity declarations are not allowed")

        try:
            root = ET.fromstring(raw.decode("utf-8", errors="strict"))
        except Exception:
            _refuse("not a well-formed XML document")

        def _local(name: str) -> str:
            return name.rsplit("}", 1)[-1].lower()

        if _local(root.tag) != "svg":
            _refuse("the root element is not <svg>")

        for el in root.iter():
            tag = _local(el.tag)
            if tag not in _SVG_ALLOWED_ELEMENTS:
                _refuse(f"element <{tag}> is not allowed")
            for attr, value in el.attrib.items():
                name = _local(attr)
                # Every event handler in one rule, rather than the two that
                # happened to be listed before.
                if name.startswith("on"):
                    _refuse(f"event handler {name} is not allowed")
                if name not in _SVG_ALLOWED_ATTRS:
                    _refuse(f"attribute {name} is not allowed")
                v = (value or "").strip().lower().replace("\x00", "")
                # Collapse whitespace an obfuscator can hide a scheme behind.
                v = "".join(v.split())
                if v.startswith("javascript:") or v.startswith("data:text/html"):
                    _refuse("script URLs are not allowed")
                # `fill` and friends are usually plain colours ("none",
                # "#0af", "currentColor"); the reference rule only applies once
                # a value actually reaches for something. When it does, it may
                # only reach inside this document — never another file, a
                # tracking pixel, or a data: payload.
                if "url(" in v and not v.startswith("url(#"):
                    _refuse(f"{name} may only reference this document")


# ★Drawing only. Deliberately absent: script, foreignObject, image, use, a,
# animate/animateTransform/set/handler, iframe, embed, object, style, filter,
# and anything else that fetches, scripts, or rewrites attributes at runtime.
# `style` is out because a stylesheet can carry url() and @import.
_SVG_ALLOWED_ELEMENTS = frozenset({
    "svg", "g", "defs", "symbol", "title", "desc", "metadata",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan",
    "lineargradient", "radialgradient", "stop", "pattern",
    "clippath", "mask",
})

# Presentational attributes a real logo uses. Same rule: anything not named
# here is refused rather than reasoned about.
_SVG_ALLOWED_ATTRS = frozenset({
    "id", "class", "xmlns", "version", "viewbox", "preserveaspectratio",
    "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r",
    "rx", "ry", "fx", "fy", "d", "points", "transform", "gradienttransform",
    "gradientunits", "patternunits", "patterncontentunits", "clippathunits",
    "maskunits", "spreadmethod", "offset", "stop-color", "stop-opacity",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
    "stroke-dashoffset", "stroke-miterlimit", "opacity", "color",
    "font-family", "font-size", "font-weight", "font-style", "letter-spacing",
    "text-anchor", "dominant-baseline", "dx", "dy", "clip-path", "clip-rule",
    "mask", "shape-rendering", "vector-effect", "overflow", "display",
    "lang", "space",
})

async def resolve_product_name(db: AsyncSession) -> str:
    """Async convenience for callers that DO have a session at request time."""
    try:
        return (await BrandingService().get_branding(db)).product_name
    except Exception:  # noqa: BLE001
        return product_name()
