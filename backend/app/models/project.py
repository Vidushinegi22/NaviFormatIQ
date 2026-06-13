"""Project — the root unit of work (no user/auth)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class Project(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255))
    flow_hint: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    status: Mapped[str] = mapped_column(String(32), default="active")
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    current_step: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    completion: Mapped[dict] = mapped_column(JSONType, default=dict)

    documents = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )
    runs = relationship("Run", back_populates="project", cascade="all, delete-orphan")
    chat_sessions = relationship(
        "ChatSession", back_populates="project", cascade="all, delete-orphan"
    )
