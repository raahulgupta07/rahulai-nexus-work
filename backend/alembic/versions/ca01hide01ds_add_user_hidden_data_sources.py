"""add user_hidden_data_sources (personal hide-from-picker)

Revision ID: ca01hide01ds
Revises: 3432b52b6d20
Create Date: 2026-07-23

Per-user 'hide from my chat picker' preference. Personal scope only — does not
disable the agent for others or touch the AI context.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca01hide01ds'
down_revision = '3432b52b6d20'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'user_hidden_data_sources' in insp.get_table_names():
        return
    op.create_table(
        'user_hidden_data_sources',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('data_source_id', sa.String(length=36), nullable=False),
        sa.Column('organization_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'data_source_id', name='uq_user_hidden_data_source'),
    )
    op.create_index('ix_user_hidden_ds_user', 'user_hidden_data_sources', ['user_id'])
    op.create_index('ix_user_hidden_ds_ds', 'user_hidden_data_sources', ['data_source_id'])
    op.create_index('ix_user_hidden_ds_org', 'user_hidden_data_sources', ['organization_id'])


def downgrade():
    op.drop_table('user_hidden_data_sources')
