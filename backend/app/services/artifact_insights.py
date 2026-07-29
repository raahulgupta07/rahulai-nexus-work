"""PHASE 4 — a grounded narrative summary for a dashboard.

A dashboard is a wall of numbers. The reader has to scan tiles and charts and
work the story out. This produces the story: one headline plus a few findings,
generated from the FINAL data the dashboard was built on.

Grounding is the whole point
----------------------------
An LLM asked to summarise numbers will occasionally produce a number that came
from nowhere. That is not hypothetical here — during testing a dashboard summary
reported an average order value of 11,499 when the true figure was 11,488.57.
The dashboard tile was right; the sentence beside it was invented. Small (0.09%),
confident, and completely untraceable: the value appeared in no query result, no
generated code, no tool output. It matched no plausible alternative formula
either — not the weighted mean, not the unweighted mean of monthly values, not
the median, last, or max month.

So every figure a finding claims is checked against the dashboard's own data
before anything is stored. A finding citing a number that appears nowhere is
dropped. This turns "the model usually gets it right" into "a wrong number
cannot be published", which is a different guarantee.

Recency is a selection problem, not a prompt problem
---------------------------------------------------
A dashboard built on 2023-Q1 through 2025-Q4 produced a headline and four
findings that all described 2023-Q1 vs 2023-Q2 — the two OLDEST quarters. Every
figure was exact and the grounding check passed, because nothing was wrong with
the numbers. What was wrong was WHICH numbers: the summariser read the rows in
the order it was handed them, and a period-ordered result starts at the
beginning of time.

So the rows are now sorted by their own period column and the window handed to
the model is the MOST RECENT one, with the span stated on each visualization so
a finding can name the period it is talking about. Telling the model "prefer
recent data" while feeding it the oldest rows first would be asking it to
compensate for its input; fixing the input is the actual fix, and the prompt
rule is there to make the period explicit in the text.

Deliberately NOT filter-aware
-----------------------------
The summary is generated once, from the complete aggregates, and describes the
whole dataset. Recomputing it per filter interaction would need either a model
call per click or client-side regeneration. Worth doing later; excluded here so
the claim stays honest — the panel describes the dashboard as built.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# How many rows of each visualization the summariser is allowed to see. The
# grounding check reads the FULL data; this only bounds the prompt.
_MAX_PROMPT_ROWS = 60
_MAX_FINDINGS = 5


# The grounding check itself now lives in `figure_grounding`, because the chat
# narrative the agent writes beside a dashboard needs exactly the same guarantee
# and a second copy of this logic would drift. The names below are re-exports,
# not wrappers: same objects, same behaviour, and `from app.services
# .artifact_insights import _is_grounded` keeps working for every existing
# caller and test.
from app.services.figure_grounding import (  # noqa: E402  (deliberate: see above)
    _DATE_PATTERNS,
    MAX_GROUP_CARDINALITY as _MAX_GROUP_CARDINALITY,
    canonical as _canonical,
    data_magnitudes as _data_magnitudes,
    is_grounded as _is_grounded,
    numbers_in as _numbers_in,
    write_precision_slack as _write_precision_slack,
)


def verify_findings(
    findings: List[Dict[str, Any]], visualizations: List[Dict[str, Any]]
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Drop findings containing a figure that appears nowhere in the data.

    Returns ``(kept, rejected_reasons)``. Rejection is logged rather than
    silently swallowed — a summariser that keeps inventing numbers is worth
    knowing about.
    """
    magnitudes = _data_magnitudes(visualizations)
    kept: List[Dict[str, Any]] = []
    rejected: List[str] = []

    for f in findings or []:
        text = (f or {}).get("text") or ""
        if not text.strip():
            continue
        bad = [
            tok for tok in _numbers_in(text)
            if not _is_grounded(tok, magnitudes)
        ]
        if bad:
            rejected.append(f"{text[:90]} — ungrounded: {', '.join(bad[:3])}")
            continue
        kept.append(f)

    # A figure can be perfectly grounded and still be broken. Grounding proves a
    # number came from the data; it says nothing about whether the data is sound.
    # So a second gate: if this artifact's own series carries a discontinuity
    # nobody has explained, a finding is not allowed to assert high confidence
    # over it. Dropped rather than reworded, for the same reason the grounding
    # check drops — a claim that cannot be justified is removed, not softened
    # into a sentence the writer never wrote.
    #
    # Imported here, not at module scope: data_quality reads this module's period
    # helpers, so a top-level import in this direction is a cycle.
    try:
        from app.services import data_quality

        if data_quality.signals_enabled():
            ceiling = None
            for viz in visualizations or []:
                if data_quality.analyze_result(viz.get("rows") or []):
                    ceiling = data_quality.CONFIDENCE_CEILING_UNVERIFIED
                    break
            if ceiling:
                kept, overconfident = data_quality.reject_overconfident(kept, ceiling)
                rejected.extend(overconfident)
    except Exception as exc:
        logger.warning("confidence gate skipped: %s", exc)

    return kept, rejected


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Every shape a period is written in, most specific first. Each yields a
# (year, sub-period) key that sorts chronologically. Quarters are scaled onto
# months so a quarter column and a month column order the same way.
_PERIOD_FORMS = (
    # 2025-01-31, 2025-01-31T09:00:00, 2025/01/31. `(?!\d)` rather than `\b`:
    # the boundary after "31" in "…-31T09" is between two word characters, so
    # `\b` does not match there and every ISO timestamp would be missed.
    (re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)"),
     lambda m: (int(m.group(1)), int(m.group(2)) * 100 + int(m.group(3)))),
    # 2025-Q3, 2025Q3, Q3-2025, Q3 2025
    (re.compile(r"^(\d{4})[-\s]?Q([1-4])$", re.IGNORECASE),
     lambda m: (int(m.group(1)), int(m.group(2)) * 3)),
    (re.compile(r"^Q([1-4])[-\s]?(\d{4})$", re.IGNORECASE),
     lambda m: (int(m.group(2)), int(m.group(1)) * 3)),
    # 2025-01, 2025/01
    (re.compile(r"^(\d{4})[-/](\d{1,2})$"),
     lambda m: (int(m.group(1)), int(m.group(2)) * 100)),
    # Jan 2025, January 2025. Anchored on the actual month names — a loose
    # `[A-Za-z]+ \d{4}` would read "Region 2024" as a date.
    (re.compile(r"^(" + "|".join(_MONTHS) + r")[a-z]*[-\s](\d{4})$", re.IGNORECASE),
     lambda m: (int(m.group(2)), _MONTHS[m.group(1).lower()] * 100)),
    # 2025 (bare year)
    (re.compile(r"^(\d{4})$"),
     lambda m: (int(m.group(1)), 0)),
)

# A "year" column is often stored as a number, not a string. Anything outside
# this range is a measure that happens to be a four-digit integer, not a year.
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def _period_key(value: Any) -> Optional[tuple]:
    """A sortable (year, sub-period) key for a cell, or None if it isn't a period."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        year = int(value)
        return (year, 0) if _YEAR_MIN <= year <= _YEAR_MAX else None
    if not isinstance(value, str):
        # datetime / date objects sort natively; give them a key of their own.
        for attr in ("year",):
            if hasattr(value, attr):
                month = getattr(value, "month", 0) or 0
                day = getattr(value, "day", 0) or 0
                return (int(value.year), month * 100 + day)
        return None
    text = value.strip()
    if not text:
        return None
    for pattern, to_key in _PERIOD_FORMS:
        m = pattern.match(text)
        if m:
            key = to_key(m)
            if _YEAR_MIN <= key[0] <= _YEAR_MAX:
                return key
    return None


# A column qualifies as THE period column only if nearly all of it parses. One
# stray "2024 refresh" label in a product-name column must not turn that column
# into a timeline and reorder the whole dashboard around it.
_PERIOD_COLUMN_THRESHOLD = 0.8


def _period_column(rows: List[Dict[str, Any]]) -> Optional[str]:
    """The column that carries this result's period, or None if there isn't one.

    Picks the leftmost qualifying column: a result grouped by month and by
    branch puts the time axis first by convention, and ties otherwise resolve
    arbitrarily.
    """
    if not rows:
        return None
    for col in rows[0].keys():
        present = [r.get(col) for r in rows if r.get(col) is not None]
        if len(present) < 2:
            continue
        keys = [_period_key(v) for v in present]
        parsed = [k for k in keys if k is not None]
        if len(parsed) < _PERIOD_COLUMN_THRESHOLD * len(present):
            continue
        if len(set(parsed)) < 2:
            continue  # a constant column is a label, not a timeline
        return col
    return None


def _recent_window(rows: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    """The rows to show the summariser, oldest-first, ending at the latest period.

    Returns ``(rows, earliest_label, latest_label)``. Without a period column
    this is the old behaviour — the first ``_MAX_PROMPT_ROWS`` rows and no
    period labels — because there is no timeline to be at the wrong end of.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    col = _period_column(rows)
    if not col:
        return rows[:_MAX_PROMPT_ROWS], None, None

    keyed = [(_period_key(r.get(col)), r) for r in rows]
    # Rows whose period is unreadable keep their place at the front rather than
    # being dropped — they are still data, just not part of the ordering.
    dated = sorted([(k, r) for k, r in keyed if k is not None], key=lambda kr: kr[0])
    undated = [r for k, r in keyed if k is None]
    ordered = undated + [r for _, r in dated]

    window = ordered[-_MAX_PROMPT_ROWS:] if len(ordered) > _MAX_PROMPT_ROWS else ordered
    earliest = str(dated[0][1].get(col)) if dated else None
    latest = str(dated[-1][1].get(col)) if dated else None
    return window, earliest, latest


def build_prompt(title: str, visualizations: List[Dict[str, Any]]) -> str:
    """The summariser prompt. Data only — no narrative framing to copy from."""
    blocks = []
    latest_seen: List[str] = []
    for viz in visualizations or []:
        rows, earliest, latest = _recent_window(viz.get("rows") or [])
        period_attr = ""
        if earliest and latest:
            period_attr = f' period_from="{earliest}" period_to="{latest}"'
            if latest not in latest_seen:
                latest_seen.append(latest)
        blocks.append(
            f"<visualization id=\"{viz.get('id')}\" title=\"{viz.get('title')}\" "
            f"rows=\"{viz.get('row_count')}\"{period_attr}>\n"
            f"{json.dumps(rows, default=str)[:6000]}\n</visualization>"
        )
    data_block = "\n\n".join(blocks)

    # Rows are handed over oldest-first, so "the end of the block" is a
    # statement about the data as sent, not a hope about how it is read.
    period_rules = (
        "\n  - If a finding describes a specific period, name that period in the sentence."
    )
    if latest_seen:
        period_rules = (
            "\n  - The rows are in chronological order, OLDEST FIRST. The most recent period in "
            f"this data is {', '.join(latest_seen[:3])} — it is at the END of each block.\n"
            "  - Lead with the most recent period. A comparison between the two oldest periods is "
            "almost never the most useful thing to say about a dashboard that runs to the present.\n"
            "  - NAME the period in every finding that describes one (\"in 2025-Q4\", \"from 2024-Q1 to "
            "2025-Q4\"). A figure without its period is not checkable by the reader.\n"
            "  - `period_from` / `period_to` on each visualization give its full span. If a finding "
            "describes the whole span, say so explicitly rather than leaving it implied."
        )

    return f"""You are writing the summary panel that sits above a dashboard titled "{title}".

The reader should understand what the data says without reading every tile.

{data_block}

Write:
  - ONE headline sentence: the single most useful thing about this data.
  - Up to {_MAX_FINDINGS} findings. Prefer, in this order: direction of travel over
    time, concentration (does a small group dominate?), notable outliers, and any
    clear change in mix. Each finding must be a complete sentence with the figures
    in it.

PERIOD — where in time to look:{period_rules}

RULES — these are checked automatically after you answer:
  - EVERY figure you write must come from the data above. A number that appears
    nowhere in the data will be rejected and the finding discarded.
  - Do not estimate, extrapolate or infer a value you were not given. If you want
    to state a total and no total is present, add up the values you can see.
  - Percentages and growth rates you calculate FROM the given values are fine.
  - No recommendations, no advice, no speculation about causes. Describe what is
    in the data.
  - Plain language. No jargon, no emoji, no markdown headers.

Return ONLY this JSON, nothing else:
{{"headline": "...", "findings": [{{"text": "...", "viz_id": "..."}}]}}"""


def parse_response(raw: str) -> Optional[Dict[str, Any]]:
    """Pull the JSON object out of a model reply, tolerating fences and chatter."""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1], strict=False)
    except Exception:
        return None
    if not isinstance(obj, dict) or not obj.get("headline"):
        return None
    findings = obj.get("findings")
    obj["findings"] = findings if isinstance(findings, list) else []
    return obj
