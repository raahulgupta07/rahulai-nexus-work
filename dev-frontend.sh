#!/bin/bash
# =============================================================================
# Front-end dev server — hot module replacement, no image build
# =============================================================================
#
#   ./dev-frontend.sh            # http://localhost:3100
#   ./dev-frontend.sh 3200       # a different port
#
# Nuxt serves the SPA from source on your machine and proxies every /api call
# into the running container, so the back end, the database, your sessions and
# your uploaded files are the same ones you already have. Only the rendering
# layer moves.
#
# A saved .vue file is on screen in well under a second. The alternative is a
# full image build for a CSS change.
#
# ★Port 3100, not 3000. Nuxt's own default is 3000, which is also the port the
# container listens on internally — and if a previous session left something
# bound there, `nuxt dev` silently walks to 3001 and prints it in a line that
# is easy to miss. Pinning an unused port makes the address predictable.
#
# ★The proxy target is the HOST-mapped port (8095), not 3000. 3000 only exists
# inside the container's network namespace.
#
# ★★★What this does NOT prove. The dev server runs Vite with HMR; the shipped
# app is a static Nuxt build served by the backend. They differ in minification,
# chunking and env inlining. A change that works here still has to survive one
# real build before it goes anywhere. This makes the loop fast, not the release
# verified.
set -euo pipefail

PORT="${1:-3100}"
TARGET="${NUXT_DEV_PROXY_TARGET:-http://localhost:8095}"

cd "$(dirname "$0")/frontend"

if [ ! -d node_modules ]; then
    echo "Installing front-end dependencies (first run only)…"
    yarn install --frozen-lockfile
fi

# Reachability is checked up front. Without it the dev server starts happily and
# every request 502s, which reads like a front-end fault and is not one.
if ! curl -sf -o /dev/null "${TARGET}/health"; then
    echo "Backend not reachable at ${TARGET}/health"
    echo "Start it first:  docker compose -f docker-compose.dev.yaml -f docker-compose.fast.yaml up -d"
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Front end (hot reload):  http://localhost:${PORT}"
echo " API proxied to:          ${TARGET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

NUXT_DEV_PROXY_TARGET="${TARGET}" exec yarn dev --port "${PORT}"
