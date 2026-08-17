"""create ownership_transfers — the ledger behind every handover

Revision ID: ownxfer01
Revises: usrdsdef01
Create Date: 2026-08-13 03:00:00.000000

Ownership on this product is one non-null column per object, so changing an
owner overwrites the previous one. This table records what the state was before
each change, which is what makes a transfer reversible, attributable and
explainable on screen.

Nothing reads or writes it at this revision — the service lands in the same
release, the routes and UI in the next. Creating it first keeps the migration
independently reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ownxfer01'
# The head measured against the live database on 2026-08-13, not read from a
# document. Re-check with `select version_num from alembic_version` before
# adding anything after this.
down_revision: Union[str, None] = 'usrdsdef01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ownership_transfers',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),

        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),

        sa.Column('resource_type', sa.String(32), nullable=False),
        sa.Column('resource_id', sa.String(36), nullable=False),

        # ★Nullable and no cascade, on purpose. A user row is deactivated but
        # never deleted on this product, so these stay resolvable — but the
        # ledger has to survive even if that ever changes. A history that
        # vanishes when an account is cleaned up is not a history.
        sa.Column('from_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('to_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        # NULL on the successor path — nobody clicked anything.
        sa.Column('actor_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),

        sa.Column('reason', sa.String(32), nullable=False),
        sa.Column('batch_id', sa.String(36), nullable=False),
        sa.Column('reverted_at', sa.DateTime(), nullable=True),
        # JSON-encoded text rather than a JSON column: this fork's existing
        # per-org config columns are plain `json` and needed casting for
        # jsonb_set, and nothing here is ever queried BY its contents — it is
        # read whole, by undo.
        sa.Column('previous_state', sa.Text(), nullable=True),
    )

    op.create_index('ix_ownership_transfers_organization_id', 'ownership_transfers', ['organization_id'])
    op.create_index('ix_ownership_transfers_from_user_id', 'ownership_transfers', ['from_user_id'])
    op.create_index('ix_ownership_transfers_to_user_id', 'ownership_transfers', ['to_user_id'])
    op.create_index('ix_ownership_transfers_batch_id', 'ownership_transfers', ['batch_id'])
    # The two queries this table actually serves: the history of one object,
    # and what one batch did.
    op.create_index(
        'ix_ownership_transfers_resource',
        'ownership_transfers',
        ['organization_id', 'resource_type', 'resource_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_ownership_transfers_resource', table_name='ownership_transfers')
    op.drop_index('ix_ownership_transfers_batch_id', table_name='ownership_transfers')
    op.drop_index('ix_ownership_transfers_to_user_id', table_name='ownership_transfers')
    op.drop_index('ix_ownership_transfers_from_user_id', table_name='ownership_transfers')
    op.drop_index('ix_ownership_transfers_organization_id', table_name='ownership_transfers')
    op.drop_table('ownership_transfers')
