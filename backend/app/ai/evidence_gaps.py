"""What this turn could not reach, and what that obliges the answer to say.

★A run can lose evidence in three ways and still finish looking healthy: a
query hits the hard limit, the inspection budget runs out, or a named file
cannot be resolved. In every case the planner carried on and answered with what
it had. Nothing on the screen distinguished "here is the total" from "here is
the total of the four months that came back".

That is the failure that matters. A slow answer costs patience; a confident
wrong number costs a decision. "H1 completed calls: 7,412" over four of six
months is not a smaller answer — it is the wrong one, and it looks exactly like
the right one.

So a gap is recorded as a fact about the run, the planner is told it may not
present the result as complete, and the reader is told what was missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: A query was abandoned at the hard limit.
GAP_QUERY_TIMEOUT = "query_timeout"
#: Pre-answer inspection was cut short; the tool stopped being offered.
GAP_INSPECTION_BUDGET = "inspection_budget"
#: A file the model asked for could not be resolved.
GAP_FILE_UNRESOLVED = "file_unresolved"

#: Kinds that mean DATA is missing, as opposed to context being thinner than
#: it could have been. Only these force the "do not present as complete" rule —
#: a curtailed inspection may still have gathered everything that mattered.
DATA_GAPS = (GAP_QUERY_TIMEOUT, GAP_FILE_UNRESOLVED)


@dataclass
class EvidenceGap:
    """One thing this turn could not reach.

    ``subject`` is what the reader needs: the table, the file, the month. Not
    an id — "MM Conso Data Report (May'25).csv", not "d203". A gap the reader
    cannot map onto their own question is only slightly better than silence.
    """

    kind: str
    subject: str
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail}


def record_gap(runtime_ctx: Optional[dict], kind: str, subject: str, detail: str = "") -> None:
    """Note that something could not be reached. Never raises.

    The list is owned by the agent loop and handed to every tool through
    ``runtime_ctx``. A tool that cannot record a gap must still complete — but
    the gap going unrecorded is the whole bug, so this is deliberately the only
    failure mode left, not one wrapped in more `try`.
    """
    if not isinstance(runtime_ctx, dict):
        return
    gaps = runtime_ctx.get("evidence_gaps")
    if gaps is None:
        return
    subject = (subject or "").strip() or "an unnamed source"
    for existing in gaps:
        if existing.kind == kind and existing.subject == subject:
            return  # one gap per subject; a retried timeout is not two gaps
    gaps.append(EvidenceGap(kind=kind, subject=subject, detail=detail))


def gaps_from_query_timings(runtime_ctx: Optional[dict], timings: Optional[List[dict]]) -> None:
    """Record a gap for every query that hit the hard limit.

    The timing entry is the only place a timeout survives: the exception is
    caught and turned into an error message the planner may or may not act on.
    """
    for t in timings or []:
        if (t or {}).get("error_type") != "timeout":
            continue
        sql = (t.get("sql") or "").strip().replace("\n", " ")
        record_gap(
            runtime_ctx,
            GAP_QUERY_TIMEOUT,
            subject=(sql[:120] or "a query"),
            detail=f"exceeded the {t.get('timeout_seconds')}s limit",
        )


def has_data_gap(gaps: Optional[List[EvidenceGap]]) -> bool:
    return any(g.kind in DATA_GAPS for g in gaps or [])


def planner_notice(gaps: Optional[List[EvidenceGap]]) -> str:
    """What the planner is told before it writes the answer.

    Deliberately an instruction, not a description. The model's default is to
    answer with what it has — which is right — and to present that as the
    answer, which is not.
    """
    gaps = list(gaps or [])
    if not gaps:
        return ""
    lines = [
        "EVIDENCE GAPS — this turn could not reach everything it needed:",
    ]
    for g in gaps:
        lines.append(f"  - {g.subject}" + (f" ({g.detail})" if g.detail else ""))
    lines.append(
        "You MUST NOT present your result as complete. Say plainly, in the "
        "answer itself and not only in a footnote, which sources are missing "
        "and what that means for the numbers."
    )
    if has_data_gap(gaps):
        lines.append(
            "If the missing data is part of WHAT WAS ASKED FOR — a month of a "
            "range, one of several sources — do NOT total or average the rest "
            "as though it were the whole. Report what you have, labelled as "
            "the part it is, and name what is absent. A wrong total is worse "
            "than an incomplete one."
        )
    return "\n".join(lines)


def reader_notice(gaps: Optional[List[EvidenceGap]]) -> str:
    """The one line shown above the answer. Empty when nothing was missed."""
    gaps = list(gaps or [])
    if not gaps:
        return ""
    subjects = [g.subject for g in gaps]
    shown = ", ".join(subjects[:3])
    more = f" and {len(subjects) - 3} more" if len(subjects) > 3 else ""
    return f"Incomplete — could not reach: {shown}{more}."


def as_dicts(gaps: Optional[List[EvidenceGap]]) -> List[Dict[str, Any]]:
    return [g.as_dict() for g in gaps or []]
