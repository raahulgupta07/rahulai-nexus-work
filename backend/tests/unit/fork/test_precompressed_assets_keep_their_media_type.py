"""A precompressed asset must keep the media type of the file it compresses.

`spa.py` now serves `app.js.br` when the client accepts brotli. The trap is that
the response still describes a JavaScript module: `Content-Encoding` describes
the TRANSFER, `Content-Type` describes the PAYLOAD. Guess the type from the
`.br` filename and the browser is told the entry chunk is `application/x-brotli`,
refuses to execute it as a module, and the whole app fails to boot with nothing
but a MIME-type line in the console — a total outage produced by a header.

★Nothing else guards this. The sign-in page is 3 requests, and one of them is
that entry chunk; if its type regresses there is no partial failure to notice.

The second property pinned here is the fail-safe. Precompression is an
OPTIMISATION performed by `nitro.compressPublicAssets` at build time
(frontend/nuxt.config.ts). A build that stops emitting siblings — config
reverted, a format nobody compressed, a file under Nitro's 1 KB floor — must
degrade to the original bytes, never 404 and never 500.

Context: before this, the bundle was served entirely uncompressed — the sign-in
page cost 4,224,858 bytes across 87 requests. Brotli siblings plus dropping the
prefetch manifest flag took it to ~264,000 bytes across 3.
"""

import gzip
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.spa import mount_spa


JS_BODY = b"export const answer = 42;\n" * 200


@pytest.fixture()
def client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "_nuxt").mkdir(parents=True)
    (dist / "assets").mkdir()

    (dist / "index.html").write_bytes(b"<!doctype html><div id=app></div>")
    # entry.js ships with a gzip sibling; plain.js deliberately ships without
    # one, standing in for every file Nitro skipped.
    (dist / "_nuxt" / "entry.js").write_bytes(JS_BODY)
    (dist / "_nuxt" / "entry.js.gz").write_bytes(gzip.compress(JS_BODY))
    (dist / "_nuxt" / "plain.js").write_bytes(JS_BODY)
    (dist / "_nuxt" / "entry.css").write_bytes(b"body{color:red}")
    (dist / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")

    monkeypatch.setenv("SERVE_FRONTEND", "1")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(dist))

    app = FastAPI()
    mount_spa(app)
    return TestClient(app)


def test_a_compressed_chunk_is_still_javascript(client):
    """★The boot-critical one. `.js.gz` must NOT be typed from its own suffix."""
    r = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip"})

    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.headers["content-type"].startswith("text/javascript")


def test_the_compressed_body_is_the_original_body(client):
    """A header claiming gzip over a non-gzip body is worse than no compression."""
    r = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip"})

    # httpx transparently decodes, so equality here proves a valid round trip.
    assert r.content == JS_BODY


def test_a_client_that_cannot_decompress_gets_the_original(client):
    r = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "identity"})

    assert "content-encoding" not in r.headers
    assert r.content == JS_BODY


def test_q_zero_is_a_refusal_not_a_preference(client):
    """`gzip;q=0` means never gzip. Reading it as "gzip mentioned" ships a body
    the client has explicitly said it cannot decode."""
    r = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip;q=0"})

    assert "content-encoding" not in r.headers
    assert r.content == JS_BODY


def test_a_missing_sibling_serves_the_original_rather_than_failing(client):
    """★The fail-safe. No sibling is a NORMAL answer, not an error."""
    r = client.get("/_nuxt/plain.js", headers={"Accept-Encoding": "br, gzip"})

    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.content == JS_BODY
    assert r.headers["content-type"].startswith("text/javascript")


def test_vary_is_set_on_both_variants(client):
    """Without Vary a shared cache keys on URL alone and hands the compressed
    body to a client that asked for identity."""
    compressed = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip"})
    identity = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "identity"})

    assert compressed.headers["vary"] == "Accept-Encoding"
    assert identity.headers["vary"] == "Accept-Encoding"


def test_each_variant_carries_its_own_etag(client):
    """A shared validator would let a cache answer an identity request with the
    compressed body under a matching If-None-Match."""
    compressed = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip"})
    identity = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "identity"})

    assert compressed.headers["etag"] != identity.headers["etag"]


def test_hashed_assets_stay_immutable_and_the_rest_revalidate(client):
    """Only `_nuxt/` filenames carry a content hash, so only they may be pinned
    for a year. Before this, everything else had NO Cache-Control at all and was
    refetched on every navigation."""
    hashed = client.get("/_nuxt/entry.css")
    unhashed = client.get("/assets/logo.png")
    shell = client.get("/reports/some-id")

    assert "immutable" in hashed.headers["cache-control"]
    assert unhashed.headers["cache-control"] == "public, max-age=3600, must-revalidate"
    assert "immutable" not in unhashed.headers["cache-control"]
    assert shell.headers["cache-control"] == "no-cache"


def test_a_sibling_pointing_outside_the_dist_dir_is_refused(client, tmp_path):
    """`resolved` is proven inside dist, but the sibling may be a symlink. It
    gets the same containment check, and failing it falls back rather than
    serving whatever it pointed at."""
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"not for the browser")
    dist = tmp_path / "dist"
    os.symlink(secret, dist / "_nuxt" / "entry.css.gz")

    r = client.get("/_nuxt/entry.css", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in r.headers
    assert r.content == b"body{color:red}"


def test_the_traversal_and_api_guards_still_hold(client):
    assert client.get("/_nuxt/does-not-exist.js").status_code == 404
    assert client.get("/api/anything").status_code == 404
    assert client.get("/%2e%2e/%2e%2e/etc/passwd").status_code == 404
