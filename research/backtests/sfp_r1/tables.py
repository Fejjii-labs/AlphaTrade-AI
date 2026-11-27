"""Markdown tables for the report, from step1.json, explore_*.json and blind.json.

Usage: python -I tables.py step1|explore|blind <out_dir>
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data as D  # noqa: E402
from classify import REASONS  # noqa: E402
from stats import by_coin_share, summary  # noqa: E402


def fr(x, d=2):
    return f"{x:+.{d}f}".replace("-", "−")


def srow(s):
    return (f"{s['trades']} | {s['per_month']:.1f} | {s['win'] * 100:.0f}% | {fr(s['mean'])}R | "
            f"{fr(s['net'], 1)}R | {s['t']:.2f} | {fr(s['dd'], 1)}R | {s['streak']} | {s['green']}")


SHEAD = ("Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | "
         "Worst losing streak | Green months")


def vname(v):
    lv = "+".join(v["levels"]) + (" +equal" if v["equal"] else "")
    bits = [v["exec"], lv, f"entry {v['entry']}", f"stop {v['stop']}"]
    if v["life"] != "prev":
        bits.append("naked levels")
    if v["reclaim"] != "next":
        bits.append("same-candle reclaim")
    if v["exit"] != "default":
        bits.append("TP2 = candle range")
    if v["vol"]:
        bits.append("volume 1.5x")
    if v["conf"] > 1:
        bits.append(f"confluence ≥{v['conf']}")
    return ", ".join(bits)


def step1(out_dir):
    s = json.load(open(os.path.join(out_dir, "step1.json")))
    print("| Version | " + SHEAD + " |\n|" + "---|" * 10)
    print(f"| HTF default | {srow(s['default']['summary'])} |")
    print(f"| Live bot SFP (15m) | {srow(s['bot']['summary'])} |")
    print()
    print("| Loss reason | HTF default | | Live bot SFP | |\n|---|---|---|---|---|")
    a, b = s["default"]["reasons"], s["bot"]["reasons"]
    na, nb = sum(a.values()), sum(b.values())
    for k, label in REASONS.items():
        if k == "refused":
            ra = sum(s["default"]["counts"][x] for x in ("reward_below_risk", "fees", "geometry"))
            rb = sum(s["bot"]["refused"].values())
            print(f"| {label} | ({ra} signals) | | ({rb} signals) | |")
            continue
        print(f"| {label} | {a.get(k, 0)} | {a.get(k, 0) / na * 100:.0f}% | {b.get(k, 0)} | "
              f"{b.get(k, 0) / nb * 100 if nb else 0:.0f}% |")
    print(f"| **All losses** | **{na}** | 100% | **{nb}** | 100% |")
    print()
    print("| Chart | Entry (Tunis) | Level | Side | Entry | Stop | Exits | R | Reason |")
    print("|---|---|---|---|---|---|---|---|---|")
    for g in s["gallery"]:
        exits = ", ".join(f"{int(round(f * 100))}% @ {p:,.0f}" for _, f, p, _ in g["fills"])
        print(f"| [{g['file']}](sfp_r1_charts/{g['file']}) | {D.tunis(g['entry_ms'])} | "
              f"{g['level']} | {'long' if g['sign'] > 0 else 'short'} | {g['entry']:,.1f} | "
              f"{g['stop']:,.1f} | {exits} | {fr(g['r'])} | "
              f"{REASONS[g['reason']] if g.get('reason') else 'win'} |")
    print()
    print("counts", s["default"]["counts"], "refused replay", s["default"]["refused_summary"],
          "bot refused", s["bot"]["refused"], "bot busy", s["bot"]["busy"])


def explore(out_dir, top=25):
    rows = {}
    for path in glob.glob(os.path.join(out_dir, "explore_*.json")):
        sym = path.rsplit("_", 1)[1][:-5]
        rows[sym] = json.load(open(path))
    btc = rows["BTCUSDT"]
    for k, r in enumerate(btc):
        r["k"] = k
    ok = [r for r in btc if r["s"]["trades"] >= 40]
    ok.sort(key=lambda r: -r["s"]["mean"])
    print(f"BTC settings with >= 40 trades: {len(ok)} of {len(btc)}; positive: "
          f"{sum(r['s']['mean'] > 0 for r in ok)}")
    print("| # | Setting | BTC trades | BTC R/trade | BTC t | other coins trades | other R/trade |")
    print("|---|---|---|---|---|---|---|")
    for r in ok[:top]:
        others = [rows[s][r["k"]]["s"] for s in rows if s != "BTCUSDT"]
        n = sum(o["trades"] for o in others)
        m = sum(o["mean"] * o["trades"] for o in others) / n if n else 0
        print(f"| {r['k']} | {vname(r['v'])} | {r['s']['trades']} | {fr(r['s']['mean'])} | "
              f"{r['s']['t']:.2f} | {n} | {fr(m)} |")


def blind(out_dir):
    b = json.load(open(os.path.join(out_dir, "blind.json")))
    for v in b["variants"]:
        print(f"\n**{v['id']}**: {v['label']}\n")
        print("| Coins | Period | " + SHEAD + " |\n|" + "---|" * 11)
        for name, coins in (("BTC", ["BTCUSDT"]), ("Other four", D.COINS[1:]),
                            ("All five", D.COINS)):
            for p, per in D.PERIODS.items():
                tr = [t for c in coins for t in b["result"][v["id"]][p][c]["trades"]]
                print(f"| {name} | {p} | {srow(summary(tr, per))} |")
        for p in D.PERIODS:
            tr = [t for c in D.COINS for t in b["result"][v["id"]][p][c]["trades"]]
            sh = by_coin_share(tr)
            print(f"\n{p} net by coin: " + ", ".join(
                f"{c[:-4]} {fr(sh[c]['net'], 1)}R ({sh[c]['share'] * 100:.0f}% of profit)"
                for c in D.COINS if c in sh))


if __name__ == "__main__":
    {"step1": step1, "explore": explore, "blind": blind}[sys.argv[1]](sys.argv[2])
