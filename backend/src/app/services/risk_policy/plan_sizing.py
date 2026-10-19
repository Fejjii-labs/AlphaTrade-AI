"""Wire ``decide_trade`` into paper and demo plan sizing.

No exchange call lives here. A missing active profile returns None so the
caller keeps its previous sizing path. An active profile that cannot be graded
or that fails a ceiling, buffer, or budget check returns a reason code and
does not fall back to the fixed 1% path.
"""

from __future__ import annotations

import logging
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
from app.services.risk_policy.ceilings import PAPER_DEMO_KINDS
from app.services.risk_policy.decision import decide_trade
from app.services.risk_policy.grader import GradeDecision, grade_setup
from app.services.risk_policy.liquidation import MaintenanceTier
from app.services.risk_policy.profile import Grade, RiskPolicyProfile
from app.services.risk_policy.records import TradeRiskRecord
from app.services.risk_policy.sizer import SizingInput

# Library fixture used by direct ``decide_trade`` tests. The wired paper and
# demo path does not use this flat 0.5% rate.
logger = logging.getLogger(__name__)

PAPER_DEMO_MAINTENANCE_TIERS: tuple[MaintenanceTier, ...] = (
    MaintenanceTier(notional_floor=Decimal("0"), maintenance_rate=Decimal("0.005")),
)

# BloFin public taker example. Charged on the entry and again at the stop.
# This is not a live fee feed.
PAPER_DEMO_TAKER_FEE_RATE = Decimal("0.0006")
# Funding floor: 0.0001 per 8 hours (AT-ADR-110). A verified live print is
# used when one exists, and never below this floor. The wired path has no
# hold forecast, so the expected hold is one 8h window. A longer
# caller-supplied hold scales the number of windows. A failed GET is not
# replaced with the mock market-data fallback. A 0.5% stop can refuse with
# ``costs_exceed_r`` at this floor; the 20% cost-share ceiling stays.
PAPER_DEMO_FUNDING_RATE_PER_8H = Decimal("0.0001")
PAPER_DEMO_FUNDING_PERIOD_HOURS = Decimal("8")
PAPER_DEMO_EXPECTED_HOLD_HOURS = Decimal("8")
PAPER_DEMO_FUNDING_RATE = PAPER_DEMO_FUNDING_RATE_PER_8H
PAPER_DEMO_FUNDING_PERIODS = Decimal("1")
# Same 0.2% book buffer the non-profile demo depth path uses (``upper * 0.002``).
DEMO_BOOK_BUFFER_RATE = Decimal("0.002")
# Paper has no venue tier feed. 0.02 is above BloFin's published first-tier
# example (0.004) and still leaves a positive liquidation distance at 20x
# (1/20 - 0.02). It is not a live maintenance feed.
PAPER_CONSERVATIVE_MAINTENANCE_RATE = Decimal("0.02")
PAPER_CONSERVATIVE_MAINTENANCE_TIERS: tuple[MaintenanceTier, ...] = (
    MaintenanceTier(
        notional_floor=Decimal("0"),
        maintenance_rate=PAPER_CONSERVATIVE_MAINTENANCE_RATE,
    ),
)
# Numeric(20, 8) money can differ by a unit in the last place when the same
# open rows are summed twice. A larger gap is a different book.
OPEN_NOTIONAL_MATCH_TOLERANCE = Decimal("0.00000001")
STORED_PLAN_EXCEEDS_LIMITS = "stored_plan_exceeds_current_limits"


@dataclass(frozen=True)
class ActiveProfile:
    profile: RiskPolicyProfile
    version_id: UUID


@dataclass(frozen=True)
class ProfileOpenBook:
    open_risk_percent: Decimal
    open_notional: Decimal
    open_positions: int
    remaining_daily_budget_percent: Decimal


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
    funding_rate: Decimal
    funding_periods: Decimal
    # Cash the sizer was allowed to spend (grade, ceiling, and budget room).
    # Stop-out loss plus round-trip costs must stay inside it.
    risk_cash_ceiling: Decimal


def remaining_daily_budget_percent(
    *,
    daily_loss_limit_percent: Decimal,
    realized_pnl: Decimal | None,
    equity: Decimal,
) -> Decimal | str:
    """Profile daily room after server realized loss. Missing inputs are not zero."""

    if (
        not isinstance(daily_loss_limit_percent, Decimal)
        or not daily_loss_limit_percent.is_finite()
        or not isinstance(equity, Decimal)
        or not equity.is_finite()
        or equity <= 0
        or not isinstance(realized_pnl, Decimal)
        or not realized_pnl.is_finite()
    ):
        return "daily_budget_uncomputed"
    loss = -realized_pnl if realized_pnl < 0 else Decimal("0")
    return daily_loss_limit_percent - (loss / equity * Decimal("100"))


def paper_demo_funding(
    live_rate: Decimal | None = None,
    *,
    live_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    """Return ``(rate per 8h, periods)`` for the paper and demo sizer.

    A verified finite live rate is ``max(abs(rate), 0.0001)``. Anything else,
    including a missing read and the mock fallback, uses 0.0001 per 8h.
    Periods are ``max(1, expected_hold_hours / 8)``. The default expected
    hold is 8 hours, which is one window. See AT-ADR-110.
    """

    hold = PAPER_DEMO_EXPECTED_HOLD_HOURS if expected_hold_hours is None else expected_hold_hours
    if not isinstance(hold, Decimal) or not hold.is_finite() or hold <= 0:
        hold = PAPER_DEMO_EXPECTED_HOLD_HOURS
    periods = hold / PAPER_DEMO_FUNDING_PERIOD_HOURS
    if periods < 1:
        periods = Decimal("1")
    floor = PAPER_DEMO_FUNDING_RATE_PER_8H
    if live_verified and isinstance(live_rate, Decimal) and live_rate.is_finite():
        rate = abs(live_rate)
        if rate < floor:
            rate = floor
        return rate, periods
    return floor, periods


def open_risk_from_stops(
    *,
    equity: Decimal,
    positions: Sequence[tuple[Decimal, Decimal, Decimal | None]],
    open_notional: Decimal,
    include_book_buffer: bool = False,
    funding_rate: Decimal | None = None,
    live_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
) -> tuple[Decimal, int] | str:
    """Open risk at the stored stops, including the sizer's unit costs.

    A position without a stop is not zero risk. ``include_book_buffer`` adds
    the demo 0.2% book buffer. Paper leaves it off. This does not write the
    shared organization-and-account ledger row.
    """

    if (
        not isinstance(equity, Decimal)
        or not equity.is_finite()
        or equity <= 0
        or not isinstance(open_notional, Decimal)
        or not open_notional.is_finite()
        or open_notional < 0
    ):
        return "open_book_uncomputed"
    risk_cash = Decimal("0")
    explained = Decimal("0")
    for size, entry, stop in positions:
        if (
            not isinstance(size, Decimal)
            or not isinstance(entry, Decimal)
            or size <= 0
            or entry <= 0
            or stop is None
            or not isinstance(stop, Decimal)
            or not stop.is_finite()
        ):
            return _open_risk_uncomputed("position size, entry, or stop is missing or not finite")
        risk_cash += stop_out_cash(
            size,
            entry,
            stop,
            include_book_buffer=include_book_buffer,
            funding_rate=funding_rate,
            live_verified=live_verified,
            expected_hold_hours=expected_hold_hours,
        )
        explained += size * entry
    if abs(open_notional - explained) > OPEN_NOTIONAL_MATCH_TOLERANCE:
        return _open_risk_uncomputed(
            "open notional differs from the notional explained by stops "
            f"by more than {OPEN_NOTIONAL_MATCH_TOLERANCE}"
        )
    return (risk_cash / equity * Decimal("100"), len(positions))


def canonical_open_stop_positions(
    rows: Sequence[tuple[Decimal | None, Decimal | None, Decimal | None]],
) -> list[tuple[Decimal, Decimal, Decimal]] | str:
    """Price open lifecycle rows that carry a stop.

    Each tuple is size, entry, and planned stop. A row that cannot be priced
    fails closed. The caller keeps the ``open_risk_uncomputed`` refusal.
    """

    priced: list[tuple[Decimal, Decimal, Decimal]] = []
    for size, entry, stop in rows:
        if (
            not isinstance(size, Decimal)
            or not isinstance(entry, Decimal)
            or not isinstance(stop, Decimal)
            or not size.is_finite()
            or not entry.is_finite()
            or not stop.is_finite()
            or size <= 0
            or entry <= 0
        ):
            return _open_risk_uncomputed(
                "canonical open lifecycle row is missing size, entry, or stop"
            )
        priced.append((size, entry, stop))
    return priced


def stored_plan_limit_refusal(
    *,
    quantity: Decimal,
    entry: Decimal,
    stop: Decimal,
    contract_multiplier: Decimal,
    equity: Decimal,
    book: ProfileOpenBook,
    profile: RiskPolicyProfile,
    execution_channel: str,
    include_book_buffer: bool,
    funding_rate: Decimal | None = None,
    live_verified: bool = False,
) -> str | None:
    """Refuse a stored plan whose stop risk no longer fits the current book.

    The stored quantity is not rewritten. Cash uses the same unit costs as
    ``size_for_profile``. Equality with the remaining room is allowed.
    """

    if (
        not isinstance(quantity, Decimal)
        or not isinstance(entry, Decimal)
        or not isinstance(stop, Decimal)
        or not isinstance(contract_multiplier, Decimal)
        or not isinstance(equity, Decimal)
        or not quantity.is_finite()
        or not entry.is_finite()
        or not stop.is_finite()
        or not contract_multiplier.is_finite()
        or not equity.is_finite()
        or quantity <= 0
        or entry <= 0
        or stop <= 0
        or contract_multiplier <= 0
        or equity <= 0
    ):
        return "stored_plan_limits_uncomputed"
    size = quantity * contract_multiplier
    cash = stop_out_cash(
        size,
        entry,
        stop,
        include_book_buffer=include_book_buffer,
        funding_rate=funding_rate,
        live_verified=live_verified,
    )
    added = cash / equity * Decimal("100")
    notional = size * entry
    if book.open_positions + 1 > profile.max_open_positions:
        return STORED_PLAN_EXCEEDS_LIMITS
    if (
        book.remaining_daily_budget_percent <= 0
        or book.open_risk_percent + added
        > book.remaining_daily_budget_percent + OPEN_NOTIONAL_MATCH_TOLERANCE
    ):
        return STORED_PLAN_EXCEEDS_LIMITS
    if (
        book.open_risk_percent + added
        > profile.max_aggregate_open_risk_percent + OPEN_NOTIONAL_MATCH_TOLERANCE
    ):
        return STORED_PLAN_EXCEEDS_LIMITS
    per_trade = equity * profile.max_notional_per_trade_percent / Decimal("100")
    aggregate = equity * profile.max_aggregate_notional_percent / Decimal("100")
    if notional > per_trade + OPEN_NOTIONAL_MATCH_TOLERANCE:
        return STORED_PLAN_EXCEEDS_LIMITS
    if book.open_notional + notional > aggregate + OPEN_NOTIONAL_MATCH_TOLERANCE:
        return STORED_PLAN_EXCEEDS_LIMITS
    profile_cap = max(profile.risk_percent_by_grade.values(), default=Decimal("0"))
    if _sized_risk_blocked_for_channel(
        channel=execution_channel,
        risk_percent=added,
        profile_cap=profile_cap,
    ):
        return "risk_percent_exceeds_channel_ceiling"
    return None


def _open_risk_uncomputed(reason: str) -> str:
    logger.warning("open risk at stops cannot be computed: %s", reason)
    return "open_risk_uncomputed"


def demo_leverage_block(account_leverage: Decimal, required: Decimal) -> str | None:
    """Block when the demo account leverage is not the sized leverage.

    The caller must not send a leverage-change request. The account is left
    as it was read.
    """

    if account_leverage != required:
        return "demo_account_leverage_mismatch"
    return None


def stop_out_cash(
    size: Decimal,
    entry: Decimal,
    stop: Decimal,
    *,
    include_book_buffer: bool,
    funding_rate: Decimal | None = None,
    funding_periods: Decimal | None = None,
    live_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
) -> Decimal:
    """Size times the stop distance plus the unit costs ``size_for_profile`` reserved."""

    return size * (
        abs(entry - stop)
        + profile_unit_cost(
            entry,
            stop,
            include_book_buffer=include_book_buffer,
            funding_rate=funding_rate,
            funding_periods=funding_periods,
            live_verified=live_verified,
            expected_hold_hours=expected_hold_hours,
        )
    )


def reserved_maximum_loss(
    *,
    size: Decimal,
    adverse_entry: Decimal,
    stop: Decimal,
    worse_price: Decimal,
    profile_costs: bool,
    funding_rate: Decimal | None = None,
    funding_periods: Decimal | None = None,
    live_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
) -> Decimal:
    """Cash stored as ``maximum_loss`` and reserved as daily loss.

    A profile plan uses the sizer's unit costs, including the demo book buffer.
    A non-profile demo plan keeps the previous 0.2% of the worse price. Neither
    path writes the shared ledger row.
    """

    if profile_costs:
        return stop_out_cash(
            size,
            adverse_entry,
            stop,
            include_book_buffer=True,
            funding_rate=funding_rate,
            funding_periods=funding_periods,
            live_verified=live_verified,
            expected_hold_hours=expected_hold_hours,
        )
    return size * (abs(adverse_entry - stop) + worse_price * Decimal("0.002"))


def profile_unit_cost(
    entry: Decimal,
    stop: Decimal,
    *,
    include_book_buffer: bool,
    funding_rate: Decimal | None = None,
    funding_periods: Decimal | None = None,
    live_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
) -> Decimal:
    """Entry fee, exit fee at the stop, funding, and the optional book buffer.

    Pass ``funding_periods`` only when the caller already resolved the rate
    with ``paper_demo_funding``. Otherwise the floor or the verified live
    rate is applied here.
    """

    if funding_periods is None:
        funding_rate, funding_periods = paper_demo_funding(
            funding_rate,
            live_verified=live_verified,
            expected_hold_hours=expected_hold_hours,
        )
    elif funding_rate is None:
        funding_rate = PAPER_DEMO_FUNDING_RATE_PER_8H
    slippage = DEMO_BOOK_BUFFER_RATE if include_book_buffer else Decimal("0")
    return (
        entry * PAPER_DEMO_TAKER_FEE_RATE
        + stop * PAPER_DEMO_TAKER_FEE_RATE
        + entry * abs(funding_rate) * funding_periods
        + entry * slippage
    )


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
    include_book_buffer: bool = False,
    maintenance_tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None = None,
    funding_rate: Decimal | None = None,
    funding_rate_verified: bool = False,
    expected_hold_hours: Decimal | None = None,
    execution_channel: str | None = None,
) -> SizedPlan | str:
    """Size one new paper or demo plan. Returns a reason code when refused.

    Fees, funding, and the demo book buffer are inside the grade cash. A
    liquidation buffer that does not clear refuses the trade. Leverage is not
    lowered on this path. ``execution_channel`` is the real path: internal
    paper or BloFin demo. Those two set the risk-engine paper/demo channel,
    so an A+ may use 5% and the promotion report stays optional. Any other
    channel keeps the non-paper ``risk_ceiling_for`` cap. When the channel
    is omitted, the profile kind is used.
    """

    if not grade.tradable or grade.grade is None:
        return grade.refusal or "grade_not_tradable"
    if grade.trend_score is None or grade.momentum_score is None:
        return "grade_unavailable"
    if atr is None or atr <= 0:
        return "atr_missing"
    if open_notional > 0 and open_risk_percent <= 0:
        # Open notional without a server-side risk-at-stop figure is not treated as zero.
        return "open_risk_uncomputed"
    rate, periods = paper_demo_funding(
        funding_rate,
        live_verified=funding_rate_verified,
        expected_hold_hours=expected_hold_hours,
    )
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
            fee_rate=PAPER_DEMO_TAKER_FEE_RATE,
            funding_rate=rate,
            funding_periods=periods,
            slippage_rate=DEMO_BOOK_BUFFER_RATE if include_book_buffer else Decimal("0"),
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
        tiers=(
            PAPER_CONSERVATIVE_MAINTENANCE_TIERS if maintenance_tiers is None else maintenance_tiers
        ),
        preserve_grade_risk=True,
        reduce_leverage=False,
    )
    if not decision.accepted or decision.record is None or decision.sizing is None:
        return decision.reason or "risk_policy_refused"
    channel = active.profile.kind.value if execution_channel is None else execution_channel
    if _sized_risk_blocked_for_channel(
        channel=channel,
        risk_percent=decision.sizing.effective_risk_percent,
        profile_cap=active.profile.risk_percent(grade.grade),
    ):
        return "risk_percent_exceeds_channel_ceiling"
    cash_ceiling = (
        equity
        * min(
            decision.sizing.grade_risk_percent,
            decision.sizing.profile_risk_percent,
            decision.sizing.ceiling_percent,
            decision.sizing.budget_room_percent,
        )
        / Decimal("100")
    )
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
        risk_cash_ceiling=cash_ceiling,
        funding_rate=rate,
        funding_periods=periods,
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


def _sized_risk_blocked_for_channel(
    *,
    channel: str,
    risk_percent: Decimal,
    profile_cap: Decimal,
) -> bool:
    """Apply the risk-engine percent rule for this execution channel.

    The full engine is not consulted. Its leverage and notional rules are a
    different contract from profile sizing. This is the 5% branch that was
    previously set only in tests.
    """

    from app.schemas.common import TradeDirection
    from app.schemas.risk import RiskCheckRequest
    from app.services.risk.limits import RiskLimits
    from app.services.risk.rules import RiskEvaluationContext, check_risk_percent

    request = RiskCheckRequest(
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        stop_loss=Decimal("99"),
        position_size=Decimal("1"),
        leverage=Decimal("1"),
        account_equity=Decimal("10000"),
        risk_percent=risk_percent,
    )
    rule = check_risk_percent(
        request,
        RiskLimits(),
        RiskEvaluationContext(
            is_weekend=False,
            autonomous_sizing=True,
            paper_or_demo_channel=channel in PAPER_DEMO_KINDS,
            profile_risk_percent_cap=profile_cap,
        ),
    )
    return rule is not None
