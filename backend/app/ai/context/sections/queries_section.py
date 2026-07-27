from typing import ClassVar, List, Optional, Dict, Any
from pydantic import BaseModel
from app.ai.context.sections.base import ContextSection, xml_tag, xml_escape


class QueryVisualizationSummary(BaseModel):
    id: str
    title: str = ""
    status: Optional[str] = None
    view: Optional[Dict[str, Any]] = None


class QueryObservation(BaseModel):
    query_id: str
    query_title: str
    default_step_id: Optional[str] = None
    default_step_title: Optional[str] = None
    row_count: int = 0
    column_names: List[str] = []
    data_model: Optional[Dict[str, Any]] = None
    stats: Dict[str, Any] = {}
    data_preview: Optional[str] = None
    visualizations: List[QueryVisualizationSummary] = []
    # DEF-005: a query whose visualization carries `superseded_by` was recomputed
    # later in the same report. Without these fields every query renders as a peer,
    # so a re-run that produced a different number looks like a second, equally
    # valid fact — which is how one question ended up answered with three
    # contradictory totals under one title.
    superseded_by: Optional[str] = None
    superseded_at: Optional[str] = None
    superseded_reason: Optional[str] = None


class QueriesSection(ContextSection):
    tag_name: ClassVar[str] = "queries"

    items: List[QueryObservation] = []

    def render(self) -> str:
        parts: List[str] = []
        # DEF-005: current queries first, superseded ones last, so recency ordering
        # reinforces the explicit marker below instead of fighting it. A stable sort
        # keeps the existing (created_at) order inside each group, and with nothing
        # superseded the partition is a no-op — the normal path stays byte-identical.
        items = list(self.items or [])
        if any(it.superseded_by for it in items):
            items = [it for it in items if not it.superseded_by] + [it for it in items if it.superseded_by]
        for it in items:
            inner: List[str] = []
            if it.superseded_by:
                # DEF-005: state the CONSEQUENCE, not just the status. A bare
                # <superseded/> tag reads as trivia; the model has to be told that
                # these numbers are dead, that it must not blend them with the
                # newer result, and which result replaced them.
                detail = f"Replacement visualization id: {xml_escape(it.superseded_by)}."
                if it.superseded_at:
                    detail += f" Replaced at: {xml_escape(it.superseded_at)}."
                if it.superseded_reason:
                    detail += f" Reason: {xml_escape(it.superseded_reason)}."
                inner.append(xml_tag(
                    "superseded",
                    "THIS RESULT IS OUT OF DATE. It was REPLACED by a newer query in this "
                    "report, which recomputed the same thing. "
                    + detail
                    + " Do NOT report, total, average, or reconcile the numbers below — "
                    "they are superseded values, not a second valid answer. Use the "
                    "replacement query's result as the current truth. This entry is kept "
                    "only so you can explain what changed between attempts if asked.",
                    {"superseded_by": it.superseded_by},
                ))
            if it.default_step_title:
                inner.append(xml_tag("step", xml_escape(it.default_step_title), {"id": it.default_step_id or ""}))
            inner.append(xml_tag("rows", str(it.row_count)))
            if it.column_names:
                inner.append(xml_tag("columns", ", ".join(xml_escape(c) for c in it.column_names)))
            if it.data_model is not None:
                inner.append(xml_tag("data_model", xml_escape(str(it.data_model))))
            if it.stats:
                inner.append(xml_tag("stats", xml_escape(str(it.stats))))
            if it.data_preview:
                inner.append(xml_tag("data_preview", xml_escape(it.data_preview)))
            # Visualizations
            if it.visualizations:
                viz_parts: List[str] = []
                for v in it.visualizations:
                    v_inner: List[str] = []
                    if v.status:
                        v_inner.append(xml_tag("status", xml_escape(v.status)))
                    if v.view is not None:
                        v_inner.append(xml_tag("view", xml_escape(str(v.view))))
                    viz_parts.append(xml_tag("visualization", "\n".join(v_inner), {"id": v.id, "title": v.title}))
                inner.append(xml_tag("visualizations", "\n\n".join(viz_parts)))
            parts.append(xml_tag("query", "\n".join(inner), {"id": it.query_id, "title": it.query_title}))
        return xml_tag(self.tag_name, "\n\n".join(parts))


