"""Deleting a duplicate membership must not take anything else with it.

Two traps, both measured on a restored production dump rather than reasoned
about, and both invisible if the migration is written the obvious way.

1. `group_memberships.membership_id` references `memberships.id`
   **ON DELETE CASCADE**. Deleting a duplicate row therefore deletes that
   person's group memberships — including directory-synced ones — with no
   error and nothing in the log. Losing a group link to fix a duplicate row is
   a strictly worse outcome than the duplicate.

2. The per-membership columns are not all on the same row. On the dump, the
   OLDEST duplicate held `memory` and `default_data_source_ids` while the
   NEWER one held a `note`. "Keep the oldest, delete the rest" silently
   destroys the note.

★These are source assertions over the migration. A behavioural test would need
Postgres (the migration uses temp tables and a partial index, neither of which
sqlite supports the same way), and the fork suite deliberately has no database.
The behavioural proof was done by hand against a clone of the production dump:
the note merged onto the survivor, and the group link was re-pointed rather
than cascaded away.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
MIG = REPO / "backend" / "alembic" / "versions" / "memuniq01_one_membership_per_person_per_org.py"


def _src() -> str:
    return MIG.read_text(encoding="utf-8")


def test_the_migration_exists():
    assert MIG.exists(), "the dedupe migration is gone"


def test_group_links_are_repointed_before_any_delete():
    """★The cascade trap. Order is the whole safety property."""
    src = _src()
    assert "group_memberships" in src, (
        "the migration never mentions group_memberships, which references "
        "memberships ON DELETE CASCADE — deleting a duplicate silently drops "
        "that person's group links"
    )
    repoint = src.index("_REPOINT_GROUPS = ")
    delete = src.index("_DELETE_LOSERS = ")
    assert repoint < delete, "group links are re-pointed after the delete, which is too late"
    # and in the executed order, not merely defined order
    body = src[src.index("def upgrade("):]
    assert body.index("_REPOINT_GROUPS") < body.index("_DELETE_LOSERS"), (
        "upgrade() deletes the losing rows before moving their group links"
    )


def test_the_merge_coalesces_rather_than_picking_a_row():
    """★Every column that only lives on the losing row must be carried over."""
    src = _src()
    for col in (
        "note", "memory", "default_llm_model_id", "default_data_source_ids",
        "successor_user_id", "profile_attributes",
    ):
        assert f"COALESCE(surv.{col}" in src, f"{col} is dropped when a duplicate is removed"


def test_role_is_not_widened_by_the_merge():
    """★A migration must not quietly give anybody more access than they had."""
    src = _src()
    assert "COALESCE(surv.role" not in src, (
        "role is merged from the losing row, which can widen access"
    )


def test_invite_token_is_not_merged():
    """It carries a UNIQUE constraint; copying it across would collide."""
    assert "COALESCE(surv.invite_token" not in _src()


def test_the_index_is_partial_on_live_rows_only():
    """★Constraining every row would forbid remove-then-re-invite.

    That sequence legitimately leaves a soft-deleted row beside a live one.
    """
    src = _src()
    assert "uq_membership_user_org" in src
    assert re.search(r"WHERE deleted_at IS NULL", src), (
        "the unique index is not partial, so a re-invited member cannot rejoin"
    )


def test_the_downgrade_does_not_pretend_to_restore_rows():
    """★An honest downgrade. The merged rows are gone and cannot come back."""
    src = _src()
    down = src[src.index("def downgrade("):]
    assert "DROP INDEX" in down
    assert "INSERT" not in down.upper(), (
        "downgrade claims to restore deleted membership rows"
    )
