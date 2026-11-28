"""Stop options, from the pattern's extreme ``ext`` (sweep wick) and the entry.

- ``a`` beyond the wick + 0.1 % of price;
- ``b`` beyond the wick + max(0.7 % of price, 1 ATR14 of the execution chart);
- ``c`` beyond the higher-timeframe candle that made the sweep (1h for 15m execution, 4h
  for 1h execution) + 0.1 %. Without look-ahead the low of a still-open 1h candle is just
  the sweep wick, so option c waits for that candle to close, trades only if it closed back
  inside the level (a higher-timeframe SFP candle), and puts the stop beyond its full range.
  The waiting is done by ``engine.ready_index``.
Stops are rounded to the tick, away from the entry.
"""

from __future__ import annotations

import math

BUFFER = 0.001
WIDE_PCT = 0.007


def stop_price(kind: str, sign: int, ext: float, atr: float, tick: float) -> float | None:
    if kind in ("a", "c"):
        raw = ext - sign * BUFFER * ext
    elif kind == "b":
        if not atr == atr:
            return None
        raw = ext - sign * max(WIDE_PCT * ext, atr)
    else:
        raise ValueError(kind)
    q = raw / tick
    return (math.floor(q) if sign > 0 else math.ceil(q)) * tick
