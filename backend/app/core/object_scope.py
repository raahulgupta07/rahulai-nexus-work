"""How an object that has no organization of its own gets authorized.

Four models that `@requires_permission(model=...)` is used with carry NEITHER
`organization_id` NOR `user_id`:

    Visualization   Widget   TextWidget   Step

They hang off a Report, and the Report is what carries ownership and visibility.
Before this module the decorator's object gate simply could not scope them:

  - it looked the object id up in a hardcoded list of eight parameter names
    (`report_id`, `completion_id`, `data_source_id`, `widget_id`, `memory_id`,
    `instruction_id`, `query_id`, `artifact_id`), so a route whose path
    parameter was `visualization_id` produced `object_id = None` and the ENTIRE
    object block — organization scope and `owner_only` alike — was skipped in
    silence;
  - and even when the id was found, the gate filtered on
    `model.organization_id`, an attribute these four do not have.

Measured on the live install 2026-08-09: an ordinary member read and then
PATCHED another member's chart through `/api/visualizations/{id}`, and the write
stuck. The only surviving check was `view_reports` / `update_reports`, which are
baseline permissions every member holds.

★The fix is NOT to add `visualization_id` to the hardcoded list. That would make
the gate evaluate `Visualization.organization_id` and raise AttributeError, and
`owner_only` would hit the "Object does not support ownership checks" branch —
turning a silent authorization hole into a 500 on a working page.

So: resolve the parent Report, then defer to the one definition of report
visibility (`ReportService.visible_reports_predicate`, whose own docstring says
"Every read path that returns report data for a caller-chosen set of ids must AND
this into its filter — duplicating the rule instead is how a surface silently
drifts into authorizing on organization alone").
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Model class name -> the attribute holding its parent report id.
# Keyed by NAME rather than by class so this module imports nothing at module
# scope and cannot create an import cycle with the models package.
REPORT_SCOPED_MODELS = {
    "Visualization": "report_id",
    "Widget": "report_id",
    "TextWidget": "report_id",
    "Step": "report_id",
}


def is_report_scoped(model) -> bool:
    """True when this model must be authorized through its parent Report."""
    return getattr(model, "__name__", None) in REPORT_SCOPED_MODELS


def has_own_organization(model) -> bool:
    """True when the model carries its own organization_id column."""
    return hasattr(model, "organization_id")


async def load_report_scoped_object(
    db: AsyncSession,
    model,
    object_id: str,
    current_user,
    organization,
    *,
    owner_only: bool = False,
):
    """Load a report-scoped object only if the caller may see its report.

    Returns the object, or None when it does not exist or the caller may not
    reach it. The caller turns None into a 404 — deliberately not a 403, so the
    route cannot be used to discover which ids exist.
    """
    from app.models.report import Report
    from app.services.report_service import ReportService

    attr = REPORT_SCOPED_MODELS.get(getattr(model, "__name__", ""), "report_id")

    obj = (await db.execute(
        select(model).where(model.id == object_id)
    )).scalar_one_or_none()
    if obj is None:
        return None

    report_id = getattr(obj, attr, None)
    if not report_id:
        # An object with no parent cannot be authorized, so it is not served.
        # Failing closed here matters: these models have no owner of their own,
        # so there is nothing else to fall back on.
        return None

    predicate = await ReportService().visible_reports_predicate(
        db, current_user, organization
    )
    stmt = select(Report).where(
        Report.id == report_id,
        Report.organization_id == organization.id,
        predicate,
    )
    report = (await db.execute(stmt)).scalar_one_or_none()
    if report is None:
        return None

    if owner_only and getattr(report, "user_id", None) != getattr(current_user, "id", None):
        # `owner_only` on a child object means "the parent report is yours".
        # Visibility alone is not enough for a write.
        return None

    return obj
