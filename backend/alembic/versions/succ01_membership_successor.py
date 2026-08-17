"""memberships.successor_user_id — who inherits your work if you leave

Revision ID: succ01
Revises: ownxfer01
Create Date: 2026-08-13 04:00:00.000000

Nominated by the member themselves, in their own settings, at a calm moment —
not guessed by an administrator on somebody's last day. Read only when a person
is removed or deprovisioned without having handed their work over first.

Nullable, no cascade and no FK-level ON DELETE: if the nominated successor
themselves leaves, the resolver skips them and falls through to the
organization's default owner. A dangling reference is a fact to handle, not a
constraint violation.

Written at this revision; nothing reads it until the release that adds the
automatic path.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'succ01'
down_revision: Union[str, None] = 'ownxfer01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'memberships',
        sa.Column('successor_user_id', sa.String(36), nullable=True),
    )
    op.create_index(
        'ix_memberships_successor_user_id', 'memberships', ['successor_user_id']
    )


def downgrade() -> None:
    op.drop_index('ix_memberships_successor_user_id', table_name='memberships')
    op.drop_column('memberships', 'successor_user_id')
