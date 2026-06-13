"""
Sequential multi-stage orchestrator.

This is a deliberately plain Python function — not LangGraph — so the
hackathon demo runs without extra dependencies. Each stage emits a JSON
artifact and the final return is a ``ProcessJobResult`` so the FastAPI
layer can serve it verbatim. A future swap to ``langgraph.StateGraph`` is a
one-file change: the stage functions are already pure.
"""

from __future__ import annotations

import base64
import difflib
import io
import os
import re
import uuid
from typing import Optional

from app.schemas.document_model import (
    ComplianceFlag,
    DraftStructure,
    Mapping,
    MappingAction,
    ProcessJobResult,
    ReviewDiff,
    SectionMapping,
    TemplateFingerprint,
)
from app.rag.retriever import DomainProfile, load_domain_profile
from app.services.generation.rewriter import compliance_check, rag_fill, rewrite_section
from app.services.mapping.section_mapper import map_sections


# ---------------------------------------------------------------------------
# Stage entry points
# ---------------------------------------------------------------------------

def fingerprint_template(
    file_bytes: bytes, filename: str
) -> TemplateFingerprint:
    """Route to the right extractor based on file extension."""
    ext = os.path.splitext(filename)[1].lower()
    stream = io.BytesIO(file_bytes)
    if ext == ".docx":
        from app.services.extraction.word_ext import fingerprint_word_template

        return fingerprint_word_template(file_stream=stream, filename=filename)
    if ext == ".pdf":
        from app.services.extraction.pdf_ext import fingerprint_pdf_template

        return fingerprint_pdf_template(file_stream=stream, filename=filename)
    raise ValueError(f"Unsupported template extension: {ext}")


def structure_draft(file_bytes: bytes, filename: str) -> DraftStructure:
    ext = os.path.splitext(filename)[1].lower()
    stream = io.BytesIO(file_bytes)
    if ext == ".docx":
        from app.services.extraction.word_ext import structure_word_draft

        return structure_word_draft(file_stream=stream, filename=filename)
    if ext == ".pdf":
        from app.services.extraction.pdf_ext import structure_pdf_draft

        return structure_pdf_draft(file_stream=stream, filename=filename)
    if ext == ".txt":
        text = file_bytes.decode("utf-8", errors="replace")
        return _structure_plain_text(text, filename)
    raise ValueError(f"Unsupported draft extension: {ext}")


def _structure_plain_text(text: str, filename: str) -> DraftStructure:
    from app.schemas.document_model import DocumentMetadata, DraftSection

    sections: list[DraftSection] = []
    current = DraftSection(index=0, heading=None, level=0, text="")

    def _flush():
        if current.heading or current.text.strip():
            sections.append(current)

    for line in text.splitlines():
        stripped = line.strip()
        # Heuristic heading: a short line in TitleCase or ALL CAPS
        if stripped and len(stripped) <= 80 and (
            stripped.isupper() or stripped.istitle()
        ) and not stripped.endswith("."):
            _flush()
            current = DraftSection(
                index=len(sections), heading=stripped, level=1, text=""
            )
        elif stripped:
            current.text = (current.text + "\n" + stripped).strip() if current.text else stripped

    _flush()
    return DraftStructure(
        metadata=DocumentMetadata(source_file=filename, source_type="txt"),
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Rewrite stage
# ---------------------------------------------------------------------------

def _is_parent_heading(slot_index: int, slots: list) -> bool:
    """Return True if this heading is a container for child subsections.

    A heading at level N is a "parent" if the very next slot in the hierarchy
    is at a deeper level (N+1, N+2, …).  Parent headings like
    "3. Responsibilities" or "4. Procedure" are structural groupings whose
    body is intentionally empty in the original — they should NOT be
    RAG-filled with a TODO placeholder.
    """
    if slot_index + 1 >= len(slots):
        return False
    return slots[slot_index + 1].level > slots[slot_index].level


_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _protect_tables(text: str) -> tuple[str, list[str]]:
    """Swap each run of markdown table rows for a ``[[TABLE_n]]`` placeholder.

    Tables are structured data — sending them through the rewriter wastes
    tokens and risks mangled/merged grids. The placeholder keeps the table's
    POSITION in the prose; the original rows are restored verbatim after the
    rewrite."""
    out_lines: list[str] = []
    blocks: list[str] = []
    cur: list[str] = []
    for ln in (text or "").split("\n"):
        if _MD_TABLE_ROW_RE.match(ln):
            cur.append(ln.strip())
            continue
        if cur:
            blocks.append("\n".join(cur))
            cur = []
            out_lines.append(f"[[TABLE_{len(blocks)}]]")
        out_lines.append(ln)
    if cur:
        blocks.append("\n".join(cur))
        out_lines.append(f"[[TABLE_{len(blocks)}]]")
    return "\n".join(out_lines), blocks


def _restore_tables(text: str, blocks: list[str]) -> str:
    """Put the original table rows back in place of their placeholders.

    Tolerates a list marker glued onto the placeholder line (the list-structure
    restorer may bulletize it into ``- [[TABLE_1]]``). A placeholder the model
    dropped gets its table appended at the end — content is never lost;
    duplicates of a placeholder are removed."""
    for i, block in enumerate(blocks, 1):
        token = f"[[TABLE_{i}]]"
        line_re = re.compile(
            rf"(?m)^[ \t]*(?:[-*•◦▪‣][ \t]+)?\[\[TABLE_{i}\]\][ \t]*$"
        )
        if line_re.search(text):
            text = line_re.sub(lambda _m: block, text, count=1)
            text = text.replace(token, "")
        elif token in text:  # inline mid-sentence — put the table on its own lines
            text = text.replace(token, "\n" + block + "\n", 1).replace(token, "")
        else:
            text = (text.rstrip() + "\n\n" + block) if text.strip() else block
    return re.sub(
        r"(?m)^[ \t]*(?:[-*•◦▪‣][ \t]+)?\[\[TABLE_\d+\]\][ \t]*\n?",
        "",
        text,
    )


def build_rewritten_bodies(
    fingerprint: TemplateFingerprint,
    draft: DraftStructure,
    mapping: SectionMapping,
    domain: DomainProfile,
    doc_context: Optional[str] = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """For every slot, produce the rewritten body text and source citations."""
    rewritten: dict[str, str] = {}
    sources: dict[str, list[str]] = {}

    sections_by_idx = {s.index: s for s in draft.sections}
    mapping_by_slot = {m.slot_id: m for m in mapping.mappings}
    slots = fingerprint.heading_hierarchy

    for i, slot in enumerate(slots):
        m: Optional[Mapping] = mapping_by_slot.get(slot.slot_id)
        if not m:
            rewritten[slot.slot_id] = ""
            sources[slot.slot_id] = []
            continue

        if m.action in (MappingAction.FILL, MappingAction.REWRITE) and m.draft_section_idx is not None:
            section = sections_by_idx.get(m.draft_section_idx)
            source_text = section.text if section else ""
            if m.action == MappingAction.FILL:
                rewritten[slot.slot_id] = source_text
            else:
                from app.services.formatting.text_safe import restore_list_structure

                # Tables never round-trip through the model — placeholders
                # hold their position; the original rows return verbatim.
                protected, table_blocks = _protect_tables(source_text)
                out = rewrite_section(slot, protected, domain.glossary, doc_context)
                # Keep the section a list if the source was one (models sometimes
                # return bullets as bare lines).
                out = restore_list_structure(protected, out)
                rewritten[slot.slot_id] = _restore_tables(out, table_blocks)
            sources[slot.slot_id] = (
                [f"draft#section-{section.index}"] if section else []
            )
        elif m.action == MappingAction.RAG:
            # Parent/container headings (e.g. "3. Responsibilities") that have
            # child subsections should stay empty rather than getting a TODO
            # placeholder — their body is intentionally blank in the original.
            if _is_parent_heading(i, slots):
                rewritten[slot.slot_id] = ""
                sources[slot.slot_id] = []
            else:
                text, srcs = rag_fill(slot, domain)
                rewritten[slot.slot_id] = text
                sources[slot.slot_id] = srcs
        else:  # FLAG
            rewritten[slot.slot_id] = ""
            sources[slot.slot_id] = []

    return rewritten, sources


def build_original_bodies(
    fingerprint: TemplateFingerprint,
    draft: DraftStructure,
    mapping: SectionMapping,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Carry every slot's ORIGINAL draft text through verbatim — no AI rewrite.

    Used by the manual-edit path (``skip_ai_rewrite``): the reviewer wants to
    edit the document by hand, so each section's existing text becomes its
    "proposed" body and the diff reads as unchanged. Mirrors ``build_diff``'s
    original-text computation so the two stay in lockstep (every section shows
    as "Unchanged" on the review screen). Field updates (version/date/revision)
    are still applied at render time — only the body rewrite is skipped.
    """
    rewritten: dict[str, str] = {}
    sources: dict[str, list[str]] = {}

    sections_by_idx = {s.index: s for s in draft.sections}
    mapping_by_slot = {m.slot_id: m for m in mapping.mappings}

    for slot in fingerprint.heading_hierarchy:
        m: Optional[Mapping] = mapping_by_slot.get(slot.slot_id)
        sec = (
            sections_by_idx.get(m.draft_section_idx)
            if m and m.draft_section_idx is not None
            else None
        )
        rewritten[slot.slot_id] = sec.text if sec else ""
        sources[slot.slot_id] = [f"draft#section-{sec.index}"] if sec else []

    return rewritten, sources


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _section_tables_md(section) -> str:
    """Render a draft section's tables as markdown rows so they show up in the
    diff/preview (the renderer recognises ``| a | b |`` rows as a table)."""
    lines: list[str] = []
    for grid in (getattr(section, "tables", None) or []):
        for row in grid:
            cells = [(c or "").replace("\n", " ").strip() for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines).strip()


def build_diff(
    fingerprint: TemplateFingerprint,
    draft: DraftStructure,
    mapping: SectionMapping,
    rewritten: dict[str, str],
    sources: dict[str, list[str]],
) -> list[ReviewDiff]:
    """One ReviewDiff per template slot."""
    from app.services.formatting.text_safe import strip_todo_placeholders

    sections_by_idx = {s.index: s for s in draft.sections}
    mapping_by_slot = {m.slot_id: m for m in mapping.mappings}
    out: list[ReviewDiff] = []
    for slot in fingerprint.heading_hierarchy:
        m = mapping_by_slot.get(slot.slot_id)
        original = ""
        tables_md = ""
        if m and m.draft_section_idx is not None:
            sec = sections_by_idx.get(m.draft_section_idx)
            if sec:
                original = sec.text
                tables_md = _section_tables_md(sec)
        proposed = rewritten.get(slot.slot_id, "")
        # Strip [TODO] placeholder lines from proposed text so they never reach
        # the diff view or get appended alongside table content.  The compliance
        # check reads from the raw ``rewritten`` dict and still sees the TODO.
        proposed = strip_todo_placeholders(proposed)
        # Append any tables (as markdown) so the preview/edit shows them — but
        # only when the text doesn't already carry them inline at position
        # (the Word extractor inlines tables; PDF/text drafts may not). The
        # real table is carried over verbatim at render time.
        if tables_md:
            if not any(_MD_TABLE_ROW_RE.match(ln) for ln in original.splitlines()):
                original = (original + "\n\n" + tables_md).strip() if original else tables_md
            if not any(_MD_TABLE_ROW_RE.match(ln) for ln in proposed.splitlines()):
                proposed = (proposed + "\n\n" + tables_md).strip() if proposed else tables_md
        out.append(
            ReviewDiff(
                slot_id=slot.slot_id,
                title=slot.title,
                original=original,
                proposed=proposed,
                sources=sources.get(slot.slot_id, []),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    template_bytes: bytes,
    template_name: str,
    draft_bytes: bytes,
    draft_name: str,
    domain_profile_id: str = "pharma",
    output_format: str = "docx",
    embed_artifacts: bool = True,
) -> ProcessJobResult:
    """Run the full template + draft pipeline and return a ProcessJobResult."""
    job_id = uuid.uuid4().hex[:12]
    warnings: list[str] = []

    fp = fingerprint_template(template_bytes, template_name)
    draft = structure_draft(draft_bytes, draft_name)
    mapping = map_sections(fp, draft)

    domain = load_domain_profile(domain_profile_id)
    rewritten, sources = build_rewritten_bodies(fp, draft, mapping, domain)

    flags: list[ComplianceFlag] = []
    for slot in fp.heading_hierarchy:
        for note in compliance_check(slot, rewritten.get(slot.slot_id, ""), domain.format_rules):
            kind = (
                "missing"
                if "empty" in note.lower()
                else "length" if "short" in note.lower()
                else "format"
            )
            flags.append(ComplianceFlag(slot_id=slot.slot_id, kind=kind, note=note))

    diff = build_diff(fp, draft, mapping, rewritten, sources)

    # Emit artifacts
    artifact_docx_b64: Optional[str] = None
    artifact_pdf_b64: Optional[str] = None
    docx_bytes: Optional[bytes] = None
    try:
        from app.services.formatting.template_emitter import render_template

        docx_bytes = render_template(fp, rewritten, tables={})
    except Exception as e:
        warnings.append(f"Template rendering failed: {e}")

    if docx_bytes:
        # LibreOffice pass to refresh TOC + page numbers (best-effort)
        try:
            from app.services.office.office_pipeline import LibreOfficeUnavailable, available, convert

            if available():
                docx_bytes = convert(docx_bytes, "docx")
            else:
                warnings.append(
                    "LibreOffice not found — TOC and page numbers may not be refreshed."
                )
        except LibreOfficeUnavailable as e:
            warnings.append(str(e))
        except Exception as e:
            warnings.append(f"LibreOffice docx refresh failed: {e}")

        if embed_artifacts:
            artifact_docx_b64 = base64.b64encode(docx_bytes).decode("ascii")

        if output_format in ("pdf", "pdfa"):
            try:
                from app.services.office.office_pipeline import convert as _convert

                pdf_bytes = _convert(docx_bytes, output_format)  # type: ignore[arg-type]
                if embed_artifacts:
                    artifact_pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
            except Exception as e:
                warnings.append(f"PDF export failed: {e}")

    # Strip the template_b64 from the fingerprint on the wire — it bloats
    # the JSON response and the caller already uploaded it.
    fp_for_wire = fp.model_copy(update={"template_b64": None})

    return ProcessJobResult(
        job_id=job_id,
        fingerprint=fp_for_wire,
        structure=draft,
        mapping=mapping,
        flags=flags,
        diff=diff,
        artifact_docx_b64=artifact_docx_b64,
        artifact_pdf_b64=artifact_pdf_b64,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Apply reviewer decisions (HITL)
# ---------------------------------------------------------------------------

def apply_review_decisions(
    template_bytes: bytes,
    template_name: str,
    decisions: list[ReviewDiff],
    output_format: str = "docx",
) -> tuple[bytes, Optional[bytes], list[str]]:
    """Re-render the document after a HITL pass.

    ``decisions`` carries either ``reviewer_edit`` (preferred), the original
    proposed text, or — if rejected — an empty string.
    """
    fp = fingerprint_template(template_bytes, template_name)
    rewritten: dict[str, str] = {}
    for d in decisions:
        if d.accepted is False:
            rewritten[d.slot_id] = ""
        elif d.reviewer_edit is not None:
            rewritten[d.slot_id] = d.reviewer_edit
        else:
            rewritten[d.slot_id] = d.proposed

    from app.services.formatting.template_emitter import render_template

    docx_bytes = render_template(fp, rewritten, tables={})
    warnings: list[str] = []
    pdf_bytes: Optional[bytes] = None

    try:
        from app.services.office.office_pipeline import LibreOfficeUnavailable, available, convert

        if available():
            docx_bytes = convert(docx_bytes, "docx")
            if output_format in ("pdf", "pdfa"):
                pdf_bytes = convert(docx_bytes, output_format)  # type: ignore[arg-type]
        else:
            warnings.append("LibreOffice not found — TOC will not be refreshed.")
    except LibreOfficeUnavailable as e:
        warnings.append(str(e))
    except Exception as e:
        warnings.append(f"LibreOffice pass failed: {e}")

    return docx_bytes, pdf_bytes, warnings
