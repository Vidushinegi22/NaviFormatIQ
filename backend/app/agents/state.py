"""Shared LangGraph state for all DocuMorph flows.

Rules that keep the checkpointer payload small & JSON-serializable:
  * inputs are R2/local URIs, never bytes;
  * parsed objects are stored as dicts (``model_dump(mode="json")``), never models;
  * ``fingerprint`` is stored WITHOUT ``template_b64`` (re-loaded in docx_writer).
"""
from __future__ import annotations

from typing import Any, TypedDict


class ComplianceState(TypedDict, total=False):
    """State for the read-only compliance audit graph (Flow: compliance).

    Audits a user document against a pre-loaded guideline across five dimensions
    (content/structure/formatting/style/tone). JSON-serialisable channels only.
    """
    # ── identity / routing ──
    run_id: str
    project_id: str
    flow: str                       # always "compliance"
    domain_id: str | None
    guideline_id: str | None
    draft_file_uri: str | None
    output_format: str              # docx | pdf
    dimensions: list[str] | None    # subset to check; None/empty = all five

    # ── extracted user document ──
    doc_sections: list[dict]        # [{index, heading, level, text, page_range}]
    doc_meta: dict | None           # {page_count, title, source_type, ...}
    full_text: str | None

    # ── guideline (loaded from Neon + Qdrant) ──
    guideline_ref: dict | None      # {id, code, title, version, collection, sections, requirements}

    # ── alignment doc↔guideline ──
    alignment: dict | None          # {section_no: {doc_idx, heading, text, score, pages}}

    # ── results ──
    findings: list[dict]            # UI-shaped finding dicts (persisted to table)
    scores: dict | None             # {overall_score, per_dimension, per_section, severity_counts, status_label}
    summary: str | None
    compliance: dict | None         # assembled ComplianceResult summary (no findings array)
    report_uris: dict | None        # {docx, pdf, json, csv}
    rendered_docx_uri: str | None
    rendered_pdf_uri: str | None

    # ── tracking ──
    traces: list[dict]
    current_agent: str
    status: str
    warnings: list[str]
    error: str | None


class DocuMorphState(TypedDict, total=False):
    # ── identity / routing ──
    run_id: str
    project_id: str
    flow: str                       # regenerate | style | compliance
    mode: str                       # apply | check  (Flow 3)
    domain_id: str | None

    # ── inputs (URIs, never bytes) ──
    template_file_uri: str | None   # F1/F3: donor structure; F2: style source
    draft_file_uri: str | None      # F1/F3: content draft; F2: content target
    content_styling_json: dict | None
    output_format: str              # docx | pdf | pdfa
    normalize_fonts: bool           # F2
    promote_headings: bool          # F2
    style_source_mode: str          # F2: auto | guideline | example
    style_interpretation: dict | None  # F2: what the style source was detected as + applied
    user_suggestions: str | None    # F1 free-text change requests
    skip_ai_rewrite: bool           # F1: skip AI body rewrite (manual-edit path)
    version_bump: str               # F1: minor | major | none (fallback)
    target_version: str | None      # F1: explicit new version (max uploaded + 1)
    context_file_uris: list[str] | None    # F1: prior versions, context-only
    context_file_names: list[str] | None   # F1: their filenames (for the digest)

    template_format: str | None
    draft_format: str | None

    # ── F1 document profile + auto field-updates ──
    doc_profile: dict | None        # type / tone / summary / detected fields
    field_updates: dict | None      # version/date replacements + revision row

    # ── parsed (document_model schemas as dicts) ──
    fingerprint: dict | None
    draft_structure: dict | None
    section_mapping: dict | None

    # ── generated ──
    rag_chunks: list[dict]
    rewritten: dict[str, str]
    sources: dict[str, list[str]]

    # ── validated ──
    flags: list[dict]
    compliance_score: float
    coverage: dict | None

    # ── review / output ──
    diff: list[dict]
    hitl_feedback: list[dict] | None
    rendered_docx_uri: str | None
    rendered_pdf_uri: str | None

    # ── tracking ──
    traces: list[dict]
    current_agent: str
    status: str                     # pending|running|hitl|done|error
    warnings: list[str]
    error: str | None
