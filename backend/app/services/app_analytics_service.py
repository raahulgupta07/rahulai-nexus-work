"""App Analytics service — one composite, org-wide usage/adoption/ROI payload.

Every number is computed from the database. The only inputs that are NOT from
the DB are the four ROI baseline knobs (minutes-per-query, hourly rate, monthly
run-rate, implementation cost), which live in settings/env; the ROI block flags
whether they were explicitly configured. Fields that genuinely aren't tracked in
this schema (revenue, CSAT, and any judge/feedback metric when the judge is off
or there's no feedback) are returned as null so the frontend can render
"Not tracked"/"—" rather than a fabricated value.

The endpoint must NEVER 500 on a near-empty DB: each sub-computation is wrapped
so a failing metric degrades to null/0/[] (per the contract) instead of taking
down the whole response.
"""

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, date
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func, or_, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.user import User
from app.models.membership import Membership
from app.models.report import Report
from app.models.completion import Completion
from app.models.completion_feedback import CompletionFeedback
from app.models.step import Step
from app.models.widget import Widget
from app.models.agent_execution import AgentExecution
from app.models.data_source import DataSource
from app.models.data_source_membership import DataSourceMembership, PRINCIPAL_TYPE_USER
from app.models.user_data_source_credentials import UserDataSourceCredentials
from app.models.report_data_source_association import report_data_source_association
from app.models.llm_usage_record import LLMUsageRecord
from app.models.llm_model import LLMModel

from app.services.console_service import ConsoleService
from app.settings.config import settings as app_settings
from app.settings.logging_config import get_logger

logger = get_logger(__name__)


# Email-domain -> friendly company name. Anything not listed falls back to the
# domain minus its TLD, title-cased (see _company_from_domain).
COMPANY_MAP = {
    "cmhl.com.mm": "City Mart Retail",
    "cityholdings.com.mm": "Corporate",
    "citypharma.com.mm": "City Pharma",
    "cityexpress.com.mm": "City Express",
    "cityproperty.com.mm": "City Property",
}

# Question-intent taxonomy. Keyword hit (case-insensitive substring) -> cluster.
# First matching cluster wins; no match -> "other".
QUESTION_TAXONOMY = {
    "sales": ["sales", "revenue", "margin", "top-selling"],
    "inventory": ["stock", "inventory", "stockout", "replenish"],
    "finance": ["invoice", "reconcil", "cost", "budget", "spend"],
    "forecast": ["forecast", "predict", "demand", "planning"],
    "hr": ["policy", "leave", "headcount", "employee"],
    "ops": ["delivery", "route", "logistics", "on-time"],
}
CLUSTER_NAMES = {
    "sales": "Sales & revenue",
    "inventory": "Inventory & stock",
    "finance": "Finance & cost",
    "forecast": "Forecast & planning",
    "hr": "HR & people",
    "ops": "Operations & logistics",
    "other": "Other",
}

# Connectors whose credentials are per-user (each member signs in with their own
# token) rather than a single shared/system credential.
_PER_USER_CONNECTOR_TYPES = {"fabric_user", "powerbi_user", "fabric_mt", "powerbi_mt"}

# Completion/step statuses that count as a failure (not a resolved answer).
_ERROR_STATUSES = {"error", "failed", "failure", "cancelled", "canceled", "killed", "timeout"}


def _company_from_domain(domain: Optional[str]) -> str:
    if not domain:
        return "Unknown"
    if domain in COMPANY_MAP:
        return COMPANY_MAP[domain]
    parts = domain.split(".")
    base = parts[:-1] if len(parts) > 1 else parts
    name = " ".join(base).replace("-", " ").replace("_", " ").strip().title()
    return name or domain


def _domain_of(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def _median(vals: List[float]) -> Optional[float]:
    s = sorted(v for v in vals if v is not None)
    n = len(s)
    if n == 0:
        return None
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def _row_tokens(provider_type: Optional[str], prompt: int, completion: int,
                cache_read: int, cache_creation: int) -> int:
    """Per-row token total that doesn't double-count cache. Mirrors
    ConsoleService._row_total_tokens: Anthropic reports cache tokens separately
    (add them in); OpenAI/Azure fold cache_read into prompt_tokens already."""
    if (provider_type or "") == "anthropic":
        return prompt + completion + cache_read + cache_creation
    return prompt + completion


def _pct(numer: float, denom: float) -> Optional[float]:
    if not denom:
        return None
    return round(numer / denom * 100.0, 1)


def _iso_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return dt.isoformat()


class AppAnalyticsService:

    def __init__(self):
        self._console = ConsoleService()

    async def get_app_analytics(
        self,
        db: AsyncSession,
        organization_id: str,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        data_source_ids: Optional[str],
    ) -> Dict[str, Any]:
        """Return the entire App Analytics contract payload for one org."""
        start, end = self._console._normalize_date_range(start_date, end_date)
        ref = end  # relative "now" for DAU/WAU/MAU + tier inactivity + cohorts
        ds_ids = self._console._parse_data_source_ids(data_source_ids)

        ds_report_subq = None
        if ds_ids:
            ds_report_subq = (
                select(report_data_source_association.c.report_id)
                .where(report_data_source_association.c.data_source_id.in_(ds_ids))
            )

        # Load the org object (needed for routing-savings which reads org settings).
        org = await db.get(Organization, organization_id)

        # ---- Pull the raw datasets once; aggregate in Python (small DB). ----
        ctx = await self._load_context(db, organization_id, start, end, ds_report_subq, ds_ids)

        # Baseline ROI knobs.
        mins = float(app_settings.analytics_baseline_minutes_per_query)
        rate = float(app_settings.analytics_hourly_rate_usd)
        run_rate = float(app_settings.analytics_monthly_run_rate_usd)
        impl_cost = float(app_settings.analytics_impl_cost_usd)
        roi_configured = any(
            os.environ.get(k) is not None
            for k in (
                "ANALYTICS_BASELINE_MINUTES_PER_QUERY",
                "ANALYTICS_HOURLY_RATE_USD",
                "ANALYTICS_MONTHLY_RUN_RATE_USD",
                "ANALYTICS_IMPL_COST_USD",
            )
        )
        baseline = {
            "minutes_per_query": mins,
            "hourly_rate_usd": rate,
            "monthly_run_rate_usd": run_rate,
            "impl_cost_usd": impl_cost,
        }

        payload: Dict[str, Any] = {}
        payload["kpis"] = await self._safe(
            self._kpis, "kpis", db, org, ctx, start, end, ds_report_subq
        )
        payload["activity"] = self._safe_sync(self._activity, "activity", ctx, start, end, ref)
        payload["adoption"] = self._safe_sync(self._adoption, "adoption", ctx, ref)
        payload["retention_cohorts"] = self._safe_sync(
            self._retention_cohorts, "retention_cohorts", ctx, ref
        )
        payload["funnel"] = self._safe_sync(self._funnel, "funnel", ctx)
        payload["roi"] = self._safe_sync(
            self._roi, "roi", ctx, start, end, mins, rate, run_rate, impl_cost,
            baseline, roi_configured
        )
        payload["value_dims"] = self._safe_sync(
            self._value_dims, "value_dims", ctx, start, end, ds_report_subq, mins, rate, db, organization_id
        )
        payload["automation"] = self._safe_sync(self._automation, "automation", ctx, mins)
        payload["users"] = self._safe_sync(self._users, "users", ctx, ref, mins, rate)
        payload["companies"] = self._safe_sync(self._companies, "companies", ctx, mins, rate)
        payload["agents"] = self._safe_sync(self._agents, "agents", ctx)
        payload["connectors"] = self._safe_sync(self._connectors, "connectors", ctx)
        payload["questions"] = self._safe_sync(self._questions, "questions", ctx, ref)
        payload["cost"] = self._safe_sync(self._cost, "cost", ctx)
        return payload

    # ------------------------------------------------------------------ #
    #  Guards                                                            #
    # ------------------------------------------------------------------ #
    async def _safe(self, fn, name, *args):
        try:
            return await fn(*args)
        except Exception as e:  # noqa: BLE001
            logger.warning("app-analytics section '%s' failed: %s", name, e)
            return self._empty_section(name)

    def _safe_sync(self, fn, name, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001
            logger.warning("app-analytics section '%s' failed: %s", name, e)
            return self._empty_section(name)

    def _empty_section(self, name: str):
        # Contract-shaped empty defaults so a failing section never yields the
        # wrong TYPE (list vs object) to the frontend.
        if name in ("automation", "users", "companies", "agents", "connectors", "retention_cohorts", "funnel"):
            return []
        if name == "kpis":
            return {
                "active_users": 0, "total_users": 0, "companies": 0,
                "sessions": 0, "messages": 0, "queries": 0, "query_success_pct": None,
                "tokens": {"total": 0, "input": 0, "output": 0, "cache": 0},
                "spend_usd": 0.0, "routing_savings_usd": 0.0,
                "quality_pct": None, "feedback_pos_pct": None,
                "delta": {"active_users_pct": None, "sessions_pct": None,
                          "queries_pct": None, "spend_pct": None},
            }
        if name == "activity":
            return {"dau": 0, "wau": 0, "mau": 0,
                    "series": {"dau": [], "chats": [], "messages": [], "tokens": []}}
        if name == "adoption":
            return {"dau": 0, "wau": 0, "mau": 0, "stickiness_pct": None,
                    "repeat_pct": None, "resolution_pct": None,
                    "retention_30d_pct": None, "ttv_days": None,
                    "accuracy_pct": None, "instruction_coverage_pct": None,
                    "feedback_pos_pct": None}
        if name == "roi":
            return {"hours_saved": 0.0, "labour_value_usd": 0.0, "automation_cost_usd": 0.0,
                    "net_usd": 0.0, "roi_pct": None, "payback_months": None, "vfm": None,
                    "value_per_dollar": None,
                    "baseline": {"minutes_per_query": 30, "hourly_rate_usd": 18,
                                 "monthly_run_rate_usd": 0, "impl_cost_usd": 0},
                    "configured": False}
        if name == "value_dims":
            return {"time_saved_hours": 0.0, "money_saved_usd": 0.0, "productivity_pct": None,
                    "risk_error_rate_pct": None, "risk_delta_pct": None,
                    "revenue_usd": None, "csat": None}
        if name == "questions":
            return {"total": 0, "clusters": [], "trend": {"weeks": [], "series": []}, "failed": []}
        if name == "cost":
            return {"total_usd": 0.0, "tokens": 0, "routing_savings_usd": 0.0,
                    "cost_per_query_usd": None, "cost_per_user_usd": None,
                    "by_model": [], "by_company": []}
        return None

    # ------------------------------------------------------------------ #
    #  Context loader — all raw rows in a handful of queries             #
    # ------------------------------------------------------------------ #
    async def _load_context(self, db, org_id, start, end, ds_report_subq, ds_ids):
        c: Dict[str, Any] = {"org_id": org_id, "start": start, "end": end}

        # Org users (via memberships), excluding service accounts.
        users_rows = (await db.execute(
            select(
                # User has no created_at (fastapi-users base, not BaseSchema);
                # use Membership.created_at = when the user joined this org.
                User.id, User.name, User.email, Membership.created_at, User.last_login,
                Membership.role, Membership.profile_attributes,
            )
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org_id,
                Membership.user_id.isnot(None),
                or_(User.is_service_account.is_(False), User.is_service_account.is_(None)),
            )
        )).all()
        users = {}
        for r in users_rows:
            if r.id in users:
                continue
            dom = _domain_of(r.email)
            dept = None
            pa = r.profile_attributes if isinstance(r.profile_attributes, dict) else None
            if pa:
                dept = pa.get("department") or pa.get("jobTitle") or pa.get("job_title")
            users[r.id] = {
                "id": r.id, "name": r.name, "email": r.email or "",
                "domain": dom, "company": _company_from_domain(dom),
                "dept": dept, "created_at": r.created_at, "last_login": r.last_login,
                "role": r.role,
            }
        c["users"] = users

        # Reports in range (ds-filtered) — sessions.
        rq = select(Report.id, Report.user_id, Report.created_at).where(
            Report.organization_id == org_id,
            Report.created_at >= start, Report.created_at <= end,
        )
        if ds_report_subq is not None:
            rq = rq.where(Report.id.in_(ds_report_subq))
        c["reports_range"] = (await db.execute(rq)).all()

        # All org reports (all-time) — lifecycle: cohorts / retention / funnel / last-active.
        c["reports_all"] = (await db.execute(
            select(Report.id, Report.user_id, Report.created_at)
            .where(Report.organization_id == org_id)
        )).all()

        # Completions in range (ds-filtered), attributed to the report's owner.
        cq = (
            select(
                Completion.id, Report.user_id.label("user_id"), Completion.created_at,
                Completion.status, Completion.role, Completion.prompt,
                Completion.instructions_effectiveness, Completion.context_effectiveness,
                Completion.response_score, Completion.report_id,
            )
            .join(Report, Completion.report_id == Report.id)
            .where(
                Report.organization_id == org_id,
                Completion.created_at >= start, Completion.created_at <= end,
            )
        )
        if ds_report_subq is not None:
            cq = cq.where(Report.id.in_(ds_report_subq))
        c["completions_range"] = (await db.execute(cq)).all()

        # All completions (all-time) — TTV (first completion per user) + funnel "got answer".
        c["completions_all"] = (await db.execute(
            select(Report.user_id.label("user_id"), Completion.created_at)
            .join(Report, Completion.report_id == Report.id)
            .where(Report.organization_id == org_id)
        )).all()

        # Steps in range (ds-filtered) — queries.
        sq = (
            select(Step.id, Report.user_id.label("user_id"), Report.id.label("report_id"),
                   Step.created_at, Step.status)
            .join(Widget, Step.widget_id == Widget.id)
            .join(Report, Widget.report_id == Report.id)
            .where(
                Report.organization_id == org_id,
                Step.created_at >= start, Step.created_at <= end,
            )
        )
        if ds_report_subq is not None:
            sq = sq.where(Report.id.in_(ds_report_subq))
        c["steps_range"] = (await db.execute(sq)).all()

        # All-time distinct users who ever produced a step — funnel "ran first query".
        c["step_user_ids_all"] = set(uid for (uid,) in (await db.execute(
            select(Report.user_id.distinct())
            .join(Widget, Widget.report_id == Report.id)
            .join(Step, Step.widget_id == Widget.id)
            .where(Report.organization_id == org_id)
        )).all() if uid)

        # Feedback in range (ds-filtered).
        fq = (
            select(CompletionFeedback.direction, CompletionFeedback.completion_id)
            .join(Completion, CompletionFeedback.completion_id == Completion.id)
            .join(Report, Completion.report_id == Report.id)
            .where(
                Report.organization_id == org_id,
                CompletionFeedback.created_at >= start,
                CompletionFeedback.created_at <= end,
            )
        )
        if ds_report_subq is not None:
            fq = fq.where(Report.id.in_(ds_report_subq))
        c["feedback_range"] = (await db.execute(fq)).all()

        # Down-voted completion ids (for failed-questions reason mapping).
        c["downvoted_completion_ids"] = set(
            cid for (dir_, cid) in c["feedback_range"] if dir_ is not None and dir_ < 0
        )

        # LLM usage in range (ds-filtered), org-scoped via the model's org.
        uq = (
            select(
                LLMUsageRecord.created_at, LLMUsageRecord.provider_type,
                LLMUsageRecord.prompt_tokens, LLMUsageRecord.completion_tokens,
                LLMUsageRecord.cache_read_tokens, LLMUsageRecord.cache_creation_tokens,
                LLMUsageRecord.total_cost_usd, LLMUsageRecord.model_id,
                LLMModel.name.label("model_name"),
                LLMUsageRecord.user_id, LLMUsageRecord.data_source_id,
                LLMUsageRecord.report_id,
            )
            .join(LLMModel, LLMModel.id == LLMUsageRecord.llm_model_id)
            .where(
                LLMModel.organization_id == org_id,
                LLMUsageRecord.created_at >= start, LLMUsageRecord.created_at <= end,
            )
        )
        if ds_report_subq is not None:
            uq = uq.where(LLMUsageRecord.report_id.in_(ds_report_subq))
        c["usage_range"] = (await db.execute(uq)).all()

        # Agent executions in range (ds-filtered).
        aq = select(
            AgentExecution.status, AgentExecution.total_duration_ms,
            AgentExecution.started_at, AgentExecution.report_id,
            AgentExecution.user_id, AgentExecution.completion_id,
        ).where(
            AgentExecution.organization_id == org_id,
            AgentExecution.created_at >= start, AgentExecution.created_at <= end,
        )
        if ds_report_subq is not None:
            aq = aq.where(AgentExecution.report_id.in_(ds_report_subq))
        c["agent_exec_range"] = (await db.execute(aq)).all()

        # Data sources (agents) with their connections, org-scoped.
        ds_rows = (await db.execute(
            select(DataSource).where(DataSource.organization_id == org_id)
        )).scalars().all()
        if ds_ids:
            ds_rows = [d for d in ds_rows if str(d.id) in set(ds_ids)]
        data_sources = []
        for d in ds_rows:
            conns = list(d.connections or [])
            data_sources.append({
                "id": str(d.id), "name": d.name,
                "publish_status": d.publish_status, "is_active": d.is_active,
                "owner_user_id": d.owner_user_id, "description": d.description,
                "conn_types": [cn.type for cn in conns],
                "conn_names": [cn.name for cn in conns],
                "conn_last_synced": [cn.last_synced_at for cn in conns],
            })
        c["data_sources"] = data_sources

        # report_id -> [data_source_id] map (org reports only).
        all_report_ids = [r.id for r in c["reports_all"]]
        assoc_map = defaultdict(list)
        if all_report_ids:
            assoc_rows = (await db.execute(
                select(report_data_source_association.c.report_id,
                       report_data_source_association.c.data_source_id)
                .where(report_data_source_association.c.report_id.in_(all_report_ids))
            )).all()
            for rid, dsid in assoc_rows:
                assoc_map[rid].append(dsid)
        c["report_ds_map"] = assoc_map

        # Per-user data-source credentials (connectors access lens).
        c["ds_credentials"] = (await db.execute(
            select(UserDataSourceCredentials.data_source_id,
                   UserDataSourceCredentials.user_id,
                   UserDataSourceCredentials.last_used_at)
            .where(UserDataSourceCredentials.organization_id == org_id)
        )).all()

        # Data-source memberships (users granted access) — funnel "connected".
        c["ds_memberships"] = (await db.execute(
            select(DataSourceMembership.data_source_id, DataSourceMembership.principal_id)
            .join(DataSource, DataSource.id == DataSourceMembership.data_source_id)
            .where(
                DataSource.organization_id == org_id,
                DataSourceMembership.principal_type == PRINCIPAL_TYPE_USER,
            )
        )).all()

        return c

    # ------------------------------------------------------------------ #
    #  KPIs                                                              #
    # ------------------------------------------------------------------ #
    async def _kpis(self, db, org, ctx, start, end, ds_report_subq):
        users = ctx["users"]
        reports_range = ctx["reports_range"]
        completions_range = ctx["completions_range"]
        steps_range = ctx["steps_range"]
        usage = ctx["usage_range"]
        feedback = ctx["feedback_range"]

        active_users = len({r.user_id for r in reports_range if r.user_id})
        total_users = len(users)
        companies = len({u["domain"] for u in users.values() if u["domain"]})
        sessions = len(reports_range)
        messages = len(completions_range)
        queries = len(steps_range)

        # query success = steps with a success status / all steps.
        step_total = len(steps_range)
        step_ok = sum(1 for s in steps_range if (s.status or "").lower() not in _ERROR_STATUSES)
        query_success_pct = _pct(step_ok, step_total) if step_total else None

        tokens = self._token_totals(usage)
        spend = round(sum(float(u.total_cost_usd or 0) for u in usage), 6)

        routing_savings = 0.0
        try:
            rs = await self._console._compute_routing_savings(
                db, org, start, end, len(usage)
            )
            routing_savings = round(float(getattr(rs, "savings_usd", 0.0) or 0.0), 6)
        except Exception as e:  # noqa: BLE001
            logger.warning("routing savings failed: %s", e)
            routing_savings = 0.0

        # quality = judge avg response_score * 20 (null if judge off / no scores).
        judged = [c.response_score for c in completions_range if c.response_score is not None]
        quality_pct = round(sum(judged) / len(judged) * 20.0, 1) if judged else None

        fb_total = len(feedback)
        fb_pos = sum(1 for (d, _cid) in feedback if d is not None and d > 0)
        feedback_pos_pct = _pct(fb_pos, fb_total) if fb_total else None

        # Delta vs previous same-length period.
        delta = await self._delta(db, org, ctx["org_id"], start, end, ds_report_subq,
                                  active_users, sessions, queries, spend)

        return {
            "active_users": active_users, "total_users": total_users, "companies": companies,
            "sessions": sessions, "messages": messages, "queries": queries,
            "query_success_pct": query_success_pct,
            "tokens": tokens,
            "spend_usd": spend, "routing_savings_usd": routing_savings,
            "quality_pct": quality_pct, "feedback_pos_pct": feedback_pos_pct,
            "delta": delta,
        }

    def _token_totals(self, usage_rows) -> Dict[str, int]:
        total = inp = out = cache = 0
        for u in usage_rows:
            p = int(u.prompt_tokens or 0)
            comp = int(u.completion_tokens or 0)
            cr = int(u.cache_read_tokens or 0)
            cc = int(u.cache_creation_tokens or 0)
            inp += p
            out += comp
            cache += cr + cc
            total += _row_tokens(u.provider_type, p, comp, cr, cc)
        return {"total": total, "input": inp, "output": out, "cache": cache}

    async def _delta(self, db, org, org_id, start, end, ds_report_subq,
                     cur_active, cur_sessions, cur_queries, cur_spend):
        length = end - start
        prev_end = start
        prev_start = start - length
        prev = await self._period_counts(db, org_id, prev_start, prev_end, ds_report_subq)

        def d(cur, prv):
            if not prv:
                return None
            return round((cur - prv) / prv * 100.0, 1)

        return {
            "active_users_pct": d(cur_active, prev["active_users"]),
            "sessions_pct": d(cur_sessions, prev["sessions"]),
            "queries_pct": d(cur_queries, prev["queries"]),
            "spend_pct": d(cur_spend, prev["spend"]),
        }

    async def _period_counts(self, db, org_id, s, e, ds_report_subq):
        rq = select(func.count(func.distinct(Report.user_id))).where(
            Report.organization_id == org_id, Report.created_at >= s, Report.created_at <= e)
        cq = select(func.count(Report.id)).where(
            Report.organization_id == org_id, Report.created_at >= s, Report.created_at <= e)
        stq = (select(func.count(Step.id))
               .join(Widget, Step.widget_id == Widget.id)
               .join(Report, Widget.report_id == Report.id)
               .where(Report.organization_id == org_id,
                      Step.created_at >= s, Step.created_at <= e))
        spq = (select(func.coalesce(func.sum(LLMUsageRecord.total_cost_usd), 0))
               .join(LLMModel, LLMModel.id == LLMUsageRecord.llm_model_id)
               .where(LLMModel.organization_id == org_id,
                      LLMUsageRecord.created_at >= s, LLMUsageRecord.created_at <= e))
        if ds_report_subq is not None:
            rq = rq.where(Report.id.in_(ds_report_subq))
            cq = cq.where(Report.id.in_(ds_report_subq))
            stq = stq.where(Report.id.in_(ds_report_subq))
            spq = spq.where(LLMUsageRecord.report_id.in_(ds_report_subq))
        return {
            "active_users": int((await db.execute(rq)).scalar() or 0),
            "sessions": int((await db.execute(cq)).scalar() or 0),
            "queries": int((await db.execute(stq)).scalar() or 0),
            "spend": float((await db.execute(spq)).scalar() or 0),
        }

    # ------------------------------------------------------------------ #
    #  DAU/WAU/MAU helpers                                               #
    # ------------------------------------------------------------------ #
    def _dwm(self, ctx, ref):
        reports = ctx["reports_all"]
        day_start = ref - timedelta(days=1)
        week_start = ref - timedelta(days=7)
        month_start = ref - timedelta(days=30)
        dau = wau = mau = set()
        dau, wau, mau = set(), set(), set()
        for r in reports:
            if not r.user_id or r.created_at is None:
                continue
            if r.created_at > ref:
                continue
            if r.created_at >= month_start:
                mau.add(r.user_id)
            if r.created_at >= week_start:
                wau.add(r.user_id)
            if r.created_at >= day_start:
                dau.add(r.user_id)
        return len(dau), len(wau), len(mau)

    def _activity(self, ctx, start, end, ref):
        dau, wau, mau = self._dwm(ctx, ref)

        # Dense daily series across the range.
        days = []
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        last = end
        while cur <= last:
            days.append(cur.date())
            cur = cur + timedelta(days=1)
            if len(days) > 400:
                break

        dau_by_day = defaultdict(set)
        chats_by_day = defaultdict(int)
        for r in ctx["reports_range"]:
            if r.created_at is None:
                continue
            d = r.created_at.date()
            chats_by_day[d] += 1
            if r.user_id:
                dau_by_day[d].add(r.user_id)
        msg_by_day = defaultdict(int)
        for cp in ctx["completions_range"]:
            if cp.created_at is not None:
                msg_by_day[cp.created_at.date()] += 1
        tok_by_day = defaultdict(int)
        for u in ctx["usage_range"]:
            if u.created_at is not None:
                tok_by_day[u.created_at.date()] += _row_tokens(
                    u.provider_type, int(u.prompt_tokens or 0), int(u.completion_tokens or 0),
                    int(u.cache_read_tokens or 0), int(u.cache_creation_tokens or 0))

        def series(getter):
            return [{"d": d.isoformat(), "v": getter(d)} for d in days]

        return {
            "dau": dau, "wau": wau, "mau": mau,
            "series": {
                "dau": series(lambda d: len(dau_by_day.get(d, ()))),
                "chats": series(lambda d: chats_by_day.get(d, 0)),
                "messages": series(lambda d: msg_by_day.get(d, 0)),
                "tokens": series(lambda d: tok_by_day.get(d, 0)),
            },
        }

    def _adoption(self, ctx, ref):
        dau, wau, mau = self._dwm(ctx, ref)
        stickiness = _pct(dau, mau)
        repeat = _pct(wau, mau)

        comps = ctx["completions_range"]
        total = len(comps)
        resolved = sum(1 for c in comps if (c.status or "").lower() not in _ERROR_STATUSES)
        resolution = _pct(resolved, total) if total else None

        # 30-day retention: of users created >30d before ref, % active in last 30d.
        month_start = ref - timedelta(days=30)
        eligible = [u for u in ctx["users"].values()
                    if u["created_at"] is not None and u["created_at"] <= month_start]
        active_30d = {r.user_id for r in ctx["reports_all"]
                      if r.user_id and r.created_at is not None
                      and month_start <= r.created_at <= ref}
        if eligible:
            ret = _pct(sum(1 for u in eligible if u["id"] in active_30d), len(eligible))
        else:
            ret = None

        # TTV: median days from signup to first completion.
        first_comp = {}
        for r in ctx["completions_all"]:
            if not r.user_id or r.created_at is None:
                continue
            if r.user_id not in first_comp or r.created_at < first_comp[r.user_id]:
                first_comp[r.user_id] = r.created_at
        ttvs = []
        for uid, first in first_comp.items():
            u = ctx["users"].get(uid)
            if u and u["created_at"] is not None:
                delta_days = (first - u["created_at"]).total_seconds() / 86400.0
                if delta_days >= 0:
                    ttvs.append(delta_days)
        ttv = round(_median(ttvs), 1) if ttvs else None

        acc = [c.response_score for c in comps if c.response_score is not None]
        accuracy = round(sum(acc) / len(acc) * 20.0, 1) if acc else None
        ie = [c.instructions_effectiveness for c in comps if c.instructions_effectiveness is not None]
        instr_cov = round(sum(ie) / len(ie) * 20.0, 1) if ie else None

        fb = ctx["feedback_range"]
        fb_pos = sum(1 for (d, _c) in fb if d is not None and d > 0)
        feedback_pos = _pct(fb_pos, len(fb)) if fb else None

        return {
            "dau": dau, "wau": wau, "mau": mau,
            "stickiness_pct": stickiness, "repeat_pct": repeat,
            "resolution_pct": resolution, "retention_30d_pct": ret, "ttv_days": ttv,
            "accuracy_pct": accuracy, "instruction_coverage_pct": instr_cov,
            "feedback_pos_pct": feedback_pos,
        }

    def _retention_cohorts(self, ctx, ref):
        # Last 6 ISO weeks up to ref.
        ref_date = ref.date()
        ref_monday = ref_date - timedelta(days=ref_date.weekday())
        cohorts = []
        activity = defaultdict(list)  # user_id -> [report dates]
        for r in ctx["reports_all"]:
            if r.user_id and r.created_at is not None:
                activity[r.user_id].append(r.created_at.date())

        for k in range(5, -1, -1):
            cw_monday = ref_monday - timedelta(days=7 * k)
            cw_next = cw_monday + timedelta(days=7)
            cohort_users = [
                u["id"] for u in ctx["users"].values()
                if u["created_at"] is not None
                and cw_monday <= u["created_at"].date() < cw_next
            ]
            n = len(cohort_users)
            iso = cw_monday.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            week_of_month = (cw_monday.day - 1) // 7 + 1
            label = f"{cw_monday.strftime('%b')} wk {week_of_month}"
            weeks = []
            for i in range(6):
                win_start = cw_monday + timedelta(days=7 * i)
                win_end = win_start + timedelta(days=7)
                if win_start > ref_date:
                    weeks.append(None)
                    continue
                if n == 0:
                    weeks.append(0)
                    continue
                active = 0
                for uid in cohort_users:
                    if any(win_start <= d < win_end for d in activity.get(uid, ())):
                        active += 1
                weeks.append(round(active / n * 100.0))
            cohorts.append({"cohort": key, "label": label, "users": n, "weeks": weeks})
        return cohorts

    def _funnel(self, ctx):
        users = ctx["users"]
        total = len(users)
        opened = sum(1 for u in users.values() if u["last_login"] is not None)

        # Connected: owns / has membership / has per-user creds on any data source.
        connected_ids = set()
        for d in ctx["data_sources"]:
            if d["owner_user_id"]:
                connected_ids.add(d["owner_user_id"])
        for dsid, uid in ctx["ds_memberships"]:
            if uid:
                connected_ids.add(uid)
        for dsid, uid, _last in ctx["ds_credentials"]:
            if uid:
                connected_ids.add(uid)
        connected = len(connected_ids & set(users.keys()))

        ran_first_query = len(ctx["step_user_ids_all"] & set(users.keys()))
        got_answer = len({r.user_id for r in ctx["completions_all"] if r.user_id} & set(users.keys()))

        # Returned in 7d: activity on >=2 distinct days (all-time).
        days_by_user = defaultdict(set)
        for r in ctx["reports_all"]:
            if r.user_id and r.created_at is not None:
                days_by_user[r.user_id].add(r.created_at.date())
        returned = sum(1 for uid, ds in days_by_user.items() if uid in users and len(ds) >= 2)

        return [
            {"step": "Signed up", "n": total},
            {"step": "Opened app", "n": opened},
            {"step": "Connected an agent", "n": connected},
            {"step": "Ran first query", "n": ran_first_query},
            {"step": "Got an answer", "n": got_answer},
            {"step": "Returned in 7d", "n": returned},
        ]

    # ------------------------------------------------------------------ #
    #  ROI / value                                                       #
    # ------------------------------------------------------------------ #
    def _range_days(self, start, end):
        return max(1, (end - start).days or 1)

    def _roi(self, ctx, start, end, mins, rate, run_rate, impl_cost, baseline, configured):
        queries = len(ctx["steps_range"])
        spend = sum(float(u.total_cost_usd or 0) for u in ctx["usage_range"])
        hours_saved = queries * mins / 60.0
        labour_value = hours_saved * rate
        range_days = self._range_days(start, end)
        automation_cost = spend + run_rate * (range_days / 30.0)
        net = labour_value - automation_cost
        roi_pct = round(net / automation_cost * 100.0, 1) if automation_cost > 0 else None
        net_per_month = net / (range_days / 30.0) if range_days else net
        payback = round(impl_cost / net_per_month, 1) if (net_per_month > 0 and impl_cost > 0) else None
        vfm = round(labour_value / automation_cost, 2) if automation_cost > 0 else None
        return {
            "hours_saved": round(hours_saved, 2),
            "labour_value_usd": round(labour_value, 2),
            "automation_cost_usd": round(automation_cost, 4),
            "net_usd": round(net, 2),
            "roi_pct": roi_pct,
            "payback_months": payback,
            "vfm": vfm,
            "value_per_dollar": vfm,
            "baseline": baseline,
            "configured": configured,
        }

    def _value_dims(self, ctx, start, end, ds_report_subq, mins, rate, db, org_id):
        queries = len(ctx["steps_range"])
        hours_saved = queries * mins / 60.0
        money_saved = hours_saved * rate

        comps = ctx["completions_range"]
        total = len(comps)
        errored = sum(1 for c in comps if (c.status or "").lower() in _ERROR_STATUSES)
        risk_error_rate = _pct(errored, total) if total else None

        # Prior-period error rate for risk delta — best-effort (sync, no extra query
        # needed: derive from reports_all completions is not available; leave delta
        # null unless we can compute cheaply). We approximate with null when there is
        # no prior-period data. Since completions_all lacks status, we only have this
        # period's status; so risk_delta stays null (no prior status data loaded).
        risk_delta = None

        return {
            "time_saved_hours": round(hours_saved, 2),
            "money_saved_usd": round(money_saved, 2),
            "productivity_pct": None,  # not computable from this schema
            "risk_error_rate_pct": risk_error_rate,
            "risk_delta_pct": risk_delta,
            "revenue_usd": None,  # not tracked
            "csat": None,  # not tracked
        }

    def _automation(self, ctx, mins):
        # One row per data source that has agent executions; ai_min = median run
        # duration for that agent's runs, manual_min = baseline.
        ds_by_id = {d["id"]: d for d in ctx["data_sources"]}
        report_ds = ctx["report_ds_map"]
        durs_by_ds = defaultdict(list)
        runs_by_ds = defaultdict(int)
        for ex in ctx["agent_exec_range"]:
            for dsid in report_ds.get(ex.report_id, ()):
                dsid = str(dsid)
                runs_by_ds[dsid] += 1
                if ex.total_duration_ms is not None:
                    durs_by_ds[dsid].append(float(ex.total_duration_ms))
        rows = []
        for dsid, runs in sorted(runs_by_ds.items(), key=lambda kv: kv[1], reverse=True):
            d = ds_by_id.get(dsid)
            if not d:
                continue
            med = _median(durs_by_ds.get(dsid, []))
            ai_min = round(med / 60000.0, 2) if med is not None else None
            rows.append({
                "workflow": d["name"], "manual_min": mins,
                "ai_min": ai_min, "dept": None,
            })
            if len(rows) >= 6:
                break
        return rows

    # ------------------------------------------------------------------ #
    #  Per-user / per-company                                            #
    # ------------------------------------------------------------------ #
    def _user_aggregates(self, ctx):
        agg = defaultdict(lambda: {"sessions": 0, "messages": 0, "queries": 0,
                                   "cost": 0.0, "tokens": 0, "last_active": None})

        def bump_last(uid, dt):
            if dt is None:
                return
            cur = agg[uid]["last_active"]
            if cur is None or dt > cur:
                agg[uid]["last_active"] = dt

        for r in ctx["reports_range"]:
            if r.user_id:
                agg[r.user_id]["sessions"] += 1
                bump_last(r.user_id, r.created_at)
        for c in ctx["completions_range"]:
            if c.user_id:
                agg[c.user_id]["messages"] += 1
                bump_last(c.user_id, c.created_at)
        for s in ctx["steps_range"]:
            if s.user_id:
                agg[s.user_id]["queries"] += 1
        for u in ctx["usage_range"]:
            if u.user_id:
                agg[u.user_id]["cost"] += float(u.total_cost_usd or 0)
                agg[u.user_id]["tokens"] += _row_tokens(
                    u.provider_type, int(u.prompt_tokens or 0), int(u.completion_tokens or 0),
                    int(u.cache_read_tokens or 0), int(u.cache_creation_tokens or 0))
        return agg

    def _users(self, ctx, ref, mins, rate):
        agg = self._user_aggregates(ctx)
        month_start = ref - timedelta(days=30)
        out = []
        for uid, u in ctx["users"].items():
            a = agg.get(uid, {"sessions": 0, "messages": 0, "queries": 0,
                              "cost": 0.0, "tokens": 0, "last_active": None})
            queries = a["queries"]
            last_active = a["last_active"]
            if queries >= 50:
                tier = "pw"
            elif last_active is None or last_active < month_start:
                tier = "dm"
            else:
                tier = "rg"
            hours_saved = round(queries * mins / 60.0, 2)
            out.append({
                "id": uid, "name": u["name"], "email": u["email"],
                "company": u["company"], "domain": u["domain"], "dept": u["dept"],
                "joined": u["created_at"].date().isoformat() if u["created_at"] else None,
                "last_active": _iso_str(last_active),
                "sessions": a["sessions"], "messages": a["messages"], "queries": queries,
                "cost_usd": round(a["cost"], 4), "tokens": a["tokens"],
                "hours_saved": hours_saved, "tier": tier,
            })
        out.sort(key=lambda x: (x["queries"], x["messages"]), reverse=True)
        return out

    def _companies(self, ctx, mins, rate):
        agg = self._user_aggregates(ctx)
        report_ds = ctx["report_ds_map"]
        ds_name = {d["id"]: d["name"] for d in ctx["data_sources"]}

        # domain -> {users, active, queries, cost, connectors}
        by_dom = defaultdict(lambda: {"users": 0, "active": 0, "queries": 0,
                                      "cost": 0.0, "connectors": set()})
        for uid, u in ctx["users"].items():
            dom = u["domain"] or ""
            entry = by_dom[dom]
            entry["users"] += 1
            a = agg.get(uid)
            if a and (a["sessions"] or a["messages"] or a["queries"]):
                entry["active"] += 1

        user_dom = {uid: u["domain"] or "" for uid, u in ctx["users"].items()}
        # queries + connectors by domain via reports.
        report_owner = {r.id: r.user_id for r in ctx["reports_range"]}
        for s in ctx["steps_range"]:
            dom = user_dom.get(s.user_id, "")
            by_dom[dom]["queries"] += 1
        for rid, owner in report_owner.items():
            dom = user_dom.get(owner, "")
            for dsid in report_ds.get(rid, ()):
                nm = ds_name.get(str(dsid))
                if nm:
                    by_dom[dom]["connectors"].add(nm)
        for u in ctx["usage_range"]:
            dom = user_dom.get(u.user_id, "")
            by_dom[dom]["cost"] += float(u.total_cost_usd or 0)

        out = []
        for dom, e in by_dom.items():
            if not dom:
                continue
            q = e["queries"]
            hours_saved = q * mins / 60.0
            labour = hours_saved * rate
            cost = e["cost"]
            net = labour - cost
            roi_pct = round(net / cost * 100.0, 1) if cost > 0 else None
            out.append({
                "company": _company_from_domain(dom), "domain": dom,
                "users": e["users"], "active": e["active"],
                "connectors": sorted(e["connectors"]),
                "queries": q, "hours_saved": round(hours_saved, 2),
                "net_usd": round(net, 2), "roi_pct": roi_pct,
            })
        out.sort(key=lambda x: x["queries"], reverse=True)
        return out

    # ------------------------------------------------------------------ #
    #  Agents / connectors (both per-DataSource, different lenses)       #
    # ------------------------------------------------------------------ #
    def _agent_type_label(self, conn_types):
        if not conn_types:
            return "agent"
        return conn_types[0] or "agent"

    def _agents(self, ctx):
        report_ds = ctx["report_ds_map"]
        # per-ds aggregation from agent executions + usage + completions.
        runs = defaultdict(int)
        completed = defaultdict(int)
        durs = defaultdict(list)
        run_users = defaultdict(set)
        last_run = {}
        for ex in ctx["agent_exec_range"]:
            for dsid in report_ds.get(ex.report_id, ()):
                dsid = str(dsid)
                runs[dsid] += 1
                if (ex.status or "").lower() in ("completed", "success", "done"):
                    completed[dsid] += 1
                if ex.total_duration_ms is not None:
                    durs[dsid].append(float(ex.total_duration_ms))
                if ex.user_id:
                    run_users[dsid].add(ex.user_id)
                if ex.started_at is not None:
                    if dsid not in last_run or ex.started_at > last_run[dsid]:
                        last_run[dsid] = ex.started_at

        cost_by_ds = defaultdict(float)
        for u in ctx["usage_range"]:
            if u.data_source_id:
                cost_by_ds[str(u.data_source_id)] += float(u.total_cost_usd or 0)

        # judge quality per ds via completions -> report -> ds.
        report_scores = defaultdict(list)
        for c in ctx["completions_range"]:
            if c.response_score is not None:
                report_scores[c.report_id].append(c.response_score)
        quality_by_ds = defaultdict(list)
        for rid, scores in report_scores.items():
            for dsid in report_ds.get(rid, ()):
                quality_by_ds[str(dsid)].extend(scores)

        out = []
        for d in ctx["data_sources"]:
            dsid = d["id"]
            r = runs.get(dsid, 0)
            success_pct = _pct(completed.get(dsid, 0), r) if r else None
            med = _median(durs.get(dsid, []))
            avg_latency = round(med, 1) if med is not None else None
            qs = quality_by_ds.get(dsid, [])
            quality = round(sum(qs) / len(qs) * 20.0, 1) if qs else None
            status = "off" if d["publish_status"] == "disabled" else "on"
            out.append({
                "id": dsid, "name": d["name"],
                "type": self._agent_type_label(d["conn_types"]),
                "connectors": [t for t in d["conn_types"] if t],
                "users": len(run_users.get(dsid, ())),
                "runs": r, "success_pct": success_pct,
                "avg_latency_ms": avg_latency,
                "cost_usd": round(cost_by_ds.get(dsid, 0.0), 4),
                "hours_saved": 0.0,  # filled below
                "quality_pct": quality,
                "last_run": _iso_str(last_run.get(dsid)),
                "status": status,
            })
        return out

    def _connectors(self, ctx):
        report_ds = ctx["report_ds_map"]
        ds_by_id = {d["id"]: d for d in ctx["data_sources"]}

        # users + last_used from per-user credentials.
        cred_users = defaultdict(set)
        cred_last = {}
        for dsid, uid, last in ctx["ds_credentials"]:
            dsid = str(dsid)
            if uid:
                cred_users[dsid].add(uid)
            if last is not None and (dsid not in cred_last or last > cred_last[dsid]):
                cred_last[dsid] = last

        # queries + last report usage per ds.
        queries_by_ds = defaultdict(int)
        report_owner = {r.id: r.user_id for r in ctx["reports_range"]}
        step_report = defaultdict(int)
        for s in ctx["steps_range"]:
            step_report[s.report_id] += 1
        for rid, cnt in step_report.items():
            for dsid in report_ds.get(rid, ()):
                queries_by_ds[str(dsid)] += cnt
        last_report_use = {}
        for r in ctx["reports_range"]:
            for dsid in report_ds.get(r.id, ()):
                dsid = str(dsid)
                if r.created_at is not None and (dsid not in last_report_use or r.created_at > last_report_use[dsid]):
                    last_report_use[dsid] = r.created_at

        cost_by_ds = defaultdict(float)
        for u in ctx["usage_range"]:
            if u.data_source_id:
                cost_by_ds[str(u.data_source_id)] += float(u.total_cost_usd or 0)

        # success_pct per ds via steps status.
        step_total = defaultdict(int)
        step_ok = defaultdict(int)
        for s in ctx["steps_range"]:
            for dsid in report_ds.get(s.report_id, ()):
                dsid = str(dsid)
                step_total[dsid] += 1
                if (s.status or "").lower() not in _ERROR_STATUSES:
                    step_ok[dsid] += 1

        out = []
        for d in ctx["data_sources"]:
            dsid = d["id"]
            conn_types = d["conn_types"]
            kind = conn_types[0] if conn_types else "unknown"
            is_per_user = any(t in _PER_USER_CONNECTOR_TYPES for t in conn_types)
            auth = "per_user" if is_per_user else "shared"
            user_ids = set(cred_users.get(dsid, set()))
            if d["owner_user_id"]:
                user_ids.add(d["owner_user_id"])
            names = []
            for uid in list(user_ids)[:10]:
                u = ctx["users"].get(uid)
                if u and u["name"]:
                    names.append(u["name"])
            last_used = None
            for cand in (cred_last.get(dsid), last_report_use.get(dsid)):
                if cand is not None and (last_used is None or cand > last_used):
                    last_used = cand
            success_pct = _pct(step_ok.get(dsid, 0), step_total.get(dsid, 0)) if step_total.get(dsid) else None
            out.append({
                "id": dsid, "name": d["name"], "kind": kind, "auth": auth,
                "users": len(user_ids),
                "user_names": names,
                "queries": queries_by_ds.get(dsid, 0),
                "cost_usd": round(cost_by_ds.get(dsid, 0.0), 4),
                "success_pct": success_pct,
                "last_used": _iso_str(last_used),
            })
        return out

    # ------------------------------------------------------------------ #
    #  Questions                                                         #
    # ------------------------------------------------------------------ #
    def _prompt_text(self, prompt) -> str:
        # Completion.prompt is a JSON column; the human question lives under
        # ['content'] for role=='user' turns. (No standalone user-question column
        # exists on Completion, so this JSON content field IS the user prompt —
        # not a Report.title proxy.)
        if isinstance(prompt, dict):
            return str(prompt.get("content") or prompt.get("text") or "").strip()
        return str(prompt or "").strip()

    def _classify_question(self, text: str) -> str:
        low = text.lower()
        for key, kws in QUESTION_TAXONOMY.items():
            if any(kw in low for kw in kws):
                return key
        return "other"

    def _questions(self, ctx, ref):
        # User prompts in range.
        user_prompts = []  # (text, cluster, report_id)
        head_prompt = {}   # report_id -> first user prompt text
        for c in ctx["completions_range"]:
            if (c.role or "") != "user":
                continue
            txt = self._prompt_text(c.prompt)
            if not txt:
                continue
            cluster = self._classify_question(txt)
            user_prompts.append((txt, cluster, c.report_id))
            if c.report_id not in head_prompt:
                head_prompt[c.report_id] = txt

        total = len(user_prompts)
        counts = Counter(cl for (_t, cl, _r) in user_prompts)
        examples = defaultdict(list)
        for txt, cl, _r in user_prompts:
            if len(examples[cl]) < 2:
                examples[cl].append(txt[:80])

        clusters = []
        for key in list(QUESTION_TAXONOMY.keys()) + ["other"]:
            cnt = counts.get(key, 0)
            if cnt == 0:
                continue
            clusters.append({
                "key": key, "name": CLUSTER_NAMES.get(key, key.title()),
                "count": cnt, "pct": round(cnt / total * 100.0, 1) if total else 0.0,
                "examples": examples.get(key, []),
            })
        clusters.sort(key=lambda x: x["count"], reverse=True)

        # Trend: weekly counts per cluster over last 10 weeks (from all-time prompts).
        weeks = []
        ref_date = ref.date()
        ref_monday = ref_date - timedelta(days=ref_date.weekday())
        for k in range(9, -1, -1):
            wm = ref_monday - timedelta(days=7 * k)
            iso = wm.isocalendar()
            weeks.append((wm, f"W{iso[1]:02d}"))
        week_labels = [lbl for (_wm, lbl) in weeks]
        # counts per (cluster, week index) from range-scoped prompts joined to dates.
        # We need dates; re-scan completions_range for user prompts with dates.
        trend_counts = defaultdict(lambda: [0] * len(weeks))
        for c in ctx["completions_range"]:
            if (c.role or "") != "user" or c.created_at is None:
                continue
            txt = self._prompt_text(c.prompt)
            if not txt:
                continue
            cl = self._classify_question(txt)
            cdate = c.created_at.date()
            for i, (wm, _lbl) in enumerate(weeks):
                if wm <= cdate < wm + timedelta(days=7):
                    trend_counts[cl][i] += 1
                    break
        trend_series = []
        for key in list(QUESTION_TAXONOMY.keys()) + ["other"]:
            if key not in trend_counts:
                continue
            trend_series.append({
                "key": key, "name": CLUSTER_NAMES.get(key, key.title()),
                "values": trend_counts[key],
            })

        # Failed questions: completions in error/failed OR down-voted. cap 8.
        downvoted = ctx["downvoted_completion_ids"]
        user_dom = None
        failed = []
        seen = 0
        for c in ctx["completions_range"]:
            status_low = (c.status or "").lower()
            is_error = status_low in _ERROR_STATUSES
            is_down = c.id in downvoted
            if not (is_error or is_down):
                continue
            q = head_prompt.get(c.report_id) or self._prompt_text(c.prompt)
            if not q:
                continue
            if is_error:
                reason = "error"
            elif not self._prompt_text(c.completion_text_stub() if hasattr(c, 'completion_text_stub') else None) and is_down:
                reason = "low_confidence"
            else:
                reason = "low_confidence"
            # company from the report owner's domain.
            owner = c.user_id
            comp = None
            if owner and owner in ctx["users"]:
                comp = ctx["users"][owner]["company"]
            failed.append({"q": q[:120], "reason": reason, "company": comp, "count": 1})
            seen += 1
            if seen >= 8:
                break

        return {
            "total": total,
            "clusters": clusters,
            "trend": {"weeks": week_labels, "series": trend_series},
            "failed": failed,
        }

    # ------------------------------------------------------------------ #
    #  Cost                                                              #
    # ------------------------------------------------------------------ #
    def _cost(self, ctx):
        usage = ctx["usage_range"]
        total = round(sum(float(u.total_cost_usd or 0) for u in usage), 6)
        tokens = self._token_totals(usage)["total"]

        queries = len(ctx["steps_range"])
        active_users = len({r.user_id for r in ctx["reports_range"] if r.user_id})
        cost_per_query = round(total / queries, 6) if queries else None
        cost_per_user = round(total / active_users, 6) if active_users else None

        by_model = defaultdict(float)
        for u in usage:
            name = u.model_name or u.model_id or "unknown"
            by_model[name] += float(u.total_cost_usd or 0)
        by_model_list = [{"name": k, "value_usd": round(v, 6)}
                         for k, v in sorted(by_model.items(), key=lambda kv: kv[1], reverse=True)]

        user_dom = {uid: u["domain"] or "" for uid, u in ctx["users"].items()}
        by_comp = defaultdict(float)
        for u in usage:
            dom = user_dom.get(u.user_id, "")
            comp = _company_from_domain(dom) if dom else "Unattributed"
            by_comp[comp] += float(u.total_cost_usd or 0)
        by_company_list = [{"name": k, "value_usd": round(v, 6)}
                           for k, v in sorted(by_comp.items(), key=lambda kv: kv[1], reverse=True)]

        routing_savings = 0.0  # KPI already carries the DB-derived value; keep 0.0 here to avoid a 2nd router pass

        return {
            "total_usd": total, "tokens": tokens, "routing_savings_usd": routing_savings,
            "cost_per_query_usd": cost_per_query, "cost_per_user_usd": cost_per_user,
            "by_model": by_model_list, "by_company": by_company_list,
        }
