"""Markdown tables for the round-3 report, from r3_*.json (run_r3.py) and step0_r3_*.json.

Usage: python -I tables_r3.py <out_dir> > tables.md
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sfp_r2"))
sys.path.insert(0, HERE)

import data as D  # noqa: E402
from classify import REASONS  # noqa: E402
from engine_r2 import portfolio  # noqa: E402
from run_r3 import PERIODS  # noqa: E402
from stats import summary  # noqa: E402
from vwap import VARIANTS  # noqa: E402

LABEL = {"blindA": "Blind A (never used)", "design": "Design (already seen)",
         "seen": "Blind-year (seen)", "forward": "Forward"}
SHORT = {c: c.replace("USDT", "") for c in D.COINS}
ORDER = ("broke", "gave_back", "hunted", "chop", "fees", "other")
# round 1, section 2: the 13 example losses (Tunis entry time)
R1_LOSSES = ["2023-11-02 04:00", "2023-11-13 13:00", "2023-11-24 11:45", "2024-01-28 02:30",
             "2024-03-07 15:45", "2024-08-02 21:30", "2024-08-21 20:45", "2024-11-29 03:15",
             "2025-01-28 05:45", "2025-04-22 02:30", "2025-08-27 15:45", "2025-10-16 10:45",
             "2025-10-30 20:45"]


def fr(x, d=2):
    return f"{x:+.{d}f}".replace("-", "−")


def load(out):
    res = {}
    for c in D.COINS:
        p = os.path.join(out, f"r3_{c}.json")
        if os.path.exists(p):
            res[c] = json.load(open(p))
    return res


def coins_with_data(res, period):
    return [c for c in res if res[c][period]]


def trades(res, coins, period, var):
    out = []
    for c in coins:
        out += portfolio([t for t in res[c][period] if t["keep"][var]])
    return out


def broke_share(rows):
    losers = [t for t in rows if t["r"] < 0]
    return sum(1 for t in losers if t["reason"] == "broke") / len(losers) if losers else 0.0


HEAD = ("| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | "
        "Worst losing streak | Losses: level broke | Green months |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|")


def row(name, rows, period):
    s = summary(rows, period)
    return (f"| {name} | {s['trades']} | {s['per_month']:.1f} | {s['win'] * 100:.0f}% | "
            f"{fr(s['mean'])}R | {fr(s['net'], 1)}R | {s['t']:.2f} | {fr(s['dd'], 1)}R | "
            f"{s['streak']} | {broke_share(rows) * 100:.0f}% | {s['green']} |")


def scopes(res, period):
    have = coins_with_data(res, period)
    out = {SHORT[c]: [c] for c in have}
    out[f"All {len(have)} together ({', '.join(SHORT[c] for c in have)})"] = have
    return out


def main_tables(res):
    for p in ("blindA", "design", "seen"):
        print(f"\n### {LABEL[p]}")
        for scope, coins in scopes(res, p).items():
            print(f"\n**{scope}**\n\n{HEAD}")
            for v in VARIANTS:
                print(row(v, trades(res, coins, p, v), PERIODS[p]))


def forward(res):
    have = coins_with_data(res, "forward") or list(res)
    print("\n| Variant | BTC trades | All coins trades | Still open at the end of the data |"
          "\n|---|---|---|---|")
    for v in VARIANTS:
        allt = trades(res, list(res), "forward", v)
        btc = [t for t in allt if t["sym"] == "BTCUSDT"]
        still = sum(1 for t in allt if t["exit_ms"] >= res[t["sym"]]["first"]["end"])
        print(f"| {v} | {len(btc)} | {len(allt)} | {still} |")
    print(f"\n(coins with a forward signal: {', '.join(SHORT[c] for c in have) or 'none'})")


def pass_rule(res):
    p = "blindA"
    btc = trades(res, ["BTCUSDT"], p, "F1")
    allc = trades(res, coins_with_data(res, p), p, "F1")
    s, sa = summary(btc, PERIODS[p]), summary(allc, PERIODS[p])
    ok_r, ok_t = s["mean"] >= 0.15, s["t"] >= 2
    ok_n = s["per_month"] >= 4 or sa["per_month"] >= 4
    print("\n| Condition (Blind A, F1) | Needed | Result | Met |\n|---|---|---|---|")
    print(f"| R per trade, BTC | ≥ +0.15R | {fr(s['mean'])}R | {'yes' if ok_r else 'no'} |")
    print(f"| t-stat, BTC | ≥ 2 | {s['t']:.2f} | {'yes' if ok_t else 'no'} |")
    print(f"| Trades a month, BTC or all coins | ≥ 4 | {s['per_month']:.1f} BTC / "
          f"{sa['per_month']:.1f} all | {'yes' if ok_n else 'no'} |")
    print(f"\n**F1 {'PASSES' if ok_r and ok_t and ok_n else 'FAILS'}.**")


def _boot(xs, ys, n_boot=10_000, seed=7):
    rng = random.Random(seed)
    out = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) - sum(rng.choice(ys) for _ in ys)
                 / len(ys) for _ in range(n_boot))
    return out[int(0.025 * n_boot)], out[int(0.975 * n_boot)]


def _ms(xs):
    n = len(xs)
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / (n - 1)) / n


def effect(res):
    """Kept vs filtered-out candidates (same signals, before the one-position rule)."""
    print("\n| Coins | Period | Variant | Candidates | Kept | R/trade kept | R/trade filtered out "
          "| Kept minus filtered out | Std. error | t | 95% interval (bootstrap) |"
          "\n|---|---|---|---|---|---|---|---|---|---|---|")
    for p in ("blindA", "design", "seen"):
        have = coins_with_data(res, p)
        for scope, coins in (("BTC", ["BTCUSDT"]), ("All", have)):
            for v in VARIANTS[1:]:
                cands = [t for c in coins for t in res[c][p]]
                k = [t["r"] for t in cands if t["keep"][v]]
                f = [t["r"] for t in cands if not t["keep"][v]]
                if len(k) < 2 or len(f) < 2:
                    continue
                (mk, vk), (mf, vf) = _ms(k), _ms(f)
                se = math.sqrt(vk + vf)
                lo, hi = _boot(k, f)
                name = scope if scope == "BTC" else f"All {len(have)}"
                print(f"| {name} | {LABEL[p]} | {v} | {len(cands)} | {len(k)} | {fr(mk)}R | "
                      f"{fr(mf)}R | {fr(mk - mf)}R | {se:.2f} | {(mk - mf) / se:.2f} | "
                      f"{fr(lo)} to {fr(hi)} |")


def reasons(res, coins=("BTCUSDT",)):
    print("\n| Loss reason | " + " | ".join(f"{LABEL[p].split(' (')[0]} {v}" for p in
                                          ("blindA", "design", "seen") for v in ("F0", "F1"))
          + " |\n|---|" + "---|" * 6)
    cols = []
    for p in ("blindA", "design", "seen"):
        for v in ("F0", "F1"):
            ls = [t for t in trades(res, list(coins), p, v) if t["r"] < 0]
            cols.append((Counter(t["reason"] for t in ls), len(ls)))
    for key in ORDER:
        if not any(c[key] for c, _ in cols):
            continue
        cells = [f"{c[key]} ({c[key] / n * 100:.0f}%)" if n else "0" for c, n in cols]
        print(f"| {REASONS[key]} | " + " | ".join(cells) + " |")
    print("| **All losses** | " + " | ".join(f"**{n}**" for _, n in cols) + " |")


def by_year(res, coins=("BTCUSDT",)):
    years = {"2020": ("2020-01-01", "2021-01-01"), "2021": ("2021-01-01", "2022-01-01"),
             "2022": ("2022-01-01", "2023-01-01"), "2023 (Jan-Oct)": ("2023-01-01", "2023-11-01")}
    print("\n| Variant | " + " | ".join(years) + " |\n|---|" + "---|" * len(years))
    for v in VARIANTS:
        allt = trades(res, list(coins), "blindA", v)
        cells = []
        for a, z in years.values():
            a, z = D.ms(a), D.ms(z)
            s = summary([t for t in allt if a <= t["entry_ms"] < z], (a, z))
            cells.append(f"{s['trades']} / {fr(s['mean'])}R")
        print(f"| {v} | " + " | ".join(cells) + " |")


def by_side(res, coins=("BTCUSDT",), p="blindA"):
    print("\n| Variant | Longs (trades / R per trade) | Shorts (trades / R per trade) |"
          "\n|---|---|---|")
    for v in VARIANTS:
        allt = trades(res, list(coins), p, v)
        cells = []
        for s in (1, -1):
            x = [t["r"] for t in allt if t["sign"] == s]
            cells.append(f"{len(x)} / {fr(sum(x) / len(x))}R" if x else "0")
        print(f"| {v} | " + " | ".join(cells) + " |")


def r1_losses(out):
    v01 = json.load(open(os.path.join(out, "v01_BTCUSDT.json")))
    by_t = {D.tunis(t["entry_ms"]): t for t in v01}
    print("\n| # | Entry (Tunis) | Level | Side | Price at decision | Weekly VWAP | Monthly VWAP "
          "| R | Reason | F1 | F2 | F3 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    n_f1 = Counter()
    for i, ts in enumerate(R1_LOSSES, 1):
        t = by_t.get(ts)
        if t is None:
            print(f"| {i} | {ts} | not found | | | | | | | | | |")
            continue

        def verdict(v):
            return "taken" if t["keep"][v] else "**filtered out**"
        for v in ("F1", "F2", "F3"):
            n_f1[v] += not t["keep"][v]
        vw = [f"{x:,.1f}" if x is not None else "n/a" for x in (t["vw_w"], t["vw_m"])]
        print(f"| {i} | {ts} | {t['level']} | {'long' if t['sign'] > 0 else 'short'} | "
              f"{t['price']:,.1f} | {vw[0]} | {vw[1]} | {fr(t['r'])} | {REASONS[t['reason']]} | "
              f"{verdict('F1')} | {verdict('F2')} | {verdict('F3')} |")
    print(f"\nFiltered out of 13: F1 {n_f1['F1']}, F2 {n_f1['F2']}, F3 {n_f1['F3']}.")
    # the same check on every V01 BTC design trade
    print("\n| V01 BTC design trades | All | Losses | Losses: level broke | Wins |\n|---|---|---|---|---|")
    for v in VARIANTS:
        k = [t for t in v01 if t["keep"][v]]
        ls = [t for t in k if t["r"] < 0]
        print(f"| kept by {v} | {len(k)} | {len(ls)} | {sum(t['reason'] == 'broke' for t in ls)} "
              f"| {sum(t['r'] > 0 for t in k)} |")


def step0_table(out):
    print("\n| Coin | Candidate VWAP verdicts re-decided | Differences | Candidates kept by F1-F3, re-decided "
          "in full | Differences | Leak control: candidates flagged |\n|---|---|---|---|---|---|")
    for c in D.COINS:
        p = os.path.join(out, f"step0_r3_{c}.json")
        if not os.path.exists(p):
            continue
        rows = json.load(open(p))
        full = [r for r in rows if r["full_check"]]
        nv = sum("vwap" in r["mismatch"] for r in rows)
        nf = sum(any(m not in ("vwap", "verdict") for m in r["mismatch"]) for r in full)
        q = os.path.join(out, f"step0_r3_{c}_leak.json")
        leak = "not run"
        if os.path.exists(q):
            lr = json.load(open(q))
            leak = (f"{sum(bool(r['mismatch']) for r in lr)} of {len(lr)} "
                    f"({sum('verdict' in r['mismatch'] for r in lr)} change a verdict)")
        print(f"| {SHORT[c]} | {len(rows)} | {nv} | {len(full)} | {nf} | {leak} |")


def coverage(res):
    print("\n| Coin | First 1h candle | First 1-minute bar | Candidates: " +
          " / ".join(LABEL[p].split(" (")[0] for p in PERIODS) + " |\n|---|---|---|---|")
    for c, r in res.items():
        print(f"| {SHORT[c]} | {D.tunis(r['first']['1h'])} | {D.tunis(r['first']['1m'])} | "
              + " / ".join(str(len(r[p])) for p in PERIODS) + " |")


def main() -> None:
    out = sys.argv[1]
    res = load(out)
    for title, fn in (("Coverage", lambda: coverage(res)), ("Main", lambda: main_tables(res)),
                      ("Forward", lambda: forward(res)), ("Pass rule", lambda: pass_rule(res)),
                      ("Effect vs noise", lambda: effect(res)),
                      ("Loss reasons BTC", lambda: reasons(res)),
                      ("Loss reasons all", lambda: reasons(res, tuple(res))),
                      ("BTC Blind A by year", lambda: by_year(res)),
                      ("All Blind A by year", lambda: by_year(res, tuple(res))),
                      ("BTC Blind A by side", lambda: by_side(res)),
                      ("Round 1 losses", lambda: r1_losses(out)),
                      ("Step 0", lambda: step0_table(out))):
        print(f"\n## {title}")
        fn()


if __name__ == "__main__":
    main()
