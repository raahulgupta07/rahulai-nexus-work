"""DEF-012 — a sheet's bold TOTAL row is not a transaction.

A real extract ends the way a person would write it: 15,000 data rows and then a
bold `TOTAL` row directly beneath, no blank line between. `header=4` finds the
header correctly; nothing looks for the trailer. So the agent opened its answer
with

    `Sales Data` (**15,001** transaction rows)

and every aggregate carried the sheet's own total a second time — doubling
sums, and pulling means and maxima toward a number that is not an observation.

On the run where this was measured the aggregates were NOT doubled, but only by
accident: DEF-011's date loss had already deleted most of the sheet, so the
trailer was dropped along with everything else. **Fixing the dates makes this
one start biting.** The two land together for that reason.

Why detection is conservative rather than clever
------------------------------------------------
Deleting a row from a person's data is the kind of help that is worse than no
help. A customer legitimately called "Total Logistics Ltd", or a final row that
happens to be the largest, must survive untouched. So a row is only a trailer
when the ARITHMETIC says so:

  * at least one numeric column whose last value equals the sum of the column
    above it, to a relative tolerance, and
  * either a label cell reading `Total` / `Grand Total` / `Sum`, **or** every
    numeric column in the frame summing that way.

A label alone is not enough (a company name is a label). A single coincidental
sum is not enough either (one column of two rows can sum by chance). Both
together, or arithmetic across the board, is a statement about the file rather
than a guess about it.

★And when it fires, it SAYS so. A row that vanishes silently is the same defect
in the other direction.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: What a summary row calls itself. Anchored: "Total Logistics Ltd" is a name.
_TOTAL_LABEL = re.compile(r"^\s*(grand\s+|running\s+|net\s+)?(total|totals|sum|subtotal)\s*:?\s*$", re.I)

#: Relative tolerance for "this equals the sum above". Loose enough for a sheet
#: that stores a rounded total, tight enough that a coincidence is unlikely.
_REL_TOL = 1e-6

#: Below this many rows there is not enough of a column to sum meaningfully,
#: and a two-row frame whose second row equals the first is not a total.
_MIN_ROWS = 3


def _numeric_columns(df: Any) -> List[Any]:
    """Columns whose BODY (everything above the last row) is numeric.

    Uses the frame's own dtypes rather than re-coercing: a column of strings
    that happens to look numeric has not been read as data yet, and guessing
    here would make the detector depend on parse order.
    """
    import pandas as pd

    out = []
    body = df.iloc[:-1]
    for col in df.columns:
        try:
            if not pd.api.types.is_numeric_dtype(df[col]):
                continue
            if body[col].notna().sum() >= 2:
                out.append(col)
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def _has_total_label(df: Any) -> Optional[str]:
    """The text of a label cell in the last row that names it a total."""
    last = df.iloc[-1]
    for value in last:
        try:
            if isinstance(value, str) and _TOTAL_LABEL.match(value):
                return value.strip()
        except Exception:  # pragma: no cover - defensive
            continue
    return None


def detect_total_row(df: Any) -> Optional[Dict[str, Any]]:
    """Is the LAST row a summary of the rows above it?

    Returns a disclosure dict when it is, else None. Never raises — a detector
    that can break a read is worse than one that misses.
    """
    try:
        import pandas as pd  # noqa: F401  (import guarded with the rest)

        if df is None or getattr(df, "empty", True) or len(df) < _MIN_ROWS:
            return None

        numeric = _numeric_columns(df)
        if not numeric:
            return None

        body = df.iloc[:-1]
        last = df.iloc[-1]
        matched: List[str] = []
        for col in numeric:
            expected = body[col].sum()
            actual = last[col]
            if actual is None or actual != actual:  # NaN
                continue
            scale = max(abs(float(expected)), abs(float(actual)), 1.0)
            if abs(float(actual) - float(expected)) <= _REL_TOL * scale:
                matched.append(str(col))

        if not matched:
            return None

        label = _has_total_label(df)
        all_numeric_match = len(matched) == len(numeric)
        if not label and not all_numeric_match:
            # One column summing by coincidence is not evidence. Requiring a
            # name OR arithmetic across the board is what keeps a legitimate
            # final row — the biggest customer, the last month — in the data.
            return None

        return {
            "removed_rows": 1,
            "label": label,
            "summing_columns": matched,
            "all_numeric_columns_matched": all_numeric_match,
            "notice": (
                "The last row of this sheet is a TOTAL of the rows above it "
                f"({', '.join(matched)}"
                + (f", labelled {label!r}" if label else "")
                + "), not an observation. It was excluded so aggregates are not "
                "counted twice."
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("DEF-012: trailer detection skipped (%s)", exc)
        return None


def strip_total_row(df: Any) -> tuple[Any, Optional[Dict[str, Any]]]:
    """Return `(frame, disclosure)` with any trailing total row removed.

    The frame is returned UNCHANGED, and the disclosure is None, whenever the
    evidence falls short — which is the common case and must stay cheap.
    """
    found = detect_total_row(df)
    if not found:
        return df, None
    return df.iloc[:-1], found
