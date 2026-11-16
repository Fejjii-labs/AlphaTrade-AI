"""Partial-exit management for one governed BloFin demo position.

Venue-agnostic: the venue port only exposes reduce-only orders on the closing
side, so no call here can open or add to a position. One ``step``:

1. Gates first, before any venue call: the demo flag (which also requires the
   demo exchange mode and real trading off), then the kill switch and worker
   pause. A gated step reads and writes nothing.
2. Venue closing fills are folded into the durable state. A duplicate fill
   counts once and arrival order does not matter. A fill that would overfill
   a leg or the position, or that no order of ours explains, holds the
   position with an alert and no venue mutation.
3. The protective stop is kept at the planned level (initial, breakeven after
   a complete TP1, then the closed-candle runner trail) and sized to the
   remaining position. A change places the new reduce-only stop first and
   cancels the old one only after the new one exists. A failed placement keeps
   the old stop; a failed cancel leaves two reduce-only stops and is retried.
   A level the mark has already crossed is held and alerted; it is never
   replaced by a market close.
4. Missing TP legs are placed as reduce-only limits with their exact sizes.

Client order ids are derived from the command and the order's content, so a
restart adopts an order it already placed instead of placing it twice.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from app.core.config import Settings
from app.schemas.common import TradeDirection
from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE, partial_exits_enabled
from app.services.partial_exits.paper_terms import PartialExitTerms
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    apply_fill,
    apply_stop_move,
    plan_breakeven_move,
    rebuild_position_state,
)
from app.services.partial_exits.runner import ClosedBar, trail_runner_stop


@dataclass(frozen=True, slots=True)
class VenueOrder:
    order_id: str
    client_order_id: str
    kind: Literal["limit", "stop"]
    side: str
    size: Decimal
    price: Decimal
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class VenueExitFill:
    order_id: str
    trade_id: str
    client_order_id: str
    quantity: Decimal
    price: Decimal
    occurred_at: datetime


class DemoReduceOnlyVenue(Protocol):
    def open_orders(self) -> tuple[VenueOrder, ...]: ...

    def exit_fills(self) -> tuple[VenueExitFill, ...]: ...

    def mark_price(self) -> Decimal: ...

    def place_reduce_only_limit(
        self, *, client_order_id: str, side: str, size: Decimal, price: Decimal
    ) -> str: ...

    def place_reduce_only_stop(
        self, *, client_order_id: str, side: str, size: Decimal, trigger_price: Decimal
    ) -> str: ...

    def cancel_stop(self, *, order_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DemoExitRecord:
    fills: tuple[ExitFill, ...] = ()
    stop_moves: tuple[StopMove, ...] = ()
    active_stop_order_id: str | None = None
    stop_order_ids: tuple[str, ...] = ()
    pending_cancel: tuple[str, ...] = ()


class DemoExitStore(Protocol):
    def load(self, command_id: UUID) -> DemoExitRecord: ...

    def save(self, command_id: UUID, record: DemoExitRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class DemoLadderPosition:
    organization_id: UUID
    command_id: UUID
    terms: PartialExitTerms
    entry_stop_order_id: str | None


@dataclass(frozen=True, slots=True)
class DemoStepReport:
    status: Literal["disabled", "held", "managed", "closed"]
    reason: str = ""
    alerts: tuple[str, ...] = ()


class GovernedDemoPartialExitManager:
    def __init__(
        self,
        *,
        settings: Settings,
        venue: DemoReduceOnlyVenue,
        store: DemoExitStore,
        hold: Callable[[UUID], str | None],
    ) -> None:
        self._settings = settings
        self._venue = venue
        self._store = store
        self._hold = hold

    @staticmethod
    def client_order_id(command_id: UUID, suffix: str) -> str:
        return f"pe{command_id.hex[:16]}{suffix}"

    def _stop_client_id(self, command_id: UUID, trigger: Decimal, size: Decimal) -> str:
        digest = hashlib.sha256(f"{trigger.normalize()}|{size.normalize()}".encode()).hexdigest()
        return self.client_order_id(command_id, f"s{digest[:8]}")

    def step(
        self,
        position: DemoLadderPosition,
        *,
        bars: Sequence[ClosedBar] = (),
        evaluated_at: datetime | None = None,
    ) -> DemoStepReport:
        if not partial_exits_enabled(self._settings, BLOFIN_DEMO_VENUE):
            return DemoStepReport("disabled")
        reason = self._hold(position.organization_id)
        if reason is not None:
            return DemoStepReport("held", reason)
        terms = position.terms
        record = self._store.load(position.command_id)
        state = rebuild_position_state(
            terms.open_state(), fills=record.fills, stop_moves=record.stop_moves
        )
        close_side = "sell" if terms.direction is TradeDirection.LONG else "buy"
        orders = self._venue.open_orders()
        ours = [order for order in orders if self._is_ours(position, record, order)]
        if any(not order.reduce_only or order.side != close_side for order in ours):
            return DemoStepReport("held", "order_shape_unexpected")

        stop_ids = set(record.stop_order_ids)
        if position.entry_stop_order_id:
            stop_ids.add(position.entry_stop_order_id)
        stop_ids.update(order.order_id for order in ours if order.kind == "stop")
        leg_by_client = {
            self.client_order_id(position.command_id, f"tp{leg.order}"): leg.order
            for leg in terms.ladder.target_legs
        }
        for venue_fill in sorted(
            self._venue.exit_fills(), key=lambda item: (item.occurred_at, item.trade_id)
        ):
            leg_order = leg_by_client.get(venue_fill.client_order_id)
            if leg_order is None and venue_fill.order_id not in stop_ids:
                self._store.save(position.command_id, replace(record, fills=state.fills))
                return DemoStepReport("held", "unexplained_fill")
            fill = ExitFill(
                fill_id=f"demo:{venue_fill.order_id}:{venue_fill.trade_id}",
                kind="target"
                if leg_order is not None
                else "stop"
                if state.pending_target_legs()
                else "runner_stop",
                leg_order=leg_order,
                quantity=venue_fill.quantity,
                price=venue_fill.price,
                occurred_at=venue_fill.occurred_at,
            )
            outcome = apply_fill(state, fill)
            if outcome.status == "refused":
                self._store.save(position.command_id, replace(record, fills=state.fills))
                return DemoStepReport("held", f"fill_refused:{outcome.reason}")
            state = outcome.state
        record = replace(record, fills=state.fills)
        if state.is_closed:
            self._store.save(position.command_id, record)
            return DemoStepReport("closed")

        alerts: list[str] = []
        planned, moves = self._planned_stop(state, terms, bars, evaluated_at)
        mark = self._venue.mark_price()
        crossed = mark <= planned if terms.direction is TradeDirection.LONG else mark >= planned
        if planned != state.current_stop and crossed:
            alerts.append("protection_crossed")
            planned, moves = state.current_stop, ()
        record, placed_alerts = self._protect(
            position, record, ours, planned, state.remaining_quantity, close_side
        )
        alerts.extend(placed_alerts)
        if "stop_place_failed" not in placed_alerts:
            for move in moves:
                state = apply_stop_move(state, move).state
            record = replace(record, stop_moves=state.stop_moves)
        self._place_legs(position, state, orders, close_side)
        self._store.save(position.command_id, record)
        return DemoStepReport("managed", alerts=tuple(alerts))

    def _is_ours(
        self, position: DemoLadderPosition, record: DemoExitRecord, order: VenueOrder
    ) -> bool:
        return (
            order.client_order_id.startswith(self.client_order_id(position.command_id, ""))
            or order.order_id == position.entry_stop_order_id
            or order.order_id in record.stop_order_ids
        )

    def _planned_stop(
        self,
        state: PositionExitState,
        terms: PartialExitTerms,
        bars: Sequence[ClosedBar],
        evaluated_at: datetime | None,
    ) -> tuple[Decimal, tuple[StopMove, ...]]:
        moves: list[StopMove] = []
        working = state
        breakeven = plan_breakeven_move(working)
        if breakeven is not None:
            working = apply_stop_move(working, breakeven).state
            moves.append(breakeven)
        if (
            terms.runner_rule is not None
            and not working.pending_target_legs()
            and bars
            and evaluated_at is not None
        ):
            closed = [bar for bar in bars if bar.interval_end <= evaluated_at]
            trailed = trail_runner_stop(
                rule=terms.runner_rule,
                direction=terms.direction,
                current_stop=working.current_stop,
                bars=closed,
                evaluated_at=evaluated_at,
            )
            if closed and trailed != working.current_stop:
                last = max(bar.interval_end for bar in closed)
                trail = StopMove(
                    key=f"trail:{last.isoformat()}",
                    reason="trail",
                    previous_stop=working.current_stop,
                    new_stop=trailed,
                    effective_at=last,
                    tightened=True,
                )
                working = apply_stop_move(working, trail).state
                moves.append(trail)
        return working.current_stop, tuple(moves)

    def _protect(
        self,
        position: DemoLadderPosition,
        record: DemoExitRecord,
        ours: Sequence[VenueOrder],
        trigger: Decimal,
        size: Decimal,
        close_side: str,
    ) -> tuple[DemoExitRecord, list[str]]:
        alerts: list[str] = []
        by_id = {order.order_id: order for order in ours}
        active_id = record.active_stop_order_id or position.entry_stop_order_id
        active = by_id.get(active_id) if active_id else None
        pending = list(record.pending_cancel)
        if active is None or (active.price, active.size) != (trigger, size):
            client_id = self._stop_client_id(position.command_id, trigger, size)
            adopted = next((order for order in ours if order.client_order_id == client_id), None)
            if adopted is not None:
                new_id = adopted.order_id
            else:
                try:
                    new_id = self._venue.place_reduce_only_stop(
                        client_order_id=client_id,
                        side=close_side,
                        size=size,
                        trigger_price=trigger,
                    )
                except Exception:
                    return record, ["stop_place_failed"]
            if active_id and active_id != new_id:
                pending.append(active_id)
            record = replace(
                record,
                active_stop_order_id=new_id,
                stop_order_ids=tuple(dict.fromkeys((*record.stop_order_ids, new_id))),
            )
        elif record.active_stop_order_id is None:
            # Adopt the entry's attached stop; it must never look stale.
            record = replace(record, active_stop_order_id=active.order_id)
        # Every stop of ours other than the active one is stale. Cancel only
        # now, after the active stop is known to exist.
        active_now = record.active_stop_order_id
        stale = [
            order.order_id
            for order in ours
            if order.kind == "stop" and order.order_id != active_now
        ]
        remaining: list[str] = []
        for order_id in dict.fromkeys((*pending, *stale)):
            if order_id == active_now or (order_id not in by_id and order_id not in stale):
                continue
            try:
                self._venue.cancel_stop(order_id=order_id)
            except Exception:
                remaining.append(order_id)
                if "stop_cancel_failed" not in alerts:
                    alerts.append("stop_cancel_failed")
        return replace(record, pending_cancel=tuple(remaining)), alerts

    def _place_legs(
        self,
        position: DemoLadderPosition,
        state: PositionExitState,
        orders: Sequence[VenueOrder],
        close_side: str,
    ) -> None:
        open_clients = {order.client_order_id for order in orders}
        for order in state.pending_target_legs():
            leg = state.ladder.leg(order)
            if leg is None or leg.price is None:
                continue
            client_id = self.client_order_id(position.command_id, f"tp{order}")
            if client_id in open_clients or state.leg_filled(order) > 0:
                # A partly filled leg keeps its resting remainder at the venue.
                continue
            self._venue.place_reduce_only_limit(
                client_order_id=client_id,
                side=close_side,
                size=leg.quantity,
                price=leg.price,
            )
