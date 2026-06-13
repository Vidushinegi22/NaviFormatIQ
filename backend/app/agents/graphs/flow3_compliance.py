"""Flow 3 — Domain templates/guidelines: apply or compliance-check (scaffold).

apply : full path (like Flow 1) + coverage_validator →[HITL]→ docx_writer → END
check : ... → rules_compliance → coverage_validator → END  (no rewrite emit)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    content_generator_node,
    coverage_validator_node,
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

INTERRUPT_BEFORE = ["docx_writer"]


def _after_coverage(state: dict) -> str:
    return "diff_builder" if state.get("mode") != "check" else "__end__"


def build_flow3_graph() -> StateGraph:
    g = StateGraph(DocuMorphState)
    g.add_node("ingestion_router", ingestion_router_node)
    g.add_node("template_parser", template_parser_node)
    g.add_node("draft_parser", draft_parser_node)
    g.add_node("structure_mapper", structure_mapper_node)
    g.add_node("rag_retriever", rag_retriever_node)
    g.add_node("content_generator", content_generator_node)
    g.add_node("rules_compliance", rules_compliance_node)
    g.add_node("coverage_validator", coverage_validator_node)
    g.add_node("diff_builder", diff_builder_node)
    g.add_node("docx_writer", docx_writer_node)

    g.set_entry_point("ingestion_router")
    g.add_edge("ingestion_router", "template_parser")
    g.add_edge("template_parser", "draft_parser")
    g.add_edge("draft_parser", "structure_mapper")
    g.add_edge("structure_mapper", "rag_retriever")
    g.add_edge("rag_retriever", "content_generator")
    g.add_edge("content_generator", "rules_compliance")
    g.add_edge("rules_compliance", "coverage_validator")
    g.add_conditional_edges(
        "coverage_validator",
        _after_coverage,
        {"diff_builder": "diff_builder", "__end__": END},
    )
    g.add_edge("diff_builder", "docx_writer")
    g.add_edge("docx_writer", END)
    return g
