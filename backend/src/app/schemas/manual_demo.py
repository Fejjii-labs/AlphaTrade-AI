"""Explicit owner confirmation of a hash-bound manual demo market order."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.trade_plan import CanonicalModel, EntrySide, PositiveCanonicalDecimal


class ManualDemoPreviewRequest(CanonicalModel):
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    side: EntrySide
    quantity: PositiveCanonicalDecimal  # Venue contracts, never base coins.
    stop: PositiveCanonicalDecimal
    target: PositiveCanonicalDecimal
    order_type: Literal["MARKET"] = "MARKET"

    @field_validator("quantity", "stop", "target", mode="before")
    @classmethod
    def _bounded_decimal(cls, value: object) -> object:
        if len(str(value)) > 64:
            raise ValueError("Manual demo decimal exceeds the input budget.")
        try:
            parsed = Decimal(str(value))
        except Exception as exc:
            raise ValueError("A finite base-10 decimal is required.") from exc
        if (
            not parsed.is_finite()
            or len(parsed.as_tuple().digits) > 24
            or abs(int(parsed.as_tuple().exponent)) > 18
        ):
            raise ValueError("Manual demo decimal exceeds the precision budget.")
        return value


class ManualDemoConfirmation(CanonicalModel):
    revision_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]
    label: Literal["manual demo test"]


class ManualDemoInstrument(CanonicalModel):
    account_id: UUID
    instrument: str
    quantity_unit: Literal["CONTRACTS"] = "CONTRACTS"
    base_currency: Literal["BTC"] = "BTC"
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    lot_increment: Decimal
    tick_size: Decimal
    contract_multiplier: Decimal
    minimum_notional: Decimal = Decimal("5")
    reference_price: Decimal
    observed_at: datetime


class ManualDemoPreview(CanonicalModel):
    origin: Literal["manual demo test"] = "manual demo test"
    account_id: UUID
    revision_id: UUID
    content_hash: str
    instrument: str
    side: EntrySide
    order_type: Literal["MARKET"] = "MARKET"
    quantity: Decimal
    quantity_unit: Literal["CONTRACTS"] = "CONTRACTS"
    base_quantity: Decimal
    reference_price: Decimal
    limit_price: None = None
    entry_lower: Decimal
    entry_upper: Decimal
    stop: Decimal
    target: Decimal
    maximum_planned_loss: Decimal
    gross_reward_risk: Decimal
    valid_until: datetime
    warnings: tuple[str, ...] = (
        "Manual demo connectivity test: strategy qualification and minimum 1R do not apply. "
        "Excluded from strategy performance statistics.",
        "Demo market entry; final fill price is unknown until venue reconciliation.",
        "Planned loss includes fee/slippage allowances; gaps may exceed it.",
        "Excluded from strategy validation. No strategy or execution flags are changed.",
    )


class DemoReconciliationDiagnostic(CanonicalModel):
    stage: Literal[
        "order_lookup",
        "order_parse",
        "fill_lookup",
        "fill_parse",
        "protection_lookup",
        "protection_parse",
        "account_lookup",
        "exit_lookup",
        "exit_parse",
    ]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    error_type: Literal[
        "InvalidVenueData",
        "UnexpectedError",
        "ExchangeAuthError",
        "ExchangeRateLimitError",
        "ExchangeUnavailableError",
        "ExchangeRequestError",
    ]
    endpoint_name: Literal[
        "GET /api/v1/trade/order-detail",
        "GET /api/v1/trade/fills-history",
        "GET /api/v1/trade/orders-tpsl-pending",
        "GET /api/v1/account/positions",
        "GET /api/v1/trade/orders-pending",
        "GET /api/v1/trade/orders-history",
        "GET /api/v1/trade/orders-tpsl-history",
    ]
    http_status: int | None = Field(default=None, ge=100, le=599)
    venue_error_code: str | None = Field(default=None, pattern=r"^[0-9]{1,16}$")
    field_name: (
        Literal[
            "size",
            "filledSize",
            "fee",
            "fillSize",
            "fillPrice",
            "slOrderPrice",
            "tpOrderPrice",
            "slTriggerPrice",
            "tpTriggerPrice",
            "positions",
            "fillPnl",
            "actualSize",
        ]
        | None
    ) = None


class ManualDemoProtectionHistory(CanonicalModel):
    tpsl_id: str
    state: Literal["live", "effective", "canceled", "order_failed"]


class ManualDemoExitFill(CanonicalModel):
    identity: str
    order_id: str
    trade_id: str
    quantity: Decimal
    price: Decimal
    occurred_at: datetime
    fee: Decimal | None
    fee_currency: str | None = None
    fillPnl: Decimal | None  # noqa: N815 — native field; excludes invented net PnL
    category: str


class ManualDemoStatus(CanonicalModel):
    origin: Literal["manual demo test"] = "manual demo test"
    revision_id: UUID
    command_id: UUID
    client_order_id: str
    venue_order_id: str | None = None
    protection_order_ids: tuple[str, ...] = ()
    status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    average_fill_price: Decimal | None
    fees: Decimal | None
    protection: str
    journal_trade_id: UUID | None
    missing_evidence: tuple[str, ...]
    reconciliation_diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
    protection_diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
    historical_protection: str = "unverified"
    triggered_protection: str = "unverified"
    entry_fees: Decimal | None = None
    gross_pnl: Decimal | None = None
    funding: Decimal | None = None
    net_pnl: Decimal | None = None
    fee_convention: Literal["positive_cost_negative_rebate"] | None = None
    execution_status: str = "submission_uncertain"
    position_status: str = "unknown"
    account_status: str = "unknown"
    protection_history: tuple[ManualDemoProtectionHistory, ...] = ()
    exit_fills: tuple[ManualDemoExitFill, ...] = ()
    observed_at: datetime | None = None
    reconciliation_freshness: str = "never_observed"
    exit_quantity: Decimal = Decimal("0")
    exit_price: Decimal | None = None
    exit_fees: Decimal | None = None
    venue_reported_fill_pnl: Decimal | None = None
    recovery_status: str = "unresolved"
    recovery_reason: str | None = None
    account_claim_command_ids: tuple[UUID, ...] = ()
    reservation_status: str = "none"
    can_reconcile: bool = False
    can_cancel: bool = False
    can_resolve: bool = False


class ManualDemoHistoryFilter(CanonicalModel):
    command_id: UUID | None = None
    account_id: UUID | None = None
    symbol: str | None = None
    side: EntrySide | None = None
    requested_quantity: PositiveCanonicalDecimal | None = None
    since: datetime | None = None
    until: datetime | None = None
    submission_status: Literal["attempt", "blocked", "submitted", "uncertain", "filled"] = "attempt"
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)

    @model_validator(mode="after")
    def valid_time_range(self) -> "ManualDemoHistoryFilter":
        for value in (self.since, self.until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Submission-time filters require an explicit timezone.")
        if self.since and self.until and self.since > self.until:
            raise ValueError("Submission-time range is reversed.")
        return self


class ManualDemoAttempt(CanonicalModel):
    command_id: UUID
    account_id: UUID
    account_name: str
    venue: Literal["BLOFIN_DEMO"] = "BLOFIN_DEMO"
    origin: Literal["manual_demo_test"] = "manual_demo_test"
    attempted_at: datetime
    submitted_at: datetime | None
    symbol: str
    side: EntrySide
    requested_contracts: Decimal
    base_quantity: Decimal
    stop: Decimal
    target: Decimal
    content_hash: str
    submission_outcome: Literal["ALLOW", "BLOCKED"]
    blocked_reason: str | None
    detail_url: str
    evidence: ManualDemoStatus


class ManualDemoHistory(CanonicalModel):
    items: tuple[ManualDemoAttempt, ...]
    total: int
    limit: int
    offset: int


class ManualDemoCancelRequest(CanonicalModel):
    confirm: Literal[True]
