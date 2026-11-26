"""Three-year study: SFP at significant levels (strategy_spec_v1 target design), one coin per run.

Not a bot detector: this is the rule Sofien described, on plain candles.
Levels: the previous completed 4h / 8h / daily / weekly candle's high and low (UTC; weeks
start on Monday), active during the next candle of that size. First sweep of a level only.
Long (shorts mirror): a trigger candle (15m / 30m / 1h / 4h) trades below the level, and
- reclaim ``same``: closes back above it; ``next``: the following candle closes back above;
- confirm 1: within two more candles a candle closes above the reclaim candle's high
  (no close back below the level first); confirm 0: no confirmation candle.
Entry M = market at the next candle's open; R = limit at the level for 6 candles (retest).
Stop beyond the lowest wick of the pattern: 1 tick, or 0.25 / 0.5 ATR(14) of the trigger.
Exits: TP1 15% at 0.75 / 1 / 1.5 R then break-even (on/off); TP2 50% at the previous
candle's opposite level; TP3 20% at the opposite extreme of the last three; 15% runner
trailing 3 / 5 / 8 trigger candles. Every variant must pass the live pre-trade checks.

Usage: python -I bt3y_levels.py <data_root> <SYMBOL>     (writes out/lvl_<SYMBOL>.npz/.json)
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from bt3y_common import (  # noqa: E402
    MIN, TF_MIN, TICK, Minutes, atr_series, context, ema, first_fill_minute, live_checks,
    load_funding, load_np, simulate,
)
from bt3y_simulate import ladder_for  # noqa: E402

HOUR = 60 * MIN
LEVELS = {"4h": 4, "8h": 8, "1d": 24, "1w": 168}
TRIGGERS = ["15m", "30m", "1h", "4h"]
STOPS = ["tick", "atr25", "atr50"]
ENTRIES = ["M", "R"]
EXITS = [f"{t}|{be}|{tr}" for t in ("0.75", "1", "1.5") for be in (1, 0) for tr in (3, 5, 8)]


def blocks(h1: dict, hours: int):
    """(start_ms, high, low) of each UTC block of ``hours`` built from 1h candles."""
    size = hours * HOUR
    offset = 4 * 24 * HOUR if hours == 168 else 0  # 1970-01-01 was a Thursday
    start = (h1["t"] - offset) // size * size + offset
    edges = np.flatnonzero(np.diff(start)) + 1
    first = np.concatenate(([0], edges))
    return start[first], np.maximum.reduceat(h1["h"], first), np.minimum.reduceat(h1["l"], first)


def patterns(b: dict, blk, size_ms: int):
    """Yield (sign, level, extreme, signal_bar, reclaim, confirm, opposite, opposite3)."""
    bs, bh, bl = blk
    o, h, low, c, t = b["o"], b["h"], b["l"], b["c"], b["t"]
    used = set()
    n = len(t)
    for i in range(n - 1):
        bi = int(np.searchsorted(bs, t[i], side="right")) - 1
        if bi < 3 or bs[bi] - bs[bi - 3] != 3 * size_ms:
            continue
        for sign in (1, -1):
            if (bi, sign) in used:
                continue
            level = bl[bi - 1] if sign > 0 else bh[bi - 1]
            adverse = low if sign > 0 else h
            if not (adverse[i] - level) * sign < 0:
                continue
            used.add((bi, sign))
            if (c[i] - level) * sign > 0:
                r, reclaim = i, "same"
            elif i + 2 < n and (c[i + 1] - level) * sign > 0:
                r, reclaim = i + 1, "next"
            else:
                continue
            ext = min(adverse[i:r + 1] * sign) * sign
            opp = bh[bi - 1] if sign > 0 else bl[bi - 1]
            opp3 = bh[bi - 3:bi].max() if sign > 0 else bl[bi - 3:bi].min()
            yield sign, level, ext, r, reclaim, 0, opp, opp3
            trigger = h[r] if sign > 0 else low[r]
            for k in (r + 1, r + 2):
                if k >= n - 1 or (c[k] - level) * sign <= 0:
                    break
                if (c[k] - trigger) * sign > 0:
                    ext2 = min(adverse[i:k + 1] * sign) * sign
                    yield sign, level, ext2, k, reclaim, 1, opp, opp3
                    break


def main() -> None:
    root, symbol = sys.argv[1], sys.argv[2]
    cache = os.path.join(root, "cache")
    M = Minutes(os.path.join(root, "klines1m"), symbol, cache)
    bars = {tf: load_np(os.path.join(root, "klines"), symbol, tf, cache) for tf in TRIGGERS}
    funding = load_funding(os.path.join(root, "funding"), symbol)
    ema4 = ema(bars["4h"]["c"], 50)
    tick = TICK[symbol]
    meta, net, fund, exit_ms, entry_ms, stop_pct = [], [], [], [], [], []
    t0 = time.time()
    for trig in TRIGGERS:
        b = bars[trig]
        atr = atr_series(b)
        for lvl, hours in LEVELS.items():
            if hours * 60 <= TF_MIN[trig]:
                continue
            count = 0
            for sign, level, ext, j, reclaim, confirm, opp, opp3 in patterns(
                    b, blocks(bars["1h"], hours), hours * HOUR):
                sig_ms = int(b["t"][j + 1])
                market = float(b["o"][j + 1])
                retest = first_fill_minute(M, sign, sig_ms, sig_ms + 6 * TF_MIN[trig] * MIN, level)
                r_net = np.full((len(STOPS), len(ENTRIES), len(EXITS)), np.nan, dtype=np.float32)
                r_fund = np.zeros_like(r_net)
                r_exit = np.zeros(r_net.shape, dtype=np.int64)
                r_pct = np.full((len(STOPS), len(ENTRIES)), np.nan, dtype=np.float32)
                for si, sv in enumerate(STOPS):
                    buffer = tick if sv == "tick" else float(atr[j]) * int(sv[3:]) / 100
                    if not buffer == buffer:
                        continue
                    stop = round((ext - sign * buffer) / tick) * tick
                    for ei, (t_in, entry) in enumerate(((sig_ms, market), retest or (0, 0.0))):
                        risk = (entry - stop) * sign
                        if not t_in or risk <= 0:
                            continue
                        r_pct[si, ei] = risk / entry * 100
                        for xi, key in enumerate(EXITS):
                            tp1, be, trail = key.split("|")
                            ladder = ladder_for(sign, entry, stop, entry + sign * float(tp1) * risk,
                                                [opp], [opp3], tick)
                            if live_checks(sign, entry, stop, ladder) is not None:
                                continue
                            res = simulate(M, b, trig, sign, t_in, entry, stop, ladder,
                                           be=be == "1", trail=int(trail), maker=ei == 1,
                                           fill_minute_stop_only=ei == 1, funding=funding)
                            r_net[si, ei, xi], r_fund[si, ei, xi] = res["net_r"], res["fund_r"]
                            r_exit[si, ei, xi] = res["exit_ms"]
                if np.isnan(r_net).all():
                    continue
                count += 1
                meta.append({"trig": trig, "lvl": lvl, "sign": sign, "reclaim": reclaim,
                             "confirm": confirm, "sig_ms": sig_ms,
                             "retest_ms": retest[0] if retest else 0,
                             **context(bars["1h"], bars["4h"], ema4, sig_ms)})
                net.append(r_net)
                fund.append(r_fund)
                exit_ms.append(r_exit)
                stop_pct.append(r_pct)
            print(f"{symbol} {trig} {lvl}: {count} patterns, {time.time() - t0:.0f}s", flush=True)
    out = os.path.join(root, "out", f"lvl_{symbol}")
    np.savez_compressed(out, net=np.array(net), fund=np.array(fund), exit_ms=np.array(exit_ms),
                        stop_pct=np.array(stop_pct))
    json.dump({"stops": STOPS, "entries": ENTRIES, "exits": EXITS, "meta": meta},
              open(out + ".json", "w"))
    print(f"done {symbol}: {len(meta)} patterns, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
