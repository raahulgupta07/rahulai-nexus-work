"""connection_indexings: trigger column + per-user scope index

The per-user Fabric/Power BI crawl now records a durable run row here (see
app/services/sync_runs.py). Two things it needs that the table did not have:

* ``trigger`` — what started the run, so the activity list can distinguish a
  sync the member asked for from one we retried for them.
* an index on ``(connection_id, user_id, created_at)`` — every read in that
  feature is "the newest runs for this member on this connection", and the
  existing single-column indexes make that a filter-then-sort over the whole
  scope.

Revision ID: syncruns01
Revises: usdsscope01
"""
from alembic import op
import sqlalchemy as sa


revision = "syncruns01"
down_revision = "usdsscope01"
branch_labels = None
depends_on = None


_INDEX_NAME = "ix_connection_indexings_scope_recent"


def upgrade() -> None:
    with op.batch_alter_table("connection_indexings") as batch:
        batch.add_column(sa.Column("trigger", sa.String(length=32), nullable=True))

    op.create_index(
        _INDEX_NAME,
        "connection_indexings",
        ["connection_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="connection_indexings")
    with op.batch_alter_table("connection_indexings") as batch:
        batch.drop_column("trigger")
