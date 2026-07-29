"""The seeded member role must actually match the registry it claims to mirror.

`permissions_registry.DEFAULT_MEMBER_PERMISSIONS` is the stated source of truth
for what an ordinary member can do. But no member role is ever built from it:
the RBAC migration hardcodes its own copy, under a comment saying it "mirrors"
the registry. The two silently diverged when `create_file_data_source` was
added, and every install since seeded members one permission short — which made
`POST /data_sources` 403 for them, so members could not create a data agent and
therefore had nothing to build a dashboard from.

Nothing detected that. A comment asserted a guarantee and no code enforced it.
This file is the enforcement.

The invariant: every permission in the registry's member list is either seeded
by the original RBAC migration, or granted by a later migration. If someone adds
a ninth permission to the registry and ships no migration, this fails and says
so — rather than the drift surfacing months later as "members can't do X".

No database: the migration files are read as source and their permission lists
extracted, so this stays in the fast fork suite.
"""
import ast
from pathlib import Path

import pytest

from app.core.permissions_registry import DEFAULT_MEMBER_PERMISSIONS

VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"
RBAC_SEED = VERSIONS / "e6f7g8h9i0j1_rbac_mvp.py"
GRANT_MIGRATION = VERSIONS / "ca09memberfileds_member_file_agent_permission.py"


def _string_literals(path: Path) -> set:
    """Every string constant in a module, without importing it.

    Importing an alembic revision pulls in `op`, model metadata and a live
    engine config; parsing the source needs none of that and cannot have side
    effects.
    """
    tree = ast.parse(path.read_text())
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_rbac_seed_migration_exists():
    assert RBAC_SEED.exists(), f"expected the RBAC seed migration at {RBAC_SEED}"


def test_registry_still_grants_members_the_file_agent_permission():
    """The permission that gates member-created upload agents.

    If this disappears from the registry, members lose the ability to bring
    their own data and the product reads as instructions-only to them.
    """
    assert "create_file_data_source" in DEFAULT_MEMBER_PERMISSIONS


def test_every_registry_member_permission_is_seeded_or_migrated():
    seeded = _string_literals(RBAC_SEED)
    granted_later = _string_literals(GRANT_MIGRATION) if GRANT_MIGRATION.exists() else set()
    covered = seeded | granted_later

    missing = [p for p in DEFAULT_MEMBER_PERMISSIONS if p not in covered]

    assert not missing, (
        "DEFAULT_MEMBER_PERMISSIONS grants permissions that no migration ever "
        f"writes to the member role: {missing}. Adding a permission to the "
        "registry does NOT reach existing or new installs — the member role is "
        "seeded from a hardcoded list in the RBAC migration. Ship a migration "
        "that adds it, the way ca09memberfileds does, or members will silently "
        "run one permission short."
    )


@pytest.mark.parametrize("permission", DEFAULT_MEMBER_PERMISSIONS)
def test_each_member_permission_is_a_known_permission(permission):
    """Guards the other direction: a typo in the registry grants nothing.

    A misspelled permission is accepted everywhere, matches no gate, and fails
    only as "the button does nothing" for whoever holds the role.
    """
    from app.core import permissions_registry as reg

    known = set()
    for value in vars(reg).values():
        if isinstance(value, dict):
            for group in value.values():
                if isinstance(group, (list, tuple)):
                    known.update(x for x in group if isinstance(x, str))
        elif isinstance(value, (list, tuple)):
            known.update(x for x in value if isinstance(x, str))

    assert permission in known, (
        f"'{permission}' is in DEFAULT_MEMBER_PERMISSIONS but is not a permission "
        "declared anywhere in the registry — likely a typo, which grants nothing."
    )
