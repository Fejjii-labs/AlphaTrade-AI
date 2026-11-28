"""Round 3 look-ahead check: round 2's truncation test on every F1-F3 trade, plus the VWAP
verdict of every candidate signal.

- Every candidate (kept or filtered out, every period, every coin): the weekly and monthly
  VWAPs, the price and the F0-F3 verdicts are computed again on 1h candles cut at T0 (the
  reclaim candle has just closed; the candle still open is rebuilt from 1-minute bars, as
  in round 2). They must be identical.
- Every trade of F1, F2 or F3: round 2's full re-decision (``sfp_r2/step0.decisions``) at T0
  (signal, extreme, stop) and one minute after the fill (entry, ladder, live check).
Negative control ``leak``: the VWAP also reads the 1h candle after T0; the test must flag it.

Usage: python -I step0_r3.py <data_root> <r3_dir> <out_dir> [SYMBOL,...] [--control leak]
Reads <r3_dir>/r3_<SYMBOL>.json (run_r3.py); writes <out_dir>/step0_r3_<SYMBOL>.json.
"""

from __future__ import annotations

import json
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sfp_r2"))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
import numpy as np  # noqa: E402
import step0  # noqa: E402
import vwap  # noqa: E402
from engine_r2 import V05  # noqa: E402

MIN = 60_000
H = 3_600_000
CONTROL = "--control" in sys.argv


def plant_leak() -> None:
    real = vwap.anchored_vwap

    def leaky(h1, anchor, T):
        return real(h1, anchor, T + H)  # reads the next, not yet closed, 1h candle

    vwap.anchored_vwap = leaky


def same(a: dict, b: dict) -> bool:
    def r(x):
        return None if x is None else round(x, 6)
    return (r(a["vw_w"]) == r(b["vw_w"]) and r(a["vw_m"]) == r(b["vw_m"])
            and a["price"] == b["price"] and a["keep"] == b["keep"])


def check_symbol(args):
    root, r3_dir, out_dir, symbol, control = args
    if control:
        plant_leak()
    full = D.Data(root, symbol)
    res = json.load(open(os.path.join(r3_dir, f"r3_{symbol}.json")))
    b = full.bars["1h"]
    rows = []
    for pname in ("blindA", "design", "seen", "forward"):
        for c in res[pname]:
            r = int(np.searchsorted(b["t"], c["reclaim_ms"]))
            T0 = int(b["t"][r]) + H
            ref = vwap.decision(b, T0, float(b["c"][r]), c["sign"])
            cut = step0.cut_bars(b, "1h", full.M, T0)
            at0 = vwap.decision(cut, T0, float(cut["c"][r]), c["sign"])
            bad = [] if same(ref, at0) else ["vwap"]
            if ref["keep"] != at0["keep"]:
                bad.append("verdict")  # an F0-F3 decision itself changes
            traded = any(c["keep"][f] for f in ("F1", "F2", "F3"))
            if traded and not control:
                T1 = int(c["entry_ms"]) + MIN
                full_ref = step0.decisions(full, V05, c["sign"], r, "all")
                d0 = step0.decisions(step0.CutData(full, T0), V05, c["sign"], r, "stop")
                d1 = step0.decisions(step0.CutData(full, T1), V05, c["sign"], r, "all")
                bad += [k for k in ("signal", "ext", "stop") if d0.get(k) != full_ref.get(k)]
                bad += [f"{k}@fill" for k in ("signal", "stop", "entry", "ladder", "check")
                        if d1.get(k) != full_ref.get(k)]
            rows.append({"period": pname, "entry_ms": c["entry_ms"], "keep": c["keep"],
                         "full_check": traded and not control, "mismatch": bad})
    name = f"step0_r3_{symbol}{'_leak' if control else ''}.json"
    json.dump(rows, open(os.path.join(out_dir, name), "w"))
    return (symbol, len(rows), sum(1 for x in rows if x["full_check"]),
            sum(1 for x in rows if x["mismatch"]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != "leak"]
    root, r3_dir, out_dir = args[:3]
    coins = args[3].split(",") if len(args) > 3 else D.COINS
    os.makedirs(out_dir, exist_ok=True)
    jobs = [(root, r3_dir, out_dir, s, CONTROL) for s in coins]
    with Pool(min(4, len(coins))) as pool:
        for sym, n, nfull, bad in pool.imap_unordered(check_symbol, jobs):
            print(f"{sym}: {n} candidate VWAP verdicts re-decided, {nfull} F1-F3 trades fully "
                  f"re-decided, {bad} with a mismatch{' (leak control)' if CONTROL else ''}",
                  flush=True)


if __name__ == "__main__":
    main()
