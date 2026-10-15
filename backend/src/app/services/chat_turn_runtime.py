"""Compatible chat route over the retained LangGraph engine with durable phases."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from app.agents.phase_runtime import node_phases, phase_evidence
from app.agents.runtime import AgentRuntime
from app.agents.state_utils import parse_state, state_to_dict
from app.core.config import Settings
from app.core.operation_policy import reset_operation_decision
from app.schemas.agent import AgentState
from app.schemas.chat import ChatMessageRequest
from app.schemas.common import (
    ConversationMessageRole,
    SafetyVerdict,
    StrategyProposalStatus,
    Timeframe,
)
from app.services.agent_service import AgentInvokeContext, build_agent_service
from app.services.conversation_service import ConversationService
from app.services.strategy_proposal_service import StrategyProposalService
from app.services.turn_context import current_turn
from app.services.turn_coordinator import (
    RESERVATION,
    TurnCoordinator,
    TurnReservation,
    transcript_revision,
)


def run_chat(
    coordinator: TurnCoordinator,
    settings: Settings,
    reservation: TurnReservation,
    body: ChatMessageRequest,
    canonical_runtime: Any,
    canonical_evidence: Any,
) -> dict[str, Any]:
    context = AgentInvokeContext(
        request_id=str(reservation.turn_id),
        user_id=reservation.user_id,
        organization_id=reservation.organization_id,
        conversation_id=reservation.conversation_id,
    )
    with coordinator.sessions() as session:
        coordinator.guard(session, reservation)
        conv = ConversationService(session).require(
            reservation.conversation_id,
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
        )
        history = ConversationService(session).history_turns(conv)
        user = ConversationService(session).append_message(
            conversation=conv,
            role=ConversationMessageRole.USER,
            content=body.message,
            request_id=context.request_id,
        )
        bound_strategy_id = conv.strategy_id
        latest = StrategyProposalService(session).latest_draft(
            conv.id, organization_id=reservation.organization_id, user_id=reservation.user_id
        )
        initial = AgentState(
            request_id=context.request_id,
            user_id=context.user_id,
            organization_id=context.organization_id,
            conversation_id=conv.id,
            bound_strategy_id=bound_strategy_id,
            pending_proposal_id=latest.id if latest else None,
            conversation_history=history,
            message=body.message,
            symbol=body.symbol,
            timeframe=Timeframe(body.timeframe) if body.timeframe else None,
        )
        user_message_id = user.id
        session.commit()
        revision = transcript_revision(session, reservation.conversation_id)
    service = build_agent_service(settings=settings)
    assert service.runtime.observability is not None
    service.runtime.observability.emit_agent_run_started(initial)
    graph_started = time.perf_counter()
    turn = current_turn()
    assert turn is not None
    phases = node_phases(
        service.runtime, coordinator, reservation, revision, canonical_runtime, canonical_evidence
    )
    # Revalidate the original transcript snapshot before each provider phase.
    # Strategy workflow tools update domain proposals, not this transcript.
    from contextlib import contextmanager

    @contextmanager
    def scope(name: str) -> Iterator[AgentRuntime]:
        if name in {
            "context_retrieval",
            "market_context_retrieval",
            "indicator_calculation",
            "narrative_enhancement",
        }:
            with coordinator.sessions() as session:
                coordinator.guard(session, reservation, revision=revision)
        with phases(name) as runtime:
            yield runtime

    turn.node_scope = scope
    try:
        agent = parse_state(service._graph.invoke(state_to_dict(initial)))
        service.runtime.observability.emit_agent_run_completed(
            agent, latency_ms=(time.perf_counter() - graph_started) * 1000
        )
    finally:
        turn.node_scope = None
        reset_operation_decision()
    with coordinator.sessions() as session:
        reservation_row = coordinator.guard(session, reservation, revision=revision)
        persisted_service = build_agent_service(
            settings=settings,
            session=session,
            canonical_runtime=canonical_runtime,
            canonical_evidence=phase_evidence(canonical_evidence, session),
        )
        if persisted_service.runtime.workflow_persistence:
            persisted = persisted_service.runtime.workflow_persistence.persist_agent_outcome(agent)
            agent = agent.model_copy(
                update={
                    "proposal_id": persisted.proposal_id or agent.proposal_id,
                    "approval_id": persisted.approval_id or agent.approval_id,
                }
            )
        conv = ConversationService(session).require(
            reservation.conversation_id,
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
        )
        proposals = StrategyProposalService(session)
        pending = None
        if (
            agent.intent.value == "structure_strategy"
            and agent.safety_verdict is not SafetyVerdict.BLOCK
        ):
            pending = proposals.create_draft_from_text(
                conv,
                text=service._structure_text(agent),
                strategy_id=bound_strategy_id,
                source_message_id=user_message_id,
            )
            agent = agent.model_copy(update={"pending_proposal_id": pending.id})
        elif agent.pending_proposal_id:
            try:
                pending = proposals.get(
                    agent.pending_proposal_id,
                    organization_id=reservation.organization_id,
                    user_id=reservation.user_id,
                    conversation_id=conv.id,
                )
            except Exception:
                pending = None
        content = agent.final_answer or "No response generated."
        if pending and pending.status is StrategyProposalStatus.DRAFT and pending.content_hash:
            from app.agents.confirmation_identity import (
                IDENTITY_BEGIN,
                format_presented_confirmation_identity,
                identity_from_proposal,
            )

            if IDENTITY_BEGIN not in content:
                footer = format_presented_confirmation_identity(
                    identity_from_proposal(
                        pending,
                        conversation_id=conv.id,
                        organization_id=reservation.organization_id,
                        user_id=reservation.user_id,
                    )
                )
                content = f"{content.rstrip()}\n\n{footer}"
                agent = agent.model_copy(update={"final_answer": content})
        ConversationService(session).append_message(
            conversation=conv,
            role=ConversationMessageRole.ASSISTANT,
            content=content,
            request_id=context.request_id,
            intent=agent.intent.value,
            payload=service._safe_payload(agent),
        )
        response = service._to_response(
            agent,
            context,
            conversation_id=reservation.conversation_id,
            history_injected=len(history),
        ).model_dump(mode="json")
        reservation_row.payload = {
            **reservation_row.payload,
            RESERVATION: {
                **reservation_row.payload[RESERVATION],
                "state": "completed",
                "response": response,
            },
        }
        session.commit()
    return response
