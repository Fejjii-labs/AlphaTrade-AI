"""Sweep and reclaim on the execution chart (15m or 1h).

For every level, the first execution candle that trades beyond it (long: low < level) is
its sweep; the level is then used, whatever happens next. The SFP signal needs a candle
close back inside: the sweep candle itself, or (``reclaim="next"``, the default) the one
after it. Sweep depth and volume are recorded; filters are applied by the engine.
When one candle reclaims several levels at once, the signal keeps the level with the best
confluence, then the highest timeframe, and lists the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from levels import CONFLUENCE_TOL, TF_RANK, Level, confluence


@dataclass
class Signal:
    sign: int
    level: Level
    i: int  # sweep candle index (execution chart)
    r: int  # reclaim candle index
    ext: float  # most extreme price of candles i..r
    depth: float  # sweep depth, fraction of the level
    vol_ratio: float  # max(volume of i, r) / mean volume of the 20 candles before i
    conf: int
    others: list = field(default_factory=list)


def first_sweeps(b: dict, levels: list[Level]) -> dict[int, int]:
    """id(level) -> index of its first sweep candle (levels never swept are absent)."""
    t, h, low = b["t"], b["h"], b["l"]
    out = {}
    for lv in levels:
        a = int(np.searchsorted(t, lv.start, side="left"))
        z = int(np.searchsorted(t, lv.end, side="left"))
        if a >= z:
            continue
        m = (low[a:z] < lv.price) if lv.sign > 0 else (h[a:z] > lv.price)
        k = int(m.argmax())
        if m[k]:
            out[id(lv)] = a + k
    return out


def find_signals(data, exec_tf: str, levels: list[Level], pool: list[Level],
                 reclaim: str = "next") -> list[Signal]:
    b = data.bars[exec_tf]
    o, h, low, c, v, t = b["o"], b["h"], b["l"], b["c"], b["v"], b["t"]
    n = len(t)
    swept_pool = first_sweeps(b, pool)
    swept = first_sweeps(b, levels)
    pool = sorted(pool, key=lambda p: p.price)
    prices = np.array([p.price for p in pool])
    by_key: dict[tuple, Signal] = {}
    for lv in levels:
        i = swept.get(id(lv))
        if i is None or i < 20:
            continue
        s = lv.sign
        if (c[i] - lv.price) * s > 0:
            r = i
        elif reclaim == "next" and i + 1 < n and (c[i + 1] - lv.price) * s > 0:
            r = i + 1
        else:
            continue
        ext = float(low[i:r + 1].min()) if s > 0 else float(h[i:r + 1].max())
        ti = int(t[i])
        lo_, hi_ = np.searchsorted(prices, [lv.price * (1 - CONFLUENCE_TOL),
                                            lv.price * (1 + CONFLUENCE_TOL)])
        near = [p for p in pool[lo_:hi_ + 1] if swept_pool.get(id(p), n) >= i]
        conf = confluence(lv, near, ti)
        vol = float(max(v[i], v[r]) / max(v[i - 20:i].mean(), 1e-12))
        sig = Signal(s, lv, i, r, ext, abs(ext - lv.price) / lv.price, vol, conf)
        key = (s, r)
        cur = by_key.get(key)
        rank = (conf, TF_RANK[lv.tf], lv.kind == "eq")
        if cur is None:
            by_key[key] = sig
        else:
            cur_rank = (cur.conf, TF_RANK[cur.level.tf], cur.level.kind == "eq")
            if rank > cur_rank:
                sig.others = cur.others + [cur.level.tag]
                sig.i, sig.ext = min(sig.i, cur.i), (min if s > 0 else max)(sig.ext, cur.ext)
                by_key[key] = sig
            else:
                cur.others.append(lv.tag)
                cur.i, cur.ext = min(cur.i, sig.i), (min if s > 0 else max)(cur.ext, sig.ext)
    return sorted(by_key.values(), key=lambda x: (x.r, -x.sign))
