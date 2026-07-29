"""grant members the file-agent permission the registry always said they had

Revision ID: ca09memberfileds
Revises: ca08lrtoggleoff
Create Date: 2026-07-27

`permissions_registry.DEFAULT_MEMBER_PERMISSIONS` lists eight permissions for a
member, the eighth being `create_file_data_source` — added when members gained
the ability to build private agents from their own uploaded files.

The system `member` role is not seeded from that list. It is seeded by the RBAC
migration (`e6f7g8h9i0j1_rbac_mvp`), which hardcodes its own copy of seven
permissions under a comment claiming it "mirrors
permissions_registry.DEFAULT_MEMBER_PERMISSIONS". It stopped mirroring it the
day the eighth entry was added, and nothing detected the drift.

Consequences, on every install including brand-new ones:

  * `POST /data_sources` returns 403 for members
    (routes/data_source.py: `if not (is_full or "create_file_data_source" in perms)`)
  * so a member can never create a data agent, and with no agent there is
    nothing to build a dashboard from — the product looks read-only to them

★ There is a fallback in `permission_resolver` that uses the correct registry
list, but it only fires for a user with NO role assignments at all. The RBAC
migration backfills assignments, so it never fires and the seven-permission
role always wins. It looks like a safety net and is not one.

This migration adds the missing permission to any system `member` role that
lacks it. It is additive and idempotent: a role that already has it is left
untouched, and custom (non-system) roles are never modified — an administrator
who deliberately built a restricted role keeps exactly what they chose.

Rows are read and written in Python rather than with a JSON operator because
`roles.permissions` is a `json` column (not `jsonb`), and this has to work on
both PostgreSQL and SQLite.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca09memberfileds"
down_revision: Union[str, None] = "ca08lrtoggleoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSION = "create_file_data_source"


def _rows(bind):
    return bind.execute(
        sa.text(
            "SELECT id, permissions FROM roles "
            "WHERE lower(name) = 'member' AND is_system = true AND deleted_at IS NULL"
            if bind.dialect.name != "sqlite"
            else
            "SELECT id, permissions FROM roles "
            "WHERE lower(name) = 'member' AND is_system = 1 AND deleted_at IS NULL"
        )
    ).fetchall()


def upgrade() -> None:
    bind = op.get_bind()
    for role_id, permissions in _rows(bind):
        try:
            perms = json.loads(permissions) if isinstance(permissions, str) else (permissions or [])
        except (TypeError, ValueError):
            # Unreadable permission blob: leave it alone rather than replace it
            # with a guess. A role that cannot be parsed is not one to rewrite.
            continue
        if not isinstance(perms, list) or PERMISSION in perms:
            continue
        bind.execute(
            sa.text("UPDATE roles SET permissions = :p WHERE id = :id"),
            {"p": json.dumps(perms + [PERMISSION]), "id": role_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for role_id, permissions in _rows(bind):
        try:
            perms = json.loads(permissions) if isinstance(permissions, str) else (permissions or [])
        except (TypeError, ValueError):
            continue
        if not isinstance(perms, list) or PERMISSION not in perms:
            continue
        bind.execute(
            sa.text("UPDATE roles SET permissions = :p WHERE id = :id"),
            {"p": json.dumps([p for p in perms if p != PERMISSION]), "id": role_id},
        )
