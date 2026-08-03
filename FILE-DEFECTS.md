# File-handling defects — every one cites a measured cell

Companion to [FILE-SUPPORT-MATRIX.md](FILE-SUPPORT-MATRIX.md). Measured
2026-08-03 against `cityagentinsights:0.0.510.14`. **A defect with no row in the
matrix does not belong in this file**, and Phase 7's scope is whatever survives
here — that rule exists because the previous pass produced a finding that had to
be retracted for having no measurement behind it.

Ordered by what a wrong answer costs, not by how hard it is to fix.

---

## D1 — Eight formats reach `pd.read_csv`, and five of them return a frame

**Severity: critical.** This is the only class here that produces a confident
wrong number instead of an error.

Measured — generated code was handed each file with no reader and no warning:

| fixture | `pd.read_csv` result |
|---|---|
| `sample.rtf` | **`FRAME 157x1`** — 157 rows of RTF control words as data |
| `sample.eml` | **`FRAME 6x1`** — mail headers as data |
| `sample.yaml` | **`FRAME 4x1`** |
| `sample.html` | `FRAME 0x1` |
| `sample.xml` | `FRAME 0x1` |
| `sample.doc` `.odt` `.odp` `.ppt` `.bmp` `.tiff` `.parquet` | raises `UnicodeDecodeError` |
| `sample.zip` | raises `ValueError` |

**Deciding line: `_source_files.py:150`.** The directive builder is a chain —

```python
if ext in _NOT_LOADABLE:      ...  # "NOT readable from generated code"
elif ext == "json":           ...
elif ext in _READERS:         ...  # "read with pd.read_csv(...)"
```

— with **no `else`**. An extension in neither set falls off the end and the model
is handed a bare line: the filename, the path, and nothing about how to open it.
`pd.read_csv` is then the obvious guess.

Two more places miss the same eight extensions:

- **`coder.py:332`** — `_impossible_request` refuses only when *every* file is in
  `_CODEGEN_UNREADABLE_EXTS`. A run whose only file is an `.rtf` does not
  trigger it, so the coder is asked to write pandas against a Word file.
- **`coder.py:370`** — the `[NOT loadable in code — use the read_file tool]`
  marker in `<excel_files>` is driven by the same set, so the `.rtf` sits in the
  list looking exactly as loadable as the CSV next to it.

★The comment at `_source_files.py:200-202` already names this exact outcome —
"pointing `pd.read_csv` at a Word document produces either an error or, worse, a
plausible-looking frame of nonsense". It is written on the branch that only fires
for the eight extensions that were already blocked.

---

## D2 — Unknown means "let codegen try"

**Severity: high (this is why D1 exists and will recur).**

The registries are allow-lists of what to *block*, so every format nobody has
thought about yet defaults to readable. Adding `.parquet` support tomorrow does
not add `.ods` to a block list, and the next unlisted binary format arrives with
the same failure. The fix is to invert it: an extension with no reader is refused
unless something says it is readable.

Evidence: 13 of 32 measured fixtures land in that default, and the 5 above prove
the default is not safe.

---

## D3 — `_NOT_LOADABLE` exists three times, byte for byte

**Severity: high (silent divergence).** Read out of the running modules:

```
_source_files.py:40  == step_files.py:62  == coder.py:284
{docx, gif, jpeg, jpg, pdf, png, pptx, webp}
```

They agree **today**. Nothing enforces it — no test asserts equality, and the
comments in two of the three say "mirrors" the other, which is a convention, not
a guard. `step_files._NOT_LOADABLE` decides whether a *refresh* refuses; the
other two decide what the model is told. Divergence means a file the model is
allowed to load in code and the refresh path then rejects, or the reverse.

---

## D4 — `.parquet` has no reader, and the library is already installed

**Severity: medium. Free capability, no new dependency.**

Measured: `pyarrow` imports in the shipped image, and `pandas.to_parquet`
round-trips. `.parquet` appears in **no** registry — not `_READERS`, not
`_NOT_LOADABLE`, not any connector's `TEXT_EXTS`/`TABULAR_EXTS`. A user who
uploads one gets `read_file` → nothing and generated code →
`UnicodeDecodeError`.

Same shape, needing a real decision rather than a line of config:

- `.eml` / `.msg` — `.eml` is stdlib (`email.message`); `.msg` needs
  `extract_msg`, which is **not** in the image. Flag it; never add it silently.
- `.zip` — no reader; currently an unguarded `ValueError`.
- `.ods` — the image has `libreoffice-writer/impress/draw` but **no
  `libreoffice-calc`** and no `odfpy`, so there is no route to a spreadsheet in
  an ODF container at all. Consistent with `CONVERTIBLE_EXTS` carrying no
  spreadsheet format.

---

## D5 — The same file is content from one connector and opaque from another

**Severity: medium.**

| extension | s3 | network_dir | google_drive | graph_drive |
|---|---|---|---|---|
| `.ndjson` / `.jsonl` | text | **–** | **–** | **–** |
| `.xml` / `.py` / `.sql` | text | text | **–** | **–** |
| `.csv` / `.tsv` | tabular | tabular | text | tabular |
| `.xls` / `.xlsx` | tabular | tabular | **–** | tabular |

`google_drive_client.py:35` has no `TABULAR_EXTS` at all — spreadsheets on Drive
are reachable only insofar as `csv`/`tsv` were folded into its TEXT set. Nothing
about a file's content justifies any of these differences; they are four
independently maintained lists.

---

## D6 — `read_file` can vanish from the catalog for project-inherited files

**Severity: medium. ★Code paths proven divergent; the end-to-end outcome is NOT
yet reproduced — treat as a strong hypothesis, not a confirmed incident.**

`app/services/file_scope.py` exists to be the single answer to "which files can
this run read?". Its own docstring records that five call sites used to answer
independently and that the disagreement produced a file the model was told about
and no tool could open.

There is a **sixth** answerer it does not cover. `agent_v2.py:781-784` decides
whether `read_file` and `grep_files` even appear in the tool catalog:

```python
available_capabilities |= capabilities_for_report_files(
    bool(getattr(report, "files", None))
)
```

That reads **one** pool. `file_scope.readable_files` merges **three** — live,
report, and `project_files`, which the docstring notes live in
`project_file_association`, a different table. So a report whose files are all
inherited from its folder has `report.files == []`, and the capability gate
concludes the run has no files — while the `<files>` catalog still advertises
them.

The parity test `tests/unit/fork/test_file_surface_parity.py` enforces the
invariant across *resolvers*. `tests/unit/test_read_file_session_files.py:338`
tests `capabilities_for_report_files(has_files=True)` — the function, given the
answer. Nothing tests that `has_files` is computed from the right pool.

**To confirm:** open a report in a project whose files are folder-inherited only,
and check whether `read_file` is in the catalog.

---

## D7 — A corrupt document has no route and says nothing

**Severity: low.** `adv_corrupt.docx` (a real docx truncated at half its length):
`extract_document_text` returns `""` *and* `render_file_images` returns
`([], 0)`. Both fallbacks are exhausted, and the failure is a `logger.debug` at
`_document_text.py:156` — deliberately debug-level so a directory scan is not
noisy, which is right for search and wrong for a file the user explicitly opened.

---

## D8 — Turning on PII protection silently removes five document formats

**Severity: latent — not live on this install.** See the four-condition sweep in
the matrix. `allow_llm_see_data = false` withholds the observation body on every
content path *and* disables the image route, which is the **only** route to
`.doc .rtf .odt .odp .ppt`, every image, and any scanned PDF. The model receives
a summary line and no content, with nothing explaining why.

Measured live: both models have `supports_vision = true`, Main Org has
`allow_llm_see_data.value = true` and `pii_protection.enabled = false`. So this
is a trap waiting on a settings toggle, not a current failure.

---

## What is NOT broken

Worth recording, because three of these were suspected:

- **The one-line docx exemption holds.** `adv_oneline.docx` is 10 characters and
  `doc_text_is_usable(text, "docx")` returns `True` — the OOXML carve-out at
  `_document_text.py:60` does what it was written for.
- **Garble detection fires and escalates.** `adv_garbled.pdf` (real PDF, every
  ToUnicode CMap stripped) is flagged, and the vision fallback renders.
- **`.xls` reads.** `xlrd 2.0.2` ships, and a real OLE2 BIFF8 workbook loads
  through `pd.read_excel`.
- **Office documents do reach vision.** `.doc .rtf .odt .odp .ppt` each rendered
  1 page through the LibreOffice route, so they are readable — just never from
  generated code, and never without vision.
