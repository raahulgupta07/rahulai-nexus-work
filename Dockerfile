# syntax=docker/dockerfile:1
# ^ enables BuildKit RUN --mount=type=cache (used in the frontend stage to make
#   repeated `yarn generate` bakes incremental).
FROM ubuntu:24.04 AS backend-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      python3 \
      python3-venv \
      python3-dev \
      build-essential \
      libpq-dev \
      gcc \
      unixodbc-dev \
      libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container for the backend
WORKDIR /app/backend

# Copy only the dependency manifests first so the uv sync layer is cached
# independently of application source changes.
COPY ./backend/pyproject.toml ./backend/uv.lock ./

# Create and use a virtual environment for dependencies
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /usr/local/bin/uv

# Install locked main deps into the venv; dev group excluded from image.
# The kerberos extra (python-gssapi) enables per-user constrained delegation
# (S4U) for on-prem SQL Server SSO; it builds against libkrb5-dev above.
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-install-project --extra kerberos

# Pre-cache tiktoken encodings for airgapped environments. Depends only on the
# installed deps (not app source) → runs BEFORE the source COPY so it stays cached
# across application code edits.
RUN TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache python3 -c \
    "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"

# Install Playwright browser (chromium only to save space). Depends only on the
# installed playwright dep → runs BEFORE the source COPY. Previously this sat
# AFTER the COPY, so every backend code edit re-downloaded ~150MB of Chromium +
# apt deps and made each bake minutes slower.
RUN playwright install chromium --with-deps

# Copy the full backend source LAST so editing application code only busts this
# cheap layer, not the tiktoken/playwright/dependency layers above.
COPY ./backend /app/backend
RUN rm -f /app/backend/db/app.db

FROM rust:1-slim-bookworm AS qvd2parquet-builder

WORKDIR /build/qvd2parquet
COPY ./tools/qvd2parquet/Cargo.toml ./tools/qvd2parquet/Cargo.lock ./
# Pre-build dependencies against a stub main so cargo caches the dep graph.
RUN mkdir src && echo 'fn main() {}' > src/main.rs && \
    cargo build --release --locked && \
    rm -rf src target/release/qvd2parquet target/release/qvd2parquet.d \
           target/release/deps/qvd2parquet-* 2>/dev/null || true
COPY ./tools/qvd2parquet/src ./src
RUN cargo build --release --locked && \
    strip target/release/qvd2parquet

FROM ubuntu:24.04 AS frontend-builder

ENV DEBIAN_FRONTEND=noninteractive

# Install Node.js 22 and prepare environment
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs git && \
    npm install --global yarn@1.22.22 && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container for the frontend
WORKDIR /app/frontend

# --- Dependency layer -------------------------------------------------------
# Copy ONLY the manifest + lockfile first and install. Because this layer's
# cache key depends on just these two files, editing frontend source below does
# NOT invalidate it — `yarn install` is skipped on every source-only rebuild
# (the previous ordering copied all source before install, so any code edit
# forced a full ~1-2 min reinstall). `--ignore-scripts` skips the `nuxt prepare`
# postinstall here (nuxt.config isn't present yet); `yarn generate` prepares.
COPY ./frontend/package.json ./frontend/yarn.lock /app/frontend/
RUN yarn install --frozen-lockfile --ignore-scripts

# --- Source layer -----------------------------------------------------------
# Copy VERSION/config first (used by Nuxt), then the rest of the source. Only
# these layers rebuild on a code change.
COPY ./VERSION /app/VERSION
COPY ./dash-config.yaml /app/dash-config.yaml
# `frontend/plugins/i18n.ts` imports `../../locales/*.json` at build time,
# so the repo-root `locales/` dir must be present for Rollup to resolve them.
COPY ./locales /app/locales
# node_modules is .dockerignore'd, so this does NOT clobber the installed deps.
# cache-bust: BuildKit serves a stale CACHED result for this COPY after live
# edits (a preceding comment change does NOT bust a COPY — Docker keys the COPY
# on the instruction string + context checksums, not comments). Pass a changing
# FE_CACHEBUST build-arg to force this layer (and yarn generate below) to re-run.
ARG FE_CACHEBUST=0
RUN echo "fe-cachebust=${FE_CACHEBUST}"
COPY ./frontend /app/frontend

# ★The vendored artifact-sandbox JS libraries are COMMITTED under
# frontend/public/libs and arrive with the `COPY ./frontend` above — they are
# no longer downloaded here. That removes nine unchecksummed CDN fetches from
# the build, makes builds reproducible (@babel/standalone was unpinned and had
# silently rolled to a new MAJOR version), and lets the image build offline.
# scripts/download-vendor-libs.sh is now an update tool run by hand; it
# verifies every download against frontend/public/libs/libs.sha256.
# Guarded by tests/unit/fork/test_vendored_libs_are_committed.py.
RUN test -f /app/frontend/public/libs/libs.sha256 \
      || (echo "FATAL: vendored artifact libs missing from the build context" >&2; exit 1)

# `nuxt generate` produces a fully static SPA under .output/public, which
# FastAPI serves directly in production (see backend/app/core/spa.py).
# Cache mounts persist Vite's transform cache and the Nuxt build cache across
# bakes so repeated `generate` runs are incremental (only changed modules
# recompile). If a build ever looks stale, clear with `docker builder prune`.
RUN --mount=type=cache,target=/app/frontend/node_modules/.cache \
    --mount=type=cache,target=/app/frontend/.nuxt/cache \
    yarn generate

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Install Python runtime and minimal system libs. Node.js is no longer
# needed at runtime: the frontend is pre-generated as static files by the
# frontend-builder stage and served directly by FastAPI.
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git openssh-client python3 python3-venv tini libpq5 vim-tiny && \
    # Kerberos runtime for Windows Integrated auth to SQL Server: GSSAPI libs
    # for the ODBC driver / python-gssapi, plus kinit/klist for keytab ops.
    # Mount /etc/krb5.conf and a keytab (see docs/sql-server-kerberos.md).
    apt-get install -y --no-install-recommends krb5-user libgssapi-krb5-2 && \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    ARCH="$(dpkg --print-architecture)" && \
    echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main" > /etc/apt/sources.list.d/microsoft-prod-24.04.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends unixodbc tdsodbc freetds-dev && \
    (ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 || echo "WARN: msodbcsql18 not available for ${ARCH}") && \
    if [ "${ARCH}" = "amd64" ]; then \
      echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" > /etc/apt/sources.list.d/microsoft-prod-22.04.list && \
      printf 'Package: *\nPin: origin packages.microsoft.com\nPin: release n=jammy\nPin-Priority: 100\n\nPackage: msodbcsql17\nPin: origin packages.microsoft.com\nPin: release n=jammy\nPin-Priority: 900\n' > /etc/apt/preferences.d/microsoft-odbc && \
      apt-get update && \
      (ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 || echo "WARN: msodbcsql17 install failed"); \
    fi && \
    # PPTX/DOCX to PDF to PNG: slide previews (slides mode) and the read_file
    # vision fallback for Office documents that yield no extractable text.
    # Both modules are required — libreoffice-core ships the soffice binary and
    # the UNO framework, but each format's import filter lives with its
    # application module (Writer's WordprocessingML filter is in
    # libreoffice-writer, Impress's in libreoffice-impress). With one missing,
    # type detection still succeeds and the load then fails with
    # "source file could not be loaded".
    apt-get install -y --no-install-recommends libreoffice-impress libreoffice-writer poppler-utils && \
    # ★★★FONTS THE DECKS ACTUALLY NAME. `--no-install-recommends` above is why
    # there were none: LibreOffice *recommends* the font packages and depends on
    # almost none of them, so the image shipped with Liberation/DejaVu/FreeSans/
    # Noto and nothing else. Generated decks ask for Cambria and Calibri;
    # fontconfig answers a `fc-match Cambria` with DejaVu Serif, which is ~30%
    # wider, so a title that needs 724pt in Times metrics needs 939pt in DejaVu
    # Serif Bold against an 835pt box and overprints. There is no error — the
    # substitution is silent, and it hits BOTH slide previews and PDF export
    # because both go through `soffice --convert-to pdf`.
    #
    # First three are the metric-compatible core and are the ones that kill the
    # live bug on their own: croscore is Arimo/Tinos/Cousine (Arial/Times/
    # Courier metrics), carlito is Calibri metrics, caladea is Cambria metrics.
    # The rest are families the shipped deck themes name by hand. Every package
    # here is in main or universe — ★deliberately NOT fonts-ibm-plex, which is
    # in **multiverse**; IBM Plex is vendored below instead so this install
    # cannot fail on an image whose sources omit that component.
    apt-get install -y --no-install-recommends \
      fonts-croscore fonts-crosextra-carlito fonts-crosextra-caladea \
      fonts-inter fonts-jetbrains-mono fonts-open-sans fonts-ebgaramond \
      fonts-quicksand fonts-karla fonts-sora fonts-dm-mono fonts-courier-prime && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# The families with no Debian/Ubuntu package. All Google Fonts (OFL/Apache), so
# redistribution is fine; each family directory carries its own LICENSE.txt.
#
# ★They are STATIC instances, not the variable fonts upstream now ships. A `.ttf`
# with an `fvar` table is reported by fontconfig at its DEFAULT instance only, so
# a VF-only install gives LibreOffice one weight and it SYNTHESISES bold — which
# is exactly the metric drift this whole change exists to remove. The instances
# were cut with fontTools at wght=400/700 (all other axes at default) with
# `updateFontNames=True`, so the name table says "Bold" and fontconfig can see it.
#
# ★`fc-cache` is required: dropping files into /usr/share/fonts changes nothing
# until the cache is rebuilt, and LibreOffice reads only the cache.
COPY assets/fonts /usr/share/fonts/truetype/cityagent
RUN fc-cache -f

# Oracle Instant Client (Basic Light) lets python-oracledb run in "thick"
# mode, which the backend enables at startup whenever these libraries are
# present (see init_thick_mode_if_available in oracledb_client.py). Thin mode
# cannot reach Oracle servers older than 12.1, accounts with 10G-only
# password verifiers (DPY-3015), or Native Network Encryption (DPY-4011
# "connection reset by peer"); thick mode handles all of these and is a
# superset of thin. Pinned to 19c rather than 23c because the 19c client
# connects to servers 11.2+ while 23c requires 19+. Install failure is
# non-fatal so airgapped builds still succeed (the driver stays thin).
RUN ARCH="$(dpkg --print-architecture)" && \
    case "${ARCH}" in \
      amd64) IC_ARCH="linux.x64"; GNU_TRIPLET="x86_64-linux-gnu" ;; \
      arm64) IC_ARCH="linux.arm64"; GNU_TRIPLET="aarch64-linux-gnu" ;; \
      *) IC_ARCH="" ;; \
    esac && \
    if [ -n "${IC_ARCH}" ]; then \
      apt-get update && \
      apt-get install -y --no-install-recommends libaio1t64 unzip && \
      ln -sf "/usr/lib/${GNU_TRIPLET}/libaio.so.1t64" "/usr/lib/${GNU_TRIPLET}/libaio.so.1" && \
      (curl -fsSL -o /tmp/instantclient.zip \
         "https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-${IC_ARCH}-19.28.0.0.0dbru.zip" && \
       mkdir -p /opt/oracle && \
       unzip -q /tmp/instantclient.zip -d /opt/oracle && \
       ln -s /opt/oracle/instantclient_19_28 /opt/oracle/instantclient && \
       echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf && \
       ldconfig \
       || echo "WARN: Oracle Instant Client install failed; python-oracledb stays in thin mode") && \
      rm -f /tmp/instantclient.zip && \
      apt-get clean && rm -rf /var/lib/apt/lists/*; \
    else \
      echo "WARN: no Oracle Instant Client build for ${ARCH}; python-oracledb stays in thin mode"; \
    fi

RUN groupadd -r app \
    && useradd -r -g app -m -d /home/app -s /usr/sbin/nologin app \
    && mkdir -p /home/app /app/backend/db /app/frontend \
    && chown -R app:app /app /home/app

# Copy Python virtual environment and application code
COPY --from=backend-builder --chown=app:app /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY --from=backend-builder --chown=app:app /app/backend /app/backend

# Streaming QVD → Parquet converter (bounded RAM; replaces in-process qvdrs wheel)
COPY --from=qvd2parquet-builder /build/qvd2parquet/target/release/qvd2parquet /usr/local/bin/qvd2parquet

# Copy pre-cached tiktoken encodings for airgapped environments
COPY --from=backend-builder --chown=app:app /opt/tiktoken_cache /opt/tiktoken_cache
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache

# Copy Playwright browser binaries from builder
COPY --from=backend-builder --chown=app:app /root/.cache/ms-playwright /home/app/.cache/ms-playwright

# Install Playwright system dependencies (runtime libs only, no browser download)
RUN playwright install-deps chromium

# OfficeCLI — renders a .pptx to HTML with real per-shape geometry, which is
# what the deck layout check measures with the chromium above. python-pptx has
# no font metrics, so generated decks can put 1300 characters in a box sized for
# 300 and nothing raises; the overflow is only visible once a layout engine has
# run. Pinned by version AND sha256: this is a prebuilt binary from a
# third-party release page, so an unpinned fetch would silently change what
# executes inside the image.
# Note: its own `view <file> issues` does NOT report pptx text overflow (it
# renders text boxes as height:auto, so nothing overflows in its model). We use
# it purely as a renderer; the measurement is ours.
# ★ The binary needs ICU (libicuuc). On a bare Ubuntu it downloads and verifies
# fine, then aborts inside the .NET runtime at CultureInfo.get_CurrentCulture.
# We get libicu incidentally from the LibreOffice/ODBC installs above — which is
# why this RUN sits AFTER them, and why it ends in `--version`: if a future
# dependency change drops ICU, the build fails here instead of shipping an image
# whose deck check silently no-ops.
ARG OFFICECLI_VERSION=v1.0.142
RUN ARCH="$(dpkg --print-architecture)" && \
    case "${ARCH}" in \
      amd64) OC_ASSET="officecli-linux-x64";   OC_SHA="f78563abc13cf70dcd420644019d2f11dc36ea2957ac738613a6911d652b5541" ;; \
      arm64) OC_ASSET="officecli-linux-arm64"; OC_SHA="260cdccd27f2e25902e9436e5e971c0ca5348ae3d36a54a3fbd794c452ba13f7" ;; \
      *)     OC_ASSET="" ;; \
    esac && \
    if [ -n "${OC_ASSET}" ]; then \
      curl -fsSL -o /usr/local/bin/officecli \
        "https://github.com/iOfficeAI/OfficeCLI/releases/download/${OFFICECLI_VERSION}/${OC_ASSET}" && \
      echo "${OC_SHA}  /usr/local/bin/officecli" | sha256sum -c - && \
      chmod 755 /usr/local/bin/officecli && \
      /usr/local/bin/officecli --version; \
    else \
      echo "WARN: no OfficeCLI build for ${ARCH}; deck layout check will no-op"; \
    fi

# Copy demo data sources (SQLite/DuckDB files for demo databases)
COPY --chown=app:app ./backend/demo-datasources /app/backend/demo-datasources

# Copy the generated static SPA (nuxt generate output includes all public/
# assets — libs, artifact-sandbox.html, icons, etc. — copied automatically).
COPY --from=frontend-builder --chown=app:app /app/frontend/.output/public /app/frontend/dist

# Keep the legacy public paths available for backend headless browser
# rendering code that reads files from disk (not over HTTP).
COPY --from=frontend-builder --chown=app:app /app/frontend/public/artifact-sandbox.html /app/frontend/public/artifact-sandbox.html
COPY --from=frontend-builder --chown=app:app /app/frontend/public/libs /app/frontend/public/libs

# Download RDS/Aurora CA certificate bundle for IAM auth SSL verification
RUN mkdir -p /app/certs && \
    curl -sSL -o /app/certs/rds-combined-ca-bundle.pem \
      https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem

# Create directories that the application needs to write to
# These paths match volume mounts in docker-compose.yaml; they must exist with
# app-user ownership so Docker seeds named volumes with writable perms on first run.
RUN mkdir -p /app/backend/uploads/files /app/backend/uploads/branding \
             /app/backend/branding_uploads /app/backend/logs && \
    chown -R app:app /app

WORKDIR /app

COPY --chown=app:app ./VERSION /app/VERSION
COPY --chown=app:app ./start.sh /app/start.sh
COPY --chown=app:app ./dash-config.yaml /app/dash-config.yaml
# Release notes served by /api/changelog (backend runs from /app/backend, so
# repo root maps to /app). Keep this so the "What's New" menu works in the image.
COPY --chown=app:app ./CHANGELOG.md /app/CHANGELOG.md

# ★ tests/unit/test_priority_erp_client.py does `sys.path.insert(0, tools)` then
# `import mock_server`. Without this the module is absent and pytest fails at
# COLLECTION with 28 errors — which reads as broken code, not a missing file.
# This was "fixed" once by copying it into a running container; every rebuild
# since silently wiped it. Copy it in the image so the fix survives.
COPY --chown=app:app ./tools/priority /app/tools/priority

# Set executable permissions for start.sh
RUN chmod +x /app/start.sh

ENV ENVIRONMENT=production
ENV GIT_PYTHON_REFRESH=quiet

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=UTF-8
ENV HOME=/home/app

# Tell FastAPI to serve the generated SPA from disk.
ENV SERVE_FRONTEND=1
ENV FRONTEND_DIST_DIR=/app/frontend/dist

# Expose the uvicorn port (documentational).
EXPOSE 3000

# Healthcheck against /health so failures reflect backend readiness, not
# just the static SPA index (which would always 200 even if uvicorn was wedged).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:3000/health || exit 1

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash", "start.sh"]
