"""Import all models so Base.metadata is fully populated."""
from __future__ import annotations

from app.models.base import Base  # noqa: F401
from app.models.project import Project  # noqa: F401
from app.models.document import Document, DocumentVersion  # noqa: F401
from app.models.run import Run  # noqa: F401
from app.models.artifact import Artifact  # noqa: F401
from app.models.chat import ChatMessage, ChatSession  # noqa: F401
from app.models.domain_profile import DomainProfileRow  # noqa: F401
from app.models.compliance import (  # noqa: F401
    ComplianceFinding,
    ComplianceReport,
    Guideline,
    GuidelineRequirement,
)

__all__ = [
    "Base",
    "Project",
    "Document",
    "DocumentVersion",
    "Run",
    "Artifact",
    "ChatSession",
    "ChatMessage",
    "DomainProfileRow",
    "Guideline",
    "GuidelineRequirement",
    "ComplianceFinding",
    "ComplianceReport",
]
