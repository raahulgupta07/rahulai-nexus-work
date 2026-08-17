"""Moving an owner has to move everything an owner is.

`ownership_service` is the single place that knows what ownership is made of on
this product. Every path that changes an owner calls it, so a gap here is a gap
everywhere. What these tests hold down is the set of things that are easy to get
wrong while looking right:

  * ★★★**The run identity.** ``shared_run_identity == 'creator'`` means the
    report queries its data **as its owner**, using that person's per-user
    connection credentials. A transfer that moves ``user_id`` and not this
    leaves the dashboard running on a departed person's tokens, or dying
    silently when they expire. Microsoft says the same about Power BI datasets
    and Metabase has it open as a bug. It is the only part of a transfer that is
    not a column update, which is exactly why it is the part that gets missed.
  * ★★★**A service account must be a legal recipient.** Its backing users row is
    ``is_active=False`` BY DESIGN. The obvious guard — "no transfers to
    deactivated accounts" — refuses the destination the entire governance
    literature recommends for business-critical dashboards. This is a positive
    control, not a nice-to-have.
  * ★**Archived is deleted.** Delete on this product sets
    ``status='archived'``; ``deleted_at`` stays NULL. A count that filters only
    one of them tells an admin they are stranding forty objects when
    twenty-five were binned months ago, and an inflated number is how people
    learn to ignore the number.
  * ★**Undo restores the run identity too.** Putting ``user_id`` back and
    leaving the identity pointed at the new owner is a half-undo that leaves
    the report running as somebody nobody chose.
  * ★**A no-op writes nothing.** If transferring zero items still produced a
    batch, "nothing happened" and "something happened" would look identical in
    the history.

★These need a schema, so they live here and NOT in `tests/unit/fork` — that
directory's conftest overrides `run_migrations` with a no-op.

★Nothing outside this file calls the service at this release. That is deliberate
and is itself asserted below: the routes and UI land in the next one, and a
migration plus a service that nothing invokes cannot break a live path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.dependencies import async_session_maker
from app.models.artifact import Artifact
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.ownership_transfer import OwnershipTransfer
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.service_account import ServiceAccount
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
        id=_uid(),
        name="Member",
        email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x",
        is_active=is_active,
        is_superuser=False,
        is_verified=True,
        is_service_account=False,
    )
    db.add(user)
    await db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="member"))
    await db.flush()
    return user


async def _service_account(db, org) -> User:
    user = User(
        id=_uid(),
        name="Reporting Bot",
        email=f"sa-{_uid()[:8]}@cityagent.io",
        hashed_password="x",
        is_active=False,          # by design
        is_superuser=False,
        is_verified=True,
        is_service_account=True,
    )
    db.add(user)
    await db.flush()
    db.add(ServiceAccount(id=_uid(), organization_id=org.id, user_id=user.id, name="Bot"))
    await db.flush()
    return user


async def _report(db, org, owner, *, run_identity="viewer", status="draft") -> Report:
    report = Report(
        id=_uid(),
        title=f"r-{_uid()[:6]}",
        slug=f"slug-{_uid()[:8]}",
        status=status,
        user_id=owner.id,
        organization_id=org.id,
        shared_run_identity=run_identity,
    )
    db.add(report)
    await db.flush()
    return report


# ──────────────────────── what a transfer moves ───────────────────────────


@pytest.mark.asyncio
async def test_a_transfer_moves_the_report_and_its_children():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        db.add(
            Artifact(
                id=_uid(), report_id=report.id, user_id=alice.id,
                organization_id=org.id, title="deck",
            )
        )
        db.add(
            ScheduledPrompt(
                id=_uid(), report_id=report.id, user_id=alice.id,
                prompt={"content": "weekly"}, cron_schedule="0 8 * * 1",
                is_active=True,
            )
        )
        await db.flush()

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )

        await db.refresh(report)
        assert str(report.user_id) == str(bob.id)
        assert result.moved["report"] == 1
        assert result.moved["artifact"] == 1, (
            "the dashboard did not move with its report — an artifact left "
            "behind is owned by someone who no longer owns the thing it lives in"
        )
        assert result.moved["scheduled_prompt"] == 1


@pytest.mark.asyncio
async def test_the_run_identity_moves_with_the_owner():
    """★The half that is not a column rename, and the one that gets missed."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice, run_identity="creator")

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=None, reason="successor",
        )

        assert result.creator_identity_repointed == 1, (
            "a report that runs as its owner changed hands without anyone "
            "noticing the identity moved too — this is how a dashboard ends up "
            "querying on a departed person's credentials"
        )
        row = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalar_one()
        assert "creator" in (row.previous_state or ""), (
            "the previous run identity was not recorded, so undo cannot restore it"
        )


@pytest.mark.asyncio
async def test_an_archived_report_is_not_counted_and_not_moved():
    """★Delete on this product is a status, not a timestamp."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        await _report(db, org, alice, status="draft")
        await _report(db, org, alice, status="archived")

        summary = await svc.summarize(db, org, alice.id)
        assert summary.reports == 1, (
            "an archived report was counted as content the person still owns; "
            "an inflated number is how an admin learns to distrust the number"
        )

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )
        assert result.moved["report"] == 1


# ─────────────────────────── who may receive ──────────────────────────────


@pytest.mark.asyncio
async def test_a_service_account_may_receive_content():
    """★★★Positive control. If this fails, team-owned dashboards are impossible.

    The backing users row is deactivated on purpose; a naive "no deactivated
    recipients" rule refuses exactly the destination the research recommends.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        bot = await _service_account(db, org)
        report = await _report(db, org, alice)

        await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bot.id,
            actor_user_id=alice.id, reason="self_handover",
        )

        await db.refresh(report)
        assert str(report.user_id) == str(bot.id)


@pytest.mark.asyncio
async def test_a_deactivated_person_may_not_receive_content():
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        gone = await _member(db, org, is_active=False)
        await _report(db, org, alice)

        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.transfer_everything(
                db, org, from_user_id=alice.id, to_user_id=gone.id,
                actor_user_id=alice.id, reason="self_handover",
            )
        assert excinfo.value.code == "inactive_recipient"


@pytest.mark.asyncio
async def test_transferring_to_the_current_owner_is_refused():
    """A no-op must not write a ledger row that says something happened."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice = await _member(db, org)
        await _report(db, org, alice)

        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.transfer_everything(
                db, org, from_user_id=alice.id, to_user_id=alice.id,
                actor_user_id=alice.id, reason="self_handover",
            )
        assert excinfo.value.code == "same_owner"


@pytest.mark.asyncio
async def test_transferring_nothing_writes_nothing():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)

        result = await svc.transfer_reports(
            db, org, [], to_user_id=bob.id, actor_user_id=alice.id,
            reason="self_handover",
        )
        assert result.total == 0

        rows = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.batch_id == result.batch_id
                )
            )
        ).scalars().all()
        assert rows == [], "an empty transfer wrote a ledger row"


# ─────────────────────────── keep access ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_previous_owner_keeps_access_when_asked():
    """Figma's rule: handing over is not the same as walking away."""
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)

        await svc.transfer_reports(
            db, org, [report.id], to_user_id=bob.id, actor_user_id=alice.id,
            reason="self_handover", keep_access_for_previous_owner=True,
        )

        share = (
            await db.execute(
                select(ReportShare).where(
                    ReportShare.report_id == str(report.id),
                    ReportShare.user_id == str(alice.id),
                    ReportShare.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert share is not None, (
            "the previous owner lost access the moment they handed over"
        )


@pytest.mark.asyncio
async def test_the_recipients_own_share_row_is_cleared():
    """★Unique on (report_id, user_id, share_type). An owner does not need a
    share to reach their own report, and leaving it collides on a later re-share.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)
        db.add(ReportShare(report_id=report.id, user_id=bob.id, share_type="artifact"))
        await db.flush()

        await svc.transfer_reports(
            db, org, [report.id], to_user_id=bob.id, actor_user_id=alice.id,
            reason="self_handover", keep_access_for_previous_owner=False,
        )

        live = (
            await db.execute(
                select(ReportShare).where(
                    ReportShare.report_id == str(report.id),
                    ReportShare.user_id == str(bob.id),
                    ReportShare.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert live is None


# ─────────────────────────────── undo ─────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_restores_the_owner_and_the_run_identity():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice, run_identity="creator")

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )
        await db.refresh(report)
        assert str(report.user_id) == str(bob.id)

        await svc.undo(db, org, result.batch_id, actor_user_id=alice.id)

        await db.refresh(report)
        assert str(report.user_id) == str(alice.id)
        assert report.shared_run_identity == "creator", (
            "undo put the owner back but left the run identity pointed at the "
            "new owner — the report now runs as somebody nobody chose"
        )


@pytest.mark.asyncio
async def test_a_batch_cannot_be_undone_twice():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        await _report(db, org, alice)

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )
        await svc.undo(db, org, result.batch_id, actor_user_id=alice.id)

        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.undo(db, org, result.batch_id, actor_user_id=alice.id)
        assert excinfo.value.code == "already_reverted"


@pytest.mark.asyncio
async def test_undo_refuses_once_the_window_has_passed():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        await _report(db, org, alice)

        result = await svc.transfer_everything(
            db, org, from_user_id=alice.id, to_user_id=bob.id,
            actor_user_id=alice.id, reason="self_handover",
        )

        # Age the batch past the window rather than waiting 30 days.
        rows = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.batch_id == result.batch_id
                )
            )
        ).scalars().all()
        for row in rows:
            row.created_at = datetime.utcnow() - timedelta(days=31)
        await db.flush()

        with pytest.raises(svc.TransferRefused) as excinfo:
            await svc.undo(db, org, result.batch_id, actor_user_id=alice.id)
        assert excinfo.value.code == "window_expired"


# ───────────────────── the ledger's own invariants ────────────────────────


@pytest.mark.asyncio
async def test_the_actor_is_recorded_separately_from_the_new_owner():
    """★Superset's documented mistake is conflating these two.

    An admin moving Alice's work to Bob is three different people, and the
    successor path has no actor at all.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob, admin = (
            await _member(db, org),
            await _member(db, org),
            await _member(db, org),
        )
        report = await _report(db, org, alice)

        await svc.transfer_reports(
            db, org, [report.id], to_user_id=bob.id, actor_user_id=admin.id,
            reason="admin_transfer",
        )

        row = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalar_one()
        assert str(row.from_user_id) == str(alice.id)
        assert str(row.to_user_id) == str(bob.id)
        assert str(row.actor_user_id) == str(admin.id)
        assert row.actor_user_id != row.to_user_id


@pytest.mark.asyncio
async def test_the_successor_path_records_no_actor():
    async with async_session_maker() as db:
        org = await _org(db)
        alice, bob = await _member(db, org), await _member(db, org)
        report = await _report(db, org, alice)

        await svc.transfer_reports(
            db, org, [report.id], to_user_id=bob.id, actor_user_id=None,
            reason="successor",
        )

        row = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalar_one()
        assert row.actor_user_id is None, (
            "an automatic transfer recorded an actor; nobody clicked anything, "
            "and inventing one makes the audit trail lie"
        )


# ───────────────── the release's own scope, asserted ──────────────────────


def test_only_the_expected_surfaces_call_the_service():
    """★Who is allowed to change an owner, checked rather than assumed.

    This began life in 0.0.531.3 as "nothing calls it yet" — the claim that
    made that release inert and therefore low risk. 0.0.531.4 added the
    member-facing routes, so the assertion narrows rather than disappears: the
    question is no longer *whether* something calls it but *what*, because a
    transfer is a privileged write and every new caller is a new place an
    escalation could hide.

    Add a caller here only after reading how it decides who may act. Each entry
    below was read before it was added:

      * ``app/routes/ownership.py`` (.4/.5) — the member half selects strictly
        on ``Report.user_id == caller``; the admin half is decorator-gated.
      * ``app/services/organization_service.py`` (.5) — reached only from
        ``remove_member``, whose route already requires
        ``remove_organization_members``, and the recipient is a parameter the
        admin supplied.
      * ``app/ee/scim/service.py`` (.6) — no HTTP caller and no recipient in the
        request. The destination comes from ``memberships.successor_user_id``,
        which only the departing person could write, through their own gate-free
        ``PUT /api/me/successor``. ★So the SCIM token cannot name where content
        goes; it can only trigger a handover the leaver arranged in advance.
        That is why this caller is safe despite being the least authenticated of
        the three.
    """
    from pathlib import Path

    ALLOWED = {
        "app/routes/ownership.py",
        "app/services/organization_service.py",
        "app/ee/scim/service.py",
        # ★The odd one out, and it is here for a reason worth reading rather
        # than skipping. The export bundle changes nobody's ownership — it
        # imports exactly two things, ``_shared_instructions_only`` and
        # ``_non_private_prompts_only``, so that the rows it WRITES INTO A FILE
        # are the same rows a transfer would move. Re-spelling those two
        # predicates locally would pass this guard and would be the worse
        # outcome: two spellings of one privacy rule drift, and the drift shows
        # up as a private instruction inside a zip somebody emails. Sharing the
        # predicate is what makes the leak impossible; the import is the point.
        "app/services/ownership_export.py",
    }

    # ★★★An IMPORT, not the word. This scanned for the bare string
    # `ownership_service` and a file that merely NAMED the module in a docstring
    # — `ownership_schema.py`, explaining where a label is derived — was
    # reported as a surface that can change ownership. A guard that fires on
    # prose trains people to widen it, and the next widening will be the one
    # that matters. Matching the import statement is strictly stronger: it stops
    # matching comments and still catches every real caller, including the
    # function-local imports this codebase uses to break cycles.
    #
    # ★Proven, not assumed — `test_the_caller_scan_still_finds_every_real_one`
    # below checks each ALLOWED file is still detected. Without it, a typo in
    # this regex would empty `callers` and the guard would pass forever.
    import re

    imports = re.compile(
        r"^\s*(?:from\s+app\.services(?:\.ownership_service)?\s+import\s+[^\n]*"
        r"ownership_service|from\s+app\.services\.ownership_service\s+import"
        r"|import\s+app\.services\.ownership_service)",
        re.MULTILINE,
    )

    backend = Path(__file__).resolve().parents[2]
    callers = set()
    for path in list((backend / "app").rglob("*.py")):
        if ".bak-" in path.name or path.name == "ownership_service.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if imports.search(text):
            callers.add(str(path.relative_to(backend)))

    unexpected = callers - ALLOWED
    assert unexpected == set(), (
        "a new surface can change ownership. Read how it authorizes the caller, "
        "then add it to ALLOWED deliberately — do not widen this to make a test "
        f"pass: {sorted(unexpected)}"
    )
    assert "app/routes/ownership.py" in callers, (
        "the member-facing transfer routes are gone; either they were removed "
        "or the import was renamed, and either way this guard is now blind"
    )
    # ★★★The positive control for the scan itself. A refusal-only guard is
    # satisfied by a scan that finds NOTHING, and a regex is exactly the kind of
    # thing that quietly stops matching. Every file we deliberately allow must
    # still be detected as a caller — otherwise `unexpected` is empty because
    # the scan is broken, not because the tree is clean.
    missed = ALLOWED - callers
    assert missed == set(), (
        "these are declared callers and the scan no longer detects them, so it "
        f"would not detect an undeclared one either: {sorted(missed)}"
    )
