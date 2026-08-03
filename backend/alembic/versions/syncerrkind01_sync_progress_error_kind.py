"""Record whose fault a failed sync was, not just what it said.

A failed sync used to carry only `error` — a raw driver message. That is enough
to display and not enough to decide with: the UI has to word an outage of ours
differently from a source refusing us, and the retry policy has to retry one and
not the other. Classification happens in `app/services/indexing_failures.py`;
this column is where the answer lands for the per-user Fabric/Power BI tracker.

Additive and nullable. Rows written before this migration read as NULL, which
the UI treats exactly as it always did — the raw error, no claim about cause.

Revision ID: syncerrkind01
Revises: stepfiles01
"""
from alembic import op
import sqlalchemy as sa


revision = "syncerrkind01"
down_revision = "stepfiles01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connection_sync_progress",
        sa.Column("error_kind", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connection_sync_progress", "error_kind")
