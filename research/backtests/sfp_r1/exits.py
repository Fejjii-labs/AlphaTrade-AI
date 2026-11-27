"""Exit ladders (no break-even move in this round).

Default: TP1 15 % at 1R; TP2 50 % at the next level in the trade direction beyond TP1;
TP3 20 % at the one after (merged into TP2 when there is none); the last 15 % is a runner
trailing the last 5 closed execution candles. With no level beyond TP1, TP2 sits 1R beyond
TP1 (``bt3y_simulate.ladder_for``).
Alternative ``range``: TP2 at the opposite side of the swept level's candle range (a
previous-day-low long aims for that day's high), then the next level.

Target levels ("the next level") are the highs and lows of the last 5 completed candles
of each timeframe in the variant's level set, plus its equal highs/lows, that price has
not traded through since their candle closed.
"""

from __future__ import annotations

import numpy as np

from bt3y_simulate import ladder_for
from levels import NAKED_CANDLES

TP1_R = 1.0
TRAIL = 5


def target_levels(data, tfs, eq_levels, sign: int, t_ms: int, entry: float) -> list[float]:
    """Untaken HTF prices beyond ``entry`` in the trade direction at ``t_ms``, judged on the
    15m candles closed by ``t_ms`` (no look-ahead)."""
    h1 = data.bars["15m"]
    end = int(np.searchsorted(h1["t"], t_ms - 15 * 60_000, side="right"))
    out = []
    for tf in tfs:
        b, close = data.bars[tf], data.close[tf]
        k = int(np.searchsorted(close, t_ms, side="right"))  # candles closed by t_ms
        for q in range(max(0, k - NAKED_CANDLES), k):
            a = int(np.searchsorted(h1["t"], close[q], side="left"))
            for p in (float(b["h"][q]), float(b["l"][q])):
                if (p - entry) * sign <= 0:
                    continue
                seg = h1["h"][a:end] if sign > 0 else h1["l"][a:end]
                if len(seg) and (seg.max() >= p if sign > 0 else seg.min() <= p):
                    continue
                out.append(p)
    for lv in eq_levels:
        if lv.sign == -sign and lv.start <= t_ms < lv.end and (lv.price - entry) * sign > 0:
            a = int(np.searchsorted(h1["t"], lv.start, side="left"))
            seg = h1["h"][a:end] if sign > 0 else h1["l"][a:end]
            if not len(seg) or (seg.max() < lv.price if sign > 0 else seg.min() > lv.price):
                out.append(lv.price)
    return out


def ladder(kind: str, sign: int, entry: float, stop: float, sig, targets, tick: float):
    risk = abs(entry - stop)
    tp1 = round((entry + sign * TP1_R * risk) / tick) * tick
    if kind == "default":
        return ladder_for(sign, entry, stop, tp1, targets, [], tick)
    if kind == "range":
        opp = sig.level.rng_hi if sign > 0 else sig.level.rng_lo
        return ladder_for(sign, entry, stop, tp1, [opp], targets, tick)
    raise ValueError(kind)
