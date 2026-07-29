"""connection_indexings: per-user scope for background catalog syncs

Per-user catalogs (OneDrive, personal Drive) are built from the signing-in
user's own token. That build now runs as a tracked background job instead of
inline in the OAuth callback, so it needs a row scoped to the user:
`user_id IS NULL` keeps meaning "the org-shared run".

Revision ID: idxuser01
Revises: ca09memberfileds
Create Date: 2026-07-26

★ Fork re-point. Upstream ships this revising `entraprof01`, and ships
`mainbuild01` revising `entraprof01` as well — two children of one parent with
nothing chaining them together, i.e. two heads, so upstream's own
`alembic upgrade head` cannot run. Both are re-pointed onto the end of our
fork's chain (this one onto `ca09memberfileds`, `mainbuild01` onto this one) so
the history stays a single line. Column contents and index are unchanged from
upstream.
"""
import sqlalchemy as sa

from alembic import op

revision = "idxuser01"
down_revision = "ca09memberfileds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain nullable column, no FK constraint: SQLite cannot ALTER a constraint
    # in, and the column is only ever filtered on (connection_id + user_id),
    # never joined. Matches how the other user-reference columns were added.
    op.add_column(
        "connection_indexings",
        sa.Column("user_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_connection_indexings_user_id", "connection_indexings", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_connection_indexings_user_id", "connection_indexings")
    op.drop_column("connection_indexings", "user_id")
