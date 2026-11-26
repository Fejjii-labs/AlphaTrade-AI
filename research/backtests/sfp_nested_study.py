"""SFP and Nested one-year replay, simulated as the live governed BloFin demo loop trades.

Signals come from the bot's own detectors with the live staging parameters:
- Nested: detect_nested on a rolling 256-bar window (as live); trade when the newest
  event is CONFIRMED on the newest bar. Stop = events[-1].stop, target = targets[0].
- SFP: detect_sfp over overlapping segments with atr_multiple_floor 0.5 (as live);
  trade the confirmation event (state CONFIRMED, non-provisional, confirmed on this
  bar; ties: smallest setup_id). Entry/stop/target from its trade_plan.

Execution model (live demo rules, governed_blofin_demo.py):
- market entry at the next bar's open (stand-in for the BloFin book), stop/target
  rounded to tick (long floor, short ceil), strict stop < entry < target;
- SFP: reward >= risk from the entry (_sfp_quote_refusal);
- zone = entry +/- 10 bps; reward >= risk measured from the adverse zone edge;
- fee gate (risk v2): (zone_upper + stop) * 0.0006 > |near_edge - stop| / 5 refuses;
- full-size TP/SL at the venue (no ladder, no break-even); taker 0.06% both sides;
- an ambiguous candle (stop and target both touched, incl. the entry candle) is
  settled with its 1-minute bars; a 1-minute bar touching both counts the stop.
Every geometry-valid signal is simulated and flagged live_ok, so variants can be
compared; one-position-per-coin is applied in the analysis per variant.

Usage: python sfp_nested_study.py <csv_dir> <csv_1m_dir> <out.json> <SYMBOL> <family> <tfs>
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dline_1m_study import Minutes, load_minutes  # noqa: E402
from dline_retest_study import TICK, load_bars  # noqa: E402

from app.market_contracts.enums import FreshnessState  # noqa: E402
from app.market_contracts.first_slice import first_slice_identity  # noqa: E402
from app.market_contracts.identity import binance_usdm_perpetual  # noqa: E402
from app.market_contracts.observation import observation_from_ohlcv  # noqa: E402
from app.schemas.common import Timeframe, TradeDirection  # noqa: E402
from app.schemas.nested_continuation import (  # noqa: E402
    BrainSetupState,
    NestedContinuationSpec,
    NestedParameters,
)
from app.strategy_brain.detector import detect_nested  # noqa: E402
from app.strategy_brain.sfp.contracts import SfpParameters, SfpSpec  # noqa: E402
from app.strategy_brain.sfp.detector import detect_sfp  # noqa: E402

TAKER = Decimal("0.0006")
ZONE = Decimal("0.001")
WINDOW = 256
TF = {"15m": Timeframe.M15, "30m": Timeframe.M30, "1h": Timeframe.H1, "4h": Timeframe.H4}

NESTED_PARAMS = {
    "version": "nested-research/v1", "provisional": True, "pivot_sensitivity": 2,
    "minimum_impulse": "0.005", "retracement_depth": "0.20", "maximum_retracement": "0.75",
    "retracement_duration": 12, "structural_invalidation": "0", "confirmation_window": 24,
    "relative_volume_threshold": "0", "entry_trigger": "closed_break_of_impulse_extreme",
    "max_sequence_bars": 96,
}
SFP_SWEEP = os.environ.get("SFP_MIN_SWEEP", "0.01")
SFP_PARAMS = {
    "version": "sfp-research/v1", "provisional": True, "level_lookback": 6, "pivot_width": 1,
    "minimum_level_significance": "2", "minimum_sweep_depth": SFP_SWEEP,
    "maximum_sweep_depth": "0.05", "equal_level_tolerance": "0.001", "reclaim_window": 2,
    "confirmation_window": 2, "breakout_confirmation_closes": 2,
    "structural_invalidation_buffer": "0.005", "expiry_bars": 6,
    "required_evidence_max_age_bars": 1, "quality_lookback": 3,
    "htf_alignment_tolerance": "0.01", "stop_buffer": "0.001", "min_stop_ticks": 1,
    "min_stop_atr_multiple": "0", "confirmation": "closed_break_of_reclaim_extreme",
    "approach_proximity": None, "approach_cooldown_seconds": 900,
}


def _round(value: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick


def gates(direction, entry, stop, target, family):
    long = direction is TradeDirection.LONG
    s = Decimal(1) if long else Decimal(-1)
    risk = (entry - stop) * s
    reward = (target - entry) * s
    out = {"geometry": risk > 0 and reward > 0}
    if not out["geometry"]:
        return out, None, None
    out["sfp_quote"] = family != "sfp" or reward >= risk
    upper, lower = entry * (1 + ZONE), entry * (1 - ZONE)
    adverse = upper if long else lower
    near = lower if long else upper
    out["rr_zone"] = (target - adverse) * s >= (adverse - stop) * s
    out["fee_gate"] = not ((upper + stop) * TAKER > abs(near - stop) / 5)
    return out, risk, reward


def simulate(bars, minutes, fill, direction, entry, stop, target):
    long = direction is TradeDirection.LONG
    s = Decimal(1) if long else Decimal(-1)
    fs, ft = float(stop), float(target)
    exit_price = None
    exit_index = None
    resolved = 0
    for k in range(fill, len(bars)):
        bar = bars[k]
        adverse = bar.low if long else bar.high
        favorable = bar.high if long else bar.low
        hit_stop = (adverse - stop) * s <= 0
        hit_target = (favorable - target) * s >= 0
        if hit_stop and hit_target:
            window = minutes.window(bar)
            exit_price = stop  # conservative fallback
            if window is not None:
                resolved += 1
                for high, low in window:
                    m_adv, m_fav = (low, high) if long else (high, low)
                    if (m_adv - fs) * float(s) <= 0:
                        exit_price = stop
                        break
                    if (m_fav - ft) * float(s) >= 0:
                        exit_price = target
                        break
            exit_index = k
            break
        if hit_stop:
            exit_price, exit_index = stop, k
            break
        if hit_target:
            exit_price, exit_index = target, k
            break
    open_at_end = exit_price is None
    if open_at_end:
        exit_price, exit_index = bars[-1].close, len(bars) - 1
    risk = (entry - stop) * s
    gross = (exit_price - entry) * s
    fees = (entry + exit_price) * TAKER
    return {
        "gross_r": float(gross / risk),
        "net_r": float((gross - fees) / risk),
        "exit": "target" if exit_price == target else "stop" if exit_price == stop else "open",
        "exit_at": bars[exit_index].interval_end.isoformat(),
        "bars_held": exit_index - fill,
        "resolved_1m": resolved,
        "open_at_end": open_at_end,
    }


def row_for(family, symbol, tf, direction, bars, minutes, t, stop_raw, target_raw, extra):
    if t + 1 >= len(bars):
        return None
    tick = TICK[symbol]
    long = direction is TradeDirection.LONG
    entry = bars[t + 1].open
    stop = _round(stop_raw, tick, up=not long)
    target = _round(target_raw, tick, up=not long)
    checks, risk, reward = gates(direction, entry, stop, target, family)
    row = {
        "family": family, "symbol": symbol, "timeframe": tf, "direction": direction.value,
        "detected_at": bars[t].interval_end.isoformat(), "entry_at": bars[t + 1].interval_start.isoformat(),
        **extra, **{f"gate_{k}": v for k, v in checks.items()},
    }
    if not checks["geometry"]:
        row["live_ok"] = False
        return row
    row["live_ok"] = all(checks.values())
    row["fee_ok"] = checks["fee_gate"]
    row["stop_pct"] = float(risk / entry * 100)
    row["planned_rr"] = float(reward / risk)
    row["trade"] = simulate(bars, minutes, t + 1, direction, entry, stop, target)
    if family == "sfp":
        # Alternative fixed-R targets on the same entry and stop (refinement test).
        s_ = Decimal(1) if long else Decimal(-1)
        row["alt"] = {}
        for k in ("1", "1.5", "2", "3"):
            alt = _round(entry + s_ * Decimal(k) * risk, tick, up=not long)
            row["alt"][k] = simulate(bars, minutes, t + 1, direction, entry, stop, alt)
    return row


def nested_rows(symbol, tf, bars, minutes):
    rows = []
    for direction in (TradeDirection.LONG, TradeDirection.SHORT):
        spec = NestedContinuationSpec(
            symbol=symbol, trigger_timeframe=TF[tf], direction=direction,
            parameters=NestedParameters(**NESTED_PARAMS),
        )
        t0 = datetime.now()
        count = 0
        for t in range(WINDOW - 1, len(bars) - 1):
            window = bars[t - WINDOW + 1 : t + 1]
            events = detect_nested(window, spec, evaluated_at=window[-1].interval_end)
            if not events:
                continue
            last = events[-1]
            if last.state is not BrainSetupState.CONFIRMED or last.confirmed_index != WINDOW - 1:
                continue
            if last.stop is None or not last.targets:
                continue
            count += 1
            row = row_for("nested", symbol, tf, direction, bars, minutes, t, last.stop,
                          last.targets[0], {"stage": last.stage})
            if row:
                rows.append(row)
        print(f"nested {symbol} {tf} {direction.value}: {count} signals, "
              f"{(datetime.now() - t0).seconds}s", flush=True)
    return rows


def sfp_rows(symbol, tf, bars, minutes, identity):
    observations = tuple(
        observation_from_ohlcv(
            bar, identity=identity, observed_at=bar.interval_end, receive_time=bar.interval_end,
            freshness_state=FreshnessState.FRESH,
        ).model_copy(update={"recorded_at": bar.interval_end})
        for bar in bars
    )
    index = {bar.source_event_id: i for i, bar in enumerate(bars)}
    rows = []
    segment, warm = 1500, 300
    for direction in (TradeDirection.LONG, TradeDirection.SHORT):
        spec = SfpSpec(symbol=symbol, trigger_timeframe=TF[tf], direction=direction,
                       parameters=SfpParameters(**SFP_PARAMS))
        t0 = datetime.now()
        found: dict[int, object] = {}
        start = 0
        while start < len(bars) - 1:
            lo = max(0, start - warm)
            hi = min(len(bars), start + segment)
            seg_bars, seg_obs = bars[lo:hi], observations[lo:hi]
            scan = detect_sfp(
                seg_bars, seg_obs, spec, evaluated_at=seg_bars[-1].interval_end,
                tick_size=TICK[symbol], atr_multiple_floor=Decimal("0.5"),
            )
            for e in scan.events:
                if (
                    e.state is BrainSetupState.CONFIRMED
                    and not e.provisional
                    and e.confirmation_observation_id == e.evidence.observation_id
                    and e.trade_plan is not None
                ):
                    t = index.get(e.evidence.source_event_id)
                    if t is None or t < start:
                        continue
                    best = found.get(t)
                    if best is None or str(e.setup_id) < str(best.setup_id):
                        found[t] = e
            start = hi
        for t, e in sorted(found.items()):
            row = row_for("sfp", symbol, tf, direction, bars, minutes, t, e.trade_plan.stop,
                          e.trade_plan.take_profit, {})
            if row:
                rows.append(row)
        print(f"sfp {symbol} {tf} {direction.value}: {len(found)} signals, "
              f"{(datetime.now() - t0).seconds}s", flush=True)
    return rows


def main() -> None:
    csv_dir, csv_1m, out, symbol, family, tfs = sys.argv[1:7]
    minutes = Minutes(*load_minutes(csv_1m, symbol))
    rows = []
    for tf in tfs.split(","):
        bars = load_bars(csv_dir, symbol, TF[tf])
        identity = first_slice_identity(
            timeframe=TF[tf], replay=True, instrument=binance_usdm_perpetual(symbol)
        )
        if family == "nested":
            rows += nested_rows(symbol, tf, bars, minutes)
        else:
            rows += sfp_rows(symbol, tf, bars, minutes, identity)
        with open(out, "w") as handle:
            json.dump(rows, handle)
    print(f"done {family} {symbol}: {len(rows)} rows; 1m used {minutes.used}, "
          f"missing {minutes.missing}", flush=True)


if __name__ == "__main__":
    main()
