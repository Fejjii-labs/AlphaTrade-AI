"""Markdown tables for the round-2 report, from r2.json (run_r2.py) and step0_*.json.

Usage: python -I tables_r2.py <out_dir> > tables.md
"""

from __future__ import annotations

import glob
import json
import math
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data as D  # noqa: E402
from engine_r2 import EXITS, portfolio  # noqa: E402
from stats import summary  # noqa: E402

OTHERS = [c for c in D.COINS if c != "BTCUSDT"]
SCOPES = {"BTC": ["BTCUSDT"], "Other four": OTHERS, "All five": D.COINS}
PLABEL = {"design": "design", "blind": "already seen"}
YEARS = {"Nov 23-Oct 24": (D.ms("2023-11-01"), D.ms("2024-11-01")),
         "Nov 24-Oct 25": (D.ms("2024-11-01"), D.ms("2025-11-01")),
         "Nov 25-Nov 26 (seen)": D.BLIND}


def fr(x, d=2):
    return f"{x:+.{d}f}".replace("-", "−")


def trades(res, coins, period, setting, kind):
    """Trades after the one-position-per-coin rule, per coin, then pooled."""
    out = []
    for c in coins:
        out += portfolio(res[c][f"{period}|{setting}|{kind}"])
    return out


def reached_1r(rows):
    losers = [t for t in rows if t["r"] < 0]
    return sum(1 for t in losers if t["hits"] >= 1) / len(losers) if losers else 0.0


def row(name, rows, period):
    s = summary(rows, period)
    return (f"| {name} | {s['trades']} | {s['per_month']:.1f} | {s['win'] * 100:.0f}% | "
            f"{fr(s['mean'])}R | {fr(s['net'], 1)}R | {s['t']:.2f} | {fr(s['dd'], 1)}R | "
            f"{s['streak']} | {reached_1r(rows) * 100:.0f}% | {s['green']} |")


HEAD = ("| Exit | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | "
        "Worst losing streak | Losers that reached +1R | Green months |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|")


def main_tables(res, setting="base"):
    for scope, coins in SCOPES.items():
        for p in D.PERIODS:
            print(f"\n**{scope}, {PLABEL[p]}**\n\n{HEAD}")
            for k in EXITS:
                print(row(k, trades(res, coins, p, setting, k), D.PERIODS[p]))


def paired(res, coins, period, kind, setting="base", n_boot=10_000, seed=7):
    """Ex - E0 on the same signals (every candidate, no one-position rule)."""
    diffs = []
    for c in coins:
        a = {t["entry_ms"]: t["r"] for t in res[c][f"{period}|{setting}|E0"]}
        b = {t["entry_ms"]: t["r"] for t in res[c][f"{period}|{setting}|{kind}"]}
        diffs += [b[k] - a[k] for k in a if k in b]
    n = len(diffs)
    mean = sum(diffs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in diffs) / (n - 1))
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(n_boot))
    return n, mean, sd / math.sqrt(n), boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot)]


def paired_table(res):
    print("\n| Coins | Period | Exit | Signals | Change vs E0 (R/trade) | Std. error | t | "
          "95% interval |\n|---|---|---|---|---|---|---|---|")
    for scope, coins in SCOPES.items():
        for p in D.PERIODS:
            for k in EXITS[1:]:
                n, m, se, lo, hi = paired(res, coins, p, k)
                print(f"| {scope} | {PLABEL[p]} | {k} | {n} | {fr(m)} | {se:.2f} | "
                      f"{m / se:.2f} | {fr(lo)} to {fr(hi)} |")


def by_year(res, coins=("BTCUSDT",)):
    print("\n| Exit | " + " | ".join(YEARS) + " |\n|---|" + "---|" * len(YEARS))
    for k in EXITS:
        allt = trades(res, coins, "design", "base", k) + trades(res, coins, "blind", "base", k)
        cells = []
        for a, z in YEARS.values():
            s = summary([t for t in allt if a <= t["entry_ms"] < z], (a, z))
            cells.append(f"{s['trades']} / {fr(s['mean'])}R")
        print(f"| {k} | " + " | ".join(cells) + " |")


def sensitivity(res):
    print("\n| Setting | Coins | Period | E0 | E1 | E2 | E3 |\n|---|---|---|---|---|---|---|")
    for setting in ("base", "through", "fill_at_lvl", "own_checks"):
        for scope in ("BTC", "All five"):
            for p in D.PERIODS:
                cells = []
                for k in EXITS:
                    s = summary(trades(res, SCOPES[scope], p, setting, k), D.PERIODS[p])
                    cells.append(f"{s['trades']} / {fr(s['mean'])}R" if s["trades"] else "0")
                print(f"| {setting} | {scope} | {PLABEL[p]} | " + " | ".join(cells) + " |")


def be_cost(res, coins=("BTCUSDT",), period="design"):
    """Signals where Ex was stopped at break-even after TP1: what E0 did with them."""
    print("\n| Exit | TP1 then stopped at break-even | E0 on the same signals: reached TP2 "
          "| E0 lost | Ex minus E0 on these (R) | Signals where E0 lost after +1R "
          "| Ex on those (R) | Ex minus E0 on those (R) |\n|---|---|---|---|---|---|---|---|")
    for k in EXITS[1:]:
        be_n = tp2 = e0_lost = 0
        d_be = d_gb = gb_x = 0.0
        gb_n = 0
        for c in coins:
            a = {t["entry_ms"]: t for t in res[c][f"{period}|base|E0"]}
            for t in res[c][f"{period}|base|{k}"]:
                e0 = a.get(t["entry_ms"])
                if e0 is None:
                    continue
                if e0["r"] < 0 and e0["hits"] >= 1:
                    gb_n += 1
                    gb_x += t["r"]
                    d_gb += t["r"] - e0["r"]
                if t["hits"] == 1 and t["fills"][-1][3] and abs(t["fills"][-1][2] - t["entry"]) \
                        < 0.002 * t["entry"]:
                    be_n += 1
                    tp2 += e0["hits"] >= 2
                    e0_lost += e0["r"] < 0
                    d_be += t["r"] - e0["r"]
        print(f"| {k} | {be_n} | {tp2} | {e0_lost} | {fr(d_be, 1)} | {gb_n} | {fr(gb_x, 1)} "
              f"| {fr(d_gb, 1)} |")


def decompose(res, coins=("BTCUSDT",), period="design"):
    """Ex - E0 on the same signals, split by what E0 did with the signal."""
    print("\n| Exit | E0 never reached +1R | E0 reached +1R, then lost | E0 reached +1R and won "
          "| Total |\n|---|---|---|---|---|")
    for k in EXITS[1:]:
        acc = {"never": [0, 0.0], "gave": [0, 0.0], "won": [0, 0.0]}
        for c in coins:
            a = {t["entry_ms"]: t for t in res[c][f"{period}|base|E0"]}
            for t in res[c][f"{period}|base|{k}"]:
                e0 = a.get(t["entry_ms"])
                if e0 is None:
                    continue
                g = "never" if e0["hits"] == 0 else "gave" if e0["r"] < 0 else "won"
                acc[g][0] += 1
                acc[g][1] += t["r"] - e0["r"]
        cells = [f"{n} signals, {fr(x, 1)}R" for n, x in acc.values()]
        tot = sum(x for _, x in acc.values())
        print(f"| {k} | " + " | ".join(cells) + f" | {fr(tot, 1)}R |")


def order_check(res):
    """Round 1 takes overlapping signals in reclaim order; a bot with resting limits would
    get the first fill. E0 under both rules."""
    print("\n| Coins | Period | Reclaim order (round 1) | First fill |\n|---|---|---|---|")
    for scope in ("BTC", "All five"):
        for p in D.PERIODS:
            a = [t for c in SCOPES[scope] for t in portfolio(res[c][f"{p}|base|E0"])]
            b = [t for c in SCOPES[scope] for t in portfolio(res[c][f"{p}|base|E0"], True)]
            sa, sb = summary(a, D.PERIODS[p]), summary(b, D.PERIODS[p])
            print(f"| {scope} | {PLABEL[p]} | {sa['trades']} / {fr(sa['mean'])}R | "
                  f"{sb['trades']} / {fr(sb['mean'])}R |")


def step0(out_dir):
    print("\n| Coin | Trades re-decided | Mismatch at the order time | Mismatch at the fill "
          "|\n|---|---|---|---|")
    for path in sorted(glob.glob(os.path.join(out_dir, "step0_*.json"))):
        rows = json.load(open(path))
        a = sum(1 for r in rows if any("@" not in m for m in r["mismatch"]))
        b = sum(1 for r in rows if any("@" in m for m in r["mismatch"]))
        kinds = Counter(m for r in rows for m in r["mismatch"])
        sym = os.path.basename(path)[6:-5]
        print(f"| {sym} | {len(rows)} | {a} | {b} |" + (f" {dict(kinds)}" if kinds else ""))


def fills_better_than_limit(res):
    """Limit entries filled at a minute open beyond the limit (price improvement)."""
    out = {}
    for c in D.COINS:
        for p in D.PERIODS:
            rows = res[c][f"{p}|base|E0"]
            lv = {t["entry_ms"]: t for t in res[c][f"{p}|fill_at_lvl|E0"]}
            imp = [(t["entry"], lv[t["entry_ms"]]["entry"]) for t in rows if t["entry_ms"] in lv
                   and t["entry"] != lv[t["entry_ms"]]["entry"]]
            out[(c, p)] = (len(rows), len(imp))
    print("\n| Coin | Period | Limit fills | Filled better than the limit |\n|---|---|---|---|")
    for (c, p), (n, k) in out.items():
        print(f"| {c} | {PLABEL[p]} | {n} | {k} |")


def main() -> None:
    out_dir = sys.argv[1]
    res = json.load(open(os.path.join(out_dir, "r2.json")))
    print("## Step 0"), step0(out_dir), fills_better_than_limit(res), order_check(res)
    print("\n## Main tables (base)"), main_tables(res)
    print("\n## Paired change vs E0"), paired_table(res)
    print("\n## BTC by year"), by_year(res)
    print("\n## All five by year"), by_year(res, D.COINS)
    print("\n## Sensitivity"), sensitivity(res)
    print("\n## Where the change comes from, BTC design"), decompose(res)
    print("\n## Where the change comes from, BTC seen"), decompose(res, period="blind")
    print("\n## Where the change comes from, all five design"), decompose(res, D.COINS)
    print("\n## Break-even cost, BTC design"), be_cost(res)
    print("\n## Break-even cost, all five design"), be_cost(res, D.COINS)


if __name__ == "__main__":
    main()
