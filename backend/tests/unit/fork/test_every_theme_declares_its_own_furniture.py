"""Every registered theme declares its own furniture, read from its own prompt.

``test_a_theme_draws_its_own_furniture`` proves the PAINTER works. This file
proves the DECLARATION is complete and honest, which is the half that was
missing: the painter drew ledger's ruling and broadsheet's masthead correctly
while most of the catalogue asked it to draw nothing at all.

Three failure modes are worth naming, because each one is green under a lazier
test:

* **A key nothing reads.** A layout may declare ``"sparkles": True`` and every
  test that only reads layouts will pass forever while the slide stays bare.
  So the vocabulary here is scanned OUT OF ``motifs.py`` and diffed, never
  copied into a constant that would drift with it.
* **Invented design.** A layout that agreed only with itself would prove
  nothing. Every colour is checked back against the theme's own vendored
  prompt, its own registry palette, or -- for the seven colours that stand in
  for a stated opacity, which no shape fill can express -- against the
  arithmetic that produced it.
* **Furniture that fights the theme.** ``keynote-minimal`` and ``ted-style``
  forbid footers, page numbers and logos in so many words. A generous default
  would break both, so their emptiness is asserted, not tolerated.

★And the whole file is paired with a positive control. Emptying every entry in
the registry satisfies "no bad key", "no bad hex" and "no forbidden footer"
perfectly; ``test_an_emptied_registry_fails_this_file`` is what makes those
assertions mean something.
"""

import re
from pathlib import Path

import pytest

from app.ai.decks import motifs, pptx_themes
from app.ai.decks.theme_layouts import EMPTY_LAYOUT, HAND_AUTHORED, LAYOUTS

PROMPTS = Path(pptx_themes.__file__).parent / "slidespeak"
MOTIFS_SOURCE = Path(motifs.__file__).with_suffix(".py").read_text(encoding="utf-8")

#: Nested layout values the painter reads through their own dict.
_NESTED = ("margin_rule", "tracker", "footer", "chip", "corner_mark", "stamp", "cover")

#: Keys whose value is a colour, wherever they appear.
_COLOUR_KEYS = ("color", "colour", "ground_color", "ground_color_2", "fill",
                "on", "off", "accent", "rule_color")

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _prompt(theme_id):
    return (PROMPTS / f"{theme_id}.md").read_text(encoding="utf-8")


def _keys_the_painter_reads():
    """The vocabulary, scanned out of ``motifs.py`` rather than declared here.

    Two forms reach a layout value in that module: ``<dict>.get("key")`` and
    ``_pick(layout, theme, "key", role, default)``. Both are collected. The
    result is a superset -- it also holds palette role names and the keys of
    the theme dict -- which is the safe direction: a key the painter never
    reads is what this must catch, and nothing here can invent one.
    """
    keys = set(re.findall(r'\.get\(\s*"([A-Za-z_][A-Za-z0-9_]*)"', MOTIFS_SOURCE))
    keys |= set(re.findall(r'_pick\(\s*\w+,\s*\w+,\s*"([A-Za-z_0-9]+)"', MOTIFS_SOURCE))
    keys |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)" in (?:layout|cover)', MOTIFS_SOURCE))
    # The three footer slots are read through a loop over a table of
    # (name, alignment, x, width), so they never appear as a literal `.get`.
    keys |= set(re.findall(r'\(\s*"([a-z_]+)",\s*PP_ALIGN', MOTIFS_SOURCE))
    return keys


def _layout_keys(layout):
    """Every key a layout uses, its nested dicts included."""
    found = set(layout)
    for name in _NESTED:
        value = layout.get(name)
        if isinstance(value, dict):
            found |= set(value)
    return found


def _hexes(value, out):
    if isinstance(value, dict):
        for key, item in value.items():
            if any(k in key for k in ("color", "colour")) or key in _COLOUR_KEYS:
                out.append(item)
            _hexes(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _hexes(item, out)


def _furniture_count(layout):
    """How many drawn things a layout asks for, the ground not counted."""
    count = 0
    for key in ("rule_spacing_px", "margin_rule", "masthead", "tracker", "footer",
                "footer_rule", "chip", "corner_mark", "stamp"):
        if layout.get(key):
            count += 1
    count += len(layout.get("ornament") or ())
    cover = layout.get("cover") or {}
    if cover:
        count += 1 + len(cover.get("ornament") or ())
    return count


# =============================================================================
# Completeness
# =============================================================================

def test_every_registered_theme_is_read_by_hand_not_merely_present():
    """A derived ground is a fallback, not an answer.

    The registry-has-an-entry check lives in the painter's own test file. This
    is the stronger claim the enrichment makes: all 81 were read out of their
    own prompt, so the scanner in ``theme_layouts`` runs for nothing today.
    """
    registered = {theme.id for theme in pptx_themes.all_themes()}
    assert registered - HAND_AUTHORED == set()
    assert HAND_AUTHORED - registered == set()
    assert len(registered) == 81


def test_every_theme_names_the_paper_it_sits_on():
    """A layout with no ground paints nothing at all.

    ``motifs._paint_ground`` returns early on a colour that is not a hex, so a
    missing or malformed ``ground_color`` is a theme that draws no paper and
    says nothing about it.
    """
    for theme_id, layout in sorted(LAYOUTS.items()):
        assert layout.get("ground") in ("flat", "ruled", "gradient"), theme_id
        assert _HEX.match(layout.get("ground_color") or ""), theme_id
        if layout["ground"] == "gradient":
            assert _HEX.match(layout.get("ground_color_2") or ""), theme_id


# =============================================================================
# ★ The vocabulary -- a key the painter does not read is furniture that never
#   appears, and nothing else in the suite can see it
# =============================================================================

def test_no_layout_uses_a_key_the_painter_never_reads():
    known = _keys_the_painter_reads()
    for theme_id, layout in sorted(LAYOUTS.items()):
        unknown = _layout_keys(layout) - known
        assert unknown == set(), f"{theme_id} declares {sorted(unknown)}"


def test_the_scan_finds_the_vocabulary_and_would_reject_an_invention():
    """The positive control for the scan above.

    A scanner that returned everything, or that silently returned nothing,
    passes the previous test on any registry at all.
    """
    known = _keys_the_painter_reads()
    for key in ("ground", "ground_color", "ground_color_2", "gradient_angle",
                "rule_spacing_px", "rule_color", "rule_width_pt", "rule_start_px",
                "margin_rule", "masthead", "tracker", "footer", "footer_rule",
                "chip", "corner_mark", "stamp", "ornament", "forbid_boxes",
                "skip_title_slide", "cover", "x_px", "kind", "count", "text",
                "rotation", "size_px"):
        assert key in known, key

    for invented in ("sparkles", "confetti", "border_frame", "scanlines"):
        assert invented not in known
    assert _layout_keys({"ground": "flat", "sparkles": True}) - known == {"sparkles"}


def test_every_ornament_named_is_one_the_painter_draws():
    for theme_id, layout in sorted(LAYOUTS.items()):
        named = set(layout.get("ornament") or ())
        named |= set((layout.get("cover") or {}).get("ornament") or ())
        assert named <= set(motifs.ORNAMENTS), theme_id


# =============================================================================
# ★ Honesty -- every colour traces back to the theme's own design system
# =============================================================================

def test_every_colour_is_a_well_formed_hex():
    for theme_id, layout in sorted(LAYOUTS.items()):
        found = []
        _hexes(layout, found)
        assert found, theme_id  # every theme names at least its paper
        for value in found:
            assert isinstance(value, str) and _HEX.match(value), f"{theme_id}: {value!r}"


#: Colours that stand in for a stated OPACITY, which a shape fill cannot carry.
#: theme -> {layout colour: (over, stated colour, opacity)}. The test does the
#: arithmetic; nothing here is taken on trust.
_BLENDED = {
    "arcade": ("#86868C", "#0D0D1A", "#FFFFFF", 0.50),          # "white at 50 percent"
    "cinema": ("#3B3B3B", "#0A0A0A", "#FFFFFF", 0.20),          # "1px white rules at 20%"
    "circuit": ("#92AFA6", "#0E4D3A", "#FFFFFF", 0.55),         # "white at 55 percent"
    "drafting-room": ("#25466F", "#173A66", "#FFFFFF", 0.06),   # "6 percent white opacity"
    "manuscript": ("#D5CCBB", "#F3EAD8", "#3B2F23", 0.16),      # "rgba(59,47,35,0.16)"
    "midnight-pitch": ("#112960", "#0A1838", "#2E6BFF", 0.20),  # "roughly 20 percent"
    "monolith": ("#3D3D3D", "#0C0C0C", "#FFFFFF", 0.20),        # "white at 20 percent"
}

#: Colours the prompt writes as a word rather than a hex.
_NAMED_NOT_HEXED = {
    "billboard": {"#FFFFFF"},        # "white type on every color except yellow"
    "deloitte-style": {"#000000"},   # "Title slide: full-bleed black"
    "one-sheet": {"#000000"},        # "a darker linear gradient toward the bottom edge"
}


def _channels(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def test_a_colour_that_stands_in_for_an_opacity_is_the_colour_it_would_read_as():
    """★Opacity is not expressible on a shape fill, so the layouts resolve it.

    ``motifs._orn_star_field`` documents the same substitution for the star
    dots. This asserts the arithmetic rather than trusting the comment: each
    channel must be the stated colour composited over that theme's own ground,
    within one unit of rounding.
    """
    for theme_id, (blended, over, stated, alpha) in sorted(_BLENDED.items()):
        prompt = _prompt(theme_id).lower()
        assert over.lower() in prompt, theme_id
        for got, ground, front in zip(_channels(blended), _channels(over), _channels(stated)):
            expected = alpha * front + (1 - alpha) * ground
            assert abs(got - expected) <= 1, f"{theme_id}: {got} vs {expected:.1f}"


def test_no_layout_invents_a_colour_its_own_design_system_never_names():
    """Every hex is in the prompt, in the registry palette, or accounted for.

    This is the guard against the failure the module docstring calls out --
    furniture the design system never asked for. A layout that agreed only
    with itself would pass every other test in this file.
    """
    palettes = {theme.id: set(v.upper() for v in theme.palette.values()
                              if isinstance(v, str))
                for theme in pptx_themes.all_themes()}

    for theme_id, layout in sorted(LAYOUTS.items()):
        prompt = _prompt(theme_id).lower()
        allowed = set(_NAMED_NOT_HEXED.get(theme_id, ()))
        if theme_id in _BLENDED:
            allowed.add(_BLENDED[theme_id][0])
        found = []
        _hexes(layout, found)
        for value in found:
            if value.lower() in prompt or value.upper() in palettes[theme_id]:
                continue
            assert value in allowed, f"{theme_id} invented {value}"


def test_every_footer_string_only_interpolates_page_and_pages():
    """A stray brace would raise inside the painter, which swallows it -- the
    footer would then simply never appear, on every slide, silently."""
    for theme_id, layout in sorted(LAYOUTS.items()):
        footer = layout.get("footer")
        if not isinstance(footer, dict):
            continue
        for slot in ("left", "center", "right"):
            text = footer.get(slot)
            if text is None:
                continue
            assert isinstance(text, str) and text.strip(), f"{theme_id}.{slot}"
            assert text.format(page=1, pages=9)
            for field in re.findall(r"\{([a-z_]*)\}", text):
                assert field in ("page", "pages"), f"{theme_id}.{slot}: {field}"


# =============================================================================
# ★ The two traps -- a theme must never be given furniture it forbids, and a
#   threshold must never be read as a prohibition
# =============================================================================

@pytest.mark.parametrize("theme_id, clause", [
    ("keynote-minimal", "no header bars, no footers, no page numbers"),
    ("ted-style", "logos, page numbers, footer bars or date stamps"),
])
def test_a_theme_that_forbids_a_footer_is_not_given_one(theme_id, clause):
    """Both prompts refuse footers in the avoid-list, in those words."""
    prompt = _prompt(theme_id)
    assert clause in prompt
    assert "strictly avoid" in prompt.lower()

    layout = LAYOUTS[theme_id]
    assert layout.get("footer") is None
    assert layout.get("footer_rule") is None
    assert layout.get("tracker") is None
    assert layout.get("chip") is None
    assert layout.get("corner_mark") is None
    assert layout.get("stamp") is None
    assert _furniture_count(layout) == 0


@pytest.mark.parametrize("theme_id, clause", [
    ("telemetry", "corner radii above 8px"),
    ("benchmark", "corner radii above 4px"),
    ("circuit", "rounded corners beyond 3px"),
    ("quiz-night", "rounded corners beyond 12px"),
    ("bain-style", "rounded cards over 4px radius"),
])
def test_a_threshold_is_not_a_prohibition(theme_id, clause):
    """★"corner radii above 8px" means this theme's own 8px panels are right.

    Reading such a clause as a ban is how a theme loses the panels its own
    design system is built on.
    """
    assert clause in _prompt(theme_id)
    assert LAYOUTS[theme_id].get("forbid_boxes") is False


def test_boxes_are_forbidden_only_where_the_prompt_rules_panels_out():
    """The other half of the same trap: the flag must still fire when it
    should. Each of these prompts refuses drawn panels outright."""
    for theme_id, clause in [
        ("ledger", "gradients on content, shadows"),
        ("drafting-room", "solid color fills other than hatching"),
        ("wireframe", "solid borders on containers"),
        ("sorbet", "hard rectangular cards"),
        ("billboard", "nothing else on the canvas"),
    ]:
        assert clause in _prompt(theme_id), theme_id
        assert LAYOUTS[theme_id].get("forbid_boxes") is True, theme_id


# =============================================================================
# The named themes, checked against the sentences they were read from
# =============================================================================

def test_ledger_matches_its_prompt():
    prompt = _prompt("ledger")
    layout = LAYOUTS["ledger"]

    assert "horizontal lines every 28px in #D9E4D2" in prompt
    assert layout["ground"] == "ruled"
    assert layout["rule_spacing_px"] == 28
    assert layout["rule_color"].upper() == "#D9E4D2"
    assert "#C75146, 2px" in prompt and "80px from the left edge" in prompt
    assert layout["margin_rule"] == {"x_px": 80, "color": "#C75146", "width_pt": 1.5}
    assert "rotated about -9 degrees" in prompt
    assert layout["stamp"]["rotation"] == -9
    assert layout["stamp"]["text"] in prompt          # "POSTED · Q3 2026"
    assert layout["stamp"]["color"].upper() == "#C75146"
    # "all content starts to its right" -- nothing exempts the title slide.
    assert layout["skip_title_slide"] is False


def test_broadsheet_matches_its_prompt():
    prompt = _prompt("broadsheet")
    layout = LAYOUTS["broadsheet"]

    assert "Every slide opens with a masthead: a 3px black rule" in prompt
    assert "closed by a thin rule" in prompt
    assert layout["masthead"] is True
    assert "Ink: near-black (#1A1A1A)" in prompt
    assert layout["rule_color"].upper() == "#1A1A1A"
    assert "Background: newsprint (#FAF7F0)" in prompt
    assert layout["ground_color"].upper() == "#FAF7F0"
    # ★The 2-or-3 justified columns are deliberately NOT declared: their
    # dividers are full-height verticals that would cut through the masthead,
    # and the count is not fixed. A margin rule here would be invention.
    assert "2 or 3 narrow justified" in prompt
    assert layout.get("margin_rule") is None


def test_boardroom_matches_its_prompt():
    prompt = _prompt("boardroom")
    layout = LAYOUTS["boardroom"]

    assert "an agenda tracker of six 8px squares" in prompt
    assert layout["tracker"] == {
        "kind": "squares", "count": 6, "on": "#1F3A5F", "off": "#D5DCE4",
    }
    assert "the current slide filled navy, the rest outlined #D5DCE4" in prompt
    assert "navy headings (#1F3A5F)" in prompt
    assert "Footer on every slide: hairline rule, 'Source: team analysis" in prompt
    # The prompt's own string carries a company name ("· Northwind") that this
    # deck does not have, so the footer takes the half that is the theme's.
    assert layout["footer"]["left"] == "Source: team analysis"
    assert layout["footer"]["left"] in prompt
    assert layout["footer"]["right"] == "{page}"
    assert layout["footer_rule"] == "hairline"


def test_notebook_matches_its_prompt():
    prompt = _prompt("notebook")
    layout = LAYOUTS["notebook"]

    assert "pale blue #BFD7EE every 26px" in prompt
    assert layout["rule_spacing_px"] == 26
    assert layout["rule_color"].upper() == "#BFD7EE"
    assert "starting about 70px from the top" in prompt
    assert layout["rule_start_px"] == 70
    assert "#E8A0A0 at 90px from the left" in prompt
    assert layout["margin_rule"]["x_px"] == 90
    assert layout["margin_rule"]["color"].upper() == "#E8A0A0"
    assert "three gray punch holes about 26px wide" in prompt
    assert layout["ornament"] == ("punch_holes",)


def test_telemetry_matches_its_prompt():
    prompt = _prompt("telemetry")
    layout = LAYOUTS["telemetry"]

    assert "Background: #0D1117" in prompt
    assert layout["ground_color"].upper() == "#0D1117"
    assert "small 8px teal status dots with a soft glow meaning healthy" in prompt
    assert "status_dot" in layout["ornament"]
    assert "neon teal #2DD4BF" in prompt
    assert layout["accent"].upper() == "#2DD4BF"
    assert "Labels are tiny 10px uppercase #8B949E" in prompt
    assert layout["corner_mark"]["color"].upper() == "#8B949E"
    assert "A header row on each slide carries" in prompt
    # ★The threshold, again -- this theme's own panels are 8px.
    assert layout["forbid_boxes"] is False


# =============================================================================
# ★ The positive control
# =============================================================================

def test_the_catalogue_actually_declares_furniture():
    """Every assertion above is satisfied by 81 empty entries.

    These counts are what an emptied registry cannot produce. They are also
    the measurement the enrichment is judged on, so they are written down
    rather than described: 31 footers, 9 gradient grounds, 4 ruled grounds,
    12 themes carrying three or more drawn things.
    """
    footers = [t for t, l in LAYOUTS.items() if l.get("footer")]
    grounds = [t for t, l in LAYOUTS.items() if l["ground"] != "flat"]
    ornamented = [t for t, l in LAYOUTS.items() if l.get("ornament")]
    rich = [t for t, l in LAYOUTS.items() if _furniture_count(l) >= 3]

    assert len(footers) >= 30
    assert len(grounds) >= 12
    assert len(ornamented) >= 10
    assert len(rich) >= 12
    assert {"ledger", "notebook", "observatory", "letterhead", "logline",
            "atlas", "art-deco"} <= set(rich)

    # And the motifs that only one or two themes ask for still reach a painter.
    asked = set()
    for layout in LAYOUTS.values():
        asked |= set(layout.get("ornament") or ())
        asked |= set((layout.get("cover") or {}).get("ornament") or ())
    assert asked == set(motifs.ORNAMENTS), sorted(set(motifs.ORNAMENTS) - asked)


def test_an_emptied_registry_fails_this_file():
    """★The control. Without it, "no bad key" and "no invented colour" are
    perfectly satisfied by a registry that declares nothing.

    This reconstructs that registry and requires the checks above to reject
    it -- so deleting the furniture can never leave this file green.
    """
    emptied = {theme_id: dict(EMPTY_LAYOUT) for theme_id in LAYOUTS}

    assert all(_furniture_count(l) == 0 for l in emptied.values())
    assert not [t for t, l in emptied.items() if l.get("footer")]
    assert not [t for t, l in emptied.items() if l["ground"] != "flat"]

    # The key scan and the hex scan pass on it, which is exactly why the
    # counting test above has to exist.
    known = _keys_the_painter_reads()
    assert all(_layout_keys(l) <= known for l in emptied.values())

    # And the ground check does not: an empty layout names no paper.
    with pytest.raises(AssertionError):
        for theme_id, layout in emptied.items():
            assert _HEX.match(layout.get("ground_color") or ""), theme_id
