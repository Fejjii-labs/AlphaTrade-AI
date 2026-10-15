"""Contracts for the interactive agent foundation.

Screenshot and voice members describe boundaries that are not implemented.
They never carry an invented analysis, transcript, or audio result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.agent_capture.contracts import SavedEntry
from app.daily_review.contracts import DailyReview
from app.interactive_agent.actions import ActionDescriptor, ActionRequest
from app.market_contracts.context import MarketEvidenceContext
from app.schemas.common import StrictModel
from app.schemas.governed_learning import GovernedLearningStatus
from app.schemas.strategy_analytics import StrategyAnalyticsFilters, StrategyAnalyticsReport

SCHEMA_VERSION: Literal["InteractiveAgent/v1"] = "InteractiveAgent/v1"
PAYLOAD_KEY = "interactive_agent"


class AgentCapability(StrEnum):
    """User-facing capabilities. One primary capability is chosen per turn."""

    GENERAL_CONVERSATION = "general_conversation"
    MARKET_AND_PORTFOLIO = "market_and_portfolio"
    STRATEGY_BRAIN = "strategy_brain"
    STRATEGY_ANALYTICS = "strategy_analytics"
    GOVERNED_LEARNING = "governed_learning"
    STRATEGY_RETRIEVAL = "strategy_retrieval"
    STRATEGY_AUTHORING = "strategy_authoring"
    PATTERN_AND_RULE_CAPTURE = "pattern_and_rule_capture"
    TRADE_DISCUSSION = "trade_discussion"
    PRE_TRADE_REASONING = "pre_trade_reasoning"
    JOURNAL_CAPTURE = "journal_capture"
    POST_TRADE_REFLECTION = "post_trade_reflection"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    STATISTICS_AND_PERFORMANCE = "statistics_and_performance"
    SCREENSHOT_ANALYSIS = "screenshot_analysis"
    VOICE_IO = "voice_io"
    PERSISTENT_CONTEXT = "persistent_context"
    DAILY_REVIEW = "daily_review"


class CapabilityStatus(StrEnum):
    IMPLEMENTED = "implemented"
    READ_THROUGH = "read_through"
    CONTRACT_ONLY = "contract_only"


class ArtifactKind(StrEnum):
    """Explicit record types. Free-form text does not collapse these together."""

    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    STRATEGY = "strategy"
    RULE = "rule"
    JOURNAL_ENTRY = "journal_entry"
    TRADE_DECISION = "trade_decision"
    LESSON = "lesson"


class ProvenanceSource(StrEnum):
    USER_SUPPLIED = "user_supplied"
    AGENT_INFERRED = "agent_inferred"
    WATCHER_OBSERVED = "watcher_observed"
    TRADE_OUTCOME = "trade_outcome"
    SYSTEM_GENERATED = "system_generated"


class StructuredActionKind(StrEnum):
    NONE = "none"
    PROPOSE_OBSERVATION = "propose_observation"
    PROPOSE_HYPOTHESIS = "propose_hypothesis"
    PROPOSE_STRATEGY = "propose_strategy"
    PROPOSE_RULE = "propose_rule"
    PROPOSE_JOURNAL_ENTRY = "propose_journal_entry"
    PROPOSE_TRADE_DECISION = "propose_trade_decision"
    PROPOSE_LESSON = "propose_lesson"
    PROPOSE_JOURNAL_APPEND = "propose_journal_append"
    PROPOSE_STRATEGY_EVIDENCE = "propose_strategy_evidence"
    PROPOSE_VALIDATION_REQUEST = "propose_validation_request"
    PROPOSE_KNOWLEDGE = "propose_knowledge"
    PROPOSE_WATCHER_CHANGE = "propose_watcher_change"
    ENABLE_REAL_TRADING = "enable_real_trading"


class ProposalLifecycle(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED_UNAPPLIED = "confirmed_unapplied"
    APPLIED = "applied"
    REJECTED = "rejected"
    REFUSED = "refused"


class TurnOperation(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    REFUSE = "refuse"


class AgentTurnRequest(StrictModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID | None = None
    strategy_id: UUID | None = None
    symbol: str | None = Field(default=None, max_length=30)
    timeframe: str | None = Field(default=None, max_length=16)
    action: ActionRequest | None = None
    analytics_filters: StrategyAnalyticsFilters | None = None
    source_document_id: UUID | None = None


class ProposalDecisionRequest(StrictModel):
    conversation_id: UUID
    expected_content_hash: str = Field(min_length=64, max_length=64)
    statement: str = Field(min_length=1, max_length=200)


class ScreenshotAnalysisRequest(StrictModel):
    """Reference only. The service does not fetch or interpret image bytes."""

    conversation_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    image_ref: str | None = Field(default=None, max_length=200)


class VoiceInputRequest(StrictModel):
    conversation_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    audio_ref: str | None = Field(default=None, max_length=200)


class VoiceOutputRequest(StrictModel):
    conversation_id: UUID | None = None
    text: str = Field(min_length=1, max_length=2000)


class PaperSafetyContract(StrictModel):
    execution_mode: Literal["paper"] = "paper"
    real_trading_enabled: Literal[False] = False
    agent_can_enable_real_trading: Literal[False] = False
    execution_attempted: Literal[False] = False
    exchange_mode: str = Field(min_length=1, max_length=64)


class ScreenshotAnalysisContract(StrictModel):
    capability: Literal[AgentCapability.SCREENSHOT_ANALYSIS] = AgentCapability.SCREENSHOT_ANALYSIS
    status: Literal[CapabilityStatus.CONTRACT_ONLY] = CapabilityStatus.CONTRACT_ONLY
    accepts_image: Literal[False] = False
    analyzed: Literal[False] = False
    analysis: None = None
    reference_received: bool = False
    reason: str = "Screenshot analysis is not implemented. No image was fetched or interpreted."


class VoiceIoContract(StrictModel):
    capability: Literal[AgentCapability.VOICE_IO] = AgentCapability.VOICE_IO
    status: Literal[CapabilityStatus.CONTRACT_ONLY] = CapabilityStatus.CONTRACT_ONLY
    input_implemented: Literal[False] = False
    output_implemented: Literal[False] = False
    transcript: None = None
    audio_generated: Literal[False] = False
    reference_received: bool = False
    reason: str = (
        "Voice input and output are not implemented. No audio was transcribed or synthesized."
    )


class KnowledgeHit(StrictModel):
    chunk_id: UUID
    document_id: UUID
    organization_id: UUID
    user_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=64)
    snippet: str = Field(max_length=240)
    match_count: int = Field(ge=0)
    provenance: ProvenanceSource
    retrieval_mode: Literal["lexical_store", "vector"]


class StrategyHit(StrictModel):
    strategy_id: UUID
    name: str = Field(min_length=1, max_length=120)
    setup_type: str = Field(min_length=1, max_length=64)
    version: int | None = Field(default=None, ge=1)
    validation_status: str | None = None
    lifecycle_status: str | None = None
    selected_version_id: UUID | None = None
    paper_eligible: bool
    summary: str = Field(min_length=1, max_length=300)
    provenance: ProvenanceSource = ProvenanceSource.USER_SUPPLIED


class ConnectionRef(StrictModel):
    artifact_kind: ArtifactKind
    record_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    relation: str = Field(min_length=1, max_length=120)
    provenance: ProvenanceSource


class MarketQuoteView(StrictModel):
    symbol: str = Field(min_length=2, max_length=30)
    last_price: str = Field(min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=80)
    is_live: bool
    is_stale: bool
    fallback_used: bool
    provider_name: str = Field(min_length=1, max_length=80)
    evidence_context: MarketEvidenceContext | None = None


class JournalDraft(StrictModel):
    symbol: str | None = None
    timeframe: str | None = None
    direction: str | None = None
    entry_rationale: str = Field(min_length=1, max_length=4000)
    lessons: str | None = Field(default=None, max_length=4000)
    mistakes: list[str] = Field(default_factory=list)
    incomplete_fields: list[str] = Field(default_factory=list)


class StructuredActionProposal(StrictModel):
    schema_version: Literal["InteractiveAgent/v1"] = SCHEMA_VERSION
    proposal_id: UUID
    conversation_id: UUID
    organization_id: UUID
    user_id: UUID
    kind: StructuredActionKind
    artifact_kind: ArtifactKind
    provenance: ProvenanceSource
    status: ProposalLifecycle
    summary: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any]
    content_hash: str = Field(min_length=64, max_length=64)
    authority: str = Field(min_length=1, max_length=80)
    applied: bool = False
    authority_mutated: bool = False
    linked_strategy_proposal_id: UUID | None = None
    resulting_record_id: UUID | None = None
    application_result: dict[str, Any] = Field(default_factory=dict)


class AgentTurnResult(StrictModel):
    model_usage: list[dict[str, Any]] = Field(default_factory=list)
    saved_entries: list[SavedEntry] = Field(default_factory=list)
    capture_status: Literal["not_needed", "saved", "failed", "clarification", "unavailable"] = (
        "not_needed"
    )
    capture_error: str | None = None
    capture_source_message_id: UUID | None = None
    capture_clarification: str | None = None

    schema_version: Literal["InteractiveAgent/v1"] = SCHEMA_VERSION
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    capability: AgentCapability
    operation: TurnOperation
    artifact_kinds: list[ArtifactKind]
    reply: str = Field(min_length=1, max_length=4000)
    recorded_evidence: str | None = Field(default=None, max_length=16000)
    full_reply: str | None = Field(default=None, max_length=16000)
    proposals: list[StructuredActionProposal] = Field(default_factory=list)
    knowledge: list[KnowledgeHit] = Field(default_factory=list)
    strategies: list[StrategyHit] = Field(default_factory=list)
    connections: list[ConnectionRef] = Field(default_factory=list)
    prior_user_messages: list[str] = Field(default_factory=list)
    market_quote: MarketQuoteView | None = None
    portfolio_summary: str | None = None
    statistics_summary: str | None = None
    daily_review: DailyReview | None = None
    strategy_analytics: list[StrategyAnalyticsReport] = Field(default_factory=list)
    governed_learning: list[GovernedLearningStatus] = Field(default_factory=list)
    paper_safety: PaperSafetyContract
    screenshot: ScreenshotAnalysisContract | None = None
    voice: VoiceIoContract | None = None
    limitations: list[str] = Field(default_factory=list)
    execution_attempted: Literal[False] = False
    real_trading_enabled: Literal[False] = False
    authority_mutated: bool = False


class CapabilityDescriptor(StrictModel):
    capability: AgentCapability
    status: CapabilityStatus
    authority: str = Field(min_length=1, max_length=160)
    notes: str = Field(min_length=1, max_length=400)


class AgentCapabilityCatalog(StrictModel):
    schema_version: Literal["InteractiveAgent/v1"] = SCHEMA_VERSION
    paper_safety: PaperSafetyContract
    items: list[CapabilityDescriptor]
    artifact_kinds: list[ArtifactKind]
    provenance_sources: list[ProvenanceSource]
    actions: list[ActionDescriptor] = Field(default_factory=list)
