"""excel_convert.py — flag-gated smart .xlsx → CSV conversion.

Drop-in replacement for ``app.services.excel_ingest.xlsx_to_csvs`` with the
SAME signature/return (absolute CSV paths, one file per extracted table).

Safety contract:
  * Flag OFF (default) → behaviour is byte-identical to the legacy path: this
    just returns ``xlsx_to_csvs(path, out_dir)`` verbatim.
  * Flag ON → tries the proven structural parser
    (``excel_structizer.parse_workbook``) and writes one friendly-named CSV per
    clean table. ANY failure — parse raises, yields nothing, or writes zero
    CSVs — logs a warning and falls back to the legacy ``xlsx_to_csvs`` path.
  * This function NEVER raises. Downstream callers see the same result shape
    they got before the flag existed.
"""

import csv
import logging
import os
import re
import uuid

from app.services.excel_ingest import xlsx_to_csvs

logger = logging.getLogger(__name__)


def _safe_str(v) -> str:
    """Coerce any cell value to a plain string safely ('' for None)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:
        return ""


def _friendly_base(name: str) -> str:
    """Turn a table's clean ``name`` into a short, safe filename stem.

    Keeps snake_case identifiers, strips anything unsafe, caps length, and
    always yields a non-empty stem so DuckDB table names stay short/queryable.
    """
    base = re.sub(r"[^A-Za-z0-9_]+", "_", _safe_str(name)).strip("_").lower()
    if not base:
        base = "table"
    # Keep it short so the derived DuckDB table name stays queryable.
    return base[:48]


def _safe_legacy(path: str, out_dir: str) -> list[str]:
    """Legacy fallback that never raises (used only on the smart/ON branch, so
    the ON path honours the 'never raise' contract even when the legacy path
    itself would raise, e.g. a missing file). The OFF path deliberately does NOT
    use this — it calls xlsx_to_csvs directly to stay byte-identical to legacy."""
    try:
        return xlsx_to_csvs(path, out_dir)
    except Exception:
        logger.warning(
            "convert_xlsx: legacy fallback also failed for %r; returning []",
            path, exc_info=True,
        )
        return []


def convert_xlsx(path: str, out_dir: str) -> list[str]:
    """Convert an .xlsx into one CSV per table. See module docstring for the
    flag/fallback contract. Same signature/return as ``xlsx_to_csvs``."""
    # Read the flag lazily so importing this module never triggers settings
    # side effects, and so tests can monkeypatch settings.smart_excel_ingest.
    smart = False
    try:
        from app.settings.config import settings
        smart = bool(getattr(settings, "smart_excel_ingest", False))
    except Exception:
        smart = False

    # Default path: byte-identical to legacy behaviour.
    if not smart:
        logger.info("convert_xlsx: smart_excel_ingest OFF -> legacy xlsx_to_csvs")
        return xlsx_to_csvs(path, out_dir)

    # Smart path — wrapped so ANY failure falls back to the legacy path.
    try:
        from app.services.excel_structizer import parse_workbook

        tables = parse_workbook(path)
        if not tables:
            logger.warning(
                "convert_xlsx: smart parse yielded no tables for %r -> falling "
                "back to legacy xlsx_to_csvs", path,
            )
            return _safe_legacy(path, out_dir)

        os.makedirs(out_dir, exist_ok=True)
        written: list[str] = []
        for tbl in tables:
            try:
                columns = tbl.get("columns") or []
                rows = tbl.get("rows") or []
                base = _friendly_base(tbl.get("name"))
                csv_path = os.path.join(out_dir, f"{base}_{uuid.uuid4().hex[:6]}.csv")
                with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow([_safe_str(c) for c in columns])
                    for row in rows:
                        writer.writerow([_safe_str(v) for v in row])
                written.append(os.path.abspath(csv_path))
            except Exception:
                # One bad table must not sink the rest.
                logger.warning(
                    "convert_xlsx: failed writing table %r from %r; skipping it",
                    tbl.get("name"), path, exc_info=True,
                )
                continue

        if not written:
            logger.warning(
                "convert_xlsx: smart path wrote zero CSVs for %r -> falling back "
                "to legacy xlsx_to_csvs", path,
            )
            return _safe_legacy(path, out_dir)

        logger.info(
            "convert_xlsx: smart_excel_ingest ON -> wrote %d CSV(s) via "
            "excel_structizer for %r", len(written), path,
        )
        return written
    except Exception:
        logger.warning(
            "convert_xlsx: smart path raised for %r -> falling back to legacy "
            "xlsx_to_csvs", path, exc_info=True,
        )
        return _safe_legacy(path, out_dir)
