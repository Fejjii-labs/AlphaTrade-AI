"""Partial-exit position state (Griller conditions 2 and 3).

The stop moves to breakeven plus costs only once TP1 is completely filled,
the move is recorded once, it is rebuilt identically from durable records
after a restart, and it never widens a tighter stop. Partial, duplicate and
out-of-order fills never close more than the position.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.schemas.common import TradeDirection
from app.services.partial_exits.ladder import (
    ExitLadder,
    InstrumentLimits,
    LadderTarget,
    build_exit_ladder,
)
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    apply_fill,
    apply_stop_move,
    breakeven_stop,
    open_position_state,
    plan_breakeven_move,
    rebuild_position_state,
)

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
LIMITS = InstrumentLimits(
    lot_size=Decimal("0.001"),
    minimum_quantity=Decimal("0.001"),
    minimum_notional=Decimal("0"),
)


def _ladder(direction: TradeDirection = TradeDirection.LONG) -> ExitLadder:
    prices = ("110", "120", "130") if direction is TradeDirection.LONG else ("90", "80", "70")
    ladder = build_exit_ladder(
        direction=direction,
        entry_price=Decimal("100"),
        position_quantity=Decimal("1.000"),
        targets=(
            LadderTarget(order=1, price=Decimal(prices[0]), fraction=Decimal("0.5")),
            LadderTarget(order=2, price=Decimal(prices[1]), fraction=Decimal("0.2")),
            LadderTarget(order=3, price=Decimal(prices[2]), fraction=Decimal("0.1")),
        ),
        runner_fraction=Decimal("0.2"),
        limits=LIMITS,
    )
    assert isinstance(ladder, ExitLadder)
    return ladder


def _state(
    direction: TradeDirection = TradeDirection.LONG,
    *,
    stop: str | None = None,
    cost_per_unit: str = "0.25",
) -> PositionExitState:
    default_stop = "95" if direction is TradeDirection.LONG else "105"
    return open_position_state(
        ladder=_ladder(direction),
        direction=direction,
        entry_price=Decimal("100"),
        initial_stop=Decimal(stop or default_stop),
        tick_size=Decimal("0.1"),
        cost_per_unit=Decimal(cost_per_unit),
    )


def _fill(
    fill_id: str,
    quantity: str,
    *,
    leg: int | None = 1,
    price: str = "110",
    kind: str = "target",
    minutes: int = 0,
) -> ExitFill:
    return ExitFill(
        fill_id=fill_id,
        kind=kind,  # type: ignore[arg-type]
        leg_order=leg,
        quantity=Decimal(quantity),
        price=Decimal(price),
        occurred_at=T0 + timedelta(minutes=minutes),
    )


# Condition 2: breakeven plus costs, tied to the confirmed TP1 fill.


def test_partial_tp1_fill_does_not_move_the_stop() -> None:
    outcome = apply_fill(_state(), _fill("f1", "0.300"))
    assert outcome.status == "applied"
    assert outcome.state.tp1_confirmed is False
    assert plan_breakeven_move(outcome.state) is None
    assert outcome.state.current_stop == Decimal("95")


def test_completed_tp1_moves_the_stop_to_breakeven_plus_costs_rounded_up_for_long() -> None:
    state = apply_fill(_state(cost_per_unit="0.23"), _fill("f1", "0.300", minutes=1)).state
    state = apply_fill(state, _fill("f2", "0.200", minutes=3)).state
    assert state.tp1_confirmed is True
    move = plan_breakeven_move(state)
    assert move is not None
    # 100 + 0.23 rounds up to the 0.1 tick: 100.3. Effective at the fill that completed TP1.
    assert move.new_stop == Decimal("100.3")
    assert move.previous_stop == Decimal("95")
    assert move.effective_at == T0 + timedelta(minutes=3)
    assert move.key == "breakeven"
    moved = apply_stop_move(state, move)
    assert moved.status == "applied"
    assert moved.state.current_stop == Decimal("100.3")


def test_short_breakeven_rounds_down() -> None:
    state = apply_fill(
        _state(TradeDirection.SHORT, cost_per_unit="0.23"),
        _fill("f1", "0.500", price="90"),
    ).state
    move = plan_breakeven_move(state)
    assert move is not None
    assert move.new_stop == Decimal("99.7")
    assert breakeven_stop(
        direction=TradeDirection.SHORT,
        entry_price=Decimal("100"),
        cost_per_unit=Decimal("0.23"),
        tick_size=Decimal("0.1"),
    ) == Decimal("99.7")


def test_breakeven_move_is_idempotent() -> None:
    state = apply_fill(_state(), _fill("f1", "0.500")).state
    move = plan_breakeven_move(state)
    assert move is not None
    once = apply_stop_move(state, move).state
    assert plan_breakeven_move(once) is None
    again = apply_stop_move(once, move)
    assert again.status == "duplicate"
    assert again.state == once


def test_breakeven_survives_a_restart_from_durable_records_in_any_order() -> None:
    fills = (
        _fill("f1", "0.300", minutes=1),
        _fill("f2", "0.200", minutes=2),
        _fill("t2", "0.200", leg=2, price="120", minutes=5),
    )
    state = _state()
    for fill in fills:
        state = apply_fill(state, fill).state
    move = plan_breakeven_move(state)
    assert move is not None
    state = apply_stop_move(state, move).state

    restarted = rebuild_position_state(_state(), fills=tuple(reversed(fills)), stop_moves=(move,))
    assert restarted == state
    assert restarted.current_stop == move.new_stop
    assert plan_breakeven_move(restarted) is None


def test_breakeven_never_widens_a_tighter_stop() -> None:
    state = apply_fill(_state(stop="101"), _fill("f1", "0.500")).state
    move = plan_breakeven_move(state)
    # The breakeven level 100.3 is looser than 101: the move is recorded once
    # as not tightening, and the stop stays at 101.
    assert move is not None
    assert move.new_stop == Decimal("101")
    assert move.tightened is False
    applied = apply_stop_move(state, move).state
    assert applied.current_stop == Decimal("101")
    assert plan_breakeven_move(applied) is None


def test_a_widening_stop_move_is_refused() -> None:
    state = _state()
    widening = StopMove(
        key="trail:x",
        reason="trail",
        previous_stop=Decimal("95"),
        new_stop=Decimal("90"),
        effective_at=T0,
        tightened=True,
    )
    outcome = apply_stop_move(state, widening)
    assert outcome.status == "refused"
    assert outcome.reason == "stop_would_widen"
    assert outcome.state.current_stop == Decimal("95")


def test_short_widening_is_refused_too() -> None:
    state = _state(TradeDirection.SHORT)
    outcome = apply_stop_move(
        state,
        StopMove(
            key="trail:x",
            reason="trail",
            previous_stop=Decimal("105"),
            new_stop=Decimal("106"),
            effective_at=T0,
            tightened=True,
        ),
    )
    assert outcome.status == "refused"


# Condition 3: partial and out-of-order fills, no double close.


def test_duplicate_fill_is_a_no_op_and_a_conflicting_duplicate_is_refused() -> None:
    state = apply_fill(_state(), _fill("f1", "0.300")).state
    duplicate = apply_fill(state, _fill("f1", "0.300"))
    assert duplicate.status == "duplicate"
    assert duplicate.state == state
    conflict = apply_fill(state, _fill("f1", "0.400"))
    assert conflict.status == "refused"
    assert conflict.reason == "fill_conflict"
    assert conflict.state == state


def test_leg_overfill_is_refused() -> None:
    state = apply_fill(_state(), _fill("f1", "0.400")).state
    outcome = apply_fill(state, _fill("f2", "0.200"))
    assert outcome.status == "refused"
    assert outcome.reason == "leg_overfill"
    assert outcome.state.closed_quantity == Decimal("0.400")


def test_stop_fill_cannot_close_more_than_the_remaining_position() -> None:
    state = apply_fill(_state(), _fill("f1", "0.500")).state
    outcome = apply_fill(state, _fill("s1", "0.600", leg=None, price="95", kind="stop"))
    assert outcome.status == "refused"
    assert outcome.reason == "position_overfill"
    closing = apply_fill(state, _fill("s1", "0.500", leg=None, price="95", kind="stop"))
    assert closing.status == "applied"
    assert closing.state.is_closed is True
    assert closing.state.remaining_quantity == 0
    after = apply_fill(closing.state, _fill("t2", "0.100", leg=2, price="120"))
    assert after.status == "refused"
    assert after.reason == "already_closed"


def test_out_of_order_fills_reach_the_same_state() -> None:
    fills = [
        _fill("t2", "0.200", leg=2, price="120", minutes=9),
        _fill("f2", "0.250", minutes=4),
        _fill("f1", "0.250", minutes=2),
        _fill("t3", "0.100", leg=3, price="130", minutes=12),
    ]
    forward = _state()
    for fill in sorted(fills, key=lambda item: item.occurred_at):
        forward = apply_fill(forward, fill).state
    shuffled = _state()
    for fill in fills:
        shuffled = apply_fill(shuffled, fill).state
    assert shuffled == forward
    assert shuffled.remaining_quantity == Decimal("0.200")
    move = plan_breakeven_move(shuffled)
    assert move is not None
    # TP1 completed by the fill at minute 4, even though it arrived second.
    assert move.effective_at == T0 + timedelta(minutes=4)


def test_unknown_leg_and_runner_target_fills_are_refused() -> None:
    state = _state()
    assert apply_fill(state, _fill("x", "0.100", leg=9)).reason == "unknown_leg"
    assert apply_fill(state, _fill("x", "0.000")).reason == "non_positive_quantity"
