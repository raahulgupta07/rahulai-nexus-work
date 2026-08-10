"""The grounding check was reading identifiers as quantity claims.

Found by: replaying the real `verify_narrative` over 405 completed answers from
live Postgres. It would remove a sentence from 96 of them. Some of those are the
genuine fabrications this module exists to stop — but three measured classes
were the checker being simply wrong, and the check is being widened to cover
every turn, which turns each of those into sentences vanishing out of correct
answers.

The three, quoted from the replay:

  * "**`DL_POC_Toey.dbo.SalesDetails_2024`** has **59,320,209** rows."
    dropped citing `2024`. `_DATE_PATTERNS` strips `\\b(19|20)\\d{2}\\b`, and `_`
    is a word character, so `\\b` does NOT match between `_` and `2`. The year
    survived stripping and was then judged as a magnitude.
  * "Schemas are mostly `dbo`, with one table under `DL_POC.analytics`
    (`kmeans_leaderboard_2024`)." — the same shape, and this sentence states no
    figure at all.
  * "**Eval run `uat531-outlet-count`** (`a13f34a8-b28b-476e-bbf3-475733ba835e`)
    finished successfully." dropped citing `475733`, a fragment sliced out of the
    middle of a hex string.

The fix is that a number must stand FREE of word characters on both sides to
count as a figure. It is deliberately NOT "loosen the tolerance": the module
docstring is explicit that 11,499 against a true 11,488.57 is 0.09% out, so any
tolerance above a tenth of a percent waves through the exact fabrication the
check was built for. That case is re-pinned below and is the load-bearing one.

Contract these tests pin:
  * an identifier's digits are not a claim — table name, slug, UUID
  * every figure this product's prose really does state IS still extracted, in
    the markdown and punctuation it really is wrapped in. Without this half, a
    "fix" that simply stopped extracting numbers passes the other half.
  * the sentences carrying those figures are still CHECKED, and still dropped
    when the data cannot justify them
  * the original fabrication is still rejected
  * a number written alone in backticks is still a claim — code spans are not
    blanked, deliberately
"""
import re

import pytest

from app.services import figure_grounding
from app.services.figure_grounding import numbers_in, verify_narrative


def _dataset(rows):
    return [{"rows": rows}]


# Nothing in here is near any figure the corpus states, so any extracted token
# of real size is ungrounded and its sentence must go. Column total 10,000.
UNRELATED = _dataset([
    {"label": "alpha", "value": 4000},
    {"label": "beta", "value": 6000},
])


# ---------------------------------------------------------------------------
# 1. the defect: a name is not a figure
# ---------------------------------------------------------------------------

# Real sentences from the replay, with the token the old checker cited.
IDENTIFIER_SENTENCES = [
    (
        "**`DL_POC_Toey.dbo.SalesDetails_2024`** has **59,320,209** rows.",
        ["59,320,209"],
    ),
    (
        "Schemas are mostly `dbo`, with one table under `DL_POC.analytics` "
        "(`kmeans_leaderboard_2024`).",
        [],
    ),
    (
        "**Eval run `uat531-outlet-count`** "
        "(`a13f34a8-b28b-476e-bbf3-475733ba835e`) finished successfully.",
        [],
    ),
]


@pytest.mark.parametrize("sentence,expected", IDENTIFIER_SENTENCES)
def test_an_identifiers_digits_are_not_extracted_as_figures(sentence, expected):
    assert numbers_in(sentence) == expected, (
        "digits welded to a name were read as a quantity claim"
    )


@pytest.mark.parametrize("sentence,_expected", IDENTIFIER_SENTENCES)
def test_a_sentence_that_only_names_things_survives_verification(sentence, _expected):
    """★ The one that matters to a user: the sentence stays in the answer.

    The first of the three DOES state one figure — the row count — and it is
    checked; here it is grounded, so the whole sentence comes back untouched.
    """
    data = _dataset([{"table": "SalesDetails_2024", "rows_": 59320209}])
    verdict = verify_narrative(sentence, data)

    assert verdict.text == sentence, f"a naming sentence was edited: {verdict.dropped}"
    assert verdict.dropped == []


def test_the_row_count_in_a_naming_sentence_is_still_a_claim():
    """The fix must not go so far that the sentence stops being checked at all.

    Same sentence, data that cannot justify 59,320,209. It must go.
    """
    sentence = "**`DL_POC_Toey.dbo.SalesDetails_2024`** has **59,320,209** rows."
    verdict = verify_narrative(sentence, UNRELATED)

    assert "59,320,209" not in verdict.text, (
        "the row count stopped being checked — the fix went too wide"
    )
    assert verdict.dropped


# ---------------------------------------------------------------------------
# 2. ★ the positive control — real figures, in the markdown they really wear
# ---------------------------------------------------------------------------
#
# Every one of these is a real sentence from a real answer in this product. The
# bold asterisks, the currency word, the parentheses and the `#` in a store name
# are all how the model actually writes, so they are the only interesting test
# of a boundary rule. Without this block, a fix that simply stopped extracting
# numbers would pass every test above.

STATED_FIGURES = [
    ("**Total net sales: 5,136,609,583 MMK**", ["5,136,609,583"]),
    (
        "**Shwe Wah Dairy & Eggs #359** — 261,080,509 MMK",
        ["359", "261,080,509"],
    ),
    (
        "**Total Express sqft:** **8,100** — City Express Bahan (4,200) "
        "+ City Express Sanchaung (3,900).",
        ["8,100", "4,200", "3,900"],
    ),
    (
        "**Top line:** Vitamins & Supplements leads at **554,556,136 MMK**, "
        "followed by Fresh Produce (**491,846,862**)",
        ["554,556,136", "491,846,862"],
    ),
    (
        "Across **1,096** sale days, **105 festival days** average "
        "**7.79M MMK/day**",
        ["1,096", "105", "7.79M"],
    ),
    ("rose from 5.39B in Jan 2025 to 8.02B in May 2026", ["5.39B", "8.02B"]),
    (
        "**Thingyan** is the spike: ~**2.4×** normal daily net and baskets.",
        ["2.4"],
    ),
    ("The cost was $1,200 for the period.", ["1,200"]),
    ("Daily net runs 39.4–39.6k across the window.", ["39.4", "39.6k"]),
]


@pytest.mark.parametrize("sentence,expected", STATED_FIGURES)
def test_a_stated_figure_is_still_extracted(sentence, expected):
    assert numbers_in(sentence) == expected, (
        "a figure this product really does state stopped being extracted"
    )


@pytest.mark.parametrize("sentence,expected", STATED_FIGURES)
def test_a_stated_figure_is_still_checked(sentence, expected):
    """Extraction is not the point; being HELD to it is.

    Against data that justifies none of them, every sentence carrying a figure
    of real size must be dropped. `#359` and `2.4` are excluded from the
    expectation for the reason `is_grounded` gives: a small integer is
    structural, and 2.4 is a multiplier, not a magnitude — see below.
    """
    substantial = [t for t in expected if (figure_grounding.canonical(t) or 0) >= 1000]
    if not substantial:
        pytest.skip("no magnitude-sized figure in this sentence")

    verdict = verify_narrative(sentence, UNRELATED)
    assert verdict.dropped, f"{substantial} went unchecked"
    for tok in substantial:
        assert tok not in verdict.text


def test_the_currency_word_is_no_longer_read_as_millions():
    """★ A fix that fell out of the boundary rule, worth pinning on its own.

    "5,136,609,583 MMK" used to extract as `5,136,609,583 M` — the first letter
    of the currency swallowed as a magnitude suffix — canonicalising a five-
    billion total to 5.1e15. It could then never match anything in the data, so
    a correct sentence was dropped for a reason nobody could have read off the
    log line. The trailing `(?!\\w)` ends it: `M` is followed by `M`.
    """
    assert numbers_in("**Total net sales: 5,136,609,583 MMK**") == ["5,136,609,583"]
    assert figure_grounding.canonical("5,136,609,583") == pytest.approx(5136609583.0)

    data = _dataset([{"outlet": "a", "net": 5136609583}])
    sentence = "**Total net sales: 5,136,609,583 MMK**"
    assert verify_narrative(sentence, data).text == sentence


# ---------------------------------------------------------------------------
# 3. ★★★ the load-bearing case — the whole module exists for this token
# ---------------------------------------------------------------------------


def test_the_original_fabrication_is_still_rejected():
    """11,499 against a true 11,488.57 is 0.09% out.

    Quoted from `write_precision_slack`'s own docstring: any tolerance above a
    tenth of a percent waves this through. If false positives are ever "fixed"
    by loosening tolerance instead of by not treating names as claims, this is
    the test that says so.
    """
    data = _dataset([{"label": "alpha", "value": 11488.57}])

    verdict = verify_narrative("The average order value is 11,499.", data)
    assert "11,499" not in verdict.text, (
        "the fabrication this module was built to stop survived"
    )
    assert figure_grounding.is_grounded("11,499", [11488.57]) is False


def test_a_bare_number_in_backticks_is_still_a_claim():
    """★ Deliberately NOT fixed by blanking code spans.

    Stripping backticked spans was the obvious fix and is strictly weaker: all
    three measured false positives are killed by the free-standing rule alone,
    and blanking code spans would hand the model somewhere to put an unchecked
    number. A number written alone in backticks is a figure.
    """
    data = _dataset([{"label": "alpha", "value": 11488.57}])

    assert numbers_in("The average is `11,499`.") == ["11,499"]
    assert "11,499" not in verify_narrative("The average is `11,499`.", data).text


# ---------------------------------------------------------------------------
# 4. ★ the red proof, carried in the test rather than left at a shell prompt
# ---------------------------------------------------------------------------


def test_the_original_defect_is_still_detected():
    """Reconstruct the pre-fix extractor and require the corpus to catch it.

    A guard whose fixtures happen to agree with today's code detects nothing.
    This rebuilds exactly what `numbers_in` used to be — the same date stripping,
    the old unanchored `\\d[\\d,]*\\.?\\d*\\s*[BMK%]?` — and asserts the three
    real sentences DO yield identifier digits under it. So the corpus is proved
    to discriminate, every time this suite runs, not once at a shell prompt.
    """
    old_pattern = re.compile(r"\d[\d,]*\.?\d*\s*[BMK%]?")

    def old_numbers_in(text):
        cleaned = text or ""
        for pat in figure_grounding._DATE_PATTERNS:
            cleaned = pat.sub(" ", cleaned)
        return old_pattern.findall(cleaned)

    magnitudes = figure_grounding.data_magnitudes(UNRELATED)

    for sentence, expected in IDENTIFIER_SENTENCES:
        old_tokens = old_numbers_in(sentence)
        assert old_tokens != expected, (
            f"the old extractor already agreed with the fix on {sentence!r} — "
            "this fixture proves nothing"
        )
        old_bad = [t for t in old_tokens if not figure_grounding.is_grounded(t, magnitudes)]
        assert old_bad, (
            f"the old extractor found nothing ungrounded in {sentence!r}; the "
            "replay says this sentence was dropped, so the fixture is wrong"
        )
        # ...and nothing survives the fix.
        assert not [
            t for t in numbers_in(sentence)
            if not figure_grounding.is_grounded(t, magnitudes)
        ] or expected, "an identifier is still being read as an ungrounded claim"


def test_the_shared_definition_is_what_changed():
    """`artifact_insights` re-exports these, so the insight panel gets the same
    fix and cannot drift into its own copy. Re-pinned here because this change
    is the first one to alter extraction since the move."""
    from app.services import artifact_insights

    assert artifact_insights._numbers_in is figure_grounding.numbers_in
    assert artifact_insights._DATE_PATTERNS is figure_grounding._DATE_PATTERNS
