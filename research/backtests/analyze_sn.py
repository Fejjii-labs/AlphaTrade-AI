"""Compare live SFP/Nested rules with refinements. Usage: python analyze_sn.py <dir>"""

import glob
import json
import os
import sys
from collections import Counter, defaultdict

D = sys.argv[1]
rows = []
for path in glob.glob(os.path.join(D, "sn_*.json")):
    rows += json.load(open(path))
rows.sort(key=lambda r: r["entry_at"])


def take(rs):
    """One position per coin (per family) at a time, in time order."""
    busy: dict[tuple, str] = {}
    out = []
    for r in rs:
        key = (r["family"], r["symbol"])
        if key in busy and r["entry_at"] < busy[key]:
            continue
        out.append(r)
        busy[key] = r["trade"]["exit_at"]
    return out


def stats(rs):
    rs = take([r for r in rs if r.get("trade")])
    if not rs:
        return "0 trades"
    vals = [r["trade"]["net_r"] for r in rs]
    eq = peak = dd = 0.0
    streak = worst = 0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)
    months = defaultdict(float)
    for r in rs:
        months[r["entry_at"][:7]] += r["trade"]["net_r"]
    wins = sum(1 for v in vals if v > 0)
    return (f"{len(rs):4d} trades ({len(rs) / 12.8:4.1f}/mo) win {100 * wins / len(rs):3.0f}% "
            f"net {eq:+7.1f}R  {eq / len(rs):+.3f}R/tr  maxDD {dd:6.1f}R  streak {worst:2d}  "
            f"green {sum(1 for v in months.values() if v > 0)}/{len(months)}")


for fam in ("nested", "sfp"):
    F = [r for r in rows if r["family"] == fam]
    print(f"\n################ {fam.upper()}: {len(F)} signals")
    fails = Counter(k for r in F for k, v in r.items() if k.startswith("gate_") and v is False)
    print("signals failing each live check:", dict(fails))
    live = [r for r in F if r["live_ok"]]
    valid = [r for r in F if r.get("trade")]
    print("\n-- LIVE RULES (passes every live check), by timeframe")
    for tf in ("15m", "30m", "1h"):
        print(f"  {tf:4s} live : {stats([r for r in live if r['timeframe'] == tf])}")
    print(f"  ALL  live : {stats(live)}")
    print("\n-- all geometry-valid signals (no 1:1 / fee checks), by timeframe")
    for tf in ("15m", "30m", "1h"):
        print(f"  {tf:4s} any  : {stats([r for r in valid if r['timeframe'] == tf])}")
    print("\n-- by stop distance (live rules, all timeframes)")
    for lo, hi in ((0, 0.6), (0.6, 0.8), (0.8, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 100)):
        print(f"  {lo}-{hi}%: {stats([r for r in live if lo <= r['stop_pct'] < hi])}")
    print("\n-- minimum stop rule on top of live rules")
    for m in (0, 0.8, 1.0, 1.25, 1.5, 2.0):
        for tf in ("15m", "30m", "1h"):
            print(f"  min {m}% {tf:4s}: {stats([r for r in live if r['stop_pct'] >= m and r['timeframe'] == tf])}")
    print("\n-- minimum planned reward:risk on top of live rules (all timeframes)")
    for m in (1, 1.25, 1.5, 2, 2.5, 3):
        print(f"  rr>={m}: {stats([r for r in live if r['planned_rr'] >= m])}")
    print("\n-- by planned reward:risk band (live, all tfs)")
    for lo, hi in ((0, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 100)):
        print(f"  {lo}-{hi}: {stats([r for r in live if lo <= r['planned_rr'] < hi])}")
    if fam == "nested":
        print("\n-- by stage (live, all tfs)")
        for st in ("N1", "N2", "N3", "N4_PLUS"):
            print(f"  {st:7s}: {stats([r for r in live if r.get('stage') == st])}")
    print("\n-- by direction / coin (live, all tfs)")
    for d in ("long", "short"):
        print(f"  {d:5s}: {stats([r for r in live if r['direction'] == d])}")
    for c in sorted({r["symbol"] for r in F}):
        print(f"  {c:8s}: {stats([r for r in live if r['symbol'] == c])}")
    resolved = sum(r["trade"]["resolved_1m"] for r in valid)
    print(f"\nambiguous candles settled with 1m data: {resolved}")
