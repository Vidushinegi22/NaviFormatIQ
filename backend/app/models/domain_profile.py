"""DomainProfile registry row (corpus → Qdrant collection)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONType, TimestampMixin, UUIDPKMixin


class DomainProfileRow(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "domain_profiles"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    glossary: Mapped[dict] = mapped_column(JSONType, default=dict)
    format_rules: Mapped[list] = mapped_column(JSONType, default=list)
    qdrant_collection: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    corpus_source: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
