from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.dependencies import get_db
from typing import Optional
import os

from app.services.file_service import FileService
from app.schemas.file_schema import FileSchema, FileSchemaWithMetadata, FileSchemaWithCompletionId
from app.models.user import User
from app.models.file import File as FileModel
from app.core.auth import current_user
from app.models.organization import Organization
from app.dependencies import get_current_organization
from fastapi import Form
from app.core.permissions_decorator import requires_permission, requires_resource_permission
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_async_db
from app.models.report import Report
from app.ee.audit.service import audit_service

router = APIRouter(tags=["files"])
file_service = FileService()

@router.post("/files", response_model=FileSchema)
@requires_permission('manage_files')
async def upload_file(request: Request, file: UploadFile = File(...), report_id: Optional[str] = Form(None), data_source_id: Optional[str] = Form(None), current_user: User = Depends(current_user), db: AsyncSession = Depends(get_async_db), organization: Organization = Depends(get_current_organization)):
    result = await file_service.upload_file(db, file, current_user, organization, report_id, data_source_id)
    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="file.uploaded",
            user_id=current_user.id,
            resource_type="file",
            resource_id=result.id,
            details={"filename": file.filename, "content_type": file.content_type, "data_source_id": data_source_id},
            request=request,
        )
    except Exception:
        pass
    # Silent session event: user uploaded a file to this report (report-scoped
    # uploads only — data-source uploads are not part of a conversation).
    if report_id:
        from types import SimpleNamespace
        from app.services.session_event_service import SessionEventService
        from app.ai.context.session_events import FILE_UPLOADED
        await SessionEventService.emit_safe(
            db, report=SimpleNamespace(id=report_id), kind=FILE_UPLOADED,
            user=current_user,
            meta={"filename": file.filename, "file_id": str(result.id),
                  "content_type": file.content_type},
            target_type="file", target_id=str(result.id),
        )
    return result

@router.post("/data_sources/{data_source_id}/files", response_model=FileSchema)
@requires_resource_permission('data_source', 'manage')
async def upload_data_source_file(
    request: Request,
    data_source_id: str,
    file: UploadFile = File(...),
    learn: bool = True,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    # ``learn`` (default True) controls whether this upload schedules the
    # background onboarding-overview re-learn. The frontend sends learn=false for
    # every file of a multi-file batch EXCEPT the last one, so the agent learns
    # once per batch instead of once per file. The synchronous schema refresh
    # (which makes the uploaded table queryable) runs regardless.
    result = await file_service.upload_file(db, file, current_user, organization, None, data_source_id, learn=learn)
    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="file.uploaded",
            user_id=current_user.id,
            resource_type="file",
            resource_id=result.id,
            details={"filename": file.filename, "content_type": file.content_type, "data_source_id": data_source_id},
            request=request,
        )
    except Exception:
        pass
    return result

@router.post("/data_sources/{data_source_id}/files/{file_id}/reingest", response_model=dict)
@requires_resource_permission('data_source', 'manage')
async def reingest_data_source_file(
    request: Request,
    data_source_id: str,
    file_id: str,
    destination: str | None = None,
    keep_existing: bool = False,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Re-run LLM classification + routing on an already-uploaded file.

    Same permission as uploading/managing a data-source file. Flag-gated behind
    ``smart_file_intake`` (returns ``skipped`` when OFF). Shares the exact intake
    path used at upload time, so a file uploaded before smart intake existed can
    be routed to table / instruction / skill / knowledge.

    Pass ``?destination=instruction|skill|knowledge|table`` to convert the file
    rather than re-classify it: the caller's choice is taken as final and the
    classifier is skipped. The rewriters still run, so the result is written up
    as proper rules or a proper procedure rather than pasted in raw.

    Converting REPLACES what the file produced before. Pass
    ``&keep_existing=true`` to add the new filing alongside the old one — for a
    document that genuinely serves as both, say a Q&A whose definitions belong in
    an instruction while the full text stays searchable as knowledge.
    """
    result = await file_service.reingest_file(
        db, file_id, data_source_id, organization, current_user,
        destination=destination, keep_existing=keep_existing,
    )
    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="file.reingested",
            user_id=current_user.id,
            resource_type="file",
            resource_id=file_id,
            details={"data_source_id": data_source_id, **{k: v for k, v in result.items() if k != "file_id"}},
            request=request,
        )
    except Exception:
        pass
    return result

@router.get("/data_sources/{data_source_id}/files", response_model=list[FileSchema])
@requires_resource_permission('data_source', 'view')
async def get_files_by_data_source(
    data_source_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    return await file_service.get_files_by_data_source(db, data_source_id, organization)

@router.delete("/data_sources/{data_source_id}/files/{file_id}")
@requires_resource_permission('data_source', 'manage')
async def remove_file_from_data_source(
    file_id: str,
    data_source_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    return await file_service.remove_file_from_data_source(db, file_id, data_source_id, organization, current_user)

@router.get("/reports/{report_id}/files", response_model=list[FileSchemaWithCompletionId])
@requires_permission('manage_files', model=Report)
async def get_files_by_report(report_id: str, current_user: User = Depends(current_user), db: AsyncSession = Depends(get_async_db), organization: Organization = Depends(get_current_organization)):
    # ★`manage_files` is a HIDDEN baseline permission every member holds, and the
    # gate scopes the report to the organization only — so without this a member
    # could list the attachments of a report they are refused when they ask for
    # the report itself (filenames alone leak plenty).
    from app.core.report_access import assert_report_visible
    await assert_report_visible(db, report_id, current_user, organization)
    return await file_service.get_files_by_report(db, report_id, organization)

@router.delete("/reports/{report_id}/files/{file_id}")
@requires_permission('manage_files', model=Report)
async def remove_file_from_report(file_id: str, report_id: str, current_user: User = Depends(current_user), db: AsyncSession = Depends(get_async_db), organization: Organization = Depends(get_current_organization)):
    # Capture the filename before removal for the event text.
    _f = await db.get(FileModel, file_id)
    _fname = getattr(_f, "filename", None) if _f is not None else None
    result = await file_service.remove_file_from_report(db, file_id, report_id, organization, current_user)
    from types import SimpleNamespace
    from app.services.session_event_service import SessionEventService
    from app.ai.context.session_events import FILE_REMOVED
    await SessionEventService.emit_safe(
        db, report=SimpleNamespace(id=report_id), kind=FILE_REMOVED,
        user=current_user,
        meta={"filename": _fname, "file_id": str(file_id)},
        target_type="file", target_id=str(file_id),
    )
    return result

@router.get("/files", response_model=list[FileSchemaWithMetadata])
@requires_permission('manage_files')
async def get_files(current_user: User = Depends(current_user), db: AsyncSession = Depends(get_async_db), organization: Organization = Depends(get_current_organization)):
    return await file_service.get_files(db, organization)

@router.get("/files/{file_id}/content")
@requires_permission('manage_files')
async def get_file_content(file_id: str, request: Request, current_user: User = Depends(current_user), db: AsyncSession = Depends(get_async_db), organization: Organization = Depends(get_current_organization)):
    """Serve file content (for displaying images in chat)."""
    # file_id must be a UUID — reject anything else at the entry so the
    # parameter cannot smuggle path characters further down (Snyk python/PT).
    import uuid as _uuid
    try:
        _uuid.UUID(file_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="File not found")

    stmt = select(FileModel).filter(FileModel.id == file_id, FileModel.organization_id == organization.id)
    result = await db.execute(stmt)
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # ★Org scope is not access. `manage_files` is a hidden baseline permission
    # every member holds, so without this a member could download any file in
    # the organization — including attachments on a report they are refused.
    from app.core.file_access import user_may_read_file
    if not await user_may_read_file(db, file, current_user, organization):
        raise HTTPException(status_code=404, detail="File not found")

    if not file.path:
        raise HTTPException(status_code=404, detail="File content not found")

    # Path-traversal guard at the sink. Uploaded files (and tool outputs) are
    # always stored flat under uploads/files/, so rebuild the path to open from
    # the trusted root plus the sanitized basename. os.path.basename strips any
    # directory-traversal sequences, so a tampered DB value can never make
    # open() escape uploads/files/.
    safe_path = os.path.join(os.getcwd(), "uploads", "files", os.path.basename(file.path))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404, detail="File content not found")

    try:
        await audit_service.log(
            db=db,
            organization_id=organization.id,
            action="file.downloaded",
            user_id=current_user.id,
            resource_type="file",
            resource_id=file_id,
            details={"filename": file.filename},
            request=request,
        )
    except Exception:
        pass

    # safe_path is verified above to live under uploads/ — read its bytes and
    # serve as an in-memory response so no path string is handed to a
    # framework file API.
    with open(safe_path, "rb") as _fh:
        content = _fh.read()
    return Response(
        content=content,
        media_type=file.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
        },
    )


def _read_file_bytes_or_404(file: FileModel) -> bytes:
    """Read a stored file's bytes from the trusted uploads root (path-traversal
    guarded), or raise 404. Shared by the authed and token-gated servers."""
    if not file.path:
        raise HTTPException(status_code=404, detail="File content not found")
    safe_path = os.path.join(os.getcwd(), "uploads", "files", os.path.basename(file.path))
    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404, detail="File content not found")
    with open(safe_path, "rb") as _fh:
        return _fh.read()


@router.get("/files/{file_id}/text")
@requires_permission('manage_files')
async def get_file_text(
    file_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """The readable text of a document, for previewing it in the UI.

    Clicking a Word or PowerPoint file showed "No inline preview for this file
    type" and offered a download — while the very same text was already
    extracted, cleaned and stored as retrievable knowledge chunks. This serves
    what the agent reads, so a user can check the document the agent is
    reasoning from without leaving the page.

    Deliberately NOT a renderer: no styling, tables or images, just text. The
    extractor is the one the ingest path uses, including its OOXML scrub —
    rendering .docx faithfully is a different and much larger job, and offering
    an honest text view now beats offering nothing.
    """
    import uuid as _uuid

    try:
        _uuid.UUID(file_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="File not found")

    stmt = select(FileModel).filter(
        FileModel.id == file_id, FileModel.organization_id == organization.id
    )
    file = (await db.execute(stmt)).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    # ★Same rule as /content and /embed_token — org membership is not access.
    # The extracted text is the document's contents; refusing the bytes while
    # serving the words would be a distinction without a difference.
    from app.core.file_access import user_may_read_file
    if not await user_may_read_file(db, file, current_user, organization):
        raise HTTPException(status_code=404, detail="File not found")

    # Reuses the shared reader, so this endpoint inherits the path-traversal
    # guard rather than carrying a second copy of it that could drift.
    data = _read_file_bytes_or_404(file)

    from app.data_sources.clients._document_text import extract_document_text_from_bytes

    text = extract_document_text_from_bytes(data, file.filename or "") or ""
    return {
        "file_id": file_id,
        "filename": file.filename,
        "content_type": file.content_type,
        # The extractor returns "" for anything it cannot read — an image, a
        # corrupt archive, a format it has no branch for. Said explicitly so the
        # UI can distinguish "nothing to show" from an empty document, instead
        # of rendering a blank panel that looks like a failure.
        "extractable": bool(text.strip()),
        "text": text,
    }


@router.get("/files/{file_id}/embed_token")
@requires_permission('manage_files')
async def get_file_embed_token(
    file_id: str,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
):
    """Mint a short-lived, file-scoped capability token for embedding.

    The token lets an artifact sandbox iframe (which can't send an auth header)
    load this file via GET /files/{id}/embed?token=… ; it is never persisted
    (minted fresh per render).

    ★This used to authorize on organization membership alone, and said so. That
    is not enough: `manage_files` is a HIDDEN baseline permission held by every
    member, and the minted token is a BEARER CREDENTIAL that needs no session at
    all. Measured 2026-08-09 — a member minted a token for a file on another
    member's report and fetched 858KB of it with no Authorization header.
    Minting now requires the same access as reading the file directly."""
    import uuid as _uuid
    try:
        _uuid.UUID(file_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="File not found")

    file = (await db.execute(
        select(FileModel).filter(FileModel.id == file_id, FileModel.organization_id == organization.id)
    )).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    from app.core.file_access import user_may_read_file
    if not await user_may_read_file(db, file, current_user, organization):
        # 404, not 403: the caller cannot see this file, so confirming it exists
        # would make the route an id oracle.
        raise HTTPException(status_code=404, detail="File not found")

    from app.core.file_tokens import mint_file_token, file_embed_url
    token = mint_file_token(file_id)
    return {"file_id": file_id, "token": token, "url": file_embed_url(file_id, token)}


@router.get("/files/{file_id}/embed")
async def get_file_embed(
    file_id: str,
    token: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Serve a file's bytes when presented a valid capability token — no session.

    Used by artifact sandboxes and published-report pages. The token is a
    capability for exactly this file id; org membership is NOT required because
    access was already authorized when the token was minted."""
    import uuid as _uuid
    try:
        _uuid.UUID(file_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="File not found")

    from app.core.file_tokens import verify_file_token
    if not verify_file_token(token, file_id):
        raise HTTPException(status_code=403, detail="Invalid or expired file token")

    # Load by id only — the token is the capability, not the session.
    file = (await db.execute(
        select(FileModel).filter(FileModel.id == file_id)
    )).scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    content = _read_file_bytes_or_404(file)
    return Response(
        content=content,
        media_type=file.content_type or "application/octet-stream",
        headers={
            # inline so <img>/<iframe> render it rather than downloading
            "Content-Disposition": f'inline; filename="{file.filename or file_id}"',
            "Cache-Control": "private, max-age=300",
        },
    )