"""Automatic review of closed bot demo trades (Sofien, 27 Nov 2026).

Twelve candles after a bot trade closes, the review reads that trade's candles
from BloFin (the venue it ran on, public market data, no order call) and records:

- the best and the worst price while the trade was open (MFE / MAE), in R;
- how far price went after the exit, in R, over the next ``LOOKAHEAD_BARS``;
- one plain reason code for the outcome:

  ``won``                     the trade ended in profit;
  ``won_left_on_table``       in profit, and price ran at least 1R further after the exit;
  ``gave_back``               a loss after price had been 1R or more in our favour;
  ``stop_hunted``             a loss, then price went 1R or more our way within 12 candles;
  ``level_broke``             a loss, and price kept going 1R or more past the stop;
  ``no_follow_through``       a loss with no clear move either way (chop).

Stored on the Journal row: ``mfe_price`` / ``mae_price``, ``excursion_source =
"trade_review"``, ``excursion_computed_at``, the candle count, a one-line summary in
``excursion_freshness_note``, and a ``review:<code>`` tag. Nothing is deleted and no
existing value is overwritten; a trade is reviewed once. The rows are a few hundred
bytes each, kept with the trade (the Journal is kept forever). The in-app agent and
the weekly summary read the tags and fields.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JournalTrade
from app.schemas.common import JournalTradeSource, JournalTradeStatus, Timeframe, TradeDirection

logger = structlog.get_logger(__name__)

LOOKAHEAD_BARS = 12
EXCURSION_SOURCE = "trade_review"
REVIEW_TAG_PREFIX = "review:"
_STEP = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
}
REASONS = {
    "won": "Won.",
    "won_left_on_table": "Won, and price ran at least 1R further after the exit.",
    "gave_back": "Lost after being 1R or more in profit: the trade gave back its gains.",
    "stop_hunted": "Stopped out, then price moved 1R or more our way: stop hunted.",
    "level_broke": "Stopped out and price kept going past the stop: the level broke.",
    "no_follow_through": "Lost with no clear move either way: no follow-through.",
}


@dataclass(frozen=True, slots=True)
class Bar:
    start: datetime
    high: Decimal
    low: Decimal


@dataclass(frozen=True, slots=True)
class TradeReview:
    mfe_price: Decimal
    mae_price: Decimal
    mfe_r: Decimal
    mae_r: Decimal
    result_r: Decimal
    after_best_r: Decimal
    after_worst_r: Decimal
    code: str
    candles: int

    @property
    def summary(self) -> str:
        return (
            f"{REASONS[self.code]} Best {self.mfe_r:+.2f}R, worst {self.mae_r:+.2f}R while "
            f"open; after exit best {self.after_best_r:+.2f}R, worst {self.after_worst_r:+.2f}R."
        )


def review_from_bars(
    *,
    direction: TradeDirection,
    entry: Decimal,
    stop: Decimal,
    exit_price: Decimal,
    entry_time: datetime,
    exit_time: datetime,
    bars: Sequence[Bar],
    step: timedelta,
) -> TradeReview | None:
    """Pure review from candles. None when the risk or the candles are missing."""

    long = direction is TradeDirection.LONG
    risk = entry - stop if long else stop - entry
    if risk <= 0:
        return None
    inside = [b for b in bars if b.start + step > entry_time and b.start < exit_time]
    after = [b for b in bars if b.start >= exit_time][:LOOKAHEAD_BARS]
    if not inside or len(after) < LOOKAHEAD_BARS:
        return None

    def r_of(price: Decimal) -> Decimal:
        return ((price - entry) if long else (entry - price)) / risk

    best = max(b.high for b in inside) if long else min(b.low for b in inside)
    worst = min(b.low for b in inside) if long else max(b.high for b in inside)
    after_best = max(b.high for b in after) if long else min(b.low for b in after)
    after_worst = min(b.low for b in after) if long else max(b.high for b in after)
    result = r_of(exit_price)
    mfe, mae = r_of(best), r_of(worst)
    after_best_r, after_worst_r = r_of(after_best), r_of(after_worst)
    if result > 0:
        code = "won_left_on_table" if after_best_r >= result + 1 else "won"
    elif mfe >= 1:
        code = "gave_back"
    elif after_best_r >= 1:
        code = "stop_hunted"
    elif after_worst_r <= -2:
        code = "level_broke"
    else:
        code = "no_follow_through"
    q = Decimal("0.01")
    return TradeReview(
        mfe_price=best,
        mae_price=worst,
        mfe_r=mfe.quantize(q),
        mae_r=mae.quantize(q),
        result_r=result.quantize(q),
        after_best_r=after_best_r.quantize(q),
        after_worst_r=after_worst_r.quantize(q),
        code=code,
        candles=len(inside) + len(after),
    )


def bars_from_blofin(rows: Any) -> list[Bar]:
    """BloFin ``/api/v1/market/candles`` rows: [ts ms, open, high, low, close, ...]."""

    from datetime import UTC

    out = []
    for row in rows if isinstance(rows, list) else []:
        try:
            start = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
            out.append(Bar(start, Decimal(str(row[2])), Decimal(str(row[3]))))
        except (IndexError, TypeError, ValueError, ArithmeticError):
            continue
    return sorted(out, key=lambda b: b.start)


def review_closed_demo_trades(
    session: Session,
    *,
    organization_id: object,
    fetch_rows: Callable[[str, str, int], Any],
    now: datetime,
    limit: int = 3,
) -> int:
    """Review up to ``limit`` closed bot trades that are due. Returns how many."""

    from app.providers.exchange.mapping import timeframe_to_bar

    due = session.scalars(
        select(JournalTrade)
        .where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.source == JournalTradeSource.PAPER_EXECUTION,
            JournalTrade.execution_lifecycle_id.is_not(None),
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.excursion_computed_at.is_(None),
            JournalTrade.exit_time.is_not(None),
        )
        .order_by(JournalTrade.exit_time)
        .limit(20)
    ).all()
    done = 0
    for trade in due:
        step = _STEP.get(str(trade.timeframe))
        entry, stop, exit_price = trade.entry_price, trade.planned_stop_price, trade.exit_price
        if step is None or entry is None or stop is None or exit_price is None:
            continue
        exit_time = _aware(trade.exit_time)
        entry_time = _aware(trade.entry_time)
        if now < exit_time + step * (LOOKAHEAD_BARS + 1):
            continue  # not enough candles after the exit yet
        needed = int((now - entry_time) / step) + 3
        if needed > 1000:
            _mark(trade, now, None, "too old to review from recent candles")
            done += 1
            continue
        try:
            rows = fetch_rows(
                trade.symbol, timeframe_to_bar(Timeframe(str(trade.timeframe))), needed
            )
        except Exception:
            logger.warning("trade_review_candles_unavailable", symbol=trade.symbol)
            continue
        review = review_from_bars(
            direction=trade.direction,
            entry=entry,
            stop=stop,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            bars=bars_from_blofin(rows),
            step=step,
        )
        if review is None:
            continue
        _mark(trade, now, review, review.summary)
        done += 1
        if done >= limit:
            break
    return done


def _mark(trade: JournalTrade, now: datetime, review: TradeReview | None, note: str) -> None:
    if review is not None:
        if trade.mfe_price is None:
            trade.mfe_price = review.mfe_price
        if trade.mae_price is None:
            trade.mae_price = review.mae_price
        trade.excursion_candle_count = review.candles
        trade.tags = [*(trade.tags or []), f"{REVIEW_TAG_PREFIX}{review.code}"]
    trade.excursion_source = trade.excursion_source or EXCURSION_SOURCE
    trade.excursion_data_source = "blofin_demo_candles"
    trade.excursion_freshness_note = note[:500]
    trade.excursion_computed_at = now


def _aware(moment: datetime | None) -> datetime:
    from datetime import UTC

    assert moment is not None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
