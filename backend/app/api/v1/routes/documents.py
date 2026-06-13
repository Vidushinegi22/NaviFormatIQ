"""Document upload (R2/local) + download + on-the-fly content/styling JSON."""
from __future__ import annotations

import hashlib
import io
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nodes.common import ext_of, filename_from_uri
from app.core.concurrency import run_sync
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.uploads import read_validated_upload
from app.deps import get_db
from app.models.artifact import Artifact
from app.models.document import Document, DocumentVersion
from app.models.project import Project
from app.schemas.api import UploadRead
from app.storage import get_storage
from app.storage.base import content_disposition

router = APIRouter(prefix="/api/v1", tags=["Documents"])


@router.post("/projects/{project_id}/uploads", response_model=UploadRead)
async def upload(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    kind: str = Form("source"),
    db: AsyncSession = Depends(get_db),
):
    if not await db.get(Project, project_id):
        raise NotFoundError("project not found")
    # Reject wrong-type / empty / oversized uploads before buffering + storing.
    data = await read_validated_upload(file)
    storage = get_storage()
    key = storage.make_key(project_id=str(project_id), kind=kind, filename=file.filename or "upload")
    obj = await run_sync(storage.put, data, key=key, content_type=file.content_type)
    art = Artifact(
        project_id=project_id,
        uri=obj.uri,
        r2_key=obj.key if obj.bucket else None,
        bucket=obj.bucket,
        kind="upload",
        filename=file.filename or "upload",
        mime=file.content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(art)
    await db.flush()
    doc = Document(
        project_id=project_id, kind=kind, display_name=file.filename or "document", current_version=1
    )
    db.add(doc)
    await db.flush()
    db.add(DocumentVersion(document_id=doc.id, version=1, artifact_id=art.id))
    await db.flush()
    return UploadRead(
        document_id=doc.id, version=1, artifact_id=art.id, uri=obj.uri, filename=file.filename or "upload"
    )


@router.get("/artifacts/{artifact_id}/download")
async def download(
    artifact_id: uuid.UUID,
    presign: bool = Query(False),
    filename: Optional[str] = Query(
        None, description="Preferred download filename (falls back to the stored name)."
    ),
    db: AsyncSession = Depends(get_db),
):
    art = await db.get(Artifact, artifact_id)
    if not art:
        raise NotFoundError("artifact not found")
    # The caller (export hub) knows a friendly name like `MyDoc-compliance.pdf`;
    # prefer it over the generic stored name (`compliance-report.pdf`).
    download_name = filename or art.filename or "download"
    storage = get_storage()
    if presign:
        url = storage.presign_get(art.uri, download_name=download_name)
        if url:
            return JSONResponse({"url": url})
    data = await run_sync(storage.get, art.uri)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=art.mime or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(download_name)},
    )


async def _version_artifact(db: AsyncSession, document_id: uuid.UUID, version: int):
    ver = (
        await db.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id, DocumentVersion.version == version
            )
        )
    ).scalars().first()
    if not ver:
        raise NotFoundError("version not found")
    return ver


def _extract(uri: str):
    data = get_storage().get(uri)
    name = filename_from_uri(uri, "doc")
    ext = ext_of(uri)
    if ext == "docx":
        from app.services.extraction.word_ext import extract_word_document

        content, styling = extract_word_document(file_stream=io.BytesIO(data), filename=name)

        # LLM refinement pass — reclassifies misidentified elements when
        # the LLM is available; falls back silently if not.
        try:
            from app.services.extraction.doc_understanding import refine_document
            content = refine_document(content)
        except Exception:
            pass  # never block extraction on LLM errors

        return content, styling
    if ext == "pdf":
        from app.services.extraction.pdf_ext import extract_pdf_document

        content, styling = extract_pdf_document(file_stream=io.BytesIO(data), filename=name)

        # LLM refinement matters even more for PDF — it is a layout format with
        # no semantic markup, so the heuristic extractor mis-classifies more
        # than the DOCX one. Same graceful fallback when the LLM is unavailable.
        try:
            from app.services.extraction.doc_understanding import refine_document
            content = refine_document(content)
        except Exception:
            pass  # never block extraction on LLM errors

        return content, styling
    raise BadRequestError(f"cannot extract {ext!r}")


async def _load_or_extract(
    db: AsyncSession, document_id: uuid.UUID, version: int, *, force: bool = False
) -> tuple[dict, dict]:
    """Return ``(content_json, styling_json)`` for a version, extracting only if
    needed.

    A single parse of the document produces BOTH payloads, so whenever we have
    to extract we cache both together. This means the content/styling/extract
    endpoints never parse the same file more than once: the first request fills
    the cache and every other request (or the other half of the pair) is a pure
    cache hit.

    ``force`` bypasses the cache and re-extracts from the stored artifact,
    overwriting both payloads — used by "Rerun Extraction" so a stale result
    (e.g. from before an extractor improvement) can be replaced.
    """
    ver = await _version_artifact(db, document_id, version)
    content_data = ver.content_json
    styling_data = ver.styling_json
    if content_data and styling_data and not force:
        return content_data, styling_data
    if not ver.artifact_id:
        raise NotFoundError("no artifact for this version")
    art = await db.get(Artifact, ver.artifact_id)
    content, styling = await run_sync(_extract, art.uri)
    if force or not content_data:
        content_data = content.model_dump(exclude_none=True)
        ver.content_json = content_data
    if force or not styling_data:
        styling_data = styling.model_dump(exclude_none=True)
        ver.styling_json = styling_data
    await db.flush()
    return content_data, styling_data


@router.get("/documents/{document_id}/versions/{version}/styling.json")
async def version_styling(
    document_id: uuid.UUID, version: int, db: AsyncSession = Depends(get_db)
):
    _, styling_data = await _load_or_extract(db, document_id, version)
    return JSONResponse(styling_data)


@router.get("/documents/{document_id}/versions/{version}/content.json")
async def version_content(
    document_id: uuid.UUID, version: int, db: AsyncSession = Depends(get_db)
):
    content_data, _ = await _load_or_extract(db, document_id, version)
    return JSONResponse(content_data)


@router.get("/documents/{document_id}/versions/{version}/extract.json")
async def version_extract(
    document_id: uuid.UUID,
    version: int,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Content + styling in ONE response from a single extraction pass.

    The Extract page uses this instead of fetching content.json and styling.json
    in parallel — which previously parsed the document twice, concurrently, and
    roughly doubled the wait shown as "Reading document…".

    Pass ``?force=true`` to bypass the cache and re-extract (the "Rerun
    Extraction" action) — e.g. to pick up extractor improvements.
    """
    content_data, styling_data = await _load_or_extract(
        db, document_id, version, force=force
    )
    return JSONResponse({"content": content_data, "styling": styling_data})
