#!/usr/bin/env bash
#
# install.sh — first install of CityAgent Insights on a clean server.
#
#   ./install.sh --domain app.example.com   PRODUCTION install (the default)
#   ./install.sh --dev                      development install, laptop only
#   ./install.sh --dry-run                  run every check, write nothing
#   ./install.sh --help
#
# Production is the default. It runs docker-compose.yaml alone: Caddy holds
# 80/443 and terminates TLS, and NEITHER the application nor Postgres is
# published on a host port.
#
# ★★★--dev additionally applies docker-compose.dev.yaml, which publishes
# Postgres on POSTGRES_PORT and the application on APP_PORT, unencrypted. On a
# cloud server whose security group allows those ports that puts the database
# on the internet with the password in .env. It used to be the default here,
# silently, which is why the flag now says what it is for.
#
# The two are not interchangeable afterwards: they use different volume names,
# so a stack installed one way and later started the other way comes up on
# empty storage, reports healthy, and shows a fresh signup screen.
#
# Use ./upgrade.sh for every install AFTER the first one. This script exists
# only because the first one is different: there is no running stack to read
# the answers from, and the two secrets it generates can never be regenerated.
#
# What it refuses to do
# ---------------------
#   * overwrite an existing .env — DASH_ENCRYPTION_KEY decrypts every stored
#     credential and a new one orphans all of them, silently, with no error
#   * install over a stack that is already running (that is an upgrade)
#   * start a production stack with no DOMAIN — Caddy would fall back to
#     localhost, issue itself a certificate nobody can use, and report healthy
#   * report success on a build that did not take, or on a container that
#     came up serving a different version than the tree it was built from
#
set -euo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
[[ -t 1 ]] || { BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; OFF=""; }

step()  { printf "\n${BOLD}▸ %s${OFF}\n" "$1"; }
ok()    { printf "  ${GRN}✓${OFF} %s\n" "$1"; }
note()  { printf "    ${DIM}%s${OFF}\n" "$1"; }
warn()  { printf "  ${YEL}!${OFF} %s\n" "$1"; }
die()   { printf "\n  ${RED}✗ ABORTED${OFF}  %s\n\n" "$1" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

PROJECT="${CITYAGENT_PROJECT:-cityagentinsights}"
# Overridable so this script can be rehearsed end-to-end against a throwaway
# stack without touching the real one. Defaults are the shipped names.
# ★The container was renamed to dash-app. This still said bow-app-cai, so the
# "is a stack already running here?" guard checked a name that no longer
# exists — it could never fire, and the health check at the end looked for
# the same absent container.
APP="${CITYAGENT_APP_CONTAINER:-dash-app}"
IMAGE_REPO="${CITYAGENT_IMAGE_REPO:-cityagentinsights}"
BASE_FILE="${CITYAGENT_COMPOSE_BASE:-docker-compose.yaml}"
DEV_FILE="${CITYAGENT_COMPOSE_OVERRIDE:-docker-compose.dev.yaml}"
DRY_RUN=0
# ★Production by default. This used to append DEV_FILE unconditionally, so
# every server installed by this script published its database on a host port
# and served the application without TLS — with no flag, no prompt and no line
# of output saying so.
MODE="prod"
DOMAIN="${CITYAGENT_DOMAIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --dev)     MODE="dev"; shift ;;
    --prod)    MODE="prod"; shift ;;
    --domain)  DOMAIN="${2:?--domain needs a value}"; shift 2 ;;
    --project) PROJECT="${2:?--project needs a value}"; shift 2 ;;
    -h|--help)
      # ★Print the whole header comment, however long it grows. A fixed line
      # range (it was '2,18p') silently truncated help mid-sentence every time
      # the header was extended, and cut off the safety notes first.
      awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *) die "unknown argument '$1'. Try --help." ;;
  esac
done

COMPOSE_FILES=("$BASE_FILE")
[[ "$MODE" == "dev" ]] && COMPOSE_FILES+=("$DEV_FILE")

printf "\n${BOLD}CityAgent Insights — install${OFF}\n"
printf "${DIM}%s  ·  %s${OFF}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$REPO_DIR"
if [[ "$MODE" == "prod" ]]; then
  ok "PRODUCTION install — Caddy on 80/443, nothing else published on the host"
else
  warn "DEVELOPMENT install — publishes Postgres and the application on host ports"
  note "Correct on a laptop. On a server this exposes the database to anything"
  note "the firewall allows, with the password from .env. Re-run without --dev"
  note "for a production install."
fi
[[ $DRY_RUN -eq 1 ]] && warn "dry run — nothing will be written, built or started"

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
step "Prerequisites"

command -v docker >/dev/null 2>&1 || die "docker is not installed."
docker info >/dev/null 2>&1 || die "the docker daemon is not reachable. Start Docker, or add this user to the docker group."
ok "docker $(docker version -f '{{.Server.Version}}' 2>/dev/null || echo '?')"

docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) is not available. The v1 'docker-compose' binary is not supported."
ok "compose $(docker compose version --short 2>/dev/null || echo '?')"

command -v git >/dev/null 2>&1 || warn "git not found — upgrade.sh will not be able to pull."

for f in "${COMPOSE_FILES[@]}" Dockerfile .env.example; do
  [[ -f "$f" ]] || die "$f is missing. Is this a complete checkout?"
done
ok "compose files and Dockerfile present"

# The build needs real room. A build that runs out of disk half way through
# leaves a partial image and a confusing error from a layer, not from disk.
AVAIL_KB="$(df -Pk . | awk 'NR==2 {print $4}')"
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
if [[ $AVAIL_GB -lt 15 ]]; then
  die "only ${AVAIL_GB}GB free on this filesystem. The image needs ~10GB and the build another ~5GB."
fi
ok "${AVAIL_GB}GB free"

# ---------------------------------------------------------------------------
# 2. This must be a FIRST install
# ---------------------------------------------------------------------------
step "Checking this is a first install"

RUNNING="$(docker ps -a --filter "name=^/${APP}$" --format '{{.Names}}' 2>/dev/null || true)"
if [[ -n "$RUNNING" ]]; then
  die "container '$APP' already exists. This is an upgrade, not an install — use ./upgrade.sh (and ./preflight.sh first)."
fi
ok "no existing application container"

# A leftover volume from a previous install carries a database encrypted with
# a key this run would not have. Catching it here is the difference between a
# clear message and a login screen that rejects every stored credential.
OLD_VOLS="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" 2>/dev/null | tr '\n' ' ' | sed 's/ $//')"
if [[ -n "$OLD_VOLS" ]]; then
  warn "volumes from a previous '$PROJECT' install are still here:"
  note "$OLD_VOLS"
  note "If you are reinstalling and want that data, keep them AND restore the"
  note "matching .env — that installation's own encryption key is the only"
  note "thing that can read the credentials in that database."
  note "To start genuinely clean: docker volume rm $OLD_VOLS"
fi

# ---------------------------------------------------------------------------
# 3. Configuration
# ---------------------------------------------------------------------------
step "Configuration"

# ★A production stack with no DOMAIN is the quiet failure this whole file
# exists to prevent. docker-compose.yaml and the Caddyfile both fall back to
# the literal string `localhost`: Caddy starts, issues itself a certificate for
# a name nobody browses to, and reports healthy. Every browser then refuses the
# site and nothing in any log says why. Decided BEFORE .env is written, so a
# re-run with --domain starts from a clean slate rather than an orphaned file.
if [[ -f .env && -z "$DOMAIN" ]]; then
  DOMAIN="$(grep -E '^DOMAIN=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
fi
if [[ "$MODE" == "prod" ]]; then
  [[ -n "$DOMAIN" && "$DOMAIN" != "localhost" ]] \
    || die "a production install needs a domain. Re-run as: ./install.sh --domain your.host.name  (or ./install.sh --dev for a laptop, which needs none). That name must already resolve to this machine, and inbound 80 and 443 must be open — port 80 is how the certificate is issued."
  ok "domain $DOMAIN"
fi

if [[ -f .env ]]; then
  ok ".env already present — leaving it exactly as it is"
  note "This script never edits an existing .env. Its encryption key is the"
  note "only thing that can decrypt the credentials in an existing database."
  # ★Accept EITHER spelling. This used to test only the old name, so a .env
  # written correctly from .env.example — which uses DASH_ — was rejected with
  # a message naming a variable that file does not contain.
  grep -qE '^(DASH|BOW)_ENCRYPTION_KEY=.+' .env || die ".env has no encryption key. Set DASH_ENCRYPTION_KEY before installing — an empty key is regenerated on every restart, in memory, and silently orphans every credential."
  grep -q '^POSTGRES_PASSWORD=.\+' .env || die ".env has an empty POSTGRES_PASSWORD."
  ok "both required secrets are set"
else
  [[ $DRY_RUN -eq 1 ]] && { note "would create .env from .env.example with two generated secrets"; }
  if [[ $DRY_RUN -eq 0 ]]; then
    command -v openssl >/dev/null 2>&1 || die "openssl is needed to generate the secrets. Install it, or write .env by hand from .env.example."
    ENC_KEY="$(openssl rand -base64 32)"
    PG_PASS="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-40)"

    # sed over the example rather than a heredoc, so every comment and every
    # default in .env.example survives into the real file. The generated
    # values contain base64 punctuation, so the delimiter is | and the key is
    # escaped — a bare s/// would break on the / in a base64 string.
    ENC_ESC="$(printf '%s' "$ENC_KEY" | sed 's/[|&\\]/\\&/g')"
    PG_ESC="$(printf '%s'  "$PG_PASS" | sed 's/[|&\\]/\\&/g')"
    # ★The variable is DASH_ENCRYPTION_KEY. This wrote BOW_ — the name from
    # before the rename — which .env.example does not contain, so the
    # substitution matched nothing and produced a .env with an EMPTY key. The
    # check below then failed and the install aborted every time, on a message
    # about writing rather than about the name.
    sed -e "s|^DASH_ENCRYPTION_KEY=.*|DASH_ENCRYPTION_KEY=${ENC_ESC}|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_ESC}|" \
        .env.example > .env
    chmod 600 .env

    grep -q "^DASH_ENCRYPTION_KEY=${ENC_KEY}$" .env || die "writing .env did not take — the encryption key is not in the file. Refusing to continue."

    # ★DOMAIN ships COMMENTED OUT in .env.example, so a substitution on an
    # uncommented line matches nothing and writes no domain at all. Append it
    # instead, and read it back — an unset DOMAIN is exactly the failure the
    # check above refused to allow.
    if [[ "$MODE" == "prod" ]]; then
      printf '\n# Set by install.sh\nDOMAIN=%s\n' "$DOMAIN" >> .env
      grep -q "^DOMAIN=${DOMAIN}$" .env || die "DOMAIN was not written to .env. Refusing to start a production stack without it."
      ok "DOMAIN=$DOMAIN written to .env"
    fi

    ok ".env created from .env.example, mode 600"
    ok "DASH_ENCRYPTION_KEY generated"
    ok "POSTGRES_PASSWORD generated"
    warn "back .env up somewhere off this server, NOW."
    note "A database dump cannot recover the encryption key. Without it every"
    note "stored connector password, OAuth token and SSO secret is unreadable."
  fi
fi

# On a dry run .env does not exist yet, so read the ports from the file the
# real run will copy. Falling back to hard-coded defaults instead would test a
# pair of ports the install is never going to use.
PORT_SRC=".env"; [[ -f .env ]] || PORT_SRC=".env.example"
APP_PORT="$(grep -E '^APP_PORT=' "$PORT_SRC" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
APP_PORT="${APP_PORT:-8095}"
PG_PORT="$(grep -E '^POSTGRES_PORT=' "$PORT_SRC" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PG_PORT="${PG_PORT:-5440}"

# ★The ports to check are decided by the compose files, not by .env. A
# production stack publishes 80 and 443 (Caddy) and nothing else, so testing
# APP_PORT/POSTGRES_PORT there checks two ports the install will never bind —
# and misses the two it will.
if [[ "$MODE" == "prod" ]]; then
  CHECK_PORTS=(80 443)
  note "publishing 80 and 443 (Caddy) · APP_PORT and POSTGRES_PORT are unused in this mode"
else
  CHECK_PORTS=("$APP_PORT" "$PG_PORT")
  note "application port $APP_PORT · postgres port $PG_PORT"
fi

port_busy() { docker ps --format '{{.Ports}}' | grep -q ":$1->" ; }
for p in "${CHECK_PORTS[@]}"; do
  if [[ "$MODE" == "prod" ]]; then
    port_busy "$p" && die "port $p is already published by another container. Caddy needs 80 and 443. Stop that stack, or install behind it with --dev and your own reverse proxy."
  else
    port_busy "$p" && die "port $p is already published by another container. Change APP_PORT/POSTGRES_PORT in .env, or stop that stack."
  fi
done
ok "ports ${CHECK_PORTS[*]} free"

if [[ $DRY_RUN -eq 1 ]]; then
  printf "\n${GRN}Dry run complete.${OFF} Every check passed. Re-run without --dry-run to install.\n\n"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Build
# ---------------------------------------------------------------------------
step "Building the image"

VERSION="$(cat VERSION 2>/dev/null || echo 'unknown')"
note "version $VERSION"

# FE_CACHEBUST is not optional. Without it Docker reuses the cached
# `COPY ./frontend` layer, the build reports success, and the image ships an
# interface from whenever that layer was last built.
DC=(docker compose -p "$PROJECT")
for _cf in "${COMPOSE_FILES[@]}"; do DC+=(-f "$_cf"); done

"${DC[@]}" build --build-arg FE_CACHEBUST="$(date +%s)" app \
  || die "the image build failed. Nothing has been started."
ok "image built"

# ★Both compose files read `image: ${DASH_IMAGE:-cityagentinsights:local}`, so
# an .env carrying DASH_IMAGE builds under THAT tag. This block hard-coded
# :local, so reusing an existing .env built one image and then inspected a
# different (often absent) one, and aborted claiming the build did not take.
BUILT_IMAGE="$(grep -E '^DASH_IMAGE=' .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)"
BUILT_IMAGE="${BUILT_IMAGE:-${IMAGE_REPO}:local}"

# Verify the version INSIDE the image before anything is started. A build that
# silently reused a cached layer looks identical from the outside.
IMG_VERSION="$(docker run --rm --entrypoint sh "$BUILT_IMAGE" -c 'cat /app/VERSION' 2>/dev/null | tr -d '\r\n' || true)"
[[ "$IMG_VERSION" == "$VERSION" ]] \
  || die "the image $BUILT_IMAGE reports version '$IMG_VERSION' but this tree is '$VERSION'. The build did not take. Not starting it."
ok "image $BUILT_IMAGE reports $IMG_VERSION"

# Tag it. Building over the same tag again re-points it and the daemon garbage
# collects the orphaned image — a version tag is the only thing that keeps it.
docker tag "$BUILT_IMAGE" "${IMAGE_REPO}:${VERSION}" 2>/dev/null \
  && ok "tagged ${IMAGE_REPO}:${VERSION}" \
  || warn "could not tag ${IMAGE_REPO}:${VERSION}"

# ---------------------------------------------------------------------------
# 5. Start
# ---------------------------------------------------------------------------
step "Starting the stack"

"${DC[@]}" up -d \
  || die "the stack failed to start. 'docker compose -p $PROJECT logs' has the reason."
ok "containers created"

# ★Asked from INSIDE the container, on its own port 3000. A host-port curl only
# works on a development stack; production publishes nothing but Caddy, so the
# old check reported "did not answer within two minutes" on an installation
# that was up and perfectly healthy.
printf "  waiting for health"
HEALTHY=0
for _ in $(seq 1 60); do
  if docker exec "$APP" curl -fsS -m 3 "http://localhost:3000/health" >/dev/null 2>&1; then HEALTHY=1; break; fi
  printf "."
  sleep 2
done
printf "\n"
[[ $HEALTHY -eq 1 ]] || die "the application did not answer /health within two minutes. Check: docker logs $APP"
ok "/health answering inside the container"

SERVED="$(docker exec "$APP" curl -fsS -m 5 "http://localhost:3000/api/settings" 2>/dev/null | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1 || true)"
[[ -n "$SERVED" ]] && ok "API serving version $SERVED"

# On production the only route a user has is through Caddy, so prove that
# separately. A 200/301/308 all mean Caddy is answering and proxying.
if [[ "$MODE" == "prod" ]]; then
  EDGE="$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://localhost/health" 2>/dev/null || true)"
  case "$EDGE" in
    200|301|302|308) ok "Caddy answering on port 80 (HTTP $EDGE)" ;;
    *) warn "Caddy returned '$EDGE' on port 80. The application itself is healthy."
       note "Check 'docker logs ${DASH_CADDY_CONTAINER:-dash-caddy}'. Until DNS for"
       note "$DOMAIN points here and 80/443 are open, no certificate can be issued." ;;
  esac
fi

HEAD_REV="$(docker exec -w /app/backend "$APP" alembic current 2>/dev/null | tail -1 || true)"
[[ -n "$HEAD_REV" ]] && ok "migrations at ${HEAD_REV}"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "\n${GRN}${BOLD}Installed.${OFF}  CityAgent Insights %s\n\n" "$VERSION"
if [[ "$MODE" == "prod" ]]; then
  printf "  Open        ${BOLD}https://%s${OFF}\n" "$DOMAIN"
  printf "  Certificate Caddy requests one on first visit. It needs DNS for that\n"
  printf "              name pointing here and inbound 80 and 443 open.\n"
else
  printf "  Open        ${BOLD}http://localhost:%s${OFF}\n" "$APP_PORT"
fi
printf "  Sign up     the FIRST account created becomes the super admin\n"
printf "  Then        add an OpenRouter key when asked — the agents need a model\n\n"
printf "  ${BOLD}Back up .env off this server.${OFF} It holds DASH_ENCRYPTION_KEY, and\n"
printf "  no database backup can recover it.\n\n"
if [[ "$MODE" == "dev" ]]; then
  printf "  ${YEL}This is a DEVELOPMENT install.${OFF} Postgres is published on port %s.\n" "$PG_PORT"
  printf "  If this machine is reachable from the internet, close that port now.\n\n"
fi
printf "  This stack was installed with: docker compose %s\n" "$(printf -- '-f %s ' "${COMPOSE_FILES[@]}")"
printf "  Every later build and start must repeat exactly those files.\n\n"
printf "  From here on:  ./preflight.sh   then   ./upgrade.sh\n\n"
