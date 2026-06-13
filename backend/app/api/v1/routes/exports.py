"""Export hub — enumerate + serve the deliverables a finished run can produce.

The catalog and producers live in ``app/services/export``; this layer resolves
the run + its artifacts, serves the bytes, and caches an on-demand PDF so repeat
downloads (and presigned URLs) work.
"""
from __future__ import annotations

import hashlib
import io
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nodes.common import PDF_MIME
from app.core.concurrency import run_office, run_sync
from app.core.exceptions import BadRequestError, NotFoundError, ServiceError
from app.deps import get_db
from app.models.artifact import Artifact
from app.models.document import Document
from app.models.run import Run
from app.schemas.api import ExportItem, ExportManifest
from app.services.export import ExportError, build_export, list_exports
from app.storage import get_storage
from app.storage.base import content_disposition

router = APIRouter(prefix="/api/v1", tags=["Exports"])


def _light_artifacts(arts: list[Artifact]) -> list[dict]:
    return [
        {
            "id": str(a.id),
            "kind": a.kind,
            "uri": a.uri,
            "filename": a.filename,
            "mime": a.mime,
            "size_bytes": a.size_bytes,
        }
        for a in arts
    ]


async def _load_run(db: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await db.get(Run, run_id)
    if not run:
        raise NotFoundError("run not found")
    return run


async def _run_artifacts(db: AsyncSession, run_id: uuid.UUID) -> list[Artifact]:
    return (
        await db.execute(select(Artifact).where(Artifact.run_id == run_id))
    ).scalars().all()


async def _base_name(db: AsyncSession, run: Run) -> str:
    """A friendly download stem from the project's source document."""
    doc = (
        await db.execute(
            select(Document)
            .where(
                Document.project_id == run.project_id,
                Document.kind.in_(["source", "draft", "content"]),
            )
            .order_by(Document.created_at.asc())
        )
    ).scalars().first()
    return (doc.display_name if doc else None) or "document"


async def _soffice_available() -> bool:
    from app.services.office.office_pipeline import available

    return await run_sync(available)


@router.get("/flows/{run_id}/exports", response_model=ExportManifest)
async def list_run_exports(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await _load_run(db, run_id)
    arts = await _run_artifacts(db, run_id)
    specs = list_exports(
        run.state or {},
        _light_artifacts(arts),
        base_name=await _base_name(db, run),
        soffice_available=await _soffice_available(),
    )
    return ExportManifest(exports=[ExportItem(**s.to_dict()) for s in specs])


@router.get("/flows/{run_id}/exports/{export_id}")
async def download_run_export(
    run_id: uuid.UUID, export_id: str, db: AsyncSession = Depends(get_db)
):
    run = await _load_run(db, run_id)
    arts = await _run_artifacts(db, run_id)
    state = run.state or {}
    light = _light_artifacts(arts)
    base = await _base_name(db, run)

    # Validate the request against the live catalog.
    specs = {
        s.id: s
        for s in list_exports(
            state, light, base_name=base, soffice_available=await _soffice_available()
        )
    }
    spec = specs.get(export_id)
    if not spec:
        raise NotFoundError(f"export {export_id!r} is not available for this run")
    if not spec.available:
        raise ServiceError(spec.reason or "This export is not available right now.")

    from app.services.office.office_pipeline import LibreOfficeUnavailable

    try:
        if export_id == "document_pdf":
            # Serialise + off-load the LibreOffice conversion.
            data, mime, filename = await run_office(
                build_export, export_id, state, light, base_name=base
            )
        else:
            data, mime, filename = await run_sync(
                build_export, export_id, state, light, base_name=base
            )
    except LibreOfficeUnavailable as e:
        raise ServiceError(f"PDF conversion is unavailable: {e}")
    except ExportError as e:
        raise BadRequestError(str(e))

    # Cache a freshly-converted PDF so repeat downloads + presign work.
    if export_id == "document_pdf" and not _already_has_pdf(state, arts):
        await _persist_pdf(db, run, data)

    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": content_disposition(filename)},
    )


def _already_has_pdf(state: dict, arts: list[Artifact]) -> bool:
    return bool(state.get("rendered_pdf_uri")) or any(a.kind == "rendered_pdf" for a in arts)


async def _persist_pdf(db: AsyncSession, run: Run, data: bytes) -> None:
    """Store an on-demand PDF as a rendered artifact + record it on the run."""
    storage = get_storage()
    key = storage.make_key(project_id=str(run.project_id), kind="rendered", filename="output.pdf")
    obj = await run_sync(storage.put, data, key=key, content_type=PDF_MIME)
    db.add(
        Artifact(
            project_id=run.project_id,
            run_id=run.id,
            uri=obj.uri,
            r2_key=obj.key if obj.bucket else None,
            bucket=obj.bucket,
            kind="rendered_pdf",
            filename="output.pdf",
            mime=PDF_MIME,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    )
    # Reassign the JSON column so SQLAlchemy flags it dirty.
    run.state = {**(run.state or {}), "rendered_pdf_uri": obj.uri}
    # Commit now — the response streams from memory, so we don't rely on the
    # request-teardown commit firing before/after the stream.
    await db.commit()
