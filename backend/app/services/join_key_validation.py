"""Check the join keys an LLM-written overview asserts, before they are stored.

Why this exists
---------------
The generated ``MICROSOFT_FABRIC_OVERVIEW`` on this deployment stated:

    ARTICLE_CODE/StockCode/item_id/Product_Code ↔ ProductCode/ProductId

Measured intersection of the two columns: **zero**. They are different code
spaces (13-digit vs 17-digit), and the tables sit in different Fabric
workspaces, so no SQL join between them is even possible. That claim then rode
into every single prompt as an always-loaded instruction.

Microsoft's own guidance says the same thing about trusting declared keys:
"since primary key and unique key constraints aren't enforced, columns with
these constraints aren't necessarily good candidates for JOINs"
(learn.microsoft.com/en-us/fabric/data-warehouse/guidelines-warehouse-performance).

Design
------
Conservative on purpose — a wrong *correction* is worse than an unchecked
claim, so a claim is only ever annotated when the evidence is unambiguous:

- Both sides must resolve to exactly ONE ``table.column`` in the known schema.
  An ambiguous name (three tables have a ``ProductCode``) is left alone.
- Only a measured **zero** overlap annotates. Any error, timeout, or empty
  sample leaves the claim untouched.
- Claims are annotated, never deleted. Silently dropping a line the model wrote
  would hide the disagreement; the point is to tell the agent what was checked.
- Sampling is bounded (``TOP``) so this cannot become a slow scan on a fact
  table.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# `A ↔ B`, `A <-> B`, `A <=> B`. Captures the two operands only.
#
# A side is one or more `/`-separated tokens, where a token is either a
# bracketed identifier (`[Account ID]`, which legitimately contains spaces) or a
# bare dotted name (`DL_POC.dbo.T.Col`). Spaces are NOT part of a bare token: an
# earlier version allowed them and the right-hand side ran greedily past the end
# of the sentence, swallowing the next claim.
_TOKEN = r"(?:\[[^\]\n]{1,60}\]|[A-Za-z_][\w.]{0,60})"
_SIDE = rf"{_TOKEN}(?:\s*/\s*{_TOKEN})*"
_CLAIM_RE = re.compile(
    rf"(?P<left>{_SIDE})\s*(?:↔|<->|<=>)\s*(?P<right>{_SIDE})"
)
# One side may list alternatives: `ARTICLE_CODE/StockCode/item_id`.
_ALT_SPLIT = re.compile(r"\s*/\s*")

# Bounded so this can never turn into a full scan of a fact table. A side is
# "complete" when it returns fewer rows than the cap — i.e. we saw every
# distinct value, not a slice of them.
SAMPLE_ROWS = 20000

# Both sides fully enumerated and nothing matched: this is proof.
NOT_JOINABLE_NOTE = (
    " [verified against the data: {left} and {right} share no values — do not join on these]"
)
# At least one side was truncated at the cap, so zero matches is strong evidence
# but not proof. Say which it is rather than overclaiming.
LIKELY_NOT_JOINABLE_NOTE = (
    " [checked against the data: 0 matches between {n_left} sampled {left} values and "
    "{n_right} sampled {right} values — verify before joining on these]"
)


def _clean(token: str) -> str:
    return token.strip().strip(".,;").replace("[", "").replace("]", "").strip()


def extract_join_claims(text: str) -> List[Tuple[str, str, str]]:
    """Return ``(matched_text, left, right)`` for every `A ↔ B` claim in `text`."""
    out: List[Tuple[str, str, str]] = []
    for m in _CLAIM_RE.finditer(text or ""):
        left, right = _clean(m.group("left")), _clean(m.group("right"))
        if left and right:
            out.append((m.group(0), left, right))
    return out


# A bare column name legitimately appears in several tables (a dimension and
# the facts referencing it). Requiring a unique match skipped the real Fabric
# claim outright: ARTICLE_CODE was unique, but ProductCode lived in 2 tables and
# ProductId in 5. Instead every plausible reading is tested, and the claim is
# only contradicted when ALL of them come back empty.
MAX_CANDIDATES = 3


def resolve_columns(
    name: str, columns_by_table: Dict[str, Sequence[str]]
) -> List[Tuple[str, str]]:
    """All ``(table, column)`` a name could refer to, capped at MAX_CANDIDATES.

    Returns [] for an unknown name, or for one so ambiguous that checking every
    reading would be a fishing expedition.
    """
    name = _clean(name)
    if not name:
        return []

    # Qualified: Schema.Table.Column / Lakehouse.dbo.Table.Column
    if "." in name:
        parts = name.split(".")
        col = parts[-1]
        tbl_hint = ".".join(parts[:-1]).lower()
        hits = [
            (t, c)
            for t, cols in columns_by_table.items()
            if t.lower().endswith(tbl_hint) or tbl_hint.endswith(t.lower())
            for c in cols
            if c.lower() == col.lower()
        ]
    else:
        hits = [
            (t, c)
            for t, cols in columns_by_table.items()
            for c in cols
            if c.lower() == name.lower()
        ]
    return sorted(hits) if 0 < len(hits) <= MAX_CANDIDATES else []


def resolve_column(
    name: str, columns_by_table: Dict[str, Sequence[str]]
) -> Optional[Tuple[str, str]]:
    """Back-compat single-result resolution: only an unambiguous name."""
    hits = resolve_columns(name, columns_by_table)
    return hits[0] if len(hits) == 1 else None


def _distinct_sql(table: str, column: str) -> str:
    """Sample distinct values of one column, as text, trimmed."""
    return (
        f"SELECT DISTINCT TOP {SAMPLE_ROWS} "
        f"LTRIM(RTRIM(CAST([{column}] AS varchar(200)))) AS k "
        f"FROM {table} WHERE [{column}] IS NOT NULL"
    )


def _sample_values(run_query, table: str, column: str) -> Optional[Tuple[set, bool]]:
    """``(values, complete)`` for `table.column`, or None if it can't be read.

    `complete` is True when the result came back under the cap, meaning every
    distinct value was seen rather than an arbitrary slice.
    """
    df = run_query(_distinct_sql(table, column))
    if df is None or getattr(df, "empty", True):
        return None
    raw = df.iloc[:, 0].tolist()
    vals = {str(v) for v in raw if v is not None and str(v) != ""}
    if not vals:
        return None
    return vals, len(raw) < SAMPLE_ROWS


def validate_join_claims(
    text: str,
    columns_by_table: Dict[str, Sequence[str]],
    run_query,
    max_checks: int = 8,
) -> Tuple[str, List[Dict]]:
    """Annotate join claims in `text` that provably have no overlapping values.

    `run_query` takes a SQL string and returns something indexable like a
    DataFrame; anything it raises is treated as "unknown", never as "no
    overlap".

    Returns ``(text, findings)``. `text` is returned unchanged when nothing was
    disproved.
    """
    findings: List[Dict] = []
    if not text or not columns_by_table:
        return text, findings

    cache: Dict[Tuple[str, str], Optional[Tuple[set, bool]]] = {}

    def sample(res: Tuple[str, str]):
        # Two separate sampled reads, NOT one JOIN query. The claims most worth
        # checking are exactly the ones spanning two lakehouses, and a
        # cross-workspace join is refused before it reaches the database — a
        # JOIN-based probe would fail safe on precisely those cases.
        if res not in cache:
            cache[res] = _sample_values(run_query, *res)
        return cache[res]

    checked = 0
    for matched, left_raw, right_raw in extract_join_claims(text):
        if checked >= max_checks:
            break

        left_names = [n.strip() for n in _ALT_SPLIT.split(left_raw) if n.strip()]
        right_names = [n.strip() for n in _ALT_SPLIT.split(right_raw) if n.strip()]

        pairs: List[Tuple[str, str, Tuple[str, str], Tuple[str, str]]] = []
        for l in left_names:
            for lres in resolve_columns(l, columns_by_table):
                for r in right_names:
                    for rres in resolve_columns(r, columns_by_table):
                        if lres != rres:
                            pairs.append((l, r, lres, rres))
        if not pairs:
            continue
        checked += 1

        # The claim survives if ANY reading of it has overlapping values.
        best_overlap = None
        all_complete = True
        n_left = n_right = 0
        example: Optional[Tuple[str, str]] = None
        readings = 0

        for l_name, r_name, l_res, r_res in pairs:
            try:
                left, right = sample(l_res), sample(r_res)
            except Exception as e:  # noqa: BLE001
                logger.info("join check skipped for %s <-> %s: %s", l_res, r_res, e)
                all_complete = False
                continue
            if left is None or right is None:
                all_complete = False
                continue

            readings += 1
            lv, lc = left
            rv, rc = right
            ov = len(lv & rv)
            all_complete = all_complete and lc and rc
            if best_overlap is None or ov > best_overlap:
                best_overlap, n_left, n_right = ov, len(lv), len(rv)
                example = (l_name, r_name)
            findings.append({
                "left": f"{l_res[0]}.{l_res[1]}", "right": f"{r_res[0]}.{r_res[1]}",
                "overlap": ov, "complete": bool(lc and rc),
                "n_left": len(lv), "n_right": len(rv), "claim": matched,
            })

        if not readings or best_overlap is None or best_overlap > 0 or example is None:
            continue

        l_name, r_name = example
        note = (
            NOT_JOINABLE_NOTE.format(left=l_name, right=r_name)
            if all_complete else
            LIKELY_NOT_JOINABLE_NOTE.format(
                left=l_name, right=r_name, n_left=n_left, n_right=n_right,
            )
        )
        # Annotate in place, and never twice for a repeated claim.
        if note not in text:
            text = text.replace(matched, matched + note, 1)
        logger.warning(
            "overview join claim has zero overlap in all %d readings: %s <-> %s (complete=%s)",
            readings, l_name, r_name, all_complete,
        )

    return text, findings
