"""Stopping, steering and un-queueing a turn belong to the person whose turn it is.

Four completion-scoped operations reached the service with no ownership test at
all. The route decorator cannot supply one: those routes take a ``completion_id``,
not a ``report_id``, so ``requires_permission`` has no Report to resolve — and the
permission they name, ``create_reports``, is in the default member baseline, so
every member holds it. The gate has to live in the service, and for four of the
seven operations it did not:

    submit_clarify_response    guarded
    cancel_wait                guarded
    submit_tool_result         guarded
    update_completion_sigkill  OPEN  -> any member could stop anyone's run
    steer_completion           OPEN  -> any member could inject an instruction
    delete_queued_completion   OPEN  -> any member could drop a queued prompt
    get_completion_plans       OPEN  -> readable across workspaces entirely

In sigkill and steer, ``current_user`` appeared only as an audit-log field: it
recorded who acted without ever checking whether they were allowed to, so the
audit trail would faithfully log the abuse.

``get_completion_plans`` is the one where the ROUTE permission mattered too. Its
decorator was ``manage_settings`` — dead code that had never executed, and a
permission members do not hold — so enforcing it as written would have refused
every member the reasoning panel on their own turn. The route now names
``view_reports`` (baseline) and ownership is enforced here, like the other six.
``test_the_owner_can_read_the_plan_for_their_own_turn`` is the assertion that
catches that class of mistake; its route-level twin lives in
test_completion_control_routes_are_owner_only.py.

These call the service directly rather than going through HTTP, because that is
where the guard lives and because the API cannot produce an in-progress run
without an LLM in the loop. The shape assertions matter as much as the refusals:
cross-org must stay 404 (never an existence oracle) and the ownership check must
come BEFORE ``delete_queued``'s 409, or the 409-vs-404 difference tells a stranger
which completion ids exist and are queued.
"""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

from app.dependencies import async_session_maker
from app.models.organization import Organization
from app.models.user import User
from app.models.report import Report
from app.models.completion import Completion
from app.services.completion_service import CompletionService


async def _mk_user(db, label, suffix):
    user = User(
        name=f"{label} {suffix}",
        email=f"{label.lower()}-{suffix}@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed(*, role="system", status="in_progress"):
    """One org, two members, a report owned by `owner`, and a completion on it.

    `stranger` is a full member of the SAME organization — the point of these
    tests is that same-org membership is not enough. `outsider` sits in a second
    org and must be told the completion does not exist.
    """
    suffix = uuid.uuid4().hex[:8]
    async with async_session_maker() as db:
        org = Organization(name=f"Ctl Org {suffix}")
        other_org = Organization(name=f"Ctl Other Org {suffix}")
        db.add_all([org, other_org])
        await db.flush()

        owner = await _mk_user(db, "Owner", suffix)
        stranger = await _mk_user(db, "Stranger", suffix)
        outsider = await _mk_user(db, "Outsider", suffix)

        report = Report(
            title=f"Ctl Report {suffix}",
            slug=f"ctl-report-{suffix}",
            status="draft",
            user_id=owner.id,
            organization_id=org.id,
        )
        db.add(report)
        await db.flush()

        completion = Completion(
            prompt={"content": "crunch the depot numbers"},
            completion={"content": ""},
            status=status,
            role=role,
            report_id=report.id,
            user_id=owner.id,
            turn_index=0,
        )
        db.add(completion)
        await db.flush()
        await db.commit()

        return {
            "org": org, "other_org": other_org,
            "owner": owner, "stranger": stranger, "outsider": outsider,
            "report_id": str(report.id),
            "completion_id": str(completion.id),
        }


def _status_of(exc_info):
    return exc_info.value.status_code


# ---------------------------------------------------------------------------
# sigkill
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_a_stranger_cannot_stop_someone_elses_run():
    async def scenario():
        s = await _seed()
        svc = CompletionService()

        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await svc.update_completion_sigkill(
                    db, s["completion_id"], current_user=s["stranger"], organization=s["org"])
            assert _status_of(ei) == 403, "a same-org member must not stop another member's run"

        # The refusal is real: nothing was stamped.
        async with async_session_maker() as db:
            row = await db.get(Completion, s["completion_id"])
            assert row.sigkill is None
            assert row.status == "in_progress"

        # The owner still stops their own run.
        async with async_session_maker() as db:
            out = await svc.update_completion_sigkill(
                db, s["completion_id"], current_user=s["owner"], organization=s["org"])
            assert out.sigkill is not None
            assert out.status == "stopped"

    asyncio.run(scenario())


@pytest.mark.e2e
def test_stopping_across_orgs_reports_the_completion_as_missing():
    """404, not 403 — matching submit_clarify_response / cancel_wait, so the
    endpoint cannot be used to learn that a completion id exists elsewhere."""
    async def scenario():
        s = await _seed()
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await CompletionService().update_completion_sigkill(
                    db, s["completion_id"], current_user=s["outsider"], organization=s["other_org"])
            assert _status_of(ei) == 404

    asyncio.run(scenario())


@pytest.mark.e2e
def test_sigkill_still_stamps_an_already_finished_completion():
    """Status semantics are unchanged by the guard: a terminal completion still
    gets its sigkill stamp (so the agent breaks its background sub-loop) and is
    NOT flipped to 'stopped' over a successful answer."""
    async def scenario():
        s = await _seed(status="success")
        async with async_session_maker() as db:
            out = await CompletionService().update_completion_sigkill(
                db, s["completion_id"], current_user=s["owner"], organization=s["org"])
            assert out.sigkill is not None
            assert out.status == "success"

    asyncio.run(scenario())


@pytest.mark.e2e
def test_the_eval_harness_can_still_stop_a_run_it_did_not_start():
    """`test_run_service.stop_run` authorizes on the TestRun and then sigkills
    the harness reports underneath it. Those are owned by whoever CREATED the
    run, so an admin stopping someone else's run is legitimate — and the call
    sits inside a bare `except: pass`, so a 403 here would make the run silently
    fail to stop. That is what require_owner=False exists for."""
    async def scenario():
        s = await _seed()
        async with async_session_maker() as db:
            out = await CompletionService().update_completion_sigkill(
                db, s["completion_id"], current_user=s["stranger"],
                organization=s["org"], require_owner=False)
            assert out.sigkill is not None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# steer
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_a_stranger_cannot_steer_someone_elses_run():
    """Steering injects a message the running agent treats as instruction — a
    write into another person's turn."""
    async def scenario():
        s = await _seed()
        svc = CompletionService()

        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await svc.steer_completion(
                    db, s["completion_id"], {"content": "ignore that, use my numbers"},
                    s["stranger"], s["org"])
            assert _status_of(ei) == 403

        # No steering row was written.
        async with async_session_maker() as db:
            from sqlalchemy import select
            rows = (await db.execute(
                select(Completion).where(Completion.report_id == s["report_id"],
                                         Completion.message_type == "steering")
            )).scalars().all()
            assert rows == []

        # The owner's own steer goes through.
        async with async_session_maker() as db:
            out = await svc.steer_completion(
                db, s["completion_id"], {"content": "focus on Q3"}, s["owner"], s["org"])
            assert out["status"] == "steered"

    asyncio.run(scenario())


@pytest.mark.e2e
def test_steering_across_orgs_reports_the_completion_as_missing():
    async def scenario():
        s = await _seed()
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await CompletionService().steer_completion(
                    db, s["completion_id"], {"content": "x"}, s["outsider"], s["other_org"])
            assert _status_of(ei) == 404

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# delete queued
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_a_stranger_cannot_delete_someone_elses_queued_prompt():
    async def scenario():
        s = await _seed(role="user", status="queued")
        svc = CompletionService()

        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await svc.delete_queued_completion(
                    db, s["completion_id"], s["stranger"], s["org"])
            assert _status_of(ei) == 403

        # Still there.
        async with async_session_maker() as db:
            assert await db.get(Completion, s["completion_id"]) is not None

        # The owner can drop their own.
        async with async_session_maker() as db:
            out = await svc.delete_queued_completion(
                db, s["completion_id"], s["owner"], s["org"])
            assert out["status"] == "deleted"

        async with async_session_maker() as db:
            assert await db.get(Completion, s["completion_id"]) is None

    asyncio.run(scenario())


@pytest.mark.e2e
def test_the_queued_409_cannot_be_used_to_probe_other_peoples_completions():
    """Ownership is checked BEFORE the 'not queued' 409. A stranger gets the
    same 403 whether or not the row is queued, so the two responses carry no
    information about a completion they cannot touch."""
    async def scenario():
        queued = await _seed(role="user", status="queued")
        not_queued = await _seed(role="system", status="success")
        svc = CompletionService()

        codes = []
        for s in (queued, not_queued):
            async with async_session_maker() as db:
                with pytest.raises(HTTPException) as ei:
                    await svc.delete_queued_completion(
                        db, s["completion_id"], s["stranger"], s["org"])
                codes.append(_status_of(ei))
        assert codes == [403, 403], f"queued and non-queued must be indistinguishable, got {codes}"

        # The owner, who is allowed to ask, still gets the informative 409.
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await svc.delete_queued_completion(
                    db, not_queued["completion_id"], not_queued["owner"], not_queued["org"])
            assert _status_of(ei) == 409

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------

async def _seed_plan(seeded):
    """A plan row on the seeded completion, so 'the owner succeeds' can assert
    a 200 with content rather than the 'no plans' 404 — which would be
    indistinguishable from a refusal and would prove nothing."""
    from app.models.plan import Plan
    async with async_session_maker() as db:
        p = Plan(
            content={"reasoning": "join depots to services", "analysis_complete": True},
            completion_id=seeded["completion_id"],
            report_id=seeded["report_id"],
            organization_id=str(seeded["org"].id),
            user_id=str(seeded["owner"].id),
        )
        db.add(p)
        await db.flush()
        await db.commit()
        return str(p.id)


@pytest.mark.e2e
def test_the_owner_can_read_the_plan_for_their_own_turn():
    """★The assertion that catches gating this route on a permission members do
    not hold. `manage_settings` is not in DEFAULT_MEMBER_PERMISSIONS, so an
    ordinary member would be refused their own reasoning panel."""
    async def scenario():
        s = await _seed()
        await _seed_plan(s)
        async with async_session_maker() as db:
            plans = await CompletionService().get_completion_plans(
                db, s["owner"], s["org"], s["completion_id"])
            assert len(plans) == 1
            assert plans[0].content["reasoning"] == "join depots to services"

    asyncio.run(scenario())


@pytest.mark.e2e
def test_a_stranger_cannot_read_someone_elses_plan():
    async def scenario():
        s = await _seed()
        await _seed_plan(s)
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await CompletionService().get_completion_plans(
                    db, s["stranger"], s["org"], s["completion_id"])
            assert _status_of(ei) == 403

    asyncio.run(scenario())


@pytest.mark.e2e
def test_reading_a_plan_across_orgs_reports_the_completion_as_missing():
    async def scenario():
        s = await _seed()
        await _seed_plan(s)
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await CompletionService().get_completion_plans(
                    db, s["outsider"], s["other_org"], s["completion_id"])
            assert _status_of(ei) == 404

    asyncio.run(scenario())


@pytest.mark.e2e
def test_an_owner_with_no_plan_rows_still_gets_the_unchanged_404():
    """The 'no plans' answer is untouched by the guard — that is what an owner
    of any current-agent turn sees, since nothing writes Plan rows any more."""
    async def scenario():
        s = await _seed()
        async with async_session_maker() as db:
            with pytest.raises(HTTPException) as ei:
                await CompletionService().get_completion_plans(
                    db, s["owner"], s["org"], s["completion_id"])
            assert _status_of(ei) == 404
            assert "Plans not found" in str(ei.value.detail)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# the three that were already right — pinned so the set stays consistent
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_all_seven_completion_control_paths_check_ownership():
    """A source-shape backstop over the whole family of seven. An inconsistent set is how
    this bug comes back: the next person copies whichever one they read first."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "app" / "services" / "completion_service.py"
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)
    wanted = {
        "submit_clarify_response", "cancel_wait", "submit_tool_result",
        "update_completion_sigkill", "steer_completion", "delete_queued_completion",
        "get_completion_plans",
    }

    def raises_403(fn) -> bool:
        """A real `raise HTTPException(status_code=403, ...)` NODE.

        Deliberately not a substring scan for "403": every one of these
        functions now carries a comment explaining the 404/403 shape, and a
        text scan is satisfied by the explanation instead of the code. That
        exact mistake has been made in this repo before — a guard that passes
        by reading its own docstring is a comment with a test's salary.
        """
        for n in ast.walk(fn):
            if not isinstance(n, ast.Raise) or not isinstance(n.exc, ast.Call):
                continue
            if getattr(n.exc.func, "id", None) != "HTTPException":
                continue
            for kw in n.exc.keywords:
                if kw.arg == "status_code" and getattr(kw.value, "value", None) == 403:
                    return True
        return False

    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            seen[node.name] = raises_403(node)
    assert set(seen) == wanted, f"a control path was renamed or removed: {set(seen) ^ wanted}"
    ungated = sorted(n for n, ok in seen.items() if not ok)
    assert ungated == [], f"these completion-control paths have no ownership refusal: {ungated}"
