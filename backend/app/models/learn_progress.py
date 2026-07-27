from sqlalchemy import Column, String, Integer, DateTime, UniqueConstraint

from app.models.base import BaseSchema


class LearnProgress(BaseSchema):
    """Live progress of a "Learn agent" run (llm_sync force_llm), shared across
    workers via the DB. The app runs multiple uvicorn workers, so an in-memory
    dict is invisible to the worker that serves the /learn-status poll — this
    table is the shared source of truth. One row per (data_source, user); the
    per-user-token connectors run a Learn per signed-in member. Ephemeral by
    intent: rows are upserted each run and can be cleaned up on a TTL sweep.
    """
    __tablename__ = "learn_progress"
    __table_args__ = (
        UniqueConstraint('data_source_id', 'user_id', name='uq_learn_progress'),
    )

    data_source_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, default="")  # "" when no user
    status = Column(String, nullable=False, default="idle")   # idle|running|done|error
    stage = Column(String, nullable=True)                     # reading_tables|analyzing|generating_overview|grounding_publishing
    step = Column(Integer, nullable=False, default=0)
    total = Column(Integer, nullable=False, default=4)
    tables = Column(Integer, nullable=False, default=0)
    columns = Column(Integer, nullable=False, default=0)
    error = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    # When the LAST successful learn finished. Persists across runs (a new
    # start() does NOT clear it) so the UI can always show "Last learned: …"
    # even when idle.
    last_done_at = Column(DateTime, nullable=True)
