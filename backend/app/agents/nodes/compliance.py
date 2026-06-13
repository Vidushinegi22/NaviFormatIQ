"""Rules/compliance + coverage validation nodes."""
from __future__ import annotations

from typing import Any

from app.core.concurrency import run_sync


def _classify(note: str) -> str:
    low = note.lower()
    if "empty" in low or "missing" in low:
        return "missing"
    if "short" in low or "length" in low:
        return "length"
    return "format"


async def rules_compliance_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.rag.retriever import load_domain_profile
    from app.schemas.document_model import TemplateFingerprint
    from app.services.generation.rewriter import compliance_check

    fp = TemplateFingerprint(**state["fingerprint"])
    domain = await run_sync(load_domain_profile, state.get("domain_id") or "generic")
    rewritten = state.get("rewritten") or {}

    def _do() -> list[dict]:
        flags: list[dict] = []
        for slot in fp.heading_hierarchy:
            for note in compliance_check(slot, rewritten.get(slot.slot_id, ""), domain.format_rules):
                flags.append({"slot_id": slot.slot_id, "kind": _classify(note), "note": note})
        return flags

    flags = await run_sync(_do)
    total = max(1, len(fp.heading_hierarchy))
    score = round(max(0.0, 1.0 - len(flags) / total), 3)
    return {"flags": flags, "compliance_score": score, "current_agent": "rules_compliance"}


async def coverage_validator_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.schemas.document_model import TemplateFingerprint

    fp = TemplateFingerprint(**state["fingerprint"])
    rewritten = state.get("rewritten") or {}
    missing, filled = [], []
    required_total = 0
    for slot in fp.heading_hierarchy:
        text = (rewritten.get(slot.slot_id) or "").strip()
        if slot.required:
            required_total += 1
            (filled if text else missing).append(slot.slot_id)
        elif text:
            filled.append(slot.slot_id)
    coverage = {"missing": missing, "filled": filled, "required_total": required_total}
    return {"coverage": coverage, "current_agent": "coverage_validator"}
