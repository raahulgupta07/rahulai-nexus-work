"""The Schedule tab, and the one sentence it exists to say.

A per-user connector has NO timer. It runs against the member's own Microsoft
token, and at 3am there is nobody to borrow that from — so it syncs when they
sign in and at no other moment. Members reliably assume the opposite: they see
"Auto learn" in settings, apply it to the whole screen, and report the Fabric
agent as broken because it did not run overnight.

So this tab must never dress "signs in" up as a schedule, and must never quote a
next-run time it cannot honour. Auto learn IS scheduled, is reported next to it,
and carries its budget — "on" without "12 a day, 3 spent" predicts nothing.

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
from app.models.organization_settings import OrganizationSettings
from app.models.user import User
from app.services.keeper_service import KeeperService

_SEQ = {"n": 0}


async def _org(db):
    _SEQ["n"] += 1
    org = Organization(name=f"Schedule Org {_SEQ['n']}")
    db.add(org)
    await db.flush()
    return org


async def _member(db):
    _SEQ["n"] += 1
    user = User(
        name="member",
        email=f"sched-{_SEQ['n']}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _agent(db, org, conn_type, name, is_public=True):
    _SEQ["n"] += 1
    conn = Connection(
        organization_id=str(org.id), name=f"Conn {_SEQ['n']}", type=conn_type, config={},
    )
    db.add(conn)
    ds = DataSource(name=name, organization_id=str(org.id), is_public=is_public)
    db.add(ds)
    await db.flush()
    await db.execute(insert(domain_connection).values(
        data_source_id=str(ds.id), connection_id=str(conn.id),
    ))
    return ds


@pytest.mark.asyncio
async def test_a_microsoft_agent_is_reported_as_signin_not_as_a_schedule():
    """★The sentence the tab exists for. `fabric_user` has no timer at all."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await _agent(db, org, "fabric_user", "Fabric")
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    entry = next(a for a in payload["agents"] if a["name"] == "Fabric")
    assert entry["runs_when"] == "signin"
    assert payload["per_user_count"] == 1
    # No next-run time anywhere. There is none, and inventing one would be a
    # lie a member could plan around.
    assert "next_run_at" not in entry


@pytest.mark.asyncio
async def test_a_shared_connector_is_not_called_a_signin_agent():
    """Only the two per-user-token types. A shared Postgres agent syncs on the
    server's own terms and must not inherit the sign-in wording."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await _agent(db, org, "postgresql", "Warehouse")
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    entry = next(a for a in payload["agents"] if a["name"] == "Warehouse")
    assert entry["runs_when"] == "auto_learn"
    assert payload["per_user_count"] == 0


@pytest.mark.asyncio
async def test_both_kinds_are_told_apart_on_one_screen():
    """The mixed case is the real one, and the one a single global sentence at
    the top of the tab would get wrong."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await _agent(db, org, "powerbi_user", "Power BI")
        await _agent(db, org, "bigquery", "BigQuery")
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    by_name = {a["name"]: a["runs_when"] for a in payload["agents"]}
    assert by_name == {"Power BI": "signin", "BigQuery": "auto_learn"}
    assert payload["per_user_count"] == 1


@pytest.mark.asyncio
async def test_auto_learn_reports_its_budget_not_just_its_switch():
    """"On" is not a prediction. A member who has spent the day's budget sees
    nothing happen and concludes the feature is broken."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        db.add(OrganizationSettings(
            organization_id=str(org.id),
            config={"auto_learn": {
                "enabled": True, "quiet_minutes": 45, "max_runs_per_day": 7,
            }},
        ))
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    auto = payload["auto_learn"]
    assert auto["enabled"] is True
    assert auto["quiet_minutes"] == 45
    assert auto["max_runs_per_day"] == 7
    assert auto["runs_today"] == 0
    assert auto["sweep_every_minutes"] > 0


@pytest.mark.asyncio
async def test_auto_learn_defaults_to_off_when_nothing_is_configured():
    """Matches `auto_learn.org_policy`, which fails closed on purpose — an
    unreadable settings row must not start spending model calls."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    assert payload["auto_learn"]["enabled"] is False


@pytest.mark.asyncio
async def test_a_member_who_can_see_nothing_gets_an_empty_list_not_an_error():
    """The scope rule applies here as it does everywhere else in this service:
    the agent list is built from what the member may SEE."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        # ★Private from the start. Creating it public and then UPDATEing can
        # leave the identity-mapped object in this session still reporting
        # is_public=True, so the re-query inside `_visible_scope` hands back the
        # stale one — and the test passes an agent it believes it hid.
        await _agent(db, org, "fabric_user", "Private", is_public=False)
        await db.commit()
        payload = await KeeperService().schedule(db, user, org)

    assert payload["agents"] == []
    assert payload["per_user_count"] == 0
    # Still a well-formed answer — the tab renders, it just has nothing to list.
    assert "auto_learn" in payload
