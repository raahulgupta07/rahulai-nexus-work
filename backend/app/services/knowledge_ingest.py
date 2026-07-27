"""Knowledge-document ingestion for CityAgent Insights file uploads.

Turns an uploaded reference document (docx / pptx / pdf, or any text the caller
already extracted) into retrievable ``metadata_resources`` rows — the same store
the agent's ``read_resources`` research tool searches. Today knowledge-classed
uploads fall through and are silently dropped; this wires them into the index.

RETRIEVABILITY CONTRACT (must match ``read_resources``)
-------------------------------------------------------
``app/ai/tools/implementations/read_resources.py`` selects
``MetadataResource`` rows where ``is_active == True`` (optionally filtered by
``data_source_id``) and then matches the agent's query regex against each row's
**name** OR **path**. The row's ``description`` (chunk text) is rendered into the
returned excerpt. So every row we create sets:
  * ``is_active = True``
  * ``data_source_id`` + ``organization_id``
  * ``name``  — the document title/stem (shared across chunks, so one query on
                the doc's name/topic matches every part) with a ``(part N/M)``
                suffix for uniqueness
  * ``path``  — the original filename (also regex-matched)
  * ``description`` — the chunk text (rendered in the excerpt)
  * ``resource_type = "knowledge"`` (read_resources does not filter on type)
  * ``chunk_start_line`` / ``chunk_end_line`` — character offsets of the chunk

``metadata_indexing_job_id`` is left NULL (the column is nullable and
read_resources does not require a job unless a git repository filter is passed).

Defensive: never raises — any failure returns 0 rows created so a bad document
never breaks the upload.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# Chunking: ~1000-char windows with a small overlap so a fact split across a
# boundary still survives intact in one chunk.
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150
_MAX_CHUNKS = 400  # hard cap so a pathological doc can't create thousands of rows


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> List[tuple]:
    """Split ``text`` into ``(chunk, start_offset, end_offset)`` windows.

    Prefers to break on a paragraph/sentence/space boundary near the window end
    so chunks read cleanly, falling back to a hard cut. Returns character
    offsets for ``chunk_start_line`` / ``chunk_end_line``.
    """
    text = (text or "").strip()
    if not text:
        return []
    out: List[tuple] = []
    n = len(text)
    start = 0
    step = max(1, size - overlap)
    while start < n and len(out) < _MAX_CHUNKS:
        end = min(n, start + size)
        if end < n:
            # try to end on a natural boundary within the last ~200 chars
            window = text[start:end]
            for sep in ("\n\n", "\n", ". ", " "):
                idx = window.rfind(sep)
                if idx != -1 and idx >= size - 250:
                    end = start + idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            out.append((chunk, start, end))
        if end <= start:
            break
        start = end - overlap if end - overlap > start else end
    return out


async def ingest_knowledge_document(
    db,
    *,
    abs_path: str,
    filename: str,
    content_type: str,
    data_source,
    organization,
    source_text: Optional[str] = None,
    metadata_indexing_job_id: Optional[str] = None,
    source_file_id: Optional[str] = None,
) -> int:
    """Ingest a knowledge document into ``metadata_resources``.

    Extracts text (or uses the pre-extracted ``source_text``), chunks it, and
    creates one active ``MetadataResource`` row per chunk scoped to
    ``data_source`` so the agent's ``read_resources`` tool can find it.

    Returns the number of chunk rows created (0 on any failure / empty text).
    """
    try:
        from app.models.metadata_resource import MetadataResource

        text = source_text
        if not text or not str(text).strip():
            try:
                from app.data_sources.clients._document_text import extract_document_text
                text = extract_document_text(abs_path, name=filename) or ""
            except Exception:
                text = ""
        text = (text or "").strip()
        if not text:
            logger.info("ingest_knowledge_document: no text extracted for '%s' — nothing to index", filename)
            return 0

        chunks = _chunk_text(text)
        if not chunks:
            return 0

        base = os.path.basename(filename or "document")
        stem = os.path.splitext(base)[0] or base
        total = len(chunks)
        ds_id = str(getattr(data_source, "id", "") or "") or None
        org_id = str(getattr(organization, "id", "") or "") or None

        created = 0
        for i, (chunk, c_start, c_end) in enumerate(chunks):
            row = MetadataResource(
                name=f"{stem} (part {i + 1}/{total})",
                resource_type="knowledge",
                path=base,
                description=chunk,
                is_active=True,
                data_source_id=ds_id,
                organization_id=org_id,
                metadata_indexing_job_id=metadata_indexing_job_id,
                load_mode="intelligent",
                chunk_start_line=c_start,
                chunk_end_line=c_end,
                raw_data={
                    "source": "file_upload",
                    "filename": base,
                    "content_type": content_type,
                    "part": i + 1,
                    "parts_total": total,
                    # Back-link to the originating File so the files API can
                    # derive fate == "knowledge" for this file.
                    "source_file_id": source_file_id,
                },
            )
            db.add(row)
            created += 1

        await db.commit()
        logger.info(
            "ingest_knowledge_document: '%s' -> %d knowledge chunk(s) for ds %s",
            filename, created, ds_id,
        )
        return created
    except Exception as e:  # never break the upload
        logger.warning("ingest_knowledge_document failed for '%s': %s", filename, e)
        try:
            await db.rollback()
        except Exception:
            pass
        return 0
