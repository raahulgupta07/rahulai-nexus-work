#!/usr/bin/env bash
#
# preflight.sh — report the state of a CityAgent Insights install.
#
# Read-only. Starts nothing, stops nothing, writes nothing. Safe to run at any
# time, including while the app is serving traffic.
#
# Run it before an upgrade and again afterwards: the two outputs side by side
# are the fastest way to see what actually moved. Also the right thing to paste
# into a bug report — it answers the questions that otherwise take three rounds
# of back-and-forth.
#
#   ./preflight.sh
#
set -uo pipefail          # deliberately NOT -e: a failed probe should print
                          # "unknown" and carry on, not abort the report.

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
[[ -t 1 ]] || { BOLD=""; DIM=""; RED=""; GRN=""; YEL=""; OFF=""; }

ok()   { printf "  ${GRN}✓${OFF} %-22s %s\n" "$1" "$2"; }
warn() { printf "  ${YEL}!${OFF} %-22s %s\n" "$1" "$2"; }
bad()  { printf "  ${RED}✗${OFF} %-22s %s\n" "$1" "$2"; }
info() { printf "    %-22s %s\n" "$1" "$2"; }
head_() { printf "\n${BOLD}%s${OFF}\n" "$1"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

printf "${BOLD}CityAgent Insights — preflight${OFF}\n"
printf "${DIM}%s  ·  %s${OFF}\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$REPO_DIR"

# --------------------------------------------------------------------------
# Which install are we looking at?
#
# There are three compose files in this repo and a server may run more than one
# stack. Everything below is derived from the RUNNING container's own labels,
# never assumed, so this reports on what is actually up.
# --------------------------------------------------------------------------
head_ "Stack"

APP_CONTAINER="$(docker ps --filter "label=com.docker.compose.service=app" \
                 --format '{{.Names}}' 2>/dev/null | head -1)"

if [[ -z "$APP_CONTAINER" ]]; then
  bad "app container" "not running"
  PROJECT=""; PG_CONTAINER=""; COMPOSE_FILE=""
else
  ok "app container" "$APP_CONTAINER"
  PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$APP_CONTAINER" 2>/dev/null)"
  COMPOSE_FILE="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$APP_CONTAINER" 2>/dev/null)"
  IMAGE="$(docker inspect -f '{{.Config.Image}}' "$APP_CONTAINER" 2>/dev/null)"
  IMAGE_ID="$(docker inspect -f '{{.Image}}' "$APP_CONTAINER" 2>/dev/null | cut -c8-19)"
  STARTED="$(docker inspect -f '{{.State.StartedAt}}' "$APP_CONTAINER" 2>/dev/null | cut -c1-19)"

  info "compose project" "${PROJECT:-unknown}"
  # ALL of them, comma-separated. A stack started with `-f a.yaml -f b.yaml`
  # carries both in the label, and reporting only the first is how upgrade.sh
  # came to drop docker-compose.dev.yaml — the only file with the build stanza.
  # ★The trailing newline is load-bearing: without it `read` returns non-zero on
  # the final unterminated line and the loop body never runs for it — which is
  # exactly how this printed one file out of two on the first attempt.
  COMPOSE_FILES_SHOWN="$(printf '%s\n' "${COMPOSE_FILE:-unknown}" | tr ',' '\n' \
                         | while IFS= read -r _f; do [[ -n "$_f" ]] && basename "$_f"; done \
                         | paste -sd, -)"
  info "compose files" "${COMPOSE_FILES_SHOWN:-unknown}"
  info "image" "${IMAGE:-unknown}  (${IMAGE_ID:-?})"
  info "started" "${STARTED:-unknown}"

  PG_CONTAINER="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
                  --filter "label=com.docker.compose.service=postgres" \
                  --format '{{.Names}}' 2>/dev/null | head -1)"
  [[ -n "$PG_CONTAINER" ]] && ok "postgres container" "$PG_CONTAINER" \
                           || bad "postgres container" "not running"
fi

# --------------------------------------------------------------------------
# Version, from all three places it can disagree.
#
# They diverge in ways that matter: the file is what the next build would ship,
# the image is what the container was built from, and the API is what a browser
# is actually being served. A mismatch localises a failed upgrade immediately.
# --------------------------------------------------------------------------
head_ "Version"

FILE_VERSION="$(cat VERSION 2>/dev/null || echo unknown)"
info "VERSION file" "$FILE_VERSION"

if [[ -n "$APP_CONTAINER" ]]; then
  IMG_VERSION="$(docker exec "$APP_CONTAINER" cat /app/VERSION 2>/dev/null || echo unknown)"
  info "inside container" "$IMG_VERSION"

  PORT="$(docker port "$APP_CONTAINER" 3000 2>/dev/null | head -1 | sed 's/.*://')"
  if [[ -n "$PORT" ]]; then
    API_VERSION="$(curl -s --max-time 5 "http://localhost:$PORT/api/changelog" 2>/dev/null \
                   | sed -n 's/.*"current_version":"\([^"]*\)".*/\1/p')"
    info "served by API" "${API_VERSION:-unreachable}"
  fi

  if [[ "$FILE_VERSION" != "$IMG_VERSION" && "$IMG_VERSION" != "unknown" ]]; then
    warn "mismatch" "repo is $FILE_VERSION, container runs $IMG_VERSION — not yet upgraded, or the build did not take"
  fi
fi

# --------------------------------------------------------------------------
# Source state
# --------------------------------------------------------------------------
head_ "Source"

if git rev-parse --git-dir >/dev/null 2>&1; then
  info "branch" "$(git rev-parse --abbrev-ref HEAD)"
  info "commit" "$(git log --oneline -1)"

  DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
  [[ "$DIRTY" == "0" ]] && ok "working tree" "clean" \
                        || warn "working tree" "$DIRTY uncommitted change(s) — an upgrade will refuse to pull"

  if git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
    [[ "$BEHIND" == "0" ]] && ok "vs origin/main" "up to date" \
                           || warn "vs origin/main" "$BEHIND commit(s) behind — run: git fetch && git log HEAD..origin/main --oneline"
  fi
else
  warn "git" "not a repository"
fi

# --------------------------------------------------------------------------
# Configuration. Values are never printed — only whether they are set.
# --------------------------------------------------------------------------
head_ "Configuration"

if [[ -f .env ]]; then
  ok ".env" "present"
  # ★Accept either spelling — .env.example writes DASH_, machines installed
  # before the rename carry BOW_, and both are read at runtime.
  if grep -qE '^DASH_ENCRYPTION_KEY=.+' .env; then
    ok "encryption key" "set"
  elif grep -qE '^BOW_ENCRYPTION_KEY=.+' .env; then
    warn "encryption key" "set under its PREVIOUS name (BOW_ENCRYPTION_KEY) — rename it to DASH_ENCRYPTION_KEY"
  else
    bad "encryption key" "MISSING — a new key is minted every restart and all stored credentials are orphaned"
  fi
  # ★Do the names in .env still describe the database that is running?
  # POSTGRES_USER / POSTGRES_DB are applied only when Postgres creates an empty
  # data directory, so they cannot rename an existing install — an edit, or a
  # compose default that moved under an install whose .env never pinned them,
  # just re-points the connection string at a database that is not there. The
  # defaults DID move (older installs are bow/bagofwords, newer dash/
  # dash_insights), so this is checked rather than assumed.
  ENV_PG_USER="$(grep -E '^POSTGRES_USER=' .env | head -1 | cut -d= -f2- || true)"
  ENV_PG_DB="$(grep -E '^POSTGRES_DB=' .env | head -1 | cut -d= -f2- || true)"
  if [[ -z "$ENV_PG_USER" || -z "$ENV_PG_DB" ]]; then
    warn "database names" "not pinned in .env — this install depends on a compose default, and those changed"
  elif [[ -z "$PG_CONTAINER" ]]; then
    info "database names" "$ENV_PG_USER/$ENV_PG_DB (postgres not running, not verified)"
  elif [[ "$(docker exec "$PG_CONTAINER" psql -U "$ENV_PG_USER" -d "$ENV_PG_DB" -tAc 'select current_database()' 2>/dev/null | tr -d '[:space:]')" == "$ENV_PG_DB" ]]; then
    ok "database names" "$ENV_PG_USER/$ENV_PG_DB — matches the running database"
  else
    bad "database names" ".env says $ENV_PG_USER/$ENV_PG_DB but the running Postgres does not answer to that"
  fi
  if grep -qE '^POSTGRES_PASSWORD=bowpassword$' .env; then
    warn "postgres password" "still the shipped default"
  elif grep -qE '^POSTGRES_PASSWORD=.+' .env; then
    ok "postgres password" "set"
  else
    warn "postgres password" "not set — falling back to the compose default"
  fi
else
  bad ".env" "missing — copy .env.example and fill it in"
fi

# --------------------------------------------------------------------------
# Health and data
# --------------------------------------------------------------------------
head_ "Health"

if [[ -n "${PORT:-}" ]]; then
  # The health path is /health. /api/health returns 404 by design — the SPA
  # catch-all owns /api/* that the backend does not claim.
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://localhost:$PORT/health" 2>/dev/null)"
  [[ "$CODE" == "200" ]] && ok "/health" "200" || bad "/health" "${CODE:-unreachable}"
fi

if [[ -n "$APP_CONTAINER" ]]; then
  MIGRATION="$(docker exec -w /app/backend "$APP_CONTAINER" alembic current 2>/dev/null | tail -1)"
  info "migration head" "${MIGRATION:-unknown}"
fi

VOLUMES="$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -cE 'postgres_data|uploads_data' || true)"
info "data volumes" "${VOLUMES:-0} matching postgres_data / uploads_data"

# On Docker Desktop the root dir lives inside a VM and is not visible to `df`
# on the host, so ask the daemon instead — it works on both Desktop and a
# native Linux daemon.
DISK="$(docker system df --format '{{.Type}} {{.Size}} (reclaimable {{.Reclaimable}})' 2>/dev/null | grep -i '^images' | head -1)"
info "docker images" "${DISK:-unknown}"

HOST_DISK="$(df -h "$HOME" 2>/dev/null | awk 'NR==2 {print $4" free of "$2" ("$5" used)"}')"
info "host disk (\$HOME)" "${HOST_DISK:-unknown}"

# --------------------------------------------------------------------------
# Backups. An upgrade is only reversible if one of these exists.
# --------------------------------------------------------------------------
head_ "Backups"

BACKUP_DIR="${CITYAGENT_BACKUP_DIR:-$HOME/cityagent-backups}"
if [[ -d "$BACKUP_DIR" ]]; then
  NEWEST="$(/bin/ls -t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1)"
  if [[ -n "$NEWEST" ]]; then
    SIZE="$(du -h "$NEWEST" | cut -f1)"
    AGE_D=$(( ( $(date +%s) - $(stat -f %m "$NEWEST" 2>/dev/null || stat -c %Y "$NEWEST" 2>/dev/null) ) / 86400 ))
    if (( AGE_D > 7 )); then
      warn "newest dump" "$(basename "$NEWEST")  ${SIZE}  ${AGE_D}d old"
    else
      ok "newest dump" "$(basename "$NEWEST")  ${SIZE}  ${AGE_D}d old"
    fi
    info "total" "$(/bin/ls -1 "$BACKUP_DIR"/*.dump 2>/dev/null | wc -l | tr -d ' ') dump(s) in $BACKUP_DIR"
  else
    warn "dumps" "none in $BACKUP_DIR"
  fi
else
  warn "backup dir" "$BACKUP_DIR does not exist — nothing has been backed up"
fi

ROLLBACK_TAGS="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c ':pre-' || true)"
if [[ "${ROLLBACK_TAGS:-0}" -gt 0 ]]; then
  ok "rollback images" "$ROLLBACK_TAGS tagged pre-*"
  docker images --format '    {{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null | grep ':pre-' | head -5
else
  warn "rollback images" "none tagged pre-* — a rebuild would leave nothing to roll back to"
fi

printf "\n"
