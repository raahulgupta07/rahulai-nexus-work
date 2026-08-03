"""Per-user workspace selection for federated sources.

See `app/models/user_data_source_scope.py` for why NULL and [] mean different
things in `selected_endpoints`.

Revision ID: usdsscope01
Revises: syncerrkind01
"""
from alembic import op
import sqlalchemy as sa


revision = "usdsscope01"
down_revision = "syncerrkind01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_data_source_scopes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("data_source_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        # Nullable on purpose — NULL is "never chosen, sync everything", which
        # is what every existing install means today.
        sa.Column("selected_endpoints", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("data_source_id", "user_id", name="uq_user_ds_scope"),
    )
    op.create_index(
        "ix_user_data_source_scopes_data_source_id",
        "user_data_source_scopes", ["data_source_id"],
    )
    op.create_index(
        "ix_user_data_source_scopes_user_id", "user_data_source_scopes", ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_data_source_scopes_user_id", table_name="user_data_source_scopes")
    op.drop_index(
        "ix_user_data_source_scopes_data_source_id", table_name="user_data_source_scopes",
    )
    op.drop_table("user_data_source_scopes")
