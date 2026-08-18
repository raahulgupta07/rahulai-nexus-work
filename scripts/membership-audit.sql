-- Membership audit — READ ONLY. Nothing here writes, locks or drops.
--
-- Run against each deployment's database before the dedupe migration, so we
-- know what the migration will actually touch rather than assuming.
--
--   docker exec -i <postgres-container> psql -U <user> -d <db> -f - < membership-audit.sql
--
-- or paste the blocks individually. Every query is a SELECT.
--
-- Context: one duplicate `memberships` row for a single user returned 500 on
-- nearly every org-scoped route for that user — 572 of 3613 requests in one
-- morning on production. Reproduced on a clone of the production dump: nine
-- endpoints 500, and the workspace switcher listed the same workspace twice.

\echo '=== 1. DUPLICATES — the rows that caused the outage ==='
-- Each row here is one person whose every request 500s until the fix ships.
select
    u.email,
    m.organization_id,
    o.name        as organization,
    count(*)      as membership_rows,
    min(m.created_at) as first_created,
    max(m.created_at) as last_created,
    string_agg(distinct m.role, ', ') as roles
from memberships m
join users u on u.id = m.user_id
left join organizations o on o.id = m.organization_id
where m.deleted_at is null
  and m.user_id is not null
group by u.email, m.organization_id, o.name
having count(*) > 1
order by count(*) desc, u.email;

\echo ''
\echo '=== 2. HOW MANY PEOPLE ARE AFFECTED ==='
select
    count(*)                            as duplicated_pairs,
    coalesce(sum(n - 1), 0)             as rows_the_migration_would_remove,
    count(distinct email)               as people_affected
from (
    select u.email, m.organization_id, count(*) as n
    from memberships m
    join users u on u.id = m.user_id
    where m.deleted_at is null and m.user_id is not null
    group by u.email, m.organization_id
    having count(*) > 1
) d;

\echo ''
\echo '=== 3. WOULD THE UNIQUE INDEX BUILD? ==='
-- Must return 0. Any other number means the dedupe has to run first, which is
-- exactly the order the migration uses.
select count(*) as pairs_blocking_the_unique_index
from (
    select user_id, organization_id
    from memberships
    where deleted_at is null and user_id is not null
    group by user_id, organization_id
    having count(*) > 1
) x;

\echo ''
\echo '=== 4. ROLES THAT DISAGREE BETWEEN DUPLICATE ROWS ==='
-- If this returns nothing, collapsing duplicates cannot change anyone's access
-- and the merge is purely mechanical. If it returns rows, read them before
-- running the migration.
select u.email, m.organization_id, string_agg(distinct m.role, ' | ') as differing_roles
from memberships m
join users u on u.id = m.user_id
where m.deleted_at is null and m.user_id is not null
group by u.email, m.organization_id
having count(distinct m.role) > 1;

\echo ''
\echo '=== 5. PER-ROW DETAIL WORTH PRESERVING ==='
-- The migration keeps the oldest row. These columns are per-membership and
-- would be lost if the row holding them is the one discarded, so they are
-- carried across. Any non-null on a NON-oldest row is a thing to preserve.
select
    u.email,
    m.id,
    m.created_at,
    m.role,
    (m.note is not null)                    as has_note,
    (m.memory is not null)                  as has_memory,
    (m.default_llm_model_id is not null)    as has_default_model,
    (m.default_data_source_ids is not null) as has_default_agents,
    (m.successor_user_id is not null)       as has_successor
from memberships m
join users u on u.id = m.user_id
where m.deleted_at is null
  and m.user_id is not null
  and (m.user_id, m.organization_id) in (
      select user_id, organization_id
      from memberships
      where deleted_at is null and user_id is not null
      group by user_id, organization_id
      having count(*) > 1
  )
order by u.email, m.created_at;

\echo ''
\echo '=== 6. SOFT-DELETED MEMBERSHIPS STILL BEING LISTED ==='
-- Separate defect, same commit: the workspace switcher had no deleted_at
-- filter while the membership CHECK does. Anyone here was shown a workspace
-- they had been removed from, and every request into it was refused.
select u.email, o.name as organization, m.deleted_at
from memberships m
join users u on u.id = m.user_id
left join organizations o on o.id = m.organization_id
where m.deleted_at is not null
  and m.user_id is not null
order by m.deleted_at desc
limit 50;

\echo ''
\echo '=== 7. THE TWO USERS FROM THE PRODUCTION LOGS ==='
select u.email, count(*) filter (where m.deleted_at is null) as live_rows,
       count(*) filter (where m.deleted_at is not null) as deleted_rows
from users u
left join memberships m on m.user_id = u.id
where u.email in ('chitsnowwai@cityholdings.com.mm',
                  'kaungminhtet@cityholdings.com.mm')
group by u.email;
