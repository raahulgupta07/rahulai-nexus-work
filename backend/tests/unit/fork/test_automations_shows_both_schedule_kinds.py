"""Automations said "Nothing scheduled yet" while a schedule was firing.

There are TWO scheduling mechanisms:

  * "Schedule and rerun report"  → writes `Report.cron_schedule`
  * "New task"                   → writes a `scheduled_prompts` row

Automations → Scheduled read only the second one. So a user who scheduled a
report refresh — and it WAS accepted, persisted, and registered with APScheduler
(job `report_<id>`, with a real next_run_time) — was shown an empty state telling
them nothing was scheduled. The natural response is to schedule it again.

Two further faults found in the same place:

  1. THE LIST WAS SILENTLY `filter: 'my'`. Nothing in the UI said so, so an empty
     page could mean "nothing exists" or "nothing of yours exists" and the user
     could not tell which.

  2. THE ORG TIMEZONE APPLIED TO ONE MECHANISM ONLY.
     `scheduled_prompt_service._register_job` passes the org timezone to the
     CronTrigger; `set_report_schedule` did not. The same "8:00 AM" chosen in the
     same UI produced two different moments — prompts in the org's zone, report
     refreshes in the server's (UTC in every deployment of ours). And because a
     job carries the timezone it was registered WITH, changing the org setting
     re-registered nothing: the setting silently disagreed with every existing
     schedule.

★ SECURITY: the ownership toggle nearly opened a hole. `GET /scheduled-prompts`
gated cross-user listing on the literal string `filter == 'shared'`, but the
service narrows only on `filter == 'my'` — so `filter=all` (or any typo) skipped
the admin check and returned other people's prompt text. The gate now reads
`filter != 'my'`.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
RS = REPO / "backend" / "app" / "services" / "report_service.py"
OSS = REPO / "backend" / "app" / "services" / "organization_settings_service.py"
SPS = REPO / "backend" / "app" / "services" / "scheduled_prompt_service.py"
RROUTE = REPO / "backend" / "app" / "routes" / "report.py"
SPROUTE = REPO / "backend" / "app" / "routes" / "scheduled_prompt.py"
TAB = REPO / "frontend" / "components" / "automations" / "ScheduledTab.vue"
EN = REPO / "locales" / "en.json"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _pyfn(src: str, header: str) -> str:
    i = src.index(header)
    rest = src[i:]
    nxt = re.search(r"\n    (?:async def |def )", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── the missing half is now served ───────────────────────────────────────────

def test_a_refresh_list_exists():
    assert "async def get_report_refreshes(" in _src(RS)


def test_refreshes_are_visibility_scoped():
    body = _pyfn(_src(RS), "async def get_report_refreshes(")
    assert "visible_reports_predicate" in body, (
        "'everyone' must not mean 'every report in the org' — it widens the "
        "ownership filter, not the permission"
    )
    assert "Report.cron_schedule.isnot(None)" in body


def test_next_run_is_read_from_the_live_job_not_reparsed():
    body = _pyfn(_src(RS), "async def get_report_refreshes(")
    assert 'scheduler.get_job(job_id=f"report_{r.id}")' in body, (
        "re-parsing the cron string here would drop the timezone the job was "
        "actually registered with and display a next-run time that is wrong"
    )


def test_a_schedule_with_no_live_job_is_flagged():
    body = _pyfn(_src(RS), "async def get_report_refreshes(")
    assert '"orphaned": next_run is None' in body, (
        "a row that will never fire must not render identically to one that will"
    )


def test_the_route_is_registered_and_gated():
    src = _src(RROUTE)
    i = src.index('@router.get("/report-refreshes")')
    assert "@requires_permission('view_reports')" in src[i:i + 200]


def test_the_path_cannot_collide_with_a_report_id():
    """`/reports/refreshes` would be a candidate match for `/reports/{id}`."""
    assert '@router.get("/reports/refreshes"' not in _src(RROUTE)


# ── timezone ─────────────────────────────────────────────────────────────────

def test_report_refresh_honours_the_org_timezone():
    body = _pyfn(_src(RS), "async def set_report_schedule(")
    assert "_org_timezone_for_report" in body and "'timezone': tz" in body, (
        "prompts fire in the org timezone and refreshes did not — the same "
        "wall-clock time in the same UI meant two different moments"
    )


def test_the_prompt_side_still_does_it_too():
    """The behaviour being mirrored. If this moves, revisit the mirror."""
    assert "cron_params = {**cron_params, 'timezone': tz}" in _src(SPS)


def test_changing_the_org_timezone_reregisters_everything():
    src = _src(OSS)
    assert "_reschedule_org_crons" in src, "the setting still changes nothing already scheduled"
    body = _pyfn(src, "async def _reschedule_org_crons(")
    assert "_register_job(sp)" in body, "prompts are not re-registered"
    assert "_reregister_report_cron(r)" in body, "report refreshes are not re-registered"


def test_the_reregistration_runs_as_the_report_owner():
    body = _pyfn(_src(RS), "def _reregister_report_cron(")
    assert "str(report.user_id)" in body, (
        "re-registering must keep the report's own owner — running someone's "
        "schedule as whoever edited the org settings is an authorization bug"
    )


def test_a_scheduler_failure_cannot_fail_the_settings_save():
    src = _src(OSS)
    i = src.index("await self._reschedule_org_crons(")
    assert "try:" in src[max(0, i - 300):i], "unguarded — a scheduler error would 500 the settings write"


# ── the near-miss escalation ─────────────────────────────────────────────────

def test_cross_user_prompt_listing_is_gated_on_the_behaviour_not_the_spelling():
    src = _src(SPROUTE)
    assert "if filter != 'my':" in src, (
        "the service narrows only on 'my', so gating the single string 'shared' "
        "left filter=all returning other users' prompt text to non-admins"
    )
    assert "if filter == 'shared':" not in src


def test_the_ui_does_not_offer_everyone_to_non_admins():
    src = _src(TAB)
    assert "canSeeEveryone" in src and "useCan('full_admin_access')" in src, (
        "offering a control that can only 403 is worse than not offering it"
    )
    assert 'v-if="!canSeeEveryone"' in src, "no fallback label explaining the scope"


# ── the false empty state ────────────────────────────────────────────────────

def test_the_empty_state_counts_both_kinds():
    src = _src(TAB)
    assert "refreshes.length === 0 && !searchTerm" in src, (
        '"Nothing scheduled yet" must not render while a refresh is listed'
    )
    assert 'v-else-if="tasks.length === 0 && !visibleRefreshes.length"' in src, (
        "the inline empty text has the same problem as the full-page one. "
        "★0.0.541.1 moved this from `refreshes` to `visibleRefreshes`: once the "
        "Active/Paused tabs filter refreshes too, the empty state has to answer "
        "'is anything showing', not 'does anything exist' — otherwise filtering "
        "to Paused with only active refreshes renders neither rows nor a message."
    )


def test_the_tab_fetches_and_renders_refreshes():
    src = _src(TAB)
    assert "useMyFetch('/report-refreshes'" in src
    # ★Was `v-for="rf in refreshes"` until 0.0.541.1. The loop now walks the
    # status-filtered computed; asserting the raw ref would fail on correct code.
    assert 'v-for="rf in visibleRefreshes"' in src
    assert "scheduled.kindRefresh" in src, "the row does not say what kind of schedule it is"


def test_refresh_rows_do_not_reach_the_prompt_endpoints():
    """A refresh has no scheduled-prompt id; calling those endpoints with a
    report id 404s. The row links out to the report and uses its OWN handlers.

    ★★★THE SLICE BOUNDARY IS THE WHOLE TEST. This read to `</NuxtLink>` while the
    entire row WAS one link. 0.0.541.1 gave the row real controls, and they sit
    AFTER that tag closes — a `<button>` inside an `<a>` is invalid and behaves
    unpredictably, so the buttons are correctly outside it. Keeping the old
    boundary would have left this guard passing over precisely the code that now
    carries the risk: a window that ends before the dangerous part measures the
    window, not the file.
    """
    src = _src(TAB)
    block = src[src.index('v-for="rf in visibleRefreshes"'):]
    block = block[: block.index("<!-- Task cards")]
    for wrong in ("toggleActive(rf", "deleteTask(rf", "openTask(rf"):
        assert wrong not in block, f"{wrong} would call a scheduled-prompt endpoint with a report id"
    # The positive half: the row must actually wire its own handlers. Absence
    # assertions alone are satisfied by a row with no controls at all, which is
    # the state this release exists to fix.
    for right in ("toggleRefresh(rf", "openRefresh(rf", "removeRefresh(rf"):
        assert right in block, f"the refresh row no longer offers {right.rstrip('(rf')}"


def test_the_scope_change_refetches_both_lists():
    src = _src(TAB)
    i = src.index("watch(scopeFilter")
    block = src[i:i + 300]
    assert "fetchTasks" in block and "fetchRefreshes" in block


def test_the_copy_exists():
    import json
    s = json.loads(_src(EN))["scheduled"]
    for k in ("refreshSection", "promptSection", "kindRefresh", "notRegistered",
              "scopeMine", "scopeEveryone", "scopeMineOnly", "nextRun"):
        assert s.get(k), f"missing scheduled.{k}"
    assert "{time}" in s["nextRun"]
