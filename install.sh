#!/usr/bin/env bash
#
# install.sh — first install of CityAgent Insights on a clean server.
#
#   ./install.sh              install here, using ./.env (created if absent)
#   ./install.sh --dry-run    run every check, write nothing, build nothing
#   ./install.sh --help
#
# Use ./upgrade.sh for every install AFTER the first one. This script exists
# only because the first one is different: there is no running stack to read
# the answers from, and the two secrets it generates can never be regenerated.
#
# What it refuses to do
# ---------------------
#   * overwrite an existing .env — BOW_ENCRYPTION_KEY decrypts every stored
#     credential and a new one orphans all of them, silently, with no error
#   * install over a stack that is already running (that is an upgrade)
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
APP="${CITYAGENT_APP_CONTAINER:-bow-app-cai}"
IMAGE_REPO="${CITYAGENT_IMAGE_REPO:-cityagentinsights}"
COMPOSE_FILES=("${CITYAGENT_COMPOSE_BASE:-docker-compose.yaml}" "${CITYAGENT_COMPOSE_OVERRIDE:-docker-compose.dev.yaml}")
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --project) PROJECT="${2:?--project needs a value}"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown argument '$1'. Try --help." ;;
  esac
done

printf "\n${BOLD}CityAgent Insights — install${OFF}\n"
printf "${DIM}%s  ·  %s${OFF}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$REPO_DIR"
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
  note "matching .env — the old BOW_ENCRYPTION_KEY is the only thing that can"
  note "read the credentials in that database."
  note "To start genuinely clean: docker volume rm $OLD_VOLS"
fi

# ---------------------------------------------------------------------------
# 3. Configuration
# ---------------------------------------------------------------------------
step "Configuration"

if [[ -f .env ]]; then
  ok ".env already present — leaving it exactly as it is"
  note "This script never edits an existing .env. Its encryption key is the"
  note "only thing that can decrypt the credentials in an existing database."
  grep -q '^BOW_ENCRYPTION_KEY=.\+' .env || die ".env has an empty BOW_ENCRYPTION_KEY. Fill it in before installing — an empty key is regenerated on every restart, in memory, and silently orphans every credential."
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
    sed -e "s|^BOW_ENCRYPTION_KEY=.*|BOW_ENCRYPTION_KEY=${ENC_ESC}|" \
        -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_ESC}|" \
        .env.example > .env
    chmod 600 .env

    grep -q "^BOW_ENCRYPTION_KEY=${ENC_KEY}$" .env || die "writing .env did not take — the encryption key is not in the file. Refusing to continue."
    ok ".env created from .env.example, mode 600"
    ok "BOW_ENCRYPTION_KEY generated"
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
note "application port $APP_PORT · postgres port $PG_PORT"

port_busy() { docker ps --format '{{.Ports}}' | grep -q ":$1->" ; }
for p in "$APP_PORT" "$PG_PORT"; do
  port_busy "$p" && die "port $p is already published by another container. Change APP_PORT/POSTGRES_PORT in .env, or stop that stack."
done
ok "both ports free"

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

# Verify the version INSIDE the image before anything is started. A build that
# silently reused a cached layer looks identical from the outside.
IMG_VERSION="$(docker run --rm --entrypoint sh "${IMAGE_REPO}:local" -c 'cat /app/VERSION' 2>/dev/null | tr -d '\r\n' || true)"
[[ "$IMG_VERSION" == "$VERSION" ]] \
  || die "the image reports version '$IMG_VERSION' but this tree is '$VERSION'. The build did not take. Not starting it."
ok "image reports $IMG_VERSION"

# Tag it. Building over :local again re-points the tag and the daemon garbage
# collects the orphaned image — a version tag is the only thing that keeps it.
docker tag "${IMAGE_REPO}:local" "${IMAGE_REPO}:${VERSION}" 2>/dev/null \
  && ok "tagged ${IMAGE_REPO}:${VERSION}" \
  || warn "could not tag ${IMAGE_REPO}:${VERSION}"

# ---------------------------------------------------------------------------
# 5. Start
# ---------------------------------------------------------------------------
step "Starting the stack"

"${DC[@]}" up -d \
  || die "the stack failed to start. 'docker compose -p $PROJECT logs' has the reason."
ok "containers created"

printf "  waiting for health"
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://localhost:${APP_PORT}/health" >/dev/null 2>&1; then HEALTHY=1; break; fi
  printf "."
  sleep 2
done
printf "\n"
[[ $HEALTHY -eq 1 ]] || die "the application did not answer /health within two minutes. Check: docker logs $APP"
ok "/health answering on port $APP_PORT"

SERVED="$(curl -fsS -m 5 "http://localhost:${APP_PORT}/api/settings" 2>/dev/null | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1 || true)"
[[ -n "$SERVED" ]] && ok "API serving version $SERVED"

HEAD_REV="$(docker exec -w /app/backend "$APP" alembic current 2>/dev/null | tail -1 || true)"
[[ -n "$HEAD_REV" ]] && ok "migrations at ${HEAD_REV}"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "\n${GRN}${BOLD}Installed.${OFF}  CityAgent Insights %s\n\n" "$VERSION"
printf "  Open        ${BOLD}http://localhost:%s${OFF}\n" "$APP_PORT"
printf "  Sign up     the FIRST account created becomes the super admin\n"
printf "  Then        add an OpenRouter key when asked — the agents need a model\n\n"
printf "  ${BOLD}Back up .env off this server.${OFF} It holds BOW_ENCRYPTION_KEY, and\n"
printf "  no database backup can recover it.\n\n"
printf "  From here on:  ./preflight.sh   then   ./upgrade.sh\n\n"
