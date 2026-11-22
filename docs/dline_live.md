# D-line live (d_line/v2)

Owner spec 2026-11-22 (Sofien). Purpose: collect data on BloFin DEMO, refine later from stats.
Code: `backend/src/app/strategy_brain/dline/live.py` (pure rules), `dline/runtime.py`
(scanner + assessment). The research draft `d_line/v1` (`docs/dline_detection.md`) stays
non-executable; nothing in v2 uses Fib, EMA, ribbons or moving averages.

## Rules (long; shorts mirror every price)

1. Timeframes 15m, 30m, 1h. Closed candles only; a candle still forming is ignored.
2. Trendline through confirmed pivot highs, descending. At least 2 touches, 3 preferred
   (2 touches multiply confidence by `two_touch_quality_factor`). No candle closed above
   the line between its start and the trigger.
3. Slope angle, deterministic: `angle = degrees(atan((|slope| / ATR) / atr_per_bar_at_45_degrees))`.
   `slope` is price change per trigger candle, `ATR` the Wilder ATR (`atr_period`) of the
   candles before the trigger. A line moving 0.2 ATR per candle is 45 degrees (the BTC 15m
   example chart's 45-degree line moved about 0.17 ATR per candle). Accepted band:
   `ideal_angle_degrees +/- angle_tolerance_degrees` (45 +/- 15). Confidence is 1 up to 45
   degrees and falls linearly to `confidence_at_band_edge` at 60.
4. Trigger: a candle BODY close above the line (wicks never count). The line is frozen at
   the trigger (`breakout_level` = line price at that candle).
5. Bigger trend on the next timeframe up (15m->1h, 30m->2h, 1h->4h), closed candles only:
   the last two confirmed pivot highs and lows must both be rising (longs) / falling (shorts).
6. Stop: structure low (lowest low from the line start to the trigger) minus
   `stop_buffer_ticks`.
7. Targets: TP1 15% at 50% of the way from entry to the structure high (line start); TP2
   50% at the structure high; TP3 20% at the latest higher-timeframe swing high beyond TP2
   (if none, its 20% is added to TP2); runner 15% (closed-candle trail, shared partial
   exits). After TP1 fills, the stop moves to break-even plus fees (shared partial exits).
8. Reward/risk to TP2 at least 2, checked at the signal and again at the executable quote.
9. Attempts: max 2 per line. A first attempt stopped at the structure stop ends the line.
   A second attempt needs the first closed at break-even after TP1, the structure low
   intact, a close back at/through the breakout level, then a new body close beyond it.
   Attempts are simulated on the following closed candles (stop first on a shared candle).
10. Entry `split` (default): `breakout_fraction` at the breakout (next open), the rest as a
    limit at the line for `retest_expiry_bars`. **On BloFin demo only the breakout half is
    sent today** (demo entries are market orders); the retest half is recorded in the plan
    notes. `breakout` sends the full size; `retest` is refused on demo.

## Where it runs

Governed BloFin demo only, with `GOVERNED_DEMO_DATA_MODE_ENABLED` (which needs
`RISK_MODEL_V2_ENABLED` and real trading off) and `GOVERNED_DEMO_PARTIAL_EXITS_ENABLED`.
Internal paper refuses it (`dline_execution_demo_only`). Kill switch, worker pause, the v2
daily stop, one position per coin and every other demo check apply unchanged.
