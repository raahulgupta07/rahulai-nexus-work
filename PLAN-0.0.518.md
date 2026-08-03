# Plan — file handling fixes + upstream 0.0.511→0.0.518 port

Written 2026-08-03. Base `v0.0.510`, our tree `0.0.510.9`, git `f8fb8700` (clean, pushed).
Target `0.0.510.10` (file fixes) then `0.0.518.1` (upstream port).

**Read the confidence column before building anything.** Two claims in an earlier
draft of this plan were wrong because they were reasoned from constant *names*
instead of following the branch. Both are marked below. The rule this plan runs
on: **a fix must cite a measurement, not a symbol.**

---

## 0. State at time of writing

| thing | value |
|---|---|
| base (measured, not from VERSION) | `v0.0.510` — 1863 identical / 421 differing, peak |
| upstream latest | `v0.0.518` = `6939029859f6` — `origin/main` is 0 non-merge commits past it |
| upstream ahead by | 8 releases, 29 non-merge commits, 76 files, +7516/−257 |
| new Alembic migrations upstream | **none** — the whole port is schema-safe |
| fork suite baseline | **1905 passed / 0 failed** (`/src` runner) |
| full `tests/unit` baseline | 182 failed / 4604 passed / 56 skipped — **169 environmental, 13 real** |
| backup | `../_backup-20260803/` — **one level up, in `CityAgentWork/`, not in the repo** — 214M, verified, restore steps in `MANIFEST.txt` |
| rollback image | `cityagentinsights:0.0.510.7` (`c8a40c003bf7`) present on disk |

★ 0.0.511, 0.0.513, 0.0.514 shipped **untagged**. A tag-driven port skips three
releases silently. Always range by commit: `v0.0.510..origin/main`.

★ `CLAUDE.md`, `CLAUDE-SESSIONS.md`, `TEST-DEFECTS.md` and `.env` are **gitignored**
— the remote never protected them. `.env` holds `DASH_ENCRYPTION_KEY`; without it
every Fernet secret in the DB (SMTP, LDAP bind, SSO client secrets) is
undecryptable and the DB dump alone does not save you.

---

## 1. Evidence table — what is proven vs claimed

| # | claim | how established | confidence |
|---|---|---|---|
| E1 | `coder.py:532` compiles prose as Python | **ran the repro**, byte-identical `SyntaxError (<string>, line 1)` | **proven** |
| E2 | all 4 strip sites are codegen paths | resolved enclosing defs | **proven** |
| E3 | codegen runs on the **user-selected** model | `create_data.py:1803` `Coder(model=runtime_ctx.get("model"))` | **proven** |
| E4 | Gemini: budget-0 400s + `title` param stripped | read upstream diff + our identical source | **proven** |
| E5 | FORK-2 broke query timeouts | **9 tests failing** in the P0 baseline | **proven** |
| E6 | FORK-1 prints `0.0034` as `0.00` | **ran it** | **proven** |
| E7 | LibreOffice + pyarrow present in image | **ran in container** — `/usr/bin/soffice`, pyarrow 18.1.0 | **verified** |
| E8 | 17 extension registries disagree | transcribed verbatim, set arithmetic | **high** |
| E9 | ~~`.doc/.rtf/.odt/.odp/.ppt` unsupported~~ | **WRONG** — `read_file.py:664` → `render_file_images` → `CONVERTIBLE_EXTS` → LibreOffice | **retracted** |
| E10 | `read_file` gated off agent-attached files | read `agent_v2.py:784` only | **UNCONFIRMED** |
| E11 | `.parquet/.eml/.msg/.zip` unreadable | grep only | **claim** |
| E12 | Auto-routing can downgrade codegen model | not checked | **unknown** |

E9 is kept visible on purpose. Do not re-add that work without a matrix cell.

---

## 2. The incident, fully traced

`summaries data for me`, one `.docx` in the folder, 70s, 2 issues, no answer.

1. Planner routed a document to a **codegen** tool.
2. `read_file` was not in its catalog → it hunted via `search_mcps`, which returned
   `(0)`. That zero is real: `_search_mcps_query.filter_tools_by_query` ends every
   branch `return matched or tools`, so an empty result means **no MCP tools existed**.
   A whole planner round-trip spent on a guaranteed-empty question.
3. The coder was handed contradictory orders:
   - `coder.py:524` — "produce ONLY the Python function code … no markdown, no text, no anything"
   - `coder.py:179` — "`.docx` → NOT readable from generated code at all … the planner must use `read_file`"
   It said so in the trace: *"However, the task requires a Python function."*
4. Being a cheap model, it **resolved the contradiction out loud** — three paragraphs,
   then a ```python fence. A strong model would have obeyed and emitted bare code,
   leaving this bug latent.
5. `coder.py:532` strips a fence only at position 0 (`^` after `.strip()`), so the
   prose survived and reached `exec()`.

→ `invalid syntax (<string>, line 1)`, line 1 being *"Looking at this request, I need to:"*.

**Trigger = cheap model × contradictory instruction. Cause = ours, either way.**
The tell: the next line, `trim_after_final_df_return`, carefully removes everything
*after* the function. Nothing removes anything *before* it.

---

## 3. Phase V — measure before building *(~1h, zero risk, no product file touched)*

Throwaway container off `cityagentinsights:0.0.510.9`, `/src` read-only. **Never `dash-app`** — that is the live app.

- **V1** Fixture corpus: ~25 real files (`pdf docx doc pptx ppt odt odp rtf csv tsv xlsx xls parquet json ndjson txt md html xml yaml log png jpg tiff bmp webp eml msg zip`) + 4 adversarial: image-only PDF, 1-line docx (trips `MIN_USABLE_DOC_CHARS=16`), garbled-font PDF, corrupt docx. **DONE** = each opens in its native reader.
- **V2** Probe the real dispatch per fixture: `extract_document_text`, `doc_text_is_usable`, `render_file_images`, `_source_files` reader lookup, `read_file` dispatch. Classify **content / honest refusal / silent garbage / crash**. Only the last two are bugs.
- **V3** For every non-content outcome, record the deciding `file:line`. **This is the step whose omission produced E9.**
- **V4** Sweep 4 conditions — the vision fallback needs **both** `model.supports_vision` (`read_file.py:586`) and `allow_llm_see_data` (`:602-605`). A format that works only with both is broken for any PII-protected org or text-only model.
- **V5** Settle E10: query the user's actual report, or reproduce (agent-attached doc → read back resolved capability set).
- **V6** Write `FILE-SUPPORT-MATRIX.md` + `FILE-DEFECTS.md` into `_backup-20260803/`. Rewrite §4 scope from it. **Any fix that cannot cite a cell is dropped.**

Also in V: check E12 — can `model_router` route codegen down? If yes, this fires on accounts that never chose a cheap model.

---

## 4. Phase F — file handling *(target `0.0.510.10`)*

**F1 — code extraction.** *(proven, independent, do first)*
One shared helper replacing the regex at `coder.py:532, 964, 1110, 1245`: take the
**last** fenced block anywhere; no fence → slice from first `def`/`import`;
`compile()` before returning; `SyntaxError` → **codegen retry**, not a user-facing
failure.

**F1-T — guards.** Prose-before-fence (the exact repro); fence at position 0 still
works; no fence untouched; multiple fences picks the code; SyntaxError → retry;
**AST/grep guard that all 4 sites use the helper** so the regex cannot return in a
5th place. ★ Each must be run against unfixed code first and **fail**.

**F2 — scope from V6.** Candidates, each to confirm or drop:
- codegen block-list gap: `.doc .rtf .odt .odp .ppt .bmp .tiff .tif` absent from `_NOT_LOADABLE` (`_source_files.py:40`, `step_files.py:62`) and `_CODEGEN_UNREADABLE_EXTS` (`coder.py:189`) → codegen still tries `pd.read_csv` on them
- E10 capability gate (blocked on V5)
- missing readers: `.parquet` (pyarrow already present — no new dep), `.eml .msg .zip .html .xml`
- connector `TEXT_EXTS` drift across s3 / google_drive / graph_drive / network_dir — same file readable from one source, opaque from another
- single registry with **default-deny** for unknown extensions (today unknown = "let codegen try", which is the inversion that produces silent garbage)
- `search_mcps` gated behind an `mcp_tools` capability, same mechanism `read_file` already uses
- **contradiction guard**: when the coder is handed a file its own rules declare unreadable, it must refuse back to the planner instead of generating. Right now it has no way to say no — which is what put the cheap model in that position.

**F2-T** — V1's corpus becomes the committed test corpus. Table-driven registry-agreement test (fails the moment a format is added to one place and not the others) + per-format content-or-honest-refusal test.

**F3 — duplicate clarifying question.** *(observed, not root-caused)*
The incident rendered the **same clarify block twice** — near-identical wording,
two separate Submit buttons, one turn. Candidates: a double emit from the clarify
tool, a frontend re-render on the SSE replay, or a genuine second planner step.
Diagnose from `tool_executions` for that run (how many `clarify` rows?) before
touching anything: if it is one row, the bug is in the frontend/SSE path; if two,
it is in the loop. **DONE** = cause identified and cited. May split to its own
phase rather than hold up F1/F2.

---

## 5. Phases P — upstream port *(target `0.0.518.1`)*

Independent of Phase F — different files entirely.

| P | scope | port difficulty |
|---|---|---|
| **P1** | **Gemini** — budget floor 128; stop stripping keywords inside `properties`/`$defs`/`definitions`/`patternProperties`; forward-pass `tool_use_id→name`; per-request id prefix; `thought_signature` capture+replay; trailing `STOP` no longer downgrades tool-use | clean |
| **P2** | FORK-1 float printing (ours) | trivial |
| **P3** | upstream `SwallowedQueryError` + FORK-2 timeout hierarchy — same function, port together | **merge** (`code_execution.py`, 550 lines diverged) |
| **P4** | PDF completeness, pptx-exports-as-deck, images-in-PDF | clean |
| **P5** | per-user OAuth connectors 401 on system-context indexing (4 paths) | mixed |
| **P6** | instruction edits stacking + pending-edit visibility + review-bar i18n | clean |
| **P7** | column metadata + PK/FK reach the prompt; stop claiming RLS is detectable | mixed |
| **P8** | evidence retention; `read_query` code in the observation | **merge** (`agent_v2.py`, 823 lines diverged) |
| **P9** | Power BI without admin scope; Snowflake semantic view; PostHog FAST + custom queries | **clean merge** (measured — see G3), semantics unverified |
| **P10** | blank-report agent picker; clickable overview counts | merge, 4 FE files |
| **P11** | FORK-3 prose gate — `_looks_like_component_code` passes any text containing `return` or `<`; 3 of 5 realistic prose replies pass | trivial |
| **P12** | repo hygiene — 1042 untracked `.bak*`, 20G tree | none |
| **P13** | release | — |

**Sequencing.** P1/P2/P3 independent, can run together. P8 after P3 (both in the agent loop). P9 and P10 independent of everything after P0.

---

## 5·0. The 8 missing versions *(enumerated 2026-08-03)*

We are at upstream `0.0.510`. Everything from `0.0.511` to `0.0.518` is missing —
8 releases, 29 non-merge commits, no migrations.

★ **Only 5 of the 8 are tagged.** `0.0.511`, `0.0.513` and `0.0.514` exist upstream
only as `VERSION` bumps on a branch. And tag boundaries do **not** match the
CHANGELOG numbering — `v0.0.512` carries 511+512, `v0.0.515` carries 511–515. So
neither tags nor `VERSION` alone give a correct grouping. **Range by commit:
`v0.0.510..origin/main`.**

| version | tagged | what it is | our phase |
|---|---|---|---|
| **0.0.511** | ✗ | agent overview counts clickable (jump to section, like the explorer tree) | P10 |
| **0.0.512** | ✓ | one "Pending review" header instead of two stacked bars; review bar translated (was English-only) | P6 |
| **0.0.513** | ✗ | sequential instruction edits **stack instead of overwriting** — several refinements in one session no longer collapse to the last; won't re-suggest something already pending | P6 |
| **0.0.514** | ✗ | custom queries on **PostHog** — HogQL materialized on a schedule, 5–600× faster, no rate limit, past the 50k-row ceiling | P9 |
| **0.0.515** | ✓ | blank report **picks its agents** — most-recently-used first, searchable, starter questions follow | P10 |
| **0.0.516** | ✓ | exported/emailed **PDFs stop losing content** (wide tables, scrolled rows, chart edges); a slides report exports as the **actual deck** not raw codegen; images in a dashboard appear in its PDF | P4 |
| **0.0.517** | ✓ | **no CHANGELOG entry upstream.** Power BI without admin scope (relationships, model types, measures); Snowflake semantic view logical tables + joins; column metadata + PK/FK into the prompt, stop claiming RLS is detectable; per-user OAuth connectors stop 401-ing system-context indexing; `read_query` code in the planner observation; `SwallowedQueryError`; evidence kept in context long enough to combine | P3, P5, P7, P8, P9 |
| **0.0.518** | ✓ | **no CHANGELOG entry upstream.** Gemini: params named like JSON Schema keywords, thinking budget 0, recycled call ids, missing `thought_signature`; LLM integration tests for realistic schemas + multi-turn replay | P1 |

★ 0.0.517 and 0.0.518 are **user-invisible upstream** — no release notes were
written for either. They are also the two that matter most to us: 517 carries the
`SwallowedQueryError` fix that collides with FORK-2, and 518 is the whole Gemini
correctness set. A changelog-driven port would skip both.

★ For the record, `0.0.500` **was** bumped upstream (`1fc86166`) and has a
CHANGELOG entry (Sign in with Snowflake) — it was just never tagged. The older
note in `CLAUDE.md` saying "500 was never published upstream" is wrong on that
detail; it was never *tagged*.

---

## 5a. Gap audit — measured 2026-08-03 *(supersedes several ratings above)*

Method: three-way file comparison (upstream `v0.0.510` base × our tree × upstream
`origin/main`), plus a real `git merge-file` simulation of the whole port, plus a
stale-file sweep against every upstream tag back to `v0.0.497`.

### G1 — the base is clean *(good news, and it was not assumed)*
3096 files tracked at `v0.0.510`: **2619 identical**, 444 ours-modified,
**0 stale** (no file is silently stuck at an older upstream release), 33 absent.
Alembic: **1 head** (`stepfiles01`), **0 dangling `down_revision`** refs across
220 revisions. Upstream adds no migrations in `510..518`. The port is schema-safe
and there is no hidden un-ported backlog.

### G2 — 7 upstream test files were never ported, and the code they cover IS here
Absent from our tree, added upstream at `v0.0.493`–`v0.0.510`, never brought over:
`test_chart_spec.py`, `test_data_preview_cell_cap.py`,
`test_powerbi_item_level_access.py`, `test_instruction_activity.py`,
`rbac/test_instruction_pending_carryover.py`, `test_scheduled_refresh_archive_guard.py`,
`test_mcp_forwarding_eu_live.py`. Verified the targets exist here:
`app/ai/tools/chart_spec.py`, `app/ai/context/data_preview.py`, `powerbi_client.py`,
`app/models/build_content.py`. **We ship the features unguarded.** Also missing:
`SECURITY.md`, `scripts/version-auto-update/version-auto-update.sh`.
→ new phase **T-4**: port these 7 and record which ones fail on our tree first.

### G3 — the port is far easier than §5 claims
`git merge-file` over every changed file: **5 files, 6 conflict hunks total.**
`powerbi_client.py` (upstream +414 / ours +557) merges **cleanly** — P9's "hard"
rating was inferred from churn, not measured. Re-rate: **P9 = clean-merge, but
semantically unverified.** The real risk in this port is semantic, not textual —
git will produce a green merge that is wrong in the two places below.

| file | conflicts | what it is |
|---|---|---|
| `frontend/components/KnowledgeExplorer.vue` | 2 | our sync-strip / drift-notice / learn-bar blocks sit immediately above the counts block upstream is making clickable. Mechanical. |
| `backend/app/ai/context/builders/schema_context_builder.py` | 1 | **see G4 — do not take either side whole** |
| `backend/app/services/data_source_service.py` | 1 | **see G5** |
| `CHANGELOG.md`, `VERSION` | 1 each | expected |

### G4 — ★taking upstream's `schema_context_builder` hunk whole breaks Power BI per-user agents
Upstream (`e218facb`, P7) rewrites the overlay column loop to carry canonical
dtype + metadata into the prompt. Our fork changed **one line inside the same
hunk**: `canonical_is_active` defaults to `True` when there is no canonical
`DataSourceTable`, because a pure user-scoped connector (`powerbi_user`) has no
canonical catalog — the overlay *is* the catalog. Upstream's side still reads
`else False`. Accept upstream's version and **every `powerbi_user` agent reports
0 tables**. Correct resolution: upstream's column-building body + our
`canonical_is_active` default. One line, easy to lose.

### G5 — `data_source_service` has three near-identical list paths; upstream patches one
Upstream `d1fb519d` adds `last_used_by_ds = await self._last_used_at_by_ds(...)`
next to `cached_by_ds`. Our tree calls `_cached_table_names_by_ds` at **three**
sites (1903, 2119, 2288). The conflict is only at one; the merge cannot tell you
which list feeds the blank-report agent picker. Put it in the one P10 reads, and
check the other two do not need it.

### G6 — RESOLVED 2026-08-03. ★Three claims below were wrong; corrected in place.

**Decision (2.A.1): keep the design, fix the tests.** Done — 9 upstream tests
rewritten to the real contract, stale class docstring corrected, and a new fork
guard added for the wiring nobody was testing
(`test_configured_query_budget_is_wired_through.py`, 13 tests).

**What I got wrong, and what the code actually does:**

| earlier claim | measured truth |
|---|---|
| "`query_hard_timeout_seconds` is a new org config **set nowhere**" | **Wrong.** It is in the settings catalog at `organization_settings_schema.py:458` — default 900, `editable=True`, with a written description. |
| "a connection **cannot lower** the hard limit, only the soft mark" | **Wrong.** It can. `max(soft, resolved)` only stops it going *below the progress mark*, which is deliberate and documented — a hard limit inside the progress mark would end every query before one was ever reported as slow. |
| "a connection set to 60s now runs to 900s — **15×**" | The org default progress mark is **180**, not 60 (`:457`); 60 is only the last-resort constant. So 5×, not 15×. Real, less severe. |

The redesign was coherent all along: both settings renamed and described
(`query_timeout_seconds` → "Query progress mark (seconds)"), both editable, and
**already guarded** by `tests/unit/fork/test_slow_query_survives.py`. The only
thing genuinely left undone was updating the 9 upstream tests — plus one stale
class docstring that still claimed the soft value raised, 30 lines above code
that says otherwise.

★The lesson is the one this plan already runs on: every wrong claim above came
from reading a constant instead of the wiring around it. Same failure as the
retracted E9.

★**New for Phase 3:** `test_slow_query_survives.py::test_parking_is_per_wrapper_and_never_shared`
documents the per-wrapper scope as **deliberate, for a security reason** — on a
per-user-credentialed connection the same SQL run by two people can legitimately
return different rows, so a result keyed on SQL alone would serve one person's
data to another. FORK-4's fix must widen parking to the *run*, never further.

---

### G6 (original finding) — FORK-2 left 9 upstream tests red
All 9 failing timeout tests (`test_query_timeout.py` ×7, `test_query_cancellation.py`,
`test_query_concurrency.py`) are **upstream files, byte-identical to `v0.0.510` in
our tree**. We changed the code under them and left them red.

The behaviour change is deliberate and documented in the docstring at
`code_execution.py:219` — `query_timeout_seconds` stopped being the kill and became
a *progress mark*; the kill moved to `resolve_hard_timeout`, default
`DEFAULT_HARD_TIMEOUT_SECONDS = 900`. Consequences nobody signed off on:
- a connection configured `query_timeout_seconds: 60` now runs to **900s — 15×**;
- `resolve_hard_timeout` returns `max(soft, resolved)`, so a connection setting
  **cannot lower** the hard limit, only the soft mark;
- `query_hard_timeout_seconds` is a **new org config that is set nowhere**, so
  every existing org is on the 900s default.
→ Decide explicitly: adopt the new contract and **rewrite the 9 upstream tests to
it**, or restore the old kill. Either is fine. Nine red upstream tests is not.

### G7 — ★★FORK-4 (new): the orphan-parking mechanism is defeated on exactly the retry it exists for
`self._parked` lives on `QueryCapturingClientWrapper`, built inside
`wrap_clients_for_capture` at `code_execution.py:1203`, which runs **inside
`execute_code`**. A `ToolRunner` retry calls `tool.run_stream` again → a fresh
wrapper → an empty `_parked`. The retried query cannot find the thread attempt 1
parked, so it starts the second identical scan `_park_orphan` was written to prevent.

Compounding it: `_PROGRESS_TICK_SECONDS = 15` is used **only** to slice the thread
join (`:807`) — it emits **no `tool.progress` event**. So nothing resets
`ToolRunner`'s `idle_timeout_s=180`. Real behaviour of a long query in the agent
path: 180s of silence → `_stream_with_idle` raises → `retry_on` includes
`timeout_error` → attempt 2 relaunches it. The 900s hard budget is **unreachable
here**; 180s + a duplicate scan is the actual product behaviour.

### G8 — inherited upstream bug: the tool hard timeout is dead code
`tool_runner.py:107-112` creates `hard_timeout()` as a bare `asyncio.create_task`
and never awaits it. A `raise` inside an un-awaited task is stored, not propagated
— so `TimeoutPolicy.hard_timeout_s=300` never fires; it only risks an
"exception never retrieved" warning. Confirmed present in upstream `origin/main`
too. **Not ours** — report upstream, fix locally, keep separate from FORK-4.

### G9 — our only `tool_runner.py` change is a fix upstream does not have
The DEF-003 `_self_declared_failure` guard (a tool declaring `success: False` must
not have its real error replaced by a schema-validation message). Upstream `main`
is still unfixed. **A naive "take upstream" on this file silently deletes it.**

### G10 — verified compatible: upstream's `SwallowedQueryError` works on our wrapper
`e7aff0b9` keys off `error` in `captured_timings`. Our rewritten wrapper records
`"error"` on both failure paths (`:734` timeout, `:752` generic), so the new guard
fires correctly here. P3 is safe to port — checked, not assumed.

---

## 5b. Phase PERF — latency *(the "why did it take 70s" work)*

Measured from the incident: 70s total, 2 steps — `inspect_data` 35.3s, `search_mcps`
134ms returning nothing, failed CSV generation 10.5s, ~24s of planner LLM time.
★ Any re-measurement must be taken while `dash-app` is **idle** — the P0 baseline
suite had it at 90.6% CPU, which confounds every timing in that window.

- **PERF-1 — kill the guaranteed-empty round-trip.** Gate `search_mcps` behind an
  `mcp_tools` capability, the same mechanism `read_file` already uses in
  `registry.py`. Saves one full planner step on every agent with no MCP
  connections, which is most of them. Cheap, and already listed under F2.
- **PERF-2 — prompt caching.** Verify whether `anthropic_client.py` sets cache
  breakpoints on the static blocks. `ContextHub`'s static (schemas / instructions /
  resources) vs warm (messages / observations) split is *exactly* the right shape
  for it. If the breakpoints are absent this is the single largest latency and cost
  lever in the product. **Unverified — measure before claiming.**
- **PERF-3 — model routing.** `model_router.py` exists and `main_execution` already
  stamps `routed` / `baseline_model_id`. Check what a trivial turn routes to, and
  whether routing can downgrade the **codegen** model (E12) — that would make the
  cheap-model failure in §2 fire on accounts that never chose one.
- **PERF-4 — `inspect_data` 35.3s.** Split warehouse time from our own schema /
  sampling overhead. If it is ours, it is cacheable. Needs the trace.
- **PERF-5 — failed-codegen cost.** F1 + the upstream `SwallowedQueryError` (P3)
  remove a failed attempt plus its retry from the critical path.

**DONE** = a before/after breakdown of the same prompt on an idle container.

## 5c. Phase T — close the test-infrastructure hole

FORK-2 shipped because the inner loop cannot see it: `tests/unit/fork/` runs in
~7s and is what gets run per change, but the 9 failing query-timeout tests live in
`tests/unit/`, which takes 1h07 and is documented as an after-port step only.

- **T-1** Define a fast non-fork subset (query timeout / cancellation / concurrency
  at minimum) that joins the inner loop.
- **T-2** Triage the 4 remaining real baseline failures (§9.6) — fix or record as
  known-and-accepted, so "13 real" stops being an unexplained number.
- **T-3** Make the `/src` runner the default documented command everywhere, so the
  169-silently-erroring `dash-app` path stops being reachable by habit.
- **T-4** Port the 7 upstream test files from **G2** and record which fail on our
  tree before fixing anything. They guard code we already ship.

## 5d. Phase DOC — durability

- **DOC-1** Update `CLAUDE.md` from this work: the measured base method, the
  untagged-releases trap, the `/src`-vs-`dash-app` split, FORK-1/2/3, and the
  file-support matrix once V6 exists. Keep it near 200 lines — it is read in full
  every session and a long guide is followed less reliably.
- **DOC-2** Decide what protects the four gitignored files (`CLAUDE.md`,
  `CLAUDE-SESSIONS.md`, `TEST-DEFECTS.md`, `.env`). Today the answer is "one backup
  taken by hand on 2026-08-03". Options: track the three docs and keep only `.env`
  out, or a scheduled backup. **Losing `.env` makes every stored secret
  undecryptable** — this is not a documentation nicety.
- **DOC-3** Append the session to `CLAUDE-SESSIONS.md`, not to `CLAUDE.md`.

## 6. Testing

**Layer 1 — fork guards** (`backend/tests/unit/fork/`). The repo's durable mechanism.
★ Every new test runs against unfixed code first and **must fail**. A guard that never saw red proves nothing.
★ Split by **cost**, not feature — `tests/unit/fork/` has no DB schema; a schema-needing test there fails "no such table" and reads like a product bug.

**Layer 2 — format corpus.** One real file per format; content or honest refusal, never crash, never silent garbage.

**Layer 3 — regression gates.**
```bash
# fork suite — MUST be 1905 + new, 0 failed. /src runner, NEVER dash-app.
cd /Users/rahulgupta/Desktop/CityAI-Final-Project/CityAgentWork
docker run --rm -v "$PWD/bagofwords:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc cityagentinsights:0.0.510.9 \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest tests/unit/fork -q -p no:cacheprovider -p no:warnings'
```
Full `tests/unit` must match the baseline **by failure name**, not count — see
`_backup-20260803/BASELINE-FAILURES.txt`. A matching count with changed membership
is the failure mode that matters.

**Layer 4 — replay the incident.** Same agent, folder, docx, prompt. PASS = `read_file`
called, no codegen, no `search_mcps`, real summary, no "paste the text" clarify.
Repeat for `.doc`, `.rtf`, `.parquet`. Use the `sandbox-feedback-loop` skill —
and **not** while a test suite is pinning `dash-app` at 90% CPU.

---

## 7. Ship

1. `VERSION` → the target (`0.0.510.10`, later `0.0.518.1`). Base = upstream release ported; `.N` = our work. Partial ports go in the changelog **body**, never the number.
2. `sed` `DASH_IMAGE` in `.env` to the **new** tag **before** building — building over the live tag orphans the manifest and **destroys the rollback**.
3. `FE_CACHEBUST` only if a frontend file changed.
4. Verify content **inside the new image**, not the running one.
5. Fork suite green in `/src` → `up -d app`.
6. Replay Layer 4 against the deployed image.
7. CHANGELOG entry. Rollback stays the previous tag.

---

## 8. Landmines

- `dash-app` **silently errors 169 fork guards** (`REPO=parents[4]`→`/app`, no `/app/locales`, no frontend source). Never validate a release there.
- The `uid=999,gid=999` on the tmpfs mounts is load-bearing — without it every test errors at setup and looks like broken code.
- A fresh image has **no pytest**.
- The inner loop (`tests/unit/fork`) **cannot see** the 9 query-timeout failures — they live in `tests/unit/`. That hole is how FORK-2 shipped. Consider a fast non-fork subset.
- Use `<<'EOF'` (quoted) for every commit-message heredoc — an unquoted one executes backticks.
- Frontend is a static SPA: FE changes need a rebuild. Prove it by chunk filename/md5 between images, never by grepping the bundle for an identifier (minifiers rename locals).

---

## 9. Open questions

1. **Report URL** from the incident — needed to settle E10 before F2 is built.
2. Can `model_router` downgrade the codegen model? (E12)
3. Is prompt caching configured on the static context blocks? `ContextHub`'s static/warm split is exactly the right shape for it; unverified.
4. `inspect_data` took 35.3s in the incident — warehouse time or our own overhead? Needs the trace.
5. `.msg` (Outlook) may need a dependency — flag rather than add silently.
6. Four of the 13 real baseline failures are untriaged: `test_artifact_key_roundtrips_through_fernet`, `test_real_org_settings_defaults_are_adaptive`, `test_resource_permissions_only_data_source_in_mvp` (asserts `{data_source, connection}`; we added `project` — likely a stale test, not a code bug), `test_data_model_to_code_prompt_carries_time_filter_rules`.

---

## 10. EXECUTION PLAN — phase by phase, with subtasks

Three releases, twelve phases. **Every phase needs approval before it runs**
(`CLAUDE.md`). Every phase ends with the same gate unless stated otherwise:

> **GATE** — fork suite in the **`/src` runner** (never `dash-app`): expect
> `1905 + new, 0 failed`. Plus: the named DONE condition of each subtask.

Estimates are working time, not wall clock.

---

### PHASE 0 — validation restored, baselines captured ✅ DONE
Fork suite 1905/0. Full suite 182 failed = 169 environmental + 13 real, listed by
name in `_backup-20260803/BASELINE-FAILURES.txt`. Backup 214M verified.
Base measured at `v0.0.510`: 0 stale files, 1 alembic head, 0 dangling revisions.

---

## RELEASE 1 — `0.0.510.10` "proven bugs"
*Nothing here needs a measurement we do not already have. No upstream code moves.*

### PHASE 1 — generated-code extraction *(~1.5h, proven, independent)*
The incident's direct cause. `coder.py:532` anchors its fence-strip at `^` after
`.strip()`, so prose before the fence reaches `exec()`.

| # | subtask | DONE when |
|---|---|---|
| 1.1 | Write `extract_generated_code()` in one place. Rules: take the **last** fenced block anywhere; no fence → slice from the first `def`/`import`; `compile()` before returning. | function exists, unit-callable |
| 1.2 | Wire all four sites: `coder.py:532, 964, 1110, 1245` (`data_model_to_code`, `generate_code`, `generate_inspection_code`, `generate_transform_code`). | zero `re.sub(r'^\s*\`\`\`` left in the file |
| 1.3 | `SyntaxError` → **codegen retry**, not a user-facing failure. | a bad extraction consumes a retry, does not surface |
| 1.4 | Guard tests, each **run against unfixed code first and made to fail**: prose-before-fence (the exact repro string `Looking at this request, I need to:`); fence at position 0 still works; no fence untouched; multiple fences picks code; SyntaxError → retry. | 5 tests, all seen red then green |
| 1.5 | AST/grep guard that all four sites call the helper. | a 5th hand-rolled strip fails the suite |

★ Do **not** also "fix" the contradictory prompt in this phase — that is 4.6.
Keep the extraction fix provable on its own.

### PHASE 2 — the timeout contract *(~2h, needs one decision from you)*
**Blocking question first.** All 9 red timeout tests are upstream files
**byte-identical to `v0.0.510` in our tree**. FORK-2 changed the code under them.
Live effect: a connection set to `60s` runs to **900s**.

| # | subtask | DONE when |
|---|---|---|
| 2.1 | **DECIDE** (yours): (a) keep the soft-mark/hard-kill design and rewrite the 9 upstream tests to the new contract, or (b) restore `query_timeout_seconds` as the kill and keep the hard limit as a ceiling only. | decision recorded in this file |
| 2.2 | If (a): make `resolve_hard_timeout` respect a connection value **downward** — today `max(soft, resolved)` means a connection can never tighten the kill. | a connection can lower the hard limit |
| 2.3 | If (a): pick a real default. 900s is 15× the old 60s and `query_hard_timeout_seconds` is set nowhere, so every org silently has it. | default agreed and documented |
| 2.4 | Rewrite/restore the 9 tests (`test_query_timeout.py` ×7, `test_query_cancellation.py`, `test_query_concurrency.py`). | 9 green, and they still assert something |
| 2.5 | Add one fork guard that the configured timeout is honoured end-to-end, so this cannot regress invisibly again. | new guard red on today's code |

### PHASE 3 — retry duplicates the warehouse scan *(~2h)*
FORK-4 + the inherited upstream bug next to it.

| # | subtask | DONE when |
|---|---|---|
| 3.1 | Hoist parked-query state above the retry boundary. `self._parked` lives on the wrapper built in `wrap_clients_for_capture` (`code_execution.py:1203`) **inside** `execute_code`, so a `ToolRunner` retry gets an empty dict and relaunches the identical scan `_park_orphan` exists to prevent. | attempt 2 of the same SQL attaches to attempt 1's thread |
| 3.2 | Emit a real progress heartbeat. `_PROGRESS_TICK_SECONDS = 15` only slices the thread join (`:807`) — no `tool.progress` event, so nothing resets `ToolRunner`'s `idle_timeout_s=180`. | a 5-minute query no longer trips the idle timer |
| 3.3 | **G8** — fix the dead tool hard timeout: `tool_runner.py:107-112` creates `hard_timeout()` as a bare `create_task` and never awaits it, so a `raise` inside is stored, not propagated. `hard_timeout_s=300` has never fired. **Inherited from upstream** — keep it a separate commit and report it upstream. | hard timeout actually fires in a test |
| 3.4 | Guard test: one slow query, one retry, **one** scan at the source. | red before 3.1 |

### PHASE 4 — the remaining proven fork bugs *(~1.5h)*

| # | subtask | DONE when |
|---|---|---|
| 4.1 | **FORK-1** — `f"{v:,.2f}"` prints `0.0034` as `0.00`. Use a format that keeps small magnitudes. | `0.0034` survives |
| 4.2 | Fix the guard that let it through: `test_small_numbers_stay_readable` only checks `0.25/0.5/0.125`. Add sub-`0.01` cases. | test red on today's formatter |
| 4.3 | **FORK-3** — `_looks_like_component_code` passes any text containing `return` or `<`; 3 of 5 realistic prose samples pass. Tighten to a real parse. | the 5 samples classify correctly |
| 4.4 | **F3** — duplicate clarifying question. Diagnose **before** touching code: count `clarify` rows in `tool_executions` for that run. One row → frontend/SSE replay. Two → the planner loop. | cause identified and cited |
| 4.5 | Fix F3 at whichever layer 4.4 names. | one block, one Submit |
| 4.6 | Resolve the coder's contradictory orders: `coder.py:524` says "ONLY the function, no text", `coder.py:179` says ".docx is NOT readable, the planner must use `read_file`". Give the coder a way to **refuse back to the planner**. | a doc handed to codegen returns a refusal, not generated code |

### PHASE 5 — ship `0.0.510.10` *(~1h)*
| # | subtask | DONE when |
|---|---|---|
| 5.1 | `VERSION` → `0.0.510.10`; CHANGELOG entry (partial scope goes in the **body**, never the number). | — |
| 5.2 | ★`sed` `DASH_IMAGE` in `.env` to the **new** tag **before** building. Building over the live tag orphans the manifest and **destroys the rollback**. | `.env` points at `0.0.510.10` |
| 5.3 | Build (`FE_CACHEBUST` only if a frontend file changed); verify content **inside the new image**. | version + marker confirmed in-image |
| 5.4 | Fork suite green in `/src`; full `tests/unit` matched **by failure name** against `BASELINE-FAILURES.txt`, not by count. | membership unchanged except the 9 fixed |
| 5.5 | Deploy, then replay the incident on an **idle** container (the P0 suite had `dash-app` at 90.6% — that confounds every timing). | real summary, no codegen, no red box |

---

## RELEASE 2 — `0.0.510.11` "any file, honestly"
*Everything here is gated on measurement. No fix ships without a matrix cell.*

### PHASE 6 — measure file support *(~1h, zero risk, no product file touched)*
Throwaway container off the current image, `/src` read-only. **Never `dash-app`.**

| # | subtask | DONE when |
|---|---|---|
| 6.1 | Fixture corpus, ~25 real files: `pdf docx doc pptx ppt odt odp rtf csv tsv xlsx xls parquet json ndjson txt md html xml yaml log png jpg tiff bmp webp eml msg zip` + 4 adversarial: image-only PDF, 1-line docx (trips `MIN_USABLE_DOC_CHARS=16`), garbled-font PDF, corrupt docx. | each opens in its native reader |
| 6.2 | Probe the **real** dispatch per fixture: `extract_document_text`, `doc_text_is_usable`, `render_file_images`, `_source_files` reader lookup, `read_file` dispatch. Classify **content / honest refusal / silent garbage / crash**. | 29 rows classified |
| 6.3 | For every non-content outcome record the deciding `file:line`. ★**Skipping this step is what produced the retracted E9.** | every non-content cell cites a line |
| 6.4 | Sweep 4 conditions — the vision fallback needs **both** `model.supports_vision` (`read_file.py:586`) **and** `allow_llm_see_data` (`:602`). A format that works only with both is broken for any PII-protected org or text-only model. | 4-way table |
| 6.5 | Settle **E10**: is `read_file` gated off agent-attached files? Read `agent_v2.py:784` in a live run, or reproduce (attach a doc, read back the resolved capability set). **Needs the incident report URL, or costs an hour to reproduce.** | confirmed or refuted |
| 6.6 | Check **E12**: can `model_router` route **codegen** down? If yes, the cheap-model failure fires on accounts that never chose one. | answered |
| 6.7 | Write `FILE-SUPPORT-MATRIX.md` + `FILE-DEFECTS.md`. **Rewrite Phase 7's scope from it. Any fix that cannot cite a cell is dropped.** | both files exist |

### PHASE 7 — file fixes *(scope set by 6.7 — the list below is candidates only)*

| # | candidate subtask | drop unless |
|---|---|---|
| 7.1 | Codegen block-list gap: `.doc .rtf .odt .odp .ppt .bmp .tiff .tif` are in **neither** `_NOT_LOADABLE` (`_source_files.py:40`, `step_files.py:62`) **nor** `_CODEGEN_UNREADABLE_EXTS` (`coder.py:189`) → codegen still tries `pd.read_csv` on them. | 6.2 shows silent garbage |
| 7.2 | Single extension registry with **default-deny**. Today unknown = "let codegen try", which is the inversion that produces garbage. | 6.7 confirms the 17-registry disagreement |
| 7.3 | Missing readers: `.parquet` (pyarrow 18.1.0 already in the image — **no new dep**), `.eml .msg .zip .html .xml`. ★`.msg` may need a dependency — flag, never add silently. | 6.2 shows crash or garbage |
| 7.4 | E10 capability gate. | 6.5 confirms it |
| 7.5 | Connector `TEXT_EXTS` drift across s3 / google_drive / graph_drive / network_dir — the same file readable from one source, opaque from another. | 6.2 shows a per-source difference |
| 7.6 | **PERF-1** — gate `search_mcps` behind an `mcp_tools` capability, the same mechanism `read_file` already uses. Saves a full planner round-trip on every agent with no MCP connection. Same file, same mechanism as 7.2 — ride it along. | always (cheap, measured 134ms + a planner step) |
| 7.7 | Registry-agreement test, table-driven: fails the moment a format is added to one registry and not the others. | always |
| 7.8 | The 6.1 corpus becomes the **committed** test corpus: per-format content-or-honest-refusal. Never crash, never silent garbage. | always |

### PHASE 8 — ship `0.0.510.11`
Same subtasks as Phase 5, plus: replay the incident for `.doc`, `.rtf`, `.parquet`
as well as `.docx`.

---

## RELEASE 3 — `0.0.518.1` "the upstream port"
*8 versions, 29 commits, **no migrations**. Measured: 5 files / 6 conflict hunks.*

### PHASE 9 — port prep *(~1h, do not skip)*
| # | subtask | DONE when |
|---|---|---|
| 9.1 | Branch. Range by **commit**, never by tag — `0.0.511/513/514` are untagged and tag boundaries do not match changelog numbering. | `v0.0.510..origin/main` |
| 9.2 | Re-run the merge simulation on the post-Release-2 tree; the conflict set will have moved. | fresh conflict list |
| 9.3 | ★**G9** — `tool_runner.py`: our only change is the DEF-003 `_self_declared_failure` guard, which upstream `main` still lacks. A naive take-upstream **deletes it**. | guard present after merge |
| 9.4 | ★**G4** — `schema_context_builder.py`: take upstream's column-building body **plus** our `canonical_is_active` default-`True`. Upstream's side reads `else False`; accepting it whole makes every `powerbi_user` agent report **0 tables**. | powerbi_user still lists tables |
| 9.5 | ★**G5** — `data_source_service.py`: upstream adds `last_used_by_ds` next to `cached_by_ds`, but we call `_cached_table_names_by_ds` at **three** sites (1903, 2119, 2288). Put it in the one the blank-report picker reads; check the other two. | picker orders by last used |

### PHASE 10 — the port, in dependency order
Each row is a subtask. All are independent unless a gate is named.

| # | version | subtask | difficulty |
|---|---|---|---|
| 10.1 | **518** | **Gemini** — budget floor 128; stop stripping JSON-Schema-keyword params inside `properties`/`$defs`/`definitions`/`patternProperties`; forward-pass `tool_use_id→name`; per-request id prefix; `thought_signature` capture + replay; trailing `STOP` no longer downgrades tool-use. File is **byte-identical** to upstream base. | clean |
| 10.2 | **518** | Port the LLM integration tests that come with it (`test_google_tool_schema.py`, `test_google_message_translation.py`, `llm_clients.py`, `bearer_gated_mcp_server.py`). | clean |
| 10.3 | **517** | `SwallowedQueryError` — **verified compatible** with our rewritten wrapper (it keys off `error` in `captured_timings`; we record it at `:734` and `:752`). Merge with the Phase 2 outcome — same function. | merge, gated on Phase 2 |
| 10.4 | **517** | Evidence retention + `read_query` code in the planner observation. | merge (`agent_v2.py`) — after 10.3 |
| 10.5 | **517** | Column metadata + PK/FK reach the prompt; stop claiming RLS is detectable. **Carries G4.** | mixed |
| 10.6 | **517** | Per-user OAuth connectors stop 401-ing system-context indexing (4 paths). | mixed |
| 10.7 | **517** | Power BI without admin scope (relationships, model types, measures) + Snowflake semantic view. Measured: **merges clean** despite +414 vs our +557 — but semantics unverified, so test it. | clean merge, verify |
| 10.8 | **516** | Exported PDFs stop losing content; slides export as the real deck; images in the PDF. Our file is **identical to upstream base** → straight take. | clean |
| 10.9 | **514** | Custom queries on PostHog (`posthog_source.py` is a new file — pure add). | clean |
| 10.10 | **513/512** | Instruction edits stack instead of overwriting; one "Pending review" header; review bar i18n. | clean |
| 10.11 | **515/511** | Blank-report agent picker; clickable overview counts. **Carries G5.** 4 FE files. | merge |
| 10.12 | — | Repo hygiene: 1042 untracked `.bak*`, 20G tree. | none |

### PHASE 11 — tests and durability *(after the port is stable; blocks no release)*
| # | subtask | DONE when |
|---|---|---|
| 11.1 | **T-4** — port the 7 upstream test files we never brought over: `test_chart_spec`, `test_data_preview_cell_cap`, `test_powerbi_item_level_access`, `test_instruction_activity`, `rbac/test_instruction_pending_carryover`, `test_scheduled_refresh_archive_guard`, `test_mcp_forwarding_eu_live`. The code they guard **is already here**. Record which fail **before** fixing. | 7 files in, results recorded |
| 11.2 | **T-1** — define a fast non-fork subset (query timeout / cancellation / concurrency at minimum) that joins the inner loop. **This hole is how FORK-2 shipped.** | subset runs in the inner loop |
| 11.3 | **T-2** — triage the 4 untriaged baseline failures so "13 real" stops being an unexplained number. `test_resource_permissions_only_data_source_in_mvp` asserts `{data_source, connection}` and we added `project` — likely a stale test, not a code bug. | each fixed or recorded as accepted |
| 11.4 | **T-3** — make the `/src` runner the default documented command everywhere, so the 169-silently-erroring `dash-app` path stops being reachable by habit. | `CLAUDE.md` updated |
| 11.5 | Restore `SECURITY.md` and `scripts/version-auto-update/version-auto-update.sh`. | present |

### PHASE 12 — latency *(independent of every release)*
★ Every measurement on an **idle** `dash-app`.
Incident breakdown: 70s total — `inspect_data` **35.3s**, `search_mcps` 134ms
returning nothing, failed codegen 10.5s, ~24s planner.

| # | subtask | DONE when |
|---|---|---|
| 12.1 | **PERF-2 — prompt caching.** Verify whether `anthropic_client.py` sets cache breakpoints on the static blocks. `ContextHub`'s static (schemas/instructions/resources) vs warm (messages/observations) split is exactly the right shape. **If absent, this is the single largest latency and cost lever in the product.** Unverified — measure before claiming. | answered with a number |
| 12.2 | **PERF-4 — `inspect_data` 35.3s.** Split warehouse time from our own schema/sampling overhead. If it is ours, it is cacheable. Needs the trace. | breakdown produced |
| 12.3 | **PERF-3 — model routing.** What does a trivial turn route to? (E12 answers half of this in 6.6.) | answered |
| 12.4 | Before/after breakdown of the same prompt. PERF-1 (7.6), Phase 1 and 10.3 each remove work from the critical path. | one table |

### PHASE 13 — release `0.0.518.1` + documentation
| # | subtask |
|---|---|
| 13.1 | Ship subtasks as Phase 5. |
| 13.2 | **DOC-1** — update `CLAUDE.md`: the measured-base method, the untagged-release trap, `/src` vs `dash-app`, FORK-1/2/3/4, the file-support matrix. Keep near 200 lines — it is read in full every session. |
| 13.3 | ★**DOC-2** — decide what protects the four gitignored files (`CLAUDE.md`, `CLAUDE-SESSIONS.md`, `TEST-DEFECTS.md`, `.env`). Today: one hand-taken backup. **Losing `.env` makes every Fernet secret in the DB undecryptable** — SMTP, LDAP bind, SSO client secrets — and the DB dump alone does not save you. Not a documentation nicety. |
| 13.4 | **DOC-3** — append the session to `CLAUDE-SESSIONS.md`, not to `CLAUDE.md`. |

---

## 11. Phase index

| phase | what | release | gate | est |
|---|---|---|---|---|
| 0 | baselines | — | ✅ done | — |
| 1 | code extraction | 510.10 | none — **start here** | 1.5h |
| 2 | timeout contract | 510.10 | **your decision (2.1)** | 2h |
| 3 | retry duplicate scan | 510.10 | none | 2h |
| 4 | FORK-1/3, F3, coder contradiction | 510.10 | 4.4 diagnosis before 4.5 | 1.5h |
| 5 | ship `0.0.510.10` | 510.10 | phases 1–4 | 1h |
| 6 | measure file support | 510.11 | none (6.5 wants the report URL) | 1h |
| 7 | file fixes | 510.11 | **6.7** | 3h |
| 8 | ship `0.0.510.11` | 510.11 | phase 7 | 1h |
| 9 | port prep + the 3 wrong merges | 518.1 | after release 2 | 1h |
| 10 | the port, 12 subtasks | 518.1 | 10.4 after 10.3; 10.3 after phase 2 | 6h |
| 11 | tests + durability | — | after port | 2h |
| 12 | latency | — | idle container | 3h |
| 13 | release + docs | 518.1 | last | 1.5h |

**Critical path to a shipped fix: phases 1 → 5.** Phase 6 can run alongside 1–4;
it touches no product file. Phase 2 is the only phase blocked on you.
