# Brief: SFP research round 1 (higher-timeframe levels, BTC first)

Agreed with Sofien on 27 Nov 2026. His own brief is `sfp_refinement_brief_sofien.md` in this
folder; this file is the version to run. Where they differ, this file wins.

Read `AGENTS.md`, `CLAUDE.md` and `.cursor/rules/*.mdc` first. **Research only:** do not
change anything under `backend/src` or `frontend/`, do not touch Render, do not place
orders, never print secrets. Scripts go in `research/backtests/sfp_r1/`, the report in
`docs/strategies/sfp_research_round1.md` (+ a PDF), charts in
`docs/strategies/sfp_r1_charts/`. One pull request, report and scripts only.

## Why

SFP stays in the strategy set; its demo orders are paused. The bot's current SFP
(15m chart, levels from the last 6 candles, stop just past the wick, one target at the
nearest opposite level) is not how Sofien trades it. He trades higher-timeframe SFPs,
mainly on BTC. Earlier studies (`backtest_report_2026-11.md` section 6,
`backtest_report_2026-11-26.md` sections 5-6) tested simple level sweeps with a one-tick
stop and found losses. This round tests his actual design properly.

## Data

- Binance USD-M klines from data.binance.vision via `research/backtests/download_klines.py`
  (allowed by the session's network). BTCUSDT first; ETH, ZEC, TAO, HYPE reported
  separately. 1m, 15m, 1h, 4h, 1d candles from **1 Jun 2023** (monthly levels need history
  before the study starts) to 25 Nov 2026. Binance funding rates for funding.
- Keep downloaded data outside `backend/` and `research/` (untrusted input).
- Times in reports: Tunis time (UTC+1). Daily/weekly/monthly levels use UTC candles.

## Periods (do not break this)

- **Design:** entries 1 Nov 2023 – 31 Oct 2025. All exploration and choices here.
- **Blind:** entries 1 Nov 2025 – 25 Nov 2026. Freeze the variant list (at most 12) in a
  committed file **before** the blind run, then run it once and report every variant.

## The SFP, round 1

**Levels** (the core): previous 4h, daily, weekly and monthly highs and lows; equal
highs/lows (within 0.1%) on those timeframes. Each level is used once (first sweep).
Confluence score = how many of these sit within 0.2% of each other.
*Not in round 1:* volume profile (POC/VAH/VAL), anchored VWAP. Round 2 adds them as a
confluence bonus only if round 1 looks promising.

**Sweep and reclaim:** price trades beyond the level, then a candle closes back inside.
Execution chart 15m or 1h. Sweep depth recorded (no minimum by default).

**Volume filter (option):** sweep or reclaim candle volume >= 1.5x the 20-candle average.

**Entry options:** (a) market at the next open after the reclaim close; (b) market after a
confirmation close beyond the reclaim candle's extreme; (c) limit at the swept level, valid
6 candles, maker fee. Model limit non-fills honestly (only fill when price trades through).

**Stop options:** (a) beyond the sweep wick + 0.1%; (b) beyond the wick + max(0.7%, 1 ATR14
of the execution chart); (c) beyond the high/low of the 1h (15m execution) or 4h (1h
execution) candle that made the sweep.

**Exits (default for all):** TP1 15% at 1R, TP2 50% at the next level in the trade direction
from the level set above, TP3 20% at the one after, 15% runner on a 5-candle trail, **no
break-even move**. A take-profit alternative (b) "TP2 at the opposite side of the swept
level's candle range" may be one of the 12.

**Live checks, as the bot trades:** reward >= 1x risk from the edge of a 0.1% entry zone
(allocation-weighted, runner counted as 0); fee rule (fees under 20% of risk); one position
per coin.

**Costs:** 0.06% taker / 0.02% maker per side, **plus 5 bps slippage per side on market
orders and on stops**, plus funding. Ambiguous minutes: stop first.

## Step 1 — show how it fails (before any tuning)

With one fixed default (15m execution, levels = previous daily/weekly highs/lows, entry
(a), stop (b), default exits), BTC, design years:

- 10-15 losers and 5 winners as PNG charts (matplotlib; mark the level, sweep wick, entry,
  stop, targets, exit), each with date/time (Tunis), level type, entry, stop, exit, R.
- Classify **every** loss: level broke for real / stop hunted then reversed (wick retest) /
  target too close or R:R refused / fees too large vs the stop / chop, no follow-through /
  other. A table of reasons by %, for this default and for the live bot SFP on the same
  years.

Put the gallery and the table in the report before anything else, so Sofien can check
whether these are trades he would have taken.

## Step 2 — explore on the design years, then freeze

Explore level sets, execution chart, entry, stop and the volume filter on the design years.
Prefer coarse, explainable choices. Then write the frozen list (<= 12 variants) to
`research/backtests/sfp_r1/frozen_variants.json` and commit it before the blind run.

## Step 3 — blind run and report

For every frozen variant, both periods: trades, trades/month, win rate, mean R/trade after
all costs, t-stat, max drawdown (R), worst losing streak, green months; BTC alone and the
other coins separately; share of profit per coin.

**Gate to go back on the demo** (Sofien): positive in both periods; blind year >= 50 trades
(BTC-only: >= 25 if all three years are positive); >= +0.15R per trade after costs; no coin
above 50% of profit. Judge by R per trade, not win rate.

**If one passes:** recommend the exact settings. On demo it would run ~100 trades at 0.25%
risk and stop if negative after 50 trades or drawdown passes 8R (needs a per-strategy risk
setting; note it, do not build it).

## Report (plain language, Sofien reads it)

1. One-page summary: does an HTF SFP have an edge on BTC, and on the others?
2. The failure gallery and loss-reason tables (step 1).
3. Exploration notes (step 2), short.
4. The frozen variants on the blind year (step 3), every one.
5. Recommendation, limits (Binance vs BloFin prices, few BTC trades, what was not tested).

Keep level, entry, stop and exit logic in separate, small modules so new variants (volume
profile, VWAP, other agents' ideas) are easy to add.
