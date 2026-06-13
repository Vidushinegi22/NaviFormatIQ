"""RAG retriever node — loads the domain profile; actual per-slot fill happens
in content_generator (ported rag_fill). Kept as a distinct node so the SSE
timeline shows a retrieval step and to surface corpus availability.
"""
from __future__ import annotations

from typing import Any

from app.core.concurrency import run_sync
from app.core.logging import get_logger

log = get_logger(__name__)


async def rag_retriever_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.rag.retriever import load_domain_profile

    domain_id = state.get("domain_id") or "generic"
    prof = await run_sync(load_domain_profile, domain_id)
    has_corpus = bool(getattr(prof, "corpus_path", None))

    # Only warn about a missing reference corpus when sections actually need it —
    # i.e. template slots with no matching draft section (action == "rag"). In the
    # common regenerate case every section comes from the prior version, so no gap
    # is filled from a corpus and the old blanket warning was just noise.
    mapping = state.get("section_mapping") or {}
    rag_slots = sum(
        1 for m in (mapping.get("mappings") or []) if m.get("action") == "rag"
    )
    warnings = list(state.get("warnings") or [])
    if not has_corpus and rag_slots > 0:
        warnings.append(
            f"{rag_slots} section{'s' if rag_slots != 1 else ''} had no matching "
            f"source and no '{domain_id}' reference corpus — left as placeholders "
            "for a reviewer to complete."
        )

    return {
        "rag_chunks": [],
        "domain_id": domain_id,
        "current_agent": "rag_retriever",
        "warnings": warnings,
    }
