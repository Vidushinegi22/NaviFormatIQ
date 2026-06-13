"""Ingestion router — validates inputs and tags formats."""
from __future__ import annotations

from typing import Any

from app.agents.nodes.common import ext_of
from app.core.exceptions import BadRequestError


async def ingestion_router_node(state: dict[str, Any]) -> dict[str, Any]:
    template_uri = state.get("template_file_uri")
    draft_uri = state.get("draft_file_uri")
    if not template_uri and not draft_uri:
        raise BadRequestError("No input documents provided to the run.")
    updates: dict[str, Any] = {"status": "running", "current_agent": "ingestion_router"}
    if template_uri:
        updates["template_format"] = ext_of(template_uri)
    if draft_uri:
        updates["draft_format"] = ext_of(draft_uri)
    return updates
