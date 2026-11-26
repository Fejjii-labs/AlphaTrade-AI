"""D-line entry-mode study: breakout vs retest vs half-half, on historical klines.

Uses the bot's own detector (detect_dline_live, default dline-live/v2 parameters)
and its own retest-fill rule (simulate_split_entry: a limit at the frozen line
projection, filled only if a later candle trades through it within 6 candles,
never better than that candle's open). Signals are identical in every entry
mode; only the entry differs, so each signal is simulated three ways.

Exit model (same targets and stop for every entry style; conservative):
- stop = the signal's structure stop; TP1 15% / TP2 50% / TP3 20% (or merged) / runner 15%
- after TP1, the stop moves to the entry from the next candle (break-even)
- after the last target, the runner trails the lowest low (highest high for
  shorts) of the last 5 candles; leftovers are marked at the last close
- a candle that touches both the stop and a target counts the stop first
- a retest fill candle credits no target, only the stop (order inside it unknown)
Fees (BloFin): taker 0.06% for market entries and all exits, maker 0.02% for the
retest limit entry. R = PnL / (|entry - stop| * size), so each entry style
risks exactly 1R; half-half sizes each half to 0.5R of its own entry.

Usage: python dline_retest_study.py <csv_dir> <out.json>
CSV files: <SYMBOL>-<interval>-*.csv in Binance kline format (no header or header).
"""

from __future__ import annotations

import csv
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import binance_usdm_perpetual
from app.market_contracts.ohlcv import build_ohlcv_bar
from app.schemas.common import Timeframe, TradeDirection
from app.strategy_brain.dline.live import (
    DlineLiveParameters,
    DlineLiveSpec,
    detect_dline_live,
    htf_timeframe_for,
    simulate_split_entry,
)

TAKER = Decimal("0.0006")
MAKER = Decimal("0.0002")
INTERVAL = {Timeframe.M15: "15m", Timeframe.M30: "30m", Timeframe.H1: "1h", Timeframe.H2: "2h", Timeframe.H4: "4h"}
TICK = {"BTCUSDT": Decimal("0.1"), "ETHUSDT": Decimal("0.01"), "ZECUSDT": Decimal("0.01"),
        "TAOUSDT": Decimal("0.01"), "HYPEUSDT": Decimal("0.001")}


def load_bars(csv_dir: str, symbol: str, timeframe: Timeframe):
    rows = []
    for path in sorted(glob.glob(os.path.join(csv_dir, f"{symbol}-{INTERVAL[timeframe]}-*.csv"))):
        with open(path) as handle:
            for row in csv.reader(handle):
                if not row or not row[0].isdigit():
                    continue
                rows.append(row)
    rows.sort(key=lambda r: int(r[0]))
    identity = first_slice_identity(
        timeframe=timeframe, replay=True, instrument=binance_usdm_perpetual(symbol)
    )
    delta = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}[INTERVAL[timeframe]]
    bars, seen = [], set()
    for r in rows:
        ms = int(r[0])
        if ms > 10**14:  # microseconds in newer archives
            ms //= 1000
        if ms in seen:
            continue
        seen.add(ms)
        start = datetime.fromtimestamp(ms / 1000, tz=UTC)
        o, h, low, c, v = (Decimal(x) for x in r[1:6])
        end = start + timedelta(minutes=delta)
        bars.append(
            build_ohlcv_bar(
                instrument=identity.instrument,
                timeframe=timeframe,
                interval_start=start,
                open_=o, high=h, low=low, close=c,
                base_volume=v, quote_volume=v * c,
                evaluated_at=end, grace=timedelta(0),
                adapter_version=identity.source.adapter_version,
            )
        )
    return tuple(bars)


def simulate(bars, signal, entry: Decimal, fill: int, *, retest: bool, maker_entry: bool):
    """Gross and net R of one entry under the signal's stop/targets ladder."""
    long = signal.direction is TradeDirection.LONG
    s = Decimal(1) if long else Decimal(-1)
    stop = signal.stop
    risk = (entry - stop) * s
    if risk <= 0:
        return None  # entry at/through the stop: no valid trade
    targets = [(t.price, t.fraction) for t in signal.targets]
    remaining = Decimal(1)
    realized = Decimal(0)
    fees = entry * (MAKER if maker_entry else TAKER)
    pending_stop = None
    last_target_done = False
    exit_index = None
    for k in range(fill, len(bars)):
        bar = bars[k]
        if pending_stop is not None:
            stop, pending_stop = pending_stop, None
        adverse = bar.low if long else bar.high
        favorable = bar.high if long else bar.low
        if (adverse - stop) * s <= 0:
            realized += remaining * (stop - entry) * s
            fees += remaining * stop * TAKER
            remaining = Decimal(0)
            exit_index = k
            break
        if retest and k == fill:
            continue
        while targets and (favorable - targets[0][0]) * s >= 0:
            price, fraction = targets.pop(0)
            realized += fraction * (price - entry) * s
            fees += fraction * price * TAKER
            remaining -= fraction
            if (stop - entry) * s < 0:
                pending_stop = entry  # break-even from the next candle
        if not targets:
            last_target_done = True
        if last_target_done and remaining > 0:
            window = bars[max(fill, k - 4): k + 1]
            trail = min(b.low for b in window) if long else max(b.high for b in window)
            base = pending_stop if pending_stop is not None else stop
            if (trail - base) * s > 0:
                pending_stop = trail
        if remaining <= 0:
            exit_index = k
            break
    if remaining > 0:
        close = bars[-1].close
        realized += remaining * (close - entry) * s
        fees += remaining * close * TAKER
    return {
        "gross_r": float(realized / risk),
        "net_r": float((realized - fees) / risk),
        "risk_pct": float(risk / entry * 100),
        "open_at_end": remaining > 0,
        "bars_held": (exit_index or len(bars) - 1) - fill,
    }


def main() -> None:
    csv_dir, out = sys.argv[1], sys.argv[2]
    symbols = sys.argv[3].split(",") if len(sys.argv) > 3 else list(TICK)
    timeframes = [Timeframe.M15, Timeframe.M30, Timeframe.H1]
    base = DlineLiveParameters()
    retest_only = base.model_copy(update={"entry_mode": "retest"})
    results = []
    for symbol in symbols:
        for timeframe in timeframes:
            bars = load_bars(csv_dir, symbol, timeframe)
            htf = load_bars(csv_dir, symbol, htf_timeframe_for(timeframe))
            if len(bars) < 200 or len(htf) < 50:
                print(f"skip {symbol} {timeframe.value}: bars={len(bars)} htf={len(htf)}", flush=True)
                continue
            for direction in (TradeDirection.LONG, TradeDirection.SHORT):
                spec = DlineLiveSpec(symbol=symbol, trigger_timeframe=timeframe, direction=direction)
                t0 = datetime.now()
                scan = detect_dline_live(
                    bars, htf, spec, evaluated_at=bars[-1].interval_end, tick_size=TICK[symbol]
                )
                print(f"{symbol} {timeframe.value} {direction.value}: {len(bars)} bars, "
                      f"{len(scan.signals)} signals, {(datetime.now() - t0).seconds}s", flush=True)
                for sig in scan.signals:
                    i = sig.trigger_index
                    if i + 1 >= len(bars):
                        continue
                    row = {
                        "symbol": symbol, "timeframe": timeframe.value, "direction": direction.value,
                        "detected_at": sig.detected_at.isoformat(), "attempt": sig.attempt,
                        "touches": sig.touch_count, "planned_rr": float(sig.reward_risk),
                    }
                    row["breakout"] = simulate(bars, sig, bars[i + 1].open, i + 1,
                                               retest=False, maker_entry=False)
                    (leg,) = simulate_split_entry(sig, bars, retest_only)
                    row["retest_status"] = leg.status
                    row["retest"] = (
                        simulate(bars, sig, leg.price, leg.filled_index, retest=True, maker_entry=True)
                        if leg.status == "filled" else None
                    )
                    results.append(row)
    with open(out, "w") as handle:
        json.dump(results, handle)
    summarize(results)


def summarize(results) -> None:
    def agg(rows, key):
        vals = [r[key]["net_r"] if r.get(key) else 0.0 for r in rows]
        taken = [r[key] for r in rows if r.get(key)]
        wins = sum(1 for t in taken if t["net_r"] > 0)
        equity = peak = dd = 0.0
        for v in vals:
            equity += v
            peak = max(peak, equity)
            dd = min(dd, equity - peak)
        return {"signals": len(rows), "trades": len(taken), "wins": wins,
                "total_net_r": round(sum(vals), 2),
                "avg_net_r_per_signal": round(sum(vals) / len(rows), 3) if rows else None,
                "avg_net_r_per_trade": round(sum(t["net_r"] for t in taken) / len(taken), 3) if taken else None,
                "max_drawdown_r": round(dd, 2)}

    rows = sorted([r for r in results if r["breakout"]], key=lambda r: r["detected_at"])
    for r in rows:
        b = r["breakout"]["net_r"]
        t = r["retest"]["net_r"] if r["retest"] else 0.0
        r["split"] = {"net_r": 0.5 * b + 0.5 * t}
    filled = [r for r in rows if r["retest"]]
    expired = [r for r in rows if r["retest_status"] == "expired"]
    report = {
        "signals": len(rows),
        "retest_fill_rate": round(len(filled) / len(rows), 3) if rows else None,
        "breakout_only": agg(rows, "breakout"),
        "retest_only": agg(rows, "retest"),
        "half_half": agg(rows, "split"),
        "missed_by_retest": {
            "count": len(expired),
            "breakout_winners_missed": sum(1 for r in expired if r["breakout"]["net_r"] > 0),
            "breakout_losers_avoided": sum(1 for r in expired if r["breakout"]["net_r"] <= 0),
            "breakout_net_r_of_missed": round(sum(r["breakout"]["net_r"] for r in expired), 2),
        },
        "when_both_traded": {
            "count": len(filled),
            "breakout_net_r": round(sum(r["breakout"]["net_r"] for r in filled), 2),
            "retest_net_r": round(sum(r["retest"]["net_r"] for r in filled), 2),
        },
        "by_timeframe": {},
    }
    groups = defaultdict(list)
    for r in rows:
        groups[r["timeframe"]].append(r)
    for tf, g in sorted(groups.items()):
        report["by_timeframe"][tf] = {
            "signals": len(g),
            "retest_fill_rate": round(sum(1 for r in g if r["retest"]) / len(g), 3),
            "breakout_total_net_r": agg(g, "breakout")["total_net_r"],
            "retest_total_net_r": agg(g, "retest")["total_net_r"],
            "half_half_total_net_r": agg(g, "split")["total_net_r"],
        }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
