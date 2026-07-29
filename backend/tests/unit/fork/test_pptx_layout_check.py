"""Deck layout check.

The fixture is not hand-written. `pptx_layout_measured.json` holds what Chromium
actually reported for five slides:

  good         — every text block fits its box, nothing off-slide
  bad_overlap  — 1,311 characters of 14pt text in a box 0.90in tall; the text
                 needs ~4.0in, so it renders over the card 0.20in below it
  bad_offslide — five shapes pushed past the slide edges
  real_slide4  — a slide of a REAL board deck. Correct on screen. Its
  real_slide5    commentary paragraph measures 1.41x / 1.52x its declared
                 height, and slide 5's bottom edge measures 5.96px off-slide.

★ The two real slides are the negative controls that matter. Both are visually
correct — the text grew into empty space inside a larger white card — and the
first version of this check reported three issues on them and none anywhere
else. The rendered height is not what PowerPoint will lay out: the deck's fonts
are not installed in the container, Chromium substitutes and wraps wider, and a
correct box measures up to 1.5x tall. Any rule that treats height alone as the
verdict fails here.

★ In `bad_overlap` the two declared boxes do NOT intersect — the generator left
a correct 0.20in gap and only the *rendered* text crosses it. So the collision
test has to be run against the GROWN rect, never the declared one.

★ OfficeCLI's own `view … issues` also reports zero on `bad_overlap` (it renders
text boxes as `height:auto`, so nothing overflows in its model). It is accurate
on the off-slide deck. We use it as a renderer only.
"""

import json
import asyncio
from pathlib import Path

import pytest

from app.ai.code_execution.pptx_lint import (
    EDGE_TOLERANCE_PX,
    GROWTH_RATIO,
    _analyse,
    check_deck_layout,
    officecli_available,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pptx_layout_measured.json"


@pytest.fixture(scope="module")
def measured():
    return json.loads(FIXTURE.read_text())


def _kinds(issues):
    return sorted(i["kind"] for i in issues)


def test_good_deck_is_silent(measured):
    """The negative control. A checker that fires on a correct deck is noise,
    and noise gets switched off."""
    assert _analyse(3, measured["good"]) == []


def test_overflowing_text_is_caught(measured):
    issues = _analyse(3, measured["bad_overlap"])

    growth = [i for i in issues if i["kind"] == "grew_past_box"]
    assert len(growth) == 1, f"expected exactly one overflow, got {_kinds(issues)}"

    issue = growth[0]
    assert issue["slide"] == 3
    assert issue["path"] == "/slide[3]/shape[@id=3]"
    # the commentary paragraph, not one of the labels
    assert issue["text"].startswith("Net revenue closed the quarter")
    assert "x)" in issue["detail"]


def test_overflow_is_invisible_to_rectangle_intersection(measured):
    """Pin the reason this detector exists. If someone later 'simplifies' it into
    an overlap test, this fails and explains why."""
    shapes = measured["bad_overlap"]["shapes"]
    culprit = next(s for s in shapes if s["path"] == "/slide[3]/shape[@id=3]")

    declared_px = culprit["declared_pt"] * (measured["bad_overlap"]["slide"]["w"] / 960.0)
    assert culprit["h"] > declared_px * 4, "fixture no longer overflows"

    # the box it was GIVEN clears everything stacked under it in the same column
    box_bottom = culprit["top"] + declared_px
    left, right = culprit["left"], culprit["left"] + culprit["w"]
    below = [
        s for s in shapes
        if s["path"] != culprit["path"]
        and s["top"] > culprit["top"]
        and s["left"] < right and s["left"] + s["w"] > left  # same column
    ]
    assert below, "fixture has nothing stacked under the culprit"
    assert all(s["top"] > box_bottom for s in below), (
        "the declared boxes intersect, so this deck no longer isolates the "
        "rendered-text-only failure mode"
    )
    # ...and the rendered text does not
    assert culprit["top"] + culprit["h"] > min(s["top"] for s in below)


@pytest.mark.parametrize("deck,slide_no", [("real_slide4", 4), ("real_slide5", 5)])
def test_real_deck_slides_are_silent(measured, deck, slide_no):
    """The regression this rule was written for.

    Both slides are correct on screen. The first version of the check reported
    `grew_past_box` on each (1.41x and 1.52x) plus `off_slide` on slide 5 (6px)
    — three issues, all false, and not one true positive anywhere in the deck.
    """
    assert _analyse(slide_no, measured[deck]) == []


def test_growth_that_only_reaches_the_footer_is_not_a_collision(measured):
    """Why the real slides are silent, stated as a fact about the geometry.

    Both commentary paragraphs DO intersect the footer line once you give them
    their rendered height — so a rule that fires on any collision would still
    report them. They are silent because the growth is inside the noise band
    that font substitution can manufacture, not because they miss the footer.
    """
    shapes = measured["real_slide5"]["shapes"]
    scale = measured["real_slide5"]["slide"]["w"] / 960.0
    para = next(s for s in shapes if s["path"] == "/slide[5]/shape[@id=41]")
    footer = next(s for s in shapes if s["path"] == "/slide[5]/shape[@id=42]")

    ratio = para["h"] / (para["declared_pt"] * scale)
    assert 1.0 < ratio < GROWTH_RATIO, "fixture no longer sits in the noise band"
    assert para["top"] + para["h"] > footer["top"], "fixture no longer collides"


def test_edge_tolerance_separates_the_real_overshoots(measured):
    """The tolerance is not a round guess — it sits in a measured gap.

    Largest benign overshoot in the corpus: 5.96px, slide 5's paragraph, from
    the same inflated height. Smallest genuine one: 62.38px, a footer pushed
    below the slide on purpose.
    """
    slide = measured["real_slide5"]["slide"]
    para = next(
        s for s in measured["real_slide5"]["shapes"]
        if s["path"] == "/slide[5]/shape[@id=41]"
    )
    benign = para["top"] + para["h"] - slide["h"]

    off = measured["bad_offslide"]
    genuine = min(
        max(
            s["left"] + s["w"] - off["slide"]["w"],
            s["top"] + s["h"] - off["slide"]["h"],
            -s["left"],
            -s["top"],
        )
        for s in off["shapes"]
        if s["path"] in {f"/slide[3]/shape[@id={n}]" for n in (6, 7, 8, 9, 10)}
    )

    assert benign < EDGE_TOLERANCE_PX < genuine
    assert round(benign, 2) == 5.96
    assert round(genuine, 2) == 62.38


def test_growth_into_empty_space_is_not_reported(measured):
    """Derived from the real overflow, not invented: take the deck that DOES
    fail and move the two shapes its text lands on out of the way. Same 5.16x
    growth, nothing underneath it, no issue."""
    deck = json.loads(json.dumps(measured["bad_overlap"]))
    for shape in deck["shapes"]:
        if shape["path"] in ("/slide[3]/shape[@id=5]", "/slide[3]/shape[@id=6]"):
            shape["top"] = 800.0  # below the grown rect, which ends at 767px

    assert _analyse(3, measured["bad_overlap"]), "control: unmoved deck still fires"
    assert [i for i in _analyse(3, deck) if i["kind"] == "grew_past_box"] == []


def test_growth_inside_a_bigger_card_is_not_reported(measured):
    """The real slides' shape: text grows, but stays inside the white card that
    was drawn around it. Derived by enlarging the fixture's own background card
    to enclose the grown text."""
    deck = json.loads(json.dumps(measured["bad_overlap"]))
    card = next(s for s in deck["shapes"] if s["path"] == "/slide[3]/shape[@id=4]")
    card.update(top=180.0, h=620.0, left=60.0, w=920.0)

    assert [i for i in _analyse(3, deck) if i["kind"] == "grew_past_box"] == []


def test_off_slide_shapes_are_caught(measured):
    issues = _analyse(3, measured["bad_offslide"])
    off = [i for i in issues if i["kind"] == "off_slide"]

    assert len(off) == 5
    assert {i["path"] for i in off} == {
        f"/slide[3]/shape[@id={n}]" for n in (6, 7, 8, 9, 10)
    }
    edges = {d.rsplit(" ", 2)[-2] for d in (i["detail"] for i in off)}
    assert edges == {"right", "bottom", "left"}


def test_a_shape_inside_the_slide_is_not_flagged(measured):
    """The off-slide deck deliberately keeps one correct card, so the check has
    to discriminate per shape rather than condemn the slide."""
    flagged = {i["path"] for i in _analyse(3, measured["bad_offslide"])}
    on_slide = [
        s["path"] for s in measured["bad_offslide"]["shapes"]
        if s["path"] and s["path"] not in flagged
    ]
    assert on_slide, "every shape was flagged — the check is not discriminating"


def test_filled_cards_are_not_measured_for_growth(measured):
    """A card is a background with a fixed height; only text boxes grow."""
    for deck in measured.values():
        for issue in _analyse(3, deck):
            if issue["kind"] != "grew_past_box":
                continue
            shape = next(s for s in deck["shapes"] if s["path"] == issue["path"])
            assert not shape["fill"]


def test_missing_file_returns_empty(tmp_path):
    result = asyncio.run(check_deck_layout(tmp_path / "nope.pptx"))
    assert result == []


def test_missing_binary_returns_empty(tmp_path, monkeypatch):
    """No officecli in the image → the deck still ships. This is the whole
    failure posture: advisory checks never block delivery."""
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a deck")

    monkeypatch.setattr("app.ai.code_execution.pptx_lint.shutil.which", lambda _: None)
    assert asyncio.run(check_deck_layout(deck)) == []


def test_render_failure_returns_empty(tmp_path, monkeypatch):
    """officecli present but exploding on every slide."""
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a deck")

    monkeypatch.setattr(
        "app.ai.code_execution.pptx_lint.shutil.which", lambda _: "/usr/local/bin/officecli"
    )
    monkeypatch.setattr("app.ai.code_execution.pptx_lint._slide_count", lambda _: 2)
    monkeypatch.setattr(
        "app.ai.code_execution.pptx_lint._render_slide_html",
        lambda *a, **k: None,
    )
    assert asyncio.run(check_deck_layout(deck)) == []


def test_flag_defaults_off():
    """Phase 1 ships dark. Turning this on is a separate, deliberate act."""
    from app.settings.config import Settings

    assert Settings().hybrid_deck_layout_check is False


def test_availability_probe_does_not_raise():
    assert isinstance(officecli_available(), bool)
