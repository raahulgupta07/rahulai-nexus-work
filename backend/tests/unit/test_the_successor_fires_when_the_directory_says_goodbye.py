"""Nobody is there to press the button at 2am.

0.0.531.4 lets a person hand over their own work; 0.0.531.5 lets an admin do it
for them. Both need somebody to be present and to remember. The common real
departure is neither: the directory deprovisions an account overnight and the
first anyone hears of it is a weekly report that stopped arriving.

This is the automatic half — the successor a person nominated in advance, fired
by the deactivation itself — plus the view that catches everything the rule
could not.

Four properties, and three of them are about NOT doing things:

  * ★★★**A deprovisioning can never fail because of a handover.** The directory
    is telling us somebody left the company; switching the account off is the
    security-critical act. If the transfer raised, an Okta deactivation would
    return 500, the IdP would retry forever, and the account would stay
    **enabled** — the exact opposite of what was asked for.
  * ★★★**The transition, not the state.** Okta and Entra re-send the whole
    object on every reconcile. A rule that fires on "is inactive" writes a fresh
    batch on every sync forever: an audit trail claiming a handover happened
    today, and an Undo offer that reverses nothing.
  * ★★★**Uncleared content is listed, never lost.** Every refusal here ends in
    the Needs-an-owner view rather than an exception.
  * ★A service account is not an orphan. Its backing row is `is_active=False`
    by design and it is the recommended DESTINATION for this content.

★These need a schema, so they live here and NOT in `tests/unit/fork`.

★★★**Red proof, and why the obvious one is not enough.** Overlaying the pre-.6
sources turns all 18 red — but almost every failure is an `AttributeError` on a
function that does not exist yet, which proves the guard imports the fix, not
that it detects the bug. So both behavioural properties were killed by mutation
of the SHIPPED file instead, each taking down exactly one test and leaving the
other 17 green:

  * firing on the state (``if user.is_active: return``) rather than on the
    transition → only `test_the_hook_does_nothing_when_the_account_was_already_off`
  * narrowing the swallow (``except ZeroDivisionError``) → only
    `test_a_broken_handover_never_fails_the_deprovisioning`, with the injected
    `RuntimeError: database on fire` escaping exactly as an IdP would see it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.dependencies import async_session_maker
from app.models.artifact import Artifact
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.ownership_transfer import OwnershipTransfer
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.user import User
from app.services import ownership_service as svc


def _uid() -> str:
    return str(uuid.uuid4())


async def _org(db) -> Organization:
    org = Organization(id=_uid(), name=f"org-{_uid()[:8]}")
    db.add(org)
    await db.flush()
    return org


async def _member(
    db, org, *, is_active: bool = True, is_service_account: bool = False
) -> tuple[User, Membership]:
    user = User(
        id=_uid(), name="Member", email=f"{_uid()[:8]}@cityagent.io",
        hashed_password="x", is_active=is_active, is_superuser=False,
        is_verified=True, is_service_account=is_service_account,
    )
    db.add(user)
    await db.flush()
    membership = Membership(
        id=_uid(), user_id=user.id, organization_id=org.id, role="member"
    )
    db.add(membership)
    await db.flush()
    return user, membership


async def _report(db, org, owner, *, working: bool = True) -> Report:
    """A report the automatic paths will actually move.

    ★★★``working=True`` by default, and that default is load-bearing. Since
    0.0.531.8 the automatic paths — the successor here, and admin offboarding —
    move only reports that still do a job for somebody: one with a dashboard, a
    schedule, or a share. A report with none of those is nothing but a chat
    thread, and reports are gated `owner_only` as conversation privacy, so
    handing somebody's chats to another person on the day they leave cannot be
    right.

    These tests are about whether the successor FIRES, not about the split, so
    they build the working kind. `working=False` builds a bare conversation and
    is used by the test that pins the split itself — without which every test
    in this file would be equally satisfied by a rule that moves nothing.
    """
    report = Report(
        id=_uid(), title=f"r-{_uid()[:6]}", slug=f"s-{_uid()[:8]}",
        status="draft", user_id=owner.id, organization_id=org.id,
        shared_run_identity="viewer",
    )
    db.add(report)
    await db.flush()
    if working:
        # A dashboard, which is the most representative of the three qualifying
        # signals — the thing an organization actually needs to keep running
        # after somebody leaves.
        db.add(Artifact(
            id=_uid(), report_id=str(report.id), user_id=str(owner.id),
            organization_id=str(org.id), title="d", mode="page",
        ))
        await db.flush()
    return report


# ───────────────────────── the successor firing ───────────────────────────


@pytest.mark.asyncio
async def test_a_nominated_successor_takes_over_on_deactivation():
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        heir, _ = await _member(db, org)
        report = await _report(db, org, leaver)

        leaver_m.successor_user_id = str(heir.id)
        leaver.is_active = False
        await db.flush()

        result = await svc.on_member_deactivated(db, org, str(leaver.id))

        assert result is not None, "the successor was recorded and never fired"
        await db.refresh(report)
        assert str(report.user_id) == str(heir.id)


@pytest.mark.asyncio
async def test_it_is_recorded_as_a_successor_with_no_actor():
    """★`successor` is the one reason in the ledger that legitimately has no
    actor — no human asked for it. Recording the heir as the actor would read as
    them having taken the work."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        heir, _ = await _member(db, org)
        report = await _report(db, org, leaver)
        leaver_m.successor_user_id = str(heir.id)
        await db.flush()

        await svc.on_member_deactivated(db, org, str(leaver.id))

        row = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalar_one()
        assert row.reason == "successor"
        assert row.actor_user_id is None, (
            "an automatic handover was attributed to a person, so the ledger "
            "says somebody took the work when nobody did"
        )


@pytest.mark.asyncio
async def test_the_departed_person_keeps_no_share():
    """The opposite default from a voluntary handover. Somebody the directory
    has just switched off must not be left holding a standing share on the work
    that moved away from them."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        heir, _ = await _member(db, org)
        report = await _report(db, org, leaver)
        leaver_m.successor_user_id = str(heir.id)
        await db.flush()

        await svc.on_member_deactivated(db, org, str(leaver.id))

        share = (
            await db.execute(
                select(ReportShare).where(
                    ReportShare.report_id == str(report.id),
                    ReportShare.user_id == str(leaver.id),
                    ReportShare.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert share is None


# ──────────────── the three refusals, none of which may raise ─────────────


@pytest.mark.asyncio
async def test_no_successor_is_not_an_error():
    """The common case. Most people never set one, and a deactivation must be
    completely ordinary when they have not."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, _ = await _member(db, org)
        report = await _report(db, org, leaver)

        result = await svc.on_member_deactivated(db, org, str(leaver.id))

        assert result is None
        await db.refresh(report)
        assert str(report.user_id) == str(leaver.id), "the owner moved to nobody"
        rows = (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.resource_id == str(report.id)
                )
            )
        ).scalars().all()
        assert rows == [], "an empty handover still wrote to the ledger"


@pytest.mark.asyncio
async def test_a_successor_who_has_also_left_is_refused_quietly():
    """★The case that makes the Needs-an-owner view necessary.

    Two people leave, the second having been the first's nominated successor.
    `assert_can_receive` correctly refuses a deactivated recipient — and that
    refusal must surface as "nothing happened", not as a failed deprovisioning.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        gone_too, _ = await _member(db, org, is_active=False)
        report = await _report(db, org, leaver)
        leaver_m.successor_user_id = str(gone_too.id)
        await db.flush()

        result = await svc.on_member_deactivated(db, org, str(leaver.id))

        assert result is None
        await db.refresh(report)
        assert str(report.user_id) == str(leaver.id)


@pytest.mark.asyncio
async def test_naming_yourself_is_refused_quietly():
    """`transfer_everything` raises `same_owner`. Swallowed like every other
    refusal — a bad nomination is not a reason to fail a deprovisioning."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        await _report(db, org, leaver)
        leaver_m.successor_user_id = str(leaver.id)
        await db.flush()

        assert await svc.on_member_deactivated(db, org, str(leaver.id)) is None


# ──────────────────── the SCIM wiring: transition only ────────────────────


@pytest.mark.asyncio
async def test_the_hook_does_nothing_when_the_account_was_already_off():
    """★★★The reconcile guard.

    Okta and Entra re-send the full object on every sync. Called with
    `was_active=False`, the hook must not touch anything — otherwise every
    nightly reconcile writes another batch for every person who ever left.
    """
    from app.ee.scim.service import ScimUserService

    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org, is_active=False)
        heir, _ = await _member(db, org)
        report = await _report(db, org, leaver)
        leaver_m.successor_user_id = str(heir.id)
        await db.flush()
        await db.commit()

        await ScimUserService()._fire_successor_if_deactivated(
            db, str(org.id), leaver, was_active=False
        )

        await db.refresh(report)
        assert str(report.user_id) == str(leaver.id), (
            "the hook fired on an account that was already inactive, so every "
            "directory reconcile re-runs the handover"
        )


@pytest.mark.asyncio
async def test_the_hook_does_nothing_when_the_account_is_still_on():
    """A rename or an email change is not a departure."""
    from app.ee.scim.service import ScimUserService

    async with async_session_maker() as db:
        org = await _org(db)
        person, person_m = await _member(db, org)
        heir, _ = await _member(db, org)
        report = await _report(db, org, person)
        person_m.successor_user_id = str(heir.id)
        await db.flush()
        await db.commit()

        await ScimUserService()._fire_successor_if_deactivated(
            db, str(org.id), person, was_active=True
        )

        await db.refresh(report)
        assert str(report.user_id) == str(person.id)


@pytest.mark.asyncio
async def test_a_broken_handover_never_fails_the_deprovisioning(monkeypatch):
    """★★★The property this whole design turns on.

    A raising transfer would make an Okta deactivation return 500. The IdP
    retries forever and the account stays **enabled** — a departed employee who
    still has access because their handover was misconfigured.
    """
    from app.ee.scim.service import ScimUserService
    from app.services import ownership_service

    async def _explode(*a, **kw):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(ownership_service, "on_member_deactivated", _explode)

    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        heir, _ = await _member(db, org)
        leaver_m.successor_user_id = str(heir.id)
        leaver.is_active = False
        await db.flush()
        await db.commit()

        # Must simply return. Any exception escaping here is the bug.
        await ScimUserService()._fire_successor_if_deactivated(
            db, str(org.id), leaver, was_active=True
        )


def test_every_scim_deactivation_path_calls_the_hook():
    """★Three ways a directory switches an account off — PUT, PATCH and DELETE.
    Wiring two of them leaves a silent gap that depends on which IdP you use,
    and Okta's DELETE is a deactivate.

    Read on the source because the behavioural version needs a full SCIM
    request stack per path, and what matters is narrow: that no site was missed.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "app" / "ee" / "scim" / "service.py"
    ).read_text(encoding="utf-8")

    calls = src.count("await self._fire_successor_if_deactivated(")
    assert calls == 3, (
        f"expected the hook on all three deactivation paths (PUT, PATCH, "
        f"DELETE); found {calls}"
    )
    assert src.count("was_active = bool(user.is_active)") == 3, (
        "a path reads is_active AFTER mutating it, so the transition can never "
        "be detected and the handover never fires"
    )


# ─────────────────────── the Needs-an-owner view ──────────────────────────


@pytest.mark.asyncio
async def test_orphaned_content_lists_a_departed_owner():
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, _ = await _member(db, org, is_active=False)
        await _report(db, org, leaver)

        rows = await svc.orphaned_owners(db, org)

        assert [r["user_id"] for r in rows] == [str(leaver.id)]
        assert rows[0]["summary"]["reports"] == 1


@pytest.mark.asyncio
async def test_a_service_account_is_not_an_orphan():
    """★★★The trap this feature is built around.

    A service account's backing row is `is_active=False` BY DESIGN so it can
    never sign in interactively while its API keys keep working. It is the
    destination the governance literature recommends for business-critical
    dashboards. Listing it here would tell an administrator to undo the single
    most correct thing they can do with this content.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        bot, _ = await _member(db, org, is_active=False, is_service_account=True)
        await _report(db, org, bot)

        assert await svc.orphaned_owners(db, org) == []


@pytest.mark.asyncio
async def test_an_active_member_is_not_an_orphan():
    """The positive control. Without it every assertion above is satisfied by a
    function that returns an empty list."""
    async with async_session_maker() as db:
        org = await _org(db)
        here, _ = await _member(db, org)
        await _report(db, org, here)
        gone, _ = await _member(db, org, is_active=False)
        await _report(db, org, gone)

        rows = await svc.orphaned_owners(db, org)

        assert [r["user_id"] for r in rows] == [str(gone.id)]


@pytest.mark.asyncio
async def test_a_departed_person_who_owns_nothing_is_not_listed():
    """Every deactivated account would otherwise appear forever, and a list
    that is mostly noise is one nobody reads."""
    async with async_session_maker() as db:
        org = await _org(db)
        await _member(db, org, is_active=False)

        assert await svc.orphaned_owners(db, org) == []


@pytest.mark.asyncio
async def test_an_archived_report_does_not_strand_anybody():
    """★Delete on this product is `status='archived'`. Counting those would tell
    an admin they are stranding work that was thrown away months ago, and the
    exaggeration is what makes people stop trusting the number."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, _ = await _member(db, org, is_active=False)
        report = await _report(db, org, leaver)
        report.status = "archived"
        await db.flush()

        assert await svc.orphaned_owners(db, org) == []


@pytest.mark.asyncio
async def test_the_biggest_pile_is_listed_first():
    async with async_session_maker() as db:
        org = await _org(db)
        small, _ = await _member(db, org, is_active=False)
        big, _ = await _member(db, org, is_active=False)
        await _report(db, org, small)
        for _ in range(3):
            await _report(db, org, big)

        rows = await svc.orphaned_owners(db, org)

        assert [r["user_id"] for r in rows] == [str(big.id), str(small.id)]


@pytest.mark.asyncio
async def test_a_stale_successor_is_named_on_the_orphan_row():
    """★Why the content is still here matters. A successor shown next to an
    orphan means the rule DID fire and was refused — normally because that
    person has since left too — which tells an admin to pick somebody else
    rather than wonder why nothing happened."""
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org, is_active=False)
        gone_too, _ = await _member(db, org, is_active=False)
        gone_too.name = "Second Leaver"
        await _report(db, org, leaver)
        leaver_m.successor_user_id = str(gone_too.id)
        await db.flush()

        rows = await svc.orphaned_owners(db, org)
        row = next(r for r in rows if r["user_id"] == str(leaver.id))
        assert row["successor_name"] == "Second Leaver"


@pytest.mark.asyncio
async def test_orphans_do_not_leak_across_organizations():
    async with async_session_maker() as db:
        org, other = await _org(db), await _org(db)
        theirs, _ = await _member(db, other, is_active=False)
        await _report(db, other, theirs)

        assert await svc.orphaned_owners(db, org) == []


# ─────────────── what the automatic path deliberately leaves ───────────────


@pytest.mark.asyncio
async def test_a_bare_conversation_is_not_handed_to_the_successor():
    """★★★The half every other test in this file is blind to.

    A Report on this product IS the conversation thread; a dashboard is an
    Artifact hanging off it. Measured on the live install, 224 of 262 reports
    are chat-only. Releases .9/.10 gated reports `owner_only` as *conversation
    privacy* — so an automatic rule that hands somebody's chats to a colleague
    the night they are deprovisioned contradicts work this same product already
    shipped.

    ★It is left behind, NOT lost: the report stays owned by the deactivated
    account and `orphaned_owners` still lists it, so an admin can move one
    deliberately if somebody asks for it. Anyone "fixing" this by re-including
    conversations should read that sentence first.

    ★The working report in the same batch is asserted too. Without it this test
    passes just as happily against a successor rule that moves nothing at all,
    which is the failure mode that makes refusal-only tests worthless.
    """
    async with async_session_maker() as db:
        org = await _org(db)
        leaver, leaver_m = await _member(db, org)
        heir, _ = await _member(db, org)

        dashboard = await _report(db, org, leaver, working=True)
        chat = await _report(db, org, leaver, working=False)

        leaver_m.successor_user_id = str(heir.id)
        leaver.is_active = False
        await db.flush()

        result = await svc.on_member_deactivated(db, org, str(leaver.id))
        assert result is not None

        await db.refresh(dashboard)
        await db.refresh(chat)
        assert str(dashboard.user_id) == str(heir.id), (
            "the dashboard did not move, so this test would pass against a rule "
            "that transfers nothing"
        )
        assert str(chat.user_id) == str(leaver.id), (
            "a chat-only report was handed to the successor"
        )
        assert result.conversations_left_behind == 1, (
            "the result does not report what it left, so no screen can say so "
            "and the person is simply never told"
        )
