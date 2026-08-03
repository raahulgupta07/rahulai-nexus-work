# File support — what actually happens, measured

**Measured 2026-08-03** against image `cityagentinsights:0.0.510.14`, repo mounted
read-only at `/src`. Every row is the product's own code called on a real file of
that format — LibreOffice and the same libraries the app ships produced the
fixtures, so nothing here is a renamed `.txt` measuring an extension check.

Reproduce: `scratchpad/mkfixtures.py` builds the corpus, `scratchpad/probe.py`
calls `extract_document_text`, `doc_text_is_usable`, `doc_text_looks_garbled`,
`render_file_images`, and reads the six extension registries out of the running
modules. Raw output in `scratchpad/probe.json`.

**Read the two content columns as different questions.** `read_file` is what the
model gets when it opens the file. *Generated code* is what happens when the file
lands in `excel_files[i]` and the model writes pandas against it — and that is
where the damage is, because a wrong answer there arrives with no error attached.

| file | ext | `read_file` outcome | generated code | s3 | net_dir | gdrive | graph |
|---|---|---|---|---|---|---|---|
| `adv_corrupt.docx` | docx | **nothing** | blocked → `read_file` | doc | doc | doc | doc |
| `adv_garbled.pdf` | pdf | garbled → **vision** | blocked → `read_file` | doc | doc | doc | doc |
| `adv_imageonly.pdf` | pdf | vision only | blocked → `read_file` | doc | doc | doc | doc |
| `adv_oneline.docx` | docx | **text** | blocked → `read_file` | doc | doc | doc | doc |
| `sample.bmp` | bmp | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.csv` | csv | plain text | `pd.read_csv(excel_files[i].path)` | tabular | tabular | text | tabular |
| `sample.doc` | doc | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.docx` | docx | **text** | blocked → `read_file` | doc | doc | doc | doc |
| `sample.eml` | eml | **nothing** | **SILENT GARBAGE** — FRAME 6x1 | - | - | - | - |
| `sample.html` | html | plain text | **SILENT GARBAGE** — FRAME 0x1 | text | text | text | text |
| `sample.jpg` | jpg | vision only | blocked → `read_file` | - | - | - | - |
| `sample.json` | json | plain text | `pd.read_json(excel_files[i].path)` | text | text | text | text |
| `sample.log` | log | plain text | `read_text(excel_files[i])` | text | text | text | text |
| `sample.md` | md | plain text | `read_text(excel_files[i])` | text | text | text | text |
| `sample.ndjson` | ndjson | plain text | `pd.read_json(excel_files[i].path, lines=True)` | text | - | - | - |
| `sample.odp` | odp | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.odt` | odt | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.parquet` | parquet | **nothing** | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.pdf` | pdf | **text** | blocked → `read_file` | doc | doc | doc | doc |
| `sample.png` | png | vision only | blocked → `read_file` | - | - | - | - |
| `sample.ppt` | ppt | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.pptx` | pptx | **text** | blocked → `read_file` | doc | doc | doc | doc |
| `sample.rtf` | rtf | vision only | **SILENT GARBAGE** — FRAME 157x1 | - | - | - | - |
| `sample.tiff` | tiff | vision only | **unguarded** — raises UnicodeDecodeError | - | - | - | - |
| `sample.tsv` | tsv | plain text | `pd.read_csv(excel_files[i].path, sep='\t')` | tabular | tabular | text | tabular |
| `sample.txt` | txt | plain text | `read_text(excel_files[i])` | text | text | text | text |
| `sample.webp` | webp | vision only | blocked → `read_file` | - | - | - | - |
| `sample.xls` | xls | plain text | `pd.read_excel(excel_files[i].path, sheet_name=0)` | tabular | tabular | - | tabular |
| `sample.xlsx` | xlsx | plain text | `pd.read_excel(excel_files[i].path, sheet_name=0)` | tabular | tabular | - | tabular |
| `sample.xml` | xml | plain text | **SILENT GARBAGE** — FRAME 0x1 | text | text | - | - |
| `sample.yaml` | yaml | plain text | **SILENT GARBAGE** — FRAME 4x1 | text | text | text | text |
| `sample.zip` | zip | **nothing** | **unguarded** — raises ValueError | - | - | - | - |

`-` in a connector column means that connector will not surface the file's
content at all. `doc` means it routes through `extract_document_text`.

## The four adversarial fixtures

| fixture | what it defeats | outcome |
|---|---|---|
| `adv_oneline.docx` | `MIN_USABLE_DOC_CHARS = 16` — the document is 10 characters | **passes**: `doc_text_is_usable(text, "docx") == True`. The OOXML exemption at `_document_text.py:60` holds. |
| `adv_garbled.pdf` | length gates — extraction "succeeds" with 92 chars of glyph soup | **caught**: `doc_text_looks_garbled == True`, and the vision fallback renders 1 page. |
| `adv_imageonly.pdf` | text extraction — there is no text layer | **honest**: 0 chars, `usable == False`, vision renders 1 page. |
| `adv_corrupt.docx` | the reader itself — the zip is truncated | **honest but a dead end**: 0 chars *and* `render_file_images` returns `([], 0)`. No route to the content and nothing says so. |

## The six registries, as they actually are

Every one of these answers a version of "what can we do with this extension",
and no two agree.

| registry | `file:line` | members |
|---|---|---|
| `DOC_EXTS` | `_document_text.py:28` | pdf docx pptx |
| `_READERS` | `_source_files.py:24` | csv tsv json ndjson jsonl xlsx xls txt log md |
| `_NOT_LOADABLE` | `_source_files.py:40` | pdf docx pptx png jpg jpeg gif webp |
| `_NOT_LOADABLE` (step) | `step_files.py:62` | *identical to the above — a second copy* |
| `_CODEGEN_UNREADABLE_EXTS` | `coder.py:284` | *identical again — a third copy* |
| `_RENDERABLE_IMAGE_EXTS` | `_file_tool_common.py:551` | png jpg jpeg gif webp **bmp tiff tif** |
| `CONVERTIBLE_EXTS` | `_office_convert.py:41` | docx doc pptx ppt **odt odp rtf** |

The two bolded groups are the whole problem: `bmp tiff tif odt odp rtf doc ppt`
are known to the *rendering* side and unknown to all three *blocking* copies.

### Connector drift

| | `TEXT_EXTS` differences |
|---|---|
| `s3_client.py:60` | the only one that reads `ndjson`, `jsonl` |
| `network_dir_client.py:60` | reads `xml py sql`, but **not** `ndjson`/`jsonl` |
| `google_drive_client.py:35` | puts `csv`/`tsv` in TEXT and has **no** TABULAR set; no `xml py sql` |
| `graph_drive_client.py:68` | narrowest — no `xml py sql`, no `ndjson jsonl` |

The same `.ndjson` is content from S3 and opaque from a network directory.

## Vision and PII — the four-condition sweep

`read_file` withholds content on **both** axes. `allow_llm_see_data` gates the
observation body for every content type (`read_file.py:343`, `:467`, `:841`) and
`supports_vision AND allow_llm_see_data` gates the image route (`:586`,
`:603-604`, `:666`).

| `supports_vision` | `allow_llm_see_data` | pdf/docx/pptx with a text layer | doc rtf odt odp ppt | images | scanned pdf |
|---|---|---|---|---|---|
| ✅ | ✅ | text | vision | vision | vision |
| ✅ | ❌ | **summary line only** | **nothing** | **nothing** | **nothing** |
| ❌ | ✅ | text | **nothing** | **nothing** | **nothing** |
| ❌ | ❌ | **summary line only** | **nothing** | **nothing** | **nothing** |

**Not a live defect on this install** — measured against the running database:
`x-ai/grok-4.5` and `openai/gpt-5.6-luna` both carry `supports_vision = true`,
and Main Org has `allow_llm_see_data.value = true`, `pii_protection.enabled =
false`. It is a live *trap*: turning on PII protection removes the only route to
five document formats and every image, with no message saying so.
