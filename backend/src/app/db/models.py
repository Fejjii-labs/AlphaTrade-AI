"""SQLAlchemy 2.0 typed ORM models for all core trading entities.

PostgreSQL is the production target; SQLite is used only for tests. Types are
chosen to be portable across both: ``Uuid`` for ids, ``Numeric(20, 8)`` for
monetary/size values, ``JSON`` for small structured lists, and non-native
``Enum`` (VARCHAR + CHECK) reusing the exact enums defined in
:mod:`app.schemas.common` to prevent string drift between API and database.

Tenant-scoped resources carry ``organization_id``; vectors live in Qdrant while
document/chunk metadata lives here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.blofin_activity import (  # noqa: F401
    BloFinActivityAccount,
    BloFinActivityCursor,
    BloFinActivityFact,
)
from app.db.canonical_candidates import (  # noqa: F401
    CanonicalCandidateCreationKeyRow,
    CanonicalCandidateRow,
    CanonicalCandidateTransitionKeyRow,
    CanonicalCandidateTransitionRow,
)
from app.db.canonical_candidates import (
    register_canonical_candidate_immutability as _register_canonical_candidate_immutability,
)
from app.db.canonical_eligibility import (  # noqa: F401
    ActionEligibilityEvaluationRow,
    ActionEligibilityIdentityBindingRow,
)
from app.db.canonical_eligibility import (
    register_canonical_eligibility_immutability as _register_canonical_eligibility_immutability,
)
from app.db.canonical_trade_plans import (  # noqa: F401
    PLAN_AUTHORITY_PAPER_VALIDATION,
    PLAN_ROOT_ANALYSIS,
    CanonicalTradePlanIdempotencyKeyRow,
    CanonicalTradePlanLineageRow,
    CanonicalTradePlanRootRow,
)
from app.db.canonical_trade_plans import (
    register_canonical_trade_plan_immutability as _register_canonical_trade_plan_immutability,
)
from app.db.historical_immutability import install_historical_immutability as _install_history
from app.db.journal_immutability import (
    register_journal_immutability as _register_journal_immutability,
)
from app.db.learning_attribution import (  # noqa: F401
    LearningAttributionEventRow,
    LearningAttributionRecordRow,
)
from app.db.learning_attribution import (
    register_learning_attribution_immutability as _register_learning_attribution_immutability,
)
from app.db.paper_evaluation import PaperEvaluationObservationRow  # noqa: F401
from app.db.paper_evaluation import (
    register_paper_evaluation_immutability as _register_paper_evaluation_immutability,
)
from app.db.public_market_observations import PublicMarketObservationRow  # noqa: F401
from app.db.runtime_status import ControlledRuntimeStatusRow  # noqa: F401
from app.db.setup_lifetime import SetupLifetimePin  # noqa: F401
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow  # noqa: F401
from app.db.strategy_immutability import (
    register_strategy_immutability as _register_strategy_immutability,
)
from app.db.telegram_activation import (  # noqa: F401
    TelegramActivationCursorRow,
    TelegramActivationSendLedgerRow,
)
from app.db.telegram_security import (  # noqa: F401
    TelegramActionNonceRow,
    TelegramActionReceiptRow,
    TelegramAuthorizationIntentRow,
    TelegramBindingRow,
    TelegramEnrollmentChallengeRow,
    TelegramOutboxRow,
    TelegramProtocolAuditEventRow,
)
from app.db.voice_realtime import VoiceSessionReservation  # noqa: F401
from app.db.watcher_assignments import (  # noqa: F401
    WatcherAssignmentReceiptRow,
    WatcherStrategyAssignmentRow,
)
from app.db.watcher_orchestration import (  # noqa: F401
    WatcherHealthSnapshotRow,
    WatcherHeartbeatRow,
    WatcherObservabilityEventRow,
    WatcherPolicyVersionRow,
    WatcherScanAttemptRow,
    WatcherScanLineageRow,
    WatcherScheduledScanRow,
    WatcherSourceFetchAttemptRow,
    WatcherSubscriptionEvalAttemptRow,
    WatcherWorkerLeaseRow,
)
from app.db.watcher_watchlist import WatcherSymbolStatusRow, WatcherWatchlistRow  # noqa: F401
from app.experiments import guards as _experiment_guards  # noqa: F401
from app.experiments.models import (  # noqa: F401
    ExperimentEventRow,
    ExperimentRow,
    ExperimentSampleRow,
    ExperimentVersionRow,
)
from app.schemas.common import (
    ActorType,
    AlertDeliveryChannel,
    AlertDeliveryStatus,
    ApprovalAction,
    ApprovalStatus,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    BacktestRunStatus,
    BacktestStatus,
    BloFinSyncHealthStatus,
    ConversationMessageRole,
    ConversationStatus,
    CostSource,
    DocumentSourceType,
    ExchangeAccountStatus,
    ExecutionMode,
    JournalEntryMethod,
    JournalEvidenceKind,
    JournalImportBatchStatus,
    JournalLifecycleEventType,
    JournalObservationCategory,
    JournalTradeSource,
    JournalTradeStatus,
    LossAcceptanceStatus,
    ManualLevelType,
    MarketRegime,
    MarketWatcherBridgeDecisionType,
    MarketWatcherObservationStatus,
    MembershipRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperAlertSeverity,
    PaperAlertType,
    PaperObservabilityEventType,
    PaperRuntimeCycleMode,
    PaperRuntimeCycleStatus,
    PaperSignalOrchestrationMode,
    PaperSignalOrchestrationStatus,
    PaperSignalStatus,
    PaperTradeStatus,
    PaperValidationRuntimeMode,
    PaperValidationStatus,
    PositionStatus,
    ProposalStatus,
    RiskAction,
    RiskProfile,
    RiskRuleId,
    RiskSeverity,
    RuleComplianceStatus,
    SetupCategory,
    SetupCompileStatus,
    StrategyChangeSource,
    StrategyId,
    StrategyLifecycleState,
    StrategyProposalStatus,
    StrategyValidationStatus,
    TradeDirection,
    TradeResult,
    TradingViewSignalStatus,
    UsageStatus,
    UserRole,
)
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    ExecutionReceiptState,
    ExecutionReconciliationStatus,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
    VenueSubmitEffectState,
)
from app.schemas.model_routing import (
    ModelFailureCategory,
    ModelFallbackPolicy,
    ModelResourceType,
    ModelRetentionCategory,
    ModelRoutingPurpose,
    ModelRoutingTier,
)
from app.schemas.trade_plan import (
    AccountMode,
    AuthorizationChannel,
    AuthorizationState,
    PlanOperation,
)
from app.schemas.trade_plan import (
    ExecutionMode as PlanExecutionMode,
)
from app.strategy_brain.trendpulse_screening.models import TrendPulseScreeningRow  # noqa: F401

_MONEY = Numeric(20, 8)
_ENUM_LEN = 40


def _enum(enum_cls: type) -> Enum:
    """Portable, non-native enum column (VARCHAR + CHECK)."""
    return Enum(enum_cls, native_enum=False, length=_ENUM_LEN, validate_strings=True)


# --------------------------------------------------------------------------- #
# Identity & tenancy
# --------------------------------------------------------------------------- #


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), default=UserRole.TRADER)
    risk_profile: Mapped[RiskProfile] = mapped_column(
        _enum(RiskProfile), default=RiskProfile.MODERATE
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        _enum(MembershipRole), default=MembershipRole.TRADER
    )


class EmailVerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time email verification token (hashed at rest)."""

    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time password reset token (hashed at rest)."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrganizationInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Organization membership invitation (token hashed at rest)."""

    __tablename__ = "organization_invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(_enum(MembershipRole), nullable=False)
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rotating refresh token store (hashed at rest)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id"), nullable=True
    )


class ExchangeAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "exchange_accounts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            "user_id",
            name="uq_exchange_account_tenant_owner",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    api_key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    has_withdrawal_permission: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ExchangeAccountStatus] = mapped_column(
        _enum(ExchangeAccountStatus), default=ExchangeAccountStatus.ACTIVE
    )


class ExecutionAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Internal paper account identity used to scope plans and authorizations."""

    __tablename__ = "execution_accounts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            "user_id",
            name="uq_execution_account_tenant_owner",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_execution_account_org_identity",
        ),
        CheckConstraint("execution_mode = 'PAPER'", name="ck_execution_account_paper"),
        CheckConstraint("account_mode = 'NET'", name="ck_execution_account_net"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    execution_mode: Mapped[PlanExecutionMode] = mapped_column(
        _enum(PlanExecutionMode), default=PlanExecutionMode.PAPER, nullable=False
    )
    account_mode: Mapped[AccountMode] = mapped_column(
        _enum(AccountMode), default=AccountMode.NET, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --------------------------------------------------------------------------- #
# Market data (global, not tenant-scoped)
# --------------------------------------------------------------------------- #


class MarketSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "market_snapshots"

    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    high: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    low: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    close: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    volume: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    funding_rate: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IndicatorSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "indicator_snapshots"

    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    rsi: Mapped[float | None] = mapped_column(nullable=True)
    vwap: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    ema_fast: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    ema_slow: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    macd: Mapped[float | None] = mapped_column(nullable=True)
    macd_signal: Mapped[float | None] = mapped_column(nullable=True)
    atr: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    volatility: Mapped[float | None] = mapped_column(nullable=True)
    funding_rate: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Setups, signals & analytics
# --------------------------------------------------------------------------- #


class SetupDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "setup_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_setup_name_version"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy_id: Mapped[StrategyId] = mapped_column(_enum(StrategyId), nullable=False)
    category: Mapped[SetupCategory] = mapped_column(_enum(SetupCategory), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    filters: Mapped[list] = mapped_column(JSON, default=list)


class GlobalSetupTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Compatibility alias over a legacy global SetupDefinition. Never tenant-owned."""

    __tablename__ = "global_setup_templates"
    __table_args__ = (
        UniqueConstraint("setup_definition_id", name="uq_global_setup_template_source"),
        CheckConstraint("organization_id IS NULL", name="ck_global_setup_template_no_org"),
    )

    setup_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("setup_definitions.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_id: Mapped[StrategyId] = mapped_column(_enum(StrategyId), nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    collision_status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SetupPerformance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "setup_performance"

    setup_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("setup_definitions.id"), unique=True, nullable=False
    )
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(default=0.0)
    expectancy: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    avg_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    avg_stress: Mapped[float | None] = mapped_column(nullable=True)


class StrategySignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_signals"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    setup_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("setup_definitions.id"), nullable=True
    )
    strategy_id: Mapped[StrategyId] = mapped_column(_enum(StrategyId), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    entry_low: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    entry_high: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    invalidation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    risk_notes: Mapped[list] = mapped_column(JSON, default=list)
    signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Proposals, approvals, execution & positions
# --------------------------------------------------------------------------- #


class TradeProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trade_proposals"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "organization_id",
            "user_id",
            name="uq_trade_proposal_tenant_owner",
        ),
        CheckConstraint(
            "plan_root_kind IN ('analysis_proposal', 'canonical_plan_root', 'manual_demo_test')",
            name="ck_trade_proposals_plan_root_kind",
        ),
        CheckConstraint(
            "(plan_root_kind = 'manual_demo_test' AND strategy_id IS NULL "
            "AND signal_id IS NULL AND user_strategy_id IS NULL) OR "
            "(plan_root_kind <> 'manual_demo_test' AND strategy_id IS NOT NULL)",
            name="ck_trade_proposals_manual_origin",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_signals.id"), nullable=True
    )
    strategy_id: Mapped[StrategyId | None] = mapped_column(_enum(StrategyId), nullable=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    position_size: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    leverage: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    take_profits: Mapped[list] = mapped_column(JSON, default=list)
    breakeven_trigger: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    runner_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    runner_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    risk_level: Mapped[RiskSeverity] = mapped_column(_enum(RiskSeverity), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        _enum(ProposalStatus), default=ProposalStatus.DRAFT
    )
    entry_low: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    entry_high: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    planned_loss_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    loss_acceptance_required: Mapped[bool] = mapped_column(Boolean, default=False)
    loss_acceptance_status: Mapped[LossAcceptanceStatus] = mapped_column(
        _enum(LossAcceptanceStatus), default=LossAcceptanceStatus.NOT_REQUIRED
    )
    actual_loss_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    latest_plan_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "trade_plan_revisions.id",
            name="fk_trade_proposals_latest_plan_revision",
            use_alter=True,
        ),
        nullable=True,
    )
    plan_root_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PLAN_ROOT_ANALYSIS
    )


class UserStrategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped strategy library entry (Slice 33)."""

    __tablename__ = "user_strategies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "name",
            name="uq_user_strategy_org_user_name",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    setup_type: Mapped[StrategyId] = mapped_column(_enum(StrategyId), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_eligible: Mapped[bool] = mapped_column(Boolean, default=False)


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped chat transcript. Not a strategy or Candidate authority."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_org_user_updated", "organization_id", "user_id", "updated_at"),
        Index("ix_conversations_org_strategy", "organization_id", "strategy_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus), default=ConversationStatus.ACTIVE, nullable=False
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One persisted chat turn. Payload is redacted metadata, never secrets."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index("ix_conversation_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_conversation_messages_org_user", "organization_id", "user_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[ConversationMessageRole] = mapped_column(
        _enum(ConversationMessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class StrategyConversationProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Preview structured-rules draft. Confirmation is required to fork a version."""

    __tablename__ = "strategy_conversation_proposals"
    __table_args__ = (
        Index(
            "ix_strategy_proposals_org_conversation",
            "organization_id",
            "conversation_id",
            "created_at",
        ),
        CheckConstraint(
            "resulting_content_hash IS NULL OR length(resulting_content_hash) = 64",
            name="ck_strategy_proposal_result_hash",
        ),
        CheckConstraint(
            "content_hash IS NULL OR length(content_hash) = 64",
            name="ck_strategy_proposal_content_hash",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=True
    )
    target_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    status: Mapped[StrategyProposalStatus] = mapped_column(
        _enum(StrategyProposalStatus), default=StrategyProposalStatus.DRAFT, nullable=False
    )
    proposed_structured_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_pattern_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_card: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    challenge_notes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_refs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resulting_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    resulting_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    resulting_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StrategyVersionConversationLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provenance: conversation proposal → immutable strategy version."""

    __tablename__ = "strategy_version_conversation_links"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", name="uq_strategy_version_conversation_link"),
        UniqueConstraint("proposal_id", name="uq_strategy_proposal_version_link"),
        Index("ix_strategy_version_links_org_conversation", "organization_id", "conversation_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id"), nullable=False
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_conversation_proposals.id"), nullable=False
    )
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id"), nullable=True
    )


class UserStrategyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned strategy card content."""

    __tablename__ = "user_strategy_versions"
    __table_args__ = (
        UniqueConstraint(
            "strategy_id",
            "version",
            name="uq_user_strategy_version",
        ),
        CheckConstraint("length(content_hash) = 64", name="ck_user_strategy_version_hash"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    card: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation_status: Mapped[StrategyValidationStatus] = mapped_column(
        _enum(StrategyValidationStatus), default=StrategyValidationStatus.DRAFT
    )
    backtest_status: Mapped[BacktestStatus] = mapped_column(
        _enum(BacktestStatus), default=BacktestStatus.NOT_RUN
    )
    paper_validation_status: Mapped[PaperValidationStatus] = mapped_column(
        _enum(PaperValidationStatus), default=PaperValidationStatus.NOT_STARTED
    )
    structured_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lesson_source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_source: Mapped[StrategyChangeSource] = mapped_column(
        _enum(StrategyChangeSource), default=StrategyChangeSource.CREATE, nullable=False
    )
    content_diff: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CompiledSetupDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-owned immutable compiler artifact for exactly one strategy version."""

    __tablename__ = "compiled_setup_definitions"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", name="uq_compiled_setup_strategy_version"),
        CheckConstraint("length(content_hash) = 64", name="ck_compiled_setup_hash_length"),
        CheckConstraint("length(compiler_version) > 0", name="ck_compiled_setup_compiler"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=False
    )
    compiler_version: Mapped[str] = mapped_column(String(80), nullable=False)
    grammar_version: Mapped[str] = mapped_column(String(80), nullable=False)
    compiled_ast: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compile_status: Mapped[SetupCompileStatus] = mapped_column(
        _enum(SetupCompileStatus), default=SetupCompileStatus.EXECUTABLE, nullable=False
    )


class StrategyLifecycleEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only lifecycle history. Policy identity stays on the strategy version."""

    __tablename__ = "strategy_lifecycle_events"
    __table_args__ = (
        CheckConstraint("length(event_hash) = 64", name="ck_strategy_lifecycle_hash"),
        Index(
            "ix_strategy_lifecycle_org_strategy",
            "organization_id",
            "strategy_id",
            "occurred_at",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=False
    )
    prior_state: Mapped[StrategyLifecycleState | None] = mapped_column(
        _enum(StrategyLifecycleState), nullable=True
    )
    new_state: Mapped[StrategyLifecycleState] = mapped_column(
        _enum(StrategyLifecycleState), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SetupMigrationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Dry-run or apply report for global-template / compiled-setup migration."""

    __tablename__ = "setup_migration_runs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class LessonCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Discipline lesson awaiting review — not auto-promoted to permanent rules."""

    __tablename__ = "lesson_candidates"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), default="journal", nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    related_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journals.id"), nullable=True
    )
    related_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journals.id"), nullable=True
    )
    lesson_text: Mapped[str] = mapped_column(Text, nullable=False)
    mistake_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending_review", nullable=False)
    proposed_rule_update: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    accepted_rule_update: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class HistoricalCandle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted OHLCV bar for backtest replay (global market data — not tenant-scoped)."""

    __tablename__ = "historical_candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "exchange",
            "timeframe",
            "open_time",
            name="uq_historical_candle",
        ),
    )

    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    high: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    low: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    close: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    volume: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    freshness_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BacktestDataset(UUIDPrimaryKeyMixin, Base):
    """Immutable candle-window snapshot for deterministic backtests (AT-034).

    Global (not tenant-scoped) — candles are shared market data. Rows are never
    updated after insert; reuse is by ``dataset_hash`` match only.
    """

    __tablename__ = "backtest_datasets"

    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    start_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    candle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    stale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class BacktestRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Backtest run record (Slice 35 / AT-034 — deterministic simulation, paper only)."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index(
            "uq_backtest_runs_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            sqlite_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[BacktestRunStatus] = mapped_column(
        _enum(BacktestRunStatus), default=BacktestRunStatus.NOT_STARTED
    )
    assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AT-034 — deterministic engine v2 metadata (nullable for pre-v2 rows)
    config_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_datasets.id"), nullable=True
    )
    engine_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_bars: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BacktestTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Simulated trade from a backtest run."""

    __tablename__ = "backtest_trades"

    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=False
    )
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    size: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    fees: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    slippage_cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    tp_hit_status: Mapped[str] = mapped_column(String(40), nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(60), nullable=False)
    rule_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AT-034 — excursion / funding / walk-forward labels (nullable for pre-v2 rows)
    mfe_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mae_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mfe_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mae_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    available_profit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    capture_pct: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    funding_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    split_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    split_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PaperValidationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Paper validation run with metrics (Slice 35 — paper only)."""

    __tablename__ = "paper_validation_runs"

    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[PaperValidationStatus] = mapped_column(
        _enum(PaperValidationStatus), default=PaperValidationStatus.NOT_STARTED
    )
    runtime_mode: Mapped[PaperValidationRuntimeMode] = mapped_column(
        _enum(PaperValidationRuntimeMode), default=PaperValidationRuntimeMode.SCAN_ONLY
    )
    paper_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    blockers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PaperSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Detected setup during paper validation scan (Slice 39)."""

    __tablename__ = "paper_signals"

    paper_validation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[PaperSignalStatus] = mapped_column(
        _enum(PaperSignalStatus), default=PaperSignalStatus.DETECTED
    )
    matched_entry_blocks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    blocked_no_trade_filters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    suggested_entry: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    tp_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runner_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rule_engine_source: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PaperTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Simulated paper trade (Slice 39 — no exchange orders)."""

    __tablename__ = "paper_trades"

    paper_validation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_from_signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_signals.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    tp_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runner_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[PaperTradeStatus] = mapped_column(
        _enum(PaperTradeStatus), default=PaperTradeStatus.PROPOSED
    )
    exit_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    gross_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    slippage: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    rule_engine_source: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PaperTradeEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Audit trail for paper trade lifecycle events."""

    __tablename__ = "paper_trade_events"

    paper_trade_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_trades.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PaperValidationMetricSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Point-in-time paper validation metrics after trade close."""

    __tablename__ = "paper_validation_metric_snapshots"

    paper_validation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=False
    )
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    trigger_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_trades.id"), nullable=True
    )


class PaperValidationSchedulerConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped paper scheduler settings (Slice 40 — env flag still required)."""

    __tablename__ = "paper_validation_scheduler_configs"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_paper_scheduler_org"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_runs_per_cycle: Mapped[int] = mapped_column(Integer, default=5)
    max_scans_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tick_status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PaperValidationRuntimeHistory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-cycle runtime history for scans, ticks, and scheduler cycles."""

    __tablename__ = "paper_validation_runtime_history"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mode: Mapped[PaperRuntimeCycleMode] = mapped_column(
        _enum(PaperRuntimeCycleMode), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[PaperRuntimeCycleStatus] = mapped_column(
        _enum(PaperRuntimeCycleStatus), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signals_created: Mapped[int] = mapped_column(Integer, default=0)
    trades_opened: Mapped[int] = mapped_column(Integer, default=0)
    trades_closed: Mapped[int] = mapped_column(Integer, default=0)
    blockers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    data_freshness: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaperValidationAlert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Paper validation alert events (Slice 40 — no Telegram/email delivery)."""

    __tablename__ = "paper_validation_alerts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    alert_type: Mapped[PaperAlertType] = mapped_column(_enum(PaperAlertType), nullable=False)
    severity: Mapped[PaperAlertSeverity] = mapped_column(
        _enum(PaperAlertSeverity), default=PaperAlertSeverity.INFO
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    paper_validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    paper_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_trades.id"), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    delivery_status: Mapped[AlertDeliveryStatus] = mapped_column(
        _enum(AlertDeliveryStatus), default=AlertDeliveryStatus.DISABLED
    )
    delivery_channel: Mapped[AlertDeliveryChannel] = mapped_column(
        _enum(AlertDeliveryChannel), default=AlertDeliveryChannel.IN_APP
    )
    delivery_attempts: Mapped[int] = mapped_column(default=0)
    last_delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed", nullable=False)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PaperValidationDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Non-executable paper validation draft from a reviewed setup alert (Slice 78)."""

    __tablename__ = "paper_validation_drafts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_alert_id",
            "status",
            name="uq_paper_validation_drafts_org_alert_status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    source_alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=False, index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="conservative")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prep_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")


class PaperValidationCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Non-executable paper validation candidate queued from a ready draft (Slice 80)."""

    __tablename__ = "paper_validation_candidates"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_drafts.id"), nullable=False, index=True
    )
    source_alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=False
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="conservative")
    candidate_status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # AT-035 — research validation provenance (nullable for alert-draft / legacy rows)
    promotion_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True, index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    dataset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oos_expectancy: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    regime: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PaperValidationRunPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Non-executable paper validation run plan from a reviewing candidate (Slice 81)."""

    __tablename__ = "paper_validation_run_plans"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_candidates.id"), nullable=False, index=True
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_drafts.id"), nullable=False, index=True
    )
    source_alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=False
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    trigger_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="conservative")
    plan_status: Mapped[str] = mapped_column(String(20), nullable=False, default="planned")
    validation_window: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observation_timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    planned_entry_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_invalidation_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_success_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_failure_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PaperValidationRunSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Manual paper validation run session from a planned run plan (Slice 82).

    Record-only observation marker. Intentionally carries no strategy, engine,
    order, exchange, proposal, or approval fields and no FK to ``paper_validation_runs``,
    so it can never be picked up by the runtime engine or scheduler.
    """

    __tablename__ = "paper_validation_run_sessions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    run_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_run_plans.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_candidates.id"), nullable=False
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_drafts.id"), nullable=False
    )
    source_alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=False
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    risk_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="conservative")
    validation_window: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observation_timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaperValidationSessionObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Manual observation log for a paper validation run session (Slice 83).

    Append-only record. No FK to ``paper_validation_runs`` or engine paths.
    """

    __tablename__ = "paper_validation_session_observations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    run_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_run_sessions.id"), nullable=False, index=True
    )
    run_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_run_plans.id"), nullable=False
    )
    observation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PaperValidationSessionResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Final outcome for a paper validation run session (Slice 83 — one per session).

    Record-only classification. No FK to ``paper_validation_runs`` or engine paths.
    """

    __tablename__ = "paper_validation_session_results"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    run_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_run_sessions.id"), nullable=False, index=True
    )
    run_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_run_plans.id"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    success_criteria_met: Mapped[str] = mapped_column(String(16), nullable=False)
    success_criteria_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_criteria_met: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_criteria_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation_hit: Mapped[bool] = mapped_column(nullable=False, default=False)
    invalidation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    discipline_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    behaved_as_expected: Mapped[bool | None] = mapped_column(nullable=True)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketWatcherObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Read-only market watcher observations (Slice 41 — no execution)."""

    __tablename__ = "market_watcher_observations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    data_freshness: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[MarketWatcherObservationStatus] = mapped_column(
        _enum(MarketWatcherObservationStatus), nullable=False
    )
    related_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    related_paper_validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=True
    )


class MarketWatcherScanRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted market watcher scan summary (Slice 75 — cross-instance)."""

    __tablename__ = "market_watcher_scan_records"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alerts_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alerts_deduped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conditions_found: Mapped[list] = mapped_column(JSON, default=list)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    timeframes: Mapped[list] = mapped_column(JSON, default=list)
    detectors_enabled: Mapped[list] = mapped_column(JSON, default=list)
    detector_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MarketWatcherBridgeDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Market watcher → paper validation bridge decision history (Slice 42)."""

    __tablename__ = "market_watcher_bridge_decisions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_watcher_observations.id"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    paper_validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    decision: Mapped[MarketWatcherBridgeDecisionType] = mapped_column(
        _enum(MarketWatcherBridgeDecisionType), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blockers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    triggered_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_signals.id"), nullable=True
    )
    created_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PaperValidationObservabilityEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Structured observability for paper validation runtime."""

    __tablename__ = "paper_validation_observability_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    event_type: Mapped[PaperObservabilityEventType] = mapped_column(
        _enum(PaperObservabilityEventType), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)


class PaperValidationSampleWindow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Walk-forward sample window metrics for paper validation evidence."""

    __tablename__ = "paper_validation_sample_windows"

    paper_validation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(default=0.0)
    net_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    max_drawdown: Mapped[float] = mapped_column(default=0.0)
    expectancy: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    recommendation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ManualChartLevel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-drawn chart levels for pre-trade analysis."""

    __tablename__ = "manual_chart_levels"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    level_type: Mapped[ManualLevelType] = mapped_column(_enum(ManualLevelType), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    price_low: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    price_high: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ManualLevelRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable manual resistance/support revision. Later edits cannot rewrite history."""

    __tablename__ = "manual_level_revisions"
    __table_args__ = (
        UniqueConstraint("level_id", "revision_number", name="uq_manual_level_revision"),
        CheckConstraint("length(content_hash) = 64", name="ck_manual_level_revision_hash"),
        Index("ix_manual_level_revision_org_level", "organization_id", "level_id"),
    )

    level_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    instrument: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    level_type: Mapped[ManualLevelType] = mapped_column(_enum(ManualLevelType), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    price_low: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    price_high: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    venue: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    market_type: Mapped[str] = mapped_column(String(40), nullable=False, default="unspecified")
    price_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="quote")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manual_level_revisions.id"), nullable=True
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WatchlistItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "symbol",
            "exchange",
            name="uq_watchlist_org_user_symbol_exchange",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframes: Mapped[list] = mapped_column(JSON, default=list)
    strategy_ids: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TradePlanRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable executable child revision of the existing TradeProposal plan root."""

    __tablename__ = "trade_plan_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "organization_id", "user_id"],
            ["trade_proposals.id", "trade_proposals.organization_id", "trade_proposals.user_id"],
            name="fk_trade_plan_revision_plan_tenant",
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id", "user_id"],
            [
                "execution_accounts.id",
                "execution_accounts.organization_id",
                "execution_accounts.user_id",
            ],
            name="fk_trade_plan_revision_account_tenant",
        ),
        ForeignKeyConstraint(
            ["exchange_account_id", "organization_id", "user_id"],
            [
                "exchange_accounts.id",
                "exchange_accounts.organization_id",
                "exchange_accounts.user_id",
            ],
            name="fk_trade_plan_revision_exchange_account_tenant",
        ),
        UniqueConstraint(
            "id",
            "plan_id",
            "organization_id",
            "user_id",
            "account_id",
            name="uq_trade_plan_revision_binding",
        ),
        CheckConstraint(
            "schema_version IN ('CanonicalTradePlanContentV1', 'ManualDemoTradePlanV1')",
            name="ck_plan_schema_v1",
        ),
        CheckConstraint("operation = 'SUBMIT_ENTRY'", name="ck_plan_submit_entry"),
        CheckConstraint("expected_account_mode = 'NET'", name="ck_plan_expected_net"),
        CheckConstraint("length(content_hash) = 64", name="ck_plan_content_hash_length"),
        CheckConstraint(
            "plan_authority IN ('paper_validation', 'canonical', 'manual_demo_test')",
            name="ck_tpr_plan_authority",
        ),
        CheckConstraint(
            "(plan_authority IN ('paper_validation', 'manual_demo_test') AND "
            "canonical_candidate_id IS NULL) OR "
            "(plan_authority = 'canonical' AND canonical_candidate_id IS NOT NULL "
            "AND canonical_candidate_id = candidate_id)",
            name="ck_tpr_canonical_candidate_bind",
        ),
        CheckConstraint(
            "(plan_authority IN ('paper_validation', 'manual_demo_test') AND "
            "compiled_setup_definition_id IS NULL) OR "
            "(plan_authority = 'canonical' AND compiled_setup_definition_id IS NOT NULL "
            "AND compiled_setup_definition_id = setup_definition_id)",
            name="ck_tpr_compiled_setup_bind",
        ),
        CheckConstraint(
            "(plan_authority = 'manual_demo_test' AND schema_version = 'ManualDemoTradePlanV1' "
            "AND strategy_version_id IS NULL AND setup_definition_id IS NULL AND "
            "candidate_id IS NULL "
            "AND execution_venue = 'BLOFIN_DEMO' AND execution_policy_version = "
            "'manual-blofin-demo/v1') OR "
            "(plan_authority <> 'manual_demo_test' AND schema_version = "
            "'CanonicalTradePlanContentV1' "
            "AND strategy_version_id IS NOT NULL AND setup_definition_id IS NOT NULL AND"
            " candidate_id IS NOT NULL)",
            name="ck_tpr_manual_origin",
        ),
        Index(
            "ix_trade_plan_revisions_plan_created",
            "organization_id",
            "plan_id",
            "created_at",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[PlanOperation] = mapped_column(_enum(PlanOperation), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    plan_authority: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PLAN_AUTHORITY_PAPER_VALIDATION
    )
    canonical_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("canonical_candidates.candidate_id", name="fk_tpr_canonical_candidate"),
        nullable=True,
    )
    compiled_setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("compiled_setup_definitions.id", name="fk_tpr_compiled_setup"),
        nullable=True,
    )
    expected_account_mode: Mapped[AccountMode] = mapped_column(_enum(AccountMode), nullable=False)
    permission_attestation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    permission_attestation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_instrument: Mapped[str] = mapped_column(String(120), nullable=False)
    execution_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    semantic_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    presentation_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


@event.listens_for(TradePlanRevision, "before_update")
@event.listens_for(TradePlanRevision, "before_delete")
def _prevent_trade_plan_revision_mutation(
    _mapper: object,
    _connection: object,
    _target: TradePlanRevision,
) -> None:
    raise ValueError("TradePlanRevision rows are immutable; create a new revision.")


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approvals"

    proposal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trade_proposals.id"), nullable=False)
    plan_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_plan_revisions.id"), unique=True, nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    proposed_action: Mapped[ApprovalAction | None] = mapped_column(
        _enum(ApprovalAction), nullable=True
    )
    modified_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[RiskSeverity] = mapped_column(_enum(RiskSeverity), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    approval_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audit_logs.id"), nullable=True
    )
    authorization_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalAuthorization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One exact, account-bound authorization issued by an APPROVE decision."""

    __tablename__ = "approval_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "plan_id", "organization_id", "user_id", "account_id"],
            [
                "trade_plan_revisions.id",
                "trade_plan_revisions.plan_id",
                "trade_plan_revisions.organization_id",
                "trade_plan_revisions.user_id",
                "trade_plan_revisions.account_id",
            ],
            name="fk_approval_authorization_revision_binding",
        ),
        ForeignKeyConstraint(
            ["exchange_account_id", "organization_id", "user_id"],
            [
                "exchange_accounts.id",
                "exchange_accounts.organization_id",
                "exchange_accounts.user_id",
            ],
            name="fk_approval_authorization_exchange_account_tenant",
        ),
        CheckConstraint("operation = 'SUBMIT_ENTRY'", name="ck_authorization_submit_entry"),
        CheckConstraint("execution_mode = 'PAPER'", name="ck_authorization_paper"),
        CheckConstraint("verified_account_mode = 'NET'", name="ck_authorization_verified_net"),
        CheckConstraint(
            "length(plan_content_hash) = 64",
            name="ck_authorization_plan_hash_length",
        ),
        CheckConstraint(
            "length(authorization_content_hash) = 64",
            name="ck_authorization_content_hash_length",
        ),
        Index(
            "uq_available_approval_authorization",
            "organization_id",
            "account_id",
            "exchange_account_scope_key",
            "revision_id",
            "plan_content_hash",
            "operation",
            unique=True,
            postgresql_where=text("state = 'AVAILABLE'"),
            sqlite_where=text("state = 'AVAILABLE'"),
        ),
    )

    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approvals.id"), unique=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_accounts.id"), nullable=False
    )
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    exchange_account_scope_key: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[PlanOperation] = mapped_column(_enum(PlanOperation), nullable=False)
    execution_mode: Mapped[PlanExecutionMode] = mapped_column(
        _enum(PlanExecutionMode), nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    plan_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_venue: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_instrument: Mapped[str] = mapped_column(String(120), nullable=False)
    verified_account_mode: Mapped[AccountMode] = mapped_column(_enum(AccountMode), nullable=False)
    permission_attestation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    permission_attestation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[AuthorizationState] = mapped_column(
        _enum(AuthorizationState), default=AuthorizationState.AVAILABLE, nullable=False
    )
    channel: Mapped[AuthorizationChannel] = mapped_column(
        _enum(AuthorizationChannel), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_execution_command_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    authorization_content_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class ExecutionIdempotencyBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped opaque-key binding for EXECUTE_PAPER_PLAN (architecture §23-§24)."""

    __tablename__ = "execution_idempotency_bindings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "opaque_key",
            name="uq_execution_idempotency_binding",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_execution_idempotency_payload_hash_length",
        ),
        ForeignKeyConstraint(
            ["account_id", "organization_id", "user_id"],
            [
                "execution_accounts.id",
                "execution_accounts.organization_id",
                "execution_accounts.user_id",
            ],
            name="fk_execution_idempotency_account_tenant",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    exchange_account_scope_key: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    opaque_key: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_plan_revisions.id"), nullable=False
    )
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_authorizations.id"), nullable=False
    )
    command_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    outcome: Mapped[ExecutionCommandOutcome | None] = mapped_column(
        _enum(ExecutionCommandOutcome), nullable=True
    )


class ExecutionCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable immutable EXECUTE_PAPER_PLAN command identity."""

    __tablename__ = "execution_commands"
    __table_args__ = (
        CheckConstraint("operation = 'SUBMIT_ENTRY'", name="ck_execution_command_submit_entry"),
        CheckConstraint(
            "operation_namespace = 'alphatrade/submit-entry/v1'",
            name="ck_execution_command_entry_namespace",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_execution_command_payload_hash_length",
        ),
        CheckConstraint("length(plan_content_hash) = 64", name="ck_execution_command_plan_hash"),
        ForeignKeyConstraint(
            ["revision_id", "plan_id", "organization_id", "user_id", "account_id"],
            [
                "trade_plan_revisions.id",
                "trade_plan_revisions.plan_id",
                "trade_plan_revisions.organization_id",
                "trade_plan_revisions.user_id",
                "trade_plan_revisions.account_id",
            ],
            name="fk_execution_command_revision_binding",
        ),
        Index("ix_execution_commands_org_account", "organization_id", "account_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    operation: Mapped[PlanOperation] = mapped_column(_enum(PlanOperation), nullable=False)
    operation_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approval_authorizations.id"), nullable=False
    )
    plan_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    opaque_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outcome: Mapped[ExecutionCommandOutcome] = mapped_column(
        _enum(ExecutionCommandOutcome), nullable=False
    )
    blocked_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


@event.listens_for(ExecutionCommand, "before_update")
@event.listens_for(ExecutionCommand, "before_delete")
def _prevent_execution_command_mutation(
    _mapper: object,
    _connection: object,
    _target: ExecutionCommand,
) -> None:
    raise ValueError("ExecutionCommand rows are immutable.")


class PlanEntryExecutionClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Database-enforced one entry execution per plan revision and account."""

    __tablename__ = "plan_entry_execution_claims"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "exchange_account_scope_key",
            "revision_id",
            "operation",
            name="uq_plan_entry_execution_claim",
        ),
        UniqueConstraint("command_id", name="uq_plan_entry_execution_claim_command"),
        CheckConstraint("operation = 'SUBMIT_ENTRY'", name="ck_plan_entry_claim_submit_entry"),
        ForeignKeyConstraint(
            ["command_id"],
            ["execution_commands.id"],
            name="fk_plan_entry_claim_command",
        ),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_plan_entry_claim_receipt",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    exchange_account_scope_key: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_plan_revisions.id"), nullable=False
    )
    operation: Mapped[PlanOperation] = mapped_column(_enum(PlanOperation), nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AccountSafetyEpoch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Account-scoped safety epoch locked before risk accounting (architecture §24)."""

    __tablename__ = "account_safety_epochs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_account_safety_epoch",
        ),
        CheckConstraint("epoch >= 1", name="ck_account_safety_epoch_positive"),
        ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["execution_accounts.id", "execution_accounts.organization_id"],
            name="fk_account_safety_epoch_account",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AccountRiskAccountingState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Account-scoped serializable risk ledger locked after the safety epoch."""

    __tablename__ = "account_risk_accounting_states"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_account_risk_accounting_state",
        ),
        CheckConstraint("reserved_notional >= 0", name="ck_account_risk_reserved_notional"),
        CheckConstraint("actual_notional >= 0", name="ck_account_risk_actual_notional"),
        CheckConstraint("reserved_trade_slots >= 0", name="ck_account_risk_reserved_slots"),
        CheckConstraint("max_notional >= 0", name="ck_account_risk_max_notional"),
        ForeignKeyConstraint(
            ["account_id", "organization_id"],
            ["execution_accounts.id", "execution_accounts.organization_id"],
            name="fk_account_risk_accounting_account",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reserved_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    reserved_daily_loss: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, default=Decimal("0")
    )
    reserved_trade_slots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    actual_daily_loss: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    actual_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbol_reserved: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    symbol_actual: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_daily_loss: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_trade_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    max_symbol_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    daily_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False, default="USDT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ExecutionReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stable receipt identity. Historical truth lives in append-only transitions."""

    __tablename__ = "execution_receipts"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_execution_receipt_command"),
        ForeignKeyConstraint(
            ["command_id"],
            ["execution_commands.id"],
            name="fk_execution_receipt_command",
        ),
    )

    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    operation: Mapped[PlanOperation] = mapped_column(_enum(PlanOperation), nullable=False)
    authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_authorizations.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


@event.listens_for(ExecutionReceipt, "before_update")
@event.listens_for(ExecutionReceipt, "before_delete")
def _prevent_execution_receipt_mutation(
    _mapper: object,
    _connection: object,
    _target: ExecutionReceipt,
) -> None:
    raise ValueError("ExecutionReceipt identity rows are immutable.")


class ExecutionTransition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only receipt state transition."""

    __tablename__ = "execution_transitions"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id",
            "sequence",
            name="uq_execution_transition_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_execution_transition_sequence"),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_execution_transition_hash_length",
        ),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_execution_transition_receipt",
        ),
        Index("ix_execution_transitions_receipt_seq", "receipt_id", "sequence"),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_state: Mapped[ExecutionReceiptState] = mapped_column(
        _enum(ExecutionReceiptState), nullable=False
    )
    source_fact: Mapped[str] = mapped_column(String(80), nullable=False)
    source_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    quantity_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(ExecutionTransition, "before_update")
@event.listens_for(ExecutionTransition, "before_delete")
def _prevent_execution_transition_mutation(
    _mapper: object,
    _connection: object,
    _target: ExecutionTransition,
) -> None:
    raise ValueError("ExecutionTransition rows are immutable.")


class GovernedDemoLifecycleResolution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable actual demo exit, projection and audited account reuse proof."""

    __tablename__ = "governed_demo_lifecycle_resolutions"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_demo_lifecycle_command"),
        CheckConstraint("length(content_hash) = 64", name="ck_demo_lifecycle_hash"),
        CheckConstraint("released_notional >= 0", name="ck_demo_lifecycle_release"),
        Index("ix_demo_lifecycle_org_account", "organization_id", "account_id"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_accounts.id"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_commands.id"), nullable=False
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_plan_revisions.id"), nullable=False
    )
    journal_close_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_lifecycle_events.id"), nullable=False
    )
    audit_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_logs.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    risk_accounting_version: Mapped[int] = mapped_column(Integer, nullable=False)


@event.listens_for(GovernedDemoLifecycleResolution, "before_update")
@event.listens_for(GovernedDemoLifecycleResolution, "before_delete")
def _prevent_demo_lifecycle_mutation(_mapper: object, _connection: object, _target: object) -> None:
    raise ValueError("Governed demo lifecycle resolutions are immutable.")


class ManualDemoLifecycleResolution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable scoped proof that releases one manual demo account claim."""

    __tablename__ = "manual_demo_lifecycle_resolutions"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_manual_demo_resolution_command"),
        CheckConstraint("length(content_hash) = 64", name="ck_manual_demo_resolution_hash"),
        CheckConstraint("released_notional >= 0", name="ck_manual_demo_resolution_release"),
        CheckConstraint(
            "reason IN ('verified_exit', 'terminal_unfilled', 'proven_unsent')",
            name="ck_manual_demo_resolution_reason",
        ),
        Index("ix_manual_demo_resolution_scope", "organization_id", "account_id"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_accounts.id"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("execution_commands.id"), nullable=False
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trade_plan_revisions.id"), nullable=False
    )
    audit_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audit_logs.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)


@event.listens_for(ManualDemoLifecycleResolution, "before_update")
@event.listens_for(ManualDemoLifecycleResolution, "before_delete")
def _prevent_manual_demo_resolution_mutation(
    _mapper: object, _connection: object, _target: object
) -> None:
    raise ValueError("Manual demo lifecycle resolutions are immutable.")


class ExecutionProjection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rebuildable optimistic-version projection of a receipt."""

    __tablename__ = "execution_projections"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_execution_projection_receipt"),
        CheckConstraint("version >= 1", name="ck_execution_projection_version"),
        CheckConstraint("event_watermark >= 0", name="ck_execution_projection_watermark"),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_execution_projection_receipt",
        ),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[ExecutionReceiptState] = mapped_column(
        _enum(ExecutionReceiptState), nullable=False
    )
    filled_quantity: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    remaining_quantity: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    weighted_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    fees: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    funding: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal("0"))
    position_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reconciliation_status: Mapped[ExecutionReconciliationStatus] = mapped_column(
        _enum(ExecutionReconciliationStatus),
        nullable=False,
        default=ExecutionReconciliationStatus.NOT_REQUIRED,
    )
    event_watermark: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RiskReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Conservative pending-exposure reservation charged through uncertainty."""

    __tablename__ = "risk_reservations"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_risk_reservation_command"),
        UniqueConstraint("receipt_id", name="uq_risk_reservation_receipt"),
        CheckConstraint("pending_order_exposure >= 0", name="ck_risk_reservation_pending"),
        CheckConstraint(
            "remaining_reserved_notional >= 0",
            name="ck_risk_reservation_remaining",
        ),
        CheckConstraint(
            "converted_trade_slots >= 0",
            name="ck_risk_reservation_converted_slots",
        ),
        CheckConstraint("contract_multiplier > 0", name="ck_risk_reservation_multiplier"),
        CheckConstraint(
            "contract_type IN ('LINEAR', 'INVERSE')",
            name="ck_risk_reservation_contract_type",
        ),
        ForeignKeyConstraint(
            ["command_id"],
            ["execution_commands.id"],
            name="fk_risk_reservation_command",
        ),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_risk_reservation_receipt",
        ),
        ForeignKeyConstraint(
            ["plan_revision_id"],
            ["trade_plan_revisions.id"],
            name="fk_risk_reservation_revision",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    plan_revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    instrument: Mapped[str] = mapped_column(String(120), nullable=False)
    risk_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_snapshot_version: Mapped[str] = mapped_column(String(64), nullable=False)
    pending_order_exposure: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    submitting_or_ambiguous_exposure: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    open_order_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    daily_trade_allocation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daily_loss_allocation: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    total_exposure: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    symbol_exposure: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    remaining_reserved_notional: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    converted_trade_slots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contract_multiplier: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    contract_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    exposure_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    safety_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    release_state: Mapped[RiskReservationReleaseState] = mapped_column(
        _enum(RiskReservationReleaseState),
        nullable=False,
        default=RiskReservationReleaseState.CHARGED,
    )
    release_reason: Mapped[RiskReservationReleaseReason | None] = mapped_column(
        _enum(RiskReservationReleaseReason), nullable=True
    )


class VenueSubmitEffect(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable venue-submit effect claimed before any provider POST."""

    __tablename__ = "venue_submit_effects"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_venue_submit_effect_command"),
        UniqueConstraint("receipt_id", name="uq_venue_submit_effect_receipt"),
        UniqueConstraint("client_order_id", name="uq_venue_submit_effect_client_order_id"),
        CheckConstraint("fencing_token >= 0", name="ck_venue_submit_effect_fence"),
        CheckConstraint("attempt >= 0", name="ck_venue_submit_effect_attempt"),
        ForeignKeyConstraint(
            ["command_id"],
            ["execution_commands.id"],
            name="fk_venue_submit_effect_command",
        ),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_venue_submit_effect_receipt",
        ),
    )

    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[VenueSubmitEffectState] = mapped_column(
        _enum(VenueSubmitEffectState),
        nullable=False,
        default=VenueSubmitEffectState.CREATED,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch_safety_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dispatch_fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dispatch_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uncertainty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reconciliation_disposition: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ExecutionFillFact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable unique venue fill fact. Duplicate identity replays by content hash."""

    __tablename__ = "execution_fill_facts"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id",
            "source_fill_identity",
            name="uq_execution_fill_fact_source",
        ),
        CheckConstraint("quantity > 0", name="ck_execution_fill_fact_quantity"),
        CheckConstraint("price > 0", name="ck_execution_fill_fact_price"),
        CheckConstraint(
            "length(trim(source_fill_identity)) > 0",
            name="ck_execution_fill_fact_source_identity",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_execution_fill_fact_hash_length",
        ),
        ForeignKeyConstraint(
            ["receipt_id"],
            ["execution_receipts.id"],
            name="fk_execution_fill_fact_receipt",
        ),
        ForeignKeyConstraint(
            ["command_id"],
            ["execution_commands.id"],
            name="fk_execution_fill_fact_command",
        ),
        Index("ix_execution_fill_facts_receipt", "receipt_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receipt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    venue_source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_fill_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


@event.listens_for(ExecutionFillFact, "before_update")
@event.listens_for(ExecutionFillFact, "before_delete")
def _prevent_execution_fill_fact_mutation(
    _mapper: object,
    _connection: object,
    _target: ExecutionFillFact,
) -> None:
    raise ValueError("ExecutionFillFact rows are immutable.")


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[StrategyId | None] = mapped_column(_enum(StrategyId), nullable=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_proposals.id"), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"), nullable=True)
    mode: Mapped[ExecutionMode] = mapped_column(_enum(ExecutionMode), default=ExecutionMode.PAPER)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[OrderSide] = mapped_column(_enum(OrderSide), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(_enum(OrderType), nullable=False)
    size: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(_enum(OrderStatus), default=OrderStatus.PENDING)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Position(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "positions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[StrategyId | None] = mapped_column(_enum(StrategyId), nullable=True)
    linked_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_proposals.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    size: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    leverage: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    liquidation_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    unrealized_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    status: Mapped[PositionStatus] = mapped_column(
        _enum(PositionStatus), default=PositionStatus.OPEN
    )
    take_profits: Mapped[list] = mapped_column(JSON, default=list)
    risk_state: Mapped[dict] = mapped_column(JSON, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --------------------------------------------------------------------------- #
# Risk state & events
# --------------------------------------------------------------------------- #


class KillSwitchState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Organization-scoped persistent kill switch (AT-014).

    One row per organization. When ``active`` is true, paper (and future sandbox /
    real) execution adapters must refuse new side effects after a live DB read.
    """

    __tablename__ = "kill_switch_states"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_kill_switch_organization"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class DailyRiskState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "daily_risk_states"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "day", name="uq_daily_risk_org_user_day"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[date_type] = mapped_column(Date, nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    # Nullable when the user has not configured an absolute daily loss limit (AT-012).
    daily_loss_limit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    daily_target: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    max_trades_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class UserRiskSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent per-user risk settings for paper discipline (Slice 45)."""

    __tablename__ = "user_risk_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_user_risk_settings_org_user"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    daily_loss_limit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    daily_target: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=20)
    max_risk_per_trade_percent: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("1"))
    default_account_balance: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("10000"))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    green_day_protection_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    one_loss_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    overtrading_guard_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserNotificationPreferences(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-user notification delivery preferences (Slice 46 — no provider secrets)."""

    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_user_notification_preferences_org_user",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    min_severity: Mapped[PaperAlertSeverity] = mapped_column(
        _enum(PaperAlertSeverity), default=PaperAlertSeverity.INFO
    )
    enabled_alert_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    digest_mode: Mapped[str] = mapped_column(String(32), default="immediate")
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RiskEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    rule_triggered: Mapped[RiskRuleId] = mapped_column(_enum(RiskRuleId), nullable=False)
    severity: Mapped[RiskSeverity] = mapped_column(_enum(RiskSeverity), nullable=False)
    action_taken: Mapped[RiskAction] = mapped_column(_enum(RiskAction), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Journal
# --------------------------------------------------------------------------- #


class TradeJournal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "journals"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    strategy_id: Mapped[StrategyId | None] = mapped_column(_enum(StrategyId), nullable=True)
    entry_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    exit_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    emotions: Mapped[list] = mapped_column(JSON, default=list)
    mistakes: Mapped[list] = mapped_column(JSON, default=list)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
    improvement_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[TradeResult] = mapped_column(_enum(TradeResult), default=TradeResult.OPEN)
    pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    stress_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    screenshot_refs: Mapped[list] = mapped_column(JSON, default=list)
    linked_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_proposals.id"), nullable=True
    )
    linked_position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True
    )


# --------------------------------------------------------------------------- #
# Canonical journal trades (AT-030 — record-only, no execution authority)
# --------------------------------------------------------------------------- #


class JournalTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Canonical journal trade unifying manual, paper, imported, and system trades.

    Record-only intelligence layer: rows never place orders and are never read by
    the execution engine, scheduler, or risk gates. Links reference existing
    records (positions, paper trades, proposals, orders, backtest trades, legacy
    journal entries) instead of duplicating them. Plan fields capture the trade
    thesis before entry; execution fields capture what actually happened; the
    excursion fields (MFE/MAE, available vs realized) are deterministic values
    supplied by callers or later replay slices — never live market I/O here.
    """

    __tablename__ = "journal_trades"
    __table_args__ = (
        # AT-033: idempotent import/backfill — one journal trade per external
        # reference within an organization (partial: NULL refs stay unconstrained).
        Index(
            "uq_journal_trades_org_external_ref",
            "organization_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
            sqlite_where=text("external_ref IS NOT NULL"),
        ),
        # Phase 4: one canonical JournalTrade per execution lifecycle in an org.
        Index(
            "uq_journal_trades_org_lifecycle",
            "organization_id",
            "execution_lifecycle_id",
            unique=True,
            postgresql_where=text("execution_lifecycle_id IS NOT NULL"),
            sqlite_where=text("execution_lifecycle_id IS NOT NULL"),
        ),
        CheckConstraint(
            "evidence_window_hash IS NULL OR length(evidence_window_hash) = 64",
            name="ck_journal_trades_lineage_window_hash",
        ),
        Index("ix_journal_trades_org_candidate", "organization_id", "candidate_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[JournalTradeSource] = mapped_column(_enum(JournalTradeSource), nullable=False)
    status: Mapped[JournalTradeStatus] = mapped_column(
        _enum(JournalTradeStatus), default=JournalTradeStatus.PLANNED, nullable=False
    )
    # AT-033: who/what created the journal row (human-vs-system analytics).
    entry_method: Mapped[JournalEntryMethod] = mapped_column(
        _enum(JournalEntryMethod), default=JournalEntryMethod.MANUAL, nullable=False
    )

    # Instrument & context
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    market_regime: Mapped[MarketRegime] = mapped_column(
        _enum(MarketRegime), default=MarketRegime.UNKNOWN, nullable=False
    )
    regime_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Setup & strategy provenance (immutable versions linked, never copied)
    setup_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("setup_definitions.id"), nullable=True
    )
    user_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    strategy_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Plan (thesis before entry)
    direction: Mapped[TradeDirection] = mapped_column(_enum(TradeDirection), nullable=False)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_entry_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    planned_stop_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    planned_targets: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    runner_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    runner_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_risk_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    # Execution (what actually happened)
    entry_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    leverage: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    funding: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    slippage: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    gross_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    result: Mapped[TradeResult] = mapped_column(_enum(TradeResult), default=TradeResult.OPEN)

    # Excursions & available-vs-realized profit (deterministic inputs only)
    mfe_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mae_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mfe_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    mae_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    available_profit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    realized_vs_available_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    excursion_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # AT-032 — provenance for HistoricalCandle replay (null when never replayed)
    excursion_data_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    excursion_is_stale: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    excursion_freshness_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    excursion_candle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excursion_gaps_detected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excursion_window_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    excursion_computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Reflection
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Links to existing records (never duplicated, always tenant-checked)
    linked_position_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True
    )
    linked_paper_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_trades.id"), nullable=True
    )
    linked_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_proposals.id"), nullable=True
    )
    linked_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    linked_backtest_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_trades.id"), nullable=True
    )
    linked_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journals.id"), nullable=True
    )
    linked_paper_validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_runs.id"), nullable=True
    )
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 4: projector-owned execution lifecycle identity (claim id). NULL for
    # manual/imported rows that are not bound to an execution lifecycle.
    execution_lifecycle_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    projector_watermark_rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    projector_lock_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Phase 8: query helpers copied from sticky payload.lineage. Projector-owned.
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence_window_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trade_plan_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class JournalTradeEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Screenshot/chart/note/link evidence attached to a journal trade (AT-030)."""

    __tablename__ = "journal_trade_evidence"

    journal_trade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    kind: Mapped[JournalEvidenceKind] = mapped_column(_enum(JournalEvidenceKind), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class JournalTradeRuleCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-rule compliance assessment for a journal trade (AT-030)."""

    __tablename__ = "journal_trade_rule_checks"

    journal_trade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    rule_key: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[RuleComplianceStatus] = mapped_column(
        _enum(RuleComplianceStatus), default=RuleComplianceStatus.UNASSESSED, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalTradeObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Behavioral/process observation recorded against a journal trade (AT-030)."""

    __tablename__ = "journal_trade_observations"

    journal_trade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    category: Mapped[JournalObservationCategory] = mapped_column(
        _enum(JournalObservationCategory), nullable=False
    )
    observation: Mapped[str] = mapped_column(Text, nullable=False)
    emotion_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One committed bulk journal import (AT-033) for reconciliation history.

    Commits are all-or-nothing in a single unit of work, so persisted batches
    always describe an applied import. The row report carries per-row outcomes
    (created / duplicate / invalid) without duplicating full trade payloads.
    """

    __tablename__ = "journal_import_batches"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[JournalImportBatchStatus] = mapped_column(
        _enum(JournalImportBatchStatus),
        default=JournalImportBatchStatus.COMMITTED,
        nullable=False,
    )
    source_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_report: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)


class JournalTradeAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Binary evidence attachment for a journal trade (AT-033).

    Bytes are stored in the database (``storage_backend='db'``) behind the
    :class:`~app.services.journal_attachment_storage.AttachmentStorage`
    interface so an object store can replace the backend later without a
    schema change. Size/MIME/quota limits are enforced in the service.
    """

    __tablename__ = "journal_trade_attachments"

    journal_trade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False, default="db")
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)


class JournalLifecycleEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only journal lifecycle event. Candidate/reject/skip never create trades."""

    __tablename__ = "journal_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "source_system",
            "source_aggregate",
            "event_type",
            "source_event_id",
            "source_event_version",
            "supersession",
            name="uq_journal_lifecycle_event_source",
        ),
        Index("ix_journal_lifecycle_org_lifecycle", "organization_id", "execution_lifecycle_id"),
        CheckConstraint("length(content_hash) = 64", name="ck_journal_lifecycle_event_hash"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[JournalLifecycleEventType] = mapped_column(
        _enum(JournalLifecycleEventType), nullable=False
    )
    execution_lifecycle_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_aggregate: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersession: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    journal_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=True
    )


class JournalProjectionReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotent projector receipt. One source event maps to at most one apply."""

    __tablename__ = "journal_projection_receipts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "source_system",
            "source_aggregate",
            "event_type",
            "source_event_id",
            "source_event_version",
            "supersession",
            name="uq_journal_projection_receipt_source",
        ),
        CheckConstraint("length(content_hash) = 64", name="ck_journal_projection_receipt_hash"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[JournalLifecycleEventType] = mapped_column(
        _enum(JournalLifecycleEventType), nullable=False
    )
    execution_lifecycle_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_aggregate: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersession: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    journal_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=True
    )
    created_journal_trade: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    skipped_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class JournalTradeVenueCorrection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only correction of projector-owned venue facts."""

    __tablename__ = "journal_trade_venue_corrections"
    __table_args__ = (
        CheckConstraint("length(content_hash) = 64", name="ck_journal_venue_correction_hash"),
        Index(
            "ix_journal_venue_correction_trade",
            "organization_id",
            "journal_trade_id",
        ),
    )

    journal_trade_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


# --------------------------------------------------------------------------- #
# RAG knowledge base (metadata only; vectors in Qdrant)
# --------------------------------------------------------------------------- #


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_hash",
            name="uq_document_org_source_hash",
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source_type: Mapped[DocumentSourceType] = mapped_column(
        _enum(DocumentSourceType), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    ingestion_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    indexing_generation: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class KnowledgeIndexingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable scoped intent; no document FK so deletion tombstones survive."""

    __tablename__ = "knowledge_indexing_jobs"
    __table_args__ = (
        Index("ix_knowledge_indexing_due", "status", "available_at"),
        Index("ix_knowledge_indexing_document", "document_id", "organization_id", "user_id"),
        CheckConstraint("attempts >= 0", name="knowledge_indexing_attempts"),
        CheckConstraint("operation IN ('upsert', 'delete')", name="knowledge_indexing_operation"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'superseded')",
            name="knowledge_indexing_status",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


# --------------------------------------------------------------------------- #
# Usage & audit
# --------------------------------------------------------------------------- #


class UsageEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feature: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    provider_reported_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    estimated_cost: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    cost_source: Mapped[CostSource] = mapped_column(
        _enum(CostSource), default=CostSource.UNAVAILABLE, nullable=False
    )
    cost_is_placeholder: Mapped[bool] = mapped_column(Boolean, default=True)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[UsageStatus] = mapped_column(
        _enum(UsageStatus), default=UsageStatus.SUCCESS, nullable=False
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ModelCallAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Actual LLM call telemetry (Phase 2). Complements UsageEvent; not a second quota system."""

    __tablename__ = "model_call_attempts"
    __table_args__ = (
        Index("ix_model_call_attempts_correlation", "correlation_id"),
        Index(
            "ix_model_call_attempts_org_event",
            "organization_id",
            "event_at",
        ),
        CheckConstraint("length(policy_version) > 0", name="ck_model_call_policy_version"),
        CheckConstraint("NOT mutation_allowed", name="ck_model_call_no_mutation"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    usage_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("usage_events.id"), nullable=True
    )
    purpose: Mapped[ModelRoutingPurpose] = mapped_column(_enum(ModelRoutingPurpose), nullable=False)
    tier: Mapped[ModelRoutingTier] = mapped_column(_enum(ModelRoutingTier), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(80), nullable=False)
    resolved_model: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    fallback_policy: Mapped[ModelFallbackPolicy] = mapped_column(
        _enum(ModelFallbackPolicy), nullable=False
    )
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_category: Mapped[ModelFailureCategory] = mapped_column(
        _enum(ModelFailureCategory), default=ModelFailureCategory.NONE, nullable=False
    )
    status: Mapped[UsageStatus] = mapped_column(
        _enum(UsageStatus), default=UsageStatus.SUCCESS, nullable=False
    )
    estimated_cost: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    cost_source: Mapped[CostSource] = mapped_column(
        _enum(CostSource), default=CostSource.UNAVAILABLE, nullable=False
    )
    resource_type: Mapped[ModelResourceType] = mapped_column(
        _enum(ModelResourceType), default=ModelResourceType.GENERIC, nullable=False
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    retention_category: Mapped[ModelRetentionCategory] = mapped_column(
        _enum(ModelRetentionCategory),
        default=ModelRetentionCategory.STANDARD,
        nullable=False,
    )
    mutation_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationQuota(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-organization usage limits (Slice 24)."""

    __tablename__ = "organization_quotas"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), unique=True, nullable=False
    )
    monthly_token_limit: Mapped[int] = mapped_column(Integer, default=2_000_000)
    monthly_cost_limit: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("100"))
    daily_request_limit: Mapped[int] = mapped_column(Integer, default=5_000)
    limit_agent_chat: Mapped[int] = mapped_column(Integer, default=2_000)
    limit_rag_ingest: Mapped[int] = mapped_column(Integer, default=500)
    limit_market_analyze: Mapped[int] = mapped_column(Integer, default=1_000)
    limit_agent_narrative: Mapped[int] = mapped_column(Integer, default=2_000)
    limit_paper_execution: Mapped[int] = mapped_column(Integer, default=200)
    soft_warning_threshold: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0.80"))
    hard_block_threshold: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("1.00"))
    plan_id: Mapped[str] = mapped_column(String(40), default="free", nullable=False)


# --------------------------------------------------------------------------- #
# Billing (Slice 26)
# --------------------------------------------------------------------------- #


class BillingCustomer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_customers"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_billing_customer_org"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)


class Subscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_subscription_org"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan_id: Mapped[str] = mapped_column(String(40), default="free", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)


class BillingEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    redacted_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageExportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_export_batches"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    provider_reported_cost: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    estimated_cost: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    billing_grade_cost: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"))
    cost_is_billing_grade: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_event_count: Mapped[int] = mapped_column(Integer, default=0)
    export_summary: Mapped[dict] = mapped_column(JSON, default=dict)


class WebhookEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider_event_id", name="uq_webhook_provider_event"),)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="processed", nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    redacted_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        _enum(ActorType), default=ActorType.SYSTEM, nullable=False
    )
    action: Mapped[AuditEventType] = mapped_column(_enum(AuditEventType), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        _enum(AuditResult), default=AuditResult.SUCCESS, nullable=False
    )
    severity: Mapped[AuditSeverity] = mapped_column(
        _enum(AuditSeverity), default=AuditSeverity.INFO, nullable=False
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    redacted_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Background worker (Slice 59)
# --------------------------------------------------------------------------- #


class WorkerHeartbeat(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Liveness + control record for a background worker instance.

    One row per ``worker_name``. ``paused`` is the manual pause/resume switch;
    ``status`` reflects the last cycle outcome.
    """

    __tablename__ = "worker_heartbeats"
    __table_args__ = (UniqueConstraint("worker_name", name="uq_worker_heartbeat_name"),)

    worker_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="starting", nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)


class MarketScanRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One worker scan cycle. Failed rows act as a dead-letter record."""

    __tablename__ = "market_scan_runs"

    worker_name: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    setups_detected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SetupDetectionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A persisted setup detection from the deterministic analysis engine."""

    __tablename__ = "setup_detections"

    scan_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_scan_runs.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    setup_name: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------------------- #
# Exchange demo execution (Slice 61) — paper_exchange_demo only, never live
# --------------------------------------------------------------------------- #


class ExchangeOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An order mirrored to the BloFin *demo* venue.

    ``exchange_mode`` is always ``paper_exchange_demo`` for rows created in this
    scaffold. ``internal_order_id`` links back to the paper :class:`Order`.
    """

    __tablename__ = "exchange_orders"

    internal_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    exchange: Mapped[str] = mapped_column(String(40), nullable=False)
    exchange_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    inst_id: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    venue_client_order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted", nullable=False)
    filled_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)


class ExchangeFill(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A fill recorded against an :class:`ExchangeOrder` (demo venue)."""

    __tablename__ = "exchange_fills"

    exchange_order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exchange_orders.id"), nullable=False
    )
    fill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)


# --------------------------------------------------------------------------- #
# Performance analytics (Slice 62)
# --------------------------------------------------------------------------- #


class PerformanceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A point-in-time account-level performance aggregate.

    Headline metrics are stored as columns for cheap querying; the full payload
    (breakdowns, equity curve) lives in ``metrics`` JSON.
    """

    __tablename__ = "performance_snapshots"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(32), default="account", nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    gross_profit: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    gross_loss: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    total_fees: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    total_funding: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    avg_r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyPerformanceDaily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-strategy, per-day performance rollup (idempotent on the natural key)."""

    __tablename__ = "strategy_performance_daily"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "strategy_id",
            "day",
            name="uq_strategy_perf_daily_org_strategy_day",
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    strategy_id: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    day: Mapped[date_type] = mapped_column(Date, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    max_drawdown: Mapped[Decimal] = mapped_column(_MONEY, default=Decimal("0"), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# TradingView signal intake + BloFin demo sync (AT-037)
# --------------------------------------------------------------------------- #


class TradingViewSignal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Inbound TradingView alert signal (AT-037 — paper-only intake)."""

    __tablename__ = "tradingview_signals"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_tv_signal_org_idempotency",
        ),
        UniqueConstraint(
            "organization_id",
            "external_alert_id",
            name="uq_tv_signal_org_alert",
        ),
        Index("ix_tv_signals_org_status_received", "organization_id", "status", "received_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    external_alert_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TradingViewSignalStatus.RECEIVED.value
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    setup_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    setup_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("setup_definitions.id"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    trigger_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_payload_redacted: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_of_signal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tradingview_signals.id"), nullable=True
    )
    source_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_alerts.id"), nullable=True
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_drafts.id"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_candidates.id"), nullable=True
    )
    journal_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=True
    )
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True
    )


class PaperSignalOrchestrationDecision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deterministic paper-signal orchestration decision (AT-038 — paper-only)."""

    __tablename__ = "paper_signal_orchestration_decisions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "tradingview_signal_id",
            name="uq_pso_org_signal",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_pso_org_idempotency",
        ),
        Index(
            "ix_pso_org_status_updated",
            "organization_id",
            "status",
            "updated_at",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    tradingview_signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tradingview_signals.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=PaperSignalOrchestrationStatus.BLOCKED.value,
    )
    mode: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default=PaperSignalOrchestrationMode.OBSERVE_ONLY.value,
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    reason_codes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk_evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    transitions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("setup_definitions.id"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategies.id"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=True
    )
    journal_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_trades.id"), nullable=True
    )
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_candidates.id"), nullable=True
    )
    run_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_validation_run_plans.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("trade_proposals.id"), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BloFinDemoSyncSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bounded BloFin demo account/position snapshot (AT-037 — read-only)."""

    __tablename__ = "blofin_demo_sync_snapshots"
    __table_args__ = (
        Index(
            "ix_blofin_sync_org_synced_at",
            "organization_id",
            "synced_at",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    health_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BloFinSyncHealthStatus.UNAVAILABLE.value
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="blofin_demo")
    exchange_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    account_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    positions_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    market_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balance_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


_ = _install_history
_register_strategy_immutability()
_register_journal_immutability()
_register_canonical_candidate_immutability()
_register_canonical_eligibility_immutability()
_register_canonical_trade_plan_immutability()
_register_learning_attribution_immutability()
_register_paper_evaluation_immutability()


class AgentSavedEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-authored notes and draft definitions, never execution authority."""

    __tablename__ = "agent_saved_entries"
    __table_args__ = (Index("ix_agent_saved_scope", "organization_id", "user_id", "category"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    undone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AgentCaptureSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable deduplication across retries and conversations, including Undo."""

    __tablename__ = "agent_capture_sources"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", "source_hash", name="uq_agent_capture_source"
        ),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
