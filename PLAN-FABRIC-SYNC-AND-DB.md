# Plan — production DB auth, Fabric sync scoping, notification, auto-resync

Written 2026-08-03 from `insights-2026-08-3-logs.txt` (prod, `app-insights-prod`,
3,236 records, 05:30:20 → 06:48:05) and the code as it stands at `0.0.510.10`.

★Nothing in this plan is inferred from a symptom alone. Where a claim comes from
the log it cites the count; where it comes from the code it cites the file. The
two items I could **not** settle from the evidence are marked ⚠ and are
diagnosis steps, not fixes — do not skip them into a code change.

---

## 0. What the evidence actually says

| finding | evidence |
|---|---|
| `password authentication failed for user "dash"` × 88 | 05:31:46 → 06:37:52, 10 different loggers |
| Not a wrong password | most connections in the same hour succeeded |
| Not pool exhaustion | 0 × `too many clients`, `QueuePool`, `pool timed out` in 3,236 records |
| Not IAM token expiry | `IAM auth hook attached` appears **0** times; prod uses a static password |
| Not two configs | `DatabaseConfig.get_url()` returns a single `self.url`; `create_async_session_factory()` returns the cached singleton engine |
| **Fails when idle, not when busy** | 05:38 → 197 records / **0** failures; 06:36 → 21 records / **9** failures |
| **The Fabric sync crash IS this bug** | `indexing.run.crash` 05:46:05, last traceback line is `asyncpg.exceptions.InvalidPasswordError` |
| Sync also times out waiting | `indexing.wait_for_active.timeout` × 4 |

The idle correlation is the load-bearing fact. Busy minutes reuse warm pooled
connections; idle minutes let the pool age out (`pool_recycle=1800`,
`pool_pre_ping=True`, `database.py:256-261`) so each tick must open a **new**
connection — and it is new connections that get rejected. The application cannot
send a password it does not have, so the rejection is server-side or in between.

★**The user-visible Fabric failure and the "internal error" banner are the same
bug.** Do not tune the sync job against this noise.

---

## Phase A — stop the bleeding (production, no code first) ⚠

**A.1 — Find where the rejection happens** *(30m)*
```bash
grep -i "password authentication failed" /var/log/postgresql/*.log   # client IP?
psql "$DASH_DATABASE_URL" -c "select inet_server_addr(), inet_server_port(), version()"
for i in $(seq 20); do psql "$DASH_DATABASE_URL" -tAc "select inet_server_addr()"; done
```
→ **DONE** = we know whether one address serves more than one backend.

**A.2 — Rank the three candidates against A.1's output** *(15m)*
1. PgBouncer / pooler with an out-of-sync `userlist` or `auth_query` — hits
   reconnects specifically, which is exactly the observed shape.
2. More than one Postgres behind one address (failover pair, load-balanced
   endpoint) where one holds a stale password. Fits "some new connects succeed,
   some fail" better than anything else.
3. Password rotated while the app ran — alone this fails *every* new connect, so
   it only explains the data in combination with (2).

**A.3 — Fix at the infrastructure layer** *(varies)* → 0 failures over an idle hour.

**A.4 — Only then, make the app survive it anyway** *(1h)*
Retry on `InvalidPasswordError` at connect time with backoff, bounded, logged
once per minute rather than per attempt. ★This is a seatbelt, not the fix — ship
it **after** A.3 so a real credential problem still surfaces loudly instead of
being retried into silence.

**A.5 — Alert on it** *(30m)* → any `InvalidPasswordError` in a 5-minute window pages.

---

## Phase B — make a sync failure visible and recoverable

Today a crashed sync leaves the user with an agent that cannot see their tables
and a message telling them to "attach or refresh the lakehouse" — which reads as
their mistake. It was ours.

- **B.1** — `indexing.run.crash` must write `connection_indexings.error` and set
  `status='failed'`, not just log *(30m)*
- **B.2** — Distinguish *infrastructure* failure from *source* failure in that
  row. "We could not reach our own database" and "Fabric refused the credential"
  need different messages and different retries *(45m)*
- **B.3** — Surface the last failure in the connections UI, with its time *(1h)*
- **B.4** — Auto-retry an infrastructure-class failure (`next_retry_at` already
  exists on `connections`) *(45m)*
- **B.5** — Tests: a crashed sync leaves a readable row; an infra failure retries;
  a credential failure does not *(45m)*

---

## Phase C — Fabric workspace scoping (the actual ask)

**★Architecture first, or this lands in the wrong place.** A Fabric connection is
**one lakehouse/warehouse** — `MsFabricClient.__init__` takes a single
`server_hostname` + `database` (`ms_fabric_client.py:40-49`). There is no
workspace crawl in it at all. `ms_fabric_federated_client.py` overlays many such
connections and routes by workspace, because a SQL endpoint hostname *is* the
workspace. So "20 workspaces" = ~20+ connections, each indexed independently.

Power BI already has exactly the filter being asked for —
`workspaces`, comma-separated, `powerbi_client.py:200-212`,
documented in `connector_docs.py:225`. **Fabric has no equivalent.** That is the gap.

- **C.1** — Decide the unit of selection: per **connection**, or per **workspace**
  across connections? Per-connection is the honest model given the architecture
  and needs no new concept *(30m, decision)*
- **C.2** — Add a per-user selection of which connections take part in a sync.
  `connection_indexings.user_id` already exists; `connections` has `is_active`
  but that is org-wide, so this needs its own store *(2h)*
- **C.3** — Honour the selection in `ConnectionIndexingService.start()` *(1h)*
- **C.4** — Sync **only the changed/selected set** on a re-run rather than all
  *(1h)*
- **C.5** — UI: workspace/connection picker with a "sync selected" action *(3h, FE rebuild)*
- **C.6** — Add a `workspaces` scope field to the Power BI-style Fabric discovery
  path **if** C.1 chooses per-workspace *(2h, conditional)*
- **C.7** — Tests: selection is honoured; deselected connections are untouched;
  an empty selection syncs nothing rather than everything *(1h)*

★**The empty-selection case is the dangerous one.** "No filter" and "filter
matching nothing" must not collapse into the same branch, or deselecting
everything triggers a full 20-workspace crawl — the precise thing being fixed.

---

## Phase D — tell the user when it finishes

Foundations exist: `connection_indexings` already carries `phase`,
`current_item`, `progress_done`, `progress_total`, `events_json`, `stats_json`,
`user_id`. What is missing is an emit on completion.

- **D.1** — Emit a completion event (SSE, same path the report activity hub uses) *(1h)*
- **D.2** — In-app notification: "Fabric sync finished — 3 workspaces, 214
  tables, 4m12s", and on failure the reason from B.2 *(2h)*
- **D.3** — Live progress in the UI from the columns already being written *(2h)*
- **D.4** — Optional email/Slack for syncs over a threshold *(1h)*
- **D.5** — Tests: success notifies, failure notifies with the reason, a
  cancelled sync does not notify success *(45m)*

---

## Phase E — the per-user auto-resync agent

**★The largest piece, and the one most easily got wrong.** `connections` already
has `auto_reindex_enabled`, `reindex_interval_hours`, `next_retry_at`,
`last_reindex_error` — so a periodic re-index already exists. The new part is
*per-user* and *change-driven*.

- **E.1** — ⚠ Establish whether Fabric can tell us a lakehouse changed without a
  full crawl. If it cannot, polling 20 workspaces per user is exactly the cost
  being avoided, and the design must change *(2h, investigation — do this before
  any E.2+ work)*
- **E.2** — Cheap change detection: table count + max modified time per schema
  before a full crawl *(2h)*
- **E.3** — Per-user schedule keyed off actual usage, not a fixed tick — a user
  who has not opened a report in a week does not need an hourly crawl *(2h)*
- **E.4** — Coalesce: one user, many connections, one job; never 20 concurrent
  crawls for one person *(1h)*
- **E.5** — Backoff on a failing source, so a broken credential is not retried
  every hour forever *(1h)*
- **E.6** — Tests: unchanged source skips the crawl; changed source triggers it;
  two users on one connection do not crawl twice *(1h)*

---

## Phase F — the "attach or refresh the lakehouse" message

The agent's message in the incident screenshot is *correct behaviour* — it only
sees synced tables. But it reads as the user's mistake when the real cause was a
crashed sync.

- **F.1** — When a table is not found, check whether that connection's last sync
  failed, and say so instead *(1h)*
- **F.2** — Name which lakehouses *were* searched, so "not found" is a fact
  rather than a guess *(45m)*
- **F.3** — Test *(30m)*

---

## Order, and why

1. **A** — everything else is measured through a broken DB connection until this is fixed
2. **B** — cheap, and turns the next failure into information instead of a mystery
3. **C** — the actual request; safe once B makes failures visible
4. **D** — small, high perceived value, depends on C's job boundaries
5. **F** — small, needs B
6. **E** — largest, and E.1 may change its shape entirely

**Rough total:** A ≈ 2h + infra · B ≈ 4h · C ≈ 10h · D ≈ 7h · E ≈ 9h · F ≈ 2h.

## Open questions — these need your answer, not my guess

1. **Is there a pooler (PgBouncer/RDS Proxy) in front of prod Postgres?**
   Decides whether A is a 10-minute fix or a failover investigation.
2. **Per-workspace or per-connection selection** (C.1) — changes the data model.
3. **Was the prod DB password rotated recently?** Candidate 3 in A.2.
4. **Is prod running the same version as this repo?** The plan reads current
   code; if prod is older, line numbers and some behaviour will differ.
