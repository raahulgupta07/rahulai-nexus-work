"""A specialty layout has to earn its place in the deck.

WHY THIS FILE EXISTS
--------------------
Decks were arriving with layouts nothing in the request called for — a hero
number on a six-slide status review, a team slide on a launch deck, chapter
dividers carving a short deck into signposts. Showcase bloat: the model had
been shown that these layouts exist and reached for them because they look
like craft, not because this deck needed them.

The fix is four rules in the slides prompt, ported from StackBlitz's MIT
``bolt-slides`` agent skill:

  * a specialty layout is used only when its entry condition holds, and the
    model has to be able to say in ONE sentence why it serves THIS deck;
  * a slide with no side visual is centred, because left-anchored text alone
    on a 13.33in slide is a defect and not a style;
  * the design system is NAMED in the generated code, so a deck built in the
    default by accident can be told apart from one built in it on purpose.

HOW THIS IS MEASURED, AND WHY
-----------------------------
Every assertion below runs against the STRING ``_build_slides_prompt`` returns,
not against the text of ``create_artifact.py``. That is deliberate twice over:

  1. It is the thing the model actually reads. A rule present in the file but
     sitting inside a branch that did not run is not a rule.
  2. ★A source-scanning test in this repo has matched its own explanation at
     least four times — the product's comments and docstrings quote the broken
     form, so a scan of file text finds the words it was looking for in a
     comment ABOUT them. Reading the return value removes that whole class of
     mistake; there is nothing here to strip.

★AND THE SCOPE IS THE POINT, NOT A DETAIL. ``"Name the design system"`` occurs
TWICE in ``create_artifact.py``: once in a comment far outside this method
(``:3253``, pre-existing, about the sentence the agent reads) and once as the
new rule inside ``_build_slides_prompt``. A whole-file grep for that marker
therefore passes with the new rule deleted — an assertion that cannot fail.
``test_a_whole_file_grep_for_the_marker_would_be_vacuous`` pins that trap so
nobody "simplifies" this file into one.

★RED PROOF. Presence assertions are cheap and can rot into decoration, so
``test_the_pre_change_prompt_is_still_detected`` reconstructs the prompt as it
was BEFORE these rules landed — the rule block cut out — and requires the same
checker to reject it. If that reconstruction ever passes, the checker has
stopped being able to say no and everything else in this file is worthless.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.ai.tools.implementations import create_artifact as ca
from app.ai.tools.implementations.create_artifact import CreateArtifactTool

# The four rules, verbatim. These strings are the contract: they are what the
# model reads, so a reword is a product decision and must break this file.
LAYOUT_DISCIPLINE_HEADING = "Layout discipline"
ONE_SENTENCE_TEST = (
    "If you cannot say in one sentence why this layout serves THIS deck, cut it."
)
NO_SIDE_VISUAL_RULE = "no side visual"
NAME_THE_SYSTEM_RULE = "Name the design system"

REQUIRED_RULES = (
    LAYOUT_DISCIPLINE_HEADING,
    ONE_SENTENCE_TEST,
    NO_SIDE_VISUAL_RULE,
    NAME_THE_SYSTEM_RULE,
)

# `_build_slides_prompt` never touches `self` (verified: the method body
# contains no `self.` reference at all), so it can be called as a plain
# function with a stand-in. This keeps the test a pure call — no tool
# construction, no database, no session — which is what `tests/unit/fork`
# requires.
_build = CreateArtifactTool.__dict__["_build_slides_prompt"]


def build_prompt(**overrides) -> str:
    """The prompt a real deck request produces."""
    kwargs = dict(
        user_prompt="quarterly revenue review for the board",
        title="Q3 revenue",
        viz_profiles=[],
        instructions_context="",
        report_title=None,
        allow_llm_see_data=False,
    )
    kwargs.update(overrides)
    return _build(object(), **kwargs)


def states_the_layout_rules(prompt: str) -> bool:
    """True when the prompt carries all four rules.

    One checker, used by every case below AND by the red proof, so the thing
    that reports green is the same thing that has to be able to report red.
    """
    return all(rule in prompt for rule in REQUIRED_RULES)


@pytest.fixture(scope="module")
def prompt() -> str:
    return build_prompt()


# ═══════════════════════════════════════════════════════════════════════════
# The rules reach the model
# ═══════════════════════════════════════════════════════════════════════════


def test_the_model_is_told_a_specialty_layout_must_earn_its_place(prompt):
    """The block that names hero/team/pricing/divider/comparison is present."""
    assert LAYOUT_DISCIPLINE_HEADING in prompt


def test_the_model_is_given_the_one_sentence_test_for_cutting_a_layout(prompt):
    """A layout it cannot justify in one sentence is cut.

    This sentence is the whole rule — the list of layouts above it is
    illustrative, this is the part that generalises to a layout nobody has
    thought of yet.
    """
    assert ONE_SENTENCE_TEST in prompt


def test_the_model_is_told_a_slide_with_nothing_beside_it_is_centred(prompt):
    """No image, no chart, no panel means no side visual — so centre it."""
    assert NO_SIDE_VISUAL_RULE in prompt


def test_the_model_is_asked_to_name_the_design_system_in_the_code(prompt):
    """The generated code has to open by naming the system it is built in.

    An unnamed system is exactly the state the theme work exists to end: a
    deck built in the default by accident, indistinguishable from a deck built
    in the default on purpose.
    """
    assert NAME_THE_SYSTEM_RULE in prompt


def test_all_four_rules_arrive_together(prompt):
    """The positive control for the checker every other case leans on."""
    assert states_the_layout_rules(prompt)


# ═══════════════════════════════════════════════════════════════════════════
# The rules are not conditional on anything
# ═══════════════════════════════════════════════════════════════════════════


def test_the_rules_survive_a_theme_that_cannot_be_rendered():
    """A broken theme costs the deck its design system, never its layout rules.

    `_build_slides_prompt` wraps the whole design-system block in a bare
    `except` that collapses it to an empty string — correct, a theme is an
    improvement and not a precondition. That fallback must not take the layout
    discipline with it, which it would the moment somebody moves these rules
    inside the block. Handing it an object that is not a Theme drives that
    fallback for real rather than asserting it from the source.
    """
    prompt = build_prompt(resolved_theme=object())

    assert "DESIGN SYSTEM — BUILD THIS DECK IN THE THEME BELOW" not in prompt, (
        "the design-system block was expected to collapse for a bogus theme; "
        "if it did not, this test is no longer exercising the fallback"
    )
    assert states_the_layout_rules(prompt)


def test_the_rules_are_there_for_a_deck_with_no_attachments_and_no_charts():
    """The barest possible request still gets the discipline."""
    assert states_the_layout_rules(
        build_prompt(user_prompt="", title=None, viz_profiles=[], files=[])
    )


def test_the_rules_are_there_for_a_deck_built_from_charts_and_images():
    """And so does the richest one — a deck WITH visuals is where the
    centring rule and the hero-slide limit actually bite."""
    prompt = build_prompt(
        viz_profiles=[{"title": "Revenue by region", "type": "bar"}],
        files=[{"id": "img-1", "filename": "logo.png", "content_type": "image/png"}],
        image_count=1,
    )
    assert states_the_layout_rules(prompt)


# ═══════════════════════════════════════════════════════════════════════════
# The centring rule was TRANSLATED to python-pptx, not pasted
# ═══════════════════════════════════════════════════════════════════════════


def test_the_centring_rule_is_written_in_python_pptx_and_not_in_css(prompt):
    """The rule names `PP_ALIGN.CENTER`, which is our stack's alignment.

    These rules are ported from a React project. A rule that arrived as prose
    about centred text — or worse, about `text-align` — reads fine and is
    unexecutable: the model is writing python-pptx, and a paragraph is centred
    by setting `PP_ALIGN.CENTER` on it, not by describing it. Naming the
    constant is what proves the port was translated rather than copied, and it
    is a stronger anchor than the phrase alone, which could survive a
    word-for-word paste.

    Scoped to the span BETWEEN the two neighbouring rules, so the assertion
    cannot be satisfied by `PP_ALIGN` appearing in the namespace listing much
    further down the prompt.
    """
    start = prompt.index(NO_SIDE_VISUAL_RULE)
    end = prompt.index(NAME_THE_SYSTEM_RULE, start)
    centring_rule = prompt[start:end]

    assert "PP_ALIGN.CENTER" in centring_rule


# ═══════════════════════════════════════════════════════════════════════════
# ★ The scope trap — why this file reads a return value and not a file
# ═══════════════════════════════════════════════════════════════════════════


def test_a_whole_file_grep_for_the_marker_would_be_vacuous():
    """`"Name the design system"` is in `create_artifact.py` TWICE.

    One occurrence is the new rule inside `_build_slides_prompt`; the other is
    a pre-existing comment elsewhere in the file, about the sentence the agent
    reads rather than the prompt the model reads. So a whole-file scan for
    that marker stays green with the rule deleted — it is an assertion that
    cannot fail, which this repo has shipped before.

    This test exists to make that fact impossible to rediscover the hard way.
    It is the reason every other case here reads the RETURNED STRING.
    """
    whole_file = Path(ca.__file__).read_text()
    method_source = inspect.getsource(_build)

    file_hits = whole_file.count(NAME_THE_SYSTEM_RULE)
    method_hits = method_source.count(NAME_THE_SYSTEM_RULE)

    assert method_hits >= 1, "the rule is no longer inside _build_slides_prompt"

    if file_hits == method_hits:
        pytest.skip(
            "the second, pre-existing occurrence of this marker is gone, so a "
            "whole-file grep is no longer vacuous. Nothing is broken — but "
            "read the module docstring before narrowing this file's scope."
        )

    assert file_hits > method_hits


def test_the_method_this_file_calls_is_the_one_that_builds_the_prompt():
    """A stand-in `self` only works because the method never touches it.

    If somebody adds a `self.` reference, `build_prompt()` above starts
    raising `AttributeError` on `object()` — a confusing failure in eight
    tests at once. This says so directly instead.
    """
    tree = ast.parse(inspect.getsource(_build).lstrip())
    attribute_uses = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }

    assert attribute_uses == set(), (
        "_build_slides_prompt now uses self; this file's stand-in `self` needs "
        f"to become a real instance or a stub providing {sorted(attribute_uses)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# ★ RED PROOF — the checker can still say no
# ═══════════════════════════════════════════════════════════════════════════


def _without_the_rule_block(prompt: str) -> str:
    """The prompt as it was before the layout rules landed.

    Cuts from the discipline heading to the section header that follows it,
    which is what the pre-change prompt looked like: everything else — the
    storyline guidance, the namespace listing, the data — untouched.
    """
    start = prompt.index(LAYOUT_DISCIPLINE_HEADING)
    # Back up to the start of the numbered rule so the cut is clean.
    start = prompt.rindex("**", 0, start)
    end = prompt.index("AVAILABLE IN NAMESPACE")
    return prompt[:start] + prompt[end:]


def test_the_pre_change_prompt_is_still_detected(prompt):
    """A prompt without the rules must FAIL the checker that passes above.

    Without this, deleting all four rules from the product would leave every
    assertion in this file satisfiable by a checker that had quietly stopped
    looking.
    """
    before = _without_the_rule_block(prompt)

    assert not states_the_layout_rules(before)
    for rule in REQUIRED_RULES:
        assert rule not in before, f"the cut left {rule!r} behind"


def test_the_cut_removed_the_rules_and_not_the_prompt(prompt):
    """The negative control's own positive control.

    A reconstruction that accidentally deleted most of the prompt would fail
    the checker for the wrong reason and prove nothing. The surrounding deck
    craft has to still be there.
    """
    before = _without_the_rule_block(prompt)

    assert "Settle the storyline first" in before
    assert "AVAILABLE IN NAMESPACE" in before
    assert len(before) > 0.5 * len(prompt), (
        "the reconstruction removed far more than the rule block"
    )
