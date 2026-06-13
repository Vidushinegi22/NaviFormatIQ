"""Compliance domain — guideline registry, requirement tree, audit findings, reports.

A *guideline* (e.g. ICH-E3) is ingested once into a normalised requirement tree
(``guideline_requirements``) plus a per-guideline Qdrant collection. A compliance
*run* audits a user document against one guideline and persists one
``compliance_findings`` row per checked requirement and a single
``compliance_reports`` rollup. Findings live in tables (not ``Run.state``) because
they are numerous, filterable, and retrieved by the chat agent; only a compact
summary is mirrored into ``Run.state`` for SSE/back-compat.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class Guideline(UUIDPKMixin, TimestampMixin, Base):
    """A pre-loaded standard a document can be audited against (e.g. ICH-E3)."""

    __tablename__ = "guidelines"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # "ICH-E3"
    title: Mapped[str] = mapped_column(String(512), default="")
    domain: Mapped[str] = mapped_column(String(64), default="pharma", index=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    source_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), default=None
    )
    qdrant_collection: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    # ingesting → extracted → ready (the human-verify gate; only "ready" is public)
    status: Mapped[str] = mapped_column(String(32), default="ingesting", index=True)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=None
    )

    requirements = relationship(
        "GuidelineRequirement",
        back_populates="guideline",
        cascade="all, delete-orphan",
    )


class GuidelineRequirement(UUIDPKMixin, TimestampMixin, Base):
    """One atomic, checkable requirement extracted from a guideline section."""

    __tablename__ = "guideline_requirements"

    guideline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("guidelines.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("guideline_requirements.id", ondelete="CASCADE"), default=None
    )
    section_no: Mapped[Optional[str]] = mapped_column(String(32), default=None)  # "9.4.6"
    sort_key: Mapped[str] = mapped_column(String(64), default="")  # zero-padded ordering
    title: Mapped[str] = mapped_column(String(512), default="")
    requirement_text: Mapped[str] = mapped_column(Text, default="")
    # content | structure | formatting | style | tone
    dimension: Mapped[str] = mapped_column(String(16), default="content")
    # critical | major | minor | info
    severity_default: Mapped[str] = mapped_column(String(16), default="major")
    # presence | content | constraint | formatting | tone
    requirement_kind: Mapped[str] = mapped_column(String(16), default="content")
    # machine-checkable spec, e.g. {"type": "max_pages", "value": 3}
    constraint_spec: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    qdrant_point_ids: Mapped[list] = mapped_column(JSONType, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    guideline = relationship("Guideline", back_populates="requirements")

    __table_args__ = (
        Index("ix_guideline_requirements_guideline_sort", "guideline_id", "sort_key"),
    )


class ComplianceFinding(UUIDPKMixin, TimestampMixin, Base):
    """The audit verdict for one requirement against one run's document."""

    __tablename__ = "compliance_findings"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        ForeignKey("guideline_requirements.id", ondelete="SET NULL"),
        default=None,
    )
    # denormalised for fast filtering without joins
    section_no: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    section_title: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    requirement_title: Mapped[str] = mapped_column(String(512), default="")
    dimension: Mapped[str] = mapped_column(String(16), default="content", index=True)
    # compliant | partial | non_compliant | not_applicable
    status: Mapped[str] = mapped_column(String(16), default="non_compliant")
    severity: Mapped[str] = mapped_column(String(16), default="major")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[Optional[str]] = mapped_column(Text, default=None)  # quote from doc
    doc_location: Mapped[Optional[str]] = mapped_column(String(256), default=None)
    rationale: Mapped[Optional[str]] = mapped_column(Text, default=None)
    citation: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)  # {section, quote}
    suggested_fix: Mapped[Optional[str]] = mapped_column(Text, default=None)

    __table_args__ = (
        Index("ix_compliance_findings_run_dim", "run_id", "dimension"),
        Index("ix_compliance_findings_run_sev", "run_id", "severity"),
    )


class ComplianceReport(UUIDPKMixin, TimestampMixin, Base):
    """The per-run rollup: scores + generated report artifacts."""

    __tablename__ = "compliance_reports"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    guideline_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("guidelines.id", ondelete="SET NULL"), default=None
    )
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    status_label: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    per_dimension: Mapped[dict] = mapped_column(JSONType, default=dict)
    per_section: Mapped[list] = mapped_column(JSONType, default=list)
    severity_counts: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[Optional[str]] = mapped_column(Text, default=None)
    docx_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), default=None
    )
    pdf_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), default=None
    )
    json_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), default=None
    )
    csv_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="SET NULL"), default=None
    )
