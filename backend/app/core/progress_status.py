"""One vocabulary for "how is that long job going".

Four trackers grew up separately and each invented its own words for the same
seven states. Counting only the ones that reach a client:

    finished, successfully   ->  "completed" | "done" | "ok"
    finished, unsuccessfully ->  "failed"    | "error"
    in flight                ->  "running"   | "syncing" | "learning"
    nothing here             ->  "idle"      | "pending"

★It is worse than untidy. Two of them appear in the SAME payload: a per-user
sync reports ``status: "done"`` at the top while each workspace inside it
reports ``status: "ok"``. And a consumer cannot tell by looking which spelling
a given endpoint uses, so the natural thing — copy the check that worked on the
last screen — produces a comparison that is simply never true. A status test
that never fires does not look broken; it looks like the job never finishes.

So: **stored values are left exactly as they are** — no migration, and every
row written by every previous version keeps working — and every payload is
normalised on the way OUT. One word per state at the boundary, whatever the
writer happened to call it.

★``idle`` and ``pending`` are NOT synonyms and are both kept. "Nothing is
running and nothing is queued" and "queued, will start shortly" are different
answers to "should I keep polling", and collapsing them would lose the only
information the poller actually needs.

★``partial`` is a SUCCESS. Some units failed, the rest are usable, and the work
is over. Calling it ``failed`` would tell a member their agent is broken when it
is merely incomplete; calling it ``completed`` would claim coverage that is not
there.
"""
from __future__ import annotations

# The canonical vocabulary. Nothing outside this set may leave a serializer.
IDLE = "idle"
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"
CANCELLED = "cancelled"

CANONICAL = (IDLE, PENDING, RUNNING, COMPLETED, PARTIAL, FAILED, CANCELLED)

TERMINAL = (COMPLETED, PARTIAL, FAILED, CANCELLED)
SUCCESSFUL = (COMPLETED, PARTIAL)

# Every historical spelling, and what it meant. Anything already canonical maps
# to itself; this table only has to cover the legacy words.
_ALIASES = {
    # finished, successfully
    "done": COMPLETED,
    "ok": COMPLETED,
    "success": COMPLETED,
    "succeeded": COMPLETED,
    "complete": COMPLETED,
    # finished, unsuccessfully
    "error": FAILED,
    "errored": FAILED,
    "failure": FAILED,
    # in flight — the specific activity survives in `phase`, which is where a
    # UI should read it from. Status answers "is it running", not "doing what".
    "syncing": RUNNING,
    "learning": RUNNING,
    "indexing": RUNNING,
    "in_progress": RUNNING,
    "started": RUNNING,
    # stopped by a person
    "canceled": CANCELLED,
    "aborted": CANCELLED,
    # nothing here
    "none": IDLE,
    "": IDLE,
}


def normalize(value) -> str:
    """Map any status a tracker has ever written onto the canonical vocabulary.

    ★Unknown values fall through to ``running``, not ``idle`` or ``failed``.
    An unrecognised status came from somewhere, so something is happening; the
    two safe-looking alternatives are both actively misleading — ``idle`` tells
    a poller to stop watching a live job, and ``failed`` reports a failure that
    may not have happened.
    """
    if value is None:
        return IDLE
    text = str(value).strip().lower()
    if text in CANONICAL:
        return text
    if text in _ALIASES:
        return _ALIASES[text]
    return RUNNING


def is_terminal(value) -> bool:
    """The job is over, however it went. Stop polling."""
    return normalize(value) in TERMINAL


def is_running(value) -> bool:
    return normalize(value) == RUNNING


def is_successful(value) -> bool:
    """Finished AND usable. ``partial`` counts — see the module docstring."""
    return normalize(value) in SUCCESSFUL
