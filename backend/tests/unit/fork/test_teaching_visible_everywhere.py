"""Teaching is the longest thing this product does, and only one of eight
places that start it ever showed anything.

Phase C fixed the Fabric sign-in. Seven other paths start exactly the same learn
— the same ``llm_sync(force_llm=True)``, stamping the same tracker — and none of
them had a surface:

    Power BI, personal sign-in         (tenant merge)
    Power BI, tenant re-selection      (in-request)
    Power BI, multi-tenant             (same merge path)
    file upload / Data Agent           (background after the upload returns)
    the first model key being saved    (auto-heal of the seeded agents)
    first-run seeding                  (three agents, at signup)

Two distinct faults, one per layer:

  1. The Power BI merge fired the learn into the background and finished its
     progress tracker first — the same ordering fault Fabric had.
  2. The learn tracker was only ever polled by the Save button that started a
     learn itself. A learn begun anywhere else ran unwatched, and the two that
     run with NO user at all (seeding, auto-heal) were stamped under a key the
     status endpoint could not even look up.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PBI = REPO / "backend" / "app" / "routes" / "fabric_user_signin.py"
PBI_USER = REPO / "backend" / "app" / "routes" / "powerbi_user_signin.py"
LEARN = REPO / "backend" / "app" / "services" / "learn_progress.py"
ROUTE = REPO / "backend" / "app" / "routes" / "data_source.py"
BAR = REPO / "frontend" / "components" / "datasources" / "LearnProgressBar.vue"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
TABLES = REPO / "frontend" / "components" / "datasources" / "TablesSelector.vue"
EN = REPO / "locales" / "en.json"


def _fn(src: str, header: str) -> str:
    """The body of a python function, from its `def` to the next top-level one."""
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n(?:async def |def |@)", rest)
    return rest[: nxt.start()] if nxt else rest


# ---------------------------------------------------------------------------
# Power BI: the same ordering fault Fabric had
# ---------------------------------------------------------------------------
def test_the_powerbi_learn_is_awaited_before_progress_finishes():
    """★The whole fault in one assertion, for the second connector."""
    src = PBI_USER.read_text(encoding="utf-8")
    body = _fn(src, "async def _run_tenant_merge(")
    learn = body.index("relearn_overview_now")
    finish = body.index("await prog.finish(")
    assert learn < finish, (
        "prog.finish() runs before the re-learn again — progress reaches a "
        "terminal state while the agent is still learning"
    )


def test_the_powerbi_learning_stage_is_reported_before_the_learn_starts():
    body = _fn(PBI_USER.read_text(encoding="utf-8"), "async def _run_tenant_merge(")
    assert body.index("await prog.learning(") < body.index("relearn_overview_now")


def test_a_failed_powerbi_learn_still_finishes_the_sync():
    """A member with tenants crawled and no overview has a working agent. The
    finish call must sit outside the try that guards the learn."""
    body = _fn(PBI_USER.read_text(encoding="utf-8"), "async def _run_tenant_merge(")
    between = body[body.index("await prog.learning("):body.index("await prog.finish(")]
    assert "except Exception as _re" in between


def test_the_merge_no_longer_fires_its_own_background_learn():
    """★`_merge_all_tenants` scheduled the learn on BOTH of its exits, which is
    exactly why the caller had to finish early — a fire-and-forget task cannot
    be awaited. The learn belongs to the caller that owns the tracker."""
    body = _fn(PBI_USER.read_text(encoding="utf-8"), "async def _merge_all_tenants(")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "schedule_overview_relearn" not in code


def test_the_tenant_reselect_path_reports_its_learn():
    """★A request path cannot await a 30-second learn — but it can still say one
    is running. This was the third silent Power BI learn."""
    src = PBI_USER.read_text(encoding="utf-8")
    body = _fn(src, "async def user_signin_select_tenant(")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "_kick_off_tracked_learn(" in code
    assert "schedule_overview_relearn" not in code


def test_the_tracked_learn_reports_a_stage_then_finishes():
    body = _fn(PBI_USER.read_text(encoding="utf-8"), "def _kick_off_tracked_learn(")
    assert body.index("prog.learning(") < body.index("relearn_overview_now")
    assert body.index("relearn_overview_now") < body.index("prog.finish(")


def test_the_tracked_learn_keeps_a_strong_task_reference():
    """★asyncio holds only a WEAK reference to a running task — without this the
    learn can be collected part-way through, silently, no traceback."""
    body = _fn(PBI_USER.read_text(encoding="utf-8"), "def _kick_off_tracked_learn(")
    assert "_SYNC_TASKS.add(task)" in body


def test_no_powerbi_learn_is_left_unreported():
    """A sweep, not a spot check: every re-learn in this file goes through one of
    the two reported paths."""
    src = PBI_USER.read_text(encoding="utf-8")
    # ★Matches the CALL, not the bare name — the replacement's own docstring
    # explains what it replaced, and naming the thing you removed is not using
    # it. The locale guard and the Phase C progress guard both hit this.
    assert "schedule_overview_relearn(" not in src


# ---------------------------------------------------------------------------
# The tracker: a learn nobody started still has to be watchable
# ---------------------------------------------------------------------------
def test_a_learn_with_no_user_is_visible_to_the_caller():
    """★Seeding and the model-key auto-heal both run with `current_user=None`,
    so they stamp under `user_id = ""` — and the status endpoint looks up the
    CALLER's id. Those two runs, the first learns of the product's life, could
    never be seen by anybody."""
    body = _fn(LEARN.read_text(encoding="utf-8"), "async def get(")
    assert 'await _row(db, data_source_id, "")' in body, (
        "the system-triggered learn is unreachable again"
    )


def test_a_members_own_row_still_wins():
    """The fallback must only fire when this member has no run of their own —
    otherwise a shared learn would mask a private one."""
    body = _fn(LEARN.read_text(encoding="utf-8"), "async def get(")
    fallback = body.index('await _row(db, data_source_id, "")')
    guard = body.index("if row is None and uid:")
    assert guard < fallback


def test_the_fallback_cannot_repeat_itself():
    """★`uid` is already `""` for a system caller. Without the `and uid` the
    lookup would simply run twice with the same key."""
    body = _fn(LEARN.read_text(encoding="utf-8"), "async def get(")
    assert "if row is None and uid:" in body


def test_the_status_endpoint_is_unchanged():
    """The fix belongs in the service, where every consumer gets it — not in one
    route that another caller could bypass."""
    body = _fn(ROUTE.read_text(encoding="utf-8"), "async def get_learn_status(")
    assert "learn_progress.get(db, str(data_source_id), str(current_user.id))" in body


# ---------------------------------------------------------------------------
# The bar: notice a learn you did not start
# ---------------------------------------------------------------------------
def test_the_bar_can_watch_for_learns_it_did_not_start():
    src = BAR.read_text(encoding="utf-8")
    assert "autoDetect?: boolean" in src
    assert "function watchTick" in src


def test_the_watcher_only_opens_on_a_running_learn():
    """★A settled `done` sits in the tracker indefinitely. Opening on anything
    but `running` would re-raise the bar on every visit to the page."""
    src = BAR.read_text(encoding="utf-8")
    body = src[src.index("async function watchTick"):]
    body = body[:body.index("\n}") + 2]
    assert "p.status === 'running'" in body


def test_the_watcher_is_silent_while_the_bar_is_already_up():
    """Two pollers on one endpoint, one of them pointless."""
    body = BAR.read_text(encoding="utf-8")
    body = body[body.index("async function watchTick"):]
    body = body[:body.index("\n}") + 2]
    assert "props.modelValue" in body


def test_the_watcher_polls_slower_than_the_live_bar():
    src = BAR.read_text(encoding="utf-8")
    assert re.search(r"const WATCH_MS = (\d+)", src)
    watch_ms = int(re.search(r"const WATCH_MS = (\d+)", src).group(1))
    assert watch_ms > 1000, "the watcher costs as much as the live poll"


def test_an_auto_detected_run_is_not_reset_to_stage_one():
    """★The Save flow stamps a fake `reading_tables` so the bar has something to
    draw. Doing that to a learn already at stage three walks the pips backwards
    on the first frame."""
    src = BAR.read_text(encoding="utf-8")
    body = src[src.index("function startPolling"):]
    body = body[:body.index("\n}") + 2]
    assert "if (reset) {" in body
    assert "startPolling(!selfOpened.value)" in src


def test_the_bar_asks_for_a_reload_when_a_learn_finishes():
    """★Otherwise the message says the overview is ready and the page still
    shows the old one — the same fault the credentials window had."""
    src = BAR.read_text(encoding="utf-8")
    assert "(e: 'learned'): void" in src
    assert "emit('learned')" in src


def test_the_completion_message_cannot_repeat():
    """Keyed on the run's own completion time, so a reload cannot say it twice
    and a genuinely new learn still gets its own."""
    src = BAR.read_text(encoding="utf-8")
    body = src[src.index("function announceDone"):]
    body = body[:body.index("\n}\n") + 3]
    assert "last_done_at" in body
    assert "localStorage" in body


def test_the_save_flow_does_not_get_a_second_message():
    """It already reports its own result inline; two notices for one action."""
    src = BAR.read_text(encoding="utf-8")
    body = src[src.index("function announceDone"):]
    body = body[:body.index("\n}\n") + 3]
    assert "if (!selfOpened.value) return" in body


def test_the_existing_save_flow_is_unchanged():
    """★The bar is shared. `autoDetect` is opt-in, so the Tables panel must not
    have acquired a watcher, a toast, or a different reset."""
    src = TABLES.read_text(encoding="utf-8")
    assert "auto-detect" not in src
    assert "autoDetect" not in src


# ---------------------------------------------------------------------------
# The agent page
# ---------------------------------------------------------------------------
def test_the_agent_page_watches_for_teaching():
    src = EXPLORER.read_text(encoding="utf-8")
    assert "DatasourcesLearnProgressBar" in src
    assert "auto-detect" in src
    assert '@learned="onAgentLearned"' in src


def test_the_agent_page_reloads_what_the_learn_wrote():
    src = EXPLORER.read_text(encoding="utf-8")
    body = src[src.index("const onAgentLearned = async ()"):]
    body = body[:body.index("\n}") + 2]
    assert "refreshAgentDetail()" in body
    assert "loadGroup(id, true)" in body, (
        "`force` is required — the group is already marked loaded, so a plain "
        "call returns immediately and the fix is a silent no-op"
    )
    assert "refreshStarterPrompts()" in body


def test_the_page_never_opens_the_bar_itself():
    """★The bar owns its own visibility in auto-detect mode. A second writer
    would fight the watcher over the same ref."""
    src = EXPLORER.read_text(encoding="utf-8")
    assert src.count("showAgentLearnBar") == 2   # the ref, and the v-model
    assert "showAgentLearnBar.value = true" not in src


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
def test_the_completion_message_has_its_locale_keys():
    """★vue-i18n renders the KEY on a miss — a member would read
    `data.learnDoneToastTitle` in a notification. This has happened twice here."""
    import json

    data = json.loads(EN.read_text(encoding="utf-8"))["data"]
    assert data["learnDoneToastTitle"]
    assert "{n}" in data["learnDoneToastBody"]
    assert data["learnDoneToastBodyPlain"]
