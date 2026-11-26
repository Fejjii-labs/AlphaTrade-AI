"""Three-year study, step 2: replay every signal under each exit scenario, one coin per run.

Reads the JSON written by the existing studies (their signals come from the bot's own
detectors) and re-simulates each signal with ``bt3y_common.simulate``:
- ``dline_<SYMBOL>_<chunk>.json``  from dline_1m_study.py (yearly chunks A/B/C, overlapping
  by one month; a signal is kept from the chunk that owns its month);
- ``sn_nested_<SYMBOL>_*.json`` and ``sn_sfp_<SYMBOL>.json`` from sfp_nested_study.py
  (stop and target are rebuilt from the stored stop distance and planned reward:risk).

Scenario key = ``<entry>|<tp1>|<be>|<trail>|<place>``:
- entry  B = market at the next candle's open; R = D-line limit at the line (6 candles);
- tp1    live (the live exits), half (D-line: halfway to the structure), or 0.75 / 1 / 1.5 R;
- be     1 = stop to break-even after the first target, 0 = stop stays;
- trail  runner trails the last 3 / 5 / 8 candles;
- place  where TP2 / TP3 sit: ``struct`` = the strategy's own structure target first
         (D-line structure high, Nested measured move, SFP opposite level), then the next
         higher-timeframe swing; ``htf`` = the nearest higher-timeframe swings first;
         ``first`` = whichever level comes first.
Every scenario must pass the live pre-trade checks with its own ladder, else it is NaN.

Usage: python -I bt3y_simulate.py <data_root> <SYMBOL> [family,...]
<data_root> holds klines/ klines1m/ funding/ out/ (results go to out/sim_<family>_<SYMBOL>.npz)
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from bisect import bisect_left
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from bt3y_common import (  # noqa: E402
    HTF, MIN, TF_MIN, TICK, Minutes, context, ema, first_fill_minute, live_checks,
    load_funding, load_np, pivot_levels, simulate,
)

CHUNK_KEEP = {"A": ("0000", "2024-11"), "B": ("2024-11", "2025-11"), "C": ("2025-11", "9999")}
TP1S = {"dline": ["half", "0.75", "1", "1.5"], "nested": ["0.75", "1", "1.5"],
        "sfp": ["0.75", "1", "1.5"]}
PLACES = {"dline": ["struct", "htf"], "nested": ["struct", "htf", "first"], "sfp": ["struct"]}
ENTRIES = {"dline": ["B", "R"], "nested": ["B"], "sfp": ["B"]}


def scenarios(family: str) -> list[str]:
    keys = [f"{e}|live|1|5|live" for e in ENTRIES[family]]
    keys += [f"{e}|{t}|{be}|{tr}|{p}" for e in ENTRIES[family] for t in TP1S[family]
             for be in (1, 0) for tr in (3, 5, 8) for p in PLACES[family]]
    return keys


def ms_of(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def ladder_for(sign, entry, stop, tp1, primary, extra, tick):
    """TP1 15%; TP2 50% = first ``primary`` level beyond TP1 (else first ``extra``, else
    1R beyond TP1); TP3 20% = the next level beyond TP2 (merged into TP2 when none)."""
    key = lambda p: p * sign  # noqa: E731
    beyond = lambda levels, ref: sorted(  # noqa: E731
        {round(p / tick) * tick for p in levels if (p - ref) * sign > tick}, key=key)
    first = beyond(primary, tp1) or beyond(extra, tp1)
    tp2 = first[0] if first else tp1 + sign * abs(entry - stop)
    rest = beyond([*primary, *extra], tp2)
    if rest:
        return [(tp1, 0.15), (tp2, 0.50), (rest[0], 0.20)]
    return [(tp1, 0.15), (tp2, 0.70)]


def load_rows(out_dir: str, symbol: str, family: str) -> list[dict]:
    rows = []
    if family == "dline":
        for chunk, (lo, hi) in CHUNK_KEEP.items():
            path = os.path.join(out_dir, f"dline_{symbol}_{chunk}.json")
            if os.path.exists(path):
                rows += [r for r in json.load(open(path)) if lo <= r["detected_at"][:7] < hi]
    else:
        for path in glob.glob(os.path.join(out_dir, f"sn_{family}_{symbol}*.json")):
            rows += [r for r in json.load(open(path)) if r.get("trade")]
    rows.sort(key=lambda r: (r["detected_at"], r["timeframe"], r["direction"]))
    return rows


def main() -> None:
    root, symbol = sys.argv[1], sys.argv[2]
    families = sys.argv[3].split(",") if len(sys.argv) > 3 else ["dline", "nested", "sfp"]
    cache = os.path.join(root, "cache")
    M = Minutes(os.path.join(root, "klines1m"), symbol, cache)
    bars = {tf: load_np(os.path.join(root, "klines"), symbol, tf, cache) for tf in TF_MIN if tf != "1m"}
    funding = load_funding(os.path.join(root, "funding"), symbol)
    ema4 = ema(bars["4h"]["c"], 50)
    tick = TICK[symbol]
    for family in families:
        rows = load_rows(os.path.join(root, "out"), symbol, family)
        keys = scenarios(family)
        net = np.full((len(rows), len(keys)), np.nan)
        fund = np.full_like(net, np.nan)
        exit_ms = np.zeros(net.shape, dtype=np.int64)
        entry_ms = np.zeros(net.shape, dtype=np.int64)
        stop_pct = np.full_like(net, np.nan)
        meta, t0 = [], time.time()
        for n, r in enumerate(rows):
            tf = r["timeframe"]
            b = bars[tf]
            sign = 1 if r["direction"] == "long" else -1
            sig_ms = ms_of(r["detected_at"])
            i = bisect_left(b["t"], sig_ms)
            meta.append({"tf": tf, "sign": sign, "sig_ms": sig_ms, "stage": r.get("stage"),
                         **context(bars["1h"], bars["4h"], ema4, sig_ms)})
            if i >= len(b["t"]) or b["t"][i] != sig_ms:
                continue  # no next candle (end of data or a gap)
            market = float(b["o"][i])
            htf = pivot_levels(bars[HTF[tf]], HTF[tf], sig_ms, sign)
            if family == "dline":
                stop = float(r["stop"])
                live = [(float(p), float(f)) for p, f in r["targets"]]
                struct = live[1][0]
                if (struct - market) * sign < 2 * (market - stop) * sign:
                    continue  # live: reward:risk to TP2 below 2 at the quote
            else:
                risk = r["stop_pct"] / 100 * market
                stop = round((market - sign * risk) / tick) * tick
                struct = round((market + sign * r["planned_rr"] * risk) / tick) * tick
                live = [(struct, 1.0)]
            retest = None
            if family == "dline" and r.get("retest_price"):
                retest = first_fill_minute(M, sign, sig_ms, sig_ms + 6 * TF_MIN[tf] * MIN,
                                           float(r["retest_price"]))
            for k, key in enumerate(keys):
                e, tp1, be, trail, place = key.split("|")
                if e == "R":
                    if retest is None:
                        continue
                    t_in, entry = retest
                else:
                    t_in, entry = sig_ms, market
                risk = (entry - stop) * sign
                if risk <= 0:
                    continue
                if tp1 == "live":
                    ladder = live
                else:
                    first = live[0][0] if tp1 == "half" else entry + sign * float(tp1) * risk
                    own = [struct] if family != "dline" else [p for p, _ in live[1:]]
                    primary, extra = {"struct": (own, htf), "htf": (htf, own),
                                      "first": ([*own, *htf], [])}[place]
                    ladder = ladder_for(sign, entry, stop, first, primary, extra, tick)
                if live_checks(sign, entry, stop, ladder) is not None:
                    continue
                single = len(ladder) == 1 and ladder[0][1] == 1.0
                res = simulate(M, b, tf, sign, t_in, entry, stop, ladder,
                               be=be == "1" and not single, trail=None if single else int(trail),
                               maker=e == "R", fill_minute_stop_only=e == "R", funding=funding)
                if res is None:
                    continue
                net[n, k], fund[n, k] = res["net_r"], res["fund_r"]
                exit_ms[n, k], entry_ms[n, k] = res["exit_ms"], t_in
                stop_pct[n, k] = risk / entry * 100
            if n % 2000 == 0:
                print(f"{family} {symbol}: {n}/{len(rows)} {time.time() - t0:.0f}s", flush=True)
        out = os.path.join(root, "out", f"sim_{family}_{symbol}")
        np.savez_compressed(out, net=net, fund=fund, exit_ms=exit_ms, entry_ms=entry_ms,
                            stop_pct=stop_pct)
        json.dump({"keys": keys, "meta": meta}, open(out + ".json", "w"))
        print(f"done {family} {symbol}: {len(rows)} signals x {len(keys)} scenarios, "
              f"{time.time() - t0:.0f}s, missing 1m bars {M.missing}", flush=True)


if __name__ == "__main__":
    main()
