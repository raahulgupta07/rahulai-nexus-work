from sqlalchemy import Column, String, DateTime, Boolean, Text

from app.models.base import BaseSchema


class LocalRuntime(BaseSchema):
    """A user's paired local helper (Cowork-style local execution runtime).

    One row per paired device. Pairing: the app mints a short-lived 6-digit
    code (hash stored here, status='pending'); the helper claims it and gets a
    long-lived runtime token (hash stored here, status='paired'). The helper
    then polls for jobs over HTTP — no websockets, because the app runs
    multiple uvicorn workers and any in-process connection map would be
    invisible to the worker that serves the next request (the proven
    learn-progress landmine). All coordination is DB-backed.
    """
    __tablename__ = "local_runtimes"

    organization_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    name = Column(String, nullable=True)              # device name, e.g. "Rahul's MacBook"
    status = Column(String, nullable=False, default="pending")  # pending|paired|revoked
    pair_code_hash = Column(String, nullable=True)    # sha256 of the 6-digit code
    pair_expires_at = Column(DateTime, nullable=True)
    token_hash = Column(String, nullable=True, index=True)  # sha256 of the runtime token
    last_seen = Column(DateTime, nullable=True)       # heartbeat / last poll
    helper_version = Column(String, nullable=True)
    # Per-user preference: actually route execution to this helper when online.
    # Starts OFF. Pairing a device is not consent to run code on it — the user
    # turns "Run analysis on my computer" on themselves once they are ready.
    run_local_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    # Schema-only catalog of the folders this helper was started with
    # (--allow-folder). Posted by the helper's `scan_folder` op; SCHEMA ONLY —
    # table names, columns+types, row counts. No rows, no file contents, ever.
    # Shape: [{"name","path","tables":[{"name","file","format","row_count",
    #          "columns":[{"name","type"}]}],"error"?}]
    # Stored as Text-JSON (not JSONB) to match how this codebase persists other
    # small blobs and to stay portable across sqlite/postgres.
    folders_schema = Column(Text, nullable=True)
    folders_scanned_at = Column(DateTime, nullable=True)


class LocalRuntimeJob(BaseSchema):
    """One remote-execution job for a paired helper. The execution seam inserts
    a row; the helper long-polls `next`, runs the code, POSTs the result back;
    the seam polls the row until done/error/timeout then falls back or returns.
    DB-backed so any uvicorn worker can serve any leg of the exchange.
    """
    __tablename__ = "local_runtime_jobs"

    runtime_id = Column(String(36), nullable=False, index=True)
    organization_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)  # pending|running|done|error|expired
    code = Column(Text, nullable=True)                # the generate_df() python
    payload_json = Column(Text, nullable=True)        # ds client keys, excel meta, options
    result_b64 = Column(Text, nullable=True)          # Arrow IPC (or JSON) DataFrame, base64
    result_format = Column(String, nullable=True)     # arrow|json
    stdout = Column(Text, nullable=True)
    queries_json = Column(Text, nullable=True)        # executed queries list
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
