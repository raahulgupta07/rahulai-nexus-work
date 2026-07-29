"""One definition of how a number is written, shared by every render path.

Two product properties live here, and nothing in this module knows anything
about any particular dataset, connector, column or currency:

1. **Abbreviation is the same everywhere.** A value axis reads ``4.3B`` in the
   browser, in a Word export and in a PowerPoint export, because all three take
   their rule from this file. Before this existed each path re-invented it and
   the exports simply printed ``70000000000``.

2. **A unit is only printed when the data supplies it.** ``currency_symbol_for``
   returns a symbol *only* for an explicit ISO-4217 code that came from the
   data / connector metadata / the view config. There is no default and no
   fallback code: an unknown unit prints as a bare number, because a wrong unit
   is worse than none.

The JS emitters return *self-contained* source. They deliberately do not call
``fmt()`` or any other sandbox global: the headless chart renderer used by the
Word export loads ECharts and nothing else, so a formatter that depended on a
global would silently fall back to raw digits in exactly the path that was
broken.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

# Magnitude → suffix. Ordered largest first; the first threshold a value clears
# is the one it is written in. Mirrored byte-for-byte by the JS emitted below
# and by `pptx_axis_number_format`, so the three render paths cannot drift.
ABBREVIATION_STEPS: Sequence[tuple] = (
    (1e12, "T"),
    (1e9, "B"),
    (1e6, "M"),
    (1e3, "K"),
)

# Excel/OOXML number-format codes. Each trailing comma inside a format code
# scales the displayed value down by 1000, so three commas render billions.
_PPTX_SCALE_CODES = {
    "T": '#,##0.0,,,,"T"',
    "B": '#,##0.0,,,"B"',
    "M": '#,##0.0,,"M"',
    "K": '#,##0.0,"K"',
}
PPTX_PLAIN_NUMBER_FORMAT = "#,##0.##"


def abbreviate_number(value: Any, decimals: int = 1) -> str:
    """Write a number the way every axis in the product writes it.

    Returns a bare number — never a currency symbol, never a unit. Callers that
    know the unit prepend it themselves.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if n != n or n in (float("inf"), float("-inf")):  # NaN / inf
        return str(value)

    magnitude = abs(n)
    for threshold, suffix in ABBREVIATION_STEPS:
        if magnitude >= threshold:
            return f"{n / threshold:.{decimals}f}{suffix}"
    # Below 1000 the exact number is short enough to print in full.
    text = f"{n:,.2f}".rstrip("0").rstrip(".")
    return text or "0"


def axis_label_formatter_js(decimals: int = 1) -> str:
    """Source of a self-contained JS arrow function for `axisLabel.formatter`.

    Same rule as `abbreviate_number`. No dependency on any global.
    """
    steps = ", ".join(f"[{t:.0f}, '{s}']" for t, s in ABBREVIATION_STEPS)
    return (
        "(function (v) {"
        " var n = Number(v);"
        " if (v == null || isNaN(n)) return String(v == null ? '' : v);"
        f" var steps = [{steps}];"
        " var a = Math.abs(n);"
        " for (var i = 0; i < steps.length; i++) {"
        f" if (a >= steps[i][0]) return (n / steps[i][0]).toFixed({decimals}) + steps[i][1];"
        " }"
        " return n.toLocaleString(undefined, { maximumFractionDigits: 2 });"
        " })"
    )


def pptx_axis_number_format(max_abs: Optional[float]) -> str:
    """Number-format code for a PowerPoint value axis holding `max_abs`.

    Picks the same magnitude bucket the browser axis would pick for the largest
    plotted value, so a chart reads `4.3B` in the deck and `4.3B` on screen.
    """
    try:
        magnitude = abs(float(max_abs))
    except (TypeError, ValueError):
        return PPTX_PLAIN_NUMBER_FORMAT
    if magnitude != magnitude:  # NaN
        return PPTX_PLAIN_NUMBER_FORMAT
    for threshold, suffix in ABBREVIATION_STEPS:
        if magnitude >= threshold:
            return _PPTX_SCALE_CODES[suffix]
    return PPTX_PLAIN_NUMBER_FORMAT


# ---------------------------------------------------------------------------
# Unit honesty
# ---------------------------------------------------------------------------

def normalize_currency_code(code: Any) -> Optional[str]:
    """Return an ISO-4217-shaped code, or None.

    None is the answer for `True`, `1`, `"yes"`, `""` and anything else that is
    not an actual three-letter code: those carry no information about which
    currency the data is in, and inventing one is the defect this guards.
    """
    if not isinstance(code, str):
        return None
    trimmed = code.strip()
    if len(trimmed) != 3 or not trimmed.isalpha():
        return None
    return trimmed.upper()


# ---------------------------------------------------------------------------
# Category-axis label collisions
# ---------------------------------------------------------------------------

def qualify_duplicate_labels(
    labels: Iterable[Any],
    qualifiers: Optional[Iterable[Any]] = None,
) -> List[str]:
    """Make every category label on an axis identify exactly one thing.

    Two rows can share a label while meaning different things — the same leaf
    name under two parents of a hierarchy, for instance. Rendering both as one
    string presents two different things as one. Where a qualifier (any other
    column whose values differ) is available the label becomes
    ``"Common (ParentA)"``; where it is not, the duplicates are numbered
    ``"Common (1 of 2)"`` so the ambiguity is stated rather than hidden.

    Labels that are already unique are returned untouched.
    """
    raw = ["" if l is None else str(l) for l in labels]
    quals = None
    if qualifiers is not None:
        quals = ["" if q is None else str(q) for q in qualifiers]
        if len(quals) != len(raw):
            quals = None

    counts: dict = {}
    for label in raw:
        counts[label] = counts.get(label, 0) + 1

    seen: dict = {}
    used = set()
    out: List[str] = []
    for i, label in enumerate(raw):
        if counts[label] < 2:
            out.append(label)
            used.add(label)
            continue
        seen[label] = seen.get(label, 0) + 1
        qualifier = quals[i].strip() if quals else ""
        if qualifier and qualifier != label:
            candidate = f"{label} ({qualifier})"
        else:
            candidate = f"{label} ({seen[label]} of {counts[label]})"
        # A qualifier can itself repeat; never hand back a label twice.
        if candidate in used:
            candidate = f"{candidate} [{seen[label]}]"
        used.add(candidate)
        out.append(candidate)
    return out


def pick_qualifier_column_js() -> str:
    """Source of a JS function that finds a column able to disambiguate an axis.

    ``pick(rows, catKey, excluded)`` returns the name of the first column that
    gives two different values to rows sharing one category label — i.e. the
    column that proves the label means two different things — or null when the
    label is unambiguous. Measure columns are skipped: a number is a value, not
    an identity.
    """
    return (
        "(function (rows, catKey, excluded) {"
        " if (!rows || !rows.length) return null;"
        " var ex = {}; (excluded || []).forEach(function (k) { if (k) ex[k] = true; });"
        " var keys = Object.keys(rows[0] || {});"
        " for (var i = 0; i < keys.length; i++) {"
        "   var k = keys[i];"
        "   if (ex[k] || k === catKey) continue;"
        "   if (rows.every(function (r) { return typeof r[k] === 'number'; })) continue;"
        "   var seen = {}, splits = false;"
        "   for (var j = 0; j < rows.length; j++) {"
        "     var c = String(rows[j][catKey] == null ? '' : rows[j][catKey]);"
        "     var v = String(rows[j][k] == null ? '' : rows[j][k]);"
        "     if (Object.prototype.hasOwnProperty.call(seen, c) && seen[c] !== v) { splits = true; break; }"
        "     seen[c] = v;"
        "   }"
        "   if (splits) return k;"
        " }"
        " return null;"
        " })"
    )


def qualify_duplicate_labels_js() -> str:
    """Source of a self-contained JS function with `qualify_duplicate_labels`'
    behaviour, for chart option code that runs in the browser."""
    return (
        "(function (labels, quals) {"
        " var raw = labels.map(function (l) { return l == null ? '' : String(l); });"
        " var q = (quals && quals.length === raw.length)"
        "   ? quals.map(function (x) { return x == null ? '' : String(x); }) : null;"
        " var counts = {};"
        " raw.forEach(function (l) { counts[l] = (counts[l] || 0) + 1; });"
        " var seen = {}, used = {}, out = [];"
        " for (var i = 0; i < raw.length; i++) {"
        "   var l = raw[i];"
        "   if (counts[l] < 2) { out.push(l); used[l] = true; continue; }"
        "   seen[l] = (seen[l] || 0) + 1;"
        "   var qual = q ? q[i].trim() : '';"
        "   var c = (qual && qual !== l) ? (l + ' (' + qual + ')')"
        "     : (l + ' (' + seen[l] + ' of ' + counts[l] + ')');"
        "   if (used[c]) c = c + ' [' + seen[l] + ']';"
        "   used[c] = true; out.push(c);"
        " }"
        " return out;"
        " })"
    )
