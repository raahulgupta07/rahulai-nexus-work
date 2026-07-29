"""DEF-010 — ONE accessor for the data an artifact is built on and rendered from.

Why this module exists
----------------------
A step persists its result under `rows`, capped by the org's `limit_row_count`
(default 1000) because that copy also feeds a table preview and the model's
context. A dashboard has different needs, so a second, wider copy is stored
under `rows_artifact` (cap `artifact_row_limit`, default 10000).

Two copies is fine. Two READERS is not: `create_artifact` preferred the wide
copy, the dashboard PDF export preferred the wide copy, and the browser read
`rows` and nothing else. One artifact, three code paths, and the totals a user
saw on screen were the sum of a 1,000-row prefix while the same artifact
exported to PDF summed the whole 1,200 rows — a ~16% gap with nothing on either
face saying so.

Worse, `create_artifact`'s recovery pass (DEF-009) re-reads a truncated result
and aggregates it, then builds the dashboard on that aggregate — which lived
only in the tool's memory. Every render path afterwards read the untouched
1,000-row prefix, which does not even have the same COLUMNS as the aggregate the
code was written against.

So: every consumer resolves rows through `resolve_artifact_rows`, every producer
of a step's data attaches the wide copy through `attach_artifact_rows`, and
anything an artifact is genuinely built on is written back to the step with
`store_artifact_dataset` so that the thing rendered is the thing that was built.
Nothing here is specific to any connector, table, column or dataset.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keys on `step.data`. `rows`/`columns` are the display copy and are NEVER
# rewritten by this module — every pre-existing consumer of them keeps its
# behaviour. The artifact copy lives beside them under its own names.
ROWS_KEY = "rows"
COLUMNS_KEY = "columns"
ARTIFACT_ROWS_KEY = "rows_artifact"
ARTIFACT_ROWS_TOTAL_KEY = "rows_artifact_total"
ARTIFACT_COLUMNS_KEY = "columns_artifact"
ARTIFACT_REDUCTION_KEY = "rows_artifact_reduction"


@dataclass
class ArtifactRows:
    """The one dataset an artifact is built on and rendered from."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[Any] = field(default_factory=list)
    #: True row count of the underlying result, as far as it is known.
    rows_total: int = 0
    #: How many of them are actually in hand.
    rows_used: int = 0
    #: Which key `rows` came from — "rows" or "rows_artifact".
    source: str = ROWS_KEY
    #: Disclosure written by whoever produced a reduced dataset, if any.
    reduction: Optional[Dict[str, Any]] = None

    @property
    def truncated(self) -> bool:
        """Do the rows in hand fall short of the whole result?

        Deliberately computed, never read from a stored flag: `rows_truncated`
        on a step describes the DISPLAY copy, so it stays True even when the
        artifact copy holds every row. Trusting it made the completeness gate
        refuse data that was complete.
        """
        return self.rows_total > self.rows_used

    def notice(self) -> Optional[str]:
        """One sentence stating any cap or reduction applied to these rows."""
        if self.reduction and self.reduction.get("notice"):
            return str(self.reduction["notice"])
        if self.truncated:
            return (
                f"Only {self.rows_used:,} of {self.rows_total:,} rows are available "
                f"here, taken in the query's own sort order, so totals over them are "
                f"PARTIAL."
            )
        return None


def _as_list(value: Any) -> Optional[List[Any]]:
    return value if isinstance(value, list) else None


def resolve_artifact_rows(step_data: Any) -> ArtifactRows:
    """Resolve the dataset for an artifact from a step's stored `data` blob.

    The ONLY sanctioned way to read rows for anything artifact-shaped: the
    dashboard the browser renders, the same dashboard printed to PDF, and the
    build that produced it. They agree because they all come through here.
    """
    if not isinstance(step_data, dict):
        return ArtifactRows()

    display_rows = _as_list(step_data.get(ROWS_KEY)) or []
    display_columns = _as_list(step_data.get(COLUMNS_KEY)) or []

    rows = display_rows
    columns = display_columns
    source = ROWS_KEY
    reduction = None

    wide_rows = _as_list(step_data.get(ARTIFACT_ROWS_KEY))
    if wide_rows is not None and len(wide_rows) >= len(display_rows):
        # ">=" and not ">": a reduced dataset (DEF-009) can legitimately have
        # FEWER rows than the display prefix — it is an aggregate — and it is
        # still the dataset the artifact was built on. A wide copy that is
        # merely shorter with no reduction recorded is a stale leftover and is
        # ignored below.
        rows = wide_rows
        columns = _as_list(step_data.get(ARTIFACT_COLUMNS_KEY)) or display_columns
        source = ARTIFACT_ROWS_KEY
        reduction = step_data.get(ARTIFACT_REDUCTION_KEY)
    elif wide_rows is not None:
        stored_reduction = step_data.get(ARTIFACT_REDUCTION_KEY)
        if isinstance(stored_reduction, dict):
            rows = wide_rows
            columns = _as_list(step_data.get(ARTIFACT_COLUMNS_KEY)) or display_columns
            source = ARTIFACT_ROWS_KEY
            reduction = stored_reduction

    info = step_data.get("info")
    declared_total = None
    if isinstance(info, dict):
        raw_total = info.get("total_rows")
        if isinstance(raw_total, (int, float)) and not isinstance(raw_total, bool):
            declared_total = int(raw_total)

    rows_used = len(rows)
    if reduction is not None:
        # An aggregate covers every source row by construction, so it is
        # complete no matter how the source compares to it.
        rows_total = rows_used
    elif declared_total is not None and declared_total >= rows_used:
        rows_total = declared_total
    else:
        rows_total = rows_used

    return ArtifactRows(
        rows=rows,
        columns=columns,
        rows_total=rows_total,
        rows_used=rows_used,
        source=source,
        reduction=reduction if isinstance(reduction, dict) else None,
    )


def attach_artifact_rows(executor: Any, df: Any, formatted: Dict[str, Any]) -> Dict[str, Any]:
    """Add the artifact-width copy to a freshly formatted step payload.

    Called by EVERY writer of a step's data — the agent's data tool and every
    re-run path — because a re-run that formats only the display copy silently
    strips the wide one, and the dashboard built on it quietly loses rows the
    next time anybody presses refresh.

    Mutates and returns `formatted`. Never raises: a step that cannot carry the
    wide copy simply behaves as it did before this existed, and the completeness
    gate is what stops an artifact being built on the remainder.
    """
    if not isinstance(formatted, dict):
        return formatted
    try:
        if not formatted.get("rows_truncated"):
            # The display copy already holds everything; a second copy of the
            # same rows would be pure weight.
            return formatted
        wide = executor.format_df_for_widget(df, for_artifact=True)
        if wide.get("rows_truncated"):
            # Past the artifact cap too. Storing a bigger prefix would only make
            # a wrong total harder to spot; this result needs aggregating, which
            # is what the completeness gate forces.
            return formatted
        wide_rows = _as_list(wide.get(ROWS_KEY)) or []
        if len(wide_rows) <= len(_as_list(formatted.get(ROWS_KEY)) or []):
            return formatted
        formatted[ARTIFACT_ROWS_KEY] = wide_rows
        formatted[ARTIFACT_ROWS_TOTAL_KEY] = len(wide_rows)
        wide_columns = _as_list(wide.get(COLUMNS_KEY))
        if wide_columns:
            formatted[ARTIFACT_COLUMNS_KEY] = wide_columns
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("DEF-010: could not build artifact-width rows: %s", exc)
    return formatted


def store_artifact_dataset(
    step_data: Any,
    rows: List[Dict[str, Any]],
    columns: Optional[List[Any]] = None,
    reduction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write back the dataset an artifact was actually built on.

    Returns a NEW dict (SQLAlchemy only notices a JSON column changed when the
    attribute is reassigned). The display copy is left exactly as it was; what
    changes is that every render path can now see what the build saw.
    """
    data = dict(step_data) if isinstance(step_data, dict) else {}
    data[ARTIFACT_ROWS_KEY] = list(rows or [])
    data[ARTIFACT_ROWS_TOTAL_KEY] = len(rows or [])
    if columns:
        data[ARTIFACT_COLUMNS_KEY] = list(columns)
    if reduction:
        data[ARTIFACT_REDUCTION_KEY] = dict(reduction)
    else:
        data.pop(ARTIFACT_REDUCTION_KEY, None)
    return data


def apply_to_step_payload(step_data: Any) -> Any:
    """Serve ONE dataset to whoever reads a step over the API.

    The browser reads a step's `rows` and has no notion of a second key, so the
    artifact copy is surfaced AS `rows` (with its own columns) and the cap is
    declared alongside. Without this the page renders a prefix while the export
    of the same artifact renders the whole thing.
    """
    if not isinstance(step_data, dict):
        return step_data
    resolved = resolve_artifact_rows(step_data)
    if resolved.source != ARTIFACT_ROWS_KEY:
        return step_data
    payload = dict(step_data)
    payload[ROWS_KEY] = resolved.rows
    if resolved.columns:
        payload[COLUMNS_KEY] = resolved.columns
    payload["rows_source"] = ARTIFACT_ROWS_KEY
    payload["rows_total"] = resolved.rows_total
    payload["rows_truncated"] = resolved.truncated
    notice = resolved.notice()
    if notice:
        payload["rows_notice"] = notice
    return payload
