"""ChatSession + ChatMessage for the doc-chat agent."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class ChatSession(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), default=None, index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="Chat")
    subject_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, default=None)
    # Bind a session to a compliance run + guideline for grounded Q&A.
    subject_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, default=None)
    guideline_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, default=None)

    project = relationship("Project", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user|assistant|tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    tool_args: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    tool_result_ref: Mapped[Optional[dict]] = mapped_column(JSONType, default=None)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)

    session = relationship("ChatSession", back_populates="messages")
