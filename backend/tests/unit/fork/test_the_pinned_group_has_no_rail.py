"""The sidebar's group headings, and the decisions that shaped them.

These are design decisions Rahul made on the mockup, not defaults. Written down
here because the next person to touch this markup will not know that the plain
version was tried first and rejected, and the obvious "improvements" are the
things that were deliberately removed.

WHAT WAS DECIDED, AND WHY

1. **No vertical rail on the Pinned group.** The first mockup held the pinned
   rows apart with a hairline down their left edge. It was cut: the heading
   already says "these rows are one set", so the line is a second device doing
   the same job — and it would be the only non-horizontal stroke in the whole
   rail. Membership is carried by the pin ON the row instead, which keeps
   working once the heading has scrolled out of sight. Separation is air.

2. **Group labels are sentence case with no tracking.** "REPORTS" is the
   section and stays uppercase with wide tracking; the time headings sit inside
   it and must read as subordinate. Two uppercase rows stacked compete for the
   same rank and neither wins.

3. **The count appears only when the group is collapsed.** Open, "2" sits
   beside two rows the eye can already count. Closed, it is the only thing that
   says what is behind the chevron.

4. **The pin toggle must not navigate.** The row is a `NuxtLink`; a pin click
   without `.stop.prevent` opens the report as well as pinning it. The user
   loses their place, which reads as the pin being broken.

★LIMIT: this reads the `.vue` file as TEXT, so it proves the markup says these
things, not that they render. Only the browser smoke suite can prove a pixel.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
LAYOUT = REPO / "frontend" / "layouts" / "default.vue"

# The block that renders one group: from the v-for over the groups to the end
# of its row list. Everything asserted below is scoped to this, so an unrelated
# border elsewhere in a 1400-line layout cannot fail the rail check.
#
# ★Anchored on the v-for, NOT on the first `group.labelKey`. The first draft of
# this test started at the label and so began INSIDE the collapse button,
# silently excluding that button's own `aria-expanded` and open tag — three
# checks failed against markup that was correct. A block regex that starts in
# the middle of the thing it is measuring fails in a way that reads like a
# product bug.
GROUP_BLOCK = re.compile(r'v-for="group in reportGroups".*?</ul>', re.S)

# The click handler on the pin control. ★The CODE name is still `star`
# (`toggleStarReport`, `is_starred`, `/reports/{id}/star`) — only the UI was
# renamed to "pin", deliberately, because renaming the field and endpoint is a
# far larger change. Matching on the word "pin" here would fail forever.
PIN_CLICK = re.compile(r"@click([.\w]*)=\"[^\"]*(?:toggleStar|togglePin)")


def _group_markup(src: str) -> str:
    """The group block, with HTML comments removed.

    ★★★Stripping comments is load-bearing, and this test proved it the hard
    way: the markup carries a comment explaining *why* the labels are not
    uppercase, and the first draft of `test_group_labels_stay_subordinate`
    matched the word "uppercase" inside that very explanation. A guard that
    reads its own documentation as evidence reports a defect that is not there
    — the mirror image of the guard that passes on a bug because the bug is
    only mentioned in a comment.
    """
    m = GROUP_BLOCK.search(src)
    assert m, (
        "could not find the group-heading block in default.vue — the sidebar "
        "list was restructured, so re-verify these decisions by hand rather "
        "than deleting this test"
    )
    return re.sub(r"<!--.*?-->", " ", m.group(0), flags=re.S)


@pytest.fixture(scope="module")
def src() -> str:
    assert LAYOUT.exists(), f"{LAYOUT} is missing"
    return LAYOUT.read_text(encoding="utf-8")


def test_the_pinned_group_has_no_vertical_rail(src):
    block = _group_markup(src)
    rails = re.findall(r"border-(?:l|s|inline-start)[-\s\"']", block)
    assert not rails, (
        f"a vertical rail came back on the group block ({rails}). The heading "
        f"already groups the rows and the pin already marks membership; a "
        f"stroke here is a third device for a job two are already doing, and "
        f"the only non-horizontal line in the rail."
    )


def test_group_labels_stay_subordinate_to_the_section_header(src):
    block = _group_markup(src)
    assert "uppercase" not in block, (
        "a group label was made uppercase. The REPORTS section header is "
        "uppercase; a second uppercase row directly beneath it competes for "
        "the same rank instead of reading as a level down."
    )
    assert "tracking-wider" not in block and "tracking-wide" not in block, (
        "a group label gained letter-spacing, which is the section header's "
        "signature in this rail"
    )
    assert "text-[11px]" in block, "group labels lost their 11px size"


def test_the_count_shows_only_when_collapsed(src):
    block = _group_markup(src)
    m = re.search(r"<span[^>]*v-if=\"([^\"]*)\"[^>]*>\s*\{\{\s*group\.items\.length\s*\}\}", block)
    assert m, (
        "the item count is rendered unconditionally, or was removed. It is "
        "meant to appear only when the group is shut — open, it labels rows "
        "the reader can already count."
    )
    condition = m.group(1)
    assert re.search(r"!\s*\w*[Oo]pen|[Cc]ollapsed", condition), (
        f"the count's condition is {condition!r}, which does not read as "
        f"'only while collapsed'"
    )


def test_the_pin_toggle_cannot_navigate(src):
    block = _group_markup(src)
    m = PIN_CLICK.search(block)
    assert m, "no pin toggle found on the row"
    modifiers = m.group(1)
    assert ".stop" in modifiers and ".prevent" in modifiers, (
        f"the pin toggle's click modifiers are {modifiers!r}. The row is a "
        f"NuxtLink, so without .stop.prevent a pin click also opens the "
        f"report — the user loses their place and the pin looks broken."
    )


def test_the_collapse_control_is_a_real_button(src):
    block = _group_markup(src)
    assert "aria-expanded" in block, (
        "the collapse control does not report its state to assistive tech"
    )
    assert re.search(r"<button[^>]*\n?[^>]*aria-expanded|aria-expanded[^>]*>", block), (
        "aria-expanded is present but not on a <button>; a div with a click "
        "handler is not keyboard reachable"
    )
