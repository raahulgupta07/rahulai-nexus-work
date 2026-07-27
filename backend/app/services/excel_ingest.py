import os
import re
import uuid

import pandas as pd


def xlsx_to_csvs(xlsx_path: str, out_dir: str) -> list[str]:
    """Convert each non-empty sheet of an .xlsx to a CSV in out_dir.
    Returns absolute paths of the CSVs written (one per non-empty sheet).
    Robust: skips empty sheets; sanitizes sheet names into safe filenames;
    never raises on a bad sheet (skip it). Uses pandas.read_excel."""
    os.makedirs(out_dir, exist_ok=True)
    sheets = pd.read_excel(xlsx_path, sheet_name=None, dtype=str)

    written: list[str] = []
    for sheet_name, df in sheets.items():
        try:
            if df is None or df.empty or df.shape[0] == 0 or df.shape[1] == 0:
                continue
            base = re.sub(r'[^A-Za-z0-9_]+', '_', sheet_name).strip('_').lower() or 'sheet'
            path = os.path.join(out_dir, f"{uuid.uuid4().hex}_{base}.csv")
            df.to_csv(path, index=False)
            written.append(os.path.abspath(path))
        except Exception:
            continue

    return written
