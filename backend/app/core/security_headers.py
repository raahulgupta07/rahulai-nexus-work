"""Response headers that tell the browser what this app is allowed to do.

Everything here is a BROWSER-side control. None of it stops a scripted attacker
holding a token — it stops the class of attack that needs a browser to cooperate:
clickjacking, MIME sniffing, referrer leakage of a report id to a third party,
and script injection escalating into data exfiltration.

★Why a middleware and not the reverse proxy: this fork ships as one container
that serves both the API and the SPA (`SERVE_FRONTEND=1`), and installs put
whatever proxy they already have in front of it. A header set in someone's nginx
is a header that does not exist on a fresh `docker compose up`, which is exactly
the deployment this product is documented for. Set them where the app is.

★A proxy that sets its own copy wins on some servers and duplicates on others.
`DASH_SECURITY_HEADERS=off` exists for that case rather than asking anyone to
edit source.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware


def _enabled() -> bool:
    return os.environ.get("DASH_SECURITY_HEADERS", "on").strip().lower() not in (
        "0", "off", "false", "no",
    )


def _hsts_enabled() -> bool:
    """HSTS is opt-in, and that is deliberate.

    `Strict-Transport-Security` is a one-way door for the whole host: once a
    browser sees it, that host is HTTPS-only for `max-age` seconds and the user
    cannot click through. Emitting it by default would brick every install that
    runs on plain HTTP behind a VPN — which is a normal way to run an internal
    analytics tool — and the damage outlives the fix, because the browser keeps
    honouring the pin long after the header stops being sent.
    """
    return os.environ.get("DASH_HSTS", "").strip().lower() in ("1", "on", "true", "yes")


# ★The SPA is a Nuxt build. `unsafe-inline` on styles is required by Vue's
# scoped-style injection, and `unsafe-eval` by the chart/visualization runtime;
# dropping either blanks the app. They are named here rather than silently
# implied so the next person can measure what removing them costs.
#
# `frame-ancestors 'none'` is the one that carries real weight: it is the modern
# X-Frame-Options and it cannot be overridden by a `<meta>` tag the way most of
# CSP can.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    # ★★★`https:` is here because two real features load third-party images and
    # neither is visible to any test we run: `WebSearchTool.vue` fetches result
    # favicons from Google's favicon service, and `ChatAvatarComponent.vue`
    # renders `author.image_url`, which for an SSO account is a googleusercontent
    # or Microsoft Graph URL. The browser smoke does neither, so a tightened
    # img-src would have passed 19/19 and shipped broken avatars.
    #
    # What it costs: an image request is a one-way GET, so `https:` leaves a
    # narrow exfiltration channel for an attacker who already has script
    # execution. That attacker also has `unsafe-inline`, which this CSP grants
    # because the Nuxt build requires it — so closing img-src would be locking a
    # window in an unlocked room. Revisit both together, never img-src alone.
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    # The SPA talks to its own origin. `ws:`/`wss:` cover the completion stream.
    "connect-src 'self' ws: wss:",
    # Generated artifacts (visualizations, embedded documents) render in a
    # sandboxed iframe served from this same origin.
    "frame-src 'self' blob:",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    # ★★★'self', NOT 'none'. This was 'none' for one iteration and it would have
    # broken artifact rendering across the whole product: charts, documents and
    # MCP visualizations render inside iframes served from THIS origin
    # (`public/artifact-sandbox.html`, `mcp-artifact-app.html`,
    # `mcp-visualization-app.html`, plus the `srcdoc` frame in
    # `ArtifactFrame.vue`). `frame-ancestors 'none'` refuses same-origin framing
    # too, so every dashboard would have rendered as an empty box with the
    # refusal only in the browser console — nothing in any Python test can see
    # that. 'self' still stops clickjacking, which needs a THIRD-PARTY frame.
    "frame-ancestors 'self'",
])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if not _enabled():
            return response

        h = response.headers
        # setdefault throughout: a route that deliberately sets its own value
        # (an embed surface loosening frame-ancestors, say) keeps it.
        h.setdefault("X-Content-Type-Options", "nosniff")
        # SAMEORIGIN for the same reason frame-ancestors is 'self' — see _CSP.
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        # `strict-origin-when-cross-origin` still sends the full URL same-origin,
        # which the SPA's own navigation needs, and sends only the origin
        # off-site — so a report id never reaches a third party in a Referer.
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Content-Security-Policy", _CSP)
        # Not a header a browser needs, and it names the server software and
        # therefore its CVE list. uvicorn writes it; `--no-server-header` in
        # start.sh removes it at the source, and this is the belt for installs
        # that launch uvicorn some other way.
        if h.get("server", "").lower().startswith("uvicorn"):
            del h["server"]
        if _hsts_enabled():
            h.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def init_security_headers(app) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
