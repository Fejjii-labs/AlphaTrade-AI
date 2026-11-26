"""Three-year study, step 3: choose settings on the in-sample years, then score the blind year.

Reads out/sim_<family>_<SYMBOL>.npz/.json written by bt3y_simulate.py.

    python bt3y_analyze.py <data_root> select   # in-sample only: grids + selection.json
    python bt3y_analyze.py <data_root> final    # frozen selection.json on both periods

``select`` drops every trade entered on or after the split (1 Nov 2025) before it computes
anything, so the choice cannot see the blind year. The rule is fixed in code:
- a setting = timeframes x filter (minimum stop distance / minimum stage) x exit scenario;
- eligible: at least 4 trades a month in-sample, worst drawdown no deeper than 20R, and
  positive R per trade in each of the two in-sample years;
- score: the mean in-sample R per trade of the setting and its neighbours (one step away in
  minimum stop, TP1, trail, break-even, target placement or entry). Highest score wins, so
  a wide good area beats a single good point.
One position per coin is applied in time order (per strategy here; across strategies in
``final``). Months are counted over the period, not per coin.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from bt3y_common import COINS, SPLIT_MS, regime  # noqa: E402

Y2_MS = 1_730_419_200_000  # 2024-11-01: start of the second in-sample year
START_MS = 1_698_796_800_000  # 2023-11-01
END_MS = 1_795_651_200_000  # 2026-11-26
MONTH = 30.4375 * 86_400_000
TFS = {"dline": ["15m", "30m", "1h"], "nested": ["15m", "30m", "1h", "4h"],
       "sfp": ["15m", "30m", "1h", "4h"]}
STAGES = ["N1", "N2", "N3", "N4_PLUS"]
FILTERS = {"dline": [0.0, 1.0, 1.5], "nested": [0, 1, 2], "sfp": [0.0, 1.0]}  # see ``mask``
PERIODS = {"is": (START_MS, SPLIT_MS), "y1": (START_MS, Y2_MS), "y2": (Y2_MS, SPLIT_MS),
           "blind": (SPLIT_MS, END_MS), "all": (START_MS, END_MS)}


class Family:
    """All coins of one strategy: per-signal columns and per-scenario matrices."""

    def __init__(self, root: str, name: str):
        cols = defaultdict(list)
        mats = defaultdict(list)
        for ci, coin in enumerate(COINS):
            base = os.path.join(root, "out", f"sim_{name}_{coin}")
            if not os.path.exists(base + ".npz"):
                continue
            z, j = np.load(base + ".npz"), json.load(open(base + ".json"))
            self.keys = j["keys"]
            for m in j["meta"]:
                cols["coin"].append(ci)
                cols["tf"].append(TFS[name].index(m["tf"]) if m["tf"] in TFS[name] else -1)
                cols["sign"].append(m["sign"])
                cols["stage"].append(STAGES.index(m["stage"]) if m.get("stage") else 0)
                cols["ret30"].append(np.nan if m["ret30"] is None else m["ret30"])
                cols["trend"].append(m["trend"])
            for k in ("net", "fund", "exit_ms", "entry_ms", "stop_pct"):
                mats[k].append(z[k])
        self.name = name
        self.c = {k: np.array(v) for k, v in cols.items()}
        self.m = {k: np.concatenate(v) for k, v in mats.items()}
        self.kidx = {k: i for i, k in enumerate(self.keys)}

    def mask(self, tfs, flt, extra=None):
        """Signals on ``tfs`` passing the family filter: D-line / SFP minimum stop distance
        in percent (applied per scenario in ``trades``), Nested minimum stage index."""
        m = np.isin(self.c["tf"], [TFS[self.name].index(t) for t in tfs])
        if self.name == "nested":
            m &= self.c["stage"] >= flt
        if extra is not None:
            m &= extra
        return m

    def trades(self, key, tfs, flt, extra=None, lo=START_MS, hi=END_MS):
        """Indices of the trades taken (one position per coin), entered in [lo, hi)."""
        k = self.kidx[key]
        ok = self.mask(tfs, flt, extra) & ~np.isnan(self.m["net"][:, k])
        if self.name != "nested":
            ok &= self.m["stop_pct"][:, k] >= flt
        idx = np.flatnonzero(ok)
        idx = idx[np.argsort(self.m["entry_ms"][idx, k], kind="stable")]
        return one_per_coin([(self, i, k) for i in idx], lo, hi)


def one_per_coin(cands, lo=START_MS, hi=END_MS):
    """Greedy in entry order: a signal is skipped while its coin has an open position."""
    busy, out = {}, []
    for fam, i, k in cands:
        t = fam.m["entry_ms"][i, k]
        coin = fam.c["coin"][i]
        if t < busy.get(coin, 0):
            continue
        busy[coin] = fam.m["exit_ms"][i, k]
        if lo <= t < hi:
            out.append((fam, i, k))
    return out


def stats(taken, lo, hi, funding=False):
    rows = sorted(((f.m["exit_ms"][i, k],
                    f.m["net"][i, k] + (f.m["fund"][i, k] if funding else 0.0)) for f, i, k in taken))
    n = len(rows)
    if not n:
        return {"n": 0, "pm": 0.0, "win": 0.0, "net": 0.0, "avg": 0.0, "dd": 0.0, "streak": 0,
                "green": "0/0"}
    eq = peak = dd = 0.0
    streak = worst = 0
    months = defaultdict(float)
    for t, v in rows:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = streak + 1 if v < 0 else 0
        worst = max(worst, streak)
        months[datetime.fromtimestamp(t / 1000, tz=UTC).strftime("%Y-%m")] += v
    return {"n": n, "pm": n / ((hi - lo) / MONTH), "win": sum(v > 0 for _, v in rows) / n,
            "net": eq, "avg": eq / n, "dd": dd, "streak": worst,
            "green": f"{sum(v > 0 for v in months.values())}/{len(months)}"}


def period(taken, name):
    lo, hi = PERIODS[name]
    return [(f, i, k) for f, i, k in taken if lo <= f.m["entry_ms"][i, k] < hi]


def fmt(s):
    return (f"{s['n']:5d} | {s['pm']:5.1f} | {100 * s['win']:3.0f}% | {s['net']:+7.1f}R | "
            f"{s['avg']:+.3f}R | {s['dd']:6.1f}R | {s['streak']:2d} | {s['green']}")


HEAD = "trades | /month | win | net | per trade | worst DD | streak | green months"
LIVE = {  # what the demo runs today (strategy_spec_v1, "Live today")
    "dline": (("30m", "1h"), 1.0, "B|live|1|5|live"),
    "nested": (("30m", "1h"), 1, "B|live|1|5|live"),
    "sfp": (("15m",), 0.0, "B|live|1|5|live"),
}
ORDERED = {"tp1": ["half", "0.75", "1", "1.5"], "trail": ["3", "5", "8"]}


def neighbours(fam, flt, key):
    """Settings one step away (same timeframes)."""
    out = []
    steps = FILTERS[fam.name]
    j = steps.index(flt)
    out += [(steps[x], key) for x in (j - 1, j + 1) if 0 <= x < len(steps)]
    e, tp1, be, trail, place = key.split("|")
    if tp1 == "live":
        return out
    alts = []
    for name, value in (("tp1", tp1), ("trail", trail)):
        seq = ORDERED[name]
        j = seq.index(value)
        alts += [(name, seq[x]) for x in (j - 1, j + 1) if 0 <= x < len(seq)]
    alts += [("be", "0" if be == "1" else "1")]
    alts += [("place", p) for p in ("struct", "htf", "first") if p != place]
    alts += [("e", x) for x in ("B", "R") if x != e]
    for name, value in alts:
        parts = {"e": e, "tp1": tp1, "be": be, "trail": trail, "place": place, name: value}
        k = "|".join(parts[x] for x in ("e", "tp1", "be", "trail", "place"))
        if k in fam.kidx:
            out.append((flt, k))
    return out


def evaluate_all(fam, names=("is", "y1", "y2")):
    """Every setting's stats on the named periods. Trades after the last period are never read."""
    hi = max(PERIODS[n][1] for n in names)
    tfsets = [c for r in range(1, len(TFS[fam.name]) + 1)
              for c in itertools.combinations(TFS[fam.name], r)]
    table = {}
    for tfs in tfsets:
        for flt in FILTERS[fam.name]:
            for key in fam.keys:
                taken = fam.trades(key, tfs, flt, hi=hi)
                table[(tfs, flt, key)] = {n: stats(period(taken, n), *PERIODS[n]) for n in names}
    return table


def choose(fam, table):
    scored = []
    for (tfs, flt, key), s in table.items():
        near = [table[(tfs, f, k)]["is"] for f, k in neighbours(fam, flt, key)]
        near = [x["avg"] for x in near if x["n"] >= 30]
        score = float(np.mean([s["is"]["avg"], *near]))
        eligible = (s["is"]["pm"] >= 4 and s["is"]["dd"] >= -20 and s["y1"]["avg"] > 0
                    and s["y2"]["avg"] > 0 and s["y1"]["n"] >= 20 and s["y2"]["n"] >= 20)
        scored.append((score, eligible, tfs, flt, key))
    scored.sort(key=lambda x: -x[0])
    return scored


def label(fam, tfs, flt, key):
    f = (f"stage>={STAGES[flt]}" if fam.name == "nested" else f"stop>={flt}%")
    return f"{'+'.join(tfs):14s} {f:10s} {key}"


def select(root, names=("dline", "nested", "sfp")):
    path = os.path.join(root, "out", "selection.json")
    selection = json.load(open(path)) if os.path.exists(path) else {}
    for name in names:
        fam = Family(root, name)
        table = evaluate_all(fam)
        scored = choose(fam, table)
        print(f"\n## {name}: {len(fam.c['coin'])} signals, {len(table)} settings (in-sample only)")
        positive = sum(1 for s in table.values() if s["is"]["avg"] > 0 and s["is"]["n"] >= 50)
        print(f"settings with positive in-sample R per trade (>= 50 trades): {positive}/{len(table)}")
        tfs, flt, key = LIVE[name]
        print(f"\nlive today  {label(fam, tfs, flt, key)}\n  {HEAD}")
        for n in ("is", "y1", "y2"):
            print(f"  {n:3s} {fmt(table[(tfs, flt, key)][n])}")
        print("\nlive exits by single timeframe and filter (in-sample)")
        for tf in TFS[name]:
            for f in FILTERS[name]:
                for e in sorted({k.split('|')[0] for k in fam.keys}):
                    s = table[((tf,), f, f"{e}|live|1|5|live")]
                    print(f"  {label(fam, (tf,), f, e)}  {fmt(s['is'])}  y1 {s['y1']['avg']:+.3f} y2 {s['y2']['avg']:+.3f}")
        print("\ntop 12 eligible settings by neighbourhood score")
        top = [x for x in scored if x[1]][:12]
        for score, _, tfs, flt, key in top:
            s = table[(tfs, flt, key)]
            print(f"  {score:+.3f}  {label(fam, tfs, flt, key)}  {fmt(s['is'])}  "
                  f"y1 {s['y1']['avg']:+.3f} y2 {s['y2']['avg']:+.3f}")
        if top:
            score, _, tfs, flt, key = top[0]
            selection[name] = {"tfs": list(tfs), "filter": flt, "key": key, "score": score,
                               "eligible": True}
        else:
            print("  none eligible; least bad by score with >= 4 trades a month:")
            rest = [x for x in scored if table[x[2:]]["is"]["pm"] >= 4][:12]
            for score, _, tfs, flt, key in rest:
                s = table[(tfs, flt, key)]
                print(f"  {score:+.3f}  {label(fam, tfs, flt, key)}  {fmt(s['is'])}  "
                      f"y1 {s['y1']['avg']:+.3f} y2 {s['y2']['avg']:+.3f}")
            score, _, tfs, flt, key = rest[0]
            selection[name] = {"tfs": list(tfs), "filter": flt, "key": key, "score": score,
                               "eligible": False}
        print("\none-at-a-time sensitivity (in-sample R per trade, mean over the other exit choices)")
        base_tfs, base_flt = tuple(selection[name]["tfs"]), selection[name]["filter"]
        for pos, dim in enumerate(("entry", "tp1", "be", "trail", "place")):
            groups = defaultdict(list)
            for (t, f, k), s in table.items():
                if t == base_tfs and f == base_flt and s["is"]["n"] >= 30:
                    groups[k.split("|")[pos]].append(s["is"]["avg"])
            print(f"  {dim:6s} " + "  ".join(f"{v}: {np.mean(a):+.3f}" for v, a in sorted(groups.items())))
    json.dump(selection, open(path, "w"), indent=1)
    print("\nselection:", json.dumps(selection))


def both(taken, funding=False):
    return [stats(period(taken, n), *PERIODS[n], funding) for n in ("is", "blind")]


def side_by_side(title, taken):
    a, b = both(taken)
    print(f"| {title} | {a['n']} | {a['pm']:.1f} | {100 * a['win']:.0f}% | {a['net']:+.1f}R | "
          f"{a['avg']:+.3f}R | {a['dd']:.1f}R | {a['streak']} | {a['green']} "
          f"| {b['n']} | {b['pm']:.1f} | {100 * b['win']:.0f}% | {b['net']:+.1f}R | {b['avg']:+.3f}R "
          f"| {b['dd']:.1f}R | {b['streak']} | {b['green']} |")


SBS_HEAD = ("| | IS trades | /mo | win | net | per trade | worst DD | streak | green | "
            "blind trades | /mo | win | net | per trade | worst DD | streak | green |\n"
            + "|---" * 17 + "|")


def breakdown(fam, taken):
    def cut(title, pred):
        side_by_side(title, [(f, i, k) for f, i, k in taken if pred(f, i)])
    for t, tf in enumerate(TFS[fam.name]):
        cut(tf, lambda f, i, t=t: f.c["tf"][i] == t)
    for c, coin in enumerate(COINS):
        cut(coin[:-4], lambda f, i, c=c: f.c["coin"][i] == c)
    cut("long", lambda f, i: f.c["sign"][i] > 0)
    cut("short", lambda f, i: f.c["sign"][i] < 0)
    for r in ("rising", "falling", "sideways"):
        cut(f"market {r}", lambda f, i, r=r: regime(None if np.isnan(f.c["ret30"][i]) else f.c["ret30"][i]) == r)
    cut("with 4h trend", lambda f, i: f.c["trend"][i] == f.c["sign"][i])
    cut("against 4h trend", lambda f, i: f.c["trend"][i] == -f.c["sign"][i])
    if fam.name == "nested":
        for s, stage in enumerate(STAGES):
            cut(stage, lambda f, i, s=s: f.c["stage"][i] == s)


def final(root):
    selection = json.load(open(os.path.join(root, "out", "selection.json")))
    fams = {n: Family(root, n) for n in ("dline", "nested", "sfp")}
    chosen = {}
    for name, fam in fams.items():
        sel = selection.get(name)
        print(f"\n## {name}\n\n{SBS_HEAD}")
        tfs, flt, key = LIVE[name]
        side_by_side(f"live today: {label(fam, tfs, flt, key)}", fam.trades(key, tfs, flt))
        if sel:
            tfs, flt, key = tuple(sel["tfs"]), sel["filter"], sel["key"]
            taken = fam.trades(key, tfs, flt)
            chosen[name] = (tfs, flt, key)
            tag = "chosen" if sel.get("eligible", True) else "least bad in-sample (failed the rule)"
            side_by_side(f"{tag}: {label(fam, tfs, flt, key)}", taken)
            a, b = both(taken, funding=True)
            print(f"\nchosen with estimated funding: IS {a['net']:+.1f}R ({a['avg']:+.3f}R/trade), "
                  f"blind {b['net']:+.1f}R ({b['avg']:+.3f}R/trade)")
            y1, y2 = (stats(period(taken, n), *PERIODS[n]) for n in ("y1", "y2"))
            print(f"chosen by in-sample year: y1 {y1['net']:+.1f}R ({y1['avg']:+.3f}), y2 {y2['net']:+.1f}R ({y2['avg']:+.3f})")
            print(f"\nbreakdown of the chosen setting\n\n{SBS_HEAD}")
            breakdown(fam, taken)
        base_tfs, base_flt, base_key = chosen.get(name, LIVE[name])
        e, tp1, be, trail, place = base_key.split("|")
        print(f"\nscenarios, one change at a time from the row above\n\n{SBS_HEAD}")
        for tf in TFS[name]:
            side_by_side(f"timeframe {tf} only", fam.trades(base_key, (tf,), base_flt))
        for f in FILTERS[name]:
            side_by_side(f"filter {f}", fam.trades(base_key, base_tfs, f))
        side_by_side("live exits", fam.trades(f"{e}|live|1|5|live", base_tfs, base_flt))
        if tp1 == "live":
            tp1, be, trail, place = "1", "1", "5", "struct"
        for dim, values in (("e", ["B", "R"]), ("tp1", ORDERED["tp1"]), ("be", ["1", "0"]),
                            ("trail", ORDERED["trail"]), ("place", ["struct", "htf", "first"])):
            for v in values:
                parts = {"e": e, "tp1": tp1, "be": be, "trail": trail, "place": place, dim: v}
                k = "|".join(parts[x] for x in ("e", "tp1", "be", "trail", "place"))
                if k in fam.kidx:
                    side_by_side(f"{dim}={v}", fam.trades(k, base_tfs, base_flt))
        table = evaluate_all(fam, names=("is", "blind"))
        pairs = [(s["is"]["avg"], s["blind"]["avg"]) for s in table.values()
                 if s["is"]["n"] >= 50 and s["blind"]["n"] >= 25]
        x, y = np.array(pairs).T
        rank = lambda v: np.argsort(np.argsort(v))  # noqa: E731
        print(f"\nall {len(pairs)} settings: rank correlation in-sample vs blind R/trade "
              f"{np.corrcoef(rank(x), rank(y))[0, 1]:+.2f}; positive in-sample {np.mean(x > 0):.0%}, "
              f"positive blind {np.mean(y > 0):.0%}, positive in both {np.mean((x > 0) & (y > 0)):.0%}")
    print(f"\n## all strategies together (one position per coin across strategies)\n\n{SBS_HEAD}")
    for title, pick in (("live today", LIVE), ("chosen", chosen)):
        cands = []
        for name, (tfs, flt, key) in pick.items():
            fam = fams[name]
            k = fam.kidx[key]
            ok = fam.mask(tfs, flt) & ~np.isnan(fam.m["net"][:, k])
            if name != "nested":
                ok &= fam.m["stop_pct"][:, k] >= flt
            cands += [(fam, i, k) for i in np.flatnonzero(ok)]
        cands.sort(key=lambda c: c[0].m["entry_ms"][c[1], c[2]])
        taken = one_per_coin(cands)
        side_by_side(title, taken)
        for name in pick:
            side_by_side(f"  of which {name}", [c for c in taken if c[0].name == name])
        a, b = both(taken, funding=True)
        print(f"| {title} with estimated funding | | | | {a['net']:+.1f}R | {a['avg']:+.3f}R | | | "
              f"| | | | {b['net']:+.1f}R | {b['avg']:+.3f}R | | | |")


# ---- SFP at significant levels (out/lvl_<SYMBOL>.npz from bt3y_levels.py) ----
L_TRIG, L_LVL, L_STOP = ["15m", "30m", "1h", "4h"], ["4h", "8h", "1d", "1w"], ["tick", "atr25", "atr50"]
L_EXITS = [f"{t}|{be}|{tr}" for t in ("0.75", "1", "1.5") for be in (1, 0) for tr in (3, 5, 8)]
L_BASE = {"coins": "all", "trig": "15m", "lvl": "4h", "reclaim": "same", "confirm": 0,
          "stop": "tick", "entry": "M", "trend": "off", "exit": "1|1|5"}
L_DIMS = {"coins": ["all", "BTC"], "trig": L_TRIG, "lvl": L_LVL, "reclaim": ["same", "any"],
          "confirm": [0, 1], "stop": L_STOP, "entry": ["M", "R"], "trend": ["off", "with"],
          "exit": L_EXITS}
TFS["levels"] = L_TRIG


class Levels:
    name = "levels"

    def __init__(self, root: str):
        cols, mats = defaultdict(list), defaultdict(list)
        for ci, coin in enumerate(COINS):
            base = os.path.join(root, "out", f"lvl_{coin}")
            z, j = np.load(base + ".npz"), json.load(open(base + ".json"))
            for m in j["meta"]:
                cols["coin"].append(ci)
                cols["tf"].append(L_TRIG.index(m["trig"]))
                cols["lvl"].append(L_LVL.index(m["lvl"]))
                cols["sign"].append(m["sign"])
                cols["same"].append(m["reclaim"] == "same")
                cols["confirm"].append(m["confirm"])
                cols["ret30"].append(np.nan if m["ret30"] is None else m["ret30"])
                cols["trend"].append(m["trend"])
                cols["sig"].append(m["sig_ms"])
                cols["retest"].append(m["retest_ms"])
            n = len(j["meta"])
            for k in ("net", "fund", "exit_ms"):
                mats[k].append(z[k].reshape(n, -1))
            mats["stop_pct"].append(np.repeat(z["stop_pct"].reshape(n, -1), len(L_EXITS), axis=1))
        self.c = {k: np.array(v) for k, v in cols.items()}
        self.m = {k: np.concatenate(v) for k, v in mats.items()}
        per_entry = np.stack([self.c["sig"], self.c["retest"]], axis=1)  # [N, 2]
        self.m["entry_ms"] = np.tile(np.repeat(per_entry, len(L_EXITS), axis=1), (1, len(L_STOP)))

    def trades(self, s: dict, hi=END_MS):
        k = (L_STOP.index(s["stop"]) * 2 + ["M", "R"].index(s["entry"])) * len(L_EXITS) \
            + L_EXITS.index(s["exit"])
        c = self.c
        ok = (c["tf"] == L_TRIG.index(s["trig"])) & (c["lvl"] == L_LVL.index(s["lvl"]))
        ok &= (c["confirm"] == s["confirm"]) & ~np.isnan(self.m["net"][:, k])
        if s["reclaim"] == "same":
            ok &= c["same"]
        if s["coins"] == "BTC":
            ok &= c["coin"] == 0
        if s["trend"] == "with":
            ok &= c["trend"] == c["sign"]
        idx = np.flatnonzero(ok)
        idx = idx[np.argsort(self.m["entry_ms"][idx, k], kind="stable")]
        return one_per_coin([(self, i, k) for i in idx], START_MS, hi)


def l_settings():
    names = list(L_DIMS)
    for values in itertools.product(*L_DIMS.values()):
        s = dict(zip(names, values, strict=True))
        if L_TRIG.index(s["trig"]) == 3 and s["lvl"] == "4h":
            continue
        yield s


def l_key(s):
    return tuple(s[n] for n in L_DIMS)


def l_neighbours(s):
    tp1, be, trail = s["exit"].split("|")
    out = [{**s, d: v} for d in ("reclaim", "confirm", "entry", "trend") for v in L_DIMS[d] if v != s[d]]
    seq = L_STOP
    out += [{**s, "stop": seq[x]} for x in (seq.index(s["stop"]) - 1, seq.index(s["stop"]) + 1)
            if 0 <= x < 3]
    out.append({**s, "exit": f"{tp1}|{1 - int(be)}|{trail}"})
    for name, value, seq in (("tp1", tp1, ["0.75", "1", "1.5"]), ("trail", trail, ["3", "5", "8"])):
        for x in (seq.index(value) - 1, seq.index(value) + 1):
            if 0 <= x < 3:
                parts = {"tp1": tp1, "trail": trail, name: seq[x]}
                out.append({**s, "exit": f"{parts['tp1']}|{be}|{parts['trail']}"})
    return out


def l_table(lv, names):
    hi = max(PERIODS[n][1] for n in names)
    table = {}
    for s in l_settings():
        taken = lv.trades(s, hi=hi)
        table[l_key(s)] = {n: stats(period(taken, n), *PERIODS[n]) for n in names}
    return table


def l_label(s):
    return " ".join(f"{k}={v}" for k, v in s.items())


def lvl_select(root):
    lv = Levels(root)
    table = l_table(lv, ("is", "y1", "y2"))
    print(f"## SFP at levels: {len(lv.c['coin'])} patterns, {len(table)} settings (in-sample only)")
    live = [s for s in table.values() if s["is"]["n"] >= 50]
    print(f"positive in-sample R per trade (>= 50 trades): {sum(s['is']['avg'] > 0 for s in live)}/{len(live)}")
    print(f"\nspec design (same-candle reclaim, 1-tick stop, market entry, TP1 1R, BE, trail 5)\n  {HEAD}")
    for coins in ("all", "BTC"):
        for trig in L_TRIG:
            for lvl in L_LVL:
                key = l_key({**L_BASE, "coins": coins, "trig": trig, "lvl": lvl})
                if key in table:
                    print(f"  {coins:3s} {trig:3s} {lvl:2s}  {fmt(table[key]['is'])}")
    print("\neffect of each idea: mean in-sample R per trade over all settings with >= 50 trades")
    for dim, values in L_DIMS.items():
        pos = list(L_DIMS).index(dim)
        cells = []
        for v in values:
            a = [s["is"]["avg"] for k, s in table.items() if k[pos] == v and s["is"]["n"] >= 50]
            cells.append(f"{v}: {np.mean(a):+.3f} ({len(a)})" if a else f"{v}: n/a")
        print(f"  {dim:8s} " + "  ".join(cells))
    scored = []
    for s in l_settings():
        me = table[l_key(s)]
        near = [table[l_key(x)]["is"] for x in l_neighbours(s)]
        score = float(np.mean([me["is"]["avg"], *[x["avg"] for x in near if x["n"] >= 30]]))
        need = 4 if s["coins"] == "all" else 1
        ok = (me["is"]["pm"] >= need and me["is"]["dd"] >= -20 and me["y1"]["avg"] > 0
              and me["y2"]["avg"] > 0 and min(me["y1"]["n"], me["y2"]["n"]) >= 10)
        scored.append((score, ok, s))
    scored.sort(key=lambda x: -x[0])
    chosen = {}
    for coins in ("all", "BTC"):
        top = [x for x in scored if x[1] and x[2]["coins"] == coins][:10]
        print(f"\ntop eligible settings, coins={coins}")
        for score, _, s in top:
            me = table[l_key(s)]
            print(f"  {score:+.3f} {l_label(s)}\n         {fmt(me['is'])} y1 {me['y1']['avg']:+.3f} y2 {me['y2']['avg']:+.3f}")
        chosen[coins] = top[0][2] if top else None
        if not top:
            print("  none eligible")
    json.dump(chosen, open(os.path.join(root, "out", "selection_levels.json"), "w"), indent=1)
    print("\nselection:", json.dumps(chosen))


def lvl_final(root):
    lv = Levels(root)
    chosen = json.load(open(os.path.join(root, "out", "selection_levels.json")))
    print(f"## SFP at levels\n\n{SBS_HEAD}")
    for coins in ("all", "BTC"):
        for trig in L_TRIG:
            for lvl in L_LVL:
                s = {**L_BASE, "coins": coins, "trig": trig, "lvl": lvl}
                if not (trig == "4h" and lvl == "4h"):
                    side_by_side(f"spec design {coins} {trig} at {lvl}", lv.trades(s))
    for coins, s in chosen.items():
        if not s:
            print(f"\nno eligible setting for coins={coins}")
            continue
        taken = lv.trades(s)
        print(f"\nchosen ({coins}): {l_label(s)}\n\n{SBS_HEAD}")
        side_by_side("chosen", taken)
        a, b = both(taken, funding=True)
        print(f"\nwith estimated funding: IS {a['net']:+.1f}R, blind {b['net']:+.1f}R")
        print(f"\n{SBS_HEAD}")
        breakdown(lv, taken)
        print(f"\none change at a time\n\n{SBS_HEAD}")
        for dim, values in L_DIMS.items():
            for v in values:
                if v != s[dim] and not ({**s, dim: v}["trig"] == "4h" and {**s, dim: v}["lvl"] == "4h"):
                    side_by_side(f"{dim}={v}", lv.trades({**s, dim: v}))
    table = l_table(lv, ("is", "blind"))
    pairs = [(s["is"]["avg"], s["blind"]["avg"]) for s in table.values()
             if s["is"]["n"] >= 50 and s["blind"]["n"] >= 25]
    x, y = np.array(pairs).T
    rank = lambda v: np.argsort(np.argsort(v))  # noqa: E731
    print(f"\nall {len(pairs)} settings: rank correlation in-sample vs blind {np.corrcoef(rank(x), rank(y))[0, 1]:+.2f}; "
          f"positive in-sample {np.mean(x > 0):.0%}, blind {np.mean(y > 0):.0%}, both {np.mean((x > 0) & (y > 0)):.0%}")


if __name__ == "__main__":
    if sys.argv[2] == "select" and len(sys.argv) > 3:
        select(sys.argv[1], sys.argv[3].split(","))
    else:
        {"select": select, "final": final, "lvl-select": lvl_select,
         "lvl-final": lvl_final}[sys.argv[2]](sys.argv[1])
