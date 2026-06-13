"""Template/draft parsing nodes (wrap the ported sync extractors)."""
from __future__ import annotations

from typing import Any

from app.agents.nodes.common import filename_from_uri, load_bytes
from app.core.concurrency import run_sync


async def template_parser_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.services.orchestration.pipeline_steps import fingerprint_template

    uri = state["template_file_uri"]
    name = filename_from_uri(uri, "template.docx")
    data = await run_sync(load_bytes, uri)
    fp = await run_sync(fingerprint_template, data, name)
    fp.template_b64 = None  # keep checkpoint small; reloaded in docx_writer

    # A formatting GUIDELINE ("body is Arial 11pt…") makes a poor regenerate
    # template: its own sections (Typography, Color Palette…) would become the
    # output's outline. Detect that early and tell the user which flow fits.
    warnings = list(state.get("warnings") or [])
    try:
        from app.services.style.guideline_interpreter import (
            decide_style_mode,
            extract_style_source_text,
        )

        digest = await run_sync(extract_style_source_text, data, name)
        # Heuristic-only classification — advisory text shouldn't cost an LLM call.
        effective, cls = decide_style_mode(digest, "auto", use_llm=False)
        if effective == "guideline" and cls.confidence >= 0.7:
            warnings.append(
                "The template reads as a formatting GUIDELINE (rules about "
                "fonts/colors/layout), not a fillable document template — its "
                "own sections would become the output's outline. For applying "
                "formatting rules to a document, use the Style flow instead."
            )
    except Exception:  # noqa: BLE001 — advisory only, never block parsing
        pass

    return {
        "fingerprint": fp.model_dump(mode="json"),
        "warnings": warnings,
        "current_agent": "template_parser",
    }


async def draft_parser_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.services.orchestration.pipeline_steps import structure_draft

    uri = state["draft_file_uri"]
    name = filename_from_uri(uri, "draft.docx")
    data = await run_sync(load_bytes, uri)
    draft = await run_sync(structure_draft, data, name)
    return {"draft_structure": draft.model_dump(mode="json"), "current_agent": "draft_parser"}
