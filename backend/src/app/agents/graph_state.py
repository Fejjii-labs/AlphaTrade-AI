"""Typed LangGraph channel schema mirroring :class:`~app.schemas.agent.AgentState`.

Values are the JSON-mode dump (``state_to_dict``): UUIDs and enums are strings,
nested models are dicts. Nodes still validate through ``parse_state`` /
``patch_state``; this schema gives the graph one named channel per field so a
dropped or renamed field fails ``test_graph_state_keys_match_agent_state``.

Merge semantics: every channel is last-value (replacement). Each node returns
the full validated state from ``patch_state``, including complete lists for
the accumulating fields (``retrieved_context``, ``citations``, ``tool_calls``,
``tool_outputs``, ``audit_events``, ``market_data_gaps``, ``strategy_signals``,
``citation_violations``). An ``operator.add`` reducer would append the full
list to itself and duplicate entries, so none is declared. Reducers become
possible only once nodes return partial updates.
"""

from __future__ import annotations

from typing import Any, TypedDict

_Model = dict[str, Any]


class AgentGraphState(TypedDict, total=False):
    # Identity / correlation
    request_id: str
    user_id: str | None
    organization_id: str | None
    conversation_id: str | None
    bound_strategy_id: str | None
    pending_proposal_id: str | None
    conversation_history: list[_Model]

    # Request context
    symbol: str | None
    timeframe: str | None
    message: str

    # Classification
    message_class: str
    intent: str
    intent_decision: _Model | None
    requires_clarification: bool
    safety_verdict: str | None

    # Gathered context
    market_context: _Model | None
    indicator_context: _Model | None
    market_data_quality: str | None
    market_data_gaps: list[str]
    strategy_signals: list[_Model]
    retrieved_context: list[_Model]
    rag_outcome: str | None

    # Tooling
    tool_calls: list[_Model]
    tool_outputs: list[_Model]

    # Proposal + risk
    trade_proposal: _Model | None
    risk_result: _Model | None
    risk_level: str | None
    confidence: float | None

    # Approval
    approval_required: bool
    approval_reason: str | None
    proposal_id: str | None
    approval_id: str | None

    # Output
    citations: list[_Model]
    cited_chunk_ids: list[str]
    citation_violations: list[str]
    paper_execution: _Model | None
    final_answer: str | None
    analysis_detail: _Model | None
    narrative_detail: _Model | None
    narrative_metadata: _Model | None

    # Observability
    usage_metadata: _Model | None
    audit_events: list[_Model]
