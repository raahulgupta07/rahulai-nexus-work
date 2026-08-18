"""add cron_is_active to reports

Revision ID: rptpause01
Revises: oauthapp01
Create Date: 2026-08-17 00:00:00.000000

Lets a report refresh be PAUSED without losing the time it was configured for.
Until now the only off switch was writing ``cron_schedule = NULL``, which
destroys the schedule: resuming meant retyping it, and a paused refresh was
indistinguishable from one that had never been scheduled.

``server_default`` is true, so every existing row backfills to "not paused" —
which is what those rows mean today, since a non-null ``cron_schedule`` has
always implied a live job. No data migration is needed and the column is
meaningless where ``cron_schedule`` is NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'rptpause01'
down_revision: Union[str, None] = 'oauthapp01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reports',
        sa.Column('cron_is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    # ★ Downgrading LOSES the paused/running distinction, and the old code reads
    # a non-null cron_schedule as "running": every paused refresh silently
    # resumes on the next scheduler registration. Unpause deliberately before
    # rolling back if that matters.
    op.drop_column('reports', 'cron_is_active')
