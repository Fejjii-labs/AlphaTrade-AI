"""SFP at significant levels (Sofien's rules), one-year replay with stepped exits.

Levels: the previous completed 4h / 8h / daily candle's high and low (UTC blocks),
active during the next block. One trade per level per block (first sweep only).
Trigger candles: 15m or 1h.
- Long: a candle trades below the level (sweep) and closes back above it (reclaim).
  Variant "2bar": the reclaim close may come on the next candle instead.
- Entry: next candle's open (market). Stop: just beyond the sweep wick (1 tick).
- Exits: TP1 = 1R (15%), then stop to break-even; TP2 = the opposite level of the
  previous block (50%); TP3 = the opposite extreme of the last 3 blocks (20%, merged
  into TP2 when not beyond it); runner 15% trailing the last 5 candles.
  A signal whose TP2 is not beyond TP1 is kept but flagged (tp2_beyond_tp1 False).
- Fees: BloFin taker 0.06% on entry and every exit. Ambiguous candles settled on 1m.
- The live fee rule (stop at least ~0.6% from the entry) is recorded as fee_ok.

Usage: python sfp_levels_study.py <csv_dir> <csv_1m_dir> <out.json> <SYMBOL>
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dline_1m_study import Minutes, load_minutes, simulate  # noqa: E402
from dline_retest_study import TICK, load_bars  # noqa: E402

from app.schemas.common import Timeframe, TradeDirection  # noqa: E402

TAKER = Decimal("0.0006")
ZONE = Decimal("0.001")
BLOCKS = {"4h": 4, "8h": 8, "1d": 24}


def blocks(h1, hours):
    """UTC-aligned blocks of ``hours`` built from 1h bars: (start, end, high, low)."""
    out, cur = [], None
    for b in h1:
        start = b.interval_start.replace(minute=0, second=0, microsecond=0)
        start = start.replace(hour=(start.hour // hours) * hours)
        if cur is None or cur[0] != start:
            if cur is not None:
                out.append(tuple(cur))
            cur = [start, start + timedelta(hours=hours), b.high, b.low]
        else:
            cur[2] = max(cur[2], b.high)
            cur[3] = min(cur[3], b.low)
    return out


def fee_ok(entry, stop, long):
    upper, lower = entry * (1 + ZONE), entry * (1 - ZONE)
    near = lower if long else upper
    return not ((upper + stop) * TAKER > abs(near - stop) / 5)


def run(symbol, trigger, level_tf, variant, bars, blk, minutes):
    tick = TICK[symbol]
    rows = []
    bi = 0
    used: set[tuple] = set()
    pending = None  # (direction, level, sweep_extreme, index) for the 2bar variant
    for i, bar in enumerate(bars[:-1]):
        while bi + 1 < len(blk) and blk[bi + 1][0] <= bar.interval_start:
            bi += 1
        if bi < 3 or blk[bi][0] > bar.interval_start:
            continue
        prev = blk[bi - 1]
        last3 = blk[bi - 3: bi]
        candidates = []
        if pending and pending[3] == i - 1:
            d, level, ext, _ = pending
            if (d == "long" and bar.close > level) or (d == "short" and bar.close < level):
                ext = min(ext, bar.low) if d == "long" else max(ext, bar.high)
                candidates.append((d, level, ext))
        pending = None
        for d, level in (("long", prev[3]), ("short", prev[2])):
            key = (bi, d)
            if key in used:
                continue
            swept = bar.low < level if d == "long" else bar.high > level
            if not swept:
                continue
            reclaimed = bar.close > level if d == "long" else bar.close < level
            if reclaimed:
                candidates.append((d, level, bar.low if d == "long" else bar.high))
            elif variant == "2bar":
                pending = (d, level, bar.low if d == "long" else bar.high, i)
            used.add(key)
        for d, level, ext in candidates:
            long = d == "long"
            s = Decimal(1) if long else Decimal(-1)
            entry = bars[i + 1].open
            stop = ext - tick if long else ext + tick
            risk = (entry - stop) * s
            if risk <= 0:
                continue
            tp1 = entry + s * risk
            tp2 = prev[2] if long else prev[3]
            tp3 = max(b[2] for b in last3) if long else min(b[3] for b in last3)
            tp2_ok = (tp2 - tp1) * s > 0
            if not tp2_ok:
                tp2 = entry + s * 2 * risk  # flagged; keeps the ladder ordered
            targets = [SimpleNamespace(price=tp1, fraction=Decimal("0.15"))]
            if (tp3 - tp2) * s > 0:
                targets += [SimpleNamespace(price=tp2, fraction=Decimal("0.5")),
                            SimpleNamespace(price=tp3, fraction=Decimal("0.2"))]
            else:
                targets += [SimpleNamespace(price=tp2, fraction=Decimal("0.7"))]
            sig = SimpleNamespace(direction=TradeDirection.LONG if long else TradeDirection.SHORT,
                                  stop=stop, targets=targets)
            res = simulate(bars, sig, entry, i + 1, retest=False, maker_entry=False, minutes=minutes)
            if res is None:
                continue
            rows.append({
                "symbol": symbol, "trigger": trigger, "level_tf": level_tf, "variant": variant,
                "direction": d, "entry_at": bars[i + 1].interval_start.isoformat(),
                "stop_pct": float(risk / entry * 100), "rr_tp2": float((tp2 - entry) * s / risk),
                "tp2_beyond_tp1": tp2_ok, "fee_ok": fee_ok(entry, stop, long),
                "bars_held": res["bars_held"], "net_r": res["net_r"], "gross_r": res["gross_r"],
            })
    return rows


def main() -> None:
    csv_dir, csv_1m, out, symbol = sys.argv[1:5]
    minutes = Minutes(*load_minutes(csv_1m, symbol))
    h1 = load_bars(csv_dir, symbol, Timeframe.H1)
    rows = []
    for trigger, tf in (("15m", Timeframe.M15), ("1h", Timeframe.H1)):
        bars = load_bars(csv_dir, symbol, tf)
        for level_tf, hours in BLOCKS.items():
            blk = blocks(h1, hours)
            for variant in ("1bar", "2bar"):
                part = run(symbol, trigger, level_tf, variant, bars, blk, minutes)
                rows += part
                print(f"{symbol} {trigger} {level_tf} {variant}: {len(part)} trades", flush=True)
    with open(out, "w") as handle:
        json.dump(rows, handle)
    print(f"done {symbol}: {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
