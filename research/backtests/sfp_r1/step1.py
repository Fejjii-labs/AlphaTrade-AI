"""Step 1: how the default HTF SFP fails on BTC in the design years, and the bot's SFP.

Default: 15m execution, previous daily and weekly highs/lows, entry a, stop b, default exits.
Writes <out>/step1.json and the PNG gallery.

Usage: python -I step1.py <data_root> <chart_dir> <out_dir>
The bot's SFP rows come from ``../sfp_nested_study.py`` (live parameters, 0.3 % minimum
sweep, 15m): <data_root>/out/sn_sfp_BTCUSDT.json, or sn_sfp_BTCUSDT_design.json when the
replay was run on the design-year candles only (Oct 2023 warm-up to Oct 2025).
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import data as D  # noqa: E402
import engine  # noqa: E402
from bt3y_common import simulate  # noqa: E402
from classify import REASONS, classify  # noqa: E402
from gallery import chart  # noqa: E402
from stats import summary  # noqa: E402

N_LOSERS, N_WINNERS = 13, 5


def pick_evenly(rows, n):
    if len(rows) <= n:
        return rows
    idx = np.linspace(0, len(rows) - 1, n).round().astype(int)
    return [rows[i] for i in idx]


def bot_sfp(d, root):
    paths = [os.path.join(root, "out", f"sn_sfp_BTCUSDT{x}.json") for x in ("", "_design")]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        return [], {}, 0
    path = paths[0]
    rows = [r for r in json.load(open(path)) if r["timeframe"] == "15m"]
    b = d.bars["15m"]
    trades, refused, busy = [], Counter(), 0
    busy_until = 0
    for r in sorted(rows, key=lambda x: x["entry_at"]):
        t_in = int(datetime.fromisoformat(r["entry_at"]).timestamp() * 1000)
        if not (D.DESIGN[0] <= t_in < D.DESIGN[1]):
            continue
        if not r.get("live_ok"):
            if r.get("gate_geometry") is False:
                refused["geometry"] += 1
            elif not (r.get("gate_sfp_quote") and r.get("gate_rr_zone")):
                refused["reward_below_risk"] += 1
            elif not r.get("gate_fee_gate"):
                refused["fees"] += 1
            continue
        if t_in < busy_until:
            busy += 1
            continue
        i = int(np.searchsorted(b["t"], t_in))
        s = 1 if r["direction"] == "long" else -1
        entry = float(b["o"][i])
        risk = r["stop_pct"] / 100 * entry
        stop = round((entry - s * risk) / d.tick) * d.tick
        target = round((entry + s * r["planned_rr"] * risk) / d.tick) * d.tick
        res = simulate(d.M, b, "15m", s, t_in, entry, stop, [(target, 1.0)], be=False,
                       trail=None, funding=d.funding, slip_bps=engine.SLIP_BPS)
        t = {"sym": "BTCUSDT", "sign": s, "entry_ms": t_in, "entry": entry, "stop": stop,
             "ladder": [(target, 1.0)], "stop_pct": r["stop_pct"], **engine._result(res)}
        busy_until = t["exit_ms"]
        trades.append(t)
    return trades, dict(refused), busy


def main() -> None:
    root, chart_dir, out_dir = sys.argv[1:4]
    os.makedirs(chart_dir, exist_ok=True)
    d = D.Data(root, "BTCUSDT")
    res = engine.run(d, {}, D.DESIGN, keep_refused=True)
    trades = res["trades"]
    for t in trades:
        t["reason"] = classify(d.M, t) if t["r"] < 0 else None
    losses = [t for t in trades if t["r"] < 0]
    wins = [t for t in trades if t["r"] > 0]
    reasons = Counter(t["reason"] for t in losses)
    # gallery: losers spread over reasons (in proportion) and over time, winners over time
    pick = []
    for key, cnt in reasons.most_common():
        k = max(1, round(N_LOSERS * cnt / len(losses)))
        pick += pick_evenly([t for t in losses if t["reason"] == key], k)
    pick = sorted(pick[:15], key=lambda t: t["entry_ms"])
    gallery = []
    for n, t in enumerate(pick, 1):
        name = f"loss_{n:02d}.png"
        chart(d, t, os.path.join(chart_dir, name),
              f"Loss {n}: {D.tunis(t['entry_ms'])} Tunis, BTC {t['level']}, "
              f"{'long' if t['sign'] > 0 else 'short'}", REASONS[t["reason"]])
        gallery.append({"file": name, **t})
    for n, t in enumerate(pick_evenly(wins, N_WINNERS), 1):
        name = f"win_{n:02d}.png"
        chart(d, t, os.path.join(chart_dir, name),
              f"Win {n}: {D.tunis(t['entry_ms'])} Tunis, BTC {t['level']}, "
              f"{'long' if t['sign'] > 0 else 'short'}")
        gallery.append({"file": name, **t})
    # refused signals: what they would have done
    ref = res["refused"]
    ref_sum = summary(ref, D.DESIGN) if ref else None
    # the live bot's SFP on the same years
    bot, bot_ref, bot_busy = bot_sfp(d, root)
    for t in bot:
        t["reason"] = classify(d.M, t) if t["r"] < 0 else None
    out = {
        "default": {"summary": summary(trades, D.DESIGN), "counts": res["counts"],
                    "reasons": dict(reasons), "refused_summary": ref_sum,
                    "refused_by": dict(Counter(t["refused"] for t in ref))},
        "bot": {"summary": summary(bot, D.DESIGN), "refused": bot_ref, "busy": bot_busy,
                "reasons": dict(Counter(t["reason"] for t in bot if t["r"] < 0))},
        "gallery": gallery,
        "trades": trades,
        "bot_trades": bot,
    }
    json.dump(out, open(os.path.join(out_dir, "step1.json"), "w"), default=float)
    print(json.dumps({k: out[k] for k in ("default", "bot")}, indent=1, default=float))


if __name__ == "__main__":
    main()
