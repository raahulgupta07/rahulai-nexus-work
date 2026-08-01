"""users.must_change_password — forced change after an admin sets a password

Additive and idempotent. Existing accounts get ``false``: an upgrade must never
lock anyone out of a product they were already using.

Revision ID: pwset001
Revises: svr0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'pwset001'
down_revision: Union[str, None] = 'svr0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "must_change_password"):
        op.add_column(
            "users",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    if _has_column("users", "must_change_password"):
        op.drop_column("users", "must_change_password")
