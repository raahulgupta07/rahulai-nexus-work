import os
import re

DEFINITION_HINTS = ("definition", "definitions", "glossary", "dictionary",
                    "data dict", "meaning", "mapping", "logic", "rules", "q&a", "q & a", "qa")


def is_definitions_file(filename: str) -> bool:
    """True if the filename suggests a dictionary/rules/definitions doc (not raw data)."""
    n = (filename or "").lower()
    return any(h in n for h in DEFINITION_HINTS)


def xlsx_to_definitions_block(xlsx_path: str, max_rows: int = 300) -> str:
    """Read a definitions-style .xlsx and return ONE formatted text block:
       'term — meaning' lines. Robust: picks the two most-populated text columns
       (term, meaning); skips empty rows and obvious header rows. Returns '' on failure."""
    try:
        import pandas as pd

        df = pd.read_excel(xlsx_path, sheet_name=0, header=None, dtype=str)

        # Drop fully-empty columns and rows.
        df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
        if df.shape[1] < 2 or df.shape[0] < 1:
            return ""

        # Count non-empty string cells per column.
        def _nonempty_count(series):
            count = 0
            for v in series:
                if v is not None and isinstance(v, str) and v.strip():
                    count += 1
                elif v is not None and not isinstance(v, str):
                    # non-string, non-null (shouldn't happen with dtype=str) -> count
                    count += 1
            return count

        counts = [(col, _nonempty_count(df[col])) for col in df.columns]
        counts.sort(key=lambda x: x[1], reverse=True)
        top_two = [c for c, _ in counts[:2]]
        if len(top_two) < 2:
            return ""

        # Keep original column order: left = TERM, right = MEANING.
        term_col, meaning_col = sorted(top_two, key=lambda c: list(df.columns).index(c))

        header_terms = {"term", "field", "column", "name"}
        header_meanings = {"meaning", "definition", "description"}

        lines = []
        for _, row in df.iterrows():
            term = row[term_col]
            meaning = row[meaning_col]
            if not isinstance(term, str) or not isinstance(meaning, str):
                continue
            term = term.strip()
            meaning = meaning.strip()
            if not term or not meaning:
                continue
            if term == meaning:
                continue
            # Skip obvious header rows.
            if term.lower() in header_terms and meaning.lower() in header_meanings:
                continue
            lines.append(f"{term} — {meaning}")
            if len(lines) >= max_rows:
                break

        if not lines:
            return ""

        return "Data dictionary (column/term meanings):\n" + "\n".join(lines)
    except Exception:
        return ""
