"""Compliance audit graph — read-only, no HITL. Supersedes the legacy flow3.

extract_user_doc → load_guideline → align_sections → check_requirements
→ deterministic_checks → verify_findings → aggregate_scores → report_build → END
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.nodes.compliance_audit import (
    aggregate_scores_node,
    align_sections_node,
    check_requirements_node,
    deterministic_checks_node,
    extract_user_doc_node,
    load_guideline_node,
    report_build_node,
    verify_findings_node,
)
from app.agents.state import ComplianceState

INTERRUPT_BEFORE: list[str] = []  # read-only audit, no human gate


def build_compliance_graph() -> StateGraph:
    g = StateGraph(ComplianceState)
    g.add_node("extract_user_doc", extract_user_doc_node)
    g.add_node("load_guideline", load_guideline_node)
    g.add_node("align_sections", align_sections_node)
    g.add_node("check_requirements", check_requirements_node)
    g.add_node("deterministic_checks", deterministic_checks_node)
    g.add_node("verify_findings", verify_findings_node)
    g.add_node("aggregate_scores", aggregate_scores_node)
    g.add_node("report_build", report_build_node)

    g.set_entry_point("extract_user_doc")
    g.add_edge("extract_user_doc", "load_guideline")
    g.add_edge("load_guideline", "align_sections")
    g.add_edge("align_sections", "check_requirements")
    g.add_edge("check_requirements", "deterministic_checks")
    g.add_edge("deterministic_checks", "verify_findings")
    g.add_edge("verify_findings", "aggregate_scores")
    g.add_edge("aggregate_scores", "report_build")
    g.add_edge("report_build", END)
    return g
