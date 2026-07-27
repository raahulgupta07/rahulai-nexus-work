"""add OAuth MCP server tables

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-02-24 10:00:00.000000

Adds tables for OAuth 2.1 Authorization Server functionality,
enabling Claude Web and other external MCP clients to authenticate
via OAuth Authorization Code + PKCE flow.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'u6v7w8x9y0z1'
down_revision: Union[str, None] = 't5u6v7w8x9y0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'oauth_mcp_clients',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False, index=True),
        sa.Column('client_id', sa.String(64), nullable=False, unique=True, index=True),
        sa.Column('client_secret_hash', sa.String(64), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('redirect_uris', sa.Text(), nullable=False),
        sa.Column('scopes', sa.String(255), nullable=False, server_default='mcp'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'oauth_mcp_authorization_codes',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('code', sa.String(128), nullable=False, unique=True, index=True),
        sa.Column('client_id', sa.String(64), nullable=False, index=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('redirect_uri', sa.String(2048), nullable=False),
        sa.Column('scope', sa.String(255), nullable=False, server_default='mcp'),
        sa.Column('code_challenge', sa.String(128), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'oauth_mcp_access_tokens',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True, index=True),
        sa.Column('client_id', sa.String(64), nullable=False, index=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('scope', sa.String(255), nullable=False, server_default='mcp'),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('refresh_token_hash', sa.String(64), nullable=True, unique=True, index=True),
        sa.Column('refresh_expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('oauth_mcp_access_tokens')
    op.drop_table('oauth_mcp_authorization_codes')
    op.drop_table('oauth_mcp_clients')
