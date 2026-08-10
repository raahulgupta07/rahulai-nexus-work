from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.services.widget_service import WidgetService
from app.schemas.widget_schema import WidgetCreate, WidgetUpdate, WidgetSchema
from app.dependencies import get_db, get_async_db, get_current_organization
from app.models.user import User
from app.models.organization import Organization
from app.core.auth import current_user, current_user_optional
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import io
from app.core.permissions_decorator import requires_permission
from app.models.report import Report
from app.ee.audit.service import audit_service

router = APIRouter(tags=["widgets"])
widget_service = WidgetService()

#@router.post("/reports/{report_slug}/widgets", response_model=WidgetSchema)
async def create_widget(report_slug: str, widget: WidgetCreate, current_user: User = Depends(current_user), organization: Organization = Depends(get_current_organization), db: AsyncSession = Depends(get_async_db)):
    return await widget_service.create_widget(db, report_slug, widget, current_user, organization)

@router.get("/reports/{report_id}/widgets", response_model=list[WidgetSchema])
@requires_permission('view_reports', model=Report)
async def get_widgets_by_report(report_id: str, current_user: User = Depends(current_user), organization: Organization = Depends(get_current_organization), db: AsyncSession = Depends(get_async_db)):
    # ★★★POSITIONAL, and the two were the wrong way round. The signature is
    # `(db_session, report_id, current_user, organization)`; this passed
    # `(db, report_id, organization, current_user)`.
    #
    # Upstream ships the same swap and it is LATENT there: their body never
    # reads `current_user`, and the only use of the other is
    # `str(organization.id)` — which quietly looked up PII display redaction
    # under the USER's id instead of the organization's.
    #
    # 0.0.528.12 added `assert_report_visible(db, report_id, current_user,
    # organization)` to that body, and from then on the visibility check was
    # handed an Organization where it expects a User and vice versa. Measured
    # live 2026-08-09: `GET /reports/{id}/widgets` answered 404 "Report not
    # found" for EVERY report, to the report's own owner, while the widgets
    # sat in the database. Found by chat-matrix T4, not by any suite.
    return await widget_service.get_widgets_by_report(db, report_id, current_user, organization)

# ★★★`report_id` is declared on every handler below even where the body does not
# use it, and that is load-bearing rather than tidy.
#
# `@requires_permission(model=Report)` reads the object id out of the handler's
# BOUND ARGUMENTS. If the function does not declare `report_id`, FastAPI never
# binds it, the decorator resolves `object_id = None`, and the entire object
# block — organization scope and ownership — is skipped in silence. The route
# reads as gated and enforces only `view_reports`, which every member holds.
#
# Measured live 2026-08-09: a member listed, read and WROTE another member's
# widgets, and could do it while passing a report id that does not exist at all,
# because the path segment was never bound to anything.
#
# The second half is `_widget_in_report`: binding the id makes the gate check
# the PARENT, but nothing tied the child to that parent, so passing your OWN
# report id with someone else's widget id still worked.

async def _widget_in_report(db: AsyncSession, widget_uuid: str, report_id: str) -> None:
    """404 unless this widget really belongs to the report just authorized."""
    from app.models.widget import Widget
    from sqlalchemy import select as _select
    row = (await db.execute(
        _select(Widget.id).where(Widget.id == widget_uuid, Widget.report_id == report_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Widget not found")


@router.get("/reports/{report_id}/widgets/{widget_uuid}", response_model=WidgetSchema)
@requires_permission('view_reports', model=Report)
async def get_widget_by_id(report_id: str, widget_uuid: str, current_user: User = Depends(current_user), organization: Organization = Depends(get_current_organization), db: AsyncSession = Depends(get_async_db)):
    await _widget_in_report(db, widget_uuid, report_id)
    # Same swap as the list route above, same consequence: this one 404s on a
    # widget the caller owns. `update_widget` and `delete_widget` below already
    # pass `(current_user, organization)` in the declared order, which is why
    # only the two READ routes were affected.
    return await widget_service.get_widget_by_id(db, widget_uuid, current_user, organization)

@router.put("/reports/{report_id}/widgets/{widget_uuid}", response_model=WidgetUpdate)
@requires_permission('update_reports', model=Report)
async def update_widget(report_id: str, widget_uuid: str, widget: WidgetUpdate, current_user: User = Depends(current_user), organization: Organization = Depends(get_current_organization), db: AsyncSession = Depends(get_async_db)):
    await _widget_in_report(db, widget_uuid, report_id)
    return await widget_service.update_widget(db, widget_uuid, widget, current_user, organization)

@router.delete("/reports/{report_id}/widgets/{widget_uuid}")
@requires_permission('delete_reports', model=Report)
async def delete_widget(report_id: str, widget_uuid: str, current_user: User = Depends(current_user), organization: Organization = Depends(get_current_organization), db: AsyncSession = Depends(get_async_db)):
    await _widget_in_report(db, widget_uuid, report_id)
    return await widget_service.delete_widget(db, widget_uuid, current_user, organization)

@router.get("/reports/{report_id}/widgets/{widget_id}/export")
@requires_permission('view_reports', model=Report)
async def export_widget(
    report_id: str,   # ★bound so the model=Report gate can resolve its object
    widget_id: str,
    request: Request,
    current_user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
    db: AsyncSession = Depends(get_async_db)
):
    logging.info(f"CSV export request received for widget {widget_id}")
    # Same pairing check as the read/write routes: the gate above authorized the
    # REPORT, so the widget must actually belong to it. Exporting is a read of
    # the full result grid, so it needs the same proof as reading the widget.
    await _widget_in_report(db, widget_id, report_id)
    try:
        # ★The call was missing `current_user` and `organization` and had never
        # been executed: the gate resolved `object_id` from `widget_id` and
        # looked it up as a Report, so every export 404'd before reaching here.
        # Binding `report_id` fixed the gate and exposed the real call.
        csv_data = await widget_service.export_widget_to_csv(
            db, widget_id, current_user, organization
        )
        logging.info(f"CSV data generated, size: {len(csv_data)} characters")

        # Create a StringIO object from the csv_data
        csv_buffer = io.StringIO()
        csv_data.to_csv(csv_buffer, index=False)

        try:
            await audit_service.log(
                db=db,
                organization_id=organization.id,
                action="data.exported",
                user_id=current_user.id,
                resource_type="widget",
                resource_id=widget_id,
                details={"format": "csv"},
                request=request,
            )
        except Exception:
            pass

        response = Response(content=csv_buffer.getvalue(), media_type="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename={widget_id}.csv"
        return response

    except Exception as e:
        logging.error(f"Error in export_widget route: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error during export: {str(e)}")

@router.get("/r/{report_id}/widgets", response_model=list[WidgetSchema])
# ★Takes the OPTIONAL user, like the eleven sibling /r/ routes in report.py.
# Without it an org-only or named-people share cannot be told apart from a
# public one, and a signed-in viewer who legitimately may see the report
# would be refused alongside the anonymous caller who may not.
async def get_widgets_for_public_report(report_id: str, user: User | None = Depends(current_user_optional), db: AsyncSession = Depends(get_async_db)):
    return await widget_service.get_widgets_for_public_report(db, report_id, user)
