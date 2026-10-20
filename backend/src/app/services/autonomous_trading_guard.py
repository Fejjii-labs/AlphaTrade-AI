"""Enforce persisted autonomous trading settings on autonomous decisions."""

from __future__ import annotations

from decimal import Decimal

from app.services.autonomous_trading_settings_service import ResolvedAutonomousSettings
from app.services.risk.daily_risk_accounting import AuthoritativeDailySnapshot
from app.workers.watcher_paper_targets import PaperScanTarget


def autonomous_target_matches(
    settings: ResolvedAutonomousSettings,
    target: PaperScanTarget,
) -> str | None:
    if settings.strategy_id is None or not settings.symbol:
        return "autonomous_settings_incomplete"
    if target.strategy_id != settings.strategy_id:
        return "autonomous_strategy_mismatch"
    if target.symbol.strip().upper() != settings.symbol:
        return "autonomous_symbol_mismatch"
    return None


def autonomous_daily_loss_exceeded(
    settings: ResolvedAutonomousSettings,
    snapshot: AuthoritativeDailySnapshot,
) -> bool:
    if snapshot.account_equity <= 0:
        return True
    loss_percent = Decimal("0")
    if snapshot.realized_pnl < 0:
        loss_percent = (-snapshot.realized_pnl / snapshot.account_equity) * Decimal("100")
    return loss_percent >= settings.daily_loss_limit_percent


def autonomous_max_trades_reached(
    settings: ResolvedAutonomousSettings,
    snapshot: AuthoritativeDailySnapshot,
) -> bool:
    return snapshot.trade_count >= settings.max_trades_per_day


def autonomous_precheck_refusal(
    settings: ResolvedAutonomousSettings,
    target: PaperScanTarget,
    snapshot: AuthoritativeDailySnapshot,
) -> str | None:
    if not settings.using_defaults:
        mismatch = autonomous_target_matches(settings, target)
        if mismatch is not None:
            return mismatch
    if autonomous_daily_loss_exceeded(settings, snapshot):
        return "autonomous_daily_loss_limit"
    if autonomous_max_trades_reached(settings, snapshot):
        return "autonomous_max_trades_per_day"
    return None


def effective_autonomous_daily_loss_percent(
    settings: ResolvedAutonomousSettings,
    profile_daily_loss_percent: Decimal,
) -> Decimal:
    """User autonomous cap, still bounded by profile and the code ceiling."""

    return min(
        settings.daily_loss_limit_percent,
        profile_daily_loss_percent,
        Decimal("6"),
    )
