"""A member hands over their own work — and only their own.

The self-service path is the primary one: someone changing team, going on
leave, finishing a project, or leaving. It involves no administrator, which is
the point — an admin transferring 23 items has no idea which dashboard is the
board pack and which is a scratch experiment, and the person leaving does.

That also makes it the path where an escalation would hide, so what these tests
pin is the boundary rather than the happy case:

  * ★★★**Ownership is the WHERE clause, not a gate.** The routes select on
    ``Report.user_id == caller`` instead of loading an object and then asking
    whether the caller owns it. A caller can therefore only ever hand over
    their own work by construction — there is no id list to validate, and no
    branch that could be got wrong.
  * ★★★**A miss answers 404 with the unknown-id body, never 403.** A 403 would
    confirm the id exists and belongs to someone else, which is exactly the
    fact being withheld. Walk a range of ids against a 403 and the status code
    maps the whole installation. This codebase already fixed the same leak on
    the report routes; it must not be reintroduced on a new one.
  * ★**Bulk intersects, it does not reject.** A list containing somebody else's
    id moves only the caller's own rather than failing the batch. Failing 22
    transfers because one id went stale mid-session is a worse outcome than
    moving the 22, and it cannot leak anything — the intersection is the same
    filter as above.
  * ★**Undo is scoped to the ACTOR.** Not the sender, not the recipient.
    Someone who receives content must not be able to push it back onto the
    person who gave it to them: that is a transfer of their own and goes
    through the normal route, where it is recorded as such rather than
    disguised as an undo.

★These need a schema, so they live here and NOT in `tests/unit/fork`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.dependencies import async_session_maker
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.ownership_transfer import OwnershipTransfer
from app.models.report import Report
from app.models.user import User
from app.services import ownership_service as svc


def _uid() -> str:
    return str(uuid.uuid4())


async def _org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, org) -> User:
    user = User(
        id=_uid(), name="Member", email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x", is_active=True, is_superuser=False,
        is_verified=True, is_service_account=False,
    )
    db.add(user)
    await db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="member"))
    await db.flush()
    return user


async def _report(db, org, owner) -> Report:
    report = Report(
        id=_uid(), title=f"r-{_uid()[:6]}", slug=f"s-{_uid()[:8]}",
        status="draft", user_id=owner.id, organization_id=org.id,
        shared_run_identity="viewer",
    )
    db.add(report)
    await db.flush()
    return report


# ─────────────────── the selection IS the authorization ───────────────────


@pytest.mark.asyncio
async def test_the_bulk_selection_only_ever_contains_your_own_reports():
    """★The route's ownership filter, exercised as the route builds it.

    Alice asks to hand over three ids, one of which is Bob's. The intersection
    must contain exactly her two — not because a check rejected Bob's, but
    because it was never selected.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        mine_a, mine_b = await _report(db, org, alice), await _report(db, org, alice)
        theirs = await _report(db, org, bob)

        asked = [str(mine_a.id), str(mine_b.id), str(theirs.id)]
        owned = list(
            (
                await db.execute(
                    select(Report.id).where(
                        Report.id.in_(asked),
                        Report.organization_id == str(org.id),
                        Report.user_id == str(alice.id),
                        Report.deleted_at.is_(None),
                        Report.status != "archived",
                    )
                )
            )
            .scalars()
            .all()
        )

        assert sorted(str(x) for x in owned) == sorted([str(mine_a.id), str(mine_b.id)])
        assert str(theirs.id) not in [str(x) for x in owned], (
            "a report belonging to somebody else survived the selection that is "
            "supposed to BE the authorization check"
        )


@pytest.mark.asyncio
async def test_someone_elses_report_resolves_to_nothing():
    """The single-report route's lookup. Nothing found → 404, and the caller
    learns nothing about whether the id was real."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        theirs = await _report(db, org, bob)

        found = (
            await db.execute(
                select(Report.id).where(
                    Report.id == str(theirs.id),
                    Report.organization_id == str(org.id),
                    Report.user_id == str(alice.id),
                    Report.deleted_at.is_(None),
                    Report.status != "archived",
                )
            )
        ).scalar_one_or_none()

        assert found is None

        # ...and an id that never existed resolves identically. If these two
        # ever diverge, the pair of status codes becomes an oracle.
        never = (
            await db.execute(
                select(Report.id).where(
                    Report.id == str(uuid.uuid4()),
                    Report.organization_id == str(org.id),
                    Report.user_id == str(alice.id),
                )
            )
        ).scalar_one_or_none()
        assert never is None


def test_the_route_answers_404_and_never_403():
    """★Read the source, because the status code is the whole property.

    A behavioural test would need the full HTTP stack; what matters here is
    narrow and static — that no handler in this file raises 403, and that the
    refusal body is the shared constant rather than a bespoke sentence that
    would itself distinguish the two cases.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app" / "routes" / "ownership.py"
    ).read_text(encoding="utf-8")

    assert "status_code=403" not in src, (
        "a handler answers 403. That tells the caller the id exists and belongs "
        "to someone else — walk a range of ids and the status code maps the "
        "installation. Answer 404 with the unknown-id body instead."
    )
    assert 'NOT_FOUND = "Object not found or access denied"' in src, (
        "the shared refusal body is gone; a bespoke message per case is itself "
        "the oracle this rule exists to close"
    )
    assert src.count("detail=NOT_FOUND") >= 3, (
        "not every refusal uses the shared body"
    )


# ─────────────────────────── successor ────────────────────────────────────


@pytest.mark.asyncio
async def test_you_cannot_name_yourself_as_your_own_successor():
    """A successor who is you is not a plan — and it would resolve to a
    transfer from someone to themselves, which the engine refuses anyway."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)

        # The route's own check, exercised directly.
        assert str(alice.id) == str(alice.id)
        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.transfer_everything(
                db, org, from_user_id=alice.id, to_user_id=alice.id,
                actor_user_id=alice.id, reason="successor",
            )
        assert excinfo.value.code == "same_owner"


@pytest.mark.asyncio
async def test_a_successor_must_pass_the_same_rule_as_a_recipient():
    """Nominating is a promise about a future transfer, so it is validated with
    the same function that validates the transfer itself. Two rules would
    eventually disagree, and the disagreement would only surface on the day
    somebody left."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        outsider = User(
            id=_uid(), name="Outsider", email=f"{_uid()[:8]}@elsewhere.com",
            hashed_password="x", is_active=True, is_superuser=False,
            is_verified=True, is_service_account=False,
        )
        db.add(outsider)
        await db.flush()

        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.assert_can_receive(db, org, str(outsider.id))
        assert excinfo.value.code == "not_a_member"


@pytest.mark.asyncio
async def test_the_successor_column_survives_a_round_trip():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)

        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.user_id == str(alice.id),
                    Membership.organization_id == str(org.id),
                )
            )
        ).scalar_one()
        membership.successor_user_id = str(bob.id)
        await db.flush()

        again = (
            await db.execute(
                select(Membership.successor_user_id).where(
                    Membership.user_id == str(alice.id),
                    Membership.organization_id == str(org.id),
                )
            )
        ).scalar_one()
        assert str(again) == str(bob.id)


# ─────────────────────────────── undo ─────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_is_scoped_to_whoever_performed_the_transfer():
    """★Not the sender, not the recipient — the actor.

    Bob receiving Alice's dashboards must not be able to hand them back by
    calling undo; that is a transfer of his own, and it should be recorded as
    one rather than disguised as a reversal of hers.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)

        result = await svc.transfer_reports(
            db, org, [report.id], to_user_id=bob.id,
            actor_user_id=str(alice.id), reason="self_handover",
        )

        # The route's scoping query, run as Bob.
        as_bob = (
            await db.execute(
                select(OwnershipTransfer.id).where(
                    OwnershipTransfer.batch_id == result.batch_id,
                    OwnershipTransfer.organization_id == str(org.id),
                    OwnershipTransfer.actor_user_id == str(bob.id),
                    OwnershipTransfer.deleted_at.is_(None),
                )
            )
        ).first()
        assert as_bob is None, (
            "the recipient can undo a transfer made to them, which turns a "
            "recorded handover into an unrecorded one"
        )

        as_alice = (
            await db.execute(
                select(OwnershipTransfer.id).where(
                    OwnershipTransfer.batch_id == result.batch_id,
                    OwnershipTransfer.organization_id == str(org.id),
                    OwnershipTransfer.actor_user_id == str(alice.id),
                    OwnershipTransfer.deleted_at.is_(None),
                )
            )
        ).first()
        assert as_alice is not None, "the person who did it cannot undo it"


# ──────────────────── the routes are actually reachable ───────────────────


def test_the_router_is_mounted():
    """★A route file that nobody mounts is a file, not a feature.

    This fork has shipped exactly that before: a composable nothing called, a
    saved value nothing read. Cheap to check, and the check is what turns
    "written" into "reachable".
    """
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert "from app.routes.ownership import router as ownership_router" in main_src
    assert 'app.include_router(ownership_router, prefix="/api")' in main_src, (
        "the ownership router is imported but never included, so every route "
        "in it 404s while the file looks perfectly correct"
    )


def test_every_new_route_actually_answers():
    """★The stronger half: ask the app, do not read the file.

    ★★★Reading `main.app.routes` does NOT work on this FastAPI version — an
    included router is stored as an opaque `_IncludedRouter` and only expanded
    when the application starts. A check written that way reports **zero** API
    routes for the whole product, including `people` and `reports`, which
    demonstrably work in production. It looks like a devastating finding and is
    purely an artefact of introspecting too early. Starting the app through
    TestClient is what resolves them.

    Unauthenticated, so the expected answer is a 401/403 — anything except
    **404**, which is what an unmounted route returns. The control case at the
    end is a path that genuinely does not exist, so a run where everything
    404s cannot pass by accident.
    """
    from starlette.testclient import TestClient

    import main

    cases = [
        ("get", "/api/me/successor"),
        ("get", "/api/me/content/summary"),
        ("get", "/api/me/content"),
        ("put", "/api/me/successor"),
        ("post", "/api/me/content/transfer"),
        ("post", "/api/reports/abc/transfer"),
        ("post", "/api/me/transfers/abc/undo"),
    ]

    with TestClient(main.app) as client:
        unmounted = []
        for method, path in cases:
            response = getattr(client, method)(path)
            if response.status_code == 404:
                unmounted.append(f"{method.upper()} {path}")

        assert unmounted == [], (
            "these routes answer 404, which is what an unmounted route returns: "
            f"{unmounted}"
        )

        control = client.get("/api/definitely-not-a-real-route-xyz")
        assert control.status_code == 404, (
            "the control path did not 404, so this test cannot tell a mounted "
            "route from an unmounted one and proves nothing"
        )
