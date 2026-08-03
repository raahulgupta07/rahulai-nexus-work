"""One button, every agent — and why they must not run at the same time.

Each of these syncs crawls hundreds of Microsoft endpoints on ONE member's
token, against a per-user rate limit every one of them shares. Starting five at
once does not finish five times sooner; it makes all five slower and throttles
some of them into failures that read as "the sync is broken" rather than "we
asked too fast". So the button queues.

The other half is what it says about the agents it did NOT start. "Queued 2 of
5" with no reason is the shape that gets reported as data loss — every skip
carries why, and neither skip is treated as an error.

★These need a schema, so they live here and NOT in `tests/unit/fork` — see
CLAUDE.md.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import insert

from app.dependencies import async_session_maker
from app.models.connection import Connection
from app.models.data_source import DataSource
from app.models.domain_connection import domain_connection
from app.models.organization import Organization
from app.models.user import User
from app.models.user_data_source_credentials import UserDataSourceCredentials
from app.services import connection_sync_progress as prog
from app.services import keeper_actions

_SEQ = {"n": 0}


async def _org(db):
    _SEQ["n"] += 1
    org = Organization(name=f"SyncAll Org {_SEQ['n']}")
    db.add(org)
    await db.flush()
    return org


async def _member(db):
    _SEQ["n"] += 1
    user = User(
        name="member",
        email=f"syncall-{_SEQ['n']}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _agent(db, org, name, conn_type="fabric_user", is_public=True):
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


async def _connect(db, ds, user, *, refresh_token="rt-abc"):
    row = UserDataSourceCredentials(
        data_source_id=str(ds.id),
        user_id=str(user.id),
        organization_id=str(ds.organization_id),
        auth_mode="user_token",
        is_active=True,
    )
    row.encrypt_credentials({"refresh_token": refresh_token})
    db.add(row)
    await db.flush()
    return row


# ────────────────────────── what gets queued ─────────────────────────────


@pytest.mark.asyncio
async def test_a_connected_agent_is_queued():
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds = await _agent(db, org, "Fabric")
        await _connect(db, ds, user)
        await db.commit()

        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert [a["name"] for a in runnable] == ["Fabric"]
    assert skipped == []


@pytest.mark.asyncio
async def test_an_agent_with_no_microsoft_account_is_skipped_with_a_reason():
    """★Not an error, and not silence. The member has simply never connected
    this one, and saying so is what turns "nothing happened" into an action."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await _agent(db, org, "Never connected")
        await db.commit()

        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert runnable == []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "not_connected"
    assert skipped[0]["name"] == "Never connected"


@pytest.mark.asyncio
async def test_a_stored_credential_that_cannot_refresh_is_treated_as_unconnected():
    """A row exists but carries no refresh token, so a sync could only fail.
    Queueing it would spend the member's time proving that."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds = await _agent(db, org, "Stale")
        row = UserDataSourceCredentials(
            data_source_id=str(ds.id), user_id=str(user.id),
            organization_id=str(org.id), auth_mode="user_token", is_active=True,
        )
        row.encrypt_credentials({"access_token": "only-this"})
        db.add(row)
        await db.commit()

        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert runnable == []
    assert skipped[0]["reason"] == "not_connected"


@pytest.mark.asyncio
async def test_an_agent_already_syncing_is_skipped_not_restarted():
    """★Starting a second crawl over a running one is the double-crawl the
    per-connector 409 exists to prevent. This button must not route around it."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds = await _agent(db, org, "Busy")
        await _connect(db, ds, user)
        await db.commit()

    await prog.start(str(ds.id), str(user.id), trigger="manual")

    async with async_session_maker() as db:
        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert runnable == []
    assert skipped[0]["reason"] == "already_running"


@pytest.mark.asyncio
async def test_a_shared_connector_is_not_queued_at_all():
    """This button drives the per-user Microsoft crawl. A Postgres agent has no
    member token to run under and nothing here to start."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds = await _agent(db, org, "Warehouse", conn_type="postgresql")
        await _connect(db, ds, user)
        await db.commit()

        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert runnable == []
    assert skipped == [], "a shared agent is out of scope, not a skipped one"


@pytest.mark.asyncio
async def test_an_agent_the_member_cannot_see_is_never_queued():
    """★The scope rule. This action must not become a second, weaker answer to
    "which agents are yours" than every read on the same screen uses."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        # ★Private from the start, not made private by an UPDATE. An UPDATE
        # leaves the identity-mapped object in this session still reporting
        # is_public=True, so the re-query inside `_visible_scope` returns the
        # stale one and the test passes an agent it thinks it hid.
        ds = await _agent(db, org, "Private", is_public=False)
        await _connect(db, ds, user)
        await db.commit()

        runnable, skipped = await keeper_actions._eligible(db, user, org)

    assert runnable == [] and skipped == []


@pytest.mark.asyncio
async def test_the_response_describes_the_queue_and_never_errors_when_empty():
    """★200 with an empty queue, not a 4xx. "They are all already syncing" is a
    normal answer, and a red toast there would be wrong."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        await db.commit()
        payload = await keeper_actions.sync_all(db, user, org)

    assert payload == {"queued": [], "skipped": []}


# ─────────────────────────── one at a time ───────────────────────────────


@pytest.mark.asyncio
async def test_the_queue_runs_them_in_sequence_not_in_parallel(monkeypatch):
    """★The whole point. Records the high-water mark of concurrent runs; if the
    drain ever fans out, `peak` is 2 and this fails."""
    live = {"now": 0, "peak": 0}

    async def _fake_run_one(agent, user_id, organization_id):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1

    async def _settled(*a, **k):
        return {"status": "completed"}

    monkeypatch.setattr(keeper_actions, "_run_one", _fake_run_one)
    monkeypatch.setattr(prog, "get", _settled)

    agents = [{"data_source_id": f"ds{i}", "name": f"A{i}", "type": "fabric_user",
               "refresh_token": "rt"} for i in range(4)]
    await keeper_actions._drain(agents, "user-1", "org-1")

    assert live["peak"] == 1, f"{live['peak']} syncs ran at once — this is a queue"


@pytest.mark.asyncio
async def test_one_agent_failing_does_not_take_the_rest_of_the_queue(monkeypatch):
    started = []

    async def _fake_run_one(agent, user_id, organization_id):
        started.append(agent["data_source_id"])
        if agent["data_source_id"] == "ds1":
            raise RuntimeError("boom")

    async def _settled(*a, **k):
        return {"status": "completed"}

    monkeypatch.setattr(keeper_actions, "_run_one", _fake_run_one)
    monkeypatch.setattr(prog, "get", _settled)

    agents = [{"data_source_id": f"ds{i}", "name": f"A{i}", "type": "fabric_user",
               "refresh_token": "rt"} for i in range(3)]
    with pytest.raises(RuntimeError):
        # `_drain` does not swallow — `_run_one` does, and this proves the real
        # `_run_one` is what makes the queue survivable rather than a `try` in
        # the loop that would also hide a genuine bug in the drain itself.
        await keeper_actions._drain(agents, "user-1", "org-1")

    assert started == ["ds0", "ds1"]


@pytest.mark.asyncio
async def test_the_real_run_one_swallows_a_failure_and_closes_the_record():
    """★A crawl that dies without recording anything would leave a run open for
    the abandoned sweep to find half an hour later, and the screen would show it
    spinning the whole time."""
    async with async_session_maker() as db:
        org = await _org(db)
        user = await _member(db)
        ds = await _agent(db, org, "Doomed")
        await db.commit()

    agent = {"data_source_id": str(ds.id), "name": "Doomed",
             "type": "fabric_user", "refresh_token": None}
    # `_run_federated_sync` on a data source with no credentials raises inside.
    await keeper_actions._run_one(agent, str(user.id), str(org.id))  # must not raise

    from app.core.progress_status import is_running
    state = await prog.get(str(ds.id), str(user.id))
    assert not is_running(state.get("status")), (
        "the run was left open — the screen would spin until the sweep found it"
    )


@pytest.mark.asyncio
async def test_a_queued_run_is_recorded_as_manual_not_as_a_signin():
    """★The trigger column exists for exactly this. The per-connector kickoffs
    hardcode "signin" because that is what calls them; a run started from this
    button is a different thing, and the history can only say so if this does."""
    import inspect

    # Only the CALL, not the whole function — the comment above it names the
    # other constant on purpose, and a naive substring scan reads that as the
    # bug it is documenting.
    source = inspect.getsource(keeper_actions._run_one)
    calls = [ln for ln in source.splitlines()
             if "prog.start(" in ln or "TRIGGER_" in ln and not ln.strip().startswith("#")]
    started = next(ln for ln in calls if "prog.start(" in ln)
    assert "TRIGGER_MANUAL" in started
    assert "TRIGGER_SIGNIN" not in started
