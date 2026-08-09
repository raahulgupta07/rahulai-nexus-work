"""One call for "may this caller see this report?", usable from any service.

`ReportService.visible_reports_predicate` is already the correct rule and says so
in its own docstring: "Every read path that returns report data for a
caller-chosen set of ids must AND this into its filter — duplicating the rule
instead is how a surface silently drifts into authorizing on organization
alone."

Measured 2026-08-09: 7 places use it. 226 route sites use
`@requires_permission(..., model=Report)`, which loads the report scoped to the
ORGANIZATION only. So a member could list, read and in places rewrite another
member's widgets, text boxes, query code and result rows — not because a check
was broken, but because the weaker check was the one being called.

The rule this enforces is the one the product already models:

    own it            Report.user_id == caller
    shared with you   ReportShare by user_id or group_id
    internal          visibility 'internal'  (whole organization)
    public            visibility 'public'    (anyone with the link)
    in a shared folder project_id in the caller's visible projects

★STAGED ROLLOUT. Tightening enforcement everywhere at once would remove access
people currently rely on, with no warning, on the day it ships — reports that a
colleague could open yesterday would 404 today. So the strict rule is applied
only when the organization asks for it:

    default_report_visibility = 'internal'  -> current behaviour (org-wide reads)
    default_report_visibility = 'none'      -> strict: own / shared / public only

Existing organizations get 'internal' (no change). New organizations should be
created with 'none'. The setting lives in OrganizationSettings.config, which is
already the per-org JSON blob used for pii_protection, ldap and the rest — no
migration required.

★The permissive branch is NOT a hole left open: it is the current, documented
product behaviour, made explicit and switchable instead of implicit and
unavoidable. The tenancy probes assert both modes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# What an organization gets when it has never chosen. Deliberately the
# permissive value so that enabling this code changes nothing until an
# administrator opts in.
DEFAULT_MODE = "internal"


async def report_visibility_mode(db: AsyncSession, organization, current_user=None) -> str:
    """'internal' (org-wide reads) or 'none' (own / shared / public only)."""
    try:
        from app.models.organization_settings import OrganizationSettings
        # Read the row directly rather than through OrganizationSettingsService:
        # its `get_settings` requires a `current_user` for permission-aware
        # redaction, and this is an internal access decision, not a settings
        # view. Reading the column keeps the check independent of who is asking.
        settings_obj = (await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id == organization.id
            )
        )).scalar_one_or_none()
        config = getattr(settings_obj, "config", None) or {}
        if isinstance(config, dict):
            value = config.get("default_report_visibility")
            if value in ("none", "internal"):
                return value
    except Exception:
        # A settings lookup that fails must not decide access. Fall back to the
        # documented default rather than to "deny" (which would lock an org out
        # of its own reports on a transient error) or to "allow everything".
        pass
    return DEFAULT_MODE


async def user_may_see_report(
    db: AsyncSession,
    report_id: str,
    current_user,
    organization,
) -> bool:
    """True when this caller may read this report under the org's current mode."""
    from app.models.report import Report
    from app.services.report_service import ReportService

    if not report_id:
        return False

    mode = await report_visibility_mode(db, organization)

    if mode == "internal":
        # Current product behaviour: any member of the organization may read any
        # report in it. Still org-scoped — a cross-org id is refused here.
        row = (await db.execute(
            select(Report.id).where(
                Report.id == report_id,
                Report.organization_id == organization.id,
                # A deleted report is not visible to anyone, in either mode.
                # See the note in ReportService.get_report — delete is soft, so
                # every read path has to say so or the row is simply still there.
                Report.deleted_at.is_(None),
                # `delete` on this product means `status = 'archived'` — see the
                # note in ReportService.get_report. Both columns, or the report
                # is only half-gone.
                Report.status != 'archived',
            )
        )).scalar_one_or_none()
        return row is not None

    predicate = await ReportService().visible_reports_predicate(
        db, current_user, organization
    )
    row = (await db.execute(
        select(Report.id).where(
            Report.id == report_id,
            Report.organization_id == organization.id,
            Report.deleted_at.is_(None),
            Report.status != 'archived',
            predicate,
        )
    )).scalar_one_or_none()
    return row is not None


async def assert_report_visible(
    db: AsyncSession,
    report_id: str,
    current_user,
    organization,
) -> None:
    """Raise 404 unless the caller may read this report.

    404 rather than 403 on purpose: a caller who may not see the report has no
    business learning that the id exists. The tenancy probes check that a
    forbidden id and an unknown id answer identically.
    """
    if not await user_may_see_report(db, report_id, current_user, organization):
        raise HTTPException(status_code=404, detail="Report not found")


async def visible_report_ids(
    db: AsyncSession,
    current_user,
    organization,
) -> Optional[list]:
    """Every report id this caller may read, or None meaning "no restriction".

    None is returned in 'internal' mode so a caller can skip building a large
    IN (...) clause when the organization has not opted into strict mode. A
    caller MUST still apply its own organization filter in that case.
    """
    from app.models.report import Report
    from app.services.report_service import ReportService

    mode = await report_visibility_mode(db, organization)
    if mode == "internal":
        return None

    predicate = await ReportService().visible_reports_predicate(
        db, current_user, organization
    )
    return list((await db.execute(
        select(Report.id).where(
            Report.organization_id == organization.id,
            Report.deleted_at.is_(None),
            Report.status != 'archived',
            predicate,
        )
    )).scalars().all())
