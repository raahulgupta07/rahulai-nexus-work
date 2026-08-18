"""A theme's prohibitions must reach the enforcer, and must not eat the theme.

★0.0.542.7 shipped all three of these broken at once, and every layer reported
success: `Theme` had no `avoid` field, `_theme_as_dict` would have dropped it
anyway, and `enforce_theme_rules` therefore ran against nothing and honestly
reported zero violations while decks carried shadows their own design system
forbids. Nothing failed. That is the shape of bug this file exists to catch.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))

from app.ai.decks import pptx_themes as themes  # noqa: E402

# The only tokens `enforce_theme_rules` knows how to act on.
ENFORCER_VOCABULARY = {
    "shadows", "rounded_corners", "gradients",
    "multiple_accents", "boxes", "legends",
}


def test_every_token_is_one_the_enforcer_understands():
    """A token nothing acts on is a silent no-op dressed as a rule."""
    for theme in themes.all_themes():
        unknown = set(theme.avoid) - ENFORCER_VOCABULARY
        assert not unknown, f"{theme.id} carries tokens no enforcer handles: {unknown}"


def test_the_prohibitions_actually_reach_some_themes():
    """Positive control. Every assertion below is satisfied by parsing NOTHING."""
    with_tokens = [t for t in themes.all_themes() if t.avoid]
    assert len(with_tokens) > 40, (
        f"only {len(with_tokens)} of {len(themes.all_themes())} themes parsed any "
        "prohibition — the parser has silently stopped matching"
    )


def test_mckinsey_forbids_what_its_own_text_forbids():
    avoid = set(themes.get("mckinsey-style").avoid)
    assert {"shadows", "rounded_corners", "gradients"} <= avoid


@pytest.mark.parametrize("theme_id, token, why", [
    (
        "keynote-minimal", "gradients",
        "its signature IS a radial gradient; the avoid clause only mentions one "
        "in 'images on white rectangles pasted onto the dark gradient'",
    ),
    (
        "telemetry", "rounded_corners",
        "it forbids corner radii ABOVE 8px and its own panels are 8px — a "
        "threshold is not a prohibition",
    ),
])
def test_a_theme_never_forbids_its_own_signature(theme_id, token, why):
    """★Both of these parsed as prohibitions on the first attempt. Enforcing
    either would have destroyed the theme the pass exists to protect."""
    assert token not in themes.get(theme_id).avoid, f"{theme_id}: {why}"


def test_a_theme_with_no_mechanical_prohibition_gets_none():
    """Outrun forbids serif fonts, white backgrounds and 'corporate gray' —
    real rules, none of them mechanical. Inventing a token here would be worse
    than an empty tuple."""
    assert themes.get("outrun").avoid == ()


def test_the_dict_handed_to_the_enforcer_carries_avoid():
    """The converter is a field-by-field rebuild, which is how the field went
    missing in the first place."""
    src = (REPO / "backend/app/ai/tools/implementations/create_artifact.py").read_text()
    body = src.split("def _theme_as_dict")[1].split("\ndef ")[0]
    assert '"avoid"' in body, "_theme_as_dict drops `avoid`; enforcement gets nothing"


def test_the_users_own_words_reach_theme_resolution():
    """`data.prompt` is the planner's brief for the tool. A user asking for
    'the ledger style' had it paraphrased away and got McKinsey."""
    src = (REPO / "backend/app/ai/tools/implementations/create_artifact.py").read_text()
    # ★Anchor on the INVOCATION, not the name — the first `_select_deck_theme(`
    # in the file is the definition, whose signature naturally has no
    # conversation in it. A slice boundary that lands on the wrong occurrence
    # measures the boundary, not the product.
    marker = 'requested_theme_id=getattr(data, "theme_id", None),'
    assert marker in src, "the call site moved; this guard is now measuring nothing"
    call = src[src.index(marker): src.index(marker) + 600]
    assert "messages_context" in call, (
        "theme resolution is fed only the planner's paraphrase of the request"
    )


def test_the_original_defect_is_still_detected():
    """★Red proof carried IN the test. Reconstruct the pre-fix shapes and
    require the checks above to reject them — a guard that has never been shown
    to fail is a comment with a test's salary."""
    pre_fix_converter = 'for attr in ("id", "name", "category", "when_to_use"):\n        out[attr] = getattr(theme, attr, None)\n'
    assert '"avoid"' not in pre_fix_converter

    pre_fix_call = 'requested_theme_id=getattr(data, "theme_id", None),\n user_text=data.prompt or "",'
    assert "messages_context" not in pre_fix_call

    # And the substring parser that ate Keynote Minimal's gradient.
    clause = "images on white rectangles pasted onto the dark gradient"
    assert "gradient" in clause          # the naive match fired
    assert not clause.startswith("gradient")   # the shipped one does not


# ---------------------------------------------------------------------------
# Naming a style must beat inferring one.
# ---------------------------------------------------------------------------

def _named():
    from app.ai.tools.implementations.create_artifact import _named_theme_in
    return _named_theme_in


def test_every_style_can_be_asked_for_by_name():
    """All 81, phrased the way a person actually asks."""
    named = _named()
    misses = []
    for theme in themes.all_themes():
        text = f"Make a short deck about our process. Use the {theme.name} style."
        got = named(text, themes)
        if got is None or got.id != theme.id:
            misses.append((theme.id, got.id if got else None))
    assert not misses, f"styles that could not be asked for by name: {misses}"


def test_a_mention_is_not_a_request():
    """★The defect this replaced: the conversation render handed to selection
    carries every prior assistant turn, and a bare-token search over it
    resolved a request for the Atelier style to `christmas` — a name that
    merely appeared in the noise."""
    noise = "Earlier we discussed the christmas campaign and the memo about metro stores."
    assert _named()(noise, themes) is None


def test_the_real_request_still_wins_after_noise():
    """Positive control for the test above — which is otherwise satisfied by an
    extractor that never matches anything at all."""
    noise = "Earlier we discussed the christmas campaign and the memo about metro stores."
    got = _named()(noise + " Make a deck. Use the Atelier style.", themes)
    assert got is not None and got.id == "atelier"


def test_the_latest_instruction_wins():
    """A person who changes their mind mid-conversation means the second one."""
    got = _named()("Use the Ledger style. Actually, make it Art Deco.", themes)
    assert got is not None and got.id == "art-deco"
