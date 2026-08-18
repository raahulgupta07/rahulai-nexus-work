"""SPA static file serving for the bundled Nuxt build.

When SERVE_FRONTEND=1 (set in the production Docker image), mount the
generated Nuxt output at root so a single uvicorn process serves both
the API and the client-side app. In dev this is disabled; the Nuxt dev
server on :3000 still proxies to uvicorn on :8000.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
# Everything under _nuxt/ carries a content hash in its filename, so it can be
# pinned for a year. Nothing else does — /assets/logo.png, favicon.ico and the
# locale files keep the same name across releases, so they get a short freshness
# window and a revalidation instead. Before this they got NO Cache-Control at
# all and were refetched on every navigation.
STATIC_ASSET_CACHE_CONTROL = "public, max-age=3600, must-revalidate"
SPA_INDEX_CACHE_CONTROL = "no-cache"

API_PREFIXES = (
    "api/",
    "ws/",
    "mcp",
    "excel",
    "scim/",
    ".well-known/",
    "slack_webhook",
    "teams_webhook",
    "whatsapp_webhook",
    "swagger",
    "openapi.json",
    "_nuxt_icon",
    "health",
)

# ★★★A request for a file that does not exist must 404, not be answered with
# index.html. Returning the HTML shell with 200 for a missing asset produces
# two failures that name nothing:
#
#   * A stale SERVICE WORKER can never be evicted. Nothing in this product
#     registers one, but a browser that picked one up from an earlier build, or
#     from a different product on the same hostname, re-fetches its script
#     periodically to decide whether to update. Getting `200 text/html` back is
#     not a valid worker script and not a 404 either, so the browser keeps the
#     OLD worker installed and it keeps serving the old interface — for ever.
#     A hard refresh does not help: that bypasses the HTTP cache, not a
#     controlling worker. On a 404 the browser unregisters the worker by
#     itself, so a stuck machine heals on its next visit with nobody opening
#     developer tools.
#
#   * A missing JavaScript chunk is parsed as HTML, and the console says
#     "Unexpected token '<'" — which reads like a corrupt bundle rather than a
#     file that was never there.
#
# Deliberately an ASSET extension list rather than "any path containing a dot".
# Application routes may legitimately carry one (a report slug such as
# /reports/q3.final), and those must still receive the SPA shell.
ASSET_SUFFIXES = (
    ".js", ".mjs", ".cjs", ".css", ".map", ".json", ".wasm",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".avif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".txt", ".xml", ".webmanifest",
)


def _looks_like_asset(path: str) -> bool:
    """True when the last path segment ends in a static-asset extension."""
    last = path.rsplit("/", 1)[-1].lower()
    return last.endswith(ASSET_SUFFIXES)


def _is_api_path(path: str) -> bool:
    p = path.lstrip("/")
    for prefix in API_PREFIXES:
        if prefix.endswith("/"):
            if p.startswith(prefix) or p == prefix.rstrip("/"):
                return True
        else:
            if p == prefix or p.startswith(prefix + "/"):
                return True
    return False


# Sibling files written next to each asset at BUILD time by Nitro's
# `compressPublicAssets` (frontend/nuxt.config.ts) — `app.js` gains `app.js.br`
# and `app.js.gz`. Nothing here compresses at request time: a per-request gzip
# would spend CPU on every hit of a file whose bytes never change.
#
# Ordered best-first. Brotli wins on size for text; gzip is the fallback for the
# handful of clients that do not advertise br.
PRECOMPRESSED_VARIANTS = (("br", ".br"), ("gzip", ".gz"))

VARY_ACCEPT_ENCODING = "Accept-Encoding"

# ★★★The compressed sibling must be served with the ORIGINAL file's media type.
# A `.js.br` is a JavaScript file that happens to have travelled compressed —
# Content-Encoding describes the transfer, Content-Type describes the payload.
# Handing the browser `application/x-brotli` (which is what guessing from the
# `.br` name gets you) makes it refuse the module and the whole app fails to
# boot with nothing but a MIME-type line in the console.
#
# mimetypes' own table is not enough: it maps `.js` to `text/plain` on some
# platform registries, and knows neither `.mjs` nor `.webmanifest`. The
# extensions the Nuxt build actually emits are pinned here.
_MEDIA_TYPE_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
}


def _media_type_for(path: str) -> str:
    """Media type of the UNCOMPRESSED file, whatever variant is served."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in _MEDIA_TYPE_OVERRIDES:
        return _MEDIA_TYPE_OVERRIDES[suffix]
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _accepted_encodings(header: str) -> set[str]:
    """Encoding tokens the client will accept, with q=0 entries dropped.

    `br;q=0` is an explicit REFUSAL, not a preference — a client that sends it
    must never be given brotli.
    """
    accepted: set[str] = set()
    for piece in header.split(","):
        token, _, params = piece.strip().partition(";")
        token = token.strip().lower()
        if not token:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, sep, value = param.partition("=")
            if sep and name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if quality > 0:
            accepted.add(token)
    return accepted


def _precompressed_sibling(
    resolved: str, accepted: set[str], dist_dir: str
) -> tuple[str, str] | None:
    """(path, encoding token) of the best sibling on disk, or None.

    None is the normal, supported answer — see the call site.
    """
    for token, suffix in PRECOMPRESSED_VARIANTS:
        if token not in accepted and "*" not in accepted:
            continue

        # `resolved` has already been proven to sit under the dist dir and
        # appending a suffix cannot climb out of it, but the sibling may itself
        # be a symlink pointing anywhere — so it gets the same containment
        # check as the original before it is opened.
        candidate = os.path.realpath(resolved + suffix)
        if not candidate.startswith(dist_dir + os.sep):
            continue

        if os.path.isfile(candidate):
            return candidate, token

    return None


def _cache_headers(spa_path: str, resolved: str, index_file: str) -> dict[str, str]:
    # ★Vary goes on EVERY response, not only the compressed ones. A shared cache
    # that stores an uncompressed body without it will hand that body to the
    # next client under an entry keyed only by URL — and vice versa. The header
    # has to be present on both variants for the key to include the encoding.
    headers = {"Vary": VARY_ACCEPT_ENCODING}

    if resolved == index_file:
        headers["Cache-Control"] = SPA_INDEX_CACHE_CONTROL
    elif spa_path.lstrip("/").startswith("_nuxt/"):
        headers["Cache-Control"] = IMMUTABLE_ASSET_CACHE_CONTROL
    else:
        headers["Cache-Control"] = STATIC_ASSET_CACHE_CONTROL

    return headers


def mount_spa(app: FastAPI) -> None:
    """Attach a SPA fallback GET handler to the app.

    Must be called AFTER all API routers are registered — the catch-all
    route is matched last by FastAPI, but registration order determines
    priority for overlapping paths.
    """
    if os.environ.get("SERVE_FRONTEND", "").lower() not in ("1", "true", "yes"):
        return

    dist_dir = Path(os.environ.get("FRONTEND_DIST_DIR", "/app/frontend/dist")).resolve()
    index_file = dist_dir / "index.html"

    if not index_file.is_file():
        raise RuntimeError(
            f"SERVE_FRONTEND is set but {index_file} does not exist. "
            "Did `nuxt generate` run during the image build?"
        )

    dist_dir_str = str(dist_dir)
    index_file_str = str(index_file)

    def _serve(resolved: str, spa_path: str, request: Request) -> FileResponse:
        """Serve `resolved`, preferring a precompressed sibling when there is one.

        ★★★If no sibling exists this returns exactly what the handler returned
        before compression was added — same path, same headers minus Vary. That
        is the whole safety property: the frontend build writing `.br`/`.gz`
        files is an OPTIMISATION, and a build that stops writing them (config
        reverted, cache miss, a format nobody compressed) must degrade to the
        original bytes rather than 404 or 500.
        """
        headers = _cache_headers(spa_path, resolved, index_file_str)
        media_type = _media_type_for(resolved)

        accepted = _accepted_encodings(request.headers.get("accept-encoding", ""))
        sibling = _precompressed_sibling(resolved, accepted, dist_dir_str)
        if sibling is not None:
            path, encoding = sibling
            # ETag and Content-Length come from FileResponse's stat of whichever
            # path it is handed, so the compressed variant gets its own ETag by
            # construction — a cache can never answer an identity request with
            # the brotli body under a shared validator.
            return FileResponse(
                path,
                media_type=media_type,
                headers={**headers, "Content-Encoding": encoding},
            )

        return FileResponse(resolved, media_type=media_type, headers=headers)

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa_fallback(spa_path: str, request: Request):
        if _is_api_path(spa_path):
            raise HTTPException(status_code=404)

        # Inline path-traversal guard at the sink (Snyk python/PT). Reject
        # absolute paths, parent refs, and backslashes before joining; then
        # verify the resolved path stays under the dist dir.
        if ".." in spa_path or spa_path.startswith("/") or "\\" in spa_path:
            raise HTTPException(status_code=404)

        resolved = os.path.realpath(os.path.join(dist_dir_str, spa_path))
        if not resolved.startswith(dist_dir_str + os.sep) and resolved != dist_dir_str:
            raise HTTPException(status_code=404)

        if os.path.isfile(resolved):
            return _serve(resolved, spa_path, request)

        # A missing ASSET is a 404. Only application routes fall through to the
        # SPA shell. See ASSET_SUFFIXES above for why this matters.
        if _looks_like_asset(spa_path):
            raise HTTPException(status_code=404)

        return _serve(index_file_str, spa_path, request)
