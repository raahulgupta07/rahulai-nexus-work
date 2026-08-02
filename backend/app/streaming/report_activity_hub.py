"""Per-worker live feed of report activity for list badges.

One background watcher task per uvicorn worker, running only while at least
one client is subscribed. Each tick it opens its own short-lived DB session,
derives the org-level activity facts for a bounded candidate set (reports
with live completions now or recent last_activity_at churn), diffs against
the previous tick, and fans the changes out to that worker's subscribers.

The DB is the bus: every worker polls the same database, so a run executing
on worker 2 is seen by a subscriber connected to worker 0 — the property the
old report-scoped in-process WebSocket broadcast never had. Cost is a few
cheap indexed queries per tick per active org, independent of how many
clients are connected; zero when nobody is subscribed.

Events carry viewer-independent facts only. The client composes the per-user
view: `unread` (an event for a report you're not currently reading), and
`awaiting_user_id` (a pending tool confirmation counts as "waiting for you"
only for its target user — everyone else renders the running state the
in-progress completion already implies). Snapshot truth on connect/reconnect
comes from GET /reports/activity — deltas here, snapshot there.

Viewer-independent is not the same as org-wide: the activity is derived once
per org, but every frame is intersected with the *subscriber's* own visible
report set before it is queued. `view_reports` on the endpoint only says the
caller may see reports — it does not say which ones, and report ids plus their
activity timestamps are exactly what a report-sharing boundary exists to hide.
The visible set is cached per subscriber and refreshed on a slow cadence, never
per tick (a query per subscriber every 2s would be a real regression).
"""
import asyncio
import contextlib
import inspect
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

TICK_SECONDS = 2.0
# Look-back margin when picking up reports whose last_activity_at moved; wide
# enough to absorb tick jitter and cross-worker clock skew on the DB writes.
CHURN_MARGIN = timedelta(seconds=10)
# Bound per-tick candidate sets; a busier org degrades to snapshot-on-nav for
# the overflow instead of unbounded queries. Sized for 500+ concurrently live
# reports per org (validated by the scale test) — the queries are IN-bound
# batches, so the cost of a large tick is bounded and measured, not magic.
MAX_CANDIDATES = 1000
# Must absorb a full burst of MAX_CANDIDATES events (e.g. a mass schedule
# firing) without dropping; drops self-heal via snapshot but cost a resync.
QUEUE_MAXSIZE = 2048
# How long a subscriber's visible-report set is trusted before it is recomputed.
# Deliberately slow: this is a badge feed, and a report shared with you while you
# sit on the list page appearing one refresh late is not worth a per-tick query
# per subscriber (that would be 15x this cost, on a loop already known to be
# expensive). Losing access is bounded the same way — worst case you keep seeing
# a badge for the rest of the window; the snapshot endpoint re-authorizes on
# every navigation.
VISIBILITY_TTL_SECONDS = 30.0
# Smear a reconnect storm (worker restart -> every client re-subscribes) across
# ticks instead of firing one visibility query per subscriber in a single tick.
MAX_VISIBILITY_REFRESH_PER_TICK = 25
# Bound the cached set per subscriber. Ordered by recency, so what falls off the
# end is the long tail that cannot emit anyway — an event needs live completions
# or last_activity_at churn, and MAX_CANDIDATES already caps a tick at 1000.
MAX_VISIBLE_IDS = 2000


class _Subscriber:
    __slots__ = ('queue', 'user_id', 'visible_ids', 'visible_at')

    def __init__(self, user_id: str):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.user_id = user_id
        # None = not computed yet -> fail closed, emit nothing to this
        # subscriber until we know what they are allowed to see.
        self.visible_ids: set[str] | None = None
        self.visible_at: datetime | None = None


class ReportActivityHub:
    def __init__(self):
        # org_id -> list of subscribers on THIS worker
        self._subs: dict[str, list[_Subscriber]] = {}
        # org_id -> report_id -> signature of the last event we emitted
        self._last_sig: dict[str, dict[str, tuple]] = {}
        # org_id -> report ids that were live (running/queued) last tick, so a
        # run finishing (leaving the live set) still emits its terminal event.
        self._prev_live: dict[str, set[str]] = {}
        self._watcher: asyncio.Task | None = None
        self._last_tick_at: datetime = datetime.utcnow()
        # Priming tasks for freshly connected subscribers, held so they are not
        # garbage-collected mid-await.
        self._priming: set[asyncio.Task] = set()
        self._predicate_warned = False

    # ── subscription ────────────────────────────────────────────────────────
    def subscribe(self, org_id: str, user_id: str) -> _Subscriber:
        org_id = str(org_id)
        sub = _Subscriber(user_id)
        self._subs.setdefault(org_id, []).append(sub)
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.create_task(self._run())
        # Resolve this subscriber's visible set now rather than waiting for the
        # next tick: until it lands they receive nothing, and a two-second blind
        # window straddles the snapshot the client takes on connect.
        task = asyncio.create_task(self._prime(org_id, sub))
        self._priming.add(task)
        task.add_done_callback(self._priming.discard)
        return sub

    def unsubscribe(self, org_id: str, sub: _Subscriber) -> None:
        org_id = str(org_id)
        subs = self._subs.get(org_id, [])
        with contextlib.suppress(ValueError):
            subs.remove(sub)
        if not subs:
            self._subs.pop(org_id, None)
            self._last_sig.pop(org_id, None)
            self._prev_live.pop(org_id, None)

    @property
    def has_subscribers(self) -> bool:
        return any(self._subs.values())

    # ── per-subscriber visibility ───────────────────────────────────────────
    async def _visible_ids(self, db, org_id: str, user_id: str) -> set[str] | None:
        """Report ids in `org_id` that `user_id` is allowed to see, or None if
        that cannot be established (unknown user, missing predicate) — callers
        treat None as "emit nothing"."""
        from app.models.organization import Organization
        from app.models.report import Report
        from app.models.user import User
        from app.services.report_service import ReportService

        svc = ReportService()
        predicate_fn = getattr(svc, 'visible_reports_predicate', None)
        if predicate_fn is None:
            if not self._predicate_warned:
                self._predicate_warned = True
                logger.error(
                    "ReportService.visible_reports_predicate missing — report "
                    "activity stream is emitting nothing (fail closed)"
                )
            return None
        user = await db.get(User, str(user_id))
        org = await db.get(Organization, str(org_id))
        if user is None or org is None:
            return None
        predicate = predicate_fn(db, user, org)
        if inspect.isawaitable(predicate):
            predicate = await predicate
        rows = await db.execute(
            select(Report.id)
            .filter(
                Report.organization_id == org_id,
                Report.deleted_at.is_(None),
                predicate,
            )
            .order_by(func.coalesce(Report.last_activity_at, Report.created_at).desc())
            .limit(MAX_VISIBLE_IDS)
        )
        return {str(r) for (r,) in rows.all()}

    async def _prime(self, org_id: str, sub: _Subscriber) -> None:
        """First visibility resolve for a just-connected subscriber, on its own
        short-lived session (the watcher's tick session is not open yet, and the
        request's pooled connection was released before subscribing)."""
        from app.settings.database import create_async_session_factory

        try:
            session_factory = create_async_session_factory()
            async with session_factory() as db:
                ids = await self._visible_ids(db, org_id, sub.user_id)
        except Exception:
            logger.exception("report-activity visibility prime failed for org %s", org_id)
            return
        # A tick may have refreshed us first; never overwrite a fresher answer.
        if ids is not None and sub.visible_at is None:
            sub.visible_ids = ids
            sub.visible_at = datetime.utcnow()

    async def _refresh_visibility(self, db, org_id: str, now: datetime) -> None:
        """Recompute the visible set for subscribers whose cache has aged out.

        One query per distinct *user* (tabs share), oldest first, capped per tick
        — never one per subscriber per tick.
        """
        due = [
            s for s in self._subs.get(org_id, [])
            if s.visible_at is None
            or (now - s.visible_at).total_seconds() >= VISIBILITY_TTL_SECONDS
        ]
        if not due:
            return
        due.sort(key=lambda s: s.visible_at or datetime.min)
        by_user: dict[str, list[_Subscriber]] = {}
        for s in due:
            by_user.setdefault(s.user_id, []).append(s)
            if len(by_user) >= MAX_VISIBILITY_REFRESH_PER_TICK:
                break
        for user_id, subs in by_user.items():
            try:
                ids = await self._visible_ids(db, org_id, user_id)
            except Exception:
                logger.exception(
                    "report-activity visibility refresh failed for user %s", user_id
                )
                continue
            if ids is None:
                continue
            for s in subs:
                # Shared set object, read-only from here on.
                s.visible_ids = ids
                s.visible_at = now

    # ── watcher ─────────────────────────────────────────────────────────────
    async def _run(self):
        logger.info("report-activity watcher started")
        try:
            while self.has_subscribers:
                started = datetime.utcnow()
                for org_id in list(self._subs.keys()):
                    try:
                        await self._tick(org_id)
                    except Exception:
                        logger.exception("report-activity tick failed for org %s", org_id)
                self._last_tick_at = started
                await asyncio.sleep(TICK_SECONDS)
        finally:
            logger.info("report-activity watcher stopped")
            self._watcher = None

    async def _tick(self, org_id: str) -> None:
        """One diff pass for one org. Opens its own session (never the pool
        slot of a request — the watcher outlives every request)."""
        from app.settings.database import create_async_session_factory
        from app.models.report import Report
        from app.models.completion import Completion
        from app.services.report_service import ReportService

        session_factory = create_async_session_factory()
        async with session_factory() as db:
            # Aged-out visible sets first, so a subscriber that connected since
            # the last tick is already filterable when this tick's events fan out.
            await self._refresh_visibility(db, org_id, datetime.utcnow())
            # Candidates: live completions now, live last tick, or recent
            # last_activity_at churn (covers error finalize, new messages,
            # viewed-elsewhere is client-side).
            since = self._last_tick_at - CHURN_MARGIN
            live_rows = await db.execute(
                select(Completion.report_id)
                .join(Report, Report.id == Completion.report_id)
                .filter(
                    Report.organization_id == org_id,
                    Completion.status.in_(['in_progress', 'queued']),
                    Completion.deleted_at.is_(None),
                )
            )
            live_now = {str(r) for (r,) in live_rows.all()}
            churn_rows = await db.execute(
                select(Report.id).filter(
                    Report.organization_id == org_id,
                    Report.last_activity_at > since,
                    Report.deleted_at.is_(None),
                )
            )
            churned = {str(r) for (r,) in churn_rows.all()}
            candidates = list(live_now | churned | self._prev_live.get(org_id, set()))[:MAX_CANDIDATES]
            self._prev_live[org_id] = live_now
            if not candidates:
                return

            svc = ReportService()
            sets = await svc.derive_activity_sets(db, candidates)
            meta_rows = await db.execute(
                select(Report.id, Report.last_activity_at, Report.created_at).filter(
                    Report.id.in_(candidates),
                    Report.organization_id == org_id,
                    Report.deleted_at.is_(None),
                )
            )
            events = []
            seen: set[str] = set()
            sig_cache = self._last_sig.setdefault(org_id, {})
            for rid, last_activity_at, created_at in meta_rows.all():
                rid = str(rid)
                seen.add(rid)
                conf_users = sets['confirmation_user_ids'].get(rid, set())
                if rid in sets['clarify_ids']:
                    state = 'awaiting_user'
                elif conf_users:
                    state = 'awaiting_user'
                elif rid in sets['running_ids']:
                    state = 'running'
                elif rid in sets['queued_ids']:
                    state = 'queued'
                else:
                    state = 'idle'
                la = (last_activity_at or created_at)
                sig = (state, rid in sets['error_ids'], la.isoformat() if la else None,
                       tuple(sorted(conf_users)))
                if sig_cache.get(rid) == sig:
                    continue
                sig_cache[rid] = sig
                events.append({
                    'report_id': rid,
                    'state': state,
                    'error': rid in sets['error_ids'],
                    'last_activity_at': sig[2],
                    # Set only when the pause belongs to specific users
                    # (tool confirmation); clarify is answerable by anyone.
                    'awaiting_user_ids': sorted(conf_users) if (conf_users and rid not in sets['clarify_ids']) else None,
                })

            # A signature is only useful while its report can still emit, i.e.
            # while it stays in the derived set. Without this the cache keeps one
            # entry per report ever seen and is only dropped when the org's last
            # subscriber leaves — unbounded on a tenant that always has someone
            # connected. Reports that fall out and later come back re-emit once,
            # which is idempotent for the client.
            if len(sig_cache) > len(seen):
                self._last_sig[org_id] = {k: v for k, v in sig_cache.items() if k in seen}

        if not events:
            return
        for sub in list(self._subs.get(org_id, [])):
            visible = sub.visible_ids
            if not visible:
                # None  = visibility not resolved yet (first tick after connect,
                #         or the resolve failed) -> fail closed.
                # empty = this subscriber may see no report in the org.
                continue
            for ev in events:
                if ev['report_id'] not in visible:
                    continue
                try:
                    sub.queue.put_nowait(ev)
                except asyncio.QueueFull:
                    # Slow consumer: drop — the next snapshot (reconnect or
                    # navigation) restores truth.
                    pass


report_activity_hub = ReportActivityHub()


def format_activity_event(ev: dict) -> str:
    return f"event: report.activity\ndata: {json.dumps(ev)}\n\n"
