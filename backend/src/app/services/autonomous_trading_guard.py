"""Enforce persisted autonomous trading settings on autonomous decisions."""

from __future__ import annotations

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
    # Unsaved settings keep the previous loop. Trade count, strategy, and coin
    # apply only after the user saves a row. The saved daily loss percent is
    # display only: the 10% account equity stop is the only daily brake
    # (AT-ADR-152).
    if settings.using_defaults:
        return None
    mismatch = autonomous_target_matches(settings, target)
    if mismatch is not None:
        return mismatch
    if autonomous_max_trades_reached(settings, snapshot):
        return "autonomous_max_trades_per_day"
    return None
