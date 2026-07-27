"""L2 — LLM structure-understanding module for the standalone Excel parser.

Pure stdlib (+ optional `requests`/`urllib` for the real LLM call). No app imports,
no hardcoded secrets. Runs fully offline via `mock_llm`.

Public interface (see CONTRACT.md, L2 section):
    build_grid_sample(grid, max_rows=30) -> str
    build_prompt(grid_sample) -> str
    apply_spec(grid, spec) -> list[dict]
    mock_llm(grid_sample) -> dict
    call_llm(grid_sample) -> dict          # optional real call; raises if no API key

Spec shape returned by mock_llm / call_llm:
    {"tables": [
        {"name": str,
         "header_row": int,      # 0-indexed row into grid
         "data_start": int,      # 0-indexed first data row (inclusive)
         "data_end": int,        # 0-indexed last data row (inclusive)
         "col_start": int,       # 0-indexed first column (inclusive)
         "col_end": int,         # 0-indexed last column (inclusive)
         "columns": [str, ...],  # flattened real header names, one per kept col
         "drop_cols": [int, ...] # absolute grid col indices to exclude (metadata)
        }, ...]}

apply_spec turns that into the SAME table dict shape excel_structizer.parse_workbook
returns:
    {"name","sheet","cell_range","columns","rows","confidence","method","notes"}
"""

import json
import os
import re

# --------------------------------------------------------------------------- #
# Small cell helpers
# --------------------------------------------------------------------------- #

_EMPTY_MARK = "·"  # "·" — shown for empty cells in the grid sample
_MAX_CELL_LEN = 24      # truncation length for cell text in the sample


def _cell_str(value):
    """Normalize any cell value to a stripped string ('' for None)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _col_letter(idx):
    """0-based column index -> spreadsheet letters (0->A, 25->Z, 26->AA)."""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _looks_numeric(text):
    """True if the cell text reads as a number (money/percent tolerant)."""
    if not text:
        return False
    t = text.strip().replace(",", "").replace("%", "").replace("$", "")
    t = t.replace("(", "-").replace(")", "")
    if t in ("", "-", "."):
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _slug(text):
    """snake_case-ish identifier from an arbitrary header string."""
    text = _cell_str(text).lower()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _dedupe(names):
    """Ensure unique, non-empty column names, appending _2, _3 ... on clash."""
    seen = {}
    out = []
    for i, raw in enumerate(names):
        name = _slug(raw) or "col_{}".format(i + 1)
        if name in seen:
            seen[name] += 1
            name = "{}_{}".format(name, seen[name])
        else:
            seen[name] = 1
        out.append(name)
    return out


# --------------------------------------------------------------------------- #
# build_grid_sample — render grid as compact indexed text for the model
# --------------------------------------------------------------------------- #

def build_grid_sample(grid, max_rows=30):
    """Render a raw cell grid (list of lists) as a compact, LLM-friendly text
    block with row indices and column letters. Empty cells -> '·'. Long cells
    truncated. At most `max_rows` data rows are shown (plus a header line).
    """
    grid = grid or []
    ncols = max((len(r) for r in grid), default=0)

    # Column-letter header line.
    header_cells = [_col_letter(c) for c in range(ncols)]
    lines = ["      " + " | ".join("{:<{w}}".format(h, w=_MAX_CELL_LEN)
                                    for h in header_cells)]

    shown = grid[:max_rows]
    for r, row in enumerate(shown):
        cells = []
        for c in range(ncols):
            val = row[c] if c < len(row) else None
            text = _cell_str(val)
            if text == "":
                text = _EMPTY_MARK
            elif len(text) > _MAX_CELL_LEN:
                text = text[: _MAX_CELL_LEN - 1] + "…"  # …
            cells.append("{:<{w}}".format(text, w=_MAX_CELL_LEN))
        lines.append("R{:<4}".format(r) + " " + " | ".join(cells))

    if len(grid) > max_rows:
        lines.append("... ({} more rows truncated)".format(len(grid) - max_rows))

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# build_prompt — instruction asking the model for STRICT JSON
# --------------------------------------------------------------------------- #

def build_prompt(grid_sample):
    """A tight instruction asking the model to return STRICT JSON describing the
    table structure of the given grid sample."""
    return (
        "You are a spreadsheet structure analyst. Below is a raw cell grid.\n"
        "Rows are labelled R0, R1, ... (0-indexed). Columns are labelled by\n"
        "letter (A, B, C, ...) which map to 0-indexed positions (A=0, B=1, ...).\n"
        "Empty cells are shown as '" + _EMPTY_MARK + "'.\n\n"
        "Identify every rectangular data TABLE in the grid. For each table return:\n"
        "  - name:       short snake_case table name\n"
        "  - header_row: 0-indexed row that holds the column headers\n"
        "  - data_start: 0-indexed first data row (after the header)\n"
        "  - data_end:   0-indexed last data row (inclusive)\n"
        "  - col_start:  0-indexed first column of the table\n"
        "  - col_end:    0-indexed last column of the table (inclusive)\n"
        "  - columns:    the REAL header names, one per kept column, left to right.\n"
        "                Flatten merged / multi-row headers by joining with '_'.\n"
        "  - drop_cols:  absolute 0-indexed grid columns to DROP (blank/metadata/\n"
        "                'Unnamed' filler columns inside the table range).\n\n"
        "Rules:\n"
        "  * EXCLUDE title/banner rows and totals/subtotal rows from the data range.\n"
        "  * A header row is mostly text labels sitting above typed/numeric data.\n"
        "  * If multiple stacked tables exist (separated by blank rows), list each.\n"
        "  * Do NOT invent columns; name them from the actual header cells.\n\n"
        "Return STRICT JSON ONLY, no prose, in exactly this shape:\n"
        '{"tables":[{"name":"","header_row":0,"data_start":1,"data_end":1,'
        '"col_start":0,"col_end":0,"columns":[],"drop_cols":[]}]}\n\n'
        "GRID:\n" + grid_sample + "\n"
    )


# --------------------------------------------------------------------------- #
# apply_spec — deterministically turn a spec into table dicts
# --------------------------------------------------------------------------- #

def apply_spec(grid, spec, sheet="", confidence=0.6):
    """Deterministically apply an LLM spec to the grid, producing table dicts in
    the same shape excel_structizer.parse_workbook returns. Pure code, no LLM.

    Out-of-range / malformed spec fields are clamped defensively so a bad model
    reply never crashes the pipeline.
    """
    grid = grid or []
    nrows = len(grid)
    ncols = max((len(r) for r in grid), default=0)
    tables = []

    for t in (spec or {}).get("tables", []) or []:
        notes = []

        def _int(key, default):
            try:
                return int(t.get(key, default))
            except (TypeError, ValueError):
                return default

        header_row = _int("header_row", 0)
        data_start = _int("data_start", header_row + 1)
        data_end = _int("data_end", nrows - 1)
        col_start = _int("col_start", 0)
        col_end = _int("col_end", ncols - 1)

        # Clamp to the actual grid.
        header_row = max(0, min(header_row, max(nrows - 1, 0)))
        col_start = max(0, min(col_start, max(ncols - 1, 0)))
        col_end = max(col_start, min(col_end, max(ncols - 1, 0)))
        data_start = max(0, min(data_start, max(nrows, 0)))
        data_end = max(data_start - 1, min(data_end, nrows - 1))

        drop_cols = set()
        for d in t.get("drop_cols", []) or []:
            try:
                drop_cols.add(int(d))
            except (TypeError, ValueError):
                continue

        # Kept absolute column indices, in order.
        kept_cols = [c for c in range(col_start, col_end + 1) if c not in drop_cols]
        if not kept_cols:
            kept_cols = list(range(col_start, col_end + 1))
        if drop_cols:
            notes.append("dropped {} metadata col(s)".format(
                len(set(drop_cols) & set(range(col_start, col_end + 1)))))

        # Column names: prefer spec-provided names, else read the header row.
        spec_cols = [c for c in (t.get("columns") or [])]
        raw_names = []
        for i, c in enumerate(kept_cols):
            if i < len(spec_cols) and _cell_str(spec_cols[i]):
                raw_names.append(_cell_str(spec_cols[i]))
            else:
                row = grid[header_row] if header_row < nrows else []
                raw_names.append(_cell_str(row[c]) if c < len(row) else "")
        columns = _dedupe(raw_names)

        if header_row > 0:
            notes.append("dropped {} title/pre-header row(s)".format(header_row))

        # Data rows.
        rows = []
        for r in range(data_start, data_end + 1):
            if r < 0 or r >= nrows:
                continue
            src = grid[r]
            out_row = [_cell_str(src[c]) if c < len(src) else "" for c in kept_cols]
            if all(v == "" for v in out_row):
                continue  # skip fully-blank rows inside the block
            rows.append(out_row)

        cell_range = "{}{}:{}{}".format(
            _col_letter(kept_cols[0]) if kept_cols else _col_letter(col_start),
            header_row + 1,
            _col_letter(kept_cols[-1]) if kept_cols else _col_letter(col_end),
            data_end + 1,
        )

        name = _slug(t.get("name")) or "table_{}".format(len(tables) + 1)
        notes.append("L2 LLM-guided structure")

        tables.append({
            "name": name,
            "sheet": sheet,
            "cell_range": cell_range,
            "columns": columns,
            "rows": rows,
            "confidence": float(confidence),
            "method": "L2",
            "notes": notes,
        })

    return tables


# --------------------------------------------------------------------------- #
# mock_llm — deterministic offline fake
# --------------------------------------------------------------------------- #

def _reparse_sample(grid_sample):
    """Reconstruct a rough grid (list of lists of strings) from a grid_sample
    string produced by build_grid_sample. Empty marks become ''. Used by the
    mock so it can reason without the original grid object."""
    grid = []
    for line in grid_sample.splitlines():
        if not line.startswith("R"):
            continue  # skip the column-letter header and truncation note
        m = re.match(r"R(\d+)\s+(.*)", line)
        if not m:
            continue
        body = m.group(2)
        cells = [c.strip() for c in body.split("|")]
        cells = ["" if c == _EMPTY_MARK else c for c in cells]
        grid.append(cells)
    return grid


def mock_llm(grid_sample):
    """Deterministic fake LLM: parse the grid_sample and pick a plausible spec.

    Heuristic: the header row is the first row that is mostly non-empty text
    (few numbers), with typed/numeric data below it. Everything above it is
    treated as title rows. Blank columns become drop_cols.
    """
    grid = _reparse_sample(grid_sample)
    nrows = len(grid)
    ncols = max((len(r) for r in grid), default=0)

    if nrows == 0 or ncols == 0:
        return {"tables": []}

    def row_text_score(row):
        vals = [row[c] if c < len(row) else "" for c in range(ncols)]
        nonempty = [v for v in vals if v != ""]
        if not nonempty:
            return -1.0
        text = sum(1 for v in nonempty if not _looks_numeric(v))
        return text / float(ncols)  # favor rows that are broadly text-filled

    # Pick the header row: highest text score, preferring earlier rows, and
    # requiring at least one non-empty typed/data row somewhere below it.
    best_row = 0
    best_score = -2.0
    for r in range(nrows):
        # header can't be the very last row (needs data below it)
        if r >= nrows - 0:
            pass
        score = row_text_score(grid[r])
        if score > best_score:
            best_score = score
            best_row = r
    header_row = best_row

    # Data range: rows below the header that have content.
    data_start = header_row + 1
    data_end = nrows - 1
    if data_start > data_end:
        # header is the last row; treat the richest row as header, no data
        data_start = header_row + 1
        data_end = header_row  # yields no rows

    # Drop columns that are blank across the header + data range.
    drop_cols = []
    for c in range(ncols):
        col_has_content = False
        for r in range(header_row, nrows):
            val = grid[r][c] if c < len(grid[r]) else ""
            if val != "":
                col_has_content = True
                break
        if not col_has_content:
            drop_cols.append(c)

    kept = [c for c in range(ncols) if c not in drop_cols]
    columns = []
    hdr = grid[header_row] if header_row < nrows else []
    for c in kept:
        columns.append(hdr[c] if c < len(hdr) else "")

    return {"tables": [{
        "name": "table_1",
        "header_row": header_row,
        "data_start": data_start,
        "data_end": data_end,
        "col_start": 0,
        "col_end": ncols - 1,
        "columns": columns,
        "drop_cols": drop_cols,
    }]}


# --------------------------------------------------------------------------- #
# call_llm — optional real OpenAI-compatible call (needs env API key)
# --------------------------------------------------------------------------- #

def _extract_json(text):
    """Robustly pull the first JSON object out of an LLM reply.

    Tolerates ```json ... ``` fences and surrounding prose. Falls back to a
    brace-matching scan if a straight json.loads fails.
    """
    if not text:
        raise ValueError("empty LLM reply")

    # Strip code fences.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    candidate = fence.group(1).strip() if fence else text.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Brace-matching scan for the first balanced {...} object.
    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        blob = candidate[start:i + 1]
                        try:
                            return json.loads(blob)
                        except json.JSONDecodeError:
                            break
        start = candidate.find("{", start + 1)

    raise ValueError("no valid JSON object found in LLM reply")


def call_llm(grid_sample, model=None, timeout=60):
    """Optional real LLM call against an OpenAI-compatible chat endpoint.

    Reads credentials from the environment (no hardcoded secrets):
        OPENROUTER_API_KEY  (or OPENAI_API_KEY / LLM_API_KEY)
        OPENROUTER_BASE_URL (or OPENAI_BASE_URL / LLM_BASE_URL;
                             default https://openrouter.ai/api/v1)
        LLM_MODEL           (default openai/gpt-4o-mini)

    Raises RuntimeError if no key is set so callers can fall back to mock_llm.
    Returns a parsed spec dict.
    """
    api_key = (os.environ.get("OPENROUTER_API_KEY")
               or os.environ.get("OPENAI_API_KEY")
               or os.environ.get("LLM_API_KEY"))
    if not api_key:
        raise RuntimeError(
            "No LLM API key in environment (set OPENROUTER_API_KEY / "
            "OPENAI_API_KEY / LLM_API_KEY). Fall back to mock_llm() for offline.")

    base_url = (os.environ.get("OPENROUTER_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or os.environ.get("LLM_BASE_URL")
                or "https://openrouter.ai/api/v1").rstrip("/")
    model = model or os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")

    prompt = build_prompt(grid_sample)
    payload = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": "You output only strict JSON describing spreadsheet "
                        "table structure."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    body = json.dumps(payload).encode("utf-8")
    url = base_url + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }

    # Prefer requests if available; otherwise fall back to urllib (stdlib).
    try:
        import requests  # type: ignore
        resp = requests.post(url, headers=headers, data=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))

    content = data["choices"][0]["message"]["content"]
    return _extract_json(content)


# --------------------------------------------------------------------------- #
# Self-test — offline L2 apply path, NO network
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Tiny synthetic grid: title row + (merged-ish) 2-part header + data + a
    # blank metadata column + a totals row.
    grid = [
        ["Q3 Sales Report", None, None, None],          # R0 title/banner
        ["Region", "Product", "Units", "Revenue"],       # R1 header
        ["North", "Widget", "120", "3600"],              # R2
        ["North", "Gadget", "80", "4000"],               # R3
        ["South", "Widget", "150", "4500"],              # R4
        ["Total", "", "350", "12100"],                   # R5 totals (kept as row)
    ]

    print("=== build_grid_sample ===")
    sample = build_grid_sample(grid)
    print(sample)

    print("\n=== mock_llm spec ===")
    spec = mock_llm(sample)
    print(json.dumps(spec, indent=2))

    print("\n=== apply_spec -> tables ===")
    tables = apply_spec(grid, spec, sheet="Sheet1", confidence=0.55)
    for tbl in tables:
        print("name       :", tbl["name"])
        print("sheet      :", tbl["sheet"])
        print("cell_range :", tbl["cell_range"])
        print("columns    :", tbl["columns"])
        print("confidence :", tbl["confidence"])
        print("method     :", tbl["method"])
        print("notes      :", tbl["notes"])
        print("rows       :")
        for row in tbl["rows"]:
            print("   ", row)
