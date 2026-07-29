"""Shared progress registry for the "Learn agent" run (LLM overview
regeneration via ``data_source_service.llm_sync(force_llm=True)``).

The Learn agent reflects the data source's tables, analyzes the schema, asks the
LLM to write an overview, then grounds @mentions and publishes an instruction.
That takes many seconds and the bare UI shows only a spinner. This module tracks
the live stage so a status endpoint can poll it and the UI can render the step.

Backed by the ``learn_progress`` DB table — NOT an in-memory dict — because the
app runs multiple uvicorn workers: the relearn runs in one worker while the
/learn-status poll round-robins to others, so only a shared store (the DB) is
visible to both. Connector-agnostic: keyed on ``(data_source_id, user_id)`` and
never inspects connector type. Per-user because the per-user-token connectors
(fabric_user / powerbi_user) run a separate Learn per signed-in member.

Every function is async, takes the caller's ``db`` session, commits its own tiny
write, and is called best-effort (the caller swallows exceptions) so a tracker
failure can NEVER affect the actual learn.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.core.progress_status import normalize as normalize_status
from app.models.learn_progress import LearnProgress

_TOTAL = 4


def _idle() -> dict:
    return {
        "status": "idle", "stage": None, "step": 0, "total": _TOTAL,
        "tables": 0, "columns": 0, "elapsed_ms": 0, "error": None,
        "last_done_at": None,
    }


async def _row(db, data_source_id: str, user_id: str):
    return (await db.execute(
        select(LearnProgress).where(
            LearnProgress.data_source_id == data_source_id,
            LearnProgress.user_id == user_id,
        )
    )).scalar_one_or_none()


async def start(db, data_source_id: str, user_id: Optional[str], tables: int = 0, columns: int = 0) -> None:
    uid = user_id or ""
    row = await _row(db, data_source_id, uid)
    if row is None:
        row = LearnProgress(data_source_id=data_source_id, user_id=uid)
        db.add(row)
    row.status = "running"
    row.stage = "reading_tables"
    row.step = 1
    row.total = _TOTAL
    row.tables = int(tables or 0)
    row.columns = int(columns or 0)
    row.error = None
    row.started_at = datetime.utcnow()
    await db.commit()


async def set_stage(db, data_source_id: str, user_id: Optional[str], stage: str, step: int) -> None:
    uid = user_id or ""
    row = await _row(db, data_source_id, uid)
    if row is None:
        return
    row.status = "running"
    row.stage = stage
    row.step = int(step)
    await db.commit()


async def done(db, data_source_id: str, user_id: Optional[str]) -> None:
    uid = user_id or ""
    row = await _row(db, data_source_id, uid)
    if row is None:
        return
    # Do not clobber a real error: llm_sync swallows its overview exception and
    # still falls through to done(), so an errored run must stay errored.
    if row.status == "error":
        return
    row.status = "done"
    row.step = _TOTAL
    row.last_done_at = datetime.utcnow()
    await db.commit()


async def error(db, data_source_id: str, user_id: Optional[str], message: str) -> None:
    uid = user_id or ""
    row = await _row(db, data_source_id, uid)
    if row is None:
        row = LearnProgress(data_source_id=data_source_id, user_id=uid, started_at=datetime.utcnow())
        db.add(row)
    row.status = "error"
    row.error = (message or "")[:500]
    await db.commit()


async def get(db, data_source_id: str, user_id: Optional[str]) -> dict:
    uid = user_id or ""
    row = await _row(db, data_source_id, uid)
    if row is None and uid:
        # ★A learn nobody started still has to be watchable.
        #
        # Two real learns run with NO user: first-run seeding (the three agents
        # created at signup) and the auto-heal that fires when the first model
        # key is saved. Both stamp under `user_id = ""`, and this lookup is keyed
        # on the CALLER's id — so those two runs, the only ones a brand-new
        # member is likely to hit, could never be seen by anyone. The screen said
        # nothing for the entire first learn of the product's life.
        #
        # Falling back to the system row is safe: a learn with no user writes a
        # SHARED overview, so there is no per-member state to leak, and the
        # route has already checked `view` on this data source. A member's own
        # row always wins when they have one.
        row = await _row(db, data_source_id, "")
    if row is None:
        return _idle()
    elapsed = 0
    if row.started_at:
        elapsed = int((datetime.utcnow() - row.started_at).total_seconds() * 1000)
    return {
        # ★Normalised on the way OUT — see app/core/progress_status.py. This
        # tracker stores "done"/"error"; the indexing trackers store
        # "completed"/"failed" for exactly the same two outcomes. A consumer
        # that carried a check across from one screen to the other wrote a
        # comparison that could never be true, and a status test that never
        # fires reads as "the job never finished", not as a bug.
        "status": normalize_status(row.status),
        "stage": row.stage,
        "step": row.step,
        "total": row.total,
        "tables": row.tables,
        "columns": row.columns,
        "elapsed_ms": max(0, elapsed),
        "error": row.error,
        "last_done_at": row.last_done_at.isoformat() if row.last_done_at else None,
    }
