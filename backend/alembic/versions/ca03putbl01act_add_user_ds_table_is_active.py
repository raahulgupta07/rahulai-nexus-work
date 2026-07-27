"""add user_data_source_tables.is_active (per-user active-table selection)

Revision ID: ca03putbl01act
Revises: ca02priv01ins
Create Date: 2026-07-24

Per-user table selection (Fabric + Power BI). Each row in user_data_source_tables
is one (data_source, user, table) the user's own Microsoft token can reach
(is_accessible). is_active adds *their* choice of which of those the agent uses.

Backfill: is_active := is_accessible for existing rows, so an inaccessible table
is never auto-active. New rows default TRUE. Byte-identical until the
HYBRID_PER_USER_TABLE_SELECT flag is enabled and the read path consults is_active.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca03putbl01act'
down_revision = 'ca02priv01ins'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = [c['name'] for c in insp.get_columns('user_data_source_tables')]
    except Exception:
        # table not present (fresh/partial DB) — nothing to alter
        return
    if 'is_active' in cols:
        return
    op.add_column(
        'user_data_source_tables',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    # Backfill: mirror accessibility so no inaccessible table starts active.
    op.execute(
        "UPDATE user_data_source_tables SET is_active = is_accessible"
    )
    op.create_index(
        'ix_user_ds_tables_active', 'user_data_source_tables', ['data_source_id', 'user_id', 'is_active']
    )


def downgrade():
    op.drop_index('ix_user_ds_tables_active', table_name='user_data_source_tables')
    op.drop_column('user_data_source_tables', 'is_active')
