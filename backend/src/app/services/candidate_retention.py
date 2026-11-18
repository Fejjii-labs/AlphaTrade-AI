"""7-day candidate retention: summarize every old candidate, then purge the missed ones.

A candidate is *taken* when a plan, plan lineage, plan revision, journal trade,
Brain setup trade link or trade-linked paper evaluation row points at it. Taken
candidates are trade history: they get a summary and are never deleted (their
plan FKs would block it anyway). Missed candidates get a summary and lose their
full rows (candidate, transitions, keys, eligibility decisions) in the same
transaction, so a summary always exists for a purged candidate.

The would-have outcome is derived only when cheap and honest: a closed journal
trade's own result, or the first touch of planned stop/target in stored
``historical_candles`` after detection. Nothing is imputed when data is missing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JournalTrade
from app.db.trade_candidate_summaries import TradeCandidateSummaryRow
from app.services.db_retention import (
    CANDIDATE_TABLES,
    RetentionResult,
    _bound_statement,
)

logger = structlog.get_logger(__name__)

_TAKEN = (
    "(EXISTS (SELECT 1 FROM canonical_trade_plan_roots AS r WHERE r.candidate_id = c.candidate_id)"
    " OR EXISTS (SELECT 1 FROM canonical_trade_plan_lineage AS l"
    " WHERE l.candidate_id = c.candidate_id)"
    " OR EXISTS (SELECT 1 FROM trade_plan_revisions AS p"
    " WHERE p.canonical_candidate_id = c.candidate_id OR p.candidate_id = c.candidate_id)"
    " OR EXISTS (SELECT 1 FROM journal_trades AS j WHERE j.candidate_id = c.candidate_id)"
    " OR EXISTS (SELECT 1 FROM strategy_brain_setups AS b"
    " WHERE b.candidate_id = c.candidate_id AND b.journal_trade_id IS NOT NULL)"
    " OR EXISTS (SELECT 1 FROM paper_evaluation_observations AS e"
    " WHERE e.candidate_id = c.candidate_id"
    " AND (e.journal_trade_id IS NOT NULL OR e.filled OR e.plan_approved)))"
)
# Old enough, no longer valid, and not yet summarized.
_PENDING = (
    "c.created_at < :cutoff AND c.valid_until < :now AND NOT EXISTS "
    "(SELECT 1 FROM trade_candidate_summaries AS s WHERE s.candidate_id = c.candidate_id)"
)
_CLOSED_RESULTS = {"win", "loss", "breakeven"}
_MAX_CANDLES = 2000


@dataclass(frozen=True, slots=True)
class _Levels:
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)).lower()


def _first_target(targets: object) -> Decimal | None:
    if not isinstance(targets, list):
        return None
    for item in targets:
        if isinstance(item, dict):
            price = _decimal(item.get("price"))
            if price is not None:
                return price
    return None


def first_touch(direction: str, levels: _Levels, bars: Iterable[Any]) -> str | None:
    """Which planned level price reached first. ``bars`` are (high, low) in time order."""

    if levels.stop is None or levels.target is None:
        return None
    seen = False
    for raw_high, raw_low in bars:
        seen = True
        high, low = Decimal(str(raw_high)), Decimal(str(raw_low))
        if direction == "short":
            hit_target, hit_stop = low <= levels.target, high >= levels.stop
        else:
            hit_target, hit_stop = high >= levels.target, low <= levels.stop
        if hit_target and hit_stop:
            return "ambiguous"
        if hit_target:
            return "target_first"
        if hit_stop:
            return "stop_first"
    return "neither" if seen else None


def _summarize(
    session: Session, candidate: Any, *, taken: bool, window: timedelta, now: datetime
) -> TradeCandidateSummaryRow:
    setup = session.execute(
        text(
            "SELECT id, symbol, payload, journal_trade_id FROM strategy_brain_setups "
            "WHERE candidate_id = :id ORDER BY observed_at DESC LIMIT 1"
        ),
        {"id": candidate.candidate_id},
    ).first()
    trade = session.scalars(
        select(JournalTrade)
        .where(JournalTrade.candidate_id == candidate.candidate_id)
        .order_by(JournalTrade.created_at)
        .limit(1)
    ).first()
    if trade is None and setup is not None and setup.journal_trade_id is not None:
        trade = session.get(JournalTrade, setup.journal_trade_id)
    if trade is None and taken:
        trade_id = session.execute(
            text(
                "SELECT journal_trade_id FROM paper_evaluation_observations "
                "WHERE candidate_id = :id AND journal_trade_id IS NOT NULL LIMIT 1"
            ),
            {"id": candidate.candidate_id},
        ).scalar()
        trade = session.get(JournalTrade, trade_id) if trade_id is not None else None
    notified = (
        session.execute(
            text("SELECT 1 FROM telegram_paper_notifications WHERE candidate_id = :id LIMIT 1"),
            {"id": candidate.candidate_id},
        ).first()
        is not None
    )
    payload = setup.payload if setup is not None and isinstance(setup.payload, dict) else {}
    raw_plan = payload.get("trade_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    levels = _Levels(
        _decimal(plan.get("entry")), _decimal(plan.get("stop")), _decimal(plan.get("take_profit"))
    )
    if trade is not None:
        levels = _Levels(
            _decimal(trade.planned_entry_price) or levels.entry,
            _decimal(trade.planned_stop_price) or levels.stop,
            _first_target(trade.planned_targets) or levels.target,
        )
    direction = str(candidate.direction).lower()
    symbol = (setup.symbol if setup is not None else None) or candidate.evidence_instrument
    outcome: str | None = None
    source = "unavailable"
    pnl: Decimal | None = None
    if taken:
        result = _enum_value(trade.result) if trade is not None else None
        if trade is not None and result in _CLOSED_RESULTS:
            outcome, source, pnl = result, "journal_trade", trade.net_pnl
        else:
            source = "trade_open"
    else:
        detected = candidate.created_at
        bars = session.execute(
            text(
                "SELECT high, low FROM historical_candles WHERE symbol = :symbol "
                "AND timeframe = :timeframe AND open_time >= :start AND open_time < :end "
                "ORDER BY open_time LIMIT :limit"
            ),
            {
                "symbol": symbol,
                "timeframe": candidate.timeframe,
                "start": detected,
                "end": min(detected + window, now),
                "limit": _MAX_CANDLES,
            },
        ).all()
        outcome = first_touch(direction, levels, bars)
        if outcome is not None:
            source = "candles"
    return TradeCandidateSummaryRow(
        candidate_id=candidate.candidate_id,
        organization_id=candidate.organization_id,
        symbol=symbol,
        venue=candidate.evidence_venue,
        timeframe=candidate.timeframe,
        strategy_version_id=candidate.strategy_version_id,
        setup_definition_id=candidate.setup_definition_id,
        brain_setup_id=setup.id if setup is not None else None,
        detected_at=candidate.created_at,
        direction=direction,
        final_state=candidate.state,
        planned_entry=levels.entry,
        planned_stop=levels.stop,
        planned_target=levels.target,
        notified=notified,
        taken=taken,
        journal_trade_id=trade.id if trade is not None else None,
        outcome=outcome,
        outcome_source=source,
        realized_net_pnl=pnl,
        summarized_at=now,
    )


def _refresh_open_trades(session: Session, *, limit: int) -> int:
    """Taken candidates summarized before their trade closed pick up the result later."""

    rows = session.scalars(
        select(TradeCandidateSummaryRow)
        .where(TradeCandidateSummaryRow.outcome_source == "trade_open")
        .order_by(TradeCandidateSummaryRow.detected_at)
        .limit(limit)
    ).all()
    updated = 0
    for row in rows:
        if row.journal_trade_id is not None:
            trade = session.get(JournalTrade, row.journal_trade_id)
        else:
            trade = session.scalars(
                select(JournalTrade).where(JournalTrade.candidate_id == row.candidate_id).limit(1)
            ).first()
            if trade is not None:
                row.journal_trade_id = trade.id
        result = _enum_value(trade.result) if trade is not None else None
        if trade is not None and result in _CLOSED_RESULTS:
            row.outcome, row.outcome_source, row.realized_net_pnl = (
                result,
                "journal_trade",
                trade.net_pnl,
            )
            updated += 1
    return updated


def _delete_candidates(session: Session, ids: list[uuid.UUID]) -> int:
    if session.get_bind().dialect.name == "postgresql":
        # Transaction-local: the a29 trigger guard admits DELETE only here.
        session.execute(
            text("SELECT set_config('alphatrade.retention_purge', 'canonical_candidates', true)")
        )
    deleted = 0
    for table in CANDIDATE_TABLES:
        statement = text(f"DELETE FROM {table} WHERE candidate_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        outcome = session.execute(statement, {"ids": ids})
        if table == "canonical_candidates":
            deleted = max(int(getattr(outcome, "rowcount", 0) or 0), 0)
    return deleted


def purge_candidates(
    session_factory: Callable[[], Session],
    settings: Settings,
    *,
    now: datetime,
    dry_run: bool,
    should_stop: Callable[[], bool] = lambda: False,
) -> RetentionResult:
    window = timedelta(days=settings.retention_candidate_days)
    cutoff = now - window
    batch = min(settings.retention_batch_size, 500)
    params = {"cutoff": cutoff, "now": now}
    session = session_factory()
    try:
        _bound_statement(session)
        matched = int(
            session.execute(
                text(
                    f"SELECT count(*) FROM canonical_candidates AS c "
                    f"WHERE {_PENDING} AND NOT {_TAKEN}"
                ),
                params,
            ).scalar_one()
        )
        session.commit()
        if dry_run:
            return RetentionResult(
                "canonical_candidates", "canonical_candidates", cutoff, matched, 0, True, False
            )
        summarized = deleted = 0
        complete = False
        for _ in range(settings.retention_max_batches_per_table):
            if should_stop():
                break
            _bound_statement(session)
            rows = session.execute(
                text(
                    "SELECT c.candidate_id, c.organization_id, c.strategy_version_id, "
                    "c.setup_definition_id, c.direction, c.evidence_venue, "
                    "c.evidence_instrument, c.timeframe, c.state, c.created_at, "
                    f"{_TAKEN} AS taken FROM canonical_candidates AS c WHERE {_PENDING} "
                    "ORDER BY c.created_at LIMIT :batch"
                ),
                {**params, "batch": batch},
            ).all()
            for row in rows:
                session.add(_summarize(session, row, taken=bool(row.taken), window=window, now=now))
            session.flush()
            missed = [row.candidate_id for row in rows if not row.taken]
            if missed:
                deleted += _delete_candidates(session, missed)
            session.commit()
            summarized += len(rows)
            if len(rows) < batch:
                complete = True
                break
        _refresh_open_trades(session, limit=batch)
        session.commit()
        return RetentionResult(
            "canonical_candidates",
            "canonical_candidates",
            cutoff,
            matched,
            deleted,
            False,
            complete,
            summarized,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
