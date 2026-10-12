"""Phase 1 slices 7-10 execution-claim, receipt, reservation and effect contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import TradeResult
from app.schemas.trade_plan import (
    CanonicalDecimal,
    NonNegativeCanonicalDecimal,
    PlanOperation,
    PositiveCanonicalDecimal,
)

SUBMIT_ENTRY_NAMESPACE = "alphatrade/submit-entry/v1"
EXECUTION_POLICY_PROTOCOL_VERSION = "phase1-execution-protocol-v1"
CLIENT_ORDER_ID_NAMESPACE = "alphatrade/venue-client-order/v1"


class ExecutionCommandOutcome(StrEnum):
    ALLOW = "ALLOW"
    BLOCKED = "BLOCKED"


class ExecutionReceiptState(StrEnum):
    BLOCKED = "BLOCKED"
    SUBMITTING = "SUBMITTING"
    BLOCKED_BEFORE_DISPATCH = "BLOCKED_BEFORE_DISPATCH"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED_CANCELLED = "PARTIALLY_FILLED_CANCELLED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ABSENCE_PENDING = "ABSENCE_PENDING"
    ABSENCE_PROVEN = "ABSENCE_PROVEN"
    RESUBMIT_AUTHORIZED = "RESUBMIT_AUTHORIZED"
    OPERATOR_HOLD = "OPERATOR_HOLD"


class VenueSubmitEffectState(StrEnum):
    CREATED = "CREATED"
    LEASED = "LEASED"
    DISPATCH_AUTHORIZED = "DISPATCH_AUTHORIZED"
    PROVEN_UNSENT = "PROVEN_UNSENT"
    SEND_ATTEMPTED = "SEND_ATTEMPTED"
    SEND_AMBIGUOUS = "SEND_AMBIGUOUS"
    RECONCILING = "RECONCILING"


class RiskReservationReleaseState(StrEnum):
    CHARGED = "CHARGED"
    PARTIALLY_RELEASED = "PARTIALLY_RELEASED"
    RELEASED = "RELEASED"


class RiskReservationReleaseReason(StrEnum):
    UNUSED_REMAINDER_AFTER_REJECTION = "UNUSED_REMAINDER_AFTER_REJECTION"
    UNUSED_REMAINDER_AFTER_CANCELLATION = "UNUSED_REMAINDER_AFTER_CANCELLATION"
    UNUSED_REMAINDER_AFTER_EXPIRY = "UNUSED_REMAINDER_AFTER_EXPIRY"
    UNUSED_REMAINDER_AFTER_ABSENCE = "UNUSED_REMAINDER_AFTER_ABSENCE"
    UNUSED_REMAINDER_AFTER_PROVEN_UNSENT = "UNUSED_REMAINDER_AFTER_PROVEN_UNSENT"
    FILL_CONVERSION = "FILL_CONVERSION"
    VERIFIED_DEMO_EXIT = "VERIFIED_DEMO_EXIT"


class ExecutionReconciliationStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    OPERATOR_HOLD = "OPERATOR_HOLD"


class VenueSendDisposition(StrEnum):
    NOT_SENT = "NOT_SENT"
    PROVEN_UNSENT = "PROVEN_UNSENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


class ExecutePaperPlanRequest(BaseModel):
    """Transport request for EXECUTE_PAPER_PLAN. Executable fields are never accepted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, frozen=True)

    organization_id: UUID
    user_id: UUID
    account_id: UUID
    authorization_id: UUID
    revision_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    requested_action: Literal["execute_paper_plan"] = "execute_paper_plan"
    correlation_id: UUID | None = None


class ExecutePaperPlanHttpRequest(BaseModel):
    """HTTP body for canonical/paper EXECUTE_PAPER_PLAN. Executable fields are forbidden."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, frozen=True)

    account_id: UUID
    authorization_id: UUID
    revision_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    correlation_id: UUID | None = None


class ClosePaperPlanRequest(BaseModel):
    """Close one filled canonical paper plan. Prices are explicit; no market I/O."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    user_id: UUID
    account_id: UUID
    revision_id: UUID
    command_id: UUID
    exit_price: PositiveCanonicalDecimal
    fees: NonNegativeCanonicalDecimal = Decimal("0")
    funding: NonNegativeCanonicalDecimal = Decimal("0")
    slippage: NonNegativeCanonicalDecimal = Decimal("0")
    exit_reason: str = Field(min_length=1, max_length=60)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)


class ClosePaperPlanHttpRequest(BaseModel):
    """HTTP body for a paper close. Executable venue fields besides the exit are forbidden."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, frozen=True)

    account_id: UUID
    revision_id: UUID
    command_id: UUID
    exit_price: PositiveCanonicalDecimal
    fees: NonNegativeCanonicalDecimal = Decimal("0")
    funding: NonNegativeCanonicalDecimal = Decimal("0")
    slippage: NonNegativeCanonicalDecimal = Decimal("0")
    exit_reason: str = Field(min_length=1, max_length=60)
    occurred_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=128)


class ClosePaperPlanResult(BaseModel):
    """Recorded paper outcome. This is not an exchange fill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    replayed: bool
    live_executable: Literal[False] = False
    command_id: UUID
    journal_trade_id: UUID
    candidate_id: UUID | None = None
    strategy_version_id: UUID | None = None
    symbol: str
    timeframe: str
    entry_price: CanonicalDecimal
    exit_price: CanonicalDecimal
    exit_reason: str
    fees: CanonicalDecimal
    gross_pnl: CanonicalDecimal
    net_pnl: CanonicalDecimal
    result: TradeResult
    thesis: str | None = None


class SemanticQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: CanonicalDecimal
    unit: str = Field(min_length=1, max_length=32)


class ExecutionReceiptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    receipt_id: UUID
    command_id: UUID
    operation: PlanOperation
    authorization_id: UUID | None
    organization_id: UUID
    user_id: UUID
    account_id: UUID
    created_at: datetime
    outcome: ExecutionCommandOutcome
    blocked_reason_code: str | None = None


class ExecutionProjectionView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    receipt_id: UUID
    version: int
    state: ExecutionReceiptState
    filled_quantity: NonNegativeCanonicalDecimal
    remaining_quantity: NonNegativeCanonicalDecimal
    quantity_unit: str
    weighted_price: CanonicalDecimal | None
    fees: CanonicalDecimal | None
    funding: CanonicalDecimal | None
    position_id: UUID | None
    reconciliation_status: ExecutionReconciliationStatus
    event_watermark: int


class VenueSubmitEffectView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    effect_id: UUID
    command_id: UUID
    receipt_id: UUID
    client_order_id: str
    state: VenueSubmitEffectState
    fencing_token: int
    attempt: int
    lease_owner: str | None
    safety_epoch: int
    dispatch_safety_epoch: int | None
    uncertainty: bool
    reconciliation_disposition: str | None


class RiskReservationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    reservation_id: UUID
    command_id: UUID
    receipt_id: UUID
    pending_order_exposure: NonNegativeCanonicalDecimal
    remaining_reserved_notional: NonNegativeCanonicalDecimal
    daily_loss_allocation: NonNegativeCanonicalDecimal
    daily_trade_allocation: int
    safety_epoch: int
    release_state: RiskReservationReleaseState
    release_reason: RiskReservationReleaseReason | None
    exposure_unit: str


class CommittedDispatchAuthorization(BaseModel):
    """Durable Barrier 3 snapshot loaded from committed storage only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    effect_id: UUID
    client_order_id: str
    state: VenueSubmitEffectState
    lease_owner: str | None
    fencing_token: int
    dispatch_fencing_token: int | None
    dispatch_safety_epoch: int | None
    attempt: int


class UniqueFillResult(BaseModel):
    """Result of applying one authoritative fill fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    replayed: bool
    fill_id: UUID
    receipt_id: UUID
    source_fill_identity: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    filled_quantity: NonNegativeCanonicalDecimal
    remaining_quantity: NonNegativeCanonicalDecimal
    weighted_price: CanonicalDecimal | None


class ExecutePaperPlanResult(BaseModel):
    """Stable claim result. Replay returns the same identities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    replayed: bool
    outcome: ExecutionCommandOutcome
    command_id: UUID
    canonical_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: ExecutionReceiptView
    projection: ExecutionProjectionView
    effect: VenueSubmitEffectView | None
    reservation: RiskReservationView | None
    client_order_id: str | None
    blocked_reason_code: str | None = None
