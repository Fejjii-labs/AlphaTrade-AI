"""Enforce persisted autonomous trading settings on autonomous decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.schemas.execution_protocol import ExecutionReceiptState
from app.services.autonomous_trading_settings_service import (
    APPROVED_LIFECYCLE_STATES,
    ResolvedAutonomousSettings,
)
from app.services.risk.daily_risk_accounting import AuthoritativeDailySnapshot
from app.workers.watcher_paper_targets import PaperScanTarget

SYMBOL_POSITION_OPEN = "autonomous_symbol_position_open"
# An entry in one of these receipt states with nothing filled never opened a position.
_ENDED_UNFILLED = (
    ExecutionReceiptState.BLOCKED,
    ExecutionReceiptState.BLOCKED_BEFORE_DISPATCH,
    ExecutionReceiptState.REJECTED,
    ExecutionReceiptState.EXPIRED,
    ExecutionReceiptState.CANCELLED,
    ExecutionReceiptState.ABSENCE_PROVEN,
)


@dataclass(frozen=True)
class AutonomousStrategyStatus:
    """What the database says about the strategy behind one target, right now."""

    strategy_id: uuid.UUID
    enabled: bool
    approved: bool
    paper_eligible: bool


def load_autonomous_strategy_status(
    session: Session, target: PaperScanTarget
) -> AutonomousStrategyStatus | None:
    """Enabled flag, target version lifecycle and paper gate. None when not the user's."""

    from app.db.models import UserStrategy, UserStrategyVersion
    from app.services.strategy_versioning import StrategyVersioningService

    strategy = session.get(UserStrategy, target.strategy_id)
    if (
        strategy is None
        or strategy.organization_id != target.organization_id
        or strategy.user_id != target.user_id
    ):
        return None
    version = session.get(UserStrategyVersion, target.strategy_version_id)
    approved = False
    if version is not None and version.strategy_id == strategy.id:
        lifecycle = StrategyVersioningService(session).latest_lifecycle_event_for_version(
            version.id
        )
        approved = lifecycle is not None and lifecycle.new_state in APPROVED_LIFECYCLE_STATES
    return AutonomousStrategyStatus(
        strategy_id=strategy.id,
        enabled=bool(strategy.enabled),
        approved=approved,
        paper_eligible=bool(strategy.paper_eligible),
    )


def autonomous_target_matches(
    settings: ResolvedAutonomousSettings,
    target: PaperScanTarget,
    strategy_status: AutonomousStrategyStatus | None,
    *,
    require_paper_eligible: bool = True,
    any_symbol: bool = False,
) -> str | None:
    selected = settings.strategy_scope == "selected"
    # Demo data mode (services/demo_data_mode.py) trades every scanned coin.
    if (selected and not settings.strategy_ids) or (not settings.symbol and not any_symbol):
        return "autonomous_settings_incomplete"
    if selected and target.strategy_id not in settings.strategy_ids:
        return "autonomous_strategy_mismatch"
    if not any_symbol and target.symbol.strip().upper() != settings.symbol:
        return "autonomous_symbol_mismatch"
    if strategy_status is None or strategy_status.strategy_id != target.strategy_id:
        return "autonomous_strategy_status_unverified"
    if not strategy_status.enabled:
        return "autonomous_strategy_disabled"
    if not strategy_status.approved:
        return "autonomous_strategy_not_approved"
    # BloFin demo needs no paper eligibility or backtest (owner decision
    # 2026-11-19): enabled plus approved/active is enough. Every other path
    # keeps the paper gate.
    if require_paper_eligible and not strategy_status.paper_eligible:
        return "autonomous_strategy_not_paper_eligible"
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
    *,
    strategy_status: AutonomousStrategyStatus | None,
    require_paper_eligible: bool = True,
    any_symbol: bool = False,
) -> str | None:
    # Unsaved settings keep the previous loop. Trade count, strategy, and coin
    # apply only after the user saves a row. The saved daily loss percent is
    # display only: the 10% account equity stop is the only daily brake
    # (AT-ADR-152).
    if settings.using_defaults:
        return None
    mismatch = autonomous_target_matches(
        settings,
        target,
        strategy_status,
        require_paper_eligible=require_paper_eligible,
        any_symbol=any_symbol,
    )
    if mismatch is not None:
        return mismatch
    if autonomous_max_trades_reached(settings, snapshot):
        return "autonomous_max_trades_per_day"
    return None


def _normalized_symbol(symbol: str) -> str:
    return symbol.replace("-", "").replace("/", "").strip().upper()


def open_symbol_position_refusal(
    session: Session,
    target: PaperScanTarget,
    *,
    candidate_id: uuid.UUID,
) -> str | None:
    """One open or pending autonomous position per coin, whichever strategy or direction.

    The first intent that opens a position on a coin wins. Any other candidate on
    that coin is refused until the position closes. The candidate that opened it
    may still replay (idempotent). A pending entry holds the coin too: a PLANNED
    journal (unless its entry provably ended unfilled) and an unresolved BloFin
    demo entry, which has no journal until it fills. On Postgres a transaction
    lock per user and coin serialises two loops checking at the same moment; the
    paper loop holds it until its fill commits.
    """

    from app.db.models import (
        ExecutionCommand,
        ExecutionProjection,
        ExecutionReceipt,
        JournalTrade,
        Position,
        TradePlanRevision,
    )
    from app.schemas.common import JournalTradeSource, JournalTradeStatus, PositionStatus
    from app.services.demo_account_history import unresolved_demo_entries

    symbol = _normalized_symbol(target.symbol)
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"autonomous-symbol:{target.organization_id}:{target.user_id}:{symbol}"},
        )
    journals = session.scalars(
        select(JournalTrade).where(
            JournalTrade.organization_id == target.organization_id,
            JournalTrade.user_id == target.user_id,
            JournalTrade.status.in_((JournalTradeStatus.OPEN, JournalTradeStatus.PLANNED)),
            JournalTrade.source.in_(
                (
                    JournalTradeSource.PAPER_EXECUTION,
                    JournalTradeSource.MANUAL_DEMO_TEST,
                    JournalTradeSource.BLOFIN_DEMO_MANUAL,
                )
            ),
        )
    ).all()
    # A PLANNED entry the venue (or a block) provably ended with no fill is free.
    planned = {
        trade.execution_lifecycle_id
        for trade in journals
        if trade.status is JournalTradeStatus.PLANNED and trade.execution_lifecycle_id is not None
    }
    ended = (
        set(
            session.scalars(
                select(ExecutionReceipt.command_id)
                .join(ExecutionProjection, ExecutionProjection.receipt_id == ExecutionReceipt.id)
                .where(
                    ExecutionReceipt.command_id.in_(planned),
                    ExecutionProjection.filled_quantity == 0,
                    ExecutionProjection.state.in_(_ENDED_UNFILLED),
                )
            ).all()
        )
        if planned
        else set()
    )
    for trade in journals:
        if (
            _normalized_symbol(trade.symbol) == symbol
            and trade.candidate_id != candidate_id
            and trade.execution_lifecycle_id not in ended
        ):
            return SYMBOL_POSITION_OPEN
    # A sent or uncertain BloFin demo entry has no journal until it fills.
    pending = session.execute(
        unresolved_demo_entries(
            select(TradePlanRevision.execution_instrument, TradePlanRevision.candidate_id),
            organization_id=target.organization_id,
        ).where(ExecutionCommand.user_id == target.user_id)
    ).all()
    for instrument, pending_candidate in pending:
        if _normalized_symbol(instrument) == symbol and pending_candidate != candidate_id:
            return SYMBOL_POSITION_OPEN
    # The replaying candidate's own position is not a second position.
    own = {trade.linked_position_id for trade in journals if trade.candidate_id == candidate_id}
    positions = session.execute(
        select(Position.id, Position.symbol).where(
            Position.organization_id == target.organization_id,
            Position.user_id == target.user_id,
            Position.status == PositionStatus.OPEN,
        )
    ).all()
    for position_id, position_symbol in positions:
        if position_id not in own and _normalized_symbol(position_symbol) == symbol:
            return SYMBOL_POSITION_OPEN
    return None
