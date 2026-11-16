"""Read partial-exit terms from a stored canonical plan payload.

Fails closed: a malformed field, an unsupported runner rule, a runner that
does not activate after the last target, or a ladder that cannot be sized
returns a refusal reason instead of terms. A single full-size target is not a
ladder and is left to the existing exit path.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.schemas.common import TradeDirection
from app.services.partial_exits.ladder import (
    ExitLadder,
    InstrumentLimits,
    LadderRefusal,
    LadderTarget,
    build_exit_ladder,
)
from app.services.partial_exits.position import PositionExitState, open_position_state
from app.services.partial_exits.runner import RunnerRule, parse_runner_rule


@dataclass(frozen=True, slots=True)
class PartialExitTerms:
    ladder: ExitLadder
    direction: TradeDirection
    entry_price: Decimal
    initial_stop: Decimal
    tick_size: Decimal
    fees: Decimal
    funding: Decimal
    slippage: Decimal
    runner_rule: RunnerRule | None

    @property
    def cost_per_unit(self) -> Decimal:
        total = self.fees + self.funding + self.slippage
        return total / self.ladder.position_quantity

    @property
    def final_target(self) -> Decimal:
        prices = [leg.price for leg in self.ladder.target_legs if leg.price is not None]
        return prices[-1]

    def open_state(self) -> PositionExitState:
        return open_position_state(
            ladder=self.ladder,
            direction=self.direction,
            entry_price=self.entry_price,
            initial_stop=self.initial_stop,
            tick_size=self.tick_size,
            cost_per_unit=self.cost_per_unit,
        )


def is_ladder_payload(risk: dict[str, object]) -> bool:
    targets = risk.get("targets")
    runner = risk.get("runner")
    if not isinstance(targets, list) or not isinstance(runner, dict):
        return False
    return runner.get("enabled") is True or len(targets) > 1


def parse_partial_exit_terms(
    payload: dict[str, object],
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    position_size: Decimal | None,
) -> PartialExitTerms | LadderRefusal:
    risk = payload.get("risk_and_exits")
    rules = payload.get("instrument_rules")
    if not isinstance(risk, dict) or not isinstance(rules, dict) or not is_ladder_payload(risk):
        return LadderRefusal("not_a_ladder")
    if position_size is None or position_size <= 0:
        return LadderRefusal("position_size_unavailable")
    if direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        return LadderRefusal("invalid_direction")
    try:
        targets = tuple(
            LadderTarget(
                order=_int(row["order"]),
                price=_decimal(row["price"]["value"]),
                fraction=_decimal(row["quantity_fraction"]),
            )
            for row in risk["targets"]
        )
        stop = _decimal(risk["stop"]["value"])
        fees = _decimal(risk["fee_allowance"]["value"])
        funding = _decimal(risk["funding_allowance"]["value"])
        slippage = _decimal(risk["slippage_allowance"]["value"])
        limits = InstrumentLimits(
            lot_size=_decimal(rules["lot_size"]),
            minimum_quantity=_decimal(rules["minimum_quantity"]),
            minimum_notional=_decimal(rules["minimum_notional"]),
            contract_multiplier=_decimal(rules["contract_multiplier"]),
        )
        tick = _decimal(rules["tick_size"])
        runner = risk["runner"]
        assert isinstance(runner, dict)
        runner_enabled = runner.get("enabled")
        runner_fraction = _decimal(runner.get("remaining_quantity_fraction", "0"))
    except (KeyError, TypeError, ValueError, AssertionError, InvalidOperation):
        return LadderRefusal("malformed_plan")
    if min(fees, funding, slippage) < 0 or stop <= 0 or tick <= 0:
        return LadderRefusal("malformed_plan")
    if runner_enabled is not True and runner_enabled is not False:
        return LadderRefusal("malformed_plan")
    rule: RunnerRule | None = None
    if runner_enabled:
        if runner.get("activation_target_order") != len(targets):
            return LadderRefusal("runner_activation_unsupported")
        rule = parse_runner_rule(
            rule_id=str(runner.get("rule_id", "")),
            rule_version=str(runner.get("rule_version", "")),
            expression=str(runner.get("expression", "")),
        )
        if rule is None:
            return LadderRefusal("runner_rule_unsupported")
    elif runner_fraction != 0:
        return LadderRefusal("malformed_plan")
    long = direction is TradeDirection.LONG
    if (long and not stop < entry_price) or (not long and not stop > entry_price):
        return LadderRefusal("stop_wrong_side")
    ladder = build_exit_ladder(
        direction=direction,
        entry_price=entry_price,
        position_quantity=position_size,
        targets=targets,
        runner_fraction=runner_fraction,
        limits=limits,
    )
    if isinstance(ladder, LadderRefusal):
        return ladder
    if not ladder.target_legs:
        return LadderRefusal("no_target_legs")
    return PartialExitTerms(
        ladder=ladder,
        direction=direction,
        entry_price=entry_price,
        initial_stop=stop,
        tick_size=tick,
        fees=fees,
        funding=funding,
        slippage=slippage,
        runner_rule=rule if ladder.runner_leg is not None else None,
    )


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("not a decimal")
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("not finite")
    return amount


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("not an int")
    return value
