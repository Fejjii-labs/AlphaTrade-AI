"""Closed round-trip performance from stored BloFin demo fills.

A trade is one position returning to flat. Position identity is
``(instrument, position_side)`` with ``position_side`` of ``long``, ``short``,
or ``net``. Partial fills accumulate until the signed size is zero. A fill that
crosses zero closes the current trade with a proportional share of that fill's
``fillPnl`` and signed fee, and the remainder opens the next trade.

The decisive result is native ``fillPnl`` only. The original signed fee is
recorded and is not added: fee sign, and whether ``fillPnl`` is gross or net of
fees or funding, are not independently verified. A trade still needs a fee on
every slice, one fee currency, and that currency equal to the stored settlement
currency. Otherwise the round-trip is unresolved. Order ``pnl`` is not added.
An order whose ``filled_quantity`` disagrees with the sum of its fills makes
those round-trips unresolved. The caller must already know the series starts
flat. This function does not prove that.

``win_rate`` is wins / (wins + losses). A net of zero is a scratch: it counts
as a trade and is not a win or a loss. This is demo-account history, not a
forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

_MONEY = Decimal("0.00000001")
_RATE = Decimal("0.0001")
METHOD = "closed-round-trip-fillpnl-fee-recorded/v1"


@dataclass(frozen=True, slots=True)
class ActivityFill:
    """One stored fill. Quantities are positive contract sizes."""

    native_id: str
    order_id: str
    instrument: str
    side: Literal["buy", "sell"]
    position_side: Literal["net", "long", "short"]
    quantity: Decimal
    fee: Decimal | None
    fee_currency: str | None
    realized_pnl: Decimal | None
    occurred_at_ms: int
    settlement_currency: str | None


@dataclass(frozen=True, slots=True)
class ActivityOrder:
    """Completed-order size used only as a cross-check, not as a second P&L."""

    order_id: str
    filled_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RoundTripPerformance:
    """Closed trades with complete economics. Unresolved closes are separate."""

    trade_count: int
    wins: int
    losses: int
    scratches: int
    unresolved: int
    win_rate: Decimal | None
    realized_pnl: Decimal | None
    fees: Decimal | None
    gross_pnl: Decimal | None
    window_start: datetime | None
    window_end: datetime | None
    missing_orders: bool
    order_mismatches: int


@dataclass(frozen=True, slots=True)
class _Slice:
    fill: ActivityFill
    quantity: Decimal


def _delta(fill: ActivityFill) -> Decimal:
    """Signed position change. Short inventory increases on a sell."""
    if fill.position_side == "short":
        return fill.quantity if fill.side == "sell" else -fill.quantity
    return fill.quantity if fill.side == "buy" else -fill.quantity


def _closed_slices(fills: list[ActivityFill]) -> list[list[_Slice]]:
    position = Decimal(0)
    open_slices: list[_Slice] = []
    closed: list[list[_Slice]] = []
    for fill in fills:
        delta = _delta(fill)
        if delta == 0:
            continue
        if position == 0:
            open_slices = [_Slice(fill, abs(delta))]
            position = delta
            continue
        same_direction = (position > 0 and delta > 0) or (position < 0 and delta < 0)
        if same_direction:
            open_slices.append(_Slice(fill, abs(delta)))
            position += delta
            continue
        close_qty = min(abs(position), abs(delta))
        remainder = abs(delta) - close_qty
        open_slices.append(_Slice(fill, close_qty))
        new_position = position + delta
        crossed = (position > 0 > new_position) or (position < 0 < new_position)
        if new_position == 0 or crossed:
            closed.append(open_slices)
            if new_position == 0:
                open_slices = []
                position = Decimal(0)
            else:
                open_slices = [_Slice(fill, remainder)]
                position = new_position
        else:
            position = new_position
    return closed


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_EVEN)


def _economics(
    slices: list[_Slice],
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return fillPnl, signed fees, and gross fillPnl. Fees are not added to the result."""
    gross = Decimal(0)
    fees = Decimal(0)
    currencies: set[str] = set()
    for sliver in slices:
        fill = sliver.fill
        if fill.quantity <= 0 or sliver.quantity <= 0 or sliver.quantity > fill.quantity:
            return None
        if fill.realized_pnl is None or fill.fee is None or not fill.fee_currency:
            return None
        if fill.settlement_currency != fill.fee_currency:
            return None
        fraction = sliver.quantity / fill.quantity
        gross += fill.realized_pnl * fraction
        fees += fill.fee * fraction
        currencies.add(fill.fee_currency)
    if len(currencies) != 1:
        return None
    return _money(gross), _money(fees), _money(gross)


def account_round_trips(
    fills: list[ActivityFill], orders: list[ActivityOrder]
) -> RoundTripPerformance:
    """Count closed round-trips. Still-open size is not a trade."""
    grouped: dict[tuple[str, str], list[ActivityFill]] = {}
    for fill in fills:
        grouped.setdefault((fill.instrument, fill.position_side), []).append(fill)
    filled_by_order: dict[str, Decimal] = {}
    for fill in fills:
        previous = filled_by_order.get(fill.order_id, Decimal(0))
        filled_by_order[fill.order_id] = previous + fill.quantity
    orders_by_id = {order.order_id: order.filled_quantity for order in orders}
    mismatched = {
        order_id
        for order_id, filled in orders_by_id.items()
        if filled_by_order.get(order_id, Decimal(0)) != filled
    }

    trades = 0
    wins = 0
    losses = 0
    scratches = 0
    unresolved = 0
    missing_orders = False
    net_total = Decimal(0)
    fee_total = Decimal(0)
    gross_total = Decimal(0)
    earliest: int | None = None
    latest: int | None = None

    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda item: (item.occurred_at_ms, item.native_id))
        for slices in _closed_slices(ordered):
            order_ids = {sliver.fill.order_id for sliver in slices}
            if order_ids & mismatched:
                unresolved += 1
                continue
            if any(order_id not in orders_by_id for order_id in order_ids):
                missing_orders = True
            money = _economics(slices)
            if money is None:
                unresolved += 1
                continue
            net, fee, gross = money
            trades += 1
            net_total += net
            fee_total += fee
            gross_total += gross
            if net > 0:
                wins += 1
            elif net < 0:
                losses += 1
            else:
                scratches += 1
            for sliver in slices:
                stamp = sliver.fill.occurred_at_ms
                earliest = stamp if earliest is None else min(earliest, stamp)
                latest = stamp if latest is None else max(latest, stamp)

    decisive = wins + losses
    win_rate = None if decisive == 0 else (Decimal(wins) / Decimal(decisive)).quantize(_RATE)
    return RoundTripPerformance(
        trade_count=trades,
        wins=wins,
        losses=losses,
        scratches=scratches,
        unresolved=unresolved,
        win_rate=win_rate,
        realized_pnl=None if trades == 0 else _money(net_total),
        fees=None if trades == 0 else _money(fee_total),
        gross_pnl=None if trades == 0 else _money(gross_total),
        window_start=_stamp(earliest),
        window_end=_stamp(latest),
        missing_orders=missing_orders,
        order_mismatches=len(mismatched),
    )


def _stamp(millis: int | None) -> datetime | None:
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC)
