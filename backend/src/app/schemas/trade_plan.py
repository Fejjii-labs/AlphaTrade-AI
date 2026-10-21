"""Immutable executable revisions extending the existing proposal aggregate."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _parse_canonical_decimal(value: object) -> Decimal:
    if isinstance(value, bool | float):
        raise ValueError("Binary floating-point and boolean values are not valid decimals.")
    if not isinstance(value, Decimal | int | str):
        raise ValueError("Decimal values must be supplied as a base-10 string or integer.")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid base-10 decimal value.") from exc
    if not parsed.is_finite():
        raise ValueError("Decimal values must be finite.")
    return parsed


CanonicalDecimal = Annotated[Decimal, BeforeValidator(_parse_canonical_decimal)]
PositiveCanonicalDecimal = Annotated[
    Decimal,
    BeforeValidator(_parse_canonical_decimal),
    Field(gt=0),
]
NonNegativeCanonicalDecimal = Annotated[
    Decimal,
    BeforeValidator(_parse_canonical_decimal),
    Field(ge=0),
]


class CanonicalModel(BaseModel):
    """Strict, frozen semantic model with no implicit string normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class PlanOperation(StrEnum):
    SUBMIT_ENTRY = "SUBMIT_ENTRY"


class AccountMode(StrEnum):
    NET = "NET"


class ExecutionMode(StrEnum):
    PAPER = "PAPER"


class MarketType(StrEnum):
    PERPETUAL = "PERPETUAL"
    SPOT = "SPOT"


class EntrySide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class EntryOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"


class MarginMode(StrEnum):
    CROSS = "CROSS"
    ISOLATED = "ISOLATED"


class QuantityUnit(StrEnum):
    CONTRACTS = "CONTRACTS"
    BASE = "BASE"
    QUOTE = "QUOTE"


class ContractType(StrEnum):
    LINEAR = "LINEAR"
    INVERSE = "INVERSE"


class AuthorizationChannel(StrEnum):
    WEB = "WEB"
    API = "API"
    TELEGRAM = "TELEGRAM"
    AUTONOMOUS = "AUTONOMOUS"


class AuthorizationState(StrEnum):
    AVAILABLE = "AVAILABLE"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AuthorizationDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SKIP = "SKIP"


class PlanPresentationMetadata(CanonicalModel):
    """Non-semantic display data accepted at the plan boundary."""

    display_title: str | None = Field(default=None, min_length=1, max_length=200)
    channel: AuthorizationChannel | None = None
    notes: str | None = Field(default=None, max_length=1000)
    strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    setup_id: UUID | None = None
    evidence_reference: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision_reference: UUID | None = None


class SemanticAmount(CanonicalModel):
    value: PositiveCanonicalDecimal
    unit: str = Field(min_length=1, max_length=32)


class NonNegativeSemanticAmount(CanonicalModel):
    value: NonNegativeCanonicalDecimal
    unit: str = Field(min_length=1, max_length=32)


class EntryZone(CanonicalModel):
    lower: PositiveCanonicalDecimal
    upper: PositiveCanonicalDecimal
    price_unit: str = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _ordered_bounds(self) -> EntryZone:
        if self.lower > self.upper:
            raise ValueError("Entry-zone lower bound cannot exceed upper bound.")
        return self


class VersionedDerivation(CanonicalModel):
    formula_id: str = Field(min_length=1, max_length=120)
    formula_version: str = Field(min_length=1, max_length=64)


class SlippagePolicy(CanonicalModel):
    policy_id: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(min_length=1, max_length=64)
    maximum_bps: NonNegativeCanonicalDecimal


class InstrumentRules(CanonicalModel):
    contract_multiplier: PositiveCanonicalDecimal
    contract_type: ContractType
    base_currency: str = Field(min_length=2, max_length=16)
    quote_currency: str = Field(min_length=2, max_length=16)
    settlement_currency: str = Field(min_length=2, max_length=16)
    tick_size: PositiveCanonicalDecimal
    lot_size: PositiveCanonicalDecimal
    minimum_quantity: PositiveCanonicalDecimal
    minimum_notional: NonNegativeCanonicalDecimal
    rules_version: str = Field(min_length=1, max_length=64)


class BasisPolicy(CanonicalModel):
    policy_id: str = Field(min_length=1, max_length=120)
    policy_version: str = Field(min_length=1, max_length=64)
    evidence_price: SemanticAmount
    execution_price: SemanticAmount
    formula: str = Field(min_length=1, max_length=240)
    timestamp: AwareDatetime
    tolerance_bps: NonNegativeCanonicalDecimal
    freshness_seconds: int = Field(gt=0)


class ExitTarget(CanonicalModel):
    order: int = Field(ge=1)
    price: SemanticAmount
    quantity_fraction: PositiveCanonicalDecimal = Field(le=1)
    derivation: VersionedDerivation


class RunnerRules(CanonicalModel):
    enabled: bool
    activation_target_order: int | None = Field(default=None, ge=1)
    remaining_quantity_fraction: NonNegativeCanonicalDecimal = Field(le=1)
    rule_id: str = Field(min_length=1, max_length=120)
    rule_version: str = Field(min_length=1, max_length=64)
    expression: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _enabled_rules_are_complete(self) -> RunnerRules:
        if self.enabled and self.activation_target_order is None:
            raise ValueError("Enabled runner rules require an activation target.")
        if not self.enabled and self.remaining_quantity_fraction != 0:
            raise ValueError("Disabled runner rules must reserve zero quantity.")
        return self


class RiskAndExits(CanonicalModel):
    risk_budget: SemanticAmount
    maximum_loss: SemanticAmount
    fee_allowance: NonNegativeSemanticAmount
    funding_allowance: NonNegativeSemanticAmount
    slippage_allowance: NonNegativeSemanticAmount
    stop: SemanticAmount
    targets: tuple[ExitTarget, ...] = Field(min_length=1)
    runner: RunnerRules
    leverage: PositiveCanonicalDecimal = Field(le=125)
    margin_assumption_id: str = Field(min_length=1, max_length=120)
    margin_assumption_version: str = Field(min_length=1, max_length=64)

    @field_validator("targets")
    @classmethod
    def _targets_have_stable_order(cls, targets: tuple[ExitTarget, ...]) -> tuple[ExitTarget, ...]:
        expected = tuple(range(1, len(targets) + 1))
        actual = tuple(target.order for target in targets)
        if actual != expected:
            raise ValueError("Targets must be supplied in contiguous semantic order.")
        return targets


class CalculationInput(CanonicalModel):
    name: str = Field(min_length=1, max_length=120)
    input_value: CanonicalDecimal
    result_value: CanonicalDecimal
    unit: str = Field(min_length=1, max_length=32)
    formula_id: str = Field(min_length=1, max_length=120)
    formula_version: str = Field(min_length=1, max_length=64)
    precision: int = Field(ge=0, le=28)
    rounding_mode: str = Field(min_length=1, max_length=40)
    conservative_remainder: NonNegativeCanonicalDecimal


class TradePlanExecutionTerms(CanonicalModel):
    """Every execution-affecting field required for an executable revision."""

    schema_version: Literal["CanonicalTradePlanContentV1", "ManualDemoTradePlanV1"] = (
        "CanonicalTradePlanContentV1"
    )
    account_id: UUID
    exchange_account_id: UUID | None
    operation: Literal[PlanOperation.SUBMIT_ENTRY] = PlanOperation.SUBMIT_ENTRY
    strategy_version_id: UUID | None
    setup_definition_id: UUID | None
    candidate_id: UUID | None
    expected_account_mode: Literal[AccountMode.NET] = AccountMode.NET
    permission_attestation_id: UUID
    permission_attestation_version: str = Field(min_length=1, max_length=64)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    evidence_venue: str = Field(min_length=1, max_length=40)
    evidence_market: MarketType
    evidence_instrument: str = Field(min_length=1, max_length=120)
    evidence_observed_at: AwareDatetime
    evidence_freshness_seconds: int = Field(gt=0)
    evidence_is_live: Literal[True]
    evidence_fallback_used: Literal[False]
    evidence_sequence_complete: Literal[True]
    evidence_final: Literal[True]
    execution_venue: str = Field(min_length=1, max_length=40)
    execution_market: MarketType
    execution_instrument: str = Field(min_length=1, max_length=120)
    timeframe: str = Field(min_length=1, max_length=16)
    instrument_mapping_version: str = Field(min_length=1, max_length=64)
    side: EntrySide
    quantity: SemanticAmount
    quantity_unit: QuantityUnit
    order_type: EntryOrderType
    time_in_force: TimeInForce
    limit_price: SemanticAmount | None
    market_marker: bool
    entry_zone: EntryZone
    entry_zone_derivation: VersionedDerivation
    slippage_policy: SlippagePolicy
    reduce_only: Literal[False] = False
    margin_mode: MarginMode
    position_mode: Literal[AccountMode.NET] = AccountMode.NET
    instrument_rules: InstrumentRules
    basis_policy: BasisPolicy
    risk_and_exits: RiskAndExits
    valid_from: AwareDatetime
    valid_until: AwareDatetime
    calculation_inputs: tuple[CalculationInput, ...] = Field(min_length=1)
    execution_policy_version: str = Field(min_length=1, max_length=64)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_are_unique(cls, evidence_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("Evidence IDs must be unique; their supplied order is semantic.")
        return evidence_ids

    @model_validator(mode="after")
    def _validate_order_and_validity(self) -> TradePlanExecutionTerms:
        lineage = (self.strategy_version_id, self.setup_definition_id, self.candidate_id)
        if self.schema_version == "ManualDemoTradePlanV1":
            if any(value is not None for value in lineage):
                raise ValueError("Manual demo tests cannot claim strategy or Candidate lineage.")
            if (
                self.execution_venue != "BLOFIN_DEMO"
                or self.execution_policy_version != "manual-blofin-demo/v1"
                or self.exchange_account_id is not None
                or self.execution_market is not MarketType.PERPETUAL
                or self.order_type is not EntryOrderType.MARKET
                or len(self.risk_and_exits.targets) != 1
                or self.risk_and_exits.targets[0].quantity_fraction != 1
                or self.risk_and_exits.runner.enabled
                or self.evidence_venue != "BLOFIN_DEMO"
                or self.basis_policy.evidence_price != self.basis_policy.execution_price
            ):
                raise ValueError(
                    "Manual demo supports one market entry with full stop/target only."
                )
        elif any(value is None for value in lineage):
            raise ValueError(
                "Canonical strategy plans require complete strategy/Candidate lineage."
            )
        if self.valid_until <= self.valid_from:
            raise ValueError("Plan validity must end after it starts.")
        if self.evidence_observed_at > self.valid_from:
            raise ValueError("Evidence observation cannot occur after plan validity starts.")
        evidence_age = (self.valid_from - self.evidence_observed_at).total_seconds()
        if evidence_age > self.evidence_freshness_seconds:
            raise ValueError("Evidence is stale for the declared freshness policy.")
        if self.basis_policy.timestamp > self.valid_from:
            raise ValueError("Basis observation cannot occur after plan validity starts.")
        basis_age = (self.valid_from - self.basis_policy.timestamp).total_seconds()
        if basis_age > self.basis_policy.freshness_seconds:
            raise ValueError("Cross-venue basis is stale for the declared freshness policy.")
        if self.quantity.unit != self.quantity_unit.value:
            raise ValueError("Quantity value/unit and quantity_unit must match exactly.")
        if self.quantity.value < self.instrument_rules.minimum_quantity:
            raise ValueError("Order quantity is below the instrument minimum.")
        if self.order_type is EntryOrderType.MARKET:
            if self.limit_price is not None or not self.market_marker:
                raise ValueError("MARKET orders require null limit_price and market_marker=true.")
        elif self.limit_price is None or self.market_marker:
            raise ValueError("LIMIT orders require an exact limit_price and market_marker=false.")
        runner_target = self.risk_and_exits.runner.activation_target_order
        if runner_target is not None and runner_target > len(self.risk_and_exits.targets):
            raise ValueError("Runner activation target does not exist.")
        return self


class TradePlanRevisionCreate(TradePlanExecutionTerms):
    """Untrusted candidate terms that cannot directly create an executable revision."""

    presentation_metadata: PlanPresentationMetadata = Field(
        default_factory=PlanPresentationMetadata
    )

    def semantic_terms(self) -> dict[str, object]:
        """Return only hash-bound trading semantics."""
        return self.model_dump(mode="python", exclude={"presentation_metadata"})


class TradePlanRevisionSemantic(TradePlanExecutionTerms):
    plan_id: UUID
    revision_id: UUID
    organization_id: UUID
    user_id: UUID


class TradePlanRevision(TradePlanRevisionSemantic):
    """Persisted immutable executable revision."""

    correlation_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    presentation_metadata: PlanPresentationMetadata = Field(
        default_factory=PlanPresentationMetadata
    )
