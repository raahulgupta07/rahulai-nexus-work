#!/usr/bin/env bash
#
# upgrade.sh — upgrade a CityAgent Insights install, safely.
#
#   ./upgrade.sh              upgrade to whatever is on the current branch
#   ./upgrade.sh --dry-run    checks and backups only; no build, no swap
#   ./upgrade.sh --rollback   return to the previously tagged image
#   ./upgrade.sh --help
#
# Why this exists
# ---------------
# The manual upgrade is eight steps, and four of them fail SILENTLY:
#
#   * no database dump      → no route back to today's data
#   * no image tag          → the build replaces :local and the daemon garbage
#                             collects the old image; there is nothing to roll
#                             back to. This has already cost two working images.
#   * no FE_CACHEBUST       → Docker reuses the cached `COPY ./frontend` layer,
#                             the build reports success, and the image ships the
#                             OLD interface
#   * no pre-swap check     → you deploy a build that did not take
#
# None of those error. They all look like success. So every one of them is a
# hard gate below: the script EXITS rather than continuing past a failed check.
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

BACKUP_DIR="${CITYAGENT_BACKUP_DIR:-$HOME/cityagent-backups}"
KEEP_BACKUPS="${CITYAGENT_KEEP_BACKUPS:-5}"
# A real dump of this schema is several MB. Overridable so the gate itself can
# be tested without having to break Postgres on a live install.
MIN_DUMP_BYTES="${CITYAGENT_MIN_DUMP_BYTES:-$((100 * 1024))}"

MODE="upgrade"
WANT_PROJECT="${CITYAGENT_PROJECT:-}"

while (( $# )); do
  case "$1" in
    --dry-run)  MODE="dryrun" ;;
    --rollback) MODE="rollback" ;;
    --project)  WANT_PROJECT="${2:-}"; shift
                [[ -n "$WANT_PROJECT" ]] || die "--project needs a name" ;;
    --help|-h)  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          die "unknown option: $1  (try --help)" ;;
  esac
  shift
done

# ===========================================================================
# 0. Identify the install
#
# This repo carries three compose files and a host may run more than one stack.
# Everything is read from the RUNNING container's own labels — never assumed,
# never guessed. If that is ambiguous, stop: upgrading the wrong stack is worse
# than not upgrading.
# ===========================================================================
step "Identifying the running install"

# ★ Deliberately NOT `mapfile` — macOS ships bash 3.2, where mapfile does not
# exist. It is a builtin, so `bash -n` still passes and the failure only shows
# up at run time, on the machine you least want it to.
DOCKER_FILTERS=(--filter "label=com.docker.compose.service=app")
if [[ -n "$WANT_PROJECT" ]]; then
  DOCKER_FILTERS+=(--filter "label=com.docker.compose.project=$WANT_PROJECT")
fi

APP_LIST="$(docker ps "${DOCKER_FILTERS[@]}" --format '{{.Names}}' 2>/dev/null || true)"
APP_COUNT="$(printf '%s' "$APP_LIST" | grep -c . || true)"

if (( APP_COUNT == 0 )); then
  if [[ -n "$WANT_PROJECT" ]]; then
    die "no running app container in project '$WANT_PROJECT'.
    Running projects:
$(docker ps --filter "label=com.docker.compose.service=app" \
    --format '      {{.Names}}  (project: {{.Label "com.docker.compose.project"}})')"
  fi
  die "no running app container found. Start the stack first, or use the manual steps in UPGRADE.md."
fi

# A host can run several stacks — this repo's own live and shadow stacks, and
# sibling products built from the same upstream. Upgrading the wrong one is far
# worse than not upgrading, so ambiguity is a hard stop, never a best guess.
if (( APP_COUNT > 1 )); then
  die "found $APP_COUNT app containers. Refusing to guess which to upgrade.
    Name one with --project:
$(docker ps "${DOCKER_FILTERS[@]}" \
    --format '      --project {{.Label "com.docker.compose.project"}}   ({{.Names}})')"
fi

APP="$(printf '%s' "$APP_LIST" | head -1)"
PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$APP")"
# ★ EVERY labelled compose file, not just the first. A stack brought up with
# `-f a.yaml -f b.yaml` is labelled with both, comma-separated, and the overlay
# is where this deployment's environment passthrough lives — the feature flags
# among them. Taking `head -1` builds and starts the app WITHOUT that overlay:
# the container comes up healthy and serves pages, so it reads as a successful
# upgrade, while flags the install depends on are simply absent. The order in
# the label is the order they were passed, and compose overlays are
# order-sensitive, so it is preserved exactly.
COMPOSE_FILES=()
while IFS= read -r _cf; do
  [[ -n "$_cf" ]] || continue
  COMPOSE_FILES+=("$(basename "$_cf")")
done < <(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$APP" | tr ',' '\n')
[[ ${#COMPOSE_FILES[@]} -gt 0 ]] || die "container '$APP' carries no compose file label. Cannot tell how it was started."
IMAGE="$(docker inspect -f '{{.Config.Image}}' "$APP")"
IMAGE_REPO="${IMAGE%%:*}"

PG="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" \
                --filter "label=com.docker.compose.service=postgres" \
                --format '{{.Names}}' | head -1)"
[[ -n "$PG" ]] || die "no postgres container in project '$PROJECT'. Cannot take a backup, so cannot continue."

for _cf in "${COMPOSE_FILES[@]}"; do
  [[ -f "$_cf" ]] || die "compose file '$_cf' not found in $REPO_DIR"
done

OLD_VERSION="$(docker exec "$APP" cat /app/VERSION 2>/dev/null || echo unknown)"

ok "app        $APP"
ok "postgres   $PG"
ok "project    $PROJECT"
ok "compose    ${COMPOSE_FILES[*]}"
ok "image      $IMAGE"
ok "version    $OLD_VERSION"

DC=(docker compose -p "$PROJECT")
for _cf in "${COMPOSE_FILES[@]}"; do DC+=(-f "$_cf"); done

# ===========================================================================
# Rollback path
# ===========================================================================
if [[ "$MODE" == "rollback" ]]; then
  step "Rollback"

  # ★ Do NOT compute the tag as "pre-$OLD_VERSION". A `pre-X` tag is written
  # while LEAVING X, so it holds the image OF X — and $OLD_VERSION here is the
  # version currently RUNNING, i.e. the one being rolled back FROM. Deriving
  # the name that way looks obviously right and is off by exactly one release:
  # after the first upgrade it searches for a tag that only exists if you had
  # already upgraded away from the running version. Rollback could never have
  # succeeded. Instead: walk the repo's own pre-* tags newest-first and take
  # the first one that is not the running version, reading each candidate's
  # /app/VERSION rather than trusting its name.
  ROLLBACK_TAG=""
  ROLLBACK_VERSION=""
  CANDIDATES="$(docker images "$IMAGE_REPO" --format '{{.Tag}}' 2>/dev/null | grep '^pre-' || true)"

  while IFS= read -r tag; do
    [[ -n "$tag" ]] || continue
    cand_version="$(docker run --rm --entrypoint sh "$IMAGE_REPO:$tag" -c 'cat /app/VERSION' 2>/dev/null || echo unknown)"
    if [[ "$cand_version" != "$OLD_VERSION" && "$cand_version" != "unknown" ]]; then
      ROLLBACK_TAG="$IMAGE_REPO:$tag"
      ROLLBACK_VERSION="$cand_version"
      break
    fi
  done <<< "$CANDIDATES"

  if [[ -z "$ROLLBACK_TAG" ]]; then
    if [[ -z "$CANDIDATES" ]]; then
      die "no '$IMAGE_REPO:pre-*' image exists, so there is nothing to roll back to.
    A rollback target is only created by an upgrade, and only for the release
    it upgraded away from."
    fi
    die "every '$IMAGE_REPO:pre-*' image already reports version $OLD_VERSION,
    which is what is running now — rolling back would change nothing. Found:
$(printf '%s\n' "$CANDIDATES" | sed "s|^|      $IMAGE_REPO:|")"
  fi

  printf "\n  This retags ${BOLD}%s${OFF} (version ${BOLD}%s${OFF}) back to ${BOLD}%s${OFF} and restarts.\n" \
    "$ROLLBACK_TAG" "$ROLLBACK_VERSION" "$IMAGE"
  printf "  The database is ${BOLD}not${OFF} touched. If a migration is the problem, restore a dump\n"
  printf "  separately — see the command printed at the end of the failed upgrade.\n\n"
  read -r -p "  Continue? [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || die "cancelled"

  docker tag "$ROLLBACK_TAG" "$IMAGE"
  "${DC[@]}" up -d app
  ok "rolled back to $ROLLBACK_VERSION  ($ROLLBACK_TAG)"
  printf "\n  Verify with: ${BOLD}./preflight.sh${OFF}\n\n"
  exit 0
fi

# ===========================================================================
# 1. Configuration sanity — BEFORE anything is changed
#
# BOW_ENCRYPTION_KEY decrypts every stored credential. If it is absent the app
# does not fail; it mints a new random key each startup and keeps it in memory
# only, silently orphaning everything the previous run encrypted.
# ===========================================================================
step "Checking configuration"

[[ -f .env ]] || die ".env not found. Copy .env.example and fill it in."

grep -qE '^BOW_ENCRYPTION_KEY=.+' .env \
  || die "BOW_ENCRYPTION_KEY is empty or missing in .env.
    Without it the app mints a NEW key on every restart and every stored
    credential — connectors, OAuth tokens, LDAP, SSO — becomes unreadable.
    Fix .env before upgrading."
ok "encryption key present"

cp .env ".env.bak-$OLD_VERSION"
ok "saved .env.bak-$OLD_VERSION"

if grep -qE '^POSTGRES_PASSWORD=bowpassword$' .env; then
  warn "POSTGRES_PASSWORD is still the shipped default 'bowpassword'"
  note "not blocking the upgrade, but change it soon"
fi

# ===========================================================================
# 2. Database backup — the real rollback
#
# The migrations do have downgrade() functions, but every one of them is a
# drop: ca03putbl01act drops user_data_source_tables.is_active (each member's
# per-user table selection), ca06localrt01 drops the local-runtime tables. So
# the schema reverses and the data it held does not. This dump is the only
# route back to the current agents, reports, instructions and credentials.
# ===========================================================================
step "Backing up the database"

mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/cityagent-$OLD_VERSION-$(date +%Y%m%d-%H%M%S).dump"

PGUSER_VAL="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- || true)"; PGUSER_VAL="${PGUSER_VAL:-bow}"
PGDB_VAL="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- || true)";     PGDB_VAL="${PGDB_VAL:-bagofwords}"

note "pg_dump -U $PGUSER_VAL -d $PGDB_VAL"
docker exec "$PG" pg_dump -U "$PGUSER_VAL" -d "$PGDB_VAL" -Fc > "$DUMP" \
  || die "pg_dump failed. Not continuing without a backup."

DUMP_BYTES="$(wc -c < "$DUMP" | tr -d ' ')"
(( DUMP_BYTES > MIN_DUMP_BYTES )) \
  || die "backup is only ${DUMP_BYTES} bytes — that is not a real dump.
    Check that '$PG' is healthy and that $PGUSER_VAL can read $PGDB_VAL.
    Bad file left at: $DUMP"

ok "$(basename "$DUMP")  ($(du -h "$DUMP" | cut -f1))"

# Retention: keep the newest N, delete the rest.
OLD_DUMPS="$(/bin/ls -t "$BACKUP_DIR"/*.dump 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) || true)"
if [[ -n "$OLD_DUMPS" ]]; then
  echo "$OLD_DUMPS" | xargs rm -f
  note "pruned $(echo "$OLD_DUMPS" | wc -l | tr -d ' ') old dump(s), keeping $KEEP_BACKUPS"
fi

# ===========================================================================
# 3. Tag the current image
#
# `docker compose build` re-points the :local tag at the new image and the
# daemon then collects the orphaned parent. A tag is the ONLY thing that keeps
# the old image alive to roll back to.
# ===========================================================================
step "Tagging the current image"

ROLLBACK_TAG="$IMAGE_REPO:pre-$OLD_VERSION"
docker tag "$IMAGE" "$ROLLBACK_TAG" || die "could not tag $IMAGE"
docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1 || die "tag $ROLLBACK_TAG did not stick"
ok "$ROLLBACK_TAG"

# ===========================================================================
# 4. Update the source
# ===========================================================================
step "Updating source"

if [[ -n "$(git status --porcelain)" ]]; then
  printf "\n"
  git status --short | sed 's/^/      /'
  die "working tree has uncommitted changes.
    Commit or stash them first. This script will not discard your work,
    and you should not use 'git reset --hard' to get past this without
    knowing what those changes are."
fi

git pull --ff-only || die "git pull failed. Resolve it manually, then re-run."

NEW_VERSION="$(cat VERSION)"
ok "$OLD_VERSION → $NEW_VERSION"

if [[ "$OLD_VERSION" == "$NEW_VERSION" ]]; then
  warn "version unchanged — there may be nothing new to deploy"
fi

if [[ -f CHANGELOG.md ]]; then
  printf "\n  ${BOLD}What is new${OFF}\n"
  awk '/^## Version/{n++} n<=3' CHANGELOG.md | head -30 | sed 's/^/      /'
fi

if [[ "$MODE" == "dryrun" ]]; then
  printf "\n  ${BOLD}Dry run complete.${OFF} Backup taken, image tagged, source updated.\n"
  printf "  Nothing was built and nothing was swapped. Re-run without --dry-run to finish.\n\n"
  exit 0
fi

# ===========================================================================
# 5. Build
#
# FE_CACHEBUST is not optional and is not a flag. Without it Docker reuses the
# cached `COPY ./frontend` layer and produces a successful build carrying the
# OLD interface — the single most confusing failure mode this project has.
# ===========================================================================
step "Building (this takes a few minutes)"

"${DC[@]}" build --build-arg FE_CACHEBUST="$(date +%s)" app \
  || die "build failed. Nothing was swapped; the running app is untouched."
ok "built"

# ===========================================================================
# 6. THE GATE — verify the artifact before deploying it
#
# Checks the image that was just built, not the container still running.
# ===========================================================================
step "Verifying the built image"

BUILT_VERSION="$(docker run --rm --entrypoint sh "$IMAGE" -c 'cat /app/VERSION' 2>/dev/null || echo unknown)"

[[ "$BUILT_VERSION" != "unknown" ]] || die "could not read /app/VERSION from the built image."

if [[ "$BUILT_VERSION" != "$NEW_VERSION" ]]; then
  die "the built image reports $BUILT_VERSION but the repo says $NEW_VERSION.
    The build did not pick up the new source — usually a cached layer.
    NOT swapping. Try:  docker builder prune  then re-run."
fi
ok "image reports $BUILT_VERSION"

# ===========================================================================
# 7. Swap. Migrations run automatically at startup.
# ===========================================================================
step "Deploying"

"${DC[@]}" up -d app || die "failed to start the new container.
    Roll back with:  ./upgrade.sh --rollback"

PORT="$(docker port "$APP" 3000 2>/dev/null | head -1 | sed 's/.*://')"
printf "  waiting for health"
HEALTHY=0
for _ in $(seq 1 60); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:${PORT}/health" 2>/dev/null || true)"
  if [[ "$CODE" == "200" ]]; then HEALTHY=1; printf "\n"; break; fi
  printf "."; sleep 2
done

if (( HEALTHY == 0 )); then
  printf "\n"
  warn "the app did not report healthy within 2 minutes"
  note "migration output:  docker logs $APP 2>&1 | grep -iE 'alembic|error' | tail -30"
  note "roll back:         ./upgrade.sh --rollback"
  die "deployment did not come up cleanly"
fi
ok "/health 200"

MIGRATION="$(docker exec -w /app/backend "$APP" alembic current 2>/dev/null | tail -1 || echo unknown)"
ok "migration head  $MIGRATION"

SERVED="$(curl -s --max-time 5 "http://localhost:${PORT}/api/changelog" | sed -n 's/.*"current_version":"\([^"]*\)".*/\1/p' || true)"
ok "serving version $SERVED"

VOLUMES="$(docker volume ls --format '{{.Name}}' | grep -cE 'postgres_data|uploads_data' || true)"
ok "data volumes    $VOLUMES"

# ===========================================================================
# 8. Tell the operator how to undo this specific run
# ===========================================================================
printf "\n${BOLD}Upgraded %s → %s${OFF}\n\n" "$OLD_VERSION" "$SERVED"
printf "  Hard-refresh the browser (Cmd/Ctrl + Shift + R) — the open tab still\n"
printf "  runs the old bundle until it reloads.\n"
printf "\n  ${BOLD}To undo this upgrade${OFF}\n"
printf "      ./upgrade.sh --rollback\n"
printf "\n  ${BOLD}If the database also needs restoring${OFF} (only if a migration is at fault)\n"
printf "      docker exec -i %s pg_restore -U %s -d %s -c < %s\n\n" \
       "$PG" "$PGUSER_VAL" "$PGDB_VAL" "$DUMP"
