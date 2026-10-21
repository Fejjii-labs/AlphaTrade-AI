"""Refuse to arm paper automation when the loss cooldown or the cycle is unsafe.

The paper exit monitor and both autonomous loops stay off unless the
consecutive-loss cooldown is a finite number greater than zero and the
worker's cycle interval is not longer than the smallest configured strategy
timeframe. A failed check returns a reason code. It does not place an order,
and it does not turn a flag on.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

from app.market_contracts.identity import INTERVAL_SECONDS
from app.schemas.common import Timeframe

COOLDOWN_INVALID = "consecutive_loss_cooldown_invalid"
CYCLE_INTERVAL_EXCEEDS_TIMEFRAME = "cycle_interval_exceeds_timeframe"


def paper_arming_refusal(settings: object) -> str | None:
    """Reason the exit monitor and the autonomous loops must stay disarmed.

    Missing, non-finite, or non-positive values fail closed. Equality between
    the cycle interval and the smallest timeframe is still allowed: a loss on
    the bar that just closed is not yet older than one candle.
    """

    if settings is None:
        return COOLDOWN_INVALID
    cooldown = consecutive_loss_cooldown_refusal(
        getattr(settings, "paper_signal_cooldown_after_loss_seconds", None)
    )
    if cooldown is not None:
        return cooldown
    return cycle_interval_refusal(
        getattr(settings, "watcher_paper_poll_interval_seconds", None),
        getattr(settings, "paper_signal_allowed_timeframes", None),
    )


def consecutive_loss_cooldown_refusal(value: object) -> str | None:
    """Refuse unless the cooldown is a finite number of seconds greater than zero."""

    if _positive_finite(value) is None:
        return COOLDOWN_INVALID
    return None


def cycle_interval_refusal(interval: object, timeframes: object) -> str | None:
    """Refuse when the cycle is longer than the smallest configured timeframe.

    An unreadable interval or an unreadable timeframe list fails closed. One
    bad token makes the whole list unreadable, so a typo cannot arm the loop.
    """

    span = _positive_finite(interval)
    smallest = smallest_timeframe_seconds(timeframes)
    if span is None or smallest is None or span > float(smallest):
        return CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    return None


def smallest_timeframe_seconds(timeframes: object) -> int | None:
    """Smallest candle length in the configured strategy timeframe list."""

    if isinstance(timeframes, (str, bytes)) or not isinstance(timeframes, Sequence):
        return None
    if len(timeframes) == 0:
        return None
    seconds: list[int] = []
    for token in timeframes:
        parsed = _timeframe_seconds(token)
        if parsed is None:
            return None
        seconds.append(parsed)
    return min(seconds)


def _timeframe_seconds(token: object) -> int | None:
    if not isinstance(token, str):
        return None
    label = token.strip()
    if not label:
        return None
    try:
        return INTERVAL_SECONDS[Timeframe(label)]
    except (ValueError, KeyError):
        return None


def _positive_finite(value: object) -> float | None:
    """A finite number greater than zero, or None.

    ``bool`` is rejected. It is an ``int`` subclass and is not a duration.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        if not value.is_finite() or value <= 0:
            return None
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        return number
    return None
