"""What furniture each deck theme draws for itself.

A theme is two different things. Half of it -- palette and fonts -- a model
applies reliably, because every colour it writes is a colour it chose. The
other half is *structure*: ledger ruling every 28px, a red margin line 80px
from the left, a rotated POSTED stamp, punch holes down the edge of a
notebook page. Measured on live decks, the model applies the first half and
never the second: asked for 'ledger' it produced green serif text on cream --
correct colours -- on a blank white page. Those motifs are dozens of drawn
shapes and a model improvising a deck will not write them.

So the product draws them. This module is the declaration of *what* to draw;
``motifs.paint_theme_furniture`` is the code that draws it.

Every number here is lifted from the theme's OWN vendored prompt text in
``slidespeak/<id>.md``. Nothing is invented. Where a prompt does not say, the
entry says nothing, and the painter draws nothing -- an honest empty layout is
better than furniture the design system never asked for. **All 81 registered
themes are read by hand**; the scanner further down is only the fallback for a
theme the registry gains before anyone has read its prompt.

Coordinates are in **CSS pixels on a 1280x720 canvas**, which is the frame the
vendored prompts are written against ("every 28px", "80px from the left edge").
The painter scales them to the real slide size, so a 4:3 deck gets the same
design at its own proportions.

Layout keys
-----------
``ground``            ``"flat"`` | ``"ruled"`` | ``"gradient"``
``ground_color``      ``'#RRGGBB'`` -- the paper. Painted full-bleed, at the back.
``ground_color_2``    second stop, gradient grounds only.
``gradient_angle``    gradient grounds only; the painter defaults to 270.
``rule_spacing_px``   ruled grounds: distance between horizontal rules.
``rule_color``        ruled grounds: the rule colour. Also the masthead's colour.
``rule_start_px``     ruled grounds: y of the first rule (default = one spacing).
``rule_width_pt``     ruled grounds: rule thickness (default hairline).
``margin_rule``       ``{"x_px": int, "color": hex, "width_pt": float}`` -- one
                      vertical rule running the full slide height.
``masthead``          bool -- newspaper masthead ruling (a heavy rule over a
                      hairline, across the top).
``tracker``           ``{"kind": "squares"|"band", "count": int, "on": hex,
                      "off": hex}`` -- an agenda position indicator, top-right.
``footer``            ``{"left": str|None, "center": str|None, "right": str|None,
                      "color": hex, "size_px": int}``. ``{page}`` and ``{pages}``
                      interpolate.
``footer_rule``       ``"hairline"`` | ``"double"`` | ``None``.
``stamp``             ``{"text": str, "color": hex, "rotation": float}`` -- one
                      rotated outline stamp, once per deck.
``chip``              ``{"text": str, "fill": hex, "color": hex}`` -- a small
                      rounded tag, top-right.
``corner_mark``       ``{"text": str, "color": hex}`` -- tiny caps, top-right.
``accent``            hex -- the colour ornaments draw in, when the theme names
                      one that is not its registry ``primary_accent``.
``ornament``          tuple of painter names; see ``motifs.ORNAMENTS``.
``forbid_boxes``      bool -- the theme's avoid-list rules out filled panels, so
                      the painter must not draw any.
``skip_title_slide``  bool -- the theme specifies a different title slide, so
                      ruling, trackers, footers and chips start at slide 2.
``cover``             ``{"ground_color": hex, "ornament": (...)}`` -- furniture
                      for the title slide only.

Four rules the entries below are written to, because breaking any one of them
puts furniture on a slide its own design system rejects:

1. **Read the avoid-list before adding anything.** ``keynote-minimal`` and
   ``ted-style`` forbid footers, page numbers and logos outright; giving either
   a footer would break the theme, so both are deliberately a ground and
   nothing else.
2. **A threshold is not a prohibition.** ``telemetry`` avoids "corner radii
   above 8px" and ``benchmark`` "corner radii above 4px" -- their own panels are
   correct, so ``forbid_boxes`` stays False. ``forbid_boxes`` is True only where
   the prompt rules panels out (``ledger``, ``drafting-room``, ``wireframe``).
3. **A slot whose text is a placeholder stays empty.** "the deck name", "the
   company", "a source-file name" are things only the deck knows; a footer that
   printed the words would be worse than one that prints the page number alone.
   Literal strings the prompt spells out (``NW-2026-OPS``, ``PRESS START``,
   ``Document classification: Public``) are used verbatim.
4. **Opacity is not expressible on a shape fill**, so a stated opacity is
   resolved into the colour it actually reads as over that theme's own ground
   -- the same substitution ``motifs._orn_star_field`` documents. Each such
   colour carries the arithmetic in its comment.

Read a layout through :func:`layout_for`, never by indexing ``LAYOUTS``
directly -- an unknown id has to answer with the empty layout, not raise.
"""

from __future__ import annotations

import re

from . import pptx_themes

__all__ = ["LAYOUTS", "EMPTY_LAYOUT", "CANVAS_W", "CANVAS_H", "layout_for"]

#: The canvas the vendored prompts describe. Every ``*_px`` value is relative
#: to this and the painter rescales to the real slide.
CANVAS_W = 1280
CANVAS_H = 720

#: What an unknown theme gets. Painting nothing is a valid outcome.
EMPTY_LAYOUT: dict = {"ground": "flat", "ornament": ()}


# =============================================================================
# Hand-authored layouts -- one per registered theme, in id order
#
# Each of these was read out of the theme's own prompt text, sentence by
# sentence. The comment above each entry quotes the part of the prompt the
# numbers came from, so the next reader can check the derivation rather than
# trusting it. Where an entry is a ground and nothing else, the comment says
# which motif the prompt describes and why the vocabulary cannot express it --
# an unexplained empty entry is indistinguishable from an unread one.
# =============================================================================

_HAND: dict[str, dict] = {
    # "Footer on every interior slide: 10px #66646e, copyright left, page
    #  number right. Title slide: black, giant purple >."
    #
    # The copyright holder is the deck's, not the theme's, so that slot stays
    # empty. The giant > and the agenda hairlines are the model's.
    "accenture-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {"right": "{page}", "color": "#66646E", "size_px": 10},
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#000000"},
        "ornament": (),
    },
    # "a HUD row at the top of every slide, '1UP 4200' left in #FF4365 and
    #  'HI-SCORE 9999' right in #2DE2E6 ... a centered 'PRESS START' footer in
    #  white at 50 percent opacity."
    #
    # The painter's corner mark is the top-RIGHT slot, so it takes the
    # HI-SCORE half; there is no top-left slot for the 1UP half.
    # #86868C is white at 50% over #0D0D1A.
    "arcade": {
        "ground": "flat",
        "ground_color": "#0D0D1A",
        "corner_mark": {"text": "HI-SCORE 9999", "color": "#2DE2E6"},
        "footer": {"center": "PRESS START", "color": "#86868C"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Title slide: the sunburst rising behind a Cinzel title ... wrapped in
    #  the double-rule frame with 12px L-shaped gold corner brackets. Footer: a
    #  full-width #6F5B30 hairline, the deck name in Marcellus uppercase
    #  #948E76 ..., page number in Cinzel at far right."
    #
    # The sunburst is a conic gradient no shape expresses. The frame is the
    # title slide's, so it is declared on the cover only.
    "art-deco": {
        "ground": "flat",
        "ground_color": "#0E1512",
        "footer": {"right": "{page}", "color": "#948E76"},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ornament": ("double_hairline_border",)},
        "accent": "#6F5B30",
        "ornament": (),
    },
    # "Background: warm paper #F4F1EA on every slide ... Strictly avoid: ...
    #  rounded cards ..."
    #
    # The drafting dimension ticks underline a heading whose y only the model
    # knows, and the plate column is content. Ground and a prohibition.
    "atelier": {
        "ground": "flat",
        "ground_color": "#F4F1EA",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "A solid 8px vertical blue (#1570EF) bar runs down the entire left edge
    #  of every slide ... closes with a thin gray footer rule carrying the deck
    #  name on the left and the page number on the right."
    #
    # 8px is 6pt on this canvas. The dotted top-right pattern is not a motif
    # the painter has.
    "atlas": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "margin_rule": {"x_px": 0, "color": "#1570EF", "width_pt": 6},
        "footer": {"right": "{page}", "color": "#98A2B3"},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm cream (#F7F2E9) ... The signature motif is the arch."
    #
    # The arch, its echo outline and the trios of arches are shapes with no
    # painter. No footer, no rule position: ground only, honestly.
    "atrium": {
        "ground": "flat",
        "ground_color": "#F7F2E9",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: deep indigo-black #0B0B16, with a wide aurora gradient
    #  sweeping from violet #7C5CFF ... eyebrows, labels, slide numbers and
    #  metric captions in Spline Sans Mono, 10 to 12px ... in muted #807CA6."
    "aurora": {
        "ground": "gradient",
        "ground_color": "#0B0B16",
        "ground_color_2": "#7C5CFF",
        "gradient_angle": 270,
        "footer": {"right": "{page}", "color": "#807CA6", "size_px": 11},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#7C5CFF",
        "ornament": (),
    },
    # "Footer: 1px #d9d9d9 rule near the bottom, 'Source:' and 'Note:' lines in
    #  10px #666666 bottom-left, page number bottom-right ... every data slide
    #  carries a source line."
    "bain-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {
            "left": "Source: team analysis",
            "right": "{page}",
            "color": "#666666",
            "size_px": 10,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "a 3px black rule across the top of every slide carrying small uppercase
    #  metadata (deck name left, slide number right)."
    #
    # That is the masthead painter: a 3px rule with a metadata row hanging
    # under it. It also closes the row with a hairline, which this prompt does
    # not ask for -- the nearest honest thing, and the alternative is drawing
    # the theme's most-stated element not at all. The 3-column grid is NOT
    # taken: ``margin_rule`` draws one vertical line and the grid needs two.
    "basel": {
        "ground": "flat",
        "ground_color": "#F4F1EA",
        "masthead": True,
        "rule_color": "#111111",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a tiny source-file name bottom-left, a centered green small-caps
    #  'Gelasio' wordmark in the footer and a plain page number bottom-right."
    #
    # The file name and the wordmark are the deck's own; the page number is
    # not. The Agenda tracker here is a whole slide, not furniture -- the same
    # call made for mckinsey-style's Contents slide.
    "bcg-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {"right": "{page}", "color": "#9A9A9A"},
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "Background: warm off-white (#FAFAF9) ... Strictly avoid: ... corner
    #  radii above 4px."
    #
    # ★A THRESHOLD, not a prohibition: this theme's own chips, tints and
    # quartile bars are panels, so ``forbid_boxes`` stays False. The
    # comparison matrix is a table, which is content.
    "benchmark": {
        "ground": "flat",
        "ground_color": "#FAFAF9",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Every slide is one full-bleed solid color, rotating in this order: red
    #  #FF3D2E ... a tiny brand line bottom-left, 12px uppercase with 0.22em
    #  tracking, reading 'NORTHWIND · SUMMER 2026' ... nothing else on the
    #  canvas."
    #
    # The rotation is per-slide and the painter has one ground, so it takes the
    # first colour of the stated order. "Nothing else on the canvas" is what
    # forbids boxes here.
    "billboard": {
        "ground": "flat",
        "ground_color": "#FF3D2E",
        "footer": {
            "left": "NORTHWIND · SUMMER 2026",
            "color": "#FFFFFF",
            "size_px": 12,
        },
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Top-right: an agenda tracker of six 8px squares, the current slide
    #  filled navy, the rest outlined #D5DCE4. Footer on every slide: hairline
    #  rule, 'Source: team analysis' left, page number right."
    "boardroom": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "tracker": {"kind": "squares", "count": 6, "on": "#1F3A5F", "off": "#D5DCE4"},
        "footer": {
            "left": "Source: team analysis",
            "right": "{page}",
            "color": "#5B6B7E",
            "size_px": 9,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "Every slide opens with a masthead: a 3px black rule, then a row with the
    #  volume and issue number ... closed by a thin rule."
    #
    # The painter draws the two rules. The words between them are the model's
    # -- drawing them here would duplicate whatever it writes. The 2-or-3
    # justified columns are NOT taken: their dividers are full-height verticals
    # that would cut straight through the masthead, and the count is not fixed.
    "broadsheet": {
        "ground": "flat",
        "ground_color": "#FAF7F0",
        "masthead": True,
        "rule_color": "#1A1A1A",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: pale bubblegum #FBEAF4 on every slide, with content sitting
    #  on pure white panels."
    #
    # Sparkles, butterflies and the chrome gradient bar are per-element, not
    # slide furniture.
    "bubblegum": {
        "ground": "flat",
        "ground_color": "#FBEAF4",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: dark green #2A3B2F inside a 10px tan wood frame #8B6B4A ...
    #  Strictly avoid: ... crisp solid borders inside the board ... rounded UI
    #  cards."
    #
    # The wood frame is a 10px band the painter cannot draw without a border
    # key; the dashed chalk rules are dashed, which no motif here is.
    "chalkboard": {
        "ground": "flat",
        "ground_color": "#2A3B2F",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: pure white (#FFFFFF) ... action titles as full sentences in
    #  navy with a hairline rule beneath."
    #
    # That rule sits under a title whose height the model chooses. The chevron
    # banners, gantt and phase-gate diamonds are exhibits.
    "chevron": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Footer: hairline #e6dcc4, the deck name in 11px small caps, the page
    #  number in a 22px gold-outlined circle. Title slide: a centered keylined
    #  card ..."
    #
    # The painter draws one footer colour, so the muted #6f7d70 the theme uses
    # for small text carries both the rule and the number; the cream hairline
    # would be invisible as text.
    "christmas": {
        "ground": "flat",
        "ground_color": "#FAF6EC",
        "footer": {"right": "{page}", "color": "#6F7D70", "size_px": 11},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "solid black (#000000) letterbox bars, 90px tall, across the top and
    #  bottom of every slide, separated from the center band by 1px white rules
    #  at 20% opacity. Left edge: a vertical film strip, a 44px wide dark strip
    #  (#141414)."
    #
    # Two rules 90px from the top and bottom of a 720px canvas is a ruling of
    # spacing 540 starting at 90 -- exactly two lines, at 90 and 630. #3B3B3B
    # is white at 20% over #0A0A0A. 44px is 33pt. The sprocket holes are ten
    # rounded rectangles the vocabulary has no key for.
    "cinema": {
        "ground": "ruled",
        "ground_color": "#0A0A0A",
        "rule_spacing_px": 540,
        "rule_start_px": 90,
        "rule_color": "#3B3B3B",
        "rule_width_pt": 0.75,
        "margin_rule": {"x_px": 0, "color": "#141414", "width_pt": 33},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: PCB green #0E4D3A on every slide ... stray silkscreen labels
    #  like 'R42' and 'TP3' ... Strictly avoid: ... rounded corners beyond 3px."
    #
    # ★A THRESHOLD again: the IC chip cards are this theme's own panels.
    # #92AFA6 is the silkscreen white-at-55% over the PCB green.
    "circuit": {
        "ground": "flat",
        "ground_color": "#0E4D3A",
        "corner_mark": {"text": "TP3", "color": "#92AFA6"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#D9A45B",
        "ornament": (),
    },
    # "Background: warm paper (#EFEBE3) ... Strictly avoid: ... clean
    #  rectangles for colored shapes ... perfectly straight alignment."
    #
    # Every shape the painter draws is a clean rectangle, so the only honest
    # additions here are the paper and the prohibition.
    "collage": {
        "ground": "flat",
        "ground_color": "#EFEBE3",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: blush #FBF1F2 on every slide, with cards and placards lifted
    #  onto pure white."
    #
    # The bow, the scalloped trims and the heart nodes are SVG paths.
    "coquette": {
        "ground": "flat",
        "ground_color": "#FBF1F2",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a thin antique-gold keyline framing the page ... a recurring signature
    #  motif of a laurel wreath, classical hairline rules and an engraved gold
    #  frame ... keep the antique gold #B08A3E as the only metal, used for the
    #  page keyline."
    #
    # The painter's page frame is a double hairline; this prompt asks for a
    # keyline plus an engraved frame, so the doubled form is the nearest thing
    # it draws. The laurel is an SVG stroke.
    "dark-academia": {
        "ground": "flat",
        "ground_color": "#1E1A17",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#B08A3E",
        "ornament": ("double_hairline_border",),
    },
    # "Title slide: full-bleed black ... Footer on every content slide, in tiny
    #  gray text: 'Copyright (c) 2026 Company. All rights reserved.'
    #  bottom-left and 'Client | Engagement | page' bottom-right."
    #
    # Both strings are quoted from the prompt verbatim, placeholders included.
    "deloitte-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {
            "left": "Copyright (c) 2026 Company. All rights reserved.",
            "right": "Client | Engagement | {page}",
            "color": "#97999B",
        },
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#000000"},
        "ornament": (),
    },
    # "Background: pure white #FFFFFF ... Top-left of every slide: a tiny 12px
    #  gray #9B9B9B uppercase label naming the slide."
    #
    # That label is top-LEFT and the painter's corner mark is top-right; moving
    # it would put it where this theme's hard left margin says nothing goes.
    "demo-day": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: drafting blue (#173A66) covered edge to edge with a fine
    #  graph-paper grid (thin lines at about 6 percent white opacity every
    #  24px, heavier lines every 120px) ... Strictly avoid: solid color fills
    #  other than hatching."
    #
    # #25466F is white at 6% over the drafting blue. Only the horizontal half
    # of the grid is drawable -- ``margin_rule`` is a single vertical -- and
    # the 120px heavier pass needs a second spacing the vocabulary has not got.
    "drafting-room": {
        "ground": "ruled",
        "ground_color": "#173A66",
        "rule_spacing_px": 24,
        "rule_start_px": 24,
        "rule_color": "#25466F",
        "rule_width_pt": 0.75,
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Every slide carries a border frame: a 1.5px sepia #4A3B28 rectangle
    #  inset about 20px with a thinner inner rule ... Strictly avoid: ...
    #  rounded cards."
    #
    # An outer rule with a thinner inner one is the double-hairline border; the
    # painter's insets are 24 and 30 rather than this theme's 20. The compass
    # rose, dashed route and lat/long ticks have no painter.
    "expedition": {
        "ground": "flat",
        "ground_color": "#F0E6D2",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "accent": "#4A3B28",
        "ornament": ("double_hairline_border",),
    },
    # "Every content slide ends with a 1px #4A4A57 footer rule, then 10px
    #  #747480 text: page number left, deck title right."
    #
    # The deck title is the deck's. The yellow beam is a skewed parallelogram.
    "ey-style": {
        "ground": "flat",
        "ground_color": "#2E2E38",
        "footer": {"left": "{page}", "color": "#747480", "size_px": 10},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "Background: kraft brown #D7BC94, bare. All content sits on white
    #  #FFFFFF index cards ... printed with faint blue ruled lines."
    #
    # ★The ruling is on the CARDS, not on the slide. A ruled ground here would
    # print blue lines across the kraft, which the prompt calls bare.
    "field-notes": {
        "ground": "flat",
        "ground_color": "#D7BC94",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Every slide closes with a 1px #4a3b73 footer rule, deck name left, page
    #  number right, 12px #a99bc7, one tiny bat centered on the rule."
    #
    # "Every slide" includes the title one, so nothing is skipped. Cobwebs,
    # bats and the moon are silhouettes.
    "halloween": {
        "ground": "flat",
        "ground_color": "#1C1030",
        "footer": {"right": "{page}", "color": "#A99BC7", "size_px": 12},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Every slide carries a footer with engagement code NW-2026-OPS left and a
    #  two digit page number right."
    #
    # The engagement code is spelled out, so it is used verbatim. Harvey balls
    # are pie wedges and belong to the exhibit.
    "harvey": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {"left": "NW-2026-OPS", "right": "{page}", "color": "#8A97A8"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: soft cream #FBF4EC across slides ... thin hairline rules in
    #  #ECDFD0 to divide sections."
    #
    # Section dividers sit wherever the sections do.
    "hearth": {
        "ground": "flat",
        "ground_color": "#FBF4EC",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "one round faded stamp outline, two concentric circles with curved
    #  lettering at about 50 percent opacity, slightly rotated in a corner ...
    #  Strictly avoid: ... rounded corners on cards."
    #
    # Two thin concentric circles is the monogram seal; the painter places it
    # top-centre rather than in a corner, which is the one thing about it that
    # is the painter's and not the prompt's. Panels here are square-cornered
    # and allowed, so boxes are not forbidden.
    "herbarium": {
        "ground": "flat",
        "ground_color": "#FBF9F2",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#5A7048",
        "ornament": ("monogram_seal",),
    },
    # "Background: near-white #FAFAFC ... one holographic gradient ... large
    #  soft blobs blurred about 60px at 45 percent opacity."
    #
    # A blurred blob is not a shape fill, and the gradient is a four-stop
    # 120-degree sweep the painter's two-stop linear fill would misreport.
    "holo": {
        "ground": "flat",
        "ground_color": "#FAFAFC",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "every slide carries the signature gradient, a soft radial glow of
    #  #2e3138 centered at the lower middle fading to near-black #0a0a0c at the
    #  corners; never flat black, never light ... no header bars, no footers,
    #  no page numbers, no dates, no logos anywhere."
    #
    # ★That last clause is why this entry is a ground and nothing else.
    "keynote-minimal": {
        "ground": "gradient",
        "ground_color": "#2E3138",
        "ground_color_2": "#0A0A0C",
        "gradient_angle": 270,
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Every content slide footer: a 1px #d8dfe9 rule 40px from the bottom,
    #  'Document classification: Public' left, page number right, 9px #697280."
    "kpmg-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {
            "left": "Document classification: Public",
            "right": "{page}",
            "color": "#697280",
            "size_px": 9,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "pale green (#F4F7F0) ruled with horizontal lines every 28px in #D9E4D2
    #  ... exactly one vertical red margin line (#C75146, 2px, about 65%
    #  opacity) running the full slide height 80px from the left edge ... One
    #  rotated outline stamp per deck reading 'POSTED · Q3 2026', 3px #C75146
    #  border, rotated about -9 degrees."
    #
    # Nothing in this prompt exempts the title slide, so the ruling runs from
    # slide 1. The stamp is explicitly *per deck*, so the painter places it
    # once.
    "ledger": {
        "ground": "ruled",
        "ground_color": "#F4F7F0",
        "rule_spacing_px": 28,
        "rule_color": "#D9E4D2",
        "rule_width_pt": 0.75,
        "margin_rule": {"x_px": 80, "color": "#C75146", "width_pt": 1.5},
        "stamp": {"text": "POSTED", "color": "#C75146", "rotation": -9},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "(1) a double hairline border on every slide, one 1px gold #B49A5B line
    #  inset 24px and a second 1px line inset 30px; (2) a circular monogram
    #  seal, two thin concentric gold circles about 96px wide ... placed top
    #  center on the title and closing slides; (3) small tilde-style flourish
    #  dividers, a thin gold wave about 70px wide."
    "letterhead": {
        "ground": "flat",
        "ground_color": "#FCF9F1",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": ("double_hairline_border",),
        "cover": {"ornament": ("double_hairline_border", "monogram_seal")},
        "accent": "#B49A5B",
    },
    # "Background: cool near-white (#F7F9FF) ... Cards: white, 16px rounded
    #  corners, very soft shadows."
    #
    # XP bars, hex badges and streak flames are per-element.
    "level-up": {
        "ground": "flat",
        "ground_color": "#F7F9FF",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a small revision stamp like 'DRAFT 3 . 06.19.2026' ... A Courier
    #  page-number footer like '6 / 6' anchors the lower corner like a script
    #  page. Down the left margin of every page run three small, evenly spaced
    #  punch holes, recessed circles in the paper ... Strictly avoid: ...
    #  rounded cards ... decorative shapes other than the binding holes and 1px
    #  rules."
    #
    # The date in the stamp is the deck's, so only 'DRAFT 3' is drawn, and no
    # rotation is stated so none is applied.
    "logline": {
        "ground": "flat",
        "ground_color": "#F3EFE7",
        "footer": {"right": "{page} / {pages}", "color": "#8C867A"},
        "stamp": {"text": "DRAFT 3", "color": "#B23A2E", "rotation": 0},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": ("punch_holes",),
    },
    # "Background: warm near-black #1A1714, with panels on #262019 and 1px
    #  hairlines in #3B332A ... Reserve terracotta amber #CC7A4D as the single
    #  accent: one hairline rule, one tone word, or one swatch per slide."
    #
    # One rule per slide, wherever the layout puts it.
    "lookbook": {
        "ground": "flat",
        "ground_color": "#1A1714",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#CC7A4D",
        "ornament": (),
    },
    # "Every slide opens with a status bar: 'SYS://DECK/<SLIDE-NAME>' on the
    #  left, 'TTY1 · 09:41 · READY' on the right."
    #
    # The left half names the slide, which is the model's. ★The scanline
    # texture is NOT taken: the prompt states no spacing, and the paper-grain
    # ornament is random noise rather than horizontal lines.
    "mainframe": {
        "ground": "flat",
        "ground_color": "#061006",
        "corner_mark": {"text": "TTY1 · 09:41 · READY", "color": "#7FBF93"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "aged paper #F3EAD8 with a subtle vignette darkening toward the edges, a
    #  radial gradient reaching rgba(59,47,35,0.16) at the rim. Frame every
    #  slide with a double rule: a 1.5px ink #3B2F23 border inset 16px, a 1px
    #  old gold #B98D3B border inset 26px ... an italic folio number centered
    #  at the foot of every slide."
    #
    # #D5CCBB is rgba(59,47,35,0.16) over the paper. The painter's frame is one
    # colour, so it takes the gold of the inner rule.
    "manuscript": {
        "ground": "gradient",
        "ground_color": "#F3EAD8",
        "ground_color_2": "#D5CCBB",
        "gradient_angle": 270,
        "footer": {"center": "{page}", "color": "#3B2F23"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#B98D3B",
        "ornament": ("double_hairline_border",),
    },
    # "Every slide is perfectly symmetric and center-aligned, wrapped in a
    #  double frame: a 2px gold border inset about 20px from the edge, and a
    #  second 1px gold border about 14px inside it."
    #
    # The sunburst of rays is a conic fan with no painter.
    "marquee": {
        "ground": "flat",
        "ground_color": "#0E0D0B",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "accent": "#D4AF37",
        "ornament": ("double_hairline_border",),
    },
    # "Title slide: full-bleed deep navy (#051C2C) background, a motif of thin
    #  electric blue horizontal lines of varying length ... Footer on every
    #  slide: 8px footnotes and 'Source: ...' bottom-left, 'Company | page
    #  number' bottom-right, above nothing but a hairline rule."
    #
    # The Contents tracker is a whole slide of its own, and the rule under the
    # action title sits at a y only the model knows. Neither is furniture.
    "mckinsey-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {
            "left": "Source: team analysis",
            "right": "Page {page}",
            "color": "#4E5B66",
            "size_px": 8,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#051C2C", "ornament": ("title_line_motif",)},
        "ornament": (),
    },
    # "a thin double rule, two 1px charcoal lines 1px apart, under every header
    #  ... Footer: PRIVILEGED & CONFIDENTIAL in 8px tracked caps with a page
    #  count."
    #
    # The header's double rule sits directly under a memo block whose height is
    # the model's business, so the painter draws its double rule where it does
    # know the position: above the footer.
    "memo": {
        "ground": "flat",
        "ground_color": "#FFFEF9",
        "footer": {
            "left": "PRIVILEGED & CONFIDENTIAL",
            "right": "{page} of {pages}",
            "color": "#232323",
            "size_px": 8,
        },
        "footer_rule": "double",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: cream (#FFF6E9) ... Strictly avoid: ... thin hairlines."
    #
    # ★That clause rules out a footer rule, and the prompt states no footer.
    # Every Memphis motif is a rotated block with a hard offset shadow.
    "memphis": {
        "ground": "flat",
        "ground_color": "#FFF6E9",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: pure white (#FFFFFF) ... Strictly avoid: ... thin or wavy
    #  lines."
    #
    # Route lines, station dots and interchange rings are the exhibit itself.
    "metro": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: deep navy (#0A1838) with two large, heavily blurred glowing
    #  orbs in opposite corners, one blue (#2E6BFF) ... at roughly 20 percent
    #  opacity ... Footer on every slide: a thin white rule at 8 percent
    #  opacity with the company name left and 'CONFIDENTIAL' right."
    #
    # #112960 is the blue orb at 20% over the navy; the painter's linear fade
    # is the nearest thing to a corner glow, the same substitution
    # keynote-minimal's radial glow takes.
    "midnight-pitch": {
        "ground": "gradient",
        "ground_color": "#112960",
        "ground_color_2": "#0A1838",
        "gradient_angle": 270,
        "footer": {"right": "CONFIDENTIAL", "color": "#5F73A0"},
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "The only structure: single 1px horizontal rules in white at 20 percent
    #  opacity, one beneath a small header row (brand name left, slide number
    #  right), at most one more above a footer line."
    #
    # The footer line's own text is not stated, so the entry declares the rule
    # and no words. #3D3D3D is white at 20% over #0C0C0C. "The only structure"
    # is what forbids boxes.
    "monolith": {
        "ground": "flat",
        "ground_color": "#0C0C0C",
        "footer": {"color": "#3D3D3D"},
        "footer_rule": "hairline",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "white #FFFFFF with 1px horizontal ruled lines in pale blue #BFD7EE every
    #  26px, starting about 70px from the top; a 2px vertical red margin line
    #  #E8A0A0 at 90px from the left; three gray punch holes about 26px wide
    #  spaced down the left edge."
    "notebook": {
        "ground": "ruled",
        "ground_color": "#FFFFFF",
        "rule_spacing_px": 26,
        "rule_start_px": 70,
        "rule_color": "#BFD7EE",
        "rule_width_pt": 0.75,
        "margin_rule": {"x_px": 90, "color": "#E8A0A0", "width_pt": 1.5},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": ("punch_holes",),
    },
    # "Every slide sits on an oat background #F4EEE4 ... Strictly avoid: ...
    #  rounded card stacks ... decorative ornament or flourishes."
    #
    # The arch is an SVG stroke, and the avoid-list refuses ornament outright.
    "oat": {
        "ground": "flat",
        "ground_color": "#F4EEE4",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a radial gradient from deep blue-black #0B1426 at upper center to
    #  #050810 at the edges, scattered with about 24 tiny white star dots of 1
    #  to 2px ... one or two large partial orbit arcs, thin dashed circles
    #  below 20 percent opacity, bleeding off the corners. Footer: tiny 'Space
    #  Mono' metadata including a latitude reading."
    "observatory": {
        "ground": "gradient",
        "ground_color": "#0B1426",
        "ground_color_2": "#050810",
        "gradient_angle": 270,
        "footer": {"left": "LAT 51.4769 N", "color": "#8A97B2", "size_px": 9},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": ("star_field", "orbit_arcs"),
        "accent": "#E3C77B",
    },
    # "Background: near-black #0E0E10 on every slide, deepened with built-in
    #  CSS vignettes (a soft radial glow behind the title, a darker linear
    #  gradient toward the bottom edge) ... Strictly avoid: ... gradients in
    #  any color other than black and crimson ... rounded card containers."
    #
    # The one gradient this theme allows itself is black into black, which is
    # exactly what a two-stop fade from #0E0E10 to #000000 is.
    "one-sheet": {
        "ground": "gradient",
        "ground_color": "#0E0E10",
        "ground_color_2": "#000000",
        "gradient_angle": 270,
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: light gray (#F8F9FA) with white (#FFFFFF) cards ... Header
    #  on every slide: title left, mono reference code right, hairline rule
    #  beneath."
    #
    # The reference code is the report's, not the theme's -- no literal to use.
    "operator": {
        "ground": "flat",
        "ground_color": "#F8F9FA",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm paper #F6F4EF ... Thin 1px dashed gray (#C9C3B8) fold
    #  lines extend horizontally or vertically from shape vertices into empty
    #  space."
    #
    # A fold line starts at a vertex of a shape the model draws, so its
    # position is not the theme's to declare.
    "origami": {
        "ground": "flat",
        "ground_color": "#F6F4EF",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: a vertical gradient sky from deep violet (#1A0B33) through
    #  magenta (#8E2470) to sunset orange (#FF7A3C), ending at a horizon line
    #  about 70 percent down the slide."
    #
    # The painter writes two stops, so the sweep runs violet to sunset and the
    # magenta mid-stop is lost. ★The perspective grid, the horizon and the
    # striped sun are below that horizon and have no painter -- and the stars
    # are NOT taken, because the star-field ornament scatters over the whole
    # slide including the floor, where this theme has no sky.
    "outrun": {
        "ground": "gradient",
        "ground_color": "#1A0B33",
        "ground_color_2": "#FF7A3C",
        "gradient_angle": 270,
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: gallery warm-white #F7F5F0 on every slide ... Strictly
    #  avoid: ... drop shadows, rounded cards ... heavy borders, and any
    #  decorative ornament around the prints."
    #
    # The mat, its keyline and the placard belong to each print.
    "passepartout": {
        "ground": "flat",
        "ground_color": "#F7F5F0",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "STRICTLY CONFIDENTIAL sits top-right in 8px uppercase gray (#66707d) ...
    #  every content slide a footer: 1px #d3d9e0 top border, small-caps
    #  wordmark, 7px disclaimer, page number ... Cover: full-bleed navy
    #  (#0c2340)."
    "pitch-book": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "corner_mark": {"text": "STRICTLY CONFIDENTIAL", "color": "#66707D"},
        "footer": {
            "left": "Confidential  ·  not for distribution",
            "right": "{page}",
            "color": "#66707D",
            "size_px": 8,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#0C2340"},
        "ornament": (),
        "accent": "#B08D3F",
    },
    # "Background: warm wall #E8E4DC ... Signature device: polaroid cards ...
    #  Each polaroid is rotated a different few degrees."
    #
    # Every element of this theme is a rotated card, which is content.
    "polaroid": {
        "ground": "flat",
        "ground_color": "#E8E4DC",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Footer on every content slide: 'Company | page number' in tiny gray text
    #  bottom-left, with 8px gray source and footnote lines directly above ...
    #  Title slide: small serif date top-left."
    #
    # The company is the deck's; the page number is not.
    "pwc-style": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {"right": "{page}", "color": "#7D7D7D", "size_px": 8},
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "a soft-blue #e4edf8 chip top-right with the quarter tag in #1d4f91 11px
    #  caps, and a 1px #d9e1ec rule; footer: a 10px #66748c line, account name
    #  left, 'Confidential' center, page number right ... Title slide is the
    #  one dark moment: full-bleed #1d4f91."
    "qbr": {
        "ground": "flat",
        "ground_color": "#F6F8FB",
        "chip": {"text": "QBR", "fill": "#E4EDF8", "color": "#1D4F91"},
        "footer": {
            "left": "Account review",
            "center": "Confidential",
            "right": "{page}",
            "color": "#66748C",
            "size_px": 10,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#1D4F91"},
        "ornament": (),
    },
    # "Background: deep game-board blue (#15206B) with a subtle radial
    #  spotlight, a lighter blue glow (#2B3DA8) centered at the top edge and
    #  fading out by 60 percent of the slide height ... Strictly avoid: ...
    #  gradients other than the single spotlight, rounded corners beyond 12px."
    #
    # The one gradient this theme allows is this one. ★A THRESHOLD again on the
    # corner radii, so its own tiles and pills stand.
    "quiz-night": {
        "ground": "gradient",
        "ground_color": "#2B3DA8",
        "ground_color_2": "#15206B",
        "gradient_angle": 270,
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "small labels, eyebrows, slide numbers and metric captions in 'Spline
    #  Sans Mono' at 10 to 12px ... muted gray #8A8E99 ... Draw 1px rules in
    #  #E7E4DE to separate sections."
    "runway": {
        "ground": "flat",
        "ground_color": "#FBFAF8",
        "footer": {"right": "{page}", "color": "#8A8E99", "size_px": 11},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm cream (#f6efe1) with a subtle paper grain ... never a
    #  flat fill ... Footer: a washi scrap with the deck title in Caveat 16px
    #  bottom left, the page number in a #f3ddd0 round sticker bottom right."
    #
    # ★The one theme in the catalogue that asks for grain outright, which is
    # the one motif shapes cannot express and Pillow can.
    "scrapbook": {
        "ground": "flat",
        "ground_color": "#F6EFE1",
        "footer": {"right": "{page}", "color": "#8A7A63", "size_px": 16},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": ("paper_grain",),
    },
    # "Near the bottom of relevant slides, a thin 180px footnote rule with 9px
    #  'Source Serif 4' footnotes, then a footer with the course code and
    #  lecture number left and the slide number right in #6B7280."
    #
    # The course code is the lecture's. The footnote rule is 180px wide and the
    # painter's is full-width, so it is left out rather than drawn wrong; the
    # navy header bar is a 40px filled band with no key.
    "seminar": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {"right": "{page}", "color": "#6B7280", "size_px": 9},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a memo header on every page, small-caps document slug top-left, date and
    #  page marker like 'p. 3 / 6' top-right in #565959 at 11px over a 1px
    #  #d5d9d9 hairline, plus a matching footer hairline with the doc owner
    #  left and 'Confidential: internal narrative' right ... Strictly avoid:
    #  ... cutting page metadata like page counts, author and date."
    #
    # The page marker is stated top-right, where the painter has no
    # interpolating slot, so it moves into the footer the theme also has -- the
    # avoid-list makes losing the page count the worse of the two errors.
    "six-pager": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "footer": {
            "left": "p. {page} / {pages}",
            "right": "Confidential: internal narrative",
            "color": "#565959",
            "size_px": 11,
        },
        "footer_rule": "hairline",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": (),
    },
    # "Background: a barely-there lilac wash #FBF7FB on every slide ...
    #  Strictly avoid: ... hard rectangular cards, and harsh outlines."
    #
    # Every panel the painter draws is a hard rectangle.
    "sorbet": {
        "ground": "flat",
        "ground_color": "#FBF7FB",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: white #FFFFFF on most slides, with full-bleed pink-red
    #  #FF2E63 fields on the cover and one or two other slides."
    #
    # The painter can restate the ground on the cover; the "one or two other
    # slides" are the model's choice and stay white here.
    "spark": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "forbid_boxes": False,
        "skip_title_slide": True,
        "cover": {"ground_color": "#FF2E63"},
        "ornament": (),
    },
    # "Background: pale lavender white (#F8F8FC) ... white module cards with
    #  14px rounded corners."
    #
    # Checkboxes, week chips and the completion ring live inside those cards.
    "syllabus": {
        "ground": "flat",
        "ground_color": "#F8F8FC",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: near-black (#0a0a0a) edge to edge, never white ... Strictly
    #  avoid: ... logos, page numbers, footer bars or date stamps."
    #
    # ★The second theme after keynote-minimal whose avoid-list forbids the
    # footer outright. Its one footer is an 11px photo credit on section
    # breaks, explicitly "the deck's only footer" -- a slide, not furniture.
    "ted-style": {
        "ground": "flat",
        "ground_color": "#0A0A0A",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: #0D1117 ... small 8px teal status dots with a soft glow
    #  meaning healthy ... A header row on each slide carries 'IBM Plex Mono'
    #  metadata and a status dot."
    #
    # ★"corner radii above 8px" is a THRESHOLD: this theme's own 8px panels
    # are correct, so boxes are not forbidden.
    "telemetry": {
        "ground": "flat",
        "ground_color": "#0D1117",
        "corner_mark": {"text": "STATUS  OK", "color": "#8B949E"},
        "footer": {"right": "{page} / {pages}", "color": "#8B949E", "size_px": 9},
        "forbid_boxes": False,
        "skip_title_slide": True,
        "ornament": ("status_dot",),
        "accent": "#2DD4BF",
    },
    # "Background: flat cool white #F7F8FA on every slide, never a gradient ...
    #  a visible structured grid with numbered sections, ruled tables and
    #  aligned columns built on white #FFFFFF cards."
    #
    # ★"never a gradient" is the sentence the derived scanner was narrowed
    # around; the grid is the table's, not the slide's.
    "term-sheet": {
        "ground": "flat",
        "ground_color": "#F7F8FA",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a ticker strip across the top of every slide, one row of entries like
    #  'NWND 42.18 ▲1.2%' separated by thin amber dividers."
    #
    # The strip is a row; the painter's corner mark is its right-hand end, set
    # in the entry the prompt spells out.
    "ticker": {
        "ground": "flat",
        "ground_color": "#000000",
        "corner_mark": {"text": "NWND 42.18 ▲1.2%", "color": "#FFB000"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: a calm #FAFBFC that never competes with the data ... a tidy
    #  dashboard grid of white #FFFFFF KPI cards."
    "traction": {
        "ground": "flat",
        "ground_color": "#FAFBFC",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm cream (#F5F1E6) with a few concentric wavy topographic
    #  contour lines drawn as SVG paths in ink (#2E2A20) at 8 percent opacity,
    #  clustered near one corner."
    #
    # Wavy concentric paths in one corner are not any ornament here; the trail,
    # its milestones and the pennants are the exhibit.
    "trailhead": {
        "ground": "flat",
        "ground_color": "#F5F1E6",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "a row of small pennant triangles, about 22px wide, hanging from a 3px
    #  navy line across the top of every slide ... Strictly avoid: ... rounded
    #  corners except circles."
    #
    # ★The masthead is NOT taken. Its 3px rule matches the navy line, but it
    # also closes with a hairline at 74px, and a bare pair of rules where the
    # theme promises a string of pennants reads as a mistake rather than as the
    # motif. basel takes it because there the rule carries a metadata row, the
    # masthead's own shape.
    "varsity": {
        "ground": "flat",
        "ground_color": "#FBF6EC",
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm white (#FDFDFB) with a thin 2px light-gray frame
    #  (#E3E3DB, 10px rounded corners) inset about 14px on every slide."
    #
    # One rounded frame at 14px is not the double square hairline at 24 and 30
    # that the border ornament draws.
    "whiteboard": {
        "ground": "flat",
        "ground_color": "#FDFDFB",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: warm cream #F6F3E9 on every slide, with cards and panels in
    #  lighter cream #FCFAF3 behind thin 1px #E3DECB hairlines."
    #
    # The botanicals are hand-drawn SVG strokes, deliberately uneven.
    "wildflower": {
        "ground": "flat",
        "ground_color": "#F6F3E9",
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "'Roboto Mono' meta labels such as 'v0.3 DRAFT' sit in a dashed header
    #  row, 11px uppercase ... Strictly avoid: any fill besides #E5E7EB gray
    #  and #2563EB blue; solid borders on containers."
    #
    # Every container here is dashed, and the painter draws solid fills only.
    "wireframe": {
        "ground": "flat",
        "ground_color": "#FFFFFF",
        "corner_mark": {"text": "v0.3 DRAFT", "color": "#6B7280"},
        "forbid_boxes": True,
        "skip_title_slide": False,
        "ornament": (),
    },
    # "Background: never flat, a radial gradient from white (#ffffff) into icy
    #  blue (#e6edfb) ... Footer: a VT323 deck-name pill bottom-left in #7c85a8
    #  and a chrome capsule page number bottom-right."
    #
    # The pink and cyan corner hazes are a third and fourth stop the painter
    # has no room for.
    "y2k": {
        "ground": "gradient",
        "ground_color": "#FFFFFF",
        "ground_color_2": "#E6EDFB",
        "gradient_angle": 270,
        "footer": {"right": "{page}", "color": "#7C85A8"},
        "forbid_boxes": False,
        "skip_title_slide": False,
        "ornament": (),
    },
}


# =============================================================================
# The fallback, for a theme the registry gains before anyone reads its prompt
#
# Every one of the 81 registered themes is hand-authored above, so this runs
# for nothing today. It exists because the alternative for a newly vendored
# theme is EMPTY_LAYOUT -- not even a background -- and a deck on the wrong
# paper is a worse first impression than one with no furniture.
#
# It is a deliberately narrow scanner. It answers two questions only -- what is
# the paper, and does the theme's avoid-list rule out drawn panels -- and
# declines whenever the prompt is not explicit. Everything it cannot read stays
# absent, so a derived layout paints a background and nothing else.
#
# The scanner is narrow because a wide one lies. 68 of the 81 prompts contain
# the word "gradient", almost always in "Strictly avoid: gradients"; one says
# "never a gradient" outright. Reading the whole prompt for keywords would have
# given most of the catalogue a gradient it explicitly forbids.
# =============================================================================

_HEX = r"#[0-9A-Fa-f]{6}"

# A sentence ends at a period that is not part of a decimal ("0.25 opacity").
_SENTENCE_END = re.compile(r"\.(?!\d)")

_AVOID = re.compile(r"strictly avoid", re.I)

# Phrases in an avoid-list that rule out drawn panel furniture.
_NO_PANELS = re.compile(
    r"filled panels|filled cards|cards?\b|panels?\b|boxes\b|drop shadows?|"
    r"soft card shadows",
    re.I,
)

# "never a gradient", "no gradients", "not a gradient".
_GRADIENT_DENIED = re.compile(r"(never|no|not)\s+(a\s+)?gradients?", re.I)

# A repeating-linear-gradient is CSS for ruling or texture, not for a ground.
_TEXTURE_GRADIENT = re.compile(r"repeating[- ]linear[- ]gradient", re.I)

_TITLE_SLIDE_DIFFERS = re.compile(
    r"(title slide|cover)\s*:|full[- ]bleed .{0,40}(title|cover)", re.I
)


def _head(prompt: str) -> str:
    """Everything before the avoid-list. The tail describes what is absent."""
    m = _AVOID.search(prompt)
    return prompt[: m.start()] if m else prompt


def _tail(prompt: str) -> str:
    """The avoid-list, or an empty string when the prompt has none."""
    m = _AVOID.search(prompt)
    return prompt[m.start() :] if m else ""


def _background_sentence(head: str) -> str:
    """The one sentence beginning 'Background:'. Empty when there is none."""
    m = re.search(r"[Bb]ackground:", head)
    if not m:
        return ""
    rest = head[m.end() :]
    end = _SENTENCE_END.search(rest)
    return rest[: end.start()] if end else rest


def _derive(theme) -> dict:
    """A minimal layout read out of one theme's prompt text.

    Only two properties are ever derived, and only when the prompt states them
    plainly: the ground, and whether panels are forbidden. Anything structural
    -- ruling, margin rules, stamps, trackers -- is hand-authored above or not
    declared at all.
    """
    prompt = getattr(theme, "prompt_text", "") or ""
    head = _head(prompt)
    tail = _tail(prompt)
    sentence = _background_sentence(head)
    hexes = re.findall(_HEX, sentence)

    layout: dict = {"ground": "flat", "ornament": ()}

    gradient = (
        bool(re.search(r"gradient", sentence, re.I))
        and not _GRADIENT_DENIED.search(sentence)
        and not _TEXTURE_GRADIENT.search(sentence)
        and len(hexes) >= 2
    )
    if gradient:
        layout["ground"] = "gradient"
        layout["ground_color"] = hexes[0].upper()
        layout["ground_color_2"] = hexes[1].upper()
        layout["gradient_angle"] = 270
    elif hexes:
        layout["ground_color"] = hexes[0].upper()
    else:
        # No stated background colour. Fall back to the registry's own
        # background role rather than guessing a shade.
        palette = getattr(theme, "palette", {}) or {}
        bg = palette.get("background")
        if isinstance(bg, str):
            layout["ground_color"] = bg.upper()

    if tail and _NO_PANELS.search(tail):
        layout["forbid_boxes"] = True

    if _TITLE_SLIDE_DIFFERS.search(head):
        layout["skip_title_slide"] = True

    return layout


def _build() -> dict[str, dict]:
    """Every registered theme gets an entry; hand-authored ones win."""
    built: dict[str, dict] = {}
    for theme in pptx_themes.all_themes():
        try:
            built[theme.id] = _derive(theme)
        except Exception:  # pragma: no cover - a layout must never fail import
            built[theme.id] = dict(EMPTY_LAYOUT)
    for theme_id, layout in _HAND.items():
        built[theme_id] = layout
    return built


#: theme id -> layout. Built once, at import.
LAYOUTS: dict[str, dict] = _build()

#: The themes whose structural motifs were read out of their own prompt by
#: hand. This is now the whole registry -- see the guard that says so.
HAND_AUTHORED: frozenset[str] = frozenset(_HAND)


def layout_for(theme_id) -> dict:
    """The layout for a theme id. Unknown or unusable ids get the empty one."""
    if not isinstance(theme_id, str):
        return EMPTY_LAYOUT
    return LAYOUTS.get(theme_id.strip().lower(), EMPTY_LAYOUT)
