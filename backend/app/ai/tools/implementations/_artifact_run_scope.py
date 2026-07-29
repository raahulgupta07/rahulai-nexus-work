"""One deliverable per run, per mode.

A single agent run may call an artifact-producing tool more than once — it
builds a dashboard, then produces more data, then builds the dashboard again
with the fuller picture. Every one of those calls used to insert a fresh
`Artifact` row at version 1, so the run ended with two independent artifacts
of the same mode, both "current", and the user could open the abandoned one.

The rule here is deliberately not about any particular data, connector or
tool: *within one run, one mode yields one artifact*. A second build of the
same mode supersedes the first in place — same artifact id, next version — so
"which one is current" has exactly one answer and no orphan is left behind.

Editing across runs is untouched: `edit_artifact` / `edit_doc` still append a
new row so history is preserved. That is a different run and a deliberate act.

★ That last sentence used to be the whole policy, and it was a claim nothing
enforced. An agent edits its OWN deliverable inside a single turn — build the
document, read it back, revise it — and the edit path appended regardless, so
one question ended with two live documents and the user could open the
abandoned one. Observed live: `create_doc` then `edit_doc`, one completion id,
two rows 90 seconds apart. `supersedes_in_place` below makes the sentence true
by testing it instead of assuming it: an edit of THIS run's own artifact
overwrites, an edit of anything else still appends.
"""

from typing import Any, List, Optional

from sqlalchemy import select

from app.models.artifact import Artifact


def pick_run_artifact(
    artifacts: List[Any],
    *,
    completion_id: Optional[str],
    mode: Optional[str],
) -> Optional[Any]:
    """Pick the artifact this run already produced for ``mode``, if any.

    Pure so the selection rule can be tested without a database. Returns the
    newest matching artifact (highest version, then most recently created) so
    a run that has already versioned its deliverable keeps building on the
    head of that chain rather than resurrecting an earlier version.

    Returns None when the run is unidentified (``completion_id`` empty) — an
    unattributable build must never adopt somebody else's artifact.
    """
    if not completion_id or not mode:
        return None

    candidates = [
        a
        for a in (artifacts or [])
        if str(getattr(a, "completion_id", "") or "") == str(completion_id)
        and getattr(a, "mode", None) == mode
        and getattr(a, "deleted_at", None) is None
    ]
    if not candidates:
        return None

    def _key(a: Any):
        return (
            int(getattr(a, "version", 1) or 1),
            getattr(a, "created_at", None) or 0,
        )

    return max(candidates, key=_key)


def next_run_version(artifact: Optional[Any]) -> int:
    """Version to store for the next build of this run's deliverable."""
    if artifact is None:
        return 1
    return int(getattr(artifact, "version", 1) or 1) + 1


def supersedes_in_place(artifact: Optional[Any], *, completion_id: Optional[str]) -> bool:
    """True when an edit should OVERWRITE ``artifact`` instead of appending.

    The one question this answers: did this run produce the thing it is now
    editing? If so the edit is the run still working on its own deliverable and
    must not leave the earlier draft behind as a second live document. If not,
    the artifact predates this turn — the user is revising something they
    already have — and a new row is exactly right, because that is what version
    history is for.

    Fails to False, which is today's append behaviour: an unattributable edit
    must never overwrite a row it cannot prove it owns. So an unidentified run
    (no ``completion_id``) or an artifact with no run stamp both append.
    """
    if artifact is None or not completion_id:
        return False
    own = getattr(artifact, "completion_id", None)
    if not own:
        return False
    return str(own) == str(completion_id)


async def find_run_artifact(
    db: Any,
    *,
    report_id: Optional[str],
    completion_id: Optional[str],
    mode: Optional[str],
) -> Optional[Artifact]:
    """Load the artifact this run already produced for ``mode``, or None.

    Thin database wrapper around :func:`pick_run_artifact`. Never raises: a
    lookup failure must degrade to "no previous artifact" (i.e. today's
    behaviour of inserting a new row), never to a failed build.
    """
    if not report_id or not completion_id or not mode:
        return None
    try:
        res = await db.execute(
            select(Artifact).where(
                Artifact.report_id == str(report_id),
                Artifact.completion_id == str(completion_id),
                Artifact.mode == mode,
                Artifact.deleted_at.is_(None),
            )
        )
        rows = list(res.scalars().all())
    except Exception:
        return None
    return pick_run_artifact(rows, completion_id=completion_id, mode=mode)
