"""Orchestrate one interactive-agent turn over existing AlphaTrade services.

A turn may read and may store a proposal on the transcript. It does not confirm
that proposal, submit an order, or change trading mode.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.db.models import Conversation
from app.interactive_agent.action_registry import (
    TOOLS,
    Tool,
    require_action_permission,
    resolve_action,
    route_action,
)
from app.interactive_agent.actions import (
    DailyReviewInput,
    LearningStatusInput,
    PaperExecutionExplanationInput,
)
from app.interactive_agent.classify import TurnClassification, classify_turn
from app.interactive_agent.contracts import (
    PAYLOAD_KEY,
    SCHEMA_VERSION,
    AgentCapability,
    AgentCapabilityCatalog,
    AgentTurnRequest,
    AgentTurnResult,
    ArtifactKind,
    ConnectionRef,
    KnowledgeHit,
    ProposalDecisionRequest,
    ProposalLifecycle,
    ProvenanceSource,
    ScreenshotAnalysisContract,
    ScreenshotAnalysisRequest,
    StrategyHit,
    StructuredActionKind,
    StructuredActionProposal,
    TurnOperation,
    VoiceInputRequest,
    VoiceIoContract,
    VoiceOutputRequest,
)
from app.interactive_agent.conversation import (
    MODEL_REPLY_UNAVAILABLE,
    ConversationalResponder,
    _prose,
    compose_visible_reply,
)
from app.interactive_agent.conversation_context import (
    context_from_sources,
    conversational_history,
    resolve_trade_followup,
)
from app.interactive_agent.daily_review import read_daily_review, render_daily_review
from app.interactive_agent.knowledge_context import build_knowledge_context
from app.interactive_agent.orchestration import propose_action
from app.interactive_agent.paper_execution_explanation import read_paper_execution
from app.interactive_agent.proposals import (
    build_proposal,
    confirm_proposal,
    find_proposal,
    parse_journal_draft,
    reject_proposal,
)
from app.interactive_agent.reads import MarketQuoteReader, ReadBundle, gather_reads
from app.interactive_agent.retrieval import (
    VectorKnowledgeRetriever,
    retrieve_knowledge,
    retrieve_strategies,
)
from app.interactive_agent.safety import (
    capability_catalog,
    paper_safety_contract,
    refuse_real_trading_enablement,
)
from app.schemas.common import ConversationMessageRole, DocumentSourceType, StrictModel
from app.schemas.conversation import ConversationCreate
from app.schemas.governed_learning import GovernedLearningStatus
from app.services.agent_paper_execution import AgentPaperExecutionService
from app.services.conversation_service import ConversationService
from app.services.strategy_proposal_service import StrategyProposalService

logger = structlog.get_logger(__name__)


def _learning_status_reply(items: list[GovernedLearningStatus]) -> str:
    lines = [
        "Governed strategy learning status. Human approval uses the typed paper promotion "
        "endpoint; Agent prose cannot approve it."
    ]
    for item in items:
        lines.append(
            f"Proposal {item.proposal_id}; state={item.approval_state}; "
            f"base={item.base_version_id}; candidate={item.proposed_version_id}. "
            f"Recorded reason={json.dumps(item.reason[:160])}. "
            f"Replayed={item.replayed}; observed baseline delta={item.observed_net_pnl_delta}; "
            f"outperformed={item.outperformed_baseline}; improvement claim=false. "
            f"Paper validation completed={item.paper_validation_completed}; "
            f"insufficient evidence={item.insufficient_evidence}; "
            f"paper active={item.paper_active_version_id}; "
            f"can roll back={item.can_roll_back}; rollback version={item.rollback_version_id}. "
            f"Blockers={json.dumps(item.blockers)[:200]}"
        )
    if not items:
        lines.append("No governed proposals were found in the requested scope.")
    lines.append(
        "Paper only. Replay and paper evidence are separate; ACTIVE grants no live permission."
    )
    return "\n\n".join(lines)[:4000]


_BASE_LIMITATIONS = (
    "Free-form agent text does not mutate strategies, rules, or journal records.",
    "This turn used deterministic orchestration and did not call a narrative model.",
)
_MODEL_LIMITATIONS = (
    "Model text is not confirmation and does not write strategies, rules, or journal records.",
)
_SKIP_RETRIEVAL = frozenset(
    {
        AgentCapability.SCREENSHOT_ANALYSIS,
        AgentCapability.VOICE_IO,
        AgentCapability.DAILY_REVIEW,
    }
)


class InteractiveAgentService:
    """Tenant-scoped orchestration. Domain writes stay behind explicit confirm."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        market_reader: MarketQuoteReader | None = None,
        vector_retriever: VectorKnowledgeRetriever | None = None,
        responder: ConversationalResponder | None = None,
        paper_execution: AgentPaperExecutionService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._market_reader = market_reader
        self._vector_retriever = vector_retriever
        self._responder = responder
        self._paper_execution = paper_execution
        self._conversations = ConversationService(session)
        self.confirmation_changed = False
        self.pending_reply_warnings: tuple[str, ...] = ()

    def catalog(self) -> AgentCapabilityCatalog:
        catalog = capability_catalog(self._settings)
        catalog.actions = [tool.descriptor() for tool in TOOLS.values()]
        return catalog

    def apply_real_trading_enablement(self) -> None:
        """Refuse. No trading flag is assigned."""
        refuse_real_trading_enablement()

    def handle_turn(
        self,
        request: AgentTurnRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        ordinary_capture: bool = False,
    ) -> AgentTurnResult:
        """Persist a turn and prepare proposals through bounded canonical authorities."""
        safety = paper_safety_contract(self._settings)
        classification = classify_turn(request.message)
        if request.conversation_id is not None:
            # Preserve scoped not-found refusal without rebinding or updating the transcript.
            self._conversations.require(
                request.conversation_id, organization_id=organization_id, user_id=user_id
            )
        # Reject invalid actions and unauthorized membership before any transcript write.
        # Scoped followup selection is still resolved and revalidated under the lock below.
        routed = (
            route_action(request) if classification.operation is not TurnOperation.REFUSE else None
        )
        note_tools = {
            "journal.create",
            "journal.append",
            "knowledge.propose",
            "strategy.create",
            "strategy.refinement",
            "strategy.observation",
            "strategy.hypothesis",
        }
        if (
            ordinary_capture
            and request.action is None
            and classification.operation is not TurnOperation.REFUSE
            and (
                request.source_document_id is not None
                or (routed is not None and routed.name in note_tools)
            )
        ):
            routed = None
            classification = classification.model_copy(
                update={"operation": TurnOperation.READ, "action_kind": StructuredActionKind.NONE}
            )
        if (
            ordinary_capture
            and request.action is None
            and routed is None
            and classification.operation is TurnOperation.PROPOSE
        ):
            classification = classification.model_copy(
                update={"operation": TurnOperation.READ, "action_kind": StructuredActionKind.NONE}
            )
        if routed is not None:
            tool, _inputs = resolve_action(routed)
            require_action_permission(
                self._session,
                organization_id=organization_id,
                user_id=user_id,
                read=tool.behavior == "read",
            )
        conversation = (
            self._conversations.get_or_create(
                organization_id=organization_id,
                user_id=user_id,
                conversation_id=request.conversation_id,
                strategy_id=request.strategy_id,
            )
            if request.conversation_id is not None
            else self._conversations.create(
                ConversationCreate(
                    title=request.message.strip()[:120], strategy_id=request.strategy_id
                ),
                organization_id=organization_id,
                user_id=user_id,
            )
        )
        # Serialize selection changes in the same conversation. This never locks
        # an execution account or gives prose/selection execution authority.
        self._session.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == conversation.id,
                Conversation.organization_id == organization_id,
                Conversation.user_id == user_id,
            )
            .with_for_update()
        )
        history = conversational_history(self._conversations, conversation)
        missing_trade_context = None
        action: tuple[Tool, StrictModel] | None = None
        if classification.operation is not TurnOperation.REFUSE:
            continuity = resolve_trade_followup(
                self._session, conversation=conversation, request=request, routed=routed
            )
            routed, missing_trade_context = continuity.action, continuity.missing_context
            if routed is not None:
                action = resolve_action(routed)
                tool, _inputs = action
                require_action_permission(
                    self._session,
                    organization_id=organization_id,
                    user_id=user_id,
                    read=tool.behavior == "read",
                )
                classification = TurnClassification(
                    capability=tool.capability,
                    operation=TurnOperation.READ
                    if tool.behavior == "read"
                    else TurnOperation.PROPOSE,
                    artifact_kinds=[tool.artifact],
                    action_kind=tool.kind,
                    screenshot_requested=classification.screenshot_requested,
                    voice_requested=classification.voice_requested,
                )
        if request.analytics_filters is not None:
            if classification.operation is not TurnOperation.READ or action is not None:
                raise ValidationAppError("Analytics filters require a read-only turn.")
            classification = classification.model_copy(
                update={"capability": AgentCapability.STRATEGY_ANALYTICS}
            )
        prior = [
            turn.content[:200]
            for turn in self._conversations.history_turns(conversation)
            if turn.role == "user"
        ][-5:]
        user_row = self._conversations.append_message(
            conversation=conversation,
            role=ConversationMessageRole.USER,
            content=request.message,
            intent=classification.capability.value,
            payload={
                PAYLOAD_KEY: {
                    "schema_version": SCHEMA_VERSION,
                    "source_document_id": str(request.source_document_id)
                    if request.source_document_id
                    else None,
                    "capability": classification.capability.value,
                    "artifact_kinds": [kind.value for kind in classification.artifact_kinds],
                    "symbol": _context_token(request.symbol),
                    "timeframe": _context_token(request.timeframe),
                }
            },
        )
        limitations = list(_BASE_LIMITATIONS if self._responder is None else _MODEL_LIMITATIONS)
        if action is not None:
            limitations.extend(action[0].limitations)
        execution_explanation = None
        explanation_read = action is not None and action[0].name == "paper_trade.explain_execution"
        if explanation_read:
            assert action is not None and isinstance(action[1], PaperExecutionExplanationInput)
            execution_explanation = read_paper_execution(
                self._session,
                settings=self._settings,
                organization_id=organization_id,
                user_id=user_id,
                command_id=action[1].command_id,
            )
        recorded_trade = None
        recorded_read = action is not None and action[0].name == "paper_trade.read_recorded"
        if recorded_read:
            from app.interactive_agent.actions import RecordedTradeInput
            from app.interactive_agent.recorded_trade import RecordedTradeRead, read_recorded_trade

            assert action is not None and isinstance(action[1], RecordedTradeInput)
            recorded_trade = (
                RecordedTradeRead(missing_trade_context, missing_trade_context)
                if missing_trade_context is not None
                else read_recorded_trade(
                    self._session, action[1], organization_id=organization_id, user_id=user_id
                )
            )
        daily_review = None
        review_inputs = None
        learning_status: list[GovernedLearningStatus] = []
        learning_read = action is not None and action[0].name == "strategy.learning_status"
        if learning_read:
            from app.services.strategy_promotion import StrategyPromotionService

            assert action is not None
            assert isinstance(action[1], LearningStatusInput)
            inputs = action[1]
            promotion = StrategyPromotionService(self._session, self._settings)
            if inputs.proposal_id is not None:
                learning_status = [
                    promotion.status(
                        inputs.proposal_id, organization_id=organization_id, user_id=user_id
                    )
                ]
            else:
                learning_status = promotion.list_status(
                    organization_id=organization_id,
                    user_id=user_id,
                    strategy_id=inputs.strategy_id
                    or request.strategy_id
                    or conversation.strategy_id,
                    limit=inputs.limit,
                ).items
        if action is not None and action[0].name == "daily_review.read":
            assert isinstance(action[1], DailyReviewInput)
            review_inputs = action[1]
            daily_review = read_daily_review(
                self._session,
                review_inputs,
                organization_id=organization_id,
                user_id=user_id,
                now=datetime.now(UTC),
            )
            limitations.extend(daily_review.limitations)
        knowledge: list[KnowledgeHit] = []
        knowledge_context = ""
        strategies: list[StrategyHit] = []
        bundle = ReadBundle()
        if (
            classification.operation is not TurnOperation.REFUSE
            and not learning_read
            and not explanation_read
            and not recorded_read
            and (classification.capability not in _SKIP_RETRIEVAL)
            and (action is None or action[0].name != "paper_trade.prepare_execution")
        ):
            if classification.capability is AgentCapability.STRATEGY_ANALYTICS:
                knowledge_notes: list[str] = []
                strategy_notes: list[str] = []
            else:
                knowledge, knowledge_notes = retrieve_knowledge(
                    self._session,
                    organization_id=organization_id,
                    user_id=user_id,
                    query=request.message,
                    vector_retriever=self._vector_retriever,
                )
                source_context = build_knowledge_context(
                    self._session,
                    organization_id=organization_id,
                    user_id=user_id,
                    query=request.message,
                    hits=knowledge,
                )
                knowledge = source_context.hits
                knowledge_context = source_context.text
                knowledge_notes.extend(source_context.limitations)
                strategies, strategy_notes = retrieve_strategies(
                    self._session,
                    organization_id=organization_id,
                    user_id=user_id,
                    query=request.message,
                    list_all=classification.capability is AgentCapability.STRATEGY_RETRIEVAL,
                    strategy_id=request.strategy_id or conversation.strategy_id,
                )
            bundle = gather_reads(
                self._session,
                organization_id=organization_id,
                user_id=user_id,
                message=request.message,
                capability=classification.capability,
                strategy_id=request.strategy_id or conversation.strategy_id,
                market_reader=self._market_reader,
                symbol=_context_token(request.symbol),
                timeframe=_context_token(request.timeframe),
                analytics_filters=request.analytics_filters,
                max_rows=self._settings.journal_stats_max_rows,
            )
            limitations.extend(knowledge_notes)
            limitations.extend(strategy_notes)
            limitations.extend(bundle.limitations)
        proposals = self._proposals_for_turn(
            classification,
            conversation=conversation,
            message=request.message,
            organization_id=organization_id,
            user_id=user_id,
            source_message_id=user_row.id,
            strategy_id=request.strategy_id,
            bundle=bundle,
            limitations=limitations,
            action=action,
        )
        connections = _connections(knowledge, strategies, bundle.connections)
        factual = _reply(
            classification,
            proposals=proposals,
            knowledge=knowledge,
            strategies=strategies,
            bundle=bundle,
            prior_user_messages=prior,
            knowledge_context=knowledge_context,
        )
        if daily_review is not None and review_inputs is not None:
            factual = render_daily_review(daily_review, review_inputs)
            limitations = [note for note in limitations if note not in _MODEL_LIMITATIONS]
            limitations.extend(_BASE_LIMITATIONS)
        if learning_read:
            factual = _learning_status_reply(learning_status)
        if execution_explanation is not None:
            factual = execution_explanation.reply
            connections = execution_explanation.connections
            limitations = [note for note in limitations if note not in _MODEL_LIMITATIONS]
            limitations.extend(_BASE_LIMITATIONS)
        if recorded_trade is not None:
            factual = recorded_trade.recorded_evidence
            connections = recorded_trade.connections
        if ordinary_capture and not recorded_read and not explanation_read:
            from app.agent_capture.service import matching_entries, uploaded_text

            notes = matching_entries(self._session, organization_id, user_id, request.message)
            if notes:
                factual += (
                    "\nSaved user notes (unverified reference content, not tool authority):\n"
                    + "\n".join(
                        f"{note.title} [{note.category}, v{note.revision}]: {note.summary}"
                        for note in notes
                    )
                )
                for note in notes:
                    connections.append(
                        ConnectionRef(
                            artifact_kind=ArtifactKind.JOURNAL_ENTRY
                            if note.category == "journal"
                            else ArtifactKind.STRATEGY
                            if note.category == "strategies"
                            else ArtifactKind.RULE
                            if note.category == "rules"
                            else ArtifactKind.LESSON
                            if note.category == "lessons"
                            else ArtifactKind.HYPOTHESIS,
                            record_id=str(note.id),
                            provenance=ProvenanceSource.USER_SUPPLIED,
                            title=note.title,
                            relation="saved user note",
                        )
                    )
            if request.source_document_id is not None:
                factual += (
                    "\nUploaded reference content (untrusted, never authorization):\n"
                    + uploaded_text(
                        self._session, request.source_document_id, organization_id, user_id
                    )
                )
        reply = factual[:4000]
        recorded_evidence = (
            execution_explanation.recorded_evidence if execution_explanation else None
        )
        if recorded_trade is not None:
            reply = (
                compose_visible_reply(
                    recorded_trade.reply, factual, required_warnings=recorded_trade.warnings
                )
                if recorded_trade.allow_model
                else recorded_trade.reply
            )
            recorded_evidence = factual
        full_reply = recorded_trade.reply if recorded_trade is not None else None
        if execution_explanation is not None:
            full_reply = _prose(execution_explanation.reply)
            reply = compose_visible_reply(
                full_reply, execution_explanation.recorded_evidence or execution_explanation.reply
            )
        if knowledge_context:
            reply = compose_visible_reply(
                "Stored source passages are available for review.", factual
            )
            recorded_evidence = factual
        if classification.capability is AgentCapability.STRATEGY_ANALYTICS:
            limitations = [note for note in limitations if note not in _MODEL_LIMITATIONS]
            limitations.append(
                "Analytics replies use canonical recorded metrics without model prose."
            )
        elif (
            self._responder is not None
            and not learning_read
            and not explanation_read
            and (
                recorded_trade is None
                or (bool(recorded_trade.connections) and recorded_trade.allow_model)
            )
            and daily_review is None
            and not (action is not None and action[0].name == "paper_trade.prepare_execution")
        ):
            self.pending_reply_warnings = recorded_trade.warnings if recorded_trade else ()
            model_text = self._responder.compose(
                organization_id=organization_id,
                user_id=user_id,
                conversation_id=conversation.id,
                message=request.message,
                factual_context=factual,
                history=history,
            )
            if model_text == MODEL_REPLY_UNAVAILABLE:
                limitations.append(MODEL_REPLY_UNAVAILABLE)
            if recorded_trade is not None and model_text == MODEL_REPLY_UNAVAILABLE:
                model_text = recorded_trade.reply
            model_text = _prose(model_text)
            full_reply = model_text
            reply = compose_visible_reply(
                model_text,
                factual,
                required_warnings=recorded_trade.warnings if recorded_trade is not None else (),
            )
            recorded_evidence = factual
        assistant = self._conversations.append_message(
            conversation=conversation,
            role=ConversationMessageRole.ASSISTANT,
            content=reply,
            intent=classification.capability.value,
            payload={
                PAYLOAD_KEY: {
                    "schema_version": SCHEMA_VERSION,
                    "capability": classification.capability.value,
                    "operation": classification.operation.value,
                    "recorded_evidence": recorded_evidence,
                    "full_reply": full_reply,
                    "sources": [item.model_dump(mode="json") for item in connections],
                    **(
                        {
                            "trade_context": context_from_sources(
                                self._session, conversation, connections
                            ).model_dump(mode="json")
                        }
                        if recorded_read or explanation_read
                        else {}
                    ),
                    "proposals": [item.model_dump(mode="json") for item in proposals],
                    **(
                        {"daily_review": daily_review.model_dump(mode="json")}
                        if daily_review is not None
                        else {}
                    ),
                    **(
                        {
                            "paper_execution_explanation": {
                                "source_message_id": str(execution_explanation.source_message_id)
                                if execution_explanation.source_message_id is not None
                                else None,
                                "sources": [item.model_dump(mode="json") for item in connections],
                            }
                        }
                        if execution_explanation is not None
                        else {}
                    ),
                    "strategy_analytics": [
                        report.model_dump(mode="json") for report in bundle.strategy_analytics
                    ],
                    "governed_learning": [item.model_dump(mode="json") for item in learning_status],
                }
            },
        )
        self._session.flush()
        return AgentTurnResult(
            conversation_id=conversation.id,
            user_message_id=user_row.id,
            model_usage=[usage] if (usage := getattr(self._responder, "last_usage", None)) else [],
            assistant_message_id=assistant.id,
            capability=classification.capability,
            operation=classification.operation,
            artifact_kinds=classification.artifact_kinds,
            reply=reply,
            recorded_evidence=recorded_evidence,
            full_reply=full_reply,
            proposals=proposals,
            authority_mutated=any(proposal.authority_mutated for proposal in proposals),
            knowledge=knowledge,
            strategies=strategies,
            connections=connections,
            prior_user_messages=prior,
            market_quote=bundle.market_quote,
            portfolio_summary=bundle.portfolio_summary,
            statistics_summary=bundle.statistics_summary,
            daily_review=daily_review,
            strategy_analytics=bundle.strategy_analytics,
            governed_learning=learning_status,
            paper_safety=safety,
            screenshot=_screenshot_contract(classification.screenshot_requested),
            voice=_voice_contract(classification.voice_requested),
            limitations=_unique(limitations),
        )

    def confirm(
        self,
        proposal_id: uuid.UUID,
        body: ProposalDecisionRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> StructuredActionProposal:
        """Apply the explicit confirm boundary for one stored proposal."""
        self.confirmation_changed = False
        paper_safety_contract(self._settings)
        self._conversations.require(
            body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        _message, before = find_proposal(
            self._session,
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            proposal_id=proposal_id,
            lock=True,
        )
        updated = confirm_proposal(
            self._session,
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            proposal_id=proposal_id,
            expected_content_hash=body.expected_content_hash,
            statement=body.statement,
            settings=self._settings,
            paper_execution=self._paper_execution,
        )
        if before.status != updated.status:
            self.confirmation_changed = True
            self._conversations.append_message(
                conversation=self._conversations.require(
                    body.conversation_id,
                    organization_id=organization_id,
                    user_id=user_id,
                ),
                role=ConversationMessageRole.ASSISTANT,
                content=_decision_reply(updated, verb="confirmed"),
                intent="proposal_confirm",
                payload={
                    PAYLOAD_KEY: {
                        "schema_version": SCHEMA_VERSION,
                        "decision": "confirm",
                        "proposal_id": str(updated.proposal_id),
                        "status": updated.status.value,
                        "authority_mutated": updated.authority_mutated,
                    }
                },
            )
            self._session.flush()
        return updated

    def reject(
        self,
        proposal_id: uuid.UUID,
        body: ProposalDecisionRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> StructuredActionProposal:
        paper_safety_contract(self._settings)
        self._conversations.require(
            body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        _message, before = find_proposal(
            self._session,
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            proposal_id=proposal_id,
        )
        updated = reject_proposal(
            self._session,
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            proposal_id=proposal_id,
            expected_content_hash=body.expected_content_hash,
            statement=body.statement,
        )
        if before.status != updated.status:
            self._conversations.append_message(
                conversation=self._conversations.require(
                    body.conversation_id,
                    organization_id=organization_id,
                    user_id=user_id,
                ),
                role=ConversationMessageRole.ASSISTANT,
                content=_decision_reply(updated, verb="rejected"),
                intent="proposal_reject",
                payload={
                    PAYLOAD_KEY: {
                        "schema_version": SCHEMA_VERSION,
                        "decision": "reject",
                        "proposal_id": str(updated.proposal_id),
                        "status": updated.status.value,
                        "authority_mutated": False,
                    }
                },
            )
            self._session.flush()
        return updated

    def screenshot_contract(
        self,
        body: ScreenshotAnalysisRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ScreenshotAnalysisContract:
        """Return the unimplemented screenshot boundary. Image bytes are not read."""
        contract = ScreenshotAnalysisContract(reference_received=body.image_ref is not None)
        self._record_contract_note(
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            content=contract.reason,
            intent=AgentCapability.SCREENSHOT_ANALYSIS.value,
        )
        return contract

    def voice_input_contract(
        self,
        body: VoiceInputRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> VoiceIoContract:
        contract = VoiceIoContract(reference_received=body.audio_ref is not None)
        self._record_contract_note(
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            content=contract.reason,
            intent=AgentCapability.VOICE_IO.value,
        )
        return contract

    def voice_output_contract(
        self,
        body: VoiceOutputRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> VoiceIoContract:
        """Return the unimplemented speech boundary. The text is not synthesized."""
        contract = VoiceIoContract()
        self._record_contract_note(
            conversation_id=body.conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            content=contract.reason,
            intent=AgentCapability.VOICE_IO.value,
        )
        return contract

    def _record_contract_note(
        self,
        *,
        conversation_id: uuid.UUID | None,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        intent: str,
    ) -> None:
        if conversation_id is None:
            return
        conversation = self._conversations.require(
            conversation_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        self._conversations.append_message(
            conversation=conversation,
            role=ConversationMessageRole.ASSISTANT,
            content=content,
            intent=intent,
            payload={PAYLOAD_KEY: {"schema_version": SCHEMA_VERSION, "implemented": False}},
        )
        self._session.flush()

    def _proposals_for_turn(
        self,
        classification: TurnClassification,
        *,
        conversation: Conversation,
        message: str,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        source_message_id: uuid.UUID,
        strategy_id: uuid.UUID | None,
        bundle: ReadBundle,
        limitations: list[str],
        action: tuple[Tool, StrictModel] | None = None,
    ) -> list[StructuredActionProposal]:
        if action is not None and action[0].behavior == "propose":
            return [
                propose_action(
                    self._session,
                    tool=action[0],
                    inputs=action[1],
                    conversation=conversation,
                    source_message_id=source_message_id,
                    paper_execution=self._paper_execution,
                )
            ]
        if classification.action_kind is StructuredActionKind.NONE:
            return []
        if classification.action_kind is StructuredActionKind.ENABLE_REAL_TRADING:
            return [
                build_proposal(
                    conversation_id=conversation.id,
                    organization_id=organization_id,
                    user_id=user_id,
                    kind=StructuredActionKind.ENABLE_REAL_TRADING,
                    artifact_kind=ArtifactKind.TRADE_DECISION,
                    provenance=ProvenanceSource.USER_SUPPLIED,
                    status=ProposalLifecycle.REFUSED,
                    summary="Real trading enablement was refused.",
                    payload={"requested": True, "applied": False},
                    authority="none",
                )
            ]
        linked: uuid.UUID | None = None
        if classification.action_kind is StructuredActionKind.PROPOSE_STRATEGY:
            linked = _strategy_preview(
                self._session,
                conversation=conversation,
                message=message,
                strategy_id=strategy_id,
                source_message_id=source_message_id,
                limitations=limitations,
            )
        payload, authority, summary, artifact = _proposal_body(
            classification.action_kind,
            message=message,
            bundle=bundle,
            linked=linked,
        )
        return [
            build_proposal(
                conversation_id=conversation.id,
                organization_id=organization_id,
                user_id=user_id,
                kind=classification.action_kind,
                artifact_kind=artifact,
                provenance=ProvenanceSource.USER_SUPPLIED,
                status=ProposalLifecycle.PROPOSED,
                summary=summary,
                payload=payload,
                authority=authority,
                linked_strategy_proposal_id=linked,
            )
        ]


def _strategy_preview(
    session: Session,
    *,
    conversation: Conversation,
    message: str,
    strategy_id: uuid.UUID | None,
    source_message_id: uuid.UUID,
    limitations: list[str],
) -> uuid.UUID | None:
    if len(message.strip()) < 10:
        limitations.append("Strategy text was too short to store a preview draft.")
        return None
    try:
        record = StrategyProposalService(session).create_draft_from_text(
            conversation,
            text=message,
            strategy_id=strategy_id,
            source_message_id=source_message_id,
        )
    except Exception:
        logger.warning("interactive_agent_strategy_preview_unavailable")
        limitations.append(
            "Strategy preview draft was not stored. No strategy version was written."
        )
        return None
    return record.id


def _proposal_body(
    kind: StructuredActionKind,
    *,
    message: str,
    bundle: ReadBundle,
    linked: uuid.UUID | None,
) -> tuple[dict[str, object], str, str, ArtifactKind]:
    text = message.strip()[:1000]
    if kind is StructuredActionKind.PROPOSE_JOURNAL_ENTRY:
        draft = parse_journal_draft(message)
        missing = ", ".join(draft.incomplete_fields) or "none"
        return (
            {"journal": draft.model_dump(mode="json")},
            "journals",
            f"Journal proposal. Missing fields: {missing}.",
            ArtifactKind.JOURNAL_ENTRY,
        )
    if kind is StructuredActionKind.PROPOSE_STRATEGY:
        return (
            {
                "text": text,
                "compiled": False,
                "approved": False,
                "mutates_strategy_authority": False,
                "linked_preview_stored": linked is not None,
            },
            "strategy_conversation_proposals" if linked is not None else "none",
            "Strategy preview proposal. Versions were not changed.",
            ArtifactKind.STRATEGY,
        )
    if kind is StructuredActionKind.PROPOSE_RULE:
        return (
            {"text": text, "promoted": False},
            "none",
            "Rule proposal. Structured rules were not written.",
            ArtifactKind.RULE,
        )
    if kind is StructuredActionKind.PROPOSE_LESSON:
        return (
            {"text": text, "accepted": False},
            "lesson_candidates",
            "Lesson proposal. It was not accepted into lesson review.",
            ArtifactKind.LESSON,
        )
    if kind is StructuredActionKind.PROPOSE_TRADE_DECISION:
        return (
            {
                "text": text,
                "executable": False,
                "execution_attempted": False,
                "invalidation": list(bundle.invalidation),
                "stop_loss": list(bundle.stop_loss),
            },
            "none",
            "Trade-decision proposal. No order was submitted.",
            ArtifactKind.TRADE_DECISION,
        )
    if kind is StructuredActionKind.PROPOSE_HYPOTHESIS:
        return (
            {"text": text},
            "conversation_messages",
            "Hypothesis proposal. It is not a strategy or a rule.",
            ArtifactKind.HYPOTHESIS,
        )
    return (
        {"text": text},
        "conversation_messages",
        "Observation proposal. It is not a strategy, rule, or journal entry.",
        ArtifactKind.OBSERVATION,
    )


def _connections(
    knowledge: list[KnowledgeHit],
    strategies: list[StrategyHit],
    extras: list[ConnectionRef],
) -> list[ConnectionRef]:
    items = list(extras)
    for strategy in strategies:
        items.append(
            ConnectionRef(
                artifact_kind=ArtifactKind.STRATEGY,
                record_id=str(strategy.strategy_id),
                title=strategy.name,
                relation="strategy library",
                provenance=strategy.provenance,
            )
        )
    for chunk_hit in knowledge:
        items.append(
            ConnectionRef(
                artifact_kind=_knowledge_kind(chunk_hit.source_type),
                record_id=str(chunk_hit.chunk_id),
                title=chunk_hit.title[:200],
                relation="knowledge chunk",
                provenance=chunk_hit.provenance,
            )
        )
    unique: list[ConnectionRef] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.artifact_kind.value, item.record_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _knowledge_kind(source_type: str) -> ArtifactKind:
    if source_type == DocumentSourceType.TRADE_JOURNAL.value:
        return ArtifactKind.JOURNAL_ENTRY
    if source_type == DocumentSourceType.MISTAKES_DATABASE.value:
        return ArtifactKind.LESSON
    if source_type == DocumentSourceType.STRATEGY_TEMPLATE.value:
        return ArtifactKind.STRATEGY
    return ArtifactKind.OBSERVATION


def _reply(
    classification: TurnClassification,
    *,
    proposals: list[StructuredActionProposal],
    knowledge: list[KnowledgeHit],
    strategies: list[StrategyHit],
    bundle: ReadBundle,
    prior_user_messages: list[str],
    knowledge_context: str = "",
) -> str:
    proposal = proposals[0] if proposals else None
    if classification.operation is TurnOperation.REFUSE:
        text = (
            "Real trading stays disabled. execution_mode is paper and "
            "real_trading_enabled is false. No order was submitted."
        )
    elif classification.capability is AgentCapability.SCREENSHOT_ANALYSIS:
        text = "Screenshot analysis is not implemented. No image was interpreted."
    elif classification.capability is AgentCapability.VOICE_IO:
        text = "Voice input and output are not implemented. No audio was transcribed."
    elif proposal is not None and proposal.payload.get("action"):
        text = f"Drafted proposal {proposal.proposal_id}. {proposal.summary} "
        text += "Use the separate confirmation request with this proposal's content hash. "
        if proposal.payload["action"]["name"] == "paper_trade.propose":
            text += (
                f"Entry: {proposal.payload.get('entry')}; stop: {proposal.payload.get('stop')}; "
                f"targets: {proposal.payload.get('targets')}; "
                f"risk: {proposal.payload['risk_state']}. "
                "Current confirmation, risk and canonical execution gates remain required. "
                "No order was submitted."
            )
        else:
            text += "Domain application remains behind the declared authority."
    elif proposal is not None and proposal.kind is StructuredActionKind.PROPOSE_JOURNAL_ENTRY:
        text = (
            f"Drafted journal proposal {proposal.proposal_id}. It is not saved. "
            f"{proposal.summary} Confirm with the content hash to write one journal entry."
        )
    elif proposal is not None and proposal.kind is StructuredActionKind.PROPOSE_STRATEGY:
        text = (
            f"Drafted strategy proposal {proposal.proposal_id}. "
            "Strategy versions were not changed. "
            "The linked preview stays unconfirmed until the existing strategy "
            "confirm endpoint is used."
        )
    elif proposal is not None and proposal.kind is StructuredActionKind.PROPOSE_RULE:
        text = f"Drafted rule proposal {proposal.proposal_id}. Structured rules were not written."
    elif proposal is not None and proposal.kind is StructuredActionKind.PROPOSE_LESSON:
        coaching = bundle.coaching_note or "No coaching summary was attached."
        text = f"Drafted lesson proposal {proposal.proposal_id}. It was not accepted. {coaching}"
    elif proposal is not None and proposal.kind is StructuredActionKind.PROPOSE_TRADE_DECISION:
        text = f"Drafted trade-decision proposal {proposal.proposal_id}. {proposal.summary}"
        text += " execution_attempted is false. No paper order was created."
    elif proposal is not None:
        text = (
            f"Drafted {proposal.artifact_kind.value} proposal {proposal.proposal_id}. "
            f"{proposal.summary}"
        )
    elif classification.capability is AgentCapability.STRATEGY_ANALYTICS:
        text = bundle.analytics_summary or "Strategy analytics unavailable; no values estimated."
    elif classification.capability is AgentCapability.STRATEGY_BRAIN:
        text = bundle.brain_summary or "No stored setup evidence was available."
    elif classification.capability is AgentCapability.STRATEGY_RETRIEVAL:
        text = _strategy_reply(strategies)
        if bundle.brain_summary:
            text += "\n" + bundle.brain_summary
            text += (
                "\nStrategy approval alone is not execution eligibility. A confirmed Candidate, "
                "fresh action evidence, risk approval and an authorized trade plan are required. "
                "Do not infer missing evidence is present or that execution is approved."
            )
    elif classification.capability is AgentCapability.KNOWLEDGE_RETRIEVAL:
        text = _knowledge_reply(knowledge, knowledge_context)
    elif classification.capability is AgentCapability.MARKET_AND_PORTFOLIO:
        text = _market_reply(bundle)
    elif classification.capability is AgentCapability.STATISTICS_AND_PERFORMANCE:
        text = bundle.statistics_summary or "No performance figures were available."
    elif classification.capability is AgentCapability.TRADE_DISCUSSION:
        text = _trade_reply(bundle, knowledge)
    else:
        last_prior = prior_user_messages[-1] if prior_user_messages else "none"
        text = (
            f"Continuing the conversation. Prior user turns stored: {len(prior_user_messages)}. "
            f"Prior user context (not authoritative evidence): {last_prior}."
        )
    if knowledge and classification.capability is not AgentCapability.KNOWLEDGE_RETRIEVAL:
        titles = ", ".join(hit.title for hit in knowledge[:3])
        text = f"{text} Related knowledge: {titles}."
        if knowledge_context:
            remaining = 16000 - len(text) - 1
            text += "\n" + knowledge_context[:remaining]
    return text[:16000]


def _strategy_reply(strategies: list[StrategyHit]) -> str:
    if not strategies:
        return "No strategies matched in this tenant."
    rendered = "; ".join(item.summary for item in strategies[:5])
    return f"Strategies in this tenant: {rendered}."[:4000]


def _knowledge_reply(knowledge: list[KnowledgeHit], source_context: str = "") -> str:
    if source_context:
        return source_context
    if not knowledge:
        return "No knowledge chunks matched in this tenant."
    rendered = "; ".join(f"{hit.title}: {hit.snippet}" for hit in knowledge)
    return f"Knowledge matches: {rendered}."[:4000]


def _market_reply(bundle: ReadBundle) -> str:
    parts: list[str] = []
    if bundle.portfolio_summary:
        parts.append(bundle.portfolio_summary)
    if bundle.market_quote is not None:
        quote = bundle.market_quote
        parts.append(
            f"{quote.symbol} last {quote.last_price} source={quote.source} "
            f"is_live={str(quote.is_live).lower()} "
            f"is_stale={str(quote.is_stale).lower()} "
            f"fallback_used={str(quote.fallback_used).lower()} "
            f"provider={quote.provider_name}."
        )
        if quote.is_stale:
            parts.append("The quote is stale and is not a current market price.")
        if quote.evidence_context is not None:
            from app.interactive_agent.market_context import market_context_text

            parts.append(market_context_text(quote.evidence_context))
    elif bundle.market_availability:
        reason = bundle.market_reason or "No price was invented."
        parts.append(f"Canonical perpetual evidence is {bundle.market_availability}. {reason}")
    else:
        parts.append("No market quote was fetched.")
    return " ".join(parts)[:4000]


def _context_token(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip()
    return token or None


def _trade_reply(bundle: ReadBundle, knowledge: list[KnowledgeHit]) -> str:
    if bundle.connections:
        titles = ", ".join(item.title for item in bundle.connections[:5])
        return f"Related records: {titles}."
    if knowledge:
        return _knowledge_reply(knowledge)
    return "No stored trades matched this discussion."


def _decision_reply(proposal: StructuredActionProposal, *, verb: str) -> str:
    mutated = "yes" if proposal.authority_mutated else "no"
    return (
        f"Proposal {proposal.proposal_id} {verb}. "
        f"Status {proposal.status.value}. Authority mutated: {mutated}. "
        f"Resulting record: {proposal.resulting_record_id}. "
        "Real trading remains disabled."
    )[:4000]


def _screenshot_contract(requested: bool) -> ScreenshotAnalysisContract | None:
    if not requested:
        return None
    return ScreenshotAnalysisContract()


def _voice_contract(requested: bool) -> VoiceIoContract | None:
    if not requested:
        return None
    return VoiceIoContract()


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered
