"""instance_settings for sso

Revision ID: 3432b52b6d20
Revises: entraprof01
Create Date: 2026-07-21 05:35:06.474619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3432b52b6d20'
down_revision: Union[str, None] = 'entraprof01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('instance_settings',
    sa.Column('config', sa.JSON(), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('instance_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_instance_settings_id'), ['id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('instance_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_instance_settings_id'))

    op.drop_table('instance_settings')
