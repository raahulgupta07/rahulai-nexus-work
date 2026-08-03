# Before / after, with the code — DB auth, sync visibility, Fabric scoping, notify, auto-resync

Companion to `PLAN-FABRIC-SYNC-AND-DB.md`. Every "before" below is quoted from
the tree at `0.0.510.10`; every claim from the log cites its count.

---

# A — the Postgres auth failure

## Before

One engine, one pool, one password. `create_async_session_factory()` returns the
cached singleton, so the failing background tasks are **not** on a second
identity:

```python
# app/settings/database.py:373
def create_async_session_factory():
    engine = create_async_database_engine()      # the singleton
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
```

```python
# app/settings/database.py:253-262 — the main pool
engine = create_async_engine(
    database_url,
    pool_size=20,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,     # ← 30 min: an idle pool ages out and must reconnect
    pool_pre_ping=True,    # ← a dead connection is discarded and re-established
    connect_args=connect_args,
)
```

★**The indexing runner deliberately uses NullPool**, so *every* session it opens
is a brand-new TCP connection and a brand-new authentication:

```python
# app/settings/database.py:297-306 (docstring)
"""Dedicated NullPool async engine for the connection-indexing background
loop. ... forces NullPool so connections never get shared across event loops."""
```

That single design choice is why indexing is hit hardest: it never benefits from
a warm connection.

**What the log shows** — failures track idleness, not load:

| minute | records | auth failures |
|---|---:|---:|
| 05:38 | 197 | **0** |
| 05:39 | 208 | **0** |
| 06:24 | 17 | **5** |
| 06:36 | 21 | **9** |

Ruled out by evidence, not by intuition:

| hypothesis | disproof |
|---|---|
| wrong password | most connections in the same hour succeeded |
| pool exhaustion | 0 × `too many clients` / `QueuePool` / `pool timed out` in 3,236 records |
| IAM token expiry | `IAM auth hook attached` appears **0** times |
| two configs | `get_url()` returns a single `self.url`; one cached engine |

## After

**A.1 (infra, no code)** — establish whether one address serves more than one
backend:
```bash
for i in $(seq 20); do psql "$DASH_DATABASE_URL" -tAc "select inet_server_addr()"; done
```
A varying address is the answer on its own.

**A.4 (code, only after the infra fix)** — a bounded connect retry so a transient
rejection does not become a user-visible failure:

```python
# app/settings/database.py — new
_CONNECT_RETRY_ATTEMPTS = 3
_CONNECT_RETRY_BASE_S = 0.2

def _attach_connect_retry(engine) -> None:
    """Retry a REJECTED connect a few times, with backoff.

    ★A seatbelt, not a fix. It is deliberately narrow: only InvalidPasswordError,
    only at connect time, only three attempts. A genuinely wrong credential still
    fails fast and loudly — retrying that one into silence would turn a five-minute
    diagnosis into a week of intermittent mystery.
    """
    @event.listens_for(engine.sync_engine, "do_connect")
    def _retrying_connect(dialect, conn_rec, cargs, cparams):
        last = None
        for attempt in range(_CONNECT_RETRY_ATTEMPTS):
            try:
                return dialect.connect(*cargs, **cparams)
            except asyncpg.exceptions.InvalidPasswordError as exc:
                last = exc
                if attempt + 1 < _CONNECT_RETRY_ATTEMPTS:
                    time.sleep(_CONNECT_RETRY_BASE_S * (2 ** attempt))
        _log_rejection_once_per_minute(last)
        raise last
```

**Before:** one rejected connect = one failed background tick, 88 times an hour.
**After:** a transient rejection costs ~600ms and is invisible; a real one still
fails, and is logged once a minute instead of 88 times.

---

# B — a crashed sync leaves no trace, and blocks every caller

## Before — this is the bug that made the Fabric sync "hang"

The crash handler tries to record the failure, but it opens that session on **the
same engine that just failed**, and swallows its own failure:

```python
# app/services/connection_indexing_service.py:676-687
except Exception as exc:  # pragma: no cover — last-ditch guard
    logger.exception("indexing.run.crash", extra={"indexing_id": indexing_id})
    try:
        async with _new_session() as err_db:          # ← NullPool engine: NEW connection
            fresh = await err_db.get(ConnectionIndexing, indexing_id)
            if fresh is not None and not fresh.is_terminal():
                fresh.status = ConnectionIndexingStatus.FAILED.value
                fresh.error = str(exc)[:4000]
                fresh.finished_at = datetime.utcnow()
                await err_db.commit()
    except Exception:
        pass                                          # ← the failure to record is discarded
```

★**When the crash cause IS the database, recording the crash uses the very thing
that is broken.** The chain, end to end:

1. Postgres rejects a new connection → `InvalidPasswordError`
2. `_run` crashes. Confirmed: `indexing.run.crash` at 05:46:05, last traceback
   line is `asyncpg.exceptions.InvalidPasswordError: password authentication
   failed for user "dash"`
3. The handler opens **another new connection** on the same NullPool engine to
   write `status='failed'` — and it is rejected too
4. `except Exception: pass` discards that
5. The row stays `PENDING`/`RUNNING` **forever** — nothing else ever finalises it
6. `wait_for_active` polls that row for its full 600s and raises:

```python
# connection_indexing_service.py:244-256
deadline = time.perf_counter() + timeout_s     # timeout_s = 600.0
while time.perf_counter() < deadline:
    active = await self.get_active(db, connection_id)
    if active is None:
        return
    await asyncio.sleep(poll_interval_s)
logger.warning("indexing.wait_for_active.timeout", ...)
raise TimeoutError(...)
```

Observed exactly: **four** `wait_for_active.timeout`, all on the *same*
connection `c27c46a2-63b3-4ee0-8fb0-29ce16eceeb6`, at 05:35:37, 05:35:43,
05:35:48 and 05:37:19 — four callers each burning ten minutes on one row that
could never reach a terminal state.

**That is the "sync takes long time" the user reports.** It is not slow. It is
stuck, and nothing can tell them so.

## After

**B.1 — record the terminal state on a connection that is not the broken one.**

```python
except Exception as exc:
    logger.exception("indexing.run.crash", extra={"indexing_id": indexing_id})
    # ★Record through the MAIN pooled engine, not this run's NullPool engine.
    # A crash caused by "cannot open a new connection" cannot be recorded by
    # opening a new connection — that is how a failed run became an eternal
    # RUNNING row that blocked four callers for ten minutes each.
    await self._record_failure_resilient(
        indexing_id,
        error=str(exc)[:4000],
        kind=_classify_failure(exc),
    )
```

```python
async def _record_failure_resilient(self, indexing_id, *, error, kind, attempts=3):
    """Best-effort but LOUD. Tries the pooled engine (warm connections), then
    the run's own engine, then gives up — and if it gives up it says so at
    ERROR, because a swallowed failure here is indistinguishable from a run
    that is still working."""
    for factory in (async_session_maker, self._run_session_factory):
        for attempt in range(attempts):
            try:
                async with factory() as db:
                    fresh = await db.get(ConnectionIndexing, indexing_id)
                    if fresh is None or fresh.is_terminal():
                        return
                    fresh.status = ConnectionIndexingStatus.FAILED.value
                    fresh.error = error
                    fresh.error_kind = kind          # new column, see B.2
                    fresh.finished_at = datetime.utcnow()
                    await db.commit()
                    return
            except Exception:
                await asyncio.sleep(0.2 * (2 ** attempt))
    logger.error("indexing.failure_unrecordable", extra={"indexing_id": indexing_id})
```

**B.2 — separate our failure from the source's.** New column
`connection_indexings.error_kind`:

| kind | meaning | retry? | user sees |
|---|---|---|---|
| `infrastructure` | our DB/network — `InvalidPasswordError`, `OperationalError` | yes, automatic | "Sync interrupted, retrying" |
| `source_auth` | Fabric/Power BI rejected the credential | no | "Fabric rejected the credential — reconnect" |
| `source_error` | source reachable, query failed | once | the source's own message |
| `cancelled` | user | no | — |

★Today all four produce the same opaque string, so a user is told to re-attach a
lakehouse when in fact **our** database was unreachable. That is the message in
the incident screenshot.

**B.3 — a reaper for rows that already got stuck.** Nothing today can rescue a
row whose runner died:

```python
async def reap_stale_indexings(db, *, stale_after_minutes=30):
    """Fail rows whose runner is provably gone.

    ★Keyed on `updated_at`, not `started_at`: a long legitimate crawl keeps
    flushing progress every few seconds (`_PROGRESS_FLUSH_SECONDS`), so a live
    run always has a recent `updated_at`. A run whose process died stops
    touching the row entirely. Using `started_at` would kill healthy long syncs,
    which is worse than the bug being fixed.
    """
```

**Before:** one dead runner blocks that connection indefinitely; four callers
wait 600s each; the user is told to re-attach their lakehouse.
**After:** the row goes terminal within 30 min at worst, immediately in the normal
case, and says which of the two systems failed.

---

# C — Fabric workspace scoping

## Before

**A Fabric connection is one lakehouse, not a workspace.** The constructor takes
a single endpoint:

```python
# app/data_sources/clients/ms_fabric_client.py:40-49
def __init__(self, server_hostname: str, database: str, tenant_id: str = None,
             client_id: str = None, client_secret: str = None,
             schema: Optional[str] = None, access_token: str = None):
```

There is **no workspace enumeration anywhere in this client** — 414 lines, zero
matches for `workspace`.

A *schema* filter already exists, and it is the exact shape the workspace filter
should copy:

```python
# ms_fabric_client.py:57-68 — comma-separated, deduped, order-preserving
if isinstance(self.schema, str) and self.schema.strip():
    parts = [s.strip() for s in self.schema.split(",") if s.strip()]
    ...
# ms_fabric_client.py:260-262 — pushed down into SQL
if self._schemas:
    schema_list = ", ".join([f"'{s}'" for s in self._schemas])
    where_clauses.append(f"c.TABLE_SCHEMA IN ({schema_list})")
```

Power BI already has the connection-level equivalent:

```python
# powerbi_client.py:206-211
# Optional comma-separated workspace names or IDs limiting discovery
self.workspaces = workspaces
self._workspace_filter = {
    w.strip().lower() for w in (workspaces or "").split(",") if w.strip()
}
```

Workspace routing lives one level up, in the federated overlay — and it already
knows a SQL endpoint host *is* a workspace:

```python
# ms_fabric_federated_client.py:124-125
# A SQL endpoint hostname is per-workspace, so same host == same
# workspace == the engine can do the join itself.
```

So **"20 workspaces" means ~20+ Fabric connections**, each indexed by its own
`ConnectionIndexing` row. The sweep picks them up per connection:

```python
# app/services/scheduled_reindex.py:105-118
select(Connection).where(
    Connection.is_active.is_(True),
    Connection.deleted_at.is_(None),
    Connection.auto_reindex_enabled.is_(True),
    (Connection.next_retry_at.is_(None)) | (Connection.next_retry_at <= now),
)
```

`is_active` is the only on/off switch and it is **org-wide** — one user
deselecting three workspaces would turn them off for everybody.

## After

**C.1 — the unit of selection is the connection.** It matches the architecture
exactly and introduces no new concept. A "workspace" in the UI is the set of
connections sharing a SQL endpoint host, which the federated client already
computes (`_workspace_groups`).

**C.2 — per-user selection, stored separately from `is_active`:**

```python
class UserConnectionSelection(Base):
    """Which connections a user wants synced and searched.

    ★Deliberately NOT `Connection.is_active`: that is org-wide, so honouring a
    personal choice there would switch a source off for every other member.
    """
    __tablename__ = "user_connection_selections"
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    connection_id = Column(String(36), ForeignKey("connections.id"), nullable=False)
    selected = Column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("user_id", "connection_id"),)
```

**C.3 — honoured at the sweep, with the trap made explicit:**

```python
async def _selected_connection_ids(db, user_id) -> Optional[set[str]]:
    """The user's chosen connections, or None when they have never chosen.

    ★★★None and set() are NOT the same and must never collapse into one branch:
      None      → no preference recorded → sync everything (today's behaviour)
      set()     → deselected everything  → sync NOTHING
    Treating an empty selection as "no filter" fires the full 20-workspace crawl
    the moment somebody clears their checkboxes — precisely the cost this exists
    to remove. `if not selected:` is the bug; `if selected is None:` is correct.
    """
```

**Before:** a user with 20 workspaces re-crawls all 20; deselecting is only
possible org-wide.
**After:** 3 selected → 3 indexing rows → roughly 3/20 of the work, and nobody
else's catalog changes.

---

# D — telling the user it finished

## Before

Everything needed is already written to the row on a throttled schedule:

```python
# connection_indexing_service.py:415-423
async with _new_session() as flush_db:
    fresh = await flush_db.get(ConnectionIndexing, indexing_id)
    fresh.phase          = snap["phase"]
    fresh.current_item   = snap["item"]
    fresh.progress_done  = snap["done"]
    fresh.progress_total = snap["total"]
```

and completion already composes a human sentence — into `events_json` only:

```python
# connection_indexing_service.py:652-656
await _append_event(
    "info", _state_snapshot()["phase"],
    f"Completed: {item_count} {item_label} in {elapsed_s}s{size_note}",
    done=item_count, total=item_count,
)
```

`stats_json` even carries `unreadable_datasets` — models found but not readable
for permission reasons (`:639-641`). **Nothing is pushed anywhere.** A user
learns the sync finished by reloading the page.

## After

**D.1** — emit on the terminal transition, reusing the SSE path the report
activity hub already runs on:

```python
await emit_user_event(user_id, {
    "type": "connection.sync.finished",
    "payload": {
        "connection_id": connection_id,
        "status": fresh.status,              # completed | failed | cancelled
        "summary": f"{item_count} {item_label} in {elapsed_s}s",
        "unreadable": stats_json.get("unreadable_dataset_count", 0),
        "error": fresh.error,
        "error_kind": fresh.error_kind,      # from B.2
    },
})
```

★Emit on **every** terminal status, not just success. A failure that notifies
nobody is what today already does.

**Before:** finished silently; a failure is indistinguishable from still-running.
**After:** "Fabric sync finished — 214 tables in 4m12s, 2 models skipped (no
permission)", or the real reason it stopped.

---

# E — the per-user auto-resync agent

## Before — more exists than you would expect

`scheduled_reindex.py` is already a real scheduler: feature-gated, batched,
oldest-first, timezone-aware, with a backoff gate stamped *before* the kick so a
mid-run crash cannot re-kick on the next tick:

```python
# scheduled_reindex.py:145-150
# Stamp the backoff gate BEFORE kicking so a crash/failure mid-run can't
# leave the connection eligible to be re-kicked on the very next tick.
for conn in due:
    conn.next_retry_at = next_run_after(conn, now, tz)
await db.commit()
```

Two gaps, both explicit in the code:

```python
# scheduled_reindex.py:131-134
# Per-user catalogs (OneDrive, personal Drive) have no admin-side
# catalog to re-index — they heal on each user's sign-in. Skip.
if svc._is_per_user_catalog(conn.type):
    continue
```

1. **Per-user sources are never scheduled at all** — they refresh only at
   sign-in. A user who stays logged in for a week gets a week-old catalog.
2. **Every run is a full crawl.** `indexing_service.start()` takes no hint that
   nothing has changed, so a 20-workspace tenant pays a full crawl per interval
   whether or not a single table moved.

## After

**E.1 is an investigation and must come first** ⚠ — can Fabric report a change
without a full crawl? If it cannot, per-user polling of 20 workspaces *is* the
cost being removed, and the design changes. Do not write E.2 before answering it.

**E.2 — cheap fingerprint before an expensive crawl:**

```python
def schema_fingerprint(client) -> str:
    """A cheap value that changes when the catalog changes.

    One INFORMATION_SCHEMA aggregate per lakehouse instead of a full column
    crawl: table count, plus the max modify date sys.tables already tracks.
    ★A fingerprint that misses a change is worse than no fingerprint — it makes
    the catalog silently stale instead of merely slow. Anything uncertain must
    fall through to the full crawl.
    """
```

**E.3 — schedule by use, not by clock.** A user who has not opened a report in a
week does not need an hourly crawl; one about to open a dashboard does.

**E.4 — coalesce per user.** One user, twenty connections, one job. Never twenty
concurrent crawls for one person — which is what a naive per-connection loop
gives you, and it is how the current slowness would come straight back.

**Before:** full crawl of everything on a fixed interval; per-user sources only
at sign-in.
**After:** fingerprint first, crawl only what moved, one coalesced job per user,
scheduled against real usage.

---

# F — "attach or refresh the lakehouse"

## Before

The agent's message is *correct given what it knows* — it can only query synced
tables. But the incident had the sync **crash** (section B), so the user was told
to fix something that was never their doing:

> Attach or refresh the lakehouse/schema that actually holds
> `cfc_accuracy_by_outlet` … then re-run the ask

There is no check of whether that connection's last sync succeeded.

Compounding it, the Fabric client hides its own failures:

```python
# ms_fabric_client.py:238-243
def get_tables(self) -> List[Table]:
    """Get tables with graceful fallback if enriched query fails."""
    try:
        return self._get_tables_enriched()
    except Exception:
        return self._get_tables_basic()     # ← the real error is discarded
```

A permission error and a syntax error are indistinguishable here; the catalog
just quietly loses its descriptions.

## After

```python
async def explain_missing_table(db, connection_ids, table_name) -> str:
    """Say why a table is not queryable, checking OUR state before blaming theirs.

    ★Order matters. A failed or stuck sync must be reported as ours; only when
    every relevant sync genuinely succeeded is "that table is not in these
    lakehouses" a true statement rather than a guess.
    """
    failed = [c for c in connection_ids if (await last_sync(db, c)).status != "completed"]
    if failed:
        return (f"'{table_name}' may exist, but the catalog for "
                f"{names(failed)} did not finish syncing ({reason(failed)}). "
                f"Retry the sync — this is not something you need to fix.")
    return (f"'{table_name}' is not in the lakehouses currently attached: "
            f"{names(connection_ids)} (all synced successfully).")
```

Plus: log the swallowed exception in `get_tables` before falling back, so a
permission problem is visible instead of silently costing every description.

**Before:** the user is told to re-attach a lakehouse when our database was down.
**After:** the message names the actual failing system, and "not found" is only
said when it is true.

---

# Order

1. **A** — every measurement below it is taken through a broken connection
2. **B** — cheap, and it is the actual cause of the "sync hangs" report
3. **C** — the request itself
4. **D** — small once C defines the job boundaries
5. **F** — small, needs B's `error_kind`
6. **E** — largest, and E.1 may reshape it

★B is listed second, not last, because **the reported symptom is B, not C.**
Scoping the sync to three workspaces would not have fixed a run that hung on a
row nothing could finalise.
