"""LangGraph agent orchestration (planner only — no direct DB or provider access).

The graph module pulls in LangGraph, LangChain and their HTTP stack (about 20 MB
RSS, ~500 modules). It is loaded on first use through PEP 562 so that modules
which only need ``app.agents.mutation_policy`` or ``app.agents.runtime`` do not
pay for it at API start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agents.runtime import AgentRuntime

if TYPE_CHECKING:
    from app.agents.graph import (
        build_agent_graph,
        compile_agent_graph,
        get_agent_graph,
        invoke_agent_graph,
    )

_GRAPH_EXPORTS = frozenset(
    {"build_agent_graph", "compile_agent_graph", "get_agent_graph", "invoke_agent_graph"}
)


def __getattr__(name: str) -> Any:
    if name in _GRAPH_EXPORTS:
        from app.agents import graph

        return getattr(graph, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentRuntime",
    "build_agent_graph",
    "compile_agent_graph",
    "get_agent_graph",
    "invoke_agent_graph",
]
