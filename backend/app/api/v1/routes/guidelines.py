"""Guideline registry — public list/detail + admin ingest/verify/reindex/delete.

End users only GET ``ready`` guidelines (the Upload-page selector). Admins ingest
a guideline PDF (→ requirement tree + Qdrant index), review it, then PATCH it to
``ready`` to publish. No auth layer exists in this app yet, so "admin" endpoints
are simply the mutating ones.
"""
from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.concurrency import run_sync
from app.core.exceptions import BadRequestError, NotFoundError
from app.deps import get_db
from app.models.artifact import Artifact
from app.models.compliance import Guideline
from app.schemas.api import (
    GuidelineDetail,
    GuidelineIngestResult,
    GuidelineRead,
    GuidelineSection,
    GuidelineUpdate,
)
from app.services.compliance.embed_index import drop_guideline_index
from app.services.compliance.ingest import ingest_guideline
from app.services.compliance.registry import (
    dimension_coverage,
    requirement_count,
    upsert_guideline,
)
from app.storage import get_storage

router = APIRouter(prefix="/api/v1/guidelines", tags=["Guidelines"])


async def _to_read(db: AsyncSession, g: Guideline) -> GuidelineRead:
    return GuidelineRead(
        id=g.id,
        code=g.code,
        title=g.title,
        domain=g.domain,
        version=g.version,
        description=g.description,
        status=g.status,
        requirement_count=await requirement_count(db, g.id),
        dimension_coverage=await dimension_coverage(db, g.id),
    )


@router.get("", response_model=list[GuidelineRead])
async def list_guidelines(
    domain: str | None = Query(None),
    include_unpublished: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Guideline)
    if domain:
        stmt = stmt.where(Guideline.domain == domain)
    if not include_unpublished:
        stmt = stmt.where(Guideline.status == "ready")
    stmt = stmt.order_by(Guideline.code)
    rows = (await db.execute(stmt)).scalars().all()
    return [await _to_read(db, g) for g in rows]


@router.get("/{guideline_id}", response_model=GuidelineDetail)
async def get_guideline(guideline_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    g = await db.get(Guideline, guideline_id)
    if not g:
        raise NotFoundError("guideline not found")
    base = await _to_read(db, g)
    meta = g.meta or {}
    sections = [
        GuidelineSection(
            section_no=s.get("section_no"), title=s.get("title", ""), level=s.get("level", 1)
        )
        for s in meta.get("sections", [])
    ]
    return GuidelineDetail(
        **base.model_dump(), sections=sections, page_count=meta.get("page_count")
    )


@router.post("", response_model=GuidelineIngestResult)
async def create_guideline(
    file: UploadFile = File(...),
    code: str = Form(...),
    title: str | None = Form(None),
    version: str | None = Form(None),
    description: str | None = Form(None),
    domain: str = Form("pharma"),
    publish: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    """Ingest a guideline PDF into the registry.

    Defaults to ``status=extracted`` (pending human review). Pass ``publish=true``
    to mark it ``ready`` immediately so it shows up in the Upload-page selector —
    used by the manual-upload flow when no pre-seeded guidelines are available.
    """
    data = await file.read()
    if not data:
        raise BadRequestError("empty file")
    storage = get_storage()
    key = storage.make_key(project_id="_guidelines", kind="guideline_source", filename=file.filename or "guideline.pdf")
    obj = await run_sync(storage.put, data, key=key, content_type=file.content_type or "application/pdf")
    art = Artifact(
        uri=obj.uri,
        r2_key=obj.key if obj.bucket else None,
        bucket=obj.bucket,
        kind="guideline_source",
        filename=file.filename or "guideline.pdf",
        mime=file.content_type or "application/pdf",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(art)
    await db.flush()

    await run_sync(drop_guideline_index, code)
    result = await run_sync(ingest_guideline, code, data)
    g = await upsert_guideline(
        db,
        code=code,
        result=result,
        title=title,
        version=version,
        description=description,
        domain=domain,
        source_artifact_id=art.id,
        status="ready" if publish else "extracted",
    )
    await db.commit()
    return GuidelineIngestResult(
        id=g.id,
        code=g.code,
        status=g.status,
        sections=len(result.get("sections", [])),
        requirements=len(result.get("requirements", [])),
        indexed_chunks=result.get("indexed_chunks", 0),
    )


@router.post("/{guideline_id}/reindex", response_model=GuidelineIngestResult)
async def reindex_guideline(guideline_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Re-run ingestion from the stored source PDF (drops + rebuilds the index)."""
    g = await db.get(Guideline, guideline_id)
    if not g:
        raise NotFoundError("guideline not found")
    if not g.source_artifact_id:
        raise BadRequestError("guideline has no stored source PDF to reindex")
    art = await db.get(Artifact, g.source_artifact_id)
    if not art:
        raise BadRequestError("source artifact missing")
    data = await run_sync(get_storage().get, art.uri)
    await run_sync(drop_guideline_index, g.code)
    result = await run_sync(ingest_guideline, g.code, data)
    g = await upsert_guideline(
        db,
        code=g.code,
        result=result,
        title=g.title,
        version=g.version,
        description=g.description,
        domain=g.domain,
        source_artifact_id=g.source_artifact_id,
        status="extracted",
    )
    await db.commit()
    return GuidelineIngestResult(
        id=g.id,
        code=g.code,
        status=g.status,
        sections=len(result.get("sections", [])),
        requirements=len(result.get("requirements", [])),
        indexed_chunks=result.get("indexed_chunks", 0),
    )


@router.patch("/{guideline_id}", response_model=GuidelineRead)
async def update_guideline(
    guideline_id: uuid.UUID, body: GuidelineUpdate, db: AsyncSession = Depends(get_db)
):
    """Update metadata / publish (status -> 'ready') after human review."""
    g = await db.get(Guideline, guideline_id)
    if not g:
        raise NotFoundError("guideline not found")
    if body.status is not None:
        if body.status not in ("ingesting", "extracted", "ready"):
            raise BadRequestError("invalid status")
        g.status = body.status
    if body.title is not None:
        g.title = body.title
    if body.version is not None:
        g.version = body.version
    if body.description is not None:
        g.description = body.description
    await db.commit()
    return await _to_read(db, g)


@router.delete("/{guideline_id}")
async def delete_guideline(guideline_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    g = await db.get(Guideline, guideline_id)
    if not g:
        raise NotFoundError("guideline not found")
    code = g.code
    await db.delete(g)  # cascades requirements
    await db.commit()
    await run_sync(drop_guideline_index, code)
    return {"ok": True}
