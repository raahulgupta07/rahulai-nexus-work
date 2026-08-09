"""The three completion-control ENDPOINTS refuse a second workspace member.

Companion to test_completion_control_is_owner_only.py, which drives the service
directly. This one goes over HTTP, because the two layers can disagree and only
the whole stack is what a user meets:

    POST   /api/completions/{id}/sigkill
    POST   /api/completions/{id}/steer
    DELETE /api/completions/{id}/queued

The route decorator cannot supply the gate — these routes take a completion_id,
so `requires_permission` has no Report to resolve, and the permission they name
(`create_reports`) is in the default member baseline, so the second member holds
it and sails through the decorator. Everything that refuses them lives in the
service. Six assertions: a stranger refused on all three, the owner served on
all three.

`test_completion_queue_steer.py` is the pre-existing happy path and only ever
acts as the owner, so it would pass just as happily with the gates deleted.
Nothing else covers this.

The completion row is seeded directly: the API cannot produce an in-progress run
without an LLM in the loop, and steering requires one.
"""
import asyncio
import uuid

import pytest

from app.dependencies import async_session_maker
from app.models.completion import Completion


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org_id)}


def _run(coro):
    return asyncio.run(coro)


async def _seed_completion(report_id, owner_id, *, role="system", status="in_progress"):
    """A completion on `report_id`, owned by `owner_id`."""
    async with async_session_maker() as db:
        c = Completion(
            prompt={"content": "crunch the depot numbers"},
            completion={"content": ""},
            status=status,
            role=role,
            report_id=str(report_id),
            user_id=str(owner_id),
            turn_index=0,
        )
        db.add(c)
        await db.flush()
        await db.commit()
        return str(c.id)


async def _reload(completion_id):
    async with async_session_maker() as db:
        return await db.get(Completion, completion_id)


@pytest.fixture
def two_members(test_client, create_user, login_user, whoami):
    """One org, an owner (the org creator) and a second full member.

    Both hold `create_reports` — that is the point. Same-org membership plus the
    role the endpoint names must NOT be enough to touch someone else's turn.
    """
    suffix = uuid.uuid4().hex[:6]
    owner_email = f"ctl_owner_{suffix}@test.com"
    create_user(email=owner_email, password="test123")
    owner_token = login_user(email=owner_email, password="test123")
    who = whoami(owner_token)
    org_id = who["organizations"][0]["id"]
    owner_id = who["id"]

    member_email = f"ctl_member_{suffix}@test.com"
    test_client.post(
        f"/api/organizations/{org_id}/members",
        json={"organization_id": org_id, "email": member_email, "role": "member"},
        headers=_headers(owner_token, org_id),
    )
    create_user(email=member_email, password="test123")
    member_token = login_user(email=member_email, password="test123")
    member_id = whoami(member_token)["id"]

    return {
        "org_id": org_id,
        "owner_token": owner_token, "owner_id": owner_id,
        "member_token": member_token, "member_id": member_id,
    }


@pytest.mark.e2e
def test_a_second_member_cannot_sigkill_the_owners_run(two_members, create_report, test_client):
    t = two_members
    report = create_report(title="Depot Analysis", user_token=t["owner_token"],
                           org_id=t["org_id"], data_sources=[])
    cid = _run(_seed_completion(report["id"], t["owner_id"]))

    resp = test_client.post(f"/api/completions/{cid}/sigkill",
                            headers=_headers(t["member_token"], t["org_id"]))
    assert resp.status_code == 403, resp.json()

    # The refusal is real, not cosmetic: nothing was stamped.
    row = _run(_reload(cid))
    assert row.sigkill is None and row.status == "in_progress"

    # The owner stops their own run.
    resp = test_client.post(f"/api/completions/{cid}/sigkill",
                            headers=_headers(t["owner_token"], t["org_id"]))
    assert resp.status_code == 200, resp.json()
    row = _run(_reload(cid))
    assert row.sigkill is not None and row.status == "stopped"


@pytest.mark.e2e
def test_a_second_member_cannot_steer_the_owners_run(two_members, create_report, test_client):
    t = two_members
    report = create_report(title="Depot Analysis", user_token=t["owner_token"],
                           org_id=t["org_id"], data_sources=[])
    cid = _run(_seed_completion(report["id"], t["owner_id"]))

    resp = test_client.post(f"/api/completions/{cid}/steer",
                            json={"content": "ignore that, use my numbers"},
                            headers=_headers(t["member_token"], t["org_id"]))
    assert resp.status_code == 403, resp.json()

    # No steering row reached the owner's transcript.
    async def _steering_rows():
        from sqlalchemy import select
        async with async_session_maker() as db:
            return (await db.execute(
                select(Completion).where(Completion.report_id == str(report["id"]),
                                         Completion.message_type == "steering")
            )).scalars().all()
    assert _run(_steering_rows()) == []

    # The owner steers their own run.
    resp = test_client.post(f"/api/completions/{cid}/steer",
                            json={"content": "focus on Q3"},
                            headers=_headers(t["owner_token"], t["org_id"]))
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "steered"


@pytest.mark.e2e
def test_a_second_member_cannot_delete_the_owners_queued_prompt(two_members, create_report, test_client):
    t = two_members
    report = create_report(title="Depot Analysis", user_token=t["owner_token"],
                           org_id=t["org_id"], data_sources=[])
    cid = _run(_seed_completion(report["id"], t["owner_id"], role="user", status="queued"))

    resp = test_client.delete(f"/api/completions/{cid}/queued",
                              headers=_headers(t["member_token"], t["org_id"]))
    assert resp.status_code == 403, resp.json()
    assert _run(_reload(cid)) is not None, "the queued prompt was deleted despite the 403"

    # The owner drops their own.
    resp = test_client.delete(f"/api/completions/{cid}/queued",
                              headers=_headers(t["owner_token"], t["org_id"]))
    assert resp.status_code == 200, resp.json()
    assert _run(_reload(cid)) is None


@pytest.mark.e2e
def test_an_ordinary_member_can_read_the_plan_for_their_own_turn(two_members, create_report, test_client):
    """★The route-level half of the /plans regression, which no service test can
    see: this catches gating the endpoint on a permission members do not hold.

    The member creates their OWN report and turn, so ownership is not in
    question — only the route permission is. Under `manage_settings` (not in
    DEFAULT_MEMBER_PERMISSIONS) this is a 403 on your own reasoning panel.
    """
    t = two_members
    report = create_report(title="My Own Analysis", user_token=t["member_token"],
                           org_id=t["org_id"], data_sources=[])
    member_id = t["member_id"]
    cid = _run(_seed_completion(report["id"], member_id))

    async def _seed_plan():
        from app.models.plan import Plan
        async with async_session_maker() as db:
            p = Plan(content={"reasoning": "own turn"}, completion_id=cid,
                     report_id=str(report["id"]), organization_id=str(t["org_id"]),
                     user_id=str(member_id))
            db.add(p)
            await db.flush()
            await db.commit()
    _run(_seed_plan())

    resp = test_client.get(f"/api/completions/{cid}/plans",
                           headers=_headers(t["member_token"], t["org_id"]))
    assert resp.status_code == 200, resp.json()
    assert resp.json()[0]["content"]["reasoning"] == "own turn"

    # ...and the owner of a different report still cannot read it.
    resp = test_client.get(f"/api/completions/{cid}/plans",
                           headers=_headers(t["owner_token"], t["org_id"]))
    assert resp.status_code == 403, resp.json()


@pytest.mark.e2e
def test_the_stranger_is_refused_before_the_409(two_members, create_report, test_client):
    """Ownership is checked before the 'not queued' 409, so the two responses
    carry no information about a completion the caller may not touch."""
    t = two_members
    report = create_report(title="Depot Analysis", user_token=t["owner_token"],
                           org_id=t["org_id"], data_sources=[])
    queued = _run(_seed_completion(report["id"], t["owner_id"], role="user", status="queued"))
    running = _run(_seed_completion(report["id"], t["owner_id"]))

    codes = [
        test_client.delete(f"/api/completions/{c}/queued",
                           headers=_headers(t["member_token"], t["org_id"])).status_code
        for c in (queued, running)
    ]
    assert codes == [403, 403], f"queued and non-queued must look identical to a stranger, got {codes}"

    # The owner, who may ask, still gets the informative 409.
    resp = test_client.delete(f"/api/completions/{running}/queued",
                              headers=_headers(t["owner_token"], t["org_id"]))
    assert resp.status_code == 409, resp.json()
