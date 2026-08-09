"""The local-runtime pairing code has to be a secret, because it is the only one.

``POST /local-runtime/pair/claim`` is unauthenticated by design — the helper has
no session — so whoever redeems the code receives the runtime token and becomes
that person's local helper: it serves the folder listings and file reads meant
for their machine and can push arbitrary content back as "local documents".

It used to be ``secrets.randbelow(1000000)``. One million values, ten minutes
alive, no attempt limit. This file pins the size of the space directly, because
the throttle in ``test_the_pairing_door_counts_wrong_answers`` only raises the
cost of guessing and does not bound it: an address is rented by the hour, and
the entropy is the half of the fix that holds no matter who is calling.

★These tests are schema-free on purpose so they can live in ``fork/``, whose
conftest makes ``run_migrations`` a no-op. The behaviour of the endpoint itself
needs a database and lives in ``tests/unit``.
"""
import re

import pytest

from app.core.pairing_codes import (
    PAIR_CODE_ALPHABET,
    PAIR_CODE_LENGTH,
    generate_pair_code,
    normalize_pair_code,
    pair_code_search_space,
)

# What the old code was worth, kept as a number so the comparison below is
# against the actual defect rather than an adjective.
OLD_SEARCH_SPACE = 1_000_000


def test_the_space_is_far_past_what_ten_minutes_of_guessing_can_cover():
    """★The direct entropy assertion.

    A single host managing 100 requests a second lands 60,000 guesses inside
    one code's ten-minute life. Against the old million that is a 6% chance on
    ONE pending pairing — and the claim lookup matches any pending row on the
    instance, so twenty people pairing at once made it roughly 70%.

    The bar here is 2**32, which the old space (about 2**20) fails and the new
    one (about 2**39) clears by three orders of magnitude. It is deliberately
    not pinned to the exact current value: widening the alphabet or the length
    later should not have to edit this line, but narrowing either must fail it.
    """
    assert pair_code_search_space() > 2 ** 32
    assert pair_code_search_space() > OLD_SEARCH_SPACE * 500_000


def test_a_guess_inside_the_codes_lifetime_is_hopeless():
    """The same fact stated the way an attacker would compute it.

    60,000 guesses is a generous ten minutes of sustained flooding against a
    single endpoint, and that is before the throttle refuses any of them.
    """
    guesses_in_ten_minutes = 100 * 600
    assert guesses_in_ten_minutes / pair_code_search_space() < 1e-6


def test_the_alphabet_drops_both_halves_of_every_look_alike_pair():
    """★The code is read off one screen and typed into another.

    ``0``/``O`` and ``1``/``I``/``L`` are the mistakes people actually make.
    Excluding one member of a pair still leaves the reader guessing which they
    are looking at; excluding both means there is nothing to disambiguate and
    no mapping table that could later disagree with the generator.
    """
    for confusable in "01OIL":
        assert confusable not in PAIR_CODE_ALPHABET, confusable
    # ★U is out for Crockford's reason: it keeps accidental words from being
    # generated and shown to a user.
    assert "U" not in PAIR_CODE_ALPHABET
    assert len(set(PAIR_CODE_ALPHABET)) == len(PAIR_CODE_ALPHABET)


def test_every_minted_code_uses_only_that_alphabet():
    """A generator that reaches outside its own alphabet re-imports exactly the
    transcription errors the alphabet was chosen to remove."""
    permitted = set(PAIR_CODE_ALPHABET)
    for _ in range(500):
        code = generate_pair_code()
        body = code.replace("-", "")
        assert len(body) == PAIR_CODE_LENGTH, code
        assert set(body) <= permitted, code


def test_a_minted_code_is_grouped_for_reading_back():
    """Presentation only — the hyphen never reaches the digest. It is asserted
    because the settings page shows the code verbatim and a user reading eight
    unbroken characters aloud loses their place."""
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", generate_pair_code())


def test_two_codes_are_not_the_same_code():
    """A generator seeded once, or one that lost its randomness to a refactor,
    passes every other test in this file."""
    assert len({generate_pair_code() for _ in range(200)}) == 200


def test_no_symbol_is_starved_or_favoured():
    """★Cheap bias check.

    The alphabet has 30 symbols and 256 does not divide by 30, so anything
    built on raw bytes plus a modulus skews toward the low symbols — a real
    reduction in the space that no other test here would notice. Over 12,000
    draws every symbol should appear a few hundred times; the bounds are wide
    enough that honest randomness will not trip them.
    """
    draws = "".join(generate_pair_code().replace("-", "") for _ in range(1500))
    counts = {symbol: draws.count(symbol) for symbol in PAIR_CODE_ALPHABET}
    expected = len(draws) / len(PAIR_CODE_ALPHABET)
    assert min(counts.values()) > expected * 0.5, counts
    assert max(counts.values()) < expected * 1.5, counts


# --------------------------------------------------------------------------- #
#  Normalisation — the part that has to be lossless for what already exists
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("typed", ["abcd-efgh", "ABCD-EFGH", "ABCDEFGH", " abcd efgh "])
def test_the_forms_a_person_might_type_all_reach_one_digest(typed):
    """The helper is pasted the grouped form and typed the ungrouped one, and
    both have to pair. Normalisation is what makes that true without storing
    the plaintext or comparing loosely at the database."""
    assert normalize_pair_code(typed) == "ABCDEFGH"


def test_a_legacy_six_digit_code_still_normalises_to_itself():
    """★★★No flag day, and this is why there is no migration.

    Every pairing already pending when this shipped has ``sha256("012345")``
    stored against it. Normalisation only uppercases and drops separators, so a
    digit string is unchanged and its stored hash still matches. Had this
    mapped look-alikes — ``O`` to ``0``, say — those rows would have silently
    stopped being claimable, and the user-visible symptom would have been a
    correct code refused with the same opaque 400 as a wrong one.
    """
    assert normalize_pair_code("012345") == "012345"
    assert normalize_pair_code("000000") == "000000"


def test_normalising_twice_changes_nothing():
    """It is applied on the minting side and again on the claiming side. If it
    were not idempotent those two would disagree and no code would ever pair."""
    for _ in range(50):
        once = normalize_pair_code(generate_pair_code())
        assert normalize_pair_code(once) == once


def test_an_empty_or_absent_code_normalises_to_nothing():
    """The claim route refuses without a lookup when this is empty. If it
    returned something truthy for ``None``, a body with no code at all would be
    hashed and compared against real rows."""
    for empty in (None, "", "   ", "---", "!!!"):
        assert normalize_pair_code(empty) == ""


def test_a_huge_submitted_code_is_cut_before_it_is_hashed():
    """The claim body is attacker-controlled and the endpoint is public. A
    megabyte of "code" should cost a truncation, not a megabyte of hashing."""
    assert len(normalize_pair_code("A" * 5_000_000)) <= 64
