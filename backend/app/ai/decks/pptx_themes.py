"""PPTX deck theme registry.

Wraps the 81 SlideSpeak "slide design prompts" (MIT, see
``slidespeak/LICENSE``) as a small, dependency-free registry the deck tools can
resolve a theme from and inject into a model prompt.

The prompt text itself is vendored verbatim, one file per theme, at
``slidespeak/<theme-id>.md``. Everything else -- display name, category, fonts,
palette -- was lifted from the upstream per-theme README at vendor time and is
baked into ``_META`` below so that importing this module does not depend on
those READMEs being present.

Palette keys are uniform across all 81 themes. The roles are::

    background, surface_panel, border, primary_accent, primary_soft_tint,
    text_on_primary, heading_text, body_text, muted_text,
    chart_1, chart_2, chart_3, chart_4

Every value is an uppercase ``#RRGGBB`` string. Treat the mapping as read-only:
it is built once at import and shared by every caller of ``get``/``all_themes``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Theme",
    "DEFAULT_THEME_ID",
    "PALETTE_ROLES",
    "CATEGORIES",
    "all_themes",
    "get",
    "resolve",
    "index_lines",
    "spec_block",
]

_PROMPT_DIR = Path(__file__).parent / "slidespeak"

DEFAULT_THEME_ID = "boardroom"

PALETTE_ROLES = (
    "background",
    "surface_panel",
    "border",
    "primary_accent",
    "primary_soft_tint",
    "text_on_primary",
    "heading_text",
    "body_text",
    "muted_text",
    "chart_1",
    "chart_2",
    "chart_3",
    "chart_4",
)

CATEGORIES = (
    "pitch",
    "business",
    "consulting",
    "marketing",
    "tech",
    "creative",
    "education",
    "finance",
    "seasonal",
)

#: Category -> the flagship theme a bare category mention resolves to.
_CATEGORY_DEFAULTS = {
    "pitch": "midnight-pitch",
    "business": "boardroom",
    "consulting": "mckinsey-style",
    "marketing": "billboard",
    "tech": "telemetry",
    "creative": "atelier",
    "education": "syllabus",
    "finance": "ledger",
    "seasonal": "christmas",
}


@dataclass(frozen=True)
class Theme:
    """One vendored deck design system."""

    id: str
    name: str
    category: str
    fonts: tuple[str, ...]
    #: role -> '#RRGGBB'; see PALETTE_ROLES. Read-only by convention.
    palette: dict[str, str] = field(hash=False)
    #: <= 12 words, cheap enough to inject one per theme as an index.
    when_to_use: str = ""
    #: The full vendored design prompt.
    prompt_text: str = field(default="", hash=False, repr=False)
    #: Mechanical prohibitions parsed from this theme's own "strictly avoid"
    #: clause. ★These are what `enforce_theme_rules` acts on. Without them the
    #: enforcement pass has nothing forbidden and honestly reports zero, which
    #: is exactly how 0.0.542.7 shipped decks carrying shadows their own design
    #: system forbids. Parsed, never hand-written, so a re-vendor cannot drift.
    avoid: tuple[str, ...] = ()



#: Phrase seen in a vendored prompt -> the token `enforce_theme_rules` acts on.
#: Deliberately narrow: a token earns its place only when the enforcer can DO
#: something mechanical about it. Anything else is left to the prompt.
_AVOID_PHRASES: tuple[tuple[str, str], ...] = (
    ("drop shadow", "shadows"),
    ("shadows", "shadows"),
    ("rounded corner", "rounded_corners"),
    ("corner radii", "rounded_corners"),
    ("gradient", "gradients"),
    ("more than one accent", "multiple_accents"),
    ("more than two accent", "multiple_accents"),
    ("legend", "legends"),
    ("card layout", "boxes"),
    ("corporate card", "boxes"),
)


def _parse_avoid(prompt_text: str) -> tuple[str, ...]:
    """Tokens from a theme's own 'strictly avoid: ...' sentence.

    Scoped to that sentence on purpose. The prompts describe what a theme DOES
    using the same words ('a soft radial gradient', 'shadows'), so scanning the
    whole text would forbid a theme's own signature — Keynote Minimal would end
    up with its gradient flattened by the pass meant to protect it.
    """
    if not prompt_text:
        return ()
    m = re.search(r"(?:strictly\s+)?avoid[:\s]+(.+?)(?:\n\n|$)", prompt_text, re.I | re.S)
    if not m:
        return ()
    clause = m.group(1).lower()
    found: list[str] = []
    # ★Match per ITEM, not across the whole clause. These sentences list what a
    # theme forbids AND mention what it uses: Keynote Minimal forbids "images on
    # white rectangles pasted onto the dark gradient", and a substring match on
    # "gradient" read that as "no gradients" — which would have flattened the
    # signature radial glow that IS the theme. An item must OPEN with the
    # phrase (after throwaway leading words) to count as prohibiting it.
    for raw in re.split(r"[;,]", clause):
        item = raw.strip()
        for lead in ("any ", "no ", "more than ", "all ", "the "):
            if item.startswith(lead) and lead != "more than ":
                item = item[len(lead):].strip()
        # ★A THRESHOLD is not a prohibition. Telemetry forbids "corner radii
        # above 8px" and its own panels are 8px — squaring them to 0 would
        # break the theme this pass exists to protect. Same for any "beyond
        # the subtle reflection" style allowance.
        if any(w in item for w in ("above ", "beyond ", "greater than ", "more than two")):
            continue
        for phrase, token in _AVOID_PHRASES:
            if token in found:
                continue
            if item.startswith(phrase) or item == phrase.rstrip("s"):
                found.append(token)
    return tuple(found)


# id -> (name, category, fonts, when_to_use, palette). Generated at vendor time.
_META: dict[str, tuple] = {
    "accenture-style": (
        "Accenture Style",
        "consulting",
        ("Archivo", "Inter"),
        "Purple, black, and greater than",
        {"background": "#FFFFFF", "surface_panel": "#F6F4F9", "border": "#E4E1EA", "primary_accent": "#A100FF", "primary_soft_tint": "#F3E6FF", "text_on_primary": "#FFFFFF", "heading_text": "#000000", "body_text": "#202020", "muted_text": "#66646E", "chart_1": "#A100FF", "chart_2": "#7500C0", "chart_3": "#000000", "chart_4": "#D2A5FF"},
    ),
    "arcade": (
        "Arcade",
        "tech",
        ("Press Start 2P", "VT323"),
        "Insert coin",
        {"background": "#0D0D1A", "surface_panel": "#1A1A2E", "border": "#2C2C4A", "primary_accent": "#FFD23F", "primary_soft_tint": "#4A3D14", "text_on_primary": "#0D0D1A", "heading_text": "#FFFFFF", "body_text": "#C9C9E0", "muted_text": "#76769A", "chart_1": "#FFD23F", "chart_2": "#FF4365", "chart_3": "#2DE2E6", "chart_4": "#7CFC00"},
    ),
    "art-deco": (
        "Art Deco",
        "creative",
        ("Cinzel", "Marcellus", "Tenor Sans"),
        "Gold geometry for the modern jazz age",
        {"background": "#0E1512", "surface_panel": "#16241F", "border": "#6F5B30", "primary_accent": "#D4AF37", "primary_soft_tint": "#2E2816", "text_on_primary": "#1B1508", "heading_text": "#EFE3C0", "body_text": "#C9C3B0", "muted_text": "#948E76", "chart_1": "#D4AF37", "chart_2": "#46A08B", "chart_3": "#C25E6A", "chart_4": "#ADB5B0"},
    ),
    "atelier": (
        "Atelier",
        "creative",
        ("Jost", "Spline Sans Mono"),
        "Built like a blueprint",
        {"background": "#F4F1EA", "surface_panel": "#FFFFFF", "border": "#DBD5C8", "primary_accent": "#B4502E", "primary_soft_tint": "#F0E1D8", "text_on_primary": "#FFFFFF", "heading_text": "#1C1B19", "body_text": "#4A4843", "muted_text": "#8E897E", "chart_1": "#B4502E", "chart_2": "#1C1B19", "chart_3": "#C9A98E", "chart_4": "#DBD5C8"},
    ),
    "atlas": (
        "Atlas",
        "business",
        ("Plus Jakarta Sans", "Inter"),
        "The dependable one",
        {"background": "#FFFFFF", "surface_panel": "#FCFCFD", "border": "#E5E9F2", "primary_accent": "#1570EF", "primary_soft_tint": "#EFF8FF", "text_on_primary": "#FFFFFF", "heading_text": "#101828", "body_text": "#475467", "muted_text": "#98A2B3", "chart_1": "#1570EF", "chart_2": "#53B1FD", "chart_3": "#B2DDFF", "chart_4": "#EBEEF5"},
    ),
    "atrium": (
        "Atrium",
        "education",
        ("Fraunces", "Work Sans"),
        "Calm, like a courtyard",
        {"background": "#F7F2E9", "surface_panel": "#EFE7D8", "border": "#DCD2BC", "primary_accent": "#C4704F", "primary_soft_tint": "#F2E0D6", "text_on_primary": "#FFF8F0", "heading_text": "#33392B", "body_text": "#5C604E", "muted_text": "#98987F", "chart_1": "#7A8C6F", "chart_2": "#C4704F", "chart_3": "#D9CBA8", "chart_4": "#E9E1CE"},
    ),
    "aurora": (
        "Aurora",
        "pitch",
        ("Sora", "Spline Sans Mono"),
        "Gradient pitch deck for SaaS",
        {"background": "#0B0B16", "surface_panel": "#15152A", "border": "#2A2A45", "primary_accent": "#7C5CFF", "primary_soft_tint": "#221A45", "text_on_primary": "#FFFFFF", "heading_text": "#F4F3FF", "body_text": "#C3C0DE", "muted_text": "#807CA6", "chart_1": "#7C5CFF", "chart_2": "#36E0D0", "chart_3": "#FF7AC6", "chart_4": "#4A4A77"},
    ),
    "bain-style": (
        "Bain Style",
        "consulting",
        ("Archivo", "Barlow Condensed"),
        "Answer first, red where it counts",
        {"background": "#FFFFFF", "surface_panel": "#F5F5F5", "border": "#D9D9D9", "primary_accent": "#CC0000", "primary_soft_tint": "#FAE5E5", "text_on_primary": "#FFFFFF", "heading_text": "#191919", "body_text": "#333333", "muted_text": "#666666", "chart_1": "#CC0000", "chart_2": "#1F1F1F", "chart_3": "#8C8C8C", "chart_4": "#21A663"},
    ),
    "basel": (
        "Basel",
        "creative",
        ("Archivo",),
        "Loud type, nothing else",
        {"background": "#F4F1EA", "surface_panel": "#FFFFFF", "border": "#111111", "primary_accent": "#E32213", "primary_soft_tint": "#F9DCD8", "text_on_primary": "#FFFFFF", "heading_text": "#111111", "body_text": "#4A463E", "muted_text": "#8A867E", "chart_1": "#111111", "chart_2": "#E32213", "chart_3": "#8A867E", "chart_4": "#DAD5C8"},
    ),
    "bcg-style": (
        "BCG Style",
        "consulting",
        ("Arimo", "Gelasio"),
        "Strategy lives in a 2x2",
        {"background": "#FFFFFF", "surface_panel": "#F6F4F3", "border": "#D9D6D0", "primary_accent": "#177B57", "primary_soft_tint": "#E4F1EA", "text_on_primary": "#FFFFFF", "heading_text": "#177B57", "body_text": "#4A4A4A", "muted_text": "#9A9A9A", "chart_1": "#177B57", "chart_2": "#21BF61", "chart_3": "#8EC6A1", "chart_4": "#E6E0DB"},
    ),
    "benchmark": (
        "Benchmark",
        "business",
        ("IBM Plex Sans", "IBM Plex Mono"),
        "Us versus the field",
        {"background": "#FAFAF9", "surface_panel": "#FFFFFF", "border": "#E7E5E4", "primary_accent": "#166534", "primary_soft_tint": "#EBF3EC", "text_on_primary": "#FFFFFF", "heading_text": "#1C1917", "body_text": "#57534E", "muted_text": "#78716C", "chart_1": "#166534", "chart_2": "#1C1917", "chart_3": "#A8A29E", "chart_4": "#F0EFEA"},
    ),
    "billboard": (
        "Billboard",
        "marketing",
        ("Anton",),
        "One color, one line",
        {"background": "#FF3D2E", "surface_panel": "#0057FF", "border": "#FFFFFF", "primary_accent": "#FFD400", "primary_soft_tint": "#FFEB80", "text_on_primary": "#111111", "heading_text": "#FFFFFF", "body_text": "#FFFFFF", "muted_text": "#FFD1CB", "chart_1": "#FFD400", "chart_2": "#0057FF", "chart_3": "#00B364", "chart_4": "#111111"},
    ),
    "boardroom": (
        "Boardroom",
        "business",
        ("Source Sans 3",),
        "The slide is the argument",
        {"background": "#FFFFFF", "surface_panel": "#EEF2F7", "border": "#D5DCE4", "primary_accent": "#1F3A5F", "primary_soft_tint": "#EEF2F7", "text_on_primary": "#FFFFFF", "heading_text": "#1F3A5F", "body_text": "#5B6B7E", "muted_text": "#8E9BAA", "chart_1": "#1F3A5F", "chart_2": "#4C7DB5", "chart_3": "#A8B6C7", "chart_4": "#EEF2F7"},
    ),
    "broadsheet": (
        "Broadsheet",
        "finance",
        ("Playfair Display", "Newsreader"),
        "Read all about it",
        {"background": "#FAF7F0", "surface_panel": "#FFFFFF", "border": "#C9C2B0", "primary_accent": "#A61B1B", "primary_soft_tint": "#F5E6E2", "text_on_primary": "#FFFCF5", "heading_text": "#1A1A1A", "body_text": "#3D3A33", "muted_text": "#8D8775", "chart_1": "#1A1A1A", "chart_2": "#5A564C", "chart_3": "#A61B1B", "chart_4": "#DAD4C4"},
    ),
    "bubblegum": (
        "Bubblegum",
        "marketing",
        ("Baloo 2", "Nunito"),
        "Y2K sparkle with a system",
        {"background": "#FBEAF4", "surface_panel": "#FFFFFF", "border": "#F6CFE6", "primary_accent": "#E5388E", "primary_soft_tint": "#FBD9EC", "text_on_primary": "#FFFFFF", "heading_text": "#5B1D52", "body_text": "#7A4A6E", "muted_text": "#B98AAC", "chart_1": "#E5388E", "chart_2": "#3CC8D6", "chart_3": "#A6E22E", "chart_4": "#9B6BE0"},
    ),
    "chalkboard": (
        "Chalkboard",
        "education",
        ("Permanent Marker", "Schoolbell"),
        "Drawn up at halftime",
        {"background": "#2A3B2F", "surface_panel": "#324639", "border": "#8B6B4A", "primary_accent": "#E8D44D", "primary_soft_tint": "#4F523B", "text_on_primary": "#2A3B2F", "heading_text": "#F2F0E4", "body_text": "#D9D6C3", "muted_text": "#9AA38F", "chart_1": "#F2F0E4", "chart_2": "#E8D44D", "chart_3": "#9AA38F", "chart_4": "#4F5D50"},
    ),
    "chevron": (
        "Chevron",
        "business",
        ("Archivo", "Inter"),
        "Four phases, one direction",
        {"background": "#FFFFFF", "surface_panel": "#F9FAFB", "border": "#E5E7EB", "primary_accent": "#1E3A8A", "primary_soft_tint": "#DBEAFE", "text_on_primary": "#FFFFFF", "heading_text": "#1E3A8A", "body_text": "#374151", "muted_text": "#6B7280", "chart_1": "#1E3A8A", "chart_2": "#60A5FA", "chart_3": "#F59E0B", "chart_4": "#E5E7EB"},
    ),
    "christmas": (
        "Christmas",
        "seasonal",
        ("Playfair Display", "Source Sans 3", "Caveat"),
        "Festive polish, zero clipart kitsch",
        {"background": "#FAF6EC", "surface_panel": "#FFFDF6", "border": "#E6DCC4", "primary_accent": "#1B4332", "primary_soft_tint": "#E3EDE6", "text_on_primary": "#FAF6EC", "heading_text": "#1F2D23", "body_text": "#3D4A40", "muted_text": "#6F7D70", "chart_1": "#1B4332", "chart_2": "#9B2226", "chart_3": "#C0902E", "chart_4": "#A3B18A"},
    ),
    "cinema": (
        "Cinema",
        "creative",
        ("Cinzel", "Space Mono"),
        "Quiet on set",
        {"background": "#0A0A0A", "surface_panel": "#141414", "border": "#2A2A2A", "primary_accent": "#E2B15C", "primary_soft_tint": "#3A2F1B", "text_on_primary": "#0A0A0A", "heading_text": "#F5F2EA", "body_text": "#C9C4B8", "muted_text": "#8A867C", "chart_1": "#E2B15C", "chart_2": "#F5F2EA", "chart_3": "#8A867C", "chart_4": "#3A3A3A"},
    ),
    "circuit": (
        "Circuit",
        "tech",
        ("Chakra Petch", "Share Tech Mono"),
        "Follow the traces",
        {"background": "#0E4D3A", "surface_panel": "#0A3528", "border": "#2B6B55", "primary_accent": "#D9A45B", "primary_soft_tint": "#5E4A2C", "text_on_primary": "#0A3528", "heading_text": "#FFFFFF", "body_text": "#CFE0D4", "muted_text": "#8FB3A0", "chart_1": "#D9A45B", "chart_2": "#E8C27A", "chart_3": "#8FB3A0", "chart_4": "#2B6B55"},
    ),
    "collage": (
        "Collage",
        "creative",
        ("Bricolage Grotesque", "Work Sans"),
        "Scissors first, layout second",
        {"background": "#EFEBE3", "surface_panel": "#FFFFFF", "border": "#D8D2C6", "primary_accent": "#E2574C", "primary_soft_tint": "#F8DCD9", "text_on_primary": "#FFFFFF", "heading_text": "#2B2722", "body_text": "#6B6459", "muted_text": "#9A9386", "chart_1": "#E2574C", "chart_2": "#2A9D8F", "chart_3": "#E9C46A", "chart_4": "#264653"},
    ),
    "coquette": (
        "Coquette",
        "creative",
        ("Cormorant Garamond", "Jost"),
        "Ribbons, blush and a cherry kiss",
        {"background": "#FBF1F2", "surface_panel": "#FFFFFF", "border": "#F0D9DC", "primary_accent": "#A8324A", "primary_soft_tint": "#F7DDE2", "text_on_primary": "#FFFFFF", "heading_text": "#5E2733", "body_text": "#6B4750", "muted_text": "#A98A91", "chart_1": "#A8324A", "chart_2": "#D98C9A", "chart_3": "#E8B4BC", "chart_4": "#C76B7E"},
    ),
    "dark-academia": (
        "Dark Academia",
        "creative",
        ("Cinzel", "EB Garamond"),
        "Candlelit library, rare-books society",
        {"background": "#1E1A17", "surface_panel": "#2A2420", "border": "#3D352E", "primary_accent": "#B08A3E", "primary_soft_tint": "#3A2E22", "text_on_primary": "#1E1A17", "heading_text": "#EDE3CE", "body_text": "#C9BBA0", "muted_text": "#8C7E68", "chart_1": "#B08A3E", "chart_2": "#8C3B2E", "chart_3": "#6E7B53", "chart_4": "#A9784B"},
    ),
    "deloitte-style": (
        "Deloitte Style",
        "consulting",
        ("Open Sans", "Source Serif 4"),
        "Black, white and one green dot",
        {"background": "#FFFFFF", "surface_panel": "#F0F1F1", "border": "#D0D0CE", "primary_accent": "#000000", "primary_soft_tint": "#F0F1F1", "text_on_primary": "#FFFFFF", "heading_text": "#000000", "body_text": "#53565A", "muted_text": "#97999B", "chart_1": "#0076A8", "chart_2": "#00ABAB", "chart_3": "#86BC25", "chart_4": "#D0D0CE"},
    ),
    "demo-day": (
        "Demo Day",
        "pitch",
        ("Poppins",),
        "Three minutes, one idea per slide",
        {"background": "#FFFFFF", "surface_panel": "#FAFAFA", "border": "#E5E5E5", "primary_accent": "#FF6B00", "primary_soft_tint": "#FFE8D9", "text_on_primary": "#FFFFFF", "heading_text": "#111111", "body_text": "#444444", "muted_text": "#9B9B9B", "chart_1": "#FF6B00", "chart_2": "#111111", "chart_3": "#9B9B9B", "chart_4": "#E5E5E5"},
    ),
    "drafting-room": (
        "Drafting Room",
        "tech",
        ("Saira", "Spline Sans Mono"),
        "Measure twice, present once",
        {"background": "#173A66", "surface_panel": "#1D4373", "border": "#9FC6FF", "primary_accent": "#9FC6FF", "primary_soft_tint": "#244D80", "text_on_primary": "#173A66", "heading_text": "#EAF3FF", "body_text": "#B7CFEA", "muted_text": "#6E93BF", "chart_1": "#9FC6FF", "chart_2": "#6FA3E0", "chart_3": "#3E72AC", "chart_4": "#244D80"},
    ),
    "expedition": (
        "Expedition",
        "education",
        ("EB Garamond",),
        "Here be agenda items",
        {"background": "#F0E6D2", "surface_panel": "#F7EFDF", "border": "#4A3B28", "primary_accent": "#A33B2E", "primary_soft_tint": "#EFD6CC", "text_on_primary": "#F0E6D2", "heading_text": "#4A3B28", "body_text": "#6B5840", "muted_text": "#9A8A72", "chart_1": "#4A3B28", "chart_2": "#A33B2E", "chart_3": "#9A8A72", "chart_4": "#D8C8A8"},
    ),
    "ey-style": (
        "EY Style",
        "consulting",
        ("Barlow", "Barlow Condensed"),
        "Dark slate, one decisive yellow beam",
        {"background": "#2E2E38", "surface_panel": "#24242E", "border": "#4A4A57", "primary_accent": "#FFE600", "primary_soft_tint": "#46421C", "text_on_primary": "#2E2E38", "heading_text": "#FFFFFF", "body_text": "#F6F6FA", "muted_text": "#C4C4CD", "chart_1": "#FFE600", "chart_2": "#C4C4CD", "chart_3": "#747480", "chart_4": "#188CE5"},
    ),
    "field-notes": (
        "Field Notes",
        "education",
        ("Courier Prime",),
        "Taped to the folder",
        {"background": "#D7BC94", "surface_panel": "#FFFFFF", "border": "#3A3530", "primary_accent": "#B5483A", "primary_soft_tint": "#F3DCD8", "text_on_primary": "#FFFFFF", "heading_text": "#3A3530", "body_text": "#5C554D", "muted_text": "#8A8178", "chart_1": "#3A3530", "chart_2": "#B5483A", "chart_3": "#7DA0C7", "chart_4": "#D7BC94"},
    ),
    "halloween": (
        "Halloween",
        "seasonal",
        ("Alfa Slab One", "Baloo 2", "Schoolbell"),
        "Spooky season, classroom approved",
        {"background": "#1C1030", "surface_panel": "#2A1B4A", "border": "#4A3B73", "primary_accent": "#FA7602", "primary_soft_tint": "#4A2A16", "text_on_primary": "#1C1030", "heading_text": "#FDF4E3", "body_text": "#E6DFF2", "muted_text": "#A99BC7", "chart_1": "#FA7602", "chart_2": "#A06CD5", "chart_3": "#7AC74F", "chart_4": "#45A8D0"},
    ),
    "harvey": (
        "Harvey",
        "business",
        ("Jost", "DM Mono"),
        "Maturity in quarter turns",
        {"background": "#FFFFFF", "surface_panel": "#F2F4F7", "border": "#D8DEE6", "primary_accent": "#14365D", "primary_soft_tint": "#E8EEF6", "text_on_primary": "#FFFFFF", "heading_text": "#14365D", "body_text": "#4D5B6E", "muted_text": "#8A97A8", "chart_1": "#14365D", "chart_2": "#7E2A33", "chart_3": "#4D5B6E", "chart_4": "#D8DEE6"},
    ),
    "hearth": (
        "Hearth",
        "pitch",
        ("Fraunces", "Karla"),
        "Warm, story-led brand pitch",
        {"background": "#FBF4EC", "surface_panel": "#FFFDF9", "border": "#ECDFD0", "primary_accent": "#C45B3C", "primary_soft_tint": "#F6E2D7", "text_on_primary": "#FFFFFF", "heading_text": "#2E2117", "body_text": "#5C4F43", "muted_text": "#998A7A", "chart_1": "#C45B3C", "chart_2": "#2E2117", "chart_3": "#E0A06F", "chart_4": "#D9C7B4"},
    ),
    "herbarium": (
        "Herbarium",
        "education",
        ("Crimson Pro", "Karla"),
        "Pressed, labeled, filed",
        {"background": "#FBF9F2", "surface_panel": "#FFFFFF", "border": "#C9D2B8", "primary_accent": "#5A7048", "primary_soft_tint": "#E7EBDD", "text_on_primary": "#FFFFFF", "heading_text": "#2F3526", "body_text": "#4C5240", "muted_text": "#8A8F7C", "chart_1": "#5A7048", "chart_2": "#2F3526", "chart_3": "#A8B58F", "chart_4": "#E7EBDD"},
    ),
    "holo": (
        "Holo",
        "pitch",
        ("Sora", "Inter"),
        "Iridescent, but disciplined",
        {"background": "#FAFAFC", "surface_panel": "#FFFFFF", "border": "#ECECF2", "primary_accent": "#C3B7FF", "primary_soft_tint": "#F1EEFF", "text_on_primary": "#3A3A46", "heading_text": "#3A3A46", "body_text": "#5C5C6A", "muted_text": "#8A8A98", "chart_1": "#FFB6E1", "chart_2": "#C3B7FF", "chart_3": "#9FE8FF", "chart_4": "#B8FFD9"},
    ),
    "keynote-minimal": (
        "Keynote Minimal",
        "pitch",
        ("Inter", "Source Sans 3"),
        "One slide, one idea, zero clutter",
        {"background": "#0A0A0C", "surface_panel": "#1D1D1F", "border": "#3A3A3C", "primary_accent": "#0071E3", "primary_soft_tint": "#10314E", "text_on_primary": "#FFFFFF", "heading_text": "#F5F5F7", "body_text": "#D2D2D7", "muted_text": "#86868B", "chart_1": "#F5F5F7", "chart_2": "#0071E3", "chart_3": "#86868B", "chart_4": "#48484C"},
    ),
    "kpmg-style": (
        "KPMG Style",
        "consulting",
        ("Barlow Condensed", "Arimo"),
        "Audit-grade clarity in KPMG blue",
        {"background": "#FFFFFF", "surface_panel": "#F4F6FA", "border": "#D8DFE9", "primary_accent": "#00338D", "primary_soft_tint": "#E6ECF6", "text_on_primary": "#FFFFFF", "heading_text": "#00338D", "body_text": "#333940", "muted_text": "#697280", "chart_1": "#00338D", "chart_2": "#005EB8", "chart_3": "#0091DA", "chart_4": "#00A3A1"},
    ),
    "ledger": (
        "Ledger",
        "finance",
        ("Libre Baskerville", "Cutive Mono"),
        "Every number reconciled",
        {"background": "#F4F7F0", "surface_panel": "#FBFCF9", "border": "#D9E4D2", "primary_accent": "#C75146", "primary_soft_tint": "#F3DDD9", "text_on_primary": "#FFFFFF", "heading_text": "#2E3A2E", "body_text": "#5F6E5C", "muted_text": "#8C9888", "chart_1": "#2E3A2E", "chart_2": "#C75146", "chart_3": "#7C8F77", "chart_4": "#D9E4D2"},
    ),
    "letterhead": (
        "Letterhead",
        "finance",
        ("Cormorant Garamond",),
        "Engraved, not printed",
        {"background": "#FCF9F1", "surface_panel": "#FFFFFF", "border": "#E3DAC2", "primary_accent": "#B49A5B", "primary_soft_tint": "#F2EAD6", "text_on_primary": "#FFFFFF", "heading_text": "#2B2B28", "body_text": "#55524A", "muted_text": "#8E897A", "chart_1": "#2B2B28", "chart_2": "#B49A5B", "chart_3": "#8E897A", "chart_4": "#E3DAC2"},
    ),
    "level-up": (
        "Level Up",
        "education",
        ("Baloo 2", "JetBrains Mono"),
        "Earn the XP",
        {"background": "#F7F9FF", "surface_panel": "#FFFFFF", "border": "#E9ECF8", "primary_accent": "#6D28D9", "primary_soft_tint": "#EFE9FB", "text_on_primary": "#FFFFFF", "heading_text": "#1E1B2E", "body_text": "#4B4763", "muted_text": "#8E8AA6", "chart_1": "#6D28D9", "chart_2": "#84CC16", "chart_3": "#F5B80B", "chart_4": "#D9D2F2"},
    ),
    "logline": (
        "Logline",
        "creative",
        ("Newsreader", "Courier Prime"),
        "The pitch in one page",
        {"background": "#F3EFE7", "surface_panel": "#FBFAF6", "border": "#DAD3C5", "primary_accent": "#B23A2E", "primary_soft_tint": "#EFDDD7", "text_on_primary": "#FFFFFF", "heading_text": "#211E1A", "body_text": "#4C4842", "muted_text": "#8C867A", "chart_1": "#B23A2E", "chart_2": "#211E1A", "chart_3": "#C9A99E", "chart_4": "#DAD3C5"},
    ),
    "lookbook": (
        "Lookbook",
        "creative",
        ("Playfair Display", "Archivo"),
        "Mood, tone and frame",
        {"background": "#1A1714", "surface_panel": "#262019", "border": "#3B332A", "primary_accent": "#CC7A4D", "primary_soft_tint": "#3A2A1E", "text_on_primary": "#1A1714", "heading_text": "#F2EADD", "body_text": "#C8BCAA", "muted_text": "#8A7F6E", "chart_1": "#CC7A4D", "chart_2": "#F2EADD", "chart_3": "#8A7F6E", "chart_4": "#4A4034"},
    ),
    "mainframe": (
        "Mainframe",
        "tech",
        ("VT323",),
        "Straight from the machine room",
        {"background": "#061006", "surface_panel": "#0B1A0C", "border": "#1E4426", "primary_accent": "#33FF66", "primary_soft_tint": "#0F2E16", "text_on_primary": "#061006", "heading_text": "#D8FFE3", "body_text": "#7FBF93", "muted_text": "#4E7F58", "chart_1": "#33FF66", "chart_2": "#28C452", "chart_3": "#1A8A39", "chart_4": "#11331C"},
    ),
    "manuscript": (
        "Manuscript",
        "creative",
        ("EB Garamond",),
        "Slides before print",
        {"background": "#F3EAD8", "surface_panel": "#FAF4E6", "border": "#3B2F23", "primary_accent": "#8E2F3C", "primary_soft_tint": "#EFD9D2", "text_on_primary": "#F3EAD8", "heading_text": "#3B2F23", "body_text": "#5A4C3B", "muted_text": "#93846E", "chart_1": "#8E2F3C", "chart_2": "#B98D3B", "chart_3": "#3B2F23", "chart_4": "#D9C9A8"},
    ),
    "marquee": (
        "Marquee",
        "creative",
        ("Cinzel", "Marcellus"),
        "Make it an occasion",
        {"background": "#0E0D0B", "surface_panel": "#171511", "border": "#4A3F25", "primary_accent": "#D4AF37", "primary_soft_tint": "#2A2415", "text_on_primary": "#0E0D0B", "heading_text": "#F3EAD3", "body_text": "#C9BC9C", "muted_text": "#857B61", "chart_1": "#D4AF37", "chart_2": "#E2C868", "chart_3": "#F0DFA6", "chart_4": "#3A3220"},
    ),
    "mckinsey-style": (
        "McKinsey Style",
        "consulting",
        ("Gelasio", "Arimo"),
        "Answer first, always",
        {"background": "#FFFFFF", "surface_panel": "#F0F4F8", "border": "#D6DEE6", "primary_accent": "#051C2C", "primary_soft_tint": "#E8EEF4", "text_on_primary": "#FFFFFF", "heading_text": "#051C2C", "body_text": "#4E5B66", "muted_text": "#8A97A3", "chart_1": "#051C2C", "chart_2": "#2251FF", "chart_3": "#8FA9BD", "chart_4": "#DDE6EE"},
    ),
    "memo": (
        "Memo",
        "business",
        ("Libre Caslon Text",),
        "Per my last memo",
        {"background": "#FFFEF9", "surface_panel": "#FFFFFF", "border": "#D9D7CE", "primary_accent": "#B3372F", "primary_soft_tint": "#F5E0DE", "text_on_primary": "#FFFFFF", "heading_text": "#232323", "body_text": "#3A3A37", "muted_text": "#6B6B66", "chart_1": "#232323", "chart_2": "#B3372F", "chart_3": "#6B6B66", "chart_4": "#FCE588"},
    ),
    "memphis": (
        "Memphis",
        "marketing",
        ("Baloo 2", "Nunito"),
        "Serious work, unserious style",
        {"background": "#FFF6E9", "surface_panel": "#FFFFFF", "border": "#1D1D1D", "primary_accent": "#FF5D73", "primary_soft_tint": "#FFE3E7", "text_on_primary": "#FFFFFF", "heading_text": "#1D1D1D", "body_text": "#4A4440", "muted_text": "#9A8F85", "chart_1": "#FF5D73", "chart_2": "#2EC4B6", "chart_3": "#FFBF00", "chart_4": "#1D1D1D"},
    ),
    "metro": (
        "Metro",
        "business",
        ("Barlow Condensed", "Barlow"),
        "Mind the gap analysis",
        {"background": "#FFFFFF", "surface_panel": "#F6F6F6", "border": "#1A1A1A", "primary_accent": "#E03A3E", "primary_soft_tint": "#FBE2E2", "text_on_primary": "#FFFFFF", "heading_text": "#1A1A1A", "body_text": "#5A5A5A", "muted_text": "#8A8A8A", "chart_1": "#E03A3E", "chart_2": "#0072BC", "chart_3": "#00A65A", "chart_4": "#F8B500"},
    ),
    "midnight-pitch": (
        "Midnight Pitch",
        "pitch",
        ("Sora", "Inter"),
        "Make investors lean in",
        {"background": "#0A1838", "surface_panel": "#13244D", "border": "#23365F", "primary_accent": "#2E6BFF", "primary_soft_tint": "#142B5C", "text_on_primary": "#FFFFFF", "heading_text": "#FFFFFF", "body_text": "#94A8CC", "muted_text": "#5F73A0", "chart_1": "#2E6BFF", "chart_2": "#22D3EE", "chart_3": "#7FA8FF", "chart_4": "#1E3566"},
    ),
    "monolith": (
        "Monolith",
        "pitch",
        ("Jost", "DM Mono"),
        "Expensive silence",
        {"background": "#0C0C0C", "surface_panel": "#161616", "border": "#2A2A2A", "primary_accent": "#F5F5F3", "primary_soft_tint": "#2E2E2C", "text_on_primary": "#0C0C0C", "heading_text": "#F5F5F3", "body_text": "#C9C9C5", "muted_text": "#8A8A86", "chart_1": "#F5F5F3", "chart_2": "#B5B5B1", "chart_3": "#8A8A86", "chart_4": "#4A4A47"},
    ),
    "notebook": (
        "Notebook",
        "education",
        ("Caveat", "Patrick Hand"),
        "Margins included",
        {"background": "#FFFFFF", "surface_panel": "#F8FAFD", "border": "#BFD7EE", "primary_accent": "#D14545", "primary_soft_tint": "#FBE3E3", "text_on_primary": "#FFFFFF", "heading_text": "#1F2937", "body_text": "#374151", "muted_text": "#9CA3AF", "chart_1": "#FDE047", "chart_2": "#86EFAC", "chart_3": "#BFD7EE", "chart_4": "#E8A0A0"},
    ),
    "oat": (
        "Oat",
        "creative",
        ("Fraunces", "Jost"),
        "Warm neutrals, quiet arches, expensive calm",
        {"background": "#F4EEE4", "surface_panel": "#FBF8F2", "border": "#E3D9C8", "primary_accent": "#6B5644", "primary_soft_tint": "#E8DECF", "text_on_primary": "#FBF8F2", "heading_text": "#3D3328", "body_text": "#5C5142", "muted_text": "#9A8E7A", "chart_1": "#6B5644", "chart_2": "#B59E80", "chart_3": "#CDBBA0", "chart_4": "#8A7355"},
    ),
    "observatory": (
        "Observatory",
        "education",
        ("Cormorant Garamond", "Spectral", "Space Mono"),
        "Data as constellations",
        {"background": "#0B1426", "surface_panel": "#101C33", "border": "#25344F", "primary_accent": "#E3C77B", "primary_soft_tint": "#342E1D", "text_on_primary": "#0B1426", "heading_text": "#E8EDF7", "body_text": "#B8C2D6", "muted_text": "#8A97B2", "chart_1": "#E3C77B", "chart_2": "#E8EDF7", "chart_3": "#8A97B2", "chart_4": "#25344F"},
    ),
    "one-sheet": (
        "One Sheet",
        "creative",
        ("Anton", "Barlow Condensed"),
        "Top of the billing",
        {"background": "#0E0E10", "surface_panel": "#18181C", "border": "#2C2C32", "primary_accent": "#E23B41", "primary_soft_tint": "#2A1416", "text_on_primary": "#FFFFFF", "heading_text": "#F5F3EF", "body_text": "#BDBAB4", "muted_text": "#807D77", "chart_1": "#E23B41", "chart_2": "#F5F3EF", "chart_3": "#807D77", "chart_4": "#3A3A40"},
    ),
    "operator": (
        "Operator",
        "business",
        ("IBM Plex Sans", "IBM Plex Mono"),
        "Status, not stories",
        {"background": "#F8F9FA", "surface_panel": "#FFFFFF", "border": "#E3E7EB", "primary_accent": "#2E90FA", "primary_soft_tint": "#EAF4FF", "text_on_primary": "#FFFFFF", "heading_text": "#23292F", "body_text": "#5C6670", "muted_text": "#6B7480", "chart_1": "#2E90FA", "chart_2": "#12B76A", "chart_3": "#F79009", "chart_4": "#F04438"},
    ),
    "origami": (
        "Origami",
        "creative",
        ("Tenor Sans", "Karla"),
        "Folded, not decorated",
        {"background": "#F6F4EF", "surface_panel": "#FFFFFF", "border": "#DDD8CD", "primary_accent": "#E25C5C", "primary_soft_tint": "#F8DEDE", "text_on_primary": "#FFFFFF", "heading_text": "#2D2A26", "body_text": "#57524B", "muted_text": "#8F897E", "chart_1": "#E25C5C", "chart_2": "#5C7185", "chart_3": "#C44545", "chart_4": "#46586A"},
    ),
    "outrun": (
        "Outrun",
        "marketing",
        ("Orbitron", "Exo 2"),
        "Straight out of 1986",
        {"background": "#1A0B33", "surface_panel": "#2A1548", "border": "#4A2670", "primary_accent": "#FF4FA3", "primary_soft_tint": "#3A1B5C", "text_on_primary": "#FFFFFF", "heading_text": "#FFFFFF", "body_text": "#C9A8E8", "muted_text": "#8E6BB8", "chart_1": "#FF4FA3", "chart_2": "#41E8E0", "chart_3": "#FF7A3C", "chart_4": "#4A2670"},
    ),
    "passepartout": (
        "Passepartout",
        "creative",
        ("Cormorant Garamond", "Tenor Sans"),
        "A gallery on every slide",
        {"background": "#F7F5F0", "surface_panel": "#FFFFFF", "border": "#E3DED3", "primary_accent": "#9A7B4F", "primary_soft_tint": "#EFE7D8", "text_on_primary": "#FFFFFF", "heading_text": "#20201E", "body_text": "#57544E", "muted_text": "#908B81", "chart_1": "#9A7B4F", "chart_2": "#20201E", "chart_3": "#C7B393", "chart_4": "#E3DED3"},
    ),
    "pitch-book": (
        "Pitch Book",
        "finance",
        ("EB Garamond", "Arimo"),
        "Navy rigor, gold rules, dense conviction",
        {"background": "#FFFFFF", "surface_panel": "#F4F6F8", "border": "#D3D9E0", "primary_accent": "#0C2340", "primary_soft_tint": "#E6EBF2", "text_on_primary": "#FFFFFF", "heading_text": "#0C2340", "body_text": "#2B3542", "muted_text": "#66707D", "chart_1": "#0C2340", "chart_2": "#B08D3F", "chart_3": "#5C7EA3", "chart_4": "#A8B2BD"},
    ),
    "polaroid": (
        "Polaroid",
        "marketing",
        ("Playfair Display", "Work Sans"),
        "Pinned to the wall",
        {"background": "#E8E4DC", "surface_panel": "#FFFFFF", "border": "#D3CDC1", "primary_accent": "#D87C72", "primary_soft_tint": "#F4A6A0", "text_on_primary": "#FFFFFF", "heading_text": "#3A352E", "body_text": "#7A7367", "muted_text": "#A39C8F", "chart_1": "#F4A6A0", "chart_2": "#9EC3B0", "chart_3": "#E9C46A", "chart_4": "#B9AFA0"},
    ),
    "pwc-style": (
        "PwC Style",
        "consulting",
        ("Gelasio", "Arimo"),
        "Serif headlines, five warm colors",
        {"background": "#FFFFFF", "surface_panel": "#F5F5F5", "border": "#DEDEDE", "primary_accent": "#D04A02", "primary_soft_tint": "#FAE8DC", "text_on_primary": "#FFFFFF", "heading_text": "#000000", "body_text": "#464646", "muted_text": "#7D7D7D", "chart_1": "#D04A02", "chart_2": "#EB8C00", "chart_3": "#FFB600", "chart_4": "#DEDEDE"},
    ),
    "qbr": (
        "QBR",
        "business",
        ("Inter", "IBM Plex Mono"),
        "Targets, actuals, and the renewal story",
        {"background": "#F6F8FB", "surface_panel": "#FFFFFF", "border": "#D9E1EC", "primary_accent": "#1D4F91", "primary_soft_tint": "#E4EDF8", "text_on_primary": "#FFFFFF", "heading_text": "#0F2344", "body_text": "#33415C", "muted_text": "#66748C", "chart_1": "#1D4F91", "chart_2": "#4F8FD0", "chart_3": "#8AB6E6", "chart_4": "#98A6BA"},
    ),
    "quiz-night": (
        "Quiz Night",
        "seasonal",
        ("Alfa Slab One", "Nunito"),
        "Training, but with buzzers",
        {"background": "#15206B", "surface_panel": "#FFFFFF", "border": "#2B3DA8", "primary_accent": "#F2B01E", "primary_soft_tint": "#1B2A86", "text_on_primary": "#15206B", "heading_text": "#FFFFFF", "body_text": "#D8DCF5", "muted_text": "#9AA3D8", "chart_1": "#F2B01E", "chart_2": "#2BA84A", "chart_3": "#FFFFFF", "chart_4": "#6B79D6"},
    ),
    "runway": (
        "Runway",
        "pitch",
        ("Bricolage Grotesque", "Spline Sans Mono"),
        "Clean, light investor pitch deck",
        {"background": "#FBFAF8", "surface_panel": "#FFFFFF", "border": "#E7E4DE", "primary_accent": "#2F5BFF", "primary_soft_tint": "#E5EBFF", "text_on_primary": "#FFFFFF", "heading_text": "#14151A", "body_text": "#44474F", "muted_text": "#8A8E99", "chart_1": "#2F5BFF", "chart_2": "#14151A", "chart_3": "#9BB0FF", "chart_4": "#C9CDD6"},
    ),
    "scrapbook": (
        "Scrapbook",
        "creative",
        ("Fraunces", "Caveat", "Karla"),
        "Paper, tape, and handwritten charm",
        {"background": "#F6EFE1", "surface_panel": "#FFFCF4", "border": "#D9C9A8", "primary_accent": "#B95C38", "primary_soft_tint": "#F3DDD0", "text_on_primary": "#FFFCF4", "heading_text": "#443627", "body_text": "#574838", "muted_text": "#8A7A63", "chart_1": "#B95C38", "chart_2": "#7D8B6A", "chart_3": "#6F8BA0", "chart_4": "#D9A544"},
    ),
    "seminar": (
        "Seminar",
        "education",
        ("Source Serif 4",),
        "Beamer, but nicer",
        {"background": "#FFFFFF", "surface_panel": "#F4F7FB", "border": "#D8DEE9", "primary_accent": "#1C3F6E", "primary_soft_tint": "#E8EEF7", "text_on_primary": "#FFFFFF", "heading_text": "#1C3F6E", "body_text": "#2B2B2B", "muted_text": "#6B7280", "chart_1": "#1C3F6E", "chart_2": "#2F7D4F", "chart_3": "#B07D2B", "chart_4": "#C9D4E5"},
    ),
    "six-pager": (
        "Six-Pager",
        "business",
        ("Source Sans 3", "Gelasio", "IBM Plex Mono"),
        "The memo that runs the meeting",
        {"background": "#FFFFFF", "surface_panel": "#F6F7F7", "border": "#D5D9D9", "primary_accent": "#FF9900", "primary_soft_tint": "#FFF3DC", "text_on_primary": "#232F3E", "heading_text": "#232F3E", "body_text": "#0F1111", "muted_text": "#565959", "chart_1": "#232F3E", "chart_2": "#FF9900", "chart_3": "#146EB4", "chart_4": "#879596"},
    ),
    "sorbet": (
        "Sorbet",
        "marketing",
        ("Baloo 2", "Quicksand"),
        "Soft pastels, kept tasteful",
        {"background": "#FBF7FB", "surface_panel": "#FFFFFF", "border": "#ECE3F0", "primary_accent": "#9B6FBF", "primary_soft_tint": "#EFE2F6", "text_on_primary": "#FFFFFF", "heading_text": "#4A3A5C", "body_text": "#6A5E76", "muted_text": "#A99FB3", "chart_1": "#9B6FBF", "chart_2": "#F2A6B3", "chart_3": "#8FD0C4", "chart_4": "#F4D58D"},
    ),
    "spark": (
        "Spark",
        "pitch",
        ("Archivo Black", "Archivo"),
        "Bold pink pitch for consumer apps",
        {"background": "#FFFFFF", "surface_panel": "#FFF0F4", "border": "#FFD2DE", "primary_accent": "#FF2E63", "primary_soft_tint": "#FFE0E8", "text_on_primary": "#FFFFFF", "heading_text": "#14080C", "body_text": "#4A2A33", "muted_text": "#9B7B84", "chart_1": "#FF2E63", "chart_2": "#14080C", "chart_3": "#FF85A1", "chart_4": "#FFC2D0"},
    ),
    "syllabus": (
        "Syllabus",
        "education",
        ("Poppins", "DM Mono"),
        "The outline is the design",
        {"background": "#F8F8FC", "surface_panel": "#FFFFFF", "border": "#E4E4F4", "primary_accent": "#4F46E5", "primary_soft_tint": "#EEEEFA", "text_on_primary": "#FFFFFF", "heading_text": "#21213B", "body_text": "#4A4A68", "muted_text": "#8B8BA8", "chart_1": "#4F46E5", "chart_2": "#34D399", "chart_3": "#F59E0B", "chart_4": "#C7C7EE"},
    ),
    "ted-style": (
        "TED Style",
        "education",
        ("Inter",),
        "Say it huge, keep it dark",
        {"background": "#0A0A0A", "surface_panel": "#161616", "border": "#2A2A2A", "primary_accent": "#E62B1E", "primary_soft_tint": "#42120D", "text_on_primary": "#FFFFFF", "heading_text": "#FFFFFF", "body_text": "#E8E8E8", "muted_text": "#9C9C9C", "chart_1": "#E62B1E", "chart_2": "#F5F5F5", "chart_3": "#A6A6A6", "chart_4": "#565656"},
    ),
    "telemetry": (
        "Telemetry",
        "tech",
        ("IBM Plex Sans", "IBM Plex Mono"),
        "Your deck as a dashboard",
        {"background": "#0D1117", "surface_panel": "#161B22", "border": "#30363D", "primary_accent": "#2DD4BF", "primary_soft_tint": "#10322E", "text_on_primary": "#0D1117", "heading_text": "#E6EDF3", "body_text": "#C9D1D9", "muted_text": "#8B949E", "chart_1": "#2DD4BF", "chart_2": "#8B5CF6", "chart_3": "#8B949E", "chart_4": "#30363D"},
    ),
    "term-sheet": (
        "Term Sheet",
        "pitch",
        ("Source Serif 4", "IBM Plex Sans"),
        "The institutional VC framework deck",
        {"background": "#F7F8FA", "surface_panel": "#FFFFFF", "border": "#DDE2EA", "primary_accent": "#16315C", "primary_soft_tint": "#DCE4F2", "text_on_primary": "#FFFFFF", "heading_text": "#0E1B33", "body_text": "#41495A", "muted_text": "#818A9C", "chart_1": "#16315C", "chart_2": "#0E1B33", "chart_3": "#6E86B6", "chart_4": "#C2CDDF"},
    ),
    "ticker": (
        "Ticker",
        "finance",
        ("Share Tech Mono",),
        "Amber on black",
        {"background": "#000000", "surface_panel": "#0D0D0D", "border": "#2A2A2A", "primary_accent": "#FFB000", "primary_soft_tint": "#332300", "text_on_primary": "#000000", "heading_text": "#FFB000", "body_text": "#D6D6D6", "muted_text": "#6E6E6E", "chart_1": "#FFB000", "chart_2": "#00C853", "chart_3": "#FF3B30", "chart_4": "#6E6E6E"},
    ),
    "traction": (
        "Traction",
        "pitch",
        ("Archivo", "JetBrains Mono"),
        "Let the numbers pitch",
        {"background": "#FAFBFC", "surface_panel": "#FFFFFF", "border": "#E4E8EC", "primary_accent": "#0E9F6E", "primary_soft_tint": "#D6F2E6", "text_on_primary": "#FFFFFF", "heading_text": "#111827", "body_text": "#3F4754", "muted_text": "#8A93A2", "chart_1": "#0E9F6E", "chart_2": "#111827", "chart_3": "#5BD0A4", "chart_4": "#C7CDD6"},
    ),
    "trailhead": (
        "Trailhead",
        "education",
        ("Fraunces", "Karla", "Space Mono"),
        "Learning, one mile at a time",
        {"background": "#F5F1E6", "surface_panel": "#FFFFFF", "border": "#C9B086", "primary_accent": "#2F5D46", "primary_soft_tint": "#E3EAE3", "text_on_primary": "#FFFFFF", "heading_text": "#2E2A20", "body_text": "#5A5343", "muted_text": "#8C8470", "chart_1": "#2F5D46", "chart_2": "#D97742", "chart_3": "#C9B086", "chart_4": "#8C8470"},
    ),
    "varsity": (
        "Varsity",
        "seasonal",
        ("Archivo Black", "Barlow"),
        "Wear the colors",
        {"background": "#FBF6EC", "surface_panel": "#FFFFFF", "border": "#7A2E2E", "primary_accent": "#7A2E2E", "primary_soft_tint": "#F6E7C8", "text_on_primary": "#FBF6EC", "heading_text": "#7A2E2E", "body_text": "#3A3A3A", "muted_text": "#8C8577", "chart_1": "#7A2E2E", "chart_2": "#D9A441", "chart_3": "#1E2A4A", "chart_4": "#E8D9BF"},
    ),
    "whiteboard": (
        "Whiteboard",
        "business",
        ("Permanent Marker", "Quicksand", "Kalam"),
        "Fresh from the workshop",
        {"background": "#FDFDFB", "surface_panel": "#FEF08A", "border": "#E3E3DB", "primary_accent": "#2563EB", "primary_soft_tint": "#DBEAFE", "text_on_primary": "#FFFFFF", "heading_text": "#111111", "body_text": "#44443F", "muted_text": "#6B6B66", "chart_1": "#2563EB", "chart_2": "#DC2626", "chart_3": "#16A34A", "chart_4": "#111111"},
    ),
    "wildflower": (
        "Wildflower",
        "education",
        ("EB Garamond", "Karla"),
        "Hand-gathered, loosely arranged",
        {"background": "#F6F3E9", "surface_panel": "#FCFAF3", "border": "#E3DECB", "primary_accent": "#7C8450", "primary_soft_tint": "#E6E8D2", "text_on_primary": "#FCFAF3", "heading_text": "#46492F", "body_text": "#5E5A48", "muted_text": "#9A957E", "chart_1": "#7C8450", "chart_2": "#C27A4E", "chart_3": "#D9B45B", "chart_4": "#A6896B"},
    ),
    "wireframe": (
        "Wireframe",
        "tech",
        ("Inter", "Roboto Mono"),
        "Shipped before the visual design",
        {"background": "#FFFFFF", "surface_panel": "#F9FAFB", "border": "#9CA3AF", "primary_accent": "#2563EB", "primary_soft_tint": "#DBEAFE", "text_on_primary": "#FFFFFF", "heading_text": "#374151", "body_text": "#6B7280", "muted_text": "#9CA3AF", "chart_1": "#2563EB", "chart_2": "#9CA3AF", "chart_3": "#D1D5DB", "chart_4": "#E5E7EB"},
    ),
    "y2k": (
        "Y2K",
        "creative",
        ("Orbitron", "Exo 2", "VT323"),
        "Chrome dreams from the year 2000",
        {"background": "#EEF2FB", "surface_panel": "#FFFFFF", "border": "#C8D2E6", "primary_accent": "#FF3FA4", "primary_soft_tint": "#FFE0F1", "text_on_primary": "#FFFFFF", "heading_text": "#221B45", "body_text": "#3A3F63", "muted_text": "#7C85A8", "chart_1": "#FF3FA4", "chart_2": "#00B8F0", "chart_3": "#8A2BE2", "chart_4": "#9FABC6"},
    ),
}


#: Every upstream PROMPT.md is exactly two paragraphs, and the second is this
#: same chat-UI call to action in all 81 files. It tells the model to stop and
#: ask the user what the deck is about, which would derail an automated
#: generation, so it is dropped at load time. The vendored .md files stay
#: byte-identical to upstream; only what we feed a model is trimmed.
_CHAT_TRAILER = (
    "Use this theme for my slides. Ask me what the presentation is about "
    "first, then apply the theme to every slide."
)


def _load_prompt(theme_id: str) -> str:
    """Read one vendored prompt. Missing/unreadable files degrade to ''."""
    try:
        text = (_PROMPT_DIR / f"{theme_id}.md").read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - vendored files ship with the package
        return ""
    if text.endswith(_CHAT_TRAILER):
        text = text[: -len(_CHAT_TRAILER)].strip()
    return text


def _build() -> dict[str, Theme]:
    built: dict[str, Theme] = {}
    for theme_id, (name, category, fonts, when_to_use, palette) in _META.items():
        _prompt = _load_prompt(theme_id)
        built[theme_id] = Theme(
            id=theme_id,
            name=name,
            category=category,
            fonts=tuple(fonts),
            palette=dict(palette),
            when_to_use=when_to_use,
            prompt_text=_prompt,
            avoid=_parse_avoid(_prompt),
        )
    return built


#: Parsed once, at import. Ordered by theme id.
_THEMES: dict[str, Theme] = _build()


def _norm(text: str) -> str:
    """Lowercase and collapse everything non-alphanumeric to single spaces.

    Padded with spaces so callers can test for whole-token containment with a
    plain ``in`` and not match inside a longer word.
    """
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _build_aliases() -> list[tuple[str, str]]:
    """Phrase -> theme id, longest phrase first so the most specific wins.

    Three tiers of phrase, in the order the contract asks resolve() to try
    them: theme id, display name, then category. Every phrase is normalised the
    same way ``_norm`` normalises the haystack, so 'mckinsey-style',
    'McKinsey Style' and 'mckinsey style' are one key.
    """
    aliases: dict[str, str] = {}

    def add(phrase: str, theme_id: str) -> None:
        key = _norm(phrase).strip()
        # First writer wins: ids are registered before names before categories,
        # so a name that collides with an id never steals it.
        if key and key not in aliases:
            aliases[key] = theme_id

    for theme_id in _THEMES:
        add(theme_id, theme_id)
        # 'mckinsey' should find 'mckinsey-style' without the suffix.
        if theme_id.endswith("-style"):
            add(theme_id[: -len("-style")], theme_id)
    for theme_id, theme in _THEMES.items():
        add(theme.name, theme_id)
    for category, theme_id in _CATEGORY_DEFAULTS.items():
        add(category, theme_id)

    return sorted(aliases.items(), key=lambda kv: (-len(kv[0]), kv[0]))


_ALIASES: list[tuple[str, str]] = _build_aliases()


def _match(text: str | None) -> Theme | None:
    """Longest alias mentioned anywhere in ``text``, or None."""
    if not text or not isinstance(text, str):
        return None
    haystack = _norm(text)
    for phrase, theme_id in _ALIASES:
        if f" {phrase} " in haystack:
            return _THEMES[theme_id]
    return None


def _from_brand(org_brand: dict | None) -> Theme | None:
    """Pull a theme out of an org brand blob, if it names one."""
    if not isinstance(org_brand, dict):
        return None
    for key in ("theme_id", "theme", "deck_theme", "slide_theme", "pptx_theme", "style", "name"):
        value = org_brand.get(key)
        if isinstance(value, str):
            found = get(value) or _match(value)
            if found is not None:
                return found
    return None


def all_themes() -> list[Theme]:
    """Every vendored theme, ordered by id."""
    return list(_THEMES.values())


def get(theme_id: str) -> Theme | None:
    """Exact lookup by id. None when the id is unknown or not a string."""
    if not isinstance(theme_id, str):
        return None
    return _THEMES.get(theme_id.strip().lower())


def resolve(
    *,
    user_text: str | None = None,
    report_theme_name: str | None = None,
    org_brand: dict | None = None,
    agent_default: str | None = None,
) -> Theme:
    """Pick a theme. First tier that matches wins; never returns None, never raises.

    The order is deliberate -- what the user asked for in this turn outranks
    what the report was last saved with, which outranks the org's house brand,
    which outranks the agent's configured fallback:

        user_text -> report_theme_name -> org_brand -> agent_default -> DEFAULT_THEME_ID
    """
    try:
        for candidate in (
            _match(user_text),
            get(report_theme_name) if report_theme_name else None,
            _match(report_theme_name),
            _from_brand(org_brand),
            get(agent_default) if agent_default else None,
            _match(agent_default),
        ):
            if candidate is not None:
                return candidate
    except Exception:  # pragma: no cover - resolve() must never raise
        pass
    return _THEMES[DEFAULT_THEME_ID]


def index_lines() -> str:
    """One line per theme -- '<id>  <category>  <when_to_use>' -- for injection.

    Columns are padded so the model reads it as a table rather than prose.
    """
    id_w = max(len(t.id) for t in _THEMES.values())
    cat_w = max(len(t.category) for t in _THEMES.values())
    return "\n".join(
        f"{t.id.ljust(id_w)}  {t.category.ljust(cat_w)}  {t.when_to_use}"
        for t in _THEMES.values()
    )


def spec_block(theme: Theme) -> str:
    """The full pinned design system for one theme, ready to drop into a prompt."""
    palette = "\n".join(f"  {role}: {theme.palette[role]}" for role in PALETTE_ROLES if role in theme.palette)
    return (
        f"DECK THEME: {theme.name} ({theme.id})\n"
        f"Category: {theme.category}\n"
        f"Use when: {theme.when_to_use}\n"
        f"Fonts: {', '.join(theme.fonts)}\n"
        f"Palette:\n{palette}\n"
        f"\nDesign system (follow it exactly):\n{theme.prompt_text}"
    )
