#!/usr/bin/env bash
#
# release-test.sh — prove a build fixes what it claims, against real data.
#
#   ./scripts/release-test.sh <image-tag> [dump.sql.gz]
#
# Runs the gates that `verify.sh` cannot: `verify.sh` proves the tree is sane,
# this proves the ARTIFACT behaves on a copy of production.
#
#   Gate 0  the image is the one you think it is
#   Gate 2  the migration, on a clone, with the data-loss cases planted
#   Gate 3  the reported bugs, control-and-treatment, over HTTP
#
# ★Everything happens on a scratch database restored from a dump. The live
# database is never touched. The scratch database is dropped at the end, and
# on failure too.
#
# ★Gate 3 is control-and-treatment on purpose. Asserting that endpoints return
# 200 proves nothing on its own — they returned 200 before the bug existed and
# would return 200 if the fix did nothing. The claim is that they 500 WITH the
# fault and 200 WITHOUT it, on the same database, so both halves are measured.
#
# ★What this cannot see: the browser. Python and curl read JSON; they cannot
# tell you a page throws on boot. A release shipped from this repo once with a
# dead agent picker and 5,451 green tests. Run the Playwright smoke as well.
#
set -uo pipefail

BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; DIM=$'\033[2m'; OFF=$'\033[0m'
[[ -t 1 ]] || { BOLD=""; RED=""; GRN=""; DIM=""; OFF=""; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$REPO"
IMAGE="${1:-}"
DUMP="${2:-$(ls -t _backups/*.sql.gz 2>/dev/null | head -1)}"
SCRATCH="reltest_$$"
# ★A SECOND database for the control, restored and never migrated. The old
# image cannot boot against a database the new migration has touched — alembic
# refuses with "Can't locate revision identified by 'memuniq01'", because that
# revision does not exist in the old image. Measured, not assumed. It is also
# the rollback rule for every deploy: going back means moving alembic_version
# back, NOT restoring a dump.
CONTROL_DB="reltest_ctl_$$"
PGC="${DASH_POSTGRES_CONTAINER:-dash-postgres}"
APPC="${DASH_APP_CONTAINER:-dash-app}"
PORT="${RELEASE_TEST_PORT:-8099}"
FAILED=0

[[ -n "$IMAGE" ]] || { echo "usage: $0 <image-tag> [dump.sql.gz]"; exit 2; }
[[ -n "$DUMP" && -f "$DUMP" ]] || { echo "no dump found — pass one explicitly"; exit 2; }

ok()   { printf "   ${GRN}ok${OFF}    %s\n" "$1"; }
bad()  { printf "   ${RED}FAIL${OFF}  %s\n" "$1"; FAILED=$((FAILED+1)); }
step() { printf "\n${BOLD}%s${OFF}\n" "$1"; }
note() { printf "   ${DIM}%s${OFF}\n" "$1"; }

Q() { docker exec "$PGC" psql -U dash -d "$SCRATCH" -tAc "$1" 2>/dev/null; }

# ★★★A DUMP MAY ALREADY BE MIGRATED, and then this test proves nothing.
#
# Taken from a database that has already run memuniq01, the dump arrives with
# uq_membership_user_org in place and alembic_version at memuniq01. The planted
# duplicate is then REFUSED by the index, the migration does not re-run, and
# every assertion about merging fails — against rows that never existed. The
# first run of this script did exactly that and reported "DATA LOSS" on a build
# that was fine, which is worse than reporting nothing.
#
# So rewind the scratch copy to just before the migration, whatever it was
# dumped from. Scratch databases only; the dump and the live database are not
# touched.
rewind_to_premigration() {
  docker exec "$PGC" psql -U dash -d "$1" -q \
    -c "DROP INDEX IF EXISTS uq_membership_user_org;" \
    -c "UPDATE alembic_version SET version_num='evalsuitefk01' WHERE version_num='memuniq01';" \
    >/dev/null 2>&1
}

cleanup() {
  docker rm -f "reltest-$$" >/dev/null 2>&1 || true
  docker exec "$PGC" psql -U dash -d postgres -q -c "DROP DATABASE IF EXISTS $SCRATCH;" >/dev/null 2>&1 || true
  docker exec "$PGC" psql -U dash -d postgres -q -c "DROP DATABASE IF EXISTS $CONTROL_DB;" >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf "\n${BOLD}release-test${OFF}  image=%s  dump=%s\n" "$IMAGE" "$(basename "$DUMP")"

# ===========================================================================
step "Gate 0 — is this the image you think it is?"
# ===========================================================================
BUILT="$(docker run --rm --entrypoint sh "$IMAGE" -c 'cat /app/VERSION' 2>/dev/null | tr -d '[:space:]')"
TREE="$(tr -d '[:space:]' < VERSION)"
[[ "$BUILT" == "$TREE" ]] && ok "image reports $BUILT, tree says $TREE" \
                          || bad "image says '${BUILT:-nothing}' but the tree says '$TREE' — a cached layer"
ARCH="$(docker image inspect "$IMAGE" --format '{{.Architecture}}' 2>/dev/null)"
note "architecture: $ARCH"
[[ "$ARCH" == "amd64" ]] || note "★ not amd64 — this image CANNOT run on the x86_64 servers"

# ===========================================================================
step "Gate 2 — the migration, on a clone, with the data-loss cases planted"
# ===========================================================================
docker exec "$PGC" psql -U dash -d postgres -q -c "CREATE DATABASE $SCRATCH;" >/dev/null 2>&1
gzcat "$DUMP" | docker exec -i "$PGC" psql -U dash -d "$SCRATCH" -q >/dev/null 2>&1
TABLES="$(Q "select count(*) from information_schema.tables where table_schema='public';")"
[[ "${TABLES:-0}" -gt 50 ]] && ok "restored $TABLES tables" || { bad "restore failed"; exit 1; }
if [[ -n "$(Q "select 1 from pg_indexes where indexname='uq_membership_user_org';")" ]]; then
  note "this dump is already migrated — rewinding the scratch copy so the migration can be tested"
  rewind_to_premigration "$SCRATCH"
fi

U="$(Q "select m.user_id from memberships m where m.deleted_at is null and m.user_id is not null
        group by m.user_id having count(*)=1 limit 1;")"
ORG="$(Q "select organization_id from memberships where user_id='$U' and deleted_at is null limit 1;")"
SURV="$(Q "select id from memberships where user_id='$U' and deleted_at is null order by created_at asc, id asc limit 1;")"

# a duplicate carrying a note the survivor does NOT have
Q "insert into memberships (id,user_id,organization_id,role,note,created_at,updated_at)
   values ('rt-dup-1','$U','$ORG','member','NOTE ONLY ON THE LOSER',now(),now());" >/dev/null
# a group link on the row that is about to be deleted
GID="$(Q "select id from groups limit 1;")"
if [[ -z "$GID" ]]; then
  Q "insert into groups (id,name,organization_id,created_at,updated_at)
     values ('rt-grp-1','Release Test','$ORG',now(),now());" >/dev/null; GID=rt-grp-1
fi
Q "delete from group_memberships where membership_id='$SURV' and group_id='$GID';" >/dev/null
Q "insert into group_memberships (id,group_id,user_id,membership_id,created_at,updated_at)
   values ('rt-gm-1','$GID','$U','rt-dup-1',now(),now());" >/dev/null
note "planted: a duplicate with a unique note, and a group link on the losing row"

NET="$(docker inspect "$APPC" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')"
PW="$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"
KEY="$(grep -E '^DASH_ENCRYPTION_KEY=' .env | cut -d= -f2-)"
URL="postgresql://dash:${PW}@postgres:5432/${SCRATCH}"
MIG=(docker run --rm -i --network "$NET" -e DASH_DATABASE_URL="$URL"
     -e DASH_ENCRYPTION_KEY="$KEY" -w /app/backend "$IMAGE")

HEADS="$("${MIG[@]}" python -m alembic heads 2>/dev/null | grep -c '(head)')"
[[ "${HEADS:-0}" -eq 1 ]] && ok "exactly one migration head" || bad "expected 1 head, found ${HEADS:-0}"
"${MIG[@]}" python -m alembic upgrade head >/dev/null 2>&1
[[ "$(Q "select version_num from alembic_version;")" == "memuniq01" ]] \
  && ok "migrated to memuniq01" || bad "migration did not reach memuniq01"

[[ "$(Q "select count(*) from memberships where user_id='$U' and deleted_at is null;")" == "1" ]] \
  && ok "duplicate collapsed to one row" || bad "duplicate not collapsed"
[[ "$(Q "select note from memberships where id='$SURV';")" == "NOTE ONLY ON THE LOSER" ]] \
  && ok "the note that existed only on the losing row was preserved" \
  || bad "★DATA LOSS: the losing row's note was discarded"
[[ "$(Q "select count(*) from group_memberships where id='rt-gm-1';")" == "1" ]] \
  && ok "group link survived the delete" \
  || bad "★DATA LOSS: the group link was cascade-deleted with the duplicate"
[[ "$(Q "select membership_id from group_memberships where id='rt-gm-1';")" == "$SURV" ]] \
  && ok "group link re-pointed at the surviving membership" || bad "group link points elsewhere"

DUPERR="$(docker exec "$PGC" psql -U dash -d "$SCRATCH" -tAc \
  "insert into memberships (id,user_id,organization_id,role,created_at,updated_at)
   values ('rt-should-fail','$U','$ORG','member',now(),now());" 2>&1 | grep -c 'uq_membership_user_org')"
[[ "${DUPERR:-0}" -ge 1 ]] && ok "a new duplicate is refused by the database" \
                           || bad "the unique index does not prevent duplicates"
SOFT="$(docker exec "$PGC" psql -U dash -d "$SCRATCH" -tAc \
  "insert into memberships (id,user_id,organization_id,role,deleted_at,created_at,updated_at)
   values ('rt-soft','$U','$ORG','member',now(),now(),now());" 2>&1 | grep -c 'INSERT')"
[[ "${SOFT:-0}" -ge 1 ]] && ok "a soft-deleted row beside a live one is still allowed" \
                         || bad "re-invite after removal is blocked"
Q "delete from memberships where id='rt-soft';" >/dev/null

# ===========================================================================
step "Gate 3 — the reported bugs, control and treatment, over HTTP"
# ===========================================================================
# ★The control gets its OWN un-migrated database. Reusing the migrated one
# would test nothing: the old image refuses to start against it at all, so a
# "control did not come up" result would be indistinguishable from the control
# being unable to reproduce the fault.
QC() { docker exec "$PGC" psql -U dash -d "$CONTROL_DB" -tAc "$1" 2>/dev/null; }
docker exec "$PGC" psql -U dash -d postgres -q -c "CREATE DATABASE $CONTROL_DB;" >/dev/null 2>&1
gzcat "$DUMP" | docker exec -i "$PGC" psql -U dash -d "$CONTROL_DB" -q >/dev/null 2>&1
# The control runs the OLD image, which predates the index — and a dump taken
# after the migration carries it, so it must come off here too or the duplicate
# cannot be planted and the control silently proves nothing.
rewind_to_premigration "$CONTROL_DB"
QC "insert into memberships (id,user_id,organization_id,role,created_at,updated_at)
    values ('rt-dup-2','$U','$ORG','member',now(),now());" >/dev/null
[[ "$(QC "select count(*) from memberships where user_id='$U' and deleted_at is null;")" == "2" ]] \
  && note "control database: duplicate planted, not migrated" \
  || bad "could not plant the duplicate on the control database"

# And the treatment keeps its duplicate too — the migration removed it, so put
# one back to prove the CODE tolerates it even before the data is cleaned.
Q "insert into memberships (id,user_id,organization_id,role,created_at,updated_at)
   values ('rt-dup-3','$U','$ORG','member',now(),now());" >/dev/null 2>&1
if [[ "$(Q "select count(*) from memberships where user_id='$U' and deleted_at is null;")" != "2" ]]; then
  note "the unique index refused it (as designed) — dropping the index to re-plant"
  Q "drop index if exists uq_membership_user_org;" >/dev/null
  Q "insert into memberships (id,user_id,organization_id,role,created_at,updated_at)
     values ('rt-dup-3','$U','$ORG','member',now(),now());" >/dev/null
fi

run_app() {  # $1 = image, $2 = database
  docker rm -f "reltest-$$" >/dev/null 2>&1
  docker run -d --name "reltest-$$" --network "$NET" -p "${PORT}:3000" \
    -e DASH_DATABASE_URL="postgresql://dash:${PW}@postgres:5432/$2" \
    -e DASH_ENCRYPTION_KEY="$KEY" -e ENVIRONMENT=production \
    "$1" >/dev/null
  for _ in $(seq 1 60); do
    [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:${PORT}/health" 2>/dev/null)" == "200" ]] && return 0
    sleep 2
  done
  return 1
}

probe() {  # prints "<200-count> <500-count>"
  local email tok org o5 o2
  email="$(Q "select email from users where id='$U';")"
  docker cp scripts/mint-user-tokens.py "reltest-$$":/app/backend/ >/dev/null 2>&1
  docker exec -w /app/backend "reltest-$$" python mint-user-tokens.py /tmp/t.json "$email" >/dev/null 2>&1
  docker cp "reltest-$$":/tmp/t.json /tmp/reltest-tok.json >/dev/null 2>&1
  tok="$(python3 -c "import json;d=json.load(open('/tmp/reltest-tok.json'));print(list(d['users'].values())[0]['token'])" 2>/dev/null)"
  org="$(python3 -c "import json;print(json.load(open('/tmp/reltest-tok.json'))['org']['id'])" 2>/dev/null)"
  [[ -n "$tok" ]] || { echo "0 0"; return; }
  o2=0; o5=0
  for p in /api/reports /api/llm/models /api/projects /api/organization/settings \
           /api/data_sources /api/files /api/instructions /api/prompts; do
    c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
         -H "Authorization: Bearer $tok" -H "X-Organization-Id: $org" "http://localhost:${PORT}${p}")"
    [[ "$c" == "200" ]] && o2=$((o2+1))
    [[ "$c" == "500" ]] && o5=$((o5+1))
  done
  echo "$o2 $o5"
}

CONTROL_IMG="${RELEASE_TEST_CONTROL:-}"
if [[ -n "$CONTROL_IMG" ]]; then
  if run_app "$CONTROL_IMG" "$CONTROL_DB"; then
    read -r C2 C5 <<< "$(probe)"
    [[ "${C5:-0}" -gt 0 ]] && ok "control ($CONTROL_IMG): $C5 endpoints fail with the duplicate present" \
                           || bad "control did not reproduce the fault — the test proves nothing"
  else
    bad "control image did not come up (see: docker logs reltest-$$)"
  fi
else
  note "no control image given (RELEASE_TEST_CONTROL=<old tag>) — treatment only"
fi

if run_app "$IMAGE" "$SCRATCH"; then
  read -r T2 T5 <<< "$(probe)"
  [[ "${T5:-1}" -eq 0 ]] && ok "treatment: no endpoint fails with the duplicate present" \
                         || bad "treatment still fails on $T5 endpoint(s)"
  [[ "${T2:-0}" -ge 8 ]] && ok "treatment: all 8 endpoints answer" || bad "only ${T2:-0}/8 answered"
else
  bad "the image under test did not come up"
fi

printf "\n"
if (( FAILED == 0 )); then
  printf "${GRN}${BOLD}PASS${OFF}  %s behaves on a copy of production\n" "$IMAGE"
  printf "  ${DIM}Still required: the Playwright smoke. Nothing above can see a rendered page.${OFF}\n\n"
  exit 0
fi
printf "${RED}${BOLD}FAIL${OFF}  %d check(s) failed — do not deploy\n\n" "$FAILED"
exit 1
