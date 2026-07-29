"""Connecting must stay visible until the agent can actually answer.

Three faults, all in the moment AFTER a Fabric sign-in succeeds. The server was
never broken - the timestamps from the live run prove it - but the screen said
nothing for the last thirty-seven seconds and then needed a manual refresh:

    08:55:32  sign-in accepted, crawl starts
    08:56:07  crawl ends, progress marked DONE, window closes
    08:56:12  the learn begins          <- five seconds after the screenshot
    08:56:44  the instruction is written

  1. `finish()` ran four lines before the learn was scheduled, so a terminal
     state was reached while the longest stage had not started.
  2. The page closed the window on the modal's `saved` signal, which means
     "reload your data", not "close me".
  3. Nothing reloaded the instruction list, so the thing the learn had just
     produced was invisible until a page reload.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
ROUTE = REPO / "backend" / "app" / "routes" / "fabric_user_signin.py"
PROGRESS = REPO / "backend" / "app" / "services" / "connection_sync_progress.py"
SERVICE = REPO / "backend" / "app" / "services" / "data_source_service.py"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
MODAL = REPO / "frontend" / "components" / "UserDataSourceCredentialsModal.vue"
COMPOSABLE = REPO / "frontend" / "composables" / "useConnectionSync.ts"
STRIP = REPO / "frontend" / "components" / "datasources" / "ConnectionSyncStrip.vue"
EN = REPO / "locales" / "en.json"


# ---------------------------------------------------------------------------
# fault 2: order of finish vs learn
# ---------------------------------------------------------------------------
def test_the_learn_is_awaited_before_progress_finishes():
    """★The whole fault in one assertion: the byte offset of `prog.finish` must
    come AFTER the re-learn call, not before it."""
    src = ROUTE.read_text(encoding="utf-8")
    learn = src.index("relearn_overview_now")
    finish = src.index("await prog.finish(")
    assert learn < finish, (
        "prog.finish() runs before the re-learn again - progress would reach a "
        "terminal state while the agent is still learning"
    )


def test_the_learning_stage_is_reported_before_the_learn_starts():
    src = ROUTE.read_text(encoding="utf-8")
    learning = src.index("await prog.learning(")
    learn = src.index("relearn_overview_now")
    assert learning < learn


def test_the_route_no_longer_fires_the_learn_into_the_background():
    """A fire-and-forget task cannot be awaited, which is precisely why the
    tracker had to be finished early."""
    src = ROUTE.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "schedule_overview_relearn" not in code


def test_a_failed_learn_still_finishes_the_sync():
    """A member with 61 synced tables and no overview has a working agent. The
    finish call must not be inside the try that guards the learn."""
    src = ROUTE.read_text(encoding="utf-8")
    body = src[src.index("await prog.learning("):src.index("await prog.finish(")]
    # the finish call sits after the except block closes, at the same indent
    assert re.search(r"\n            await prog\.finish\(", src), (
        "prog.finish must run at the function's own indent level, outside the "
        "learn's try/except"
    )
    assert "except Exception as _re" in body


# ---------------------------------------------------------------------------
# the learning state itself
# ---------------------------------------------------------------------------
def test_learning_exists_and_is_not_terminal():
    """★`last_done_at` is what marks a sync finished. Setting it here would make
    `learning` read as a completed sync to every consumer."""
    src = PROGRESS.read_text(encoding="utf-8")
    body = src[src.index("async def learning("):src.index("async def finish(")]
    assert 'row.status = "learning"' in body
    # ★Matches the ASSIGNMENT, not the bare name. The first version asserted
    # `"last_done_at" not in body` and failed on the docstring above, which
    # explains the very rule it enforces - the same trap the locale guard hit.
    assert "row.last_done_at" not in body, "learning must not stamp a completion time"


def test_finish_is_still_the_only_terminal_success():
    src = PROGRESS.read_text(encoding="utf-8")
    body = src[src.index("async def finish("):src.index("async def fail(")]
    assert "row.last_done_at = datetime.utcnow()" in body


def test_the_awaitable_relearn_keeps_the_inflight_guard():
    """Without the guard, two syncs for one agent run two overviews at once and
    the second overwrites the first."""
    src = SERVICE.read_text(encoding="utf-8")
    body = src[src.index("async def relearn_overview_now("):src.index("async def _relearn_overview_bg(")]
    assert "_RELEARN_INFLIGHT" in body
    assert "await self._relearn_overview_bg(" in body


def test_schedule_overview_relearn_still_exists_for_other_callers():
    """File upload and the model-key auto-heal still want fire-and-forget."""
    assert "def schedule_overview_relearn(" in SERVICE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# fault 1: the window
# ---------------------------------------------------------------------------
def test_the_page_no_longer_closes_the_window_on_saved():
    """★One line. It made the summary screen unreachable for everyone."""
    src = EXPLORER.read_text(encoding="utf-8")
    body = src[src.index("const onCredsSaved = async ()"):]
    body = body[:body.index("\n}") + 2]
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//"))
    assert "showCredsModal.value = false" not in code, (
        "the page closes the credentials window on `saved` again - `saved` means "
        "'reload your data', and the modal closes itself when closing is right"
    )


def test_the_modal_still_closes_itself():
    """Removing the page's close must not leave a window nobody can dismiss."""
    src = MODAL.read_text(encoding="utf-8")
    assert "emit('update:modelValue', false)" in src


def test_the_modal_keeps_polling_through_the_learning_stage():
    """★The learning stage must NOT be treated as terminal, or the modal stops
    watching at exactly the moment the new stage begins.

    ★Since the vocabulary was unified, learning is no longer a STATUS at all —
    it is `running`, with the stage in `phase`. So the stronger statement is
    that the terminal check contains ONLY terminal statuses; a `running` in
    there would end the poll mid-run.
    """
    src = MODAL.read_text(encoding="utf-8")
    # ★Anchored on the START of the condition, not on a value inside it. The
    # first version began matching at `p.status === 'done'`, so a value inserted
    # BEFORE it sat outside the match and the guard stayed green against a real
    # plant.
    terminal = re.search(r"if \(p && \((.*?)\)\) \{", src, re.S)
    assert terminal, "could not find the terminal-status check"
    cond = terminal.group(1)
    assert "'completed'" in cond, "matched the wrong condition"
    for not_terminal in ("'running'", "'learning'", "'syncing'", "'pending'"):
        assert not_terminal not in cond, (
            f"the modal stops polling on {not_terminal} - it would close while "
            f"the sync is still working"
        )


# ---------------------------------------------------------------------------
# fault 3: the instruction list
# ---------------------------------------------------------------------------
def test_instructions_are_reloaded_when_the_connector_finishes():
    src = EXPLORER.read_text(encoding="utf-8")
    body = src[src.index("const onCredsSaved = async ()"):]
    body = body[:body.index("\n}") + 2]
    assert "loadGroup(id, true)" in body, (
        "the instruction list is not reloaded - the overview the learn just "
        "wrote stays invisible until a page refresh"
    )


def test_the_instruction_reload_forces_a_refetch():
    """★`loadGroup` returns immediately for a group it has already loaded. The
    call is a silent no-op without `force`, which looks exactly like a fix."""
    src = EXPLORER.read_text(encoding="utf-8")
    assert re.search(r"const loadGroup = async \(key: string, force = false\)", src)
    body = src[src.index("const loadGroup = async"):]
    body = body[:body.index("\n}") + 2]
    assert "if (!force && loadedGroups.value.has(key)) return" in body


# ---------------------------------------------------------------------------
# the frontend must understand the new state
# ---------------------------------------------------------------------------
def test_the_composable_knows_learning_is_a_running_state():
    """★Learning moved from `status` to `phase` when the four trackers were given
    one vocabulary: "is it running" and "running what" are different questions,
    and every other tracker already kept the second one in `phase`. What must
    still hold is that the stage is visible AND counts as running."""
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert "'learning'" in src, "the stage is no longer visible at all"
    assert "export function isRunningStatus" in src
    body = src[src.index("export function isRunningStatus"):]
    body = body[:body.index("\n}") + 2]
    assert "status === 'running'" in body
    # the stage itself is still surfaced, from phase
    assert "phase === 'learning'" in src


def test_learning_polls_at_the_fast_rate():
    """★Missing this would be invisible in testing: the strip still appears, it
    just updates every 30 seconds during the stage the member is watching."""
    src = COMPOSABLE.read_text(encoding="utf-8")
    body = src[src.index("function desiredInterval"):]
    body = body[:body.index("\n}") + 2]
    assert "isRunningStatus(state.status)" in body


def test_the_strip_names_the_learning_stage_before_the_generic_running_one():
    """★Order matters: learning IS running, so a plain isRunning check first
    would report 'Reading' for the whole learn."""
    src = STRIP.read_text(encoding="utf-8")
    label = src[src.index("const chipLabel = computed"):]
    label = label[:label.index("\n})") + 3]
    assert label.index("isLearning.value") < label.index("isRunning.value")


def test_both_new_locale_keys_exist():
    """★vue-i18n renders the KEY on a miss - a member would see
    `data.syncStatLearning` on screen. This has happened twice here."""
    import json

    data = json.loads(EN.read_text(encoding="utf-8"))["data"]
    assert data["syncStatLearning"]
    assert "{n}" in data["syncLearningBody"]


def test_the_reading_label_says_reading_not_syncing():
    """The two stages are now distinct, so the first one needs a name that is
    not a synonym for the whole process."""
    import json

    data = json.loads(EN.read_text(encoding="utf-8"))["data"]
    assert data["syncStatSyncing"] == "Reading"
