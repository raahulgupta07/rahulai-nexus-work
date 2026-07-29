"""add connection_sync_progress (cross-worker per-user connector sync progress)

Revision ID: ca10connsync01
Revises: mainbuild01
Create Date: 2026-07-28

Replaces the in-memory ``fabric_sync_progress`` dict. The app runs uvicorn with
up to 4 workers, so a module-level dict written by the worker running the sync
is invisible to the worker serving the poll — three polls in four came back
``idle`` while a sync was in flight. This table is the shared source of truth,
and it now covers ``powerbi_user`` as well as ``fabric_user``.

Additive: one new table, no existing table touched, nothing backfilled. An
in-flight sync at upgrade time simply loses its progress row, which the UI
already handles as ``idle``.
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca10connsync01'
down_revision = 'mainbuild01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'connection_sync_progress' in insp.get_table_names():
        return
    op.create_table(
        'connection_sync_progress',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('data_source_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False, server_default=''),
        sa.Column('status', sa.String(), nullable=False, server_default='syncing'),
        sa.Column('phase', sa.String(), nullable=True),
        sa.Column('endpoints_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('endpoints_done', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('endpoints_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tables', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('last_done_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('data_source_id', 'user_id', name='uq_connection_sync_progress'),
    )
    op.create_index(
        'ix_connection_sync_progress_data_source_id',
        'connection_sync_progress', ['data_source_id'],
    )


def downgrade():
    op.drop_index(
        'ix_connection_sync_progress_data_source_id',
        table_name='connection_sync_progress',
    )
    op.drop_table('connection_sync_progress')
