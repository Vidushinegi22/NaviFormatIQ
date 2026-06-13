"""DB-aware persistence for the guideline registry (shared by seed + admin API).

Keeps the pure ingestion pipeline (``ingest``) free of DB concerns; this module
turns an ingest result into ``guidelines`` + ``guideline_requirements`` rows.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import Guideline, GuidelineRequirement


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def upsert_guideline(
    session: AsyncSession,
    *,
    code: str,
    result: dict[str, Any],
    title: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
    domain: str = "pharma",
    source_artifact_id: Optional[uuid.UUID] = None,
    status: str = "extracted",
) -> Guideline:
    """Create or replace a guideline + its requirement rows from an ingest result."""
    g = (
        await session.execute(select(Guideline).where(Guideline.code == code))
    ).scalars().first()
    if g is None:
        g = Guideline(code=code)
        session.add(g)
        await session.flush()
    else:
        await session.execute(
            delete(GuidelineRequirement).where(GuidelineRequirement.guideline_id == g.id)
        )

    g.title = title or result.get("title") or code
    g.domain = domain
    if version is not None:
        g.version = version
    if description is not None:
        g.description = description
    if source_artifact_id is not None:
        g.source_artifact_id = source_artifact_id
    g.qdrant_collection = result.get("collection")
    g.status = status
    g.meta = {
        "sections": result.get("sections", []),
        "page_count": result.get("page_count"),
        "indexed_chunks": result.get("indexed_chunks", 0),
    }
    g.indexed_at = _utcnow()

    for r in result.get("requirements", []):
        session.add(
            GuidelineRequirement(
                guideline_id=g.id,
                section_no=r.get("section_no"),
                sort_key=r.get("sort_key", ""),
                title=r.get("title", ""),
                requirement_text=r.get("requirement_text", ""),
                dimension=r.get("dimension", "content"),
                severity_default=r.get("severity_default", "major"),
                requirement_kind=r.get("requirement_kind", "content"),
                constraint_spec=r.get("constraint_spec"),
                qdrant_point_ids=r.get("qdrant_point_ids", []),
                enabled=True,
            )
        )
    await session.flush()
    return g


async def list_guidelines(
    session: AsyncSession, *, domain: Optional[str] = None, ready_only: bool = True
) -> list[Guideline]:
    stmt = select(Guideline)
    if domain:
        stmt = stmt.where(Guideline.domain == domain)
    if ready_only:
        stmt = stmt.where(Guideline.status == "ready")
    stmt = stmt.order_by(Guideline.code)
    return list((await session.execute(stmt)).scalars().all())


async def requirement_count(session: AsyncSession, guideline_id: uuid.UUID) -> int:
    from sqlalchemy import func

    return int(
        (
            await session.execute(
                select(func.count(GuidelineRequirement.id)).where(
                    GuidelineRequirement.guideline_id == guideline_id
                )
            )
        ).scalar()
        or 0
    )


async def dimension_coverage(session: AsyncSession, guideline_id: uuid.UUID) -> list[str]:
    rows = (
        await session.execute(
            select(GuidelineRequirement.dimension)
            .where(GuidelineRequirement.guideline_id == guideline_id)
            .distinct()
        )
    ).scalars().all()
    return sorted(set(rows))
