"""add auth_throttle (cross-worker rate limiting for sign-in and registration)

Revision ID: ca11auththrottle
Revises: ca10connsync01
Create Date: 2026-07-28

Sign-in and registration had no rate limit of any kind: an attacker could try
passwords against a known email address as fast as the network allowed, and
could create accounts in a loop wherever self-registration is open.

★A table rather than a module-level dict, because the app runs uvicorn with up
to 4 workers. A counter in one worker's memory is invisible to the other three,
so a limit of "10 per five minutes" would actually admit forty — a limit that
states one number and enforces another. The same mistake has already been made
twice in this codebase (``learn_progress`` and the LLM circuit breaker); for a
login limiter it is the entire point of the feature.

Additive: one new table, nothing existing touched, nothing backfilled. Rows are
disposable — dropping them all only resets the counters.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca11auththrottle'
down_revision = 'ca10connsync01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'auth_throttle' in insp.get_table_names():
        return
    op.create_table(
        'auth_throttle',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('bucket', sa.String(length=255), nullable=False),
        sa.Column('window_start', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    # ★UNIQUE, and load-bearing: the counter is an upsert keyed on this column.
    # Without the constraint, two workers racing the same bucket each insert a
    # row and each counts to the limit separately — the exact per-worker split
    # this table exists to remove.
    op.create_index('ix_auth_throttle_bucket', 'auth_throttle', ['bucket'], unique=True)
    op.create_index('ix_auth_throttle_window_start', 'auth_throttle', ['window_start'])


def downgrade():
    op.drop_index('ix_auth_throttle_window_start', table_name='auth_throttle')
    op.drop_index('ix_auth_throttle_bucket', table_name='auth_throttle')
    op.drop_table('auth_throttle')
