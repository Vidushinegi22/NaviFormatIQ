"""Structure mapper node (LLM + TF-IDF via the ported section_mapper)."""
from __future__ import annotations

from typing import Any

from app.core.concurrency import run_sync


async def structure_mapper_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.schemas.document_model import DraftStructure, TemplateFingerprint
    from app.services.mapping.section_mapper import map_sections

    fp = TemplateFingerprint(**state["fingerprint"])
    draft = DraftStructure(**state["draft_structure"])
    mapping = await run_sync(map_sections, fp, draft)
    return {"section_mapping": mapping.model_dump(mode="json"), "current_agent": "structure_mapper"}
