# Frontend + backend before/after — Fabric sync, notification, auto-resync

Supersedes the Fabric sections of `PLAN-FABRIC-SYNC-BEFORE-AFTER.md`. Read the
correction below first.

---

## ★★★CORRECTION to the earlier analysis

I previously wrote that Fabric "has no workspace concept — 414 lines, zero
matches for `workspace`". That was true of `ms_fabric_client.py` and **wrong as a
conclusion**. I had read the *system* connector and generalised from it.

There are **two** Fabric connectors, and the user's 20-workspace case is the one
I had not read:

| | `ms_fabric` (system) | **`fabric_user` (per-user)** |
|---|---|---|
| auth | admin's client_id/secret | the signing-in user's own token |
| scope | ONE lakehouse (`server_hostname` + `database`) | **every workspace the user can see** |
| discovery | none | `fabric_discovery.discover_endpoints()` |
| crawl | `MsFabricClient.get_tables()` | `_merge_all_fabric_endpoints()`, concurrent |
| progress | `connection_indexings` | `connection_sync_progress` (per endpoint) |
| indexed by | `ConnectionIndexingService` | `_run_federated_sync` at sign-in |

The per-user path **already** enumerates workspaces, crawls them concurrently,
records per-workspace progress, and renders per-workspace failures in the UI.

**This makes Phase C much cheaper than I estimated** — the filter goes in one
place, and the data the picker needs is already being produced.

---

# A — the Postgres auth failure

## Backend before

Covered in the previous document; unchanged. One engine, one password, failures
concentrated on **new** connections, and the indexing runner uses `NullPool`
(`database.py:297-306`) so every one of its sessions is a fresh authentication.

## Frontend before

The user sees the raw SQLAlchemy sentence, mid-conversation:

> Hit an internal error — retrying from the latest context (attempt 1/2)
> `This Session's transaction has been rolled back due to a previous exception
> during flush. ... Original exception was: password authentication failed for
> user "dash" (Background on this error at: https://sqlalche.me/e/20/7s2a)`

Three things wrong with it as a *user-facing* string: it names our database role,
it links to SQLAlchemy's docs, and it describes the *second* error (the rolled
back transaction) rather than the cause.

## After

**Backend** — bounded connect retry (`A.4`), plus classification so the message
that escapes to a user is ours, not the driver's.

**Frontend** — the retry banner keeps its behaviour but renders a written
sentence for infrastructure-class errors:

```vue
<!-- reports/[id]/index.vue — the retry banner -->
<span v-if="err.kind === 'infrastructure'">
  {{ $t('reportView.retryingInternal') }}   <!-- "A temporary problem on our side — retrying" -->
</span>
<span v-else>{{ err.message }}</span>       <!-- source errors keep their real text -->
```

★Only *our* failures get the friendly sentence. A Fabric permission error must
keep its own words — that one the user can act on, and replacing it with
"something went wrong" is how a fixable problem becomes a support ticket.

**Before:** the user reads `password authentication failed for user "dash"`.
**After:** the user reads that we had a temporary problem and it is retrying.

---

# B — a stuck sync is invisible

## Backend before

The full chain is in the previous document: the crash handler records the failure
through the same NullPool engine that just failed
(`connection_indexing_service.py:676-687`), swallows its own failure with
`except Exception: pass`, and the row stays non-terminal — so `wait_for_active`
burns its full 600s. Confirmed four times on connection `c27c46a2`.

## Frontend before

The per-user path polls, and the poll cannot distinguish "still working" from
"the runner died":

```ts
// composables/useConnectionSync.ts:122-127
function desiredInterval(state: SyncState): number {
  return isRunningStatus(state.status) ? FAST_MS : SLOW_MS   // 2s : 30s
}
```

A row stuck at `running` polls **every two seconds, forever**, and the strip
spins forever:

```vue
<!-- ConnectionSyncStrip.vue:35 -->
<Icon v-else-if="isRunning" name="heroicons:arrow-path" class="w-3.5 h-3.5 animate-spin" />
```

There is no client-side age limit anywhere in the composable. And the failure to
even *fetch* status is deliberately silent:

```ts
// useConnectionSync.ts:110-114
} catch (e) {
  // Deliberately silent. A status poll that cannot reach the server must not
  // put an error on screen — the sync itself may be perfectly healthy, and
  // the next tick will correct us.
}
```

★That comment is right for one missed tick and wrong for forty. Right now
"server unreachable for two minutes" and "everything is fine" look identical.

## After

**Backend**
1. Record the terminal state through the **main pooled** engine, with retry, and
   log at ERROR if it truly cannot be recorded (`_record_failure_resilient`).
2. New `error_kind`: `infrastructure` / `source_auth` / `source_error` /
   `cancelled` — four different messages and four different retry policies.
3. A reaper for rows already stuck, keyed on `updated_at` **not** `started_at`:
   a live crawl flushes progress every few seconds, so a healthy long sync always
   has a recent `updated_at`; keying on `started_at` would kill legitimate long
   syncs, which is worse than the bug.

**Frontend** — the strip must be able to say "this is not moving":

```ts
// useConnectionSync.ts — new
const STALL_AFTER_MS = 5 * 60 * 1000

/** Running, but nothing has changed for five minutes.
 *  ★Compares the PROGRESS FIELDS, not the poll count. A crawl of one very large
 *  lakehouse legitimately sits on the same endpoint for minutes while
 *  `tables` climbs; only a run where nothing at all advances is stalled. */
const isStalled = computed(() =>
  isRunning.value && Date.now() - lastProgressChangeAt.value > STALL_AFTER_MS
)

/** Back off a poll that is telling us nothing — a stuck row polled every 2s for
 *  an hour is 1,800 pointless requests per viewer. */
function desiredInterval(state: SyncState): number {
  if (isStalled.value) return SLOW_MS
  return isRunningStatus(state.status) ? FAST_MS : SLOW_MS
}
```

and consecutive fetch failures stop being silent after a threshold:

```ts
let consecutiveFailures = 0   // reset on any success
// after 5 in a row (~10s at FAST_MS) the strip says "can't reach the server",
// which is true, instead of continuing to show the last good state as if live.
```

**Before:** a dead sync spins forever, polling every 2s; the user is eventually
told to re-attach their lakehouse.
**After:** it goes terminal server-side within 30 min at worst, and the UI says
"not responding" rather than pretending to work.

---

# C — sync only the workspaces the user kept

## Backend before

Discovery returns **everything**, and there is no filter between discovery and
the crawl:

```python
# data_source_service.py:4993-4999
await _prog.update(_ds_id, _uid, phase="discovering")
from app.services.fabric_discovery import discover_endpoints
endpoints = await asyncio.to_thread(discover_endpoints, refresh_token)
endpoints = [e for e in endpoints if not self._is_fabric_staging_db(e.get("database"))]
if not endpoints:
    return None
await _prog.set_endpoints(_ds_id, _uid, endpoints)   # every one, all pending
```

`discover_endpoints` already returns the fields a picker needs —
`{tenant_id, tenant_name, workspace_id, workspace_name, item_type, item_id,
host, database}` (`fabric_discovery.py:238-239`) — and already accepts a
`tenant_ids` filter. **There is no `workspace_ids` filter.**

The crawl is already tuned, and this is the number to know:

```python
# data_source_service.py:5061-5062
_FABRIC_CRAWL_CONCURRENCY = 6
_sem = asyncio.Semaphore(min(_FABRIC_CRAWL_CONCURRENCY, max(1, len(endpoints))))
```

So 20 workspaces do not run serially — they run 6 at a time. **The fix is not
more concurrency, it is fewer endpoints.** Raising the semaphore would open more
concurrent ODBC connections per user, which the comment above it explicitly
guards against.

## Frontend before

The UI can already *display* per-workspace state — it just cannot change it. The
type carries the field:

```ts
// useConnectionSync.ts:45-55
export interface SyncDetailRow {
  name: string
  workspace?: string | null
  status: 'pending' | 'completed' | 'failed'
  tables: number
  error?: string | null
}
```

and the strip already lists the ones that did not answer:

```vue
<!-- ConnectionSyncStrip.vue:73-83 -->
<!-- Which workspaces did not answer. Shown under the strip on a partial
     result, because "3 of 4" is only useful if you can see which one. -->
<div v-for="d in failed" :key="d.name"> … </div>
```

**There is no selection control anywhere.** The user watches 20 workspaces sync
and cannot say "not those seventeen".

## After

**Backend** — one filter, one new store, applied at the single point where the
endpoint list is decided:

```python
# data_source_service.py — inside _merge_all_fabric_endpoints
endpoints = await asyncio.to_thread(discover_endpoints, refresh_token)
endpoints = [e for e in endpoints if not self._is_fabric_staging_db(e.get("database"))]

# ★Publish the FULL discovered list before filtering, so the picker can offer
# workspaces the user has not chosen yet. Filtering first would make a
# deselected workspace invisible and therefore un-reselectable.
await _prog.set_discovered(_ds_id, _uid, endpoints)

selected = await get_selected_workspace_ids(db, user_id=_uid, data_source_id=_ds_id)
if selected is not None:
    endpoints = [e for e in endpoints if e.get("workspace_id") in selected]

await _prog.set_endpoints(_ds_id, _uid, endpoints)   # only what will be crawled
```

```python
async def get_selected_workspace_ids(db, *, user_id, data_source_id) -> Optional[set[str]]:
    """The workspaces this user chose, or None if they never chose.

    ★★★None and set() must not collapse into one branch:
        None   → no preference → crawl everything (today's behaviour, unchanged)
        set()  → chose none    → crawl NOTHING
    `if not selected:` is the bug — it turns "I deselected everything" into the
    full 20-workspace crawl, which is the exact cost this exists to remove.
    """
```

New table, per user **and** per data source (a user may have two Fabric agents
and want different scopes):

```python
class UserWorkspaceSelection(Base):
    __tablename__ = "user_workspace_selections"
    user_id        = Column(String(36), ForeignKey("users.id"), nullable=False)
    data_source_id = Column(String(36), ForeignKey("data_sources.id"), nullable=False)
    workspace_id   = Column(String(64), nullable=False)
    selected       = Column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("user_id", "data_source_id", "workspace_id"),)
```

Routes:
```
GET  /data_sources/{id}/fabric-signin/workspaces    → discovered + selected flags
PUT  /data_sources/{id}/fabric-signin/workspaces    → {workspace_ids: [...]}
POST /data_sources/{id}/fabric-signin/resync        → exists already (:418)
```

**Frontend** — a picker fed by data the backend already produces:

```vue
<!-- new: datasources/FabricWorkspacePicker.vue -->
<div v-for="ws in workspaces" :key="ws.workspace_id" class="flex items-center gap-2 py-1.5">
  <UCheckbox v-model="ws.selected" :disabled="saving" />
  <span class="flex-1 truncate">{{ ws.workspace_name }}</span>
  <span class="text-xs text-gray-500">{{ ws.tables ?? '—' }}</span>
  <span v-if="ws.error" class="text-xs text-amber-600" :title="ws.error">
    {{ $t('data.syncDidNotAnswer') }}
  </span>
</div>

<!-- ★Never a bare "Save". Saving a smaller set is a promise about the NEXT sync,
     and the tables from deselected workspaces stay queryable until it runs —
     so say what will happen and when. -->
<p class="text-xs text-gray-500 mt-2">
  {{ $t('data.workspaceScopeNote', { selected: selectedCount, total: workspaces.length }) }}
</p>
<UButton :disabled="!dirty" @click="saveAndResync">
  {{ $t('data.saveAndResync') }}
</UButton>
```

```ts
// composables/useFabricWorkspaces.ts — new
// ★Deselecting everything is a legitimate choice and the UI must not silently
// treat it as "all". Confirm it, then send an empty array, which the backend
// reads as set() → crawl nothing.
async function save(ids: string[]) {
  if (ids.length === 0 && !(await confirmNoWorkspaces())) return
  await useMyFetch(`/data_sources/${dsId}/fabric-signin/workspaces`,
    { method: 'PUT', body: { workspace_ids: ids } })
}
```

**Before:** 20 workspaces discovered, 20 crawled, 6 concurrently, every sign-in.
**After:** 20 discovered and shown, 3 crawled — roughly 3/20 of the ODBC work,
with the other 17 still visible so they can be turned back on.

---

# D — telling the user it finished

## Backend before

The per-user path already writes a complete progress row —
`connection_sync_progress` has `status`, `phase`, `endpoints_total`,
`endpoints_done`, `endpoints_failed`, `tables`, `detail`, `error`, `started_at`,
`last_done_at`, uniquely keyed `(data_source_id, user_id)`.

Nothing is ever **pushed**. There is no emit on `prog.finish()`.

## Frontend before

★**The notification problem is structural, not cosmetic.** The poller only exists
while a component is mounted:

```ts
// useConnectionSync.ts:206-231
onMounted(async () => {
  entry.subscribers += 1
  if (entry.subscribers === 1) { … schedule(dsId.value, entry) }
})
onUnmounted(() => {
  entry.subscribers -= 1
  if (entry.subscribers <= 0) { if (entry.timer) clearInterval(entry.timer) … }
})
```

So the exact case the user describes — start a sync, go and do something else —
is the case where **nobody is watching and no notification can fire**. Navigating
to another page unmounts the last subscriber and stops the poll entirely.

## After

**Backend** — emit on every terminal transition, and persist the notification so
it survives the user being away:

```python
# services/connection_sync_progress.py — in finish() and fail()
await emit_user_event(user_id, {
    "type": "connection.sync.finished",
    "payload": {
        "data_source_id": ds_id,
        "status": row.status,                    # completed | partial | failed
        "workspaces_done": row.endpoints_done,
        "workspaces_failed": row.endpoints_failed,
        "tables": row.tables,
        "elapsed_ms": elapsed_ms,
        "error": row.error,
        "error_kind": row.error_kind,            # from B
    },
})
await create_notification(db, user_id=user_id, kind="sync_finished", payload=…)
```

★Emit on **failure and partial too**. A notification that only fires on success
leaves the two cases that actually need the user indistinguishable from a sync
still in progress.

**Frontend** — move the terminal signal off the per-component poller:

```ts
// A session-level subscription, mounted once in the layout, independent of
// whether any agent page is open. The per-component poller stays for live
// progress; this exists so the RESULT reaches the user wherever they are.
onUserEvent('connection.sync.finished', (p) => {
  toast.add({
    title: p.status === 'completed'
      ? t('data.syncFinished', { tables: p.tables, n: p.workspaces_done })
      : t('data.syncFinishedWithProblems', { failed: p.workspaces_failed }),
    color: p.status === 'completed' ? 'green' : 'amber',
    timeout: p.status === 'completed' ? 5000 : 0,   // a problem waits to be read
  })
})
```

**Before:** the result is only visible to someone already looking at that agent's
page; leave the page and it is lost.
**After:** "Fabric sync finished — 214 tables across 3 workspaces in 4m12s",
wherever they are, and a failure that stays on screen until dismissed.

---

# E — the per-user auto-resync

## Backend before

Per-user sync runs at **sign-in only**:

```python
# routes/fabric_user_signin.py:256-261
def _kick_off_sync(data_source: DataSource, user: User) -> None:
    """Mark a sync started and schedule it on the event loop (fire-and-forget).
    Returns immediately so the sign-in request is not blocked on the ~20-30s
    multi-endpoint pull. The UI polls ``/fabric-signin/sync-status`` for progress."""
```

and the scheduler deliberately skips this whole class:

```python
# services/scheduled_reindex.py:131-134
# Per-user catalogs (OneDrive, personal Drive) have no admin-side
# catalog to re-index — they heal on each user's sign-in. Skip.
if svc._is_per_user_catalog(conn.type):
    continue
```

"They heal on each user's sign-in" is true and insufficient: a user who stays
signed in for a week has a week-old catalog, and every sync is a **full** crawl
of every endpoint regardless of whether anything changed.

There is a manual escape hatch — `POST /fabric-signin/resync` (`:418`) — so the
user *can* force it, if they know to.

## Frontend before

No display of catalog age. `last_done_at` is in the state object
(`useConnectionSync.ts:67`) and is used only to decide whether the strip is worth
showing at all (`hasSomethingToSay`, `:265-267`). The user is never told their
catalog is stale, so the resync button is only pressed after something has
already gone wrong.

## After

**Backend**
- **E.1 ⚠ first, and it may change everything else:** can Fabric report that a
  workspace changed without a crawl? If not, per-user polling of 20 workspaces
  *is* the cost being removed and the design must change. Do not write E.2 before
  answering this.
- **E.2** cheap fingerprint per endpoint (table count + max modify date) before
  paying for a full column crawl. ★A fingerprint that misses a change is worse
  than no fingerprint — silently stale beats merely slow only in the wrong
  direction. Anything uncertain falls through to the full crawl.
- **E.3** schedule against **use**: someone who has not opened a report in a week
  does not need an hourly crawl; someone about to open a dashboard does.
- **E.4** coalesce per user — one user, twenty endpoints, one job. A naive
  per-connection loop gives twenty concurrent crawls for one person, which is
  how today's slowness returns by another route.

**Frontend**
```vue
<!-- in the strip, when idle and a catalog age is known -->
<span v-if="isDone && staleHours > 24" class="text-xs text-gray-500">
  {{ $t('data.syncAgeHours', { hours: staleHours }) }}
  <UButton size="2xs" variant="link" @click="resync">{{ $t('data.syncNow') }}</UButton>
</span>
```
★Shown only past a threshold. A permanent "last synced 4 minutes ago" is the
noise the strip's own design comment already argues against.

**Before:** catalogs refresh at sign-in; every refresh is a full crawl; staleness
is invisible.
**After:** fingerprint first, crawl what moved, one job per user, and the age is
on screen once it is old enough to matter.

---

# F — "attach or refresh the lakehouse"

## Backend before

The agent can only query synced tables, and nothing checks whether the sync that
should have produced them actually succeeded. The Fabric client also discards its
own errors:

```python
# ms_fabric_client.py:238-243
def get_tables(self) -> List[Table]:
    """Get tables with graceful fallback if enriched query fails."""
    try:
        return self._get_tables_enriched()
    except Exception:
        return self._get_tables_basic()     # ← the real reason is thrown away
```

A permission error and a syntax error are indistinguishable here.

## Frontend before

The message renders as ordinary assistant markdown — no link to the connection,
no sync state, nothing actionable. The user is told to "attach or refresh the
lakehouse" with no way to do either from where they are standing.

## After

**Backend** — check our own state before blaming theirs:

```python
async def explain_missing_table(db, endpoints, table_name) -> str:
    """★Order matters. A failed or stalled sync is OUR failure and must be named
    as one; only when every relevant sync genuinely succeeded is "that table is
    not here" a fact rather than a guess."""
    bad = [e for e in endpoints if (await last_sync_status(db, e)) != "completed"]
    if bad:
        return (f"'{table_name}' may well exist — the catalog for {names(bad)} "
                f"did not finish syncing ({reason(bad)}). This is not something "
                f"you need to fix; retry the sync.")
    return (f"'{table_name}' is not in the workspaces currently synced: "
            f"{names(endpoints)}.")
```

and log the swallowed exception in `get_tables` before falling back, so a
permission problem is visible instead of silently costing every description.

**Frontend** — make the message actionable where it appears:

```vue
<!-- in the tool-error block -->
<div v-if="err.code === 'table_not_synced'" class="mt-2 flex items-center gap-2">
  <UButton size="2xs" @click="resync(err.data_source_id)">{{ $t('data.syncNow') }}</UButton>
  <UButton size="2xs" variant="soft" @click="openWorkspacePicker(err.data_source_id)">
    {{ $t('data.chooseWorkspaces') }}
  </UButton>
</div>
```

**Before:** a wall of prose telling the user to fix something that was our outage.
**After:** the true cause, and the two buttons that resolve it.

---

# Revised sizing

C is smaller than first estimated — the per-user path already discovers
workspaces, records them, and renders them.

| phase | backend | frontend | total |
|---|---|---|---|
| A | 1h + infra | 30m | ~2h |
| B | 3h | 1.5h | ~5h |
| **C** | **2h** | **3h** | **~5h** (was 10h) |
| D | 2h | 2h | ~4h |
| E | 6h + E.1 | 1h | ~8h |
| F | 1.5h | 1h | ~3h |

Order unchanged: **A → B → C → D → F → E.**
