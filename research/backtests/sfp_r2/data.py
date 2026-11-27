"""Data for the SFP round-1 study: candles, 1-minute grid, funding, periods, Tunis time.

Reads the CSV folders written by ``../download_klines.py`` (untrusted input: keep the data
folder outside the code tree). Reuses the loaders of ``../bt3y_common.py``.

<data_root> layout: klines/ (15m, 1h, 4h, 1d), klines1m/ (1m), funding/, cache/ (npy).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from bt3y_common import (  # noqa: E402
    MIN, TICK, Minutes, atr_series, load_funding, load_np,
)

TF_MIN = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
EXEC_TFS = ("15m", "1h")
HTF_OF = {"15m": "1h", "1h": "4h"}  # the candle that "made the sweep" (stop option c)
COINS = ["BTCUSDT", "ETHUSDT", "ZECUSDT", "TAOUSDT", "HYPEUSDT"]
TUNIS = timezone(timedelta(hours=1))


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


DESIGN = (ms("2023-11-01"), ms("2025-11-01"))  # entries in [start, end)
BLIND = (ms("2025-11-01"), ms("2026-11-26"))
PERIODS = {"design": DESIGN, "blind": BLIND}


def tunis(t_ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(t_ms / 1000, TUNIS).strftime(fmt)


def month_key(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).strftime("%Y-%m")


def _group(d: dict, keys: np.ndarray) -> dict:
    """OHLCV candles grouped by ``keys`` (start ms of the group, non-decreasing)."""
    edges = np.flatnonzero(np.diff(keys)) + 1
    first = np.concatenate(([0], edges))
    last = np.concatenate((edges - 1, [len(keys) - 1]))
    return {"t": keys[first], "o": d["o"][first], "h": np.maximum.reduceat(d["h"], first),
            "l": np.minimum.reduceat(d["l"], first), "c": d["c"][last],
            "v": np.add.reduceat(d["v"], first)}


def weekly_monthly(d1: dict) -> tuple[dict, dict]:
    """UTC weeks (Monday start) and calendar months built from daily candles."""
    day = 86_400_000
    week = 7 * day
    wk = (d1["t"] - 4 * day) // week * week + 4 * day  # 1970-01-01 was a Thursday
    months = np.array([ms(datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-01"))
                       for t in d1["t"]], dtype=np.int64)
    return _group(d1, wk), _group(d1, months)


def close_times(b: dict, tf: str) -> np.ndarray:
    """Close time (ms) of each candle; months and weeks use the next candle's start."""
    if tf in TF_MIN:
        return b["t"] + TF_MIN[tf] * MIN
    if tf == "1w":
        return b["t"] + 7 * 86_400_000
    nxt = []
    for t in b["t"]:
        d = datetime.fromtimestamp(t / 1000, timezone.utc)
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        nxt.append(ms(f"{y:04d}-{m:02d}-01"))
    return np.array(nxt, dtype=np.int64)


class Data:
    """Everything one coin needs: candles by timeframe, 1-minute grid, funding, ATR."""

    def __init__(self, root: str, symbol: str, minutes: bool = True):
        cache = os.path.join(root, "cache")
        self.symbol, self.tick = symbol, TICK[symbol]
        self.bars = {tf: load_np(os.path.join(root, "klines"), symbol, tf, cache)
                     for tf in TF_MIN}
        self.bars["1w"], self.bars["1M"] = weekly_monthly(self.bars["1d"])
        self.close = {tf: close_times(b, tf) for tf, b in self.bars.items()}
        self.atr = {tf: atr_series(self.bars[tf]) for tf in EXEC_TFS}
        self.M = Minutes(os.path.join(root, "klines1m"), symbol, cache) if minutes else None
        self.funding = load_funding(os.path.join(root, "funding"), symbol)
