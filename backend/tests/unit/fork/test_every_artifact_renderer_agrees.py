"""Every renderer of an artifact document must agree with every other one.

★The same dashboard is assembled into an HTML document in SIX places, and they
had already drifted apart before this file existed:

  * `pdf.min.js` was loaded by the in-app renderer and by no other, so a
    dashboard embedding a PDF renders in the app while the thumbnail, the PDF
    export and the planner's preview all show BowPdfViewer's "nolib" state.
  * `public/artifact-sandbox.html` carried a FORKED inline copy of
    artifact-globals.js holding 16 of its 31 symbols — missing DataTable,
    useFilters, BowKpi, exportCSV, InfoPopover, FilterSearch, BowFitText and
    resizeAllCharts among others.
  * `public/mcp-artifact-app.html` loads the real globals file and then
    redefines three of its symbols on top.

None of it errors. A renderer that is one library behind produces a page that
loads, renders, and is quietly wrong. So the agreement is asserted here rather
than left to whoever remembers to edit all six.

★Two of the six were found BY this test rather than by reading the tree:
create_artifact.py and mcp-artifact-app.html were both missing from the first
draft's list. That is what test_no_unregistered_renderer exists for — the
renderer nobody has written yet is the one no other guard can cover.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
_FRONTEND = _REPO / "frontend"

MANIFEST = _FRONTEND / "public" / "libs" / "manifest.json"
GLOBALS_JS = _FRONTEND / "public" / "libs" / "artifact-globals.js"

# Every file that assembles a page-mode artifact document. A renderer absent
# from this list is caught by test_no_unregistered_renderer below.
# Renderers that READ the manifest at build or run time. They may not restate
# any library name — the list is resolved from libs/manifest.json.
MANIFEST_READERS = {
    "frontend/utils/artifactIframe.ts": _FRONTEND / "utils" / "artifactIframe.ts",
    "backend/app/services/artifact_libs.py": _REPO / "backend" / "app" / "services" / "artifact_libs.py",
}

# ★Static shells. They are plain HTML served as-is and cannot read a JSON file,
# so their <script> tags are compared against the manifest here instead. This
# test is the only thing holding them in step; without it they drift silently,
# which is exactly what happened to pdf.min.js.
STATIC_SHELLS = {
    "frontend/public/artifact-sandbox.html": _FRONTEND / "public" / "artifact-sandbox.html",
    "frontend/public/mcp-artifact-app.html": _FRONTEND / "public" / "mcp-artifact-app.html",
}

RENDERERS = {**MANIFEST_READERS, **STATIC_SHELLS}

# Documents assembled from artifact_libs.get_inline_scripts(). They must never
# name a library themselves.
SERVER_CONSUMERS = {
    "backend/app/services/thumbnail_service.py": _REPO / "backend" / "app" / "services" / "thumbnail_service.py",
    "backend/app/services/dashboard_pdf_export_service.py":
        _REPO / "backend" / "app" / "services" / "dashboard_pdf_export_service.py",
    "backend/app/services/report_pdf_service.py": _REPO / "backend" / "app" / "services" / "report_pdf_service.py",
    "backend/app/ai/tools/implementations/create_artifact.py":
        _REPO / "backend" / "app" / "ai" / "tools" / "implementations" / "create_artifact.py",
}

# ★The one symbol a renderer is allowed to define for itself, with the reason.
# artifact-sandbox.html receives its data by postMessage AFTER the document has
# loaded, so its useArtifactData must be a React hook that subscribes to the
# `artifactdata` event. Every other renderer sets window.ARTIFACT_DATA before
# the artifact code runs, where a plain getter is correct. Replacing one with
# the other renders an empty dashboard, so this divergence is deliberate.
GLOBALS_OVERRIDE_ALLOWLIST = {
    "frontend/public/artifact-sandbox.html": {"useArtifactData"},
    "frontend/public/mcp-artifact-app.html": {"useArtifactData"},
}

_REACT_VARIANT = re.compile(r"^(react-18|react-dom-18)[.-].*\.js$")


def _canonical(lib: str) -> str:
    """react-18.development.js and react-18.production.min.js are one library.

    The in-app renderer picks a build at runtime; the servers hardcode one.
    That is a legitimate difference — which React build, not which libraries.
    """
    name = lib.rsplit("/", 1)[-1]
    m = _REACT_VARIANT.match(name)
    return m.group(1) if m else name


def _libs_in(text: str) -> set[str]:
    return {_canonical(m) for m in re.findall(r"/libs/([A-Za-z0-9._-]+\.js)", text)}


def _manifest_libs() -> set[str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {_canonical(x) for x in data["page"]}


def _globals_symbols() -> set[str]:
    return set(re.findall(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=", GLOBALS_JS.read_text(encoding="utf-8")))


def test_the_manifest_exists_and_is_not_empty():
    """One ordered list of page libraries, read by every renderer."""
    assert MANIFEST.exists(), (
        f"{MANIFEST} is missing. It is the single source of which libraries an "
        "artifact document loads; without it each renderer keeps its own list "
        "and they drift silently."
    )
    libs = _manifest_libs()
    assert libs, "the manifest lists no page libraries"
    # Positive control: a manifest that lost React would satisfy "not empty".
    assert "react-18" in libs and "echarts-5.min.js" in libs, (
        f"the manifest is missing a library every dashboard needs: {sorted(libs)}"
    )


@pytest.mark.parametrize("name", sorted(MANIFEST_READERS))
def test_a_manifest_reader_never_restates_the_library_list(name):
    """The point of the manifest is that these files do not hold a second copy."""
    text = MANIFEST_READERS[name].read_text(encoding="utf-8")
    assert "manifest.json" in text, (
        f"{name} does not read libs/manifest.json — it is keeping its own "
        "library list, which is how pdf.min.js came to be loaded in one "
        "renderer out of six."
    )


@pytest.mark.parametrize("name", sorted(STATIC_SHELLS))
def test_every_static_shell_matches_the_manifest(name):
    """A shell cannot read the manifest, so it is compared against it."""
    want = _manifest_libs()
    got = _libs_in(STATIC_SHELLS[name].read_text(encoding="utf-8")) - {"artifact-globals.js"}
    missing, extra = want - got, got - want
    assert not missing and not extra, (
        f"{name} disagrees with libs/manifest.json.\n"
        f"  missing here : {sorted(missing)}\n"
        f"  only here    : {sorted(extra)}\n"
        "A renderer one library behind produces a page that loads and is quietly wrong."
    )


@pytest.mark.parametrize("name", sorted(STATIC_SHELLS))
def test_no_shell_forks_the_globals(name):
    """Artifact globals come from artifact-globals.js. No private copies."""

    text = STATIC_SHELLS[name].read_text(encoding="utf-8")
    shared = _globals_symbols()
    allowed = GLOBALS_OVERRIDE_ALLOWLIST.get(name, set())

    assert "artifact-globals.js" in text, (
        f"{name} never loads artifact-globals.js, so the artifact code it runs "
        "sees a different set of helpers than every other renderer provides."
    )

    defined_here = set(re.findall(r"window\.([A-Za-z_][A-Za-z0-9_]*)\s*=", text))
    forked = (defined_here & shared) - allowed
    assert not forked, (
        f"{name} defines {sorted(forked)} itself, shadowing artifact-globals.js.\n"
        "A private copy goes stale without erroring: the fork in "
        "artifact-sandbox.html sat 18 symbols behind, and it renders the preview "
        "the planner reflects on."
    )


@pytest.mark.parametrize("name", sorted(SERVER_CONSUMERS))
def test_the_server_renderers_do_not_restate_the_libraries(name):
    """Both server documents must go through artifact_libs, never inline tags."""
    text = SERVER_CONSUMERS[name].read_text(encoding="utf-8")
    inlined = _libs_in(text)
    assert not inlined, (
        f"{name} names libraries directly ({sorted(inlined)}) instead of asking "
        "artifact_libs for them."
    )
    # ★A consumer either assembles the document itself (and asks artifact_libs
    # for the scripts) or delegates to one that does. dashboard_pdf_export_service
    # is the second shape — it hands off to CreateArtifactTool._build_thumbnail_html
    # — and requiring get_inline_scripts of it asserts the wrong thing.
    assembles = "get_inline_scripts" in text
    delegates = "_build_thumbnail_html" in text or "build_dashboard_html" in text
    assert assembles or delegates, (
        f"{name} builds an artifact document without asking artifact_libs for "
        "the scripts and without delegating to a builder that does"
    )


def test_no_unregistered_renderer():
    """★The guard for the renderer nobody has written yet.

    Layers above keep today's four in step. This one fails the day a fifth
    appears, because every guard above would keep passing while the new one
    quietly ships a different document.
    """
    known = {p.resolve() for p in list(RENDERERS.values()) + list(SERVER_CONSUMERS.values())}
    # Only the document shape that mounts an artifact counts — a #root div in a
    # full HTML document. Ordinary pages and mail templates are not renderers.
    suspects = []
    roots = [
        _FRONTEND / "utils", _FRONTEND / "public", _FRONTEND / "components", _FRONTEND / "pages",
        _REPO / "backend" / "app",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".vue", ".html", ".py"}:
                continue
            if path.resolve() in known or ".bak-" in path.name or "node_modules" in str(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "<!DOCTYPE html>" in text and 'id="root"' in text:
                suspects.append(str(path.relative_to(_REPO)))

    assert not suspects, (
        "these files assemble an artifact document but are not registered in "
        f"this test, so nothing keeps them in step with the others: {sorted(suspects)}.\n"
        "Add them to RENDERERS (and make them read the manifest), or stop them "
        "building their own document."
    )


# ---------------------------------------------------------------------------
# The grounded narrative is rendered by two implementations — one TypeScript,
# one Python — because the document is assembled on both sides. They must agree.
# ---------------------------------------------------------------------------

TS_BUILDER = _FRONTEND / "utils" / "artifactIframe.ts"
PY_BUILDER = _REPO / "backend" / "app" / "services" / "artifact_insights_html.py"

_CLASS = re.compile(r"bow-insight-[a-z]+")


def _insight_classes(path: Path) -> set[str]:
    return set(_CLASS.findall(path.read_text(encoding="utf-8")))


def test_both_insight_renderers_use_the_same_markup():
    """One section, two languages. A class added on one side and not the other
    styles nothing — and nothing errors, so the PDF simply comes out plainer
    than the screen."""
    ts, py = _insight_classes(TS_BUILDER), _insight_classes(PY_BUILDER)
    assert ts, "the TypeScript builder renders no insight markup at all"
    assert ts == py, (
        "the two insight renderers disagree.\n"
        f"  only in artifactIframe.ts        : {sorted(ts - py)}\n"
        f"  only in artifact_insights_html.py: {sorted(py - ts)}"
    )


@pytest.mark.parametrize("path", [TS_BUILDER, PY_BUILDER])
def test_both_insight_renderers_anchor_on_the_same_element(path):
    """The id is what every rule is scoped to and what a test can look for."""
    text = path.read_text(encoding="utf-8")
    assert 'id="artifact-insights"' in text, f"{path.name} does not emit #artifact-insights"
    assert "data-polish-ignore" in text, (
        f"{path.name} does not mark the section as ignored by the element picker; "
        "the editor would offer to restyle a section the dashboard does not own"
    )


@pytest.mark.parametrize("path", [TS_BUILDER, PY_BUILDER])
def test_the_narrative_is_escaped_before_it_is_embedded(path):
    """★The text is model-written and lands in a document that also runs the
    model's own code. Unescaped, a finding could close the section and open a
    script tag."""
    text = path.read_text(encoding="utf-8")
    escaped = "escapeHtml(" in text or "html.escape(" in text
    assert escaped, f"{path.name} embeds insight text without escaping it"


def test_the_dashboard_keeps_a_full_viewport():
    """★The narrative must not change the dashboard's height. Either way.

    The first version made body a flex column with #root{flex:1 1 auto}. It
    measured correct — #root 753px, section 207px, both on screen — and was
    wrong anyway: every dashboard permanently lost 207px and scrolled inside
    itself while the section sat pinned to the bottom. Geometry that adds up is
    not the same as behaviour that is right.

    The version after that gave #root min-height:100vh, on the theory that a
    definite height was needed by inner panels sized with h-full or
    height:100%. That theory was tested, not assumed: all 19 stored page
    artifacts were rendered with the rule and without it, and the count of
    collapsed elements was zero in BOTH modes — as was the count of zero-sized
    canvases. What the rule did do was pad short dashboards out to a full
    viewport, opening a white gap between the dashboard and the narrative:
    532px on the shortest, and 392px on an artifact carrying no narrative at
    all, where the padding could not possibly help.

    So #root now carries no height rule, and this asserts that it stays that
    way — no `height`, no `min-height`, no flex parent.
    """
    for path in (TS_BUILDER, PY_BUILDER):
        text = path.read_text(encoding="utf-8")
        # ★The body reset IS load-bearing and must survive: without margin:0
        # the UA default 8px draws a border of page-ground around every
        # dashboard.
        assert "body { min-height: 100%; margin: 0; padding: 0; }" in text, (
            f"{path.name} dropped the document reset; the UA default body "
            "margin will show as an 8px frame around the dashboard"
        )
        assert "#root { min-height: 100vh; }" not in text, (
            f"{path.name} pads #root out to a viewport again — that opens a "
            "gap between a short dashboard and the narrative below it, and "
            "buys nothing, because nothing was collapsing without it"
        )
        # ★height pins the box to one viewport while the dashboard's content
        # overflows it — and the narrative then renders on top of that
        # overflow. Rect comparison misses it; only a screenshot shows it. So
        # the wrong rule is named here explicitly.
        assert "#root { height: 100vh; }" not in text, (
            f"{path.name} pins #root to one viewport; a taller dashboard will "
            "overflow it and the narrative will collide with the overflow"
        )
        # ★Scoped to the BODY rule. A bare "flex-direction: column" also matches
        # the bullet list's own styling, and asserting on that would fail
        # against correct code — which is how a guard teaches people to delete
        # it rather than to read it.
        assert "body { display: flex" not in text, (
            f"{path.name} lays the document out as a flex column again — that "
            "shrinks the dashboard to make room for the narrative"
        )


# ═══════════════════════════════════════════════════════════════════════════
# The static shells must actually PARSE
# ═══════════════════════════════════════════════════════════════════════════

def _strip_js_noise(src: str) -> str:
    """Blank out comments, strings and regex literals, keeping brackets intact.

    Not a JS parser — a lexer just good enough that the bracket balance below
    counts real brackets and not ones inside a string or a comment.
    """
    out = []
    i, n = 0, len(src)
    # A '/' starts a regex only where a value is expected. After an identifier,
    # a number or a closing bracket it is division.
    prev_significant = ""
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if two == "/*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "\"'`":
            quote, i = c, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            out.append('""')
            prev_significant = '"'
            continue
        if c == "/" and prev_significant not in (")", "]", "}", "") and not (
            prev_significant.isalnum() or prev_significant == "_"
        ):
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "/":
                    i += 1
                    break
                if src[i] == "\n":
                    break
                i += 1
            out.append("R")
            prev_significant = "R"
            continue
        out.append(c)
        if not c.isspace():
            prev_significant = c
        i += 1
    return "".join(out)


def _inline_scripts(html: str) -> list:
    """Every <script> block that has no src= — i.e. the shell's own code."""
    return [
        m.group(1)
        for m in re.finditer(
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I
        )
    ]


@pytest.mark.parametrize("name", sorted(STATIC_SHELLS))
def test_static_shell_inline_scripts_are_balanced(name):
    """★A shell whose own <script> does not parse is a shell that does nothing.

    This is not hypothetical. Un-forking artifact-sandbox.html meant deleting
    211 lines of stale copies of artifact-globals.js, and the cut landed in the
    MIDDLE of the one function that had to stay: useArtifactData lost its
    addEventListener, its cleanup, its `return data` and its closing brace, so
    the whole IIFE ended with "Unexpected end of input".

    Everything in that block died with it — the postMessage listener, the
    ARTIFACT_READY signal to the parent, the loader teardown, window.onerror.
    The file still looked right, still passed the library-manifest tests above,
    and would have rendered a permanent loading spinner in every sandboxed
    dashboard. Nothing else in the suite reads these files as CODE.

    Bracket balance is a weak proxy for "parses", but it is the failure mode a
    truncating edit actually produces, and it needs no JS runtime — the test
    image has no node.
    """
    text = STATIC_SHELLS[name].read_text(encoding="utf-8")
    blocks = _inline_scripts(text)
    assert blocks, f"{name} has no inline script block — did the runtime vanish?"

    pairs = {")": "(", "]": "[", "}": "{"}
    for idx, block in enumerate(blocks):
        stack = []
        for ch in _strip_js_noise(block):
            if ch in "([{":
                stack.append(ch)
            elif ch in pairs:
                assert stack, (
                    f"{name} inline script #{idx} closes '{ch}' that was never "
                    "opened — the block cannot parse"
                )
                assert stack.pop() == pairs[ch], (
                    f"{name} inline script #{idx} closes '{ch}' against the "
                    "wrong opener — the block cannot parse"
                )
        assert not stack, (
            f"{name} inline script #{idx} ends with {len(stack)} unclosed "
            f"{''.join(stack[-3:])!r} — the block cannot parse, so NOTHING in "
            "it runs (no message listener, no ready signal, no globals)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# MCP bundles must be self-contained
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bundle", ["mcp-artifact-app", "artifact-sandbox",
                                    "mcp-visualization-app"])
def test_mcp_bundle_inlines_every_library(bundle):
    """★A bundle that still names a /libs/ URL is a bundle missing that library.

    MCP app HTML is handed to the host as a STRING and rendered in a srcdoc or
    blob: frame, where `/libs/foo.js` resolves against nothing. So
    _load_html_bundle inlines each vendored file — and when it cannot find one
    it keeps the original tag, which looks like a graceful fallback and is
    actually a silent deletion.

    That is not hypothetical either. mcp-artifact-app.html asked for
    `/libs/artifact-globals.js?v=2`; the loader looked up a file whose name
    ended in `?v=2`, did not find it, and kept the tag. The bundle shipped with
    all 15 artifact globals undefined — KPICard, DataTable, useFilters, EChart,
    fmt and the rest — while React, ECharts, Babel and Tailwind all inlined
    correctly, so the page booted and only died when a dashboard touched a
    helper. One query string, one file, no error.
    """
    from app.routes.mcp import _load_html_bundle

    html = _load_html_bundle(bundle)
    assert "not found" not in html[:200], f"{bundle}: bundle did not load at all"
    leftover = re.findall(r'<script[^>]*\bsrc="(/libs/[^"]+)"', html)
    assert not leftover, (
        f"{bundle} still points at {leftover} instead of inlining it — those "
        "libraries will not load in the sandboxed frame the bundle renders in"
    )


# ═══════════════════════════════════════════════════════════════════════════
# An attribute that is emitted must be read
# ═══════════════════════════════════════════════════════════════════════════

def test_polish_honours_the_ignore_attribute():
    """★`data-polish-ignore` must be consumed, not merely written.

    Both renderers emit it on the narrative <section>. For a while NOTHING
    read it: it appeared exactly once in the whole frontend, at the point of
    emission, and polishScript()'s snapToMeaningful() never looked at it. The
    markup carried an instruction that no code obeyed, which reads as a working
    guard in review and is not one.

    It mattered because snapToMeaningful() returns any <section> on sight and
    the narrative IS a <section> — so the one element polish must never offer
    was the easiest element on the page to pick. Polish hands the selection to
    the model to rewrite; the narrative is composed server-side from figures
    already verified against the data, and is re-emitted on every render, so
    that rewrite would be both discarded and a fresh chance to restate a number
    wrongly.
    """
    ts = TS_BUILDER.read_text(encoding="utf-8")
    py = PY_BUILDER.read_text(encoding="utf-8")

    for name, text in (("artifactIframe.ts", ts), ("artifact_insights_html.py", py)):
        assert "data-polish-ignore" in text, (
            f"{name} no longer marks the narrative as un-pickable"
        )

    # The reader side: an attribute selector, actually consulted by the picker.
    assert "[data-polish-ignore]" in ts, (
        "polishScript() does not query the attribute the renderers emit — the "
        "narrative is selectable again, and snapToMeaningful() returns any "
        "<section> on sight, so it is the easiest thing on the page to pick"
    )
    for handler in ("function onHover", "function onClick"):
        start = ts.index(handler)
        body = ts[start:start + 700]
        assert "isPolishIgnored" in body, (
            f"polishScript()'s {handler} does not consult the ignore list"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Slides never grow a narrative
# ═══════════════════════════════════════════════════════════════════════════

def test_a_slides_document_never_carries_the_narrative():
    """★The section belongs to dashboards only.

    A deck is a sequence of authored slides; appending a "What this means"
    block after the last one would land it outside the slide frame, on the
    deck's own background, at dashboard type sizes. The page and slides
    branches of _build_thumbnail_html are separate templates and only the page
    one interpolates the section — this pins that, including the case where a
    caller passes an insights payload for a deck anyway.

    The page assertion at the end is the positive control: without it, a
    builder that had stopped emitting the section ENTIRELY would pass every
    line above.
    """
    from app.ai.tools.implementations.create_artifact import CreateArtifactTool

    payload = {
        "headline": "should never appear on a deck",
        "findings": [{"text": "nor should this"}],
        "rejected_count": 2,
    }
    tool = CreateArtifactTool()
    data = {"report": {}, "visualizations": []}

    deck = tool._build_thumbnail_html(data, "<div class='slide'>hi</div>",
                                      mode="slides", insights=payload)
    assert "artifact-insights" not in deck
    assert "bow-insight-label" not in deck
    assert "should never appear" not in deck

    page = tool._build_thumbnail_html(data, "<div/>", mode="page", insights=payload)
    assert "artifact-insights" in page, (
        "the page builder stopped emitting the section — the slides "
        "assertions above are passing for the wrong reason"
    )
