"""LangGraph workflow definition for the trading copilot agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from langgraph.graph import END, StateGraph

from app.agents import nodes
from app.agents.routing import (
    route_after_approval,
    route_after_injection,
    route_after_intent,
    route_after_moderation,
    route_after_risk,
)
from app.agents.runtime import AgentRuntime


def _wrap(
    node_fn: Callable[[dict[str, Any], AgentRuntime], dict[str, Any]], runtime: AgentRuntime
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _node(state: dict[str, Any]) -> dict[str, Any]:
        from app.services.turn_context import current_turn

        turn = current_turn()
        if turn is not None and turn.node_scope is not None:
            with turn.node_scope(node_fn.__name__) as phase_runtime:
                return node_fn(state, phase_runtime)
        return node_fn(state, runtime)

    return _node


def build_agent_graph(runtime: AgentRuntime) -> StateGraph:
    """Construct the uncompiled StateGraph with all workflow nodes wired."""
    graph: StateGraph = StateGraph(dict)

    node_names = [
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
    ]
    node_fns = {
        "receive_request": nodes.receive_request,
        "auth_context": nodes.auth_context,
        "quota_check": nodes.quota_check,
        "rate_limit_check": nodes.rate_limit_check,
        "prompt_injection_check": nodes.prompt_injection_check,
        "moderation_check": nodes.moderation_check,
        "message_classification": nodes.message_classification,
        "intent_classification": nodes.intent_classification,
        "context_retrieval": nodes.context_retrieval,
        "strategy_workflow_tools": nodes.strategy_workflow_tools,
        "paper_action": nodes.paper_action,
        "trading_analytics_retrieval": nodes.trading_analytics_retrieval,
        "market_context_retrieval": nodes.market_context_retrieval,
        "indicator_calculation": nodes.indicator_calculation,
        "strategy_module_execution": nodes.strategy_module_execution,
        "trade_proposal_generation": nodes.trade_proposal_generation,
        "trading_policy_check": nodes.trading_policy_check,
        "deterministic_risk_gate": nodes.deterministic_risk_gate,
        "approval_decision": nodes.approval_decision,
        "tool_execution_if_allowed": nodes.tool_execution_if_allowed,
        "memory_update": nodes.memory_update,
        "usage_tracking": nodes.usage_tracking,
        "final_response": nodes.final_response,
        "narrative_enhancement": nodes.narrative_enhancement,
        "output_validation": nodes.output_validation,
    }
    for name in node_names:
        # This established dict graph predates LangGraph's typed StateLike bound.
        graph.add_node(name, cast(Any, _wrap(node_fns[name], runtime)))

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


def compile_agent_graph(runtime: AgentRuntime):
    """Return a compiled graph ready for ``invoke``."""
    return build_agent_graph(runtime).compile()
