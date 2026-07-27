"""local runtime: "run analysis on my computer" starts off

Pairing a device and agreeing to run code on it are two different decisions.
The column defaulted to true, so a freshly paired laptop began executing agent
Python immediately — before the user had seen the toggle, let alone chosen it.
New devices now start off and the user opts in.

Existing rows are deliberately NOT flipped: someone who already turned this on
(or was defaulted into it and kept using it) should not have it silently taken
away by an upgrade. Only the default for new pairings changes.

Revision ID: ca08lrtoggleoff
Revises: ca07lrfolders01
"""
from alembic import op
import sqlalchemy as sa

revision = "ca08lrtoggleoff"
down_revision = "ca07lrfolders01"
branch_labels = None
depends_on = None


def _set_default(default: str) -> None:
    """Change the column's server default.

    SQLite has no ALTER COLUMN at all, so a plain ``op.alter_column`` here
    aborts the whole migration chain with ``near "ALTER": syntax error`` —
    which takes the entire sqlite-backed test suite down at fixture setup,
    not just this revision. The only production backend is Postgres, and on
    sqlite the value this sets is moot (the model's ``default=False`` still
    applies on insert), so skip it rather than rebuild the table in batch mode
    and lose the foreign keys sqlite's copy-and-rename cannot preserve.
    """
    if op.get_bind().dialect.name == "sqlite":
        return
    op.alter_column(
        "local_runtimes",
        "run_local_enabled",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text(default),
    )


def upgrade() -> None:
    _set_default("false")


def downgrade() -> None:
    _set_default("true")
