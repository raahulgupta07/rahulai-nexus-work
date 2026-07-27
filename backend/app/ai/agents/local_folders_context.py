"""Render attached local folders into the ``<local_folders>`` planner block.

A "local folder" is a directory on the user's own laptop that their paired
helper shares (``helper.py run --allow-folder …``). The helper posts back only
the SCHEMA of the files it finds; the rows never leave the device. This module
turns that stored schema into prompt context so the planner can write real SQL
against those tables instead of guessing they exist.

Where the attachment lives
--------------------------
On the completion prompt, not in a new table. The composer sends
``prompt.local_folders: ["Sales"]`` with the turn, and CompletionService
already persists the whole prompt dict into ``completions.prompt`` (JSON). We
resolve the *most recent user turn that mentioned the key at all*, so:

  - attaching a folder keeps it attached for later turns (sticky, like a data
    source) without the UI having to re-send it every message, and
  - detaching sends ``local_folders: []`` — an explicit empty list, which wins
    over the older non-empty one.

Flag-gated by HYBRID_LOCAL_FOLDER_ATTACH: off -> every function here returns
empty and the planner sees no block at all.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

ONLINE_WINDOW_S = 30      # matches local_runtime_exec.ONLINE_WINDOW_S
_MAX_TABLES_PER_FOLDER = 40
_MAX_COLUMNS_PER_TABLE = 60
_MAX_DOCS_PER_FOLDER = 30
_LOOKBACK_TURNS = 40


def _enabled() -> bool:
    from app.settings.config import settings
    return bool(getattr(settings, "hybrid_local_folder_attach", False)) and \
        bool(getattr(settings, "hybrid_local_runtime", False))


async def resolve_attached_folder_names(db: Any, report_id: str) -> List[str]:
    """Folder names attached to this report, newest explicit choice wins."""
    if not (_enabled() and report_id):
        return []
    from app.models.completion import Completion
    try:
        rows = (await db.execute(
            select(Completion)
            .where(
                Completion.report_id == str(report_id),
                Completion.role == "user",
                Completion.deleted_at.is_(None),
            )
            .order_by(Completion.created_at.desc())
            .limit(_LOOKBACK_TURNS)
        )).scalars().all()
    except Exception as e:  # noqa: BLE001 — context is best-effort, never fatal
        logger.warning("local-folder attachment lookup failed: %s", e)
        return []
    for c in rows:
        prompt = c.prompt if isinstance(c.prompt, dict) else {}
        if "local_folders" in prompt:
            names = prompt.get("local_folders") or []
            return [str(n) for n in names if n][:10] if isinstance(names, list) else []
    return []


async def get_attached_folders(db: Any, report_id: str, user_id: str) -> Dict[str, Any]:
    """Attached folders joined with the schema their helper last published.

    Returns ``{"names", "folders", "online", "paired", "device_name",
    "missing"}``. ``missing`` = attached names the helper no longer shares.
    """
    empty = {"names": [], "folders": [], "online": False, "paired": False,
             "device_name": None, "missing": []}
    names = await resolve_attached_folder_names(db, report_id)
    if not names or not user_id:
        return empty
    from app.models.local_runtime import LocalRuntime
    try:
        rt = (await db.execute(
            select(LocalRuntime).where(
                LocalRuntime.user_id == str(user_id),
                LocalRuntime.status == "paired",
                LocalRuntime.deleted_at.is_(None),
            ).order_by(LocalRuntime.created_at.desc())
        )).scalars().first()
    except Exception as e:  # noqa: BLE001
        logger.warning("local-folder runtime lookup failed: %s", e)
        return empty
    if not rt:
        return {**empty, "names": names, "missing": names}

    online = bool(rt.last_seen and (datetime.utcnow() - rt.last_seen).total_seconds() < ONLINE_WINDOW_S)
    try:
        catalog = json.loads(rt.folders_schema or "[]")
        catalog = catalog if isinstance(catalog, list) else []
    except Exception:
        catalog = []
    by_name = {f.get("name"): f for f in catalog if isinstance(f, dict)}
    folders = [by_name[n] for n in names if n in by_name]
    return {
        "names": names,
        "folders": folders,
        "online": online and bool(rt.run_local_enabled),
        "paired": True,
        "device_name": rt.name,
        "missing": [n for n in names if n not in by_name],
    }


def render_local_folders_context(state: Dict[str, Any]) -> str:
    """Render the ``<local_folders>`` block from get_attached_folders() output."""
    names = state.get("names") or []
    if not names:
        return ""
    folders = state.get("folders") or []
    online = bool(state.get("online"))
    device = (state.get("device_name") or "the user's device").replace('"', "'")

    lines = ["<local_folders>"]
    lines.append(
        f'  The user attached folders from their own computer ("{device}"). These files are '
        "queried IN PLACE on that machine with DuckDB — they are never uploaded here, so "
        "their rows are not in any warehouse and no data-source SQL can reach them."
    )
    if not state.get("paired"):
        lines.append("  <status>No helper is paired for this user — these folders cannot be queried.</status>")
    elif not online:
        lines.append(
            "  <status>OFFLINE — the CityAgent Helper is not running right now, so these tables "
            "cannot be queried this turn. Do not invent numbers for them: tell the user to open "
            "CityAgent Helper on their computer, and answer any part of the question that does "
            "not need these folders.</status>"
        )
    else:
        lines.append("  <status>ONLINE — ready to query.</status>")

    for f in folders:
        name = str(f.get("name") or "")
        safe = name.replace('"', "'")
        tables = [t for t in (f.get("tables") or []) if isinstance(t, dict)]
        documents = [d for d in (f.get("documents") or []) if isinstance(d, dict) and d.get("file")]
        lines.append(f'  <folder name="{safe}" client_key="local:{safe}" tables="{len(tables)}" documents="{len(documents)}">')
        if f.get("error"):
            lines.append(f'    <error>{str(f["error"])[:300]}</error>')
        for t in tables[:_MAX_TABLES_PER_FOLDER]:
            cols = [c for c in (t.get("columns") or []) if isinstance(c, dict)]
            rendered = ", ".join(
                f"{c.get('name')} ({c.get('type')})" if c.get("type") else str(c.get("name"))
                for c in cols[:_MAX_COLUMNS_PER_TABLE]
            )
            if len(cols) > _MAX_COLUMNS_PER_TABLE:
                rendered += f", …(+{len(cols) - _MAX_COLUMNS_PER_TABLE} more columns)"
            rows = t.get("row_count")
            rows_attr = f' rows="{rows}"' if isinstance(rows, int) else ""
            tname = str(t.get("name") or "").replace('"', "'")
            lines.append(f'    <table name="{tname}"{rows_attr}>{rendered}</table>')
        if len(tables) > _MAX_TABLES_PER_FOLDER:
            lines.append(f"    <note>+{len(tables) - _MAX_TABLES_PER_FOLDER} more tables not listed</note>")
        for d in documents[:_MAX_DOCS_PER_FOLDER]:
            dname = str(d.get("file") or "").replace('"', "'")
            size = d.get("size_bytes")
            size_attr = f' size_bytes="{size}"' if isinstance(size, int) else ""
            lines.append(f'    <document file="{dname}"{size_attr}/>')
        if len(documents) > _MAX_DOCS_PER_FOLDER:
            lines.append(f"    <note>+{len(documents) - _MAX_DOCS_PER_FOLDER} more documents not listed</note>")
        lines.append("  </folder>")

    for m in state.get("missing") or []:
        safe = str(m).replace('"', "'")
        lines.append(
            f'  <folder name="{safe}" status="unavailable">The helper is no longer sharing this '
            "folder, so its tables cannot be queried. Tell the user to restart CityAgent Helper "
            "with this folder shared.</folder>"
        )

    lines.append(
        "  <usage>Documents listed as &lt;document&gt; entries (pdf/docx/pptx/txt/md) are read "
        "with the read_local_document tool (folder + file name exactly as listed) — their text is "
        "extracted on the user's device, never uploaded. Use it whenever the question is about one "
        "of those documents.</usage>"
    )
    lines.append(
        "  <usage>In generate_df, query a local folder through its own client key, exactly like "
        'any other connection: ds_clients["local:&lt;folder&gt;"].execute_query("SELECT … FROM '
        '&lt;table&gt;"). These client keys are valid even though they do not appear in '
        "&lt;connection_clients&gt; — the user's device supplies them at run time. Table names are "
        "exactly the names above (DuckDB views over the files). A local table can NEVER appear in a "
        "warehouse/data-source query and vice versa — run one query per side and join the results in "
        "pandas. Any step touching a local folder runs on the user's computer.</usage>"
    )
    lines.append(
        "  <usage>Table names come from file names, so many contain spaces or punctuation. "
        "Use the WHOLE name exactly as listed above and wrap it in double quotes in SQL: "
        'SELECT * FROM "AWS Console Login events". Shortening a name to its first word is the '
        "most common cause of 'Catalog Error: Table with name ... does not exist' here — DuckDB "
        "reads an unquoted space as the end of the identifier.</usage>"
    )
    lines.append("</local_folders>")
    return "\n".join(lines)


async def build_local_folders_context(db: Any, report_id: str, user_id: str) -> str:
    """Convenience wrapper: resolve + render in one call ("" when nothing attached)."""
    if not _enabled():
        return ""
    try:
        state = await get_attached_folders(db, report_id, user_id)
        return render_local_folders_context(state)
    except Exception as e:  # noqa: BLE001 — never break a run over context
        logger.warning("local-folder context build failed: %s", e)
        return ""
