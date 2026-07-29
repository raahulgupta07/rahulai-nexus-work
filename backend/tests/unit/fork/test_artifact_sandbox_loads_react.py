"""The artifact sandbox must be able to load React.

Every dashboard, chart and slide the product renders in a browser is React
running inside `frontend/public/artifact-sandbox.html`, loaded into an iframe
carrying `sandbox="allow-scripts"` — deliberately WITHOUT `allow-same-origin`,
which is the sandbox-escape fix shipped in 0.0.490.14.

That attribute has a consequence the security fix did not account for: a frame
without `allow-same-origin` runs at an **opaque origin**. It sends
`Origin: null` on every request it makes, including requests back to the very
server that served it. Our own `/libs/` responses carry no
`Access-Control-Allow-Origin` header, so anything the frame fetches in CORS
mode is refused.

The two React tags — and only those two — carried `crossorigin`, which forces
CORS mode. The observed result, reproduced headless in the container:

    Access to script at 'http://localhost:3000/libs/react-18.production.min.js'
    from origin 'null' has been blocked by CORS policy: No
    'Access-Control-Allow-Origin' header is present on the requested resource.

    window.React    = undefined
    window.ReactDOM = undefined
    window.Babel    = object      <- no crossorigin, loaded fine
    window.echarts  = object      <- no crossorigin, loaded fine

and on screen: **"Dashboard failed to render — React is not defined"**, for
every dashboard, for every user.

★Why no test caught it. PDF export renders the same artifacts server-side
through `artifact_libs.get_inline_scripts()`, which pastes the library source
straight into the HTML — no HTTP request, no origin, no CORS. That path was
green throughout. A server-side render cannot observe a browser-only failure.

★Why this file asserts on the markup rather than on a browser. The defect is
one attribute on one tag; a headless browser proves the behaviour but needs
chromium and a live server, so it cannot live in the fast fork suite. What has
to survive is the RULE, and the rule is checkable in the source: nothing this
frame loads may be fetched in CORS mode while the server sends no CORS headers.

The pairing is what matters, so both halves are asserted: the sandbox attribute
stays locked down (`allow-same-origin` must NOT come back — that would undo the
security fix), and the script tags stay in no-cors mode.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PUBLIC = REPO / "frontend" / "public"
FRAME = REPO / "frontend" / "components" / "dashboard" / "ArtifactFrame.vue"

# ★There are TWO ways sandbox markup reaches the browser, and fixing only the
# first one leaves the product just as broken. Static pages the iframe navigates
# to, AND `buildArtifactIframeHtml` in artifactIframe.ts, which assembles the
# HTML in JavaScript and hands it to the frame as srcdoc. The dashboards users
# actually open go through the SECOND one. The first fix here covered only the
# static files, the dashboard still failed, and the browser named a filename
# (`react-18.development.js`) that appears in neither of them — which is what
# exposed the second loader.
SANDBOX_PAGES = [
    PUBLIC / "artifact-sandbox.html",
    PUBLIC / "mcp-artifact-app.html",
    PUBLIC / "mcp-visualization-app.html",
]

IFRAME_BUILDER = REPO / "frontend" / "utils" / "artifactIframe.ts"

# Everywhere a <script> tag destined for the sandbox is written.
SANDBOX_SOURCES = SANDBOX_PAGES + [IFRAME_BUILDER]

# Where a NEW such emitter could appear without anyone updating this file.
FRONTEND_SCAN_DIRS = [
    REPO / "frontend" / "utils",
    REPO / "frontend" / "components",
    REPO / "frontend" / "public",
]
SCAN_SUFFIXES = {".ts", ".js", ".vue", ".html"}

_SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.I)


def _strip_comments(html: str) -> str:
    """Drop HTML comments — the fix is DOCUMENTED with the word `crossorigin`
    right above the tags it was removed from, and a naive search would match
    the explanation and fail on a correct file. (This exact trap has bitten
    five times now in this repo.)"""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def _script_tags(path: Path) -> list[str]:
    return _SCRIPT_TAG.findall(_strip_comments(path.read_text()))


# --- guard the guard ---------------------------------------------------------

def test_the_pages_this_file_guards_actually_exist():
    """If a path is wrong every assertion below passes against nothing."""
    for p in SANDBOX_SOURCES:
        assert p.is_file(), f"sandbox source not found: {p}"
    assert FRAME.is_file(), f"ArtifactFrame.vue not found: {FRAME}"


def test_the_sandbox_page_really_does_load_react():
    """The premise. If React ever stops being loaded here, the CORS assertions
    below become vacuous and would keep passing forever."""
    tags = " ".join(_script_tags(PUBLIC / "artifact-sandbox.html"))
    assert "react-18" in tags, "artifact-sandbox.html no longer loads React"
    assert "react-dom-18" in tags, "artifact-sandbox.html no longer loads ReactDOM"


def test_the_javascript_builder_really_does_load_react():
    """Same premise for the srcdoc path — the one dashboards actually use."""
    body = IFRAME_BUILDER.read_text()
    assert "react-18.production.min.js" in body
    assert "react-18.development.js" in body, (
        "the development build is what the failing dashboard requested; if this "
        "branch is gone, re-check which files the frame really loads"
    )


# --- the fix -----------------------------------------------------------------

def test_no_sandbox_script_is_fetched_in_cors_mode():
    """★The actual bug. `crossorigin` on a tag inside an opaque-origin frame is
    a guaranteed block, because the server sends no CORS headers."""
    offenders = []
    for page in SANDBOX_SOURCES:
        for tag in _script_tags(page):
            if "crossorigin" in tag.lower():
                offenders.append(f"{page.name}: {tag}")
    assert not offenders, (
        "these tags are fetched in CORS mode from a frame that has an opaque "
        "origin, and /libs/ sends no Access-Control-Allow-Origin — the browser "
        "will refuse them and the artifact will not render:\n  "
        + "\n  ".join(offenders)
    )


def test_no_third_loader_writes_a_cors_script_tag_anywhere():
    """★The lesson from fixing this twice. The list above is a list, and a list
    goes stale — the second loader was found by a browser, not by review, and
    only because the error named a filename that appeared in neither file the
    first fix touched.

    So instead of trusting the list, sweep the frontend for ANY `<script` tag
    carrying `crossorigin`. There is currently no legitimate use in this
    codebase: every script we load is our own, served from our own origin, and
    a page that genuinely needed CORS mode would need CORS headers to go with
    it — a deliberate change, not something to slip in on one tag.
    """
    pattern = re.compile(r"<script\b[^>]*crossorigin", re.I)
    offenders = []
    for d in FRONTEND_SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix not in SCAN_SUFFIXES or not p.is_file():
                continue
            # Backups of the broken versions are kept on purpose.
            if ".bak-" in p.name:
                continue
            for m in pattern.finditer(_strip_comments(p.read_text(errors="ignore"))):
                offenders.append(f"{p.relative_to(REPO)}: …{m.group(0)[-60:]}")
    assert not offenders, (
        "a script tag is being written in CORS mode. If it is loaded into the "
        "artifact sandbox it WILL be blocked (opaque origin + no CORS headers) "
        "and the artifact will render blank:\n  " + "\n  ".join(offenders)
    )


def test_the_libs_are_still_served_from_our_own_origin():
    """`crossorigin` would become necessary if these moved to a CDN. They must
    not: the whole point of vendoring was to remove network fetches. A relative
    /libs/ path is what makes no-cors correct."""
    for page in SANDBOX_SOURCES:
        for tag in _script_tags(page):
            m = re.search(r'src\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not m:
                continue
            src = m.group(1)
            # The JS builder interpolates the path (`${reactSrc}`); both
            # branches of that variable are asserted separately above.
            if src.startswith("${"):
                continue
            assert src.startswith("/libs/"), (
                f"{page.name} loads {src!r} — sandbox scripts must be served "
                "from our own /libs/, never a CDN"
            )


# --- the same rule, applied to file BYTES rather than scripts ----------------
#
# The rule this file exists to protect is "nothing this frame loads may be
# fetched in CORS mode while the server sends no CORS headers". `crossorigin` on
# a <script> tag was one way to break it. `fetch()` in the frame's own runtime is
# the other, and it broke the same way, for the same reason, undetected:
#
#     Access to fetch at 'http://…/api/files/…/embed_token…' from origin 'null'
#     has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header
#     is present on the requested resource.
#
# so every PDF embedded in an artifact failed to load. `BowPdfViewer` fetched the
# bytes of a signed token URL under a comment asserting "the token URL is
# same-origin and needs no auth header" — true when written, false from the
# moment the frame lost `allow-same-origin`, and nothing enforced it.
#
# ★The fix is NOT to send `Access-Control-Allow-Origin` from that endpoint. It
# serves file bytes to anyone holding the token and the token travels in the URL;
# today a third party who obtains such a URL can display the file but cannot read
# its bytes into JavaScript, and `*` would remove exactly that barrier. Instead
# the HOST resolves the bytes (it is same-origin and already authenticated) and
# passes them in as a data: URI — which is what `ArtifactIframeFile.dataUri` and
# the `BowFile` header comment had described all along.
#
# ★The pdf.js worker needed no fix, and this was tested rather than assumed.
# `new Worker('/libs/pdf.worker.min.js')` IS refused from a null origin, and no
# CORS header can lift that (a classic dedicated worker script must be
# same-origin, which an opaque origin can never be). But pdf.js catches the
# failure and falls back to a main-thread worker loaded with a <script> tag —
# not a CORS fetch. Confirmed in Chromium inside a real `allow-scripts` frame:
# the PDF parsed and rasterised with the Worker constructor failing.

FILE_TOKEN_MARKER = "embed_token"
HOST_RESOLVER = "inlinePdfBytes"


def _frontend_files():
    for d in FRONTEND_SCAN_DIRS + [REPO / "frontend" / "pages"]:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix in SCAN_SUFFIXES and p.is_file() and ".bak-" not in p.name:
                yield p


def _strip_js_comments(src: str) -> str:
    """Same necessity as `_strip_comments` above, for .vue/.ts sources: the fix
    is explained in comments that name `inlinePdfBytes`, so an unstripped search
    would match the explanation instead of the call."""
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?<!:)//[^\n]*", "", src)
    return src


def _pages_that_resolve_artifact_files():
    """Derived, not listed. Any page that mints a file embed token is building
    the `files` array the sandbox renders — the public share page is one, and it
    is exactly the code path a file-by-file fix forgets."""
    out = []
    for p in _frontend_files():
        body = _strip_js_comments(p.read_text(errors="ignore"))
        if FILE_TOKEN_MARKER in body:
            out.append((p, body))
    return out


def test_the_host_resolves_pdf_bytes_for_every_page_that_feeds_the_frame():
    """★The actual bug. The frame cannot fetch these bytes; whoever mints the
    token has to resolve them."""
    pages = _pages_that_resolve_artifact_files()
    offenders = [
        str(p.relative_to(REPO)) for p, body in pages if HOST_RESOLVER not in body
    ]
    assert not offenders, (
        "these pages hand the artifact sandbox a bare token URL. The frame runs "
        "at an opaque origin, so its fetch of that URL is refused by CORS and the "
        f"embedded PDF never loads. Resolve the bytes host-side with {HOST_RESOLVER}():\n  "
        + "\n  ".join(offenders)
    )


def test_the_host_resolver_exists_and_at_least_two_pages_need_it():
    """★Guard the guard. The test above passes vacuously if the marker stops
    matching or the directory scan finds nothing — and 'no pages feed the frame'
    is never true. Two are known: the in-app panel and the public share page."""
    pages = _pages_that_resolve_artifact_files()
    assert len(pages) >= 2, (
        f"expected at least 2 pages minting file embed tokens, found "
        f"{[str(p.relative_to(REPO)) for p, _ in pages]}"
    )
    helper = (REPO / "frontend" / "utils" / "artifactIframe.ts").read_text()
    assert f"export async function {HOST_RESOLVER}" in helper, (
        f"{HOST_RESOLVER} is gone from artifactIframe.ts — the assertion above "
        "would then be checking for a call to nothing"
    )


def test_the_pdf_viewer_is_given_the_host_resolved_bytes_first():
    """The pairing. Resolving bytes host-side achieves nothing if `BowFile`
    still hands `BowPdfViewer` the token URL — `file.url || file.dataUri` would
    keep choosing the URL, and the fetch, and the CORS failure."""
    body = _strip_js_comments(
        (REPO / "frontend" / "public" / "libs" / "artifact-globals.js").read_text()
    )
    m = re.search(r"h\(window\.BowPdfViewer,\s*\{(.*?)\}\)", body, re.S)
    assert m, "the BowPdfViewer call site moved — re-check how the PDF gets its bytes"
    call = m.group(1)
    src = re.search(r"\bsrc\s*:\s*([^,}]+)", call)
    assert src, f"BowPdfViewer call has no src: {call.strip()[:120]}"
    assert "dataUri" in src.group(1), (
        "BowPdfViewer's src no longer prefers the host-resolved bytes "
        f"(src: {src.group(1).strip()}); it will fetch the token URL from an "
        "opaque origin and be blocked"
    )
    # The URL must still be passed as `href`, or the "Open PDF" fallback card
    # would try to navigate to a data: URI, which Chrome blocks outright.
    assert re.search(r"\bhref\s*:\s*[^,}]*\burl\b", call), (
        "BowPdfViewer lost the real URL for its fallback card"
    )


# --- and the security fix it must not undo -----------------------------------

def test_the_iframe_is_still_sandboxed_without_same_origin():
    """★The other half of the pair. The tempting 'fix' for the CORS block is to
    put `allow-same-origin` back, which would re-open the sandbox escape closed
    in 0.0.490.14. Removing `crossorigin` is the correct fix precisely because
    it leaves this alone."""
    body = FRAME.read_text()
    attrs = re.findall(r'sandbox\s*=\s*"([^"]*)"', body)
    assert attrs, "no sandbox attribute found in ArtifactFrame.vue"
    for value in attrs:
        assert "allow-scripts" in value, f"sandbox lost allow-scripts: {value!r}"
        assert "allow-same-origin" not in value, (
            "allow-same-origin is back on the artifact iframe — that re-opens "
            f"the sandbox escape fixed in 0.0.490.14: {value!r}"
        )
