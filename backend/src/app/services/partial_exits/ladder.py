"""Exit-leg sizing for a plan with several targets and an optional runner.

Each leg except the last is the plan fraction of the position rounded down
to the lot. The last leg takes the remainder, so the legs always sum to the
position exactly. A leg under the instrument minimum quantity or notional
merges into the next leg; the last leg, having no next leg, merges back into
the previous one. When even a single leg cannot meet the minimum, the plan is
refused with a reason the caller audits. Every merge is recorded.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from app.schemas.common import TradeDirection

LegKind = Literal["target", "runner"]


@dataclass(frozen=True, slots=True)
class LadderTarget:
    order: int
    price: Decimal
    fraction: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentLimits:
    lot_size: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    contract_multiplier: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class ExitLeg:
    """One reduction. ``order`` is the plan target order whose price it uses.

    A runner leg has no price: it exits at the trailing stop. ``source_orders``
    lists the plan targets merged into this leg.
    """

    order: int
    kind: LegKind
    price: Decimal | None
    quantity: Decimal
    source_orders: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExitLadder:
    position_quantity: Decimal
    legs: tuple[ExitLeg, ...]
    merges: tuple[str, ...] = ()

    @property
    def target_legs(self) -> tuple[ExitLeg, ...]:
        return tuple(leg for leg in self.legs if leg.kind == "target")

    @property
    def runner_leg(self) -> ExitLeg | None:
        return next((leg for leg in self.legs if leg.kind == "runner"), None)

    def leg(self, order: int) -> ExitLeg | None:
        return next((leg for leg in self.target_legs if leg.order == order), None)


@dataclass(frozen=True, slots=True)
class LadderRefusal:
    reason: str
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _Draft:
    order: int
    kind: LegKind
    price: Decimal | None
    quantity: Decimal
    sources: list[int]


def build_exit_ladder(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    position_quantity: Decimal,
    targets: Sequence[LadderTarget],
    runner_fraction: Decimal,
    limits: InstrumentLimits,
) -> ExitLadder | LadderRefusal:
    """Size the legs. The result sums to ``position_quantity`` exactly."""

    lot = limits.lot_size
    if (
        lot <= 0
        or position_quantity <= 0
        or entry_price <= 0
        or limits.minimum_quantity <= 0
        or limits.minimum_notional < 0
        or limits.contract_multiplier <= 0
    ):
        return LadderRefusal("invalid_inputs")
    if position_quantity % lot != 0:
        return LadderRefusal(
            "position_not_lot_multiple",
            {"position_quantity": str(position_quantity), "lot_size": str(lot)},
        )
    if not targets or any(target.fraction <= 0 or target.price <= 0 for target in targets):
        return LadderRefusal("invalid_fraction")
    if runner_fraction < 0:
        return LadderRefusal("invalid_fraction")
    if sum((target.fraction for target in targets), runner_fraction) != Decimal("1"):
        return LadderRefusal("fractions_do_not_sum")
    if [target.order for target in targets] != list(range(1, len(targets) + 1)):
        return LadderRefusal("targets_not_ordered")
    long = direction is TradeDirection.LONG
    if direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        return LadderRefusal("invalid_direction")
    previous = entry_price
    for index, target in enumerate(targets):
        favorable = target.price > previous if long else target.price < previous
        if not favorable:
            beyond_entry = target.price > entry_price if long else target.price < entry_price
            reason = "targets_not_ordered" if index and beyond_entry else "target_wrong_side"
            return LadderRefusal(reason, {"order": str(target.order)})
        previous = target.price

    drafts = [
        _Draft(target.order, "target", target.price, Decimal("0"), [target.order])
        for target in targets
    ]
    fractions = [target.fraction for target in targets]
    if runner_fraction > 0:
        drafts.append(_Draft(len(targets) + 1, "runner", None, Decimal("0"), []))
        fractions.append(runner_fraction)
    allocated = Decimal("0")
    for draft, fraction in zip(drafts[:-1], fractions[:-1], strict=True):
        draft.quantity = _floor_to_lot(position_quantity * fraction, lot)
        allocated += draft.quantity
    drafts[-1].quantity = position_quantity - allocated

    def meets_minimum(draft: _Draft) -> bool:
        reference = draft.price if draft.price is not None else entry_price
        notional = draft.quantity * reference * limits.contract_multiplier
        return draft.quantity >= limits.minimum_quantity and notional >= limits.minimum_notional

    merges: list[str] = []
    runner = drafts[-1] if drafts[-1].kind == "runner" else None
    if runner is not None and not meets_minimum(runner):
        drafts.pop()
        last = drafts[-1]
        last.quantity += runner.quantity
        merges.append(f"runner->target:{last.order}")
        runner = None
    target_drafts = [draft for draft in drafts if draft.kind == "target"]
    kept: list[_Draft] = []
    carry: _Draft | None = None
    for index, draft in enumerate(target_drafts):
        if carry is not None:
            draft.quantity += carry.quantity
            draft.sources = carry.sources + draft.sources
            carry = None
        if meets_minimum(draft):
            kept.append(draft)
            continue
        if index + 1 < len(target_drafts):
            merges.append(f"target:{draft.order}->target:{target_drafts[index + 1].order}")
            carry = draft
            continue
        if not kept:
            return LadderRefusal(
                "leg_below_minimum",
                {
                    "position_quantity": str(position_quantity),
                    "minimum_quantity": str(limits.minimum_quantity),
                    "minimum_notional": str(limits.minimum_notional),
                },
            )
        previous_kept = kept[-1]
        merges.append(f"target:{draft.order}->target:{previous_kept.order}")
        previous_kept.quantity += draft.quantity
        previous_kept.sources = previous_kept.sources + draft.sources
    legs = [
        ExitLeg(
            order=draft.order,
            kind="target",
            price=draft.price,
            quantity=draft.quantity,
            source_orders=tuple(sorted(draft.sources)),
        )
        for draft in kept
    ]
    if runner is not None:
        legs.append(
            ExitLeg(
                order=runner.order,
                kind="runner",
                price=None,
                quantity=runner.quantity,
                source_orders=(),
            )
        )
    if sum((leg.quantity for leg in legs), Decimal("0")) != position_quantity:
        return LadderRefusal("legs_do_not_sum")
    return ExitLadder(
        position_quantity=position_quantity,
        legs=tuple(legs),
        merges=tuple(merges),
    )


def _floor_to_lot(value: Decimal, lot: Decimal) -> Decimal:
    return (value / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
