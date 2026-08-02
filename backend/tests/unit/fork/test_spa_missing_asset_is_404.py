"""A missing static file must 404, not be answered with the SPA shell.

The catch-all that serves the built Nuxt app used to return ``index.html`` with
status 200 for *any* path it could not find on disk. For application routes
that is exactly right — the client-side router owns them. For a file it is the
cause of two failures that name nothing:

  * ``GET /sw.js`` answered ``200 text/html``. Nothing in this product
    registers a service worker, but a browser that acquired one from an earlier
    build — or from a different product previously served on the same hostname
    — re-fetches that script periodically to decide whether to update. HTML is
    not a valid worker script, and a 200 is not a 404, so the browser keeps the
    OLD worker installed and it keeps serving the OLD interface indefinitely. A
    hard refresh does not clear it: that bypasses the HTTP cache, not a
    controlling worker. Answer 404 and the browser unregisters the worker
    itself, so a stuck machine heals on its next visit.

  * A missing JavaScript chunk was parsed as HTML, and the console reported
    ``Unexpected token '<'`` — which reads as a corrupt bundle rather than as a
    file that was never built.

The rule is an asset EXTENSION list, deliberately, not "any path with a dot":
application routes may legitimately contain one and must still receive the
shell.
"""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import spa


@pytest.fixture()
def client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "_nuxt").mkdir(parents=True)
    (dist / "index.html").write_text("<!DOCTYPE html><div id='__nuxt'></div>")
    (dist / "_nuxt" / "entry.abc123.js").write_text("console.log(1)")

    monkeypatch.setenv("SERVE_FRONTEND", "1")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(dist))

    app = FastAPI()
    spa.mount_spa(app)
    return TestClient(app)


# ---------------------------------------------------------------------------
# The failure this file exists for
# ---------------------------------------------------------------------------
def test_sw_js_is_404_so_a_stale_worker_can_unregister_itself(client):
    r = client.get("/sw.js")
    assert r.status_code == 404, (
        "answering /sw.js with the HTML shell leaves a stale service worker "
        "installed for ever — the browser only unregisters it on a 404"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/sw.js",
        "/service-worker.js",
        "/workbox-a1b2c3.js",
        "/firebase-messaging-sw.js",
        "/_nuxt/never-built-CAFEBABE.js",
        "/_nuxt/gone.css",
        "/_nuxt/entry.abc123.js.map",
        "/favicon.ico",
        "/robots.txt",
        "/site.webmanifest",
        "/images/missing.png",
    ],
)
def test_a_missing_asset_is_404(client, path):
    assert client.get(path).status_code == 404


# ---------------------------------------------------------------------------
# ...without breaking the thing the catch-all is FOR
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/users/sign-in",
        "/reports/1234",
        "/reports/q3.final",          # a dot in a slug is not an asset
        "/dashboards/2026.q1.review",
    ],
)
def test_application_routes_still_get_the_shell(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "__nuxt" in r.text
    assert r.headers["cache-control"] == "no-cache", (
        "the shell names the current bundle hashes, so it must be revalidated "
        "on every load or the whole interface freezes at an old build"
    )


def test_a_real_asset_is_still_served_and_cached_for_ever(client):
    r = client.get("/_nuxt/entry.abc123.js")
    assert r.status_code == 200
    assert "console.log(1)" in r.text
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


# ---------------------------------------------------------------------------
# The classifier on its own, so a future edit to the list is deliberate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,is_asset",
    [
        ("sw.js", True),
        ("_nuxt/x.CSS", True),           # matched case-insensitively
        ("a/b/c.woff2", True),
        ("reports/q3.final", False),
        ("users/sign-in", False),
        ("", False),
        ("deep/path/no-extension", False),
    ],
)
def test_looks_like_asset(path, is_asset):
    assert spa._looks_like_asset(path) is is_asset


def test_the_api_prefixes_are_untouched_by_this_change(client):
    # An unknown /api path must still 404 from the catch-all rather than be
    # handed the HTML shell — that behaviour predates this file and must stay.
    assert client.get("/api/nope").status_code == 404
