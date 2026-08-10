"""Never state a figure the run's own data cannot justify.

Why this module exists
----------------------
The grounding check was built for the insight panel that sits above a dashboard
(`artifact_insights.verify_findings`) and it works: every figure a finding
claims is checked against the artifact's own data, and a finding citing a number
that appears nowhere is dropped.

The chat narrative the agent writes beside that same dashboard had no such
check, and the asymmetry was the whole defect. On one observed run the panel was
correct while the prose next to it claimed a total, an order count and an
average that matched NOTHING in the run — not the preview copy of the dataset,
not the artifact-width copy, not the aggregate query the dashboard was built on.
They came from the model's own working memory during exploration and were never
re-derived. Two surfaces, one run, one set of numbers, and only one of them was
being held to it.

So the verification lives here, once, and both surfaces import it:

  * `artifact_insights` keeps `_numbers_in` / `_canonical` / `_data_magnitudes`
    / `_write_precision_slack` / `_is_grounded` as thin re-exports of these, so
    its behaviour is unchanged to the token.
  * the agent's completion narrative runs `verify_narrative` before the answer
    is persisted.

A copy of this logic in a second file would drift, and the surface that drifted
would be the one nobody was reading. There is one definition.

Nothing here is specific to any dataset, column, currency, table or domain. The
inputs are "some text" and "some rows"; the output is the same text with any
claim the rows cannot justify removed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Dates are not magnitude claims. "rose from 5.39B in Jan 2025 to 8.02B in May
# 2026" contains four number-like tokens, but two of them are YEARS — they say
# WHEN, not HOW MUCH, and no year will ever appear among a dataset's measures.
# Left in, they made every correctly-grounded finding fail. Stripped first.
_DATE_PATTERNS = (
    re.compile(r"\b(19|20)\d{2}-\d{1,2}(-\d{1,2})?\b"),   # 2025-01, 2025-01-31
    re.compile(r"\b(19|20)\d{2}\b"),                        # 2025
    re.compile(r"\bQ[1-4]\b", re.IGNORECASE),               # Q3
)


# A figure is a claim about QUANTITY. A run of digits welded to letters, an
# underscore or another digit-run is a NAME, and naming a thing is not claiming
# anything about it. Replaying the real checker over 405 completed answers from
# live Postgres found it would strip a sentence from 96 of them, and three
# measured classes of that were the checker misreading an identifier:
#
#   * a year inside a table name —
#     "**`DL_POC_Toey.dbo.SalesDetails_2024`** has **59,320,209** rows."
#     was dropped citing `2024`. `_DATE_PATTERNS` could not save it: `_` is a
#     word character, so the `\b` in `\b(19|20)\d{2}\b` does NOT match between
#     `_` and `2`, the year survived stripping, and was then judged as a
#     magnitude. Same for "(`kmeans_leaderboard_2024`)".
#   * digits out of the middle of a UUID —
#     "**Eval run `uat531-outlet-count`** (`a13f34a8-b28b-476e-bbf3-475733ba835e`)
#     finished successfully." was dropped citing `475733`, a fragment of a hex
#     string that means nothing on its own.
#   * a slug — the `531` in `uat531-outlet-count`.
#
# So a token has to stand FREE to count as a figure:
#   * nothing word-like immediately before it (`(?<![\w.,])` — letters, digits,
#     `_`, and a preceding `.`/`,` so `.dbo.` and `1,2` fragments cannot start a
#     token mid-way through something bigger), and
#   * nothing word-like immediately after it (`(?!\w)`), which is what kills
#     `475733ba835e`: the engine tries `475733b`, then `475733`, and every
#     attempt lands on a letter.
#
# The punctuation this product's own prose actually wraps figures in still
# passes, because none of it is a word character — verified against real
# answers: `$1,200`, `(4,200)`, `**8,100**`, `~2.4×`, `39.4–39.6k`,
# `5,136,609,583 MMK`, `**554,556,136 MMK**`.
#
# ★ The suffix now allows lowercase (`39.6k` is written that way constantly) and
#   the trailing `(?!\w)` is what makes that safe: "3 bikes" tries `3 b`, fails
#   on the `i`, and backtracks to a bare `3`. Same mechanism means
#   "5,136,609,583 MMK" no longer mis-reads the first `M` of the currency as
#   "millions" — it used to canonicalise that sentence's total as 5.1e15 and
#   then, of course, find nothing in the data anywhere near it.
#
# ★ NOT done, deliberately: stripping backticked code spans wholesale. It was
#   the obvious fix and it is strictly weaker — all three measured classes above
#   are already killed by the free-standing rule, and blanking code spans would
#   additionally hand the model a place to put an unchecked number ("the total
#   is `11,499`"). This module exists to catch exactly that token. A number
#   written alone in backticks stays a claim.
#
# ★ NOT a defect: the `#359`/`#116`/`#5` in store names ("Shwe Wah Dairy & Eggs
#   #359") are extracted, and always were. They are small integers, which
#   `is_grounded` already passes as structural — they were never what sank those
#   sentences, and narrowing extraction around `#` would buy nothing.
_NUMBER_TOKEN = re.compile(r"(?<![\w.,])\d[\d,]*(?:\.\d+)?\s*[BMKbmk%]?(?!\w)")


def numbers_in(text: str) -> List[str]:
    """Every free-standing number-like token in a string, excluding dates.

    Catches `104.8B`, `9,120,492`, `48.8%`, `11,489`, `5.39B`. Deliberately
    string-level: the check is "did this figure come from the data", and the data
    is what the model was shown.

    Does NOT catch digits that are part of a name — `SalesDetails_2024`,
    `uat531`, `475733ba835e`. See `_NUMBER_TOKEN` for the measured reason.
    """
    cleaned = text or ""
    for pat in _DATE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    return [m.group(0) for m in _NUMBER_TOKEN.finditer(cleaned)]


def canonical(token: str) -> Optional[float]:
    """Turn a written figure into a comparable magnitude, or None."""
    t = (token or "").strip().replace(",", "").upper()
    if not t:
        return None
    mult = 1.0
    if t.endswith("%"):
        t = t[:-1]
    elif t.endswith("B"):
        mult, t = 1e9, t[:-1]
    elif t.endswith("M"):
        mult, t = 1e6, t[:-1]
    elif t.endswith("K"):
        mult, t = 1e3, t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


# A categorical column with more distinct values than this is an identifier, not
# a grouping anyone summarises by; grouping on it would cost time and add nothing.
MAX_GROUP_CARDINALITY = 250


def data_magnitudes(datasets: List[Dict[str, Any]]) -> List[float]:
    """Every figure a truthful sentence could legitimately state.

    Not just the cells. A summary of a two-dimensional result says things like
    "the leading group reaches 4.03B" — a group TOTAL, which is the sum of that
    group's rows and appears in no single cell. Checking against cells alone
    rejected those as invented; two live runs each dropped a true finding that
    way. What the writer is allowed to do is add up what it can see, so the pool
    has to contain those sums too:

      * every cell value
      * every column's total and mean
      * every per-group total (group by each low-cardinality categorical column,
        sum each numeric column) — i.e. exactly the aggregates a summary cites

    It stays a whitelist of values DERIVABLE from the data. A figure that is none
    of these still cannot be published, which is the property that matters: the
    observed fabrication (a stated average of 11,499 against a true 11,488.57) is
    not a cell, a column total, a mean, or any group total.

    Each dataset is a dict with a `rows` key holding a list of row dicts — the
    same shape on both call sites, so neither has to reshape for the other.
    """
    out: List[float] = []
    for viz in datasets or []:
        rows = [r for r in ((viz or {}).get("rows") or []) if isinstance(r, dict)]
        if not rows:
            continue

        numeric_cols: Dict[str, List[float]] = {}
        categorical_cols: Dict[str, set] = {}
        for row in rows:
            for k, v in row.items():
                if isinstance(v, bool) or v is None:
                    continue
                if isinstance(v, (int, float)):
                    out.append(float(v))
                    numeric_cols.setdefault(k, []).append(float(v))
                elif isinstance(v, str) and len(v) <= 80:
                    categorical_cols.setdefault(k, set()).add(v)

        # Column totals and means.
        for values in numeric_cols.values():
            if values:
                out.append(sum(values))
                out.append(sum(values) / len(values))

        # Per-group totals: the shape of nearly every real finding.
        for cat, distinct in categorical_cols.items():
            if not distinct or len(distinct) > MAX_GROUP_CARDINALITY:
                continue
            for num_col in numeric_cols:
                sums: Dict[str, float] = {}
                for row in rows:
                    key = row.get(cat)
                    val = row.get(num_col)
                    if isinstance(key, str) and isinstance(val, (int, float)) and not isinstance(val, bool):
                        sums[key] = sums.get(key, 0.0) + float(val)
                out.extend(sums.values())

    return out


def write_precision_slack(token: str) -> float:
    """The largest rounding error the token's OWN notation can justify.

    A flat percentage tolerance cannot work here, and getting this wrong in
    either direction is the whole difficulty:

      * Too tight and "104.8B" is rejected for 104,781,422,679 — correct
        reporting called a fabrication.
      * Too loose and the real failure survives: "11,499" against a true
        11,488.57 is only 0.09% out, so ANY tolerance above a tenth of a percent
        waves through the exact number this check exists to stop.

    So derive the allowance from how precisely the figure was written. "104.8B"
    is stated to a tenth of a billion, and may legitimately be anything within
    half of that. "11,499" is stated to the unit, so it may be out by at most
    half a unit — and 10.43 is not half a unit. Precision is a claim; hold the
    writer to it.
    """
    t = (token or "").strip().replace(",", "").upper()
    mult = 1.0
    if t.endswith("B"):
        mult, t = 1e9, t[:-1]
    elif t.endswith("M"):
        mult, t = 1e6, t[:-1]
    elif t.endswith("K"):
        mult, t = 1e3, t[:-1]
    decimals = len(t.split(".")[1]) if "." in t else 0
    ulp = (10.0 ** -decimals) * mult
    return ulp / 2.0


def is_grounded(token: str, magnitudes: List[float]) -> bool:
    """True when `token` is a value the data can actually justify.

    Percentages, growth rates and shares are computed FROM the values and will
    not appear among them, so a bare percentage is accepted — the check targets
    invented magnitudes, which is where every observed failure has been.
    """
    if token.strip().endswith("%"):
        return True
    value = canonical(token)
    if value is None:
        return False
    if value == 0:
        return True

    slack = write_precision_slack(token)
    for m in magnitudes:
        if abs(value - m) <= slack:
            return True

    # Small integers are structural — counts of periods, groups, ranks, "top
    # 10". They describe the shape of the data, not a measured quantity, and
    # rejecting them would make every well-written sentence fail.
    if abs(value) < 1000 and float(value).is_integer():
        return True
    return False


def ungrounded_tokens(text: str, magnitudes: List[float]) -> List[str]:
    """The figures in `text` that `magnitudes` cannot justify."""
    return [tok for tok in numbers_in(text) if not is_grounded(tok, magnitudes)]


# ---------------------------------------------------------------------------
# The narrative surface
# ---------------------------------------------------------------------------

# What is left of a line once its prose is gone: a bullet, a numbering marker,
# a heading rule. Kept as its own test so an emptied bullet does not survive as
# a dangling "- " with nothing after it.
_STRUCTURE_ONLY = re.compile(r"^[\s\-\*\+#>\d\.\)\|:]*$")

# Sentence boundary: terminal punctuation followed by whitespace. `\d\.\d` never
# matches because a decimal point has no space after it, so "1.5 million" stays
# one token — the reason this is a lookbehind on the punctuation rather than a
# split on ".".
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# When every sentence in a narrative made an unjustifiable claim there is no
# honest prose left to show. Returning an empty answer would look like a crash,
# and inventing a replacement summary is the very thing this module exists to
# prevent — so state the situation, with no figure in it.
EMPTY_NARRATIVE_NOTICE = (
    "The figures in this answer could not be verified against the data from this "
    "run, so they have been withheld. Please re-run the question."
)


@dataclass
class NarrativeVerdict:
    """The outcome of checking one narrative."""

    #: The text to persist and show. Unchanged when nothing was checked.
    text: str
    #: Whether the check actually ran (False = failed open, text is untouched).
    checked: bool = False
    #: One entry per removed sentence, for the log.
    dropped: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.dropped)


def verify_narrative(text: str, datasets: List[Dict[str, Any]]) -> NarrativeVerdict:
    """Remove any sentence stating a figure the run's data cannot justify.

    WHY DROP THE SENTENCE rather than correct the figure. `verify_findings`
    drops the whole finding, deliberately, and the reasoning carries over: a
    wrong figure has no single right replacement. The observed fabrication
    claimed a total that matched no dataset in the run — substituting "the"
    grounded value would mean picking one of hundreds of derivable magnitudes
    and putting it in the writer's mouth, producing a sentence the writer never
    wrote and nobody can be held to. Removing the claim is the only edit that
    cannot itself be wrong. The sentence is the unit because the sentence is the
    unit of claim: dropping the whole answer over one bad number would throw
    away correct work, and dropping the token alone would leave a mutilated
    clause ("total sales reached ") that reads as a rendering bug.

    FAILS OPEN. If there are no datasets to check against, or anything raises,
    the narrative passes through untouched — a broken verifier must never blank
    an answer. But a figure that IS checked and IS ungrounded does not survive.
    """
    original = text or ""
    if not original.strip():
        return NarrativeVerdict(text=original)

    try:
        if not enabled():
            return NarrativeVerdict(text=original)

        magnitudes = data_magnitudes(datasets)
        if not magnitudes:
            # Nothing to check against. This is the "cannot resolve the run's
            # data" case — a run with no stored rows, or a lookup that came back
            # empty — and it is indistinguishable from infrastructure trouble
            # from here, so it is treated as such.
            return NarrativeVerdict(text=original)

        dropped: List[str] = []
        kept_lines: List[str] = []
        for line in original.split("\n"):
            if not line.strip():
                kept_lines.append(line)
                continue

            sentences = _SENTENCE_SPLIT.split(line)
            kept_sentences = []
            for sentence in sentences:
                bad = ungrounded_tokens(sentence, magnitudes)
                if bad:
                    dropped.append(f"{sentence.strip()[:120]} — ungrounded: {', '.join(bad[:3])}")
                    continue
                kept_sentences.append(sentence)

            if not kept_sentences:
                continue
            rebuilt = " ".join(s for s in kept_sentences if s)
            if _STRUCTURE_ONLY.match(rebuilt):
                # Only a bullet marker survived; the claim it introduced is gone.
                continue
            kept_lines.append(rebuilt)

        if not dropped:
            return NarrativeVerdict(text=original, checked=True)

        rebuilt_text = "\n".join(kept_lines).strip()
        if not rebuilt_text:
            rebuilt_text = EMPTY_NARRATIVE_NOTICE

        logger.warning(
            "narrative grounding: removed %d sentence(s) — %s",
            len(dropped), " | ".join(dropped[:3]),
        )
        return NarrativeVerdict(text=rebuilt_text, checked=True, dropped=dropped)
    except Exception as exc:  # pragma: no cover - defensive
        # Fail OPEN, loudly. A verifier that throws must cost the user nothing.
        logger.warning("narrative grounding skipped: %s", exc)
        return NarrativeVerdict(text=original)


def enabled() -> bool:
    """Is the narrative check on? Default ON, like the other artifact gates.

    ★ Uses `_read_bool_setting`, which type-checks the value instead of reading
    its truthiness: the string "off" is TRUTHY in both Python and JS, and this
    codebase has been bitten by that three times. Imported lazily because the
    module that owns it pulls the whole tool stack.
    """
    try:
        from app.ai.tools.implementations.create_artifact import _read_bool_setting

        return _read_bool_setting("hybrid_narrative_grounding", True)
    except Exception:
        return True
