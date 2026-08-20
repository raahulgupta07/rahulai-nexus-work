"""The Keeper button has to be readable without being opened.

That is the entire justification for putting it in the toolbar rather than
adding a nav item: a member walking past the Agents screen should know whether
anything is wrong before they click. If the state is carried only by colour,
the button has bought nothing — it fails for anyone who cannot tell the two
tints apart, it fails in a screenshot pasted into a ticket, and it fails on the
first support call where somebody says "the icon is the normal one, I think".

So the three visible states are checked mechanically here: each must carry an
icon, a word, and a hover/assistive sentence, and none may be distinguished by
colour alone.

★These read files only, so they belong in `tests/unit/fork` — no schema is
touched. See CLAUDE.md.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
EN = REPO / "locales" / "en.json"
FRONTEND = REPO / "frontend"
BUTTON = FRONTEND / "components" / "KeeperButton.vue"
COMPOSABLE = FRONTEND / "composables" / "useKeeper.ts"
TOOLBAR = FRONTEND / "components" / "KnowledgeExplorer.vue"

# `hidden` is deliberately excluded: it renders nothing, so it has nothing to
# be legible with.
#
# ★★★Six, not three. `resting` used to be the FALLBACK — every situation that
# was neither running nor flagged printed "Synced", including an installation
# that had never synced anything at all, and one whose last run had failed. The
# states below are the ones that were hiding inside it, and each has to be as
# legible as the three that were always visible.
VISIBLE_STATES = ("working", "attention", "failed", "never", "stale", "resting")

# ★The label key is NOT derivable from the state name any more, and pretending
# it is would have quietly excused the one state this file most needs to watch.
# `resting` reads `labelUpToDate`; `labelResting` no longer exists.
LABEL_KEY = {
    "working": "keeper.labelWorking",
    "attention": "keeper.labelAttention",
    "failed": "keeper.labelFailed",
    "never": "keeper.labelNever",
    "stale": "keeper.labelStale",
    "resting": "keeper.labelUpToDate",
}
TIP_KEY = {
    "working": "keeper.tipWorking",
    "attention": "keeper.tipAttention",
    "failed": "keeper.tipFailed",
    "never": "keeper.tipNever",
    "stale": "keeper.tipStale",
    "resting": "keeper.tipResting",
}

KEY_RE = re.compile(r"(?<![\w.])\$?t\(\s*['\"]([a-zA-Z][\w.]*)['\"]")


def _has_key(locale: dict, dotted: str) -> bool:
    node = locale
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


@pytest.fixture(scope="module")
def button() -> str:
    return BUTTON.read_text(encoding="utf-8")


def test_every_visible_state_has_its_own_icon(button: str):
    """Shape, not just tint. Two states sharing an icon means the difference
    between "working" and "something is wrong" is a hue."""
    block = button[button.index("const icon = computed"):]
    block = block[: block.index("})")]
    icons = dict(re.findall(r"(\w+):\s*'([^']*)'", block))
    for state in VISIBLE_STATES:
        assert icons.get(state), f"{state} has no icon"
    chosen = [icons[s] for s in VISIBLE_STATES]
    assert len(set(chosen)) == len(chosen), (
        f"two states share an icon ({chosen}) — the only thing left telling "
        f"them apart is colour"
    )


def test_every_visible_state_says_something_in_words(button: str):
    """A label, not a bare dot. The count belongs on the button: "3 need you"
    is actionable where a red dot is a prompt to go and find out."""
    block = button[button.index("const label = computed"):]
    block = block[: block.index("\nconst summary")]
    for state in VISIBLE_STATES:
        assert LABEL_KEY[state] in block, (
            f"{state} has no label key — the button would render an icon alone"
        )


def test_the_state_is_also_available_to_a_screen_reader(button: str):
    """`title` covers the mouse, `aria-label` covers everyone else. A tooltip
    that only a pointer can reach is not an accessible label."""
    # The whole template is the button now that the popover is gone.
    head = button[: button.index("</template>")]
    assert ':title="summary"' in head
    assert ':aria-label="summary"' in head

    block = button[button.index("const summary = computed"):]
    block = block[: block.index("\n// ★Colour")]
    for state in VISIBLE_STATES:
        assert TIP_KEY[state] in block, (
            f"{state} has no hover sentence"
        )


def test_colour_alone_never_distinguishes_a_state(button: str):
    """The colour map is allowed to exist — it just must not be the only map.
    If icon/label/summary ever collapse to one entry, this catches it."""
    for name in ("const icon", "const label", "const summary", "const buttonClass"):
        assert name in button, f"{name} is gone — a distinguishing channel was removed"


def test_the_button_never_invents_its_own_state(button: str):
    """It reads `useKeeper`, the same feed the history screen reads. A prop or
    a local ref here is how a button ends up spinning after the sync finished."""
    assert "useKeeper()" in button
    assert "state" in button
    assert not re.search(r"defineProps<\{[^}]*\bstate\b", button, re.S), (
        "state must be derived from the feed, never passed in"
    )


def test_a_member_with_nothing_to_watch_gets_no_button():
    """An empty button is a question the member cannot answer. `hidden` is a
    state in the union precisely so this is a decision rather than an oversight.
    """
    source = COMPOSABLE.read_text(encoding="utf-8")
    assert "'hidden'" in source
    assert "agents.length === 0" in source, (
        "nothing turns the button off when the member can see no agents"
    )
    assert 'v-if="state !== \'hidden\'"' in BUTTON.read_text(encoding="utf-8")


def test_the_button_is_mounted_next_to_the_new_action():
    source = TOOLBAR.read_text(encoding="utf-8")
    assert "<KeeperButton" in source
    assert "import KeeperButton" in source
    # Left of "+ New" — the last thing read before the action.
    assert source.index("<KeeperButton") < source.index("agentsPage.new'")


def test_polling_slows_down_when_nothing_is_running():
    """A one-second poll of an endpoint that runs several joins, on a screen
    left open all day, is a self-inflicted load problem."""
    source = COMPOSABLE.read_text(encoding="utf-8")
    fast = int(re.search(r"FAST_MS = (\d+)", source).group(1))
    slow = int(re.search(r"SLOW_MS = (\d+)", source).group(1))
    assert slow > fast
    assert "working_now?.length" in source, "the cadence must follow the data"
    assert "if (inFlight) return" in source, (
        "a slow response must not queue another request behind it"
    )


def test_every_locale_key_the_button_uses_exists():
    """★vue-i18n renders the KEY ITSELF when it is missing — a member sees
    `keeper.labelWorking` on a button. Checked mechanically for that reason."""
    locale = json.loads(EN.read_text(encoding="utf-8"))
    used = set(KEY_RE.findall(BUTTON.read_text(encoding="utf-8")))
    # ★A floor, not an equality: adding a key must not fail this test, but
    # KEY_RE matching NOTHING must. `missing` is derived from `used`, so an
    # empty `used` passes silently over a button rendering raw keys — the exact
    # failure this test exists to catch. 7 keys as of 2026-08-09.
    assert len(used) >= 5, (
        f"KEY_RE found only {len(used)} locale keys in {BUTTON.name} — it used "
        "to find 7. Either the button stopped calling $t/t directly or KEY_RE "
        "no longer matches how it does; this check cannot fail until that is "
        "fixed."
    )
    missing = sorted(k for k in used if not _has_key(locale, k))
    assert not missing, f"missing from en.json: {missing}"


def test_one_click_reaches_the_history():
    """★Changed 2026-08-03. The button used to open a popover whose only real
    action was a "See all activity" link, so the history took TWO clicks and the
    first one showed a summary nobody had asked for.

    Worse, that summary duplicated the screen it stood in front of: today's
    counts, the recent list and what needs a person are all tabs on the history.
    A panel that paraphrases the thing behind it is a second place for the same
    facts to be wrong.

    So: no popover, and the button's own click opens the screen.
    """
    source = BUTTON.read_text(encoding="utf-8")
    assert "UPopover" not in source, "the intermediate menu is back"
    assert "keeper.seeAll" not in source, "the link that needed a second click is back"
    assert '@click="$emit(\'open\')"' in source, (
        "the button itself must open the history"
    )

    toolbar = TOOLBAR.read_text(encoding="utf-8")
    mount = re.search(r"<KeeperButton[^>]*>", toolbar).group(0)
    assert "@open=" in mount, "the button emits with nothing listening"
    assert "<KeeperScreen" in toolbar, "nothing on this screen to open"


# --- what "Synced" cost, pinned ----------------------------------------------


def test_the_button_never_calls_an_unsynced_install_synced(button: str):
    """★★★THE DEFECT THIS RELEASE FIXES, as a mechanical check.

    `labelResting` was the fallback. It rendered "Synced" for a brand-new
    installation that had never run anything, for an agent whose last sync had
    failed, and for one that last succeeded nine days ago — three claims that
    were not true, in the one word the member had no reason to doubt. The honest
    string existed the whole time and only ever reached the tooltip.

    Two things are required, and the second is the one that matters: the retired
    key must be gone, AND the states that were hiding behind it must each be
    reachable in the state machine. Deleting the key alone would pass a check
    that only looked for its absence.
    """
    assert "labelResting" not in button, (
        "the fallback label is back — every unflagged state will read 'Synced' "
        "again, including one that has never synced"
    )
    source = COMPOSABLE.read_text(encoding="utf-8")
    for state in ("'failed'", "'never'", "'stale'"):
        assert state in source, (
            f"{state} is not a state in useKeeper — it will fall through to "
            "`resting`, which is exactly how one label came to cover four "
            "different situations"
        )


def test_never_synced_is_decided_over_the_whole_history_not_a_window():
    """★A seven-day window cannot answer "has this ever synced".

    The overview is built from `_AGENT_HISTORY_DAYS` of runs, so `never_synced`
    was true for an agent that synced perfectly well nine days ago. That was
    harmless while it only sorted a list, and became a lie the moment a button
    started rendering the words "Never synced". The lifetime facts are computed
    separately for this reason.
    """
    service = (REPO / "backend" / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    assert "_lifetime_marks" in service
    assert '"never_synced": lifetime.get("last_run_at") is None' in service, (
        "never_synced is being decided from the windowed history again"
    )
    assert '"last_run_at"' in service, (
        "nothing tells the client when an agent last ran outside the window, so "
        "'Last synced 9 days ago' cannot be said without guessing"
    )
    composable = COMPOSABLE.read_text(encoding="utf-8")
    assert "a.last_run_at ||" in composable, (
        "the button still measures staleness from the windowed run alone"
    )


def test_staleness_is_measured_in_utc(button: str):
    """★The API serializes `datetime.utcnow()` with no 'Z'. `Date.parse` reads
    that as LOCAL time, so a threshold measured with it is wrong by the viewer's
    offset — invisible in the timezone the developer sits in, and hours out in
    Yangon. `toDate` is the shared parser that gets this right."""
    source = COMPOSABLE.read_text(encoding="utf-8")
    block = source[source.index("const state = computed"):]
    block = block[: block.index("\n  })")]
    assert "toDate(" in block, "staleness is parsed without the UTC-aware helper"
    # ★★★Comments stripped FIRST. This assertion failed on the comment directly
    # above the code it was checking — the comment says "`toDate`, not
    # `Date.parse`", and a naive substring search cannot tell an explanation of
    # a rule from a violation of it. A source-scanning test that reads prose as
    # code reports whatever the author happened to write about the subject.
    code = "\n".join(
        line for line in block.splitlines() if not line.strip().startswith("//")
    )
    assert "Date.parse" not in code, (
        "Date.parse reads a naive-UTC timestamp as local time"
    )


def test_a_running_button_shows_how_far_it_has_got(button: str):
    """"Syncing 2" and nothing else was the whole of the progress report on a
    control whose job is progress. The counters exist live in
    `connection_sync_progress`; the overview now carries them."""
    assert "runningProgress" in button
    assert "keeper.progressRatio" in button
    service = (REPO / "backend" / "app" / "services" / "keeper_service.py").read_text(encoding="utf-8")
    assert "_live_overlay" in service
    assert 'summary["result"] != "running"' in service, (
        "the overview no longer joins live progress onto a running run"
    )
