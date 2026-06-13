"""FastAPI dependencies + small resolvers (no auth)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.artifact import Artifact
from app.models.document import Document, DocumentVersion

# re-export for route signatures
get_db = get_session


async def resolve_artifact_uri(
    session: AsyncSession,
    *,
    artifact_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> tuple[str, str, uuid.UUID]:
    """Return (uri, filename, artifact_id) for an artifact or a document's latest version."""
    if artifact_id:
        art = await session.get(Artifact, artifact_id)
        if not art:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return art.uri, art.filename, art.id
    if document_id:
        doc = await session.get(Document, document_id)
        if not doc:
            raise NotFoundError(f"document {document_id} not found")
        ver = (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version.desc())
            )
        ).scalars().first()
        if not ver or not ver.artifact_id:
            raise NotFoundError(f"document {document_id} has no stored artifact")
        art = await session.get(Artifact, ver.artifact_id)
        if not art:
            raise NotFoundError("artifact for document version not found")
        return art.uri, art.filename, art.id
    raise BadRequestError("provide an artifact_id or document_id")
