"""Shared enums, constrained types, and base models for all schemas.

Centralizing enums and constrained scalar types keeps validation consistent at
every system boundary and lets the persistence layer (Slice 4) reuse the exact
same enums, avoiding string drift between API and database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
)

# --------------------------------------------------------------------------- #
# Base models
# --------------------------------------------------------------------------- #


class TimestampModel(BaseModel):
    """SQL timestamps are UTC; SQLite omits the offset on ORM reload.

    Normalize response serialization without relaxing date-time input validation
    or changing stored values and deterministic execution controls.
    """

    @field_serializer("*", mode="wrap", check_fields=False)
    def serialize_timestamp(self, value: Any, handler: Any):  # type: ignore[no-untyped-def]
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return handler(value)


class StrictModel(TimestampModel):
    """Base for external request models: rejects unknown fields.

    Using ``extra="forbid"`` at request boundaries prevents typos and smuggled
    fields from silently passing through.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ORMModel(TimestampModel):
    """Base for response models that may be built from ORM rows."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Constrained scalar types
# --------------------------------------------------------------------------- #


def _normalize_symbol(value: object) -> object:
    """Strip and uppercase a symbol before pattern validation."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


# Trading symbols: uppercase, optional quote separated by ``/`` or ``-``
# (e.g. ``BTCUSDT``, ``BTC/USDT``, ``ETH-PERP``). Normalization runs before the
# pattern check so lowercase input is accepted and stored uppercased.
Symbol = Annotated[
    str,
    BeforeValidator(_normalize_symbol),
    StringConstraints(min_length=2, max_length=30, pattern=r"^[A-Z0-9]+([/-][A-Z0-9]+)?$"),
]

# Confidence in [0, 1].
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

# Leverage multiplier; capped to a sane upper bound. Hard caps are additionally
# enforced by the deterministic risk engine (Slice 5).
Leverage = Annotated[Decimal, Field(gt=0, le=125)]

# Risk as a percentage of account equity, in [0, 100].
RiskPercent = Annotated[Decimal, Field(ge=0, le=100)]

# Strictly positive monetary/size value.
PositiveDecimal = Annotated[Decimal, Field(gt=0)]

# Non-negative monetary value (e.g. balances, fees).
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Timeframe(StrEnum):
    """Supported candle timeframes."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TAKE_PROFIT = "take_profit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    CLOSED = "closed"
    NEEDS_MORE_ANALYSIS = "needs_more_analysis"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    PAUSE = "pause"
    CANCEL = "cancel"
    CLOSE = "close"
    NEEDS_MORE_ANALYSIS = "needs_more_analysis"


class RiskAction(StrEnum):
    """Deterministic risk-engine verdict. Default-deny favors ``BLOCK``."""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class RiskSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskRuleId(StrEnum):
    """Stable identifiers for risk rules (reconciled with PRD/Architecture)."""

    MAX_LEVERAGE = "max_leverage"
    MAX_POSITION_SIZE = "max_position_size"
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_WEEKLY_LOSS = "max_weekly_loss"
    NO_STOP_LOSS = "no_stop_loss"
    INVALID_STOP_LOSS = "invalid_stop_loss"
    UNSUPPORTED_COIN = "unsupported_coin"
    COUNTERTREND_REDUCED_SIZE = "countertrend_reduced_size"
    VOLATILE_ALTCOIN_REDUCED_SIZE = "volatile_altcoin_reduced_size"
    EXTREME_FUNDING = "extreme_funding"
    LOW_VOLUME = "low_volume"
    WEEKEND_CONDITION = "weekend_condition"
    SLEEP_TEST = "sleep_test"
    OVERTRADING = "overtrading"
    STRONG_GREEN_DAY = "strong_green_day"
    COOLDOWN_AFTER_LOSS = "cooldown_after_loss"
    KILL_SWITCH = "kill_switch"


class StrategyId(StrEnum):
    """Trading setup types (strategy modules plus manual review)."""

    HTF_TREND_PULLBACK = "htf_trend_pullback"
    LIQUIDITY_SWEEP_REVERSAL = "liquidity_sweep_reversal"
    COUNTERTREND_SHORT_BUILD = "countertrend_short_build"
    PASSIVE_LEVEL_ORDER = "passive_level_order"
    PROFIT_PROTECTION = "profit_protection"
    GREEN_DAY_GUARD = "green_day_guard"
    MENTAL_CAPITAL_GUARD = "mental_capital_guard"
    NESTED_CONTINUATION = "nested_continuation"
    SFP = "sfp"
    MANUAL_REVIEW = "manual_review"


# Alias used by analytics and journal UX.
SetupType = StrategyId


class SetupCategory(StrEnum):
    TREND = "trend"
    REVERSAL = "reversal"
    BREAKOUT = "breakout"
    MEAN_REVERSION = "mean_reversion"
    FUNDING = "funding"
    PASSIVE = "passive"
    PROTECTION = "protection"


class ExecutionMode(StrEnum):
    """Mirror of :class:`app.core.config.ExecutionMode` for schema reuse."""

    PAPER = "paper"
    READ_ONLY = "read_only"
    TRADE = "trade"


class UserRole(StrEnum):
    ADMIN = "admin"
    TRADER = "trader"
    VIEWER = "viewer"


class MembershipRole(StrEnum):
    OWNER = "owner"
    TRADER = "trader"
    VIEWER = "viewer"


class RiskProfile(StrEnum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class ExchangeAccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class DocumentSourceType(StrEnum):
    """RAG corpus source types (reconciled with Architecture §7)."""

    TRADING_PLAYBOOK = "trading_playbook"
    PRODUCT_REQUIREMENTS = "product_requirements"
    SYSTEM_ARCHITECTURE = "system_architecture"
    RISK_POLICY = "risk_policy"
    STRATEGY_TEMPLATE = "strategy_template"
    TRADE_JOURNAL = "trade_journal"
    REVIEW_NOTE = "review_note"
    MISTAKES_DATABASE = "mistakes_database"
    GENERAL_NOTE = "general_note"


class ToolRiskLevel(StrEnum):
    READ = "read"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SENSITIVE = "sensitive"


class AuditEventType(StrEnum):
    """Canonical audit event types (Slice 11 observability)."""

    GUARDRAIL_BLOCK = "guardrail_block"
    GUARDRAIL_WARNING = "guardrail_warning"
    RISK_BLOCK = "risk_block"
    RISK_WARNING = "risk_warning"
    TRADE_PROPOSAL_CREATED = "trade_proposal_created"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECISION = "approval_decision"
    PAPER_ORDER_CREATED = "paper_order_created"
    EXECUTION_ACCOUNT_REGISTERED = "execution_account_registered"
    MANUAL_DEMO_CANCEL_REQUESTED = "manual_demo_cancel_requested"
    DEMO_LIFECYCLE_RECONCILED = "demo_lifecycle_reconciled"
    PAPER_ORDER_REJECTED = "paper_order_rejected"
    EXCHANGE_DEMO_ORDER_CREATED = "exchange_demo_order_created"
    EXCHANGE_DEMO_ORDER_FAILED = "exchange_demo_order_failed"
    EXCHANGE_DEMO_ORDER_CANCELLED = "exchange_demo_order_cancelled"
    POSITION_UPDATED = "position_updated"
    JOURNAL_ENTRY_CREATED = "journal_entry_created"
    TOOL_CALLED = "tool_called"
    TOOL_FAILED = "tool_failed"
    PROVIDER_FALLBACK_USED = "provider_fallback_used"
    KILL_SWITCH_TRIGGERED = "kill_switch_triggered"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"
    KILL_SWITCH_DEACTIVATED = "kill_switch_deactivated"
    SIGNAL_CREATED = "signal_created"
    PROVIDER_FAILURE = "provider_failure"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    AUTH_INVALID_TOKEN = "auth_invalid_token"
    AUTH_REFRESH_REUSE = "auth_refresh_reuse"
    AUTH_ACCESS_REVOKED = "auth_access_revoked"
    AUTH_EMAIL_VERIFICATION_SENT = "auth_email_verification_sent"
    AUTH_EMAIL_VERIFIED = "auth_email_verified"
    AUTH_EMAIL_VERIFICATION_FAILED = "auth_email_verification_failed"
    AUTH_PASSWORD_RESET_REQUESTED = "auth_password_reset_requested"
    AUTH_PASSWORD_RESET_COMPLETED = "auth_password_reset_completed"
    AUTH_PASSWORD_RESET_FAILED = "auth_password_reset_failed"
    AUTH_INVITE_CREATED = "auth_invite_created"
    AUTH_INVITE_ACCEPTED = "auth_invite_accepted"
    AUTH_INVITE_REVOKED = "auth_invite_revoked"
    QUOTA_WARNING = "quota_warning"
    QUOTA_BLOCK = "quota_block"
    QUOTA_UPDATED = "quota_updated"
    BILLING_METADATA_MISSING = "billing_metadata_missing"
    BILLING_CUSTOMER_CREATED = "billing_customer_created"
    BILLING_CHECKOUT_CREATED = "billing_checkout_created"
    BILLING_PORTAL_OPENED = "billing_portal_opened"
    BILLING_WEBHOOK_RECEIVED = "billing_webhook_received"
    BILLING_PLAN_CHANGED = "billing_plan_changed"
    BILLING_USAGE_EXPORTED = "billing_usage_exported"
    PROVIDER_USAGE_METADATA_MISSING = "provider_usage_metadata_missing"
    TRADINGVIEW_SIGNAL_RECEIVED = "tradingview_signal_received"
    TRADINGVIEW_SIGNAL_REJECTED = "tradingview_signal_rejected"
    TRADINGVIEW_SIGNAL_CANDIDATE_CREATED = "tradingview_signal_candidate_created"
    BLOFIN_DEMO_SYNC = "blofin_demo_sync"
    PAPER_SIGNAL_ORCHESTRATION_EVALUATED = "paper_signal_orchestration_evaluated"
    PAPER_SIGNAL_ORCHESTRATION_TRANSITION = "paper_signal_orchestration_transition"
    PAPER_SIGNAL_ORCHESTRATION_BLOCKED = "paper_signal_orchestration_blocked"
    PAPER_SIGNAL_ORCHESTRATION_CANDIDATE = "paper_signal_orchestration_candidate"
    PAPER_SIGNAL_ORCHESTRATION_PROPOSAL = "paper_signal_orchestration_proposal"
    LESSON_CANDIDATE_CREATED = "lesson_candidate_created"
    LESSON_ACCEPTED = "lesson_accepted"
    LESSON_REJECTED = "lesson_rejected"
    LESSON_ARCHIVED = "lesson_archived"
    PAPER_SCHEDULER_TICK = "paper_scheduler_tick"
    PAPER_VALIDATION_RUNTIME = "paper_validation_runtime"
    RISK_SETTINGS_UPDATED = "risk_settings_updated"
    NOTIFICATION_PREFERENCES_UPDATED = "notification_preferences_updated"
    NOTIFICATION_TEST_SENT = "notification_test_sent"
    ALERT_TELEGRAM_DELIVERY_REQUESTED = "alert_telegram_delivery_requested"
    ALERT_TELEGRAM_DELIVERY_SENT = "alert_telegram_delivery_sent"
    ALERT_TELEGRAM_DELIVERY_FAILED = "alert_telegram_delivery_failed"
    JOURNAL_TRADE_CREATED = "journal_trade_created"
    JOURNAL_TRADE_UPDATED = "journal_trade_updated"
    JOURNAL_TRADE_DELETED = "journal_trade_deleted"
    JOURNAL_TRADE_EVIDENCE_ADDED = "journal_trade_evidence_added"
    JOURNAL_TRADE_RULE_CHECKED = "journal_trade_rule_checked"
    JOURNAL_TRADE_OBSERVED = "journal_trade_observed"
    JOURNAL_TRADE_EXCURSION_REPLAYED = "journal_trade_excursion_replayed"
    JOURNAL_IMPORT_COMPLETED = "journal_import_completed"
    JOURNAL_BACKFILL_COMPLETED = "journal_backfill_completed"
    JOURNAL_TRADE_VENUE_CORRECTED = "journal_trade_venue_corrected"
    JOURNAL_LIFECYCLE_PROJECTED = "journal_lifecycle_projected"
    JOURNAL_ATTACHMENT_ADDED = "journal_attachment_added"
    JOURNAL_ATTACHMENT_DELETED = "journal_attachment_deleted"
    BACKTEST_RUN_CREATED = "backtest_run_created"
    BACKTEST_RUN_CANCELLED = "backtest_run_cancelled"
    BACKTEST_RUN_COMPLETED = "backtest_run_completed"
    BACKTEST_RUN_FAILED = "backtest_run_failed"
    BACKTEST_RUN_VERIFIED = "backtest_run_verified"
    BACKTEST_JOURNALED = "backtest_journaled"
    # Legacy values retained for existing DB rows
    PROPOSAL_CREATED = "proposal_created"
    POSITION_UPDATE = "position_update"
    JOURNAL_ENTRY = "journal_entry"
    KILL_SWITCH = "kill_switch"


# Backward-compatible alias used by earlier slices
AuditAction = AuditEventType


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
    TOOL = "tool"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    WARNING = "warning"


class AuditSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UsageStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class CostSource(StrEnum):
    """How usage cost was determined — only ``provider_reported`` is billing-grade."""

    PROVIDER_REPORTED = "provider_reported"
    TOKENIZER_ESTIMATED = "tokenizer_estimated"
    STATIC_ESTIMATED = "static_estimated"
    UNAVAILABLE = "unavailable"


class SafetyVerdict(StrEnum):
    PASS = "pass"
    FLAG = "flag"
    BLOCK = "block"


class TradeResult(StrEnum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    OPEN = "open"


class MarketType(StrEnum):
    """Market context for a user strategy card."""

    CRYPTO_PERP = "crypto_perp"
    CRYPTO_SPOT = "crypto_spot"
    FOREX = "forex"
    EQUITIES = "equities"
    COMMODITIES = "commodities"


class JournalTradeSource(StrEnum):
    """Origin of a canonical journal trade (AT-030)."""

    MANUAL = "manual"
    PAPER_EXECUTION = "paper_execution"
    MANUAL_DEMO_TEST = "manual_demo_test"
    PAPER_VALIDATION = "paper_validation"
    BACKTEST = "backtest"
    IMPORTED = "imported"
    SYSTEM = "system"


class JournalTradeStatus(StrEnum):
    """Lifecycle of a canonical journal trade (AT-030)."""

    PLANNED = "planned"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class JournalEntryMethod(StrEnum):
    """How a canonical journal trade record came into existence (AT-033).

    Orthogonal to ``JournalTradeSource``: source describes the origin of the
    underlying trade, entry_method describes who/what created the journal row
    (human-vs-system analytics readiness).
    """

    MANUAL = "manual"
    AUTO = "auto"
    IMPORT = "import"
    BACKFILL = "backfill"


class JournalImportBatchStatus(StrEnum):
    """Outcome of a bulk journal import batch (AT-033).

    Only ``committed`` batches are persisted today (commits are all-or-nothing
    in one unit of work); ``dry_run``/``failed`` exist for forward compatibility.
    """

    DRY_RUN = "dry_run"
    COMMITTED = "committed"
    FAILED = "failed"


class MarketRegime(StrEnum):
    """Coarse market-regime label recorded on a journal trade (AT-030)."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"
    QUIET = "quiet"
    UNKNOWN = "unknown"


class JournalEvidenceKind(StrEnum):
    """Kind of evidence attached to a journal trade (AT-030)."""

    SCREENSHOT = "screenshot"
    CHART = "chart"
    NOTE = "note"
    LINK = "link"
    FILE = "file"


class RuleComplianceStatus(StrEnum):
    """Compliance outcome for a single rule check on a journal trade (AT-030)."""

    FOLLOWED = "followed"
    VIOLATED = "violated"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    UNASSESSED = "unassessed"


class JournalObservationCategory(StrEnum):
    """Category of a behavioral/process observation on a journal trade (AT-030)."""

    BEHAVIORAL = "behavioral"
    EMOTIONAL = "emotional"
    EXECUTION = "execution"
    MARKET = "market"
    RISK = "risk"
    PROCESS = "process"
    MISTAKE = "mistake"
    DISCIPLINE = "discipline"
    LESSON = "lesson"


class JournalLifecycleEventType(StrEnum):
    """Projector event types. Candidate/reject/skip never create JournalTrade."""

    CANDIDATE_CONFIRMED = "candidate_confirmed"
    REJECT = "reject"
    SKIP = "skip"
    APPROVED_PLAN = "approved_plan"
    FILL = "fill"
    CLOSE = "close"
    RECONCILE = "reconcile"


class JournalReconciliationPosition(StrEnum):
    """Authoritative venue position asserted by a RECONCILE event."""

    POSITION_OPEN = "POSITION_OPEN"
    CLOSED = "CLOSED"


class StrategyValidationStatus(StrEnum):
    """Validation lifecycle for a user strategy card."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    VALIDATED = "validated"
    RESTRICTED = "restricted"
    RETIRED = "retired"
    NEEDS_REVISION = "needs_revision"
    DEPRECATED = "deprecated"


class BacktestStatus(StrEnum):
    """Placeholder backtest lifecycle."""

    NOT_RUN = "not_run"
    NOT_STARTED = "not_started"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestRunStatus(StrEnum):
    """Backtest run lifecycle (Slice 34 / AT-034)."""

    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class BacktestSplitLabel(StrEnum):
    """Walk-forward / holdout split label for a simulated trade (AT-034)."""

    IN_SAMPLE = "in_sample"
    OUT_OF_SAMPLE = "out_of_sample"


class PaperValidationStatus(StrEnum):
    """Paper validation lifecycle."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"


class BacktestRecommendation(StrEnum):
    """Conservative strategy recommendation after backtest v1."""

    NEEDS_STRUCTURED_RULES = "needs_structured_rules"
    NEEDS_MORE_SAMPLE = "needs_more_sample_size"
    NEEDS_REVIEW = "needs_review"
    BACKTESTED = "backtested"
    PAPER_ELIGIBLE = "paper_eligible"
    RESTRICTED = "restricted"
    UNRELIABLE_DATA = "unreliable_data"


class PaperValidationRecommendation(StrEnum):
    """Paper validation outcome recommendation."""

    CONTINUE = "continue"
    IMPROVE = "improve"
    RESTRICT = "restrict"
    RETIRE = "retire"
    INSUFFICIENT_DATA = "insufficient_data"
    PAPER_VALIDATED = "paper_validated"


class PaperValidationRuntimeMode(StrEnum):
    """Paper validation runtime mode (Slice 39 — paper only)."""

    SCAN_ONLY = "scan_only"
    AUTO_PAPER = "auto_paper"


class PaperSignalStatus(StrEnum):
    """Paper signal lifecycle."""

    DETECTED = "detected"
    NOT_TESTABLE = "not_testable"
    BLOCKED_FILTER = "blocked_filter"
    CONSUMED = "consumed"


class PaperTradeStatus(StrEnum):
    """Simulated paper trade status."""

    PROPOSED = "proposed"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class PaperRuntimeCycleStatus(StrEnum):
    """Runtime scan/tick cycle outcome (Slice 40)."""

    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class PaperRuntimeCycleMode(StrEnum):
    """Runtime cycle mode (Slice 40)."""

    SCAN = "scan"
    TICK = "tick"
    SCHEDULER_TICK = "scheduler_tick"
    MARKET_WATCHER_BRIDGE = "market_watcher_bridge"


class PaperAlertType(StrEnum):
    """Paper validation alert types (Slice 40 — storage only, no delivery)."""

    SETUP_SIGNAL_DETECTED = "setup_signal_detected"
    PAPER_TRADE_OPENED = "paper_trade_opened"
    PAPER_TRADE_CLOSED = "paper_trade_closed"
    STOP_HIT = "stop_hit"
    TP_HIT = "tp_hit"
    RUNNER_EXIT = "runner_exit"
    STRATEGY_BLOCKED = "strategy_blocked"
    DATA_STALE = "data_stale"
    PROMOTION_STATUS_CHANGED = "promotion_status_changed"
    PAPER_VALIDATION_RESTRICTED = "paper_validation_restricted"
    OVERTRADING_WARNING = "overtrading_warning"
    DAILY_LOSS_LOCK_WARNING = "daily_loss_lock_warning"
    RESEARCH_VALIDATION_PROMOTION = "research_validation_promotion"
    TRADINGVIEW_SIGNAL = "tradingview_signal"


class PromotionSource(StrEnum):
    """Origin of a paper validation candidate (AT-035 / AT-037)."""

    ALERT_DRAFT = "alert_draft"
    RESEARCH_VALIDATION = "research_validation"
    TRADINGVIEW_SIGNAL = "tradingview_signal"


class TradingViewSignalStatus(StrEnum):
    """Lifecycle status for inbound TradingView signals (AT-037)."""

    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    CANDIDATE_CREATED = "candidate_created"


class BloFinSyncHealthStatus(StrEnum):
    """Health of a BloFin demo read-only sync snapshot (AT-037)."""

    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class PaperSignalOrchestrationMode(StrEnum):
    """Human-review modes for paper-signal orchestration (AT-038).

    No autonomous live mode exists. All modes remain paper-only.
    """

    OBSERVE_ONLY = "observe_only"
    CANDIDATE_ONLY = "candidate_only"
    APPROVAL_REQUIRED = "approval_required"


class PaperSignalOrchestrationStatus(StrEnum):
    """Decision state machine for paper-signal orchestration (AT-038)."""

    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    AWAITING_REVIEW = "awaiting_review"
    PAPER_CANDIDATE_CREATED = "paper_candidate_created"
    PAPER_PROPOSAL_CREATED = "paper_proposal_created"
    EXPIRED = "expired"
    REJECTED = "rejected"


class PaperAlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertDeliveryStatus(StrEnum):
    """External alert delivery lifecycle (Slice 41)."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"
    DISABLED = "disabled"


class AlertDeliveryChannel(StrEnum):
    """Alert delivery channel (Slice 41)."""

    IN_APP = "in_app"
    WEBHOOK = "webhook"
    TELEGRAM = "telegram"
    EMAIL = "email"
    PUSH = "push"


class NotificationDigestMode(StrEnum):
    """External notification delivery cadence (Slice 46)."""

    IMMEDIATE = "immediate"
    DAILY_DIGEST = "daily_digest"
    DISABLED = "disabled"


class MarketWatcherObservationStatus(StrEnum):
    """Read-only market watcher observation freshness (Slice 41)."""

    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class MarketWatcherBridgeDecisionType(StrEnum):
    """Bridge decision outcomes (Slice 42 — paper scan bridge only)."""

    TRIGGERED_SCAN = "triggered_scan"
    SKIPPED_STALE_DATA = "skipped_stale_data"
    SKIPPED_BLOCKED_STRATEGY = "skipped_blocked_strategy"
    SKIPPED_NO_MATCHING_RUN = "skipped_no_matching_run"
    SKIPPED_DISABLED = "skipped_disabled"
    FAILED = "failed"


class PaperAlertSource(StrEnum):
    """Origin of a paper validation alert (Slice 42)."""

    PAPER_VALIDATION_RUNTIME = "paper_validation_runtime"
    MARKET_WATCHER = "market_watcher"
    MARKET_WATCHER_BRIDGE = "market_watcher_bridge"
    MANUAL_ACTION = "manual_action"


class SetupAlertReviewStatus(StrEnum):
    """Review lifecycle for scanner-created setup alerts (Slice 77)."""

    UNREVIEWED = "unreviewed"
    WATCHING = "watching"
    IGNORED = "ignored"
    IMPORTANT = "important"


class PaperValidationDraftStatus(StrEnum):
    """Non-executable paper validation draft lifecycle (Slice 78)."""

    DRAFT = "draft"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class PaperValidationDraftRiskMode(StrEnum):
    """Risk posture captured on a paper validation draft (Slice 78)."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class PaperValidationDraftPrepStatus(StrEnum):
    """Prep lifecycle for paper validation drafts (Slice 79 — planning only)."""

    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_VALIDATION = "ready_for_validation"
    ARCHIVED = "archived"


class PaperValidationCandidateStatus(StrEnum):
    """Non-executable paper validation candidate queue lifecycle (Slice 80)."""

    QUEUED = "queued"
    REVIEWING = "reviewing"
    ARCHIVED = "archived"


class PaperValidationRunPlanStatus(StrEnum):
    """Non-executable paper validation run plan lifecycle (Slice 81 — planning only)."""

    PLANNED = "planned"
    NEEDS_REVISION = "needs_revision"
    ARCHIVED = "archived"


class PaperValidationRunSessionStatus(StrEnum):
    """Manual paper validation run session lifecycle (Slice 82 — record only, no engine)."""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PaperValidationObservationKind(StrEnum):
    """Manual observation kinds for a paper validation run session (Slice 83)."""

    APPROACHED_TRIGGER = "approached_trigger"
    HIT_TRIGGER = "hit_trigger"
    HIT_INVALIDATION = "hit_invalidation"
    MISSED_ENTRY = "missed_entry"
    PRICE_MOVED_WITHOUT_ENTRY = "price_moved_without_entry"
    PRICE_UPDATE = "price_update"
    GENERAL_NOTE = "general_note"


class PaperValidationOutcome(StrEnum):
    """Final outcome classification for a paper validation run session (Slice 83)."""

    SUCCESS = "success"
    FAILURE = "failure"
    INVALIDATED = "invalidated"
    MISSED_ENTRY = "missed_entry"
    NO_TRADE = "no_trade"
    INCONCLUSIVE = "inconclusive"


class PaperValidationCriteriaMet(StrEnum):
    """Whether planned success/failure criteria were met (Slice 83)."""

    MET = "met"
    NOT_MET = "not_met"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class PaperValidationEntryAssessment(StrEnum):
    """Entry discipline assessment for a paper validation run session (Slice 83)."""

    ENTERED_AS_PLANNED = "entered_as_planned"
    MISSED_ENTRY = "missed_entry"
    PRICE_MOVED_WITHOUT_ENTRY = "price_moved_without_entry"
    NO_ENTRY = "no_entry"


class PaperValidationDisciplineAssessment(StrEnum):
    """Discipline assessment for a paper validation run session (Slice 83)."""

    DISCIPLINED = "disciplined"
    SHOULD_HAVE_WAITED = "should_have_waited"
    SHOULD_HAVE_ENTERED = "should_have_entered"
    SHOULD_HAVE_AVOIDED = "should_have_avoided"


class PaperObservabilityEventType(StrEnum):
    """Structured paper validation observability events (Slice 40)."""

    SCHEDULER_TICK_STARTED = "scheduler_tick_started"
    SCHEDULER_TICK_COMPLETED = "scheduler_tick_completed"
    SCAN_SKIPPED = "scan_skipped"
    SIGNAL_CREATED = "signal_created"
    PAPER_TRADE_OPENED = "paper_trade_opened"
    PAPER_TRADE_CLOSED = "paper_trade_closed"
    METRICS_UPDATED = "metrics_updated"
    PROMOTION_STATUS_CHANGED = "promotion_status_changed"
    STRATEGY_BLOCKED = "strategy_blocked"
    DATA_STALE = "data_stale"
    PROVIDER_FALLBACK_USED = "provider_fallback_used"
    RUNTIME_ERROR = "runtime_error"
    ALERT_DELIVERY_FAILED = "alert_delivery_failed"
    ALERT_DELIVERY_SUCCEEDED = "alert_delivery_succeeded"
    MARKET_WATCHER_SCAN = "market_watcher_scan"
    MARKET_WATCHER_BRIDGE_TICK = "market_watcher_bridge_tick"
    MARKET_WATCHER_BRIDGE_SCAN_TRIGGERED = "market_watcher_bridge_scan_triggered"


class PaperEligibilityStatus(StrEnum):
    """Unified paper promotion gate status (Slice 38 — paper only, no live trading)."""

    NEEDS_STRUCTURE = "needs_structure"
    NEEDS_BACKTEST = "needs_backtest"
    NEEDS_MORE_SAMPLE = "needs_more_sample"
    NEEDS_LESSON_REVIEW = "needs_lesson_review"
    PAPER_ELIGIBLE = "paper_eligible"
    PAPER_VALIDATION_RUNNING = "paper_validation_running"
    PAPER_VALIDATED = "paper_validated"
    RESTRICTED = "restricted"


class ManualLevelType(StrEnum):
    """Manual chart level types (Slice 33)."""

    SUPPORT = "support"
    RESISTANCE = "resistance"
    FIBONACCI = "fibonacci"
    TREND_LINE = "trend_line"
    VWAP = "vwap"
    LIQUIDITY_ZONE = "liquidity_zone"
    PREVIOUS_HIGH = "previous_high"
    PREVIOUS_LOW = "previous_low"
    USER_NOTE = "user_note"


class PreTradeRecommendation(StrEnum):
    """Deterministic pre-trade recommendation bands."""

    NO_TRADE = "no_trade"
    WATCH = "watch"
    SMALL_PROBE = "small_probe"
    NORMAL_SIZE = "normal_size"
    HIGH_CONVICTION = "high_conviction"


class LossAcceptanceStatus(StrEnum):
    """Trader confirmation of planned loss."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RuleEngineSource(StrEnum):
    """Which rule engine powered a backtest run (Slice 36)."""

    STRUCTURED = "structured"
    ADAPTER = "adapter"
    DEFAULT_SETUP = "default_setup"
    UNSUPPORTED = "unsupported"
    CANONICAL_SETUP = "canonical_setup"


class EntryTriggerType(StrEnum):
    """Machine-testable entry trigger types (Slice 36)."""

    EMA_PULLBACK = "ema_pullback"
    BREAKOUT = "breakout"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    RECLAIM = "reclaim"
    FAILED_BREAKOUT = "failed_breakout"
    RSI_THRESHOLD = "rsi_threshold"
    VOLUME_CONFIRMATION = "volume_confirmation"
    TREND_ALIGNMENT = "trend_alignment"


class RuleConditionOperator(StrEnum):
    """Comparison operator for structured rule conditions."""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class ExitRuleType(StrEnum):
    """Machine-testable exit rule blocks."""

    FIXED_STOP = "fixed_stop"
    ATR_STOP = "atr_stop"
    SWING_STOP = "swing_stop"
    TP_MULTIPLE = "tp_multiple"
    TP_PRICE_LEVELS = "tp_price_levels"
    PARTIAL_TP = "partial_tp"
    RUNNER_STRUCTURE_BREAK = "runner_structure_break"


class NoTradeRuleType(StrEnum):
    """Machine-testable no-trade filters."""

    LOW_VOLUME = "low_volume"
    HIGH_FUNDING = "high_funding"
    WEEKEND_CHOP = "weekend_chop"
    DAILY_LOSS_LOCK = "daily_loss_lock"
    GREEN_DAY_PROTECTION = "green_day_protection"
    HTF_CONFLICT = "htf_conflict"


class TestabilityBand(StrEnum):
    """Strategy testability score band."""

    VAGUE = "vague"
    PARTIAL = "partial"
    MACHINE_TESTABLE = "machine_testable"


class LessonSourceType(StrEnum):
    """Origin of a lesson candidate (Slice 37)."""

    HUMAN_VS_SYSTEM = "human_vs_system"
    RUNNER_ANALYSIS = "runner_analysis"
    STOP_LOSS_REFUSAL = "stop_loss_refusal"
    JOURNAL = "journal"
    BACKTEST = "backtest"
    AGENT = "agent"
    COACHING = "coaching"


class LessonSeverity(StrEnum):
    """Severity of a discipline mistake."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LessonCandidateStatus(StrEnum):
    """Lesson candidate review lifecycle (Slice 37)."""

    PENDING_REVIEW = "pending_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    # Legacy aliases (Slice 36)
    CANDIDATE = "pending_review"
    NEEDS_REVIEW = "pending_review"


class AnalysisConfidence(StrEnum):
    """Confidence level for discipline estimates."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyChangeSource(StrEnum):
    """Provenance for an immutable strategy-version fork (Phase 3)."""

    CREATE = "create"
    CARD_UPDATE = "card_update"
    STRUCTURED_RULES = "structured_rules"
    PATTERN_SPEC = "pattern_spec"
    LESSON_ATTACHMENT = "lesson_attachment"
    ROLLBACK_SELECT = "rollback_select"
    MIGRATION = "migration"
    LEGACY = "legacy"
    CONVERSATION_CONFIRM = "conversation_confirm"


class ConversationStatus(StrEnum):
    """Lifecycle of a tenant-scoped strategy conversation transcript."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ConversationMessageRole(StrEnum):
    """Persisted chat roles. Transcripts are not strategy authority."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class StrategyProposalStatus(StrEnum):
    """Structured strategy proposal preview. Drafts never mutate versions."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class StrategyLifecycleState(StrEnum):
    """Append-only lifecycle projection. Not Pattern Card content."""

    OBSERVATION = "observation"
    HYPOTHESIS = "hypothesis"
    TESTING = "testing"
    PAPER_ACTIVE = "paper_active"
    PAUSED = "paused"
    DRAFT = "draft"
    STRUCTURED = "structured"
    HISTORICALLY_VALIDATED = "historically_validated"
    PAPER_VALIDATING = "paper_validating"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    ACTIVE = "active"
    RETIRED = "retired"


class SetupCompileStatus(StrEnum):
    """Compiler outcome. Ambiguous legacy mappings fail closed."""

    EXECUTABLE = "executable"
    NON_EXECUTABLE = "non_executable"
    COMPATIBILITY_ONLY = "compatibility_only"


class SetupMigrationMode(StrEnum):
    DRY_RUN = "dry_run"
    APPLY = "apply"
