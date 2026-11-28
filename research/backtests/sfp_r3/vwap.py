"""Round 3: the anchored-VWAP trend filter (pre-registered as F1, F2, F3).

Each VWAP is anchored at the open of the current UTC week (Monday 00:00) or the current UTC
calendar month, and built from 1h candles: sum(typical price * volume) / sum(volume), with
typical price = (high + low + close) / 3. At decision time ``T`` (the close of the SFP's
reclaim candle, when the limit order would be placed) it uses only the 1h candles that
opened at or after the anchor and closed by ``T``. The price compared with it is the close
of the reclaim candle (the last completed price at ``T``).

Rules (fixed before any round-3 result was seen):
- F0: no filter (round 1's V05, the control);
- F1: a long (sweep of a low) only if the price is strictly above BOTH the weekly-open and
      the monthly-open VWAP; a short (sweep of a high) only if strictly below both;
- F2: the weekly-open VWAP only; F3: the monthly-open VWAP only.
If an anchor has no closed 1h candle yet at ``T`` (``T`` is exactly the week or month open),
that VWAP is undefined and the signal is not taken (price is not "above" it).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

H = 3_600_000
DAY = 86_400_000
WEEK = 7 * DAY
VARIANTS = ("F0", "F1", "F2", "F3")
USES = {"F0": (), "F1": ("w", "m"), "F2": ("w",), "F3": ("m",)}


def week_anchor(t_ms: int) -> int:
    """Monday 00:00 UTC of the week containing ``t_ms`` (1970-01-01 was a Thursday)."""
    return (t_ms - 4 * DAY) // WEEK * WEEK + 4 * DAY


def month_anchor(t_ms: int) -> int:
    d = datetime.fromtimestamp(t_ms / 1000, timezone.utc)
    return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def anchored_vwap(h1: dict, anchor: int, T: int) -> float | None:
    """VWAP of the 1h candles with open >= ``anchor`` and close <= ``T``."""
    a = int(np.searchsorted(h1["t"], anchor, side="left"))
    z = int(np.searchsorted(h1["t"], T - H, side="right"))
    if z <= a:
        return None
    v = h1["v"][a:z]
    tp = (h1["h"][a:z] + h1["l"][a:z] + h1["c"][a:z]) / 3.0
    vol = float(v.sum())
    return float((tp * v).sum() / vol) if vol > 0 else None


def decision(h1: dict, T: int, price: float, sign: int) -> dict:
    """Both VWAPs at ``T`` and the verdict of every variant for a signal of side ``sign``."""
    # At exactly a week/month open the new anchor has no closed candle: VWAP undefined.
    vw = {"w": anchored_vwap(h1, week_anchor(T), T), "m": anchored_vwap(h1, month_anchor(T), T)}
    ok = {k: (x is not None and (price - x) * sign > 0) for k, x in vw.items()}
    keep = {f: all(ok[k] for k in USES[f]) for f in VARIANTS}
    return {"vw_w": vw["w"], "vw_m": vw["m"], "price": price, "keep": keep}
