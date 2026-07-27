"""add instructions.is_private (per-user private instructions)

Revision ID: ca02priv01ins
Revises: ca01hide01ds
Create Date: 2026-07-24

Per-user private instructions (Phase 4). False = shared org rule (as today);
True = private to the row's user_id. Existing rows default to shared, so the
change is byte-identical until the per_user_instructions flag is enabled.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca02priv01ins'
down_revision = 'ca01hide01ds'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c['name'] for c in insp.get_columns('instructions')]
    if 'is_private' in cols:
        return
    op.add_column(
        'instructions',
        sa.Column('is_private', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_instructions_is_private', 'instructions', ['is_private'])


def downgrade():
    op.drop_index('ix_instructions_is_private', table_name='instructions')
    op.drop_column('instructions', 'is_private')
