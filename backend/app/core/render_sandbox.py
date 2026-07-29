"""Shared guards for the headless-Chromium renderers.

Four services drive a real browser over content the model wrote: dashboard PDF
export, document PDF export, the chart pre-render for documents, and the
artifact render preflight. Every one of them executes attacker-influenceable
JavaScript inside the server's own network.

That gives a blind SSRF with a read-back channel: artifact code can
`fetch('http://169.254.169.254/…')` or hit an internal host, then draw the
response into the page — and the response comes back to whoever asked for the
export, printed in the PDF.

Two guards live here:

  block_external_requests(page)
      Refuses every request the page makes that is not local content. This is
      the fix — no network, no SSRF, no metadata service, no internal hosts.

  launch_chromium(p, args=…)
      Tries to start Chromium WITH its own sandbox and only falls back to
      `--no-sandbox` if the platform refuses, saying so in the log. Every
      renderer used to pass `--no-sandbox` unconditionally, which means a
      renderer bug is a container-level compromise rather than a tab-level one.

★Consequence worth knowing: a dashboard that references a REMOTE image or font
will render without it in the PDF. That is the intended trade — the same
mechanism is what an attacker uses to reach inside the network.
"""
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Local content only. `file:` is how the dashboard renderer loads its own
# temporary HTML and the vendored libraries next to it; `data:`/`blob:` are
# inlined bytes; `about:` is the blank page every render starts from.
_LOCAL_SCHEMES = ("file:", "data:", "blob:", "about:")


def _is_local(url: str) -> bool:
    return url.startswith(_LOCAL_SCHEMES)


async def block_external_requests(page, allow: Optional[Iterable[str]] = None) -> None:
    """Abort every request that leaves the machine.

    `allow` is an optional list of exact URL prefixes to permit — nothing uses
    it today, and anything that does should be a specific local origin, never a
    wildcard.
    """
    allowed = tuple(allow or ())

    async def _route(route, request):
        url = request.url
        if _is_local(url) or (allowed and url.startswith(allowed)):
            await route.continue_()
            return
        logger.warning("Render sandbox blocked an outbound request to %s", url)
        await route.abort()

    await page.route("**/*", _route)


async def launch_chromium(playwright, args: Optional[list] = None):
    """Chromium with its own sandbox where the platform allows it.

    ★`--no-sandbox` was passed unconditionally by all four renderers. It is
    often genuinely required (Docker's default seccomp profile blocks the user
    namespaces Chromium's sandbox needs), which is exactly why nobody removed
    it — but "often required" is not "always required", and paying that price
    on hosts that do not need it is free risk.

    So: try sandboxed, and only drop it if the browser refuses to start. The
    fallback is logged, so an operator can see which mode their box is in.
    """
    extra = list(args or [])
    try:
        return await playwright.chromium.launch(args=extra)
    except Exception as e:
        logger.warning(
            "Chromium refused to start with its sandbox (%s); falling back to "
            "--no-sandbox. Renderer output is unchanged, isolation is weaker.", e,
        )
        return await playwright.chromium.launch(args=["--no-sandbox", *extra])
