"""Flow 1 — Regenerate a new version of a document.

ingestion → template_parser → draft_parser → structure_mapper → rag_retriever
→ content_generator → rules_compliance → diff_builder →[HITL]→ docx_writer → END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    content_generator_node,
    diff_builder_node,
    docx_writer_node,
    draft_parser_node,
    ingestion_router_node,
    rag_retriever_node,
    rules_compliance_node,
    structure_mapper_node,
    template_parser_node,
)
from app.agents.state import DocuMorphState

# Pause before docx_writer for human review of the diff.
INTERRUPT_BEFORE = ["docx_writer"]


def build_flow1_graph() -> StateGraph:
    g = StateGraph(DocuMorphState)
    g.add_node("ingestion_router", ingestion_router_node)
    g.add_node("template_parser", template_parser_node)
    g.add_node("draft_parser", draft_parser_node)
    g.add_node("structure_mapper", structure_mapper_node)
    g.add_node("rag_retriever", rag_retriever_node)
    g.add_node("content_generator", content_generator_node)
    g.add_node("rules_compliance", rules_compliance_node)
    g.add_node("diff_builder", diff_builder_node)
    g.add_node("docx_writer", docx_writer_node)

    g.set_entry_point("ingestion_router")
    g.add_edge("ingestion_router", "template_parser")
    g.add_edge("template_parser", "draft_parser")
    g.add_edge("draft_parser", "structure_mapper")
    g.add_edge("structure_mapper", "rag_retriever")
    g.add_edge("rag_retriever", "content_generator")
    g.add_edge("content_generator", "rules_compliance")
    g.add_edge("rules_compliance", "diff_builder")
    g.add_edge("diff_builder", "docx_writer")
    g.add_edge("docx_writer", END)
    return g
