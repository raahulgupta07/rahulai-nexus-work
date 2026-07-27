# Defect register — end-to-end agent testing

Defects found by driving the live app as a real user (`raahulgupta07@gmail.com` on :8095), one connector at a time, easy-to-complex questions plus a document report, a PowerPoint deck and a dashboard for each.

Every defect carries the **test that found it** and the **regression test that pins it**, so a fix can never quietly come undone. A defect is only closed when its regression test has been shown to **fail on the pre-fix code** — a test that passes on both versions proves nothing.

Build under test: **`0.0.489`**, image `ba48d9d245b7`.

> **Deploy state:** the DEF-001 and DEF-002 fixes are live on `bow-app-cai` via `docker cp` + restart (backend-only, no frontend change) and are in the source tree. They are **not yet baked into an image** — the container is ahead of `cityagentinsights:0.0.489`. Bake before any release, tagging the current image first.

## Verdict vocabulary

`PASS` · `WRONG` (answered confidently, number is wrong) · `FAIL` (errored, user sees it) · `REFUSED` (declined; may be correct) · `DEGRADED` (right answer, but only after failed attempts)

**`WRONG` ranks above `FAIL`.** A crash is visible to the user; a wrong total gets pasted into a board deck.

## Defect classes

| class | meaning | urgency |
|---|---|---|
| 1 | **NEW — caused by the 0.0.489 port** | urgent |
| 2 | pre-existing in our fork | scheduled |
| 3 | upstream's | scheduled, report upstream |
| 4 | environment / data (expired token, empty table) | not a code defect |
| 5 | not a bug — correct refusal | closed on sight |

---

## Register

| ID | title | found by | class | severity | status |
|---|---|---|---|---|---|
| [DEF-001](#def-001) | Exported doc PDFs print raw `{{viz:…}}` tokens instead of charts | E2E-P1.5 | 2 | medium — every doc PDF with a chart | **FIXED** |
| [DEF-002](#def-002) | Dashboard preview built from a silently truncated 100-row slice | E2E-P1.7 | 3 (unconfirmed) | medium — wrong figures fed back to the model | **FIXED** |

---

## DEF-001

**Exported doc PDFs print raw `{{viz:<uuid>}}` tokens where charts belong.**

*Found by* `E2E-P1.5` — City Mart Retail document report, PDF export.
*Regression test* `backend/tests/unit/test_def001_doc_pdf_viz_placeholder.py` (8 tests).
*Fix* `backend/app/services/pdf_export_service.py` — `_VIZ_EMBED` pattern + shared `_VIZ_PLACEHOLDER`.
*Backup* `pdf_export_service.py.bak-testfix-20260726`.

### What happened

A 4-page business report exported cleanly — valid PDF, real text, correct numbers — but printed five literal `{{viz:4045d5a6-43fa-464b-8889-23cd2c34684b}}` tokens in place of its charts.

### Root cause

`_strip_viz` matched two syntaxes that never occur in real doc markdown:

```python
_VIZ_FENCE = re.compile(r"```(?:viz|chart|visualization)[^\n]*\n.*?```", ...)  # ```viz fences
_VIZ_TAG   = re.compile(r"<\s*(?:viz|visualization|chart)\b[^>]*>", ...)       # <viz> tags
```

The syntax `create_doc`/`edit_doc` actually emit — and `DocViewer`/`DocEditor` render — is **`{{viz:<uuid>}}`**. Neither pattern matched it, so the intended *"chart omitted — view in app"* note never fired and the raw token reached the customer-facing PDF.

### Class

**Class 2 — ours.** `pdf_export_service.py` is our own addition (2026-07-23, "Real PDF export for doc artifacts"), untouched since, and not in the 489 port surface. Present since the file was written.

### Verification

- 4 of 8 regression tests **fail on the pre-fix image**, 4 pass (those 4 guard the neighbouring behaviour the new pattern must not disturb: prose, tables, `{braces}`, fence and tag forms).
- All 8 pass after the fix.
- **Live**: the *same* artifact re-exported → `raw {{viz tokens: 0`, `chart-omitted placeholders: 5`, still HTTP 200 and 4 pages.

### Note

Server-side PDF still renders charts as a *placeholder*, not an image — that is the documented limitation of this lane (charts are a frontend render; the browser print path captures them). This defect was that the placeholder never appeared at all.

---

## DEF-002

**Dashboard preview and codegen context were built from a silently truncated 100-row slice, described as complete.**

*Found by* `E2E-P1.7` — City Mart Retail dashboard: the stored preview disagreed with a real browser render.
*Regression test* `backend/tests/unit/test_def002_artifact_row_fidelity.py` (13 tests).
*Fix* `backend/app/ai/tools/implementations/create_artifact.py` — rows carried in full; `_PROMPT_STATS_ROWS` / `_RENDER_ROW_LIMIT` per consumer; `stats_from_sample` and `rows_truncated` declared.
*Backup* `create_artifact.py.bak-testfix-20260726`.

### What happened

The dashboard's stored preview showed **TOTAL NET SALES 1.4B · BASKETS 36K · City Mart 652.3M** with a monthly trend ending at Oct 2023. The live dashboard, rendered in a real browser, showed **5.1B · 130K · City Mart 2.3B** across all 36 months — all exact against ground truth.

1.4B is precisely the sum of Jan–Oct 2023, and 10 months × 5 banners × 2 member types = **exactly 100 rows**.

### Root cause

`create_artifact.py:654` truncated rows at fetch time:

```python
rows = (step_data.get("rows") or [])[:100] if step_data else []
```

and line 685 then reported `"row_count": len(rows)` — the truncated length. Three consequences, none of them announced:

1. **`row_count` was a lie** — a 360-row dataset was reported to the model as 100 rows, under a prompt that says *"(Full sample data included above)"* and instructs it to *"use row_count"*.
2. **Column stats** (`min`/`max`/`sample_values`) were computed from the prefix and presented as whole-dataset ranges.
3. **The preview screenshot and the stored thumbnail** were rendered from the truncated list injected as `window.ARTIFACT_DATA`, so the preview computed its KPI tiles over 100 of 360 rows.

The generated SQL was correct, and the step stored all 360 rows — so nothing upstream of the artifact was at fault. The live frontend passes `step.data.rows` uncapped (`ArtifactFrame.vue:1038`), which is why the browser was right and the preview was wrong.

### Who saw the wrong numbers

Not the user directly — **no frontend component renders `screenshot_base64`**. But the model does:

- `read_artifact.py:538` feeds it to the model as a **vision image**, so the agent can read back its own dashboard and see 1.4B where the user sees 5.1B.
- `create_artifact.py:397` feeds it to the **self-heal** path as *"the broken render"*, so a correct dashboard can be "repaired" against a misleading picture.

The live artifact was right this time because the model chose to aggregate at runtime rather than hardcode from the sample. Nothing guaranteed that choice.

### Fix

Rows are carried in **full** on the visualization entry; each consumer takes its own slice and declares it:

| consumer | budget | declared as |
|---|---|---|
| codegen prompt stats | `_PROMPT_STATS_ROWS = 100` | `stats_from_sample: 100` |
| codegen prompt sample | 5 rows (unchanged) | `sample_rows` |
| headless preview / thumbnail | `_RENDER_ROW_LIMIT = 20000` | `rows_truncated` + `rows_total` |
| live frontend | uncapped (unchanged) | — |

`row_count` is now the true count in every case, including privacy mode. Prompt size is unchanged — the fix does **not** inline 360 rows into the prompt.

### Class

**Class 3 (unconfirmed) — upstream's or longstanding.** `create_artifact.py` is not in the 489 port surface, so this is **not** a 489 regression. Assigning 3 versus 2 needs a diff against the v0.0.489 reference tree, which is no longer on disk — worth doing before reporting upstream.

### Verification

- 13 regression tests pass on the fix. They cannot even import on the pre-fix code (the constants don't exist), so the pre-fix proof is the **captured live artifact** plus the `[:100]` line, both recorded above — the seam being absent pre-fix is itself confirmed (`_render_visualizations` count = 0).
- Neighbouring suite `test_image_generation.py` + both defect suites: **38 passed**.
- **Live**: a fresh dashboard run on the fixed code produced a **208-row** dataset — above the old 100-row cap, so the bug would have bitten. The stored preview now shows all 12 months of its stated scope with exact figures: 1,700,404,461 net sales, 43,270 baskets, ABV 39,298, and every banner exact (775.59M / 447.21M / 310.79M / 112.69M / 54.13M). No clipped series, no prefix totals.

### The pattern worth remembering

This is the third defect of the same shape in this codebase: **a payload that carries a partial or historical view without saying so, and a consumer that reads it as the whole truth.** The others were the codegen trim that trusted the first `return df`, and `inspect_data` reading an accumulated error history as the outcome. When a payload can be partial, the payload must say it is partial.

---

## Test index

| test id | what it drives | connector |
|---|---|---|
| E2E-P1.1 | L1 row count, L2 aggregate | City Mart Retail |
| E2E-P1.2 | L3 group + rank, L4 two-table join | City Mart Retail |
| E2E-P1.3 | L5 time series, member split | City Mart Retail |
| E2E-P1.4 | L6 multi-condition festival uplift | City Mart Retail |
| E2E-P1.5 | document report + PDF export | City Mart Retail |
| E2E-P1.6 | PowerPoint deck + export | City Mart Retail |
| E2E-P1.7 | dashboard + live browser render | City Mart Retail |
| E2E-P2.* | same ladder + three artifacts | Microsoft Fabric |
| E2E-P3.* | same ladder + three artifacts | Power BI |

Ladder rungs: **L1** lookup · **L2** aggregate · **L3** group/sort · **L4** join · **L5** time window · **L6** multi-condition analytical.

## Phase results

### Phase 1 — City Mart Retail (DuckDB, shared connector)

All six rungs **PASS** with exact numbers and zero failed attempts (L1 20s · L2 23s · L3 25s · L4 26s · L5 29s · L6 74s). Document report, deck and dashboard all **PASS** on content, not merely on producing a file: the deck's derived figures (YoY −1.4%, units 224.1k vs 226.1k, April peak 187,254,718) and the report's cross-checks (period totals summing to the annual figure) were verified against ground truth queried directly from the source before the agent was asked. Zero tracebacks.

### Phase 2 — Microsoft Fabric

Not yet run.

### Phase 3 — Power BI

Not yet run.
