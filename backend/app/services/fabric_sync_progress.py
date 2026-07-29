"""SUPERSEDED — use ``app.services.connection_sync_progress`` instead.

This module used to hold Fabric sync progress in a module-level dict. Its own
docstring justified that with "single-process, single event-loop → a plain dict
is safe". That was true when it was written and stopped being true when the app
started running uvicorn with up to 4 workers (see ``start.sh``): the sync runs
in whichever worker served the sign-in, while the browser's ``/sync-status``
poll round-robins across all of them, so most polls hit a worker whose dict is
empty and get back ``idle``. The UI reads ``idle`` as "nothing is running" and
stops — a large part of why a sync appeared to vanish mid-flight.

The replacement is DB-backed, so every worker sees the same state, and it covers
``powerbi_user`` as well as ``fabric_user``.

★The functions below raise instead of returning something harmless. A silent
no-op is precisely the failure this module already caused once: progress that
looked fine and reported nothing. If anything imports it again, it should break
loudly at the call site — in a test, not in front of a member.
"""
from __future__ import annotations

_MESSAGE = (
    "fabric_sync_progress is superseded by app.services.connection_sync_progress "
    "(DB-backed, visible across uvicorn workers, shared with powerbi_user). Its "
    "async API is: start / update / set_endpoints / endpoint_done / finish / "
    "fail / get."
)


def _superseded(*_args, **_kwargs):
    raise RuntimeError(_MESSAGE)


start = update = finish = fail = get = _superseded
