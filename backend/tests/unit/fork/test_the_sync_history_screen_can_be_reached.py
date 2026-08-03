"""The three ways into the sync-history screen, and the words on the way in.

A screen nobody can reach from where the question occurs to them is a screen
nobody uses. The question occurs in three places, and each one gets a route:

* the **notification** that says a sync had gaps — it must land on THAT sync,
  not on a page that says "open the agent to see which" about a breakdown the
  agent page does not have;
* the **sync strip** on an agent, which reports the last run and used to be a
  dead end for "has this been failing all week?";
* the **agent filter** on the screen itself, which has to survive being linked
  to — a filter in a ref makes both routes above one-way trips.

★These read files only — no schema — so they belong in `tests/unit/fork`. See
CLAUDE.md.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
BACKEND = Path(__file__).resolve().parents[3]
EN = REPO / "locales" / "en.json"
FRONTEND = REPO / "frontend"
SCREEN = FRONTEND / "components" / "KeeperScreen.vue"
STRIP = FRONTEND / "components" / "datasources" / "ConnectionSyncStrip.vue"
NOTIFY = BACKEND / "app" / "services" / "sync_notifications.py"
ACTIONS = BACKEND / "app" / "services" / "keeper_actions.py"


def _has_key(locale: dict, dotted: str) -> bool:
    node = locale
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


@pytest.fixture(scope="module")
def locale() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def notify() -> str:
    return NOTIFY.read_text(encoding="utf-8")


# ───────────────────────── the notification lands ────────────────────────


def test_a_failed_sync_notification_opens_that_sync(notify: str):
    """★A failure is a question by definition, and the answer — the error, the
    phase it died in, which workspaces had already answered — is on the run and
    nowhere else."""
    block = notify[notify.index("async def notify_sync_failed"):]
    assert "_link_for(data_source_id, user_id, to_the_run=True)" in block
    assert 'link=f"/agents/{data_source_id}"' not in block


def test_a_gappy_sync_notification_opens_that_sync_but_a_clean_one_does_not(notify: str):
    """★Different outcomes want different destinations. "Your agent is ready" is
    an invitation to go and use the data; "two workspaces did not answer" is a
    question. Sending both to the agent page is what made the old wording say
    "open the agent to see which" about a breakdown that was not there."""
    block = notify[notify.index("async def notify_sync_finished"):]
    block = block[: block.index("async def notify_sync_failed")]
    assert "to_the_run=(partial or tables == 0)" in block
    assert "open the agent to see which" not in block, (
        "the body still sends the member to a page without the breakdown"
    )


def test_the_deep_link_falls_back_rather_than_dropping_the_notification(notify: str):
    """★A link that is merely less specific beats a notification that was never
    sent. The run lookup is best-effort and cannot be allowed to raise."""
    block = notify[notify.index("async def _link_for"):]
    block = block[: block.index("\n\ndef _plural")]
    assert "except Exception" in block
    assert 'return f"/agents/{data_source_id}"' in block


def test_the_link_points_at_a_screen_that_reads_that_query(notify: str):
    """The notification writes `?keeper=activity&run=…`; the screen decides what
    it shows from exactly those two parameters. Pinned so a rename of either one
    fails here rather than becoming a link that opens an empty page."""
    assert "/agents?keeper=activity&run=" in notify
    screen = SCREEN.read_text(encoding="utf-8")
    assert "route.query.keeper" in screen and "route.query.run" in screen


# ──────────────────────── the strip is not a dead end ────────────────────


def test_the_sync_strip_links_to_the_rest_of_the_history():
    """The strip says what the LAST sync did. Whether the same workspace has
    been missing all week is a different question with the same urgency."""
    strip = STRIP.read_text(encoding="utf-8")
    assert "historyLink" in strip
    assert "keeper=activity&agent=" in strip, (
        "the link must carry the agent, or it opens every agent's history"
    )


def test_the_strip_link_is_absent_rather_than_unfiltered():
    """No id → no link. An unfiltered list the member did not ask for is worse
    than no link at all."""
    strip = STRIP.read_text(encoding="utf-8")
    block = strip[strip.index("const historyLink"):]
    block = block[: block.index("\n\n")]
    assert "id ?" in block and "null" in block
    assert 'v-if="historyLink"' in strip


def test_the_agent_filter_lives_in_the_url():
    """★Both routes above are one-way trips otherwise: the back button would
    return to a screen showing every agent instead of the one being linked to."""
    screen = SCREEN.read_text(encoding="utf-8")
    assert "const filterAgent = computed(() => (route.query.agent" in screen
    assert "const filterAgent = ref(" not in screen


# ───────────────────────────── sync all now ──────────────────────────────


def test_every_skip_reason_the_service_emits_has_wording(locale: dict):
    """★"Queued 2 of 5" with no reason is the shape that gets reported as data
    loss. A reason added in the service and not here renders as
    `keeper.skipped.token_expired` on screen — vue-i18n prints the key."""
    source = ACTIONS.read_text(encoding="utf-8")
    reasons = set(re.findall(r'"reason": "(\w+)"', source))
    assert reasons, "no skip reasons found — the shape changed"
    missing = sorted(r for r in reasons if not _has_key(locale, f"keeper.skipped.{r}"))
    assert not missing, f"skip reasons with no wording: {missing}"


def test_the_button_and_its_report_are_worded(locale: dict):
    for key in ("keeper.syncAll", "keeper.queuedN", "keeper.queuedNone"):
        assert _has_key(locale, key), key


def test_the_screen_says_what_the_button_did():
    """A button that queues four syncs and shows nothing is indistinguishable
    from a button that did nothing."""
    screen = SCREEN.read_text(encoding="utf-8")
    assert "syncAllResult" in screen
    assert "keeper.skipped." in screen, "skips must be rendered, not just returned"


def test_the_overview_is_refreshed_after_queueing():
    """The toolbar button going from "Synced" to "Syncing 1" is the only signal
    that the queue started; without a refresh it appears for up to a minute."""
    screen = SCREEN.read_text(encoding="utf-8")
    block = screen[screen.index("async function syncAll()"):]
    block = block[: block.index("\n}")]
    assert "refresh()" in block


def test_the_queue_is_sequential_by_construction():
    """★Guarded in the unit tests by measuring concurrency; guarded here against
    the edit that would break it — a `gather` or a `create_task` per agent in
    the drain turns the queue back into the stampede it exists to prevent."""
    source = ACTIONS.read_text(encoding="utf-8")
    block = source[source.index("async def _drain("):]
    block = block[: block.index("\n\nasync def sync_all")]
    assert "gather" not in block
    assert "create_task" not in block
    assert "for agent in agents:" in block
