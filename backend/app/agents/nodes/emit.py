"""Output nodes: docx_writer (Flow 1/3) and style_apply (Flow 2)."""
from __future__ import annotations

import base64
from typing import Any

from app.agents.nodes.common import DOCX_MIME, PDF_MIME, filename_from_uri, load_bytes
from app.core.concurrency import run_office, run_sync
from app.core.logging import get_logger
from app.storage import get_storage

log = get_logger(__name__)


def _apply_hitl(rewritten: dict[str, str], feedback: list[dict] | None) -> dict[str, str]:
    if not feedback:
        return rewritten
    out = dict(rewritten)
    for d in feedback:
        sid = d.get("slot_id")
        if not sid:
            continue
        if d.get("accepted") is False:
            out[sid] = ""
        elif d.get("reviewer_edit") is not None:
            out[sid] = d["reviewer_edit"]
        elif d.get("proposed") is not None:
            out[sid] = d["proposed"]
    return out


def _plan_from_feedback(
    rewritten: dict[str, str], feedback: list[dict] | None
) -> tuple[dict[str, str], dict]:
    """Turn reviewer decisions into (body text per slot, structural edits).

    Decisions may carry: ``accepted`` (False → remove the whole section),
    ``reviewer_edit`` (the final body text), ``title`` (rename the heading),
    and ``is_new`` + ``level`` (insert a brand-new section after the previous
    kept section)."""
    rewritten = dict(rewritten)
    titles: dict[str, str] = {}
    removed: list[str] = []
    new_sections: list[dict] = []
    if not feedback:
        return rewritten, {"titles": titles, "removed": removed, "new_sections": new_sections}

    last_kept: str | None = None
    for d in feedback:
        sid = d.get("slot_id")
        if not sid:
            continue
        if d.get("is_new"):
            body = d.get("reviewer_edit")
            if body is None:
                body = d.get("proposed") or ""
            new_sections.append({
                "after_slot_id": last_kept,
                "title": d.get("title") or "New Section",
                "level": d.get("level") or 1,
                "text": body,
            })
            continue
        if d.get("accepted") is False:
            removed.append(sid)
            continue
        if d.get("reviewer_edit") is not None:
            rewritten[sid] = d["reviewer_edit"]
        elif d.get("proposed") is not None:
            rewritten[sid] = d["proposed"]
        if d.get("title"):
            titles[sid] = d["title"]
        last_kept = sid
    return rewritten, {"titles": titles, "removed": removed, "new_sections": new_sections}


async def docx_writer_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.schemas.document_model import TemplateFingerprint
    from app.services.formatting.template_emitter import render_template

    fp = TemplateFingerprint(**state["fingerprint"])
    warnings = list(state.get("warnings") or [])

    # Reload the ORIGINAL template bytes only when the source is a real .docx —
    # the heading-walk renderer mutates that file in place to preserve styles.
    # For PDF (or any non-OOXML) sources there is no .docx to reuse, so we leave
    # template_b64 unset and let render_template synthesize the document from the
    # fingerprint instead of opening non-zip bytes as a .docx (→ BadZipFile).
    tpl_uri = state.get("template_file_uri")
    if tpl_uri and fp.source_format == "docx":
        tpl_bytes = await run_sync(load_bytes, tpl_uri)
        if tpl_bytes[:4] == b"PK\x03\x04":  # OOXML packages are ZIP archives
            fp.template_b64 = base64.b64encode(tpl_bytes).decode("ascii")
        else:
            warnings.append(
                "Template source is not a valid Word file — building the "
                "document from its detected structure instead."
            )

    rewritten, edits = _plan_from_feedback(
        dict(state.get("rewritten") or {}), state.get("hitl_feedback")
    )
    field_updates = state.get("field_updates")

    docx_bytes = await run_sync(render_template, fp, rewritten, {}, edits, field_updates)

    out_format = state.get("output_format", "docx")
    pdf_bytes = None
    try:
        from app.services.office.office_pipeline import available, convert

        if await run_sync(available):
            docx_bytes = await run_office(convert, docx_bytes, "docx")
            if out_format in ("pdf", "pdfa"):
                pdf_bytes = await run_office(convert, docx_bytes, out_format)
        else:
            warnings.append("LibreOffice not found — TOC/page fields not refreshed.")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"LibreOffice pass failed: {e}")

    storage = get_storage()
    pid = state.get("project_id", "unknown")
    key = storage.make_key(project_id=pid, kind="rendered", filename="output.docx")
    obj = await run_sync(storage.put, docx_bytes, key=key, content_type=DOCX_MIME)
    updates: dict[str, Any] = {
        "rendered_docx_uri": obj.uri,
        "warnings": warnings,
        "status": "done",
        "current_agent": "docx_writer",
    }
    if pdf_bytes:
        pkey = storage.make_key(project_id=pid, kind="rendered", filename="output.pdf")
        pobj = await run_sync(storage.put, pdf_bytes, key=pkey, content_type=PDF_MIME)
        updates["rendered_pdf_uri"] = pobj.uri
    return updates


async def style_apply_node(state: dict[str, Any]) -> dict[str, Any]:
    """Flow 2: apply a style source's look to a content target (or a styling JSON)."""
    storage = get_storage()
    pid = state.get("project_id", "unknown")
    content_uri = state.get("draft_file_uri")
    styling_json = state.get("content_styling_json")
    warnings = list(state.get("warnings") or [])
    interpretation: dict[str, Any] | None = None

    if styling_json is not None and content_uri:
        # Manual-edit path: apply an edited DocumentStyling to extracted content.
        from app.schemas.document_model import DocumentStyling
        from app.services.extraction.pdf_ext import extract_pdf_document
        from app.services.extraction.word_ext import extract_word_document
        from app.services.formatting.formater_apply import apply_styling
        import io

        data = await run_sync(load_bytes, content_uri)
        name = filename_from_uri(content_uri, "content.docx")

        def _apply() -> bytes:
            if data[:5] == b"%PDF-" or name.lower().endswith(".pdf"):
                content, _ = extract_pdf_document(file_stream=io.BytesIO(data), filename=name)
            else:
                content, _ = extract_word_document(file_stream=io.BytesIO(data), filename=name)
            styling = DocumentStyling(**styling_json)
            return apply_styling(content, styling).getvalue()

        result = await run_sync(_apply)
    else:
        # Style-source path: adapt to whether the style template is an EXAMPLE
        # document (copy its look) or a FORMATTING GUIDELINE (read its rules).
        from app.services.style.style_engine import transfer_style_smart

        style_uri = state.get("template_file_uri")
        mode = state.get("style_source_mode", "auto")
        content_bytes = await run_sync(load_bytes, content_uri)
        style_bytes = await run_sync(load_bytes, style_uri)
        outcome = await run_sync(
            transfer_style_smart,
            content_bytes,
            filename_from_uri(content_uri, "content.docx"),
            style_bytes,
            filename_from_uri(style_uri, "style.docx"),
            mode=mode,
            normalize_fonts=state.get("normalize_fonts", True),
            promote_headings=state.get("promote_headings", True),
        )
        result = outcome.docx_bytes
        interpretation = {
            "mode_used": outcome.mode_used,
            "detected_kind": outcome.detected_kind,
            "confidence": outcome.confidence,
            "reason": outcome.reason,
            "summary": outcome.summary,
            "spec": outcome.spec,
            "structure": outcome.structure,
        }
        s = outcome.structure or {}
        struct_note = (
            f"Understood the document: {s.get('headings', 0)} heading(s), "
            f"{s.get('list_items', 0)} list item(s), {s.get('tables', 0)} table(s)."
        )
        if outcome.mode_used == "guideline":
            warnings.append(
                f"Style template read as a STYLE GUIDE "
                f"({outcome.confidence:.0%} confidence). {struct_note}"
            )
        else:
            warnings.append(
                f"Style template profiled as an example document. {struct_note}"
            )
        warnings.extend(outcome.warnings)

    key = storage.make_key(project_id=pid, kind="rendered", filename="styled.docx")
    obj = await run_sync(storage.put, result, key=key, content_type=DOCX_MIME)
    return {
        "rendered_docx_uri": obj.uri,
        "warnings": warnings,
        "style_interpretation": interpretation,
        "status": "done",
        "current_agent": "style_apply",
    }
