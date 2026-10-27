"""Read-only mirror of hand-traded BloFin demo positions into the journal.

Official contracts, read 2026-10-18 from https://docs.blofin.com/index.html :

* ``GET /api/v1/account/positions-history``
  (https://docs.blofin.com/index.html#get-positions-history).
  ``realizedPnl`` is "Realized profit and loss". The published ETH row
  (side ``buy``, open 1591.8, close 1592.5, closePositions 1, realizedPnl
  ``0.7``, fee ``0.190536``) equals ``(close - open) * contracts`` and does
  not subtract ``fee``. ``fee`` is "Trading fee". Both published examples are
  positive. That page does not define a negative fee. Order history on the
  same document calls its fee field "Fee and rebate"
  (https://docs.blofin.com/index.html#get-order-history).
* ``GET /api/v1/account/funding-fees``
  (https://docs.blofin.com/index.html#get-funding-fee-history).
  ``fundingFee``: "A positive value means received; a negative value means
  paid." Positions history does not include a funding field. The funding
  route covers at most seven days.
* ``GET /api/v1/trade/fills-history``
  (https://docs.blofin.com/index.html#get-trade-history).
  ``fillPnl`` is "Last filled profit and loss, applicable to orders which
  have a trade and aim to close position." The page does not say whether
  that figure is gross or net of fees or funding. This mirror does not use
  ``fillPnl``. Order ids on those fills are kept so a later rerun stays
  attached to the same orders.
* ``GET /api/v1/trade/orders-history`` supplies ``slTriggerPrice`` and
  ``tpTriggerPrice`` when the order carried them.
* ``GET /api/v1/account/positions`` is the open-position read already used
  by the snapshot. ``GET /api/v1/market/instruments`` supplies
  ``contractValue`` and ``settleCurrency``.

Journal economics follow ``paper_close_pnl``: long price P&L is
``(exit - entry) * size``, short flips the price delta, and net subtracts
fees and funding. ``size`` is contracts times ``contractValue``. A
non-negative positions-history fee is a cost. A negative fee is stored as a
rebate so the same subtraction adds it. Journal funding is ``-fundingFee``
(paid is positive, received is negative).

A closed row is verified only when that price P&L equals ``realizedPnl``
after quantizing to 1e-8, settlement is USDT, the fee is present, and
funding for the slice sits inside the fetched window and is not shared with
another position. The published BCH ``sell`` example does not match the
short formula, so that row stays unverified. The dashboard publishes win
rate only when every closed mirrored row is verified.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Protocol

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import ExecutionMode, Settings
from app.core.errors import ExchangeDemoInactiveError
from app.db.models import JournalTrade
from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    TradeDirection,
    TradeResult,
)
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.tenant import TenantContext
from app.services.risk.mirror_economics import MIRROR_REF_PREFIX, RECONCILED_TAG, exact_prefix

logger = structlog.get_logger("services.blofin_demo_manual_mirror")

METHOD = "positions-history-price-pnl-fee-funding/v1"
_Q = Decimal("0.00000001")
_RATE = Decimal("0.0001")
_WINDOW = timedelta(days=7) - timedelta(seconds=1)
_MAX_PAGES = 40
_CYCLE_REF = "blofin-demo-mirror-cycle"
_CYCLE_OK = "mirror-cycle:ok"
_CYCLE_FAILED = "mirror-cycle:failed"
_CYCLE_PAGE_CAP = "mirror-cycle:page-cap"
_HISTORY = "/api/v1/account/positions-history"
_FILLS = "/api/v1/trade/fills-history"
_FUNDING = "/api/v1/account/funding-fees"
_ORDERS = "/api/v1/trade/orders-history"
_POSITIONS = "/api/v1/account/positions"
_INSTRUMENTS = "/api/v1/market/instruments"
_CURSOR = {
    _HISTORY: "historyId",
    _FILLS: "tradeId",
    _FUNDING: "billId",
    _ORDERS: "orderId",
}
_VERIFIED = "pnl-verified"
_UNVERIFIED = "pnl-unverified"
_MIRROR_SOURCES = (
    JournalTradeSource.BLOFIN_DEMO_MANUAL,
    JournalTradeSource.MANUAL_DEMO_TEST,
    JournalTradeSource.MANUAL,
)

_DISABLED = (
    "Manual mirror is off. Win rate and realized P&L stay withheld because "
    "fillPnl is not documented as gross or net of fees or funding."
)
_READY = (
    "Closed BloFin demo manual journal entries. Realized P&L is the "
    "positions-history price movement times size, minus the trading fee, "
    "minus funding paid. fillPnl is not used."
)


class _HistoryClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
        before_send: Any = None,
    ) -> Any: ...


class MirrorReadError(Exception):
    """A read-only history page was incomplete or malformed."""


@dataclass(frozen=True, slots=True)
class MirrorDraft:
    """One journal upsert. Venue economics are recomputed from the payload."""

    external_ref: str
    position_id: str
    history_ids: tuple[str, ...]
    symbol: str
    direction: TradeDirection
    status: JournalTradeStatus
    entry_price: Decimal | None
    exit_price: Decimal | None
    entry_time: datetime | None
    exit_time: datetime | None
    size: Decimal | None
    leverage: Decimal | None
    fees: Decimal | None
    funding: Decimal | None
    slippage: Decimal | None
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    result: TradeResult
    exit_reason: str | None
    stop: Decimal | None
    target: Decimal | None
    planned_risk: Decimal | None
    verified: bool
    price_matches: bool
    order_ids: tuple[str, ...]
    open_tracker: bool


@dataclass(frozen=True, slots=True)
class SkippedRow:
    """One positions-history row that was not mirrored, and why.

    ``history_id``/``inst_id`` are venue row ids (not secrets); ``reason`` is a
    fixed code such as ``ambiguous_both_sides``, ``no_opening_fill``,
    ``pnl_direction_conflict`` or ``unknown_position_side``.
    """

    history_id: str | None
    inst_id: str | None
    reason: str

    def as_dict(self) -> dict[str, str | None]:
        return {"history_id": self.history_id, "inst_id": self.inst_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Closed slices and the open trackers that are not a second trade."""

    closed: tuple[MirrorDraft, ...]
    opens: tuple[MirrorDraft, ...]
    skipped: int
    history_incomplete: bool = False
    funding_incomplete: bool = False
    positions_complete: bool = True
    instruments_incomplete: bool = False
    skipped_rows: tuple[SkippedRow, ...] = ()


@dataclass(frozen=True, slots=True)
class MirrorReport:
    """Counts from one apply. Zero rows is a successful no-op."""

    skipped_flag: bool
    created: int
    updated: int
    reconciled: int
    closed: int
    opens: int
    ambiguous: int = 0
    skipped_rows: tuple[SkippedRow, ...] = ()


@dataclass(frozen=True, slots=True)
class _Instrument:
    contract_value: Decimal
    settle: str


@dataclass(slots=True)
class _Slice:
    history_id: str
    position_id: str
    inst_id: str
    side: str
    direction: TradeDirection
    contracts: Decimal
    max_contracts: Decimal
    open_price: Decimal
    close_price: Decimal
    opened_at: datetime
    closed_at: datetime
    leverage: Decimal | None
    realized: Decimal | None
    fee: Decimal | None
    contract_value: Decimal | None
    settle: str | None
    side_conflict: bool
    funding: Decimal | None = None
    funding_ok: bool = False
    stop: Decimal | None = None
    target: Decimal | None = None
    order_ids: tuple[str, ...] = ()


def _money(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_EVEN)


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _time(value: object) -> datetime | None:
    number = _decimal(value) if isinstance(value, str) else None
    if number is None or number < 0 or number != number.to_integral_value():
        return None
    return datetime.fromtimestamp(int(number) / 1000, tz=UTC)


def _symbol(inst_id: str) -> str:
    text = inst_id.strip().upper()
    if text.endswith("-SWAP"):
        text = text[: -len("-SWAP")]
    return text.replace("-", "").replace("_", "")


def _direction_from_side(side: str, position_side: str) -> tuple[TradeDirection | None, bool]:
    """Map BloFin ``side`` buy/sell to long/short. Disagreeing positionSide conflicts."""
    if side == "buy":
        direction = TradeDirection.LONG
    elif side == "sell":
        direction = TradeDirection.SHORT
    else:
        return None, False
    if position_side in {"", "net"}:
        return direction, False
    expected = "long" if direction is TradeDirection.LONG else "short"
    return direction, position_side != expected


def _instruments(rows: list[dict[str, Any]]) -> tuple[dict[str, _Instrument], bool]:
    found: dict[str, _Instrument] = {}
    conflict = False
    for row in rows:
        inst_id = row.get("instId")
        value = _decimal(row.get("contractValue"))
        settle = row.get("settleCurrency")
        if not isinstance(inst_id, str) or value is None or value <= 0:
            continue
        if not isinstance(settle, str):
            continue
        current = _Instrument(value, settle.upper())
        previous = found.get(inst_id)
        if previous is not None and (
            previous.contract_value != current.contract_value or previous.settle != current.settle
        ):
            conflict = True
        found[inst_id] = current
    return found, conflict


_OPEN_FILL_LEAD = timedelta(seconds=5)


def _price_move_side(row: dict[str, Any]) -> str | None:
    """Opening side implied by the price move and the sign of ``realizedPnl``.

    A long gains when price rises, a short when it falls. ``None`` when either
    the move or the P&L is zero or missing (no signal).
    """

    open_price = _decimal(row.get("openAveragePrice"))
    close_price = _decimal(row.get("closeAveragePrice"))
    realized = _decimal(row.get("realizedPnl"))
    if open_price is None or close_price is None or realized is None:
        return None
    move = close_price - open_price
    if move == 0 or realized == 0:
        return None
    return "buy" if (move > 0) == (realized > 0) else "sell"


def _opening_fill_side(row: dict[str, Any], fills: list[dict[str, Any]] | None) -> str | None:
    return _opening_fill_side_reason(row, fills)[0]


def _opening_fill_side_reason(
    row: dict[str, Any], fills: list[dict[str, Any]] | None
) -> tuple[str | None, str | None]:
    """The one side of the opening fill(s) at the row's ``createTime``.

    Only fills on the same instrument within +/- ``_OPEN_FILL_LEAD`` of the open
    count, and fills with a non-zero ``fillPnl`` (BloFin sets it on fills that
    close a position) are excluded. Both sides present, or none: ``None``.
    """

    opened_at = _time(row.get("createTime"))
    inst_id = row.get("instId")
    if not fills or opened_at is None or not isinstance(inst_id, str):
        return None, "no_opening_fill"
    sides: set[str] = set()
    for fill in fills:
        when = _time(fill.get("ts"))
        fill_side = fill.get("side")
        if fill.get("instId") != inst_id or when is None or fill_side not in {"buy", "sell"}:
            continue
        if abs(when - opened_at) > _OPEN_FILL_LEAD:
            continue
        fill_pnl = _decimal(fill.get("fillPnl"))
        if fill.get("fillPnl") not in (None, "") and (fill_pnl is None or fill_pnl != 0):
            continue
        sides.add(fill_side)
    if len(sides) > 1:
        return None, "ambiguous_both_sides"
    if not sides:
        return None, "no_opening_fill"
    return sides.pop(), None


def _opening_side(row: dict[str, Any], fills: list[dict[str, Any]] | None) -> str | None:
    return _opening_side_reason(row, fills)[0]


def _opening_side_reason(
    row: dict[str, Any], fills: list[dict[str, Any]] | None
) -> tuple[str | None, str | None]:
    """Side of the slice's opening trade when the history row carries no ``side``.

    Some BloFin ``positions-history`` rows carry ``positionSide`` without
    ``side``. ``long``/``short`` decide it directly. In one-way (``net``) mode
    the side must come from an unambiguous opening fill at ``createTime``
    (see ``_opening_fill_side``); when the price move and ``realizedPnl`` give a
    signal it must agree. Anything else returns ``None`` so the row is skipped:
    a wrong direction is never guessed.
    """

    side = row.get("side")
    if isinstance(side, str) and side:
        return side, None
    position_side = row.get("positionSide")
    if position_side == "long":
        return "buy", None
    if position_side == "short":
        return "sell", None
    if position_side not in (None, "", "net"):
        return None, "unknown_position_side"
    fill_side, reason = _opening_fill_side_reason(row, fills)
    if fill_side is None:
        return None, reason
    price_side = _price_move_side(row)
    if price_side is not None and price_side != fill_side:
        return None, "pnl_direction_conflict"
    return fill_side, None


def _parse_slices(
    history: list[dict[str, Any]],
    instruments: dict[str, _Instrument],
    fills: list[dict[str, Any]] | None = None,
) -> tuple[list[_Slice], int, bool]:
    slices, skipped_rows, incomplete = _parse_slices_detailed(history, instruments, fills)
    return slices, len(skipped_rows), incomplete


def _parse_slices_detailed(
    history: list[dict[str, Any]],
    instruments: dict[str, _Instrument],
    fills: list[dict[str, Any]] | None = None,
) -> tuple[list[_Slice], list[SkippedRow], bool]:
    """Like ``_parse_slices`` but returns each skipped row with its reason code."""
    slices: list[_Slice] = []
    skipped_rows: list[SkippedRow] = []
    incomplete = False
    seen: set[str] = set()
    for row in history:
        history_id = row.get("historyId")
        position_id = row.get("positionId")
        inst_id = row.get("instId")
        side, side_reason = _opening_side_reason(row, fills)
        ref = history_id if isinstance(history_id, str) and history_id else None
        inst_ref = inst_id if isinstance(inst_id, str) and inst_id else None

        def _skip(reason: str, *, ref: str | None = ref, inst_ref: str | None = inst_ref) -> None:
            skipped_rows.append(SkippedRow(ref, inst_ref, reason))

        if not all(isinstance(item, str) and item for item in (history_id, position_id, inst_id)):
            _skip("missing_identity")
            incomplete = True
            continue
        if not (isinstance(side, str) and side):
            _skip(side_reason or "missing_side")
            incomplete = True
            continue
        if history_id in seen:
            _skip("duplicate_history_id")
            continue
        contracts = _decimal(row.get("closePositions"))
        max_contracts = _decimal(row.get("maxPositions"))
        open_price = _decimal(row.get("openAveragePrice"))
        close_price = _decimal(row.get("closeAveragePrice"))
        opened_at = _time(row.get("createTime"))
        closed_at = _time(row.get("updateTime"))
        if (
            contracts is None
            or contracts <= 0
            or max_contracts is None
            or max_contracts <= 0
            or open_price is None
            or open_price <= 0
            or close_price is None
            or close_price <= 0
            or opened_at is None
            or closed_at is None
            or not isinstance(side, str)
            or not isinstance(inst_id, str)
            or not isinstance(history_id, str)
            or not isinstance(position_id, str)
        ):
            _skip("invalid_fields")
            incomplete = True
            continue
        position_side = row.get("positionSide")
        direction, conflict = _direction_from_side(
            side, position_side if isinstance(position_side, str) else ""
        )
        if direction is None:
            _skip("side_conflict")
            incomplete = True
            continue
        meta = instruments.get(inst_id)
        leverage = _decimal(row.get("leverage"))
        if leverage is not None and leverage <= 0:
            leverage = None
        seen.add(str(history_id))
        slices.append(
            _Slice(
                history_id=history_id,
                position_id=position_id,
                inst_id=inst_id,
                side=side,
                direction=direction,
                contracts=contracts,
                max_contracts=max_contracts,
                open_price=open_price,
                close_price=close_price,
                opened_at=opened_at,
                closed_at=closed_at,
                leverage=leverage,
                realized=_decimal(row.get("realizedPnl")),
                fee=_decimal(row.get("fee")),
                contract_value=meta.contract_value if meta else None,
                settle=meta.settle if meta else None,
                side_conflict=conflict,
            )
        )
    return slices, skipped_rows, incomplete


def _price_pnl(sliver: _Slice) -> Decimal | None:
    """Direction-aware price P&L. ``size`` is contracts times contract value."""
    if sliver.contract_value is None:
        return None
    size = sliver.contracts * sliver.contract_value
    delta = sliver.close_price - sliver.open_price
    if sliver.direction is TradeDirection.SHORT:
        delta = -delta
    return _money(delta * size)


def _overlaps(left: _Slice, right: _Slice) -> bool:
    return left.opened_at < right.closed_at and right.opened_at < left.closed_at


def _assign_funding(
    slices: list[_Slice],
    bills: list[dict[str, Any]],
    *,
    window_begin: datetime,
) -> bool:
    """Give each funding bill to one slice. Shared windows stay unverified.

    Returns true when a bill could not be parsed. Those instruments stay
    unverified so a dropped fee is not treated as zero.
    """
    parsed: list[tuple[str, datetime, Decimal]] = []
    unparsed_instruments: set[str] = set()
    unparsed_unknown = False
    for row in bills:
        inst_id = row.get("instId")
        amount = _decimal(row.get("fundingFee"))
        when = _time(row.get("fundingTime"))
        if not isinstance(inst_id, str) or not inst_id or amount is None or when is None:
            if isinstance(inst_id, str) and inst_id:
                unparsed_instruments.add(inst_id)
            else:
                unparsed_unknown = True
            continue
        parsed.append((inst_id, when, amount))
    by_inst: dict[str, list[_Slice]] = {}
    for sliver in slices:
        by_inst.setdefault(sliver.inst_id, []).append(sliver)
    ambiguous: set[int] = set()
    for group in by_inst.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                same_position = left.position_id == right.position_id
                shared_close = same_position and left.closed_at == right.closed_at
                shared_window = not same_position and _overlaps(left, right)
                if shared_close or shared_window:
                    ambiguous.add(id(left))
                    ambiguous.add(id(right))
    windows: list[tuple[_Slice, datetime, bool]] = []
    for sliver in slices:
        if id(sliver) in ambiguous or sliver.opened_at < window_begin:
            sliver.funding = None
            sliver.funding_ok = False
            continue
        # Same position: bills after the previous slice's close belong here.
        previous = [
            other.closed_at
            for other in slices
            if other.position_id == sliver.position_id
            and other.history_id != sliver.history_id
            and other.closed_at <= sliver.closed_at
        ]
        start = max(previous) if previous else sliver.opened_at
        windows.append((sliver, start, not previous))
    claimed: dict[int, list[_Slice]] = {}
    for index, (inst_id, when, _amount) in enumerate(parsed):
        owners = [
            sliver
            for sliver, start, include_open in windows
            if inst_id == sliver.inst_id
            and (
                start < when <= sliver.closed_at
                or (include_open and sliver.opened_at <= when <= sliver.closed_at)
            )
        ]
        if len(owners) > 1:
            claimed[index] = owners
    shared = {id(sliver) for owners in claimed.values() for sliver in owners}
    for sliver, start, include_open in windows:
        if id(sliver) in shared:
            sliver.funding = None
            sliver.funding_ok = False
            continue
        total = Decimal("0")
        for inst_id, when, amount in parsed:
            if inst_id != sliver.inst_id:
                continue
            in_window = start < when <= sliver.closed_at
            at_open = include_open and sliver.opened_at <= when <= sliver.closed_at
            if in_window or at_open:
                # Journal funding is the cost: paid is positive, received is negative.
                total += -amount
        sliver.funding = _money(total)
        sliver.funding_ok = True
    if unparsed_unknown:
        for sliver in slices:
            sliver.funding = None
            sliver.funding_ok = False
    else:
        for sliver in slices:
            if sliver.inst_id in unparsed_instruments:
                sliver.funding = None
                sliver.funding_ok = False
    return bool(unparsed_instruments) or unparsed_unknown


def _order_levels(
    sliver: _Slice,
    orders: list[dict[str, Any]],
    order_ids: list[str] | None,
) -> tuple[set[Decimal], set[Decimal]]:
    stops: set[Decimal] = set()
    targets: set[Decimal] = set()
    earliest = sliver.opened_at - timedelta(minutes=1)
    for row in orders:
        if row.get("instId") != sliver.inst_id or row.get("side") != sliver.side:
            continue
        if str(row.get("reduceOnly", "false")).lower() == "true":
            continue
        when = _time(row.get("createTime"))
        if when is None or when < earliest or when > sliver.closed_at:
            continue
        if order_ids is not None and row.get("orderId") not in order_ids:
            continue
        stop = _decimal(row.get("slTriggerPrice"))
        target = _decimal(row.get("tpTriggerPrice"))
        if stop is not None and stop > 0:
            stops.add(stop)
        if target is not None and target > 0:
            targets.add(target)
    return stops, targets


def _attach_orders(
    slices: list[_Slice], fills: list[dict[str, Any]], orders: list[dict[str, Any]]
) -> None:
    for sliver in slices:
        order_ids: list[str] = []
        for row in fills:
            if row.get("instId") != sliver.inst_id:
                continue
            when = _time(row.get("ts"))
            order_id = row.get("orderId")
            if when is None or when < sliver.opened_at or when > sliver.closed_at:
                continue
            if isinstance(order_id, str) and order_id and order_id not in order_ids:
                order_ids.append(order_id)
        sliver.order_ids = tuple(order_ids[:8])
        stops, targets = _order_levels(sliver, orders, order_ids or None)
        if not stops and not targets:
            stops, targets = _order_levels(sliver, orders, None)
        sliver.stop = next(iter(stops)) if len(stops) == 1 else None
        sliver.target = next(iter(targets)) if len(targets) == 1 else None


def _protective(sliver: _Slice) -> bool:
    if sliver.stop is None:
        return False
    if sliver.direction is TradeDirection.LONG:
        return sliver.stop < sliver.open_price
    return sliver.stop > sliver.open_price


def _draft_from_slice(sliver: _Slice) -> MirrorDraft:
    price = _price_pnl(sliver)
    price_matches = (
        price is not None
        and sliver.realized is not None
        and price == _money(sliver.realized)
        and not sliver.side_conflict
        and sliver.settle == "USDT"
    )
    fee = sliver.fee
    verified = (
        price_matches and fee is not None and sliver.funding_ok and sliver.funding is not None
    )
    size = (
        _money(sliver.contracts * sliver.contract_value)
        if sliver.contract_value is not None
        else None
    )
    gross = price if price_matches else None
    funding = sliver.funding if sliver.funding_ok else None
    net = None
    if verified and gross is not None and fee is not None and funding is not None:
        net = _money(gross - fee - funding)
    if net is None:
        result = TradeResult.OPEN
    elif net > 0:
        result = TradeResult.WIN
    elif net < 0:
        result = TradeResult.LOSS
    else:
        result = TradeResult.BREAKEVEN
    risk = None
    if verified and size is not None and _protective(sliver) and sliver.stop is not None:
        risk = _money(abs(sliver.open_price - sliver.stop) * size)
        if risk <= 0:
            risk = None
    reason = "closed" if sliver.contracts == sliver.max_contracts else "partially_closed"
    if not verified:
        reason = "unverified"
    return MirrorDraft(
        external_ref=f"blofin-demo-history:{sliver.history_id}",
        position_id=sliver.position_id,
        history_ids=(sliver.history_id,),
        symbol=_symbol(sliver.inst_id),
        direction=sliver.direction,
        status=JournalTradeStatus.CLOSED,
        entry_price=sliver.open_price,
        exit_price=sliver.close_price,
        entry_time=sliver.opened_at,
        exit_time=sliver.closed_at,
        size=size,
        leverage=sliver.leverage,
        fees=fee if verified else None,
        funding=funding if verified else None,
        slippage=Decimal("0") if verified else None,
        gross_pnl=gross if verified else None,
        net_pnl=net,
        result=result,
        exit_reason=reason,
        stop=sliver.stop if _protective(sliver) else None,
        target=sliver.target,
        planned_risk=risk,
        verified=verified,
        price_matches=price_matches,
        order_ids=sliver.order_ids,
        open_tracker=False,
    )


def _open_direction(row: dict[str, Any], quantity: Decimal) -> TradeDirection | None:
    side = row.get("positionSide")
    if side == "long":
        return TradeDirection.LONG
    if side == "short":
        return TradeDirection.SHORT
    if side == "net":
        if quantity > 0:
            return TradeDirection.LONG
        if quantity < 0:
            return TradeDirection.SHORT
    return None


def _open_drafts(
    positions: list[dict[str, Any]],
    instruments: dict[str, _Instrument],
) -> tuple[list[MirrorDraft], int, bool]:
    drafts: list[MirrorDraft] = []
    skipped = 0
    complete = True
    for row in positions:
        position_id = row.get("positionId")
        inst_id = row.get("instId")
        quantity = _decimal(row.get("positions"))
        if (
            not isinstance(position_id, str)
            or not position_id
            or not isinstance(inst_id, str)
            or not inst_id
            or quantity is None
        ):
            skipped += 1
            complete = False
            continue
        direction = _open_direction(row, quantity)
        if quantity == 0:
            skipped += 1
            continue
        if direction is None:
            skipped += 1
            complete = False
            continue
        meta = instruments.get(inst_id)
        opened_at = _time(row.get("createTime"))
        stamp = row.get("createTime") if isinstance(row.get("createTime"), str) else "na"
        entry = _decimal(row.get("averagePrice"))
        leverage = _decimal(row.get("leverage"))
        size = _money(abs(quantity) * meta.contract_value) if meta is not None else None
        drafts.append(
            MirrorDraft(
                external_ref=f"blofin-demo-open:{position_id}:{stamp}",
                position_id=position_id,
                history_ids=(),
                symbol=_symbol(inst_id),
                direction=direction,
                status=JournalTradeStatus.OPEN,
                entry_price=entry if entry is not None and entry > 0 else None,
                exit_price=None,
                entry_time=opened_at,
                exit_time=None,
                size=size,
                leverage=leverage if leverage is not None and leverage > 0 else None,
                fees=None,
                funding=None,
                slippage=None,
                gross_pnl=None,
                net_pnl=None,
                result=TradeResult.OPEN,
                exit_reason=None,
                stop=None,
                target=None,
                planned_risk=None,
                verified=False,
                price_matches=False,
                order_ids=(),
                open_tracker=True,
            )
        )
    return drafts, skipped, complete


def drafts_from_raw(
    *,
    instruments: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    history: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    funding: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    window_begin: datetime,
    window_end: datetime,
) -> BuildResult:
    """Build journal drafts. ``fillPnl`` never feeds P&L; it only marks closing fills."""
    del window_end
    meta, instruments_incomplete = _instruments(instruments)
    slices, skipped_rows, history_incomplete = _parse_slices_detailed(history, meta, fills)
    funding_incomplete = _assign_funding(slices, funding, window_begin=window_begin)
    _attach_orders(slices, fills, orders)
    opens, open_skipped, positions_complete = _open_drafts(positions, meta)
    return BuildResult(
        closed=tuple(_draft_from_slice(sliver) for sliver in slices),
        opens=tuple(opens),
        skipped=len(skipped_rows) + open_skipped,
        history_incomplete=history_incomplete,
        funding_incomplete=funding_incomplete,
        positions_complete=positions_complete,
        instruments_incomplete=instruments_incomplete,
        skipped_rows=tuple(skipped_rows),
    )


def _history_pages(
    client: _HistoryClient, path: str, begin: str, end: str
) -> tuple[list[dict[str, Any]], bool]:
    """Return rows and whether the safety cap stopped the cursor."""
    rows: list[dict[str, Any]] = []
    after: str | None = None
    seen: set[str] = set()
    cursor_key = _CURSOR[path]
    for _page in range(_MAX_PAGES):
        params = {"begin": begin, "end": end, "limit": "100"}
        if after is not None:
            params["after"] = after
        page = client.request("GET", path, params=params, signed=True)
        if not isinstance(page, list):
            raise MirrorReadError("history page was not a list")
        if not page:
            return rows, False
        last = page[-1]
        if not isinstance(last, dict):
            raise MirrorReadError("history row was not an object")
        cursor = last.get(cursor_key)
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise MirrorReadError("history cursor did not advance")
        seen.add(cursor)
        for item in page:
            if isinstance(item, dict):
                rows.append(item)
            else:
                raise MirrorReadError("history row was not an object")
        if len(page) < 100:
            return rows, False
        after = cursor
    return rows, True


def _window(now: datetime) -> tuple[datetime, datetime, str, str]:
    end = now.astimezone(UTC)
    begin = end - _WINDOW
    return begin, end, str(int(begin.timestamp() * 1000)), str(int(end.timestamp() * 1000))


def _tags(draft: MirrorDraft, existing: list[str], *, reconciled: bool) -> list[str]:
    kept = [
        tag
        for tag in existing
        if isinstance(tag, str)
        and not tag.startswith("blofin-")
        and tag not in {_VERIFIED, _UNVERIFIED}
    ]
    mirror = [f"blofin-history:{item}" for item in draft.history_ids]
    mirror.extend(f"blofin-order:{item}" for item in draft.order_ids)
    mirror.append(_VERIFIED if draft.verified else _UNVERIFIED)
    if draft.open_tracker:
        mirror = [tag for tag in mirror if tag != _UNVERIFIED]
    if reconciled:
        mirror.append(RECONCILED_TAG)
    return kept + mirror


def _same_money(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return False
    return _money(left) == _money(right)


def _claim_manual(row: JournalTrade, draft: MirrorDraft, *, user_id: uuid.UUID) -> bool:
    """Match this user's open row to a venue close without guessing."""
    if row.user_id != user_id:
        return False
    if row.status is not JournalTradeStatus.OPEN:
        return False
    if row.source not in {JournalTradeSource.MANUAL_DEMO_TEST, JournalTradeSource.MANUAL}:
        return False
    if row.external_ref:
        return False
    if row.exchange not in {None, "BLOFIN_DEMO"}:
        return False
    if _symbol(row.symbol) != draft.symbol or row.direction is not draft.direction:
        return False
    if not _same_money(row.entry_price, draft.entry_price):
        return False
    return _same_money(row.size, draft.size)


def _apply_venue(row: JournalTrade, draft: MirrorDraft, *, fresh: bool) -> None:
    if fresh:
        row.symbol = draft.symbol
        row.exchange = "BLOFIN_DEMO"
        row.timeframe = "manual"
        row.direction = draft.direction
        row.market_regime = MarketRegime.UNKNOWN
        row.entry_method = JournalEntryMethod.AUTO
        row.source = JournalTradeSource.BLOFIN_DEMO_MANUAL
        row.tags = []
        row.planned_targets = []
    # A lifecycle-backed book already counts this entry. Keep the time, size,
    # and entry that open risk and the daily trade count already used.
    lifecycle_backed = row.execution_lifecycle_id is not None
    kept_entry_price = row.entry_price
    kept_entry_time = row.entry_time
    kept_size = row.size
    row.external_ref = draft.external_ref
    row.status = draft.status
    row.entry_price = kept_entry_price if lifecycle_backed else draft.entry_price
    row.entry_time = kept_entry_time if lifecycle_backed else draft.entry_time
    row.exit_price = draft.exit_price
    row.exit_time = draft.exit_time
    row.exit_reason = draft.exit_reason
    row.size = kept_size if lifecycle_backed else draft.size
    row.leverage = draft.leverage
    row.fees = draft.fees
    row.funding = draft.funding
    row.slippage = draft.slippage
    row.gross_pnl = draft.gross_pnl
    row.net_pnl = draft.net_pnl
    row.result = draft.result
    if row.planned_stop_price is None and draft.stop is not None:
        row.planned_stop_price = draft.stop
        if draft.planned_risk is not None:
            row.planned_risk_amount = draft.planned_risk
    if not row.planned_targets and draft.target is not None:
        row.planned_targets = [
            {"price": format(draft.target, "f"), "size_fraction": 1.0, "label": "tp"}
        ]
    reconciled = row.source in {JournalTradeSource.MANUAL, JournalTradeSource.MANUAL_DEMO_TEST}
    row.tags = _tags(draft, list(row.tags or []), reconciled=reconciled)


def _history_ids(row: JournalTrade) -> set[str]:
    found: set[str] = set()
    for tag in row.tags or []:
        if isinstance(tag, str) and tag.startswith("blofin-history:"):
            found.add(tag.split(":", 1)[1])
    return found


def _should_recompute(row: JournalTrade, draft: MirrorDraft) -> bool:
    stored = _history_ids(row)
    fetched = set(draft.history_ids)
    if not stored:
        return True
    return stored <= fetched


class BloFinDemoManualMirror:
    """Upsert journal rows from one read-only account snapshot of history."""

    def __init__(self, client: _HistoryClient, *, now: datetime) -> None:
        self._client = client
        self._now = now

    def apply(
        self,
        session: Session,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        settings: Settings,
    ) -> MirrorReport:
        """Write journal rows. Flag off performs no venue read and no write."""
        if settings.enable_real_trading or settings.execution_mode is not ExecutionMode.PAPER:
            raise ExchangeDemoInactiveError("BloFin manual mirror stays paper-only.")
        if not settings.blofin_demo_manual_mirror_enabled:
            return MirrorReport(True, 0, 0, 0, 0, 0)
        self._require_internal_read_pin(organization_id=organization_id, settings=settings)
        begin, end, begin_ms, end_ms = _window(self._now)
        instruments = self._client.request("GET", _INSTRUMENTS, params=None, signed=False)
        positions = self._client.request("GET", _POSITIONS, params=None, signed=True)
        if not isinstance(instruments, list) or not isinstance(positions, list):
            raise MirrorReadError("instrument or position payload was not a list")
        instrument_rows = [row for row in instruments if isinstance(row, dict)]
        position_rows = [row for row in positions if isinstance(row, dict)]
        history, history_capped = _history_pages(self._client, _HISTORY, begin_ms, end_ms)
        if history_capped:
            raise MirrorReadError("positions-history page cap reached")
        fills, fills_capped = _history_pages(self._client, _FILLS, begin_ms, end_ms)
        funding, funding_capped = _history_pages(self._client, _FUNDING, begin_ms, end_ms)
        orders, orders_capped = _history_pages(self._client, _ORDERS, begin_ms, end_ms)
        built = drafts_from_raw(
            instruments=instrument_rows,
            positions=position_rows,
            history=history,
            fills=fills,
            funding=funding,
            orders=orders,
            window_begin=begin,
            window_end=end,
        )
        if len(position_rows) != len(positions):
            built = replace(built, positions_complete=False)
        page_capped = fills_capped or funding_capped or orders_capped
        incomplete = (
            page_capped
            or built.history_incomplete
            or built.funding_incomplete
            or not built.positions_complete
            or built.instruments_incomplete
            or len(instrument_rows) != len(instruments)
        )
        if incomplete:
            built = _without_verified_economics(built)
        report = self._persist(session, built, organization_id=organization_id, user_id=user_id)
        if report.ambiguous:
            incomplete = True
        if page_capped:
            outcome = "page-cap"
        elif incomplete:
            outcome = "incomplete"
        else:
            outcome = "ok"
        record_mirror_cycle(
            session,
            organization_id=organization_id,
            user_id=user_id,
            outcome=outcome,
        )
        return report

    def _require_internal_read_pin(self, *, organization_id: uuid.UUID, settings: Settings) -> None:
        """Run the #304 org pin and UID check before any account read.

        Runs for every selected key (read-only, paper_internal main key and
        paper_exchange_demo trading key). The check lives here,
        rather than as a startup refusal of the mirror flag, so it still runs
        when the snapshot cycle skips ``BloFinSyncService.sync`` because the
        stored snapshot is fresh. A missing pin raises before a socket. The
        UID check's only request is ``GET /api/v1/user/query-apikey``.
        """

        from app.providers.exchange.blofin_account import PERMISSIONS_ENDPOINT
        from app.services.blofin_sync_service import require_internal_snapshot_mirror_identity

        def fetch_identity() -> Any:
            return self._client.request("GET", PERMISSIONS_ENDPOINT, params=None, signed=True)

        require_internal_snapshot_mirror_identity(
            settings,
            organization_id,
            fetch_identity=fetch_identity,
        )

    def _persist(
        self,
        session: Session,
        built: BuildResult,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> MirrorReport:
        rows = list(
            session.scalars(
                select(JournalTrade).where(
                    JournalTrade.organization_id == organization_id,
                    JournalTrade.user_id == user_id,
                    or_(
                        exact_prefix(JournalTrade.external_ref, MIRROR_REF_PREFIX),
                        JournalTrade.source.in_(_MIRROR_SOURCES),
                    ),
                )
            ).all()
        )
        by_ref = {row.external_ref: row for row in rows if row.external_ref}
        created = updated = reconciled = ambiguous = 0
        for draft in built.closed:
            claimed = [row for row in rows if _claim_manual(row, draft, user_id=user_id)]
            target = by_ref.get(draft.external_ref)
            if target is not None and target.user_id != user_id:
                target = None
            if target is None and len(claimed) == 1:
                target = claimed[0]
                reconciled += 1
            elif target is None and len(claimed) > 1:
                logger.warning(
                    "blofin_demo_manual_mirror_ambiguous",
                    position_id=draft.position_id,
                )
                ambiguous += 1
                continue
            if target is None:
                target = JournalTrade(
                    organization_id=organization_id,
                    user_id=user_id,
                    source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
                    status=JournalTradeStatus.CLOSED,
                    symbol=draft.symbol,
                    timeframe="manual",
                    direction=draft.direction,
                )
                _apply_venue(target, draft, fresh=True)
                session.add(target)
                rows.append(target)
                by_ref[draft.external_ref] = target
                created += 1
            else:
                same_ref = target.external_ref == draft.external_ref
                if same_ref and not _should_recompute(target, draft):
                    continue
                _apply_venue(target, draft, fresh=False)
                by_ref[draft.external_ref] = target
                updated += 1
        open_refs = {draft.external_ref for draft in built.opens}
        opens_written = 0
        for draft in built.opens:
            target = by_ref.get(draft.external_ref)
            if target is None:
                target = JournalTrade(
                    organization_id=organization_id,
                    user_id=user_id,
                    source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
                    status=JournalTradeStatus.OPEN,
                    symbol=draft.symbol,
                    timeframe="manual",
                    direction=draft.direction,
                )
                _apply_venue(target, draft, fresh=True)
                session.add(target)
                by_ref[draft.external_ref] = target
                created += 1
            else:
                _apply_venue(target, draft, fresh=False)
                updated += 1
            opens_written += 1
        for row in list(by_ref.values()):
            if row.user_id != user_id:
                continue
            if not built.positions_complete:
                break
            ref = row.external_ref or ""
            if not ref.startswith("blofin-demo-open:") or ref in open_refs:
                continue
            if row.status is JournalTradeStatus.CANCELLED:
                continue
            row.status = JournalTradeStatus.CANCELLED
            row.exit_reason = "flat_on_venue"
            row.result = TradeResult.OPEN
            row.net_pnl = None
            updated += 1
        session.flush()
        logger.info(
            "blofin_demo_manual_mirror_applied",
            created=created,
            updated=updated,
            reconciled=reconciled,
            skipped=built.skipped,
            skipped_rows=[row.as_dict() for row in built.skipped_rows],
        )
        return MirrorReport(
            skipped_flag=False,
            created=created,
            updated=updated,
            reconciled=reconciled,
            closed=len(built.closed),
            opens=opens_written,
            ambiguous=ambiguous,
            skipped_rows=built.skipped_rows,
        )


def _without_verified_economics(built: BuildResult) -> BuildResult:
    """A capped fills, funding, or orders read cannot prove net."""
    closed = tuple(
        replace(
            draft,
            fees=None,
            funding=None,
            slippage=None,
            gross_pnl=None,
            net_pnl=None,
            result=TradeResult.OPEN,
            exit_reason="unverified",
            verified=False,
            planned_risk=None,
        )
        for draft in built.closed
    )
    return BuildResult(
        closed=closed,
        opens=built.opens,
        skipped=built.skipped,
        history_incomplete=built.history_incomplete,
        funding_incomplete=built.funding_incomplete,
        positions_complete=built.positions_complete,
        instruments_incomplete=built.instruments_incomplete,
        skipped_rows=built.skipped_rows,
    )


def cycle_marker_ok(row: JournalTrade | None) -> bool:
    """True only when the stored marker's sole cycle tag is a successful read."""
    if row is None:
        return False
    tags = [
        tag for tag in (row.tags or []) if isinstance(tag, str) and tag.startswith("mirror-cycle:")
    ]
    return tags == [_CYCLE_OK]


def _cycle_tags(tags: object) -> set[str]:
    if not isinstance(tags, list):
        return set()
    return {tag for tag in tags if isinstance(tag, str) and tag.startswith("mirror-cycle:")}


def cycle_marker_locked(row: JournalTrade) -> bool:
    return (row.external_ref or "") == _CYCLE_REF


def cycle_tags_rewritten(before: object, after: object) -> bool:
    return _cycle_tags(before) != _cycle_tags(after)


def record_mirror_cycle(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    outcome: str,
) -> None:
    """Remember the latest mirror read. A failed read must not look ready."""
    tag = {
        "ok": _CYCLE_OK,
        "failed": _CYCLE_FAILED,
        "page-cap": _CYCLE_PAGE_CAP,
        "incomplete": "mirror-cycle:incomplete",
    }.get(outcome)
    if tag is None:
        raise ValueError("unknown mirror cycle outcome")
    row = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.user_id == user_id,
            JournalTrade.external_ref == _CYCLE_REF,
        )
    )
    if row is None:
        occupied = session.scalar(
            select(JournalTrade.id).where(
                JournalTrade.organization_id == organization_id,
                JournalTrade.external_ref == _CYCLE_REF,
                JournalTrade.user_id != user_id,
            )
        )
        if occupied is not None:
            logger.warning("blofin_demo_manual_mirror_cycle_owner_mismatch")
            return
        row = JournalTrade(
            organization_id=organization_id,
            user_id=user_id,
            source=JournalTradeSource.BLOFIN_DEMO_MANUAL,
            status=JournalTradeStatus.CANCELLED,
            symbol="MIRROR",
            timeframe="manual",
            direction=TradeDirection.LONG,
            external_ref=_CYCLE_REF,
            result=TradeResult.OPEN,
        )
        session.add(row)
    row.status = JournalTradeStatus.CANCELLED
    row.result = TradeResult.OPEN
    row.net_pnl = None
    row.gross_pnl = None
    row.fees = None
    row.funding = None
    row.tags = [tag]
    reasons = {
        "ok": "mirror_ok",
        "page-cap": "mirror_page_cap",
        "incomplete": "mirror_incomplete",
    }
    row.exit_reason = reasons.get(outcome, "mirror_read_failed")
    session.flush()


def annotation_allowed(row: JournalTrade) -> bool:
    """Hand-mirrored rows, and AlphaTrade rows this mirror has already closed."""
    if cycle_marker_locked(row):
        return False
    if row.source is JournalTradeSource.BLOFIN_DEMO_MANUAL:
        return True
    ref = row.external_ref or ""
    reconciled = row.source in {JournalTradeSource.MANUAL_DEMO_TEST, JournalTradeSource.MANUAL}
    return reconciled and ref.startswith("blofin-demo-")


def strategy_group_key(
    *,
    source: JournalTradeSource,
    strategy_id: uuid.UUID | None,
    strategy_label: str | None,
) -> str | None:
    """Group a hand-mirrored trade by its tagged label when no strategy id is set."""
    if strategy_id is not None:
        return str(strategy_id)
    if source is not JournalTradeSource.BLOFIN_DEMO_MANUAL:
        return None
    label = (strategy_label or "").strip()
    return label or None


def _formula_net(row: JournalTrade) -> Decimal | None:
    """Price P&L minus stored fee and funding. None when a price input is missing."""
    if row.entry_price is None or row.exit_price is None or row.size is None:
        return None
    if row.fees is None or row.funding is None or row.gross_pnl is None or row.net_pnl is None:
        return None
    delta = row.exit_price - row.entry_price
    if row.direction is TradeDirection.SHORT:
        delta = -delta
    gross = _money(delta * row.size)
    if gross != _money(row.gross_pnl):
        return None
    return _money(gross - row.fees - row.funding)


def without_annotation_grade(tags: Iterable[object] | None) -> list[str]:
    """Drop hand-assigned ``grade:`` tags before verification or a headline.

    The grade is a journal annotation. It is not an input to win rate, realized
    P&L, average R, journal statistics, discipline scoring, or calibration.
    """
    return [tag for tag in tags or [] if isinstance(tag, str) and not tag.startswith("grade:")]


def _verified_row(row: JournalTrade) -> bool:
    """A headline row is one this mirror verified, recomputed from stored prices."""
    tags = without_annotation_grade(row.tags)
    ref = row.external_ref or ""
    net = _formula_net(row)
    if net is None or _money(row.net_pnl or Decimal("0")) != net:
        return False
    if net > 0:
        expected = TradeResult.WIN
    elif net < 0:
        expected = TradeResult.LOSS
    else:
        expected = TradeResult.BREAKEVEN
    return (
        row.status is JournalTradeStatus.CLOSED
        and row.source is JournalTradeSource.BLOFIN_DEMO_MANUAL
        and ref.startswith("blofin-demo-history:")
        and ref != _CYCLE_REF
        and _VERIFIED in tags
        and _UNVERIFIED not in tags
        and row.result is expected
    )


def publish_mirror_performance(
    account: DashboardDemoAccount,
    *,
    session: Session,
    tenant: TenantContext,
    settings: Settings,
) -> None:
    """Fill headline numbers only when every closed mirrored row is verified."""
    performance = account.performance
    if not settings.blofin_demo_manual_mirror_enabled:
        performance.manual_mirror_status = "disabled"
        performance.manual_mirror_reason = _DISABLED
        performance.average_r = None
        return
    cycle = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == tenant.organization_id,
            JournalTrade.user_id == tenant.user_id,
            JournalTrade.external_ref == _CYCLE_REF,
        )
    )
    rows = list(
        session.scalars(
            select(JournalTrade).where(
                JournalTrade.organization_id == tenant.organization_id,
                JournalTrade.user_id == tenant.user_id,
                JournalTrade.source == JournalTradeSource.BLOFIN_DEMO_MANUAL,
                JournalTrade.status == JournalTradeStatus.CLOSED,
            )
        ).all()
    )
    rows = [row for row in rows if row.external_ref != _CYCLE_REF]
    if not cycle_marker_ok(cycle):
        performance.manual_mirror_status = "withheld"
        performance.manual_mirror_reason = (
            "Win rate and realized P&L stay withheld. The latest hand-trade "
            "mirror read is missing, failed, or stopped at the page cap, so "
            "the journal is not a complete account."
        )
        performance.average_r = None
        performance.win_rate = None
        performance.trade_count = None
        performance.wins = None
        performance.losses = None
        performance.realized_pnl = None
        return
    unverified = [row for row in rows if not _verified_row(row)]
    if unverified:
        performance.manual_mirror_status = "withheld"
        performance.manual_mirror_reason = (
            "Win rate and realized P&L stay withheld. "
            f"{len(unverified)} closed mirrored trades do not match the "
            "positions-history price formula, or fee or funding coverage is "
            "incomplete. fillPnl remains undocumented as gross or net."
        )
        performance.average_r = None
        performance.win_rate = None
        performance.trade_count = None
        performance.wins = None
        performance.losses = None
        performance.realized_pnl = None
        return
    wins = sum(1 for row in rows if row.result is TradeResult.WIN)
    losses = sum(1 for row in rows if row.result is TradeResult.LOSS)
    decided = wins + losses
    realized = _money(sum((row.net_pnl or Decimal("0") for row in rows), Decimal("0")))
    r_values = [
        row.net_pnl / row.planned_risk_amount
        for row in rows
        if row.net_pnl is not None
        and row.planned_risk_amount is not None
        and row.planned_risk_amount > 0
    ]
    average = (
        (sum(r_values, Decimal("0")) / Decimal(len(r_values))).quantize(
            _RATE, rounding=ROUND_HALF_EVEN
        )
        if r_values
        else None
    )
    performance.manual_mirror_status = "ready"
    performance.manual_mirror_reason = (
        _READY
        if rows
        else ("Manual mirror is on. No closed mirrored trades yet. fillPnl is not used.")
    )
    performance.pnl_method = METHOD
    performance.trade_count = len(rows)
    performance.wins = wins
    performance.losses = losses
    performance.win_rate = (
        (Decimal(wins) / Decimal(decided)).quantize(_RATE, rounding=ROUND_HALF_EVEN)
        if decided
        else None
    )
    performance.realized_pnl = realized
    performance.average_r = average
    times = [row.exit_time for row in rows if row.exit_time is not None]
    performance.window_start = min(times) if times else None
    performance.window_end = max(times) if times else None


def grade_tag(tags: Iterable[str], grade: str | None) -> list[str]:
    """Replace the single ``grade:`` tag. ``None`` clears it."""
    kept = [tag for tag in tags if not (isinstance(tag, str) and tag.startswith("grade:"))]
    if grade:
        kept.append(f"grade:{grade}")
    return kept


def grade_from_tags(tags: Iterable[object] | None) -> str | None:
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith("grade:") and tag.split(":", 1)[1]:
            return tag.split(":", 1)[1]
    return None
