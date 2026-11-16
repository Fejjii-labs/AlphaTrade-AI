"""Partial-exit management for one governed BloFin demo position.

Bot-managed: no exchange-side TP/SL is used for a ladder, because BloFin does
not document how an attached or standalone TP/SL is sized after the position
is partly reduced. Each step reads the mark and closes with market
``reduceOnly`` orders on the closing side only, so no call here can open or
add to a position. One ``step``:

1. Gates first, before any venue call: the demo flag (which also requires the
   demo exchange mode and real trading off), then the hold (kill switch,
   worker pause, daily loss lock). A gated step reads and writes nothing.
2. Our closing orders (client id prefix) and their fills are folded into the
   durable state. A duplicate fill counts once and arrival order does not
   matter. An order of ours that is not reduce-only on the closing side, an
   unknown client id, or a fill that would overfill a leg or the position
   holds with no venue mutation. A close still live at the venue holds too.
3. The planned stop is computed: initial, breakeven plus costs after a
   complete TP1, then the closed-candle runner trail. Moves are recorded and
   never loosen.
4. The venue position must equal the recorded remainder, or the step holds.
   Then a crossed stop closes the whole remainder; otherwise every pending
   target leg whose price is reached is closed for its unfilled quantity.

Client order ids are ``pe`` + 20 hex of SHA-256(command id) + ``t<leg>`` or ``x`` +
``n<attempt>``, where the attempt counts our earlier orders for that leg. A
restart therefore reuses the id of a close that was never accepted, and a
close that was accepted but only partly filled is completed by the next
attempt for the unfilled remainder only.
"""

from __future__ import annotations

import hashlib
import re
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

CloseState = Literal["live", "partially_filled", "filled", "canceled"]
_IN_FLIGHT: frozenset[str] = frozenset({"live", "partially_filled"})
_SUFFIX = re.compile(r"(?:t(?P<leg>[1-9][0-9]?)|x)n(?P<attempt>[0-9]{1,3})")


@dataclass(frozen=True, slots=True)
class VenueCloseOrder:
    order_id: str
    client_order_id: str
    side: str
    size: Decimal
    filled: Decimal
    reduce_only: bool
    state: CloseState


@dataclass(frozen=True, slots=True)
class VenueExitFill:
    order_id: str
    trade_id: str
    client_order_id: str
    quantity: Decimal
    price: Decimal
    occurred_at: datetime


class DemoReduceOnlyVenue(Protocol):
    def closing_orders(self, client_prefix: str) -> tuple[VenueCloseOrder, ...]: ...

    def exit_fills(self, order_ids: tuple[str, ...]) -> tuple[VenueExitFill, ...]: ...

    def position_quantity(self) -> Decimal:
        """Signed net position on the plan's instrument (short is negative)."""
        ...

    def mark_price(self) -> Decimal: ...

    def place_reduce_only_market(
        self, *, client_order_id: str, side: str, size: Decimal
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class DemoExitRecord:
    fills: tuple[ExitFill, ...] = ()
    stop_moves: tuple[StopMove, ...] = ()


class DemoExitStore(Protocol):
    def load(self, command_id: UUID) -> DemoExitRecord: ...

    def save(self, command_id: UUID, record: DemoExitRecord) -> None: ...


@dataclass(frozen=True, slots=True)
class DemoLadderPosition:
    organization_id: UUID
    command_id: UUID
    terms: PartialExitTerms


@dataclass(frozen=True, slots=True)
class DemoStepReport:
    status: Literal["disabled", "held", "managed", "closed"]
    reason: str = ""
    alerts: tuple[str, ...] = ()
    placed: tuple[str, ...] = ()


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
    def client_prefix(command_id: UUID) -> str:
        # A digest of the whole id: two commands never share a prefix by
        # sharing their leading UUID digits. 26 characters at most in all.
        return f"pe{hashlib.sha256(command_id.bytes).hexdigest()[:20]}"

    @classmethod
    def close_client_id(cls, command_id: UUID, leg: str, attempt: int) -> str:
        """``leg`` is ``t<order>`` for a target leg or ``x`` for a stop close."""
        return f"{cls.client_prefix(command_id)}{leg}n{attempt}"

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
        command_id = position.command_id
        record = self._store.load(command_id)
        state = rebuild_position_state(
            terms.open_state(), fills=record.fills, stop_moves=record.stop_moves
        )
        long = terms.direction is TradeDirection.LONG
        close_side = "sell" if long else "buy"
        prefix = self.client_prefix(command_id)

        # Our closing orders: every one must be a reduce-only close we can name.
        orders = self._venue.closing_orders(prefix)
        leg_of: dict[str, int | None] = {}
        for order in orders:
            if not order.client_order_id.startswith(prefix):
                return DemoStepReport("held", "unexplained_order")
            match = _SUFFIX.fullmatch(order.client_order_id[len(prefix) :])
            if match is None:
                return DemoStepReport("held", "unexplained_order")
            if not order.reduce_only or order.side != close_side:
                return DemoStepReport("held", "order_shape_unexpected")
            leg = int(match["leg"]) if match["leg"] else None
            if leg is not None and terms.ladder.leg(leg) is None:
                return DemoStepReport("held", "unexplained_order")
            leg_of[order.order_id] = leg

        filled_ids = tuple(order.order_id for order in orders if order.filled > 0)
        venue_fills = self._venue.exit_fills(filled_ids) if filled_ids else ()
        for venue_fill in sorted(venue_fills, key=lambda item: (item.occurred_at, item.trade_id)):
            if venue_fill.order_id not in leg_of:
                self._store.save(command_id, replace(record, fills=state.fills))
                return DemoStepReport("held", "unexplained_fill")
            leg = leg_of[venue_fill.order_id]
            fill = ExitFill(
                fill_id=f"demo:{venue_fill.order_id}:{venue_fill.trade_id}",
                kind="target"
                if leg is not None
                else "stop"
                if state.pending_target_legs()
                else "runner_stop",
                leg_order=leg,
                quantity=venue_fill.quantity,
                price=venue_fill.price,
                occurred_at=venue_fill.occurred_at,
            )
            outcome = apply_fill(state, fill)
            if outcome.status == "refused":
                self._store.save(command_id, replace(record, fills=state.fills))
                return DemoStepReport("held", f"fill_refused:{outcome.reason}")
            state = outcome.state
        record = replace(record, fills=state.fills)
        if any(order.state in _IN_FLIGHT for order in orders):
            self._store.save(command_id, record)
            return DemoStepReport("held", "close_in_flight")
        if state.is_closed:
            self._store.save(command_id, record)
            return DemoStepReport("closed")

        for move in self._planned_moves(state, terms, bars, evaluated_at):
            state = apply_stop_move(state, move).state
        record = replace(record, stop_moves=state.stop_moves)
        self._store.save(command_id, record)

        expected = state.remaining_quantity if long else -state.remaining_quantity
        if self._venue.position_quantity() != expected:
            return DemoStepReport("held", "venue_position_mismatch")
        mark = self._venue.mark_price()
        stop = state.current_stop
        attempts: dict[int | None, int] = {}
        for leg in leg_of.values():
            attempts[leg] = attempts.get(leg, 0) + 1

        closes: list[tuple[str, Decimal]] = []
        if (mark <= stop) if long else (mark >= stop):
            closes.append(
                (
                    self.close_client_id(command_id, "x", attempts.get(None, 0)),
                    state.remaining_quantity,
                )
            )
        else:
            for leg_order in state.pending_target_legs():
                ladder_leg = state.ladder.leg(leg_order)
                if ladder_leg is None or ladder_leg.price is None:
                    continue
                if not ((mark >= ladder_leg.price) if long else (mark <= ladder_leg.price)):
                    break
                closes.append(
                    (
                        self.close_client_id(
                            command_id, f"t{leg_order}", attempts.get(leg_order, 0)
                        ),
                        ladder_leg.quantity - state.leg_filled(leg_order),
                    )
                )
        placed: list[str] = []
        alerts: list[str] = []
        for client_id, size in closes:
            try:
                self._venue.place_reduce_only_market(
                    client_order_id=client_id, side=close_side, size=size
                )
            except Exception:
                alerts.append("close_place_failed")
                break
            placed.append(client_id)
        return DemoStepReport("managed", alerts=tuple(alerts), placed=tuple(placed))

    @staticmethod
    def _planned_moves(
        state: PositionExitState,
        terms: PartialExitTerms,
        bars: Sequence[ClosedBar],
        evaluated_at: datetime | None,
    ) -> tuple[StopMove, ...]:
        moves: list[StopMove] = []
        working = state
        breakeven = plan_breakeven_move(working)
        if breakeven is not None:
            working = apply_stop_move(working, breakeven).state
            moves.append(breakeven)
        if (
            terms.runner_rule is None
            or working.pending_target_legs()
            or not bars
            or evaluated_at is None
        ):
            return tuple(moves)
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
            # Replay sorts moves by effective time; a trail must never sort
            # before the move it tightens or a fill it follows.
            effective = max(
                (
                    last,
                    *(move.effective_at for move in working.stop_moves),
                    *(fill.occurred_at for fill in working.fills),
                )
            )
            moves.append(
                StopMove(
                    key=f"trail:{last.isoformat()}",
                    reason="trail",
                    previous_stop=working.current_stop,
                    new_stop=trailed,
                    effective_at=effective,
                    tightened=True,
                )
            )
        return tuple(moves)
