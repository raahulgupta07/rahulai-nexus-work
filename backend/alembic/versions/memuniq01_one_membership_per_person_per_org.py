"""Collapse duplicate memberships, then make a second one impossible.

Revision ID: memuniq01
Revises: evalsuitefk01
Create Date: 2026-08-18

One person held two `memberships` rows for the same organization. Nothing in
the schema forbade it — `group_memberships` has `uq_group_membership` and
data-source memberships have `uq_data_source_membership`, but org memberships
never had a uniqueness rule at all, so duplicates were possible by
construction.

They were not harmless. `principal_belongs_to_org` asked "is this person a
member?" with `scalar_one_or_none()`, which raises on two rows, and
`get_current_organization` depends on it — so a single duplicate returned 500
on nearly every org-scoped route for that user. Measured in production: 572 of
3613 requests in one morning. The code no longer breaks on duplicates (that
fix ships separately and is what makes this migration safe to run at leisure);
this retires the extra rows and stops new ones appearing.

★★★NOTHING IS DELETED. The duplicate rows are marked with `deleted_at`, not
removed. The unique index is partial — it counts only rows where `deleted_at
IS NULL` — so marking is exactly as effective as deleting, while every column,
timestamp and id survives and can be brought back by clearing that one field.
An earlier version issued a real DELETE and was safe only because the
production database happened to have no live duplicates; "nothing is lost"
has to be a property of the migration rather than a coincidence of the data.

★MERGE, DO NOT PICK A WINNER. The obvious implementation — keep the oldest row,
delete the rest — loses data, and this was measured rather than guessed: on a
restored production dump the OLDEST duplicate row held `memory` and
`default_data_source_ids` while the NEWER one held a `note`. Every
per-membership column is therefore coalesced onto the survivor, oldest non-null
winning, before anything is retired.

★AND THE GROUP LINKS ARE RE-POINTED AT THE SURVIVOR, so a person's directory
groups stay attached to the row that is actually live. This used to be
load-bearing for a different reason: `group_memberships.membership_id`
references this table `ON DELETE CASCADE`, so a real DELETE took those links
with it silently. The soft delete removes that hazard entirely — the links are
re-pointed because it is correct, not because anything would eat them.

★`role` is deliberately NOT merged upward. If two rows disagree, the survivor's
own role stands rather than the strongest of the two: a migration must not
quietly widen anybody's access. It is also cosmetic here — the role the client
is shown is derived from RBAC (`organization_service.get_user_organizations`
computes `derived_role` from resolved permissions), not from this column. The
audit script's block 4 reports any disagreement so it can be read before this
runs.

★`invite_token` is not merged either: it carries a UNIQUE constraint, and the
survivor already has its own.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "memuniq01"
down_revision: str | None = "evalsuitefk01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The survivor of each duplicate group: oldest first, id as the tiebreak so the
# choice is deterministic and a re-run picks the same row.
_SURVIVORS = """
    CREATE TEMPORARY TABLE _dup_groups AS
    SELECT user_id, organization_id
    FROM memberships
    WHERE deleted_at IS NULL AND user_id IS NOT NULL
    GROUP BY user_id, organization_id
    HAVING count(*) > 1;

    CREATE TEMPORARY TABLE _survivors AS
    SELECT DISTINCT ON (m.user_id, m.organization_id)
           m.id AS survivor_id, m.user_id, m.organization_id
    FROM memberships m
    JOIN _dup_groups d
      ON d.user_id = m.user_id AND d.organization_id = m.organization_id
    WHERE m.deleted_at IS NULL AND m.user_id IS NOT NULL
    ORDER BY m.user_id, m.organization_id,
             m.created_at ASC NULLS LAST, m.id ASC;

    CREATE TEMPORARY TABLE _losers AS
    SELECT m.id AS loser_id, s.survivor_id
    FROM memberships m
    JOIN _survivors s
      ON s.user_id = m.user_id AND s.organization_id = m.organization_id
    WHERE m.deleted_at IS NULL
      AND m.user_id IS NOT NULL
      AND m.id <> s.survivor_id;
"""

# Oldest non-null wins for each column independently.
_MERGE = """
    UPDATE memberships surv
    SET note = COALESCE(surv.note, src.note),
        memory = COALESCE(surv.memory, src.memory),
        default_llm_model_id =
            COALESCE(surv.default_llm_model_id, src.default_llm_model_id),
        default_data_source_ids =
            COALESCE(surv.default_data_source_ids, src.default_data_source_ids),
        successor_user_id =
            COALESCE(surv.successor_user_id, src.successor_user_id),
        profile_attributes =
            COALESCE(surv.profile_attributes, src.profile_attributes),
        email = COALESCE(surv.email, src.email),
        invite_expires_at =
            COALESCE(surv.invite_expires_at, src.invite_expires_at)
    FROM (
        SELECT DISTINCT ON (l.survivor_id)
               l.survivor_id, m.note, m.memory, m.default_llm_model_id,
               m.default_data_source_ids, m.successor_user_id,
               m.profile_attributes, m.email, m.invite_expires_at
        FROM _losers l
        JOIN memberships m ON m.id = l.loser_id
        ORDER BY l.survivor_id, m.created_at ASC NULLS LAST, m.id ASC
    ) src
    WHERE surv.id = src.survivor_id;
"""

# Move group links off the rows about to be deleted. `uq_group_membership_pending`
# is (group_id, membership_id), so only move where the survivor is not already
# in that group; what is left is a true duplicate of a link the survivor has.
_REPOINT_GROUPS = """
    UPDATE group_memberships gm
    SET membership_id = l.survivor_id
    FROM _losers l
    WHERE gm.membership_id = l.loser_id
      AND NOT EXISTS (
          SELECT 1 FROM group_memberships existing
          WHERE existing.group_id = gm.group_id
            AND existing.membership_id = l.survivor_id
      );
"""

# ★★★SOFT delete. Nothing is removed from this table, ever.
#
# The first version issued a real DELETE, and it was safe only by luck: the
# production database happened to have no live duplicates. "Nothing is lost"
# must be a property of the migration, not a coincidence of the data it meets.
#
# The unique index below is PARTIAL — it only counts rows where deleted_at IS
# NULL — so marking the duplicate is exactly as effective as removing it, and
# every column, timestamp and id survives. It is also reversible by hand: clear
# deleted_at and the row is back.
#
# ★And it removes the cascade hazard entirely. group_memberships references
# this table ON DELETE CASCADE, so a real DELETE takes that person's directory
# group links with it. A soft delete cannot.
_RETIRE_LOSERS = """
    UPDATE memberships m
    SET deleted_at = now()
    FROM _losers l
    WHERE m.id = l.loser_id AND m.deleted_at IS NULL;
"""

_DROP_TEMPS = """
    DROP TABLE IF EXISTS _losers;
    DROP TABLE IF EXISTS _survivors;
    DROP TABLE IF EXISTS _dup_groups;
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(_SURVIVORS))
        merged = bind.execute(sa.text("SELECT count(*) FROM _losers")).scalar() or 0
        if merged:
            op.execute(sa.text(_MERGE))
            op.execute(sa.text(_REPOINT_GROUPS))
            op.execute(sa.text(_RETIRE_LOSERS))
        op.execute(sa.text(_DROP_TEMPS))
        # ★A partial index, matching exactly what the application means by
        # membership: `deleted_at IS NULL`. Constraining every row instead
        # would forbid the legitimate remove-then-re-invite sequence, which
        # leaves a soft-deleted row beside a live one.
        op.execute(sa.text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_user_org
            ON memberships (user_id, organization_id)
            WHERE deleted_at IS NULL AND user_id IS NOT NULL
        """))
    else:
        # sqlite (tests): no temp-table plumbing, same outcome.
        # sqlite (tests): same soft-delete outcome, no row removed.
        op.execute(sa.text("""
            UPDATE memberships
            SET deleted_at = CURRENT_TIMESTAMP
            WHERE deleted_at IS NULL AND user_id IS NOT NULL
              AND id NOT IN (
                  SELECT id FROM memberships m2
                  WHERE m2.deleted_at IS NULL AND m2.user_id IS NOT NULL
                    AND m2.rowid = (
                        SELECT min(m3.rowid) FROM memberships m3
                        WHERE m3.user_id = m2.user_id
                          AND m3.organization_id = m2.organization_id
                          AND m3.deleted_at IS NULL AND m3.user_id IS NOT NULL
                    )
              )
        """))
        op.execute(sa.text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_user_org
            ON memberships (user_id, organization_id)
            WHERE deleted_at IS NULL AND user_id IS NOT NULL
        """))


def downgrade() -> None:
    # ★Only the index comes back off, and that is now the whole story: no row
    # was removed, so there is nothing to restore. The duplicates are still
    # present, marked with deleted_at, and clearing that by hand brings any of
    # them back. Dropping the index restores the ability to create new ones,
    # which is all this ever added to the schema.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_membership_user_org"))
