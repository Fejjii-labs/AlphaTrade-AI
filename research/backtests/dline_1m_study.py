"""D-line entry study, refined with 1-minute candles.

Same signals, entries, ladder and fees as dline_retest_study.py. The difference:
when a trigger candle touches both the stop and the next target (or is the retest
fill candle), the order inside it is read from that candle's 1-minute bars instead
of assuming the stop came first. A 1-minute bar that touches both still counts the
stop first. Break-even still applies from the next trigger candle.

Also stores each signal's stop distance and planned R so later cuts need no
re-detection.

Usage: python dline_1m_study.py <csv_dir> <csv_1m_dir> <out.json> <SYMBOL>
"""

from __future__ import annotations

import bisect
import csv
import glob
import json
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dline_retest_study import MAKER, TAKER, TICK, load_bars  # noqa: E402

from app.schemas.common import Timeframe, TradeDirection  # noqa: E402
from app.strategy_brain.dline.live import (  # noqa: E402
    DlineLiveParameters,
    DlineLiveSpec,
    detect_dline_live,
    htf_timeframe_for,
    simulate_split_entry,
)


def load_minutes(csv_dir: str, symbol: str):
    rows = {}
    for path in sorted(glob.glob(os.path.join(csv_dir, f"{symbol}-1m-*.csv"))):
        with open(path) as handle:
            for r in csv.reader(handle):
                if not r or not r[0].isdigit():
                    continue
                ms = int(r[0])
                if ms > 10**14:
                    ms //= 1000
                rows[ms] = (float(r[2]), float(r[3]))  # high, low
    keys = sorted(rows)
    return keys, [rows[k] for k in keys]


class Minutes:
    def __init__(self, keys, values):
        self.keys, self.values = keys, values
        self.used = self.missing = 0

    def window(self, bar):
        start = int(bar.interval_start.timestamp() * 1000)
        end = int(bar.interval_end.timestamp() * 1000)
        i, j = bisect.bisect_left(self.keys, start), bisect.bisect_left(self.keys, end)
        expected = (end - start) // 60000
        if j - i != expected:
            self.missing += 1
            return None
        self.used += 1
        return self.values[i:j]


def simulate(bars, signal, entry: Decimal, fill: int, *, retest: bool, maker_entry: bool, minutes):
    long = signal.direction is TradeDirection.LONG
    s = Decimal(1) if long else Decimal(-1)
    stop = signal.stop
    risk = (entry - stop) * s
    if risk <= 0:
        return None
    targets = [(t.price, t.fraction) for t in signal.targets]
    remaining = Decimal(1)
    realized = Decimal(0)
    fees = entry * (MAKER if maker_entry else TAKER)
    pending_stop = None
    last_target_done = False
    exit_index = None
    resolved = 0

    def take_targets(favorable) -> None:
        nonlocal realized, fees, remaining, pending_stop
        while targets and (favorable - targets[0][0]) * s >= 0:
            price, fraction = targets.pop(0)
            realized += fraction * (price - entry) * s
            fees += fraction * price * TAKER
            remaining -= fraction
            if (stop - entry) * s < 0:
                pending_stop = entry  # break-even from the next trigger candle

    for k in range(fill, len(bars)):
        bar = bars[k]
        if pending_stop is not None:
            stop, pending_stop = pending_stop, None
        adverse = bar.low if long else bar.high
        favorable = bar.high if long else bar.low
        stop_touched = (adverse - stop) * s <= 0
        target_touched = bool(targets) and (favorable - targets[0][0]) * s >= 0
        fill_candle = retest and k == fill
        window = (
            minutes.window(bar) if (stop_touched and target_touched) or fill_candle else None
        )
        if window is not None:
            resolved += 1
            fs = float(stop)
            started = not fill_candle
            stopped = False
            for high, low in window:
                m_adverse, m_favorable = (low, high) if long else (high, low)
                if not started:
                    # The limit fills in the first minute that trades through it;
                    # that minute is ambiguous, so it can only stop.
                    if (m_adverse - float(entry)) * float(s) <= 0:
                        started = True
                        if (m_adverse - fs) * float(s) <= 0:
                            stopped = True
                            break
                    continue
                if (m_adverse - fs) * float(s) <= 0:
                    stopped = True
                    break
                take_targets(Decimal(str(m_favorable)))
            if stopped:
                realized += remaining * (stop - entry) * s
                fees += remaining * stop * TAKER
                remaining = Decimal(0)
                exit_index = k
                break
        else:
            if stop_touched:
                realized += remaining * (stop - entry) * s
                fees += remaining * stop * TAKER
                remaining = Decimal(0)
                exit_index = k
                break
            if not fill_candle:
                take_targets(favorable)
        if not targets:
            last_target_done = True
        if last_target_done and remaining > 0:
            trail_window = bars[max(fill, k - 4): k + 1]
            trail = min(b.low for b in trail_window) if long else max(b.high for b in trail_window)
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
        "resolved_candles": resolved,
    }


def main() -> None:
    csv_dir, csv_1m, out, symbol = sys.argv[1:5]
    minutes = Minutes(*load_minutes(csv_1m, symbol))
    print(f"{symbol}: {len(minutes.keys)} minutes", flush=True)
    retest_only = DlineLiveParameters().model_copy(update={"entry_mode": "retest"})
    results = []
    for timeframe in (Timeframe.M15, Timeframe.M30, Timeframe.H1):
        bars = load_bars(csv_dir, symbol, timeframe)
        htf = load_bars(csv_dir, symbol, htf_timeframe_for(timeframe))
        for direction in (TradeDirection.LONG, TradeDirection.SHORT):
            spec = DlineLiveSpec(symbol=symbol, trigger_timeframe=timeframe, direction=direction)
            t0 = datetime.now()
            scan = detect_dline_live(
                bars, htf, spec, evaluated_at=bars[-1].interval_end, tick_size=TICK[symbol]
            )
            print(f"{symbol} {timeframe.value} {direction.value}: {len(scan.signals)} signals, "
                  f"{(datetime.now() - t0).seconds}s", flush=True)
            for sig in scan.signals:
                i = sig.trigger_index
                if i + 1 >= len(bars):
                    continue
                entry = bars[i + 1].open
                row = {
                    "symbol": symbol, "timeframe": timeframe.value, "direction": direction.value,
                    "detected_at": sig.detected_at.isoformat(), "attempt": sig.attempt,
                    "touches": sig.touch_count, "planned_rr": float(sig.reward_risk),
                    "stop": str(sig.stop), "breakout_close": str(bars[i].close),
                    "targets": [[str(t.price), str(t.fraction)] for t in sig.targets],
                }
                row["breakout"] = simulate(bars, sig, entry, i + 1, retest=False,
                                           maker_entry=False, minutes=minutes)
                (leg,) = simulate_split_entry(sig, bars, retest_only)
                row["retest_status"] = leg.status
                row["retest_price"] = str(leg.price) if leg.price is not None else None
                row["retest"] = (
                    simulate(bars, sig, leg.price, leg.filled_index, retest=True,
                             maker_entry=True, minutes=minutes)
                    if leg.status == "filled" else None
                )
                results.append(row)
    with open(out, "w") as handle:
        json.dump(results, handle)
    print(f"done {symbol}: {len(results)} rows, 1m windows used {minutes.used}, "
          f"missing {minutes.missing}", flush=True)


if __name__ == "__main__":
    main()
