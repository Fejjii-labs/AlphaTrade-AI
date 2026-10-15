"""Pure admission preview over existing sizing/exposure helpers, never an executor."""

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.market_contracts.models import (
    CanonicalModel,
    NonNegativeCanonicalDecimal,
    PositiveCanonicalDecimal,
)
from app.schemas.experiments import (
    ExperimentFamily,
    ExperimentSource,
    ExperimentState,
    ExperimentVersion,
)
from app.schemas.position_sizing import PaperPositionSizingRequest
from app.schemas.trade_plan import ContractType, InstrumentRules, QuantityUnit
from app.services.execution_exposure import linear_quote_exposure
from app.services.position_sizing_service import PositionSizingService


class AccountExposure(CanonicalModel):
    source: ExperimentSource
    execution_account_id: UUID | None
    native_uid: str | None
    quote_exposure: NonNegativeCanonicalDecimal
    origin: Literal["manual", "experiment"]


class ExperimentRiskState(CanonicalModel):
    """Trusted server input, deliberately not an HTTP request contract."""

    organization_id: UUID
    execution_account_id: UUID
    native_uid: str | None
    source: ExperimentSource
    observed_at: AwareDatetime
    identity_verified: bool
    coverage_complete: bool
    canonical_plan_verified: bool
    canonical_risk_decision: Literal["ALLOW", "WARN", "BLOCK"]
    kill_switch_active: bool
    available_margin: PositiveCanonicalDecimal
    positions: tuple[AccountExposure, ...]
    reserved_notional: NonNegativeCanonicalDecimal
    reserved_positions: int = Field(ge=0)
    daily_loss: NonNegativeCanonicalDecimal
    weekly_loss: NonNegativeCanonicalDecimal
    drawdown: NonNegativeCanonicalDecimal
    trades_today: int = Field(ge=0)
    trades_total: int = Field(ge=0)


class ExperimentRiskPlan(CanonicalModel):
    entry: PositiveCanonicalDecimal
    structural_stop: PositiveCanonicalDecimal
    target: PositiveCanonicalDecimal
    direction: Literal["LONG", "SHORT"]
    leverage: PositiveCanonicalDecimal
    quantity_unit: QuantityUnit
    instrument_rules: InstrumentRules


class ExperimentAdmission(CanonicalModel):
    admissible: bool
    reason: str
    quantity: Decimal | None = None
    quote_exposure: Decimal | None = None
    maximum_loss: Decimal | None = None
    management_authority: Literal[False] = False
    runtime_activated: Literal[False] = False


def preview_admission(
    version: ExperimentVersion,
    plan: ExperimentRiskPlan,
    state: ExperimentRiskState,
    *,
    now: datetime,
) -> ExperimentAdmission:
    """Account-wide capacity includes manual exposure; BLOCK remains final."""
    config, rules = version.configuration, plan.instrument_rules
    limits = config.risk_limits

    def refuse(reason: str) -> ExperimentAdmission:
        return ExperimentAdmission(admissible=False, reason=reason)

    if (
        version.state is not ExperimentState.RUNNING
        or version.authorized_until is None
        or now >= version.authorized_until
    ):
        return refuse("approval_inactive")
    if config.family not in {ExperimentFamily.NESTED, ExperimentFamily.TRENDPULSE_1R}:
        return refuse("authorized_execution_adapter_missing")
    if config.family is ExperimentFamily.TRENDPULSE_1R and (
        config.account.source is not ExperimentSource.INTERNAL_SIMULATION
        or abs(plan.target - plan.entry) != abs(plan.entry - plan.structural_stop)
    ):
        return refuse("trendpulse_source_or_1r_geometry_mismatch")
    if (state.organization_id, state.execution_account_id, state.native_uid, state.source) != (
        version.organization_id,
        config.account.execution_account_id,
        config.account.native_uid,
        config.account.source,
    ):
        return refuse("account_mismatch")
    if (
        not state.identity_verified
        or not state.coverage_complete
        or not 0 <= (now - state.observed_at).total_seconds() <= 30
    ):
        return refuse("account_evidence_unverified_or_stale")
    if (
        state.kill_switch_active
        or not state.canonical_plan_verified
        or state.canonical_risk_decision != "ALLOW"
    ):
        return refuse("canonical_risk_or_authority_refusal")
    if any(
        p.source != state.source
        or (
            p.native_uid != state.native_uid
            if state.source is ExperimentSource.BLOFIN_DEMO
            else p.execution_account_id != state.execution_account_id or p.native_uid is not None
        )
        for p in state.positions
    ):
        return refuse("position_identity_mismatch")
    if rules.contract_type is not ContractType.LINEAR or plan.quantity_unit not in {
        QuantityUnit.BASE,
        QuantityUnit.CONTRACTS,
    }:
        return refuse("unsupported_instrument_semantics")
    if (
        rules.quote_currency != limits.quote_currency
        or rules.settlement_currency != limits.quote_currency
    ):
        return refuse("currency_mismatch")
    if plan.leverage > limits.max_leverage:
        return refuse("leverage_limit")
    if len(state.positions) + state.reserved_positions >= limits.max_open_positions:
        return refuse("open_position_limit")
    if (
        state.trades_today >= limits.max_trades_per_day
        or state.trades_total >= limits.max_trades_total
    ):
        return refuse("trade_limit")
    amounts = (
        plan.entry,
        plan.structural_stop,
        plan.target,
        plan.leverage,
        rules.tick_size,
        rules.lot_size,
        rules.minimum_quantity,
        rules.minimum_notional,
        rules.contract_multiplier,
        state.available_margin,
        state.reserved_notional,
        state.daily_loss,
        state.weekly_loss,
        state.drawdown,
        *(position.quote_exposure for position in state.positions),
    )
    if any(
        len(value.as_tuple().digits) > 32
        or value.adjusted() > 30
        or value.adjusted() + 1 - len(value.as_tuple().digits) < -18
        for value in amounts
    ):
        return refuse("unsupported_numeric_precision")
    with localcontext() as context:
        context.prec = 80
        if any(
            value % rules.tick_size != 0
            for value in (plan.entry, plan.structural_stop, plan.target)
        ):
            return refuse("off_tick_structural_price")
        if not (
            plan.structural_stop < plan.entry < plan.target
            if plan.direction == "LONG"
            else plan.target < plan.entry < plan.structural_stop
        ):
            return refuse("invalid_structural_geometry")
        risk = min(
            limits.max_risk_per_trade,
            limits.max_daily_loss - state.daily_loss,
            limits.max_weekly_loss - state.weekly_loss,
            limits.max_drawdown - state.drawdown,
        )
        if risk <= limits.cost_allowance:
            return refuse("loss_limit")
        exposure = (
            sum((p.quote_exposure for p in state.positions), Decimal(0)) + state.reserved_notional
        )
        notional = min(
            limits.max_position_notional,
            limits.max_total_exposure - exposure,
            state.available_margin * plan.leverage,
        )
        if notional <= limits.cost_allowance:
            return refuse("exposure_limit")
        multiplier = (
            rules.contract_multiplier
            if plan.quantity_unit is QuantityUnit.CONTRACTS
            else Decimal(1)
        )
        try:
            sized = PositionSizingService().calculate_paper(
                PaperPositionSizingRequest(
                    approved_risk_amount=risk,
                    maximum_notional=notional,
                    entry=plan.entry,
                    stop=plan.structural_stop,
                    lot_size=rules.lot_size * multiplier,
                    minimum_quantity=rules.minimum_quantity * multiplier,
                    minimum_notional=rules.minimum_notional,
                    fee_allowance=limits.cost_allowance,
                )
            )
        except ValueError:
            return refuse("instrument_minimum_or_budget")
        quantity = sized.quantity / multiplier
        quote = linear_quote_exposure(
            quantity=quantity,
            price=plan.entry,
            quantity_unit=plan.quantity_unit.value,
            contract_multiplier=rules.contract_multiplier,
            contract_type=rules.contract_type.value,
        )
        if (
            sized.maximum_loss > risk
            or quote + limits.cost_allowance > notional
            or quantity % rules.lot_size != 0
        ):
            return refuse("rounded_size_exceeds_envelope")
        return ExperimentAdmission(
            admissible=True,
            reason="within_envelope_preview_only",
            quantity=quantity,
            quote_exposure=quote,
            maximum_loss=sized.maximum_loss,
        )
