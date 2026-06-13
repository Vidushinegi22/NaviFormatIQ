"""Document + DocumentVersion (version history with R2-backed artifacts)."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class Document(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="source")
    display_name: Mapped[str] = mapped_column(String(255), default="document")
    current_version: Mapped[int] = mapped_column(Integer, default=0)

    project = relationship("Project", back_populates="documents")
    versions = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("artifacts.id"), default=None
    )
    content_json: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    styling_json: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    structure_json: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    diff_from_prev: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    created_by_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, default=None)

    document = relationship("Document", back_populates="versions")
