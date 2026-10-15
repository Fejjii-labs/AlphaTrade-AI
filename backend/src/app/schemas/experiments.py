"""Experiment contracts: bounded approvals, independent samples, no execution activation."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.models import (
    CanonicalModel,
    NonNegativeCanonicalDecimal,
    PositiveCanonicalDecimal,
)
from app.schemas.common import Timeframe
from app.services.canonical_serialization import canonical_sha256


class ExperimentMode(StrEnum):
    EXPLORATION = "exploration"
    VALIDATION = "validation"


class ExperimentSource(StrEnum):
    BLOFIN_DEMO = "blofin_demo"
    INTERNAL_SIMULATION = "internal_simulation"


class ExperimentState(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PROMOTED = "promoted"


class ExperimentFamily(StrEnum):
    NESTED = "operational_nested_continuation/v1"
    SFP = "swing_failure_pattern/v1"
    TRENDPULSE_1R = "trendpulse_1r/v1"


class ExperimentAccount(CanonicalModel):
    execution_account_id: UUID
    source: ExperimentSource
    native_uid: str | None = Field(default=None, min_length=1, max_length=128)
    execution_identity_audit_id: UUID | None = None

    @model_validator(mode="after")
    def source_identity(self) -> Self:
        native = self.native_uid is not None and self.execution_identity_audit_id is not None
        if self.source is ExperimentSource.BLOFIN_DEMO and not native:
            raise ValueError("BloFin requires native UID and a stored execution identity audit")
        if self.source is ExperimentSource.INTERNAL_SIMULATION and (
            self.native_uid is not None or self.execution_identity_audit_id is not None
        ):
            raise ValueError("Internal simulation cannot claim native account identity")
        return self


class ExperimentVariant(CanonicalModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    strategy_version_id: UUID
    parameters: dict[str, Any]

    @model_validator(mode="after")
    def exact_parameters(self) -> Self:
        canonical_sha256(self.parameters)  # Reject floats/NaN, including nested values.
        return self


class ExperimentModelPolicy(CanonicalModel):
    mode: Literal["disabled", "advisory"] = "disabled"
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    max_calls: int = Field(default=0, ge=0, le=10000)
    max_tokens: int = Field(default=0, ge=0, le=10000000)
    max_cost_usd: NonNegativeCanonicalDecimal = Field(default=Decimal("0"), le=1000000)

    @model_validator(mode="after")
    def bounded_advice(self) -> Self:
        if self.mode == "advisory" and not (
            self.provider
            and self.model
            and self.max_calls
            and self.max_tokens
            and self.max_cost_usd
        ):
            raise ValueError("Advisory models require explicit finite call/token/cost budgets")
        if self.mode == "disabled" and (
            self.provider or self.model or self.max_calls or self.max_tokens or self.max_cost_usd
        ):
            raise ValueError("Disabled models cannot carry an active model budget")
        return self


class ExperimentRiskLimits(CanonicalModel):
    quote_currency: Literal["USDT"] = "USDT"
    max_risk_per_trade: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_position_notional: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_total_exposure: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_daily_loss: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_weekly_loss: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_drawdown: PositiveCanonicalDecimal = Field(max_digits=24, decimal_places=12)
    max_leverage: PositiveCanonicalDecimal = Field(le=10)
    max_open_positions: int = Field(ge=1, le=100)
    max_trades_per_day: int = Field(ge=1, le=1000)
    max_trades_total: int = Field(ge=1, le=100000)
    cost_allowance: NonNegativeCanonicalDecimal = Field(max_digits=24, decimal_places=12)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if (
            self.max_risk_per_trade > self.max_daily_loss
            or self.max_daily_loss > self.max_weekly_loss
        ):
            raise ValueError("Per-trade, daily and weekly loss limits must be ordered")
        if self.max_position_notional > self.max_total_exposure:
            raise ValueError("Position notional cannot exceed total exposure")
        if self.cost_allowance >= self.max_risk_per_trade:
            raise ValueError("Risk budget must exceed reserved costs")
        return self


class ExperimentSampleTarget(CanonicalModel):
    kind: Literal["closed_trade", "setup_observation"]
    minimum: int = Field(ge=1, le=100000)
    maximum: int = Field(ge=1, le=100000)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("Minimum sample cannot exceed maximum")
        return self


class ExperimentConfiguration(CanonicalModel):
    contract_version: Literal["experiment-config/v1"] = "experiment-config/v1"
    mode: ExperimentMode
    account: ExperimentAccount
    family: ExperimentFamily
    strategy_id: UUID
    strategy_version_id: UUID
    variants: tuple[ExperimentVariant, ...] = Field(min_length=1, max_length=32)
    model_policy: ExperimentModelPolicy
    symbols: tuple[str, ...] = Field(min_length=1, max_length=32)
    timeframes: tuple[Timeframe, ...] = Field(min_length=1, max_length=16)
    risk_limits: ExperimentRiskLimits
    sample_target: ExperimentSampleTarget

    @model_validator(mode="after")
    def distinct(self) -> Self:
        if len({v.key for v in self.variants}) != len(self.variants):
            raise ValueError("Variant keys must be distinct")
        if len(set(self.symbols)) != len(self.symbols) or len(set(self.timeframes)) != len(
            self.timeframes
        ):
            raise ValueError("Symbols/timeframes must be distinct")
        if any(not symbol.isascii() or not 2 <= len(symbol) <= 32 for symbol in self.symbols):
            raise ValueError("Invalid symbol")
        if self.mode is ExperimentMode.VALIDATION and len(self.variants) != 1:
            raise ValueError("Validation freezes exactly one selected variant")
        if self.strategy_version_id not in {v.strategy_version_id for v in self.variants}:
            raise ValueError("Base strategy version must be one of the variants")
        return self


class ExperimentCreate(CanonicalModel):
    name: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=120)
    configuration: ExperimentConfiguration


class ExperimentVersionCreate(CanonicalModel):
    parent_version_id: UUID
    configuration: ExperimentConfiguration


class ExperimentTransition(CanonicalModel):
    action: Literal["submit", "start", "pause", "complete"]
    expected_revision: int = Field(ge=0)


class ExperimentApproval(CanonicalModel):
    expected_revision: int = Field(ge=0)
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorized_until: AwareDatetime
    confirm: Literal["APPROVE_BOUNDED_EXPERIMENT"]


class ExperimentPromotion(CanonicalModel):
    expected_revision: int = Field(ge=0)
    variant_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")


class ExperimentSampleCreate(CanonicalModel):
    variant_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    source_record_id: str = Field(min_length=1, max_length=128)


class ExperimentVersion(CanonicalModel):
    id: UUID
    experiment_id: UUID
    organization_id: UUID
    user_id: UUID
    version: int
    parent_version_id: UUID | None
    state: ExperimentState
    revision: int
    configuration: ExperimentConfiguration
    configuration_hash: str
    strategy_content_hashes: dict[str, str]
    sample_group_id: UUID
    sample_counts: dict[str, int]
    created_at: AwareDatetime
    submitted_at: AwareDatetime | None
    approved_at: AwareDatetime | None
    approved_by: UUID | None
    authorized_until: AwareDatetime | None
    started_at: AwareDatetime | None
    paused_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    promoted_at: AwareDatetime | None
    promotion_version_id: UUID | None
    runtime_activated: Literal[False] = False
    performance: None = None


class ExperimentDetail(CanonicalModel):
    id: UUID
    name: str
    versions: list[ExperimentVersion]


class ExperimentPage(CanonicalModel):
    items: list[ExperimentDetail]
    total: int
    limit: int
    offset: int


class ExperimentSample(CanonicalModel):
    id: UUID
    version_id: UUID
    sample_group_id: UUID
    variant_key: str
    source: ExperimentSource
    kind: Literal["closed_trade", "setup_observation"]
    source_record_id: str
    evidence_hash: str
    opened_at: AwareDatetime
    completed_at: AwareDatetime
