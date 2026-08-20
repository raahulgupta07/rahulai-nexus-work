#!/usr/bin/env python3
"""Release gate: render every artifact kind in a REAL browser and fail loudly.

WHY THIS EXISTS
---------------
Every dashboard in the product once rendered "Dashboard failed to render —
React is not defined" for an entire release. Two <script> tags in
frontend/public/artifact-sandbox.html carried `crossorigin`; the artifact
iframe runs at an OPAQUE origin (sandbox="allow-scripts" WITHOUT
allow-same-origin), so it sends `Origin: null`, our own /libs/ responses carry
no Access-Control-Allow-Origin header, and the browser refused to execute
React.

That outage survived 3,330 passing unit tests, a live end-to-end sweep, and a
page-by-page read of every exported artifact — because dashboards were only
ever verified through PDF export, and PDF export inlines the libraries
server-side. The verification path was structurally incapable of observing a
browser-only fault.

★ THE RULE THIS SCRIPT ENFORCES: a server-side render is never proof of a
  browser-side feature. Every release must render the real UI in a real browser.

★ WHY `requestfailed` IS THE LOAD-BEARING ASSERTION
  A CORS-blocked subresource is not a JS exception and not an HTTP error. The
  server logs a clean 200. The page does not throw at load time. The ONLY place
  the browser reports it is the `requestfailed` event
  ("net::ERR_FAILED" / "net::ERR_BLOCKED_BY_RESPONSE"). Everything else in this
  file is a second line of defence; `requestfailed` is the one that would have
  caught the actual outage, at the exact moment it was introduced.

WHAT IT CHECKS, per artifact mode (page / doc / slides)
  1. zero uncaught page errors            (`pageerror`)
  2. zero failed network requests         (`requestfailed`)   ★ see above
  3. no failure text anywhere on the page ("failed to render", "is not defined")
  4. the artifact surface actually produced content — a silently-empty frame
     FAILS. A dashboard that renders nothing without throwing is exactly what
     the CORS bug looked like from the outside.
  5. no rendered text is clipped — every leaf element's own text must fit its
     own box (`scrollWidth <= clientWidth`). A KPI card that draws
     105,150,299,75 where the value is 105,150,299,753 throws nothing, logs
     nothing and looks fine; the user simply reads a wrong number. Same shape
     of invisible fault as #4, so it gets the same treatment: a hard failure.

PORTABILITY (hard requirement)
  Nothing here is tied to a dataset, connector, tenant, credential or Microsoft
  account. Artifacts are DISCOVERED from the database (`artifacts` JOIN
  `reports`); the JWT is minted in-process for the artifact's own owner. If a
  mode has no artifact, that mode SKIPS with a clear message instead of
  failing — a fresh install must be able to run this.

RUN IT
  docker exec -w /app/backend dash-app python scripts/browser_smoke.py

  Scripts must live under /app/backend (not /tmp): Python puts the SCRIPT's
  directory on sys.path, not the cwd, and `import main` — which registers the
  full SQLAlchemy ORM registry — only resolves from there. Hence the sys.path
  fix-up below, which lets the file sit in scripts/ while still importing main.

  Exits 0 when every discovered mode passes, 1 on any failure.

SELF-TEST
  docker exec -w /app/backend dash-app python scripts/browser_smoke.py --self-test

  Proves the gate can still FAIL, in two stages, touching no shipped file and
  no running container state:
    stage 1 — synthetic: an iframe with sandbox="allow-scripts" whose content
              loads `<script crossorigin src="/libs/react-...">`, i.e. the
              original bug rebuilt in the browser.
    stage 2 — live: the REAL dashboard with /libs/react-* refused at the
              network layer, asserting the full checker fails it.
    stage 3 — clipping, both directions: hand-rolled fixed-size KPI tiles in a
              real sandboxed frame MUST be flagged, and the same values through
              the shared <BowKpi> helper MUST come back clean.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field

# ★ Let this file live in scripts/ while still importing the app. `import main`
#   is what registers the complete ORM registry; importing individual models
#   instead blows up resolving relationships (ApiKey, DataSourceApplication...).
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# The app is reachable on port 3000 INSIDE the container. 8095 is the host port
# mapping and is not routable from in here.
BASE_URL = os.environ.get("BROWSER_SMOKE_BASE_URL", "http://localhost:3000")
COOKIE_DOMAIN = os.environ.get("BROWSER_SMOKE_COOKIE_DOMAIN", "localhost")

# Modes we gate on, in the order they are reported.
MODES = ("page", "doc", "slides")

# Text that means "this rendered, but broken". Matched case-insensitively
# against the page and against the artifact frame.
FAILURE_TEXT = (
    "failed to render",
    "is not defined",
    "something went wrong",
    "unexpected error",
)

# ★ Deliberately tiny. Every entry here is a hole in the load-bearing check, so
#   each one must name the exact benign cause. `net::ERR_ABORTED` is emitted for
#   requests the PAGE ITSELF cancelled — a fetch superseded by a newer one, an
#   EventSource closed on unmount, a navigation away. It is never how a blocked
#   or refused response is reported: those are ERR_FAILED,
#   ERR_BLOCKED_BY_RESPONSE, ERR_CONNECTION_*, ERR_NAME_NOT_RESOLVED.
IGNORED_FAILURE_TEXTS = ("net::ERR_ABORTED",)

NAV_TIMEOUT_MS = 90_000
# The artifact iframe transforms JSX with Babel and then draws charts. Give it
# room, but poll so a fast render does not pay the full wait.
RENDER_SETTLE_MS = 15_000
RENDER_POLL_MS = 500


# --------------------------------------------------------------------------
# result plumbing
# --------------------------------------------------------------------------


@dataclass
class ModeResult:
    mode: str
    status: str = "pass"  # pass | fail | skip
    artifact_id: str | None = None
    artifact_title: str | None = None
    report_id: str | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def fail(self, msg: str) -> None:
        self.status = "fail"
        self.problems.append(msg)


# --------------------------------------------------------------------------
# discovery — everything below is derived from the DB, never hardcoded
# --------------------------------------------------------------------------


async def discover(db):
    """Newest completed artifact per mode, with a live report behind it.

    No report id, table name or figure is hardcoded anywhere. A mode with no
    artifact simply returns None and is skipped by the caller.
    """
    from sqlalchemy import select
    from app.models.artifact import Artifact
    from app.models.report import Report

    picked: dict[str, object] = {}
    for mode in MODES:
        q = (
            select(Artifact)
            .join(Report, Report.id == Artifact.report_id)
            .where(
                Artifact.mode == mode,
                Artifact.status == "completed",
                Artifact.deleted_at.is_(None),
                Report.deleted_at.is_(None),
                Report.status != "archived",
            )
            .order_by(Artifact.created_at.desc())
            .limit(1)
        )
        picked[mode] = (await db.execute(q)).scalars().first()
    return picked


async def mint_token(db, user_id: str) -> str:
    """A JWT for the artifact's OWN owner — guarantees access without needing a
    seeded admin, a password, or any external identity provider."""
    from sqlalchemy import select
    from app.core.auth import get_jwt_strategy
    from app.models.user import User

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return await get_jwt_strategy().write_token(user)


# --------------------------------------------------------------------------
# the browser checks
# --------------------------------------------------------------------------


def _attach_listeners(page, errors: list[str], failures: list[str]) -> None:
    page.on("pageerror", lambda e: errors.append(str(e).strip()[:400]))

    def on_requestfailed(req):
        why = (req.failure or "") if isinstance(req.failure, str) else (
            (req.failure or {}).get("errorText", "") if isinstance(req.failure, dict) else ""
        )
        why = why or ""
        if any(ig in why for ig in IGNORED_FAILURE_TEXTS):
            return
        failures.append(f"{why or 'request failed'} :: {req.resource_type} :: {req.url}")

    page.on("requestfailed", on_requestfailed)


async def _artifact_frame(page):
    """The VISIBLE sandboxed artifact iframe, or None.

    Located by ELEMENT, not by URL: the frame is a `srcdoc` document, so its
    URL is `about:srcdoc` and any url-substring filter silently matches
    nothing.

    ★ Visibility is not cosmetic here. ArtifactFrame.vue hides the iframe with
      `v-show`, which leaves the element in the DOM. In doc mode the document
      renders in the host page while an empty, hidden iframe is still present —
      grabbing it blindly reports "frame produced no content" and fails a
      perfectly healthy document.
    """
    for handle in await page.query_selector_all("iframe[sandbox]"):
        if not await handle.is_visible():
            continue
        frame = await handle.content_frame()
        if frame is not None:
            return frame
    return None


async def _frame_content(frame) -> dict:
    return await frame.evaluate(
        """() => {
            const root = document.getElementById('root');
            const text = (document.body && document.body.innerText) || '';
            return {
                react: typeof window.React,
                rootChildren: root ? root.children.length : -1,
                canvases: document.querySelectorAll('canvas').length,
                svgs: document.querySelectorAll('svg').length,
                textLen: text.trim().length,
                text: text.slice(0, 600),
            };
        }"""
    )


# ★ Overflow probe, evaluated INSIDE the artifact frame.
#
#   The artifact iframe is sandboxed WITHOUT allow-same-origin, so it runs at an
#   opaque origin: `iframe.contentDocument` from the host page is a SecurityError
#   and every host-side measurement is structurally impossible. Playwright's
#   `frame.evaluate()` runs the script in the frame's own world, which is the
#   only place these boxes can be measured.
#
#   THE RULE: a leaf element whose own text does not fit its own box is a
#   defect. `scrollWidth > clientWidth` is the browser's own statement that it
#   had to put content outside the padding box — with `overflow:hidden` (KPI
#   cards) the tail is silently CUT, so the user reads 105,150,299,75 and
#   believes it. No exception, no failed request, a serene-looking card. Same
#   class of invisible fault as the React-never-loaded outage above.
_OVERFLOW_JS = """() => {
  const SLACK = 2;              // sub-pixel rounding, not a real overflow
  const out = [];
  const scrollable = (el) => {
    // A pane the user can genuinely scroll is not a clip — its content is
    // reachable. Walk up: any ancestor that scrolls horizontally excuses it.
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  };
  const els = document.querySelectorAll('#root *');
  for (const el of els) {
    // Leaf-ish only: an element whose children are all inline text carries the
    // text itself. A wrapper reports its child's overflow too, which would
    // name the wrong element in the report.
    if (el.querySelector('*')) continue;
    if (el.namespaceURI && el.namespaceURI.indexOf('svg') !== -1) continue;
    const text = (el.textContent || '').trim();
    if (!text) continue;
    if (el.clientWidth <= 0) continue;              // hidden / not laid out
    const over = el.scrollWidth - el.clientWidth;
    if (over <= SLACK) continue;
    if (scrollable(el)) continue;
    const cs = getComputedStyle(el);
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.getAttribute('class') || '').slice(0, 120),
      text: text.slice(0, 80),
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
      overflowPx: over,
      fontSize: cs.fontSize,
      overflowX: cs.overflowX,
      // The nearest ancestor that actually hides the tail — that is the box
      // doing the cutting, and the thing a fix has to change.
      // Walked all the way to <html>: the iframe's own root is a clipping box
      // too, and it is the one that swallows a tail on the right-most card.
      clippedBy: (() => {
        for (let n = el; n; n = n.parentElement) {
          const ox = getComputedStyle(n).overflowX;
          if (ox === 'hidden' || ox === 'clip') {
            return n.tagName.toLowerCase() + '.' + (n.getAttribute('class') || '').slice(0, 60);
          }
        }
        return null;
      })(),
    });
  }
  return out;
}"""


async def _frame_overflows(frame) -> list[dict]:
    return await frame.evaluate(_OVERFLOW_JS)


def _describe_overflow(item: dict) -> str:
    where = f"<{item['tag']} class=\"{item['cls']}\">" if item["cls"] else f"<{item['tag']}>"
    clipped = (
        f" — clipped by <{item['clippedBy']}>"
        if item.get("clippedBy")
        else " — spills outside its box (overlaps neighbours)"
    )
    return (
        f'text does not fit its box: {where} text="{item["text"]}" '
        f"needs {item['scrollWidth']}px, has {item['clientWidth']}px "
        f"(overflow {item['overflowPx']}px at font-size {item['fontSize']}){clipped}"
    )


async def _main_doc_content(page) -> dict:
    return await page.evaluate(
        """() => {
            const doc = document.querySelector('.doc-viewer, .bow-doc, .bow-doc-editor');
            const slides = document.querySelector('.slide-viewer');
            const slideImgs = slides
                ? slides.querySelectorAll('img').length : 0;
            const loadedSlideImgs = slides
                ? Array.from(slides.querySelectorAll('img')).filter(i => i.naturalWidth > 0).length
                : 0;
            return {
                hasDoc: !!doc,
                docTextLen: doc ? (doc.innerText || '').trim().length : 0,
                hasSlideViewer: !!slides,
                slideImgs,
                loadedSlideImgs,
            };
        }"""
    )


def _content_verdict(mode: str, frame_info: dict | None, doc_info: dict) -> tuple[bool, str]:
    """Did the artifact surface actually produce something?

    ★ A silently-empty frame must FAIL. Rendering nothing without throwing is
      precisely how the CORS outage presented: React never loaded, so the
      artifact code never ran, so #root stayed empty — no exception, no HTTP
      error, a perfectly serene blank panel.
    """
    if frame_info is not None:
        rich = (
            frame_info["rootChildren"] > 0
            or frame_info["canvases"] > 0
            or frame_info["svgs"] > 0
            or frame_info["textLen"] > 40
        )
        if rich:
            return True, (
                f"frame: React={frame_info['react']} root_children="
                f"{frame_info['rootChildren']} canvas={frame_info['canvases']} "
                f"svg={frame_info['svgs']} text={frame_info['textLen']}ch"
            )
        return False, (
            "artifact frame produced NO content "
            f"(React={frame_info['react']} root_children={frame_info['rootChildren']} "
            f"canvas={frame_info['canvases']} svg={frame_info['svgs']} "
            f"text={frame_info['textLen']}ch) — this is the shape of the "
            "React-never-loaded outage"
        )

    if mode == "doc":
        if doc_info["hasDoc"] and doc_info["docTextLen"] > 40:
            return True, f"doc body: {doc_info['docTextLen']}ch"
        return False, (
            "document artifact rendered no readable body "
            f"(container={doc_info['hasDoc']} text={doc_info['docTextLen']}ch)"
        )

    if mode == "slides" and doc_info["hasSlideViewer"]:
        if doc_info["loadedSlideImgs"] > 0:
            return True, f"slide viewer: {doc_info['loadedSlideImgs']} slide image(s) loaded"
        return False, (
            "slide viewer present but no slide image loaded "
            f"({doc_info['slideImgs']} <img> tags, {doc_info['loadedSlideImgs']} decoded)"
        )

    return False, (
        "no artifact surface found — neither a sandboxed iframe nor a "
        "document/slide viewer rendered"
    )


async def check_mode(ctx, mode: str, artifact, block_react: bool = False) -> ModeResult:
    res = ModeResult(
        mode=mode,
        artifact_id=str(artifact.id),
        artifact_title=artifact.title,
        report_id=str(artifact.report_id),
    )
    started = time.time()

    errors: list[str] = []
    failures: list[str] = []
    page = await ctx.new_page()
    _attach_listeners(page, errors, failures)

    if block_react:
        # ★ SELF-TEST ONLY. Reproduces the outage against the REAL dashboard by
        #   refusing React at the network layer, exactly as the browser did when
        #   `crossorigin` put the tag in CORS mode and /libs/ answered without
        #   Access-Control-Allow-Origin. Nothing on disk and nothing in the
        #   running container is modified — the block lives in this page only.
        await page.route("**/libs/react-*", lambda route: route.abort("failed"))

    try:
        await page.goto(
            f"{BASE_URL}/reports/{artifact.report_id}",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
        # The report page auto-opens the artifact pane when the report has
        # artifacts; `artifact:select` then pins the exact one we discovered,
        # so a report holding several artifacts still tests the intended mode.
        await page.wait_for_timeout(2500)
        await page.evaluate(
            """(id) => window.dispatchEvent(
                new CustomEvent('artifact:select', { detail: { artifact_id: id } }))""",
            str(artifact.id),
        )

        # Poll for content instead of sleeping the full budget.
        frame_info: dict | None = None
        doc_info: dict = {}
        deadline = time.time() + RENDER_SETTLE_MS / 1000
        ok = False
        detail = "never evaluated"
        while time.time() < deadline:
            await page.wait_for_timeout(RENDER_POLL_MS)
            frame = await _artifact_frame(page)
            frame_info = await _frame_content(frame) if frame else None
            doc_info = await _main_doc_content(page)
            ok, detail = _content_verdict(mode, frame_info, doc_info)
            if ok:
                break

        if ok:
            res.notes.append(detail)
        else:
            res.fail(detail)

        # ★ Clipped-value check. Runs only once the frame has settled, because
        #   a mid-render layout measures boxes that do not exist yet.
        frame = await _artifact_frame(page)
        if frame is not None:
            overflows = await _frame_overflows(frame)
            for item in overflows:
                res.fail(_describe_overflow(item))

        # Failure text, on the host page and inside the artifact frame.
        page_text = await page.evaluate("document.body.innerText || ''")
        haystacks = [("page", page_text)]
        if frame_info:
            haystacks.append(("artifact frame", frame_info.get("text", "")))
        for where, text in haystacks:
            low = text.lower()
            for needle in FAILURE_TEXT:
                if needle in low:
                    idx = low.index(needle)
                    excerpt = text[max(0, idx - 80): idx + 120].replace("\n", " ")
                    res.fail(f'failure text on {where}: "{needle}" — …{excerpt.strip()}…')

    except Exception as exc:  # noqa: BLE001 — any browser fault is a gate failure
        res.fail(f"{type(exc).__name__}: {exc}")
    finally:
        for e in errors:
            res.fail(f"page error: {e}")
        # ★ The one that would have caught the outage.
        for f in failures:
            res.fail(f"failed request: {f}")
        res.elapsed_s = time.time() - started
        await page.close()

    return res


# --------------------------------------------------------------------------
# self-test: reproduce the original outage and prove the gate still bites
# --------------------------------------------------------------------------

# Byte-for-byte the shape of the bug: an iframe sandboxed WITHOUT
# allow-same-origin (opaque origin ⇒ `Origin: null`) whose content pulls React
# with `crossorigin`, i.e. in CORS mode, from a /libs/ path that sends no
# Access-Control-Allow-Origin. Built here in the browser; NO shipped file is
# touched and the running app is not modified.
_BROKEN_IFRAME_DOC = """<!DOCTYPE html>
<html><head>
  <script crossorigin src="/libs/react-18.production.min.js"></script>
  <script crossorigin src="/libs/react-dom-18.production.min.js"></script>
</head><body>
  <div id="root">Loading...</div>
  <script>
    // Same failure mode as the real sandbox: the artifact code runs, React is
    // absent, #root is never populated.
    try { ReactDOM.createRoot(document.getElementById('root')).render(
      React.createElement('div', null, 'ok')); } catch (e) {}
  </script>
</body></html>"""

_HOST_PAGE = (
    "<!DOCTYPE html><html><body><iframe sandbox=\"allow-scripts\" "
    "style=\"width:600px;height:400px\" srcdoc='__DOC__'></iframe></body></html>"
)


async def self_test_live(ctx, artifact) -> bool:
    """Stage 2: the REAL dashboard, with React refused at the network layer.

    Stage 1 proves the checker's logic on a synthetic page. This proves it end
    to end on the actual shipped UI and the actual artifact — the strongest
    statement available without reintroducing the bug into a shipped file.
    """
    print("\n--- self-test stage 2: real dashboard, React blocked in-browser ---")
    if artifact is None:
        print("  SKIP: no completed 'page' artifact to render against.")
        return True

    res = await check_mode(ctx, "page", artifact, block_react=True)
    print(f"  artifact {res.artifact_id} ({res.artifact_title!r})")
    print(f"  verdict  : {res.status.upper()} {'(correct)' if res.status == 'fail' else '(BAD — gate is blind)'}")
    for prob in res.problems:
        print(f"    ✗ {prob}")
    caught = res.status == "fail" and any("/libs/react" in p for p in res.problems)
    print(f"  requestfailed named the blocked React: {'YES' if caught else 'NO'}")
    return caught


# --------------------------------------------------------------------------
# self-test stage 3: the clipped-value check, both directions
# --------------------------------------------------------------------------

# The real sandbox document, minus the data bridge: same libs, same load order,
# same opaque-origin iframe. Built here in the browser so the check exercises
# the SHIPPED /libs/artifact-globals.js rather than a copy of it.
_SANDBOX_DOC = """<!DOCTYPE html>
<html><head>
  <script src="/libs/tailwindcss-3.4.16.js"></script>
  <script src="/libs/react-18.production.min.js"></script>
  <script src="/libs/react-dom-18.production.min.js"></script>
  <script src="/libs/babel-standalone.min.js"></script>
  <script src="/libs/echarts-5.min.js"></script>
  <!-- ★pdf.js, as `artifactIframe.ts` loads it. The static
       `artifact-sandbox.html` does NOT include it, so a fixture copied from
       that page renders the viewer's "Open PDF" fallback instead of the
       document — which cost a false PASS here before the canvas assertion
       went in. The fixture must mirror the frame the product actually
       builds for dashboards. -->
  <script src="/libs/pdf.min.js"></script>
  <script src="/libs/artifact-globals.js"></script>
</head><body><div id="root"></div>
<script type="text/babel">
function App() { return (__BODY__); }
ReactDOM.createRoot(document.getElementById("root")).render(<App />);
</script>
</body></html>"""

# Deliberately narrow columns — this is the shape a real dashboard row has, and
# the width at which a fixed type size starts losing digits.
_GRID = '<div className="grid grid-cols-3 gap-3" style={{ width: 660 }}>'

# The three values both fixtures render: a long number, a long non-numeric
# string, and a short one. Nothing about them is dataset-specific — the point is
# that the helper is told nothing about magnitude, unit or type.
_FIXTURE_VALUES = ("105,150,299,753", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123", "42")

# NEGATIVE CONTROL: exactly what the model writes when left to hand-roll a
# tile. If the checker does NOT flag this, the checker is broken.
_CLIPPING_BODY = _GRID + """
  <div className="rounded-lg border bg-white px-4 py-3">
    <div className="text-xs uppercase text-slate-500">Total Sales</div>
    <div className="mt-1 text-3xl font-semibold tabular-nums">105,150,299,753</div>
  </div>
  <div className="rounded-lg border bg-white px-4 py-3">
    <div className="text-xs uppercase text-slate-500">Reference</div>
    <div className="mt-1 text-3xl font-semibold">ABCDEFGHIJKLMNOPQRSTUVWXYZ0123</div>
  </div>
  <div className="rounded-lg border bg-white px-4 py-3">
    <div className="text-xs uppercase text-slate-500">Orders</div>
    <div className="mt-1 text-3xl font-semibold tabular-nums">42</div>
  </div>
</div>"""

# THE FIX: the same three values through the shared helper. Same widths, same
# grid. Nothing about the values is known to the helper — one is a 15-char
# number, one a 30-char identifier, one two digits.
_FIXED_BODY = _GRID + """
  <BowKpi title="Total Sales" value="105,150,299,753" subtitle="SUM(sales)" />
  <BowKpi title="Reference" value="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123" />
  <BowKpi title="Orders" value="42" />
</div>"""


async def _render_sandbox(ctx, body_jsx: str) -> list[dict]:
    """Render a hand-constructed artifact in a real sandboxed frame and return
    every element whose text does not fit its box."""
    doc = _SANDBOX_DOC.replace("__BODY__", body_jsx)
    page = await ctx.new_page()
    # ★ The host page must be a REAL navigation to the app's origin, and the
    #   iframe must be injected into it — not `page.set_content` and not
    #   `route.fulfill`:
    #     • set_content leaves the base URL at about:blank, so the srcdoc frame
    #       inherits it and `/libs/…` cannot resolve at all;
    #     • a fulfilled response never touched the network, so Chromium files
    #       the document under an unknown address space and Private Network
    #       Access blocks every subresource request it makes to loopback
    #       ("Permission was denied for this request to access the `loopback`
    #       address space") — every library silently fails and the fixture
    #       measures an empty frame.
    #   A real loopback document is in the loopback address space, so its
    #   srcdoc child may load /libs/ exactly as the shipped ArtifactFrame does.
    await page.goto(
        f"{BASE_URL}/artifact-sandbox.html",
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS,
    )
    await page.evaluate(
        """(srcdoc) => {
            const f = document.createElement('iframe');
            // Same sandbox attribute as the shipped frame: opaque origin, which
            // is the environment the measurement has to hold in.
            f.setAttribute('sandbox', 'allow-scripts allow-downloads');
            f.style.cssText = 'position:fixed;left:0;top:0;width:760px;height:520px;border:0;background:#fff;z-index:99999';
            f.srcdoc = srcdoc;
            document.body.appendChild(f);
        }""",
        doc,
    )
    await page.wait_for_timeout(4000)  # Tailwind JIT + Babel + fit measurement
    frame = await _artifact_frame(page)
    if frame is None:
        await page.close()
        return [{"__error__": "no sandboxed frame rendered"}]
    # ★ A stage that measures an EMPTY frame reports "0 clipped elements" and
    #   looks like a pass. Assert the fixture actually rendered before believing
    #   anything it says.
    info = await _frame_content(frame)
    if info["rootChildren"] <= 0:
        await page.close()
        return [{"__error__": f"fixture did not render: {info}"}]
    # ★ "No clipped elements" is also what a card that dropped the value
    #   entirely would report. Every value must be present and complete —
    #   a fit that works by not drawing the number is not a fit.
    shown = info.get("text", "")
    for expected in _FIXTURE_VALUES:
        if expected not in shown:
            await page.close()
            return [{"__error__": f"value {expected!r} missing from the rendered frame"}]
    overflows = await _frame_overflows(frame)
    await page.close()
    return overflows


async def self_test_clipping(ctx) -> bool:
    """Stage 3: prove the clipped-value check bites, and that the shared KPI
    helper is what stops it biting.

    ★ Both directions are required. A checker that never fires is not a guard,
      and a fix that is never measured is not a fix.
    """
    print("\n--- self-test stage 3: clipped value, negative control + helper ---")

    control = await _render_sandbox(ctx, _CLIPPING_BODY)
    print(f"  hand-rolled fixed-size tiles : {len(control)} clipped element(s)"
          f" {'(correct)' if control else '(BAD — gate is blind)'}")
    for item in control:
        print(f"    ✗ {_describe_overflow(item)}" if "tag" in item else f"    ✗ {item}")

    fixed = await _render_sandbox(ctx, _FIXED_BODY)
    print(f"  <BowKpi> tiles               : {len(fixed)} clipped element(s)"
          f" {'(correct)' if not fixed else '(BAD — helper does not fit)'}")
    for item in fixed:
        print(f"    ✗ {_describe_overflow(item)}" if "tag" in item else f"    ✗ {item}")

    caught = any("tag" in i for i in control)
    clean = len(fixed) == 0
    print(f"  STAGE 3 {'PASSED' if caught and clean else 'FAILED'} — "
          f"detector fires on the defect: {'YES' if caught else 'NO'}, "
          f"helper renders clean: {'YES' if clean else 'NO'}")
    return caught and clean


# ---------------------------------------------------------------------------
# Stage 4 — the two things an opaque-origin frame is most likely to lose.
#
# Both were fixed and neither had ever been exercised in a browser, which is the
# only place either can fail. They share a root cause: the artifact frame runs
# at an OPAQUE origin (sandbox without allow-same-origin), so it is cross-origin
# to our own server. `fetch()` is blocked, and a download is refused outright
# unless the frame also carries allow-downloads.
#
#   • PDF embed — the frame cannot fetch its own token URL, so the host has to
#     resolve the bytes and hand them in as a data: URI.
#   • CSV download — `exportCSV` builds a blob and clicks a link. Without
#     allow-downloads on the iframe, Chromium refuses it silently: no error, no
#     file, a button that does nothing.
#
# ★ A one-pixel PDF, built here rather than shipped, so the stage carries its
#   own fixture and cannot pass by finding somebody else's file.
# ---------------------------------------------------------------------------

# ★ Structurally VALID — real xref table and startxref offset. A truncated
#   "PDF-ish" blob is rejected by pdf.js, which would fail this stage for a
#   reason that has nothing to do with the frame. Built here so the stage owns
#   its fixture and cannot pass by finding somebody else's file.
_TINY_PDF = (
    "JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2Jq"
    "CjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2Jq"
    "CjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAyMDAg"
    "MTAwXSAvQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIgPj4g"
    "Pj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA0NCA+PgpzdHJlYW0KQlQgL0YxIDE4IFRm"
    "IDIwIDQwIFRkIChTTU9LRSBQREYpIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKNSAwIG9iago8PCAv"
    "VHlwZSAvRm9udCAvU3VidHlwZSAvVHlwZTEgL0Jhc2VGb250IC9IZWx2ZXRpY2EgPj4KZW5kb2Jq"
    "CnhyZWYKMCA2CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAwOSAwMDAwMCBuIAowMDAwMDAw"
    "MDU4IDAwMDAwIG4gCjAwMDAwMDAxMTUgMDAwMDAgbiAKMDAwMDAwMDI0MSAwMDAwMCBuIAowMDAw"
    "MDAwMzMwIDAwMDAwIG4gCnRyYWlsZXIKPDwgL1NpemUgNiAvUm9vdCAxIDAgUiA+PgpzdGFydHhy"
    "ZWYKNDAwCiUlRU9GCg=="
)

_DOWNLOAD_BODY = """
<div className="p-6">
  <button id="csv-btn" className="px-3 py-2 border rounded"
    onClick={() => exportCSV(
      [{region: 'north', total: 1200}, {region: 'south', total: 900}],
      {filename: 'smoke-export.csv'}
    )}>Download CSV</button>
</div>"""

# ★ `BowPdfViewer` directly, not `BowFile`. BowFile resolves `props.id` against
#   the run's file list and renders "File not found" for anything it cannot look
#   up — so a fixture handing it an inline object tests the lookup, not the
#   embed. The fix under test is that the viewer accepts inlined BYTES, because
#   an opaque-origin frame cannot fetch its own token URL. That is this.
_PDF_BODY = """
<div className="p-6" style={{width: '700px'}}>
  <BowPdfViewer src="data:application/pdf;base64,__PDF_B64__"
                href="" filename="smoke.pdf" height={420} />
</div>"""


async def _render_page(ctx, body_jsx: str):
    """Render a fixture in a real sandboxed frame; return (page, frame)."""
    doc = _SANDBOX_DOC.replace("__BODY__", body_jsx)
    page = await ctx.new_page()
    await page.goto(
        f"{BASE_URL}/artifact-sandbox.html",
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS,
    )
    await page.evaluate(
        """(srcdoc) => {
            const f = document.createElement('iframe');
            f.setAttribute('sandbox', 'allow-scripts allow-downloads');
            f.style.cssText = 'position:fixed;left:0;top:0;width:900px;height:640px;border:0;background:#fff;z-index:99999';
            f.srcdoc = srcdoc;
            document.body.appendChild(f);
        }""",
        doc,
    )
    await page.wait_for_timeout(4000)
    return page, await _artifact_frame(page)


async def self_test_downloads(ctx) -> bool:
    """Stage 4: a CSV download actually arrives, and a PDF actually renders."""
    print("\n--- self-test stage 4: CSV download + embedded PDF ---")
    ok = True

    # --- CSV ---------------------------------------------------------------
    page, frame = await _render_page(ctx, _DOWNLOAD_BODY)
    if frame is None:
        print("  CSV : FAILED — no sandboxed frame rendered")
        ok = False
    else:
        try:
            async with page.expect_download(timeout=15000) as info:
                await frame.click("#csv-btn")
            download = await info.value
            name = download.suggested_filename
            path = await download.path()
            size = path.stat().st_size if path else 0
            # ★ A download EVENT is not a file. Chromium reports the event before
            #   the body lands, and an empty file is exactly what a blocked frame
            #   would produce if the event ever fired at all.
            body = path.read_text(encoding="utf-8") if path and size else ""
            good = size > 0 and "region" in body and "north" in body
            print(f"  CSV : {'PASSED' if good else 'FAILED'} — {name!r}, {size} bytes")
            if not good:
                print(f"        content was {body[:120]!r}")
                ok = False
        except Exception as exc:
            print(f"  CSV : FAILED — no download arrived ({type(exc).__name__})")
            print("        this is the shape of a frame missing allow-downloads:")
            print("        the click succeeds, nothing is refused out loud, no file appears")
            ok = False
    await page.close()

    # --- PDF ---------------------------------------------------------------
    page, frame = await _render_page(ctx, _PDF_BODY.replace("__PDF_B64__", _TINY_PDF))
    if frame is None:
        print("  PDF : FAILED — no sandboxed frame rendered")
        ok = False
    else:
        state = await frame.evaluate(
            """() => {
                const root = document.getElementById('root');
                return {
                    canvases: document.querySelectorAll('canvas').length,
                    embeds: document.querySelectorAll('embed,object,iframe').length,
                    text: (root ? root.innerText : '').slice(0, 300),
                    children: root ? root.children.length : 0,
                };
            }"""
        )
        # ★ A CANVAS is the whole assertion. The viewer's fallback is an "Open
        #   PDF" card that shows the filename and looks perfectly healthy, so
        #   accepting "the filename is on screen" would pass on a PDF that never
        #   rendered — which is exactly what this stage exists to catch. This
        #   check was written the lenient way first and did pass that way; the
        #   canvas count is what exposed it.
        drew = state["canvases"] > 0 or state["embeds"] > 0
        broke = any(
            w in (state["text"] or "").lower()
            for w in ("failed", "could not", "unable", "error")
        )
        good = state["children"] > 0 and drew and not broke
        print(f"  PDF : {'PASSED' if good else 'FAILED'} — canvases={state['canvases']} "
              f"embeds={state['embeds']} children={state['children']}")
        if not good:
            print(f"        rendered text was {state['text']!r}")
            if "smoke.pdf" in (state["text"] or "") and not drew:
                print("        the fallback card rendered, not the document — "
                      "pdf.js is missing or refused the bytes")
            ok = False
    await page.close()

    print(f"  STAGE 4 {'PASSED' if ok else 'FAILED'}")
    return ok


async def self_test(ctx) -> bool:
    """Return True when the checker correctly flags the reproduced bug."""
    errors: list[str] = []
    failures: list[str] = []
    page = await ctx.new_page()
    _attach_listeners(page, errors, failures)

    # Serve the host page from the app's own origin so /libs/ resolves and the
    # request is genuinely same-server-but-cross-origin, as in production.
    await page.route(
        f"{BASE_URL}/__browser_smoke_selftest__",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=_HOST_PAGE.replace("__DOC__", _BROKEN_IFRAME_DOC.replace("'", "&#39;")),
        ),
    )
    await page.goto(
        f"{BASE_URL}/__browser_smoke_selftest__",
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS,
    )
    await page.wait_for_timeout(4000)

    frame = await _artifact_frame(page)
    frame_info = await _frame_content(frame) if frame else None
    doc_info = await _main_doc_content(page)
    ok, detail = _content_verdict("page", frame_info, doc_info)

    print("\n--- self-test stage 1: synthetic crossorigin/opaque-origin outage ---")
    print(f"  content check   : {'PASS (BAD — gate is blind)' if ok else 'FAIL (correct)'}")
    print(f"    {detail}")
    print(f"  failed requests : {len(failures)}")
    for f in failures:
        print(f"    {f}")
    print(f"  page errors     : {len(errors)}")
    for e in errors:
        print(f"    {e}")

    await page.close()

    caught_by_requestfailed = any("/libs/react" in f for f in failures)
    caught_by_content = not ok
    verdict = caught_by_requestfailed and caught_by_content
    print(
        "\n  requestfailed caught it : "
        f"{'YES' if caught_by_requestfailed else 'NO'}   "
        f"empty-frame caught it : {'YES' if caught_by_content else 'NO'}"
    )
    print(f"  STAGE 1 {'PASSED — the gate still bites' if verdict else 'FAILED — the gate is blind'}")
    return verdict


# --------------------------------------------------------------------------


async def run(args) -> int:
    import main  # noqa: F401  ★ registers the full ORM registry. Must precede models.
    from app.dependencies import async_session_maker
    from playwright.async_api import async_playwright

    async with async_session_maker() as db:
        found = await discover(db)
        if args.artifact:
            # ★ The gate checks the NEWEST artifact per mode, so a defect in an
            #   OLDER dashboard is invisible to it. When a specific one is
            #   reported broken, check that one — same checks, no new code path.
            from sqlalchemy import select
            from app.models.artifact import Artifact

            one = (
                await db.execute(select(Artifact).where(Artifact.id == args.artifact))
            ).scalars().first()
            if one is None:
                print(f"no artifact with id {args.artifact}")
                return 1
            found = {one.mode: one}
        # ★ One token per OWNER, not one token for the whole run.
        #
        #   This used to mint a single token from whichever artifact came first
        #   and browse every report with it. Reports are private to the person
        #   who made them until they are shared, so on any install where two
        #   people have each built something, the gate opened somebody else's
        #   draft as the wrong user, got the empty page it deserved, and
        #   reported the PRODUCT as broken: "document artifact rendered no
        #   readable body (container=False text=0ch)".
        #
        #   Measured on the dev server, 0.0.543.11: page owned by one member,
        #   doc and slides by another, all three drafts. Two of three modes
        #   failed, both at the full 17.8s timeout, and both rendered perfectly
        #   when checked one at a time — because a single-artifact run happens
        #   to mint the right owner's token by accident.
        #
        #   A release gate that fails on a second author is worse than no gate:
        #   it fails on exactly the installations that are being used.
        tokens: dict[str, str] = {}
        for a in found.values():
            if a is not None and a.user_id not in tokens:
                tokens[a.user_id] = await mint_token(db, a.user_id)
        owner = next((a.user_id for a in found.values() if a is not None), None)
        token = tokens.get(owner) if owner else None

    print(f"CityAgent Insights — browser smoke gate   ({BASE_URL})")
    print("=" * 72)

    results: list[ModeResult] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        # ★ accept_downloads is required or stage 4 can never observe a CSV: with
        #   it off Playwright cancels the transfer, which looks identical to the
        #   frame being refused permission — the exact defect being tested for.
        ctx = await browser.new_context(
            viewport={"width": 1500, "height": 1000}, accept_downloads=True
        )
        if token:
            # ★ Cookie name is exactly `auth.token`. The nuxt.config nested
            #   `cookie.name` option is ignored by sidebase-auth.
            await ctx.add_cookies([{
                "name": "auth.token", "value": token,
                "domain": COOKIE_DOMAIN, "path": "/",
            }])

        if args.self_test:
            stage1 = await self_test(ctx)
            stage2 = await self_test_live(ctx, found.get("page"))
            stage3 = await self_test_clipping(ctx)
            stage4 = await self_test_downloads(ctx)
            await browser.close()
            passed = stage1 and stage2 and stage3 and stage4
            print("\n" + "=" * 72)
            print(
                f"SELF-TEST {'PASSED' if passed else 'FAILED'} — "
                f"synthetic {'ok' if stage1 else 'BLIND'}, live {'ok' if stage2 else 'BLIND'}, "
                f"clipping {'ok' if stage3 else 'BLIND'}, "
                f"downloads {'ok' if stage4 else 'BLIND'}"
            )
            return 0 if passed else 1

        if token is None:
            print("\nSKIP ALL: no completed artifact of any mode in this database.")
            print("  Nothing to render. This is a clean skip, not a failure —")
            print("  a fresh install has no artifacts until someone builds one.")
            await browser.close()
            return 0

        for mode in MODES:
            artifact = found.get(mode)
            if artifact is None:
                r = ModeResult(mode=mode, status="skip")
                r.notes.append(
                    f"no completed '{mode}' artifact in this database — nothing to render"
                )
                results.append(r)
                print(f"\n[{mode:6}] SKIP  {r.notes[0]}")
                continue

            print(f"\n[{mode:6}] checking artifact {artifact.id} "
                  f"({artifact.title or 'Untitled'!r}) on report {artifact.report_id} …")
            # ★ Sign in as THIS artifact's owner before opening their report.
            #   Cleared first: add_cookies does not replace a cookie of the same
            #   name reliably across contexts, and a stale token here would be
            #   invisible — it authenticates fine, it simply belongs to somebody
            #   who cannot see the page.
            mode_token = tokens.get(artifact.user_id)
            if mode_token and mode_token != token:
                await ctx.clear_cookies()
                await ctx.add_cookies([{
                    "name": "auth.token", "value": mode_token,
                    "domain": COOKIE_DOMAIN, "path": "/",
                }])
                token = mode_token
            r = await check_mode(ctx, mode, artifact)
            results.append(r)
            head = "PASS" if r.status == "pass" else "FAIL"
            print(f"[{mode:6}] {head}  ({r.elapsed_s:.1f}s)")
            for n in r.notes:
                print(f"          {n}")
            for prob in r.problems:
                print(f"          ✗ {prob}")

        await browser.close()

    print("\n" + "=" * 72)
    failed = [r for r in results if r.status == "fail"]
    skipped = [r for r in results if r.status == "skip"]
    passed = [r for r in results if r.status == "pass"]
    print(f"passed {len(passed)}   failed {len(failed)}   skipped {len(skipped)}")

    if failed:
        print("\nFAILURES")
        for r in failed:
            print(f"  {r.mode}: artifact {r.artifact_id} "
                  f"({r.artifact_title!r}) report {r.report_id}")
            for prob in r.problems:
                print(f"    - {prob}")
        print("\nRELEASE GATE FAILED — do not ship.")
        return 1

    print("\nRELEASE GATE PASSED.")
    return 0


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="reproduce the crossorigin outage in an isolated page and prove "
             "the checker flags it (touches no shipped file)",
    )
    ap.add_argument(
        "--artifact",
        metavar="ID",
        help="check this artifact instead of the newest one of each mode — for "
             "re-testing a specific dashboard someone reported as broken",
    )
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main_cli())
