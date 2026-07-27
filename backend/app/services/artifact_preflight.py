"""PHASE 3 — parse a generated dashboard before it is stored.

Why this exists
---------------
A generated artifact is React/JSX inside ``<script type="text/babel">``. Nothing
validated it. A build that produced unparseable code was still returned as
``success``, stored with status ``completed``, and the failure was discovered by
the BROWSER, in front of the user:

    Dashboard failed to render
    /Inline Babel script: Missing semicolon. (3:8)

DEF-008 added a structural guard that catches the specific case of the model
replying with prose instead of code. It cannot catch genuinely malformed JSX,
because JSX is not Python and ``compile()`` will not read it.

Why Babel-in-Chromium rather than a parser dependency
-----------------------------------------------------
The image has no ``node``, no ``npx`` and no Python JS parser, so a real parse
looked impossible without shipping a JavaScript toolchain. But two things are
ALREADY here for other features:

  * Chromium + Playwright — installed for server-side PDF export
  * ``/app/frontend/dist/libs/babel-standalone.min.js`` — shipped for the
    artifact sandbox, which is the very thing that transforms this code at
    render time

So we run the SAME Babel the browser runs, over the same code, and read the same
error. This is not an approximation of the render check — for parse errors it is
the identical operation, just performed before storing instead of after.

It deliberately does NOT render the component. Rendering needs data, layout and
network, and would turn a deterministic syntax check into a flaky one. Parse
errors are what reach users as "failed to render"; runtime errors inside a
component are a separate problem and are not claimed to be covered here.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Shipped with the frontend for the artifact sandbox; this is the exact file the
# browser loads when it renders a dashboard.
_BABEL_PATHS = (
    "/app/frontend/dist/libs/babel-standalone.min.js",
    os.path.join(os.path.dirname(__file__), "../../../frontend/public/libs/babel-standalone.min.js"),
)

# A parse of a large dashboard is milliseconds; the budget is for browser start.
_PREFLIGHT_TIMEOUT_MS = 20_000


def _find_babel() -> Optional[str]:
    for p in _BABEL_PATHS:
        rp = os.path.realpath(p)
        if os.path.isfile(rp):
            return rp
    return None


def _strip_script_tags(code: str) -> str:
    """Return the JSX inside the <script type="text/babel"> wrapper.

    Babel transforms JavaScript, not HTML — handing it the wrapper would report a
    syntax error on the tag itself and every artifact would fail preflight.
    """
    import re

    m = re.search(
        r'<script\s+type=["\']text/babel["\']>\s*([\s\S]*?)\s*</script>',
        code,
        re.IGNORECASE,
    )
    return m.group(1) if m else code


async def check_artifact_code(code: str) -> Tuple[bool, Optional[str]]:
    """Parse ``code`` with Babel. Returns ``(ok, error_message)``.

    ★ Fails OPEN. If Babel is missing, Chromium will not start, or anything else
    goes wrong with the check itself, this returns ``(True, None)``. A broken
    preflight must never block a dashboard that would have rendered perfectly
    well — the check exists to catch the model's mistakes, not to add a new way
    for the platform to fail.
    """
    inner = _strip_script_tags(code or "")
    if not inner.strip():
        return False, "The generated artifact contained no code."

    babel_path = _find_babel()
    if not babel_path:
        logger.warning("artifact preflight: babel-standalone not found — skipping check")
        return True, None

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("artifact preflight: playwright unavailable (%s) — skipping check", exc)
        return True, None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content("<!doctype html><html><body></body></html>")
                # ★ `add_script_tag(path=...)` INLINES the file's contents. A
                # `<script src="file://…">` inside set_content() is silently
                # blocked by Chromium — the page loads, Babel is never defined,
                # and the check then fails open on every artifact while looking
                # like it passed. Cost an hour the first time; do not "simplify"
                # this back to a src attribute.
                await page.add_script_tag(path=babel_path)
                # Same presets the sandbox uses to transform artifact code.
                result = await page.evaluate(
                    """(src) => {
                        try {
                            if (typeof Babel === 'undefined') return {skipped: true};
                            Babel.transform(src, {presets: ['react']});
                            return {ok: true};
                        } catch (e) {
                            return {ok: false, message: String(e && e.message ? e.message : e)};
                        }
                    }""",
                    inner,
                )
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning("artifact preflight: check could not run (%s) — allowing artifact", exc)
        return True, None

    if not isinstance(result, dict) or result.get("skipped"):
        logger.warning("artifact preflight: Babel did not load — allowing artifact")
        return True, None
    if result.get("ok"):
        return True, None

    message = result.get("message") or "Unknown parse error"
    logger.warning("artifact preflight: generated code does not parse — %s", message)
    return False, message
