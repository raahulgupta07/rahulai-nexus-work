"""Every string the pausable-refresh work put on screen is translated.

Two screens gained user-visible copy: the Scheduled tab's always-visible
pause / edit / remove controls, and the Refresh schedule modal. Between them
that is nine new ``scheduled.*`` keys and a new ``refreshSchedule`` namespace.

Three distinct failures are asserted, because they look nothing alike:

★A MISSING key does not fail, warn, or fall back to English — vue-i18n renders
the KEY ITSELF, so a member reads ``refreshSchedule.modeOnOpenHelp`` where a
sentence should be. That has already happened twice in this codebase.

★A key added to ten files by COPYING the English value is the usual way a
locale ships untranslated: every file has the key, every presence check passes,
and the product is half English. So the value is compared against en as well.

★★★A string that never reached the locale files at all cannot be caught by
either of the above. A guard pinned to ``locales/*.json`` once passed with all
smiles while two copies of the same wrong sentence sat hardcoded in
``settings/smtp.vue`` (CLAUDE.md records it). The English copy is therefore
also asserted ABSENT from the components.

This deliberately follows the two locale guards already in this directory —
``test_every_locale_has_the_group_labels`` for the shape checks and
``test_locale_keys_for_connect_ux`` for the reference checks — rather than
inventing a third way of doing the same thing.

★``locales/en.json`` ships WITHOUT a trailing newline and must keep doing so.
Adding one is a whole-file diff on a file upstream also edits, so every future
port then conflicts on the last line.

★Read-only, no schema — ``tests/unit/fork``. See CLAUDE.md.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
LOCALES = REPO / "locales"
EN = LOCALES / "en.json"
FRONTEND = REPO / "frontend"

# The components this work touched. An explicit list, not a glob: a sweep over
# the whole frontend drags in pre-existing debt and turns a targeted guard into
# a wall of failures nobody acts on.
COMPONENTS = [
    FRONTEND / "components" / "automations" / "ScheduledTab.vue",
    FRONTEND / "components" / "RefreshScheduleModal.vue",
]

NEW_SCHEDULED_KEYS = (
    "editTask",
    "editSchedule",
    "removeSchedule",
    "pauseRefresh",
    "resumeRefresh",
    "pausedLabel",
    "willNotRun",
    "removeScheduleFailed",
    "removeScheduleConfirm",
)

NEW_REFRESH_KEYS = (
    "title",
    "refreshData",
    "modeOff",
    "modeOffHelp",
    "modeRecurring",
    "modeRecurringHelp",
    "modeOnOpen",
    "modeOnOpenHelp",
    "runs",
    "at",
    "emailTo",
    "addSomeone",
    "active",
    "activeHelp",
    "nextRun",
    "remove",
    "removeConfirm",
    "saveFailed",
)

NEW_KEYS = tuple(
    ["scheduled.%s" % k for k in NEW_SCHEDULED_KEYS]
    + ["refreshSchedule.%s" % k for k in NEW_REFRESH_KEYS]
)

# `$t('a.b')` and `t('a.b')`, single or double quoted.
KEY_RE = re.compile(r"(?<![\w.])\$?t\(\s*['\"]([a-zA-Z][\w.]*)['\"]")


def locale_files():
    """Every shipped locale. ``.bak-*`` snapshots are not ``*.json`` and so are
    out. Enumerated from the directory, never hardcoded — a locale added later
    is covered the day it lands."""
    files = sorted(LOCALES.glob("*.json"))
    assert files, "no locale files under %s" % LOCALES
    assert EN in files, "en.json is missing"
    return files


def lookup(data, dotted):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def english():
    return json.loads(EN.read_text(encoding="utf-8"))


def placeholders(value):
    return set(re.findall(r"\{(\w+)\}", value or ""))


def test_every_locale_defines_every_new_key():
    """A miss renders the raw dotted path to the user."""
    missing = []
    for path in locale_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in NEW_KEYS:
            value = lookup(data, key)
            if not isinstance(value, str) or not value.strip():
                missing.append("%s:%s" % (path.name, key))
    assert missing == [], (
        "these keys are missing or empty — they render as the raw key path on "
        "screen: %s" % missing
    )


def test_no_locale_ships_the_english_string():
    """★The check that matters. Copying the English value into ten files
    satisfies every presence test and ships a half-English product."""
    en = english()
    untranslated = []
    for path in locale_files():
        if path.name == "en.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in NEW_KEYS:
            value = lookup(data, key)
            if isinstance(value, str) and value == lookup(en, key):
                untranslated.append("%s:%s=%r" % (path.name, key, value))
    assert untranslated == [], (
        "these carry the English string verbatim, so that language shows "
        "English in an otherwise translated screen: %s" % untranslated
    )


def test_placeholders_survive_translation():
    """A translator who drops ``{time}`` produces a sentence with a hole; one
    who invents ``{n}`` produces a literal ``{n}`` on screen. Compared against
    en rather than a hardcoded list, so a placeholder added later is covered
    without editing this file."""
    en = english()
    wrong = []
    for path in locale_files():
        if path.name == "en.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in NEW_KEYS:
            expected = placeholders(lookup(en, key))
            found = placeholders(lookup(data, key))
            if found != expected:
                wrong.append("%s:%s has %s, en has %s" % (path.name, key, sorted(found), sorted(expected)))
    assert wrong == [], "placeholder mismatch: %s" % wrong


def test_english_still_has_no_trailing_newline():
    """★en.json ships without one while the other nine locales have one.
    Adding one is a whole-file diff on a file upstream also edits, and every
    future port then conflicts on the last line."""
    raw = EN.read_bytes()
    assert raw and not raw.endswith(b"\n"), (
        "locales/en.json gained a trailing newline — that is a whole-file diff "
        "against upstream and every future port of this file will conflict"
    )


def test_the_other_locales_keep_the_endings_they_had():
    """The mirror image. en is the odd one out; the rest all end with a
    newline, and quietly stripping one is the same whole-file diff in the other
    direction."""
    wrong = [
        path.name for path in locale_files()
        if path.name != "en.json" and not path.read_bytes().endswith(b"\n")
    ]
    assert wrong == [], "these lost their trailing newline: %s" % wrong


@pytest.mark.parametrize("path", COMPONENTS, ids=lambda p: p.name)
def test_every_key_a_component_asks_for_exists(path):
    """★vue-i18n renders the KEY on a miss, so this is checked mechanically
    rather than by reading. Only en is asserted: the other nine fall back to
    English for anything they do not define."""
    assert path.exists(), path
    en = english()
    referenced = sorted(set(KEY_RE.findall(path.read_text(encoding="utf-8"))))
    assert referenced, "%s references no locale keys at all" % path.name
    missing = [k for k in referenced if not isinstance(lookup(en, k), str)]
    assert missing == [], "%s renders raw keys: %s" % (path.name, missing)


@pytest.mark.parametrize("path", COMPONENTS, ids=lambda p: p.name)
def test_no_component_hardcodes_the_english_copy(path):
    """★★★The failure neither locale check above can see.

    A guard pinned to ``locales/*.json`` is perfectly green while the sentence
    the user actually reads sits spelled out in the template. Every locale file
    can be complete and correct and the screen still be English-only.

    Only the sentences are checked, not single words: ``Off`` or ``at`` appear
    in class names and attribute values for entirely innocent reasons, and a
    guard that fires on those gets muted.
    """
    assert path.exists(), path
    text = path.read_text(encoding="utf-8")
    en = english()
    leaked = []
    for key in NEW_KEYS:
        value = lookup(en, key)
        if not isinstance(value, str) or len(value) < 12:
            continue
        if value in text:
            leaked.append("%s: %r" % (key, value))
    assert leaked == [], (
        "%s spells out copy that already has a locale key — that text cannot "
        "be translated: %s" % (path.name, leaked)
    )


@pytest.mark.parametrize("path", COMPONENTS, ids=lambda p: p.name)
def test_no_dead_translate_fallbacks(path):
    """``$t('x') || 'Fallback'`` never falls back — $t returns the key on a
    miss and a non-empty string is truthy. The fallback is dead code hiding a
    missing key behind what looks like a safety net.

    Note the direction: ``value || $t('key')`` is the OPPOSITE and fine — a real
    value with a translated default.
    """
    assert path.exists(), path
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    text = re.sub(r"^(\s*)//.*$", r"\1", text, flags=re.M)
    bad = [
        i for i, line in enumerate(text.splitlines(), 1)
        if re.search(r"\$?t\([^)]*\)\s*\|\|", line)
    ]
    assert bad == [], "dead $t fallbacks in %s at lines %s" % (path.name, bad)


def test_the_copy_says_what_pausing_actually_does():
    """★The sentence is the feature. A user who reads "Paused" with no further
    explanation has no way to know whether the configured time survived — and
    the whole reason this column exists is that it did not use to. The English
    copy has to say the schedule is kept.

    ★And it must not say "cron". These screens are for business staff; the word
    names an implementation detail and answers a question nobody asked.
    """
    en = english()
    kept = " ".join([
        en["scheduled"]["willNotRun"],
        en["refreshSchedule"]["activeHelp"],
    ]).lower()
    assert "saved" in kept, (
        "nothing tells the user that pausing keeps the configured time, which "
        "is the one thing that changed"
    )

    for key in NEW_KEYS:
        value = lookup(en, key)
        assert "cron" not in value.lower(), "%s exposes system vocabulary: %r" % (key, value)


def test_the_hardcoded_copy_check_can_actually_fail():
    """★★★A guard that has never been shown to fail is a comment with a test's
    salary. This runs the same comparison against a template that DOES spell
    the sentence out, so the check above cannot silently stop working."""
    en = english()
    sentence = en["refreshSchedule"]["modeOffHelp"]
    fake = '<template><p class="text-xs">%s</p></template>' % sentence
    leaked = [
        key for key in NEW_KEYS
        if isinstance(lookup(en, key), str)
        and len(lookup(en, key)) >= 12
        and lookup(en, key) in fake
    ]
    assert leaked == ["refreshSchedule.modeOffHelp"], (
        "the hardcoded-copy check no longer detects hardcoded copy: %s" % leaked
    )
