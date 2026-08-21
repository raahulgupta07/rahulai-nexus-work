"""DEF-E — the fix that separates a request from a mention had never run.

`_named_theme_in` exists to stop conversation noise choosing a deck's design
system. Its own docstring says so:

    "Requiring naming grammar ('in the X style') is what separates a request
     from a mention. Scans from the END so the latest instruction wins."

It never fired once. `_STYLE_PHRASE` ended:

    \\s*(?:style|theme|look)?\\s*[.,;!]

— the naming noun OPTIONAL, the trailing punctuation REQUIRED. Both the wrong
way round. Measured on the shipped 0.0.543.16:

    "make it in the boardroom style"                  -> no match
    "make it in the boardroom style."                 -> boardroom
    "build it in the telemetry theme for the board"   -> no match
    "build it in the telemetry theme, for the board"  -> telemetry

A chat message rarely ends in a full stop, so tier 2 was dead and every deck
fell through to `pptx_themes._match` — "longest alias mentioned ANYWHERE in
text". And the caller hands that function
`messages_context + data.prompt`: the ENTIRE conversation render, every prior
assistant turn and tool summary. So a word that appeared once, anywhere,
including in our own earlier reply, picked the design system:

    "our christmas revenue fell 12% year on year"  ->  christmas theme
    "review of the telemetry team headcount"       ->  telemetry theme

Worse, the choice was reported as `method="resolved"` even for a textbook
request, so a deliberate ask and an accidental substring hit were recorded
identically.

ONE change shipped, and the ORDER of it is the subtle part: the naming noun is
now REQUIRED and the punctuation optional. Making punctuation optional while
leaving the noun optional would match "our meeting in the boardroom" — a
mention, and precisely what this rejects.

★The SECOND half is now FIXED — at the CALLER, and the resolver is untouched.
The first attempt removed the free-text alias tier from `resolve_with_reason`
itself and was BACKED OUT: it broke a documented precedence contract and 9
assertions in `test_the_theme_registry_holds_every_theme.py` — an existing guard
that pins `resolve(user_text='use pitch-book') == 'pitch-book'` and
`resolve(user_text='a midnight pitch please') == 'midnight-pitch'`. Whether
those phrasings should pick a theme is a product decision that guard already
answered, and a guard which fires is a prompt to decide explicitly, not to
delete.

So nothing in `pptx_themes.py` changed. What changed is that the two callers
STOP FEEDING IT THE CONVERSATION: `_select_deck_theme` and `_build_slides_prompt`
both call `_resolve_deck_theme(user_text="", …)`. The alias scan can still be
reached deliberately by anything that hands it text on purpose; it is simply no
longer handed `messages_context + data.prompt`. The sanctioned conversation path
is `_named_theme_in`, which runs one tier earlier on exactly that text and
requires naming grammar — and the resolver keeps every source somebody typed on
purpose (report theme name, org brand, agent default), which is why
`test_a_deck_nobody_styled_still_gets_a_theme` still passes.

★These tests are pure calls. Nothing here needs a schema.
"""
import pathlib
import re

import pytest


BACKEND = pathlib.Path(__file__).resolve().parents[3]
THEMES_PY = BACKEND / "app" / "ai" / "decks" / "pptx_themes.py"


@pytest.fixture(scope="module")
def ca():
    from app.ai.tools.implementations import create_artifact
    return create_artifact


@pytest.fixture(scope="module")
def themes(ca):
    t = ca._load_pptx_themes()
    assert t is not None, "the theme registry did not load"
    return t


# --------------------------------------------------------------------------
# The grammar tier must now actually fire
# --------------------------------------------------------------------------

REQUESTS = [
    "make it in the boardroom style",
    "build it in the telemetry theme for the board",
    "use the mckinsey look please",
    "make it in the boardroom style.",
    "build it in the telemetry theme, for the board",
]


@pytest.mark.parametrize("text", REQUESTS)
def test_a_style_someone_asked_for_is_recognised(ca, themes, text):
    """★Three of these have no trailing punctuation. Every one of them returned
    None before this fix, which is the whole defect."""
    assert ca._named_theme_in(text, themes) is not None, text


def test_a_request_without_a_full_stop_works(ca, themes):
    """The single most common shape — a chat message that just ends."""
    assert ca._named_theme_in("make it in the boardroom style", themes) is not None


# --------------------------------------------------------------------------
# ★The positive control that keeps this a NARROWING, not a loosening
# --------------------------------------------------------------------------

MENTIONS = [
    "our meeting in the boardroom ran long",
    "our christmas revenue fell 12% year on year",
    "review of the telemetry team headcount",
    "the atelier opened a new site",
    "sales in the boardroom were discussed",
]


@pytest.mark.parametrize("text", MENTIONS)
def test_a_theme_merely_mentioned_is_not_a_request(ca, themes, text):
    """★★★The reason the noun is REQUIRED and the punctuation optional, and not
    the other way round. "our meeting in the boardroom ran long" contains
    "in the boardroom" — a lazier repair that only made punctuation optional
    would match it and reintroduce the defect wearing the fix's clothes."""
    assert ca._named_theme_in(text, themes) is None, text


def test_the_weak_intros_still_require_the_naming_noun(ca):
    """★UPDATED with the strong/weak split (2026-08-20). The first repair made
    the noun mandatory EVERYWHERE, and that broke a legitimate request shape —
    "make it Art Deco." names no style noun and is unmistakably a request
    (caught by test_the_latest_instruction_wins). The pattern now has two
    grades: STRONG intros (make it / styled as) may omit the noun, bounded by
    terminal punctuation; WEAK intros (in/use/using) — the ones that appear in
    ordinary prose — still require it. This test pins the half that guards the
    mention hole: the weak alternation must keep its mandatory noun, and the
    behavioural MENTIONS above are the proof it suffices."""
    pattern = ca._STYLE_PHRASE.pattern
    weak = pattern[pattern.index("in|use|using"):]
    assert "(?:style|theme|look)\\b" in weak or "(?:style|theme|look)\b" in weak, \
        "the weak-intro branch lost its mandatory noun"
    assert "(?:style|theme|look)?" not in weak, \
        "the weak-intro branch made the noun optional — the mention hole is open"


def test_a_strong_intro_may_omit_the_noun(ca, themes):
    """★Positive control for the split: the request shape the first repair broke."""
    assert ca._named_theme_in("make it art deco", themes) is not None
    got = ca._named_theme_in("Use the Ledger style. Actually, make it Art Deco.", themes)
    assert got is not None and got.id == "art-deco"


def test_a_weak_intro_with_punctuation_still_cannot_leak_a_mention(ca, themes):
    """★The trap a naive punctuation-only repair walks into: a mention at the
    end of a sentence. Weak intro + full stop must still miss."""
    assert ca._named_theme_in("we sat in the boardroom.", themes) is None


def test_the_pattern_does_not_require_trailing_punctuation(ca):
    assert not re.search(r"\[\.,;!\]\s*$", ca._STYLE_PHRASE.pattern.rstrip())


# --------------------------------------------------------------------------
# What a deck actually gets, through the real entry point
# --------------------------------------------------------------------------

def _pick(ca, text):
    theme, how = ca._select_deck_theme(
        requested_theme_id=None, user_text=text, report=None, organization_settings=None
    )
    return getattr(theme, "id", theme), (how or {}).get("method")


def test_a_deck_about_christmas_is_not_built_in_the_christmas_theme(ca):
    """★The defect in one sentence: a deck ABOUT Christmas revenue is built in
    the Christmas design system."""
    theme_id, _ = _pick(ca, "our christmas revenue fell 12% year on year")
    assert theme_id != "christmas"


def test_a_style_someone_asked_for_still_reaches_the_deck(ca):
    """★The positive control for the pair above. A change that simply stopped
    free text choosing anything would satisfy both of them and delete the
    feature."""
    theme_id, method = _pick(ca, "use the mckinsey look please")
    assert theme_id == "mckinsey-style"
    assert method == "named_by_user"


def test_a_request_is_no_longer_mislabelled_as_a_resolution(ca):
    """Before, even a textbook request recorded `method="resolved"`, so a
    deliberate ask and an accidental substring hit were indistinguishable in the
    telemetry."""
    _, method = _pick(ca, "make it in the boardroom style")
    assert method == "named_by_user"


def test_a_deck_nobody_styled_still_gets_a_theme(ca):
    """★A theme is a design improvement, not a precondition. Narrowing the match
    must never leave a deck with no design system."""
    theme_id, method = _pick(ca, "build a deck on Q3 sales for the board")
    assert theme_id
    assert method in ("resolved", "named_by_user")


# --------------------------------------------------------------------------
# The resolver tier that was removed, and the ones that were kept
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kept", ["_match(report_theme_name)", "_match(agent_default)"])
def test_configuration_fields_still_accept_a_theme_name(kept):
    """★Positive control. A theme name typed into a settings field was typed on
    purpose; only conversation is untrusted. Removing these too would be a
    different defect."""
    src = THEMES_PY.read_text(encoding="utf-8")
    body = src[src.index("def resolve_with_reason"):]
    body = body[:body.index("fallback = _THEMES[DEFAULT_THEME_ID]")]
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    assert kept in body


def test_a_saved_report_theme_still_wins(ca):
    from app.ai.decks import pptx_themes as pt
    theme, reason = pt.resolve_with_reason(report_theme_name="telemetry")
    assert getattr(theme, "id", theme) == "telemetry"
    assert reason == pt.THEME_REASON_EXPLICIT
