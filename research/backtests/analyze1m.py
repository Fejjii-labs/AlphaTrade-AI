"""Summarize the 1-minute D-line study: entry styles, stop-distance rule, timeframes.

Usage: python analyze1m.py <study_dir>
"""

import glob
import json
import os
import sys
from collections import defaultdict

S = sys.argv[1]
rows = []
for path in sorted(glob.glob(os.path.join(S, "out1m_*.json"))):
    rows += json.load(open(path))
rows = sorted([r for r in rows if r["breakout"]], key=lambda r: r["detected_at"])
for r in rows:
    b = r["breakout"]["net_r"]
    t = r["retest"]["net_r"] if r["retest"] else 0.0
    r["split"] = {"net_r": 0.5 * b + 0.5 * t, "risk_pct": r["breakout"]["risk_pct"]}


def agg(rs, key):
    taken = [r[key] for r in rs if r.get(key)]
    vals = [r[key]["net_r"] if r.get(key) else 0.0 for r in rs]
    eq = peak = dd = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    wins = sum(1 for t in taken if t["net_r"] > 0)
    return {
        "trades": len(taken),
        "win%": round(100 * wins / len(taken), 1) if taken else None,
        "net_R": round(sum(vals), 1),
        "R/trade": round(sum(t["net_r"] for t in taken) / len(taken), 3) if taken else None,
        "maxDD_R": round(dd, 1),
    }


def show(title, rs):
    print(f"\n== {title}: {len(rs)} signals, retest fill {round(100 * sum(1 for r in rs if r['retest']) / max(1, len(rs)))}%")
    for key in ("breakout", "retest", "split"):
        print(f"  {key:9s}", agg(rs, key))


def stop_pct(r, key):
    leg = r.get(key)
    return leg["risk_pct"] if leg else None


show("ALL", rows)
resolved = sum(r["breakout"]["resolved_candles"] for r in rows) + sum(
    r["retest"]["resolved_candles"] for r in rows if r["retest"]
)
print("candles settled with 1m data:", resolved)

by_tf = defaultdict(list)
for r in rows:
    by_tf[r["timeframe"]].append(r)
for tf in ("15m", "30m", "1h"):
    show(f"timeframe {tf}", by_tf[tf])

print("\n== by stop distance (each style's own stop %):")
buckets = [(0, 0.5), (0.5, 1), (1, 2), (2, 100)]
for key in ("breakout", "retest"):
    for lo, hi in buckets:
        legs = [r[key] for r in rows if r.get(key) and lo <= r[key]["risk_pct"] < hi]
        if legs:
            tot = sum(x["net_r"] for x in legs)
            print(f"  {key:9s} {lo}-{hi}%: {len(legs)} trades, net {tot:.1f}R, {tot / len(legs):.3f}R/trade, "
                  f"win {100 * sum(1 for x in legs if x['net_r'] > 0) / len(legs):.0f}%")

print("\n== minimum stop rule (skip a trade whose own stop is closer than X% of entry):")
for minimum in (0, 0.5, 0.75, 1.0, 1.25, 1.5):
    out = []
    for key in ("breakout", "retest"):
        legs = [r[key] for r in rows if r.get(key) and r[key]["risk_pct"] >= minimum]
        tot = sum(x["net_r"] for x in legs)
        out.append(f"{key} {len(legs)} trades {tot:+.1f}R ({tot / max(1, len(legs)):+.3f}/trade)")
    print(f"  min {minimum}%: " + " | ".join(out))

print("\n== minimum stop 1% by timeframe (retest | breakout):")
for tf in ("15m", "30m", "1h"):
    parts = []
    for key in ("retest", "breakout"):
        legs = [r[key] for r in by_tf[tf] if r.get(key) and r[key]["risk_pct"] >= 1.0]
        tot = sum(x["net_r"] for x in legs)
        parts.append(f"{key} {len(legs)} trades {tot:+.1f}R")
    print(f"  {tf}: " + " | ".join(parts))

print("\n== retest misses (expired) and their breakout result:")
missed = [r for r in rows if r["retest_status"] == "expired"]
print(f"  {len(missed)} missed, {sum(1 for r in missed if r['breakout']['net_r'] > 0)} would have won, "
      f"{sum(r['breakout']['net_r'] for r in missed):+.1f}R as breakouts")
big = [r for r in missed if r["breakout"]["risk_pct"] >= 1.0]
print(f"  with stop >=1%: {len(big)} missed, {sum(r['breakout']['net_r'] for r in big):+.1f}R as breakouts")

print("\n== per coin, retest with stop >=1%:")
by_coin = defaultdict(list)
for r in rows:
    by_coin[r["symbol"]].append(r)
for coin, rs in sorted(by_coin.items()):
    legs = [r["retest"] for r in rs if r.get("retest") and r["retest"]["risk_pct"] >= 1.0]
    tot = sum(x["net_r"] for x in legs)
    print(f"  {coin}: {len(legs)} trades {tot:+.1f}R")

print("\n== per month, retest with stop >=1% (net R):")
by_month = defaultdict(float)
count = defaultdict(int)
for r in rows:
    if r.get("retest") and r["retest"]["risk_pct"] >= 1.0:
        by_month[r["detected_at"][:7]] += r["retest"]["net_r"]
        count[r["detected_at"][:7]] += 1
print("  " + ", ".join(f"{m}: {v:+.1f}R/{count[m]}" for m, v in sorted(by_month.items())))
