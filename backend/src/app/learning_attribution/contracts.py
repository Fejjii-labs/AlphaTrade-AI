"""Immutable contracts for Phase 7 learning attribution.

Facts are copied from canonical SetupAssessment, Candidate, TradePlan lineage,
journal projection, and JournalTrade. This package does not evaluate setups,
create candidates, authorize plans, or dispatch execution.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.market_contracts.models import CanonicalDecimal, CanonicalModel
from app.schemas.common import (
    JournalLifecycleEventType,
    JournalTradeStatus,
    StrictModel,
    TradeResult,
)
from app.schemas.journal_lifecycle import JournalLifecycleEventInput, JournalProjectionResult
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import CandidateState, SetupAssessmentState
from app.signal_fusion.types import Sha256Hex

ATTRIBUTION_SCHEMA: Literal["LearningAttribution/v2"] = "LearningAttribution/v2"
NARRATIVE_NOT_FACT_BANNER = "NARRATIVE_NOT_FACT — LLM wording cannot rewrite deterministic facts"
EARLY_EXIT_CAPTURE_PCT = Decimal("50")
ENTRY_MATCH_BPS = Decimal("10")
SIZE_MATCH_BPS = Decimal("100")


class DecisionActor(StrEnum):
    """Who produced the lifecycle event. Not a second execution actor system."""

    SYSTEM_SETUP = "system_setup"
    HUMAN_DECISION = "human_decision"
    HUMAN_APPROVAL = "human_approval"
    AUTONOMOUS_APPROVAL = "autonomous_approval"
    PAPER_SYSTEM_EXECUTION = "paper_system_execution"


class PlannedSetupQuality(StrEnum):
    """Setup-truth quality at confirmation time. Outcome cannot change this."""

    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class ExecutionQuality(StrEnum):
    """Fill/close quality versus the approved plan. N/A until a JournalTrade exists."""

    NOT_APPLICABLE = "not_applicable"
    NOT_YET_EXECUTED = "not_yet_executed"
    INCOMPLETE_VENUE_FACTS = "incomplete_venue_facts"
    MATCHED_PLAN = "matched_plan"
    SLIPPAGE_DEVIATION = "slippage_deviation"
    EARLY_EXIT = "early_exit"
    STOP_DEVIATION = "stop_deviation"


class TraderBehavior(StrEnum):
    """Trader/system decision relative to the candidate. Independent of setup truth."""

    AWAITING_DECISION = "awaiting_decision"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    APPROVED_PLAN = "approved_plan"
    AUTONOMOUS_APPROVAL = "autonomous_approval"
    EXECUTED = "executed"
    RECONCILED = "reconciled"


class RiskAdherence(StrEnum):
    """Plan-risk adherence. Independent of setup quality, execution fill quality, and PnL."""

    NOT_APPLICABLE = "not_applicable"
    NOT_YET_ASSESSED = "not_yet_assessed"
    INCOMPLETE_FACTS = "incomplete_facts"
    ADHERED = "adhered"
    STOP_VIOLATION = "stop_violation"
    SIZE_OR_RISK_VIOLATION = "size_or_risk_violation"


class LearningVenueMode(StrEnum):
    """Cohort for learning stats. Live/real execution is not a valid learning venue."""

    PAPER_INTERNAL = "paper_internal"
    PAPER_EXCHANGE_DEMO = "paper_exchange_demo"


class TradePlanLineageRef(CanonicalModel):
    """Identity-only TradePlan pointer. Not a second TradePlan implementation."""

    revision_id: UUID
    content_hash: Sha256Hex
    candidate_id: UUID
    organization_id: UUID
    account_id: UUID
    strategy_version_id: UUID
    setup_definition_id: UUID


class LineageSnapshot(CanonicalModel):
    """Frozen lineage the attribution layer may read but never rewrite."""

    assessment: SetupAssessment
    candidate: Candidate
    account_id: UUID
    execution_lifecycle_id: UUID | None = None
    trade_plan: TradePlanLineageRef | None = None


class AttributionCommand(StrictModel):
    """One learning application against an existing journal lifecycle event."""

    organization_id: UUID
    user_id: UUID
    actor_user_id: UUID | None = None
    event: JournalLifecycleEventInput
    lineage: LineageSnapshot
    learning_venue_mode: LearningVenueMode = LearningVenueMode.PAPER_INTERNAL
    narrative_explanation: str | None = Field(default=None, max_length=4000)


class PlannedSetupQualityFacts(CanonicalModel):
    """Copied SetupAssessment facts. Learning must not mutate these hashes."""

    axis: PlannedSetupQuality
    assessment_id: UUID
    assessment_state: SetupAssessmentState
    assessment_content_hash: Sha256Hex
    evidence_window_hash: Sha256Hex
    reason_codes: tuple[str, ...]
    confirmed: bool


class ExecutionQualityFacts(CanonicalModel):
    axis: ExecutionQuality
    journal_trade_id: UUID | None = None
    journal_status: JournalTradeStatus | None = None
    planned_entry_price: CanonicalDecimal | None = None
    actual_entry_price: CanonicalDecimal | None = None
    entry_deviation_bps: CanonicalDecimal | None = None
    planned_stop_price: CanonicalDecimal | None = None
    actual_exit_price: CanonicalDecimal | None = None
    recorded_slippage: CanonicalDecimal | None = None
    capture_pct: CanonicalDecimal | None = None


class TraderBehaviorFacts(CanonicalModel):
    axis: TraderBehavior
    actor: DecisionActor
    event_type: JournalLifecycleEventType
    candidate_state: CandidateState
    executed_trade_outcome: bool


class RiskAdherenceFacts(CanonicalModel):
    """Copied plan-risk facts. Outcome PnL cannot change this axis."""

    axis: RiskAdherence
    journal_trade_id: UUID | None = None
    planned_risk_amount: CanonicalDecimal | None = None
    planned_stop_price: CanonicalDecimal | None = None
    actual_exit_price: CanonicalDecimal | None = None
    planned_size: CanonicalDecimal | None = None
    actual_size: CanonicalDecimal | None = None
    size_deviation_bps: CanonicalDecimal | None = None


class TradeOutcomeFacts(CanonicalModel):
    """Executed outcome copied from JournalTrade. None when REJECT/SKIP."""

    eligible: bool
    journal_trade_id: UUID | None = None
    status: JournalTradeStatus | None = None
    result: TradeResult | None = None
    net_pnl: CanonicalDecimal | None = None
    gross_pnl: CanonicalDecimal | None = None


class HumanVsSystemAttributionFacts(CanonicalModel):
    """Structured comparison facts for later human-versus-system analytics."""

    decision_actor: DecisionActor
    setup_quality_axis: PlannedSetupQuality
    execution_quality_axis: ExecutionQuality
    risk_adherence_axis: RiskAdherence
    trader_behavior_axis: TraderBehavior
    planned_entry_price: CanonicalDecimal | None = None
    actual_entry_price: CanonicalDecimal | None = None
    entry_deviation_bps: CanonicalDecimal | None = None
    planned_stop_price: CanonicalDecimal | None = None
    actual_exit_price: CanonicalDecimal | None = None
    net_pnl: CanonicalDecimal | None = None
    result: TradeResult | None = None
    executed_trade_outcome: bool
    journal_trade_id: UUID | None = None
    candidate_id: UUID
    strategy_version_id: UUID
    setup_definition_id: UUID


class StrategyPatternStatFacts(CanonicalModel):
    """Increment-style facts for later strategy/pattern performance rollups."""

    strategy_version_id: UUID
    setup_definition_id: UUID
    fusion_policy_version: str = Field(min_length=3, max_length=120)
    uniqueness_tuple_hash: Sha256Hex
    learning_venue_mode: LearningVenueMode = LearningVenueMode.PAPER_INTERNAL
    candidate_confirmed: bool
    rejected: bool
    skipped: bool
    plan_approved: bool
    autonomous_approval: bool = False
    filled: bool
    closed: bool
    executed_outcome: bool
    win: bool
    loss: bool
    breakeven: bool


class AttributionFacts(CanonicalModel):
    """Deterministic facts. Narrative is excluded from the content hash."""

    schema_version: Literal["LearningAttribution/v2"] = ATTRIBUTION_SCHEMA
    attribution_id: UUID
    organization_id: UUID
    account_id: UUID
    user_id: UUID
    candidate_id: UUID
    candidate_content_hash: Sha256Hex
    uniqueness_tuple_hash: Sha256Hex
    execution_lifecycle_id: UUID | None
    journal_trade_id: UUID | None
    assessment_id: UUID
    evidence_window_hash: Sha256Hex
    trade_plan_revision_id: UUID | None
    learning_venue_mode: LearningVenueMode = LearningVenueMode.PAPER_INTERNAL
    setup_quality: PlannedSetupQualityFacts
    execution_quality: ExecutionQualityFacts
    risk_adherence: RiskAdherenceFacts
    trader_behavior: TraderBehaviorFacts
    outcome: TradeOutcomeFacts
    human_vs_system: HumanVsSystemAttributionFacts
    strategy_pattern: StrategyPatternStatFacts
    projection_replayed: bool
    projection_skipped_reason: str | None = None
    content_hash: Sha256Hex


class AttributionEvent(CanonicalModel):
    """Append-only attribution evidence. Duplicate source identity converges."""

    event_type: JournalLifecycleEventType
    source_system: str
    source_aggregate: str
    source_event_id: str
    source_event_version: int
    supersession: int
    event_content_hash: Sha256Hex
    facts_hash: Sha256Hex
    journal_trade_id: UUID | None
    projection: JournalProjectionResult
    narrative_explanation: str | None = None


class AttributionRecord(CanonicalModel):
    """One candidate-scoped attribution aggregate. Not a JournalTrade writer."""

    attribution_id: UUID
    organization_id: UUID
    candidate_id: UUID
    execution_lifecycle_id: UUID | None
    journal_trade_id: UUID | None
    facts: AttributionFacts
    events: tuple[AttributionEvent, ...]
    narrative_explanation: str | None = None


class AttributionResult(CanonicalModel):
    record: AttributionRecord
    created: bool
    replayed: bool
    journal_trade_id: UUID | None
    executed_trade_outcome: bool
    facts_hash: Sha256Hex


class LessonSuggestionFact(CanonicalModel):
    """Suggestion only. Adapters must not auto-persist lesson candidates."""

    category: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=2000)
    mistake_type: str = Field(min_length=1, max_length=60)
    severity: str = Field(min_length=1, max_length=16)
    executed_trade_outcome: bool
    persist: Literal[False] = False


class DurableColumnSpec(CanonicalModel):
    """Exact later-Alembic column. This wave does not emit a migration."""

    name: str = Field(min_length=1, max_length=80)
    sql_type: str = Field(min_length=1, max_length=80)
    nullable: bool
    purpose: str = Field(min_length=1, max_length=400)


class DurableAttributionIntegrationRequirement(CanonicalModel):
    """Agent 1 integration contract for later PostgreSQL binding."""

    owner: Literal["agent_1_alembic_orm"] = "agent_1_alembic_orm"
    proposed_table: Literal["learning_attribution_records"] = "learning_attribution_records"
    proposed_event_table: Literal["learning_attribution_events"] = "learning_attribution_events"
    candidate_uniqueness: tuple[str, ...] = ("organization_id", "candidate_id")
    lifecycle_uniqueness: tuple[str, ...] = ("organization_id", "execution_lifecycle_id")
    record_columns: tuple[DurableColumnSpec, ...]
    event_columns: tuple[DurableColumnSpec, ...]
    optional_journal_trade_columns: tuple[DurableColumnSpec, ...]
    reuse_now: str = Field(min_length=1, max_length=800)
    must_not: str = Field(min_length=1, max_length=800)
