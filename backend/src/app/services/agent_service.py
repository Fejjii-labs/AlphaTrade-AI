"""Agent orchestration service — runs the LangGraph workflow."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.graph import compile_agent_graph
from app.agents.runtime import AgentRuntime
from app.agents.state_utils import parse_state, state_to_dict
from app.core.config import Settings, get_settings
from app.core.operation_policy import reset_operation_decision
from app.observability.context import bind_identity, get_or_create_trace_id, set_trace_id
from app.providers.factory import resolve_market_data_provider
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.agent import AgentState
from app.schemas.chat import AgentMessageResponse
from app.schemas.common import (
    ConversationMessageRole,
    RiskAction,
    SafetyVerdict,
    StrategyProposalStatus,
    Timeframe,
)
from app.services.agent_paper_execution import CanonicalPaperEvidencePort
from app.services.indicator_service import IndicatorService
from app.services.market_cache import MarketDataCache
from app.services.market_data_service import MarketDataService
from app.services.rag_service import build_rag_service
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.strategies.registry import get_strategy_registry
from app.tools.registry import build_default_registry as build_tool_registry


@dataclass(frozen=True)
class AgentInvokeContext:
    """Identity context supplied by the API layer (not loaded from DB here)."""

    request_id: str
    user_id: uuid.UUID
    organization_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    trace_id: str | None = None


class AgentService:
    """Runs the compiled LangGraph agent with injected runtime boundaries."""

    def __init__(self, runtime: AgentRuntime | None = None) -> None:
        if runtime is None:
            settings = get_settings()
            rag = build_rag_service(settings)
            strategy_service = StrategyService(registry=get_strategy_registry())
            market_data_service = MarketDataService(
                resolve_market_data_provider(settings),
                cache=MarketDataCache(settings),
                indicator_service=IndicatorService(),
                strategy_service=strategy_service,
            )
            runtime = AgentRuntime(
                settings=settings,
                risk_service=RiskService(),
                strategy_service=strategy_service,
                tool_registry=build_tool_registry(
                    settings,
                    rag_service=rag,
                    market_data_service=market_data_service,
                ),
                market_data_service=market_data_service,
                rag_service=rag,
            )
        self._runtime = runtime
        self._graph = compile_agent_graph(self._runtime)

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    def run(
        self,
        message: str,
        context: AgentInvokeContext,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> AgentMessageResponse:
        from app.core.errors import ValidationAppError
        from app.providers.llm import OpenAILLMProvider

        if self._runtime.session is not None and isinstance(
            self._runtime.llm_provider, OpenAILLMProvider
        ):
            raise ValidationAppError(
                "Transaction-bound live Agent calls require the durable turn runtime."
            )
        if context.trace_id:
            set_trace_id(context.trace_id)
        else:
            get_or_create_trace_id()
        bind_identity(
            user_id=str(context.user_id),
            organization_id=str(context.organization_id),
        )

        conversation_id = context.conversation_id
        bound_strategy_id = context.strategy_id
        history: list = []
        user_message_id = None
        pending_proposal_id = None
        if self._runtime.session is not None:
            from app.services.conversation_service import ConversationService

            conv_service = ConversationService(self._runtime.session)
            conversation = conv_service.get_or_create(
                organization_id=context.organization_id,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                strategy_id=context.strategy_id,
            )
            conversation_id = conversation.id
            bound_strategy_id = conversation.strategy_id
            history = conv_service.history_turns(conversation)
            user_row = conv_service.append_message(
                conversation=conversation,
                role=ConversationMessageRole.USER,
                content=message,
                request_id=context.request_id,
            )
            user_message_id = user_row.id
            from app.services.strategy_proposal_service import StrategyProposalService

            latest_draft = StrategyProposalService(self._runtime.session).latest_draft(
                conversation.id,
                organization_id=context.organization_id,
                user_id=context.user_id,
            )
            pending_proposal_id = latest_draft.id if latest_draft is not None else None

        initial = AgentState(
            request_id=context.request_id,
            user_id=context.user_id,
            organization_id=context.organization_id,
            conversation_id=conversation_id,
            bound_strategy_id=bound_strategy_id,
            pending_proposal_id=pending_proposal_id,
            conversation_history=history,
            message=message,
            symbol=symbol,  # type: ignore[arg-type]
            timeframe=Timeframe(timeframe) if timeframe else None,
        )
        self._runtime.observability.emit_agent_run_started(initial)
        started = time.perf_counter()
        try:
            result_dict = self._graph.invoke(state_to_dict(initial))
            agent = parse_state(result_dict)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            self._runtime.observability.emit_agent_run_completed(agent, latency_ms=elapsed_ms)

            if self._runtime.workflow_persistence is not None:
                persisted = self._runtime.workflow_persistence.persist_agent_outcome(agent)
                if persisted.proposal_id is not None:
                    agent = agent.model_copy(update={"proposal_id": persisted.proposal_id})
                if persisted.approval_id is not None:
                    agent = agent.model_copy(update={"approval_id": persisted.approval_id})
            if self._runtime.session is not None and conversation_id is not None:
                from app.services.conversation_service import ConversationService
                from app.services.strategy_proposal_service import StrategyProposalService

                conv_service = ConversationService(self._runtime.session)
                conversation = conv_service.require(
                    conversation_id,
                    organization_id=context.organization_id,
                    user_id=context.user_id,
                )
                pending = None
                proposal_service = StrategyProposalService(self._runtime.session)
                if (
                    agent.intent.value == "structure_strategy"
                    and agent.safety_verdict is not SafetyVerdict.BLOCK
                ):
                    # A later structure request supersedes any open draft so confirmation
                    # cannot bind to a stale presented identity.
                    pending = proposal_service.create_draft_from_text(
                        conversation,
                        text=self._structure_text(agent),
                        strategy_id=bound_strategy_id,
                        source_message_id=user_message_id,
                    )
                    agent = agent.model_copy(update={"pending_proposal_id": pending.id})
                elif agent.pending_proposal_id is not None:
                    try:
                        pending = proposal_service.get(
                            agent.pending_proposal_id,
                            organization_id=context.organization_id,
                            user_id=context.user_id,
                            conversation_id=conversation.id,
                        )
                    except Exception:
                        pending = None
                content = agent.final_answer or "No response generated."
                if (
                    pending is not None
                    and pending.status is StrategyProposalStatus.DRAFT
                    and pending.content_hash
                ):
                    from app.agents.confirmation_identity import (
                        IDENTITY_BEGIN,
                        format_presented_confirmation_identity,
                        identity_from_proposal,
                    )

                    if IDENTITY_BEGIN not in content:
                        footer = format_presented_confirmation_identity(
                            identity_from_proposal(
                                pending,
                                conversation_id=conversation.id,
                                organization_id=context.organization_id,
                                user_id=context.user_id,
                            )
                        )
                        content = f"{content.rstrip()}\n\n{footer}"
                        agent = agent.model_copy(update={"final_answer": content})
                conv_service.append_message(
                    conversation=conversation,
                    role=ConversationMessageRole.ASSISTANT,
                    content=content,
                    request_id=context.request_id,
                    intent=agent.intent.value,
                    payload=self._safe_payload(agent),
                )
                self._runtime.session.commit()
            elif self._runtime.session is not None:
                self._runtime.session.commit()
        finally:
            reset_operation_decision()

        return self._to_response(
            agent,
            context,
            conversation_id=conversation_id,
            history_injected=len(history),
        )

    def _to_response(
        self,
        agent: AgentState,
        ctx: AgentInvokeContext,
        *,
        conversation_id: uuid.UUID | None,
        history_injected: int,
    ) -> AgentMessageResponse:
        limitations = [
            "Analysis and education only — not financial advice.",
            "Real exchange execution is disabled by default.",
            "Deterministic risk engine is the final authority.",
            "Structured strategy proposals remain drafts until explicit confirmation.",
        ]
        approval_status = "blocked"
        if agent.safety_verdict is not SafetyVerdict.BLOCK:
            if agent.risk_result and agent.risk_result.action is RiskAction.BLOCK:
                approval_status = "blocked"
            elif agent.approval_required:
                approval_status = "pending"
            else:
                approval_status = "not_required"

        resolved_conversation = conversation_id or ctx.conversation_id or uuid.uuid4()
        pending = None
        if (
            agent.pending_proposal_id is not None
            and self._runtime.session is not None
            and ctx.user_id is not None
        ):
            from app.services.strategy_proposal_service import StrategyProposalService

            try:
                pending = StrategyProposalService(self._runtime.session).get(
                    agent.pending_proposal_id,
                    organization_id=ctx.organization_id,
                    user_id=ctx.user_id,
                )
            except Exception:
                pending = None
        return AgentMessageResponse(
            conversation_id=str(resolved_conversation),
            request_id=agent.request_id,
            reply=agent.final_answer or "No response generated.",
            risk_level=agent.risk_level,
            confidence=agent.confidence,
            approval_required=agent.approval_required,
            approval_status=approval_status,
            approval_reason=agent.approval_reason,
            proposal_id=str(agent.proposal_id) if agent.proposal_id else None,
            approval_id=str(agent.approval_id) if agent.approval_id else None,
            citations=agent.citations,
            tool_outputs=agent.tool_outputs,
            risk_result=agent.risk_result,
            limitations=limitations,
            usage=agent.usage_metadata,
            analysis=agent.analysis_detail,
            narrative=agent.narrative_detail,
            narrative_meta=agent.narrative_metadata,
            paper_execution=agent.paper_execution,
            pending_proposal=pending,
            history_injected=history_injected,
        )

    def _structure_text(self, agent: AgentState) -> str:
        prior = [
            turn.content
            for turn in agent.conversation_history
            if turn.role == "user" and turn.content.strip()
        ]
        parts = [*prior[-6:], agent.message]
        return "\n".join(parts)[:8000]

    def _safe_payload(self, agent: AgentState) -> dict[str, object]:
        tool_names = [item.tool_name for item in agent.tool_outputs[:12]]
        source_types = [
            (
                citation.source_type.value
                if hasattr(citation.source_type, "value")
                else str(citation.source_type)
            )
            for citation in agent.citations[:12]
        ]
        return {
            "paper_execution": agent.paper_execution.model_dump(mode="json")
            if agent.paper_execution
            else None,
            "intent": agent.intent.value,
            "tool_names": tool_names,
            "citation_source_types": source_types,
            "pending_proposal_id": str(agent.pending_proposal_id)
            if agent.pending_proposal_id
            else None,
            "bound_strategy_id": str(agent.bound_strategy_id) if agent.bound_strategy_id else None,
        }


def build_agent_service(
    settings: Settings | None = None,
    session: Session | None = None,
    canonical_runtime: ProductionCanonicalRuntime | None = None,
    canonical_evidence: CanonicalPaperEvidencePort | None = None,
) -> AgentService:
    settings = settings or get_settings()
    risk = RiskService()
    strategies = StrategyService(registry=get_strategy_registry())
    if session is not None:
        runtime = AgentRuntime.from_session(
            session,
            settings=settings,
            risk_service=risk,
            strategy_service=strategies,
            strict_observability=settings.observability_strict_mode,
        )
    else:
        rag = build_rag_service(settings)
        tools = build_tool_registry(settings, rag_service=rag)
        runtime = AgentRuntime(
            settings=settings,
            risk_service=risk,
            strategy_service=strategies,
            tool_registry=tools,
            rag_service=rag,
        )
    if session is not None and canonical_runtime is not None and canonical_evidence is not None:
        from app.services.agent_paper_execution import AgentPaperExecutionService

        runtime.paper_execution_service = AgentPaperExecutionService(
            session, settings, canonical_runtime, canonical_evidence
        )
    return AgentService(runtime=runtime)
