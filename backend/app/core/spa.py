"""SPA static file serving for the bundled Nuxt build.

When SERVE_FRONTEND=1 (set in the production Docker image), mount the
generated Nuxt output at root so a single uvicorn process serves both
the API and the client-side app. In dev this is disabled; the Nuxt dev
server on :3000 still proxies to uvicorn on :8000.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
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


def _cache_headers(spa_path: str, resolved: str, index_file: str) -> dict[str, str]:
    if resolved == index_file:
        return {"Cache-Control": SPA_INDEX_CACHE_CONTROL}

    if spa_path.lstrip("/").startswith("_nuxt/"):
        return {"Cache-Control": IMMUTABLE_ASSET_CACHE_CONTROL}

    return {}


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
            return FileResponse(
                resolved,
                headers=_cache_headers(spa_path, resolved, index_file_str),
            )

        # A missing ASSET is a 404. Only application routes fall through to the
        # SPA shell. See ASSET_SUFFIXES above for why this matters.
        if _looks_like_asset(spa_path):
            raise HTTPException(status_code=404)

        return FileResponse(
            index_file_str,
            headers={"Cache-Control": SPA_INDEX_CACHE_CONTROL},
        )
