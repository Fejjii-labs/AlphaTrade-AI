"""Step 2: explore on the design years only (entries 1 Nov 2023 - 31 Oct 2025).

Stage A: execution chart x level set x entry x stop (previous-candle levels, reclaim on the
sweep candle or the next, default exits, no filters).
Stage B: one change at a time on top of every stage-A setting: level life ``naked``,
equal highs/lows, same-candle reclaim, TP alternative ``range``, volume filter,
confluence >= 2.
Never reads the blind year (``engine.run`` drops signals outside the period).

Usage: python -I explore.py <data_root> <out_dir> [SYMBOL ...]   (one process per coin)
Writes <out_dir>/explore_<SYMBOL>.json: [{"v": variant, "s": summary}, ...]
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data as D  # noqa: E402
import engine  # noqa: E402
from stats import summary  # noqa: E402

LEVEL_SETS = [["4h"], ["1d"], ["1w"], ["1M"], ["1d", "1w"], ["1d", "1w", "1M"],
              ["4h", "1d", "1w", "1M"]]
CHANGES = [{"life": "naked"}, {"equal": True}, {"reclaim": "same"}, {"exit": "range"},
           {"vol": True}, {"conf": 2}]


def grid():
    base = []
    for ex, lv, en, st in itertools.product(D.EXEC_TFS, LEVEL_SETS, "abc", "abc"):
        base.append({"exec": ex, "levels": lv, "entry": en, "stop": st})
    out = list(base)
    for b in base:
        out += [{**b, **c} for c in CHANGES]
    return out


def work(args):
    root, out_dir, symbol = args
    d = D.Data(root, symbol)
    rows = []
    for v in grid():
        res = engine.run(d, v, D.DESIGN)
        s = summary(res["trades"], D.DESIGN)
        rows.append({"v": {**engine.DEFAULT, **v}, "s": s, "c": res["counts"]})
    json.dump(rows, open(os.path.join(out_dir, f"explore_{symbol}.json"), "w"))
    return symbol, len(rows)


def main() -> None:
    root, out_dir = sys.argv[1:3]
    symbols = sys.argv[3:] or D.COINS
    with Pool(min(4, len(symbols))) as pool:
        for sym, n in pool.imap_unordered(work, [(root, out_dir, s) for s in symbols]):
            print("done", sym, n, flush=True)


if __name__ == "__main__":
    main()
