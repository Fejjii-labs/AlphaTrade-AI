"""Individual deterministic risk rules.

Each rule returns a :class:`~app.schemas.risk.TriggeredRule` or ``None`` when it
does not fire. Rules are pure functions — no LLM, no side effects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.schemas.common import RiskAction, RiskRuleId, RiskSeverity
from app.schemas.risk import RiskCheckRequest, TriggeredRule
from app.services.risk.limits import RiskLimits
from app.services.risk_policy.ceilings import HARD_MAX_RISK_PER_TRADE_PERCENT
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.ledger import ledger_symbol_cap_equals_notional

RuleFn = Callable[[RiskCheckRequest, RiskLimits, "RiskEvaluationContext"], TriggeredRule | None]


@dataclass(frozen=True)
class RiskEvaluationContext:
    """Optional runtime context beyond the request payload."""

    daily_locked: bool = False
    realized_pnl_today: Decimal = Decimal("0")
    daily_loss_limit: Decimal | None = None
    weekly_loss_pct: Decimal | None = None
    trades_today: int = 0
    protect_green_day: bool = False
    kill_switch_active: bool = False
    overtrading: bool = False
    is_weekend: bool = False
    # Sum of open paper position notionals (size * entry) before this order.
    open_exposure_notional: Decimal = Decimal("0")
    # Active grade-policy cap in percent points. None means the hard ceiling only.
    profile_risk_percent_cap: Decimal | None = None
    # PR 258 account ledger. Both must be set together. Equal caps fail closed.
    ledger_max_symbol_notional: Decimal | None = None
    ledger_max_notional: Decimal | None = None
    # The grade-policy ceiling applies only when autonomous sizing opted in.
    # Human paper execution and strategy replays leave this false.
    autonomous_sizing: bool = False


def _rule(
    rule_id: RiskRuleId,
    action: RiskAction,
    severity: RiskSeverity,
    message: str,
) -> TriggeredRule:
    return TriggeredRule(rule_id=rule_id, action=action, severity=severity, message=message)


def check_kill_switch(
    req: RiskCheckRequest, _limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.kill_switch_active:
        return _rule(
            RiskRuleId.KILL_SWITCH,
            RiskAction.BLOCK,
            RiskSeverity.CRITICAL,
            "Kill switch is active; all new execution is blocked.",
        )
    return None


def check_no_stop_loss(
    req: RiskCheckRequest, _limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.stop_loss is None:
        return _rule(
            RiskRuleId.NO_STOP_LOSS,
            RiskAction.BLOCK,
            RiskSeverity.CRITICAL,
            "Stop loss is required before any trade can proceed.",
        )
    return None


def check_invalid_stop_loss(
    req: RiskCheckRequest, _limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.stop_loss is None:
        return None
    if abs(req.entry_price - req.stop_loss) <= 0:
        return _rule(
            RiskRuleId.INVALID_STOP_LOSS,
            RiskAction.BLOCK,
            RiskSeverity.CRITICAL,
            "Stop loss distance must be greater than zero.",
        )
    return None


def check_max_leverage(
    req: RiskCheckRequest, limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    cap = limits.max_leverage
    if req.is_countertrend:
        cap = min(cap, limits.countertrend_max_leverage)
    if req.is_volatile_altcoin:
        cap = min(cap, limits.volatile_alt_max_leverage)
    if req.leverage > cap:
        return _rule(
            RiskRuleId.MAX_LEVERAGE,
            RiskAction.BLOCK,
            RiskSeverity.HIGH,
            f"Leverage {req.leverage} exceeds maximum allowed {cap}.",
        )
    return None


def check_max_position_size(
    req: RiskCheckRequest, limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    notional = req.position_size * req.entry_price
    max_notional = req.account_equity * (limits.max_position_pct_of_equity / Decimal("100"))
    if notional > max_notional:
        return _rule(
            RiskRuleId.MAX_POSITION_SIZE,
            RiskAction.BLOCK,
            RiskSeverity.HIGH,
            f"Position notional {notional} exceeds {limits.max_position_pct_of_equity}% of equity.",
        )
    total_exposure = ctx.open_exposure_notional + notional
    if total_exposure > max_notional:
        return _rule(
            RiskRuleId.MAX_POSITION_SIZE,
            RiskAction.BLOCK,
            RiskSeverity.HIGH,
            (
                f"Open exposure {total_exposure} would exceed "
                f"{limits.max_position_pct_of_equity}% of equity."
            ),
        )
    return None


def check_daily_loss_lock(
    req: RiskCheckRequest, limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.daily_locked:
        return _rule(
            RiskRuleId.MAX_DAILY_LOSS,
            RiskAction.BLOCK,
            RiskSeverity.CRITICAL,
            "Daily loss limit reached; trading is locked for today.",
        )
    if ctx.daily_loss_limit is not None and ctx.realized_pnl_today <= -ctx.daily_loss_limit:
        return _rule(
            RiskRuleId.MAX_DAILY_LOSS,
            RiskAction.BLOCK,
            RiskSeverity.CRITICAL,
            "Realized PnL has breached the daily loss limit.",
        )
    if ctx.realized_pnl_today < 0 and req.account_equity > 0:
        loss_pct = abs(ctx.realized_pnl_today) / req.account_equity * Decimal("100")
        if loss_pct >= limits.max_daily_loss_pct:
            return _rule(
                RiskRuleId.MAX_DAILY_LOSS,
                RiskAction.BLOCK,
                RiskSeverity.HIGH,
                f"Daily loss {loss_pct:.2f}% exceeds limit {limits.max_daily_loss_pct}%.",
            )
    return None


def check_weekly_loss(
    req: RiskCheckRequest, limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.weekly_loss_pct is not None and ctx.weekly_loss_pct >= limits.max_weekly_loss_pct:
        return _rule(
            RiskRuleId.MAX_WEEKLY_LOSS,
            RiskAction.BLOCK,
            RiskSeverity.HIGH,
            "Weekly loss limit breached; new trades blocked.",
        )
    return None


def check_unsupported_coin(
    req: RiskCheckRequest, limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.symbol not in limits.supported_symbols:
        return _rule(
            RiskRuleId.UNSUPPORTED_COIN,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            f"{req.symbol} is not on the supported symbol list.",
        )
    return None


def check_countertrend(
    req: RiskCheckRequest, _limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.is_countertrend:
        return _rule(
            RiskRuleId.COUNTERTREND_REDUCED_SIZE,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            "Countertrend trade: use reduced size and wait for confirmation.",
        )
    return None


def check_volatile_alt(
    req: RiskCheckRequest, _limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.is_volatile_altcoin:
        return _rule(
            RiskRuleId.VOLATILE_ALTCOIN_REDUCED_SIZE,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            "Volatile altcoin: reduce size and leverage.",
        )
    return None


def check_extreme_funding(
    req: RiskCheckRequest, limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.funding_rate is not None and abs(req.funding_rate) >= limits.extreme_funding_rate:
        return _rule(
            RiskRuleId.EXTREME_FUNDING,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            "Extreme funding rate; confirm with structure before trading.",
        )
    return None


def check_low_volume(
    req: RiskCheckRequest, limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.volume_24h is not None and req.volume_24h < limits.min_volume_24h:
        return _rule(
            RiskRuleId.LOW_VOLUME,
            RiskAction.WARN,
            RiskSeverity.LOW,
            "24h volume is below the minimum liquidity threshold.",
        )
    return None


def check_weekend(
    _req: RiskCheckRequest, _limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.is_weekend:
        return _rule(
            RiskRuleId.WEEKEND_CONDITION,
            RiskAction.WARN,
            RiskSeverity.LOW,
            "Weekend conditions: prefer weekday trading when edge is strongest.",
        )
    return None


def check_sleep_test(
    req: RiskCheckRequest, _limits: RiskLimits, _ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if req.sleep_test_passed is False:
        return _rule(
            RiskRuleId.SLEEP_TEST,
            RiskAction.BLOCK,
            RiskSeverity.HIGH,
            "Sleep test failed: position size or leverage is too large for mental capital.",
        )
    return None


def check_overtrading(
    req: RiskCheckRequest, limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.overtrading or ctx.trades_today >= limits.max_trades_per_day:
        return _rule(
            RiskRuleId.OVERTRADING,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            "Overtrading warning: reduce frequency and protect mental capital.",
        )
    return None


def check_risk_percent(
    req: RiskCheckRequest, _limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    """Enforce grade-policy risk only for autonomous sizing.

    Human paper execution copies ``max_risk_per_trade_percent`` (the settings
    API allows up to 10%) into ``risk_percent``. Strategy replays pass 2-5%.
    Those callers leave ``autonomous_sizing`` false, and this rule stays idle
    so their previous allow path is unchanged. Autonomous sizing opts in and
    is then held to the code ceiling, the active profile cap, and the PR 258
    ledger independence check.
    """

    if not ctx.autonomous_sizing:
        return None
    ledger_supplied = (
        ctx.ledger_max_symbol_notional is not None or ctx.ledger_max_notional is not None
    )
    if req.risk_percent is None and not ledger_supplied:
        return None
    if req.risk_percent is not None:
        cap = HARD_MAX_RISK_PER_TRADE_PERCENT
        if ctx.profile_risk_percent_cap is not None:
            if ctx.profile_risk_percent_cap <= 0:
                return _rule(
                    RiskRuleId.RISK_PERCENT,
                    RiskAction.BLOCK,
                    RiskSeverity.CRITICAL,
                    "Profile risk cap is unreadable; risk percent fails closed.",
                )
            cap = min(cap, ctx.profile_risk_percent_cap)
        if req.risk_percent > cap:
            return _rule(
                RiskRuleId.RISK_PERCENT,
                RiskAction.BLOCK,
                RiskSeverity.HIGH,
                f"Risk percent {req.risk_percent} exceeds the cap {cap}.",
            )
    if ledger_supplied:
        if ctx.ledger_max_symbol_notional is None or ctx.ledger_max_notional is None:
            return _rule(
                RiskRuleId.RISK_PERCENT,
                RiskAction.BLOCK,
                RiskSeverity.CRITICAL,
                "Account ledger caps are incomplete; symbol cap fails closed.",
            )
        try:
            equal = ledger_symbol_cap_equals_notional(
                ctx.ledger_max_symbol_notional, ctx.ledger_max_notional
            )
        except RiskPolicyRejected:
            return _rule(
                RiskRuleId.RISK_PERCENT,
                RiskAction.BLOCK,
                RiskSeverity.CRITICAL,
                "Account ledger caps are unreadable; symbol cap fails closed.",
            )
        if equal:
            return _rule(
                RiskRuleId.RISK_PERCENT,
                RiskAction.BLOCK,
                RiskSeverity.HIGH,
                "Ledger symbol cap is not independent of total notional.",
            )
    return None


def check_green_day_protection(
    _req: RiskCheckRequest, _limits: RiskLimits, ctx: RiskEvaluationContext
) -> TriggeredRule | None:
    if ctx.protect_green_day:
        return _rule(
            RiskRuleId.STRONG_GREEN_DAY,
            RiskAction.WARN,
            RiskSeverity.MEDIUM,
            "Strong green day protection active: avoid giving back profits.",
        )
    return None


def default_is_weekend() -> bool:
    """Return True when UTC day is Saturday or Sunday."""
    return datetime.now(UTC).weekday() >= 5


ALL_RULES: tuple[RuleFn, ...] = (
    check_kill_switch,
    check_no_stop_loss,
    check_invalid_stop_loss,
    check_max_leverage,
    check_max_position_size,
    check_daily_loss_lock,
    check_weekly_loss,
    check_unsupported_coin,
    check_countertrend,
    check_volatile_alt,
    check_extreme_funding,
    check_low_volume,
    check_weekend,
    check_sleep_test,
    check_overtrading,
    check_green_day_protection,
    check_risk_percent,
)
