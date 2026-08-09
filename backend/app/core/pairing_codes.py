"""The secret that pairs a local helper with an account.

`POST /local-runtime/pair/claim` is public by design — the helper has no
session, so the code IS the credential. Whoever redeems it receives a
long-lived runtime token and becomes that person's local helper: it then
serves the folder listings and file reads meant for their machine, and can
feed arbitrary content back into the workspace as "local documents". A code
that can be guessed is therefore a full account-scoped compromise, not a
nuisance.

★★★**Six digits was not a secret, it was a speed bump.** The old code was
``secrets.randbelow(1000000)`` — 1,000,000 values, alive for ten minutes,
against an endpoint with no attempt limit at all. A single host managing 100
requests a second lands 60,000 guesses inside one code's lifetime, which is a
6% chance against one pending pairing. And the claim lookup matches ANY
pending row on the instance, so the odds scale with how many people happen to
be pairing at once: twenty open pairings turns that 6% into roughly 70%.

★★**Entropy is the half of the fix that does not depend on knowing who is
calling.** The throttle in ``login_throttle`` keys on a source address, and an
address is rented by the hour; it raises the cost of a distributed attempt
without bounding it. Widening the code bounds it outright, and it is the
cheaper half for the user, who copies or types the thing exactly once. Both
are applied — see ``throttle_pair_claim``.

Choices worth stating:

★**Thirty characters, not thirty-six.** ``0``/``O`` and ``1``/``I``/``L`` are
the transcription errors people actually make, and this code gets read off one
screen and typed into another. Both members of each pair are dropped rather
than one, so there is nothing to disambiguate and no mapping table that could
later disagree with itself. ``U`` goes too (Crockford's reason: it keeps
accidental words out of generated codes). What is left is 30 symbols, and
eight of them is 30**8 = 656,100,000,000 — about 2**39.3, six orders of
magnitude past where it was.

★**Normalisation is deliberately lossless for the old format.** It uppercases
and drops separators, nothing more. A legacy six-digit code normalises to
itself, so its stored hash still matches and every pairing already pending
when this shipped stayed claimable. There is no flag day and no migration.

★**The hyphen is presentation only.** The code is minted as ``ABCD-EFGH``
because two groups of four are easier to read back than a run of eight, but
the hyphen is stripped before hashing — so a helper that was pasted the
grouped form and one that was typed the ungrouped form both work, and the
plaintext is still never stored anywhere.
"""
import secrets

# ★No 0/O, no 1/I/L, no U. See the module docstring — both members of each
# look-alike pair are excluded, which is what makes a mapping table
# unnecessary.
PAIR_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

PAIR_CODE_LENGTH = 8
PAIR_CODE_GROUP = 4

# ★A claim body is attacker-controlled. Normalisation caps its length so a
# megabyte of "code" is not hashed, and so the comparison cost stays flat.
_MAX_SUBMITTED_LENGTH = 64


def generate_pair_code() -> str:
    """Mint one pairing code, grouped for reading: ``ABCD-EFGH``.

    ★``secrets.choice`` per position, not an index into a shuffled string:
    the alphabet's length (30) does not divide 256, so anything built on raw
    bytes and a modulus is biased toward the low symbols. ``secrets.choice``
    rejects rather than folds.
    """
    body = "".join(secrets.choice(PAIR_CODE_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
    return "-".join(
        body[i:i + PAIR_CODE_GROUP] for i in range(0, PAIR_CODE_LENGTH, PAIR_CODE_GROUP)
    )


def normalize_pair_code(raw) -> str:
    """The form a code is hashed in, on both the minting and the claiming side.

    Uppercases and keeps only alphanumerics, so spaces, hyphens and a
    lower-case paste all reach the same digest. Applied identically in both
    places — a normaliser used on one side only is just a bug with a helpful
    name.
    """
    if not raw:
        return ""
    text = str(raw)[:_MAX_SUBMITTED_LENGTH * 4]
    return "".join(ch for ch in text.upper() if ch.isalnum())[:_MAX_SUBMITTED_LENGTH]


def pair_code_search_space() -> int:
    """How many codes exist. Used by the guard so the number in the tests is
    derived from the alphabet rather than copied beside it and left to rot."""
    return len(PAIR_CODE_ALPHABET) ** PAIR_CODE_LENGTH
