"""Diff builder node — per-slot ReviewDiff for the HITL screen."""
from __future__ import annotations

from typing import Any

from app.core.concurrency import run_sync


async def diff_builder_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.schemas.document_model import (
        DraftStructure,
        SectionMapping,
        TemplateFingerprint,
    )
    from app.services.orchestration.pipeline_steps import build_diff

    fp = TemplateFingerprint(**state["fingerprint"])
    draft = DraftStructure(**state["draft_structure"])
    mapping = SectionMapping(**state["section_mapping"])
    diff = await run_sync(
        build_diff, fp, draft, mapping, state.get("rewritten") or {}, state.get("sources") or {}
    )
    return {
        "diff": [d.model_dump(mode="json") for d in diff],
        "status": "hitl",
        "current_agent": "diff_builder",
    }
