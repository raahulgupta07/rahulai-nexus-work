"""An administrator can move somebody else's work — and removal says so first.

The member path (0.0.531.4) covers the common case. This is the fallback for
when it did not happen: someone left abruptly, or left three years ago, or was
dropped by the directory at 2am. What it must do is rescue content whose owner
is gone. What it must NOT do is become a way for one member to take another's
work, or a mandatory step that blocks an ordinary removal.

Four things are pinned:

  * ★★★**The transfer runs BEFORE the removal body.** `_revoke_departed_member_
    access` switches the person's scheduled prompts off. The rows survive, so a
    transfer afterwards is recoverable — but it inherits a set of dead
    schedules somebody has to notice, and "nobody noticed" means a weekly
    report silently stops arriving. Ordering is the design.
  * ★★★**Remove without a transfer still works.** An escape hatch that becomes
    mandatory is not an escape hatch. There are real removals with nothing to
    move: a pending invite, an account created by mistake, someone who owns
    nothing.
  * ★★★**A refused transfer refuses the whole removal.** Removing them anyway
    would strand exactly the content the caller asked to rescue, while
    reporting success.
  * ★**An admin moving somebody else's report does not grant them a parting
    share.** `keep_access` is a courtesy for a person handing over their own
    work, not something to apply to a departure.

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
from app.models.report_share import ReportShare
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.user import User
from app.services import ownership_service as svc


def _uid() -> str:
    return str(uuid.uuid4())


async def _org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _member(db, org, *, is_active: bool = True) -> User:
    user = User(
        id=_uid(), name="Member", email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x", is_active=is_active, is_superuser=False,
        is_verified=True, is_service_account=False,
    )
    db.add(user)
    await db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="member"))
    await db.flush()
    return user


async def _report(db, org, owner, *, run_identity="viewer") -> Report:
    report = Report(
        id=_uid(), title=f"r-{_uid()[:6]}", slug=f"s-{_uid()[:8]}",
        status="draft", user_id=owner.id, organization_id=org.id,
        shared_run_identity=run_identity,
    )
    db.add(report)
    await db.flush()
    return report


# ─────────────── rescuing work whose owner is already gone ────────────────


@pytest.mark.asyncio
async def test_a_deactivated_persons_work_can_still_be_moved():
    """★The case the whole admin path exists for.

    `assert_can_receive` applies to the RECIPIENT. The person being transferred
    FROM is never validated, deliberately — they are usually the one who has
    left, and refusing to rescue their content because they can no longer sign
    in would be precisely backwards.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        departed = await _member(db, org, is_active=False)
        rescuer = await _member(db, org)
        report = await _report(db, org, departed)

        result = await svc.transfer_everything(
            db, org, from_user_id=departed.id, to_user_id=rescuer.id,
            actor_user_id=rescuer.id, reason="admin_transfer",
            keep_access_for_previous_owner=False,
        )

        await db.refresh(report)
        assert str(report.user_id) == str(rescuer.id)
        assert result.moved["report"] == 1


@pytest.mark.asyncio
async def test_a_departed_person_gets_no_parting_share():
    """`keep_access` is a courtesy for someone handing over their own work. An
    offboarding that quietly grants the leaver a standing share is the opposite
    of what the operation is for."""
    async with async_session_maker() as db:
        org = await _org(db)
        departed = await _member(db, org, is_active=False)
        rescuer = await _member(db, org)
        report = await _report(db, org, departed)

        await svc.transfer_everything(
            db, org, from_user_id=departed.id, to_user_id=rescuer.id,
            actor_user_id=rescuer.id, reason="admin_transfer",
            keep_access_for_previous_owner=False,
        )

        share = (
            await db.execute(
                select(ReportShare).where(
                    ReportShare.report_id == str(report.id),
                    ReportShare.user_id == str(departed.id),
                    ReportShare.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert share is None, (
            "removing somebody left them a share on their own former report"
        )


@pytest.mark.asyncio
async def test_the_transfer_is_recorded_as_an_admin_act_not_a_handover():
    """★The ledger must never blur "I gave my work away" into "an administrator
    moved somebody's work". Three distinct people, three distinct columns."""
    async with async_session_maker() as db:
        org = await _org(db)
        departed, rescuer, admin = (
            await _member(db, org, is_active=False),
            await _member(db, org),
            await _member(db, org),
        )
        report = await _report(db, org, departed)

        await svc.transfer_reports(
            db, org, [report.id], to_user_id=rescuer.id,
            actor_user_id=admin.id, reason="admin_transfer",
            keep_access_for_previous_owner=False,
        )

        row = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalar_one()
        assert row.reason == "admin_transfer"
        assert str(row.from_user_id) == str(departed.id)
        assert str(row.to_user_id) == str(rescuer.id)
        assert str(row.actor_user_id) == str(admin.id)


# ──────────────────── ordering, and the escape hatch ──────────────────────


@pytest.mark.asyncio
async def test_transferring_first_leaves_the_schedules_running():
    """★Ordering is the design.

    A transfer performed while the schedules are still active carries them over
    live. Reversed, the removal switches them off first and the transfer
    inherits dead rows — recoverable, but only if somebody notices, and nobody
    is watching a report that simply stopped arriving.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, rescuer = await _member(db, org), await _member(db, org)
        report = await _report(db, org, leaver)
        db.add(
            ScheduledPrompt(
                id=_uid(), report_id=report.id, user_id=leaver.id,
                prompt={"content": "weekly"}, cron_schedule="0 8 * * 1",
                is_active=True,
            )
        )
        await db.flush()

        await svc.transfer_everything(
            db, org, from_user_id=leaver.id, to_user_id=rescuer.id,
            actor_user_id=rescuer.id, reason="offboarding",
            keep_access_for_previous_owner=False,
        )

        sp = (
            await db.execute(
                select(ScheduledPrompt).where(ScheduledPrompt.report_id == str(report.id))
            )
        ).scalar_one()
        assert str(sp.user_id) == str(rescuer.id), "the schedule did not change hands"
        assert sp.is_active is True, (
            "the schedule was switched off during a transfer that ran first — "
            "the ordering that makes this operation worth doing is broken"
        )


def test_removal_still_works_without_a_transfer():
    """★The escape hatch must not become mandatory.

    Checked at the signature rather than behaviourally: `transfer_content_to`
    has to keep a default so every existing caller — and the DELETE route's own
    204 contract — is unchanged. A required parameter here would break removal
    for pending invites and for anyone who owns nothing.
    """
    import inspect

    from app.services.organization_service import OrganizationService

    sig = inspect.signature(OrganizationService.remove_member)
    param = sig.parameters.get("transfer_content_to")
    assert param is not None, "the offboarding transfer was never wired in"
    assert param.default is None, (
        "transfer_content_to has no default, so every existing caller of "
        "remove_member now fails and an ordinary removal is impossible"
    )


def test_a_refused_transfer_aborts_the_whole_removal():
    """★Removing them anyway would strand exactly the content the caller asked
    to rescue, while returning 204. Read the source: the behavioural version
    needs the full route stack, and what matters is narrow — that the refusal
    raises rather than being swallowed."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "organization_service.py"
    ).read_text(encoding="utf-8")

    start = src.find("if transfer_content_to and membership.user_id:")
    assert start != -1, "the offboarding transfer block is gone"
    # ★★★Bounded by the NEXT STEP in the sequence, never by a character count.
    # This read `start + 1400` and went red against a completely correct file
    # the moment a subscriber notice was added inside the block: the refusal
    # handler was pushed past the cut, so the guard reported that a refusal was
    # being swallowed when it was handled six lines further down. A window that
    # ends mid-function measures the window.
    end = src.find("await self._revoke_departed_member_access", start)
    assert end != -1, "the revoke step no longer follows the transfer block"
    block = src[start:end]

    assert "except ownership_service.TransferRefused" in block
    assert "raise HTTPException" in block, (
        "a refused transfer is swallowed, so the member is removed anyway and "
        "their content is stranded by a request that reported success"
    )
    assert "pass" not in block.split("except ownership_service.TransferRefused")[1][:200], (
        "the refusal is caught and ignored"
    )


def test_the_transfer_runs_before_the_revoke():
    """Ordering, asserted on the source because it is a property of position.

    If the transfer block ever moves below `_revoke_departed_member_access`,
    every transfer inherits switched-off schedules and the test above stops
    describing reality.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "app" / "services" / "organization_service.py"
    ).read_text(encoding="utf-8")

    transfer_at = src.find("if transfer_content_to and membership.user_id:")
    revoke_at = src.find("await self._revoke_departed_member_access(")
    assert transfer_at != -1 and revoke_at != -1
    assert transfer_at < revoke_at, (
        "the offboarding transfer now runs AFTER the revoke, so it inherits "
        "schedules that have already been switched off"
    )


# ───────────────────── the admin route's own gate ─────────────────────────


def test_the_admin_routes_are_gated_and_the_member_ones_are_not():
    """★Two halves of one file with deliberately different rules.

    The member routes need no permission string — their query IS the
    authorization. The admin routes act on somebody else's content and are
    gated on the full-admin wildcard. Getting this backwards in either
    direction is the bug: a permission string on the member half would lock
    people out of their own work (the 0.0.528.9 mistake with /plans), and its
    absence on the admin half would let any member move anyone's dashboards.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app" / "routes" / "ownership.py"
    ).read_text(encoding="utf-8")

    admin_half = src[src.find("# ADMIN — transferring on somebody else's behalf"):]
    assert admin_half, "the admin section marker is gone"
    assert "@requires_permission('full_admin_access')" in admin_half, (
        "the bulk transfer route is not gated on the full-admin wildcard"
    )

    member_half = src[: src.find("# ADMIN — transferring on somebody else's behalf")]
    assert "@requires_permission" not in member_half, (
        "a permission string appeared on the member half. Handing over your own "
        "work is not an administrative act; gating it is how members get locked "
        "out of their own content."
    )
