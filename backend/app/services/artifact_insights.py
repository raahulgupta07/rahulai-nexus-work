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


# Dates are not magnitude claims. "rose from 5.39B in Jan 2025 to 8.02B in May
# 2026" contains four number-like tokens, but two of them are YEARS — they say
# WHEN, not HOW MUCH, and no year will ever appear among a dataset's measures.
# Left in, they made every correctly-grounded finding fail. Stripped first.
_DATE_PATTERNS = (
    re.compile(r"\b(19|20)\d{2}-\d{1,2}(-\d{1,2})?\b"),   # 2025-01, 2025-01-31
    re.compile(r"\b(19|20)\d{2}\b"),                        # 2025
    re.compile(r"\bQ[1-4]\b", re.IGNORECASE),               # Q3
)


def _numbers_in(text: str) -> List[str]:
    """Every number-like token in a string, as written, excluding dates.

    Catches `104.8B`, `9,120,492`, `48.8%`, `11,489`, `5.39B`. Deliberately
    string-level: the check is "did this figure come from the data", and the data
    is what the model was shown.
    """
    cleaned = text or ""
    for pat in _DATE_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    return re.findall(r"\d[\d,]*\.?\d*\s*[BMK%]?", cleaned)


def _canonical(token: str) -> Optional[float]:
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
_MAX_GROUP_CARDINALITY = 250


def _data_magnitudes(visualizations: List[Dict[str, Any]]) -> List[float]:
    """Every figure a truthful finding could legitimately state.

    Not just the cells. A summary of a branch x month table says things like
    "Mawlamyine leads with 4.03B" — a branch TOTAL, which is the sum of that
    branch's seventeen monthly rows and appears in no single cell. Checking
    against cells alone rejected those as invented; two live runs each dropped a
    true finding that way. What the model is allowed to do is add up what it can
    see, so the pool has to contain those sums too:

      * every cell value
      * every column's total and mean
      * every per-group total (group by each low-cardinality categorical column,
        sum each numeric column) — i.e. exactly the aggregates a summary cites

    It stays a whitelist of values DERIVABLE from the data. A figure that is none
    of these still cannot be published, which is the property that matters: the
    observed fabrication (an average order value of 11,499 against a true
    11,488.57) is not a cell, a column total, a mean, or any group total.
    """
    out: List[float] = []
    for viz in visualizations or []:
        rows = [r for r in (viz.get("rows") or []) if isinstance(r, dict)]
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
            if not distinct or len(distinct) > _MAX_GROUP_CARDINALITY:
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


def _write_precision_slack(token: str) -> float:
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


def _is_grounded(token: str, magnitudes: List[float]) -> bool:
    """True when `token` is a value the data can actually justify.

    Percentages, growth rates and shares are computed FROM the values and will
    not appear among them, so a bare percentage is accepted — the check targets
    invented magnitudes, which is where every observed failure has been.
    """
    if token.strip().endswith("%"):
        return True
    value = _canonical(token)
    if value is None:
        return False
    if value == 0:
        return True

    slack = _write_precision_slack(token)
    for m in magnitudes:
        if abs(value - m) <= slack:
            return True

    # Small integers are structural — counts of months, branches, ranks, "top
    # 10". They describe the shape of the data, not a measured quantity, and
    # rejecting them would make every well-written finding fail.
    if abs(value) < 1000 and float(value).is_integer():
        return True
    return False


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

    return kept, rejected


def build_prompt(title: str, visualizations: List[Dict[str, Any]]) -> str:
    """The summariser prompt. Data only — no narrative framing to copy from."""
    blocks = []
    for viz in visualizations or []:
        rows = (viz.get("rows") or [])[:_MAX_PROMPT_ROWS]
        blocks.append(
            f"<visualization id=\"{viz.get('id')}\" title=\"{viz.get('title')}\" "
            f"rows=\"{viz.get('row_count')}\">\n{json.dumps(rows, default=str)[:6000]}\n</visualization>"
        )
    data_block = "\n\n".join(blocks)

    return f"""You are writing the summary panel that sits above a dashboard titled "{title}".

The reader should understand what the data says without reading every tile.

{data_block}

Write:
  - ONE headline sentence: the single most useful thing about this data.
  - Up to {_MAX_FINDINGS} findings. Prefer, in this order: direction of travel over
    time, concentration (does a small group dominate?), notable outliers, and any
    clear change in mix. Each finding must be a complete sentence with the figures
    in it.

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
