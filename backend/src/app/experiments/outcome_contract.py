"""Module-level native outcome contract; shared HTTP generation belongs to integration."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.experiments.native_lineage import NativeExperimentSourceProof
from app.market_contracts.models import CanonicalDecimal, CanonicalModel
from app.schemas.experiments import ExperimentSource


class NativeFactProvenance(CanonicalModel):
    kind: Literal["order", "fill"]
    native_id: str
    content_hash: str
    event_time: AwareDatetime
    first_observed_at: AwareDatetime
    instrument: str
    quantity: CanonicalDecimal
    quantity_unit: Literal["contracts"] = "contracts"
    price: CanonicalDecimal | None
    fee: CanonicalDecimal | None
    fee_currency: str | None
    realized_pnl: CanonicalDecimal | None


class NativeOutcome(CanonicalModel):
    contract_version: Literal["blofin-experiment-outcome/v1"] = "blofin-experiment-outcome/v1"
    instrument: str | None = None
    entry_binding_hash: str | None = None
    exit_lineage_hash: str | None = None
    native_exit_order_ids: tuple[str, ...] = ()
    monetary_methodology_version: str | None = None
    provider: Literal["blofin_native_activity"] = "blofin_native_activity"
    venue: Literal["BLOFIN"] = "BLOFIN"
    environment: Literal["demo"] = "demo"
    source: Literal[ExperimentSource.BLOFIN_DEMO] = ExperimentSource.BLOFIN_DEMO
    organization_id: UUID
    execution_account_id: UUID
    native_uid: str
    version_id: UUID
    strategy_version_id: UUID
    variant_key: str
    source_record_id: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    proof: NativeExperimentSourceProof | None = None
    facts: tuple[NativeFactProvenance, ...] = ()
    evaluated_at: AwareDatetime
    identity_verified_at: AwareDatetime | None = None
    coverage: Literal["reconciled_lineage_only", "incomplete"] = "incomplete"
    account_history_complete: Literal[False] = False
    freshness: Literal["fresh", "stale", "unavailable"] = "unavailable"
    methodology_version: Literal["blofin-native-reconciliation/v1"] = (
        "blofin-native-reconciliation/v1"
    )
    quantity_unit: Literal["contracts"] = "contracts"
    quantity: CanonicalDecimal | None = None
    currency: str | None = None
    reported_realized_pnl: CanonicalDecimal | None = None
    fee_cost: CanonicalDecimal | None = None
    realized_pnl_after_fees_excluding_funding: CanonicalDecimal | None = None
    funding: None = None
    rounding: Literal["18_decimal_places_half_even"] = "18_decimal_places_half_even"
    limitations: tuple[str, ...] = (
        "funding_not_collected",
        "account_history_retention_unverified",
        "current_instrument_metadata_not_historical_conversion_proof",
    )

    @model_validator(mode="after")
    def availability_is_complete(self) -> "NativeOutcome":
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("Unavailable outcomes require an explicit reason.")
        if self.status == "available" and (
            self.reason is not None
            or self.proof is None
            or not self.facts
            or self.quantity is None
            or self.currency is None
            or self.reported_realized_pnl is None
            or self.fee_cost is None
            or self.realized_pnl_after_fees_excluding_funding is None
            or self.coverage != "reconciled_lineage_only"
            or self.freshness != "fresh"
            or self.identity_verified_at is None
            or not self.native_exit_order_ids
            or not self.monetary_methodology_version
            or self.proof.completed_at > self.evaluated_at
            or any(
                f.event_time > f.first_observed_at or f.first_observed_at > self.evaluated_at
                for f in self.facts
            )
        ):
            raise ValueError(
                "Available native outcomes require complete reconciled provenance and money."
            )
        return self


class ExperimentPerformance(CanonicalModel):
    contract_version: Literal["blofin-experiment-performance/v1"] = (
        "blofin-experiment-performance/v1"
    )
    organization_id: UUID
    version_id: UUID
    sample_group_id: UUID
    variant_key: str
    source: Literal[ExperimentSource.BLOFIN_DEMO] = ExperimentSource.BLOFIN_DEMO
    native_uid: str | None
    evaluated_at: AwareDatetime
    status: Literal["available", "unavailable"]
    reason: str | None = None
    sample_count: int = Field(ge=0)
    required_sample_count: int = Field(ge=1)
    currency: str | None = None
    reported_realized_pnl: CanonicalDecimal | None = None
    fee_cost: CanonicalDecimal | None = None
    realized_pnl_after_fees_excluding_funding: CanonicalDecimal | None = None
    win_rate: CanonicalDecimal | None = None
    outcomes: tuple[NativeOutcome, ...] = ()
    funding: None = None
    account_history_complete: Literal[False] = False
    methodology_version: Literal["attributable-native-outcomes/v1"] = (
        "attributable-native-outcomes/v1"
    )
    rounding: Literal["18_decimal_places_half_even"] = "18_decimal_places_half_even"

    @model_validator(mode="after")
    def performance_requires_samples(self) -> "ExperimentPerformance":
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("Unavailable performance requires an explicit reason.")
        money = (
            self.reported_realized_pnl,
            self.fee_cost,
            self.realized_pnl_after_fees_excluding_funding,
            self.win_rate,
        )
        if self.status == "available" and (
            self.reason is not None
            or self.sample_count < self.required_sample_count
            or self.sample_count != len(self.outcomes)
            or not self.currency
            or any(v is None for v in money)
            or any(o.status != "available" for o in self.outcomes)
            or self.win_rate is None
            or not 0 <= self.win_rate <= 1
        ):
            raise ValueError("Available performance requires sufficient reconciled native samples.")
        if self.status == "unavailable" and any(v is not None for v in money):
            raise ValueError("Unavailable performance cannot expose fabricated or partial totals.")
        return self
