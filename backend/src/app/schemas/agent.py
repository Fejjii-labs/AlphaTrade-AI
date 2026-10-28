"""Typed LangGraph agent state (master prompt §10).

This is the single mutable object threaded through the agent workflow. Keeping
it strongly typed avoids untyped-dict drift between nodes and makes the graph
testable. Fields are optional and filled progressively as nodes execute.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_paper import AgentPaperResult
from app.schemas.analysis import TradingAnalysisDetail
from app.schemas.audit import AuditEvent
from app.schemas.common import Confidence, RiskSeverity, SafetyVerdict, Symbol, Timeframe
from app.schemas.market import IndicatorContext, MarketSnapshot
from app.schemas.narrative import NarrativeMetadata, TradingNarrativeDetail
from app.schemas.proposal import TradeProposal
from app.schemas.rag import Citation
from app.schemas.risk import RiskCheckResult
from app.schemas.strategy import StrategySignal
from app.schemas.tools import ToolInput, ToolOutput
from app.schemas.usage import UsageEvent


class MessageClass(StrEnum):
    """Surface class of the inbound message.

    ``COMMAND`` is tombstoned. The IntentDecision migration must not emit it;
    routing uses ``(intent, requested_action)`` instead.
    """

    QUESTION = "question"
    COMMAND = "command"  # tombstone — classifier must never emit this
    ANALYSIS_REQUEST = "analysis_request"
    APPROVAL_RESPONSE = "approval_response"
    JOURNAL_ENTRY = "journal_entry"
    SMALLTALK = "smalltalk"
    UNKNOWN = "unknown"


class Intent(StrEnum):
    """Canonical and legacy intents.

    New routing keys (architecture §4): MARKET_ANALYSIS, SETUP_ANALYSIS, APPROVE,
    REJECT, SKIP, EXECUTE_PAPER_PLAN. Legacy ``EXECUTE`` / ``MONITOR`` remain as
    enum members so stored rows parse, but the classifier never emits them.
    """

    MARKET_ANALYSIS = "market_analysis"
    SETUP_ANALYSIS = "setup_analysis"
    PLAN_TRADE = "plan_trade"
    PREPARE_PAPER_TRADE = "prepare_paper_trade"
    CONFIRM_PAPER_EXECUTION = "confirm_paper_execution"
    REVIEW_TRADE = "review_trade"
    MANAGE_POSITION = "manage_position"
    JOURNAL = "journal"
    EXPLAIN = "explain"
    CONFIGURE = "configure"
    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"
    EXECUTE_PAPER_PLAN = "execute_paper_plan"
    # Legacy aliases — classifier must not emit these.
    MONITOR = "monitor"
    EXECUTE = "execute"
    REVIEW = "review"
    UPDATE_RULE = "update_rule"
    STRATEGY_CARD = "strategy_card"
    PRE_TRADE = "pre_trade"
    POSITION_SIZE = "position_size"
    INVALIDATION_QUERY = "invalidation_query"
    LOSS_ACCEPTANCE = "loss_acceptance"
    HUMAN_VS_SYSTEM = "human_vs_system"
    MANUAL_LEVELS = "manual_levels"
    STRATEGY_STATUS = "strategy_status"
    BACKTEST_QUEUE = "backtest_queue"
    BACKTEST_RUN = "backtest_run"
    BACKTEST_RESULTS = "backtest_results"
    BACKTEST_ELIGIBILITY = "backtest_eligibility"
    EARLY_EXIT_QUERY = "early_exit_query"
    STOP_DISCIPLINE_QUERY = "stop_discipline_query"
    STRATEGY_TESTABILITY = "strategy_testability"
    STRUCTURE_STRATEGY = "structure_strategy"
    LESSON_PENDING_QUERY = "lesson_pending_query"
    LESSON_ACCEPTED_QUERY = "lesson_accepted_query"
    LESSON_ACCEPT = "lesson_accept"
    LESSON_REJECT = "lesson_reject"
    LESSON_RULE_SUGGEST = "lesson_rule_suggest"
    ADD_RUNNER_RULE = "add_runner_rule"
    PAPER_ELIGIBILITY_BLOCKERS = "paper_eligibility_blockers"
    LESSON_STRATEGY_UPDATE = "lesson_strategy_update"
    LESSON_CREATE_VERSION = "lesson_create_version"
    LESSON_STRATEGY_LINKED = "lesson_strategy_linked"
    LESSON_UNRESOLVED_BLOCKERS = "lesson_unresolved_blockers"
    BACKTEST_PREP = "backtest_prep"
    PAPER_VALIDATION_START = "paper_validation_start"
    PAPER_VALIDATION_SCAN = "paper_validation_scan"
    STRATEGY_PROPOSAL_CONFIRM = "strategy_proposal_confirm"
    STRATEGY_PROPOSAL_REJECT = "strategy_proposal_reject"
    STRATEGY_DISCUSSION = "strategy_discussion"
    PAPER_VALIDATION_QUERY = "paper_validation_query"
    PAPER_VALIDATION_RECOMMEND = "paper_validation_recommend"
    PAPER_SCHEDULER_QUERY = "paper_scheduler_query"
    PAPER_ALERTS_QUERY = "paper_alerts_query"
    ALERT_DELIVERY_QUERY = "alert_delivery_query"
    MARKET_WATCHER_QUERY = "market_watcher_query"
    MARKET_WATCHER_BRIDGE_QUERY = "market_watcher_bridge_query"
    UNKNOWN = "unknown"


class OperationClass(StrEnum):
    READ_ONLY = "read_only"
    PLAN = "plan"
    MUTATION = "mutation"
    APPROVAL = "approval"
    EXECUTION = "execution"
    CONFIGURATION = "configuration"
    JOURNAL = "journal"


class AgentChannel(StrEnum):
    WEB = "web"
    API = "api"
    TELEGRAM = "telegram"
    WORKER = "worker"


class ClassifierSource(StrEnum):
    DETERMINISTIC = "deterministic"
    TIER_B = "tier_b"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class RequestedAction(StrEnum):
    """Exact requested side-effect. Authorization issuance requires APPROVE."""

    NONE = "none"
    CLARIFY = "clarify"
    APPROVE = "approve"
    REJECT = "reject"
    SKIP = "skip"
    CREATE_PLAN = "create_plan"
    EXECUTE_PAPER_PLAN = "execute_paper_plan"
    PREVIEW = "preview"
    CONFIRM_WRITE = "confirm_write"


class MemoryClass(StrEnum):
    """Only NON_DOMAIN_MEMORY may persist under READ_ONLY."""

    NON_DOMAIN_MEMORY = "non_domain_memory"
    DOMAIN_MEMORY = "domain_memory"


class PrincipalRef(BaseModel):
    """Typed user/account principal bound to one IntentDecision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID | None = None
    account_id: UUID | None = None


class ExtractedParameters(BaseModel):
    """Bounded extracted parameters. Extra fields are forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str | None = None
    timeframe: str | None = None
    plan_id: UUID | None = None
    revision_id: UUID | None = None
    target_id: UUID | None = None


class IntentDecision(BaseModel):
    """Immutable per-request routing contract (architecture §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: Intent
    operation_class: OperationClass
    organization_id: UUID | None = None
    principal: PrincipalRef = Field(default_factory=PrincipalRef)
    channel: AgentChannel = AgentChannel.WEB
    target_type: str | None = None
    target_id: UUID | None = None
    target_revision_id: UUID | None = None
    target_content_hash: str | None = None
    requested_action: RequestedAction = RequestedAction.NONE
    extracted_parameters: ExtractedParameters = Field(default_factory=ExtractedParameters)
    explicit_confirmation: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ambiguity_reasons: tuple[str, ...] = ()
    requires_clarification: bool = False
    classifier_source: ClassifierSource = ClassifierSource.DETERMINISTIC

    @property
    def is_read_only(self) -> bool:
        return self.operation_class is OperationClass.READ_ONLY

    @property
    def may_issue_authorization(self) -> bool:
        """Only exact APPROVE may issue authorization. REJECT/SKIP never authorize."""
        return (
            self.intent is Intent.APPROVE
            and self.requested_action is RequestedAction.APPROVE
            and self.operation_class is OperationClass.APPROVAL
            and not self.requires_clarification
        )


class ConversationTurn(BaseModel):
    """Prior transcript turn injected into graph state. Not domain memory."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class AgentState(BaseModel):
    """Mutable workflow state passed between graph nodes."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # Identity / correlation
    request_id: str
    user_id: UUID | None = None
    organization_id: UUID | None = None
    conversation_id: UUID | None = None
    bound_strategy_id: UUID | None = None
    pending_proposal_id: UUID | None = None
    conversation_history: list[ConversationTurn] = Field(default_factory=list)

    # Request context
    symbol: Symbol | None = None
    timeframe: Timeframe | None = None
    message: str = ""

    # Classification
    message_class: MessageClass = MessageClass.UNKNOWN
    intent: Intent = Intent.UNKNOWN
    intent_decision: IntentDecision | None = None
    requires_clarification: bool = False
    safety_verdict: SafetyVerdict | None = None

    # Gathered context
    market_context: MarketSnapshot | None = None
    indicator_context: IndicatorContext | None = None
    market_data_quality: str | None = Field(
        default=None, description="live | mock | stale | missing"
    )
    market_data_gaps: list[str] = Field(
        default_factory=list,
        description="Market data fields unavailable for this turn (e.g. funding during a ban)",
    )
    strategy_signals: list[StrategySignal] = Field(default_factory=list)
    retrieved_context: list[Citation] = Field(default_factory=list)

    # Tooling
    tool_calls: list[ToolInput] = Field(default_factory=list)
    tool_outputs: list[ToolOutput] = Field(default_factory=list)

    # Proposal + risk
    trade_proposal: TradeProposal | None = None
    risk_result: RiskCheckResult | None = None
    risk_level: RiskSeverity | None = None
    confidence: Confidence | None = None

    # Approval
    approval_required: bool = False
    approval_reason: str | None = None
    proposal_id: UUID | None = None
    approval_id: UUID | None = None

    # Output
    citations: list[Citation] = Field(default_factory=list)
    paper_execution: AgentPaperResult | None = None
    final_answer: str | None = None
    analysis_detail: TradingAnalysisDetail | None = None
    narrative_detail: TradingNarrativeDetail | None = None
    narrative_metadata: NarrativeMetadata | None = None

    # Observability
    usage_metadata: UsageEvent | None = None
    audit_events: list[AuditEvent] = Field(default_factory=list)
