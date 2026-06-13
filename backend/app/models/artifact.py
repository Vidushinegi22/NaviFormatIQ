"""Artifact — a stored file (R2 object or local file) referenced by URI/key."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Artifact(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "artifacts"

    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), default=None
    )
    uri: Mapped[str] = mapped_column(String(1024))
    r2_key: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    bucket: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    kind: Mapped[str] = mapped_column(String(32), default="upload")
    filename: Mapped[str] = mapped_column(String(512), default="file")
    mime: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), default=None)

    run = relationship("Run", back_populates="artifacts")
