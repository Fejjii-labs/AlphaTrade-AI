"""Step 3: run every frozen variant once on both periods, every coin.

Reads ``frozen_variants.json`` (committed before this run) and refuses to run when the
file is not committed in git with no local changes, so the blind year is scored only on
variants fixed in advance.

Usage: python -I blind_run.py <data_root> <out_dir>
Writes <out_dir>/blind.json: per variant, per period, per coin, trades and summaries.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
import engine  # noqa: E402

FROZEN = os.path.join(HERE, "frozen_variants.json")


def committed() -> bool:
    rel = os.path.relpath(FROZEN, HERE)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=HERE,
                             capture_output=True).returncode == 0
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=HERE).returncode == 0
    return tracked and clean


def work(args):
    root, symbol, variants = args
    d = D.Data(root, symbol)
    out = {}
    for v in variants:
        for pname, period in D.PERIODS.items():
            res = engine.run(d, v["settings"], period)
            out[(v["id"], pname)] = {"trades": res["trades"], "counts": res["counts"]}
    return symbol, out


def main() -> None:
    root, out_dir = sys.argv[1:3]
    if not committed():
        sys.exit("frozen_variants.json must be committed (and unchanged) before the blind run")
    variants = json.load(open(FROZEN))["variants"]
    assert len(variants) <= 12
    result = {v["id"]: {p: {} for p in D.PERIODS} for v in variants}
    with Pool(4) as pool:
        for sym, out in pool.imap_unordered(work, [(root, s, variants) for s in D.COINS]):
            for (vid, p), r in out.items():
                result[vid][p][sym] = r
            print("done", sym, flush=True)
    json.dump({"variants": variants, "result": result},
              open(os.path.join(out_dir, "blind.json"), "w"), default=float)


if __name__ == "__main__":
    main()
