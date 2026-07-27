"""add local_runtimes + local_runtime_jobs (Cowork-style local execution)

Revision ID: ca06localrt01
Revises: ca05learnprog02
Create Date: 2026-07-25

DB-backed pairing + job queue for the local helper. HTTP polling, no
websockets: the app runs multiple uvicorn workers, so all coordination
must live in the DB (learn-progress landmine).
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca06localrt01'
down_revision = 'ca05learnprog02'
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade():
    if not _has_table('local_runtimes'):
        op.create_table(
            'local_runtimes',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('organization_id', sa.String(36), nullable=False, index=True),
            sa.Column('user_id', sa.String(36), nullable=False, index=True),
            sa.Column('name', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('pair_code_hash', sa.String(), nullable=True),
            sa.Column('pair_expires_at', sa.DateTime(), nullable=True),
            sa.Column('token_hash', sa.String(), nullable=True),
            sa.Column('last_seen', sa.DateTime(), nullable=True),
            sa.Column('helper_version', sa.String(), nullable=True),
            sa.Column('run_local_enabled', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('deleted_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_local_runtimes_user', 'local_runtimes', ['user_id'])
        op.create_index('ix_local_runtimes_token', 'local_runtimes', ['token_hash'])

    if not _has_table('local_runtime_jobs'):
        op.create_table(
            'local_runtime_jobs',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('runtime_id', sa.String(36), nullable=False, index=True),
            sa.Column('organization_id', sa.String(36), nullable=False),
            sa.Column('user_id', sa.String(36), nullable=False),
            sa.Column('status', sa.String(), nullable=False, server_default='pending'),
            sa.Column('code', sa.Text(), nullable=True),
            sa.Column('payload_json', sa.Text(), nullable=True),
            sa.Column('result_b64', sa.Text(), nullable=True),
            sa.Column('result_format', sa.String(), nullable=True),
            sa.Column('stdout', sa.Text(), nullable=True),
            sa.Column('queries_json', sa.Text(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('started_at', sa.DateTime(), nullable=True),
            sa.Column('finished_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('deleted_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_lrt_jobs_runtime_status', 'local_runtime_jobs', ['runtime_id', 'status'])


def downgrade():
    op.drop_table('local_runtime_jobs')
    op.drop_table('local_runtimes')
