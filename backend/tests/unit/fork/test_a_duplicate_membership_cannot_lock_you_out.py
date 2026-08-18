"""One person, one workspace, two rows — and the whole product stops.

WHAT HAPPENED IN PRODUCTION
---------------------------
`chitsnowwai@cityholdings.com.mm` had TWO `memberships` rows for the same
organization. `principal_belongs_to_org` asks "is this person a member?" with
`scalar_one_or_none()`, which does not mean "is there one?" — it means "there
must be at most one, or raise". Two rows raised `MultipleResultsFound`.

That function is called by `get_current_organization`, which nearly every
org-scoped route depends on. So a single duplicate row turned into a 500 on
almost every request that user made: 572 of 3613 requests in one morning, on
`/api/reports`, `/api/llm/models`, `/api/projects`, `/api/organization/settings`,
`/api/files`, `/api/instructions` and more.

What the user saw was not an error page. It was "No reports found" and
"Connect your LLM" — because the frontend turns a failed request into an empty
state. Their reports, their models and a slide deck they had just built were
all still in the database the whole time.

THREE DEFECTS, ONE SYMPTOM
--------------------------
1. `principal_belongs_to_org` uses `scalar_one_or_none()` for what is purely an
   existence question. Both branches do — the human one and the service-account
   one.

2. `get_user_organizations` (the workspace switcher, and the source of
   `orgs[0]`) has no `DISTINCT`, so a duplicate membership lists the same
   workspace twice. That is the two identical "Insights" entries in the
   account menu.

3. The same query has no `deleted_at` filter, while the membership CHECK does.
   So a workspace someone was REMOVED from still appears in their switcher, and
   every request into it is then refused by the check twelve files away. The
   list and the check must agree on what membership means.

★And a fourth, which is why this is not merely cosmetic: that query has no
`ORDER BY`. Postgres may return rows in any order, so `orgs[0]` — the
workspace the app silently selects when nothing is persisted — can differ
between two loads of the same page. A report opened in one workspace 404s in
the other, which is the same "my work disappeared" symptom arriving by a
completely different route.

★These tests seed rows and call the real functions. A source-text assertion
would pass against a query that still returns duplicates; only real rows can
prove which rows SQL gives back. Schema is built once per module with
`Base.metadata.create_all` and copied per test, matching
`test_report_activity_is_authorized.py` — the fork suite's no-database rule
stays intact for every other file.
"""
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main  # noqa: F401  — boots the app's ORM registry
from app.models.base import Base
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.core.permission_resolver import principal_belongs_to_org


@pytest.fixture(scope="module")
def schema_template():
    tmp = Path(tempfile.mkdtemp(prefix="dup-membership-"))
    template = tmp / "template.db"
    engine = create_engine(f"sqlite:///{template}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield template
    shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def db(schema_template):
    copy = schema_template.parent / f"{uuid.uuid4().hex}.db"
    shutil.copyfile(schema_template, copy)
    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        copy.unlink(missing_ok=True)


# --- explicit row builders; nothing here selects an actor by pattern -------

def _user(name: str) -> User:
    return User(
        id=str(uuid.uuid4()),
        name=name,
        email=f"{name}@example.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )


def _org(name: str) -> Organization:
    return Organization(id=str(uuid.uuid4()), name=name)


def _membership(user: User, org: Organization, *, role: str = "member",
                deleted_at=None) -> Membership:
    return Membership(
        id=str(uuid.uuid4()),
        user_id=user.id,
        organization_id=org.id,
        role=role,
        deleted_at=deleted_at,
    )


# ---------------------------------------------------------------------------
# 1. The check itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_duplicate_membership_still_answers_the_question(db):
    """★The production outage, in three rows.

    Two membership rows is a data problem worth fixing on its own, but it must
    never be able to take the product down. "Is this person a member?" has a
    correct answer here — yes — and the only reason it ever raised is that the
    question was asked with a method that also asserts uniqueness.
    """
    user, org = _user("chit"), _org("Insights")
    db.add_all([user, org, _membership(user, org), _membership(user, org)])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is True


@pytest.mark.asyncio
async def test_one_membership_still_answers_yes(db):
    """Positive control: the fix must not make everyone a member of nothing."""
    user, org = _user("solo"), _org("Insights")
    db.add_all([user, org, _membership(user, org)])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is True


@pytest.mark.asyncio
async def test_no_membership_still_answers_no(db):
    """Negative control: the fix must not turn the check into "always true".

    Without this, `return True` passes every other test in this file.
    """
    user, org = _user("stranger"), _org("Insights")
    db.add_all([user, org])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is False


@pytest.mark.asyncio
async def test_a_removed_member_is_still_refused_even_with_a_duplicate(db):
    """★Tolerating duplicates must not tolerate REMOVAL.

    Both rows are soft-deleted, so the answer is still no. A fix that stopped
    filtering `deleted_at` in order to stop raising would restore access to
    everyone who has ever been removed from a workspace — a far worse bug than
    the one being fixed.
    """
    user, org = _user("removed"), _org("Insights")
    from datetime import datetime
    now = datetime.utcnow()
    db.add_all([
        user, org,
        _membership(user, org, deleted_at=now),
        _membership(user, org, deleted_at=now),
    ])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is False


@pytest.mark.asyncio
async def test_a_live_row_beside_a_deleted_one_still_belongs(db):
    """Removed once, re-invited later: the live row is the one that counts."""
    user, org = _user("rejoined"), _org("Insights")
    from datetime import datetime
    db.add_all([
        user, org,
        _membership(user, org, deleted_at=datetime.utcnow()),
        _membership(user, org),
    ])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is True


@pytest.mark.asyncio
async def test_a_disabled_account_is_still_refused_with_a_duplicate(db):
    """★The is_active gate must survive the fix.

    Directory deprovision sets `is_active = False` and LEAVES the membership
    standing, so this is the check that stops a deprovisioned person. It sits
    above the membership query and must not be reordered past it.
    """
    user, org = _user("disabled"), _org("Insights")
    user.is_active = False
    db.add_all([user, org, _membership(user, org), _membership(user, org)])
    await db.commit()

    assert await principal_belongs_to_org(db, user, org.id) is False


# ---------------------------------------------------------------------------
# 2. The list the workspace switcher is built from
#
# ★These call the REAL `get_user_organizations`, not a re-typed copy of its
# query. An earlier draft of this file re-implemented the SELECT here and
# asserted against that — which proves the test's own SQL is correct and would
# stay green through any regression in the service. If it does not call the
# shipping function, it is not a guard.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta

from app.services.organization_service import OrganizationService


async def _orgs(db, user):
    return await OrganizationService().get_user_organizations(db, user)


@pytest.mark.asyncio
async def test_a_duplicate_membership_lists_the_workspace_once(db):
    """★The two identical "Insights" entries in the account menu."""
    user, org = _user("dup"), _org("Insights")
    db.add_all([user, org, _membership(user, org), _membership(user, org)])
    await db.commit()

    assert [o.name for o in await _orgs(db, user)] == ["Insights"]


@pytest.mark.asyncio
async def test_a_removed_workspace_is_not_offered(db):
    """★The list must agree with the check.

    Without the `deleted_at` filter this row still appears in the switcher, and
    `principal_belongs_to_org` then refuses every request into it — a workspace
    the product offers and cannot open.
    """
    user = _user("exmember")
    stays, removed = _org("Stays"), _org("Removed")
    db.add_all([
        user, stays, removed,
        _membership(user, stays),
        _membership(user, removed, deleted_at=datetime.utcnow()),
    ])
    await db.commit()

    assert [o.name for o in await _orgs(db, user)] == ["Stays"]


@pytest.mark.asyncio
async def test_the_workspace_order_is_stable(db):
    """★`orgs[0]` is the workspace the app selects when nothing is persisted.

    An unordered query lets that be a different workspace on the next load,
    which is how a report becomes a 404 on refresh and then comes back. A
    private window persists nothing, so it takes `orgs[0]` every time.
    """
    user = _user("multi")
    base = datetime(2026, 1, 1)
    orgs = []
    for n in range(5):
        o = _org(f"Org{n}")
        o.created_at = base + timedelta(days=n)
        orgs.append(o)
    db.add_all([user, *orgs])
    for o in orgs:
        db.add(_membership(user, o))
    await db.commit()

    first = [o.id for o in await _orgs(db, user)]
    for _ in range(5):
        assert [o.id for o in await _orgs(db, user)] == first
    # and it is the DEFINED order, not merely a repeatable accident
    assert [o.name for o in await _orgs(db, user)] == \
        ["Org0", "Org1", "Org2", "Org3", "Org4"]


@pytest.mark.asyncio
async def test_the_switcher_query_orders_explicitly():
    """★The behavioural order test above CANNOT see this defect, so this exists.

    sqlite returns rows in insertion order for a query with no ORDER BY, so
    `test_the_workspace_order_is_stable` passes against the unordered query it
    was written to catch — measured, not assumed. Postgres makes no such
    promise, and production is Postgres. An unordered result there changes
    `orgs[0]`, which is the workspace the app selects when the user has no
    persisted choice.

    So the ordering is pinned at the source instead. A shape assertion is the
    weaker kind of test and is the right one here: the property is "this query
    states its order", and no in-memory database can demonstrate its absence.
    """
    from pathlib import Path
    import re

    src = Path(__file__).resolve().parents[3] / "app" / "services" / "organization_service.py"
    body = src.read_text(encoding="utf-8")
    start = body.index("async def get_user_organizations(")
    end = body.index("\n    async def ", start + 10)
    fn = body[start:end]

    assert "select(Organization, Membership.role)" in fn, \
        "the switcher query moved — re-point this guard at it"
    assert ".order_by(" in fn, (
        "get_user_organizations has no ORDER BY. Postgres may then return the "
        "workspaces in any order, and the frontend takes orgs[0]."
    )
    assert "deleted_at" in fn, (
        "the switcher lists memberships the membership CHECK would refuse"
    )
