---
title: "AlphaTrade strategy spec v1"
subtitle: "D-line, Nested Continuation and SFP: what runs today, and the agreed target design"
date: "26 November 2026 (v1.1: corrected after the external review)"
---

# How to read this

For each strategy, this document gives:

- **Live today:** what the BloFin demo bot does right now.
- **Target (agreed with Sofien):** the design to backtest over 2–3 years and then build.
- **Open points:** what still needs a decision or a test.

All trading is on the BloFin **demo** account only.

- **Coins:** BTC, ETH, ZEC, TAO and HYPE, long and short.
- **Price data for signals:** Binance USD-M candles.
- **Orders:** placed on BloFin demo.

The supporting numbers are in *AlphaTrade: one-year strategy backtest report* (26 Nov 2026).

# Rules shared by all three strategies

## Safety (live today)

- **Kill switch:** while on, no new trades.
- **Daily loss lock:** stops new trades for the day after the daily loss limit.
- **One position per coin at a time**, across all strategies.
- **Reward:risk:** at least 1:1 from the edge of the entry zone. The entry zone is the market price ± 10 bps.
- **Fee rule:** the round-trip fees must be under 20% of the risk, with the risk measured from the near edge of the entry zone. In practice the stop must be at least about **0.7%** from the mid entry (2 × 0.06% ≤ (d − 0.1%) / 5).
- **Sizing:** the stop distance sets the size, so the planned loss at the stop equals the configured risk per trade.

## Live configuration (checked on staging, 26 Nov)

| Setting | Value |
|---|---|
| Risk per trade | 1% of the demo account |
| Daily loss lock | 5% of the account |
| Leverage and margin | 10×, isolated |
| Positions | one per coin, across all strategies |
| Strategies scanned | 50: 5 coins × long/short × (D-line 30m and 1h, Nested 30m and 1h, SFP 15m) |
| Stepped exits for Nested and SFP | built, **switched off** (`GOVERNED_DEMO_LADDER_FAMILIES` empty) |

These are demo settings. The review proposes 0.25% risk per trade, at most 2R open in one direction and a 3R daily stop for real money; that is decided before any real-money start.

## Exits (target design: the same for all three)

| Step | Size | Where | After it fills |
|---|---|---|---|
| TP1 | 15% | Enough to cover fees and some of the risk: about **1R** (to be confirmed by the backtest) | **Stop moves to break-even** |
| TP2 | 50% | The first **significant structure level** (support/resistance) in the trade direction | — |
| TP3 | 20% | The next significant structure level beyond TP2 | — |
| Runner | 15% | Trails the low (long) or high (short) of the **last 5 candles** | Closes when price breaks the trail |

- **Break-even after TP1:** the stop moves to the entry **plus the round-trip costs** (live, `breakeven_stop`), not exactly the entry. It turns a trade into a no-loss trade once it has worked a little, and costs the trades that dip back and then go on to the target.
  - It probably helps SFP (a return to the entry means the reversal failed) and probably hurts D-line and Nested, whose breakouts often retest the line. The 2–3 year backtest tests it per strategy against "no break-even" and "break-even after TP2".
- **TP1 does not pay for the fees on a tight stop.** 15% at 1R earns 0.15R, while the round trip costs about 0.17R at a 0.7% stop and 0.12R at 1%. Its real job is to trigger the break-even move.
- **Runner:** it only starts trailing once **all targets have filled** (live, `partial_exits/demo.py`). Until then its 15% sits at the stop or break-even.
- **Live today:** D-line already uses this ladder. Nested and SFP still exit at once: full size at one target, or the stop.
- **Small positions:** a 15% step can fall below BloFin's minimum order size. In that case the steps must merge sensibly. This is part of the build.
- **Backup stop on BloFin (to build):**
  - Stepped exits are managed by the bot, so no stop order rests on BloFin today.
  - A stop order on BloFin at the same level is a safety net if the bot is down.
  - It moves to break-even after TP1, like the bot's own stop.

# 1. D-line

**Idea:** trade the breakout of a trendline (the "D-line") drawn through earlier swing points, in the direction of the bigger trend.

**Live today (since 25 Nov)**

- **Timeframes:** 30m and 1h. 15m is switched off.
- **Line:** at least 2 touches. The trend on the next timeframe up must agree (30m → 2h, 1h → 4h).
- **Entry:** market order after a candle closes through the line. Full size (`entry_mode` "breakout", not the half-size "split").
- **Second attempts:** after a break-even exit the detector can enter the same line again. These re-entries are part of the backtest result.
- **Stop:** just beyond the structure low (long) or high (short): the extreme between the line's start and the breakout, plus 1 tick.
- **Filters:**
  - the structure stop must be at least **1% from the entry**, otherwise the trade is skipped;
  - at least 2R to TP2.
- **Exits:**
  - TP1 15% halfway to the structure high, then break-even;
  - TP2 50% at the structure high (the line's start);
  - TP3 20% at the higher-timeframe swing;
  - runner 15%, 5-candle trail.

**Target**

- Same stop: the structure low/high. The 1% is only a filter, not the stop.
- TP1 moves to about 1R (covers fees), instead of halfway to the structure.
- TP2 and TP3 stay at the structure high and the higher-timeframe swing.

**Open points**

- TP1 at 1R vs halfway: the backtest decides.
- Retest entry (limit order at the line): on hold. In the 1-year study it lowered the worst drawdown (−14R vs −37R) for a similar return.

# 2. Nested Continuation

**Idea:** a trend makes an impulse, pulls back, then breaks the impulse extreme again. Each new continuation is one stage: N1, N2, N3, N4+.

**Live today (since 26 Nov)**

- **Timeframes:** 30m and 1h. 15m is off.
- **Stage:** N2 and later only. N1 lost money on every timeframe. N2+ was positive only on 30m and 1h, and most of that edge is 1h (+43R of +53R).
- **Entry:** market order after a candle closes beyond the impulse extreme.
- **Stop:** the pullback extreme (low of the pullback for a long).
- **Exit:** one target at the measured move (the impulse length added to the extreme), full size.

**Target**

- Stepped exits (shared table):
  - TP1 about 1R, then break-even;
  - TP2 at the first significant structure level: the measured move or the nearest higher-timeframe swing, whichever comes first;
  - TP3 at the next structure level;
  - runner 15%.
- Stop unchanged.

**Open points**

- Exact choice of TP2/TP3 structure: measured move vs previous swing highs/lows on a higher timeframe.

# 3. SFP (Swing Failure Pattern)

**Idea:** price sweeps a significant level (takes the stops beyond it), then fails and closes back inside. We trade the reversal.

**Live today (since 26 Nov)**

- **Timeframe:** 15m.
- **Levels:** swing highs and lows, equal highs and lows, and the range high/low of the **last 6 candles** (1.5 hours, `level_lookback` 6).
- **Sweep:** between **0.3% and 5%** beyond the level (`minimum_sweep_depth` 0.003, `maximum_sweep_depth` 0.05), then a close back inside, then a confirmation close.
- **Stop:** beyond the sweep wick, plus a small buffer (the larger of 0.1% of price or half an ATR).
- **Exit:** one target at the nearest opposite level, full size.

**Target (Sofien)**

- **Levels:** significant ones only, such as the previous daily, 8h or 4h high and low. BTC respects these levels well.
- **Entry:** after the sweep candle closes back inside the level.
- **Stop:** beyond the sweep wick.
- **Exits:** the shared stepped exits.
  - TP1 about 1R, then break-even;
  - TP2 at the opposite level;
  - TP3 at the next bigger structure;
  - runner 15%.

**What the 1-year data says about this design** (report section 6)

- As a simple rule (previous 4h/8h/daily high or low, close back, stop 1 tick beyond the wick), it lost on every combination: about −0.14R to −0.29R per trade.
- BTC with 15m entries at 4h levels was about break-even.

**Open points for the 2–3 year study**

- A confirmation candle after the close back.
- A stop buffer beyond the wick based on volatility (ATR).
- Agreement with the higher-timeframe trend.
- Weekly levels.
- Entry on the retest of the level.

# Claude's view

- **Stepped exits, break-even after TP1 and a 5-candle runner for all three: agree.**
  - It is safer, it locks in fees early, and it keeps exposure to big extensions.
  - The backtest should still compare TP1 at 0.75R, 1R and 1.5R, because the break-even move has a cost.
- **D-line:** the structure stop is the right stop. Keep the 1% filter: it is the single change that turned D-line positive.
- **Nested:** the N2+ and 30m/1h result was the most stable finding (positive in both halves of the year and on every coin on 1h). The stepped exits must not destroy that, so compare both in the backtest.
- **SFP:** the level choice matters, but the 1-year data says levels alone are not enough.
  - I'd treat SFP as experimental until the 2–3 year study finds a version with an edge out of sample.
  - The ideas listed in its open points (confirmation, ATR stop buffer, trend filter, weekly levels, retest entry) are the ones to test first.
- **In every test, aim for the most profit per trade after fees with a tolerable drawdown, not the highest win rate.** A higher win rate usually comes from smaller wins.

# Changes in v1.1 (26 Nov, after the external review)

- Fee rule: about 0.7%, not 0.6%.
- Live configuration table added (risk 1%, daily lock 5%, leverage 10× isolated, 50 strategies).
- Break-even is entry plus costs; TP1's fee coverage and the runner start are stated.
- D-line: full-size breakout entry and second attempts stated.
- Nested: N2+ is a 30m/1h result, mostly 1h.
- SFP: stored level lookback and sweep range stated.
