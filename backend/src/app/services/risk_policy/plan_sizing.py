"""Wire ``decide_trade`` into paper and demo plan sizing.

No exchange call lives here. A missing active profile returns None so the
caller keeps its previous sizing path. An active profile that cannot be graded
or that fails a ceiling, buffer, or budget check returns a reason code and
does not fall back to the fixed 1% path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol, cast
from uuid import UUID

from app.analysis.engine import analyze
from app.analysis.types import Indicators
from app.providers.market_data import OHLCVBar
from app.services.risk_policy.adjusters import AdjusterInputs
from app.services.risk_policy.breakers import BreakerVerdict
from app.services.risk_policy.decision import decide_trade
from app.services.risk_policy.grader import GradeDecision, grade_setup
from app.services.risk_policy.liquidation import MaintenanceTier
from app.services.risk_policy.profile import Grade, RiskPolicyProfile
from app.services.risk_policy.records import TradeRiskRecord
from app.services.risk_policy.sizer import SizingInput

# Local isolated-margin assumption for paper and demo buffer math.
# It is not an exchange tier feed and it does not set margin mode.
PAPER_DEMO_MAINTENANCE_TIERS: tuple[MaintenanceTier, ...] = (
    MaintenanceTier(notional_floor=Decimal("0"), maintenance_rate=Decimal("0.005")),
)


@dataclass(frozen=True)
class ActiveProfile:
    profile: RiskPolicyProfile
    version_id: UUID


@dataclass(frozen=True)
class SizedPlan:
    quantity: Decimal
    leverage: Decimal
    record: TradeRiskRecord
    grade: Grade
    risk_percent: Decimal
    planned_r: Decimal
    entry: Decimal
    stop: Decimal
    target: Decimal


def demo_leverage_block(account_leverage: Decimal, required: Decimal) -> str | None:
    """Block when the demo account leverage is not the sized leverage.

    The caller must not send a leverage-change request. The account is left
    as it was read.
    """

    if account_leverage != required:
        return "demo_account_leverage_mismatch"
    return None


def demo_margin_mode_block(margin_mode: str | None) -> str | None:
    """The liquidation buffer is isolated-only. This does not switch the account."""

    if margin_mode is None or margin_mode.lower() != "isolated":
        return "demo_margin_mode_not_isolated"
    return None


class _Candle(Protocol):
    open: object
    high: object
    low: object
    close: object
    interval_start: datetime


def grade_from_bars(
    *,
    direction: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[object],
) -> tuple[GradeDecision, Decimal | None]:
    """Grade from bars the caller already holds. Does not fetch a price."""

    converted: list[OHLCVBar] = []
    for item in bars:
        bar = cast(_Candle, item)
        volume = getattr(bar, "base_volume", None)
        if volume is None:
            volume = getattr(bar, "volume", 0)
        converted.append(
            OHLCVBar(
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                volume=Decimal(str(volume)),
                timestamp=bar.interval_start,
            )
        )
    if len(converted) < 20:
        return GradeDecision(None, "deterministic-grade/v1", None, None, "grade_unavailable"), None
    result = analyze(symbol, timeframe, converted)
    decision = grade_setup(
        direction=direction, structure=result.structure, indicators=result.indicators
    )
    return decision, _atr(result.indicators)


def size_for_profile(
    *,
    active: ActiveProfile,
    grade: GradeDecision,
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    direction: str,
    atr: Decimal | None,
    lot_step: Decimal,
    min_quantity: Decimal,
    open_risk_percent: Decimal,
    open_notional: Decimal,
    open_positions: int,
    remaining_daily_budget_percent: Decimal,
    breaker_refusing: bool,
    leverage: Decimal | None = None,
) -> SizedPlan | str:
    """Size one new paper or demo plan. Returns a reason code when refused."""

    if not grade.tradable or grade.grade is None:
        return grade.refusal or "grade_not_tradable"
    if grade.trend_score is None or grade.momentum_score is None:
        return "grade_unavailable"
    if atr is None or atr <= 0:
        return "atr_missing"
    if open_notional > 0 and open_risk_percent <= 0:
        # Open notional without a server-side risk-at-stop figure is not treated as zero.
        return "open_risk_uncomputed"
    distance = abs(entry - stop)
    reward = abs(target - entry)
    if distance <= 0 or reward <= 0 or entry <= 0:
        return "invalidation_unavailable"
    volatility = _volatility(atr, entry)
    decision = decide_trade(
        profile=active.profile,
        profile_version_id=active.version_id,
        grade=grade,
        adjusters=AdjusterInputs(
            volatility_regime=volatility,
            trend_aligned=grade.trend_score == Decimal("1"),
            momentum_aligned=grade.momentum_score == Decimal("1"),
        ),
        sizing=SizingInput(
            equity=equity,
            entry=entry,
            stop=stop,
            direction=direction,
            grade=grade.grade,
            grade_risk_percent=active.profile.risk_percent(grade.grade),
            atr=atr,
            gap_beyond_stop=Decimal("0"),
            fee_rate=Decimal("0"),
            funding_rate=Decimal("0"),
            funding_periods=Decimal("0"),
            slippage_rate=Decimal("0"),
            lot_step=lot_step,
            min_quantity=min_quantity,
            remaining_daily_budget_percent=remaining_daily_budget_percent,
            open_risk_percent=open_risk_percent,
            open_notional=open_notional,
            open_positions=open_positions,
            leverage=leverage if leverage is not None else active.profile.default_leverage,
            margin_mode="isolated",
        ),
        breaker=_verdict(equity, refusing=breaker_refusing),
        tiers=PAPER_DEMO_MAINTENANCE_TIERS,
        preserve_grade_risk=True,
    )
    if not decision.accepted or decision.record is None or decision.sizing is None:
        return decision.reason or "risk_policy_refused"
    planned_r = (reward / distance).quantize(Decimal("0.00000001"))
    calculation = {
        **decision.record.calculation,
        "entry": format(entry, "f"),
        "stop": format(stop, "f"),
        "target": format(target, "f"),
        "planned_r": format(planned_r, "f"),
        "outcome": "open",
        "profile_name": active.profile.profile_name,
        "paper_demo_data_collection": (
            "true" if active.profile.paper_demo_data_collection else "false"
        ),
    }
    record = replace(decision.record, calculation=calculation)
    return SizedPlan(
        quantity=decision.sizing.quantity,
        leverage=decision.sizing.leverage,
        record=record,
        grade=grade.grade,
        risk_percent=decision.sizing.effective_risk_percent,
        planned_r=planned_r,
        entry=entry,
        stop=stop,
        target=target,
    )


def _volatility(atr: Decimal, entry: Decimal) -> str:
    ratio = atr / entry
    if ratio < Decimal("0.01"):
        return "low"
    if ratio < Decimal("0.03"):
        return "normal"
    if ratio < Decimal("0.06"):
        return "high"
    return "extreme"


def _atr(indicators: Indicators) -> Decimal | None:
    if indicators.atr is None:
        return None
    value = Decimal(str(indicators.atr))
    if not value.is_finite() or value <= 0:
        return None
    return value


def _verdict(equity: Decimal, *, refusing: bool) -> BreakerVerdict:
    return BreakerVerdict(
        daily_loss_active=refusing,
        drawdown_active=False,
        consecutive_loss_active=False,
        trip_kill_switch=False,
        paused_until=None,
        equity_peak=equity,
        refuse_new_trades=refusing,
        human_reset_required=False,
    )
