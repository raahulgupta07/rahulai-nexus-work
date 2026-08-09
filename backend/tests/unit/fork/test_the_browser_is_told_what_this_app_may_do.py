"""Phase 5 guards: response headers, the 422 body echo, and the SSRF host guard.

Three unrelated-looking defects with one shape: the server was handing out
something it had no reason to hand out.

  * no CSP / frame-ancestors / nosniff / Referrer-Policy, so the browser was
    given no instructions at all about what this origin may do;
  * `server: uvicorn` on every response, unauthenticated;
  * a 422 body that replayed the submitted value — including, on a `missing`
    field, the WHOLE body with the plaintext password in it;
  * an arbitrary-URL data source that would fetch the cloud metadata endpoint
    and hand its IAM credentials back through a tool result.

★Red-proof against a `git worktree` at HEAD, 2026-08-09: **15 failed, 1 passed.**

★★And most of those failures are the WEAK kind — `ModuleNotFoundError` on
`app.core.outbound_host`, which proves the guard imports the fix, not that it
detects the bug. That is unavoidable when the defect is "no such control
exists", but it is not the same evidence as a scan that read the old file and
disagreed with it. The three that ARE that stronger kind, and the reason this
file is worth keeping:

  * `test_the_middleware_is_actually_installed` — read HEAD's `main.py`
  * `test_start_sh_suppresses_the_banner_at_the_source` — read HEAD's `start.sh`
  * `test_the_redirect_chain_is_checked_too` — read HEAD's `custom_api_client.py`

The single pass is `test_the_validation_handler_is_registered`, which was green
on HEAD because FastAPI installs its own default handler for that exception —
a reminder that "a handler is registered" was never the question; WHICH handler
was.
"""

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "backend"


# ---------------------------------------------------------------- headers ---

def _mw():
    from app.core import security_headers
    return security_headers


class _Resp:
    """Minimal stand-in for a Starlette response — headers is all we touch."""

    def __init__(self, headers=None):
        from starlette.datastructures import MutableHeaders
        self.headers = MutableHeaders(headers or {})


async def _dispatch(initial=None):
    mw = _mw().SecurityHeadersMiddleware(app=None)
    resp = _Resp(initial)

    async def _call_next(_request):
        return resp

    return await mw.dispatch(object(), _call_next)


@pytest.mark.asyncio
async def test_every_response_carries_the_browser_controls():
    out = await _dispatch()
    h = out.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert "Referrer-Policy" in h
    assert "Content-Security-Policy" in h


@pytest.mark.asyncio
async def test_the_page_cannot_be_framed_by_a_third_party():
    """frame-ancestors is the one that stops clickjacking on a modern browser.

    X-Frame-Options alone is not enough — it is ignored where CSP is present,
    so a CSP without frame-ancestors is WEAKER than no CSP at all here.

    ★★★'self', and the assertion that it is NOT 'none' is the load-bearing half.
    Charts, documents and MCP visualizations all render in same-origin iframes;
    'none' refuses those too, and the refusal appears only in the browser
    console, where no Python test can see it. Anyone tightening this to 'none'
    for neatness fails here and reads why.
    """
    out = await _dispatch()
    csp = out.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp
    assert "frame-ancestors 'none'" not in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp


@pytest.mark.asyncio
async def test_the_server_banner_is_removed():
    out = await _dispatch({"server": "uvicorn"})
    assert "server" not in {k.lower() for k in out.headers.keys()}


@pytest.mark.asyncio
async def test_hsts_is_opt_in_and_not_emitted_by_default():
    """A default-on HSTS would brick every plain-HTTP install, and the browser
    keeps honouring the pin long after the header stops being sent — so getting
    this wrong is not something a later release can undo."""
    os.environ.pop("DASH_HSTS", None)
    out = await _dispatch()
    assert "Strict-Transport-Security" not in out.headers
    os.environ["DASH_HSTS"] = "1"
    try:
        out = await _dispatch()
        assert "max-age=" in out.headers["Strict-Transport-Security"]
    finally:
        os.environ.pop("DASH_HSTS", None)


@pytest.mark.asyncio
async def test_a_route_that_sets_its_own_value_keeps_it():
    out = await _dispatch({"X-Frame-Options": "SAMEORIGIN"})
    assert out.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_the_middleware_is_actually_installed():
    """★A middleware nothing registers is a file, not a control. This is the
    binding half — the tests above only prove the class behaves."""
    src = (BACKEND / "main.py").read_text()
    assert "init_security_headers(app)" in src


def test_start_sh_suppresses_the_banner_at_the_source():
    src = (REPO / "start.sh").read_text()
    # Both branches: the production exec and the hot-reload one.
    # ★Count ARGUMENT lines, not occurrences of the string: the comment above
    # the production exec names the flag too, and counting raw occurrences
    # made this assert 3 == 2 against a correct file.
    flag_lines = [
        ln for ln in src.splitlines()
        if ln.strip().rstrip("\\").strip() == "--no-server-header"
    ]
    assert len(flag_lines) == 2, flag_lines


# ------------------------------------------------------------ 422 echo ---

def test_the_validation_body_no_longer_replays_what_was_submitted():
    from app.errors.handlers import _scrub_validation_errors
    errors = [
        {
            "type": "missing",
            "loc": ["body", "name"],
            "msg": "Field required",
            "input": {"email": "a@b.c", "password": "hunter2-the-real-one"},
        }
    ]
    out = _scrub_validation_errors(errors)
    blob = repr(out)
    assert "hunter2-the-real-one" not in blob
    assert "input" not in out[0]


def test_the_client_can_still_tell_which_field_failed():
    """★Scrubbing must not turn a usable 422 into a mystery: the form still has
    to highlight the right box. Drop the value, keep the location and reason."""
    from app.errors.handlers import _scrub_validation_errors
    out = _scrub_validation_errors([
        {"type": "value_error", "loc": ["body", "email"], "msg": "bad email",
         "input": "nope", "ctx": {"reason": "no @-sign"}}
    ])
    assert out[0]["loc"] == ["body", "email"]
    assert out[0]["msg"] == "bad email"
    assert "ctx" not in out[0]


def test_the_validation_handler_is_registered():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from app.errors import register_exception_handlers
    app = FastAPI()
    register_exception_handlers(app)
    assert RequestValidationError in app.exception_handlers


# ------------------------------------------------------------------ SSRF ---

def test_the_metadata_endpoint_is_refused():
    from app.core.outbound_host import assert_url_allowed, OutboundHostRefused
    for url in (
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
    ):
        with pytest.raises(OutboundHostRefused):
            assert_url_allowed(url)


def test_loopback_and_odd_schemes_are_refused():
    from app.core.outbound_host import assert_url_allowed, OutboundHostRefused
    for url in ("http://127.0.0.1:3000/api/settings",
                "http://localhost:3000/",
                "file:///etc/passwd"):
        with pytest.raises(OutboundHostRefused):
            assert_url_allowed(url)


def test_a_private_data_source_still_works_by_default():
    """★★★The deliberate non-block. This is a self-hosted analytics product:
    a Postgres on 10.x or a Druid on the Docker bridge is the normal case. A
    default that refused these would break every real install on upgrade, and
    the operator's fix would be to switch the guard off entirely."""
    from app.core.outbound_host import assert_url_allowed
    os.environ.pop("DASH_BLOCK_PRIVATE_HOSTS", None)
    assert_url_allowed("https://10.4.1.9:8080/api")   # must not raise
    assert_url_allowed("http://192.168.1.50/druid")


def test_the_strict_posture_is_available_when_wanted():
    from app.core.outbound_host import assert_url_allowed, OutboundHostRefused
    os.environ["DASH_BLOCK_PRIVATE_HOSTS"] = "1"
    try:
        with pytest.raises(OutboundHostRefused):
            assert_url_allowed("https://10.4.1.9:8080/api")
    finally:
        os.environ.pop("DASH_BLOCK_PRIVATE_HOSTS", None)


def test_an_allowlist_entry_wins():
    from app.core.outbound_host import assert_url_allowed
    os.environ["DASH_OUTBOUND_HOST_ALLOWLIST"] = "127.0.0.1"
    try:
        assert_url_allowed("http://127.0.0.1:9000/")  # must not raise
    finally:
        os.environ.pop("DASH_OUTBOUND_HOST_ALLOWLIST", None)


def test_the_redirect_chain_is_checked_too():
    """★Validating the URL you intend to fetch says nothing about the URL you
    end up fetching. `follow_redirects=True` is still on, so a legitimate host
    answering `302 Location: http://169.254.169.254/...` is the live bypass."""
    src = (BACKEND / "app/data_sources/clients/custom_api_client.py").read_text()
    assert "_redirect_guard" in src
    assert "event_hooks=self._redirect_guard()" in src
    # And the construction-time check, which is what stops `test_connection`
    # being used as a reachability oracle before any tool call happens.
    assert "assert_url_allowed(self.base_url)" in src
