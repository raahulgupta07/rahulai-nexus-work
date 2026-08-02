"""Record the ordered file list a step's code was generated against.

Generated code reads uploads by position (`excel_files[2].path`). The position
is only meaningful against the list the code generator saw. Re-running fed it
`report.files` instead — the report's whole attachment history, unordered — so
adding any file silently re-pointed every index.

This column stores the identities, so a rerun can rebuild the same list.

Revision ID: stepfiles01
Revises: complive01
"""
from alembic import op
import sqlalchemy as sa


revision = 'stepfiles01'
down_revision = 'complive01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no server default: NULL is meaningful here. It marks a step
    # written before the binding existed, whose file order cannot be recovered
    # and must therefore never be guessed at.
    op.add_column('steps', sa.Column('source_file_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('steps', 'source_file_ids')
