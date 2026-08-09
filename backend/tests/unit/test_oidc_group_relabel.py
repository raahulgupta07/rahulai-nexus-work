"""Group sync must relabel a GUID-named row, and never overwrite a real name.

Companion to ``tests/unit/fork/test_an_unnameable_entra_group_is_not_named_after_its_guid.py``,
which covers the Graph helpers and the labelling rule with no database. These
tests need a real ``groups`` table — including its ``UNIQUE (organization_id,
name)`` constraint — so they cannot live in ``tests/unit/fork`` (that
directory's conftest makes ``run_migrations`` a no-op).

What is pinned here, from upstream 2e811b30, hand-ported onto this fork's
diverged ``group_sync_service`` (name-collision handling + savepoint):

  - a resolved name always wins, including over a placeholder written on an
    earlier login when Graph was unavailable — a transient Graph failure must
    never overwrite a known name with a GUID;
  - a row already stored under its raw GUID is relabelled on the next sync;
  - only GUID-shaped ids get the placeholder; a readable claim value
    ("Engineering") is left exactly as it is.

★Measured against a copy of the working tree with graph_client.py and
group_sync_service.py restored from ``git show HEAD:`` (the pre-fix files):
   HEAD (pre-fix): 4 failed, 3 passed.
   Fixed:          7 passed.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_env_src = (Path(__file__).resolve().parents[2] / "alembic" / "env.py").read_text()
for _stmt in re.findall(
    r"^from app\.models\S* import \([^)]*\)|^from app\.models[^\n]+", _env_src, re.M
):
    exec(_stmt)  # noqa: S102 — test-only, mirrors env.py verbatim

from app.models.base import Base
from app.models.group import Group
from app.models.organization import Organization
from app.models.user import User

from app.ee.oidc.group_sync_service import PROVIDER_NAME, sync_user_oidc_groups

GUID = "85f43b45-99ae-43a0-a780-a05c119e8b9c"
PLACEHOLDER = "Unresolved directory group (85f43b45…)"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _seed(db: AsyncSession):
    org = Organization(name=f"Org-{uuid.uuid4()}")
    db.add(org)
    await db.flush()
    user = User(name="U", email=f"u-{uuid.uuid4()}@x.dev", hashed_password="x")
    db.add(user)
    await db.flush()
    return str(org.id), str(user.id)


async def _names(db: AsyncSession, org_id: str):
    rows = (await db.execute(
        select(Group).where(Group.organization_id == org_id)
    )).scalars().all()
    return {g.external_id: g.name for g in rows}


@pytest.mark.asyncio
async def test_an_unresolvable_id_is_labelled_not_named_after_itself(db):
    """The whole point: Graph omitted this object, so the admin must see that it
    is unresolved rather than a group apparently called 85f43b45-…"""
    org_id, user_id = await _seed(db)

    await sync_user_oidc_groups(db, user_id, org_id, [GUID], {GUID: None})

    assert await _names(db, org_id) == {GUID: PLACEHOLDER}


@pytest.mark.asyncio
async def test_a_backfilled_guid_lookup_is_still_treated_as_unresolved(db):
    """The shape the OLD Graph helper produced: every unresolved id backfilled
    with itself. The placeholder was gated on the lookup being falsy and a GUID
    is truthy, so the raw id became the group's name — this is the exact path
    that put 85f43b45-… in the admin's list."""
    org_id, user_id = await _seed(db)

    await sync_user_oidc_groups(db, user_id, org_id, [GUID], {GUID: GUID})

    assert await _names(db, org_id) == {GUID: PLACEHOLDER}


@pytest.mark.asyncio
async def test_a_legacy_guid_named_row_is_relabelled_on_the_next_sync(db):
    """Rows written by the old backfill are already in customers' databases; the
    next login fixes them without waiting for the migration."""
    org_id, user_id = await _seed(db)
    db.add(Group(organization_id=org_id, name=GUID, external_id=GUID,
                 external_provider=PROVIDER_NAME))
    await db.flush()

    result = await sync_user_oidc_groups(db, user_id, org_id, [GUID], {GUID: None})

    assert await _names(db, org_id) == {GUID: PLACEHOLDER}
    assert result.groups_updated == 1


@pytest.mark.asyncio
async def test_a_transient_graph_failure_cannot_erase_a_known_name(db):
    """Graph was down on this login. The stored name is the last thing anyone
    knows about the group and must survive — replacing it with a placeholder is
    strictly worse than serving a possibly stale name."""
    org_id, user_id = await _seed(db)
    db.add(Group(organization_id=org_id, name="PowerBI-ServicePrincipals",
                 external_id=GUID, external_provider=PROVIDER_NAME))
    await db.flush()

    result = await sync_user_oidc_groups(db, user_id, org_id, [GUID], {GUID: None})

    assert await _names(db, org_id) == {GUID: "PowerBI-ServicePrincipals"}
    assert result.groups_updated == 0


@pytest.mark.asyncio
async def test_a_resolved_name_replaces_a_placeholder_written_earlier(db):
    """Consent was granted, or Graph came back. The real name wins."""
    org_id, user_id = await _seed(db)
    db.add(Group(organization_id=org_id, name=PLACEHOLDER, external_id=GUID,
                 external_provider=PROVIDER_NAME))
    await db.flush()

    result = await sync_user_oidc_groups(
        db, user_id, org_id, [GUID], {GUID: "PowerBI-ServicePrincipals"}
    )

    assert await _names(db, org_id) == {GUID: "PowerBI-ServicePrincipals"}
    assert result.groups_updated == 1


@pytest.mark.asyncio
async def test_two_unresolved_ids_sharing_a_prefix_do_not_fight_over_one_row(db):
    """The LIVE counterpart of the migration's collision case.

    The label keeps only 8 hex digits, so two unresolved ids sharing that prefix
    want the same name, and ``groups`` is UNIQUE on (organization_id, name).
    This runs on EVERY login, so the failure mode that matters is not a one-off
    error but a row whose ``external_id`` flips back and forth — memberships
    would follow it. Whatever the outcome, it must be STABLE across logins.
    """
    org_id, user_id = await _seed(db)
    twin = "85f43b45-0000-0000-0000-000000000000"  # same first 8 hex as GUID
    assert twin[:8] == GUID[:8]

    seen = []
    for _ in range(3):
        await sync_user_oidc_groups(
            db, user_id, org_id, [GUID, twin], {GUID: None, twin: None}
        )
        seen.append(await _names(db, org_id))

    assert seen[0] == seen[1] == seen[2], f"unstable across logins: {seen}"
    # Exactly one row holds the contested label; no two rows share a name.
    names = list(seen[-1].values())
    assert len(names) == len(set(names))
    assert PLACEHOLDER in names


@pytest.mark.asyncio
async def test_a_readable_claim_value_is_never_labelled_unresolved(db):
    """Okta and Keycloak put the name straight in the claim, with no Graph
    lookup at all. Labelling "Engineering" as unresolved would be a downgrade —
    and would then be relabelled to a placeholder on every single login."""
    org_id, user_id = await _seed(db)

    await sync_user_oidc_groups(db, user_id, org_id, ["Engineering"], {})
    await sync_user_oidc_groups(db, user_id, org_id, ["Engineering"], {})

    assert await _names(db, org_id) == {"Engineering": "Engineering"}
