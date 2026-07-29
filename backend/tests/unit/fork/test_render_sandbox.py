"""The headless renderers must not be a hole into the server's network.

Four services drive a real Chromium over code the model wrote — dashboard PDF
export, document PDF export, the document chart pre-render, and the artifact
render preflight. None of them constrained what the page could request, so
artifact JavaScript could reach the cloud metadata service or any internal
host and draw the answer into the page, which is then returned to the person
who asked for the export. A blind SSRF with a read-back channel.

All four also passed `--no-sandbox` unconditionally, which turns a renderer
bug from a tab-level problem into a container-level one.
"""
import asyncio
import inspect
from pathlib import Path

import pytest

from app.core.render_sandbox import block_external_requests, launch_chromium

REPO = Path(__file__).resolve().parents[4]
SERVICES = REPO / "backend" / "app" / "services"

RENDERERS = [
    SERVICES / "dashboard_pdf_export_service.py",
    SERVICES / "pdf_export_service.py",
    SERVICES / "doc_viz_render.py",
    SERVICES / "artifact_preflight.py",
]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Request:
    def __init__(self, url):
        self.url = url


class _Route:
    def __init__(self):
        self.verdict = None

    async def continue_(self):
        self.verdict = "allowed"

    async def abort(self):
        self.verdict = "blocked"


class _Page:
    def __init__(self):
        self.handler = None
        self.pattern = None

    async def route(self, pattern, handler):
        self.pattern = pattern
        self.handler = handler

    def verdict_for(self, url):
        route = _Route()
        _run(self.handler(route, _Request(url)))
        return route.verdict


@pytest.fixture
def page():
    p = _Page()
    _run(block_external_requests(p))
    return p


# --- what must be refused --------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://localhost:5432/",
    "http://127.0.0.1:8095/api/users/me",
    "http://10.0.0.5/internal",
    "https://attacker.example/collect?d=stolen",
    "ws://internal.example/socket",
])
def test_the_page_cannot_reach_the_network(page, url):
    """★The SSRF. Cloud metadata, the app's own API, the database port, and
    plain exfiltration all go through the same door."""
    assert page.verdict_for(url) == "blocked"


def test_the_route_covers_every_request(page):
    """A narrower pattern would leave subresources — images, XHR, fonts —
    unrouted, and those are the ones an exfiltration uses."""
    assert page.pattern == "**/*"


# --- what must still work --------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///tmp/dashboard-abc123.html",
    "file:///app/frontend/dist/libs/echarts-5.min.js",
    "data:image/png;base64,iVBORw0KGgo=",
    "blob:null/9d1f",
    "about:blank",
])
def test_local_content_still_loads(page, url):
    """★The renderers load their own HTML from a temp file and their vendored
    libraries from disk. Blocking those would break every export."""
    assert page.verdict_for(url) == "allowed"


def test_a_lookalike_host_is_not_mistaken_for_a_local_scheme():
    """`file:` matching must be on the SCHEME. A host that merely starts with
    the same letters is still the network."""
    p = _Page()
    _run(block_external_requests(p))
    assert p.verdict_for("http://filestore.example/x") == "blocked"


# --- the browser itself ----------------------------------------------------

def test_the_sandbox_is_attempted_before_it_is_given_up():
    """★`--no-sandbox` may genuinely be required under Docker's seccomp
    profile, which is why nobody ever removed it. Try without, fall back with,
    and say which happened."""
    src = inspect.getsource(launch_chromium)
    # Judge the code: the docstring explains the flag at length.
    body = src[src.index('"""', src.index('"""') + 3) + 3:]
    code = "\n".join(l.split("#", 1)[0] for l in body.splitlines())
    first = code.index("chromium.launch")
    assert "--no-sandbox" not in code[:first], "the first attempt already gives up"
    assert "--no-sandbox" in src, "there is no fallback for hosts that need it"
    assert "logger" in src


def test_the_fallback_actually_launches():
    """A fallback that raises is not a fallback."""
    class _Chromium:
        def __init__(self):
            self.calls = []

        async def launch(self, args=None):
            self.calls.append(list(args or []))
            if len(self.calls) == 1:
                raise RuntimeError("Running as root without --no-sandbox is not supported")
            return "browser"

    class _PW:
        def __init__(self):
            self.chromium = _Chromium()

    pw = _PW()
    assert _run(launch_chromium(pw, args=["--disable-dev-shm-usage"])) == "browser"
    assert "--no-sandbox" not in pw.chromium.calls[0]
    assert "--no-sandbox" in pw.chromium.calls[1]
    assert "--disable-dev-shm-usage" in pw.chromium.calls[1], "caller args were dropped"


# --- every renderer, not just the one that was reported --------------------

@pytest.mark.parametrize("path", RENDERERS, ids=lambda p: p.stem)
def test_every_renderer_blocks_the_network(path):
    """★All four run model-written code. Fixing only the dashboard exporter
    would leave three identical doors open."""
    src = path.read_text(encoding="utf-8")
    assert "block_external_requests" in src, f"{path.name} renders with an open network"


@pytest.mark.parametrize("path", RENDERERS, ids=lambda p: p.stem)
def test_no_renderer_hardcodes_no_sandbox_any_more(path):
    src = path.read_text(encoding="utf-8")
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "--no-sandbox" not in code, f"{path.name} still disables the browser sandbox outright"
    assert "launch_chromium" in code
