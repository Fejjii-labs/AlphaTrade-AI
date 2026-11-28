"""Higher-timeframe levels: previous 4h / daily / weekly / monthly highs and lows, and equal
highs / lows on those timeframes. Daily, weekly and monthly use UTC candles.

A level is a price that becomes known when its candle closes (``start``) and stays tradable
until ``end`` or its first sweep, whichever comes first (the first sweep is found by
``signals.py``). ``sign`` is the trade direction it offers: a low gives longs (+1), a high
gives shorts (-1).

Lives:
- ``prev``  : the previous candle's high/low, tradable during the next candle only;
- ``naked`` : tradable until first swept, for up to ``NAKED_CANDLES`` candles of its timeframe.
Equal highs/lows (two candle highs within ``EQ_TOL`` of each other, at least one candle
apart, nothing higher in between) are created when the second one closes and live like
``naked`` levels; the level price is the more extreme of the two.
New level kinds (volume-profile POC/VAH/VAL, anchored VWAP...) only need to return more
``Level`` rows from a function like these.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LEVEL_TFS = ("4h", "1d", "1w", "1M")
TF_RANK = {"4h": 1, "1d": 2, "1w": 3, "1M": 4}
EQ_TOL = 0.001  # 0.1 %
EQ_LOOKBACK = 10  # candles of that timeframe
NAKED_CANDLES = 5
CONFLUENCE_TOL = 0.002  # 0.2 %


@dataclass
class Level:
    price: float
    sign: int  # trade direction offered: +1 (a low, long) / -1 (a high, short)
    tf: str
    kind: str  # "hl" (previous high/low) or "eq" (equal highs/lows)
    start: int  # ms when known
    end: int  # ms when it stops being tradable
    rng_hi: float  # range of the candle that made it (TP alternative b)
    rng_lo: float

    @property
    def tag(self) -> str:
        return f"{self.tf} {'equal ' if self.kind == 'eq' else ''}{'low' if self.sign > 0 else 'high'}"


def hl_levels(data, tf: str, life: str) -> list[Level]:
    b, close = data.bars[tf], data.close[tf]
    out = []
    n = len(b["t"])
    for k in range(n):
        start = int(close[k])
        if life == "prev":
            end = int(close[k + 1]) if k + 1 < n else start
        else:
            end = int(close[min(k + NAKED_CANDLES, n - 1)])
        if end <= start:
            continue
        hi, lo = float(b["h"][k]), float(b["l"][k])
        out.append(Level(lo, 1, tf, "hl", start, end, hi, lo))
        out.append(Level(hi, -1, tf, "hl", start, end, hi, lo))
    return out


def eq_levels(data, tf: str) -> list[Level]:
    b, close = data.bars[tf], data.close[tf]
    out = []
    n = len(b["t"])
    for j in range(2, n):
        start = int(close[j])
        end = int(close[min(j + NAKED_CANDLES, n - 1)])
        if end <= start:
            continue
        for sign, x in ((-1, b["h"]), (1, -b["l"])):  # work on highs; lows mirrored
            best = None
            for i in range(max(0, j - EQ_LOOKBACK), j - 1):
                a, c = x[i], x[j]
                if abs(a - c) > EQ_TOL * abs(c):
                    continue
                top = max(a, c)
                if i + 1 < j and x[i + 1:j].max() > top:
                    continue
                best = top if best is None else max(best, top)
            if best is not None:
                price = float(best if sign < 0 else -best)
                out.append(Level(price, sign, tf, "eq", start, end,
                                 float(b["h"][j]), float(b["l"][j])))
    return out


def build_levels(data, tfs, life: str = "prev", equal: bool = False) -> list[Level]:
    levels = []
    for tf in tfs:
        levels += hl_levels(data, tf, life)
        if equal:
            levels += eq_levels(data, tf)
    levels.sort(key=lambda lv: lv.start)
    return levels


def confluence(level: Level, pool: list[Level], t_ms: int) -> int:
    """Distinct level types (timeframe x kind x side) tradable at ``t_ms`` within 0.2 %."""
    tags = {(lv.tf, lv.kind) for lv in pool
            if lv.sign == level.sign and lv.start <= t_ms < lv.end
            and abs(lv.price - level.price) <= CONFLUENCE_TOL * level.price}
    return max(1, len(tags | {(level.tf, level.kind)}))
