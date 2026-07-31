"""The agent header must be refreshable, and the agent trainable from it.

Two gaps, both reported from the running product with screenshots.

**Refresh.** The header's counts are fetched once by `openAgent` and never
again. Upload a file, reload tables, convert a document — all of it happens on
another tab, and the header goes on stating "0 tables · 0 files · 0 instructions"
over an agent that plainly has them. There was no way to make it re-read short
of navigating away and back.

**Train.** `POST /data_sources/{id}/relearn` reads the active tables and rewrites
the agent's overview. It has existed for a long time and was reachable from
exactly one place: the Tables tab's "Save & Learn". So an agent whose table
selection was never re-saved could not be taught from the agent page at all —
which is precisely where someone looking at an empty "No primary instruction"
panel is standing.
"""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[4] / "frontend"
EXPLORER = FRONTEND / "components" / "KnowledgeExplorer.vue"
LOCALE = Path(__file__).resolve().parents[4] / "locales" / "en.json"


@pytest.fixture(scope="module")
def src() -> str:
    if not EXPLORER.exists():
        pytest.skip(f"component not present at {EXPLORER}")
    return EXPLORER.read_text()


def test_the_header_offers_a_refresh(src):
    assert "refreshAgent(" in src


def test_refresh_reloads_everything_the_header_counts(src):
    """A refresh that updated only one of them would leave the header
    internally inconsistent, which is worse than uniformly stale."""
    body = src[src.index("const refreshAgent"):src.index("const trainAgent")]
    for loader in ("refreshAgentDetail", "loadAgentMeta", "fetchCounts"):
        assert loader in body, f"refresh does not re-run {loader}"


def test_refresh_survives_one_loader_failing(src):
    """Five independent fetches. One failing endpoint must not leave the other
    four unrefreshed and the spinner stuck."""
    body = src[src.index("const refreshAgent"):src.index("const trainAgent")]
    assert ".catch(() => null)" in body
    assert "finally" in body


def test_the_header_offers_training(src):
    assert "trainAgent(" in src
    body = src[src.index("const trainAgent"):]
    assert "/relearn" in body[:900], "the train button does not call the relearn endpoint"


def test_training_cannot_be_double_fired(src):
    """It costs an LLM call. A button that stays live while the first request is
    in flight invites a second press that spends again."""
    body = src[src.index("const trainAgent"):]
    guard = body[:body.index("try")]
    assert "agentTraining.value" in guard
    assert ':disabled="agentTraining"' in src


def test_training_reports_both_outcomes(src):
    """A silent failure is indistinguishable from a slow success, and this one
    takes long enough that the difference matters."""
    body = src[src.index("const trainAgent"):]
    assert "toastTrained" in body
    assert "toastTrainFailed" in body


def test_the_header_refreshes_itself_after_training(src):
    """Training writes the overview the header reports as missing. Without this
    the panel still says "No primary instruction" after a successful run."""
    body = src[src.index("const trainAgent"):]
    assert "refreshAgent(id)" in body


def test_the_new_strings_are_translatable(src):
    """Hardcoded English here would render as raw keys in every other locale."""
    import json

    keys = json.loads(LOCALE.read_text())["agentsPage"]
    for key in ("refreshTip", "trainAgent", "trainingAgent", "trainAgentTip",
                "toastTrained", "toastTrainFailed"):
        assert key in keys, f"missing locale key agentsPage.{key}"
        assert f"agentsPage.{key}" in src


def test_the_page_asks_the_bar_to_look_rather_than_opening_it(src):
    """Caught by an existing guard the moment it was violated.

    The progress bar owns its own visibility in auto-detect mode: it collapses
    itself when the run ends, but ONLY if it was the thing that opened it. A
    page that set `showAgentLearnBar` directly would therefore leave the bar up
    permanently — and the pre-existing test says so in one line.

    A click still needs the stages immediately, though; a five-second poll gap
    is the entire feedback for a button the user just pressed. So the page calls
    the bar's `checkNow()` — asking it to look now, leaving it to decide.
    """
    body = src[src.index("const trainAgent"):]
    assert "checkNow" in body[:1200], (
        "training no longer asks the bar to look, so the stages appear up to "
        "five seconds after the click that started them"
    )
    assert "showAgentLearnBar.value = true" not in src, (
        "the page writes the bar's visibility ref again — the bar will never "
        "collapse itself after the run ends"
    )


def test_the_bar_exposes_that_hook():
    """Otherwise the call above is a silent no-op — `?.()` on a method that does
    not exist fails exactly like a method that does nothing."""
    from pathlib import Path

    bar = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "datasources" / "LearnProgressBar.vue").read_text()
    assert "defineExpose" in bar
    assert "checkNow" in bar


# ── the run panel ───────────────────────────────────────────────────────────

def test_the_run_panel_is_a_column_not_an_overlay(src):
    """A teleported right-side drawer was tried on this page before and did not
    render reliably; it was replaced with an in-flow strip. The panel is laid out
    beside the content for the same reason — nothing it does not control can clip
    it."""
    assert "showTrainingPanel" in src
    assert "<aside v-if=\"showTrainingPanel\"" in src
    assert "Teleport" not in src[src.index("showTrainingPanel"):src.index("showTrainingPanel") + 4000]


def test_stage_state_comes_from_the_step_counter(src):
    """Derived from `step`, not from matching the stage NAME. A future rename
    then degrades to a wrong label rather than to no progress at all."""
    body = src[src.index("const stageState"):]
    assert "run.step" in body[:600]


def test_the_panel_stops_polling_when_the_run_ends(src):
    """It stays open showing the result, so a timer left running would cost a
    request a second for as long as the tab is open."""
    body = src[src.index("const pollTrainingRun"):]
    assert "clearInterval" in body[:900]
    assert "!== 'running'" in body[:900]


def test_the_poll_is_cleaned_up_when_the_page_goes_away(src):
    assert "onBeforeUnmount" in src
    assert "clearInterval(trainingPoll)" in src


def test_a_failed_run_is_shown_as_a_failure(src):
    """The tracker records the error. Without reading it, a failure and a slow
    success look the same — which is the state this replaced."""
    assert "trainingRun?.status === 'failed'" in src
    assert "agentsPage.trainFailedIntact" in src, (
        "the failure does not say the previous overview survived, which is the "
        "part that actually settles the question the user has"
    )


def test_a_finished_run_says_what_it_did(src):
    """"Agent trained" is not a result — the run replaced the instruction applied
    to every report."""
    assert "agentsPage.trainDoneRead" in src
    assert "agentsPage.trainDoneOverview" in src


def test_the_panel_can_be_reopened_after_it_is_closed(src):
    """Otherwise the only way back to the result is to train again, which costs
    a model call to re-read something already known."""
    assert "openTrainingPanel(agentView.agentId)" in src


def test_unknown_drift_is_not_reported_as_up_to_date(src):
    """Three states, not two: stale, current, and never recorded."""
    assert "agentsPage.trainUnknown" in src
    assert "trainingStatus?.known" in src


def test_every_panel_string_is_translatable():
    import json
    from pathlib import Path

    keys = json.loads((Path(__file__).resolve().parents[4] / "locales" / "en.json").read_text())["agentsPage"]
    for k in ("trainingRun", "stageReadTables", "stageAnalyze", "stageGenerate",
              "stagePublish", "runStep", "runFinished", "runStopped",
              "trainDoneTitle", "trainDoneRead", "trainDoneOverview",
              "trainFailedTitle", "trainFailedIntact", "tryAgain",
              "trainStatusLabel", "trainUpToDate", "trainUnknown", "lastTrained"):
        assert k in keys, f"missing locale key agentsPage.{k}"


def test_only_one_widget_reports_a_run_at_a_time(src):
    """Reported from a screenshot: the inline strip read "step 1/4 · 0:03" while
    the panel beside it read "finished in 0:41". Two widgets rendering the same
    run drift apart the moment one stops polling, and the user has no way to know
    which to believe.

    The panel holds everything the strip would say and more, so the strip is the
    fallback for when the panel is closed — not a second opinion.
    """
    assert 'v-if="agentView && !showTrainingPanel"' in src, (
        "the inline progress bar renders alongside the run panel again"
    )


def test_the_drift_notice_also_defers_to_the_panel(src):
    """The panel states the same thing under "Against its data now"; showing
    both is the same duplication in a quieter form."""
    assert 'trainingStatus?.stale && !showTrainingPanel' in src


def test_the_panel_carries_the_action_it_took_over(src):
    """Hiding the inline notice removes its Train button, so the panel has to
    offer one — otherwise closing the gap between the two widgets would cost the
    user the fix."""
    panel = src[src.index("agentsPage.trainStatusLabel"):]
    assert "agentsPage.trainNow" in panel[:2000]
    assert "trainAgent(agentView.agentId)" in panel[:2000]


def test_the_train_button_is_freed_by_the_tracker_not_the_request(src):
    """Reported from a screenshot: the button read "Training…" and stayed
    disabled while the panel beside it said the run had finished ten minutes
    earlier.

    `relearn` is answered synchronously and a real run takes minutes, so tying
    the button to the request means it is wrong for the whole gap between the
    work finishing and the response arriving. The tracker knows first — the same
    two-readings-of-one-event fault as the duplicated progress strip, in a
    different place.
    """
    body = src[src.index("const pollTrainingRun"):]
    terminal = body[:body.index("const openTrainingPanel")]
    assert "agentTraining.value = false" in terminal, (
        "the button is released only when the HTTP request returns, so it stays "
        "disabled after the run has visibly finished"
    )


def test_the_run_panel_is_reset_when_the_agent_changes(src):
    """Reported from a screenshot: the Power BI page showed a run that read
    "63 tables · 785 columns" — Microsoft Fabric's schema — and an elapsed time
    from a run hours old.

    The panel belongs to one agent. Every other per-agent ref in `openAgent` is
    cleared; this one was missed, so the previous agent's run stayed on screen
    under the new agent's name, which is worse than showing nothing.
    """
    body = src[src.index("const openAgent = async"):]
    assert "resetTrainingRun()" in body[:700], (
        "switching agents leaves the previous agent's training run on screen"
    )


def test_the_reset_also_stops_the_poll(src):
    """Clearing the data is not enough — a timer left running keeps writing the
    OLD agent's status into the panel the new one is looking at, so the stale
    run would reappear a second later."""
    body = src[src.index("const resetTrainingRun"):]
    assert "clearInterval(trainingPoll)" in body[:400]
    assert "trainingRun.value = null" in body[:400]


def test_a_long_run_is_not_rendered_as_minutes(src):
    """A stale row rendered "451:07", which reads as seven and a half minutes and
    is actually seven and a half hours. Wrong by a factor of sixty, and in the
    direction that makes it look plausible."""
    body = src[src.index("const trainingRunSubtitle"):]
    assert "3600" in body[:700], "elapsed time never shows hours"
