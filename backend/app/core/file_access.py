"""One answer to "may this caller read this file?".

Three file routes used to answer it with `@requires_permission('manage_files')`
and an organization filter, and nothing else. `manage_files` sits in
`HIDDEN_PERMISSION_CATEGORIES` — it is granted to EVERY member of an
organization and is not shown in the role editor — so in practice those routes
authorized on organization membership alone.

Measured 2026-08-09 against the live install: an ordinary member requested an
embed token for a file attached to another member's report, one they are refused
when they ask for the report itself, and received HTTP 200. Presenting that
token with **no Authorization header at all** returned 858,769 bytes of the
file's real contents. The mint route's own docstring described the design
honestly — "Authorized by org membership here" — which is the bug, not a
mitigation: an embed token is a bearer credential that outlives the session and
needs no login, so minting one must require at least as much access as reading
the file directly.

The rule here is deliberately built ON `report_service.visible_reports_predicate`
rather than beside it. That function's docstring is explicit that it is "the ONE
definition" and that "duplicating the rule instead is how a surface silently
drifts into authorizing on organization alone" — which is precisely what
happened. So this module resolves reachability and delegates the visibility
judgement.

A file is readable when ANY of these holds:

  - the caller uploaded it (`file.user_id`)
  - it is attached to a report the caller may see (the predicate above)
  - it is attached to a data source the caller may use — file-backed agents load
    their own CSVs, and the member who can query the agent must be able to read
    the file behind it
  - the caller holds `full_admin_access`

Organization scope is assumed to have been applied by the caller and is asserted
here as a belt-and-braces check, never as the whole answer.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def user_may_read_file(
    db: AsyncSession,
    file,
    current_user,
    organization,
    *,
    permissions=None,
) -> bool:
    """True when this caller may read this file's bytes or text.

    `permissions` is an already-resolved permission set when the caller has one
    (the routes do); it is re-resolved here when omitted, so a future caller
    cannot get a weaker answer by forgetting to pass it.
    """
    if file is None:
        return False

    # Org scope is the caller's job, but a mismatch here is always a refusal —
    # never let a cross-org object through on the strength of a later check.
    if organization is not None and getattr(file, "organization_id", None) != organization.id:
        return False

    if current_user is not None and getattr(file, "user_id", None) == current_user.id:
        return True

    if permissions is None:
        from app.core.permission_resolver import resolve_permissions
        # ★IDs, not objects. Passing the ORM objects raises ArgumentError deep in
        # SQLAlchemy ("not legal as a SQL literal value"), which POISONS the
        # session — every later statement then fails with PendingRollbackError
        # and the refusal surfaces as a 500. Worse, the traceback is unreadable:
        # rendering it hits BaseSchema.__repr__, which lazy-loads an expired
        # attribute and dies with RecursionError, hiding the real cause.
        permissions = await resolve_permissions(
            db, current_user.id, organization.id
        )
    if permissions is not None and permissions.has_org_permission("full_admin_access"):
        return True

    # ── attached to a report the caller may see ──────────────────────────────
    from app.models.report import Report
    from app.services.report_service import ReportService

    report_ids = await _report_ids(db, file)
    if report_ids:
        predicate = await ReportService().visible_reports_predicate(
            db, current_user, organization
        )
        visible = (await db.execute(
            select(Report.id).filter(Report.id.in_(report_ids), predicate)
        )).scalars().all()
        if visible:
            return True

    # ── attached to a data source the caller may use ─────────────────────────
    # A file-backed agent loads its own uploaded CSVs; a member who may query
    # the agent must be able to read the file behind it, or the agent breaks.
    ds_ids = await _data_source_ids(db, file)
    if ds_ids:
        from app.services.data_source_service import DataSourceService
        # `get_data_sources` with show_all=False is the list this caller may
        # actually use — the same call the agents page makes. Reusing it keeps
        # this in step with data-source access rules instead of restating them.
        allowed = await DataSourceService().get_data_sources(
            db, current_user, organization, show_all=False
        )
        if ds_ids & {str(getattr(a, "id", None)) for a in (allowed or [])}:
            return True

    return False


async def _report_ids(db: AsyncSession, file) -> list:
    """The ids of reports this file is attached to.

    ★Always an explicit query, never `file.reports`. Touching an unloaded
    relationship on an AsyncSession raises MissingGreenlet, and — the part that
    cost an hour — that exception POISONS the session, so every subsequent
    statement fails with PendingRollbackError. Wrapping the attribute access in
    try/except does not save you: by the time the handler runs, the transaction
    is already unusable and the refusal surfaces as a 500 instead of a 404.
    """
    from app.models.report_file_association import report_file_association
    return list((await db.execute(
        select(report_file_association.c.report_id).filter(
            report_file_association.c.file_id == file.id
        )
    )).scalars().all())


async def _data_source_ids(db: AsyncSession, file) -> set:
    """The ids of data sources this file backs. Explicit query, same reason."""
    from app.models.data_source import DataSource
    try:
        from app.models.data_source_file_association import (  # type: ignore
            data_source_file_association as assoc,
        )
    except ImportError:
        # The association table is named differently on this build; fall back to
        # the file's own column if it has one, and otherwise refuse rather than
        # guess a weaker rule.
        ds_id = getattr(file, "data_source_id", None)
        return {str(ds_id)} if ds_id else set()
    return {str(i) for i in (await db.execute(
        select(assoc.c.data_source_id).filter(assoc.c.file_id == file.id)
    )).scalars().all()}
