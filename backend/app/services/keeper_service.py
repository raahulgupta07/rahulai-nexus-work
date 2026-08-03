"""Reads the per-user sync history that `app.services.sync_runs` writes.

One service behind one screen. The shapes here are the screen's shapes — an
overview, an activity list, a run detail — rather than a generic run API, because
every one of them needs the same two joins (run → connection → data source) and
the same scope rule, and doing that once is what keeps the scope rule honest.

★Scope is enforced by construction, not by a filter
---------------------------------------------------
Every query starts from "which data sources may this member SEE", resolves those
to connection ids, and only then looks for runs. A run is therefore unreachable
unless the caller can already see the agent it belongs to — there is no code path
where forgetting a `where` clause widens the result. The alternative (query runs,
then filter) is one missing line away from showing a member somebody else's
lakehouse names.

★Runs are the member's own
--------------------------
A Fabric sync runs against the member's own Microsoft account and reports what
THAT account could reach. Two members on one agent legitimately see different
workspaces, so a run is only ever shown to the member who caused it. An admin
view over other members' runs is a separate decision with a permission attached
to it, and is deliberately not built here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.connection_indexing import (
    ConnectionIndexing,
    ConnectionIndexingStatus,
    TERMINAL_INDEXING_STATUSES,
)
from app.models.data_source import DataSource
from app.models.domain_connection import domain_connection
from app.models.organization import Organization
from app.models.user import User

logger = logging.getLogger(__name__)

# How many days of per-agent history the overview sparkline covers.
_AGENT_HISTORY_DAYS = 7

# A workspace that has failed on this many consecutive runs has stopped being a
# blip. One failure is noise — a token that expired, a lakehouse mid-deploy —
# and surfacing it as something to act on trains members to ignore the list.
_REPEATED_MISS_RUNS = 2

# Overview lists are previews; the activity tab is where everything lives.
_RECENT_LIMIT = 20

# ★Mirrors the APScheduler registration in `main.py` (`auto_learn_sweep`). There
# is no API to ask the scheduler its own cadence, so this is a copy — and a copy
# that drifts silently is how a screen ends up quoting a number nobody honours.
# `tests/unit/fork/test_the_sync_history_screen_is_linkable.py` pins the two
# together.
_AUTO_LEARN_SWEEP_MINUTES = 15


def _is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_INDEXING_STATUSES


def _result_of(run: ConnectionIndexing) -> str:
    """What the member should be told this run did.

    ``partial`` is carried in stats by `sync_runs` because
    ``ConnectionIndexingStatus`` has no such value — flattening it into
    ``completed`` would hide that some workspaces were missed on a run we are
    otherwise calling a success.
    """
    stats = run.stats_json or {}
    if run.status == ConnectionIndexingStatus.COMPLETED.value:
        return "partial" if stats.get("result") == "partial" else "completed"
    if run.status == ConnectionIndexingStatus.FAILED.value:
        return "failed"
    if run.status == ConnectionIndexingStatus.CANCELLED.value:
        return "cancelled"
    return "running"


def _duration_ms(run: ConnectionIndexing) -> Optional[int]:
    if not run.started_at:
        return None
    end = run.finished_at or datetime.utcnow()
    return max(0, int((end - run.started_at).total_seconds() * 1000))


def _run_summary(run: ConnectionIndexing, ds_name: str, ds_id: Optional[str]) -> Dict[str, Any]:
    stats = run.stats_json or {}
    workspaces = stats.get("workspaces") or []
    return {
        "id": str(run.id),
        "data_source_id": ds_id,
        "data_source_name": ds_name,
        "result": _result_of(run),
        "trigger": run.trigger,
        "phase": run.phase,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": _duration_ms(run),
        "tables": int(stats.get("tables") or 0),
        "workspaces_total": int(run.progress_total or len(workspaces)),
        "workspaces_done": int(run.progress_done or 0),
        "workspaces_failed": int(stats.get("endpoints_failed") or 0),
        "error": run.error,
        # ★Kept on the summary, not just the detail. "Failed" alone sends a
        # member to check a credential; "failed, and it was our side" does not.
        "error_kind": stats.get("error_kind"),
        "abandoned": bool(stats.get("abandoned")),
    }


class KeeperService:
    """Everything the sync-history screen reads."""

    async def _visible_scope(
        self, db: AsyncSession, user: User, organization: Organization
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """(connection_id → data_source_id, data_source_id → name) for what the
        member may see. An empty map means an empty screen, by construction."""
        from app.services.data_source_service import DataSourceService

        rows = (await db.execute(
            select(DataSource).where(DataSource.organization_id == str(organization.id))
        )).scalars().all()
        visible = await DataSourceService().filter_user_visible_data_sources(
            db, rows, user, organization
        )
        names = {str(ds.id): ds.name for ds in visible}
        if not names:
            return {}, {}

        pairs = (await db.execute(
            select(
                domain_connection.c.connection_id,
                domain_connection.c.data_source_id,
            ).where(domain_connection.c.data_source_id.in_(list(names.keys())))
        )).all()
        # A connection can belong to several agents. The run row only knows the
        # connection, so it is attributed to one of them; picking the first
        # deterministically is better than showing the same run once per agent.
        conn_to_ds: Dict[str, str] = {}
        for connection_id, data_source_id in pairs:
            conn_to_ds.setdefault(str(connection_id), str(data_source_id))
        return conn_to_ds, names

    async def _runs(
        self,
        db: AsyncSession,
        user: User,
        conn_to_ds: Dict[str, str],
        *,
        connection_ids: Optional[List[str]] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[ConnectionIndexing]:
        ids = connection_ids if connection_ids is not None else list(conn_to_ds.keys())
        if not ids:
            return []
        stmt = (
            select(ConnectionIndexing)
            .where(
                ConnectionIndexing.connection_id.in_(ids),
                ConnectionIndexing.user_id == str(user.id),
            )
            .order_by(ConnectionIndexing.created_at.desc())
        )
        if since is not None:
            stmt = stmt.where(ConnectionIndexing.created_at >= since)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list((await db.execute(stmt)).scalars().all())

    # ───────────────────────────── overview ──────────────────────────────

    async def overview(
        self, db: AsyncSession, user: User, organization: Organization
    ) -> Dict[str, Any]:
        conn_to_ds, names = await self._visible_scope(db, user, organization)
        if not conn_to_ds:
            return {
                "working_now": [],
                "today": {"runs": 0, "completed": 0, "failed": 0, "tables": 0},
                "agents": [],
                "needs_a_person": [],
                "recent": [],
            }

        since = datetime.utcnow() - timedelta(days=_AGENT_HISTORY_DAYS)
        runs = await self._runs(db, user, conn_to_ds, since=since)

        def summarize(run: ConnectionIndexing) -> Dict[str, Any]:
            ds_id = conn_to_ds.get(str(run.connection_id))
            return _run_summary(run, names.get(ds_id, "Unknown agent"), ds_id)

        summaries = [summarize(r) for r in runs]

        midnight = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today = [r for r, s in zip(runs, summaries) if (r.created_at or datetime.utcnow()) >= midnight]
        today_s = [s for r, s in zip(runs, summaries) if (r.created_at or datetime.utcnow()) >= midnight]

        # Per agent, newest first — the order `_runs` already returns.
        by_ds: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for s in summaries:
            if s["data_source_id"]:
                by_ds[s["data_source_id"]].append(s)

        agents = []
        for ds_id, name in names.items():
            history = by_ds.get(ds_id, [])
            last_success = next(
                (s for s in history if s["result"] in ("completed", "partial")), None
            )
            agents.append({
                "data_source_id": ds_id,
                "name": name,
                "last_run": history[0] if history else None,
                "last_success_at": last_success["finished_at"] if last_success else None,
                "runs": history[:_AGENT_HISTORY_DAYS],
                "never_synced": not history,
            })
        # Agents with a problem first, then most recently active, then the ones
        # that have never run — the order someone scanning the screen wants.
        agents.sort(key=lambda a: (
            0 if (a["last_run"] and a["last_run"]["result"] == "failed") else 1,
            2 if a["never_synced"] else 0,
            -(len(a["runs"])),
            a["name"].lower(),
        ))

        return {
            "working_now": [s for s in summaries if s["result"] == "running"],
            "today": {
                "runs": len(today),
                "completed": sum(1 for s in today_s if s["result"] in ("completed", "partial")),
                "failed": sum(1 for s in today_s if s["result"] == "failed"),
                "tables": sum(s["tables"] for s in today_s),
            },
            "agents": agents,
            "needs_a_person": self._needs_a_person(by_ds, names),
            "recent": summaries[:_RECENT_LIMIT],
        }

    # ────────────────────────── needs a person ───────────────────────────

    def _needs_a_person(
        self, by_ds: Dict[str, List[Dict[str, Any]]], names: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """Only what a member can actually do something about.

        ★An infrastructure failure is deliberately EXCLUDED. It is our outage,
        the retry is already scheduled, and putting it on a list headed "needs a
        person" tells the member to go and check a credential that is fine —
        which is precisely the wrong advice the 2026-08-03 incident produced.
        """
        out: List[Dict[str, Any]] = []
        for ds_id, history in by_ds.items():
            if not history:
                continue
            latest = history[0]
            name = names.get(ds_id, "Unknown agent")

            if latest["result"] == "failed" and latest["error_kind"] != "infrastructure":
                out.append({
                    "kind": "last_sync_failed",
                    "data_source_id": ds_id,
                    "data_source_name": name,
                    "run_id": latest["id"],
                    "detail": latest["error"] or "The last sync did not finish.",
                    "since": latest["finished_at"] or latest["started_at"],
                })
                continue

            # A workspace that missed the last few runs in a row. One miss is a
            # blip; a run of them means access changed or the lakehouse is gone.
            recent = [h for h in history if h["result"] != "running"][:_REPEATED_MISS_RUNS]
            if len(recent) < _REPEATED_MISS_RUNS:
                continue
            out.extend(self._repeated_misses(recent, ds_id, name))
        return out

    def _repeated_misses(
        self, recent: List[Dict[str, Any]], ds_id: str, name: str
    ) -> List[Dict[str, Any]]:
        """Workspaces that failed on every one of the last `_REPEATED_MISS_RUNS`.

        Works off the counts on the summary — the per-workspace names live on the
        run detail, which the screen fetches when a row is opened. Reporting the
        count here and the names on open keeps the overview one query.
        """
        if not all(r["workspaces_failed"] > 0 for r in recent):
            return []
        return [{
            "kind": "workspace_repeatedly_missed",
            "data_source_id": ds_id,
            "data_source_name": name,
            "run_id": recent[0]["id"],
            "detail": (
                f"{recent[0]['workspaces_failed']} workspace(s) have failed to answer "
                f"on the last {len(recent)} syncs. Open the run to see which."
            ),
            "since": recent[-1]["finished_at"] or recent[-1]["started_at"],
        }]

    # ─────────────────────────── activity list ───────────────────────────

    async def activity(
        self,
        db: AsyncSession,
        user: User,
        organization: Organization,
        *,
        data_source_id: Optional[str] = None,
        problems_only: bool = False,
        days: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        conn_to_ds, names = await self._visible_scope(db, user, organization)
        if not conn_to_ds:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}

        connection_ids = list(conn_to_ds.keys())
        if data_source_id:
            connection_ids = [c for c, d in conn_to_ds.items() if d == str(data_source_id)]
            if not connection_ids:
                # Either no such agent or not one this member can see. Same
                # answer either way — an empty list tells them nothing they
                # could not already work out.
                return {"items": [], "total": 0, "limit": limit, "offset": offset}

        since = datetime.utcnow() - timedelta(days=days) if days else None

        # ★`problems_only` is applied in Python, not SQL. "A problem" is
        # `failed`, or a success that missed a workspace — and `partial` lives in
        # a JSON blob, not a column. Filtering in SQL would mean either a JSON
        # predicate that differs between SQLite and Postgres, or quietly dropping
        # partials from a filter whose whole purpose is to surface them.
        rows = await self._runs(db, user, conn_to_ds, connection_ids=connection_ids, since=since)
        items = [
            _run_summary(r, names.get(conn_to_ds.get(str(r.connection_id)), "Unknown agent"),
                         conn_to_ds.get(str(r.connection_id)))
            for r in rows
        ]
        if problems_only:
            items = [
                i for i in items
                if i["result"] == "failed" or i["workspaces_failed"] > 0
            ]
        total = len(items)
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # ───────────────────────────── schedule ──────────────────────────────

    async def schedule(
        self, db: AsyncSession, user: User, organization: Organization
    ) -> Dict[str, Any]:
        """What runs by itself, and what does not.

        ★The one fact this tab exists to state: a per-user connector has NO
        timer. It syncs when the member signs in, because it runs against THEIR
        Microsoft token and there is nobody to borrow that from at 3am. Members
        reliably assume the opposite — they see "Auto learn" in settings, apply
        it to the whole screen, and then report the sync as broken because it
        did not run overnight. Saying "signs you in, then syncs" out loud is
        cheaper than answering that a fourth time.

        Auto learn is a genuinely scheduled thing and is reported next to it,
        with the budget, because "on" without "12 runs a day, 3 spent" is not
        enough to predict anything.
        """
        from app.schemas.data_source_registry import PER_USER_TOKEN_TYPES
        from app.services import auto_learn

        conn_to_ds, names = await self._visible_scope(db, user, organization)

        policy = await auto_learn.org_policy(db, organization)
        spent = await auto_learn.runs_today(db, organization, datetime.utcnow())

        types_by_conn: Dict[str, str] = {}
        if conn_to_ds:
            from app.models.connection import Connection
            rows = (await db.execute(
                select(Connection.id, Connection.type).where(
                    Connection.id.in_(list(conn_to_ds.keys()))
                )
            )).all()
            types_by_conn = {str(cid): ctype for cid, ctype in rows}

        per_user_ds = {
            ds_id for conn_id, ds_id in conn_to_ds.items()
            if types_by_conn.get(conn_id) in PER_USER_TOKEN_TYPES
        }

        # ★An agent is only swept by Auto learn when the ORG switch is on AND
        # that agent's own mode is `auto` — `auto_learn.py:213` filters on
        # exactly that, and `:240` bails on the switch. This tab used to label
        # every non-per-user agent "Auto learn" regardless of both, so a member
        # with the switch off, or an agent left on `manual`/`notify`, was told
        # something would run by itself that never would. A schedule screen that
        # is wrong about what is scheduled is worse than no schedule screen.
        from app.services import training_drift

        modes_by_ds: Dict[str, str] = {}
        if names:
            from app.models.data_source import DataSource
            ds_rows = (await db.execute(
                select(DataSource).where(DataSource.id.in_(list(names.keys())))
            )).scalars().all()
            modes_by_ds = {str(d.id): training_drift.mode_of(d) for d in ds_rows}

        org_auto_on = bool(policy.get("enabled"))

        def _runs_when(ds_id: str) -> str:
            # "signin" is not a schedule and is deliberately not dressed up as
            # one — there is no next-run time to show, and inventing one would
            # be a lie the member could act on.
            if ds_id in per_user_ds:
                return "signin"
            if org_auto_on and modes_by_ds.get(ds_id) == training_drift.MODE_AUTO:
                return "auto_learn"
            return "manual"

        agents = [
            {
                "data_source_id": ds_id,
                "name": name,
                "runs_when": _runs_when(ds_id),
            }
            for ds_id, name in sorted(names.items(), key=lambda kv: kv[1].lower())
        ]

        return {
            "auto_learn": {
                "enabled": bool(policy.get("enabled")),
                "quiet_minutes": int(policy.get("quiet_minutes") or 0),
                "max_runs_per_day": int(policy.get("max_runs_per_day") or 0),
                "runs_today": spent,
                # The sweep tick, not a per-agent cadence — an agent inside its
                # quiet period is skipped, so this is the FLOOR on how soon a
                # change can be noticed, not a promise of how often it is.
                "sweep_every_minutes": _AUTO_LEARN_SWEEP_MINUTES,
            },
            "agents": agents,
            "per_user_count": len(per_user_ds),
        }

    # ──────────────────────────── run detail ─────────────────────────────

    async def run_detail(
        self, db: AsyncSession, user: User, organization: Organization, run_id: str
    ) -> Optional[Dict[str, Any]]:
        """One run, with its per-workspace breakdown and event log.

        Returns None when the run does not exist, belongs to another member, or
        sits on an agent this member cannot see — the caller turns all three into
        the same 404, because distinguishing them would confirm the existence of
        a run they may not know about.
        """
        conn_to_ds, names = await self._visible_scope(db, user, organization)
        if not conn_to_ds:
            return None
        run = (await db.execute(
            select(ConnectionIndexing).where(
                ConnectionIndexing.id == str(run_id),
                ConnectionIndexing.user_id == str(user.id),
                ConnectionIndexing.connection_id.in_(list(conn_to_ds.keys())),
            )
        )).scalars().first()
        if run is None:
            return None

        ds_id = conn_to_ds.get(str(run.connection_id))
        stats = run.stats_json or {}
        payload = _run_summary(run, names.get(ds_id, "Unknown agent"), ds_id)
        payload["workspaces"] = stats.get("workspaces") or []
        payload["events"] = run.events_json or []
        return payload
