# Brief: 2–3 year strategy backtest (for a Fable 5.2 session)

You are working in the AlphaTrade repo (`Fejjii-labs/AlphaTrade-AI`). Read `AGENTS.md`,
`CLAUDE.md` and `.cursor/rules/*.mdc` first and follow them: `claude/` branch prefix,
no force-push, CI runs locally, never print secrets. Never trade with real money, never
place orders, never deploy, and never change production behaviour in this task.

## Goal

Find, with evidence that holds up out of sample, the best settings for the bot's three
strategies (D-line, Nested Continuation, SFP) on BTC, ETH, ZEC, TAO and HYPE. The target
is **the most net profit per trade after fees with a tolerable drawdown**, not the
highest win rate. Produce a report Sofien can read and share (PDF + Markdown).

## Read first

- `docs/strategies/strategy_spec_v1.md`: the live logic and the agreed target design
  (stepped exits for all three: TP1 about 1R 15% then break-even, TP2 50% and TP3 20% at
  structure levels, 15% runner trailing the last 5 candles; D-line stop at structure; SFP
  at significant levels with the stop beyond the sweep wick).
- `docs/strategies/backtest_report_2026-11.md`: the one-year results and method.
- `research/backtests/README.md` and its scripts: reuse them. They call the bot's own
  detectors (`backend/src/app/strategy_brain/`) and copy the live BloFin demo checks
  (`backend/src/app/services/governed_blofin_demo.py`: 10 bps entry zone, 1:1 reward:risk
  from the zone edge, fee rule, one position per coin) and BloFin fees (0.06% taker).

## Data

`research/backtests/download_klines.py` (Binance USD-M from data.binance.vision). Take
as much history as each coin has, up to 3 years (from November 2023): 1m, 15m, 30m,
1h, 2h, 4h (and 1d/1w if you test those levels). Some coins listed later (HYPE in 2025);
report per coin how much history it has. Keep the data folders outside the code folders.

## Method (important)

1. **Blind test year.** Choose settings only on the earlier data (for example Nov
   2023 – Nov 2025), then run the chosen settings once on the last year (Nov 2025 –
   Nov 2026) and report both. Prefer settings that are good across a wide area of nearby
   values over a single best point.
2. Simulate as the live bot trades: market entry at the next candle's open, live checks,
   stepped exits per the spec, break-even after TP1, runner trail, fees on every exit,
   ambiguous candles settled on 1-minute bars (stop first inside a 1-minute bar). Apply
   one position per coin **across all strategies** (the live rule), and report funding as
   an estimate if you include it.
3. Report per strategy: trades/month, win rate, net R, R per trade, worst drawdown, worst
   losing streak, green months, by timeframe, coin, direction and market regime (rising
   / falling / sideways, define it), in-sample vs blind year.

## Scenarios to compare

- **All strategies:** current live exits vs the spec's stepped exits; TP1 at 0.75R / 1R /
  1.5R; break-even after TP1 on/off; runner trail 3 / 5 / 8 candles; timeframes 15m /
  30m / 1h / 4h.
- **D-line:** minimum stop distance 0.6% / 1% / 1.5%; breakout vs retest entry; TP2/TP3
  placement (structure high, higher-timeframe swing).
- **Nested:** minimum stage N1 / N2 / N3; TP2 at the measured move vs the nearest
  higher-timeframe swing.
- **SFP:** levels from the previous 4h / 8h / daily / weekly candle; same-candle vs next-
  candle reclaim; a confirmation candle; stop 1 tick beyond the wick vs an ATR buffer;
  higher-timeframe trend filter; entry on the retest of the level. Check BTC on its own.

## Deliverables

1. `docs/strategies/backtest_report_<date>.md` and a PDF of it (pandoc to HTML, then
   Chromium print; Chromium and Playwright are pre-installed).
2. New or updated scripts in `research/backtests/` with a README entry.
3. A short recommendation table: for each strategy, the settings to run on the demo next,
   with in-sample and blind-year numbers side by side, and what to drop.
4. A pull request with the docs and scripts only.
