"""Pydantic request/response models for the HTTP API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_ORM = ConfigDict(from_attributes=True)


# ── projects ───────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    flow_hint: Optional[str] = None
    meta: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    current_step: Optional[str] = None
    completion: Optional[dict] = None


class ProjectRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    name: str
    status: str
    # Derived workflow progress (not_started | in_progress | completed): computed
    # at read time from uploaded documents + run outcomes, not stored. `status`
    # above is the lifecycle field (active | archived); `progress` is what the
    # dashboard badge reflects.
    progress: str = "not_started"
    flow_hint: Optional[str] = None
    meta: dict = Field(default_factory=dict)
    current_step: Optional[str] = None
    completion: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    kind: str
    display_name: str
    current_version: int


class VersionRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    version: int
    artifact_id: Optional[uuid.UUID] = None
    created_by_run_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None


class ArtifactRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    uri: str
    kind: str
    filename: str
    mime: Optional[str] = None
    size_bytes: int = 0


class RunRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    flow: str
    mode: Optional[str] = None
    status: str
    domain_id: Optional[str] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ProjectDetail(ProjectRead):
    documents: list[DocumentRead] = Field(default_factory=list)
    runs: list[RunRead] = Field(default_factory=list)


class UploadRead(BaseModel):
    document_id: uuid.UUID
    version: int
    artifact_id: uuid.UUID
    uri: str
    filename: str


# ── flows ──────────────────────────────────────────────────────────────────
class RegenerateRequest(BaseModel):
    draft_artifact_id: Optional[uuid.UUID] = None
    draft_document_id: Optional[uuid.UUID] = None
    template_artifact_id: Optional[uuid.UUID] = None
    template_document_id: Optional[uuid.UUID] = None
    user_suggestions: Optional[str] = None
    domain_id: Optional[str] = None
    output_format: str = "docx"
    # Skip the AI body rewrite entirely (and the suggestion pass): carry every
    # section through unchanged so the user edits it by hand on the review
    # screen. The version/date/revision-row auto-updates still run.
    skip_ai_rewrite: bool = False
    # How to auto-bump the version on this new generation: minor | major | none.
    # Used only as a fallback when ``target_version`` is not supplied.
    version_bump: str = "minor"
    # Other uploaded versions of the SAME document, used purely as context so
    # the generator understands what changed across revisions. Not rewritten.
    context_document_ids: list[uuid.UUID] = Field(default_factory=list)
    # Explicit version number for the generated document (e.g. "10" when the
    # user uploaded versions 9 and 2). When set, it overrides version_bump.
    target_version: Optional[str] = None


class SuggestRequest(BaseModel):
    """Ask for smart, substantive change suggestions for the new version."""
    draft_artifact_id: Optional[uuid.UUID] = None
    draft_document_id: Optional[uuid.UUID] = None
    # Earlier uploaded versions — context on how the document evolved.
    context_document_ids: list[uuid.UUID] = Field(default_factory=list)
    domain_id: Optional[str] = None


class RevisionSuggestion(BaseModel):
    """One mature, content-level change the author could make for the revision."""
    title: str
    detail: str
    # Existing heading to change, or the proposed new section's heading.
    section: Optional[str] = None
    kind: str = "revise_section"  # add_section | expand_section | revise_section


class SuggestionsResponse(BaseModel):
    suggestions: list[RevisionSuggestion] = Field(default_factory=list)


class StyleRequest(BaseModel):
    content_artifact_id: Optional[uuid.UUID] = None
    content_document_id: Optional[uuid.UUID] = None
    style_artifact_id: Optional[uuid.UUID] = None
    style_document_id: Optional[uuid.UUID] = None
    normalize_fonts: bool = True
    promote_headings: bool = True
    # How to interpret the style source: 'auto' (detect), 'guideline' (read the
    # described rules), or 'example' (copy the document's own look).
    style_source_mode: str = "auto"
    output_format: str = "docx"


class StyleInterpretRequest(BaseModel):
    """Preview how the style source will be interpreted (no document produced)."""
    style_artifact_id: Optional[uuid.UUID] = None
    style_document_id: Optional[uuid.UUID] = None
    mode: str = "auto"  # auto | guideline | example


class StyleInterpretResponse(BaseModel):
    effective_kind: str          # the mode that will actually be used
    detected_kind: str           # the classifier's verdict (before any override)
    confidence: float
    reason: str
    method: str                  # "llm" | "heuristic" | "forced"
    summary: str
    spec: Optional[dict] = None  # the extracted StyleSpec (guideline mode only)
    notes: list[str] = Field(default_factory=list)
    palette: dict = Field(default_factory=dict)


class ComplianceRequest(BaseModel):
    draft_artifact_id: Optional[uuid.UUID] = None
    draft_document_id: Optional[uuid.UUID] = None
    template_artifact_id: Optional[uuid.UUID] = None
    template_document_id: Optional[uuid.UUID] = None
    domain_id: str = "pharma"
    # New audit engine: the guideline (e.g. ICH-E3) to audit the document against.
    guideline_id: Optional[uuid.UUID] = None
    # Optional subset of dimensions to check; None/empty = all five.
    dimensions: Optional[list[str]] = None
    mode: str = "check"          # apply | check
    output_format: str = "docx"


class RunStarted(BaseModel):
    run_id: uuid.UUID
    status: str


class RunDetail(BaseModel):
    id: uuid.UUID
    flow: str
    mode: Optional[str] = None
    status: str
    domain_id: Optional[str] = None
    diff: list[dict] = Field(default_factory=list)
    flags: list[dict] = Field(default_factory=list)
    coverage: Optional[dict] = None
    compliance_score: Optional[float] = None
    traces: list[dict] = Field(default_factory=list)
    rendered_docx_uri: Optional[str] = None
    rendered_pdf_uri: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    artifacts: list[ArtifactRead] = Field(default_factory=list)
    # Flow 2: what the style source was detected as + the structure recognised
    # in the content (headings/lists/tables) + the applied spec.
    style_interpretation: Optional[dict] = None
    # Flow 1: document profile (type/tone/summary) + the auto field-updates
    # (version bump, date, revision row) applied to the new version.
    doc_profile: Optional[dict] = None
    field_updates: Optional[dict] = None
    # Compliance audit (v2): the rich, multi-dimensional result. Null on other flows.
    compliance: Optional["ComplianceResult"] = None


class ExportItem(BaseModel):
    """One downloadable deliverable offered for a finished run."""
    id: str
    category: str                 # document | formatting | data | report
    label: str
    description: str
    format: str                   # docx | pdf | json
    filename: str
    available: bool = True
    reason: Optional[str] = None  # why it can't be produced (e.g. no LibreOffice)
    artifact_id: Optional[str] = None  # when it maps to an existing stored artifact
    size_bytes: Optional[int] = None


class ExportManifest(BaseModel):
    exports: list[ExportItem] = Field(default_factory=list)


class ReviewDecisionItem(BaseModel):
    slot_id: str
    accepted: Optional[bool] = None
    reviewer_edit: Optional[str] = None
    proposed: Optional[str] = None
    # Structural edits from the manual review screen.
    title: Optional[str] = None       # rename the section heading
    level: Optional[int] = None       # heading level for a new section
    is_new: Optional[bool] = None     # this is a brand-new section to insert


class ResumeRequest(BaseModel):
    decisions: list[ReviewDecisionItem] = Field(default_factory=list)


# ── chat ───────────────────────────────────────────────────────────────────
class ChatSessionCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    title: str = "Chat"
    subject_document_id: Optional[uuid.UUID] = None
    subject_run_id: Optional[uuid.UUID] = None   # bind chat to a compliance run
    guideline_id: Optional[uuid.UUID] = None     # ground chat in a guideline


class ChatSessionRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    title: str
    subject_document_id: Optional[uuid.UUID] = None
    subject_run_id: Optional[uuid.UUID] = None
    guideline_id: Optional[uuid.UUID] = None


class ChatMessageCreate(BaseModel):
    message: str
    subject_document_id: Optional[uuid.UUID] = None
    subject_artifact_id: Optional[uuid.UUID] = None
    subject_run_id: Optional[uuid.UUID] = None
    guideline_id: Optional[uuid.UUID] = None


class ChatMessageRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    role: str
    content: str
    tool_name: Optional[str] = None
    tool_result_ref: Optional[dict] = None
    created_at: Optional[datetime] = None


class ChatTurnResponse(BaseModel):
    answer: str
    steps: list[dict] = Field(default_factory=list)


# ── domains ────────────────────────────────────────────────────────────────
class DomainRead(BaseModel):
    slug: str
    name: str
    has_corpus: bool = False
    qdrant_collection: Optional[str] = None


class IndexResult(BaseModel):
    slug: str
    indexed_chunks: int
    embeddings_available: bool


# ── compliance / guidelines ─────────────────────────────────────────────────
class GuidelineSection(BaseModel):
    section_no: Optional[str] = None
    title: str
    level: int = 1


class GuidelineRead(BaseModel):
    model_config = _ORM
    id: uuid.UUID
    code: str
    title: str
    domain: str
    version: Optional[str] = None
    description: Optional[str] = None
    status: str
    requirement_count: int = 0
    dimension_coverage: list[str] = Field(default_factory=list)


class GuidelineDetail(GuidelineRead):
    sections: list[GuidelineSection] = Field(default_factory=list)
    page_count: Optional[int] = None


class GuidelineUpdate(BaseModel):
    status: Optional[str] = None    # ingesting | extracted | ready
    title: Optional[str] = None
    version: Optional[str] = None
    description: Optional[str] = None


class GuidelineIngestResult(BaseModel):
    id: uuid.UUID
    code: str
    status: str
    sections: int
    requirements: int
    indexed_chunks: int


class ComplianceResult(BaseModel):
    """The rich, multi-dimensional audit result surfaced on RunDetail.compliance."""
    overall_score: float = 0.0                      # 0..1
    status_label: Optional[str] = None              # strong | moderate | weak | failing
    per_dimension: dict = Field(default_factory=dict)        # {dimension: 0..1}
    per_section: list[dict] = Field(default_factory=list)    # [{section,score,status,findings_count}]
    severity_counts: dict = Field(default_factory=dict)      # {critical,major,minor,info}
    findings: list[dict] = Field(default_factory=list)       # UI-shaped finding dicts
    summary: Optional[str] = None
    guideline: Optional[dict] = None                # {id, code, title, version}


# Resolve the forward reference used by RunDetail.compliance.
RunDetail.model_rebuild()
