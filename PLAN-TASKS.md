# Task list — every phase broken into small steps

Companion to `PLAN-EXECUTION.md` (the reasoning) and `PLAN-0.0.518.md` (the evidence).
This file is the **checklist**. Nothing here should take more than ~30 minutes.

Format: `[ ] N.M — action *(est)* → DONE when …`

★ = a landmine. ⛔ = blocked, do not start.

---

## Conventions used by every step

**Run the fork suite** (referred to below as *the gate*):
```bash
cd /Users/rahulgupta/Desktop/CityAI-Final-Project/CityAgentWork
docker run --rm -v "$PWD/bagofwords:/src:ro" \
  --tmpfs /src/backend/db:uid=999,gid=999 --tmpfs /src/backend/logs:uid=999,gid=999 \
  -w /src/backend -e PYTHONPYCACHEPREFIX=/tmp/pyc cityagentinsights:0.0.510.9 \
  sh -c 'pip install -q pytest pytest-asyncio; python -m pytest tests/unit/fork -q -p no:cacheprovider -p no:warnings'
```
★Never in `dash-app` — 169 real guards silently error there.
★`uid=999,gid=999` is load-bearing; without it all tests error at setup.
★New guard tests must be **run red first**. A test that never failed proves nothing.

**Commit style:** one commit per numbered group (1.A, 1.B…), not per micro-step.
★Use `<<'EOF'` (quoted) for every commit-message heredoc — unquoted executes backticks.

---

# PHASE 1 — code extraction *(≈1.5h, 14 steps)*

### 1.A Set up the failing case
- [x] **1.A.1** — Open `backend/app/ai/agents/coder/coder.py`, confirm the strip regex is still at lines **532, 964, 1110, 1245** *(5m)* → four line numbers confirmed or corrected in this file
- [x] **1.A.2** — Save the model's actual failing reply (prose + ```` ```python ```` fence) as `backend/tests/unit/fork/fixtures/prose_before_fence.txt` *(10m)* → file exists, starts `Looking at this request, I need to:`
- [x] **1.A.3** — Create `backend/tests/unit/fork/test_generated_code_extraction.py` with one test that feeds 1.A.2 through today's code path *(15m)* → **test FAILS** with `SyntaxError: invalid syntax (<string>, line 1)`

### 1.B Build the helper
- [x] **1.B.1** — Add `extract_generated_code(raw: str) -> str` returning `raw` unchanged *(5m)* → importable, test still fails
- [x] **1.B.2** — Find every fenced block with a regex, return the **last** one *(15m)* → 1.A.3 passes
- [x] **1.B.3** — Add the no-fence fallback: slice from the first `def ` or `import ` line *(10m)* → bare-code case works
- [x] **1.B.4** — Add `compile(result, '<string>', 'exec')` before returning; raise on failure *(10m)* → non-compiling input raises

### 1.C Wire the four call sites
- [x] **1.C.1** — Replace the regex in `data_model_to_code` (line ~532) *(5m)*
- [x] **1.C.2** — Replace it in `generate_code` (line ~964) *(5m)*
- [x] **1.C.3** — Replace it in `generate_inspection_code` (line ~1110) *(5m)*
- [x] **1.C.4** — Replace it in `generate_transform_code` (line ~1245) *(5m)*
- [x] **1.C.5** — `grep -c "re.sub(r'^\\s*\`\`\`" coder.py` *(2m)* → returns **0**

### 1.D Retry instead of failing the user
- [x] **1.D.1** — Catch the `SyntaxError` from 1.B.4 in the codegen retry loop *(15m)* → a bad extraction consumes a retry, no user-facing error
- [x] **1.D.2** — Test: extraction fails twice → user sees the normal failure path, not a raw `SyntaxError` *(10m)* → red first

### 1.E Lock it down
- [x] **1.E.1** — Test: fence at position 0 still works *(5m)*
- [x] **1.E.2** — Test: no fence at all, bare `def` *(5m)*
- [x] **1.E.3** — Test: two fences (prose example, then real code) → last one wins *(5m)*
- [x] **1.E.4** — Guard test: all four sites call the helper (AST or grep) *(15m)* → adding a 5th hand-rolled strip fails the suite
- [x] **1.E.5** — Run the gate *(10m)* → 1905 + 6 new, 0 failed

---

# PHASE 2 — timeout contract *(≈2h, 11 steps)* ⛔ blocked on 2.A.1

### 2.A The decision
- [x] **2.A.1** ⛔ — **Ask the user**: keep the two-stage design (soft mark + 900s kill) and rewrite the 9 tests, or restore `query_timeout_seconds` as the kill? *(5m)* → answer written into `PLAN-0.0.518.md` §5a G6

### 2.B Understand before changing *(do regardless of the answer)*
- [x] **2.B.1** — Run the 9 failing tests alone, capture the exact assertion each makes *(15m)* → 9 assertions listed
- [x] **2.B.2** — Confirm with `git log` that we never modified those 3 test files *(5m)* → confirmed upstream + unmodified
- [x] **2.B.3** — Read `resolve_query_timeout` (`code_execution.py:197`) and `resolve_hard_timeout` (`:219`); write down the resolution order for both *(15m)* → two orders written

### 2.C If (a) — keep the new design
- [x] **2.C.1** — Fix `resolve_hard_timeout` so a connection value can **lower** the limit, not only raise it (`:247`, `max(soft, resolved)`) *(15m)* → a connection can tighten
- [x] **2.C.2** — Choose and set a defensible `DEFAULT_HARD_TIMEOUT_SECONDS` (`:116`, today 900) *(10m)* → value + one-line rationale in the changelog body
- [x] **2.C.3** — Add `query_hard_timeout_seconds` to the org settings catalog so it is visible and editable *(20m)* → appears in AI Settings
- [x] **2.C.4** — Rewrite the 9 tests to the new contract *(30m)* → 9 green, still asserting something real

### 2.D If (b) — restore the old kill
- [x] **2.D.1** — Make `query_timeout_seconds` kill again; hard limit becomes a ceiling only *(30m)* → the 9 tests pass **unmodified**
- [x] **2.D.2** — Keep `_park_orphan` reachable — Phase 3 still needs it *(5m)* → parking still called

### 2.E Close it out *(either branch)*
- [x] **2.E.1** — New fork guard: the configured timeout is honoured end to end *(20m)* → red on today's code
- [x] **2.E.2** — Run the gate *(10m)*

---

# PHASE 3 — retry duplicates the scan *(≈2h, 12 steps)*

### 3.A Prove it first
- [x] **3.A.1** — Write a test: one slow query, force a tool retry, count how many reach the source *(25m)* → **fails, showing 2**
- [x] **3.A.2** — Confirm the wrapper is rebuilt per attempt: add a temporary log line in `wrap_clients_for_capture` (`code_execution.py:1034`), run 3.A.1 *(10m)* → two constructions logged
- [x] **3.A.3** — Remove the temporary log *(2m)*

### 3.B Make parking survive the retry
- [x] **3.B.1** — Decide where run-scoped parked state should live (report id? run id?) and write it down *(15m)* → one sentence
- [x] **3.B.2** — Move `self._parked` (`:668`) to that scope *(25m)* → 3.A.1 now shows **1**
- [x] **3.B.3** — Verify a *different* run never adopts a stale thread *(15m)* → test added

### 3.C Heartbeat — ⛔ DEFERRED, needs its own design (see below)
- [ ] **3.C.1** — Emit a `tool.progress` event on each 15s tick (`code_execution.py:807`) *(20m)* → event visible in the stream
- [ ] **3.C.2** — Confirm `ToolRunner` treats it as activity (`tool_runner.py:139`) *(10m)* → idle timer resets
- [ ] **3.C.3** — Test: a query longer than 180s with ticks does not trip the idle timeout *(15m)* → red first

### 3.D G8 — the dead hard timeout *(separate commit — inherited from upstream)*
- [x] **3.D.1** — Write a test proving `TimeoutPolicy.hard_timeout_s` never fires today *(15m)* → **fails to fire**
- [x] **3.D.2** — Fix it: race the watchdog against the stream instead of `create_task` + never awaiting (`tool_runner.py:107-112`) *(20m)* → test passes
- [x] **3.D.3** — Commit alone, message noting it is **upstream's bug, present in `origin/main`** *(5m)*
- [x] **3.D.4** — Run the gate *(10m)*

---

# PHASE 4 — four small bugs *(≈1.5h, 15 steps)*

### 4.A Numbers losing digits (FORK-1)
- [x] **4.A.1** — Add a failing case to `test_printed_numbers_keep_their_digits.py`: `0.0034` must survive *(10m)* → **fails, prints `0.00`**
- [x] **4.A.2** — Add two more: a rate `0.00087`, a conversion factor `0.000021` *(5m)*
- [x] **4.A.3** — Replace the format at `code_execution.py:1073` with one that keeps both magnitudes *(20m)* → new cases pass
- [x] **4.A.4** — Confirm the original ten-digit case still reads `2,332,757,360.00` *(5m)* → existing tests still green

### 4.B Prose passing as code (FORK-3)
- [x] **4.B.1** — Collect the 5 realistic prose samples into a fixture *(10m)*
- [x] **4.B.2** — Test all 5 against `_looks_like_component_code` *(10m)* → **3 wrongly pass**
- [x] **4.B.3** — Replace the substring check with a real parse *(20m)* → all 5 classify correctly

### 4.C Duplicate clarifying question (F3)
- [x] **4.C.1** — ★Query `tool_executions` for the incident run: how many `clarify` rows? *(15m)* → **a number**
- [x] **4.C.2** — One row → look at the SSE/replay path. Two rows → look at the planner loop. Pick the branch *(10m)* → branch chosen and cited
- [x] **4.C.3** — Write a failing test at that layer *(20m)* → red
- [x] **4.C.4** — Fix *(20m)* → green, one block + one Submit
- [x] ~~**4.C.5**~~ — not needed, 4.C.1 answered from the live database — If 4.C.1 cannot be answered (run not retained), reproduce instead — do **not** guess *(30m)*

### 4.D The coder's contradiction
- [x] **4.D.1** — Read `coder.py:524` and `coder.py:179` side by side; write the contradiction in one sentence *(10m)*
- [x] **4.D.2** — Test: hand a `.docx` to codegen today *(10m)* → **generates unusable code**
- [x] **4.D.3** — Add a refusal path the coder can take back to the planner *(25m)* → returns a refusal, not code
- [x] **4.D.4** — Make the refusal name `read_file` as the route *(10m)* → planner's next step is `read_file`
- [x] **4.D.5** — Run the gate *(10m)*

---

# PHASE 5 — ship `0.0.510.10` *(≈1h, 12 steps)*

### 5.A Prepare
- [ ] **5.A.1** — `printf '0.0.510.10' > VERSION` *(1m)*
- [ ] **5.A.2** — Write the CHANGELOG entry; partial scope goes in the **body**, never the number *(15m)*
- [ ] **5.A.3** — ★★★`sed -i '' 's|^DASH_IMAGE=.*|DASH_IMAGE=cityagentinsights:0.0.510.10|' .env` **before** building *(2m)* → building over the live tag destroys the rollback
- [ ] **5.A.4** — Confirm the previous image still exists on disk *(2m)* → rollback available

### 5.B Build
- [ ] **5.B.1** — `docker compose -p cityagentinsights -f docker-compose.dev.yaml build app` *(15m)* → no frontend change this release, so **omit** `FE_CACHEBUST`
- [ ] **5.B.2** — Verify **inside the new image**: `docker run --rm --entrypoint sh cityagentinsights:0.0.510.10 -c 'cat /app/VERSION; grep -c extract_generated_code /app/backend/app/ai/agents/coder/coder.py'` *(5m)* → version + non-zero count

### 5.C Gate
- [ ] **5.C.1** — Fork suite in `/src` *(10m)* → 1905 + new, 0 failed
- [ ] **5.C.2** — Full `tests/unit` *(70m, background)* → completes
- [ ] **5.C.3** — ★Diff failure **names** against `_backup-20260803/BASELINE-FAILURES.txt`, not counts *(10m)* → membership unchanged except what Phase 2 fixed

### 5.D Deploy and prove
- [ ] **5.D.1** — `docker compose -p cityagentinsights -f docker-compose.dev.yaml up -d app` *(5m)*
- [ ] **5.D.2** — Check for stale bind mounts: `docker inspect dash-app --format '{{range .Mounts}}{{.Type}} {{.Destination}}{{"\n"}}{{end}}' | grep bind` *(2m)* → only `dash-config.yaml`
- [ ] **5.D.3** — ★Confirm nothing heavy is running, then replay the original question: same agent, same folder, same `.docx` *(15m)* → real summary, no red box

---

# PHASE 6 — measure file support *(≈1h, 13 steps)*

★No product file is touched in this phase. Throwaway container only.

### 6.A Corpus
- [ ] **6.A.1** — Collect 12 common formats: `pdf docx xlsx csv tsv json txt md png jpg html xml` *(15m)*
- [ ] **6.A.2** — Collect 9 less common: `doc pptx ppt odt odp rtf parquet yaml log` *(15m)*
- [ ] **6.A.3** — Collect 4 containers: `zip eml msg ndjson` *(10m)*
- [ ] **6.A.4** — Build 4 adversarial: image-only PDF, 1-line docx (under `MIN_USABLE_DOC_CHARS=16`), garbled-font PDF, corrupt docx *(20m)* → 29 fixtures
- [ ] **6.A.5** — Verify each opens in its own native app *(10m)* → no broken fixture

### 6.B Probe
- [ ] **6.B.1** — Write a probe script calling the real path per file: `extract_document_text`, `doc_text_is_usable`, `render_file_images`, `_source_files` lookup, `read_file` dispatch *(25m)*
- [ ] **6.B.2** — Run it in a throwaway container off the current image *(10m)* → 29 rows of raw output
- [ ] **6.B.3** — Classify each row: **content / honest refusal / silent garbage / crash** *(20m)* → every row labelled
- [ ] **6.B.4** — ★For each non-content row, record the deciding `file:line` *(30m)* → **this is the step whose omission produced the retracted E9**

### 6.C Conditions and open questions
- [ ] **6.C.1** — Re-run 6.B under 4 combinations of `model.supports_vision` (`read_file.py:586`) × `allow_llm_see_data` (`:602`) *(20m)* → 4-way table
- [ ] **6.C.2** — Settle E10: does an agent-attached doc populate `report.files`? (`agent_v2.py:784`) *(20m)* → confirmed or refuted
- [ ] **6.C.3** — Settle E12: can `model_router` downgrade the **codegen** model? *(15m)* → answered

### 6.D Write it up
- [ ] **6.D.1** — `FILE-SUPPORT-MATRIX.md` — format × condition *(20m)*
- [ ] **6.D.2** — `FILE-DEFECTS.md` — only silent-garbage and crash rows, each with its line *(15m)*
- [ ] **6.D.3** — ★Rewrite Phase 7's list from these two files. **Drop anything that cannot cite a cell** *(15m)*

---

# PHASE 7 — file fixes *(≈3h, scope set by 6.D.3)*

⛔ Do not start 7.A or 7.B until 6.D.3 is done. 7.D is unconditional.

### 7.A Registry *(candidate)*
- [ ] **7.A.1** — List all 17 extension registries with their file:line *(20m)*
- [ ] **7.A.2** — Write a table-driven test asserting they agree *(25m)* → red, showing the disagreements
- [ ] **7.A.3** — Add the confirmed-broken extensions to `_NOT_LOADABLE` (`_source_files.py:40`, `step_files.py:62`) and `_CODEGEN_UNREADABLE_EXTS` (`coder.py:189`) *(20m)*
- [ ] **7.A.4** — Make unknown extensions **default-deny** instead of "let codegen try" *(30m)*
- [ ] **7.A.5** — ★Check the guard catches `excel_files[i]` inside a loop, not only literal indexes *(15m)*

### 7.B Readers *(candidate — one step each, only if 6.D.2 lists it)*
- [ ] **7.B.1** — `.parquet` via `pandas.read_parquet` (pyarrow 18.1.0 already present, **no new dependency**) *(20m)*
- [ ] **7.B.2** — `.html` / `.xml` as text *(20m)*
- [ ] **7.B.3** — `.eml` via the stdlib `email` module *(20m)*
- [ ] **7.B.4** — `.msg` — ★flag the dependency and **ask** before adding it *(10m)*
- [ ] **7.B.5** — `.zip` — decide: honest refusal, or list members *(15m)*

### 7.C Reachability *(candidate)*
- [ ] **7.C.1** — If 6.C.2 confirmed E10, make `read_file` reachable for agent-attached files *(25m)*
- [ ] **7.C.2** — Compare `TEXT_EXTS` across s3 / google_drive / graph_drive / network_dir *(20m)* → differences listed
- [ ] **7.C.3** — Align them *(20m)*

### 7.D Unconditional
- [ ] **7.D.1** — Gate `search_mcps` behind an `mcp_tools` capability, same mechanism `read_file` uses in `registry.py` *(25m)* → a no-MCP agent never calls it
- [ ] **7.D.2** — Test: agent with no MCP connection → zero `search_mcps` calls *(15m)* → red first
- [ ] **7.D.3** — Commit the 29 fixtures into `backend/tests/unit/fork/fixtures/` *(10m)*
- [ ] **7.D.4** — Per-format test: content or honest refusal, never crash, never silent garbage *(30m)*
- [ ] **7.D.5** — Run the gate *(10m)*

---

# PHASE 8 — ship `0.0.510.11` *(≈1h)*

- [ ] **8.1** — Repeat 5.A.1 → 5.D.2 with `VERSION` = `0.0.510.11` *(45m)*
- [ ] **8.2** — Replay the original question with a `.docx` *(5m)*
- [ ] **8.3** — Replay with a `.doc` *(5m)*
- [ ] **8.4** — Replay with an `.rtf` *(5m)*
- [ ] **8.5** — Replay with a `.parquet` *(5m)* → each returns content or an honest refusal

---

# PHASE 9 — port prep *(≈1h, 9 steps)*

### 9.A Setup
- [ ] **9.A.1** — Branch from the post-Release-2 tree *(5m)*
- [ ] **9.A.2** — Re-run the merge simulation — the conflict set has moved *(15m)* → fresh list
- [ ] **9.A.3** — Re-check alembic: 1 head, 0 dangling *(5m)*

### 9.B Write the three resolutions **before** merging
- [ ] **9.B.1** — `tool_runner.py`: note that our DEF-003 `_self_declared_failure` guard (`:167`) must survive — upstream `main` still lacks it *(10m)*
- [ ] **9.B.2** — `schema_context_builder.py`: note the resolution — upstream's column body **+ our** `canonical_is_active` default-`True`. Taking upstream whole makes `powerbi_user` agents report 0 tables *(15m)*
- [ ] **9.B.3** — `data_source_service.py`: identify which of the three `_cached_table_names_by_ds` sites (`:1903`, `:2119`, `:2288`) the blank-report picker reads *(15m)* → one line number
- [ ] **9.B.4** — `KnowledgeExplorer.vue`: note that our blocks stay, upstream's changes go **inside** the counts block *(10m)*

### 9.C Safety
- [ ] **9.C.1** — Fresh backup before the merge *(10m)*
- [ ] **9.C.2** — Confirm the rollback image for the current release exists *(2m)*

---

# PHASE 10 — the port *(≈6h)*

One commit per group. Run the gate after each group, not after each step.

### 10.A Gemini (0.0.518)
- [ ] **10.A.1** — Confirm `google_client.py` is still byte-identical to `v0.0.510` *(5m)* → straight take is safe
- [ ] **10.A.2** — Apply `266355c0` (keyword params, thinking budget) *(20m)*
- [ ] **10.A.3** — Apply `8e51177b` (call ids, `thought_signature`) *(20m)*
- [ ] **10.A.4** — ★Note: `thought_signature` already exists in our tree (9 files) but `_encode_signature` does not — we have partial support, **not** the fix *(5m)*
- [ ] **10.A.5** — Port `test_google_tool_schema.py`, `test_google_message_translation.py` *(15m)*
- [ ] **10.A.6** — Port `tests/integrations/llm_clients.py`, `tests/mocks/bearer_gated_mcp_server.py` *(15m)*
- [ ] **10.A.7** — Run the gate *(10m)*

### 10.B Query correctness (0.0.517) — ⛔ after Phase 2
- [ ] **10.B.1** — Add `QUERY_FAILED_SILENTLY` to `errors/codes.py` *(5m)*
- [ ] **10.B.2** — Add the `SwallowedQueryError` class *(15m)*
- [ ] **10.B.3** — Add `_raise_if_query_errors_were_swallowed` and call it *(20m)*
- [ ] **10.B.4** — ★Verify it fires against **our** wrapper — it keys off `error` in `captured_timings`, which we record at `:734` and `:752` *(15m)*
- [ ] **10.B.5** — Handle it at every call site: agent retry loops, step/query rerun, report refresh, global handler *(30m)*
- [ ] **10.B.6** — Port `test_swallowed_query_error.py` *(10m)*
- [ ] **10.B.7** — Run the gate *(10m)*

### 10.C Agent context (0.0.517) — ⛔ after 10.B
- [ ] **10.C.1** — Apply `98189074` (evidence retention) *(30m)*
- [ ] **10.C.2** — Apply `aab074b6` (`read_query` code in the observation) *(15m)*
- [ ] **10.C.3** — Port `test_vision_image_retention.py`, `test_read_query_observation_code.py` *(15m)*
- [ ] **10.C.4** — Run the gate *(10m)*

### 10.D Connectors (0.0.517)
- [ ] **10.D.1** — Apply `e218facb` (column metadata to prompt) — ★**carries 9.B.2** *(30m)*
- [ ] **10.D.2** — Verify a `powerbi_user` agent still lists tables *(10m)* → not zero
- [ ] **10.D.3** — Apply `2a22654c` + `6c8f683b` (Power BI without admin scope) *(30m)*
- [ ] **10.D.4** — Apply `6dfb1ba9` (Snowflake semantic view) *(20m)*
- [ ] **10.D.5** — Apply `52e26863` (per-user OAuth indexing 401) *(20m)*
- [ ] **10.D.6** — Port the 5 connector tests *(20m)*
- [ ] **10.D.7** — Run the gate *(10m)*

### 10.E Export (0.0.516)
- [ ] **10.E.1** — Confirm `report_pdf_service.py` is byte-identical to base *(5m)* → straight take
- [ ] **10.E.2** — Apply `ef6f38bf` (PDF completeness) *(20m)*
- [ ] **10.E.3** — Port `test_report_pdf_completeness.py` *(10m)*
- [ ] **10.E.4** — Export one real wide dashboard and one slides report by hand *(15m)* → nothing cut, deck is a real deck

### 10.F PostHog (0.0.514)
- [ ] **10.F.1** — Add `data_sources/fast/posthog_source.py` (new file) *(15m)*
- [ ] **10.F.2** — Apply `04292979` + `a4dc9944` *(25m)*
- [ ] **10.F.3** — Port `test_posthog_source.py`, `test_fast_sql_dialect.py` *(15m)*

### 10.G Instructions (0.0.513 / 0.0.512)
- [ ] **10.G.1** — Apply `96973abe` (edits stack instead of overwriting) *(25m)*
- [ ] **10.G.2** — Apply `b051164a` (staged edits surface in search) *(15m)*
- [ ] **10.G.3** — Apply `6b238ab0` (one Pending-review header + i18n) *(20m)*
- [ ] **10.G.4** — ★Check `locales/en.json` gains **no trailing newline** — two tests exist for this and one slipped past both *(5m)*
- [ ] **10.G.5** — Port the 2 instruction tests *(10m)*

### 10.H Pickers (0.0.515 / 0.0.511)
- [ ] **10.H.1** — Apply `d3e884f4` + the 5 follow-up commits (blank-report picker) *(35m)*
- [ ] **10.H.2** — Apply `d1fb519d` — ★**carries 9.B.3**, put `last_used_by_ds` in the right list *(20m)*
- [ ] **10.H.3** — Apply `8b1e9e1e` (clickable counts) — ★**carries 9.B.4** *(20m)*
- [ ] **10.H.4** — Port `test_agent_last_used.py` *(10m)*
- [ ] **10.H.5** — ★Frontend changed → this release needs `FE_CACHEBUST` *(2m)*
- [ ] **10.H.6** — Run the gate *(10m)*

### 10.I Hygiene
- [ ] **10.I.1** — Deal with the 1042 untracked `.bak*` files *(20m)*
- [ ] **10.I.2** — ★`./scripts/prune-safe.sh 168h` — **never** `docker builder prune -af` *(10m)*

---

# PHASE 11 — tests and durability *(≈2h, 13 steps)*

### 11.A The 7 missing test files *(one step each — copy, run, record)*
- [ ] **11.A.1** — `tests/unit/test_chart_spec.py` *(10m)*
- [ ] **11.A.2** — `tests/unit/test_data_preview_cell_cap.py` *(10m)*
- [ ] **11.A.3** — `tests/unit/test_powerbi_item_level_access.py` *(10m)*
- [ ] **11.A.4** — `tests/e2e/test_instruction_activity.py` *(10m)*
- [ ] **11.A.5** — `tests/e2e/rbac/test_instruction_pending_carryover.py` *(10m)*
- [ ] **11.A.6** — `tests/e2e/test_scheduled_refresh_archive_guard.py` *(10m)*
- [ ] **11.A.7** — `tests/e2e/test_mcp_forwarding_eu_live.py` *(10m)*
- [ ] **11.A.8** — ★Record which fail **before** fixing anything — the failures are the finding *(15m)*

### 11.B The inner-loop hole
- [ ] **11.B.1** — Pick the fast non-fork subset: query timeout, cancellation, concurrency *(15m)*
- [ ] **11.B.2** — Time it *(10m)* → must stay usable per-change
- [ ] **11.B.3** — Document it as part of the inner loop in `CLAUDE.md` *(10m)*

### 11.C The 4 unexplained failures *(one step each)*
- [ ] **11.C.1** — `test_resource_permissions_only_data_source_in_mvp` — asserts `{data_source, connection}`, we added `project`. Likely a stale test *(15m)*
- [ ] **11.C.2** — `test_artifact_key_roundtrips_through_fernet` *(15m)*
- [ ] **11.C.3** — `test_real_org_settings_defaults_are_adaptive` *(15m)*
- [ ] **11.C.4** — `test_data_model_to_code_prompt_carries_time_filter_rules` *(15m)*

### 11.D Restore
- [ ] **11.D.1** — `SECURITY.md` *(5m)*
- [ ] **11.D.2** — `scripts/version-auto-update/version-auto-update.sh` *(5m)*

---

# PHASE 12 — latency *(≈3h, 11 steps)*

★Every measurement on an **idle** container. The P0 suite had `dash-app` at 90.6% CPU — every timing from that window is void.

### 12.A Baseline
- [ ] **12.A.1** — Confirm nothing heavy is running *(2m)*
- [ ] **12.A.2** — Run one representative question, capture the full trace *(15m)*
- [ ] **12.A.3** — Break the total into steps *(15m)* → a table

### 12.B Prompt caching — the biggest unknown
- [ ] **12.B.1** — Read `anthropic_client.py`; are cache breakpoints set? *(20m)* → yes/no
- [ ] **12.B.2** — If no: check whether `ContextHub`'s static/warm split already gives a stable prefix *(20m)*
- [ ] **12.B.3** — Estimate the saving from real token counts before building anything *(20m)* → a number
- [ ] **12.B.4** — Only then decide whether to implement *(5m)*

### 12.C `inspect_data` — 35.3s, half the total
- [ ] **12.C.1** — Instrument it: warehouse time vs our own overhead *(25m)*
- [ ] **12.C.2** — Run against a real connection *(10m)* → split confirmed
- [ ] **12.C.3** — If ours: identify what is cacheable *(20m)*

### 12.D Close
- [ ] **12.D.1** — Check what a trivial turn routes to (`model_router.py`) *(15m)*
- [ ] **12.D.2** — Re-run 12.A.2 after all fixes *(15m)*
- [ ] **12.D.3** — Write the before/after table *(15m)*

---

# PHASE 13 — release and durability *(≈1.5h, 10 steps)*

### 13.A Ship
- [ ] **13.A.1** — `VERSION` → `0.0.518.1` *(1m)*
- [ ] **13.A.2** — CHANGELOG covering all 8 upstream versions *(25m)*
- [ ] **13.A.3** — Repeat 5.A.3 → 5.D.2 *(45m)*
- [ ] **13.A.4** — ★Prove the frontend actually shipped: compare chunk filename/md5 between images — **never** grep the bundle for an identifier (minifiers rename locals) *(10m)*

### 13.B Docs
- [ ] **13.B.1** — Update `CLAUDE.md`: measured-base method, untagged-release trap, `/src` vs `dash-app`, FORK-1/2/3/4 *(25m)*
- [ ] **13.B.2** — Correct the `0.0.500` note — it **was** bumped upstream (`1fc86166`) and has a changelog entry; it was never *tagged* *(5m)*
- [ ] **13.B.3** — Check `CLAUDE.md` is still near 200 lines *(5m)*
- [ ] **13.B.4** — Append the session to `CLAUDE-SESSIONS.md`, not `CLAUDE.md` *(20m)*

### 13.C Protect the key
- [ ] **13.C.1** — ★**Ask the user**: track the three doc files and keep only `.env` out, or a scheduled backup, or a secret store? *(5m)* → losing `.env` makes every stored secret undecryptable
- [ ] **13.C.2** — Implement the choice *(25m)*
- [ ] **13.C.3** — Confirm `.env` itself is still gitignored *(2m)*

---

# Totals

| phase | steps | est | blocked by |
|---|---|---|---|
| 1 | 19 | 1.5h | — **start here** |
| 2 | 11 | 2h | ⛔ 2.A.1 — your decision |
| 3 | 12 | 2h | — |
| 4 | 15 | 1.5h | 4.C.1 before 4.C.3 |
| 5 | 12 | 1h | phases 1–4 |
| 6 | 13 | 1h | — runs alongside 1–4 |
| 7 | 17 | 3h | ⛔ 6.D.3 |
| 8 | 5 | 1h | phase 7 |
| 9 | 9 | 1h | release 2 |
| 10 | 40 | 6h | 10.B after phase 2; 10.C after 10.B |
| 11 | 13 | 2h | after the port |
| 12 | 11 | 3h | idle container |
| 13 | 10 | 1.5h | last |

**≈187 steps, ≈26h.** Longest single step: 35 minutes.
