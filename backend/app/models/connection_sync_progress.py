from sqlalchemy import Column, String, Integer, DateTime, JSON, UniqueConstraint

from app.models.base import BaseSchema


class ConnectionSyncProgress(BaseSchema):
    """Live progress of a per-user connector sync, shared across workers via the DB.

    Covers the connectors where a member signs in with their own Microsoft
    account and we then crawl every workspace that account can reach —
    ``fabric_user`` and ``powerbi_user``. One row per (data_source, user),
    because each member's sync covers a different set of workspaces.

    ★Why this is a TABLE and not a module-level dict
    ------------------------------------------------
    The predecessor, ``app/services/fabric_sync_progress.py``, was a plain dict
    whose docstring claimed "single-process, single event-loop → safe". That was
    true when it was written and is not true now: ``start.sh`` runs uvicorn with
    up to 4 workers. The sync runs in whichever worker served the sign-in, while
    the browser's ``/sync-status`` poll round-robins across all of them — so 3
    polls out of 4 hit a worker whose dict is empty and get back ``idle``. The
    UI reads ``idle`` as "nothing is running", stops polling and closes down,
    which is a large part of why a sync appears to vanish. Same class of bug as
    the one already fixed in ``learn_progress``, and fixed the same way.

    Ephemeral by intent: rows are upserted per run, and a terminal row is simply
    overwritten by the next sync. ``last_done_at`` deliberately survives a new
    run so the UI can always say when this member last synced, even when idle.
    """
    __tablename__ = "connection_sync_progress"
    __table_args__ = (
        UniqueConstraint('data_source_id', 'user_id', name='uq_connection_sync_progress'),
    )

    data_source_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, default="")

    # syncing | done | partial | error.  `partial` is a SUCCESS state: some
    # workspaces answered and some did not, which is the ordinary outcome for a
    # member whose account spans tenants. It is tracked separately from `error`
    # so the UI can say "3 of 4, and here is the one that did not answer"
    # instead of showing a failure over a working agent.
    status = Column(String, nullable=False, default="syncing")
    # discovering | ingesting | done | error
    phase = Column(String, nullable=True)

    endpoints_total = Column(Integer, nullable=False, default=0)
    endpoints_done = Column(Integer, nullable=False, default=0)
    endpoints_failed = Column(Integer, nullable=False, default=0)
    tables = Column(Integer, nullable=False, default=0)

    # Per-endpoint detail, so the UI can name each workspace as it lands rather
    # than showing one anonymous bar:
    #   [{"name": "DL_POC", "kind": "lakehouse", "tenant": "...",
    #     "status": "ok|failed|pending", "tables": 29, "error": "timed out"}]
    # JSON (not JSONB) to match the column type used elsewhere in this schema.
    detail = Column(JSON, nullable=True)

    error = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    # Survives across runs — a fresh start() never clears it.
    last_done_at = Column(DateTime, nullable=True)
