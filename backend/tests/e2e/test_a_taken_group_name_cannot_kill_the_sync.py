"""A group name already in use must cost one group, never the whole sync.

`groups` is unique on `(organization_id, name)`. The constraint carries no
provider column and no `deleted_at` predicate, so a name is equally taken by a
hand-made group, one synced from another directory, and a soft-deleted
tombstone.

Both directory syncs keyed their "do I already have this?" lookup on the
EXTERNAL ID only — DN for LDAP, claim id for OIDC — and narrowed it to live rows
of their own provider. Any of the three cases above therefore reported the name
as free, the INSERT hit the constraint, and because the commit sits at the end
of the whole sync the `IntegrityError` rolled back EVERY group and EVERY
membership in that run. It then repeated on the next tick, forever.

★★★Measured in production 2026-08-04: `CN=Administrators,CN=Builtin,DC=cmhl,...`
failed on the hour, five hours running, and LDAP directory sync was dead for
that organisation the whole time. The five `PendingRollbackError`s in the same
log are the poisoned session that follows.

★These live in `tests/e2e/`, NOT `tests/unit/fork/`: they need a real schema,
and `tests/unit/fork/conftest.py` neuters `run_migrations`, so a schema-needing
test there fails "no such table" and reads as a product bug.
"""
import asyncio
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.dependencies import async_session_maker
from app.ee.ldap.sync_service import LDAPGroupSyncService
from app.models.group import Group
from app.models.organization import Organization


GROUPS = [
    {"dn": "cn=Administrators,cn=Builtin,dc=test,dc=com",
     "name": "Administrators", "members": []},
    {"dn": "cn=Engineering,ou=Groups,dc=test,dc=com",
     "name": "Engineering", "members": []},
]


def _svc():
    """The service with its LDAP connection replaced.

    ★`LDAPGroupSyncService.__init__` builds a real `LDAPConnectionManager` from
    the config, so the mock has to be swapped in AFTER construction — patching
    the class would also have worked, but this keeps the seam visible.
    """
    svc = LDAPGroupSyncService(config=MagicMock())
    conn = MagicMock()
    conn.search_users.return_value = []
    conn.search_groups.return_value = GROUPS
    svc.connection = conn
    return svc


def _run(coro):
    return asyncio.run(coro)


async def _org(db):
    org = Organization(name=f"LDAP Collide {uuid.uuid4().hex[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _names(db, org_id):
    rows = (await db.execute(
        select(Group).where(Group.organization_id == str(org_id))
    )).scalars().all()
    return {g.name: g for g in rows}


@pytest.mark.e2e
def test_a_manually_created_name_does_not_abort_the_run():
    """★The production failure. One taken name must not take the rest with it.

    The clash is skipped and reported; the OTHER group still lands. Before the
    fix, `Engineering` was rolled back too — collateral from a name it has
    nothing to do with.
    """
    async def go():
        async with async_session_maker() as db:
            org = await _org(db)
            db.add(Group(organization_id=str(org.id), name="Administrators"))
            await db.commit()

            result = await _svc().sync_groups(db, str(org.id))

            names = await _names(db, org.id)
            assert "Engineering" in names, (
                "an unrelated group was rolled back because a DIFFERENT group's "
                "name was taken — the whole sync aborted"
            )
            assert any("Administrators" in e for e in result.errors), (
                f"the skipped group must be reported, got errors={result.errors}"
            )
    _run(go())


@pytest.mark.e2e
def test_the_hand_made_group_is_not_taken_over():
    """★Deliberately NOT adopted.

    Adopting it would hand its membership to the directory and silently drop
    everyone an admin added by hand. Skipping keeps that decision with a person.
    """
    async def go():
        async with async_session_maker() as db:
            org = await _org(db)
            db.add(Group(organization_id=str(org.id), name="Administrators"))
            await db.commit()

            await _svc().sync_groups(db, str(org.id))

            admins = (await _names(db, org.id))["Administrators"]
            assert admins.external_provider is None, (
                "LDAP took over a manually-created group"
            )
            assert admins.external_id is None
    _run(go())


@pytest.mark.e2e
def test_a_tombstone_of_our_own_is_revived_not_duplicated():
    """★The self-inflicted case, and the one that loops forever.

    The sync soft-deletes groups that leave the directory. When such a group
    returns, the provider-and-live-only lookup cannot see its tombstone — but
    the unique constraint still can. Revive it instead of inserting a twin.
    """
    async def go():
        async with async_session_maker() as db:
            org = await _org(db)
            dead = Group(
                organization_id=str(org.id),
                name="Administrators",
                external_id="cn=OLD,dc=test,dc=com",
                external_provider="ldap",
                deleted_at=datetime.utcnow(),
            )
            db.add(dead)
            await db.commit()
            dead_id = str(dead.id)

            await _svc().sync_groups(db, str(org.id))

            revived = (await _names(db, org.id))["Administrators"]
            assert str(revived.id) == dead_id, (
                "a duplicate was created beside the tombstone"
            )
            assert revived.deleted_at is None, "the tombstone was not revived"
            assert revived.external_id == "cn=Administrators,cn=Builtin,dc=test,dc=com", (
                "the revived group kept its stale DN"
            )
    _run(go())


@pytest.mark.e2e
def test_a_clean_org_still_creates_every_group():
    """The ordinary path, asserted so the guards above cannot pass by refusing
    to create anything at all."""
    async def go():
        async with async_session_maker() as db:
            org = await _org(db)
            await db.commit()

            result = await _svc().sync_groups(db, str(org.id))

            names = await _names(db, org.id)
            assert {"Administrators", "Engineering"} <= set(names)
            assert result.groups_created == 2
    _run(go())
