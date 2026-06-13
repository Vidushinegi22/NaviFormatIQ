"""Flow graph registry + checkpointer wiring.

First pass uses an in-process MemorySaver (HITL resume within the running
process); durable run history is persisted to Neon by the runner. To enable
cross-restart HITL, swap get_checkpointer() for an AsyncPostgresSaver opened in
the app lifespan (langgraph-checkpoint-postgres is installed).
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import MemorySaver

from app.agents.graphs.flow1_regenerate import INTERRUPT_BEFORE as F1
from app.agents.graphs.flow1_regenerate import build_flow1_graph
from app.agents.graphs.flow2_style import INTERRUPT_BEFORE as F2
from app.agents.graphs.flow2_style import build_flow2_graph
from app.agents.graphs.flow3_compliance import INTERRUPT_BEFORE as F3
from app.agents.graphs.flow3_compliance import build_flow3_graph
from app.agents.graphs.flow_compliance import INTERRUPT_BEFORE as FC
from app.agents.graphs.flow_compliance import build_compliance_graph

_BUILDERS = {
    "regenerate": (build_flow1_graph, F1),
    "style": (build_flow2_graph, F2),
    # New read-only audit engine. The legacy flow3 (build_flow3_graph) is kept
    # importable for reference but is no longer the registered compliance flow.
    "compliance": (build_compliance_graph, FC),
}

FLOWS = tuple(_BUILDERS.keys())


@lru_cache(maxsize=1)
def get_checkpointer():
    return MemorySaver()


@lru_cache(maxsize=8)
def get_compiled_graph(flow: str):
    if flow not in _BUILDERS:
        raise ValueError(f"unknown flow: {flow}")
    builder, interrupt_before = _BUILDERS[flow]
    g = builder()
    return g.compile(checkpointer=get_checkpointer(), interrupt_before=list(interrupt_before))
