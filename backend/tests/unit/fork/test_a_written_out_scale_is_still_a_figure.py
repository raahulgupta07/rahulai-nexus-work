"""A figure written with its scale as a WORD was read as the mantissa alone.

WHAT THIS COST
--------------
Measured live on the dev install, 2026-08-19. Asked for total net sales, the
agent answered:

    The total net sales across all time is **5,136,609,583 MMK**
    (~5.14 billion MMK).

That sentence is correct in both halves — 5.14 billion IS 5,136,609,583 — and
the whole sentence was DELETED. The log names the reason:

    narrative grounding: removed 1 sentence(s) — ... — ungrounded: 5.14

`numbers_in` matches an optional `[BMKbmk%]` suffix welded to the digits, so
`5.14B` carries its scale and `5.14 billion` does not: the word is left behind
and the token is a bare `5.14`. `canonical` then reads it as five-point-one-four
and `write_precision_slack` allows ±0.005, against a magnitude of five billion.

So the product answered the question correctly and then withheld its own
answer, and a user reading the screen sees the withholding notice. The check
exists to stop invented figures; here it deleted a true one because of how the
writer chose to spell the scale — and "5.14 billion" is exactly how this
product's own prose writes a large number beside its exact form.

THE OTHER HALF, WHICH IS A REAL FABRICATION HOLE
------------------------------------------------
The same blindness lets a WRONG figure through. "The total is 9 billion" against
five billion of data tokenises as `9`, which `is_grounded` waves past as a small
structural integer ("top 3", "8 groups"). A claim of nine billion is not
structural. Reading the scale closes both directions at once, which is why the
negative cases below matter as much as the positive one.

★The refused figure has to be one the data genuinely cannot justify, and my
first draft of this test used "3 billion" — which the data CAN justify, because
group beta totals 3.14 billion and a figure written to the billion may be out by
half of one. The module was right and the test was wrong. A written figure is
held to the precision it claims, in this direction too.

★The suffix must stay ATTACHED to the number. "3 bikes" and "5 million-dollar
questions" are not the same shape as "3 billion", and a scale word floating
anywhere in the sentence must never adopt a number.
"""
import pytest

from app.services import figure_grounding


# The measured value from the live install.
TOTAL = 5_136_609_583
DATA = [{"rows": [
    {"label": "alpha", "value": 2_000_000_000},
    {"label": "beta", "value": 3_136_609_583},
]}]


# --- the defect --------------------------------------------------------------


def test_the_sentence_that_was_deleted_survives():
    narrative = (
        "The total net sales across all time is **5,136,609,583 MMK** "
        "(~5.14 billion MMK)."
    )
    verdict = figure_grounding.verify_narrative(narrative, DATA)

    assert verdict.text == narrative, (
        "a correct sentence was withheld because its scale was spelled as a "
        "word: " + repr(verdict.dropped)
    )


def test_the_scale_word_is_read_as_a_scale():
    assert figure_grounding.canonical("5.14 billion") == pytest.approx(5.14e9)
    assert figure_grounding.canonical("2.5 million") == pytest.approx(2.5e6)
    assert figure_grounding.canonical("40 thousand") == pytest.approx(40e3)
    assert figure_grounding.canonical("1.2 trillion") == pytest.approx(1.2e12)


def test_the_token_keeps_its_scale():
    """★The tolerance is derived from the TOKEN. A token that has lost its scale
    carries a tolerance a million times too tight, which is the whole defect."""
    tokens = figure_grounding.numbers_in("about 5.14 billion MMK in total")
    assert any("billion" in t for t in tokens), (
        "the scale word was left behind and the token is a bare mantissa: %r" % tokens
    )


def test_the_precision_claimed_is_the_precision_held_to():
    """"5.14 billion" is stated to a hundredth of a billion, so it may be out by
    at most half of that — five million. The same figure written "5.14" claims a
    hundredth of ONE, and must keep claiming it."""
    assert figure_grounding.write_precision_slack("5.14 billion") == pytest.approx(5e6)
    assert figure_grounding.write_precision_slack("5.14") == pytest.approx(0.005)

    assert figure_grounding.is_grounded("5.14 billion", [TOTAL]) is True
    assert figure_grounding.is_grounded("5.14", [TOTAL]) is False


# --- the fabrication half ----------------------------------------------------


def test_a_wrong_figure_written_with_a_word_is_still_refused():
    """★Today this passes as a small structural integer. Nine billion is not
    "top 3"."""
    verdict = figure_grounding.verify_narrative("The total is 9 billion.", DATA)
    assert "9 billion" not in verdict.text, (
        "a claim of nine billion against five billion of data survived because "
        "the checker only ever saw the digit 9"
    )


def test_a_rounded_figure_written_with_a_word_is_still_accepted():
    """★The other side of the same coin: group beta totals 3,136,609,583, and
    "3.1 billion" is honest reporting of it. Refusing that would trade the
    deletion this file exists for onto a different sentence."""
    verdict = figure_grounding.verify_narrative("The leading group holds 3.1 billion.", DATA)
    assert "3.1 billion" in verdict.text


def test_a_scale_word_only_binds_when_it_is_attached():
    """★A number does not adopt a scale word that belongs to the prose."""
    assert figure_grounding.canonical("3 bikes") is None
    tokens = figure_grounding.numbers_in("we sold 3 bikes and 8 scooters")
    assert not any("billion" in t or "million" in t for t in tokens)

    # 3 and 8 are structural, so the sentence stands.
    sentence = "We sold 3 bikes and 8 scooters."
    assert figure_grounding.verify_narrative(sentence, DATA).text == sentence


def test_the_short_forms_still_work_exactly_as_before():
    """The existing suffix behaviour is unchanged — this is an addition."""
    assert figure_grounding.canonical("5.14B") == pytest.approx(5.14e9)
    assert figure_grounding.canonical("39.6k") == pytest.approx(39_600)
    assert figure_grounding.canonical("48.8%") == pytest.approx(48.8)


def test_a_currency_code_is_not_a_scale():
    """★"5,136,609,583 MMK" must not read the M of the currency as millions —
    the trailing `(?!\\w)` is what stops it, and a word-form scale must not
    reopen that."""
    assert figure_grounding.canonical("5,136,609,583") == pytest.approx(TOTAL)
    tokens = figure_grounding.numbers_in("**5,136,609,583 MMK**")
    assert [figure_grounding.canonical(t) for t in tokens] == [pytest.approx(TOTAL)]
