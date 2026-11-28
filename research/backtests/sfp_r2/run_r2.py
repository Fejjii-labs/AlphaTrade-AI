"""Round 2: run V05 with exits E0-E3 on every coin, both periods, once.

Settings (``SETTINGS``):
- ``base``         the pre-registered test: same signals for every exit (round 1's checks);
- ``own_checks``   each exit's own ladder must pass the bot's reward check (E2 never can);
- ``through``      targets fill only once price trades one tick beyond them (fill check);
- ``fill_at_lvl``  a limit entry never fills better than its limit price (step 0 check).
Every candidate signal is simulated with every exit; the one-position-per-coin rule is
applied per exit in ``tables_r2.py``, so each exit gets its own trade list.

Usage: python -I run_r2.py <data_root> <out_dir>
Writes <out_dir>/r2.json.
"""

from __future__ import annotations

import json
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
from engine_r2 import EXITS, candidates, sim  # noqa: E402

SETTINGS = {"base": {}, "own_checks": {"own_checks": True}, "through": {"through": True},
            "fill_at_lvl": {}}
KEEP = ("sym", "sign", "level", "conf", "reclaim_ms", "entry_ms", "entry", "stop", "stop_pct",
        "r1_ladder", "ladder", "r", "net_r", "fund_r", "gross_r", "exit_ms", "hits", "fills")


def work(args):
    root, symbol = args
    d = D.Data(root, symbol)
    out = {}
    for pname, period in D.PERIODS.items():
        cands = candidates(d, period)
        cands_lvl = candidates(d, period, fill_at_level=True)
        for sname, kw in SETTINGS.items():
            cs = cands_lvl if sname == "fill_at_lvl" else cands
            for kind in EXITS:
                rows = [sim(d, c, kind, **kw) for c in cs]
                out[f"{pname}|{sname}|{kind}"] = [
                    {k: t[k] for k in KEEP} for t in rows if t is not None]
    return symbol, out


def main() -> None:
    root, out_dir = sys.argv[1:3]
    os.makedirs(out_dir, exist_ok=True)
    result = {}
    with Pool(4) as pool:
        for sym, out in pool.imap_unordered(work, [(root, s) for s in D.COINS]):
            result[sym] = out
            print("done", sym, flush=True)
    json.dump(result, open(os.path.join(out_dir, "r2.json"), "w"), default=float)


if __name__ == "__main__":
    main()
