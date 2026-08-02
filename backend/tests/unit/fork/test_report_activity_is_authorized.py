"""Report surfaces must authorize on VISIBILITY, not on organization.

Four surfaces, one bug class: each decided what a caller could see from the
organization alone. Org membership is not entitlement to a report. Three took
a report id from the caller; the fourth pushed every org event to every
listener.

THE HOLE THIS PINS — 1: GET /reports/activity
---------------------------------------------
`ReportService.get_reports_activity(db, ids, current_user, organization)` takes
its `ids` straight off the query string of `GET /reports/activity?ids=a,b,c`.
It used to filter only on `Report.organization_id == organization.id`, on the
stated assumption that "the ids themselves come from lists the server already
filtered". They do not — they come from the caller.

So any org member holding `view_reports` could name the id of a report they
cannot open and read back its live state: running / queued / awaiting_user /
error, plus `last_activity_at`. That is a working presence channel over
someone else's private conversation — when it runs, how long it ran, whether
it failed — and `last_activity_at` alone is enough to watch a colleague work.

The fix is to AND `visible_reports_predicate` into the same query. A report the
caller cannot see is simply ABSENT from the response; it is never a 403,
because this endpoint feeds list badges and a 403 would itself confirm the id
exists.

THE HOLE THIS PINS — 2 and 3: star and mark-viewed
--------------------------------------------------
`set_report_star` and `mark_report_viewed` take `report_id` from the URL PATH
rather than a list, and had the same org-only filter. Both then WRITE — a
`ReportStar` or a `ReportView` row keyed to the caller. So a member could star
a colleague's private report, and the 200-vs-404 split told them whether the
id named a real report at all. The route decorator does not cover this: its
per-object ladder runs only under `owner_only=True`, which these two do not
use.

★These two are checked at the TABLE, not just the response. A fix that returns
404 while still inserting the row would pass a response-only test and leave
the write — and the write is half the bug (a star on someone else's private
report surfaces it in the attacker's own starred list).

★What the fix does on an invisible report — 404 or a silent no-op — is left
open on purpose. The tests below assert the two things that must hold either
way: nothing is written, and the answer for an invisible REAL report is
byte-identical to the answer for an id that does not exist. Pinning the status
code would make this file break the next time that (legitimately) changes.

THE HOLE THIS PINS — 4: the SSE activity feed
---------------------------------------------
`report_activity_hub` derives activity once per organization and fans it out
to that worker's subscribers. It used to queue every org event to every
subscriber, so anyone holding an open `/reports/activity/stream` connection
got a live tap on every report in the org — the same disclosure as (1), but
pushed continuously and without one request per guess.

The existing hub test subscribes the report's OWNER, so it proves events
still FLOW; nothing proved one is WITHHELD. That negative is the entire claim
of a filter, and it lives at the end of this file.

WHY THIS FILE BUILDS ITS OWN SCHEMA
-----------------------------------
`tests/unit/fork/conftest.py` no-ops the parent's per-test migration fixture, so
there is no database here by default (see that file — it is a deliberate 94×
speedup, not an oversight). A predicate assembled in Python proves nothing
about which rows SQL returns, so this test needs real rows. It therefore builds
its own sqlite schema ONCE per module with `Base.metadata.create_all` and hands
each test a private copy of that file: no alembic replay, no shared state, and
the fork suite's no-DB rule stays intact for every other file.

★`users` has NO `created_at` column in this schema (`User` extends
`SQLAlchemyBaseUserTable`, not `BaseSchema`). Never order or filter on it here.

★Every principal below is constructed explicitly. Nothing in this file selects
an actor by pattern ("the first admin", `email.like('%admin%')`) — a harness
that silently falls back to whoever it finds first has previously reported a
working product as broken. A missing fixture must ERROR, not resolve to
somebody else.
"""
import inspect
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import main  # noqa: F401  — boots the app's ORM registry
from app.models.base import Base
from app.models.completion import Completion
from app.models.group import Group
from app.models.group_membership import GroupMembership
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.report_star import ReportStar
from app.models.report_view import ReportView
from app.models.user import User
from app.services.report_service import ReportService
from app.streaming.report_activity_hub import ReportActivityHub
from app.streaming.report_activity_hub import _Subscriber as _HubSubscriber

# ★`import main` is NOT enough for `create_all`. `derive_activity_sets` imports
# these three inside the function body, so their tables are absent from
# `Base.metadata` when the schema is built and the run dies with
# `no such table: tool_confirmations` — which reads like a product bug and is
# not one. Import them here; do NOT replace this with a walk over
# `app.models`, which also picks up orphan modules (`application.py`) whose
# mappers no longer resolve and breaks every test at fixture setup.
from app.models.completion_block import CompletionBlock  # noqa: F401
from app.models.tool_confirmation import ToolConfirmation  # noqa: F401
from app.models.tool_execution import ToolExecution  # noqa: F401


# --- schema ----------------------------------------------------------------

@pytest.fixture(scope="module")
def schema_template():
    """One sqlite file with the full schema, built once, copied per test."""
    tmp = Path(tempfile.mkdtemp(prefix="report-activity-authz-"))
    template = tmp / "template.db"
    engine = create_engine(f"sqlite:///{template}")
    Base.metadata.create_all(engine)
    engine.dispose()
    yield template
    shutil.rmtree(tmp, ignore_errors=True)


@pytest_asyncio.fixture
async def session_factory(schema_template):
    """A private copy of the schema, plus the factory that opens sessions on it.

    Exposed as a factory (not just one session) because the activity hub's
    watcher deliberately opens its OWN short-lived session per tick — it
    outlives every request and must never hold a request's pooled connection.
    The hub tests hand it this factory; see the `hub` fixture.
    """
    copy = schema_template.parent / f"{uuid.uuid4().hex}.db"
    shutil.copyfile(schema_template, copy)
    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
        copy.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def db(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()


# --- row builders (explicit; no lookups, no fallbacks) ---------------------

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


def _report(*, owner: User, org: Organization, title: str,
            artifact_visibility: str = "none",
            conversation_visibility: str = "none") -> Report:
    return Report(
        id=str(uuid.uuid4()),
        title=title,
        slug=f"{title}-{uuid.uuid4().hex[:8]}",
        user_id=owner.id,
        organization_id=org.id,
        artifact_visibility=artifact_visibility,
        conversation_visibility=conversation_visibility,
        last_activity_at=datetime.utcnow(),
    )


def _live_completion(*, report: Report, user: User) -> Completion:
    """An in-progress run: what makes a report 'running' to every surface."""
    return Completion(
        id=str(uuid.uuid4()),
        report_id=report.id,
        user_id=user.id,
        status="in_progress",
        role="system",
        prompt={},
        completion={},
        sigkill=datetime.utcnow(),
    )


class World:
    """The fixtures a test names, held by attribute so a typo raises."""


@pytest_asyncio.fixture
async def world(db):
    w = World()

    w.org = Organization(id=str(uuid.uuid4()), name="Acme")
    w.other_org = Organization(id=str(uuid.uuid4()), name="Contoso")
    # A third org kept deliberately clean for the SSE hub tests: it contains
    # NO public/internal report, so "this subscriber may see nothing" is a
    # state that can actually be reached. In `w.org` it cannot — `internal` is
    # org-wide by design, so every member there sees at least one report and
    # an "empty queue" assertion would be vacuous.
    w.hub_org = Organization(id=str(uuid.uuid4()), name="Hubco")

    # The actor: an ordinary member of w.org. Not an admin — the endpoint is
    # gated on `view_reports`, which every member holds, and that permission is
    # exactly what made the leak reachable by anyone.
    w.member = _user("member")
    # Owns the reports the actor is not entitled to.
    w.colleague = _user("colleague")
    # Exists only so a "shared with someone else" grant has a real grantee.
    w.stranger = _user("stranger")

    # Hubco's cast (see w.hub_org): one member who owns a private report, one
    # who owns nothing and is granted nothing.
    w.hub_viewer = _user("hub-viewer")
    w.hub_owner = _user("hub-owner")
    w.hub_outsider = _user("hub-outsider")

    db.add_all([w.org, w.other_org, w.hub_org,
                w.member, w.colleague, w.stranger,
                w.hub_viewer, w.hub_owner, w.hub_outsider])
    await db.flush()

    for user in (w.member, w.colleague, w.stranger):
        db.add(Membership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=w.org.id,
            role="member",
        ))
    for user in (w.hub_viewer, w.hub_owner, w.hub_outsider):
        db.add(Membership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=w.hub_org.id,
            role="member",
        ))

    # Visible to the actor -------------------------------------------------
    w.own = _report(owner=w.member, org=w.org, title="my-own-private-report")
    w.internal = _report(owner=w.colleague, org=w.org, title="team-internal",
                         artifact_visibility="internal")
    w.shared_to_member = _report(owner=w.colleague, org=w.org, title="shared-with-me",
                                 artifact_visibility="shared")
    w.shared_to_group = _report(owner=w.colleague, org=w.org, title="shared-with-my-group",
                                artifact_visibility="shared")

    # NOT visible to the actor ---------------------------------------------
    w.secret = _report(owner=w.colleague, org=w.org, title="colleagues-private-report")
    w.shared_to_stranger = _report(owner=w.colleague, org=w.org, title="shared-with-someone-else",
                                   artifact_visibility="shared")
    w.foreign = _report(owner=w.stranger, org=w.other_org, title="another-orgs-report",
                        artifact_visibility="internal")

    # Hubco: two private reports with different owners, nothing shared, nothing
    # internal. hub_viewer sees exactly one; hub_outsider sees none.
    w.hub_visible = _report(owner=w.hub_viewer, org=w.hub_org, title="hub-viewers-own")
    w.hub_secret = _report(owner=w.hub_owner, org=w.hub_org, title="hub-someone-elses")

    db.add_all([w.own, w.internal, w.shared_to_member, w.shared_to_group,
                w.secret, w.shared_to_stranger, w.foreign,
                w.hub_visible, w.hub_secret])
    await db.flush()

    w.group = Group(id=str(uuid.uuid4()), organization_id=w.org.id, name="analysts")
    db.add(w.group)
    await db.flush()
    db.add(GroupMembership(id=str(uuid.uuid4()), group_id=w.group.id, user_id=w.member.id))

    db.add_all([
        ReportShare(id=str(uuid.uuid4()), report_id=w.shared_to_member.id,
                    user_id=w.member.id, share_type="artifact"),
        ReportShare(id=str(uuid.uuid4()), report_id=w.shared_to_group.id,
                    group_id=w.group.id, share_type="artifact"),
        ReportShare(id=str(uuid.uuid4()), report_id=w.shared_to_stranger.id,
                    user_id=w.stranger.id, share_type="artifact"),
    ])

    # Real activity on the report the actor may not see. Without this the
    # endpoint could look "safe" merely because there was nothing to disclose;
    # here the leaked row would carry a live state and a timestamp.
    db.add(_live_completion(report=w.secret, user=w.colleague))

    # Both Hubco reports are live, so the hub's candidate set contains them on
    # the very first tick regardless of clock skew in the churn window. Without
    # this, "the subscriber did not receive it" could just mean "nothing was
    # emitted for it" — the failure mode that makes a negative test worthless.
    db.add(_live_completion(report=w.hub_visible, user=w.hub_viewer))
    db.add(_live_completion(report=w.hub_secret, user=w.hub_owner))
    await db.commit()

    # ★Detach the fixtures from the session before any test touches them.
    # `db.rollback()` — which `_outcome` performs whenever the product raises
    # — EXPIRES every instance still attached, and the next `world.secret.id`
    # then fires a lazy reload from a plain (sync) attribute access. That
    # surfaces as `MissingGreenlet` inside whichever assertion happened to run
    # it, i.e. as a failure of the test rather than of the fixture.
    # `expire_on_commit=False` alone does NOT cover this: it governs commit,
    # not rollback.
    db.expunge_all()
    return w


async def _activity(db, world, ids):
    """Call the endpoint's service exactly as the route does, as `member`."""
    return await ReportService().get_reports_activity(
        db, ids, world.member, world.org
    )


def _ids(result) -> set[str]:
    return {row.id for row in result["activity"]}


# --- 1. a report the caller CAN see still comes back -----------------------

@pytest.mark.asyncio
async def test_a_member_gets_activity_for_a_report_they_can_see(db, world):
    result = await _activity(db, world, [world.internal.id])
    assert _ids(result) == {world.internal.id}


@pytest.mark.asyncio
async def test_every_way_of_being_visible_still_works(db, world):
    """★The fix must not be a blunt "owner only" — internal, direct share and
    group share are all legitimate ways to see a report, and each is a separate
    SQL term that a narrowed filter would silently drop."""
    visible = [world.own, world.internal, world.shared_to_member, world.shared_to_group]
    result = await _activity(db, world, [r.id for r in visible])
    assert _ids(result) == {r.id for r in visible}


# --- 2. THE LEAK: naming an id you may not see returns nothing -------------

@pytest.mark.asyncio
async def test_a_named_id_the_member_cannot_see_comes_back_empty(db, world):
    """★This is the vulnerability. The caller supplies the id of a colleague's
    private report — the strongest form of the attack, since it needs nothing
    but the id — and must learn nothing at all."""
    result = await _activity(db, world, [world.secret.id])
    assert result == {"activity": []}, (
        "activity for a report the caller cannot open was returned; "
        f"leaked: {[r.model_dump() for r in result['activity']]}"
    )


@pytest.mark.asyncio
async def test_an_invisible_id_is_dropped_not_refused(db, world):
    """Absent, not an error. A 403 would confirm the id names a real report;
    and the visible ids in the same call must still be answered."""
    result = await _activity(db, world, [world.internal.id, world.secret.id])
    assert _ids(result) == {world.internal.id}


@pytest.mark.asyncio
async def test_a_share_addressed_to_someone_else_is_not_a_share_to_you(db, world):
    """A `shared` report is visible only to its grantees. Reading the share
    table without matching the principal is the classic way this fails open."""
    result = await _activity(db, world, [world.shared_to_stranger.id])
    assert _ids(result) == set()


@pytest.mark.asyncio
async def test_another_organizations_report_stays_out(db, world):
    """The org filter that used to be the ONLY check must survive the fix."""
    result = await _activity(db, world, [world.foreign.id])
    assert _ids(result) == set()


@pytest.mark.asyncio
async def test_a_hidden_report_leaks_neither_its_state_nor_its_timestamp(db, world):
    """★What the leak was worth: `secret` has a live in_progress run. Sweeping
    every id in the org must not reveal that anything is running, nor when it
    last moved."""
    every_id = [world.own.id, world.internal.id, world.shared_to_member.id,
                world.shared_to_group.id, world.secret.id,
                world.shared_to_stranger.id, world.foreign.id]
    result = await _activity(db, world, every_id)

    assert world.secret.id not in _ids(result)
    assert all(row.state == "idle" for row in result["activity"]), (
        "the only running report in this world is one the caller cannot see"
    )
    assert all(row.last_activity_at is None or row.id != world.secret.id
               for row in result["activity"])


# --- 3. ownership is visibility --------------------------------------------

@pytest.mark.asyncio
async def test_a_user_always_sees_their_own_report(db, world):
    """`own` is visibility 'none' on both surfaces and shared with nobody — it
    is reachable ONLY through ownership. A fix that filtered on share/visibility
    alone would hide every user's own reports from their own list badges."""
    result = await _activity(db, world, [world.own.id])
    assert _ids(result) == {world.own.id}


@pytest.mark.asyncio
async def test_the_owners_own_unread_watermark_still_applies(db, world):
    """Ownership decides visibility; it does not short-circuit the per-viewer
    fields the endpoint exists to compute."""
    db.add(ReportView(
        id=str(uuid.uuid4()),
        report_id=world.own.id,
        user_id=world.member.id,
        last_viewed_at=datetime.utcnow() + timedelta(hours=1),
    ))
    await db.commit()
    result = await _activity(db, world, [world.own.id])
    row = next(r for r in result["activity"] if r.id == world.own.id)
    assert row.unread is False


# --- 4. the 100-id cap ------------------------------------------------------

@pytest.mark.asyncio
async def test_only_the_first_hundred_ids_are_considered(db, world):
    """The cap bounds the query, so it has to be applied to the RAW list before
    anything else. A visible id pushed past position 100 must fall off — that
    is what proves the cap is still there rather than that the id was hidden."""
    padding = [str(uuid.uuid4()) for _ in range(100)]
    result = await _activity(db, world, padding + [world.internal.id])
    assert _ids(result) == set()

    result = await _activity(db, world, [world.internal.id] + padding)
    assert _ids(result) == {world.internal.id}


@pytest.mark.asyncio
async def test_duplicates_collapse_before_the_cap_is_counted(db, world):
    """★The cap is applied after de-duplication. If it were not, a caller could
    be denied their own 101st distinct report by repeating one id — and, read
    the other way, a hostile caller could pad the list to push ids around."""
    padding = [str(uuid.uuid4())] * 100
    result = await _activity(db, world, padding + [world.internal.id])
    assert _ids(result) == {world.internal.id}


@pytest.mark.asyncio
async def test_no_ids_is_an_empty_answer_not_a_query(db, world):
    assert await _activity(db, world, []) == {"activity": []}


# ===========================================================================
# STAR and MARK-VIEWED — same bug class, but these two WRITE
# ===========================================================================

async def _star(db, world, report_id, starred=True):
    return await ReportService().set_report_star(
        db, report_id, world.member, world.org, starred=starred
    )


async def _mark_viewed(db, world, report_id):
    return await ReportService().mark_report_viewed(
        db, report_id, world.member, world.org
    )


async def _outcome(db, call):
    """Run `call()` and describe what came back WITHOUT pinning which it was.

    ★The product may legitimately answer an invisible report with a 404 or
    with a silent no-op. Both are safe; choosing between them is not this
    file's business. Returning a comparable token lets a test assert the only
    thing that matters — that an invisible real report and a made-up id are
    answered identically — and survive either choice.
    """
    try:
        return ("returned", await call())
    except HTTPException as exc:
        await db.rollback()
        return ("raised", exc.status_code)


async def _star_rows(db, report_id, user_id) -> int:
    """Every star row for this pair, soft-deleted ones included: the question
    is whether anything was WRITTEN, not whether it is currently active."""
    return (await db.execute(
        select(func.count()).select_from(ReportStar).where(
            ReportStar.report_id == report_id,
            ReportStar.user_id == user_id,
        )
    )).scalar_one()


async def _view_rows(db, report_id, user_id) -> int:
    return (await db.execute(
        select(func.count()).select_from(ReportView).where(
            ReportView.report_id == report_id,
            ReportView.user_id == user_id,
        )
    )).scalar_one()


# --- the write must not happen ---------------------------------------------

@pytest.mark.asyncio
async def test_a_member_cannot_star_a_report_they_cannot_see(db, world):
    """★Asserted at the TABLE. A fix that 404s but still inserts the row would
    pass a response-only test and leave a star on a colleague's private report
    sitting in the attacker's own starred list."""
    await _outcome(db, lambda: _star(db, world, world.secret.id))
    assert await _star_rows(db, world.secret.id, world.member.id) == 0


@pytest.mark.asyncio
async def test_a_member_cannot_mark_viewed_a_report_they_cannot_see(db, world):
    await _outcome(db, lambda: _mark_viewed(db, world, world.secret.id))
    assert await _view_rows(db, world.secret.id, world.member.id) == 0


@pytest.mark.asyncio
async def test_a_share_addressed_to_someone_else_does_not_let_you_star(db, world):
    await _outcome(db, lambda: _star(db, world, world.shared_to_stranger.id))
    assert await _star_rows(db, world.shared_to_stranger.id, world.member.id) == 0


@pytest.mark.asyncio
async def test_another_organizations_report_can_be_neither_starred_nor_viewed(db, world):
    """The org filter that used to be the ONLY check must survive the fix."""
    await _outcome(db, lambda: _star(db, world, world.foreign.id))
    await _outcome(db, lambda: _mark_viewed(db, world, world.foreign.id))
    assert await _star_rows(db, world.foreign.id, world.member.id) == 0
    assert await _view_rows(db, world.foreign.id, world.member.id) == 0


@pytest.mark.asyncio
async def test_unstarring_an_invisible_report_writes_nothing_either(db, world):
    """The unstar path runs the same function with `starred=False` and takes a
    different branch through it. It is gated by the same check or it is not
    gated at all."""
    await _outcome(db, lambda: _star(db, world, world.secret.id, starred=False))
    assert await _star_rows(db, world.secret.id, world.member.id) == 0


# --- the answer must not be an existence oracle ----------------------------

@pytest.mark.asyncio
async def test_starring_an_invisible_report_answers_like_a_made_up_id(db, world):
    """★The disclosure that survives even with the write removed. If a real
    but invisible report answers differently from an id that names nothing,
    the endpoint still confirms which ids exist — one request per guess."""
    invisible = await _outcome(db, lambda: _star(db, world, world.secret.id))
    nonexistent = await _outcome(db, lambda: _star(db, world, str(uuid.uuid4())))
    assert invisible == nonexistent, (
        "a report the caller cannot see is distinguishable from one that does "
        f"not exist: {invisible!r} vs {nonexistent!r}"
    )


@pytest.mark.asyncio
async def test_marking_viewed_an_invisible_report_answers_like_a_made_up_id(db, world):
    invisible = await _outcome(db, lambda: _mark_viewed(db, world, world.secret.id))
    nonexistent = await _outcome(db, lambda: _mark_viewed(db, world, str(uuid.uuid4())))
    assert invisible == nonexistent, (
        f"existence oracle on mark-viewed: {invisible!r} vs {nonexistent!r}"
    )


# --- and the fix must not over-tighten -------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["own", "internal", "shared_to_member", "shared_to_group"])
async def test_every_report_a_member_may_see_can_still_be_starred(db, world, which):
    """★Both docstrings promise "any user who can view it, including reports
    shared with them read-only". Starring is per-user bookkeeping, not an edit
    — a fix that narrowed this to ownership would silently take the star
    button away from every shared report.

    `own` covers the other side: a report visible ONLY through ownership.
    """
    report = getattr(world, which)
    result = await _star(db, world, report.id)
    assert result["is_starred"] is True
    assert await _star_rows(db, report.id, world.member.id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["own", "internal", "shared_to_member", "shared_to_group"])
async def test_every_report_a_member_may_see_can_still_be_marked_viewed(db, world, which):
    report = getattr(world, which)
    await _mark_viewed(db, world, report.id)
    assert await _view_rows(db, report.id, world.member.id) == 1


@pytest.mark.asyncio
async def test_a_star_the_member_may_set_can_still_be_removed(db, world):
    """Star then unstar on a visible report: the gate sits in front of both
    branches, so both have to keep working."""
    await _star(db, world, world.shared_to_member.id)
    result = await _star(db, world, world.shared_to_member.id, starred=False)
    assert result["is_starred"] is False
    row = (await db.execute(
        select(ReportStar).where(
            ReportStar.report_id == world.shared_to_member.id,
            ReportStar.user_id == world.member.id,
        )
    )).scalar_one()
    assert row.deleted_at is not None, "unstar soft-deletes rather than dropping the row"


@pytest.mark.asyncio
async def test_marking_viewed_clears_the_unread_badge_end_to_end(db, world):
    """The two surfaces have to agree: what mark-viewed writes is what the
    activity endpoint reads. Gating one without the other would leave a report
    permanently unread."""
    before = await _activity(db, world, [world.shared_to_member.id])
    assert next(r for r in before["activity"]).unread is True

    await _mark_viewed(db, world, world.shared_to_member.id)

    after = await _activity(db, world, [world.shared_to_member.id])
    assert next(r for r in after["activity"]).unread is False


# ===========================================================================
# THE SSE HUB — the live feed must WITHHOLD, not merely deliver
# ===========================================================================
#
# `report_activity_hub` derives activity once per organization and fans it out
# to that worker's subscribers. Derived per org, delivered per subscriber: the
# fan-out at the bottom of `_tick` intersects every frame with the
# subscriber's own visible report set before queueing it.
#
# ★The existing hub test (`tests/e2e/test_report_activity.py -k watcher`)
# subscribes the report's OWNER. It proves events still FLOW. For a filter,
# that is the easy half — nothing there fails if the intersect is deleted. The
# tests below are the other half: that a subscriber who cannot see a report
# receives nothing, and that an unresolved visible set withholds rather than
# passes.
#
# These drive `_tick` directly. There is exactly one write to a subscriber
# queue in the whole hub (`sub.queue.put_nowait`), asserted below, so the
# HTTP/SSE route adds no path that could bypass the intersect.


@pytest_asyncio.fixture
async def hub(session_factory, monkeypatch):
    """A private hub whose watcher session lands on THIS test's database.

    `_tick` resolves `create_async_session_factory` from
    `app.settings.database` at call time (the import is inside the function),
    so patching the module attribute is enough and no import order matters.

    ★A fresh `ReportActivityHub()` per test, never the module singleton: the
    hub carries per-org signature and liveness caches, and a shared instance
    would let one test's last tick decide whether the next one emits anything.
    """
    import app.settings.database as database

    monkeypatch.setattr(
        database, "create_async_session_factory", lambda *a, **k: session_factory
    )
    return ReportActivityHub()


def _subscriber(hub, org_id: str, user_id: str) -> _HubSubscriber:
    """Register a subscriber without going through `subscribe()`.

    `subscribe()` spawns a background priming task; a test that raced it would
    be reporting the scheduler, not the filter. Registering directly leaves
    `visible_ids` unresolved (None) exactly as a just-connected subscriber has
    it, and lets the tick's own `_refresh_visibility` resolve it for real —
    which is the code path under test.
    """
    sub = _HubSubscriber(str(user_id))
    hub._subs.setdefault(str(org_id), []).append(sub)
    return sub


def _drain(sub) -> list[str]:
    """Report ids this subscriber actually received."""
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait()["report_id"])
    return out


def _force_visibility(sub, ids) -> None:
    """Pin a subscriber's cached visible set and mark it FRESH.

    Fresh matters: `_refresh_visibility` recomputes any subscriber whose cache
    is missing or older than the TTL, which would overwrite exactly the state
    these tests are trying to hold still.
    """
    try:
        sub.visible_ids = ids
        sub.visible_at = datetime.utcnow()
    except AttributeError as exc:  # pragma: no cover - only on an unfixed hub
        pytest.fail(
            "_Subscriber has no visible_ids/visible_at slot, so the hub has no "
            f"per-subscriber visibility at all and fans out to everyone: {exc}"
        )


# --- 1. two subscribers, one report visible only to A ----------------------

@pytest.mark.asyncio
async def test_an_event_reaches_only_the_subscriber_who_can_see_the_report(db, world, hub):
    """★The whole point of the fix. Both reports in Hubco are live, so both
    are in the tick's candidate set and both emit. `hub_viewer` owns one of
    them and may see nothing else; `hub_outsider` may see neither.

    B's queue must be EMPTY — not "missing that one id". Hubco holds no
    public/internal report, so there is nothing B is entitled to and any event
    at all in that queue is a leak.
    """
    a = _subscriber(hub, world.hub_org.id, world.hub_viewer.id)
    b = _subscriber(hub, world.hub_org.id, world.hub_outsider.id)

    await hub._tick(str(world.hub_org.id))

    received_by_a = _drain(a)
    # Sanity first: if A got nothing, the tick emitted nothing and every
    # negative below would pass for the wrong reason.
    assert world.hub_visible.id in received_by_a, (
        "the subscriber who owns the report received nothing — either the tick "
        "emitted nothing at all, or the filter withholds from the person it "
        "exists to serve"
    )
    # THE claim. Ordered ahead of A's negative so that when this regresses, the
    # failure names the subscriber who should have received nothing.
    assert b.queue.empty(), (
        "a subscriber who may see no report in this organization received "
        f"{_drain(b)}"
    )
    assert world.hub_secret.id not in received_by_a, (
        "a subscriber received activity for a report owned by someone else and "
        "shared with nobody"
    )


@pytest.mark.asyncio
async def test_the_filter_is_per_subscriber_not_per_organization(db, world, hub):
    """Two subscribers, same org, same tick, different answers. If the hub
    filtered once per org (or not at all) both queues would match."""
    a = _subscriber(hub, world.hub_org.id, world.hub_viewer.id)
    b = _subscriber(hub, world.hub_org.id, world.hub_owner.id)

    await hub._tick(str(world.hub_org.id))

    assert _drain(a) == [world.hub_visible.id]
    assert _drain(b) == [world.hub_secret.id]


# --- 2. visible_ids still None = fail closed -------------------------------

@pytest.mark.asyncio
async def test_a_subscriber_whose_visibility_never_resolved_receives_nothing(db, world, hub):
    """★The fail-closed path, and the one most likely to be undone later.

    `None` means "we do not yet know what this user may see" — first tick
    after connect, or a resolve that failed. It must withhold. The guard is
    `if not visible: continue`, and `None` and `set()` both have to take it;
    someone "tidying" that into `if visible is None` would silently restore
    the old behaviour for every subscriber with an empty set, and someone
    tightening it to `if visible == set()` would restore it for every
    subscriber mid-connect.
    """
    sub = _subscriber(hub, world.hub_org.id, world.hub_viewer.id)
    _force_visibility(sub, None)
    assert sub.visible_ids is None

    await hub._tick(str(world.hub_org.id))

    assert sub.queue.empty(), (
        "events were delivered to a subscriber whose visible set was never "
        f"resolved: {_drain(sub)}"
    )


@pytest.mark.asyncio
async def test_an_unresolvable_subscriber_stays_closed_across_ticks(db, world, hub):
    """The same fail-closed state reached the way production reaches it: a
    subscriber whose user cannot be resolved (`_visible_ids` returns None), so
    `_refresh_visibility` leaves the cache unset. Two ticks, still nothing —
    it must not "warm up" into delivering."""
    sub = _subscriber(hub, world.hub_org.id, str(uuid.uuid4()))  # no such user

    await hub._tick(str(world.hub_org.id))
    await hub._tick(str(world.hub_org.id))

    assert sub.visible_ids is None, "an unknown user resolved to a visible set"
    assert sub.queue.empty(), f"unresolvable subscriber received {_drain(sub)}"


# --- 3. an empty visible set = nothing -------------------------------------

@pytest.mark.asyncio
async def test_a_subscriber_with_an_empty_visible_set_receives_nothing(db, world, hub):
    """`set()` is a RESOLVED answer — "this user may see no report here" — and
    it is the case a truthiness check gets right by accident and an identity
    check (`is None`) gets wrong."""
    sub = _subscriber(hub, world.hub_org.id, world.hub_viewer.id)
    _force_visibility(sub, set())

    await hub._tick(str(world.hub_org.id))

    assert sub.queue.empty(), (
        f"events were delivered to a subscriber entitled to none: {_drain(sub)}"
    )


@pytest.mark.asyncio
async def test_the_empty_set_is_what_the_product_itself_resolves(db, world, hub):
    """★Not hand-set. `hub_outsider` is a real member of Hubco who owns no
    report and holds no share, so the product's own resolver must return an
    empty set for them — which is what makes the previous test's premise real
    rather than an invented state."""
    ids = await hub._visible_ids(db, str(world.hub_org.id), str(world.hub_outsider.id))
    assert ids == set()

    owner_ids = await hub._visible_ids(db, str(world.hub_org.id), str(world.hub_viewer.id))
    assert owner_ids == {world.hub_visible.id}


# --- the fan-out has exactly one door --------------------------------------

def test_there_is_only_one_way_into_a_subscriber_queue():
    """★These tests drive `_tick` rather than the SSE route, which is only
    sound while `_tick` is the sole writer. A second `put_nowait` — a priming
    path that pushes a snapshot, say — would be a delivery route this file
    never sees.
    """
    src = Path(inspect.getfile(ReportActivityHub)).read_text(encoding="utf-8")
    assert src.count("queue.put_nowait") == 1, (
        "the hub has more than one write into a subscriber queue; every one of "
        "them needs the visibility intersect, and the tests above only cover _tick"
    )
