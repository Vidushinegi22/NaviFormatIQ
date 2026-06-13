"""Projects = the unit of work; list/open previous work + version/run history."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.deps import get_db
from app.models.document import Document, DocumentVersion
from app.models.project import Project
from app.models.run import Run
from app.schemas.api import (
    DocumentRead,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    ProjectUpdate,
    RunRead,
    VersionRead,
)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


def _derive_progress(*, has_docs: bool, has_run: bool, has_done_run: bool) -> str:
    """Workflow progress derived from real signals rather than a stored flag.

    - ``completed``   → a flow produced output (a run reached ``done``: a new
                        version generated, style applied, or compliance report).
    - ``in_progress`` → the user uploaded a document or started a run, but
                        nothing has finished yet.
    - ``not_started`` → an empty project: nothing uploaded, no run kicked off.
    """
    if has_done_run:
        return "completed"
    if has_docs or has_run:
        return "in_progress"
    return "not_started"


@router.post("", response_model=ProjectRead)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    p = Project(name=body.name, flow_hint=body.flow_hint, meta=body.meta or {})
    db.add(p)
    await db.flush()
    return p


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    limit: int = Query(50, le=200), offset: int = 0, db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    if not rows:
        return []

    # Batch the progress signals for the whole page (3 lightweight queries)
    # instead of N+1 lazy loads of each project's documents/runs.
    proj_ids = [p.id for p in rows]
    with_docs = set(
        (
            await db.execute(
                select(Document.project_id)
                .where(Document.project_id.in_(proj_ids))
                .distinct()
            )
        ).scalars().all()
    )
    with_run = set(
        (
            await db.execute(
                select(Run.project_id).where(Run.project_id.in_(proj_ids)).distinct()
            )
        ).scalars().all()
    )
    with_done_run = set(
        (
            await db.execute(
                select(Run.project_id)
                .where(Run.project_id.in_(proj_ids), Run.status == "done")
                .distinct()
            )
        ).scalars().all()
    )

    return [
        ProjectRead(
            id=p.id,
            name=p.name,
            status=p.status,
            progress=_derive_progress(
                has_docs=p.id in with_docs,
                has_run=p.id in with_run,
                has_done_run=p.id in with_done_run,
            ),
            flow_hint=p.flow_hint,
            meta=p.meta or {},
            current_step=p.current_step,
            completion=p.completion or {},
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in rows
    ]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("project not found")
    docs = (
        await db.execute(select(Document).where(Document.project_id == project_id))
    ).scalars().all()
    runs = (
        await db.execute(
            select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc())
        )
    ).scalars().all()
    # Build from already-loaded scalars; validating the ORM object directly
    # would lazy-load the `documents`/`runs` relationships and raise
    # MissingGreenlet in this async context.
    return ProjectDetail(
        id=p.id,
        name=p.name,
        status=p.status,
        progress=_derive_progress(
            has_docs=bool(docs),
            has_run=bool(runs),
            has_done_run=any(r.status == "done" for r in runs),
        ),
        flow_hint=p.flow_hint,
        meta=p.meta or {},
        current_step=p.current_step,
        completion=p.completion or {},
        created_at=p.created_at,
        updated_at=p.updated_at,
        documents=[DocumentRead.model_validate(d) for d in docs],
        runs=[RunRead.model_validate(r) for r in runs],
    )


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, db: AsyncSession = Depends(get_db)
):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("project not found")
    if body.name is not None:
        p.name = body.name
    if body.status is not None:
        p.status = body.status
    if body.current_step is not None:
        p.current_step = body.current_step
    if body.completion is not None:
        p.completion = body.completion
    await db.flush()
    # Reload the server-side `onupdate` timestamp inside the async context.
    # Returning the ORM object instead would let FastAPI lazy-load the
    # freshly-expired `updated_at` during response serialization — after the
    # request greenlet is gone — raising MissingGreenlet (same failure mode as
    # get_project, which is why that one also builds the schema by hand).
    await db.refresh(p)
    doc_count = (
        await db.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.project_id == project_id)
        )
    ).scalar_one()
    run_statuses = (
        await db.execute(select(Run.status).where(Run.project_id == project_id))
    ).scalars().all()
    return ProjectRead(
        id=p.id,
        name=p.name,
        status=p.status,
        progress=_derive_progress(
            has_docs=doc_count > 0,
            has_run=bool(run_statuses),
            has_done_run=any(s == "done" for s in run_statuses),
        ),
        flow_hint=p.flow_hint,
        meta=p.meta or {},
        current_step=p.current_step,
        completion=p.completion or {},
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    p = await db.get(Project, project_id)
    if not p:
        raise NotFoundError("project not found")
    await db.delete(p)
    await db.flush()


@router.get("/{project_id}/documents", response_model=list[DocumentRead])
async def project_documents(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return (
        await db.execute(select(Document).where(Document.project_id == project_id))
    ).scalars().all()


@router.get(
    "/{project_id}/documents/{document_id}/versions", response_model=list[VersionRead]
)
async def document_versions(
    project_id: uuid.UUID, document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    return (
        await db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc())
        )
    ).scalars().all()


@router.get("/{project_id}/runs", response_model=list[RunRead])
async def project_runs(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return (
        await db.execute(
            select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc())
        )
    ).scalars().all()
