"""add learn_progress table (shared cross-worker Learn-agent progress)

Revision ID: ca04learnprog01
Revises: ca03putbl01act
Create Date: 2026-07-24

The app runs multiple uvicorn workers; an in-memory progress dict is invisible to
the worker serving the /learn-status poll. This table is the shared source of
truth for "Learn agent" (llm_sync force_llm) progress. One row per
(data_source_id, user_id). Only written when HYBRID_LEARN_PROGRESS is on.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca04learnprog01'
down_revision = 'ca03putbl01act'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'learn_progress' in insp.get_table_names():
        return
    op.create_table(
        'learn_progress',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('data_source_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False, server_default=''),
        sa.Column('status', sa.String(), nullable=False, server_default='idle'),
        sa.Column('stage', sa.String(), nullable=True),
        sa.Column('step', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('tables', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('columns', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('data_source_id', 'user_id', name='uq_learn_progress'),
    )
    op.create_index('ix_learn_progress_data_source_id', 'learn_progress', ['data_source_id'])


def downgrade():
    op.drop_index('ix_learn_progress_data_source_id', table_name='learn_progress')
    op.drop_table('learn_progress')
