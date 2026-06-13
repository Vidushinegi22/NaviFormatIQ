"""Run — a single flow execution (status, agent traces, LangGraph thread)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class Run(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    flow: Mapped[str] = mapped_column(String(32))            # regenerate|style|compliance
    mode: Mapped[Optional[str]] = mapped_column(String(32), default=None)  # apply|check
    status: Mapped[str] = mapped_column(String(32), default="pending")
    langgraph_thread_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    domain_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    input_refs: Mapped[dict] = mapped_column(JSONType, default=dict)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)

    project = relationship("Project", back_populates="runs")
    artifacts = relationship("Artifact", back_populates="run")
