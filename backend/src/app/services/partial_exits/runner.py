"""Trailing runner rule. Only closed candles are read; the stop never widens.

One rule is supported: ``closed-candle-trail`` version ``1`` with the exact
expression ``lookback_bars=<n>``. A long runner trails the lowest low of the
last ``n`` closed bars, a short runner the highest high. Any other rule id,
version or expression is refused, so an unknown runner is never guessed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.schemas.common import TradeDirection
from app.services.partial_exits.position import tighter_stop

RUNNER_RULE_ID = "closed-candle-trail"
RUNNER_RULE_VERSION = "1"
_EXPRESSION = re.compile(r"lookback_bars=([1-9][0-9]{0,2})")


class ClosedBar(Protocol):
    @property
    def interval_start(self) -> datetime: ...

    @property
    def interval_end(self) -> datetime: ...

    @property
    def high(self) -> Decimal: ...

    @property
    def low(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class RunnerRule:
    lookback_bars: int


def parse_runner_rule(*, rule_id: str, rule_version: str, expression: str) -> RunnerRule | None:
    if rule_id != RUNNER_RULE_ID or rule_version != RUNNER_RULE_VERSION:
        return None
    match = _EXPRESSION.fullmatch(expression)
    if match is None:
        return None
    return RunnerRule(lookback_bars=int(match.group(1)))


def trail_runner_stop(
    *,
    rule: RunnerRule,
    direction: TradeDirection,
    current_stop: Decimal,
    bars: Sequence[ClosedBar],
    evaluated_at: datetime,
) -> Decimal:
    """The trailed stop after the newest closed bar, never looser than now."""

    closed = sorted(
        (bar for bar in bars if bar.interval_end <= evaluated_at),
        key=lambda bar: bar.interval_start,
    )
    if len(closed) < rule.lookback_bars:
        return current_stop
    window = closed[-rule.lookback_bars :]
    if direction is TradeDirection.LONG:
        candidate = min(bar.low for bar in window)
    else:
        candidate = max(bar.high for bar in window)
    return tighter_stop(direction, current_stop, candidate)
