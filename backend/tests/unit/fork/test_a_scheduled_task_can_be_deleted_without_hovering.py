"""The delete control on a scheduled task must not be hidden until hover.

The reported defect. Every row in Automations → Scheduled carried its delete
button as::

    class="… opacity-0 group-hover:opacity-100 focus:opacity-100 …"

which is invisible on a desktop until the pointer happens to cross the row, and
on a touch device is not merely hard to find but **unreachable**: there is no
hover, the button never becomes visible, and there was no way at all to delete a
scheduled task from a phone or tablet. The row looked complete. Nothing errored.

★A destructive control is the worst possible thing to hide this way, and the
reason is not aesthetic: a control the user cannot find is indistinguishable
from a feature that does not exist, so the support question is "why can't I
delete this?" rather than "where is the button?". The sibling Triggers tab had
always got this right, which is how the difference went unnoticed for so long.

★★★The comment stripper is load-bearing, and it is the trap this repo has
already fallen into once. The fix left a warning comment in the template that
quotes the broken pattern **verbatim**, so a scan over the raw text fails
against its own documentation — and a guard that fires on prose gets muted, and
a muted guard protects nothing. See CLAUDE.md, "Prove a guard test fails on the
bug before believing it".

★★★And the other half of that same trap: this guard must NOT strip string
literals. The class list IS a string literal. A stripper that removes quoted
text can never match real markup and can never fail at all.

★Read-only, no schema — ``tests/unit/fork``. See CLAUDE.md.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
SCHEDULED_TAB = REPO / "frontend" / "components" / "automations" / "ScheduledTab.vue"

HIDDEN = "opacity-0"
REVEALED_ON_HOVER = "group-hover:opacity-100"

# A control is destructive if it deletes or removes something. Matched against
# the testid and the click handler, because either alone can be absent.
DESTRUCTIVE = re.compile(r"delete|remove|destroy", re.I)


def strip_comments(text: str) -> str:
    """Blank out comments, preserving line numbering. Literals are untouched.

    Line numbering is preserved so a failure can name the line a human will see
    in their editor; a stripper that collapses lines reports offsets nobody can
    act on.
    """
    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    # Only a `//` that opens the line. Anywhere else it is far more likely to be
    # a URL or part of a string than a comment, and removing the rest of the
    # line would silently delete real markup.
    return re.sub(r"^(\s*)//.*$", r"\1", text, flags=re.M)


def open_tags(text: str, name: str):
    """Every ``<name …>`` opening tag as (line number, tag source).

    Scanned with quote awareness rather than by regex: these attributes span
    many lines and contain `>` inside Vue expressions, so `<button[^>]*>` stops
    in the wrong place and reads half a tag.
    """
    tags = []
    for match in re.finditer(r"<%s\b" % re.escape(name), text):
        i = match.start()
        quote = None
        j = i
        while j < len(text):
            c = text[j]
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == ">":
                break
            j += 1
        tags.append((text.count("\n", 0, i) + 1, text[i:j + 1]))
    return tags


def attributes(tag: str) -> dict:
    """Attribute name -> value, for the double-quoted attributes Vue uses."""
    return {
        name: value
        for name, value in re.findall(r"([:@\w][\w.\-]*)\s*=\s*\"(.*?)\"", tag, flags=re.S)
    }


def class_text(attrs: dict) -> str:
    """Everything that can end up in the element's class list."""
    return " ".join(v for k, v in attrs.items() if k in ("class", ":class"))


def is_destructive(attrs: dict) -> bool:
    probe = " ".join(
        v for k, v in attrs.items()
        if k in ("data-testid", ":data-testid", "@click", "@click.stop", "@click.stop.prevent")
    )
    return bool(DESTRUCTIVE.search(probe))


def hover_hidden_controls(text: str, destructive_only: bool = True):
    """(line, testid) for every control revealed only by hover."""
    stripped = strip_comments(text)
    found = []
    for tag_name in ("button", "a", "UButton"):
        for line, tag in open_tags(stripped, tag_name):
            attrs = attributes(tag)
            if destructive_only and not is_destructive(attrs):
                continue
            classes = class_text(attrs)
            if HIDDEN in classes and REVEALED_ON_HOVER in classes:
                found.append((line, attrs.get(":data-testid") or attrs.get("data-testid") or "?"))
    return found


def destructive_controls(text: str):
    stripped = strip_comments(text)
    out = []
    for tag_name in ("button", "a", "UButton"):
        for line, tag in open_tags(stripped, tag_name):
            attrs = attributes(tag)
            if is_destructive(attrs):
                out.append((line, attrs.get(":data-testid") or attrs.get("data-testid") or "?"))
    return out


@pytest.fixture(scope="module")
def tab():
    assert SCHEDULED_TAB.exists(), SCHEDULED_TAB
    return SCHEDULED_TAB.read_text(encoding="utf-8")


def test_no_destructive_control_is_revealed_only_by_hover(tab):
    """The reported defect itself."""
    hidden = hover_hidden_controls(tab)
    assert hidden == [], (
        "these delete/remove controls are invisible until the pointer crosses "
        "the row, which on a touch device means there is no way to reach them "
        f"at all: {hidden}"
    )


def test_the_destructive_controls_still_exist(tab):
    """★★★The positive control, and it is not optional.

    "No hover-hidden delete button" is satisfied perfectly by having no delete
    button. Every absence assertion in this repo has to be paired with a
    presence assertion over the SAME locator family, or deleting the feature
    turns the guard green — that mistake has already shipped here once, in
    ``owns-column.spec.ts``.
    """
    controls = destructive_controls(tab)
    testids = {testid for _, testid in controls}
    assert any("task-delete" in t for t in testids), (
        f"the scheduled-prompt delete control is gone; found {sorted(testids)}"
    )
    assert any("refresh-remove" in t for t in testids), (
        f"the report-refresh remove control is gone; found {sorted(testids)}"
    )


def test_nothing_at_all_in_this_file_pairs_those_two_classes(tab):
    """★Broader than the check above and deliberately so.

    The pause toggle and the edit pencil are not destructive, but a row whose
    controls appear one at a time on hover is the same defect wearing a
    different hat, and this file is small enough that a blanket rule costs
    nothing. Comments are stripped first — the fix's own warning comment quotes
    the pattern.
    """
    stripped = strip_comments(tab)
    offenders = [
        i for i, line in enumerate(stripped.splitlines(), 1)
        if HIDDEN in line and REVEALED_ON_HOVER in line
    ]
    assert offenders == [], f"hover-only reveal at lines {offenders}"


PRE_FIX_MARKUP = '''
<template>
  <div class="group flex items-start justify-between gap-3">
    <UTooltip :text="$t('scheduled.delete')">
      <button
        @click.stop="deleteTask(task)"
        :disabled="deletingId === task.id"
        class="p-1 rounded text-gray-300 hover:text-red-600 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all disabled:opacity-50"
        :data-testid="`task-delete-${task.id}`"
      >
        <UIcon name="heroicons-trash" class="w-3.5 h-3.5" />
      </button>
    </UTooltip>
  </div>
</template>
'''

COMMENT_ONLY_MARKUP = '''
<template>
  <!-- No `opacity-0 group-hover:opacity-100` here. It was invisible until
       hover, which on a touch device means unreachable. -->
  <button
    @click.stop="deleteTask(task)"
    class="p-1 rounded text-gray-300 hover:text-red-600 transition-colors"
    :data-testid="`task-delete-${task.id}`"
  >
    <UIcon name="heroicons-trash" class="w-3.5 h-3.5" />
  </button>
</template>
'''


def test_the_pre_fix_markup_is_still_detected():
    """★★★The red proof, carried in the test rather than done once at a shell
    prompt. A proof performed by hand rots into a comment; one that runs on
    every suite cannot."""
    hidden = hover_hidden_controls(PRE_FIX_MARKUP)
    assert len(hidden) == 1, f"the checker no longer detects the defect: {hidden}"
    assert "task-delete" in hidden[0][1]


def test_the_stripper_does_not_fire_on_the_warning_comment():
    """★The trap the last three worthless guards in this repo fell into. The
    fix documents the broken pattern by quoting it, so a scan over raw text
    fails against its own explanation and gets muted."""
    assert hover_hidden_controls(COMMENT_ONLY_MARKUP) == []
    assert HIDDEN in COMMENT_ONLY_MARKUP, "fixture no longer exercises the trap"


def test_the_stripper_keeps_string_literals():
    """★The OTHER half of that trap: stripping quoted text instead of comments
    makes the scan unable to match real markup, so it can never fail at all.
    The class list is a string literal — it has to survive."""
    stripped = strip_comments(PRE_FIX_MARKUP)
    assert "group-hover:opacity-100" in stripped
    assert "$t('scheduled.delete')" in stripped


def test_a_hover_hidden_control_is_found_even_across_line_breaks():
    """The two classes sit in one attribute that Vue templates wrap freely; a
    line-oriented scan alone would miss them once Prettier reflows the file."""
    wrapped = '''
<button
  @click="removeRefresh(rf)"
  class="p-1 rounded
         opacity-0
         group-hover:opacity-100"
  data-testid="refresh-remove-1"
>x</button>
'''
    assert len(hover_hidden_controls(wrapped)) == 1
