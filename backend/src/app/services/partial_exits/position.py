"""Exit state of one position managed by a partial-exit ladder.

The state is a pure fold of durable records: exit fills keyed by a stable
fill id and stop moves keyed by a stable move key. Applying a record twice is
a no-op, the same key with different content is refused, and a fill can never
close more than its leg or the remaining position. Fills are kept in
chronological order, so the state does not depend on arrival order and a
restart that reloads the same records rebuilds the same state.

TP1 is confirmed only when the first target leg is completely filled. The
breakeven stop is then entry plus the per-unit cost allowance (prior partial
profit is ignored), rounded away from entry to the tick, and never looser
than the current stop.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from app.schemas.common import TradeDirection
from app.services.partial_exits.ladder import ExitLadder

FillKind = Literal["target", "stop", "runner_stop"]
StopMoveReason = Literal["breakeven", "trail"]
BREAKEVEN_MOVE_KEY = "breakeven"


@dataclass(frozen=True, slots=True)
class ExitFill:
    """One reduction fill. ``leg_order`` is set for target fills only."""

    fill_id: str
    kind: FillKind
    leg_order: int | None
    quantity: Decimal
    price: Decimal
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StopMove:
    """A recorded protective stop change. ``tightened`` is False when the
    computed level was not tighter and the stop stayed where it was."""

    key: str
    reason: StopMoveReason
    previous_stop: Decimal
    new_stop: Decimal
    effective_at: datetime
    tightened: bool


@dataclass(frozen=True, slots=True)
class PositionExitState:
    ladder: ExitLadder
    direction: TradeDirection
    entry_price: Decimal
    initial_stop: Decimal
    tick_size: Decimal
    cost_per_unit: Decimal
    fills: tuple[ExitFill, ...] = ()
    stop_moves: tuple[StopMove, ...] = ()

    @property
    def closed_quantity(self) -> Decimal:
        return sum((fill.quantity for fill in self.fills), Decimal("0"))

    @property
    def remaining_quantity(self) -> Decimal:
        return self.ladder.position_quantity - self.closed_quantity

    @property
    def is_closed(self) -> bool:
        return self.remaining_quantity == 0

    @property
    def current_stop(self) -> Decimal:
        return self.stop_moves[-1].new_stop if self.stop_moves else self.initial_stop

    def leg_filled(self, order: int) -> Decimal:
        return sum(
            (
                fill.quantity
                for fill in self.fills
                if fill.kind == "target" and fill.leg_order == order
            ),
            Decimal("0"),
        )

    @property
    def first_leg_order(self) -> int | None:
        legs = self.ladder.target_legs
        return legs[0].order if legs else None

    @property
    def tp1_confirmed(self) -> bool:
        first = self.ladder.target_legs[0] if self.ladder.target_legs else None
        return first is not None and self.leg_filled(first.order) == first.quantity

    def tp1_completed_at(self) -> datetime | None:
        """When the fill that completed the first target leg occurred."""

        first = self.ladder.target_legs[0] if self.ladder.target_legs else None
        if first is None:
            return None
        filled = Decimal("0")
        for fill in self.fills:
            if fill.kind == "target" and fill.leg_order == first.order:
                filled += fill.quantity
                if filled == first.quantity:
                    return fill.occurred_at
        return None

    def pending_target_legs(self) -> tuple[int, ...]:
        return tuple(
            leg.order
            for leg in self.ladder.target_legs
            if self.leg_filled(leg.order) < leg.quantity
        )

    def has_move(self, key: str) -> bool:
        return any(move.key == key for move in self.stop_moves)


@dataclass(frozen=True, slots=True)
class FillOutcome:
    state: PositionExitState
    status: Literal["applied", "duplicate", "refused"]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StopMoveOutcome:
    state: PositionExitState
    status: Literal["applied", "duplicate", "refused"]
    reason: str = ""


def open_position_state(
    *,
    ladder: ExitLadder,
    direction: TradeDirection,
    entry_price: Decimal,
    initial_stop: Decimal,
    tick_size: Decimal,
    cost_per_unit: Decimal,
) -> PositionExitState:
    return PositionExitState(
        ladder=ladder,
        direction=direction,
        entry_price=entry_price,
        initial_stop=initial_stop,
        tick_size=tick_size,
        cost_per_unit=cost_per_unit,
    )


def _sort_key(fill: ExitFill) -> tuple[datetime, str]:
    return (fill.occurred_at, fill.fill_id)


def apply_fill(state: PositionExitState, fill: ExitFill) -> FillOutcome:
    """Add one fill. Never closes more than its leg or the remaining position."""

    existing = next((item for item in state.fills if item.fill_id == fill.fill_id), None)
    if existing is not None:
        if existing == fill:
            return FillOutcome(state, "duplicate")
        return FillOutcome(state, "refused", "fill_conflict")
    if not fill.quantity.is_finite() or fill.quantity <= 0:
        return FillOutcome(state, "refused", "non_positive_quantity")
    if not fill.price.is_finite() or fill.price <= 0:
        return FillOutcome(state, "refused", "non_positive_price")
    if state.is_closed:
        return FillOutcome(state, "refused", "already_closed")
    if fill.kind == "target":
        leg = state.ladder.leg(fill.leg_order) if fill.leg_order is not None else None
        if leg is None:
            return FillOutcome(state, "refused", "unknown_leg")
        if state.leg_filled(leg.order) + fill.quantity > leg.quantity:
            return FillOutcome(state, "refused", "leg_overfill")
    elif fill.leg_order is not None:
        return FillOutcome(state, "refused", "unknown_leg")
    if fill.quantity > state.remaining_quantity:
        return FillOutcome(state, "refused", "position_overfill")
    fills = tuple(sorted((*state.fills, fill), key=_sort_key))
    return FillOutcome(replace(state, fills=fills), "applied")


def breakeven_stop(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    cost_per_unit: Decimal,
    tick_size: Decimal,
) -> Decimal:
    """Entry plus costs per unit, rounded away from entry to the tick."""

    if direction is TradeDirection.LONG:
        raw = entry_price + cost_per_unit
        return (raw / tick_size).to_integral_value(rounding=ROUND_CEILING) * tick_size
    raw = entry_price - cost_per_unit
    return (raw / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size


def tighter_stop(direction: TradeDirection, current: Decimal, candidate: Decimal) -> Decimal:
    if direction is TradeDirection.LONG:
        return max(current, candidate)
    return min(current, candidate)


def plan_breakeven_move(state: PositionExitState) -> StopMove | None:
    """The one breakeven move after TP1 completes, or None (not yet, or done)."""

    if state.has_move(BREAKEVEN_MOVE_KEY) or state.is_closed or not state.tp1_confirmed:
        return None
    completed_at = state.tp1_completed_at()
    if completed_at is None:
        return None
    level = breakeven_stop(
        direction=state.direction,
        entry_price=state.entry_price,
        cost_per_unit=state.cost_per_unit,
        tick_size=state.tick_size,
    )
    current = state.current_stop
    new_stop = tighter_stop(state.direction, current, level)
    return StopMove(
        key=BREAKEVEN_MOVE_KEY,
        reason="breakeven",
        previous_stop=current,
        new_stop=new_stop,
        effective_at=completed_at,
        tightened=new_stop != current,
    )


def apply_stop_move(state: PositionExitState, move: StopMove) -> StopMoveOutcome:
    """Record one stop move. A move that would loosen the stop is refused."""

    existing = next((item for item in state.stop_moves if item.key == move.key), None)
    if existing is not None:
        if existing == move:
            return StopMoveOutcome(state, "duplicate")
        return StopMoveOutcome(state, "refused", "stop_move_conflict")
    if tighter_stop(state.direction, state.current_stop, move.new_stop) != move.new_stop:
        return StopMoveOutcome(state, "refused", "stop_would_widen")
    return StopMoveOutcome(replace(state, stop_moves=(*state.stop_moves, move)), "applied")


def rebuild_position_state(
    base: PositionExitState,
    *,
    fills: Iterable[ExitFill],
    stop_moves: Iterable[StopMove],
) -> PositionExitState:
    """Rebuild from durable records after a restart. Refuses inconsistent records.

    Stop moves are replayed in effective order; a move that no longer applies
    (it would widen, or its key conflicts) raises, because durable state that
    does not fold cleanly must not be silently trusted.
    """

    state = base
    for fill in sorted(fills, key=_sort_key):
        outcome = apply_fill(state, fill)
        if outcome.status == "refused":
            raise ValueError(f"inconsistent exit fill record: {outcome.reason}")
        state = outcome.state
    for move in sorted(stop_moves, key=lambda item: (item.effective_at, item.key)):
        moved = apply_stop_move(state, move)
        if moved.status == "refused":
            raise ValueError(f"inconsistent stop move record: {moved.reason}")
        state = moved.state
    return state
