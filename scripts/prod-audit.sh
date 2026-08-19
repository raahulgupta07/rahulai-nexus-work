#!/usr/bin/env bash
#
# prod-audit.sh — Phase 0. Confirm the membership diagnosis on a live server.
#
#   ./prod-audit.sh                    # auto-detect the postgres container
#   ./prod-audit.sh <container> <user> <db>
#
# ★READ ONLY. Every statement is a SELECT. Nothing is created, altered,
# deleted or locked. Safe to run on production during business hours.
#
# It answers four questions:
#   1. who holds duplicate membership rows (the cause of the 500s)
#   2. how many rows the migration would remove
#   3. whether the unique index would build
#   4. whether anything would be LOST by merging (notes, memory, defaults)
#
# Expected, from the members list screenshots:
#   kaungminhtet@cityholdings.com.mm  -> 3 rows
#   chitsnowwai@cityholdings.com.mm   -> 2 rows
#
set -uo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; GRN=$'\033[32m'; YEL=$'\033[33m'; OFF=$'\033[0m'
[[ -t 1 ]] || { BOLD=""; DIM=""; GRN=""; YEL=""; OFF=""; }

PGC="${1:-}"
PGUSER_IN="${2:-}"
PGDB_IN="${3:-}"

# --- find the database that actually holds THIS application's schema -------
# ★Not "a postgres container that answers" — a host can run several. The test
# is whether the `memberships` table exists, which is definitive and costs one
# query. Without it this picked an unrelated product's database on the first
# machine it ran on.
find_db() {
  local candidates users_dbs c u d
  if [[ -n "$PGC" ]]; then candidates="$PGC"
  else
    # ★Preference order, because a host can run several databases that all
    # carry this schema (a live install and a stock compare-instance, say).
    # Most specific first: the conventional name, then anything in a compose
    # project that looks like this product, then any postgres at all.
    candidates="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -x 'dash-postgres')
$(docker ps --filter "label=com.docker.compose.service=postgres" --format '{{.Label "com.docker.compose.project"}} {{.Names}}' 2>/dev/null | grep -iE 'insight|dash' | awk '{print $NF}')
$(docker ps --filter "label=com.docker.compose.service=postgres" --format '{{.Names}}' 2>/dev/null)
$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'postgres|pgvector|dash-db')"
  fi
  if [[ -n "$PGUSER_IN" && -n "$PGDB_IN" ]]; then users_dbs="$PGUSER_IN $PGDB_IN"
  else users_dbs="dash dash_insights
bow bagofwords
postgres postgres"
  fi
  while read -r c; do
    [[ -n "$c" ]] || continue
    while read -r u d; do
      [[ -n "$u" ]] || continue
      if docker exec "$c" psql -U "$u" -d "$d" -tAc \
           "select to_regclass('public.memberships') is not null" 2>/dev/null | grep -q t; then
        PGC="$c"; PGU="$u"; PGD="$d"; return 0
      fi
    done <<< "$users_dbs"
  done <<< "$(printf '%s\n' "$candidates" | awk 'NF && !seen[$0]++')"
  return 1
}

PGU=""; PGD=""
if ! find_db; then
  echo "Could not find a database containing this application's 'memberships' table."
  echo "Pass it explicitly:  $0 <container> <user> <db>"
  echo "Containers on this host:"
  docker ps --format '   {{.Names}}  ({{.Image}})' 2>/dev/null
  exit 2
fi

printf "\n${BOLD}membership audit${OFF}  container=%s  user=%s  db=%s  ${DIM}(read only)${OFF}\n" \
  "$PGC" "$PGU" "$PGD"

psql() { docker exec -i "$PGC" psql -U "$PGU" -d "$PGD" "$@"; }

psql <<'SQL'
\pset border 2

\echo ''
\echo '=== 1. WHO HAS DUPLICATE MEMBERSHIPS (the cause of the 500s) ==='
select
    u.email,
    o.name                as organization,
    count(*)              as membership_rows,
    string_agg(distinct m.role, ', ') as roles,
    min(m.created_at)::date as first_added,
    max(m.created_at)::date as last_added
from memberships m
join users u on u.id = m.user_id
left join organizations o on o.id = m.organization_id
where m.deleted_at is null and m.user_id is not null
group by u.email, o.name
having count(*) > 1
order by count(*) desc, u.email;

\echo ''
\echo '=== 2. SCALE — what the migration would do ==='
select
    count(*)                 as duplicated_pairs,
    coalesce(sum(n - 1), 0)  as rows_to_be_removed,
    count(distinct email)    as people_affected
from (
    select u.email, m.organization_id, count(*) as n
    from memberships m join users u on u.id = m.user_id
    where m.deleted_at is null and m.user_id is not null
    group by u.email, m.organization_id having count(*) > 1
) d;

\echo ''
\echo '=== 3. WOULD THE UNIQUE INDEX BUILD? (must be 0 after the merge) ==='
select count(*) as pairs_blocking_the_index
from (
    select user_id, organization_id from memberships
    where deleted_at is null and user_id is not null
    group by user_id, organization_id having count(*) > 1
) x;

\echo ''
\echo '=== 4. IS ANYTHING AT RISK IN THE MERGE? ==='
\echo '(the migration keeps every one of these; this shows what it must carry)'
select
    u.email,
    m.created_at::date,
    m.role,
    (m.note is not null)                    as has_note,
    (m.memory is not null)                  as has_memory,
    (m.default_llm_model_id is not null)    as has_default_model,
    (m.default_data_source_ids is not null) as has_default_agents,
    (select count(*) from group_memberships g where g.membership_id = m.id) as group_links
from memberships m
join users u on u.id = m.user_id
where m.deleted_at is null and m.user_id is not null
  and (m.user_id, m.organization_id) in (
      select user_id, organization_id from memberships
      where deleted_at is null and user_id is not null
      group by user_id, organization_id having count(*) > 1
  )
order by u.email, m.created_at;

\echo ''
\echo '=== 5. THE TWO USERS FROM THE LOGS ==='
select u.email,
       count(*) filter (where m.deleted_at is null)     as live_rows,
       count(*) filter (where m.deleted_at is not null) as removed_rows
from users u left join memberships m on m.user_id = u.id
where u.email in ('chitsnowwai@cityholdings.com.mm',
                  'kaungminhtet@cityholdings.com.mm')
group by u.email;

\echo ''
\echo '=== 6. REAL HEADCOUNT vs WHAT THE MEMBERS PAGE SHOWS ==='
select count(*)                as membership_rows_listed,
       count(distinct user_id) as actual_people
from memberships
where deleted_at is null and user_id is not null;

\echo ''
\echo '=== 7. DUPLICATE USER ACCOUNTS (expected: none) ==='
select lower(email) as email, count(*) as accounts
from users group by 1 having count(*) > 1;
SQL

printf "\n${BOLD}what to look for${OFF}\n"
printf "  ${DIM}1${OFF} kaungminhtet should show 3 rows, chitsnowwai 2 — that confirms the diagnosis\n"
printf "  ${DIM}2${OFF} 'rows_to_be_removed' is exactly what the migration deletes, nothing else\n"
printf "  ${DIM}3${OFF} must reach 0 after the migration, or the index cannot build\n"
printf "  ${DIM}6${OFF} the two numbers differ by the duplicate count — your real headcount\n"
printf "  ${DIM}7${OFF} any rows here would mean duplicate ACCOUNTS, a different problem\n\n"
