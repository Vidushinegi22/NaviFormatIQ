"""Flow 2 — Style/format transfer (scaffold).

ingestion → style_apply → END
(Donor-doc transfer via style_engine, or apply an edited styling JSON.)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.nodes import ingestion_router_node, style_apply_node
from app.agents.state import DocuMorphState

INTERRUPT_BEFORE: list[str] = []


def build_flow2_graph() -> StateGraph:
    g = StateGraph(DocuMorphState)
    g.add_node("ingestion_router", ingestion_router_node)
    g.add_node("style_apply", style_apply_node)
    g.set_entry_point("ingestion_router")
    g.add_edge("ingestion_router", "style_apply")
    g.add_edge("style_apply", END)
    return g
