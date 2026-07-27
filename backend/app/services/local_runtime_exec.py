"""Remote-execution bridge for the local runtime (Cowork-style).

Called from the execution seam (StreamingCodeExecutor._try_run_remote). Given
the agent-generated code, finds the requesting user's paired + online helper,
enqueues a DB job, and polls for the result. Returns the exact
``(pd.DataFrame, output_log, executed_queries)`` tuple the server sandbox
would return, or ``None`` to fall back to server execution.

Design rules (why it can't break the product):
- ANY doubt -> return None -> the existing server path runs.
- DB-backed job queue only (multi-worker safe; no in-process state).
- Credentials never leave the server: the payload carries only the ds client
  KEYS; the helper proxies actual queries via the server data-proxy endpoint.
"""
import asyncio
import base64
import contextlib
import io
import json
import logging
import re
import time as _time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select

logger = logging.getLogger(__name__)

# v0 support guard: jobs that need these server-side extras stay on the server.
_HTTP_PARAM_RE = re.compile(r"def\s+generate_df\s*\(([^)]*)\)")

REMOTE_TIMEOUT_S = 120          # max wall-clock for a remote job
POLL_INTERVAL_S = 0.7
ONLINE_WINDOW_S = 30            # helper considered online if seen within this

# ds_clients["local:<folder>"] — the only way generated code can reach a folder
# that lives on the user's laptop. Its presence makes a job UNRUNNABLE on the
# server (no such client exists there), which is what forces local routing.
# Anchored on the ds_clients subscript: a bare "local:…" inside an ordinary
# string literal (e.g. an error message the coder echoed back on a retry) must
# NOT be mistaken for a folder reference — that garbles the folder list sent to
# the helper.
_LOCAL_KEY_RE = re.compile(r"""ds_clients\s*\[\s*['"]local:([^'"]{1,120})['"]\s*\]""")


class LocalFolderUnavailable(RuntimeError):
    """The code needs a folder on the user's device and we cannot get to it.

    Raised (never swallowed) so the agent surfaces a real explanation instead of
    silently falling back to a server run that would die on a KeyError, or —
    worse — returning an empty result that reads like "there is no data".
    """


def referenced_local_folders(code: str) -> List[str]:
    """Folder names the generated code addresses via ds_clients["local:…"]."""
    if not code:
        return []
    seen, out = set(), []
    for name in _LOCAL_KEY_RE.findall(code):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _decode_df(result_b64: Optional[str], result_format: Optional[str]) -> Optional[pd.DataFrame]:
    if not result_b64:
        return None
    raw = base64.b64decode(result_b64)
    if (result_format or "arrow") == "arrow":
        import pyarrow as pa  # lazy
        with pa.ipc.open_stream(io.BytesIO(raw)) as reader:
            return reader.read_all().to_pandas()
    # JSON fallback: orient='split' preserves column order
    return pd.read_json(io.BytesIO(raw), orient="split")


def _job_supported(code: str, excel_files: List, loadables: Optional[Dict]) -> bool:
    """v0: only pure ds_clients+pandas jobs run remotely."""
    if excel_files:
        return False
    if loadables:
        return False
    m = _HTTP_PARAM_RE.search(code or "")
    if m and "http" in [p.strip().split("=")[0].strip() for p in m.group(1).split(",")]:
        return False  # web-fetch jobs stay server-side in v0
    return True


def _set_prov(provenance_out: Optional[Dict[str, Any]], **fields) -> None:
    """Best-effort provenance stamp. Never raises — a badge must not be able to
    break execution dispatch."""
    if provenance_out is None:
        return
    try:
        provenance_out.update(fields)
    except Exception:  # pragma: no cover - defensive
        pass



async def _org_allows_local_runtime(db, organization_id) -> bool:
    """The organisation-level switch, set by an admin.

    Distinct from the per-device `run_local_enabled`: this decides whether the
    org has the feature at all. Unset means on — an org that has never seen the
    setting keeps working exactly as before. Any lookup failure also means on,
    so a settings hiccup degrades to today's behaviour rather than silently
    moving everyone's work back to the server.
    """
    try:
        from sqlalchemy import select as _select
        from app.models.organization_settings import OrganizationSettings
        row = (await db.execute(
            _select(OrganizationSettings).where(
                OrganizationSettings.organization_id == str(organization_id),
                OrganizationSettings.deleted_at.is_(None),
            )
        )).scalars().first()
        cfg = (getattr(row, "config", None) or {}) if row is not None else {}
        entry = cfg.get("local_runtime")
        if isinstance(entry, dict) and "value" in entry:
            return bool(entry["value"])
        return True
    except Exception:
        logger.warning("[local_runtime] org switch lookup failed; assuming enabled", exc_info=True)
        return True


async def try_run_remote(
    *,
    usage_context,
    code: str,
    ds_clients: Dict,
    excel_files: List,
    loadables: Optional[Dict] = None,
    provenance_out: Optional[Dict[str, Any]] = None,
    require_local: bool = False,
    local_folders: Optional[List[str]] = None,
) -> Optional[Tuple[pd.DataFrame, str, List[str]]]:
    """Attempt remote execution. None => caller falls back to server sandbox.

    ``provenance_out``, when given, is filled in place with where the code
    actually ran: ``{"executed_on": "local"|"server", ...}``. It is left
    UNTOUCHED (i.e. stays empty) when the user has no paired runtime at all —
    that user gets no badge, so nothing about their chat changes.

    ``require_local`` flips the failure contract for jobs that read a folder on
    the user's device: there is no server fallback for those (the server has no
    such client), so every "return None" becomes a LocalFolderUnavailable with
    an explanation the agent can relay. ``local_folders`` names the folders the
    code needs so the helper can say which one is missing.
    """
    folder_label = (local_folders or ["your local folder"])[0]

    def _unavailable(message: str):
        if require_local:
            raise LocalFolderUnavailable(message)
        return None

    if usage_context is None:
        return _unavailable(
            f"'{folder_label}' lives on your computer and this run has no device context, "
            "so it cannot be queried."
        )
    session_maker = getattr(usage_context, "session_maker", None)
    user_id = getattr(usage_context, "user_id", None)
    org_id = getattr(usage_context, "organization_id", None)
    if not (session_maker and user_id and org_id):
        return _unavailable(
            f"'{folder_label}' lives on your computer and this run has no device context, "
            "so it cannot be queried."
        )

    # Lazy import: avoid model imports at module import time in the executor.
    from app.models.local_runtime import LocalRuntime, LocalRuntimeJob

    try:
        async with session_maker() as db:
            # Look up the paired device WITHOUT the run_local_enabled filter so
            # a user who paired but toggled local execution off is still known
            # (provenance reason 'disabled'). The routing decision below is the
            # same as before: only paired + enabled + fresh heartbeat runs.
            rt = (await db.execute(
                select(LocalRuntime).where(
                    LocalRuntime.user_id == str(user_id),
                    LocalRuntime.status == "paired",
                    LocalRuntime.deleted_at.is_(None),
                ).order_by(LocalRuntime.created_at.desc())
            )).scalars().first()
            if not rt:
                if require_local:
                    raise LocalFolderUnavailable(
                        f"'{folder_label}' is a folder on your computer, but no CityAgent Helper "
                        "is paired with your account. Pair one in Settings → Local Runtime to "
                        "query it."
                    )
                return None  # no paired device -> no badge, no change
            runtime_name = rt.name or None
            _set_prov(provenance_out, executed_on="server", runtime_name=runtime_name)
            # Admin switch first: if the org has Local Runtime turned off, no
            # device runs anything, whatever the user's own toggle says.
            if not await _org_allows_local_runtime(db, rt.organization_id):
                _set_prov(provenance_out, reason="org_disabled")
                return _unavailable(
                    f"'{folder_label}' can only be read on {runtime_name or 'your computer'}, but "
                    "Local Runtime is switched off for this organisation. Ask an administrator to "
                    "turn it on."
                )
            if not rt.run_local_enabled:
                _set_prov(provenance_out, reason="disabled")
                return _unavailable(
                    f"'{folder_label}' can only be read on {runtime_name or 'your computer'}, but "
                    "'run on my computer' is switched off. Turn it back on in Settings → Local "
                    "Runtime to query this folder."
                )
            if not _job_supported(code, excel_files, loadables):
                _set_prov(provenance_out, reason="unsupported")
                return _unavailable(
                    f"This step mixes '{folder_label}' (on your computer) with uploaded Excel files "
                    "or a previously loaded step, which the local runtime cannot do yet. Query the "
                    "folder on its own, or use data that lives in a connected source."
                )
            if not rt.last_seen or (datetime.utcnow() - rt.last_seen).total_seconds() > ONLINE_WINDOW_S:
                _set_prov(provenance_out, reason="offline")
                return _unavailable(
                    f"Your device is offline — open CityAgent Helper on "
                    f"{runtime_name or 'your computer'} to query '{folder_label}'."
                )
            job = LocalRuntimeJob(
                runtime_id=str(rt.id),
                organization_id=str(org_id),
                user_id=str(user_id),
                status="pending",
                code=code,
                payload_json=json.dumps({
                    "client_keys": sorted(ds_clients.keys()),
                    # Names only — the helper already knows the paths it shares.
                    "local_folders": list(local_folders or []),
                }),
            )
            db.add(job)
            await db.commit()
            job_id = str(job.id)
    except LocalFolderUnavailable:
        raise  # deliberate, explained refusal — not a fallback candidate
    except Exception as e:
        logger.warning("local-runtime enqueue failed, falling back to server: %s", e)
        # Only stamp if we already know the user owns a device — a failure in
        # the lookup itself must not conjure a badge for someone with none.
        if provenance_out:
            _set_prov(provenance_out, reason="failed")
        return _unavailable(
            f"Could not reach your computer to query '{folder_label}'. Check that CityAgent "
            "Helper is running, then try again."
        )

    _dispatch_started = _time.monotonic()
    deadline = datetime.utcnow() + timedelta(seconds=REMOTE_TIMEOUT_S)
    try:
        while datetime.utcnow() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            async with session_maker() as db:
                row = (await db.execute(
                    select(LocalRuntimeJob).where(LocalRuntimeJob.id == job_id)
                )).scalars().first()
                if row is None:
                    _set_prov(provenance_out, reason="failed")
                    return _unavailable(
                        f"The job that reads '{folder_label}' from your computer disappeared "
                        "before it finished. Try again."
                    )
                if row.status == "done":
                    df = _decode_df(row.result_b64, row.result_format)
                    if df is None:
                        _set_prov(provenance_out, reason="failed")
                        return _unavailable(
                            f"Your computer returned an unreadable result for '{folder_label}'. "
                            "Try again."
                        )
                    queries = json.loads(row.queries_json or "[]")
                    logger.info("local-runtime job %s done on device (rows=%s)", job_id, len(df))
                    _set_prov(
                        provenance_out,
                        executed_on="local",
                        runtime_name=runtime_name,
                        job_id=job_id,
                        elapsed_ms=round((_time.monotonic() - _dispatch_started) * 1000.0, 1),
                    )
                    return df, (row.stdout or ""), list(queries)
                if row.status == "error":
                    # Return the error to the normal retry loop by raising the
                    # same shape the sandbox would: the seam treats exceptions
                    # as fallback, so v0 = fall back to server for correctness.
                    logger.info("local-runtime job %s errored on device: %s", job_id, (row.error or "")[:200])
                    _set_prov(provenance_out, reason="failed")
                    if require_local:
                        # The device's own error is the most useful thing we can
                        # give the agent — it names the missing folder or file.
                        detail = (row.error or "").strip().splitlines()[0][:400] if row.error else ""
                        raise LocalFolderUnavailable(
                            f"Reading '{folder_label}' on your computer failed"
                            + (f": {detail}" if detail else ".")
                        )
                    return None
        # timeout: expire the job so a slow helper doesn't run it twice
        async with session_maker() as db:
            row = (await db.execute(
                select(LocalRuntimeJob).where(LocalRuntimeJob.id == job_id)
            )).scalars().first()
            if row is not None and row.status in ("pending", "running"):
                row.status = "expired"
                await db.commit()
        logger.info("local-runtime job %s timed out; falling back to server", job_id)
        _set_prov(provenance_out, reason="timeout")
        return _unavailable(
            f"Your computer did not answer in time while reading '{folder_label}'. Check that "
            "CityAgent Helper is still running, then try again."
        )
    except LocalFolderUnavailable:
        raise
    except Exception as e:
        logger.warning("local-runtime polling failed, falling back to server: %s", e)
        _set_prov(provenance_out, reason="failed")
        return _unavailable(
            f"Lost contact with your computer while reading '{folder_label}'. Check that "
            "CityAgent Helper is still running, then try again."
        )


# --------------------------------------------------------------------------- #
#  read_local_document — pull a document's text from the user's device
# --------------------------------------------------------------------------- #

DOC_READ_TIMEOUT_S = 60


async def read_local_document_remote(
    *,
    db,
    user_id: str,
    org_id: str,
    folder: str,
    file: str,
) -> Dict[str, Any]:
    """Queue a read_document job on the user's paired helper and wait for the
    extracted text. The document itself never leaves the device — the helper
    extracts text locally and returns only that (capped there).

    Returns {"ok": True, "text": str, "meta": {...}} or {"ok": False, "error": str}.
    Uses the caller's db session for enqueue and fresh reads for polling.
    """
    from app.models.local_runtime import LocalRuntime, LocalRuntimeJob

    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user_id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        ).order_by(LocalRuntime.created_at.desc())
    )).scalars().first()
    if not rt:
        return {"ok": False, "error": (
            f"'{file}' is on the user's computer, but no CityAgent Helper is paired. "
            "They can pair one in Settings → Local Runtime."
        )}
    if not rt.last_seen or (datetime.utcnow() - rt.last_seen).total_seconds() > ONLINE_WINDOW_S:
        return {"ok": False, "error": (
            f"The user's device is offline — CityAgent Helper must be running on "
            f"{rt.name or 'their computer'} to read '{file}'."
        )}
    # The folder must actually be shared (server-side backstop; the helper
    # validates again on its side).
    try:
        shared = {f.get("name") for f in json.loads(rt.folders_schema or "[]") if isinstance(f, dict)}
    except Exception:
        shared = set()
    if folder not in shared:
        return {"ok": False, "error": f"Folder '{folder}' is not shared from the user's device."}

    job = LocalRuntimeJob(
        runtime_id=str(rt.id),
        organization_id=str(org_id),
        user_id=str(user_id),
        status="pending",
        code=None,
        payload_json=json.dumps({"kind": "read_document", "folder": folder, "file": file}),
    )
    db.add(job)
    await db.commit()
    job_id = str(job.id)

    deadline = datetime.utcnow() + timedelta(seconds=DOC_READ_TIMEOUT_S)
    while datetime.utcnow() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        row = (await db.execute(
            select(LocalRuntimeJob).where(LocalRuntimeJob.id == job_id)
        )).scalars().first()
        if row is None:
            return {"ok": False, "error": "The document-read job disappeared before finishing."}
        await db.refresh(row)
        if row.status == "done":
            return {"ok": True, "text": row.stdout or "",
                    "runtime_name": rt.name, "job_id": job_id}
        if row.status == "error":
            return {"ok": False, "error": (row.error or "Reading the document failed on the device.")[:500]}
    # timeout — expire so a slow helper doesn't double-run
    row = (await db.execute(
        select(LocalRuntimeJob).where(LocalRuntimeJob.id == job_id)
    )).scalars().first()
    if row is not None and row.status in ("pending", "running"):
        row.status = "expired"
        await db.commit()
    return {"ok": False, "error": (
        f"The user's computer did not answer in time while reading '{file}'. "
        "CityAgent Helper may be busy or stopped."
    )}
