from app.models.widget import Widget
from app.models.report import Report
from app.schemas.widget_schema import WidgetCreate, WidgetUpdate, WidgetSchema
from app.schemas.step_schema import StepSchema
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.step_service import StepService
from app.models.step import Step
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
import pandas as pd
import io
import logging
import csv
from app.models.user import User
from app.models.organization import Organization

class WidgetService:
    def __init__(self):
        self.step_service = StepService()

    async def create_widget(self, db: AsyncSession, report_slug: str, widget_data: WidgetCreate, current_user: User, organization: Organization) -> WidgetSchema:
        report = await db.execute(select(Report).filter(Report.slug == report_slug))
        report = report.scalar_one_or_none()

        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        messages = widget_data.messages
        new_message = widget_data.new_message

        del widget_data.messages
        del widget_data.new_message

        widget = Widget(report_id=report.id, **widget_data.dict())
        self._set_widget_slug(db, widget)
        self._set_widget_as_published(db, widget)

        db.add(widget)
        await db.commit()
        await db.refresh(widget)

        # Create the first step associated with this widget
        # step_schema = self.step_service.create_step(db, widget.id, new_message=new_message, messages=messages)
        # db.refresh(widget)

        w = WidgetSchema(title=widget.title,slug=widget.slug,status=widget.status,x=widget.x,y=widget.y,width=widget.width,height=widget.height)
                    
        return w
    
    async def run_widget_step(self, db: AsyncSession, widget: Widget, current_user: User, organization: Organization) -> WidgetSchema:
        step = await self._get_last_step(db, widget.id)

        if not step:
            raise ValueError("Step not found")
        # Run as the triggering user so user_required connections resolve
        # their credentials (mirrors the artifact rerun path).
        return await self.step_service.rerun_step(db, step.id, current_user=current_user)

    async def get_widgets_by_report(self, db_session, report_id: str, current_user: User, organization: Organization) -> list[WidgetSchema]:
        from app.ai.llm.pii.display import display_redaction
        from app.dependencies import async_session_maker
        # ★The route gate authorizes the report against the ORGANIZATION only.
        # Under strict mode that is not enough: a member could list the widgets
        # of a report they are refused when they ask for the report itself.
        # This applies the one definition of report visibility.
        from app.core.report_access import assert_report_visible
        await assert_report_visible(db_session, report_id, current_user, organization)
        report = await db_session.execute(select(Report).filter(Report.id == report_id))
        report = report.scalar_one_or_none()
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")
        widgets = await db_session.execute(select(Widget).filter(Widget.report_id == report.id).filter(Widget.status != 'archived'))
        widgets = widgets.scalars().all()
        async with display_redaction(str(organization.id) if organization else None, async_session_maker):
            return [
                WidgetSchema.from_orm(widget).copy(update={"last_step": await self._get_last_step(db_session, widget.id)})
                for widget in widgets
            ]
    
    async def get_widgets_for_public_report(self, db_session, report_id: str, user=None) -> list[WidgetSchema]:
        """The chart widgets behind a shared report link.

        ★This gated on `report.status != 'published'`, and that is not the same
        question. `report_service` sets `status = 'published' if visibility !=
        'none'`, so a report shared with the ORGANIZATION ('internal') or with
        NAMED PEOPLE ('shared') is 'published' too. An anonymous caller with the
        id therefore received the widgets of an org-only dashboard — including
        `last_step`, which carries the generated SQL and the result rows.
        Measured live 2026-08-09; the eleven sibling `/r/` routes in report.py
        all take `current_user_optional` and do this correctly, these two were
        the outliers.

        `_check_visibility` is the canonical rule: 401 when a login would help,
        403 when it would not, silence when access is allowed.
        """
        report = await db_session.execute(select(Report).filter(Report.id == report_id))
        report = report.scalar_one_or_none()
        if report is None:
            # Was an unguarded attribute access on None — an unknown id raised
            # AttributeError and surfaced as a 500 rather than a 404.
            raise HTTPException(status_code=404, detail="Report not found")

        from app.services.report_service import ReportService
        await ReportService()._check_visibility(
            db_session, report, 'artifact_visibility', user
        )

        widgets = await db_session.execute(select(Widget).filter(Widget.report_id == report.id).filter(Widget.status != 'archived'))
        widgets = widgets.scalars().all()
        return [
            WidgetSchema.from_orm(widget).copy(update={"last_step": await self._get_last_step(db_session, widget.id)})
            for widget in widgets
        ]
    
    async def get_published_widgets_for_report(self, db_session, report_id: str) -> list[WidgetSchema]:
        # Existence check via the id column only — a bare select(Report) would
        # trigger the mapper-level selectin cascade and hydrate the whole
        # report graph (every step version's data) just to look up widgets.
        report_row = await db_session.execute(select(Report.id).filter(Report.id == report_id))
        if report_row.first() is None:
            raise HTTPException(status_code=404, detail="Report not found")

        widgets = await db_session.execute(select(Widget).filter(Widget.report_id == report_id).filter(Widget.status != 'archived'))
        widgets = widgets.scalars().all()
        return [
            WidgetSchema.from_orm(widget).copy(update={"last_step": await self._get_last_step(db_session, widget.id)})
            for widget in widgets
        ]

    async def get_widget_by_id(self, db_session, widget_id: str, current_user: User, organization: Organization) -> WidgetSchema:
        from app.ai.llm.pii.display import display_redaction
        from app.dependencies import async_session_maker
        widget = await db_session.execute(select(Widget).filter(Widget.id == widget_id))
        widget = widget.scalar_one_or_none()
        if widget is None:
            # Was an unguarded attribute access below — an unknown id raised
            # AttributeError and surfaced as a 500 rather than a 404.
            raise HTTPException(status_code=404, detail="Widget not found")
        # Authorize through the widget's OWN parent, not the report id in the
        # path: the two are checked against each other at the route, but a
        # service must not assume its caller did that.
        from app.core.report_access import assert_report_visible
        await assert_report_visible(db_session, widget.report_id, current_user, organization)
        async with display_redaction(str(organization.id) if organization else None, async_session_maker):
            return WidgetSchema.from_orm(widget).copy(update={"last_step": await self._get_last_step(db_session, widget.id)})

    async def update_widget(self, db_session, widget_id, widget_data: WidgetUpdate, current_user: User, organization: Organization):
        widget = await db_session.execute(select(Widget).filter(Widget.id == widget_id))
        widget = widget.scalar_one_or_none()
        if widget:
            for key, value in widget_data.dict().items():
                # Skip height/width if they are 0
                if key in ['height', 'width'] and value == 0:
                    continue
                if value is not None:
                   setattr(widget, key, value)
            await db_session.commit()
            await db_session.refresh(widget)
        return widget

    async def delete_widget(self, db_session, widget_id: str, current_user: User, organization: Organization):
        widget = await db_session.execute(select(Widget).filter(Widget.id == widget_id))
        widget = widget.scalar_one_or_none()
        if widget:
            widget.status = 'draft'
            await db_session.commit()
            await db_session.refresh(widget)
        return widget
    
    async def get_widget_by_id_and_step(self, db_session, widget_id: str, step_id: str, current_user: User, organization: Organization) -> WidgetSchema:
        widget = await db_session.execute(select(Widget).filter(Widget.id == widget_id))
        widget = widget.scalar_one_or_none()

        step = await db_session.execute(select(Step).filter(Step.id == step_id))
        step = step.scalar_one_or_none()

        step_schema = StepSchema.from_orm(step)
        from app.ai.llm.pii.display import load_and_redact_grid
        from app.dependencies import async_session_maker
        redacted = await load_and_redact_grid(
            step_schema.data, str(organization.id) if organization else None, async_session_maker
        )
        if redacted is not step_schema.data:
            step_schema = step_schema.model_copy(update={"data": redacted})
        return WidgetSchema.from_orm(widget).copy(update={"last_step": step_schema})

    async def export_widget_to_csv(self, db_session, widget_id: str, current_user: User, organization: Organization) -> str:
        logging.info(f"Starting CSV export for widget {widget_id}")
        try:
            widget = await db_session.execute(select(Widget).filter(Widget.id == widget_id))
            widget = widget.scalar_one_or_none()
            if not widget:
                logging.error(f"Widget {widget_id} not found")
                raise ValueError(f"Widget {widget_id} not found")
            
            last_step = await self._get_last_step(db_session, widget.id)
            logging.info(f"Got last step: {last_step}")

            # ★★★A chart whose grid is no longer stored is the COMMON case, not an
            # edge one: measured on the live install, 196 of 351 widget steps have
            # no 'columns' — 171 because `data` is NULL outright. The maintenance
            # service purges `data`/`data_model` on old steps deliberately (see
            # `_get_last_step`, which defaults them back to {}), so a raw
            # `data['columns']` raised KeyError on 56% of charts and the route
            # turned it into `500 Internal server error during export: 'columns'`.
            # A person clicking Download CSV was told the server broke.
            # ★This surfaced only after the route's permission gate was repaired:
            # `object_id` used to resolve from `widget_id` and get looked up as a
            # Report, so the export 404'd for everyone and this call never ran.
            data = (last_step.data if last_step else None) or {}
            columns = data.get('columns')
            if not columns:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This chart's results are no longer stored, so there is "
                        "nothing to download. Re-run the report and try again."
                    ),
                )

            # ★Column dicts are grid descriptors, not a fixed schema. Fall back
            # through the keys the grid itself accepts rather than assuming
            # 'headerName' is present — one missing key would restore the 500.
            headers = [
                col.get('headerName') or col.get('field') or col.get('name') or f"column_{i}"
                for i, col in enumerate(columns)
            ]
            df = pd.DataFrame(data.get('rows') or [], columns=headers)

            return df

        except HTTPException:
            # Already a deliberate, worded answer — must not be relabelled as a
            # server error by the handler below.
            raise
        except Exception as e:
            logging.error(f"Error during CSV export: {str(e)}")
            raise


    async def _set_widget_slug(self, db: AsyncSession, widget: Widget):
        title_slug = widget.title.replace(" ", "-").lower()
        title_slug = "".join(e for e in title_slug if e.isalnum() or e == "-")

        _uuid = uuid.uuid4().hex[:4]

        while (await db.execute(select(Report).filter(Report.slug == (title_slug + "-" + _uuid)))).scalar_one_or_none():
            _uuid = uuid.uuid4().hex[:6]
        else:
            title_slug = title_slug + "-" + _uuid
            widget.slug = title_slug
    
    async def _set_widget_as_published(self, db: AsyncSession, widget: Widget):
        widget.status = 'published'

    async def _get_last_step(self, db_session: AsyncSession, widget_id: str) -> StepSchema | None:
        last_step = await db_session.execute(select(Step).filter(Step.widget_id == widget_id).order_by(Step.created_at.desc()).limit(1))
        last_step = last_step.scalar_one_or_none()
        if last_step:
            # Ensure data and data_model are dictionaries, defaulting to empty dict if None
            # (maintenance service purges these fields for old steps)
            from app.ai.llm.pii.display import redact_grid_display
            step_dict = {
                **last_step.__dict__,
                'data': redact_grid_display(last_step.data or {}),
                'data_model': last_step.data_model or {}
            }
            return StepSchema.model_validate(step_dict)
        return None