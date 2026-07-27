"""In-memory progress registry for the federated Fabric sync (Phase 4).

The federated `fabric_user` sync takes ~20-30s (discover every endpoint, then
pull each one's schema). Blocking the sign-in request for that long gives the
member no feedback. Phase 4 runs the sync as a background task and exposes its
live progress here so the UI can poll a status endpoint and render a bar.

Single-process, single event-loop → a plain dict is safe (no lock needed; all
reads/writes happen on the asyncio loop). State is intentionally ephemeral: on a
container restart an in-flight sync's progress is lost, which is fine — the last
completed overlay is already persisted in the DB, and the user can re-sync.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

# key = f"{data_source_id}:{user_id}"  →  progress dict
_PROGRESS: Dict[str, dict] = {}

# Terminal states are kept this long so a slow poll still sees the result.
_TTL_SECONDS = 600


def _key(data_source_id: str, user_id: str) -> str:
    return f"{data_source_id}:{user_id}"


def _now() -> float:
    return time.time()


def start(data_source_id: str, user_id: str) -> None:
    """Mark a sync as started (phase=discovering, counters zeroed)."""
    _PROGRESS[_key(data_source_id, user_id)] = {
        "status": "syncing",       # syncing | done | error
        "phase": "discovering",    # discovering | ingesting | done | error
        "endpoints_total": 0,
        "endpoints_done": 0,
        "tables": 0,
        "error": None,
        "started_at": _now(),
        "updated_at": _now(),
    }


def update(data_source_id: str, user_id: str, **fields) -> None:
    """Patch fields on an in-flight sync (no-op if none started)."""
    p = _PROGRESS.get(_key(data_source_id, user_id))
    if p is None:
        return
    p.update(fields)
    p["updated_at"] = _now()


def finish(data_source_id: str, user_id: str, tables: int) -> None:
    update(data_source_id, user_id, status="done", phase="done", tables=tables)


def fail(data_source_id: str, user_id: str, error: str) -> None:
    update(data_source_id, user_id, status="error", phase="error", error=str(error)[:300])


def get(data_source_id: str, user_id: str) -> Optional[dict]:
    """Return the progress dict, or None if nothing is tracked. Expires terminal
    states after the TTL so stale rows don't leak."""
    k = _key(data_source_id, user_id)
    p = _PROGRESS.get(k)
    if p is None:
        return None
    if p.get("status") in ("done", "error") and (_now() - p.get("updated_at", 0)) > _TTL_SECONDS:
        _PROGRESS.pop(k, None)
        return None
    return dict(p)
