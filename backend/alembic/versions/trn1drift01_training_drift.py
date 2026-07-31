"""Record what an agent was trained on, so drift can be noticed.

An agent's overview describes its tables — which ones exist, what columns they
carry. Training writes that description; nothing has ever recorded WHAT it
described. So when a table is removed or a column appears, the schema moves and
the overview does not, and there is no way to tell: the text still names six
month tables long after one of them is gone.

Three additive columns, all nullable, no backfill:

* ``trained_schema_signature`` — a fingerprint of the tables and columns the
  last training actually read.
* ``trained_at`` — when that was.
* ``training_settings`` — per-agent policy (notice only, or re-learn on its own).

Existing agents get NULL for all three, which reads as "never trained by a
version that recorded this" — deliberately not as "out of date". Marking every
agent in an installation stale on upgrade would put a warning on agents that are
perfectly current, and a warning that is usually wrong is one people stop
reading.

Revision ID: trn1drift01
Revises: rfv1o2n3v4w5
"""
import sqlalchemy as sa
from alembic import op

revision = "trn1drift01"
down_revision = "rfv1o2n3v4w5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_sources", sa.Column("trained_schema_signature", sa.Text(), nullable=True))
    op.add_column("data_sources", sa.Column("trained_at", sa.DateTime(), nullable=True))
    op.add_column("data_sources", sa.Column("training_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_sources", "training_settings")
    op.drop_column("data_sources", "trained_at")
    op.drop_column("data_sources", "trained_schema_signature")
