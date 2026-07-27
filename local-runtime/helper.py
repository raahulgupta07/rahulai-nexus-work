#!/usr/bin/env python3
"""CityAgent Insights — Local Runtime Helper (v0).

Runs the agent-generated Python on THIS machine (Cowork-style local
execution). The cloud does the thinking; this helper does the work:

- pairs once with a 6-digit code from the app (Settings → Local Runtime)
- long-polls the server for execution jobs over plain HTTPS
- executes ``generate_df(ds_clients, excel_files)`` locally with pandas/numpy
- data-source clients are THIN PROXIES: every ``execute_query(sql)`` round-
  trips to the server, which runs the real connector (credentials never come
  to this machine) and streams rows back as Arrow
- optional: whitelisted local folders are exposed as a DuckDB client so files
  are analyzed IN PLACE and never uploaded
- publishes those folders' SCHEMA (table/column names, types, row counts — never
  rows) so they can be attached in chat and the agent can write SQL for them
- ships back only the result DataFrame (Arrow IPC) + stdout

Usage:
    python3 helper.py pair 123456 --server http://localhost:8095
    python3 helper.py run
    python3 helper.py run --allow-folder ~/Data/sales
    python3 helper.py scan --allow-folder ~/Data/sales   # re-publish schemas now

Config is stored in ~/.cityagent-local-runtime.json (token + server URL).

Dependencies: requests, pandas, numpy, pyarrow  (optional: duckdb, openpyxl)
"""
import argparse
import base64
import contextlib
import io
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import requests

# Windows: Path.home() resolves to %USERPROFILE%, so the config lands in
# %USERPROFILE%\.cityagent-local-runtime.json — same single-file contract.
CONFIG_PATH = Path.home() / ".cityagent-local-runtime.json"
IS_WINDOWS = os.name == "nt"
HELPER_VERSION = "0.4.0"

if IS_WINDOWS:  # the console defaults to cp1252 and chokes on the status emoji
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
POLL_WAIT_S = 20
HEARTBEAT_EVERY_S = 10
RESCAN_EVERY_S = 600          # re-scan shared folders every 10 min while running
SCAN_SUFFIXES = (".csv", ".tsv", ".parquet", ".xlsx", ".xls")
# Document files listed in the folder catalog (names/sizes only at scan time;
# text is pulled on-demand by a read_document job and extracted ON this device).
DOC_SUFFIXES = (".pdf", ".docx", ".pptx", ".txt", ".md")


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    if not IS_WINDOWS:
        with contextlib.suppress(Exception):
            CONFIG_PATH.chmod(0o600)  # token is a secret — owner-only
    # On Windows the POSIX mode bits are meaningless (the file already sits in
    # the user's profile, protected by NTFS ACLs), so chmod is skipped.


def config_folders() -> list:
    """Shared folders persisted by the menu-bar/tray apps ("Add folder…").

    The CLI unions these with its --allow-folder flags so a folder added from
    the app keeps working when the helper is later run from the terminal.
    Reads BOTH keys: "folders" (current) and "allowed_folders" (written by
    earlier Windows-tray builds) so nobody's shared folders vanish on upgrade."""
    try:
        cfg = load_config()
        merged = list(cfg.get("folders") or []) + list(cfg.get("allowed_folders") or [])
        return list(dict.fromkeys(f for f in merged if isinstance(f, str)))
    except Exception:  # noqa: BLE001 — a corrupt config must not kill the helper
        return []


def remember_folder(path: str) -> list:
    """Persist one shared folder into the config (merge + dedupe) and return
    the full list. Config-only — the caller decides when to scan/publish."""
    cfg = load_config()
    folders = [f for f in (cfg.get("folders") or []) if isinstance(f, str)]
    if path not in folders:
        folders.append(path)
    cfg["folders"] = folders
    save_config(cfg)
    return folders


def config_mtime() -> float:
    """Last-modified time of the config, or 0.0 when it is unreadable.

    The running loop watches this so folders added/removed by ANOTHER process
    (the menu-bar/tray app, or `helper.py scan`, or a hand-edit) are picked up
    without a restart — the config is the only channel those processes share.
    """
    try:
        return CONFIG_PATH.stat().st_mtime
    except Exception:  # noqa: BLE001
        return 0.0


def sync_folders(folders: list, cli_folders: list) -> list:
    """Fold external config changes into the RUNNING whitelist, in place.

    Mutates `folders` rather than rebinding it: the add/remove job handlers
    hold the same list object, so identity has to survive.

    Deliberately asymmetric — only *config* folders are added, never CLI ones.
    A folder un-shared during this session was removed from both the config and
    this list by `_handle_remove_folder`; re-adding CLI entries here would
    resurrect it on the next tick. Conversely a config folder that vanished
    elsewhere is dropped, unless it also came in on `--allow-folder` (an
    explicit flag for THIS process outranks another process's config edit).
    """
    cfg = config_folders()
    for f in cfg:
        if f not in folders:
            folders.append(f)
    for f in list(folders):
        if f not in cfg and f not in cli_folders:
            folders.remove(f)
    return folders


def forget_folder_by_name(name: str) -> list:
    """Remove every configured folder whose LEAF NAME matches (the UI only
    knows names, not paths) from both config keys. Returns the surviving list."""
    cfg = load_config()
    def keep(f):
        return not (isinstance(f, str) and Path(f).name == name)
    cfg["folders"] = [f for f in (cfg.get("folders") or []) if keep(f)]
    if cfg.get("allowed_folders"):
        cfg["allowed_folders"] = [f for f in cfg["allowed_folders"] if keep(f)]
    save_config(cfg)
    return config_folders()


# --------------------------------------------------------------------------- #
#  Pairing
# --------------------------------------------------------------------------- #

def cmd_pair(args) -> int:
    server = args.server.rstrip("/")
    import platform
    name = args.name or (platform.node() or "My computer")  # os.uname() is POSIX-only
    r = requests.post(
        f"{server}/api/local-runtime/pair/claim",
        json={"code": args.code, "name": name, "helper_version": HELPER_VERSION},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"Pairing failed ({r.status_code}): {r.text}")
        return 1
    data = r.json()
    save_config({"server": server, "token": data["token"], "runtime_id": data["runtime_id"], "name": name})
    print(f"✅ Paired as '{name}' (runtime {data['runtime_id'][:8]}…). Run:  python3 helper.py run")
    return 0


# --------------------------------------------------------------------------- #
#  Proxied data-source client (credentials never live here)
# --------------------------------------------------------------------------- #

class RemoteClientProxy:
    """Stands in for a server-side connector client inside generate_df().
    Only read-only querying is supported; anything else fails the job cleanly
    so the server falls back to its own sandbox."""

    def __init__(self, session: "ServerSession", client_key: str):
        self._session = session
        self._client_key = client_key

    def execute_query(self, sql, *args, **kwargs):
        payload = {"client_key": self._client_key, "sql": str(sql)}
        r = self._session.post("/api/local-runtime/query", json=payload, timeout=120)
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise RuntimeError(f"proxy query failed ({r.status_code}): {detail}")
        body = r.json()
        # DEF-007: the proxy caps the result at MAX_ROWS and SAYS SO in `truncated`,
        # but this consumer used to read only `data_b64` and hand the partial frame
        # to generated pandas code — so the honest signal was computed, transmitted,
        # and thrown away one hop later. That is the same shape as DEF-006 (Power BI
        # silently returning 48,222 of 300,086 rows): an analysis executes perfectly
        # over a fraction of the data and reports one clean, confident, wrong number.
        # Raise instead. A partial frame entering pandas unannounced IS the defect,
        # and the message is written for the model that has to fix its own query.
        if body.get("truncated"):
            rows = body.get("rows")
            raise RuntimeError(
                "QUERY RESULT WAS TRUNCATED — DO NOT USE THIS DATA. The data proxy "
                f"returned {rows:,} rows and stopped there because the result exceeded "
                "its row cap; the real table is larger. Any number computed from this "
                "in pandas — sum, count, mean, top-N, distinct count — will be WRONG, "
                "and will look plausible. Do NOT retry this query unchanged and do NOT "
                "aggregate the partial result. Push the aggregation into SQL so the "
                "database returns only the small result you need: use GROUP BY with "
                "SUM/COUNT/AVG, ORDER BY ... LIMIT for a top-N, or COUNT(DISTINCT col) "
                "for a distinct count, instead of selecting every row."
                if isinstance(rows, int)
                else "QUERY RESULT WAS TRUNCATED — DO NOT USE THIS DATA. Push the "
                "aggregation into SQL (GROUP BY, ORDER BY ... LIMIT, COUNT(DISTINCT)) "
                "instead of selecting every row."
            )
        raw = base64.b64decode(body["data_b64"])
        import pyarrow as pa
        with pa.ipc.open_stream(io.BytesIO(raw)) as reader:
            df = reader.read_all().to_pandas()
        return df

    # generated code sometimes uses .query(...) — same thing
    def query(self, *a, **k):
        return self.execute_query(*a, **k)

    def __getattr__(self, item):
        raise AttributeError(
            f"'{item}' is not supported by the local runtime proxy client "
            f"(only execute_query/query). The job will fall back to the server."
        )


class LocalFolderClient:
    """DuckDB over a whitelisted local folder — files are read IN PLACE and
    never leave this machine. Table name = cleaned file stem."""

    def __init__(self, folder: Path):
        self.folder = folder

    @staticmethod
    def _sql_path(f: Path) -> str:
        # DuckDB accepts forward slashes on every OS; backslashes from Windows
        # paths would otherwise land inside a SQL string literal.
        return f.as_posix().replace("'", "''")

    def execute_query(self, sql, *args, **kwargs):
        import duckdb
        con = duckdb.connect()
        try:
            for f in sorted(self.folder.rglob("*")):
                if f.suffix.lower() in (".csv", ".tsv"):
                    con.execute(f"CREATE VIEW \"{f.stem}\" AS SELECT * FROM read_csv_auto('{self._sql_path(f)}')")
                elif f.suffix.lower() == ".parquet":
                    con.execute(f"CREATE VIEW \"{f.stem}\" AS SELECT * FROM read_parquet('{self._sql_path(f)}')")
                elif f.suffix.lower() in (".xlsx", ".xls"):
                    with contextlib.suppress(Exception):
                        import pandas as pd
                        con.register(f.stem, pd.read_excel(f))
            return con.execute(str(sql)).df()
        finally:
            con.close()

    query = execute_query


# --------------------------------------------------------------------------- #
#  scan_folder — publish the SCHEMA of shared folders (never the data)
# --------------------------------------------------------------------------- #

def scan_folder(folder: str) -> dict:
    """DuckDB-inspect one whitelisted folder and return its schema catalog.

    What leaves this machine: folder name, file names, table names, column
    names + types, row counts. NEVER a row, a cell or a sample value — the
    whole point of a local folder is that the data stays put. Table names match
    LocalFolderClient exactly (the file stem), so SQL the agent writes against
    this catalog resolves against the same views at query time.
    """
    p = Path(folder).expanduser()
    out = {"name": p.name, "path": str(p), "tables": [], "documents": [], "error": None}
    if not p.is_dir():
        out["error"] = "Folder not found on this device"
        return out

    # Documents ride the same catalog as tables: names + sizes ONLY. Their
    # text is extracted on-demand by a read_document job, never at scan time.
    for f in sorted(p.rglob("*")):
        if f.is_file() and f.suffix.lower() in DOC_SUFFIXES:
            with contextlib.suppress(Exception):
                out["documents"].append({
                    "file": f.name,
                    "format": f.suffix.lower().lstrip("."),
                    "size_bytes": int(f.stat().st_size),
                })
    try:
        import duckdb
    except ImportError:
        out["error"] = "duckdb is not installed (pip install duckdb)"
        return out

    con = duckdb.connect()
    try:
        for f in sorted(p.rglob("*")):
            suffix = f.suffix.lower()
            if suffix not in SCAN_SUFFIXES or not f.is_file():
                continue
            entry = {"name": f.stem, "file": f.name, "format": suffix.lstrip("."),
                     "row_count": None, "columns": []}
            try:
                if suffix in (".csv", ".tsv"):
                    src = f"read_csv_auto('{LocalFolderClient._sql_path(f)}')"
                elif suffix == ".parquet":
                    src = f"read_parquet('{LocalFolderClient._sql_path(f)}')"
                else:  # excel: no native duckdb reader here — go through pandas
                    import pandas as pd
                    df = pd.read_excel(f)
                    entry["columns"] = [{"name": str(c), "type": str(df[c].dtype)} for c in df.columns]
                    entry["row_count"] = int(len(df))
                    out["tables"].append(entry)
                    continue
                # DESCRIBE reads only the header/footer metadata — no full scan.
                for row in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall():
                    entry["columns"].append({"name": str(row[0]), "type": str(row[1])})
                with contextlib.suppress(Exception):
                    entry["row_count"] = int(con.execute(f"SELECT count(*) FROM {src}").fetchone()[0])
                out["tables"].append(entry)
            except Exception as e:  # one unreadable file must not sink the folder
                entry["error"] = f"{e.__class__.__name__}: {e}"[:200]
                out["tables"].append(entry)
    finally:
        con.close()
    return out


def post_folder_scan(session: "ServerSession", folders: list, quiet: bool = False,
                     allow_empty: bool = False) -> bool:
    """Scan every shared folder and publish the schemas to the server.

    `allow_empty=True` publishes an EMPTY list too — needed when the last
    folder is un-shared, so the server-side menu actually clears instead of
    keeping a stale entry. Default keeps the old no-op (startup with no
    folders shouldn't clobber anything published by another code path)."""
    if not folders and not allow_empty:
        return True
    payload = [scan_folder(f) for f in folders]
    try:
        r = session.post(
            "/api/local-runtime/folders",
            json={"folders": payload, "helper_version": HELPER_VERSION},
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"   (folder scan could not reach the server: {e})")
        return False
    if r.status_code == 403:
        if not quiet:
            print("   (folder attach is disabled on the server — folders stay queryable, "
                  "but they won't appear in chat)")
        return False
    if r.status_code != 200:
        if not quiet:
            print(f"   (folder scan rejected [{r.status_code}]: {r.text[:200]})")
        return False
    n_tables = sum(len(f["tables"]) for f in payload)
    if not quiet:
        print(f"📁 Shared {len(payload)} folder(s), {n_tables} table(s) — schema only, no data uploaded")
    return True


def cmd_scan(args) -> int:
    """One-shot rescan (use after adding/removing files in a shared folder)."""
    cfg = load_config()
    if not cfg.get("token"):
        print("Not paired. Run:  python3 helper.py pair <code> --server <app-url>")
        return 1
    folders = list(dict.fromkeys(list(args.allow_folder or []) + config_folders()))
    if not folders:
        print("Nothing to scan. Pass the folders you want to share:")
        print("  python3 helper.py scan --allow-folder ~/Data/sales")
        return 1
    session = ServerSession(cfg["server"], cfg["token"])
    for f in folders:
        info = scan_folder(f)
        if info["error"]:
            print(f"  ⚠️  {info['name']}: {info['error']}")
        else:
            print(f"  📁 {info['name']}: {len(info['tables'])} table(s)")
            for t in info["tables"]:
                rows = f"{t['row_count']:,} rows" if t.get("row_count") is not None else "rows unknown"
                print(f"       • {t['name']} ({len(t['columns'])} cols, {rows})")
    return 0 if post_folder_scan(session, folders) else 1


def _pick_folder_native() -> "str | None":
    """Open THIS machine's real folder chooser and return the picked path.

    Runs inside whichever helper process is active (CLI, Mac menu-bar app,
    Windows tray) — all of them live in the user's GUI session, so the dialog
    appears on their screen. Returns None on cancel; raises RuntimeError when
    no picker is available (headless Linux) so the caller can tell the user to
    type a path instead."""
    if IS_WINDOWS:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"No folder dialog available on this device ({e})")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            picked = filedialog.askdirectory(
                title="Share a folder with CityAgent — only table/column names are sent, never the data"
            )
        finally:
            root.destroy()
        return picked or None
    if sys.platform == "darwin":
        import subprocess
        out = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt "Share a folder with CityAgent — '
             'only table/column names are sent, never the data")'],
            capture_output=True, text=True, timeout=240,
        )
        if out.returncode != 0:  # user hit Cancel (or dialog timed out)
            return None
        return out.stdout.strip().rstrip("/") or None
    raise RuntimeError("No folder dialog available on this device — type the folder path instead")


def _handle_add_folder(payload: dict, session: "ServerSession", allowed_folders: list) -> dict:
    """Handle an `add_folder` job queued by the web app's "Add folder" row.

    Two modes: an explicit `path` (typed in the browser), or `pick: true` —
    open the native folder chooser on THIS machine and let the user click the
    folder, no typing anywhere."""
    raw = str(payload.get("path") or "").strip()
    if not raw and payload.get("pick"):
        try:
            picked = _pick_folder_native()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "stdout": "", "queries": [], "error": str(e)}
        if not picked:
            return {"status": "error", "stdout": "", "queries": [], "error": "cancelled"}
        raw = picked
    p = Path(raw).expanduser()
    if not raw:
        return {"status": "error", "stdout": "", "queries": [], "error": "No folder path given"}
    if not p.is_dir():
        return {"status": "error", "stdout": "", "queries": [],
                "error": f"'{raw}' is not a folder on this device"}
    path = str(p)
    already = path in allowed_folders
    if not already:
        remember_folder(path)          # persists for restarts (config)
        allowed_folders.append(path)   # in place: this running process too
    ok = post_folder_scan(session, allowed_folders, quiet=True)
    info = scan_folder(path)
    if info.get("error"):
        return {"status": "error", "stdout": "", "queries": [],
                "error": f"Shared, but scan failed: {info['error']}"}
    n = len(info.get("tables") or [])
    note = "already shared; rescanned" if already else "shared"
    if not ok:
        note += " (publish to server failed — will retry on the next periodic scan)"
    return {"status": "done", "stdout": f"Folder {p.name} {note}: {n} table(s), schema only.",
            "queries": []}


def _handle_remove_folder(payload: dict, session: "ServerSession", allowed_folders: list) -> dict:
    """Stop sharing a folder (by leaf name — that's all the UI knows). Removes
    it from the persisted config AND the running whitelist, then republishes
    the remaining list so it drops out of the chat menu immediately."""
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"status": "error", "stdout": "", "queries": [], "error": "No folder name given"}
    matches = [f for f in allowed_folders if Path(f).name == name]
    forget_folder_by_name(name)
    for f in matches:
        allowed_folders.remove(f)
    if not matches:
        # Nothing in the running list — still republish so a stale server entry clears.
        post_folder_scan(session, allowed_folders, quiet=True, allow_empty=True)
        return {"status": "done", "stdout": f"Folder {name} was not shared; list refreshed.",
                "queries": []}
    post_folder_scan(session, allowed_folders, quiet=True, allow_empty=True)
    return {"status": "done", "stdout": f"Stopped sharing {name}.", "queries": []}


# --------------------------------------------------------------------------- #
#  read_document — extract text from a document IN a shared folder, on-device
# --------------------------------------------------------------------------- #

# Cap what one job may return. Bigger docs are truncated with a marker so the
# agent knows it saw a prefix, not the whole thing.
DOC_TEXT_CAP = 90_000

_OOXML_TEXT_RE = re.compile(r"<(?:w|a):t(?:\s[^>]*)?>(.*?)</(?:w|a):t>", re.S)
_OOXML_PARA_RE = re.compile(r"</(?:w|a):p>")
_TAG_RE = re.compile(r"<[^>]+>")


def _extract_ooxml_text(path: "Path") -> str:
    """docx/pptx text via stdlib zipfile — no python-docx/pptx dependency.
    Mirrors the server's _document_text approach (anchored <w:t>/<a:t> runs)."""
    import zipfile
    suffix = path.suffix.lower()
    parts: list = []
    with zipfile.ZipFile(path) as z:
        if suffix == ".docx":
            members = ["word/document.xml"]
        else:  # pptx: slides in order
            members = sorted(
                (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for i, m in enumerate(members):
            try:
                xml = z.read(m).decode("utf-8", "ignore")
            except KeyError:
                continue
            xml = _OOXML_PARA_RE.sub("\n", xml)
            runs = _OOXML_TEXT_RE.findall(xml)
            text = "".join(runs) if runs else _TAG_RE.sub(" ", xml)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                parts.append(f"--- Slide {i + 1} ---\n{text}" if suffix == ".pptx" and len(members) > 1 else text)
    return "\n\n".join(parts)


def _extract_pdf_text(path: "Path") -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # older installs
        except ImportError:
            raise RuntimeError("PDF reading needs pypdf on this device: pip3 install pypdf")
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        with contextlib.suppress(Exception):
            t = (page.extract_text() or "").strip()
            if t:
                pages.append(f"--- Page {i + 1} ---\n{t}")
        if sum(len(x) for x in pages) > DOC_TEXT_CAP:
            break
    return "\n\n".join(pages)


def _handle_read_document(payload: dict, allowed_folders: list) -> dict:
    """Extract a document's text ON THIS DEVICE and return it as the job result.
    The file itself never leaves the machine — only its text, capped, and only
    because the agent explicitly asked for this document."""
    folder_name = str(payload.get("folder") or "").strip()
    file_name = str(payload.get("file") or "").strip()
    folder = next((f for f in allowed_folders if Path(f).expanduser().name == folder_name), None)
    if not folder:
        return {"status": "error", "error": f"Folder not shared on this device: {folder_name}"}
    root = Path(folder).expanduser().resolve()
    target = None
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name == file_name:
            target = f
            break
    if target is None or root not in target.resolve().parents and target.resolve() != root:
        return {"status": "error", "error": f"File not found in {folder_name}: {file_name}"}
    suffix = target.suffix.lower()
    try:
        if suffix in (".docx", ".pptx"):
            text = _extract_ooxml_text(target)
        elif suffix == ".pdf":
            text = _extract_pdf_text(target)
        elif suffix in (".txt", ".md"):
            text = target.read_text(encoding="utf-8", errors="ignore")
        else:
            return {"status": "error", "error": f"Unsupported document type: {suffix}"}
    except Exception as e:
        return {"status": "error", "error": f"{e.__class__.__name__}: {e}"[:400]}
    if not text.strip():
        return {"status": "error",
                "error": f"No text found in {file_name} — likely a scanned/image-only document."}
    truncated = len(text) > DOC_TEXT_CAP
    if truncated:
        text = text[:DOC_TEXT_CAP] + "\n\n[TRUNCATED — document is longer]"
    print(f"📄 read_document: {folder_name}/{file_name} → {len(text)} chars"
          + (" (truncated)" if truncated else ""))
    return {"status": "done", "stdout": text, "queries": [],
            "meta": {"file": file_name, "folder": folder_name, "chars": len(text),
                     "truncated": truncated}}


class ServerSession:
    def __init__(self, server: str, token: str):
        self.server = server.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, path, **kw):
        return self.s.get(self.server + path, **kw)

    def post(self, path, **kw):
        return self.s.post(self.server + path, **kw)


# --------------------------------------------------------------------------- #
#  Job execution — mirrors the server sandbox contract
# --------------------------------------------------------------------------- #

def execute_job(job: dict, session: "ServerSession", allowed_folders: list) -> dict:
    """Run one job. Returns the result body for POST /jobs/{id}/result."""
    payload_early = job.get("payload") or {}
    # "Add folder" requested from the chat paperclip menu. THIS machine decides:
    # validate the path, extend the whitelist (in place, so the running process
    # serves queries against it immediately + persisted for restarts), scan and
    # publish the schema. No code execution involved.
    if payload_early.get("kind") == "add_folder":
        return _handle_add_folder(payload_early, session, allowed_folders)
    if payload_early.get("kind") == "remove_folder":
        return _handle_remove_folder(payload_early, session, allowed_folders)
    if payload_early.get("kind") == "read_document":
        return _handle_read_document(payload_early, allowed_folders)

    import pandas as pd
    import numpy as np

    code = job.get("code") or ""
    payload = job.get("payload") or {}
    client_keys = payload.get("client_keys") or []

    executed_queries: list = []

    def capturing(cls):
        """Record every SQL string that actually runs, whichever client ran it.

        Folder queries count as much as remote ones. The server branches its
        retry advice on whether ANY query was executed, so while folder SQL
        went unrecorded a folder job that lost its return value was told "no
        query was executed — you MUST call ds_clients[...]", plus a table list
        belonging to some other connector entirely.

        ``query`` is overridden too, not just ``execute_query``:
        LocalFolderClient aliases them with a plain class attribute
        (``query = execute_query``), which binds to the ORIGINAL function at
        class-creation time — so overriding ``execute_query`` alone would leave
        the alias uncaptured, and the generated code uses both spellings.
        """
        class Capturing(cls):
            def execute_query(self, sql, *a, **k):
                executed_queries.append(str(sql))
                return super().execute_query(sql, *a, **k)
            query = execute_query
        Capturing.__name__ = f"Capturing{cls.__name__}"
        return Capturing

    CapturingProxy = capturing(RemoteClientProxy)
    CapturingFolderClient = capturing(LocalFolderClient)

    ds_clients = {key: CapturingProxy(session, key) for key in client_keys}
    # Whitelisted local folders appear as extra clients (in-place, no upload).
    shared_names = set()
    for folder in allowed_folders:
        p = Path(folder).expanduser()
        if p.is_dir():
            ds_clients[f"local:{p.name}"] = CapturingFolderClient(p)
            shared_names.add(p.name)

    # The server only routes a job here when it needs a local folder, so a
    # folder that is no longer shared has to fail loudly and specifically —
    # otherwise the generated code dies on an opaque KeyError.
    missing = [n for n in (payload.get("local_folders") or []) if n not in shared_names]
    if missing:
        return {
            "status": "error",
            "stdout": "",
            "queries": [],
            "error": (
                f"Folder '{missing[0]}' is not shared by this helper. Restart it with "
                f"--allow-folder <path-to-{missing[0]}> to make it queryable again."
            ),
        }

    stdout_buf = io.StringIO()
    try:
        namespace = {"pd": pd, "np": np, "db_clients": ds_clients}
        with contextlib.redirect_stdout(stdout_buf):
            exec(code, namespace)  # noqa: S102 — user's own machine, user's own job
            fn = namespace.get("generate_df")
            if not callable(fn):
                raise RuntimeError("No generate_df function found in code")
            # v0 contract: jobs needing http/load_step/load_entity never reach
            # the helper (the server-side guard keeps them on the server), so
            # the call is always fn(ds_clients, excel_files).
            df = fn(ds_clients, [])
        if df is None or not isinstance(df, pd.DataFrame):
            raise RuntimeError("generate_df did not return a DataFrame")
        import pyarrow as pa
        sink = io.BytesIO()
        table = pa.Table.from_pandas(df)
        with pa.ipc.new_stream(sink, table.schema) as w:
            w.write_table(table)
        return {
            "status": "done",
            "result_b64": base64.b64encode(sink.getvalue()).decode(),
            "result_format": "arrow",
            "stdout": stdout_buf.getvalue()[-100000:],
            "queries": executed_queries,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "status": "error",
            "stdout": stdout_buf.getvalue()[-100000:],
            "queries": executed_queries,
            "error": f"{e.__class__.__name__}: {e}\n{traceback.format_exc()[-4000:]}",
        }


# --------------------------------------------------------------------------- #
#  Main loop
# --------------------------------------------------------------------------- #

def cmd_run(args) -> int:
    cfg = load_config()
    if not cfg.get("token"):
        print("Not paired. Get a code from the app (Settings → Local Runtime) then:")
        print("  python3 helper.py pair <code> --server <app-url>")
        return 1
    session = ServerSession(cfg["server"], cfg["token"])
    # CLI flags + folders added from the menu-bar/tray app, deduped in order.
    cli_folders = list(args.allow_folder or [])
    folders = list(dict.fromkeys(cli_folders + config_folders()))
    print(f"💻 CityAgent local runtime '{cfg.get('name')}' — connected to {cfg['server']}")
    if folders:
        print(f"📁 Local folders (never uploaded): {', '.join(folders)}")
    # Publish folder schemas on startup so they can be attached in chat right
    # away; re-scan periodically so files added later show up without a restart
    # (`python3 helper.py scan --allow-folder …` forces it immediately). This
    # rides the existing long-poll loop rather than adding a job type — one
    # fewer moving part, and it self-heals after any server restart.
    #
    # allow_empty: starting with NO shared folders must publish the empty list,
    # not skip the call. Otherwise a helper restarted without --allow-folder
    # leaves the server still advertising folders it will now refuse to query —
    # the chat menu offers a folder that errors out.
    post_folder_scan(session, folders, allow_empty=True)
    last_scan = time.time()
    cfg_mtime = config_mtime()
    print("Waiting for jobs… (Ctrl-C to stop)")
    last_beat = 0.0
    while True:
        try:
            now = time.time()
            if now - last_beat > HEARTBEAT_EVERY_S:
                session.post("/api/local-runtime/heartbeat", timeout=10)
                last_beat = now
            # Another process edited the config (tray "Add folder…", `helper.py
            # scan`, hand-edit) — adopt the change and republish, so the running
            # helper never refuses a folder the app just offered.
            m = config_mtime()
            if m != cfg_mtime:
                cfg_mtime = m
                before = list(folders)
                sync_folders(folders, cli_folders)
                if folders != before:
                    added = [f for f in folders if f not in before]
                    dropped = [f for f in before if f not in folders]
                    if added:
                        print(f"📁 Now sharing: {', '.join(Path(f).name for f in added)}")
                    if dropped:
                        print(f"📁 Stopped sharing: {', '.join(Path(f).name for f in dropped)}")
                    post_folder_scan(session, folders, quiet=True, allow_empty=True)
                    last_scan = now
            if folders and now - last_scan > RESCAN_EVERY_S:
                post_folder_scan(session, folders, quiet=True)
                last_scan = now
            r = session.get(f"/api/local-runtime/jobs/next?wait_s={POLL_WAIT_S}", timeout=POLL_WAIT_S + 10)
            if r.status_code == 401:
                print("Token rejected (unpaired/revoked). Re-pair from the app.")
                return 1
            if r.status_code != 200:
                time.sleep(3)
                continue
            job = r.json()
            if not job.get("job_id"):
                continue
            print(f"⚡ Job {job['job_id'][:8]}… running locally")
            t0 = time.time()
            result = execute_job(job, session, folders)
            took = time.time() - t0
            session.post(f"/api/local-runtime/jobs/{job['job_id']}/result", json=result, timeout=60)
            state = "done" if result["status"] == "done" else "error"
            print(f"   {'✅' if state == 'done' else '❌'} {state} in {took:.1f}s")
        except KeyboardInterrupt:
            print("\nStopped.")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"   (network hiccup: {e}; retrying)")
            time.sleep(3)


def main() -> int:
    ap = argparse.ArgumentParser(description="CityAgent Insights local runtime helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("pair", help="pair with the app using a 6-digit code")
    p1.add_argument("code")
    p1.add_argument("--server", required=True, help="app URL, e.g. http://localhost:8095")
    p1.add_argument("--name", default=None)
    p1.set_defaults(fn=cmd_pair)
    p2 = sub.add_parser("run", help="run the job loop")
    p2.add_argument("--allow-folder", action="append", help="local folder to expose (repeatable)")
    p2.set_defaults(fn=cmd_run)
    p3 = sub.add_parser("scan", help="publish shared folder schemas to the app (schema only)")
    p3.add_argument("--allow-folder", action="append", help="local folder to expose (repeatable)")
    p3.set_defaults(fn=cmd_scan)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
