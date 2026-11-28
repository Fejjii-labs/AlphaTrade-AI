"""Run one variant on one coin: signals -> filters -> entry -> stop -> exits -> live checks
-> one position per coin -> 1-minute simulation with all costs.

A variant is a dict (see ``frozen_variants.json``):
  exec      "15m" | "1h"            execution chart
  levels    ["1d", "1w", ...]       level timeframes (from 4h, 1d, 1w, 1M)
  equal     bool                    add equal highs/lows on those timeframes
  life      "prev" | "naked"        level life (levels.py)
  reclaim   "next" | "same"         reclaim on the sweep candle or the one after
  entry     "a" | "b" | "c"         entries.py
  stop      "a" | "b" | "c"         stops.py
  exit      "default" | "range"     exits.py
  vol       bool                    sweep or reclaim volume >= 1.5x the 20-candle mean
  conf      int                     minimum confluence score (1 = no filter)
Costs: 0.06 % taker / 0.02 % maker per side, 5 bps slippage per side on market entries and
on every stop (incl. the runner trail), funding from Binance rates; ambiguous minutes
settle stop first (``bt3y_common.simulate``).
"""

from __future__ import annotations

import numpy as np

from bt3y_common import MIN, live_checks, simulate
from data import HTF_OF, TF_MIN
from entries import ENTRIES
from exits import TRAIL, ladder, target_levels
from levels import LEVEL_TFS, build_levels
from signals import find_signals
from stops import stop_price

SLIP_BPS = 5.0
VOL_MULT = 1.5
DEFAULT = {"exec": "15m", "levels": ["1d", "1w"], "equal": False, "life": "prev",
           "reclaim": "next", "entry": "a", "stop": "b", "exit": "default", "vol": False,
           "conf": 1}

_cache: dict = {}


def signals_for(data, v: dict):
    key = (data.symbol, v["exec"], tuple(v["levels"]), v["equal"], v["life"], v["reclaim"])
    if key not in _cache:
        levels = build_levels(data, v["levels"], v["life"], v["equal"])
        pkey = (data.symbol, v["life"], "pool")
        if pkey not in _cache:
            _cache[pkey] = build_levels(data, LEVEL_TFS, v["life"], True)
        sigs = find_signals(data, v["exec"], levels, _cache[pkey], v["reclaim"])
        _cache[key] = (sigs, [lv for lv in levels if lv.kind == "eq"])
    return _cache[key]


def ready_index(data, tf: str, sig, stop_kind: str):
    """Candle after which the setup is complete (stop c waits for the HTF candle close)."""
    if stop_kind != "c":
        return sig.r
    b = data.bars[tf]
    hms = TF_MIN[HTF_OF[tf]] * MIN
    close = (int(b["t"][sig.i]) // hms + 1) * hms
    r0 = int(np.searchsorted(b["t"], close - TF_MIN[tf] * MIN, side="left"))
    r0 = max(r0, sig.r)
    if r0 >= len(b["t"]) or (b["c"][r0] - sig.level.price) * sig.sign <= 0:
        return None
    return r0


def run(data, v: dict, period=None, keep_refused: bool = False) -> dict:
    v = {**DEFAULT, **v}
    tf = v["exec"]
    sigs, eqs = signals_for(data, v)
    b = data.bars[tf]
    atr = data.atr[tf]
    trades, refused = [], []
    counts = {"signals": 0, "filtered": 0, "no_entry": 0, "busy": 0, "geometry": 0,
              "reward_below_risk": 0, "fees": 0}
    busy_until = 0
    for sig in sigs:
        t_sig = int(b["t"][sig.r])
        if period and not (period[0] - 86_400_000 <= t_sig < period[1]):
            continue
        if v["vol"] and sig.vol_ratio < VOL_MULT or sig.conf < v["conf"]:
            counts["filtered"] += 1
            continue
        r0 = ready_index(data, tf, sig, v["stop"])
        e = ENTRIES[v["entry"]](data, tf, sig, r0) if r0 is not None else None
        if e is None or (period and not (period[0] <= e["t"] < period[1])):
            counts["no_entry"] += e is None and r0 is not None
            continue
        counts["signals"] += 1
        s = sig.sign
        stop = stop_price(v["stop"], s, e["ext"], float(atr[e["k"]]), data.tick)
        entry = e["price"]
        if stop is None or (entry - stop) * s <= 0:
            counts["geometry"] += 1
            continue
        targets = target_levels(data, v["levels"], eqs, s, e["t"], entry)
        lad = ladder(v["exit"], s, entry, stop, sig, targets, data.tick)
        why = live_checks(s, entry, stop, lad)
        row = {"sym": data.symbol, "sign": s, "level": sig.level.tag, "lvl": sig.level.price,
               "others": sig.others, "conf": sig.conf, "vol": round(sig.vol_ratio, 2),
               "depth": round(sig.depth * 100, 3), "sweep_ms": int(b["t"][sig.i]),
               "reclaim_ms": t_sig, "ext": e["ext"], "entry_ms": e["t"], "entry": entry,
               "stop": stop, "maker": e["maker"], "ladder": lad,
               "stop_pct": round(abs(entry - stop) / entry * 100, 3)}
        if why is not None:
            counts[why] += 1
            if keep_refused:
                row["refused"] = why
                res = simulate(data.M, b, tf, s, e["t"], entry, stop, lad, be=False,
                               trail=TRAIL, maker=e["maker"], fill_minute_stop_only=e["maker"],
                               funding=data.funding, slip_bps=SLIP_BPS)
                row.update(_result(res))
                refused.append(row)
            continue
        if e["t"] < busy_until:
            counts["busy"] += 1
            continue
        res = simulate(data.M, b, tf, s, e["t"], entry, stop, lad, be=False, trail=TRAIL,
                       maker=e["maker"], fill_minute_stop_only=e["maker"],
                       funding=data.funding, slip_bps=SLIP_BPS)
        if res is None:
            continue
        row.update(_result(res))
        busy_until = row["exit_ms"]
        trades.append(row)
    return {"trades": trades, "refused": refused, "counts": counts}


def _result(res: dict) -> dict:
    return {"r": res["net_r"] + res["fund_r"], "net_r": res["net_r"], "fund_r": res["fund_r"],
            "gross_r": res["gross_r"], "exit_ms": res["exit_ms"], "hits": res["hits"],
            "fills": [(int(a), float(f), float(p), bool(st)) for a, f, p, st in res["fills"]]}
