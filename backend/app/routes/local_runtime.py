"""Local runtime (Cowork-style) pairing + job queue routes.

A user pairs a small local helper with a 6-digit code; the helper gets a
long-lived runtime token and then long-polls for execution jobs over HTTP.
Everything is DB-backed (multi-worker safe — no in-process state). All
endpoints 403 when the HYBRID_LOCAL_RUNTIME flag is off.

Security model:
- User endpoints use the normal session auth.
- Helper endpoints authenticate with the runtime token (sha256 stored).
- Credentials for data sources NEVER go to the helper; the helper calls the
  query proxy (added in P4) and the server runs the real connector.
"""
import hashlib
import json
import secrets
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Body
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_async_db, get_current_organization, release_request_db
from app.core.auth import current_user
from app.models.user import User
from app.models.organization import Organization
from app.models.local_runtime import LocalRuntime, LocalRuntimeJob
from app.settings.config import settings

router = APIRouter(tags=["local_runtime"])


def _flag_or_403():
    if not getattr(settings, "hybrid_local_runtime", False):
        raise HTTPException(status_code=403, detail="Local runtime is not enabled")



async def _org_gate(db, organization) -> None:
    """403 when an admin has switched Local Runtime off for the organisation.

    The env flag (`_flag_or_403`) says whether the build has the feature; this
    says whether this org chose to have it. Both must pass. Paired devices are
    left untouched — turning the switch back on restores them.
    """
    from app.services.local_runtime_exec import _org_allows_local_runtime
    if not await _org_allows_local_runtime(db, getattr(organization, "id", organization)):
        raise HTTPException(status_code=403, detail="Local runtime is disabled for this organization")


def _folders_gate(organization) -> None:
    """403 unless an admin has released shared folders to members.

    Separate from `_org_gate`: an organisation can run Local Runtime (analyses
    execute on the member's machine against SERVER data) without also letting
    members expose their own local folders to the agent, which is a different
    and larger decision about what data reaches the product.
    """
    from app.core.access_gate import require_access
    require_access(organization, "local_folders", "Shared folders")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def _runtime_from_token(db: AsyncSession, authorization: Optional[str]) -> LocalRuntime:
    """Resolve + authenticate a helper by its runtime token."""
    _flag_or_403()
    token = (authorization or "").replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing runtime token")
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.token_hash == _sha(token),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        )
    )).scalars().first()
    if not rt:
        raise HTTPException(status_code=401, detail="Invalid runtime token")
    return rt


# --------------------------------------------------------------------------- #
#  User-facing endpoints (session auth)
# --------------------------------------------------------------------------- #

@router.post("/local-runtime/pair/start")
async def pair_start(
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(current_user),
):
    """Mint a 6-digit pairing code (10 min TTL). Replaces any prior pending row."""
    _flag_or_403()
    # Drop stale pending rows for this user
    olds = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "pending",
            LocalRuntime.deleted_at.is_(None),
        )
    )).scalars().all()
    for o in olds:
        o.status = "revoked"
    code = f"{secrets.randbelow(1000000):06d}"
    rt = LocalRuntime(
        organization_id=str(organization.id),
        user_id=str(user.id),
        status="pending",
        pair_code_hash=_sha(code),
        pair_expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.add(rt)
    await db.commit()
    _result = {"code": code, "expires_in_s": 600}
    await release_request_db(db)
    return _result


def _folder_flag_or_403():
    """Folder attach rides on top of the local runtime and has its own flag."""
    _flag_or_403()
    if not getattr(settings, "hybrid_local_folder_attach", False):
        raise HTTPException(status_code=403, detail="Local folder attach is not enabled")


def _load_folders(rt: LocalRuntime) -> list:
    try:
        data = json.loads(rt.folders_schema or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


class ClaimBody(BaseModel):
    code: str
    name: Optional[str] = None
    helper_version: Optional[str] = None


@router.post("/local-runtime/pair/claim")
async def pair_claim(body: ClaimBody, db: AsyncSession = Depends(get_async_db)):
    """Helper redeems the pairing code → gets its long-lived runtime token.
    Public endpoint by design (the helper has no session); the secret is the
    short-lived code itself."""
    _flag_or_403()
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.pair_code_hash == _sha((body.code or "").strip()),
            LocalRuntime.status == "pending",
            LocalRuntime.deleted_at.is_(None),
        )
    )).scalars().first()
    if not rt or not rt.pair_expires_at or rt.pair_expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code")
    token = secrets.token_urlsafe(32)
    rt.token_hash = _sha(token)
    rt.status = "paired"
    rt.pair_code_hash = None
    rt.pair_expires_at = None
    rt.name = (body.name or "Local helper")[:120]
    rt.helper_version = (body.helper_version or "")[:40]
    rt.last_seen = datetime.utcnow()
    await db.commit()
    _result = {"runtime_id": str(rt.id), "token": token}
    await release_request_db(db)
    return _result


@router.get("/local-runtime/status")
async def runtime_status(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    _flag_or_403()
    await _org_gate(db, organization)
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        ).order_by(LocalRuntime.created_at.desc())
    )).scalars().first()
    if not rt:
        _result = {"paired": False}
    else:
        online = bool(rt.last_seen and (datetime.utcnow() - rt.last_seen).total_seconds() < 30)
        _result = {
            "paired": True,
            "runtime_id": str(rt.id),
            "name": rt.name,
            "online": online,
            "last_seen": rt.last_seen.isoformat() if rt.last_seen else None,
            "run_local_enabled": bool(rt.run_local_enabled),
            "helper_version": rt.helper_version,
        }
    await release_request_db(db)
    return _result


@router.put("/local-runtime/toggle")
async def runtime_toggle(
    enabled: bool = Body(..., embed=True),
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
):
    _flag_or_403()
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        )
    )).scalars().first()
    if not rt:
        raise HTTPException(status_code=404, detail="No paired runtime")
    rt.run_local_enabled = bool(enabled)
    await db.commit()
    _result = {"run_local_enabled": rt.run_local_enabled}
    await release_request_db(db)
    return _result


@router.delete("/local-runtime")
async def runtime_unpair(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
):
    _flag_or_403()
    rts = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.deleted_at.is_(None),
        )
    )).scalars().all()
    for rt in rts:
        rt.status = "revoked"
        rt.token_hash = None
    await db.commit()
    _result = {"revoked": len(rts)}
    await release_request_db(db)
    return _result


@router.get("/local-runtime/folders")
async def list_folders(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Folders the current user's helper has shared, with their table schemas.
    Feeds the composer's "Attach local folder" menu. Schema only — the server
    has never seen a single row of these files."""
    _folder_flag_or_403()
    _folders_gate(organization)
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        ).order_by(LocalRuntime.created_at.desc())
    )).scalars().first()
    if not rt:
        _result = {"paired": False, "online": False, "folders": []}
    else:
        online = bool(rt.last_seen and (datetime.utcnow() - rt.last_seen).total_seconds() < 30)
        folders = []
        for f in _load_folders(rt):
            tables = f.get("tables") or []
            documents = f.get("documents") or []
            folders.append({
                "name": f.get("name"),
                "key": f"local:{f.get('name')}",
                "path": f.get("path"),
                "table_count": len(tables),
                "tables": tables,
                "doc_count": len(documents),
                "documents": documents,
                "error": f.get("error"),
            })
        _result = {
            "paired": True,
            "online": online,
            "device_name": rt.name,
            "run_local_enabled": bool(rt.run_local_enabled),
            "scanned_at": rt.folders_scanned_at.isoformat() if rt.folders_scanned_at else None,
            "folders": folders,
        }
    await release_request_db(db)
    return _result


class FolderRequestBody(BaseModel):
    # Either an explicit path typed in the browser, or pick=true to open the
    # native folder chooser on the user's device (no typing).
    path: Optional[str] = None
    pick: bool = False


@router.post("/local-runtime/folders/request")
async def request_folder(
    body: FolderRequestBody,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Ask the CURRENT USER'S paired helper to share a folder ("Add folder"
    from the chat paperclip menu).

    The server never touches the path itself — it only queues an `add_folder`
    job on the user's own runtime; the helper on their machine validates the
    path, updates its own whitelist, scans (schema only) and publishes. Any
    other user's runtime can never be targeted (runtime looked up by user_id).
    """
    _folder_flag_or_403()
    _folders_gate(organization)
    path = (body.path or "").strip()
    if (not path and not body.pick) or len(path) > 1024:
        raise HTTPException(status_code=400, detail="A folder path (or pick=true) is required")
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        ).order_by(LocalRuntime.created_at.desc())
    )).scalars().first()
    if not rt:
        raise HTTPException(status_code=404, detail="No paired device")
    online = bool(rt.last_seen and (datetime.utcnow() - rt.last_seen).total_seconds() < 30)
    if not online:
        raise HTTPException(status_code=409, detail="Your device is offline — start the helper first")
    job = LocalRuntimeJob(
        runtime_id=str(rt.id),
        organization_id=str(rt.organization_id),
        user_id=str(user.id),
        status="pending",
        code=None,
        payload_json=json.dumps({"kind": "add_folder", "path": path, "pick": bool(body.pick and not path)}),
    )
    db.add(job)
    await db.commit()
    _result = {"job_id": str(job.id), "queued": True}
    await release_request_db(db)
    return _result


@router.delete("/local-runtime/folders/{folder_name}")
async def unshare_folder(
    folder_name: str,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
):
    """Queue a stop-sharing request for one folder (by name) on the CURRENT
    USER'S paired helper. Same trust model as adding: the server only asks,
    the user's machine updates its own whitelist and republishes."""
    # Deliberately NOT behind _folders_gate: stopping a share is the safe
    # direction. If an admin switches folders off, a member must still be able
    # to withdraw one they had already exposed.
    _folder_flag_or_403()
    name = (folder_name or "").strip()
    if not name or len(name) > 512:
        raise HTTPException(status_code=400, detail="A folder name is required")
    rt = (await db.execute(
        select(LocalRuntime).where(
            LocalRuntime.user_id == str(user.id),
            LocalRuntime.status == "paired",
            LocalRuntime.deleted_at.is_(None),
        ).order_by(LocalRuntime.created_at.desc())
    )).scalars().first()
    if not rt:
        raise HTTPException(status_code=404, detail="No paired device")
    online = bool(rt.last_seen and (datetime.utcnow() - rt.last_seen).total_seconds() < 30)
    if not online:
        raise HTTPException(status_code=409, detail="Your device is offline — start the helper first")
    job = LocalRuntimeJob(
        runtime_id=str(rt.id),
        organization_id=str(rt.organization_id),
        user_id=str(user.id),
        status="pending",
        code=None,
        payload_json=json.dumps({"kind": "remove_folder", "name": name}),
    )
    db.add(job)
    await db.commit()
    _result = {"job_id": str(job.id), "queued": True}
    await release_request_db(db)
    return _result


@router.get("/local-runtime/folders/request/{job_id}")
async def folder_request_status(
    job_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
    organization: Organization = Depends(get_current_organization),
):
    """Poll an add-folder job's outcome (only the requesting user's own jobs)."""
    _folder_flag_or_403()
    _folders_gate(organization)
    job = (await db.execute(
        select(LocalRuntimeJob).where(
            LocalRuntimeJob.id == job_id,
            LocalRuntimeJob.user_id == str(user.id),
            LocalRuntimeJob.deleted_at.is_(None),
        )
    )).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _result = {"status": job.status, "message": job.stdout or None, "error": job.error or None}
    await release_request_db(db)
    return _result


# --------------------------------------------------------------------------- #
#  Helper-facing endpoints (runtime-token auth)
# --------------------------------------------------------------------------- #

class FolderColumn(BaseModel):
    name: str
    type: Optional[str] = None


class FolderTable(BaseModel):
    name: str
    file: Optional[str] = None
    format: Optional[str] = None
    row_count: Optional[int] = None
    columns: list = []


class FolderScan(BaseModel):
    name: str
    path: Optional[str] = None
    tables: list = []
    error: Optional[str] = None


class FoldersBody(BaseModel):
    folders: list = []
    helper_version: Optional[str] = None


@router.post("/local-runtime/folders")
async def register_folders(
    body: FoldersBody,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_async_db),
):
    """Helper posts the schema catalog of its whitelisted folders (scan_folder).

    SCHEMA ONLY by contract: names, columns+types, row counts. Anything that
    looks like sample data is dropped here as a server-side backstop, so a
    future/rogue helper build can never smuggle rows in through this endpoint.
    """
    rt = await _runtime_from_token(db, authorization)
    _folder_flag_or_403()
    cleaned = []
    for f in (body.folders or [])[:50]:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip()[:120]
        if not name:
            continue
        tables = []
        for t in (f.get("tables") or [])[:500]:
            if not isinstance(t, dict):
                continue
            cols = []
            for c in (t.get("columns") or [])[:500]:
                if isinstance(c, dict) and c.get("name"):
                    cols.append({"name": str(c["name"])[:200], "type": str(c.get("type") or "")[:60]})
            row_count = t.get("row_count")
            tables.append({
                "name": str(t.get("name") or "")[:200],
                "file": str(t.get("file") or "")[:400],
                "format": str(t.get("format") or "")[:20],
                "row_count": int(row_count) if isinstance(row_count, (int, float)) else None,
                "columns": cols,
            })
        documents = []
        for d in (f.get("documents") or [])[:200]:
            if isinstance(d, dict) and d.get("file"):
                size = d.get("size_bytes")
                documents.append({
                    "file": str(d["file"])[:400],
                    "format": str(d.get("format") or "")[:20],
                    "size_bytes": int(size) if isinstance(size, (int, float)) else None,
                })
        cleaned.append({
            "name": name,
            "path": str(f.get("path") or "")[:400],
            "tables": tables,
            "documents": documents,
            "error": (str(f.get("error"))[:500] if f.get("error") else None),
        })
    rt.folders_schema = json.dumps(cleaned)
    rt.folders_scanned_at = datetime.utcnow()
    rt.last_seen = datetime.utcnow()
    if body.helper_version:
        rt.helper_version = body.helper_version[:40]
    await db.commit()
    _result = {"ok": True, "folders": len(cleaned), "tables": sum(len(f["tables"]) for f in cleaned)}
    await release_request_db(db)
    return _result

@router.post("/local-runtime/heartbeat")
async def heartbeat(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_async_db),
):
    rt = await _runtime_from_token(db, authorization)
    rt.last_seen = datetime.utcnow()
    await db.commit()
    _result = {"ok": True}
    await release_request_db(db)
    return _result


@router.get("/local-runtime/jobs/next")
async def next_job(
    wait_s: int = 20,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_async_db),
):
    """Long-poll for the next pending job (claims it atomically-enough for a
    single helper per runtime: one row, one poller)."""
    rt = await _runtime_from_token(db, authorization)
    deadline = datetime.utcnow() + timedelta(seconds=max(0, min(wait_s, 25)))
    while True:
        rt.last_seen = datetime.utcnow()
        job = (await db.execute(
            select(LocalRuntimeJob).where(
                LocalRuntimeJob.runtime_id == str(rt.id),
                LocalRuntimeJob.status == "pending",
                LocalRuntimeJob.deleted_at.is_(None),
            ).order_by(LocalRuntimeJob.created_at.asc())
        )).scalars().first()
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            await db.commit()
            _result = {
                "job_id": str(job.id),
                "code": job.code,
                "payload": json.loads(job.payload_json or "{}"),
            }
            await release_request_db(db)
            return _result
        await db.commit()  # persist heartbeat
        if datetime.utcnow() >= deadline:
            _result = {"job_id": None}
            await release_request_db(db)
            return _result
        await asyncio.sleep(1.0)


class ProxyQueryBody(BaseModel):
    client_key: str          # "{domain}:{connection}" as seen in ds_clients
    sql: str


@router.post("/local-runtime/query")
async def proxy_query(
    body: ProxyQueryBody,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_async_db),
):
    """Data-fetch proxy for the helper: runs a READ-ONLY query on the real
    server-side connector (credentials + RBAC + quota all stay here) and
    returns the rows as Arrow IPC base64. The helper's proxied ds_clients call
    this instead of holding any credentials."""
    rt = await _runtime_from_token(db, authorization)
    rt.last_seen = datetime.utcnow()

    # Enforce the same read-only SQL policy as the sandbox.
    from app.ai.code_execution.code_execution import validate_sql_query, UnsafeSQLError
    try:
        validate_sql_query(body.sql)
    except UnsafeSQLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    domain = (body.client_key or "").split(":", 1)[0].strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Invalid client_key")

    from app.models.data_source import DataSource
    from app.models.user import User as UserModel
    from sqlalchemy.orm import selectinload
    ds = (await db.execute(
        select(DataSource).options(selectinload(DataSource.connections)).where(
            DataSource.organization_id == rt.organization_id,
            DataSource.name == domain,
            DataSource.deleted_at.is_(None),
        )
    )).scalars().first()
    if not ds:
        raise HTTPException(status_code=404, detail=f"Data source '{domain}' not found")
    user = (await db.execute(
        select(UserModel).where(UserModel.id == rt.user_id)
    )).scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="Runtime user not found")

    # construct_clients applies the RBAC backstop (403 if this user can't
    # access the source) and builds real credentialed clients server-side.
    from app.services.data_source_service import DataSourceService
    clients = await DataSourceService().construct_clients(db, ds, user)
    client = clients.get(body.client_key) or clients.get(domain)
    if client is None:
        raise HTTPException(status_code=404, detail=f"No client for '{body.client_key}'")

    loop = asyncio.get_running_loop()
    try:
        df = await loop.run_in_executor(None, lambda: client.execute_query(body.sql))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query failed: {e}")

    import pandas as pd  # noqa: F401
    import pyarrow as pa
    import io as _io, base64 as _b64
    if df is None:
        raise HTTPException(status_code=400, detail="Query returned no result")
    # Row cap so a runaway SELECT doesn't ship gigabytes through the proxy.
    MAX_ROWS = 250_000
    truncated = False
    if len(df) > MAX_ROWS:
        df = df.head(MAX_ROWS)
        truncated = True
    sink = _io.BytesIO()
    table = pa.Table.from_pandas(df)
    with pa.ipc.new_stream(sink, table.schema) as w:
        w.write_table(table)
    _result = {
        "format": "arrow",
        "rows": int(len(df)),
        "truncated": truncated,
        "data_b64": _b64.b64encode(sink.getvalue()).decode(),
    }
    await db.commit()  # persist last_seen
    await release_request_db(db)
    return _result


class JobResultBody(BaseModel):
    status: str                      # done|error
    result_b64: Optional[str] = None
    result_format: Optional[str] = None   # arrow|json
    stdout: Optional[str] = None
    queries: Optional[list] = None
    error: Optional[str] = None


@router.post("/local-runtime/jobs/{job_id}/result")
async def job_result(
    job_id: str,
    body: JobResultBody,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_async_db),
):
    rt = await _runtime_from_token(db, authorization)
    job = (await db.execute(
        select(LocalRuntimeJob).where(
            LocalRuntimeJob.id == job_id,
            LocalRuntimeJob.runtime_id == str(rt.id),
            LocalRuntimeJob.deleted_at.is_(None),
        )
    )).scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "done" if body.status == "done" else "error"
    job.result_b64 = body.result_b64
    job.result_format = body.result_format
    job.stdout = (body.stdout or "")[:100000]
    job.queries_json = json.dumps(body.queries or [])
    job.error = (body.error or "")[:20000] or None
    job.finished_at = datetime.utcnow()
    await db.commit()
    _result = {"ok": True}
    await release_request_db(db)
    return _result
