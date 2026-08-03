"""The sync-history screen: its URL, its exits, and the words it renders.

Three separate things are held down here, and each one has already gone wrong
somewhere in this codebase.

**The URL is the state.** `?keeper=<tab>&run=<id>` decides whether the screen is
open, which tab shows, and which run is expanded. A second copy of "is it open"
in a ref is how back, forward, refresh and a pasted support link stop agreeing.

**Every vocabulary the screen renders comes from somewhere else.** The trigger
names are written by `sync_runs`, the result names by `KeeperService`, the
`runs_when` values by `KeeperService.schedule`. vue-i18n renders the KEY when it
is missing, so a value added on the backend and not here does not fail — it puts
`keeper.trigger.backfill` on screen next to a real word.

**A quoted cadence must be the real one.** The Schedule tab tells members auto
learn is checked every N minutes. N is a copy of an APScheduler registration in
`main.py`, because the scheduler has no API to ask, and a copy that drifts is a
number nobody honours.

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
COMPOSABLE = FRONTEND / "composables" / "useKeeper.ts"
TOOLBAR = FRONTEND / "components" / "KnowledgeExplorer.vue"

KEY_RE = re.compile(r"(?<![\w.])\$?t\(\s*['\"]([a-zA-Z][\w.]*)['\"]")


@pytest.fixture(scope="module")
def screen() -> str:
    return SCREEN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def locale() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


def _has_key(locale: dict, dotted: str) -> bool:
    node = locale
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


# ───────────────────────── the URL is the state ──────────────────────────


def test_the_screen_opens_from_the_url_and_not_from_a_flag(screen: str):
    """★No `show` ref. Visibility is computed from `route.query.keeper`, so a
    pasted link and a click land in exactly the same place."""
    assert "const isOpen = computed(() => TABS.includes(route.query.keeper" in screen
    assert not re.search(r"const\s+(isOpen|show|visible)\s*=\s*ref\(", screen), (
        "a local open flag would be a second source of truth for the same fact"
    )


def test_the_open_run_comes_from_the_url_too(screen: str):
    """A deep link may name a run that is not on the first page of the list.
    Fetching by the id in the URL rather than from the clicked row is what makes
    that link work."""
    assert "const openRunId = computed<string | null>(() => (route.query.run" in screen
    assert "watch(openRunId" in screen


def test_closing_removes_the_query_rather_than_hiding_the_element(screen: str):
    assert "function close() { setQuery({ keeper: undefined, run: undefined }) }" in screen


def test_it_is_the_same_kind_of_dialog_as_every_other_one(screen: str):
    """★Changed 2026-08-03, and the reason is worth keeping.

    This began as a `fixed inset-0` overlay that took the whole window, so
    opening it REPLACED the Agents screen instead of sitting over it — nothing
    signalled a temporary view and the only way back was one button. It is now
    the shell `AllInstructionsModal` uses, which is also what Connections and
    Trace use.

    The practical consequence is that Escape, focus trapping and the backdrop
    click come from `UModal` rather than being hand-rolled here. The two tests
    that used to assert the hand-rolled versions are gone deliberately; asserting
    a `keydown` listener that no longer exists would force it back, and a
    window-level Escape handler fires even when another modal is on top of this
    one.
    """
    assert "<UModal" in screen
    assert "sm:max-w-5xl" in screen, (
        "the width must match AllInstructionsModal, or this reads as a different "
        "kind of thing"
    )
    assert "fixed inset-0" not in screen.split("</template>")[0].replace(
        # the explanatory comment names the old class on purpose
        "This started life as a `fixed inset-0` overlay", ""
    ), "the full-bleed overlay is back"
    assert "addEventListener('keydown'" not in screen, (
        "UModal already handles Escape — a second handler races it"
    )


def test_dismissing_the_modal_also_clears_the_url(screen: str):
    """★The bug this shape makes possible, and the reason the test above is not
    the whole story.

    The URL is the source of truth for whether this is open. `UModal` closing
    itself is therefore NOT enough: press Escape without routing that back
    through `close()`, and the card disappears while `?keeper=activity` is still
    in the address bar — so the next render re-opens it, and the back button
    lands on a screen the member already dismissed.
    """
    # To `<UCard`, not to the first `>` — the handler contains an arrow
    # function, so `>` appears inside the attribute value.
    block = screen[screen.index("<UModal"):]
    block = block[: block.index("<UCard")]
    assert "@update:model-value" in block
    assert "close()" in block, (
        "a dismissal must clear the query, not just hide the element"
    )


def test_a_run_row_does_not_put_five_columns_on_one_line(screen: str):
    """★The reason this modal is 1024px and still readable.

    A run carries agent · result · trigger · duration · time. All five on one
    line fit the old full-bleed overlay and do not fit here — and the first
    things to crowd out are the trigger chip and the duration, which are exactly
    what answers "did I start this, and how long did it take". So the outcome
    stays on line one where it is scanned, and the circumstances drop to a muted
    second line.
    """
    row = screen[screen.index('v-for="run in activity.items"'):]
    row = row[: row.index("<!-- Expanded detail")]
    head, _, meta = row.partition("</span>\n              <span class=\"mt-0.5")
    assert meta, "the second line is gone — the row is back to one line"
    # Line one: what happened. Line two: the circumstances.
    assert "run.data_source_name" in head and "keeper.result." in head
    assert "keeper.trigger." in meta and "humanDuration" in meta and "relativeTime" in meta


def test_activity_is_the_landing_tab(screen: str):
    """"What just happened" is the question people arrive with. Overview
    summarises it and is worth reading second."""
    assert "TABS = ['activity'" in screen
    assert "route.query.keeper as Tab) || 'activity'" in screen
    assert "keeper: 'activity'" in TOOLBAR.read_text(encoding="utf-8"), (
        "the toolbar must open the same landing tab"
    )


def test_opening_a_run_from_another_tab_is_one_navigation(screen: str):
    """Switching tab and expanding the row in two pushes means the back button
    undoes half the jump and leaves the reader somewhere they never chose."""
    block = screen[screen.index("function openRunFromAnywhere"):]
    block = block[: block.index("\n\n")]
    assert block.count("setQuery(") == 1
    assert "keeper: 'activity'" in block and "run: id" in block


# ──────────────────────── the vocabularies agree ─────────────────────────


def test_every_trigger_the_backend_writes_has_a_word_on_screen(locale: dict):
    """★`sync_runs` owns this list. A trigger added there and not here renders
    as `keeper.trigger.backfill` beside real words — vue-i18n does not fall
    back, it prints the key."""
    source = (BACKEND / "app" / "services" / "sync_runs.py").read_text(encoding="utf-8")
    triggers = set(re.findall(r'^TRIGGER_\w+ = "(\w+)"', source, re.M))
    assert triggers, "no triggers found — the constant naming changed"
    missing = sorted(t for t in triggers if not _has_key(locale, f"keeper.trigger.{t}"))
    assert not missing, f"triggers with no wording: {missing}"


def test_every_result_the_service_can_return_has_a_word_on_screen(locale: dict):
    """`_result_of` is the only place these five are decided."""
    source = (BACKEND / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    block = source[source.index("def _result_of("):]
    block = block[: block.index("\n\ndef ")]
    # Every string literal on a `return` line — `_result_of` returns one
    # through a ternary, which a `return "(\w+)"` pattern silently misses.
    # Every string literal on a `return` line — `_result_of` returns one through
    # a ternary, which a `return "(\w+)"` pattern silently misses. Dictionary
    # lookups on the same line are stripped first: `stats.get("result")` is a
    # key being read, not a value being returned.
    results = {
        literal
        for line in block.splitlines() if "return" in line
        for literal in re.findall(r'"(\w+)"', re.sub(r'\.get\([^)]*\)', "", line))
    }
    assert results == {"partial", "completed", "failed", "cancelled", "running"}, results
    missing = sorted(r for r in results if not _has_key(locale, f"keeper.result.{r}"))
    assert not missing, f"results with no wording: {missing}"


def test_every_tab_has_a_name(screen: str, locale: dict):
    tabs = re.search(r"const TABS = \[([^\]]+)\]", screen).group(1)
    names = re.findall(r"'(\w+)'", tabs)
    assert len(names) == 5
    missing = sorted(n for n in names if not _has_key(locale, f"keeper.tab.{n}"))
    assert not missing, f"tabs with no name: {missing}"


def test_every_runs_when_value_has_a_word(locale: dict):
    source = (BACKEND / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    block = source[source.index('"runs_when":'):]
    block = block[: block.index("\n            }")]
    values = set(re.findall(r'"(\w+)"', block)) - {"runs_when"}
    assert values >= {"signin", "auto_learn"}
    missing = sorted(v for v in values if not _has_key(locale, f"keeper.runsWhen.{v}"))
    assert not missing, f"runs_when values with no wording: {missing}"


def test_every_literal_locale_key_on_the_screen_exists(screen: str, locale: dict):
    used = set(KEY_RE.findall(screen))
    missing = sorted(k for k in used if not _has_key(locale, k))
    assert not missing, f"missing from en.json: {missing}"


# ───────────────────────── the numbers are real ──────────────────────────


def test_the_schedule_tab_quotes_the_cadence_the_scheduler_actually_uses():
    """★A copy of an APScheduler registration. Nothing enforces the two agreeing
    except this."""
    service = (BACKEND / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    quoted = int(re.search(r"_AUTO_LEARN_SWEEP_MINUTES = (\d+)", service).group(1))

    main = (BACKEND / "main.py").read_text(encoding="utf-8")
    job = main[: main.index('id="auto_learn_sweep"')]
    registered = int(re.findall(r"minutes=(\d+),", job)[-1])

    assert quoted == registered, (
        f"the Schedule tab tells members every {quoted} minutes; the scheduler "
        f"runs every {registered}"
    )


def test_a_signin_agent_is_never_given_a_next_run_time():
    """★There is none. A per-user connector runs on the member's own token, so
    it syncs when they sign in and at no other moment — quoting a next run would
    be a lie they could plan around."""
    service = (BACKEND / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    block = service[service.index("    async def schedule("):]
    block = block[: block.index("    # ──────────────────────────── run detail")]
    assert "next_run_at" not in block
    assert "signin" in block


# ─────────────────────────── list behaviour ──────────────────────────────


def test_the_list_is_not_polled(screen: str):
    """The overview is a heartbeat; a paginated list that reshuffles under a
    reader's cursor every five seconds is hostile. Refresh is a button."""
    source = COMPOSABLE.read_text(encoding="utf-8")
    fetchers = source[source.index("export async function fetchKeeperActivity"):]
    assert "setInterval" not in fetchers
    assert "keeper.refresh" in screen, "there must be a manual refresh instead"


def test_run_detail_is_fetched_on_open_not_with_the_list(screen: str):
    """The per-workspace breakdown and the event log are the large half of the
    payload, and most rows are never opened."""
    assert "async function loadDetail(" in screen
    assert "fetchKeeperRun(id)" in screen


def test_a_run_the_member_cannot_see_says_so(screen: str, locale: dict):
    """`fetchKeeperRun` returns null for "no such run", "not yours" and "agent
    you cannot see" alike. An expanded row with nothing in it reads as broken."""
    assert "keeper.runGone" in screen
    assert _has_key(locale, "keeper.runGone")


def test_our_own_outages_are_labelled_as_ours(screen: str):
    """★The 2026-08-03 harm was telling a member to check a credential that was
    fine. `needs_a_person` already excludes infrastructure failures; when one is
    opened directly it still has to say whose fault it was."""
    assert "error_kind === 'infrastructure'" in screen
    assert "keeper.ourSide" in screen


def test_the_screen_is_mounted():
    source = TOOLBAR.read_text(encoding="utf-8")
    assert "<KeeperScreen />" in source
    assert "import KeeperScreen" in source
