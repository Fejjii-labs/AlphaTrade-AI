"""Shared pieces of the three-year study: data loaders, live checks, ladder simulator.

Everything here is research code. It places no orders and reads only CSV files.

Simulation model (one trade, driven by 1-minute bars from the entry minute on):
- inside a 1-minute bar the stop is checked first, then the targets in order;
- a stop fills at the stop price, or at the minute's open when price gapped through it;
- targets fill at their price; the first filled target moves the stop to break-even
  (entry plus the round-trip fee per unit, as ``partial_exits.position.breakeven_stop``)
  from the next minute, when ``be`` is on;
- once every target is filled, the rest (the runner) trails the lowest low (long) /
  highest high (short) of the last ``trail`` closed trigger candles, never loosening
  (as ``partial_exits.runner.trail_runner_stop``); it updates at each candle close;
- fees: taker on a market entry (maker on a limit entry) and taker on every exit;
- funding (an estimate): Binance funding rates applied to the open fraction.
R = PnL / (|entry - stop| * size) with the initial stop.
"""

from __future__ import annotations

import csv
import glob
import os
from bisect import bisect_right

import numpy as np

TAKER = 0.0006
MAKER = 0.0002
ZONE = 0.001  # the live 10 bps entry-zone allowance
TICK = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "ZECUSDT": 0.01, "TAOUSDT": 0.01, "HYPEUSDT": 0.001}
COINS = list(TICK)
TF_MIN = {"1m": 1, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
HTF = {"15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}
MIN = 60_000
SPLIT_MS = 1_761_955_200_000  # 2025-11-01 00:00 UTC: in-sample before, blind year from here


def load_np(csv_dir: str, symbol: str, interval: str, cache_dir: str | None = None):
    """Klines as float arrays: dict(t, o, h, l, c, v); t = open time in ms, sorted, unique."""
    cache = os.path.join(cache_dir, f"{symbol}-{interval}.npy") if cache_dir else None
    if cache and os.path.exists(cache):
        a = np.load(cache)
    else:
        rows = {}
        for path in sorted(glob.glob(os.path.join(csv_dir, f"{symbol}-{interval}-*.csv"))):
            with open(path) as handle:
                for r in csv.reader(handle):
                    if not r or not r[0].isdigit():
                        continue
                    ms = int(r[0])
                    if ms > 10**14:  # microseconds in newer archives
                        ms //= 1000
                    rows[ms] = (ms, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        a = np.array([rows[k] for k in sorted(rows)], dtype=np.float64)
        if cache:
            os.makedirs(cache_dir, exist_ok=True)
            np.save(cache, a)
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3],
            "c": a[:, 4], "v": a[:, 5]}


class Minutes:
    """1-minute bars on a gap-free grid (missing minutes are NaN and touch nothing)."""

    def __init__(self, csv_dir: str, symbol: str, cache_dir: str | None = None):
        m = load_np(csv_dir, symbol, "1m", cache_dir)
        self.t0 = int(m["t"][0])
        n = int((m["t"][-1] - self.t0) // MIN) + 1
        idx = (m["t"] - self.t0) // MIN
        self.o, self.h, self.l, self.c = (np.full(n, np.nan) for _ in range(4))
        self.o[idx], self.h[idx], self.l[idx], self.c[idx] = m["o"], m["h"], m["l"], m["c"]
        self.n, self.missing = n, n - len(idx)
        self.last_close = float(m["c"][-1])

    def index(self, ms: int) -> int:
        return int((ms - self.t0) // MIN)


def load_funding(csv_dir: str, symbol: str):
    rows = {}
    for path in sorted(glob.glob(os.path.join(csv_dir, f"{symbol}-fundingRate-*.csv"))):
        with open(path) as handle:
            for r in csv.reader(handle):
                if r and r[0].isdigit():
                    rows[int(r[0]) // 1000 * 1000] = float(r[2])
    keys = sorted(rows)
    return keys, [rows[k] for k in keys]


def ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(values)
    k, acc = 2.0 / (period + 1), values[0]
    for i, v in enumerate(values):
        acc = acc + k * (v - acc)
        out[i] = acc
    return out


def atr_series(b: dict, period: int = 14) -> np.ndarray:
    """Wilder ATR known at the close of each bar (NaN until the period is full)."""
    prev = np.concatenate(([b["c"][0]], b["c"][:-1]))
    tr = np.maximum(b["h"] - b["l"], np.maximum(abs(b["h"] - prev), abs(b["l"] - prev)))
    tr[0] = b["h"][0] - b["l"][0]
    out = np.full(len(tr), np.nan)
    if len(tr) >= period:
        acc = tr[:period].mean()
        out[period - 1] = acc
        for i in range(period, len(tr)):
            acc = (acc * (period - 1) + tr[i]) / period
            out[i] = acc
    return out


def pivot_levels(b: dict, tf: str, ms: int, sign: int, width: int = 2, lookback: int = 120):
    """Confirmed swing highs (long) / lows (short) of the bars closed by ``ms``, newest last."""
    end = bisect_right(b["t"], ms - TF_MIN[tf] * MIN)  # bars with close time <= ms
    lo = max(0, end - lookback)
    x = b["h"][lo:end] if sign > 0 else -b["l"][lo:end]
    return [float(x[i] * sign) for i in range(width, len(x) - width)
            if x[i] > max(x[i - width:i].max(), x[i + 1:i + width + 1].max())]


def context(h1: dict, h4: dict, ema4: np.ndarray, ms: int) -> dict:
    """Market context at ``ms`` from closed candles: 30-day return and the 4h EMA-50 trend."""
    i = bisect_right(h1["t"], ms - 60 * MIN) - 1
    j = i - 24 * 30
    k = bisect_right(h4["t"], ms - 240 * MIN) - 1
    return {
        "ret30": float(h1["c"][i] / h1["c"][j] - 1) if j >= 0 else None,
        "trend": (1 if h4["c"][k] > ema4[k] else -1) if k >= 50 else 0,
    }


def regime(ret30) -> str:
    """Rising / falling when the coin moved more than 10% over the last 30 days."""
    if ret30 is None:
        return "n/a"
    return "rising" if ret30 > 0.10 else "falling" if ret30 < -0.10 else "sideways"


def live_checks(sign: int, entry: float, stop: float, ladder) -> str | None:
    """The live demo pre-trade checks. ``ladder`` = [(price, fraction), ...]; rest = runner.

    - stop and every target on the right side of the whole 10 bps zone;
    - planned reward >= risk: size-weighted target distance from the worst zone edge,
      an unpriced runner counting zero (``planned_reward_risk``);
    - fee rule (risk v2): (zone_upper + stop) * 0.06% <= nearest-edge stop distance / 5.
    """
    upper, lower = entry * (1 + ZONE), entry * (1 - ZONE)
    adverse, near = (upper, lower) if sign > 0 else (lower, upper)
    if (near - stop) * sign <= 0:
        return "geometry"
    reward = 0.0
    for price, fraction in ladder:
        if (price - adverse) * sign <= 0:
            return "geometry"
        reward += (price - adverse) * sign * fraction
    if reward < (adverse - stop) * sign:
        return "reward_below_risk"
    if (upper + stop) * TAKER > abs(near - stop) / 5:
        return "fees"
    return None


def simulate(M: Minutes, bars: dict, tf: str, sign: int, entry_ms: int, entry: float,
             stop: float, ladder, *, be: bool, trail: int | None, maker: bool = False,
             fill_minute_stop_only: bool = False, funding=None, slip_bps: float = 0.0):
    """One trade on 1-minute bars. Returns dict(net_r, gross_r, fund_r, exit_ms, hits)."""
    risk = (entry - stop) * sign
    if risk <= 0:
        return None
    slip = slip_bps / 10_000
    entry_fill = entry * (1 + sign * slip) if not maker else entry
    targets = list(ladder)
    remaining, pnl = 1.0, 0.0
    fees = entry_fill * (MAKER if maker else TAKER)
    fills = []  # (ms, fraction closed)
    cur = M.index(entry_ms)
    S, pending_be, hits = stop, False, 0
    tf_ms = TF_MIN[tf] * MIN
    lo_arr, hi_arr, o_arr = M.l, M.h, M.o

    def close(fraction, price, j, is_stop):
        nonlocal remaining, pnl, fees
        if is_stop:
            price *= 1 - sign * slip
        pnl += fraction * (price - entry_fill) * sign
        fees += fraction * price * TAKER
        remaining -= fraction
        fills.append((M.t0 + (j + 1) * MIN, fraction))

    def touch(a, b, with_target):
        """First minute in [a, b) touching the stop or (optionally) the next target."""
        step = 240
        while a < b:
            e = min(b, a + step)
            m = (lo_arr[a:e] <= S) if sign > 0 else (hi_arr[a:e] >= S)
            if with_target:
                T = targets[0][0]
                m = m | ((hi_arr[a:e] >= T) if sign > 0 else (lo_arr[a:e] <= T))
            k = int(m.argmax())
            if m[k]:
                return a + k
            a, step = e, min(step * 2, 40_000)
        return None

    def stopped(j):
        return (lo_arr[j] <= S) if sign > 0 else (hi_arr[j] >= S)

    def stop_price(j):
        return min(o_arr[j], S) if sign > 0 else max(o_arr[j], S)

    first = cur
    while remaining > 1e-9 and targets:
        j = touch(cur, M.n, True)
        if j is None:
            break
        if stopped(j):
            close(remaining, stop_price(j), j, True)
            break
        if not (fill_minute_stop_only and j == first):
            fav = hi_arr[j] if sign > 0 else lo_arr[j]
            while targets and (fav - targets[0][0]) * sign >= 0:
                price, fraction = targets.pop(0)
                close(min(fraction, remaining), price, j, False)
                hits += 1
                if be:
                    level = entry_fill * (1 + sign * 2 * TAKER)
                    if (level - S) * sign > 0:
                        S = level
        cur = j + 1
    if remaining > 1e-9 and not targets:
        if trail is None:
            close(remaining, M.c[cur - 1], cur - 1, False)  # no runner rule: flat at once
        else:
            bt, bh, bl = bars["t"], bars["h"], bars["l"]
            k = bisect_right(bt, M.t0 + cur * MIN) - 1  # candle containing minute ``cur``
            while remaining > 1e-9:
                if k >= trail:  # trail from the last ``trail`` closed candles
                    cand = bl[k - trail:k].min() if sign > 0 else bh[k - trail:k].max()
                    if (cand - S) * sign > 0:
                        S = float(cand)
                end = min(M.n, M.index(int(bt[k]) + tf_ms)) if k < len(bt) else M.n
                j = touch(cur, end, False)
                if j is not None:
                    close(remaining, stop_price(j), j, True)
                    break
                cur, k = end, k + 1
                if cur >= M.n:
                    break
    if remaining > 1e-9:  # still open at the end of the data: mark at the last close
        close(remaining, M.last_close, M.n - 1, False)
    exit_ms = fills[-1][0]
    fund = 0.0
    if funding is not None:
        keys, rates = funding
        open_fraction, fi = 1.0, 0
        for q in range(bisect_right(keys, entry_ms), bisect_right(keys, exit_ms)):
            while fi < len(fills) and fills[fi][0] <= keys[q]:
                open_fraction -= fills[fi][1]
                fi += 1
            fund -= sign * rates[q] * entry * max(open_fraction, 0.0)
    return {"net_r": (pnl - fees) / risk, "gross_r": pnl / risk, "fund_r": fund / risk,
            "exit_ms": int(exit_ms), "hits": hits}


def first_fill_minute(M: Minutes, sign: int, start_ms: int, end_ms: int, limit: float):
    """First minute in [start, end) that trades through a resting limit, with its fill price
    (never better than that minute's open). None when it never trades there."""
    a, b = M.index(start_ms), min(M.n, M.index(end_ms))
    if a >= b:
        return None
    m = (M.l[a:b] <= limit) if sign > 0 else (M.h[a:b] >= limit)
    k = int(m.argmax())
    if not m[k]:
        return None
    j = a + k
    price = min(M.o[j], limit) if sign > 0 else max(M.o[j], limit)
    return M.t0 + j * MIN, float(price)

