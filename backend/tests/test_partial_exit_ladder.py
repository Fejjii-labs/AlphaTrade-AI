"""Partial-exit leg sizing (Griller condition 1).

Legs sum exactly to the position after lot rounding, the last leg takes the
remainder, and a leg under the instrument minimum merges or the plan is
refused with an auditable reason.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.common import TradeDirection
from app.services.partial_exits.ladder import (
    ExitLadder,
    InstrumentLimits,
    LadderRefusal,
    LadderTarget,
    build_exit_ladder,
)

LIMITS = InstrumentLimits(
    lot_size=Decimal("0.001"),
    minimum_quantity=Decimal("0.001"),
    minimum_notional=Decimal("0"),
)


def _targets(*rows: tuple[str, str]) -> tuple[LadderTarget, ...]:
    return tuple(
        LadderTarget(order=index, price=Decimal(price), fraction=Decimal(fraction))
        for index, (price, fraction) in enumerate(rows, start=1)
    )


def _ladder(
    *,
    quantity: str = "0.103",
    targets: tuple[LadderTarget, ...] | None = None,
    runner: str = "0.2",
    limits: InstrumentLimits = LIMITS,
    direction: TradeDirection = TradeDirection.LONG,
    entry: str = "100",
) -> ExitLadder | LadderRefusal:
    return build_exit_ladder(
        direction=direction,
        entry_price=Decimal(entry),
        position_quantity=Decimal(quantity),
        targets=targets
        if targets is not None
        else _targets(("110", "0.4"), ("120", "0.2"), ("130", "0.1"), ("140", "0.1")),
        runner_fraction=Decimal(runner),
        limits=limits,
    )


def test_four_targets_and_runner_sum_exactly_and_the_runner_takes_the_remainder() -> None:
    ladder = _ladder()
    assert isinstance(ladder, ExitLadder)
    quantities = [leg.quantity for leg in ladder.legs]
    # floor(0.103 * f) to 0.001 lots: 0.041, 0.020, 0.010, 0.010; runner = remainder.
    assert quantities == [
        Decimal("0.041"),
        Decimal("0.020"),
        Decimal("0.010"),
        Decimal("0.010"),
        Decimal("0.022"),
    ]
    assert sum(quantities) == Decimal("0.103")
    assert ladder.runner_leg is not None and ladder.runner_leg.quantity == Decimal("0.022")
    assert all(leg.quantity % LIMITS.lot_size == 0 for leg in ladder.legs)


def test_without_a_runner_the_last_target_takes_the_remainder() -> None:
    ladder = _ladder(
        quantity="0.007",
        targets=_targets(("110", "0.5"), ("120", "0.5")),
        runner="0",
    )
    assert isinstance(ladder, ExitLadder)
    assert [leg.quantity for leg in ladder.legs] == [Decimal("0.003"), Decimal("0.004")]
    assert ladder.runner_leg is None


@pytest.mark.parametrize(
    ("quantity", "fractions"),
    [
        ("0.103", ("0.4", "0.2", "0.1", "0.1", "0.2")),
        ("1.337", ("0.4", "0.2", "0.1", "0.1", "0.2")),
        ("0.019", ("0.5", "0.3", "0.2")),
        ("12.345", ("0.33", "0.33", "0.34")),
    ],
)
def test_legs_always_sum_to_the_position(quantity: str, fractions: tuple[str, ...]) -> None:
    *target_fractions, runner = fractions
    targets = _targets(
        *((str(110 + 10 * index), fraction) for index, fraction in enumerate(target_fractions))
    )
    ladder = _ladder(quantity=quantity, targets=targets, runner=runner)
    assert isinstance(ladder, ExitLadder)
    assert sum(leg.quantity for leg in ladder.legs) == Decimal(quantity)


def test_fractions_that_do_not_sum_to_one_are_refused() -> None:
    refusal = _ladder(targets=_targets(("110", "0.4"), ("120", "0.3")), runner="0.2")
    assert isinstance(refusal, LadderRefusal)
    assert refusal.reason == "fractions_do_not_sum"


def test_sub_minimum_leg_merges_into_the_next_leg_and_records_the_merge() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.005"),
        minimum_notional=Decimal("0"),
    )
    # 0.020 * 0.1 = 0.002 is under the 0.005 minimum for TP3.
    ladder = _ladder(
        quantity="0.020",
        targets=_targets(("110", "0.5"), ("120", "0.3"), ("130", "0.1"), ("140", "0.1")),
        runner="0",
        limits=limits,
    )
    assert isinstance(ladder, ExitLadder)
    # TP3 0.002 merges forward into TP4 (0.004, still short), and the last
    # target has no next leg, so it merges back into TP2. The merge log keeps
    # that earlier exit auditable.
    assert [(leg.price, leg.quantity, leg.source_orders) for leg in ladder.legs] == [
        (Decimal("110"), Decimal("0.010"), (1,)),
        (Decimal("120"), Decimal("0.010"), (2, 3, 4)),
    ]
    assert ladder.merges == ("target:3->target:4", "target:4->target:2")
    assert all(leg.quantity >= limits.minimum_quantity for leg in ladder.legs)


def test_every_leg_meets_minimum_quantity_after_merging() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.004"),
        minimum_notional=Decimal("0"),
    )
    ladder = _ladder(
        quantity="0.020",
        targets=_targets(("110", "0.5"), ("120", "0.3"), ("130", "0.1"), ("140", "0.1")),
        runner="0",
        limits=limits,
    )
    assert isinstance(ladder, ExitLadder)
    assert sum(leg.quantity for leg in ladder.legs) == Decimal("0.020")
    assert all(leg.quantity >= limits.minimum_quantity for leg in ladder.legs)
    # TP3 (0.002) merged forward into TP4: 0.002 + remainder 0.004 = 0.006.
    assert [leg.source_orders for leg in ladder.legs] == [(1,), (2,), (3, 4)]


def test_sub_minimum_last_target_merges_back_into_the_previous_target() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.004"),
        minimum_notional=Decimal("0"),
    )
    ladder = _ladder(
        quantity="0.010",
        targets=_targets(("110", "0.7"), ("120", "0.3")),
        runner="0",
        limits=limits,
    )
    assert isinstance(ladder, ExitLadder)
    assert [(leg.price, leg.quantity, leg.source_orders) for leg in ladder.legs] == [
        (Decimal("110"), Decimal("0.010"), (1, 2)),
    ]
    assert ladder.merges == ("target:2->target:1",)


def test_sub_minimum_runner_merges_into_the_last_target_and_disables_the_runner() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.003"),
        minimum_notional=Decimal("0"),
    )
    ladder = _ladder(
        quantity="0.010",
        targets=_targets(("110", "0.5"), ("120", "0.3")),
        runner="0.2",
        limits=limits,
    )
    assert isinstance(ladder, ExitLadder)
    assert ladder.runner_leg is None
    assert [leg.quantity for leg in ladder.legs] == [Decimal("0.005"), Decimal("0.005")]
    assert "runner->target:2" in ladder.merges


def test_minimum_notional_uses_the_leg_price_and_merges() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
    )
    # TP1 0.040 * 110 = 4.4 < 5 merges into TP2; TP2 total 0.100 * 120 = 12.
    ladder = _ladder(
        quantity="0.100",
        targets=_targets(("110", "0.4"), ("120", "0.6")),
        runner="0",
        limits=limits,
    )
    assert isinstance(ladder, ExitLadder)
    assert [(leg.price, leg.quantity) for leg in ladder.legs] == [
        (Decimal("120"), Decimal("0.100"))
    ]
    assert ladder.merges == ("target:1->target:2",)


def test_refuses_when_even_the_whole_position_is_below_the_minimum() -> None:
    limits = InstrumentLimits(
        lot_size=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("100"),
    )
    refusal = _ladder(
        quantity="0.010",
        targets=_targets(("110", "0.5"), ("120", "0.5")),
        runner="0",
        limits=limits,
    )
    assert isinstance(refusal, LadderRefusal)
    assert refusal.reason == "leg_below_minimum"
    assert refusal.detail["position_quantity"] == "0.010"


def test_position_that_is_not_a_lot_multiple_is_refused() -> None:
    refusal = _ladder(quantity="0.1035")
    assert isinstance(refusal, LadderRefusal)
    assert refusal.reason == "position_not_lot_multiple"


def test_targets_must_be_strictly_favorable_and_ordered_for_longs() -> None:
    refusal = _ladder(targets=_targets(("110", "0.5"), ("105", "0.3")), runner="0.2")
    assert isinstance(refusal, LadderRefusal)
    assert refusal.reason == "targets_not_ordered"
    wrong_side = _ladder(targets=_targets(("95", "0.8")), runner="0.2")
    assert isinstance(wrong_side, LadderRefusal)
    assert wrong_side.reason == "target_wrong_side"


def test_short_targets_descend_below_entry() -> None:
    ladder = _ladder(
        direction=TradeDirection.SHORT,
        targets=_targets(("90", "0.4"), ("80", "0.2"), ("70", "0.2")),
        runner="0.2",
    )
    assert isinstance(ladder, ExitLadder)
    assert sum(leg.quantity for leg in ladder.legs) == Decimal("0.103")
    refusal = _ladder(
        direction=TradeDirection.SHORT,
        targets=_targets(("90", "0.4"), ("95", "0.4")),
        runner="0.2",
    )
    assert isinstance(refusal, LadderRefusal)
    assert refusal.reason == "targets_not_ordered"
