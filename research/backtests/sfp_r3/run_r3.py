"""Round 3: round 1's V05 (exit E0) with the anchored-VWAP filters F0-F3, on four periods.

Every V05 candidate signal (round 1's signals, filters, limit entry, wide stop and live
checks; ``sfp_r2/engine_r2.candidates``) is simulated once with round 1's exit E0 and
tagged with the VWAP verdict of every variant (``vwap.decision``). A variant's trades are
its kept candidates after the one-position-per-coin rule (``engine_r2.portfolio``), so a
filtered-out signal never blocks the coin.

Periods (entries in [start, end)):
- blindA  2020-01-01 .. 2023-11-01  never used by any round: the main evidence;
- design  2023-11-01 .. 2025-11-01  round 1's design years (already seen);
- seen    2025-11-01 .. 2026-11-26  round 1's blind year (seen since);
- forward 2026-11-26 .. end of data (count only).
Also writes round 1's V01 BTC design trades (for the 13 example losses of round 1, section 2).

Usage: python -I run_r3.py <data_root> <out_dir> [SYMBOL,...]
Writes <out_dir>/r3_<SYMBOL>.json (and v01_BTCUSDT.json).
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
import engine  # noqa: E402
import numpy as np  # noqa: E402
from classify import classify  # noqa: E402
from engine_r2 import V05, candidates, sim  # noqa: E402
from vwap import decision  # noqa: E402

H = 3_600_000
PERIODS = {"blindA": (D.ms("2020-01-01"), D.ms("2023-11-01")),
           "design": D.DESIGN,
           "seen": D.BLIND,
           "forward": (D.ms("2026-11-26"), D.ms("2026-12-01"))}
V01 = {"exec": "15m", "levels": ["1d", "1w"], "entry": "a", "stop": "b", "conf": 1}
KEEP = ("sym", "sign", "level", "conf", "reclaim_ms", "entry_ms", "entry", "stop", "stop_pct",
        "ladder", "r", "net_r", "fund_r", "gross_r", "exit_ms", "hits", "fills")


def tag(d, row: dict, tf: str) -> dict:
    """Add the VWAP verdict at the close of the signal's reclaim candle."""
    b = d.bars[tf]
    k = int(np.searchsorted(b["t"], row["reclaim_ms"]))
    T = int(b["t"][k]) + D.TF_MIN[tf] * 60_000
    return decision(d.bars["1h"], T, float(b["c"][k]), row["sign"])


def work(args):
    root, symbol = args
    d = D.Data(root, symbol)
    out = {}
    for pname, period in PERIODS.items():
        rows = []
        for c in candidates(d, period, V05):
            t = sim(d, c, "E0")
            if t is None:
                continue
            row = {k: t[k] for k in KEEP}
            row.update(tag(d, row, "1h"))
            row["reason"] = classify(d.M, row) if row["r"] < 0 else None
            rows.append(row)
        out[pname] = rows
    v01 = None
    if symbol == "BTCUSDT":
        v01 = []
        for t in engine.run(d, V01, PERIODS["design"])["trades"]:
            row = {k: t[k] for k in KEEP}
            row.update(tag(d, row, "15m"))
            row["reason"] = classify(d.M, row) if row["r"] < 0 else None
            v01.append(row)
    first = {tf: int(d.bars[tf]["t"][0]) for tf in ("1h",)}
    first["1m"] = int(d.M.t0)
    return symbol, out, v01, first


def main() -> None:
    root, out_dir = sys.argv[1:3]
    coins = sys.argv[3].split(",") if len(sys.argv) > 3 else D.COINS
    os.makedirs(out_dir, exist_ok=True)
    with Pool(min(4, len(coins))) as pool:
        for sym, out, v01, first in pool.imap_unordered(work, [(root, s) for s in coins]):
            json.dump({"first": first, **out}, open(os.path.join(out_dir, f"r3_{sym}.json"), "w"),
                      default=float)
            if v01 is not None:
                json.dump(v01, open(os.path.join(out_dir, "v01_BTCUSDT.json"), "w"),
                          default=float)
            print("done", sym, {p: len(r) for p, r in out.items()}, flush=True)


if __name__ == "__main__":
    main()
