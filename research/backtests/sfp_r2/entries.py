"""Entry options. Each returns dict(t, price, maker, ext, k) or None.

``r0`` is the candle after which the setup is complete (the reclaim candle, or for stop
option c the last execution candle of the higher-timeframe candle that made the sweep).
- ``a`` market at the next candle's open after ``r0``;
- ``b`` market at the next open after a confirmation close beyond the reclaim candle's
  extreme, within two candles, with no close back beyond the level first;
- ``c`` limit at the swept level, valid 6 candles from the next open, maker fee. It fills
  only when price trades through the level (long: a 1-minute low strictly below it), at
  the level or that minute's open if better.
``ext`` is the pattern's extreme up to the decision candle; ``k`` the decision candle.
"""

from __future__ import annotations

from bt3y_common import MIN  # noqa: F401  (path set by data.py)

LIMIT_CANDLES = 6


def _ext(b, s, i, k):
    return float(b["l"][i:k + 1].min()) if s > 0 else float(b["h"][i:k + 1].max())


def entry_a(data, tf, sig, r0):
    b = data.bars[tf]
    j = r0 + 1
    if j >= len(b["t"]):
        return None
    return {"t": int(b["t"][j]), "price": float(b["o"][j]), "maker": False,
            "ext": _ext(b, sig.sign, sig.i, r0), "k": r0}


def entry_b(data, tf, sig, r0):
    b = data.bars[tf]
    s, n = sig.sign, len(b["t"])
    trigger = float(b["h"][sig.r:r0 + 1].max()) if s > 0 else float(b["l"][sig.r:r0 + 1].min())
    for k in (r0 + 1, r0 + 2):
        if k + 1 >= n or (b["c"][k] - sig.level.price) * s <= 0:
            return None
        if (b["c"][k] - trigger) * s > 0:
            return {"t": int(b["t"][k + 1]), "price": float(b["o"][k + 1]), "maker": False,
                    "ext": _ext(b, s, sig.i, k), "k": k}
    return None


def entry_c(data, tf, sig, r0):
    b, M = data.bars[tf], data.M
    j = r0 + 1
    if j >= len(b["t"]):
        return None
    start = int(b["t"][j])
    end = start + LIMIT_CANDLES * (int(b["t"][1] - b["t"][0]))
    lvl, s = sig.level.price, sig.sign
    a, z = M.index(start), min(M.n, M.index(end))
    if a >= z:
        return None
    m = (M.l[a:z] < lvl) if s > 0 else (M.h[a:z] > lvl)
    q = int(m.argmax())
    if not m[q]:
        return None
    q += a
    price = min(float(M.o[q]), lvl) if s > 0 else max(float(M.o[q]), lvl)
    return {"t": int(M.t0 + q * 60_000), "price": price, "maker": True,
            "ext": _ext(b, s, sig.i, r0), "k": r0}


ENTRIES = {"a": entry_a, "b": entry_b, "c": entry_c}
