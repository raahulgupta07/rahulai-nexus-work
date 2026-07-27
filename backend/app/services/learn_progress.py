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
    if row is None:
        return _idle()
    elapsed = 0
    if row.started_at:
        elapsed = int((datetime.utcnow() - row.started_at).total_seconds() * 1000)
    return {
        "status": row.status,
        "stage": row.stage,
        "step": row.step,
        "total": row.total,
        "tables": row.tables,
        "columns": row.columns,
        "elapsed_ms": max(0, elapsed),
        "error": row.error,
        "last_done_at": row.last_done_at.isoformat() if row.last_done_at else None,
    }
