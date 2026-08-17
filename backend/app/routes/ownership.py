"""Handing your own work to a colleague.

Two halves, separated by a banner comment partway down. Everything ABOVE it is
scoped to the **caller's own content** and carries no permission string — the
query is the authorization. Everything BELOW acts on somebody else's content and
is decorator-gated. Keeping them in one file is deliberate: two files that both
move ownership is how the rules drift apart.

★Ownership is checked in the handler, not by a decorator. `owner_only=True`
exists and would be the obvious choice, but it answers "is this caller the owner
of this object" one object at a time; the bulk route needs "select exactly the
objects this caller owns", which is a filter, not a gate. Building the selection
from `Report.user_id == caller` means a caller can only ever hand over their own
work by construction — there is no id list to validate against.

★A miss answers **404 with the same body as an unknown id**, never 403. A 403
would confirm that an id exists and belongs to somebody else, which is the fact
being withheld; walk a range of ids and the status code maps the installation.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.permissions_decorator import requires_permission
from app.dependencies import get_async_db, get_current_organization
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.user import User
from app.schemas.ownership_schema import (
    ContentSummarySchema,
    DepartureRiskSchema,
    OrphanedOwnerSchema,
    OwnedItemSchema,
    SuccessorSchema,
    SuccessorUpdate,
    TransferRequest,
    TransferResultSchema,
)
from app.services import ownership_service as svc
from app.services.ownership_export import _safe, build_bundle

router = APIRouter(tags=["ownership"])

NOT_FOUND = "Object not found or access denied"


def _export_response(payload: bytes, who: str) -> Response:
    """A zip, named after the person whose work it is.

    ★The filename reaches a Content-Disposition header, so it is sanitized for
    the same reason the paths inside the zip are: a report title or a display
    name is user-supplied, and a newline in a header value is header injection,
    not a cosmetic problem.
    """
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    safe = _safe(who, "content") or "content"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}-export-{stamp}.zip"'
        },
    )


async def _record_export(
    db: AsyncSession,
    organization: Organization,
    *,
    actor_user_id: str,
    subject_user_id: str,
    stats,
) -> None:
    """★★★The real control on this feature.

    An administrator holding `full_admin_access` can already read every one of
    these reports through the ordinary API; a bundle is those same reads in one
    request. So withholding content here would protect nothing and break the
    backup — the honest limit is not blocking the capability but making bulk use
    of it **visible**. This row is what turns "somebody could have" into
    "somebody did, on this date, for this person, this much".

    ★Failures are swallowed. An audit backend that is down must not stop a
    person downloading their own work, and `audit_service.log` is called the
    same defensive way everywhere else in this codebase.
    """
    try:
        from app.ee.audit.service import audit_service

        await audit_service.log(
            db=db,
            organization_id=str(organization.id),
            action="content.exported",
            user_id=actor_user_id,
            resource_type="user_content",
            resource_id=subject_user_id,
            details={
                "self_export": actor_user_id == subject_user_id,
                "reports": stats.reports,
                "steps": stats.steps,
                "artifacts": stats.artifacts,
                "result_bytes": stats.result_bytes,
            },
        )
    except Exception:
        pass


logger = logging.getLogger(__name__)


def _refused(exc: svc.TransferRefused) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.message)


async def _tell_the_subscribers(db, organization, result, *, to_user_id, actor_user_id):
    """People who RECEIVE a scheduled dashboard learn it changed hands.

    ★★★**Called after the commit, and unable to fail the request.** The transfer
    is the act; telling people is a courtesy on top of it. A handover that 500s
    because an SMTP host was briefly unreachable would be strictly worse than a
    handover nobody was told about — and the caller has already committed, so a
    raise here would report failure for something that definitively happened.

    ★One helper rather than the same six lines at three call sites. They drifted
    once already: only the offboarding path had this, so a member handing over
    their own scheduled dashboard told its subscribers nothing, which is the
    same event from the recipient's side.

    ★Deliberately NOT wired to ``undo``. An undo is a correction, almost always
    within minutes of the mistake it corrects; a second message saying the owner
    changed back would confuse more people than the first one informed. If undo
    ever grows a long tail, revisit this — it is a judgement, not a rule.
    """
    try:
        from app.services.schedule_owner_notice import (
            notify_schedule_subscribers_of_owner_change,
        )

        await notify_schedule_subscribers_of_owner_change(
            db,
            organization,
            batch_id=result.batch_id,
            to_user_id=str(to_user_id),
            actor_user_id=str(actor_user_id) if actor_user_id else None,
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "owner-change notice failed; the transfer itself stands and is "
            "unaffected"
        )


# ───────────────────────── what do I own ──────────────────────────────────


@router.get("/me/content/summary", response_model=ContentSummarySchema)
async def my_content_summary(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> ContentSummarySchema:
    summary = await svc.summarize(db, organization, str(user.id))
    return ContentSummarySchema(**summary.as_dict())


@router.get("/me/content", response_model=List[OwnedItemSchema])
async def my_content(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> List[OwnedItemSchema]:
    """The list behind the My content page.

    ★Both filters — a report you archived months ago is gone as far as you are
    concerned, and offering to hand it over is noise that makes the whole list
    less trustworthy.
    """
    rows = (
        (
            await db.execute(
                select(Report)
                .where(
                    Report.organization_id == str(organization.id),
                    Report.user_id == str(user.id),
                    Report.deleted_at.is_(None),
                    Report.status != "archived",
                )
                .order_by(Report.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    report_ids = [str(r.id) for r in rows]

    share_counts: dict[str, int] = {}
    scheduled: set[str] = set()
    # report_id -> the modes of its live artifacts. ★One batched query for the
    # whole page, not one per row — and the same query the transfer split reads,
    # so the label beside a row and the behaviour of an offboarding cannot
    # disagree about whether that row is a conversation.
    artifact_modes: dict[str, set[str]] = {}
    if report_ids:
        artifact_modes = await svc.artifact_modes_by_report(db, report_ids)
        for rid in (
            (
                await db.execute(
                    select(ReportShare.report_id).where(
                        ReportShare.report_id.in_(report_ids),
                        ReportShare.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        ):
            share_counts[str(rid)] = share_counts.get(str(rid), 0) + 1

        scheduled = {
            str(rid)
            for rid in (
                (
                    await db.execute(
                        select(ScheduledPrompt.report_id).where(
                            ScheduledPrompt.report_id.in_(report_ids),
                            ScheduledPrompt.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        }

    return [
        OwnedItemSchema(
            id=str(r.id),
            kind="report",
            title=r.title,
            type_label=svc.report_type_label(artifact_modes.get(str(r.id))),
            shared_with_count=share_counts.get(str(r.id), 0),
            has_schedule=str(r.id) in scheduled,
            runs_as_owner=(r.shared_run_identity == "creator"),
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in rows
    ]


# ───────────────────────── hand it over ───────────────────────────────────


@router.post("/reports/{report_id}/transfer", response_model=TransferResultSchema)
async def hand_over_report(
    report_id: str,
    body: TransferRequest,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> TransferResultSchema:
    """Hand one report — and everything hanging off it — to a colleague.

    ★For the OWNER the ownership check is the WHERE clause: selecting by
    ``user_id == caller`` means somebody else's report resolves to nothing and
    answers 404, identically to an id that was never real.

    ★A FULL ADMIN may also move a report they do not own — that is the whole
    point of the admin path, and it is how a departed person's dashboard gets
    rescued one item at a time without touching their other twenty-two. The
    admin branch is still org-scoped, so a cross-organization id answers 404
    exactly as before, and the two cases are recorded under different reasons
    so the ledger never blurs "I gave my work away" into "an administrator
    moved somebody's work".
    """
    scoped = [
        Report.id == str(report_id),
        Report.organization_id == str(organization.id),
        Report.deleted_at.is_(None),
        Report.status != "archived",
    ]

    owned = (
        await db.execute(select(Report.id).where(*scoped, Report.user_id == str(user.id)))
    ).scalar_one_or_none()

    is_admin = False
    if owned is None:
        from app.core.permission_resolver import resolve_permissions, FULL_ADMIN

        resolved = await resolve_permissions(db, str(user.id), str(organization.id))
        is_admin = FULL_ADMIN in resolved.org_permissions
        if is_admin:
            owned = (await db.execute(select(Report.id).where(*scoped))).scalar_one_or_none()

    if owned is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    try:
        result = await svc.transfer_reports(
            db,
            organization,
            [str(report_id)],
            to_user_id=body.to_user_id,
            actor_user_id=str(user.id),
            reason="admin_transfer" if is_admin else "self_handover",
            # An admin moving somebody else's report must not silently grant
            # that person a standing share on the way past.
            keep_access_for_previous_owner=False if is_admin else body.keep_access,
        )
    except svc.TransferRefused as exc:
        raise _refused(exc)

    await db.commit()
    await _tell_the_subscribers(
        db, organization, result,
        to_user_id=body.to_user_id, actor_user_id=str(user.id),
    )
    return TransferResultSchema(
        batch_id=result.batch_id,
        moved=result.moved,
        total=result.total,
        creator_identity_repointed=result.creator_identity_repointed,
        # Always zero on this path — agents are org-level and never move with a
        # single report — but passed rather than defaulted, so the four result
        # sites read identically and none of them is the one somebody forgot.
        credential_bound_data_sources=result.credential_bound_data_sources,
        # Also always zero: picking one report by hand is the deliberate act, so
        # nothing is ever withheld from it, whether it is a dashboard or a chat.
        conversations_left_behind=result.conversations_left_behind,
    )


@router.post("/me/content/transfer", response_model=TransferResultSchema)
async def hand_over_my_content(
    body: TransferRequest,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> TransferResultSchema:
    """Hand over a selection, or everything.

    ★``report_ids`` is INTERSECTED with what the caller owns rather than
    validated and rejected. A list containing somebody else's id silently moves
    only the caller's own — which is both safe and the behaviour a bulk action
    wants, since failing the whole batch because one id went stale mid-session
    is a worse experience than moving the rest.
    """
    try:
        if body.report_ids:
            owned = list(
                (
                    await db.execute(
                        select(Report.id).where(
                            Report.id.in_([str(r) for r in body.report_ids]),
                            Report.organization_id == str(organization.id),
                            Report.user_id == str(user.id),
                            Report.deleted_at.is_(None),
                            Report.status != "archived",
                        )
                    )
                )
                .scalars()
                .all()
            )
            result = await svc.transfer_reports(
                db,
                organization,
                owned,
                to_user_id=body.to_user_id,
                actor_user_id=str(user.id),
                reason="self_handover",
                keep_access_for_previous_owner=body.keep_access,
            )
        else:
            result = await svc.transfer_everything(
                db,
                organization,
                from_user_id=str(user.id),
                to_user_id=body.to_user_id,
                actor_user_id=str(user.id),
                reason="self_handover",
                include_projects=body.include_projects,
                keep_access_for_previous_owner=body.keep_access,
            )
    except svc.TransferRefused as exc:
        raise _refused(exc)

    await db.commit()
    await _tell_the_subscribers(
        db, organization, result,
        to_user_id=body.to_user_id, actor_user_id=str(user.id),
    )
    return TransferResultSchema(
        batch_id=result.batch_id,
        moved=result.moved,
        total=result.total,
        creator_identity_repointed=result.creator_identity_repointed,
        # ★The warning the confirmation screen shows. Nonzero only on the
        # everything branch, which is exactly when it matters: these agents
        # either stop answering for the recipient or silently reach the system
        # credentials, and a count of zero here would say neither happened.
        credential_bound_data_sources=result.credential_bound_data_sources,
        # ★Zero on both branches of this route, and that is the point: this is
        # the DELIBERATE path. A person handing their work to a colleague is
        # handing over their conversations too, and withholding them here would
        # be the product overruling a choice it just asked them to make.
        conversations_left_behind=result.conversations_left_behind,
    )


@router.post("/me/transfers/{batch_id}/undo", response_model=TransferResultSchema)
async def undo_my_transfer(
    batch_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> TransferResultSchema:
    """Walk back a handover you made.

    ★Scoped to batches this caller was the ACTOR of — not the from_user and not
    the to_user. Someone who receives content should not be able to push it
    back onto the person who gave it to them; that is a transfer of their own
    and goes through the normal route, where it is recorded as such.
    """
    from app.models.ownership_transfer import OwnershipTransfer

    mine = (
        await db.execute(
            select(OwnershipTransfer.id).where(
                OwnershipTransfer.batch_id == str(batch_id),
                OwnershipTransfer.organization_id == str(organization.id),
                OwnershipTransfer.actor_user_id == str(user.id),
                OwnershipTransfer.deleted_at.is_(None),
            )
        )
    ).first()
    if mine is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    try:
        result = await svc.undo(db, organization, str(batch_id), actor_user_id=str(user.id))
    except svc.TransferRefused as exc:
        raise HTTPException(status_code=409, detail=exc.message)

    await db.commit()
    return TransferResultSchema(
        batch_id=result.batch_id,
        moved=result.moved,
        total=result.total,
        # ★An undo restores the owner column; it does not re-derive the warning.
        # ``svc.undo`` leaves this at 0 and that is honest — the agents went
        # back, so there is nothing left for anyone to act on.
        credential_bound_data_sources=result.credential_bound_data_sources,
        # ★An undo walks the ledger, and the ledger only ever recorded rows that
        # MOVED. A conversation left behind was never in it, so there is nothing
        # to put back and this is honestly zero — not a dropped count.
        conversations_left_behind=result.conversations_left_behind,
    )


# ───────────────────────── successor ──────────────────────────────────────


@router.get("/me/successor", response_model=SuccessorSchema)
async def get_my_successor(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> SuccessorSchema:
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == str(user.id),
                Membership.organization_id == str(organization.id),
            )
        )
    ).scalar_one_or_none()
    if membership is None or not membership.successor_user_id:
        return SuccessorSchema()

    successor = (
        await db.execute(
            select(User).where(User.id == str(membership.successor_user_id))
        )
    ).scalar_one_or_none()
    if successor is None:
        # Nominated somebody who has since been cleaned up. Report it as unset
        # rather than as a broken id — the resolver treats it the same way.
        return SuccessorSchema()

    return SuccessorSchema(
        successor_user_id=str(successor.id),
        successor_name=successor.name,
        successor_email=successor.email,
    )


@router.put("/me/successor", response_model=SuccessorSchema)
async def set_my_successor(
    body: SuccessorUpdate,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> SuccessorSchema:
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == str(user.id),
                Membership.organization_id == str(organization.id),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    if body.successor_user_id:
        if str(body.successor_user_id) == str(user.id):
            raise HTTPException(
                status_code=400,
                detail="You cannot name yourself — pick someone who would still be here.",
            )
        try:
            # Same rule as a transfer recipient: active person OR service
            # account, and bound to this organization.
            await svc.assert_can_receive(db, organization, str(body.successor_user_id))
        except svc.TransferRefused as exc:
            raise _refused(exc)
        membership.successor_user_id = str(body.successor_user_id)
    else:
        membership.successor_user_id = None

    await db.commit()
    return await get_my_successor(db=db, organization=organization, user=user)


@router.get("/me/content/export")
async def export_my_content(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
) -> Response:
    """Download a copy of your own work.

    ★No permission string, like every other route in this half: it selects on
    `Report.user_id == caller`, so there is nothing here a caller could ask for
    that is not already theirs. Auditing it anyway is not suspicion — it is what
    makes the admin export below auditable without the two rows meaning
    different things, and `self_export` distinguishes them.
    """
    payload, stats = await build_bundle(db, organization, str(user.id))
    await _record_export(
        db,
        organization,
        actor_user_id=str(user.id),
        subject_user_id=str(user.id),
        stats=stats,
    )
    return _export_response(payload, user.name or user.email or "my")


# ═════════════════════════════════════════════════════════════════════════
# ADMIN — transferring on somebody else's behalf
#
# Everything above is the caller's own work and needs no permission string:
# the query IS the authorization. Everything below acts on another person's
# content, so it is gated on `full_admin_access` — the wildcard an owner or
# admin holds, checked by the decorator against resolved RBAC rather than the
# legacy role column.
#
# ★These are deliberately in the same file as the member routes rather than in
# organization.py. Two files that both move ownership is how the rules drift
# apart; one file means the next person reads both halves before changing
# either. The separator is doing the work a second module would have done.
# ═════════════════════════════════════════════════════════════════════════


@router.get(
    "/organizations/{organization_id}/members/{membership_id}/content-summary",
    response_model=ContentSummarySchema,
)
@requires_permission('manage_settings')
async def member_content_summary(
    organization_id: str,
    membership_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> ContentSummarySchema:
    """How much this person owns. Backs the Owns column and the Remove dialog.

    Read-only, so it is gated on `manage_settings` rather than the full-admin
    wildcard: an administrator looking at the members list needs the number in
    order to decide, and seeing a count is not the same act as moving the
    content behind it.
    """
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.id == str(membership_id),
                Membership.organization_id == str(organization.id),
            )
        )
    ).scalar_one_or_none()
    if membership is None or not membership.user_id:
        # A pending invite owns nothing by construction; answering with zeroes
        # rather than 404 keeps the members list simple.
        return ContentSummarySchema()

    summary = await svc.summarize(db, organization, str(membership.user_id))
    return ContentSummarySchema(**summary.as_dict())


@router.get(
    "/organizations/{organization_id}/orphaned-content",
    response_model=List[OrphanedOwnerSchema],
)
@requires_permission('manage_settings')
async def orphaned_content(
    organization_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> List[OrphanedOwnerSchema]:
    """Work whose owner can no longer sign in.

    ★★★**The only surface in the product that can see this content.** Report
    listings filter on `Report.user_id == current_user.id` for "mine" and on
    org membership for the rest, so a dashboard owned by a deactivated account
    is in nobody's list — it is invisible rather than missing. Without this
    route an administrator has to already know whose work is stranded before
    any of the transfer paths are usable.

    Read-only and gated on `manage_settings`, the same rule as the Owns column:
    seeing that content needs an owner is not the same act as moving it.
    """
    return [OrphanedOwnerSchema(**row) for row in await svc.orphaned_owners(db, organization)]


@router.get(
    "/organizations/{organization_id}/departure-risk",
    response_model=List[DepartureRiskSchema],
)
@requires_permission('manage_settings')
async def departure_risk(
    organization_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> List[DepartureRiskSchema]:
    """If any of these people left tomorrow, what would break?

    ★★★**The route beside it is a post-mortem; this one is the warning.**
    `orphaned-content` can only speak once somebody's account is already off,
    by which time the dashboards have stopped and the agents have stopped and
    nobody holds the sign-in that made them work. Nothing in the product says
    so beforehand. This answers it while there is still time to name a
    successor or move an agent — the same counting, one day earlier.

    Read-only and gated on `manage_settings`, deliberately the same rule as the
    Owns column and the orphan listing above: seeing that something is at risk
    is a read, not an act. Everything it reports is already visible to an
    administrator one object at a time; what it adds is that nobody has to know
    which objects to look at first.

    ★Returns only people with something at risk, worst first, and somebody who
    has named a successor sits below everybody who has not. An absent member is
    an answer — nothing of theirs would stop — not a gap.
    """
    return [
        DepartureRiskSchema(**row)
        for row in await svc.departure_risk_for_organization(db, organization)
    ]


@router.get("/organizations/{organization_id}/members/{membership_id}/content-export")
@requires_permission('full_admin_access')
async def export_member_content(
    organization_id: str,
    membership_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> Response:
    """Download a copy of somebody else's work, before or after they leave.

    ★★★Gated on the full-admin wildcard, **not** `manage_settings` — unlike the
    read-only count and the orphan listing beside it. Those answer "how much";
    this hands over the contents, including the generated code and the last
    computed rows. It is the heaviest read in the feature and is deliberately
    the same gate as moving the content.

    ★Works for a deprovisioned account, which is the point: `build_bundle`
    selects on the OWNER's id and never touches their account.
    """
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.id == str(membership_id),
                Membership.organization_id == str(organization.id),
            )
        )
    ).scalar_one_or_none()
    if membership is None or not membership.user_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    subject = (
        await db.execute(select(User).where(User.id == str(membership.user_id)))
    ).scalar_one_or_none()

    payload, stats = await build_bundle(db, organization, str(membership.user_id))
    await _record_export(
        db,
        organization,
        actor_user_id=str(current_user.id),
        subject_user_id=str(membership.user_id),
        stats=stats,
    )
    return _export_response(
        payload, (getattr(subject, "name", None) or getattr(subject, "email", None) or "member")
    )


@router.post(
    "/organizations/{organization_id}/members/{membership_id}/transfer-content",
    response_model=TransferResultSchema,
)
@requires_permission('full_admin_access')
async def transfer_member_content(
    organization_id: str,
    membership_id: str,
    body: TransferRequest,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> TransferResultSchema:
    """Move one person's WORKING content to somebody else.

    ★★★**Not their conversations.** A Report on this product is the chat thread;
    a dashboard or deck is an Artifact hanging off it, and 224 of 262 live
    reports here carry neither. So "transfer everything" on an offboarding
    screen would mostly mean handing one person's private chat history to
    another, which is the thing releases 0.0.531.9/.10 gated six report routes
    `owner_only` to prevent. This path takes what still does a job — a live
    artifact, a live schedule, or a live share — and leaves the rest with the
    departing account, where `orphaned-content` still lists it and any of the
    deliberate paths can move it if somebody actually asks. The result carries
    `conversations_left_behind` so the screen can say so rather than looking
    like it half-worked.

    ★Works whether that person is still here, already deactivated, or was
    deprovisioned by the directory months ago — which is the whole point of the
    admin path. `summarize` and `transfer_everything` select on the OWNER's id
    and never touch the owner's account, so a disabled account is not an
    obstacle. (`assert_can_receive` applies to the RECIPIENT only, and correctly
    refuses a deactivated one.)

    ★`keep_access` defaults to False here and True on the member path. Handing
    over a project you still work on is not the same act as offboarding someone
    who has left, and silently granting a departed person a share on their way
    out would be exactly wrong.
    """
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.id == str(membership_id),
                Membership.organization_id == str(organization.id),
            )
        )
    ).scalar_one_or_none()
    if membership is None or not membership.user_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    try:
        result = await svc.transfer_everything(
            db,
            organization,
            from_user_id=str(membership.user_id),
            to_user_id=body.to_user_id,
            actor_user_id=str(current_user.id),
            reason="admin_transfer",
            include_projects=body.include_projects,
            # ★Not exposed on TransferRequest, deliberately. An administrator
            # offboarding somebody is not in a position to consent on that
            # person's behalf to their chat history changing hands, so this is a
            # property of the path rather than a checkbox on it. A specific
            # conversation that genuinely needs moving goes through
            # `/reports/{id}/transfer`, one at a time, on the record.
            include_conversations=False,
            keep_access_for_previous_owner=False,
        )
    except svc.TransferRefused as exc:
        raise _refused(exc)

    await db.commit()
    await _tell_the_subscribers(
        db, organization, result,
        to_user_id=body.to_user_id, actor_user_id=str(current_user.id),
    )
    return TransferResultSchema(
        batch_id=result.batch_id,
        moved=result.moved,
        total=result.total,
        creator_identity_repointed=result.creator_identity_repointed,
        # ★The path that most needs it: an admin offboarding somebody has no
        # other way to learn that some of the agents they just moved will stop
        # answering until the recipient signs in.
        credential_bound_data_sources=result.credential_bound_data_sources,
        # ★The one site where this is normally NONZERO, and the reason the field
        # exists at all: without it the admin sees a small number against a much
        # larger Owns count and reasonably concludes the transfer failed.
        conversations_left_behind=result.conversations_left_behind,
    )
