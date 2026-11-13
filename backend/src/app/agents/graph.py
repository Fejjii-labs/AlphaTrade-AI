"""LangGraph workflow definition for the trading copilot agent."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents import nodes
from app.agents.graph_state import AgentGraphState
from app.agents.routing import (
    route_after_approval,
    route_after_injection,
    route_after_intent,
    route_after_moderation,
    route_after_risk,
)
from app.agents.runtime import AgentRuntime

# Per-request dependencies travel in the invoke config, never in the compiled graph.
RUNTIME_CONFIG_KEY = "agent_runtime"

_compiled: CompiledStateGraph[Any, Any, Any, Any] | None = None
_compile_lock = threading.Lock()


def _invoke_runtime(config: RunnableConfig) -> AgentRuntime:
    runtime = (config.get("configurable") or {}).get(RUNTIME_CONFIG_KEY)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent graph invoked without an AgentRuntime; use invoke_agent_graph.")
    return runtime


def _wrap(name: str) -> Callable[[dict[str, Any], RunnableConfig], dict[str, Any]]:
    def _node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        from app.services.turn_context import current_turn

        # Resolve the node on every call, not at compile time: the graph is compiled
        # once per process, and a module-level replacement of ``nodes.<name>`` (test
        # stalls, forbidden-node guards) must still be the function that runs.
        node_fn: Callable[[dict[str, Any], AgentRuntime], dict[str, Any]] = getattr(nodes, name)
        turn = current_turn()
        # A durable chat turn scopes each node to its own phase runtime (session,
        # quota and provider ports); it takes precedence over the invoke runtime.
        if turn is not None and turn.node_scope is not None:
            with turn.node_scope(name) as phase_runtime:
                return node_fn(state, phase_runtime)
        return node_fn(state, _invoke_runtime(config))

    return _node


def build_agent_graph() -> StateGraph[Any, Any, Any, Any]:
    """Construct the uncompiled StateGraph with all workflow nodes wired."""
    # One last-value channel per AgentState field (see graph_state for why no reducers).
    graph: StateGraph[Any, Any, Any, Any] = StateGraph(AgentGraphState)

    node_names = (
        "receive_request",
        "auth_context",
        "quota_check",
        "rate_limit_check",
        "prompt_injection_check",
        "moderation_check",
        "message_classification",
        "intent_classification",
        "context_retrieval",
        "strategy_workflow_tools",
        "paper_action",
        "trading_analytics_retrieval",
        "market_context_retrieval",
        "indicator_calculation",
        "strategy_module_execution",
        "trade_proposal_generation",
        "trading_policy_check",
        "deterministic_risk_gate",
        "approval_decision",
        "tool_execution_if_allowed",
        "memory_update",
        "usage_tracking",
        "final_response",
        "narrative_enhancement",
        "output_validation",
    )
    for name in node_names:
        if not callable(getattr(nodes, name, None)):
            raise RuntimeError(f"Agent graph node {name!r} is not defined in app.agents.nodes.")
        # Nodes keep their dict-in/dict-out signature (parse_state/patch_state).
        graph.add_node(name, cast(Any, _wrap(name)))

    graph.set_entry_point("receive_request")
    graph.add_edge("receive_request", "auth_context")
    graph.add_edge("auth_context", "quota_check")
    graph.add_edge("quota_check", "rate_limit_check")
    graph.add_edge("rate_limit_check", "prompt_injection_check")
    graph.add_conditional_edges(
        "prompt_injection_check",
        route_after_injection,
        {"blocked": "final_response", "continue": "moderation_check"},
    )

    graph.add_conditional_edges(
        "moderation_check",
        route_after_moderation,
        {"blocked": "final_response", "continue": "message_classification"},
    )

    graph.add_edge("message_classification", "intent_classification")
    graph.add_edge("intent_classification", "context_retrieval")

    graph.add_conditional_edges(
        "context_retrieval",
        route_after_intent,
        {
            "trading_analysis": "market_context_retrieval",
            "analytics": "trading_analytics_retrieval",
            "strategy_workflow": "strategy_workflow_tools",
            "paper_action": "paper_action",
            "general": "memory_update",
        },
    )
    graph.add_edge("paper_action", "memory_update")
    graph.add_edge("strategy_workflow_tools", "memory_update")
    graph.add_edge("trading_analytics_retrieval", "memory_update")

    graph.add_edge("market_context_retrieval", "indicator_calculation")
    graph.add_edge("indicator_calculation", "strategy_module_execution")
    graph.add_edge("strategy_module_execution", "trade_proposal_generation")
    graph.add_edge("trade_proposal_generation", "trading_policy_check")
    graph.add_edge("trading_policy_check", "deterministic_risk_gate")

    graph.add_conditional_edges(
        "deterministic_risk_gate",
        route_after_risk,
        {
            "blocked": "final_response",
            "approval": "approval_decision",
            "tools": "tool_execution_if_allowed",
            "respond": "memory_update",
        },
    )

    graph.add_conditional_edges(
        "approval_decision",
        route_after_approval,
        {
            "tools": "tool_execution_if_allowed",
            "respond": "memory_update",
        },
    )

    graph.add_edge("tool_execution_if_allowed", "memory_update")
    graph.add_edge("memory_update", "usage_tracking")
    graph.add_edge("usage_tracking", "final_response")
    graph.add_edge("final_response", "narrative_enhancement")
    graph.add_edge("narrative_enhancement", "output_validation")
    graph.add_edge("output_validation", END)

    return graph


def compile_agent_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Compile a fresh graph. Callers normally want the shared :func:`get_agent_graph`."""
    return build_agent_graph().compile()


def get_agent_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """Process-wide compiled graph. It holds no per-request state, so threads share it."""
    global _compiled
    graph = _compiled
    if graph is None:
        with _compile_lock:
            if _compiled is None:
                _compiled = compile_agent_graph()
            graph = _compiled
    return graph


def invoke_agent_graph(state: dict[str, Any], runtime: AgentRuntime) -> dict[str, Any]:
    """Run one turn on the shared graph with this request's runtime."""
    config: RunnableConfig = {"configurable": {RUNTIME_CONFIG_KEY: runtime}}
    return cast(dict[str, Any], get_agent_graph().invoke(state, config=config))
