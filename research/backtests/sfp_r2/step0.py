"""Step 0: an independent look-ahead check of round 1's V05 pipeline (truncation test).

For every V05 trade of the full-data run, the data is cut twice and the decision pipeline is
run again on the cut data only:
- at T0, the open of the first candle after the setup is complete (when the limit order would
  be placed): the signal (side, sweep and reclaim candles, level, confluence), the pattern
  extreme and the stop must be the same;
- at T1, one minute after the entry fill: the fill time and price, the take-profit ladder and
  the live-check verdict must be the same.
A cut keeps every candle closed by T, plus the candle still open at T rebuilt from the
1-minute bars before T (what a live bot would see), and 1-minute bars before T only. Any step
that reads a later candle, a later high/low or a later average gives a different answer on
the cut data and is listed as a mismatch.

Usage: python -I step0.py <data_root> <out_dir> [SYMBOL,...]
Writes <out_dir>/step0_<SYMBOL>.json.
"""

from __future__ import annotations

import json
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
import engine  # noqa: E402
import numpy as np  # noqa: E402
from bt3y_common import MIN, atr_series, live_checks  # noqa: E402
from entries import ENTRIES, _ext  # noqa: E402
from exits import ladder, target_levels  # noqa: E402
from levels import LEVEL_TFS, build_levels  # noqa: E402
from signals import find_signals  # noqa: E402
from stops import stop_price  # noqa: E402

V05 = {"exec": "1h", "levels": ["1d", "1w"], "equal": False, "life": "prev",
       "reclaim": "next", "entry": "c", "stop": "b", "exit": "default", "vol": False, "conf": 2}


class CutMinutes:
    """The 1-minute grid of ``M`` with every minute from ``T`` on removed."""

    def __init__(self, M, T: int):
        n = max(0, min(M.n, M.index(T)))
        self.t0, self.n = M.t0, n
        self.o, self.h, self.l, self.c = M.o[:n], M.h[:n], M.l[:n], M.c[:n]
        ok = np.flatnonzero(~np.isnan(self.c))
        self.last_close = float(self.c[ok[-1]]) if len(ok) else float("nan")

    def index(self, ms: int) -> int:
        return int((ms - self.t0) // MIN)


def cut_bars(b: dict, tf: str, M, T: int) -> dict:
    """Candles closed by T, plus the candle open at T rebuilt from the minutes before T."""
    tf_ms = D.TF_MIN[tf] * MIN
    k = int(np.searchsorted(b["t"], T - tf_ms, side="right"))  # closed: t + tf <= T
    out = {key: b[key][:k].copy() for key in b}
    if k < len(b["t"]) and b["t"][k] < T:
        a, z = M.index(int(b["t"][k])), min(M.n, M.index(T))
        h, lo, c, o = M.h[a:z], M.l[a:z], M.c[a:z], M.o[a:z]
        ok = ~np.isnan(c)
        if ok.any():
            part = {"t": b["t"][k], "o": o[ok][0], "h": h[ok].max(), "l": lo[ok].min(),
                    "c": c[ok][-1], "v": np.nan}
            out = {key: np.append(out[key], part[key]) for key in out}
    return out


class CutData:
    """A ``data.Data`` that only knows what was known at time T."""

    def __init__(self, full, T: int):
        self.symbol, self.tick, self.funding = full.symbol, full.tick, full.funding
        self.bars = {tf: cut_bars(full.bars[tf], tf, full.M, T) for tf in D.TF_MIN}
        self.bars["1w"], self.bars["1M"] = D.weekly_monthly(self.bars["1d"])
        self.close = {tf: D.close_times(b, tf) for tf, b in self.bars.items()}
        self.atr = {tf: atr_series(self.bars[tf]) for tf in D.EXEC_TFS}
        self.M = CutMinutes(full.M, T)


def decisions(d, v: dict, sign: int, r: int, upto: str):
    """Re-run signals -> filter -> entry -> stop (-> targets -> ladder -> checks) for the
    signal reclaimed on candle ``r`` (side ``sign``) on the data object ``d``."""
    levels = build_levels(d, v["levels"], v["life"], v["equal"])
    pool = build_levels(d, LEVEL_TFS, v["life"], True)
    sigs = [s for s in find_signals(d, v["exec"], levels, pool, v["reclaim"])
            if s.r == r and s.sign == sign]
    if not sigs:
        return {"signal": None}
    sig = sigs[0]
    tf, b = v["exec"], d.bars[v["exec"]]
    out = {"signal": [sig.sign, sig.i, sig.r, round(sig.level.price, 8), sig.level.tag,
                      sig.conf, sorted(sig.others)],
           "passes_conf": sig.conf >= v["conf"]}
    r0 = engine.ready_index(d, tf, sig, v["stop"])
    ext = _ext(b, sig.sign, sig.i, r0)
    out["ext"] = ext
    out["stop"] = stop_price(v["stop"], sig.sign, ext, float(d.atr[tf][r0]), d.tick)
    if upto == "stop":
        return out
    e = ENTRIES[v["entry"]](d, tf, sig, r0)
    if e is None:
        out["entry"] = None
        return out
    out["entry"] = [e["t"], e["price"]]
    eqs = [lv for lv in levels if lv.kind == "eq"]
    targets = target_levels(d, v["levels"], eqs, sig.sign, e["t"], e["price"])
    lad = ladder(v["exit"], sig.sign, e["price"], out["stop"], sig, targets, d.tick)
    out["ladder"] = [[round(p, 8), f] for p, f in lad]
    out["check"] = live_checks(sig.sign, e["price"], out["stop"], lad)
    return out


def check_symbol(args):
    root, out_dir, symbol = args
    full = D.Data(root, symbol)
    b = full.bars["1h"]
    rows = []
    for pname, period in D.PERIODS.items():
        res = engine.run(full, V05, period)
        for t in res["trades"]:
            r = int(np.searchsorted(b["t"], t["reclaim_ms"]))
            T0 = int(b["t"][r]) + 60 * MIN  # the reclaim candle has just closed
            T1 = int(t["entry_ms"]) + MIN  # the fill minute has just closed
            ref = decisions(full, V05, t["sign"], r, "all")
            at0 = decisions(CutData(full, T0), V05, t["sign"], r, "stop")
            at1 = decisions(CutData(full, T1), V05, t["sign"], r, "all")
            bad = [k for k in ("signal", "ext", "stop") if at0.get(k) != ref.get(k)]
            bad += [f"{k}@fill" for k in ("signal", "stop", "entry", "ladder", "check")
                    if at1.get(k) != ref.get(k)]
            rows.append({"period": pname, "entry_ms": t["entry_ms"], "sign": t["sign"],
                         "level": t["level"], "mismatch": bad,
                         "ref": ref, "at_T0": at0, "at_T1": at1})
    json.dump(rows, open(os.path.join(out_dir, f"step0_{symbol}.json"), "w"), default=float)
    n_bad = sum(1 for x in rows if x["mismatch"])
    return symbol, len(rows), n_bad


def main() -> None:
    root, out_dir = sys.argv[1:3]
    coins = sys.argv[3].split(",") if len(sys.argv) > 3 else D.COINS
    os.makedirs(out_dir, exist_ok=True)
    with Pool(min(4, len(coins))) as pool:
        for sym, n, bad in pool.imap_unordered(check_symbol, [(root, out_dir, s) for s in coins]):
            print(f"{sym}: {n} trades re-decided on cut data, {bad} with a mismatch", flush=True)


if __name__ == "__main__":
    main()
