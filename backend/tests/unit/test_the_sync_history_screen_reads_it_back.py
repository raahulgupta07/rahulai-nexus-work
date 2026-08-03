"""What the sync-history screen is allowed to see, and what it says.

`sync_runs` writes the history; this is the half that reads it. Two things are
being held down here, and only one of them is about display.

**Scope.** A Fabric run reports what ONE member's Microsoft account could reach —
their workspace names, their lakehouses, their failures. Two members on the same
agent legitimately see different things. So a run belongs to the member who
caused it and to nobody else, and an agent a member cannot see contributes
nothing at all. Both rules are enforced inside the service by construction
rather than by a filter at the end, and the tests below are what stop that from
quietly regressing into "query everything, then remember to filter".

**Wording.** A sync that returned four lakehouses out of five is not a success
and not a failure, and the screen has to be able to say so. `needs_a_person`
carries the other half of that: our own outage is deliberately absent from it,
because the 2026-08-03 incident's actual harm was telling a member to go and fix
a credential that was working perfectly.

★These need a schema, so they live here and NOT in `tests/unit/fork` — see
CLAUDE.md.
"""
from __future__ import annotations

import pytest
from sqlalchemy import insert

from app.dependencies import async_session_maker
from app.models.connection import Connection
from app.models.data_source import DataSource
from app.models.domain_connection import domain_connection
from app.models.organization import Organization
from app.models.user import User
from app.services import connection_sync_progress as prog
from app.services.keeper_service import KeeperService


_SEQ = {"n": 0}


async def _org(db, label="Keeper"):
    _SEQ["n"] += 1
    org = Organization(name=f"{label} Org {_SEQ['n']}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, label="member"):
    _SEQ["n"] += 1
    user = User(
        name=label,
        email=f"{label}-{_SEQ['n']}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _agent(db, org, name=None, is_public=True):
    """A data source wired to a connection. Public by default so the visibility
    filter lets a plain member see it — the scope tests below turn that off."""
    _SEQ["n"] += 1
    n = _SEQ["n"]
    conn = Connection(
        organization_id=str(org.id), name=f"Conn {n}", type="fabric_user", config={},
    )
    db.add(conn)
    ds = DataSource(
        name=name or f"Agent {n}",
        organization_id=str(org.id),
        is_public=is_public,
    )
    db.add(ds)
    await db.flush()
    await db.execute(insert(domain_connection).values(
        data_source_id=str(ds.id), connection_id=str(conn.id),
    ))
    await db.commit()
    return ds, conn


async def _sync(ds, user, *, workspaces, tables, trigger="signin"):
    """Drive one complete sync. `workspaces` is [(name, tables, error)]."""
    ds_id, uid = str(ds.id), str(user.id)
    await prog.start(ds_id, uid, trigger=trigger)
    await prog.set_endpoints(ds_id, uid, [{"database": w[0]} for w in workspaces])
    for name, count, error in workspaces:
        await prog.endpoint_done(ds_id, uid, name, tables=count, error=error)
    await prog.finish(ds_id, uid, tables=tables)


async def _failed_sync(ds, user, message, error_kind=None):
    await prog.start(str(ds.id), str(user.id), trigger="signin")
    await prog.fail(str(ds.id), str(user.id), message, error_kind=error_kind)


# ──────────────────────────── scope ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_never_sees_another_members_run():
    """★The rule the whole design turns on. Both members synced the same agent;
    each sync reports what THEIR Microsoft account could reach."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, "alice")
        bob = await _member(db, "bob")
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(ds, alice, workspaces=[("AliceLakehouse", 5, None)], tables=5)
    await _sync(ds, bob, workspaces=[("BobLakehouse", 9, None)], tables=9)

    async with async_session_maker() as db:
        seen = await KeeperService().activity(db, alice, org)

    assert seen["total"] == 1
    assert seen["items"][0]["tables"] == 5
    detail = None
    async with async_session_maker() as db:
        detail = await KeeperService().run_detail(
            db, alice, org, seen["items"][0]["id"]
        )
    names = [w["name"] for w in detail["workspaces"]]
    assert names == ["AliceLakehouse"], (
        "another member's workspace names must not appear in this member's history"
    )


@pytest.mark.asyncio
async def test_a_run_on_an_invisible_agent_is_not_listed():
    """The agent is private and this member is not on it, so its runs are not
    theirs to read — even though the run row itself carries their user id."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        public_ds, _ = await _agent(db, org, name="Shared", is_public=True)
        private_ds, _ = await _agent(db, org, name="Private", is_public=False)
        await db.commit()

    await _sync(public_ds, user, workspaces=[("Open", 2, None)], tables=2)
    await _sync(private_ds, user, workspaces=[("Closed", 7, None)], tables=7)

    async with async_session_maker() as db:
        seen = await KeeperService().activity(db, user, org)

    assert [i["data_source_name"] for i in seen["items"]] == ["Shared"]


@pytest.mark.asyncio
async def test_another_members_run_is_a_404_not_a_403():
    """Distinguishing "not yours" from "does not exist" would confirm that a run
    exists to someone not entitled to know it does."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, "alice")
        bob = await _member(db, "bob")
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(ds, alice, workspaces=[("Sales", 3, None)], tables=3)
    async with async_session_maker() as db:
        alices = await KeeperService().activity(db, alice, org)
        run_id = alices["items"][0]["id"]
        assert await KeeperService().run_detail(db, bob, org, run_id) is None
        assert await KeeperService().run_detail(db, alice, org, "no-such-run") is None


# ─────────────────────── what a run is called ────────────────────────────


@pytest.mark.asyncio
async def test_a_sync_that_missed_a_workspace_is_called_partial():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(
        ds, user,
        workspaces=[("Sales", 12, None), ("HR", 0, "login timeout expired")],
        tables=12,
    )

    async with async_session_maker() as db:
        item = (await KeeperService().activity(db, user, org))["items"][0]

    assert item["result"] == "partial", (
        "calling this 'completed' hides that a lakehouse was missed; calling it "
        "'failed' tells a member their working agent is broken"
    )
    assert item["workspaces_failed"] == 1
    assert item["tables"] == 12
    assert item["trigger"] == "signin"


@pytest.mark.asyncio
async def test_problems_only_keeps_the_partial_success():
    """★The case the filter exists for. A sync that quietly returned four of
    five lakehouses is a problem, and its status is `completed`."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(ds, user, workspaces=[("Sales", 3, None)], tables=3)
    await _sync(
        ds, user,
        workspaces=[("Sales", 3, None), ("HR", 0, "access denied")],
        tables=3,
    )

    async with async_session_maker() as db:
        everything = await KeeperService().activity(db, user, org)
        problems = await KeeperService().activity(db, user, org, problems_only=True)

    assert everything["total"] == 2
    assert problems["total"] == 1
    assert problems["items"][0]["result"] == "partial"


@pytest.mark.asyncio
async def test_the_run_detail_carries_the_workspaces_and_the_log():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(
        ds, user,
        workspaces=[("Sales", 12, None), ("HR", 0, "login timeout expired")],
        tables=12,
    )

    async with async_session_maker() as db:
        listed = await KeeperService().activity(db, user, org)
        detail = await KeeperService().run_detail(db, user, org, listed["items"][0]["id"])

    by_name = {w["name"]: w for w in detail["workspaces"]}
    assert by_name["Sales"]["tables"] == 12
    assert by_name["HR"]["status"] == "failed"
    assert any("HR" in e["message"] for e in detail["events"])


# ───────────────────────── needs a person ────────────────────────────────


@pytest.mark.asyncio
async def test_our_own_outage_is_not_something_the_member_must_fix():
    """★The 2026-08-03 harm, as an assertion. Our Postgres refused connections;
    the member was told to attach or refresh their lakehouse. An infrastructure
    failure is ours, the retry is already scheduled, and putting it on a list
    headed 'needs a person' is the same wrong advice in a new place."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _failed_sync(
        ds, user,
        "We could not reach our own database while syncing.",
        error_kind="infrastructure",
    )

    async with async_session_maker() as db:
        overview = await KeeperService().overview(db, user, org)

    assert overview["needs_a_person"] == []
    assert overview["today"]["failed"] == 1, (
        "it is still reported as a failure — hidden is not the same as excused"
    )


@pytest.mark.asyncio
async def test_a_failure_the_member_can_act_on_is_surfaced():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _failed_sync(
        ds, user,
        "Microsoft rejected the sign-in — the account no longer has access.",
        error_kind="source",
    )

    async with async_session_maker() as db:
        overview = await KeeperService().overview(db, user, org)

    assert len(overview["needs_a_person"]) == 1
    item = overview["needs_a_person"][0]
    assert item["kind"] == "last_sync_failed"
    assert item["data_source_id"] == str(ds.id)
    assert "Microsoft" in item["detail"]


@pytest.mark.asyncio
async def test_a_failure_that_has_since_been_fixed_stops_being_reported():
    """Only the LATEST run decides. A member who fixed their access should not
    keep being asked to fix it."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _failed_sync(ds, user, "Access denied.", error_kind="source")
    await _sync(ds, user, workspaces=[("Sales", 4, None)], tables=4)

    async with async_session_maker() as db:
        overview = await KeeperService().overview(db, user, org)

    assert overview["needs_a_person"] == []


@pytest.mark.asyncio
async def test_one_bad_workspace_run_is_a_blip_and_two_is_a_pattern():
    """One miss is noise — an expired token, a lakehouse mid-deploy. Surfacing
    it trains members to ignore the list."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org)
        await db.commit()

    await _sync(
        ds, user,
        workspaces=[("Sales", 3, None), ("HR", 0, "access denied")],
        tables=3,
    )
    async with async_session_maker() as db:
        assert (await KeeperService().overview(db, user, org))["needs_a_person"] == []

    await _sync(
        ds, user,
        workspaces=[("Sales", 3, None), ("HR", 0, "access denied")],
        tables=3,
    )
    async with async_session_maker() as db:
        flagged = (await KeeperService().overview(db, user, org))["needs_a_person"]

    assert len(flagged) == 1
    assert flagged[0]["kind"] == "workspace_repeatedly_missed"


# ──────────────────────────── overview ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_that_has_never_synced_is_shown_as_such():
    """An empty row is a fact, not a gap to hide — it is the difference between
    'nothing happened today' and 'this was never connected'."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org, name="Untouched")
        await db.commit()

        overview = await KeeperService().overview(db, user, org)

    agent = next(a for a in overview["agents"] if a["name"] == "Untouched")
    assert agent["never_synced"] is True
    assert agent["last_run"] is None
    assert agent["last_success_at"] is None


@pytest.mark.asyncio
async def test_the_overview_counts_today_and_names_what_is_running():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds, conn = await _agent(db, org, name="Fabric")
        other, _ = await _agent(db, org, name="PowerBI")
        await db.commit()

    await _sync(ds, user, workspaces=[("Sales", 12, None)], tables=12)
    await _failed_sync(other, user, "Access denied.", error_kind="source")
    # Left mid-flight on purpose.
    await prog.start(str(ds.id), str(user.id), trigger="manual")

    async with async_session_maker() as db:
        overview = await KeeperService().overview(db, user, org)

    assert overview["today"]["runs"] == 3
    assert overview["today"]["completed"] == 1
    assert overview["today"]["failed"] == 1
    assert overview["today"]["tables"] == 12
    assert len(overview["working_now"]) == 1
    assert overview["working_now"][0]["data_source_name"] == "Fabric"
    assert overview["working_now"][0]["trigger"] == "manual"


@pytest.mark.asyncio
async def test_agents_with_a_problem_sort_to_the_top():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        healthy, _ = await _agent(db, org, name="AAA Healthy")
        broken, _ = await _agent(db, org, name="ZZZ Broken")
        await db.commit()

    await _sync(healthy, user, workspaces=[("Sales", 3, None)], tables=3)
    await _failed_sync(broken, user, "Access denied.", error_kind="source")

    async with async_session_maker() as db:
        overview = await KeeperService().overview(db, user, org)

    assert overview["agents"][0]["name"] == "ZZZ Broken", (
        "alphabetical would bury the one thing on this screen that needs doing"
    )


@pytest.mark.asyncio
async def test_activity_can_be_narrowed_to_one_agent_and_paged():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        a, _ = await _agent(db, org, name="Alpha")
        b, _ = await _agent(db, org, name="Beta")
        await db.commit()

    for _ in range(3):
        await _sync(a, user, workspaces=[("Sales", 1, None)], tables=1)
    await _sync(b, user, workspaces=[("Ops", 1, None)], tables=1)

    async with async_session_maker() as db:
        svc = KeeperService()
        only_a = await svc.activity(db, user, org, data_source_id=str(a.id))
        page = await svc.activity(db, user, org, data_source_id=str(a.id), limit=2)
        page2 = await svc.activity(db, user, org, data_source_id=str(a.id), limit=2, offset=2)

    assert only_a["total"] == 3
    assert {i["data_source_name"] for i in only_a["items"]} == {"Alpha"}
    assert len(page["items"]) == 2
    assert len(page2["items"]) == 1
    assert page2["total"] == 3, "total counts the whole result, not the page"


@pytest.mark.asyncio
async def test_filtering_by_an_agent_you_cannot_see_returns_nothing():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        hidden, _ = await _agent(db, org, name="Hidden", is_public=False)
        await db.commit()

    await _sync(hidden, user, workspaces=[("Secret", 5, None)], tables=5)

    async with async_session_maker() as db:
        seen = await KeeperService().activity(db, user, org, data_source_id=str(hidden.id))

    assert seen == {"items": [], "total": 0, "limit": 50, "offset": 0}
