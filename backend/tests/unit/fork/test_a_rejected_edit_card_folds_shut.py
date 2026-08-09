"""A rejected edit card folds shut, and no card renders an unbounded document.

An `edit_instruction` call the backend refused changed nothing: there is no diff
to read and no action to take, only the reason — which the card's header already
carries. It used to open expanded, showing an empty bordered box (the wrapper
rendered whenever there was no diff, while its only child was `v-if="displayText"`
and a rejection has no `new_text`), and a wall of red text. That red text is
written for the MODEL: it restates how to retry and then quotes the entire
current instruction verbatim so the next call can anchor on it. Printed as-is, a
one-line "your anchor wasn't unique" became the whole instruction shouted back —
above a card that renders the instruction anyway.

Separately, the resolved branches were the only ones with no height bound, so a
transcript of edits was a transcript of whole documents. `max-h-80` matches the
pending review panel's compact mode on purpose: resolving a suggestion swaps this
body in for that one, and any other number makes the card jump height at that
moment. Clamped, never truncated — a diff is only meaningful whole, so the rest
scrolls.

★`userToggled` is the part worth pinning. A card that fails mid-stream collapses
when the verdict lands, but only if the reader has not opened it themselves;
without that flag the collapse yanks the card shut under someone reading it.

MEASURED, guard logic run against `git show HEAD:<path>` and the working tree:

    test_an_unsuccessful_card_collapses            HEAD fail -> now pass
    test_a_reader_who_opened_it_keeps_it_open      HEAD fail -> now pass
    test_the_echoed_instruction_is_stripped        HEAD fail -> now pass
    test_the_empty_card_wrapper_requires_its_text  HEAD fail -> now pass
    test_every_resolved_body_is_clamped            HEAD 0 of 2 -> now 2 of 2

Upstream: 8ff7f32f and 4011e3ec.
"""

from __future__ import annotations

import re

from vue_source import read_source

CARD = "components/tools/EditInstructionTool.vue"


def test_an_unsuccessful_card_collapses():
    src = read_source(CARD)
    assert re.search(r"const isUnsuccessful = computed\(", src), (
        "no isUnsuccessful computed: a rejected or errored card has no diff and "
        "no action, and must open collapsed."
    )
    watcher = re.search(r"watch\(isUnsuccessful,.*?\{ immediate: true \}\)", src, re.DOTALL)
    assert watcher, (
        "isUnsuccessful must be watched with `immediate: true` — a card that "
        "fails mid-stream has to collapse when the verdict lands, not only on "
        "a later mount."
    )
    assert "isExpanded.value = false" in watcher.group(0)


def test_a_reader_who_opened_it_keeps_it_open():
    src = read_source(CARD)
    assert re.search(r"const userToggled = ref\(false\)", src), "userToggled not declared"
    watcher = re.search(r"watch\(isUnsuccessful,.*?\{ immediate: true \}\)", src, re.DOTALL)
    assert watcher and "userToggled" in watcher.group(0), (
        "the collapse does not consult userToggled, so it yanks the card shut "
        "under a reader who deliberately opened it."
    )


def test_the_echoed_instruction_is_stripped():
    src = read_source(CARD)
    assert "stripEchoedText" in src, (
        "the rejection message is written for the model and quotes the whole "
        "current instruction after 'Current instruction text:'. It must be cut "
        "before being shown to a person."
    )
    fn = re.search(r"const stripEchoedText = .*?\n\}", src, re.DOTALL)
    assert fn and "Current instruction text" in fn.group(0)
    assert re.search(r"errorMessage = computed", src)
    err = src[src.index("const errorMessage = computed") :][:600]
    assert "stripEchoedText(" in err, (
        "errorMessage returns the raw message; the stripper exists but nothing "
        "calls it."
    )


def test_the_empty_card_wrapper_requires_its_text():
    """The wrapper drew a bordered box whenever there was no diff, while its
    only child was conditional on text a rejection does not have."""
    src = read_source(CARD)
    # ★Anchor on the OUTERMOST element of the card being measured. Two sibling
    # `v-else-if="!turnActive && !awaitingFinal …"` divs exist — the diff card
    # and this one — and matching the first silently measures the wrong wrapper.
    # Locate the body it wraps, then take the div that opens it.
    body = src.index('class="instruction-content')
    opening = src.rindex("<div v-else-if=", 0, body)
    cond = src[opening : src.index(">", opening)]
    assert "!turnActive" in cond, "did not land on the instruction card wrapper"
    assert "displayText" in cond, (
        "the instruction card renders without requiring displayText, so a "
        "rejected edit draws an empty bordered box."
    )
    assert "isUnsuccessful" in cond, (
        "the instruction card renders for an unsuccessful edit, shouting the "
        "instruction back beside the rejection."
    )


def test_every_resolved_body_is_clamped():
    """Both resolved branches — the tracked-changes view and the plain
    instruction body. Clamping one leaves the other unbounded."""
    src = read_source(CARD)

    diff_body = re.search(r'<div class="px-3 py-2 bg-white[^"]*"', src)
    assert diff_body, "could not find the resolved tracked-changes body"
    assert "max-h-80" in diff_body.group(0) and "overflow-y-auto" in diff_body.group(0), (
        "the resolved diff body has no height bound, so the card jumps height "
        "the moment a suggestion resolves (the pending panel's compact mode is "
        "px-3 py-2 max-h-80)."
    )

    text_body = re.search(r'class="instruction-content text-\[12px\][^"]*"', src)
    assert text_body, "could not find the instruction-content body"
    assert "max-h-80" in text_body.group(0) and "overflow-y-auto" in text_body.group(0), (
        "the resolved instruction body has no height bound — a transcript of "
        "edits becomes a transcript of whole documents."
    )
