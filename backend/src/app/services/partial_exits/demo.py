"""Partial-exit management for one governed BloFin demo position.

Bot-managed: no exchange-side TP/SL is used for a ladder, because BloFin does
not document how an attached or standalone TP/SL is sized after the position
is partly reduced. Each step reads the mark and closes with market
``reduceOnly`` orders on the closing side only, so no call here can open or
add to a position. One ``step``:

1. Gates first, before any venue call: the demo flag (which also requires the
   demo exchange mode and real trading off), then the hold. The kill switch,
   the worker pause and the daily loss lock stop new risk, not risk
   reduction: they block target closes only, and a crossed stop still closes
   the remainder (a ladder has no exchange-side stop). Any other hold reason,
   an unreadable gate included, reads and writes nothing.
2. Our closing orders (client id prefix) and their fills are folded into the
   durable state. A duplicate fill counts once and arrival order does not
   matter. An order of ours that is not reduce-only on the closing side, an
   unknown client id, or a fill that would overfill a leg or the position
   holds for an operator; only a crossed stop, at the recorded stop, still
   closes what is left on our side. A close still live at the venue holds
   too, with no new order; the stop close follows once it is resolved.
3. The planned stop is computed: initial, breakeven plus costs after a
   complete TP1, then the closed-candle runner trail. Moves are recorded and
   never loosen.
4. A crossed stop closes the whole remainder. If the venue position differs
   from the recorded remainder, the step holds, except that a crossed stop
   still closes what is left of the position on the closing side, up to the
   remainder. Otherwise, with no hold, every pending target leg whose price is
   reached is closed for its unfilled quantity. A close the venue refuses
   (including a failed account identity check) is sent no further.

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
# Holds that stop new risk but never risk reduction: a crossed stop still
# closes. Any other reason (an unreadable gate, for one) holds every call.
STOP_ONLY_HOLD_REASONS: frozenset[str] = frozenset(
    {"kill_switch_active", "worker_paused", "daily_loss_lock"}
)
_SUFFIX = re.compile(r"(?:t(?P<leg>[1-9][0-9]?)|x)n(?P<attempt>[0-9]{1,3})")


def _crossed(mark: Decimal, stop: Decimal, *, long: bool) -> bool:
    return mark <= stop if long else mark >= stop


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
        hold = self._hold(position.organization_id)
        if hold is not None and hold not in STOP_ONLY_HOLD_REASONS:
            return DemoStepReport("held", hold)
        terms = position.terms
        command_id = position.command_id
        record = self._store.load(command_id)
        state = rebuild_position_state(
            terms.open_state(), fills=record.fills, stop_moves=record.stop_moves
        )
        recorded = state
        long = terms.direction is TradeDirection.LONG
        close_side = "sell" if long else "buy"
        prefix = self.client_prefix(command_id)

        # Our closing orders: every one must be a reduce-only close we can name.
        orders = self._venue.closing_orders(prefix)
        leg_of: dict[str, int | None] = {}
        for order in orders:
            if not order.client_order_id.startswith(prefix):
                return self._hold_with_stop(command_id, long, recorded, orders, "unexplained_order")
            match = _SUFFIX.fullmatch(order.client_order_id[len(prefix) :])
            if match is None:
                return self._hold_with_stop(command_id, long, recorded, orders, "unexplained_order")
            if not order.reduce_only or order.side != close_side:
                return self._hold_with_stop(
                    command_id, long, recorded, orders, "order_shape_unexpected"
                )
            leg = int(match["leg"]) if match["leg"] else None
            if leg is not None and terms.ladder.leg(leg) is None:
                return self._hold_with_stop(command_id, long, recorded, orders, "unexplained_order")
            leg_of[order.order_id] = leg

        filled_ids = tuple(order.order_id for order in orders if order.filled > 0)
        venue_fills = self._venue.exit_fills(filled_ids) if filled_ids else ()
        for venue_fill in sorted(venue_fills, key=lambda item: (item.occurred_at, item.trade_id)):
            if venue_fill.order_id not in leg_of:
                self._store.save(command_id, replace(record, fills=state.fills))
                return self._hold_with_stop(command_id, long, recorded, orders, "unexplained_fill")
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
                return self._hold_with_stop(
                    command_id, long, recorded, orders, f"fill_refused:{outcome.reason}"
                )
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

        venue_quantity = self._venue.position_quantity()
        held_quantity = venue_quantity if long else -venue_quantity
        reason = hold
        stop_size = state.remaining_quantity
        if held_quantity != state.remaining_quantity:
            reason = "venue_position_mismatch"
            # Never more than the venue holds on our side; flat or flipped
            # leaves nothing of ours to reduce.
            stop_size = min(held_quantity, state.remaining_quantity)
        mark = self._venue.mark_price()
        stop = state.current_stop
        attempts: dict[int | None, int] = {}
        for leg in leg_of.values():
            attempts[leg] = attempts.get(leg, 0) + 1

        closes: list[tuple[str, Decimal]] = []
        if _crossed(mark, stop, long=long):
            if stop_size > 0:
                closes.append(
                    (self.close_client_id(command_id, "x", attempts.get(None, 0)), stop_size)
                )
        elif reason is None:
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
        placed, alerts = self._place(closes, close_side)
        if reason is not None:
            return DemoStepReport("held", reason, alerts, placed)
        return DemoStepReport("managed", alerts=alerts, placed=placed)

    def _hold_with_stop(
        self,
        command_id: UUID,
        long: bool,
        recorded: PositionExitState,
        orders: Sequence[VenueCloseOrder],
        reason: str,
    ) -> DemoStepReport:
        """Our own orders or fills cannot be explained: hold for an operator.

        A crossed stop still closes, at the recorded stop (moves never loosen)
        and for what is left on our side up to the recorded remainder. Nothing
        is sent while any of our closes is in flight.
        """

        if any(order.state in _IN_FLIGHT for order in orders):
            return DemoStepReport("held", reason)
        venue_quantity = self._venue.position_quantity()
        size = min(venue_quantity if long else -venue_quantity, recorded.remaining_quantity)
        if size <= 0 or not _crossed(self._venue.mark_price(), recorded.current_stop, long=long):
            return DemoStepReport("held", reason)
        prefix = self.client_prefix(command_id)
        attempt = 0
        for order in orders:
            match = _SUFFIX.fullmatch(order.client_order_id.removeprefix(prefix))
            if order.client_order_id.startswith(prefix) and match and match["leg"] is None:
                attempt += 1
        client_id = self.close_client_id(command_id, "x", attempt)
        placed, alerts = self._place([(client_id, size)], "sell" if long else "buy")
        return DemoStepReport("held", reason, alerts, placed)

    def _place(
        self, closes: Sequence[tuple[str, Decimal]], side: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        placed: list[str] = []
        alerts: list[str] = []
        for client_id, size in closes:
            try:
                self._venue.place_reduce_only_market(
                    client_order_id=client_id, side=side, size=size
                )
            except Exception:
                alerts.append("close_place_failed")
                break
            placed.append(client_id)
        return tuple(placed), tuple(alerts)

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
