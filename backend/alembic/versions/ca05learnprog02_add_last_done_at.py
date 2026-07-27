"""add learn_progress.last_done_at (persistent last-learned timestamp)

Revision ID: ca05learnprog02
Revises: ca04learnprog01
Create Date: 2026-07-24

Persists when the last successful Learn finished (survives a new run's start()),
so the UI can show "Last learned: <date time>" even when idle.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca05learnprog02'
down_revision = 'ca04learnprog01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        cols = [c['name'] for c in insp.get_columns('learn_progress')]
    except Exception:
        return
    if 'last_done_at' in cols:
        return
    op.add_column('learn_progress', sa.Column('last_done_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('learn_progress', 'last_done_at')
