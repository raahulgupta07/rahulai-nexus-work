"""Deterministic data-quality signals over an analytical result.

Why this exists
---------------
A monthly trend was charted, narrated, and published with the sentence
"confidence is high on direction of monthly volume". One period in that series
sat at roughly a sixth of both its neighbours while the same period carried the
highest row count of the year. A value falling 84% while volume rises is not a
business result; it is a broken number. Nothing flagged it. Asked about that one
period directly, the product found the cause in under a minute — so the
capability was never missing. What was missing was anything that PROMPTED a
look.

So the look is now taken automatically, and it is taken on the SHAPE of the
result rather than on any cause. The cause that day was a column that was
NULL for most of the period's rows. Next time it will be a wrong join, a unit
change mid-series, a partial load, or a currency switch. All of them produce the
same shape: a figure moves by a factor that the volume underneath it does not
explain. Detecting the shape catches the whole family; detecting "NULL-heavy
column" catches one member of it and nothing else.

Two shapes are checked
----------------------
1. **Co-movement divergence** — the strong one, because it carries its own
   corroboration. A measure jumps or collapses at one period while ANOTHER
   series in the same result (a count, a quantity, or the per-period row count)
   sits still. The stationary series is the evidence: whatever moved, it was not
   the amount of business.

2. **Isolated discontinuity** — the fallback for a result that carries only one
   number per period, where there is no second series to corroborate with. Here
   only the shape is available, so the bar is much higher (see the thresholds
   below) and the surrounding periods must agree with each other before a point
   between them is called out.

What this deliberately is NOT
-----------------------------
It is not an outlier detector and must not become one. A dashboard that
announces a data-quality problem every quiet February is noise, and noise is
ignored — which would leave the product worse off than saying nothing. Every
threshold here is set to sit clear of ordinary seasonality, and the cost of that
is stated honestly in the module's tests: a genuine 2x fault in a series with no
companion measure will pass unremarked.

Nothing in this module knows anything about any particular dataset. It reads
rows of a result: a period column found by its own values, numeric columns found
by their own types.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.artifact_insights import _period_column, _period_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds. Each one is a claim about where a business result stops and a
# broken number starts, so each one is justified here rather than tuned quietly.
# ---------------------------------------------------------------------------

# Below four periods there is no such thing as a discontinuity. Three points can
# always be read as a trend, and a two-point "series" is a comparison.
_MIN_PERIODS = 4

# What counts as "did not move". A companion series inside this band at the
# suspect period is treated as stationary. 1.5x is generous on purpose: the
# claim being made is only that the companion did not move ANYTHING like as far
# as the measure did, and the measure has to have moved at least 3x to get here.
_STABLE_BAND = 1.5

# Co-movement divergence. 3x is far outside the band the companion stayed
# inside, and the companion is doing the work of ruling out "the business moved"
# — which is the explanation an ordinary seasonal swing would offer. A measure
# tripling while the volume underneath it holds steady has no benign reading.
_DIVERGENCE_RATIO = 3.0

# Isolated discontinuity, with no companion to corroborate. Only the shape is
# available, so this has to clear every seasonal peak a real business has.
# Retail December against November runs about 2-3x; the festival multiplier
# carried in this codebase's own demo generator is 2.4x; a payday spike is under
# 2x. 5x sits clear of all of them, and the collapse that motivated this module
# was about 6x. The honest cost of that headroom: a single-series period that is
# merely half of what it should be will not be reported.
_ISOLATED_RATIO = 5.0

# What makes a companion series a WITNESS rather than just another column.
#
# The naive rule — "any other series that happens to be flat here" — is a false
# positive generator. A result carrying revenue, units and average price has a
# flat average price through a seasonal peak that units fully explain, and that
# flat column would be read as evidence the peak is broken. What actually
# licenses the inference is a series that normally moves WITH the measure and
# conspicuously did not this time. So a witness has to track: across the rest of
# the series, the two must change in the same direction most of the time.
#
# This subsumes the constant-companion problem for free. A result with one row
# per period has a constant row count, which produces no directional steps at
# all, so it cannot witness anything — which is correct, because "the row count
# did not move" is vacuous when it never moves.
_COMPANION_TRACKING_AGREEMENT = 0.7
_COMPANION_MIN_STEPS = 3

# Guards against dividing by a baseline that is itself noise.
_NEAR_ZERO = 1e-9

# Bounds. These are budget limits, not analysis choices.
_MAX_SIGNALS = 5
_MAX_NUMERIC_COLUMNS = 12
_MAX_PERIODS = 500

# A four-digit integer column is a year, not a measure. Summing it produces a
# number with no meaning and a discontinuity in it would be nonsense.
_YEAR_MIN, _YEAR_MAX = 1900, 2100

# The synthetic companion: how many source rows fell into each period. This is
# the literal "row count" the check is named for, and it is available even when
# the result carries a single measure column.
ROW_COUNT_SERIES = "__rows_per_period__"


def _flag(name: str, default: bool) -> bool:
    """Read a hybrid_* flag, type-checked.

    Re-imports the settings MODULE on every call so a test can swap the module
    attribute — `settings` is a pydantic BaseSettings instance and rejects
    assignment of fields it does not declare, so patching the instance is not
    available. And the value is type-checked because the string "off" is truthy
    in Python, which has silently enabled a disabled feature in this codebase
    more than once.
    """
    try:
        from app.settings import config as _config

        value = getattr(_config.settings, name, default)
    except Exception:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def signals_enabled() -> bool:
    return _flag("hybrid_data_quality_signals", True)


def disclosure_enabled() -> bool:
    return _flag("hybrid_measure_disclosure", True)


# ---------------------------------------------------------------------------
# P1 — an aggregate that moves while its volume does not
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _looks_like_years(values: Sequence[float]) -> bool:
    return bool(values) and all(
        float(v).is_integer() and _YEAR_MIN <= v <= _YEAR_MAX for v in values
    )


def _label_columns(buckets: Dict[Any, Dict[str, Any]]) -> set:
    """Numeric columns that LABEL the period axis rather than measure anything.

    ★A result often carries the period more than once — a date column beside a
    `month` 1-12, a `year`, a week number, a period index. Only one of them wins
    the axis; the losers are numeric, so they used to land in the measure pool
    and get summed. A calendar cycle then looks exactly like the thing this
    module hunts: December 12 followed by January 1 is a 12x collapse with a
    flat row count beside it, which is the corroborated-discontinuity shape.
    Observed live: `month = 1` in January reported as an anomaly, and the answer
    had to talk the reader out of the product's own warning.

    The test is not a calendar test — a whitelist of month/quarter/week names
    would miss the next spelling, and would wrongly insist that a column called
    "month" can never be a measure. It is a test of DERIVABILITY, against this
    result's own axis: a column is a label when, in every period, it is

      * constant inside that period — a label does not vary within the thing it
        labels, while a measure grouped by branch or product does; and
      * equal to a component of the period this module already parsed — its
        year, its month, its day, or its quarter.

    The second condition is what makes this safe. A quantity that merely happens
    to be flat within each period (a headcount, a fixed fee, a target) is a real
    measure and keeps its place; only a column that RESTATES the axis is
    removed, and "restates" means the value is computable from the axis rather
    than merely correlated with it.

    ★ An earlier version of this used "constant inside, distinct across" instead
      of derivability. It fails on the one case that matters: a cycling index
      repeats (…, 11, 12, 1, 2 …), so at the year boundary — precisely where the
      false positive lives — the values are NOT distinct and the label slipped
      straight back into the measure pool.

    Both conditions are only meaningful when the result actually groups: with
    one row per period every column is trivially constant inside its period, so
    an unguarded rule would eat the measures and go permanently silent. That
    case is excluded and keeps its existing treatment.
    """
    grouped = any(b["count"] > 1 for b in buckets.values())
    if not grouped or len(buckets) < 2:
        return set()

    columns: set = set()
    for bucket in buckets.values():
        columns.update(bucket["distinct"].keys())

    labels = set()
    for column in columns:
        matches = True
        for key, bucket in buckets.items():
            values = bucket["distinct"].get(column)
            if not values or len(values) != 1:
                matches = False  # varies inside the period → a measure
                break
            value = next(iter(values))
            if value not in _axis_components(key):
                matches = False
                break
        if matches:
            labels.add(column)
    return labels


def _axis_components(key: Any) -> set:
    """Every number the period key itself already states.

    ``_period_key`` yields ``(year, sub)`` where ``sub`` encodes month*100+day
    for a dated period, month*100 for a month, quarter*3 for a quarter, and 0
    for a bare year. Anything a caller could have written as a separate column
    comes out of those two numbers.
    """
    try:
        year, sub = key
        year, sub = int(year), int(sub)
    except Exception:
        return set()
    out = {float(year)}
    if sub:
        month, day = divmod(sub, 100)
        if month:
            out.add(float(month))
            out.add(float((month - 1) // 3 + 1))  # quarter
        if day:
            out.add(float(day))
        out.add(float(sub))
    return out


def _build_period_series(
    rows: List[Dict[str, Any]]
) -> Tuple[Optional[str], List[str], Dict[str, List[float]]]:
    """Collapse rows onto their period axis.

    Returns ``(period_column, ordered_period_labels, {column: values})``. Rows
    sharing a period are summed, so a result grouped by period AND by something
    else (branch, product, channel) still yields one series per measure — which
    is exactly the total a chart of that result would plot.

    Columns that merely spell the period a second way are dropped before any
    of that; see :func:`_label_columns`.
    """
    period_col = _period_column(rows)
    if not period_col:
        return None, [], {}

    buckets: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        key = _period_key(row.get(period_col))
        if key is None:
            continue
        bucket = buckets.setdefault(
            key,
            {
                "label": str(row.get(period_col)),
                "sums": {},
                "count": 0.0,
                # Raw per-period values, kept so a column can be recognised as a
                # second spelling of the axis. Summing destroys the evidence:
                # a month column summed over 80 branches is 80x the month.
                "distinct": {},
            },
        )
        bucket["count"] += 1.0
        for column, value in row.items():
            if column == period_col or not _is_number(value):
                continue
            bucket["sums"][column] = bucket["sums"].get(column, 0.0) + float(value)
            seen = bucket["distinct"].setdefault(column, set())
            if len(seen) <= 2:  # one value is enough to decide; cap the memory
                seen.add(float(value))

    for column in _label_columns(buckets):
        for bucket in buckets.values():
            bucket["sums"].pop(column, None)

    if len(buckets) < _MIN_PERIODS or len(buckets) > _MAX_PERIODS:
        return period_col, [], {}

    ordered_keys = sorted(buckets)
    labels = [buckets[k]["label"] for k in ordered_keys]

    columns: List[str] = []
    for k in ordered_keys:
        for column in buckets[k]["sums"]:
            if column not in columns:
                columns.append(column)
    columns = columns[:_MAX_NUMERIC_COLUMNS]

    series: Dict[str, List[float]] = {
        column: [float(buckets[k]["sums"].get(column, 0.0)) for k in ordered_keys]
        for column in columns
    }
    series = {c: v for c, v in series.items() if not _looks_like_years(v)}
    series[ROW_COUNT_SERIES] = [float(buckets[k]["count"]) for k in ordered_keys]
    return period_col, labels, series


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _spread(values: Sequence[float]) -> float:
    """How far apart the values in a window are, as a ratio. inf if any is ~0."""
    magnitudes = [abs(v) for v in values]
    low, high = min(magnitudes), max(magnitudes)
    if low <= _NEAR_ZERO:
        return float("inf") if high > _NEAR_ZERO else 1.0
    return high / low


def _baseline_around(values: Sequence[float], index: int) -> Tuple[Optional[float], bool]:
    """What this period would look like if it behaved like its surroundings.

    Returns ``(baseline, surroundings_agree)``. For an interior point the
    surroundings are its two immediate neighbours; ``surroundings_agree`` says
    whether those two are within ``_STABLE_BAND`` of each other, which is what
    separates "a hole between two similar periods" from "a step in a trend".

    An endpoint has only one neighbour, so it is compared against the nearest
    three remaining periods and those three must agree among themselves. Without
    that, the first or last period of any rising series would be reported. This
    matters: a partially-loaded most-recent period is one of the commonest real
    faults there is, and dropping endpoints entirely would miss all of them.
    """
    n = len(values)
    if n < _MIN_PERIODS:
        return None, False
    if 0 < index < n - 1:
        window = [values[index - 1], values[index + 1]]
    elif index == 0:
        window = list(values[1:4])
    else:
        window = list(values[max(0, n - 4) : n - 1])
    if len(window) < 2:
        return None, False
    return _median(window), _spread(window) <= _STABLE_BAND


def _move_factor(value: float, baseline: Optional[float]) -> float:
    """How many times over the value differs from its baseline. 1.0 = no move."""
    if baseline is None or abs(baseline) <= _NEAR_ZERO:
        return 1.0
    if abs(value) <= _NEAR_ZERO:
        return float("inf")
    ratio = abs(value) / abs(baseline)
    return max(ratio, 1.0 / ratio)


def _tracks(measure: Sequence[float], companion: Sequence[float], skip: int) -> bool:
    """Does the companion normally move with the measure?

    Steps touching the suspect period are excluded — that period is the thing in
    question and cannot be part of its own evidence. Steps where either series
    barely moves carry no direction and are not counted either way.
    """
    agree = comparable = 0
    for i in range(len(measure) - 1):
        if i == skip or i + 1 == skip:
            continue
        d_measure = measure[i + 1] - measure[i]
        d_companion = companion[i + 1] - companion[i]
        if abs(d_measure) <= _NEAR_ZERO or abs(d_companion) <= _NEAR_ZERO:
            continue
        comparable += 1
        if (d_measure > 0) == (d_companion > 0):
            agree += 1
    if comparable < _COMPANION_MIN_STEPS:
        return False
    return agree / comparable >= _COMPANION_TRACKING_AGREEMENT


def _companion_columns(
    series: Dict[str, List[float]], measure: str, values: Sequence[float], index: int
) -> List[str]:
    """Series whose stillness at `index` would actually mean something."""
    return [
        column
        for column, companion in series.items()
        if column != measure and _tracks(values, companion, index)
    ]


def _describe(column: str) -> str:
    return "row count" if column == ROW_COUNT_SERIES else f"'{column}'"


def detect_discontinuities(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Periods where a figure moved by a factor its volume does not explain.

    Pure. Takes rows as they appear in a result, returns a list of signals,
    each naming the column, the period, the observed value, what the
    surrounding periods would have led you to expect, and — when the finding is
    corroborated — which other series stayed still.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if len(rows) < _MIN_PERIODS:
        return []

    period_col, labels, series = _build_period_series(rows)
    if not period_col or not labels:
        return []

    signals: List[Dict[str, Any]] = []
    for measure, values in series.items():
        if measure == ROW_COUNT_SERIES:
            continue  # the row count is the witness, not the accused
        if _spread(values) < _DIVERGENCE_RATIO:
            continue  # nothing in this series moved far enough to be worth testing
        for i in range(len(values)):
            baseline, surroundings_agree = _baseline_around(values, i)
            move = _move_factor(values[i], baseline)
            if move < _DIVERGENCE_RATIO:
                continue

            witness = None
            for companion in _companion_columns(series, measure, values, i):
                c_values = series[companion]
                c_baseline, _ = _baseline_around(c_values, i)
                if c_baseline is None or abs(c_baseline) <= _NEAR_ZERO:
                    continue
                if _move_factor(c_values[i], c_baseline) < _STABLE_BAND:
                    witness = companion
                    break

            if witness is not None:
                kind = "co_movement_divergence"
            elif surroundings_agree and move >= _ISOLATED_RATIO:
                kind = "isolated_discontinuity"
            else:
                continue

            direction = "collapses" if abs(values[i]) < abs(baseline or 0.0) else "jumps"
            factor = "beyond measure" if move == float("inf") else f"{move:.1f}x"
            message = (
                f"'{measure}' {direction} to {values[i]:,.4g} at {labels[i]} "
                f"({factor} away from the {baseline:,.4g} its neighbouring periods "
                f"would suggest)"
            )
            if witness is not None:
                message += f", while {_describe(witness)} holds steady"
            message += "."

            signals.append(
                {
                    "kind": kind,
                    "column": measure,
                    "period_column": period_col,
                    "period": labels[i],
                    "value": values[i],
                    "expected_around": baseline,
                    "move_factor": None if move == float("inf") else round(move, 3),
                    "steady_series": witness,
                    "message": message,
                }
            )
            if len(signals) >= _MAX_SIGNALS:
                return signals
    return signals


# What the model is told when a signal fires. The ceiling is the operative part:
# the tie between P1 and the confidence rule is that an unexplained
# discontinuity removes "high" from the vocabulary, and the ceiling is what says
# so in a form a prompt rule and a text filter can both act on.
CONFIDENCE_CEILING_UNVERIFIED = "medium"

_GUIDANCE = (
    "A figure in this result moves by a factor that the volume underneath it does "
    "not explain. Treat this as a possible data-quality fault, not as a finding: "
    "before charting or narrating this series, check the affected period at source "
    "(NULL or zero values in the aggregated column, a partial load, a unit or "
    "currency change, a join that drops rows). Say what you checked and what you "
    "found. Until it is explained, this series carries an unexplained "
    "discontinuity and you may NOT state high confidence in it or in any trend "
    "drawn from it — say 'medium' or lower and name this as the reason."
)


def analyze_result(
    rows: List[Dict[str, Any]], truncated: bool = False
) -> Optional[Dict[str, Any]]:
    """The whole P1 check for one result, or None when there is nothing to say.

    ``truncated`` must be passed honestly. A result cut to a prefix in the
    query's own sort order has a manufactured last period, and reporting that as
    a collapse would be the check inventing its own false positive.
    """
    if truncated:
        return None
    try:
        discontinuities = detect_discontinuities(rows)
    except Exception as exc:  # a signal must never be able to fail a tool
        logger.warning("data-quality check failed, continuing without it: %s", exc)
        return None
    if not discontinuities:
        return None
    return {
        "discontinuities": discontinuities,
        "confidence_ceiling": CONFIDENCE_CEILING_UNVERIFIED,
        "guidance": _GUIDANCE,
    }


# ---------------------------------------------------------------------------
# The confidence tie-in, as an actual filter and not only as an instruction
# ---------------------------------------------------------------------------

_HIGH_CONFIDENCE_PATTERNS = (
    re.compile(r"\bconfidence\s*(?:is|:|level\s*(?:is|:)?)?\s*(?:very\s+)?high\b", re.I),
    re.compile(r"\b(?:very\s+)?high\s+confidence\b", re.I),
    re.compile(r"\b(?:highly|very)\s+confident\b", re.I),
    re.compile(r"\bwith\s+certainty\b", re.I),
    re.compile(r"\b(?:we\s+can\s+be\s+)?certain\s+that\b", re.I),
)


def asserts_high_confidence(text: str) -> bool:
    """True when a sentence claims a confidence level a discontinuity forbids."""
    return any(p.search(text or "") for p in _HIGH_CONFIDENCE_PATTERNS)


def reject_overconfident(
    findings: List[Dict[str, Any]], ceiling: Optional[str]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Drop findings that assert high confidence while a ceiling is in force.

    Dropping rather than rewording follows the grounding check next door: a
    claim that cannot be justified is removed, not softened into something the
    writer never wrote. Returns ``(kept, rejected_reasons)``.
    """
    if not ceiling or ceiling == "high":
        return list(findings or []), []
    kept, rejected = [], []
    for finding in findings or []:
        text = (finding or {}).get("text") or ""
        if asserts_high_confidence(text):
            rejected.append(f"{text[:90]} — high confidence over an unexplained discontinuity")
            continue
        kept.append(finding)
    return kept, rejected


# ---------------------------------------------------------------------------
# P2 — which column answered the question
# ---------------------------------------------------------------------------

_AGG_CALL = re.compile(
    r"\b(sum|avg|average|mean|median|min|max|count|count_big|total)\s*\(\s*"
    r"(distinct\s+)?([^(),]*?)\s*\)",
    re.IGNORECASE,
)

_AGG_ALIASES = {"average": "avg", "mean": "avg", "count_big": "count", "total": "sum"}

# A bare column reference, once qualifiers and quoting are stripped. Anything
# containing an operator, a literal or a nested call is an expression, and
# naming an expression as "the column chosen" would be a lie.
_BARE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")


def _normalize_column(raw: str) -> Optional[str]:
    text = (raw or "").strip()
    if not text or text == "*":
        return None
    text = text.replace("`", "").replace('"', "").replace("[", "").replace("]", "")
    text = text.split(".")[-1].strip()
    if not _BARE_COLUMN.match(text):
        return None
    return text


def extract_measure_selection(
    queries: Optional[Sequence[Any]],
) -> Optional[Dict[str, Any]]:
    """Which columns this turn aggregated, read off the SQL it actually ran.

    Read from the executed queries rather than inferred from the question,
    because the executed queries are what produced the number. Expressions and
    ``COUNT(*)`` are skipped: only a plain column reference can honestly be
    reported as "the column I chose".
    """
    aggregations: List[Dict[str, str]] = []
    seen = set()
    for query in queries or []:
        text = query if isinstance(query, str) else (query or {}).get("query") if isinstance(query, dict) else None
        if not isinstance(text, str):
            continue
        for match in _AGG_CALL.finditer(text):
            fn = match.group(1).lower()
            fn = _AGG_ALIASES.get(fn, fn)
            column = _normalize_column(match.group(3))
            if not column:
                continue
            key = (fn, column.lower())
            if key in seen:
                continue
            seen.add(key)
            aggregations.append({"function": fn, "column": column})
    if not aggregations:
        return None
    return {
        "aggregations": aggregations,
        "columns": sorted({a["column"] for a in aggregations}, key=str.lower),
        "note": (
            "Several columns can usually answer the same question. State in your "
            "answer which column you aggregated, and use the same one for the rest "
            "of this conversation unless the user asks for a different basis."
        ),
    }


def detect_measure_drift(
    previous: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The same aggregation, applied to a different column than last turn.

    The defect this catches is not the choice — either column was defensible.
    It is changing basis mid-thread without saying so, which produced two
    totals 4% apart and no way for the reader to know why.
    """
    if not previous or not current:
        return None
    changes = []
    for fn in sorted({a["function"] for a in current.get("aggregations") or []}):
        prev_cols = {
            a["column"].lower()
            for a in previous.get("aggregations") or []
            if a["function"] == fn
        }
        curr_cols = {
            a["column"].lower()
            for a in current.get("aggregations") or []
            if a["function"] == fn
        }
        if prev_cols and curr_cols and prev_cols != curr_cols:
            changes.append(
                {
                    "function": fn,
                    "previous_columns": sorted(prev_cols),
                    "current_columns": sorted(curr_cols),
                }
            )
    if not changes:
        return None
    return {
        "changes": changes,
        "guidance": (
            "This turn aggregated a different column than an earlier turn in this "
            "conversation did for the same kind of figure. Either reuse the earlier "
            "column, or say plainly in your answer that you changed the basis and "
            "why — two totals from two columns are not comparable and must never be "
            "presented as if they were."
        ),
    }
