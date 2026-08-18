"""Every vendored deck theme is present, well-formed, and reachable.

The 81 SlideSpeak prompts are VENDORED FILES, not code. Nothing at runtime
would notice if one of them failed to ship: a missing ``.md`` degrades to an
empty ``prompt_text``, and a theme with an empty design system still renders a
deck -- just an unstyled one. That is the failure this file exists to catch,
so the count, the prompt body and the font list are all asserted per theme
rather than in aggregate.

``resolve()`` is the other half. It is the only entry point the deck tools
call, it is handed whatever the user typed, and it must ALWAYS hand back a
theme. A resolver that raises on a surprising input takes the deck with it, so
the garbage cases below are as load-bearing as the precedence ones.

No schema needed -- the registry is files and a dict.
"""

import pytest

from app.ai.decks.pptx_themes import (
    CATEGORIES,
    DEFAULT_THEME_ID,
    PALETTE_ROLES,
    Theme,
    all_themes,
    get,
    index_lines,
    resolve,
    spec_block,
)

# Measured at vendor time against the upstream repo. A drift in either number
# means a file failed to ship or an extra one crept in.
EXPECTED_THEME_COUNT = 81

# Sums to 81 -- asserted below, so a typo here fails rather than weakens.
EXPECTED_CATEGORY_COUNTS = {
    "creative": 17,
    "education": 13,
    "pitch": 11,
    "business": 11,
    "consulting": 8,
    "marketing": 6,
    "tech": 6,
    "finance": 5,
    "seasonal": 4,
}

# The upstream chat call-to-action. It must never reach a prompt: it tells the
# model to stop and ask the user what the deck is about.
CHAT_TRAILER_MARKER = "Ask me what the presentation is about"


def test_every_theme_loads():
    assert len(all_themes()) == EXPECTED_THEME_COUNT


def test_ids_are_unique_and_slug_shaped():
    ids = [t.id for t in all_themes()]
    assert len(set(ids)) == len(ids)
    for theme_id in ids:
        assert theme_id == theme_id.lower()
        assert theme_id.replace("-", "").isalnum(), theme_id
        assert not theme_id.startswith("-") and not theme_id.endswith("-")


def test_the_categories_sum_to_every_theme():
    assert sum(EXPECTED_CATEGORY_COUNTS.values()) == EXPECTED_THEME_COUNT

    counts: dict[str, int] = {}
    for theme in all_themes():
        counts[theme.category] = counts.get(theme.category, 0) + 1
    assert counts == EXPECTED_CATEGORY_COUNTS


def test_every_category_is_one_of_the_declared_ones():
    assert set(EXPECTED_CATEGORY_COUNTS) == set(CATEGORIES)
    for theme in all_themes():
        assert theme.category in CATEGORIES


@pytest.mark.parametrize("theme", all_themes(), ids=lambda t: t.id)
def test_a_theme_carries_a_real_design_system(theme: Theme):
    """The per-theme assertions. Parametrised so a failure NAMES the theme."""
    assert theme.name.strip()
    assert theme.when_to_use.strip()
    # The index injects one of these per theme; a runaway line would blow the
    # prompt budget on 81 rows at once.
    assert len(theme.when_to_use.split()) <= 12, theme.when_to_use

    # A vendored file that did not ship reads as an empty prompt, never an error.
    assert theme.prompt_text.strip(), f"{theme.id} has no vendored prompt text"
    assert len(theme.prompt_text) > 200, f"{theme.id} prompt looks truncated"
    assert CHAT_TRAILER_MARKER not in theme.prompt_text

    assert theme.fonts, f"{theme.id} names no font"
    assert isinstance(theme.fonts, tuple)
    for font in theme.fonts:
        assert font.strip() and font == font.strip()

    # Uniform across all 81 upstream themes; a partial palette means the
    # README table parse silently half-failed at vendor time.
    assert set(theme.palette) == set(PALETTE_ROLES), theme.id
    for role, value in theme.palette.items():
        assert value.startswith("#"), (theme.id, role, value)
        assert len(value) == 7, (theme.id, role, value)
        int(value[1:], 16)  # raises on a non-hex body


def test_the_three_awkward_font_themes_parsed_whole():
    """Multi-word families, families with digits, and a lone-font theme.

    These three are the ones a naive parse of the upstream '**Fonts:** A + B'
    line gets wrong -- it splits 'Spline Sans Mono' on whitespace, chokes on
    the '4' in 'Source Serif 4', and finds no '+' at all in a single-font
    theme. The registry reads the structured bullet list instead.
    """
    assert get("aurora").fonts == ("Sora", "Spline Sans Mono")
    assert get("term-sheet").fonts == ("Source Serif 4", "IBM Plex Sans")
    assert get("mainframe").fonts == ("VT323",)


def test_get_returns_none_for_an_unknown_id():
    assert get("no-such-theme") is None
    assert get("") is None
    assert get(None) is None


def test_get_finds_every_theme_by_its_own_id():
    for theme in all_themes():
        assert get(theme.id) is theme


def test_the_default_theme_exists():
    assert get(DEFAULT_THEME_ID) is not None


# --- resolve() -----------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [None, "", "   ", "asdfqwer zxcvbnm", "12345", "!!!", "a" * 500],
)
def test_garbage_resolves_to_the_default(garbage):
    assert resolve(user_text=garbage).id == DEFAULT_THEME_ID


def test_no_arguments_at_all_resolves_to_the_default():
    assert resolve().id == DEFAULT_THEME_ID


@pytest.mark.parametrize(
    "wrong_type",
    [123, [], {}, object(), b"bytes"],
)
def test_resolve_never_raises_on_a_wrong_type(wrong_type):
    """Every tier is handed something of the wrong shape at once."""
    theme = resolve(
        user_text=wrong_type,
        report_theme_name=wrong_type,
        org_brand=wrong_type,
        agent_default=wrong_type,
    )
    assert isinstance(theme, Theme)


def test_user_text_matches_an_id_a_name_and_a_category():
    assert resolve(user_text="build me a mckinsey-style deck").id == "mckinsey-style"
    assert resolve(user_text="use the Dark Academia look").id == "dark-academia"
    # A bare category resolves to that category's flagship theme.
    assert resolve(user_text="I need a pitch deck").category == "pitch"
    assert resolve(user_text="something for finance").category == "finance"


def test_the_longest_match_wins():
    """'midnight pitch' must beat the bare category word 'pitch' inside it."""
    assert resolve(user_text="a midnight pitch please").id == "midnight-pitch"
    assert resolve(user_text="use pitch-book").id == "pitch-book"


def test_a_theme_name_is_not_matched_inside_a_longer_word():
    # 'oat' is a theme id; 'oatmeal' and 'coat' are not requests for it.
    assert resolve(user_text="a deck about oatmeal and coats").id == DEFAULT_THEME_ID


# Each tier, and the tier that must beat it. Ordered exactly as the contract
# specifies: user_text -> report_theme_name -> org_brand -> agent_default.
PRECEDENCE_KWARGS = [
    ("user_text", "mckinsey-style", "mckinsey-style"),
    ("report_theme_name", "atlas", "atlas"),
    ("org_brand", {"theme": "ledger"}, "ledger"),
    ("agent_default", "outrun", "outrun"),
]


@pytest.mark.parametrize("field,value,expected", PRECEDENCE_KWARGS)
def test_each_tier_resolves_on_its_own(field, value, expected):
    assert resolve(**{field: value}).id == expected


@pytest.mark.parametrize("index", range(len(PRECEDENCE_KWARGS)))
def test_each_tier_outranks_every_tier_below_it(index):
    """Fill this tier and all lower ones; this tier must win.

    A resolver that merely picked the first non-empty argument would pass the
    single-tier tests above and fail here.
    """
    winner_field, winner_value, expected = PRECEDENCE_KWARGS[index]
    kwargs = {winner_field: winner_value}
    for field, value, _ in PRECEDENCE_KWARGS[index + 1 :]:
        kwargs[field] = value
    assert resolve(**kwargs).id == expected


def test_a_lower_tier_still_wins_when_the_higher_one_names_nothing():
    """An unmatched higher tier must fall THROUGH, not fall back to default."""
    assert resolve(user_text="hello there", report_theme_name="atlas").id == "atlas"
    assert (
        resolve(user_text="hello", report_theme_name="nonsense", agent_default="outrun").id
        == "outrun"
    )


def test_a_report_theme_name_is_accepted_as_a_display_name():
    """Stored report themes carry the display name, not always the slug."""
    assert resolve(report_theme_name="Dark Academia").id == "dark-academia"
    assert resolve(report_theme_name="McKinsey Style").id == "mckinsey-style"


def test_an_org_brand_without_a_theme_falls_through():
    assert resolve(org_brand={"primary_color": "#FF0000"}).id == DEFAULT_THEME_ID
    assert resolve(org_brand={"theme": None}).id == DEFAULT_THEME_ID
    assert resolve(org_brand={}, agent_default="outrun").id == "outrun"


def test_resolve_always_returns_a_registered_theme():
    for kwargs in ({}, {"user_text": "??"}, {"agent_default": "nope"}):
        theme = resolve(**kwargs)
        assert get(theme.id) is theme


# --- the two prompt surfaces --------------------------------------------


def test_index_lines_has_one_line_per_theme():
    lines = index_lines().splitlines()
    assert len(lines) == EXPECTED_THEME_COUNT

    ids = {t.id for t in all_themes()}
    for line, theme in zip(lines, all_themes()):
        parts = line.split()
        assert parts[0] == theme.id
        assert parts[0] in ids
        assert parts[1] == theme.category
        assert theme.when_to_use in line


def test_the_index_stays_small_enough_to_inject():
    """81 rows go into the prompt on every deck turn."""
    assert len(index_lines()) < 8000


def test_spec_block_carries_the_whole_design_system():
    theme = get("mckinsey-style")
    block = spec_block(theme)

    assert theme.name in block
    assert theme.id in block
    assert theme.category in block
    assert theme.when_to_use in block
    for font in theme.fonts:
        assert font in block
    for value in theme.palette.values():
        assert value in block
    # The point of the block: the vendored system reaches the model intact.
    assert theme.prompt_text in block
    assert CHAT_TRAILER_MARKER not in block


@pytest.mark.parametrize("theme", all_themes(), ids=lambda t: t.id)
def test_spec_block_works_for_every_theme(theme: Theme):
    block = spec_block(theme)
    assert theme.prompt_text in block
    assert block.startswith("DECK THEME:")
