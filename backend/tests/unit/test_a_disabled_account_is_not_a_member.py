"""A deactivated account must stop being a member of the organization.

`principal_belongs_to_org` is the single membership chokepoint. It is what
`get_current_organization` calls for the ~138 org-scoped routes that carry no
decorator, what both `requires_permission` decorators call, and what the
scheduled-prompt runner calls before firing a task on someone's behalf. Its
docstring is explicit that humans bind through a `Membership` row — and that is
all it checked. It never looked at `User.is_active`.

That gap is invisible on one path and load-bearing on the other:

  * **Removal** deletes the `Membership` row, so the check fails anyway and a
    removed member is refused everywhere. Nothing here changes for them.
  * **Directory deprovision (SCIM)** does NOT delete the membership. It sets
    `user.is_active = False` and stops (`ee/scim/service.py`). The membership
    row survives, so `principal_belongs_to_org` keeps answering True for an
    account that can no longer sign in.

Two things follow from that, and both are pinned below.

**1. Their scheduled tasks keep firing.** `scheduled_prompt_service` has an
invariant written for exactly this case — its comment says "don't keep running
a departed member's schedule as them… removed directly, or via LDAP/OIDC/SCIM
sync" — and it asks `principal_belongs_to_org`. On the SCIM path that guard
never fires, so the task runs, queries data and emails subscribers on behalf of
a disabled account, indefinitely. This is the same defect Metabase has open as
issue #52407.

**2. A human API key keeps working.** `ApiKeyService.get_user_by_api_key`
rejects a *service-account* key whose account is disabled, deliberately and with
a comment saying so. For a *human* key it checks the hash, the soft-delete and
the expiry — and then returns the user without ever consulting `is_active`. The
JWT door is closed (`fapi.current_user(active=True)`), so this is the only way a
disabled human still authenticates. Measured on this install 13 Aug 2026:
**0 API keys exist and 0 deactivated humans hold a membership**, so it is a
latent defect here rather than a live exposure — which is exactly the moment to
close it.

★★★**The service-account branch is the whole risk of this change.** A service
account is backed by a `users` row created deliberately with
`is_active=False` so it can never log in interactively, while its API keys keep
working. A naive "require is_active" placed at the top of
`principal_belongs_to_org` — or an unconditional one in `get_user_by_api_key` —
disables **every service account and every API key in the installation**. That
is why `test_a_service_account_still_belongs` and
`test_a_service_account_key_still_authenticates` are here: they are positive
controls, and they are the two tests that tell a working fix from an outage.

★Red proof, measured rather than assumed. On a detached worktree at the
pre-fix commit `44207df0d` (`git worktree add /tmp/base531 HEAD --detach`),
run on the `/src` runner:

    2 failed, 3 passed in 6.56s

The two failures are `test_a_deprovisioned_human_is_refused_despite_keeping_
their_membership` and `test_a_human_api_key_stops_working_when_the_account_is_
disabled` — both defects real. The three that passed are the positive controls
plus the already-correct removal path, which is what proves the two failures
are the bug and not a broken fixture. On the fixed tree: **5 passed**.

★These need a schema, so they live here and NOT in `tests/unit/fork` — that
directory's conftest overrides `run_migrations` with a no-op and anything
touching a table fails with "no such table", which reads as a product bug.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.permission_resolver import principal_belongs_to_org
from app.dependencies import async_session_maker
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.service_account import ServiceAccount
from app.models.user import User


# ────────────────────────────── fixtures ──────────────────────────────────


def _uid() -> str:
    return str(uuid.uuid4())


async def _make_org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _make_human(db, org, *, is_active: bool, with_membership: bool = True) -> User:
    """A human account, optionally still carrying its membership row.

    `with_membership=True` and `is_active=False` together are the SCIM
    deprovision shape: the row survives, the account is disabled.
    """
    user = User(
        id=_uid(),
        name="Departed Person",
        email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x",
        is_active=is_active,
        is_superuser=False,
        is_verified=True,
        is_service_account=False,
    )
    db.add(user)
    await db.flush()
    if with_membership:
        db.add(Membership(user_id=user.id, organization_id=org.id, role="member"))
        await db.flush()
    return user


async def _make_service_account(db, org) -> User:
    """The backing user of a service account: is_active=False BY DESIGN.

    No Membership row — service accounts consume no seat and never appear in
    member lists; they bind to the org through the ServiceAccount row itself.
    """
    user = User(
        id=_uid(),
        name="Reporting Bot",
        email=f"sa-{_uid()[:8]}@cityagent.io",
        hashed_password="x",
        is_active=False,
        is_superuser=False,
        is_verified=True,
        is_service_account=True,
    )
    db.add(user)
    await db.flush()
    db.add(ServiceAccount(id=_uid(), organization_id=org.id, user_id=user.id, name="Reporting Bot"))
    await db.flush()
    return user


# ─────────────────────── what must start refusing ─────────────────────────


@pytest.mark.asyncio
async def test_a_deprovisioned_human_is_refused_despite_keeping_their_membership():
    """The SCIM shape, and the reason this file exists.

    The membership row is still there — that is what SCIM leaves behind — so
    the pre-fix check answers True and the account keeps its access.
    """
    async with async_session_maker() as db:
        org = await _make_org(db)
        user = await _make_human(db, org, is_active=False, with_membership=True)

        assert await principal_belongs_to_org(db, user, str(org.id)) is False, (
            "a disabled account still counts as a member of the organization; "
            "on the SCIM deprovision path the membership row survives, so this "
            "is the only thing standing between a departed person and their "
            "scheduled tasks continuing to run as them"
        )


@pytest.mark.asyncio
async def test_a_human_api_key_stops_working_when_the_account_is_disabled():
    """The second door into the same room.

    The JWT door already refuses a disabled account. This one did not, and it
    is the door that stays open for months because nobody revokes a key on the
    way out.
    """
    from app.schemas.api_key_schema import ApiKeyCreate
    from app.services.api_key_service import ApiKeyService

    async with async_session_maker() as db:
        org = await _make_org(db)
        user = await _make_human(db, org, is_active=True, with_membership=True)

        service = ApiKeyService()
        created = await service.create_api_key(
            db, ApiKeyCreate(name="laptop"), user, org
        )
        raw_key = created.key

        # Still employed: the key works.
        assert await service.get_user_by_api_key(db, raw_key) is not None

        # Deprovisioned by the directory.
        user.is_active = False
        await db.flush()

        assert await service.get_user_by_api_key(db, raw_key) is None, (
            "a human API key still authenticates after the account was "
            "disabled; the service-account branch of this same function "
            "already refuses a disabled account, the human branch did not"
        )


# ──────────────────── positive controls — the real risk ───────────────────


@pytest.mark.asyncio
async def test_a_service_account_still_belongs():
    """★If this ever fails, every service account in the installation is dead.

    Its backing user is `is_active=False` on purpose. The fix must live in the
    human branch only; the service-account branch returns before it and binds
    through the ServiceAccount row, not a Membership.
    """
    async with async_session_maker() as db:
        org = await _make_org(db)
        sa_user = await _make_service_account(db, org)

        assert await principal_belongs_to_org(db, sa_user, str(org.id)) is True, (
            "a service account no longer belongs to its organization — this is "
            "an outage, not a tightening: every API integration in the "
            "installation stops working"
        )


@pytest.mark.asyncio
async def test_an_active_member_still_belongs():
    """The other half of the control. Refusal-only tests pass on a gate that
    refuses everyone."""
    async with async_session_maker() as db:
        org = await _make_org(db)
        user = await _make_human(db, org, is_active=True, with_membership=True)

        assert await principal_belongs_to_org(db, user, str(org.id)) is True


@pytest.mark.asyncio
async def test_a_removed_member_is_still_refused():
    """Unchanged behaviour, pinned so the fix cannot be mistaken for the cause.

    Removal deletes the membership row, so this case was already correct. It is
    here because a future reader comparing before and after needs to see which
    path actually moved.
    """
    async with async_session_maker() as db:
        org = await _make_org(db)
        user = await _make_human(db, org, is_active=True, with_membership=False)

        assert await principal_belongs_to_org(db, user, str(org.id)) is False
