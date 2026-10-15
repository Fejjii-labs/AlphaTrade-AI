"""Native account activity contract; decimals are original venue strings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NativeActivityFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["order", "fill"]
    native_id: str
    order_id: str
    trade_id: str | None = None
    client_order_id: str | None = None
    instrument: str
    side: str
    position_side: str
    occurred_at_ms: str
    created_at_ms: str | None = None
    updated_at_ms: str | None = None
    quantity: str
    quantity_unit: Literal["contracts"] = "contracts"
    filled_quantity: str | None = None
    price: str | None = None
    average_price: str | None = None
    state: str | None = None
    order_type: str | None = None
    reduce_only: str | None = None
    fee: str | None = None
    fee_currency: str | None = None
    realized_pnl: str | None = None
    funding: None = None


class ActivityItem(NativeActivityFact):
    origin: Literal["native", "alphatrade_matched"]
    command_id: UUID | None = None
    strategy_id: UUID | None = None
    contract_multiplier: str | None = None
    contract_type: str | None = None
    base_currency: str | None = None
    settlement_currency: str | None = None
    metadata_observed_at: datetime | None = None


class ActivityCoverage(BaseModel):
    kind: Literal["order", "fill"]
    selection: Literal["cursor_sweep", "time_window"]
    window_begin_ms: str | None = None
    window_end_ms: str | None = None
    native_cursor: str | None = None
    window_complete: bool = False
    covered_begin_ms: str | None = None
    covered_end_ms: str | None = None
    gap_detected: bool = False
    last_successful_sync: datetime | None = None
    last_attempt_at: datetime | None = None
    last_error_code: str | None = None
    next_retry_at: datetime | None = None


class ActivityPage(BaseModel):
    schema_version: Literal["BloFinActivityV1"] = "BloFinActivityV1"
    organization_id: UUID
    venue: Literal["BLOFIN"] = "BLOFIN"
    environment: Literal["demo"] = "demo"
    account_uid: str
    identity_status: Literal["verified", "unverified"]
    identity_error_code: str | None = None
    identity_verified_at: datetime | None = None
    items: list[ActivityItem]
    next_cursor: str | None = None
    coverage: list[ActivityCoverage]
    freshness: Literal["fresh", "stale", "never_synced", "unverified"]
    partial_coverage: Literal[True] = True
    limitations: list[str]
    generated_at: datetime
