"""partial index on the live-completion predicate

`app/streaming/report_activity_hub.py` runs a watcher tick every 2 seconds, per
organization, per uvicorn worker, for as long as any client holds the report
list open. Its first query is:

    SELECT completions.report_id
      FROM completions JOIN reports ON reports.id = completions.report_id
     WHERE reports.organization_id = :org
       AND completions.status IN ('in_progress', 'queued')
       AND completions.deleted_at IS NULL

`completions.status` has no index at all — the existing ones are
ix_completions_report_id and ix_completions_report_created (report_id-leading,
so neither can narrow on status). On an install where completions is the
largest table (it grows with every message ever sent) that is a full scan of
it every 2 seconds, forever.

The index is keyed on report_id under a partial predicate rather than keyed on
status, because the predicate already pins status to the two live values —
putting status in the key would only store a constant. Keying on report_id
makes the whole query answerable from the index (report_id is the only column
selected), and the partial WHERE keeps the index to just the handful of rows
that are live *right now*, not one entry per completion ever written. The same
predicate backs ReportService.derive_activity_sets' live-completion query,
which additionally filters report_id IN (...) — served by the same key.

SQLite (the test suite's database) does support partial indexes, but the
`postgresql_where` kwarg is dialect-scoped and Postgres is the deployment
target, so the partial form is emitted only there; other dialects get an
equivalent plain (status, report_id) index, which is correct if larger.

Revision ID: complive01
Revises: instrdir01
Create Date: 2026-08-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "complive01"
down_revision: Union[str, None] = "instrdir01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "ix_completions_live_status"
TABLE_NAME = "completions"


def _existing_indexes(inspector, table_name):
    try:
        return {ix["name"] for ix in inspector.get_indexes(table_name)}
    except Exception:
        return set()


def upgrade() -> None:
    # Check existence up front so create_index never raises — a failed
    # statement would abort the whole migration transaction in Postgres.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if INDEX_NAME in _existing_indexes(inspector, TABLE_NAME):
        return

    if bind.dialect.name == "postgresql":
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            ["report_id"],
            postgresql_where=sa.text(
                "deleted_at IS NULL AND status IN ('in_progress', 'queued')"
            ),
        )
    else:
        op.create_index(INDEX_NAME, TABLE_NAME, ["status", "report_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if INDEX_NAME in _existing_indexes(inspector, TABLE_NAME):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
