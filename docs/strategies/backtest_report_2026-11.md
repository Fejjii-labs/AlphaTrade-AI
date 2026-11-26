---
title: "AlphaTrade: one-year strategy backtest report"
subtitle: "D-line, Nested Continuation and SFP on BTC, ETH, ZEC, TAO and HYPE"
date: "26 November 2026 (v1.1: corrected after the external review)"
---

# 1. Summary

We replayed the bot's own D-line, Nested and SFP logic on one year of price history for five coins, and simulated every trade the way the BloFin demo bot places it, with real fees.

| | Before (as live until 25 Nov) | After the changes (live since 26 Nov) |
|---|---|---|
| D-line | −338R, 15m/30m/1h, any stop | **+52R**: 30m and 1h only, stop at least 1% from the entry |
| Nested | −165R, 15m, all stages | **+53R**: 30m and 1h only, stage N2 and later |
| SFP | −6.5R, 21 trades in a year (1% sweep) | **+11R**: 15m, 0.3% sweep, about 7 trades/month |

Results are in **R**: one R is the amount risked on a trade. +52R means the trades made 52 times the risk of one trade over the year, after fees.

**The main lessons**

1. **Fees decide the outcome.**
   - BloFin charges 0.06% on the entry and on every exit.
   - With a stop under about 0.5% from the entry, those fees take 25% or more of each R.
   - Most of D-line's loss came from those tight-stop trades.
2. **15-minute charts were too noisy for D-line and Nested.** The same rules on 30m and 1h were profitable.
3. **Nested's first continuation (N1) lost money on every timeframe.** From the second continuation on (N2+), it was profitable on 30m and 1h only, and mostly on 1h.
4. **SFP is the weakest.**
   - The live SFP rarely fired, because its target was usually closer than its stop.
   - A simple "sweep of the previous 4h, 8h or daily high/low and close back" lost money on every combination tested (section 6).

**The edges found are thin**, about +0.1R to +0.2R per trade. The rules were chosen on the same year they were measured on, so live results will likely be weaker. That's why they run on the demo account first, and why a 2–3 year study with a blind test year comes next.

# 2. Method

**Data**

- Binance USD-M perpetual candles from data.binance.vision, 1 November 2025 to 24 November 2026 (about 12.8 months).
- 15m, 30m, 1h, 2h and 4h candles, plus 1-minute candles to settle ambiguous candles.

**Coins**

- BTC, ETH, ZEC, TAO and HYPE, long and short.
- The year was mostly a falling market for the majors: BTC 110k → 60k, ETH 3,846 → 1,679, TAO 487 → 181. ZEC (407 → 739) and HYPE (44 → 60) rose.

**Signals**

- The bot's own detectors, with the parameters stored in the live strategies:
  - D-line: `detect_dline_live`;
  - Nested: `detect_nested` on a rolling 256-candle window, exactly like the live loop;
  - SFP: `detect_sfp` with the live ATR floor.
- No hand-labelled setups.

**Execution, as the live BloFin demo loop does it**

- Market entry at the next candle's open.
- Stop and target rounded to the tick.
- Live checks:
  - reward ≥ 1× risk, measured from the edge of the 10 bps entry zone;
  - fee rule: fees under 20% of the risk, which means a stop at least about 0.7% away;
  - one open position per coin per strategy.
- **Exceptions in the D-line study** (`dline_1m_study.py`, `analyze1m.py`):
  - no fee rule and no one-position-per-coin limit;
  - the retest variant paid a 0.02% maker fee on its entry instead of 0.06%.
  - The chosen D-line rule (stop at least 1%) already clears the fee rule, so the fee point only affects the tables without that filter. The position limit and the maker fee do affect the D-line numbers; the 2–3 year study redoes them.

**Exits**

- D-line: stepped exits, as live.
  - TP1 15%, then the stop moves to break-even.
  - TP2 50%, TP3 20%.
  - 15% runner trailing the last 5 candles.
- Nested and SFP: one full-size take-profit and stop, as live today.
- Break-even in the study is exactly the entry, from the next candle. Live uses entry plus costs, right after the TP1 fill.

**Fees and ambiguous candles**

- Fees: 0.06% taker on the entry and on every exit. Funding is not included.
- When one candle touched both the stop and a target, the order was read from its 1-minute candles. If a 1-minute candle touched both, the stop counted first.

**Not modelled**

- Slippage beyond the fee, funding, Binance vs BloFin price differences, and cross-strategy limits (only one position per coin across all strategies).

# 3. D-line

2,453 breakout signals.

**Entry styles** (all timeframes, before the new rules)

| Entry | Trades | Win rate | Net |
|---|---|---|---|
| Market at the breakout (live) | 2,453 | 36% | −338R |
| Limit at the line (retest only) | 2,049 (84% filled) | 32% | −314R |
| Half and half | 2,453 | 37% | −326R |

**By stop distance from the entry** (breakout entry)

| Stop distance | Trades | Net | Per trade |
|---|---|---|---|
| under 0.5% | 728 | −300R | −0.41R |
| 0.5–1% | 810 | −61R | −0.08R |
| 1–2% | 653 | +38R | +0.06R |
| over 2% | 262 | −14R | −0.05R |

**By timeframe** (breakout entry, all stops): 15m −304R, 30m −66R, 1h +33R.

**Chosen rules** (live since 25 Nov): 30m and 1h only, and the stop must be at least 1% from the entry.

| | Trades/month | Net | Worst drawdown | Green months |
|---|---|---|---|---|
| Breakout, stop ≥1%, 30m + 1h (**live**) | 43 | +52R | −37R | 6/13 |
| Retest entry, stop ≥1%, 30m + 1h (on hold) | 28 | +55R | −14R | 6/13 |

**Per coin** (retest, stop ≥1%): ETH +37R, HYPE +7R, BTC +6R, TAO +1R, ZEC −10R.

# 4. Nested Continuation

9,287 signals. 2,045 passed every live check, counted with one position per coin.

| Rules | Trades/month | Net | Worst drawdown | Green months |
|---|---|---|---|---|
| **15m, all stages (live until 26 Nov)** | 139 | **−165R** | −188R | 2/13 |
| 30m, all stages | 84 | −88R | −119R | 5/13 |
| 1h, all stages | 47 | +6R | −58R | 5/13 |
| 30m, N2+ | 27 | +15R | −17R | 6/13 |
| 1h, N2+ | 15 | +43R | −12R | 9/13 |
| **30m + 1h, N2+ (live since 26 Nov)** | 36 | **+53R** | −20R | 8/13 |

**By stage** (all timeframes): N1 −197R, N2 −39R, N3 +16R, N4+ +7R.

**Stability check** (30m + 1h, N2+): +31R in the first half of the year and +23R in the second. On 1h, N2+ was positive on all five coins (about +8R to +11R each).

**A stricter minimum stop helped less here than for D-line.** The stage and the timeframe matter most.

# 5. SFP (bot's detector, live parameters)

| Rules | Trades in the year | Net |
|---|---|---|
| Live until 26 Nov: 1% minimum sweep, target at the nearest opposite level | 21 | −6.5R |
| Same signals, ignoring the 1:1 and fee checks | 669 | −33R (69% win rate, small wins) |
| Fixed targets at 1.5R, 2R and 3R (1% or 0.3% sweep) | 350–2,200 | −34R to −233R |
| **0.3% minimum sweep, live target and checks (live since 26 Nov)** | 206 (all timeframes) | **+12.5R** |
| of which 15m | 89 (7/month) | +11.4R (51% win rate) |

Why the live SFP barely traded: its target is the nearest opposite level, which is usually closer than the stop, so the 1:1 check refused about 97% of signals.

The 0.3% sweep result is small. By half year it was +1R then +10R, so treat it as an experiment.

# 6. SFP at significant levels

This tests Sofien's SFP rules:

- **Levels:** the previous completed 4h, 8h or daily candle's high and low.
- **Signal:**
  - a 15m or 1h candle wicks beyond the level and closes back inside;
  - variant "2bar" also allows the close back on the next candle;
  - first sweep of a level only.
- **Entry and stop:** entry at the next open; stop 1 tick beyond the sweep wick.
- **Stepped exits:**
  - TP1 at 1R (15%), then the stop moves to break-even;
  - TP2 at the opposite level of the previous candle (50%);
  - TP3 at the opposite extreme of the last 3 candles (20%);
  - 15% runner trailing 5 candles.
- **Checks:** the live fee rule, and TP2 must lie beyond TP1.

| Entry candle | Levels | Trades/month | Win rate | Net | Per trade |
|---|---|---|---|---|---|
| 15m | 4h | 68 | 42% | −126R | −0.14R |
| 15m | 8h | 44 | 41% | −100R | −0.18R |
| 15m | daily | 19 | 40% | −69R | −0.29R |
| 1h | 4h | 119 | 43% | −252R | −0.17R |
| 1h | 8h | 79 | 42% | −209R | −0.21R |
| 1h | daily | 34 | 43% | −101R | −0.23R |

- **BTC alone:** about break-even on 15m entries at 4h levels (28 trades, +2.5R). Every other BTC combination lost.
- **Without the fee rule:** about 80% of these signals have stops so close to the entry that fees dominate. The win rate then drops to 16–18%.

**Reading.** In this year, a sweep and close back past the previous 4h, 8h or daily level was not enough on its own: price came back through the sweep wick too often. Ideas for the 2–3 year study:

- a confirmation candle;
- a stop buffer beyond the wick based on volatility (ATR);
- higher-timeframe trend agreement;
- weekly levels;
- entry on the retest of the level.

# 7. Limits of this study

- **One year, mostly one market regime** (falling majors).
- **Chosen in-sample:** the rules were picked after looking at the results, so expect weaker live results.
- **Binance candles stand in for BloFin prices.**
- **Funding and slippage are not included.**
- **Overlapping positions across strategies on the same coin are not modelled.** The three results cannot simply be added: live allows one position per coin across all strategies.
- **The edges are not statistically proven yet.** With roughly 200–460 trades per setup, per-trade means of +0.1R to +0.2R are within about 1–2.5 standard errors of zero. Nested 1h N2+ is the strongest (t ≈ 2.4).
- **Nested and SFP were tested with today's full-size exits.** The stepped exits Sofien asked for (section 8) still need their own backtest.

# 8. Next steps

1. **2–3 year backtest** (separate session):
   - the strategy spec v1 rules, including stepped exits for all three strategies;
   - settings chosen on the earlier years, then checked blind on the last year.
2. **Repo and safety audit** (separate session).
3. **Build the agreed spec into the bot behind switches.** Includes stepped exits for Nested and SFP, and a backup stop order on BloFin for stepped trades.
4. **Several weeks on the demo account**, then compare live results with this report.

# Appendix: files

- Replay scripts (one-off, outside the repo):
  - `dline_retest_study.py`, `dline_1m_study.py`;
  - `sfp_nested_study.py`, `sfp_levels_study.py`;
  - `analyze*.py`.
- The detectors and live checks they call are in `backend/src/app/strategy_brain/` and `backend/src/app/services/governed_blofin_demo.py`.
- Live settings: `GOVERNED_DEMO_DLINE_MIN_STOP_DISTANCE_PERCENT=1`, `GOVERNED_DEMO_DLINE_TIMEFRAMES=30m,1h`, `GOVERNED_DEMO_NESTED_TIMEFRAMES=30m,1h`, `GOVERNED_DEMO_NESTED_MIN_STAGE=N2`.
- SFP strategies with `minimum_sweep_depth=0.003` were approved on staging on 26 Nov.
