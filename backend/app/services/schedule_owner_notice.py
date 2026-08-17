"""Tell the people a scheduled dashboard is delivered to that it changed hands.

A dashboard with a live schedule is not just a page somebody owns — it is a
recurring email and inbox item other people rely on. When its owner changes,
those recipients see a familiar delivery start coming from a different name, and
today nothing explains that. This module sends the explanation.

**Delivery path.** Nothing new is invented here. A scheduled run already reaches
its audience two ways, both driven off ``ScheduledPrompt.notification_subscribers``
(``[{"type": "user", "id": …}, {"type": "email", "address": …}]``):

  * ``inbox_service.notify_users`` for the ``user`` subscribers — the in-app bell;
  * ``notification_service`` → org SMTP → global, for every resolved address.

``scheduled_prompt_service._notify`` is the model this follows, right down to
writing the inbox rows on the caller's session and handing the email off to a
background task. The recipients are the SCHEDULE's subscriber list, not the
report's shares: a share means "you may open this", a subscription means "this
arrives in your inbox on Tuesdays", and only the second group is affected by
whose name is on it.

★★★**This can never fail a transfer.** Every public entry point swallows, and
the transfer has already committed by the time it is called. The rule is
stronger on the automatic path, where the caller is a directory telling us
somebody has left: switching the account off is the security-critical act and
this is a courtesy on top of it. So a failure here is LOGGED at error level with
a traceback and then dropped. Never a bare ``except`` and never silent — a bare
``except`` around a config read hid a settings rename in this codebase for three
releases, and the failure mode of a courtesy that quietly stops going out is
that nobody ever learns it stopped.

★**No fan-out.** The recipient list is inverted before anything is sent: one
person who receives eleven of the moved dashboards gets ONE email listing them,
not eleven. A bulk offboarding is exactly the case this feature exists for and
exactly the case a naive per-report loop would turn into a mailbox flood
attributable to a single admin click.

★This module deliberately imports nothing from the transfer engine — it reads
the ledger (``ownership_transfers``) the engine already wrote, keyed by the batch
id the engine returns. That keeps it a reader of a recorded fact rather than a
second caller of a service whose authorization rules live elsewhere.
"""
from __future__ import annotations

import asyncio
from logging import getLogger
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ownership_transfer import OwnershipTransfer
from app.models.report import Report
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.user import User
from app.settings.config import settings

logger = getLogger(__name__)

# The ledger's spelling for a report. Kept as a literal rather than imported so
# this module stays a pure reader of the table; the value is documented on
# ``OwnershipTransfer.resource_type``.
_RESOURCE_REPORT = "report"

# Beyond this a message stops being readable and starts being a list. The rest
# are counted, not dropped — the email says how many more there are.
_MAX_LISTED = 10


def _base_url() -> str:
    try:
        return getattr(settings.dash_config, "base_url", "http://localhost:3000") or "http://localhost:3000"
    except Exception:  # noqa: BLE001 — a branding/config read must not stop a notice
        logger.warning("Could not resolve base_url for owner-change notice", exc_info=True)
        return "http://localhost:3000"


async def notify_schedule_subscribers_of_owner_change(
    db: AsyncSession,
    organization,
    *,
    batch_id: str,
    to_user_id: str,
    actor_user_id: Optional[str] = None,
    locale: Optional[str] = None,
) -> int:
    """Notify the subscribers of every live schedule whose report just moved.

    Reads the ledger rows written under ``batch_id`` — so the caller supplies
    nothing but the batch the transfer returned. Returns the number of people
    notified, for logging; **never raises**, including on a bad batch id.

    Call it AFTER the transfer has committed. A notice about a change that then
    rolls back is worse than no notice at all.
    """
    try:
        return await _run(
            db,
            organization,
            batch_id=batch_id,
            to_user_id=to_user_id,
            actor_user_id=actor_user_id,
            locale=locale,
        )
    except Exception as e:  # noqa: BLE001 — see the module docstring
        logger.error(
            "Owner-change notice failed for batch %s in org %s: %s",
            batch_id, getattr(organization, "id", None), e, exc_info=True,
        )
        return 0


async def _run(
    db: AsyncSession,
    organization,
    *,
    batch_id: str,
    to_user_id: str,
    actor_user_id: Optional[str],
    locale: Optional[str],
) -> int:
    org_id = str(organization.id)
    to_uid = str(to_user_id)

    moved_report_ids = list(
        (
            await db.execute(
                select(OwnershipTransfer.resource_id).where(
                    OwnershipTransfer.organization_id == org_id,
                    OwnershipTransfer.batch_id == str(batch_id),
                    OwnershipTransfer.resource_type == _RESOURCE_REPORT,
                    OwnershipTransfer.reverted_at.is_(None),
                    OwnershipTransfer.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not moved_report_ids:
        return 0

    # Only reports that are actually DELIVERED to somebody. A dashboard with no
    # live schedule has no audience to reassure, and telling every viewer of
    # every report that an owner changed is noise, not a courtesy.
    rows = (
        await db.execute(
            select(ScheduledPrompt, Report)
            .join(Report, Report.id == ScheduledPrompt.report_id)
            .where(
                ScheduledPrompt.report_id.in_(moved_report_ids),
                ScheduledPrompt.is_active.is_(True),
                ScheduledPrompt.deleted_at.is_(None),
                Report.organization_id == org_id,
                Report.deleted_at.is_(None),
                Report.status != "archived",
            )
        )
    ).all()
    if not rows:
        return 0

    owner = await db.get(User, to_uid)
    owner_name = (getattr(owner, "name", None) or getattr(owner, "email", None) or "someone else")

    base = _base_url()
    # recipient -> dashboards. Two maps, because the two channels address people
    # differently: the bell knows user ids, the mailbox knows addresses.
    by_user: dict[str, list[dict[str, str]]] = {}
    by_address: dict[str, list[dict[str, str]]] = {}
    user_ids_needing_email: set[str] = set()

    for sp, report in rows:
        title = sp.title or report.title or "Untitled"
        entry = {"title": title, "url": f"{base}/reports/{report.id}"}
        for sub in (sp.notification_subscribers or []):
            if not isinstance(sub, dict):
                continue
            if sub.get("type") == "user" and sub.get("id"):
                uid = str(sub["id"])
                # The new owner knows; the person who clicked knows. Neither
                # needs telling, and the actor is filtered again inside
                # ``notify_users``.
                if uid == to_uid or (actor_user_id and uid == str(actor_user_id)):
                    continue
                by_user.setdefault(uid, []).append(entry)
                user_ids_needing_email.add(uid)
            elif sub.get("type") == "email" and sub.get("address"):
                by_address.setdefault(str(sub["address"]), []).append(entry)

    if not by_user and not by_address:
        return 0

    # ---- in-app, on the caller's session (same as a scheduled run) ----
    if by_user:
        try:
            from app.models.notification import SOURCE_SCHEDULE
            from app.services.inbox_service import inbox_service

            for uid, entries in by_user.items():
                first = entries[0]["title"]
                if len(entries) == 1:
                    title = f'"{first}" has a new owner'
                    body = (
                        f"{owner_name} now looks after it. Nothing changes for you — "
                        "it will keep arriving on its usual schedule."
                    )
                    link = entries[0]["url"].replace(base, "") or "/"
                else:
                    title = "Some of the dashboards you receive have a new owner"
                    body = (
                        f"{owner_name} now looks after {len(entries)} of them, including "
                        f'"{first}". Nothing changes for you — they will keep arriving on '
                        "their usual schedule."
                    )
                    link = None
                await inbox_service.notify_users(
                    db,
                    organization_id=org_id,
                    user_ids=[uid],
                    # ★``SOURCE_SCHEDULE``, not a new source of its own. The
                    # inbox filters by source and the set is a closed registry
                    # (``models/notification.SOURCES``) that the screen renders
                    # from; this notice is about a scheduled delivery and
                    # belongs beside that schedule's other messages, which is
                    # also where a recipient would look for it.
                    source=SOURCE_SCHEDULE,
                    type="owner_changed",
                    title=title,
                    body=body,
                    link=link,
                    actor_user_id=str(actor_user_id) if actor_user_id else None,
                    # One row per person per handover, so a batch that moves
                    # eleven of their dashboards does not ring the bell eleven
                    # times.
                    group_key=f"owner_changed:{batch_id}",
                )
        except Exception as e:  # noqa: BLE001 — see the module docstring
            logger.error(
                "Owner-change in-app notice failed for batch %s: %s", batch_id, e,
                exc_info=True,
            )

    # ---- email addresses, resolved before the session can go away ----
    for uid in user_ids_needing_email:
        try:
            user = await db.get(User, uid)
        except Exception:  # noqa: BLE001
            logger.warning("Could not resolve subscriber %s for owner-change notice", uid, exc_info=True)
            continue
        if user and user.email:
            by_address.setdefault(user.email, []).extend(by_user.get(uid, []))

    org_locale = locale
    if org_locale is None:
        try:
            from app.dependencies import _locale_from_org

            org_locale = _locale_from_org(organization)
        except Exception:  # noqa: BLE001 — a locale lookup must not stop a notice
            logger.warning("Could not resolve locale for owner-change notice", exc_info=True)
            org_locale = None

    # De-duplicate per address: the same dashboard can reach one person as both
    # a user subscriber and a bare address.
    for address, entries in by_address.items():
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for e in entries:
            if e["url"] in seen:
                continue
            seen.add(e["url"])
            unique.append(e)
        _dispatch_email(
            address=address,
            owner_name=owner_name,
            dashboards=unique,
            organization_id=org_id,
            locale=org_locale,
        )

    notified = len(set(by_address) | set(by_user))
    logger.info(
        "Owner-change notice queued for %d recipient(s) across %d scheduled dashboard(s) "
        "(batch %s)", notified, len(rows), batch_id,
    )
    return notified


def _dispatch_email(
    *,
    address: str,
    owner_name: str,
    dashboards: list[dict[str, Any]],
    organization_id: str,
    locale: Optional[str],
) -> None:
    """Hand one recipient's email to a background task.

    Fire-and-forget, exactly like the scheduled-result email: the send talks to
    an SMTP server that can be slow or down, and the caller here is finishing a
    transfer request that must not wait on it.
    """
    from app.services.notification_service import notification_service

    listed = dashboards[:_MAX_LISTED]
    truncated = max(0, len(dashboards) - len(listed))

    async def _send() -> None:
        # ``send_owner_changed`` already swallows and logs; this second guard is
        # for anything that could go wrong before it is entered, because an
        # exception escaping a bare task is only ever seen as a warning from the
        # event loop at interpreter shutdown.
        try:
            await notification_service.send_owner_changed(
                recipient_email=address,
                owner_name=owner_name,
                dashboards=listed,
                truncated=truncated,
                organization_id=organization_id,
                locale=locale,
            )
        except Exception as e:  # noqa: BLE001 — see the module docstring
            logger.error("Owner-change email task failed for %s: %s", address, e, exc_info=True)

    try:
        asyncio.create_task(_send())
    except RuntimeError:
        # No running loop (a synchronous script, a test harness). Not an error
        # worth failing anything over, but say so rather than dropping it.
        logger.warning("No event loop for owner-change notice to %s; not sent", address)
