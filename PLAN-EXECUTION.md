# Execution plan — every phase, sub-phase and subtask

Companion to `PLAN-0.0.518.md`. That file holds the **evidence** (what is proven,
what is claimed, how each was established). This file holds the **work**.

Written 2026-08-03. Base `v0.0.510`, tree `0.0.510.9`, git `f8fb8700` (clean, pushed).
Three releases: `0.0.510.10` → `0.0.510.11` → `0.0.518.1`.

---

## How to read this file

- **Phase** = an approval unit. `CLAUDE.md` requires the user's approval before each one.
- **Sub-phase** = a stage inside a phase, lettered (`1.A`, `1.B`). Stages run in order.
- **Subtask** = numbered (`1.A.1`). Each carries a **DONE** condition that can be checked
  by someone who was not in the room.
- ★ = a landmine. Skipping it produces a failure that looks like something else.

### The operating rule

> **A fix must cite a measurement, not a symbol.**

Two claims in an earlier draft were wrong because they were reasoned from constant
*names* instead of following the branch (see `E9`, retracted, in `PLAN-0.0.518.md` §1).
Any subtask below whose justification is "the constant does not list it" is marked
**candidate** and is dropped unless a measurement confirms it.

### The gate every phase ends with

```bash
# fork suite — the ONLY correct runner. Expect 1905 + new, 0 failed.
cd /Users/rahulgupta/Desktop/CityAI-Final-Project/CityAgentWork
docker run --rm -v "$PWD/bagofwords:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc cityagentinsights:0.0.510.9 \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest tests/unit/fork -q -p no:cacheprovider -p no:warnings'
```

★★★**Never validate in `dash-app`.** Fork tests resolve `REPO = parents[4]` → `/app`;
the image has no `/app/locales` and no frontend source, so **169 real guard tests error
on `FileNotFoundError`** and are reported as environmental noise. Seven phases were
once validated that way and a trailing-newline regression in `en.json` shipped past
**two tests written specifically to catch it**.

★★**The `uid=999,gid=999` on the tmpfs mounts is load-bearing.** A tmpfs mounts
`root:root 0755`; the container runs as `uid=999(app)`. Without it pytest cannot create
its sqlite template and **every** test errors at setup — 139 of them, looking exactly
like broken code.

★A fresh image has **no pytest**. `pip install -q pytest pytest-asyncio` after every bake.

### Test placement rule

★**Never put a schema-needing test in `tests/unit/fork/`.** That directory's `conftest.py`
no-ops the parent's per-test migration (210.06s → 2.24s on 236 tests). A test that needs
a schema fails "no such table", which reads as a product bug. **Split by cost, not feature.**

★Every new guard test must be **run against unfixed code first and made to fail**.
A guard that never saw red proves nothing.

### Backup / rollback

- Backup: `../_backup-20260803/` — **one level up, in `CityAgentWork/`, not in the repo**.
  214M, verified. Restore steps in `MANIFEST.txt`.
- Rollback image: `cityagentinsights:0.0.510.7` (`c8a40c003bf7`) present on disk.
- ★★★Repoint `DASH_IMAGE` in `.env` at the **new** tag **before** building. Building over
  the live tag orphans the manifest and containerd collects it — **the rollback image is gone**.
  Two working images were lost exactly this way on 2026-07-25.

---

# PHASE 0 — baselines ✅ DONE

| id | subtask | result |
|---|---|---|
| 0.1 | Docker up, fork suite in `/src` | **1905 passed / 0 failed** |
| 0.2 | Full `tests/unit` baseline | 182 failed / 4604 passed / 56 skipped, 1:07:11 |
| 0.3 | Split the failures | **169 environmental + 13 real**, named in `BASELINE-FAILURES.txt` |
| 0.4 | Backup repo + DB + uploads + unprotected files | 214M, verified |
| 0.5 | Measure the base (do not trust `VERSION`) | `v0.0.510`: 2619/3096 identical, **0 stale** |
| 0.6 | Alembic integrity | **1 head** (`stepfiles01`), **0 dangling** across 220 revisions |

★0.2 was run inside the live `dash-app`, putting it at **90.6% CPU** while the user was
testing. Every latency number taken in that window is confounded. Phase 12 re-measures.

---

# RELEASE 1 — `0.0.510.10` "proven bugs"

Nothing in this release needs a measurement we do not already have.
No upstream code moves. Estimated 8h.

---

## PHASE 1 — generated-code extraction *(1.5h — proven, independent, start here)*

**The defect.** `coder.py:532`:

```python
result = re.sub(r'^\s*```(?:[A-Za-z0-9_\-]+)?\s*\r?\n', '', result.strip(), flags=re.IGNORECASE)
```

`^` after `.strip()` anchors to the very start of the response. Prose **before** the
fence is never stripped and reaches `exec()`.

**Proven by running it.** First line handed to `exec()` was
`'Looking at this request, I need to:'` → `SyntaxError: invalid syntax (<string>, line 1)`,
byte-identical to the screenshot.

**The tell.** The very next line calls `trim_after_final_df_return(result)`, which carefully
removes everything *after* the function. Nothing removes anything *before* it.

### 1.A — Build the helper

| id | subtask | target | DONE |
|---|---|---|---|
| 1.A.1 | Write `extract_generated_code(raw: str) -> str` in one shared place | new helper near `coder.py` top | function exists, importable, unit-callable |
| 1.A.2 | Rule 1 — take the **last** fenced block anywhere in the response, not the one at position 0 | — | prose-then-fence yields the fence body |
| 1.A.3 | Rule 2 — no fence at all → slice from the first `def` or `import` line | — | bare code with a preamble yields the code |
| 1.A.4 | Rule 3 — `compile(result, '<string>', 'exec')` before returning | — | a non-compiling result raises, not returns |

### 1.B — Wire every call site

| id | subtask | file:line | DONE |
|---|---|---|---|
| 1.B.1 | `data_model_to_code` | `coder.py:532` (def at 258) | uses the helper |
| 1.B.2 | `generate_code` | `coder.py:964` (def at 698) | uses the helper |
| 1.B.3 | `generate_inspection_code` | `coder.py:1110` (def at 972) | uses the helper |
| 1.B.4 | `generate_transform_code` | `coder.py:1245` (def at 1116) | uses the helper |
| 1.B.5 | Remove the four `re.sub(r'^\s*\`\`\`…')` pairs | `coder.py` | zero occurrences remain in the file |
| 1.B.6 | `SyntaxError` from 1.A.4 → **codegen retry**, not a user-facing failure | the codegen retry loop | a bad extraction consumes a retry silently |

### 1.C — Guards *(each red before green)*

| id | test | asserts |
|---|---|---|
| 1.C.1 | prose-before-fence — the exact repro, `"Looking at this request, I need to:"` + 3 paragraphs + a ```python fence | the function body is extracted, nothing else |
| 1.C.2 | fence at position 0 still works | no regression on the common case |
| 1.C.3 | no fence at all, bare `def` | untouched |
| 1.C.4 | multiple fences (a prose example, then the real code) | the **last** one wins |
| 1.C.5 | extraction yields non-compiling text | raises → retry, no user-facing failure |
| 1.C.6 | ★AST/grep guard: all four sites call the helper | a 5th hand-rolled strip fails the suite |

★**Do not also "fix" the contradictory prompt here.** That is `4.D`. Keeping this phase
to extraction alone is what makes it provable on its own.

---

## PHASE 2 — the timeout contract *(2h — BLOCKED ON YOUR DECISION)*

**The finding.** All 9 red timeout tests are **upstream files, byte-identical to
`v0.0.510` in our tree**. We changed the code under them and left them red.

```
tests/unit/test_query_timeout.py      x7
tests/unit/test_query_cancellation.py x1
tests/unit/test_query_concurrency.py  x1
```

**What FORK-2 changed**, per the docstring at `code_execution.py:219`:
`query_timeout_seconds` stopped being the kill and became a **progress mark**. The kill
moved to `resolve_hard_timeout`, default `DEFAULT_HARD_TIMEOUT_SECONDS = 900`.

**Consequences nobody signed off on:**
1. A connection configured `query_timeout_seconds: 60` now runs to **900s — 15×**.
2. `resolve_hard_timeout` returns `max(soft, resolved)`, so a connection setting
   **cannot lower** the hard limit, only the soft mark. The docstring says a connection
   "may tighten it"; the code does the opposite.
3. `query_hard_timeout_seconds` is a **new org config set nowhere**, so every existing
   org silently runs on the 900s default.

### 2.A — Decide *(yours; everything below branches on it)*

| id | subtask | DONE |
|---|---|---|
| 2.A.1 | Choose **(a)** keep the soft-mark / hard-kill design and rewrite the 9 upstream tests to the new contract, or **(b)** restore `query_timeout_seconds` as the kill and keep the hard limit as a ceiling only | decision recorded in `PLAN-0.0.518.md` §5a G6 |

Both are defensible. The fork's reasoning (a retry alongside a still-running scan wastes
six minutes and keeps nothing) is sound. Nine red upstream tests is not.

### 2.B — If (a): make the design match its own docstring

| id | subtask | file:line | DONE |
|---|---|---|---|
| 2.B.1 | Let a connection value lower the hard limit | `resolve_hard_timeout`, `code_execution.py:247` | `max(soft, resolved)` no longer blocks tightening |
| 2.B.2 | Pick a real default; 900s is 15× the old 60s | `DEFAULT_HARD_TIMEOUT_SECONDS`, `:116` | value agreed and written in the changelog body |
| 2.B.3 | Seed `query_hard_timeout_seconds` into the org settings catalog so it is visible and editable, not an invisible default | org settings sync | the setting appears in AI Settings |

### 2.C — If (b): restore the kill

| id | subtask | DONE |
|---|---|---|
| 2.C.1 | `query_timeout_seconds` kills again; hard limit becomes a ceiling only | the 9 tests pass unmodified |
| 2.C.2 | Keep `_park_orphan` — it is still the right answer for the retry case (Phase 3) | parking still reachable |

### 2.D — Tests, either branch

| id | subtask | DONE |
|---|---|---|
| 2.D.1 | Rewrite (a) or restore (b) the 9 tests | 9 green, **and they still assert something** |
| 2.D.2 | Add one fork guard: the configured timeout is honoured end-to-end | red on today's code |
| 2.D.3 | Record the decision + reasoning in `CLAUDE.md` so the next port does not re-litigate it | one paragraph |

---

## PHASE 3 — the retry duplicates the warehouse scan *(2h)*

Two defects that compound. One is ours, one is inherited — keep them in separate commits.

**FORK-4 (ours).** `self._parked` lives on `QueryCapturingClientWrapper`, built in
`wrap_clients_for_capture` at `code_execution.py:1034`, called at `:1203` — **inside
`execute_code`**. A `ToolRunner` retry re-enters `tool.run_stream`, gets a fresh wrapper
and an **empty** `_parked`. The retried query cannot find the thread attempt 1 parked, so
it starts the second identical scan `_park_orphan` exists to prevent.

**No heartbeat.** `_PROGRESS_TICK_SECONDS = 15` (`:119`) is used **only** to slice the
thread join at `:807`. It emits no `tool.progress` event, so nothing resets `ToolRunner`'s
`idle_timeout_s=180`. Real behaviour of a long query in the agent path:

```
180s silence → _stream_with_idle raises TimeoutError
             → retry_on includes "timeout_error"  (policies.py)
             → attempt 2 relaunches the identical query
             → two live scans on the warehouse, first one unparked
```

The 900s hard budget from Phase 2 is **unreachable here**. 180s + a duplicate scan is the
actual product behaviour.

### 3.A — Make parking survive a retry

| id | subtask | file:line | DONE |
|---|---|---|---|
| 3.A.1 | Hoist parked state above the retry boundary (per report/run, keyed by connection + SQL as today) | `code_execution.py:668, 838, 860` | attempt 2 of the same SQL attaches to attempt 1's thread |
| 3.A.2 | Keep the existing "never shared across runs" property — this widens scope to the run, not globally | `_park_orphan` docstring | a different run never adopts a stale thread |

### 3.B — Emit a real heartbeat

| id | subtask | file:line | DONE |
|---|---|---|---|
| 3.B.1 | Emit `tool.progress` on each 15s tick so the idle timer resets | `code_execution.py:807` → tool event stream | a 5-minute query no longer trips `idle_timeout_s` |
| 3.B.2 | Confirm `ToolRunner` treats it as activity | `tool_runner.py:139` (`et == "tool.progress"`) | idle timer resets in a test |

### 3.C — G8: the tool hard timeout is dead code *(inherited — separate commit)*

`tool_runner.py:107-112` creates `hard_timeout()` as a bare `asyncio.create_task` and
never awaits it. A `raise` inside an un-awaited task is **stored, not propagated** — so
`TimeoutPolicy.hard_timeout_s = 300` has never fired. It only risks an
"exception never retrieved" warning at GC.

| id | subtask | DONE |
|---|---|---|
| 3.C.1 | Make the hard timeout actually fire (race it against the stream, or drop the field) | a test proves it fires |
| 3.C.2 | ★Keep this in its own commit — **confirmed present in upstream `origin/main`**, so it is not ours | commit isolated |
| 3.C.3 | Report upstream | issue filed or noted |

### 3.D — Guard

| id | test | asserts |
|---|---|---|
| 3.D.1 | one slow query, one retry | **one** scan reaches the source, not two |
| 3.D.2 | long query with ticks | idle timer does not fire |

---

## PHASE 4 — remaining proven fork bugs *(1.5h)*

### 4.A — FORK-1: small numbers lose their digits

`apply_readable_number_printing()` sets `display.float_format` to `f"{v:,.2f}"`. It fixes
the ten-digit case it was written for (`2.332757e+09` → `2,332,757,360.00`) and **breaks
the small one**: `0.0034` prints as `0.00`. Proven by running it. Process-global, never reset.

| id | subtask | file:line | DONE |
|---|---|---|---|
| 4.A.1 | Use a format that keeps both magnitudes | `code_execution.py:1073` | `0.0034` survives; `2,332,757,360.00` still correct |
| 4.A.2 | Fix the guard that let it through — `test_small_numbers_stay_readable` only checks `0.25 / 0.5 / 0.125`, all of which survive `,.2f` | `tests/unit/fork/test_printed_numbers_keep_their_digits.py:108` | test red on today's formatter |
| 4.A.3 | Add sub-`0.01` cases: a rate, a ratio, a currency-conversion factor | same file | ≥3 new asserts |

### 4.B — FORK-3: the prose gate passes prose

`_looks_like_component_code` passes any text containing `return` or `<`. 3 of 5 realistic
prose replies pass.

| id | subtask | DONE |
|---|---|---|
| 4.B.1 | Tighten to a real parse rather than substring presence | the 5 samples classify correctly |
| 4.B.2 | Guard with all 5 samples, red first | 5 asserts |

### 4.C — F3: the duplicate clarifying question

The incident rendered the **same clarify block twice** — near-identical wording, two
Submit buttons, one turn. **Not root-caused.**

| id | subtask | DONE |
|---|---|---|
| 4.C.1 | ★Diagnose **before** touching code: count `clarify` rows in `tool_executions` for that run | a number |
| 4.C.2 | One row → the bug is in the frontend / SSE replay. Two rows → the planner loop. Follow whichever | cause identified **and cited** |
| 4.C.3 | Fix at that layer only | one block, one Submit |
| 4.C.4 | Guard | red first |

★If 4.C.1 cannot be answered (run not retained), reproduce rather than guess. This may
split into its own phase rather than hold up the release.

### 4.D — The coder's contradictory orders

`coder.py:524` — *"produce ONLY the Python function code … no markdown, no comments, no
triple backticks, no text, no anything."*
`coder.py:179` — *"`.pdf` / `.docx` / `.pptx` / images → NOT readable from generated code
at all. Do not attempt it; the planner must use the `read_file` tool instead."*

Handed a `.docx`, the model must violate one. It said so in the trace: *"However, the task
requires a Python function."* A cheap model resolved the contradiction **out loud**, in
prose, which Phase 1 now survives — but the contradiction is still there.

| id | subtask | DONE |
|---|---|---|
| 4.D.1 | Give the coder a structured way to **refuse back to the planner** | a doc handed to codegen returns a refusal, not generated code |
| 4.D.2 | Make the refusal actionable — name `read_file` as the route | the planner's next step is `read_file` |
| 4.D.3 | Guard: `.docx` into codegen → refusal, not a `SyntaxError` | red first |

★Phase 1 makes this failure survivable. 4.D makes it not happen. Both are needed —
Phase 1 also covers cases 4.D never sees.

---

## PHASE 5 — ship `0.0.510.10` *(1h)*

### 5.A — Prepare

| id | subtask | DONE |
|---|---|---|
| 5.A.1 | `printf '0.0.510.10' > VERSION` | — |
| 5.A.2 | CHANGELOG entry. ★Partial scope goes in the **body**, never in the number | entry written |
| 5.A.3 | ★★★`sed -i '' 's\|^DASH_IMAGE=.*\|DASH_IMAGE=cityagentinsights:0.0.510.10\|' .env` **before** building | `.env` points at the new tag |

### 5.B — Build and verify

| id | subtask | DONE |
|---|---|---|
| 5.B.1 | `docker compose -p cityagentinsights -f docker-compose.dev.yaml build app` (add `FE_CACHEBUST=$(date +%s)` **only** if a frontend file changed) | image built |
| 5.B.2 | ★Verify content **inside the new image**, not the running one: `docker run --rm --entrypoint sh cityagentinsights:0.0.510.10 -c 'cat /app/VERSION; grep -c extract_generated_code /app/backend/app/ai/agents/coder/coder.py'` | version + marker confirmed |
| 5.B.3 | ★If a frontend file changed, prove it by chunk filename/md5 between images — **never** by grepping the bundle for an identifier (minifiers rename locals) | md5 differs |

### 5.C — Gate

| id | subtask | DONE |
|---|---|---|
| 5.C.1 | Fork suite in `/src` | 1905 + new, 0 failed |
| 5.C.2 | Full `tests/unit`, matched **by failure name** against `BASELINE-FAILURES.txt` | membership unchanged except the 9 fixed in Phase 2 |
| 5.C.3 | ★A matching *count* with changed *membership* is the failure mode that matters. Diff the names | names diffed |

### 5.D — Deploy and replay

| id | subtask | DONE |
|---|---|---|
| 5.D.1 | `docker compose -p cityagentinsights -f docker-compose.dev.yaml up -d app` | container on the new tag |
| 5.D.2 | Confirm no stale bind mount: `docker inspect dash-app --format '{{range .Mounts}}{{.Type}} {{.Destination}}{{"\n"}}{{end}}' \| grep bind` | only `dash-config.yaml` |
| 5.D.3 | Replay the incident: same agent, same folder, one `.docx`, `summaries data for me` | real summary; no codegen; no red box |
| 5.D.4 | ★Replay on an **idle** container | no suite running |

---

# RELEASE 2 — `0.0.510.11` "any file, honestly"

Everything here is gated on measurement. Estimated 5h.

---

## PHASE 6 — measure file support *(1h, zero risk, no product file touched)*

Throwaway container off the current image, `/src` read-only. ★**Never `dash-app`** —
that is the live app.

### 6.A — Corpus

| id | subtask | DONE |
|---|---|---|
| 6.A.1 | ~25 real files: `pdf docx doc pptx ppt odt odp rtf csv tsv xlsx xls parquet json ndjson txt md html xml yaml log png jpg tiff bmp webp eml msg zip` | each opens in its native reader |
| 6.A.2 | 4 adversarial: image-only PDF; 1-line docx (trips `MIN_USABLE_DOC_CHARS = 16`); garbled-font PDF; corrupt docx | 29 fixtures total |

### 6.B — Probe the real dispatch

| id | subtask | DONE |
|---|---|---|
| 6.B.1 | Per fixture, call the real path: `extract_document_text`, `doc_text_is_usable`, `render_file_images`, `_source_files` reader lookup, `read_file` dispatch | 29 rows |
| 6.B.2 | Classify each: **content / honest refusal / silent garbage / crash**. Only the last two are bugs | every row classified |
| 6.B.3 | ★★★For every non-content outcome, record the deciding `file:line`. **Omitting this step is exactly what produced the retracted E9** — `.doc/.rtf/.odt/.odp/.ppt` were called unsupported from a constant name, when `read_file.py:664` (`content_type == "binary"`) → `:671 render_file_images` → `_file_tool_common.py:583` → `CONVERTIBLE_EXTS` → LibreOffice already handles them | every non-content cell cites a line |

### 6.C — Conditions

| id | subtask | DONE |
|---|---|---|
| 6.C.1 | Sweep 4 conditions — the vision fallback needs **both** `model.supports_vision` (`read_file.py:586`) **and** `allow_llm_see_data` (`:602`) | 4-way table |
| 6.C.2 | Flag any format that works only with both — it is **broken for any PII-protected org or text-only model** | flagged list |

### 6.D — Open questions this phase closes

| id | subtask | DONE |
|---|---|---|
| 6.D.1 | **E10** — is `read_file` gated off agent-attached files? `agent_v2.py:784` calls `capabilities_for_report_files(bool(getattr(report, "files", None)))`; the question is whether an agent-attached doc populates `report.files`. Settle from the incident report, or reproduce | confirmed or refuted |
| 6.D.2 | **E12** — can `model_router` route the **codegen** model down? Codegen runs on `runtime_ctx.get("model")` (`create_data.py:1803`, and 5 other sites), not `small_model`. If routing can downgrade it, the Phase 1 failure fires on accounts that never chose a cheap model | answered |
| 6.D.3 | **E11** — `.parquet / .eml / .msg / .zip` genuinely unreadable? (grep-only claim today) | measured |

★6.D.1 needs the **incident report URL**. Without it, reproduce — about an hour.

### 6.E — Output

| id | subtask | DONE |
|---|---|---|
| 6.E.1 | Write `FILE-SUPPORT-MATRIX.md` — one row per format, one column per condition | file exists |
| 6.E.2 | Write `FILE-DEFECTS.md` — only the silent-garbage and crash cells, each with its `file:line` | file exists |
| 6.E.3 | ★Rewrite Phase 7's scope from these two files. **Any fix that cannot cite a cell is dropped** | Phase 7 table rewritten |

---

## PHASE 7 — file fixes *(3h — scope set by 6.E.3; below are candidates)*

### 7.A — Registry coherence *(candidate)*

17 extension registries disagree. Set arithmetic says `.doc .rtf .odt .odp .ppt .bmp
.tiff .tif .parquet .eml .msg .html .xml .yaml .zip` have **no reader and no codegen
block** — so codegen silently guesses. E9 proved that set arithmetic alone is not enough:
some of those work through a path the constants do not mention.

| id | candidate | drop unless |
|---|---|---|
| 7.A.1 | Add the missing extensions to `_NOT_LOADABLE` (`_source_files.py:40`, `step_files.py:62`) and `_CODEGEN_UNREADABLE_EXTS` (`coder.py:189`) so codegen stops trying `pd.read_csv` on them | 6.B.2 shows silent garbage |
| 7.A.2 | Single registry, **default-deny** for unknown extensions. Today unknown = "let codegen try", the inversion that produces garbage | 6.E.3 confirms the disagreement matters |
| 7.A.3 | ★When writing the guard: a literal-index scan misses `excel_files[i]` in a loop, and every generated function **declares** `excel_files` in its signature. Both mistakes were made before and both let a bug through | the guard catches both shapes |

### 7.B — Missing readers *(candidate)*

| id | candidate | note | drop unless |
|---|---|---|---|
| 7.B.1 | `.parquet` | pyarrow 18.1.0 already in the image, `pandas.read_parquet` available — **no new dependency** | 6.B.2 shows crash/garbage |
| 7.B.2 | `.html .xml` | text-ish, cheap | 6.B.2 shows garbage |
| 7.B.3 | `.eml` | stdlib `email` | 6.B.2 shows crash |
| 7.B.4 | `.msg` (Outlook) | ★may need a dependency — **flag it, never add silently** | 6.B.2 **and** an explicit dependency decision |
| 7.B.5 | `.zip` | decide: refuse honestly, or list members | 6.B.2 |

### 7.C — Reachability *(candidate)*

| id | candidate | drop unless |
|---|---|---|
| 7.C.1 | E10 capability gate — make `read_file` reachable for agent-attached files | 6.D.1 confirms it |
| 7.C.2 | Connector `TEXT_EXTS` drift across s3 / google_drive / graph_drive / network_dir — the same file readable from one source, opaque from another | 6.B.2 shows a per-source difference |

### 7.D — Unconditional

| id | subtask | why | DONE |
|---|---|---|---|
| 7.D.1 | **PERF-1** — gate `search_mcps` behind an `mcp_tools` capability, the same mechanism `read_file` already uses in `registry.py` | in the incident it returned `(0)` after 134ms and cost a **full planner round-trip**. That zero is real: `filter_tools_by_query` ends every branch `return matched or tools`, so empty means none existed | a no-MCP agent never calls it |
| 7.D.2 | Registry-agreement test, table-driven | fails the moment a format is added to one registry and not the others | red if a format is added to one place only |
| 7.D.3 | The 6.A corpus becomes the **committed** test corpus | per format: content or honest refusal. Never crash, never silent garbage | 29 cases in CI |

---

## PHASE 8 — ship `0.0.510.11`

Same sub-phases as Phase 5 (5.A → 5.D), with one addition:

| id | subtask | DONE |
|---|---|---|
| 8.D.5 | Replay the incident for `.doc`, `.rtf` and `.parquet` as well as `.docx` | each returns content or an honest refusal |

---

# RELEASE 3 — `0.0.518.1` "the upstream port"

8 versions, 29 non-merge commits, 76 files, +7516/−257. **No migrations.**
Measured merge cost: **5 files, 6 conflict hunks.** Estimated 7h.

★Range by **commit**, never by tag. `0.0.511`, `0.0.513`, `0.0.514` are untagged, and tag
boundaries do not match changelog numbering (`v0.0.512` carries 511+512; `v0.0.515`
carries 511–515). `0.0.517` and `0.0.518` have **no upstream changelog entry at all** —
and they are the two that matter most to us.

---

## PHASE 9 — port prep *(1h — do not skip)*

### 9.A — Setup

| id | subtask | DONE |
|---|---|---|
| 9.A.1 | Branch off the post-Release-2 tree | branch exists |
| 9.A.2 | Re-run the merge simulation — the conflict set moves once Phases 1–7 land | fresh conflict list |
| 9.A.3 | Re-confirm alembic: 1 head, 0 dangling. ★`fccfb9232670` is an upstream no-op merge revision our fork deliberately never took (`svr0001` is re-pointed onto `fastq002`) | still 1 head |

### 9.B — The three merges git will get wrong

These produce a **clean, green, incorrect** merge. Git cannot see any of them.

| id | file | what happens if you take upstream whole | correct resolution |
|---|---|---|---|
| 9.B.1 | `backend/app/ai/runner/tool_runner.py` | our **DEF-003 fix is silently deleted**. It is our only change to the file, and upstream `main` still lacks it: a tool that sets `success: False` has its real message ("find text not found") replaced by a generic "Output validation failed" aimed at the tool author, so the model cannot correct its own edit | keep `_self_declared_failure` (`:167`) |
| 9.B.2 | `backend/app/ai/context/builders/schema_context_builder.py` | **every `powerbi_user` agent reports 0 tables.** Upstream `e218facb` rewrites the overlay column loop; our fork changed one line *inside the same hunk* — `canonical_is_active` defaults `True` when there is no canonical `DataSourceTable`, because a pure user-scoped connector has no canonical catalog (the overlay **is** the catalog). Upstream's side still reads `else False` | upstream's column-building body **+** our default-`True` |
| 9.B.3 | `backend/app/services/data_source_service.py` | the blank-report picker does not order by last used. Upstream `d1fb519d` adds `last_used_by_ds` next to `cached_by_ds`, but we call `_cached_table_names_by_ds` at **three** sites (`:1903`, `:2119`, `:2288`) and the conflict surfaces at only one | put it in the path the picker reads; check the other two |

### 9.C — Mechanical conflicts

| id | file | note |
|---|---|---|
| 9.C.1 | `frontend/components/KnowledgeExplorer.vue` (2 hunks) | our sync-strip / drift-notice / learn-bar blocks sit immediately above the counts block upstream is making clickable. Keep ours, apply upstream's changes inside the counts block |
| 9.C.2 | `CHANGELOG.md`, `VERSION` | expected; resolve by hand |

---

## PHASE 10 — the port

Newest-first, because 518 and 517 carry the correctness fixes and 517 collides with
Phase 2. Each row is a subtask.

### 10.A — Version 0.0.518 — Gemini *(clean; our file is byte-identical to base)*

| id | subtask | detail |
|---|---|---|
| 10.A.1 | Thinking-budget floor | `google_client.py:53, 75, 219` — `thinking_budget = 128 if "pro" in model_id else 0`; budget 0 **400s** on some models |
| 10.A.2 | Stop stripping JSON-Schema-keyword params inside `properties` / `$defs` / `definitions` / `patternProperties` | `_GOOGLE_SCHEMA_STRIP` applied at every depth (`:172, :178`) currently deletes real params — e.g. `create_data`'s required `title` (`schemas/create_data.py:32`) |
| 10.A.3 | Forward-pass `tool_use_id → name`; per-request id prefix | recycled call ids |
| 10.A.4 | `thought_signature` capture + replay | multi-turn tool replay |
| 10.A.5 | Trailing `STOP` no longer downgrades tool-use | — |
| 10.A.6 | Port the tests that come with it | `test_google_tool_schema.py`, `test_google_message_translation.py`, `tests/integrations/llm_clients.py`, `tests/mocks/bearer_gated_mcp_server.py` |

### 10.B — Version 0.0.517 *(the dense one — no upstream changelog entry)*

| id | subtask | difficulty | gate |
|---|---|---|---|
| 10.B.1 | `SwallowedQueryError` — an empty frame **plus** a recorded query error raises instead of shipping a 0-row "success". ★**Verified compatible**: it keys off `error` in `captured_timings`, which our rewritten wrapper records at `:734` (timeout) and `:752` (generic) | merge | **after Phase 2** — same function |
| 10.B.2 | Port `test_swallowed_query_error.py` | clean | — |
| 10.B.3 | Evidence kept in context long enough for the agent to combine it | merge (`agent_v2.py`, 823 lines diverged, **0 textual conflicts**) | after 10.B.1 |
| 10.B.4 | `read_query`'s code surfaced in the planner observation | clean (our delta: 3 lines) | — |
| 10.B.5 | Column metadata + PK/FK reach the prompt; stop claiming RLS is detectable | mixed | **carries 9.B.2** |
| 10.B.6 | Per-user OAuth connectors stop 401-ing system-context indexing (4 paths) | mixed | — |
| 10.B.7 | Power BI without admin scope — relationships, model types, measures. ★Measured: **merges clean** despite upstream +414 against our +557. The old "hard" rating was inferred from churn, not measured — but clean ≠ correct, so test it | clean merge, verify | — |
| 10.B.8 | Snowflake semantic view — logical tables and joins | clean | — |
| 10.B.9 | Port `test_powerbi_relationships.py`, `test_snowflake_semantic_view.py`, `test_schema_context_overlay_metadata.py`, `test_schema_prompt_rendering.py`, `test_per_user_oauth_connector_indexing.py`, `test_read_query_observation_code.py`, `test_vision_image_retention.py` | clean | — |

### 10.C — Version 0.0.516 — export *(clean; our files identical to base)*

| id | subtask |
|---|---|
| 10.C.1 | Exported and emailed PDFs stop losing content — scrolling panels opened, printable width, landscape, charts redrawn, pagination without splitting cards, repeated table headers (`report_pdf_service.py`, 460 lines, straight take) |
| 10.C.2 | A slides report exports as the **actual deck** instead of pages of raw generation code |
| 10.C.3 | Images embedded in a dashboard appear in its PDF instead of a placeholder |
| 10.C.4 | Port `test_report_pdf_completeness.py` (marked e2e upstream) |

### 10.D — Version 0.0.514 — PostHog *(clean; mostly new files)*

| id | subtask |
|---|---|
| 10.D.1 | Custom queries on PostHog — HogQL materialized on a schedule; 5–600× faster, no rate limit, past the 50,000-row ceiling |
| 10.D.2 | `posthog_source.py` is a **new file** — pure add |
| 10.D.3 | Extraction in verified windows with real column types |
| 10.D.4 | Port `test_posthog_source.py` (689 lines), `test_fast_sql_dialect.py` |

### 10.E — Versions 0.0.513 / 0.0.512 — instructions *(clean)*

| id | subtask |
|---|---|
| 10.E.1 | Sequential instruction edits **stack instead of discarding all but the last** |
| 10.E.2 | Stop suggesting something already waiting for review |
| 10.E.3 | One "Pending review" header, not two stacked bars; count + Accept all / Reject all move into the instruction header |
| 10.E.4 | Review bar i18n — it was English-only regardless of selected language |
| 10.E.5 | Port `test_instruction_edit_anchors.py`, `test_search_instructions_pending.py` |

### 10.F — Versions 0.0.515 / 0.0.511 — pickers *(merge, 4 FE files)*

| id | subtask |
|---|---|
| 10.F.1 | Blank report picks its agents — most-recently-used first, searchable, starter questions follow. **Carries 9.B.3** |
| 10.F.2 | Agent overview counts clickable — jump to that section, like the explorer tree |
| 10.F.3 | Port `test_agent_last_used.py` |
| 10.F.4 | ★FE is a static SPA — this needs a rebuild with `FE_CACHEBUST` |

### 10.G — Hygiene

| id | subtask |
|---|---|
| 10.G.1 | 1042 untracked `.bak*` files; 20G tree |
| 10.G.2 | ★Never `docker builder prune -af` — it deletes the apt (658 MB) and playwright/Chromium (1 GB) layers. Use `./scripts/prune-safe.sh 168h` |

---

## PHASE 11 — tests and durability *(2h — blocks no release)*

### 11.A — Port the tests we never brought over

Absent from our tree, added upstream `v0.0.493`–`v0.0.510`, never ported. **The code they
guard is already here** — verified: `app/ai/tools/chart_spec.py`,
`app/ai/context/data_preview.py`, `powerbi_client.py`, `app/models/build_content.py`.
We ship those features unguarded.

| id | file | covers |
|---|---|---|
| 11.A.1 | `tests/unit/test_chart_spec.py` | chart derived from data, LLM inference as refinement |
| 11.A.2 | `tests/unit/test_data_preview_cell_cap.py` | cell width cap in previews and stats |
| 11.A.3 | `tests/unit/test_powerbi_item_level_access.py` | identities with item-level model access |
| 11.A.4 | `tests/e2e/test_instruction_activity.py` | org-wide instruction list + changelog |
| 11.A.5 | `tests/e2e/rbac/test_instruction_pending_carryover.py` | real changes on `build_contents` |
| 11.A.6 | `tests/e2e/test_scheduled_refresh_archive_guard.py` | fire-time guard for archived reports |
| 11.A.7 | `tests/e2e/test_mcp_forwarding_eu_live.py` | MCP forwarding |
| 11.A.8 | ★Record which **fail on our tree before fixing anything** | a list |

### 11.B — Close the hole that let FORK-2 ship

`tests/unit/fork/` runs in ~7s and is what gets run per change. The 9 failing
query-timeout tests live in `tests/unit/`, which takes 1h07 and is documented as an
after-port step only. **That gap is the whole reason FORK-2 shipped.**

| id | subtask | DONE |
|---|---|---|
| 11.B.1 | Define a fast non-fork subset — query timeout / cancellation / concurrency at minimum | subset runs in the inner loop |
| 11.B.2 | Keep the cost split honest: schema-needing tests stay out of `tests/unit/fork/` | no "no such table" failures |
| 11.B.3 | Make `/src` the default documented runner everywhere so the 169-silently-erroring `dash-app` path stops being reachable by habit | `CLAUDE.md` updated |

### 11.C — Triage the rest

| id | test | note |
|---|---|---|
| 11.C.1 | `test_resource_permissions_only_data_source_in_mvp` | asserts `{data_source, connection}`; we added `project`. **Likely a stale test, not a code bug** |
| 11.C.2 | `test_artifact_key_roundtrips_through_fernet` | untriaged |
| 11.C.3 | `test_real_org_settings_defaults_are_adaptive` | untriaged |
| 11.C.4 | `test_data_model_to_code_prompt_carries_time_filter_rules` | untriaged |
| 11.C.5 | Each fixed, or recorded as known-and-accepted | "13 real" stops being an unexplained number |

### 11.D — Restore what was dropped

| id | subtask |
|---|---|
| 11.D.1 | `SECURITY.md` (upstream vulnerability-reporting policy) |
| 11.D.2 | `scripts/version-auto-update/version-auto-update.sh` |

---

## PHASE 12 — latency *(3h — independent of every release)*

★★★Every measurement on an **idle** `dash-app`. The P0 baseline suite had it at
**90.6% CPU**, which confounds every timing taken in that window.

Incident breakdown, 70s total: `inspect_data` **35.3s**, `search_mcps` 134ms returning
nothing, failed codegen 10.5s, ~24s planner LLM time.

| id | subtask | DONE |
|---|---|---|
| 12.1 | **PERF-2 — prompt caching.** Verify whether `anthropic_client.py` sets cache breakpoints on the static blocks. `ContextHub`'s static (schemas / instructions / resources) vs warm (messages / observations / widgets / steps) split is **exactly** the right shape for it. If the breakpoints are absent this is the single largest latency **and cost** lever in the product. ★Unverified — measure before claiming | answered with a number |
| 12.2 | **PERF-4 — `inspect_data` 35.3s.** Split warehouse time from our own schema / sampling overhead. If it is ours, it is cacheable. Needs the trace | breakdown produced |
| 12.3 | **PERF-3 — model routing.** `model_router.py` exists; `main_execution` already stamps `routed` / `baseline_model_id`. What does a trivial turn route to? (6.D.2 answers the codegen half) | answered |
| 12.4 | **PERF-5 — failed-codegen cost.** Phase 1 and 10.B.1 each remove a failed attempt plus its retry from the critical path | measured |
| 12.5 | Before/after breakdown of the same prompt, idle container | one table |

---

## PHASE 13 — release `0.0.518.1` and documentation *(1.5h)*

### 13.A — Ship
Sub-phases 5.A → 5.D, with `VERSION` = `0.0.518.1`.
★Version scheme: base = the **upstream release ported**; `.N` = **our** work on top.
A partial port is described in the changelog **body**, never encoded in the number.

### 13.B — Documentation

| id | subtask | DONE |
|---|---|---|
| 13.B.1 | Update `CLAUDE.md`: the measured-base method (count byte-identical files per tag, do not trust `VERSION`), the untagged-release trap, `/src` vs `dash-app`, FORK-1/2/3/4, the file-support matrix. ★Keep near 200 lines — it is read in full every session and a long guide is followed less reliably | updated |
| 13.B.2 | Correct the `0.0.500` note — it **was** bumped upstream (`1fc86166`) and has a changelog entry (Sign in with Snowflake). It was never *tagged*, which is a different thing | corrected |
| 13.B.3 | Append the session to `CLAUDE-SESSIONS.md`, **not** to `CLAUDE.md` | appended |

### 13.C — ★Protect the four gitignored files

`CLAUDE.md`, `CLAUDE-SESSIONS.md`, `TEST-DEFECTS.md` and `.env` are gitignored — **the
remote has never held them**. Today's only protection is one backup taken by hand on
2026-08-03.

**`.env` holds `DASH_ENCRYPTION_KEY`. Without it, every Fernet secret in the database is
undecryptable** — SMTP credentials, the LDAP bind password, every SSO client secret — and
a database dump alone does not recover them.

| id | subtask | DONE |
|---|---|---|
| 13.C.1 | Decide: track the three docs and keep only `.env` out, or a scheduled backup, or a secret store | decision recorded |
| 13.C.2 | Implement it | protection in place |
| 13.C.3 | ★Never commit `.env` itself | still gitignored |

---

# Phase index

| phase | sub-phases | what | release | gate | est |
|---|---|---|---|---|---|
| 0 | 0.1–0.6 | baselines | — | ✅ done | — |
| 1 | 1.A–1.C | code extraction | 510.10 | none — **start here** | 1.5h |
| 2 | 2.A–2.D | timeout contract | 510.10 | **your decision (2.A.1)** | 2h |
| 3 | 3.A–3.D | retry duplicates the scan | 510.10 | none | 2h |
| 4 | 4.A–4.D | FORK-1, FORK-3, F3, coder contradiction | 510.10 | 4.C.1 before 4.C.3 | 1.5h |
| 5 | 5.A–5.D | ship `0.0.510.10` | 510.10 | phases 1–4 | 1h |
| 6 | 6.A–6.E | measure file support | 510.11 | none (6.D.1 wants the report URL) | 1h |
| 7 | 7.A–7.D | file fixes | 510.11 | **6.E.3** | 3h |
| 8 | 8.A–8.D | ship `0.0.510.11` | 510.11 | phase 7 | 1h |
| 9 | 9.A–9.C | port prep + the 3 wrong merges | 518.1 | after release 2 | 1h |
| 10 | 10.A–10.G | the port | 518.1 | 10.B.1 after phase 2; 10.B.3 after 10.B.1 | 6h |
| 11 | 11.A–11.D | tests + durability | — | after the port | 2h |
| 12 | 12.1–12.5 | latency | — | idle container | 3h |
| 13 | 13.A–13.C | release + docs + secrets | 518.1 | last | 1.5h |

**Critical path to a shipped fix: 1 → 2 → 3 → 4 → 5.**
Phase 6 runs alongside 1–4 — it touches no product file.
Phase 2 is the only phase blocked on you.

---

# Open questions

1. **The incident report URL** — closes 6.D.1 directly; without it, reproduce (~1h).
2. **2.A.1** — which timeout contract. Blocking.
3. Prompt caching on the static context blocks — unverified, possibly the largest lever (12.1).
4. `inspect_data` 35.3s — warehouse or ours? Needs the trace (12.2).
5. `.msg` may need a dependency — flag, never add silently (7.B.4).
6. Should `PLAN-0.0.518.md` and this file stay at repo root? Neither is gitignored, so both commit.
