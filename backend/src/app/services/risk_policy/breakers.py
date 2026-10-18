"""Daily-loss, drawdown, and consecutive-loss breakers.

Drawdown from the equity peak trips the kill switch and stays tripped until
a human reset, and only once equity is back inside the threshold. Daily loss
and consecutive losses pause new trades until the next UTC day.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from app.services.risk_policy.clock import next_utc_day
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import PolicyActor, PolicyChannel


@dataclass(frozen=True)
class BreakerConfig:
    daily_loss_limit_percent: Decimal
    drawdown_breaker_percent: Decimal
    consecutive_loss_limit: int


@dataclass(frozen=True)
class BreakerSnapshot:
    equity: Decimal
    equity_peak: Decimal
    reference_equity: Decimal
    realized_pnl_today: Decimal
    consecutive_losses: int
    drawdown_latched: bool
    now: datetime


@dataclass(frozen=True)
class BreakerVerdict:
    daily_loss_active: bool
    drawdown_active: bool
    consecutive_loss_active: bool
    trip_kill_switch: bool
    paused_until: datetime | None
    equity_peak: Decimal
    refuse_new_trades: bool
    human_reset_required: bool

    @property
    def active(self) -> bool:
        return self.refuse_new_trades


def evaluate_breakers(config: BreakerConfig, snapshot: BreakerSnapshot) -> BreakerVerdict:
    """Return the breaker state. Missing or non-positive equity refuses."""

    _require_money(snapshot.equity, "equity_invalid")
    _require_money(snapshot.equity_peak, "equity_peak_invalid")
    _require_money(snapshot.reference_equity, "reference_equity_invalid")
    if snapshot.now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if (
        not isinstance(snapshot.consecutive_losses, int)
        or isinstance(snapshot.consecutive_losses, bool)
        or snapshot.consecutive_losses < 0
    ):
        raise RiskPolicyRejected("consecutive_losses_invalid")
    peak = snapshot.equity_peak
    if not snapshot.drawdown_latched and snapshot.equity > peak:
        peak = snapshot.equity
    drawdown_percent = (peak - snapshot.equity) / peak * Decimal("100")
    drawdown_hit = snapshot.drawdown_latched or drawdown_percent >= config.drawdown_breaker_percent
    daily_limit = snapshot.reference_equity * config.daily_loss_limit_percent / Decimal("100")
    daily_hit = snapshot.realized_pnl_today <= -daily_limit and snapshot.realized_pnl_today < 0
    consecutive_hit = snapshot.consecutive_losses >= config.consecutive_loss_limit
    pause = next_utc_day(snapshot.now) if daily_hit or consecutive_hit else None
    return BreakerVerdict(
        daily_loss_active=daily_hit,
        drawdown_active=drawdown_hit,
        consecutive_loss_active=consecutive_hit,
        trip_kill_switch=drawdown_hit,
        paused_until=pause,
        equity_peak=peak,
        refuse_new_trades=daily_hit or drawdown_hit or consecutive_hit,
        human_reset_required=drawdown_hit,
    )


def reset_drawdown(
    actor: PolicyActor,
    config: BreakerConfig,
    snapshot: BreakerSnapshot,
) -> BreakerSnapshot:
    """Clear the drawdown latch. Agents cannot reset it, and a live breach cannot."""

    if not actor.authenticated or actor.channel is not PolicyChannel.HUMAN:
        raise RiskPolicyRejected("drawdown_reset_requires_human")
    verdict = evaluate_breakers(
        config, replace(snapshot, drawdown_latched=False, equity_peak=snapshot.equity_peak)
    )
    # evaluate_breakers with the latch cleared still reports drawdown when the
    # live percentage is outside the threshold. That reset does not stick.
    if verdict.drawdown_active:
        raise RiskPolicyRejected("drawdown_still_active")
    return replace(snapshot, drawdown_latched=False, equity_peak=snapshot.equity)


def _require_money(value: Decimal, code: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise RiskPolicyRejected(code)
