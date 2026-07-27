"""add folders_schema + folders_scanned_at to local_runtimes (attach local folder in chat)

Revision ID: ca07lrfolders01
Revises: ca06localrt01
Create Date: 2026-07-25

The paired helper scans its whitelisted folders with DuckDB and posts back the
SCHEMA ONLY (table name, columns+types, row count) so the planner can write SQL
against files that never leave the user's laptop. Text-JSON column, additive:
existing rows read as NULL = "no folders scanned yet".
"""
from alembic import op
import sqlalchemy as sa


revision = 'ca07lrfolders01'
down_revision = 'ca06localrt01'
branch_labels = None
depends_on = None


def _columns(table: str) -> set:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    cols = _columns('local_runtimes')
    if not cols:
        return  # ca06 never ran on this DB; nothing to extend
    if 'folders_schema' not in cols:
        op.add_column('local_runtimes', sa.Column('folders_schema', sa.Text(), nullable=True))
    if 'folders_scanned_at' not in cols:
        op.add_column('local_runtimes', sa.Column('folders_scanned_at', sa.DateTime(), nullable=True))


def downgrade():
    cols = _columns('local_runtimes')
    if 'folders_scanned_at' in cols:
        op.drop_column('local_runtimes', 'folders_scanned_at')
    if 'folders_schema' in cols:
        op.drop_column('local_runtimes', 'folders_schema')
