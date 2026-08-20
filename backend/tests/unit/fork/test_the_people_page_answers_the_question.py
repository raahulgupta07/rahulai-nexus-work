"""People & Identities exists to answer "how does this person sign in".

WHAT THIS COST
--------------
The page rendered every person as a card carrying FIVE provider badges — one
per possible sign-in method — with the unlinked ones dimmed to `opacity-25
grayscale`. On a real eight-person org that is **32 badges meaning "no" against
8 meaning "yes"**, and absence was rendered as a faint picture of a thing rather
than as nothing at all. The only key to it was a legend printed AFTER the last
card, so the reader had to scroll past everything to learn what they had been
looking at, then scroll back.

Four of eight people have no display name, so the row printed the same email
address as both its title and its subtitle.

Clicking a row opened an accordion that pushed every row below it down, so
comparing two people meant losing the position of both. And there was no way to
ask the question the screen exists for — "who signs in with a password?" —
except by eye, across dimmed badges, one row at a time.

WHAT IS PINNED HERE
-------------------
The CONTRACT, not the styling: a table, a summary strip, a filter, a non-modal
side panel with an addressable selection, and a locale key for every string in
all ten languages. Nothing here asserts a colour, a spacing or a class name —
those are the page author's to change.

★vue-i18n renders the KEY ITSELF when it is missing, so a locale gap is not a
cosmetic defect: an admin sees `settings.people.summaryPassword` on an
administration screen. Checked mechanically for that reason, in every language.

★These read files only, so they belong in `tests/unit/fork` — no schema is
touched. See CLAUDE.md.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
LOCALES = REPO / "locales"
EN = LOCALES / "en.json"
PAGE = REPO / "frontend" / "pages" / "settings" / "people.vue"

LANGUAGES = ("en", "de", "es", "fr", "it", "pt", "ru", "sv", "ar", "he")

# The keys the rewritten page renders. A missing one is a raw key on screen.
REQUIRED_KEYS = tuple(
    "settings.people." + suffix
    for suffix in (
        "summaryPeople",
        "summaryPassword",
        "summarySso",
        "summaryDirectory",
        "summaryAdmins",
        "filter.all",
        "filter.password",
        "filter.sso",
        "filter.directory",
        "filter.multiple",
        "filter.admins",
        "col.person",
        "col.methods",
        "col.role",
        "col.joined",
        "method.password",
        "mergeFootnote",
        "panel.identities",
        "panel.oneIdentity",
        "panel.sameEmail",
        "panel.close",
        "panel.open",
    )
)

# The legend's keys. Gone from the page AND gone from en.json — a retired key
# left behind is a string the next author re-renders by accident.
RETIRED_KEYS = ("notLinked", "mergeKey", "clickHint", "mergedSummary")

KEY_RE = re.compile(r"(?<![\w.])\$?t\(\s*['\"]([a-zA-Z][\w.]*)['\"]")

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _code(source):
    """★★★Source with its prose removed, for every NEGATIVE assertion below.

    A source-scanning test matches its own comments. `assert "opacity-25" not in
    source` fails on a comment reading "no more opacity-25 dimming" — the test
    reports whatever the author happened to write ABOUT the rule as a violation
    of it. A `.vue` file carries both `<!-- -->` (template) and `//` (script)
    comments, so both are stripped. Third time in this repo; see CLAUDE.md.
    """
    stripped = _BLOCK_COMMENT.sub(" ", _HTML_COMMENT.sub(" ", source))
    return "\n".join(
        line for line in stripped.splitlines() if not line.strip().startswith("//")
    )


def _has_key(locale, dotted):
    node = locale
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


@pytest.fixture(scope="module")
def page():
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(page):
    return _code(page)


@pytest.fixture(scope="module")
def en():
    return json.loads(EN.read_text(encoding="utf-8"))


# --- absence is rendered as nothing, not as a faint picture of a thing --------


def test_absence_is_not_drawn(code):
    """Thirty-two dimmed badges meaning "no" is thirty-two things to read
    before the eight meaning "yes" can be found. A method a person does not use
    should occupy no space at all."""
    for token in ("opacity-25", "grayscale"):
        assert token not in code, (
            "{} is back — unlinked sign-in methods are being drawn as dimmed "
            "badges again, so most of what is on screen means 'no'".format(token)
        )


def test_the_legend_is_gone(code, en):
    """A key printed after the last card is a key nobody reads in time. If the
    rendering needs no decoding, the legend has nothing to explain."""
    for suffix in RETIRED_KEYS:
        assert "settings.people." + suffix not in code, (
            "the page still renders settings.people.{} — the legend is back".format(suffix)
        )
        assert not _has_key(en, "settings.people." + suffix), (
            "settings.people.{} is still in en.json — a retired string left in "
            "place is one the next author re-renders by accident".format(suffix)
        )


# --- the locale contract, in all ten languages -------------------------------


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_language_carries_every_key(lang):
    """★A missing key is not a fallback to English — vue-i18n renders the KEY,
    so a French admin reads `settings.people.summaryAdmins` in a table cell."""
    path = LOCALES / (lang + ".json")
    locale = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if not _has_key(locale, k)]
    assert not missing, "{}.json is missing {}".format(lang, missing)


def test_every_key_the_page_uses_exists(page, en):
    """★Guard the guard. `missing` is derived from `used`, so a KEY_RE that
    stops matching passes silently over a page rendering raw keys — the exact
    failure mode documented in test_the_sync_button_reads_at_a_glance.py. The
    floor makes that impossible to miss."""
    used = set(KEY_RE.findall(page))
    assert len(used) >= 15, (
        "KEY_RE found only {} locale keys in people.vue. Either the page stopped "
        "calling $t/t directly or the regex no longer matches how it does; until "
        "that is fixed this check cannot fail.".format(len(used))
    )
    missing = sorted(k for k in used if not _has_key(en, k))
    assert not missing, "missing from en.json: {}".format(missing)


# --- the shape of the answer --------------------------------------------------


def test_the_people_are_in_a_table(code):
    """Eight cards cannot be compared; eight rows can. Columns are what make
    "who signs in with a password" a scan rather than a hunt."""
    for tag in ("<table", "<thead", "<tbody"):
        assert tag in code, (
            "{} is missing — the roster is not a table, so nothing lines "
            "up".format(tag)
        )


def test_the_summary_and_the_filter_exist(code):
    """The counts at the top ARE the filter: read "3 password", click it, see
    those three. Without the filter the strip is trivia; without the strip the
    filter is a control with no reason to be pressed."""
    assert "summary" in code, "the counts strip is gone"
    assert "activeFilter" in code, (
        "there is no way to filter by sign-in method — the security question "
        "this page exists to answer is back to being answered by eye"
    )
    assert "signInMethods" in code, (
        "nothing derives which methods a person actually uses"
    )


# --- the detail does not move the thing you were reading ----------------------


def test_the_detail_is_a_side_panel_not_a_modal_or_a_popover(code):
    """An accordion pushed every row below it down; a modal hides the roster
    you opened it from. A panel beside the table leaves both readable."""
    assert "UModal" not in code, (
        "the detail is a modal again — it covers the roster it was opened from"
    )
    assert "UPopover" not in code, (
        "the detail is a popover again — it closes on the next click, so two "
        "people cannot be compared"
    )
    assert "<aside" in code, "no side panel element — the detail has nowhere to go"


def test_the_panel_can_be_named_and_dismissed(code):
    """A region a screen reader cannot name is a region nobody can leave. The
    same is true of a panel with no Escape."""
    assert "aria-label" in code, "the panel is unnamed to assistive technology"
    assert re.search(r"[Ee]scape", code), (
        "nothing closes the panel with Escape"
    )


def test_the_selected_person_is_addressable(code):
    """The URL is what makes a row shareable — "look at this person" is a link,
    not a set of directions. It is also what survives a reload."""
    assert "route.query" in code, "the selection is not read back from the URL"
    # ★A bare `"person" in code` is vacuous on a page about people — every type
    # annotation in the file contains the word. The READ and the WRITE of the
    # query key are asserted separately instead.
    assert "route.query.person" in code, (
        "nothing reads the selected person back out of the URL, so a reload or "
        "a shared link loses it"
    )
    assert re.search(r"\{\s*person:", code), (
        "nothing writes the selection into the route query — the panel opens "
        "without the URL following it"
    )
    for name in ("selectedId", "openPerson", "closePanel"):
        assert name in code, "{} is gone — the selection has no owner".format(name)


def test_a_person_with_nothing_to_show_gets_no_disclosure(code):
    """★`hasDetail` exists so a person with one identity and no groups is not
    offered a control that opens an empty panel. Defining it and never using it
    in the template is the whole rule as dead code — so the reference in the
    markup is what is asserted, not the declaration."""
    assert "hasDetail" in code, "the rule is gone entirely"
    template = code[: code.index("</template>")]
    assert "hasDetail" in template, (
        "hasDetail is defined but never read in the template — every person "
        "gets a disclosure control again, including those with nothing behind it"
    )


# --- and it is still an administration screen ---------------------------------


def test_the_page_still_gates_on_manage_settings(code):
    """This lists every person's email address, role and linked identities. A
    weakened permission here is a data leak, not a UX decision."""
    assert re.search(r"permissions:\s*\[\s*['\"]manage_settings['\"]\s*\]", code), (
        "the People page no longer requires manage_settings — every member can "
        "now read the organization's full identity roster"
    )
