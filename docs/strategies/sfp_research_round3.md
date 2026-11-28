---
title: "AlphaTrade: SFP research round 3"
subtitle: "An anchored-VWAP trend filter on round 1's V05, tested on 2020-2023 data no round has used"
date: "28 November 2026"
---

# Summary

**Question.** Round 1's biggest loss reason was "the level broke for real" (45-47% of losses). Does a higher-timeframe trend filter built from the weekly-open and monthly-open anchored VWAPs turn round 1's V05 positive on data nobody had looked at (January 2020 to October 2023)?

**Answer: no. F1 fails all three parts of the pass rule, and the filter did not reduce the "level broke" share.**

- **The main evidence: Blind A, BTC.** F1 made −0.11R per trade on 24 trades (t −0.39), at 0.5 trades a month (1.4 a month on BTC, ETH and ZEC together). The pass rule needed ≥ +0.15R, t ≥ 2 and ≥ 4 trades a month. F1 met none of them.
- **The control lost too.** On the same data, V05 without the filter (F0) made −0.14R per trade on 169 BTC trades (t −1.17). This is the first period that played no part in choosing V05. Its +0.29R in round 1's design years was the best of 882 settings, and it does not hold up out of sample.
- **The filter removes almost every signal.** F1 kept 24 of 213 BTC candidates (11%) and 64 of 643 on BTC, ETH and ZEC. A long SFP comes right after price swept a low, so price is usually below the week's and the month's VWAP at that moment; the rule asks for the opposite.
- **Effect against noise (Blind A, same signals):** BTC trades F1 kept made +0.09R more per trade than the ones it filtered out, with a standard error of 0.31R (t 0.29; 95% interval −0.47R to +0.69R). On all three coins the difference was −0.05R (SE 0.19R). Both are indistinguishable from zero. At F1's pace (about 0.5 BTC trades a month), showing a +0.15R edge at t = 2 would take roughly 340 trades: more than 50 years.
- **"Level broke for real" did not fall.** Among BTC losses on Blind A it went from 35% (F0) to 50% (F1); on all three coins, from 37% to 44%.
- **F2 and F3 (sensitivity only)** did no better: on BTC in Blind A, −0.23R (45 trades) and −0.07R (63 trades). Neither anchor carries an effect.
- **Round 1's 13 example losses:** F1 would have filtered out all 13. That says little, because on the same round-1 trades it also filtered out 57 of the 58 winners (it kept 11 of 235 trades).
- **Look-ahead check:** 0 differences on 1,566 VWAP verdicts and 638 full re-decisions. A planted VWAP look-ahead is flagged on all 1,280 BTC, ETH and ZEC candidates.

**Recommendation: keep SFP paused.** Do not start the demo forward test, and do not build the per-strategy risk setting for SFP. Round 4 (POC and value-area confluence) was conditional on F1 passing, so it is not recommended either. Section 8 gives the details.

All results are in **R** (one R is the amount risked on a trade), after fees, slippage and funding, on Binance USD-M prices. Times are Tunis time (UTC+1).

# 1. Pre-registration (written before any round-3 result)

This section was committed to the branch `claude/sfp-research-round3` before any round-3
backtest was run. Nothing in it was changed after the results were seen.

**Question.** Round 1's biggest loss reason was "the level broke for real" (45-47% of losses).
Does a higher-timeframe trend filter built from anchored VWAP turn round 1's V05 positive on
data nobody has looked at?

**Setup (V05 exactly as in round 1).** 1h execution chart; previous daily and weekly highs and
lows; confluence of at least 2 levels within 0.2%; limit entry at the swept level (valid 6
candles, maker fee, filled only when price trades through); stop beyond the wick by max(0.7%,
1 ATR14); round 1's exit E0 (TP1 15% at 1R, TP2 50% at the next level, TP3 20% at the one
after, 15% runner on a 5-candle trail, no break-even); round 1's live checks; one position per
coin. Costs: 0.06% taker / 0.02% maker, 5 bps slippage per side on market orders and stops,
Binance funding; 1-minute simulation.

**Variants (no others):**

| Id | Rule |
|---|---|
| F0 | V05 unchanged (control) |
| F1 (primary) | Long SFPs (sweeps of a low) only when price is above BOTH the weekly-open and monthly-open anchored VWAPs; shorts (sweeps of a high) only when price is below both |
| F2 (sensitivity) | Weekly-open anchored VWAP only |
| F3 (sensitivity) | Monthly-open anchored VWAP only |

**VWAP definition.** Anchored at the open of the current UTC week (Monday 00:00) and the
current UTC calendar month. Built from 1h candles: sum(typical price × volume) / sum(volume),
typical price = (high + low + close) / 3. At decision time (the close of the SFP's reclaim
candle, when the limit order would be placed) only 1h candles that opened at or after the
anchor and have closed are used. "Price" is the close of the reclaim candle; above / below are
strict. If an anchor has no closed candle yet (the decision falls exactly on a week or month
open), that VWAP is undefined and the signal is not taken. Code: `research/backtests/sfp_r3/vwap.py`.

**Periods (entries in [start, end)).**

| Period | Dates | Status |
|---|---|---|
| Blind A (main evidence) | 2020-01-01 to 2023-10-31 | Never used by any round. BTC first, then every coin with Binance USD-M data in the period, each separately, then all together |
| Design | 2023-11-01 to 2025-10-31 | Already seen (round 1's design years) |
| Blind-year | 2025-11-01 to 2026-11-25 | Seen (round 1's blind year, seen since) |
| Forward | 2026-11-26 onward | Count only |

**Reported for each variant and period:** trades, trades per month, win rate, R per trade after
all costs, t-stat, max drawdown, worst losing streak, and the share of losses where "the level
broke for real" (round 1's `classify.py`).

**Pass rule.** F1 passes only if, on Blind A for BTC, it makes ≥ +0.15R per trade after costs,
with t ≥ 2, and ≥ 4 trades a month on BTC alone or on all coins together. F2 and F3 cannot
rescue a failed F1; they only show which anchor carries the effect.

- If F1 passes: recommend a small demo forward test (0.25% risk per trade, about 100 trades,
  stop if negative after 50 trades or a drawdown beyond 8R), plus a round 4 adding
  point-of-control and value-area confluence.
- If F1 fails: recommend SFP stays paused, and state how big the effect was against its noise.

**Look-ahead check.** Round 2's truncation test (`sfp_r2/step0.py`) on every F1-F3 trade, and the
VWAP verdict of every candidate signal recomputed on data cut at the decision time, with a
negative control that plants a VWAP look-ahead (`sfp_r3/step0_r3.py`).

**Also reported.** Round 1's 13 example losses (round 1, section 2), with the F1 verdict at each
one's decision time.

# 2. Look-ahead check

Round 2's truncation test (`sfp_r2/step0.py`, imported unchanged) was run again with the new filter (`sfp_r3/step0_r3.py`):

- **Every candidate signal** (kept or filtered out, all periods, all coins): the weekly and monthly VWAPs, the price and the F0-F3 verdicts were computed again on 1h candles cut at the decision time (the reclaim candle has just closed; the candle still open is rebuilt from 1-minute bars, as in round 2).
- **Every candidate kept by F1, F2 or F3** (a superset of their trades): round 2's full re-decision at the order time (signal, pattern extreme, stop) and one minute after the fill (fill, take-profit ladder, live check).
- **Negative control:** a VWAP that also reads the 1h candle after the decision time (not yet closed then).

| Coin | Candidate VWAP verdicts re-decided | Differences | Candidates kept by F1-F3, re-decided in full | Differences | Leak control: candidates flagged |
|---|---|---|---|---|---|
| BTC | 449 | 0 | 196 | 0 | 449 of 449 (19 change a verdict) |
| ETH | 394 | 0 | 149 | 0 | 394 of 394 (12 change a verdict) |
| ZEC | 437 | 0 | 169 | 0 | 437 of 437 (11 change a verdict) |
| TAO | 179 | 0 | 74 | 0 | not run |
| HYPE | 107 | 0 | 50 | 0 | not run |

No look-ahead was found. The planted leak changes the VWAP value of every candidate, and on 42 of the 1,280 it flips an F0-F3 verdict, so the test can see this kind of bug. V05 itself also reproduces round 1 exactly: F0 on BTC makes 121 trades / +0.29R in the design years and 53 / −0.18R in the blind-year, and 469 / −0.07R on all five coins in the design years (round 1, section 4).

# 3. Blind A (2020-01-01 to 2023-10-31): the main evidence

No round had used this period. BTC and ETH have Binance USD-M data from 31 December 2019, ZEC from 5 February 2020. TAO and HYPE were listed in 2024 and 2025 and have no data in this period. 46 months; "per month" divides by 46 for every coin.

**BTC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 169 | 3.7 | 25% | −0.14R | −23.6R | −1.17 | −38.5R | 35 | 35% | 18/46 |
| F1 | 24 | 0.5 | 33% | −0.11R | −2.6R | −0.39 | −7.1R | 5 | 50% | 8/46 |
| F2 | 45 | 1.0 | 29% | −0.23R | −10.5R | −1.21 | −18.0R | 9 | 44% | 10/46 |
| F3 | 63 | 1.4 | 29% | −0.07R | −4.7R | −0.38 | −16.1R | 17 | 31% | 14/46 |

**ETH**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 177 | 3.8 | 24% | −0.11R | −19.9R | −0.92 | −39.3R | 14 | 42% | 18/46 |
| F1 | 20 | 0.4 | 30% | −0.05R | −0.9R | −0.12 | −7.7R | 6 | 50% | 6/46 |
| F2 | 33 | 0.7 | 24% | −0.21R | −6.9R | −0.75 | −11.5R | 6 | 60% | 7/46 |
| F3 | 60 | 1.3 | 30% | +0.01R | +0.5R | 0.04 | −11.1R | 8 | 33% | 13/46 |

**ZEC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 190 | 4.1 | 28% | +0.10R | +19.5R | 0.77 | −26.3R | 16 | 35% | 23/46 |
| F1 | 20 | 0.4 | 25% | −0.28R | −5.6R | −0.93 | −9.3R | 6 | 33% | 5/46 |
| F2 | 31 | 0.7 | 19% | −0.40R | −12.3R | −1.65 | −15.9R | 10 | 32% | 6/46 |
| F3 | 69 | 1.5 | 25% | −0.03R | −2.4R | −0.16 | −13.5R | 8 | 42% | 14/46 |

**All 3 together (BTC, ETH, ZEC)**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 536 | 11.7 | 26% | −0.04R | −24.1R | −0.62 | −85.6R | 19 | 37% | 21/46 |
| F1 | 64 | 1.4 | 30% | −0.14R | −9.2R | −0.79 | −18.7R | 8 | 44% | 14/46 |
| F2 | 109 | 2.4 | 25% | −0.27R | −29.7R | −2.04 | −39.2R | 12 | 45% | 14/46 |
| F3 | 192 | 4.2 | 28% | −0.03R | −6.5R | −0.29 | −29.2R | 11 | 36% | 20/46 |

## 3.1 The pass rule

| Condition (Blind A, F1) | Needed | Result | Met |
|---|---|---|---|
| R per trade, BTC | ≥ +0.15R | −0.11R | no |
| t-stat, BTC | ≥ 2 | −0.39 | no |
| Trades a month, BTC or all coins | ≥ 4 | 0.5 BTC / 1.4 all | no |

**F1 fails: none of the three conditions is met.**

F2 and F3 cannot rescue a failed F1. They did not do better anyway (BTC: F2 −0.23R on 45 trades, F3 −0.07R on 63 trades).

## 3.2 Why the filter keeps so few signals

An SFP long comes just after price swept a previous daily or weekly low, so at the reclaim close price is usually still below the week's and the month's VWAP. Shorts mirror this. F1 asks for the opposite, a sweep against the higher-timeframe flow, and that is rare:

| Coin (Blind A) | Side | Candidates | On the wrong side of the weekly VWAP | On the wrong side of the monthly VWAP | Kept by F1 |
|---|---|---|---|---|---|
| BTC | long | 91 | 66 (73%) | 66 (73%) | 11 |
| BTC | short | 122 | 100 (82%) | 78 (64%) | 13 |
| ETH | long | 89 | 72 (81%) | 64 (72%) | 10 |
| ETH | short | 117 | 100 (85%) | 81 (69%) | 10 |
| ZEC | long | 87 | 71 (82%) | 67 (77%) | 7 |
| ZEC | short | 137 | 120 (88%) | 86 (63%) | 13 |

("Wrong side" = not strictly above the VWAP for a long, not strictly below for a short. Two candidates fell exactly on a week or month open, where the VWAP is undefined, and were not taken.)

## 3.3 By year and by side (BTC unless stated)

Trades / R per trade:

| Variant | 2020 | 2021 | 2022 | 2023 (Jan-Oct) |
|---|---|---|---|---|
| F0 | 35 / −0.10R | 44 / −0.45R | 56 / +0.06R | 34 / −0.10R |
| F1 | 3 / −0.18R | 2 / −0.91R | 10 / −0.31R | 9 / +0.31R |
| F2 | 7 / +0.07R | 7 / −0.32R | 19 / −0.63R | 12 / +0.26R |
| F3 | 9 / −0.67R | 13 / −0.04R | 24 / +0.31R | 17 / −0.33R |

All three coins:

| Variant | 2020 | 2021 | 2022 | 2023 (Jan-Oct) |
|---|---|---|---|---|
| F0 | 115 / +0.22R | 154 / −0.24R | 163 / −0.12R | 104 / +0.07R |
| F1 | 8 / +0.47R | 11 / −0.84R | 24 / −0.29R | 21 / +0.16R |
| F2 | 13 / +0.50R | 21 / −0.47R | 47 / −0.55R | 28 / −0.02R |
| F3 | 29 / −0.12R | 50 / +0.18R | 72 / +0.02R | 41 / −0.32R |

BTC by side:

| Variant | Longs (trades / R per trade) | Shorts (trades / R per trade) |
|---|---|---|
| F0 | 71 / −0.11R | 98 / −0.16R |
| F1 | 11 / −0.21R | 13 / −0.03R |
| F2 | 24 / −0.16R | 21 / −0.32R |
| F3 | 24 / −0.26R | 39 / +0.04R |

F1's best year on BTC (2023, +0.31R) is 9 trades. No year or side shows a steady effect.

# 4. How big the effect was against its noise

The cleanest measure compares, on the same candidate signals (before the one-position-per-coin rule), the trades a variant keeps with the ones it filters out. A real trend filter would show kept trades clearly better than filtered-out ones.

| Coins | Period | Variant | Candidates | Kept | R/trade kept | R/trade filtered out | Kept minus filtered out | Std. error | t | 95% interval (bootstrap) |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | Blind A (never used) | F1 | 213 | 24 | −0.11R | −0.20R | +0.09R | 0.31 | 0.29 | −0.47 to +0.69 |
| BTC | Blind A (never used) | F2 | 213 | 46 | −0.25R | −0.17R | −0.08R | 0.23 | −0.36 | −0.52 to +0.37 |
| BTC | Blind A (never used) | F3 | 213 | 69 | −0.06R | −0.25R | +0.19R | 0.23 | 0.86 | −0.23 to +0.63 |
| All 3 | Blind A (never used) | F1 | 643 | 64 | −0.14R | −0.09R | −0.05R | 0.19 | −0.27 | −0.42 to +0.34 |
| All 3 | Blind A (never used) | F2 | 643 | 112 | −0.29R | −0.05R | −0.24R | 0.15 | −1.61 | −0.53 to +0.05 |
| All 3 | Blind A (never used) | F3 | 643 | 201 | −0.05R | −0.12R | +0.07R | 0.14 | 0.51 | −0.20 to +0.35 |
| BTC | Design (already seen) | F1 | 161 | 16 | +0.79R | +0.18R | +0.62R | 0.55 | 1.12 | −0.41 to +1.68 |
| BTC | Design (already seen) | F2 | 161 | 34 | +0.35R | +0.21R | +0.13R | 0.36 | 0.37 | −0.55 to +0.84 |
| BTC | Design (already seen) | F3 | 161 | 53 | +0.35R | +0.19R | +0.16R | 0.34 | 0.48 | −0.47 to +0.83 |
| All 5 | Design (already seen) | F1 | 577 | 64 | −0.27R | −0.03R | −0.25R | 0.21 | −1.18 | −0.63 to +0.18 |
| All 5 | Design (already seen) | F2 | 577 | 115 | −0.19R | −0.02R | −0.16R | 0.17 | −0.95 | −0.50 to +0.18 |
| All 5 | Design (already seen) | F3 | 577 | 192 | −0.29R | +0.06R | −0.36R | 0.15 | −2.44 | −0.64 to −0.07 |
| BTC | Blind-year (seen) | F1 | 73 | 15 | −0.51R | −0.10R | −0.41R | 0.38 | −1.06 | −1.09 to +0.34 |
| BTC | Blind-year (seen) | F2 | 73 | 21 | −0.55R | −0.04R | −0.50R | 0.34 | −1.49 | −1.13 to +0.17 |
| BTC | Blind-year (seen) | F3 | 73 | 26 | −0.07R | −0.25R | +0.18R | 0.39 | 0.45 | −0.55 to +0.95 |
| All 5 | Blind-year (seen) | F1 | 341 | 44 | −0.15R | +0.07R | −0.23R | 0.25 | −0.90 | −0.70 to +0.27 |
| All 5 | Blind-year (seen) | F2 | 341 | 67 | −0.19R | +0.10R | −0.29R | 0.21 | −1.35 | −0.69 to +0.13 |
| All 5 | Blind-year (seen) | F3 | 341 | 118 | +0.05R | +0.04R | +0.00R | 0.22 | 0.00 | −0.42 to +0.44 |

- **Blind A, BTC, F1:** +0.09R per trade in favour of the kept trades, standard error 0.31R (t 0.29). The 95% interval runs from −0.47R to +0.69R. All three coins: −0.05R (SE 0.19R). There is no detectable effect.
- **The design years** (already seen) show the effect the owner and the review hoped for on BTC (+0.62R, t 1.12, on 16 kept trades), but it is not significant there either, it reverses on all five coins (−0.25R), and it is gone in Blind A and in the blind-year (−0.41R on BTC).
- **How long a test would take.** F1's BTC trades have a spread of about 1.4R. Showing +0.15R per trade at t = 2 needs about 340 trades. At F1's 0.5 BTC trades a month (1.4 on three coins) that is several decades of data.

# 5. Did "the level broke for real" fall?

No. The filter was meant to remove the trades where the level breaks for real. Among the losses it left, that share went up on Blind A.

**BTC** (losses, count and share):

| Loss reason | Blind A F0 | Blind A F1 | Design F0 | Design F1 | Blind-year F0 | Blind-year F1 |
|---|---|---|---|---|---|---|
| Level broke for real | 44 (35%) | 8 (50%) | 36 (47%) | 2 (25%) | 15 (39%) | 6 (55%) |
| Target too far: reached +1R, then back to the stop | 45 (36%) | 4 (25%) | 23 (30%) | 2 (25%) | 11 (29%) | 3 (27%) |
| Stop hunted, then reversed (wick retest) | 16 (13%) | 2 (12%) | 11 (14%) | 3 (38%) | 8 (21%) | 1 (9%) |
| Chop, no follow-through | 21 (17%) | 2 (12%) | 7 (9%) | 1 (12%) | 4 (11%) | 1 (9%) |
| **All losses** | **126** | **16** | **77** | **8** | **38** | **11** |

**All coins with data in the period:**

| Loss reason | Blind A F0 | Blind A F1 | Design F0 | Design F1 | Blind-year F0 | Blind-year F1 |
|---|---|---|---|---|---|---|
| Level broke for real | 148 (37%) | 20 (44%) | 141 (39%) | 16 (33%) | 66 (38%) | 13 (46%) |
| Target too far: reached +1R, then back to the stop | 147 (37%) | 11 (24%) | 122 (34%) | 16 (33%) | 67 (38%) | 10 (36%) |
| Stop hunted, then reversed (wick retest) | 56 (14%) | 9 (20%) | 69 (19%) | 11 (22%) | 29 (17%) | 4 (14%) |
| Chop, no follow-through | 46 (12%) | 5 (11%) | 27 (8%) | 6 (12%) | 13 (7%) | 1 (4%) |
| **All losses** | **397** | **45** | **359** | **49** | **175** | **28** |

On BTC in the design years the share did fall (47% to 25%), but on 8 losses, in a period already seen, and it rose again in Blind A (35% to 50%) and in the blind-year (39% to 55%).

# 6. The periods already seen, and forward

## 6.1 Design years (Nov 2023 to Oct 2025): already seen

**BTC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 121 | 5.0 | 36% | +0.29R | +35.2R | 1.68 | −12.9R | 12 | 47% | 18/24 |
| F1 | 16 | 0.7 | 50% | +0.79R | +12.7R | 1.51 | −3.2R | 3 | 25% | 8/24 |
| F2 | 34 | 1.4 | 41% | +0.35R | +11.8R | 1.10 | −6.5R | 6 | 45% | 9/24 |
| F3 | 48 | 2.0 | 35% | +0.40R | +19.2R | 1.30 | −7.1R | 7 | 42% | 13/24 |

**ETH**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 100 | 4.2 | 21% | −0.16R | −16.0R | −0.89 | −22.9R | 9 | 44% | 10/24 |
| F1 | 17 | 0.7 | 18% | −0.47R | −8.0R | −1.47 | −9.8R | 9 | 50% | 3/24 |
| F2 | 23 | 1.0 | 22% | −0.14R | −3.2R | −0.34 | −10.5R | 10 | 44% | 4/24 |
| F3 | 40 | 1.7 | 12% | −0.55R | −22.1R | −2.88 | −22.1R | 12 | 34% | 5/24 |

**ZEC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 129 | 5.4 | 21% | −0.11R | −13.8R | −0.69 | −41.4R | 24 | 34% | 12/24 |
| F1 | 15 | 0.6 | 13% | −0.46R | −6.9R | −1.30 | −6.9R | 6 | 38% | 2/24 |
| F2 | 26 | 1.1 | 12% | −0.55R | −14.3R | −2.38 | −14.3R | 10 | 30% | 3/24 |
| F3 | 47 | 2.0 | 21% | −0.12R | −5.9R | −0.52 | −13.2R | 7 | 30% | 7/24 |

**TAO**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 88 | 3.7 | 15% | −0.39R | −34.1R | −2.46 | −45.3R | 23 | 36% | 4/24 |
| F1 | 10 | 0.4 | 0% | −0.98R | −9.8R | −21.18 | −9.8R | 10 | 20% | 0/24 |
| F2 | 21 | 0.9 | 10% | −0.66R | −13.9R | −2.86 | −18.8R | 19 | 32% | 2/24 |
| F3 | 32 | 1.3 | 3% | −0.88R | −28.3R | −11.19 | −28.3R | 21 | 26% | 1/24 |

**HYPE**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 31 | 1.3 | 16% | −0.19R | −6.0R | −0.61 | −7.3R | 8 | 31% | 2/24 |
| F1 | 4 | 0.2 | 0% | −0.84R | −3.4R | −10.90 | −3.4R | 4 | 0% | 0/24 |
| F2 | 7 | 0.3 | 14% | −0.31R | −2.2R | −0.56 | −4.5R | 5 | 0% | 1/24 |
| F3 | 12 | 0.5 | 0% | −0.91R | −11.0R | −18.58 | −11.0R | 12 | 25% | 0/24 |

**All 5 together (BTC, ETH, ZEC, TAO, HYPE)**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 469 | 19.5 | 23% | −0.07R | −34.7R | −0.90 | −95.7R | 19 | 39% | 11/24 |
| F1 | 62 | 2.6 | 21% | −0.25R | −15.3R | −1.25 | −18.8R | 16 | 33% | 7/24 |
| F2 | 111 | 4.6 | 23% | −0.20R | −21.8R | −1.29 | −29.9R | 15 | 35% | 7/24 |
| F3 | 179 | 7.5 | 18% | −0.27R | −48.0R | −2.27 | −51.7R | 24 | 32% | 7/24 |

## 6.2 Blind-year (Nov 2025 to 25 Nov 2026): seen

**BTC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 53 | 4.1 | 28% | −0.18R | −9.5R | −0.87 | −18.7R | 13 | 39% | 6/13 |
| F1 | 14 | 1.1 | 21% | −0.46R | −6.5R | −1.36 | −7.4R | 7 | 55% | 3/13 |
| F2 | 20 | 1.5 | 20% | −0.52R | −10.3R | −1.96 | −12.2R | 11 | 50% | 4/13 |
| F3 | 25 | 1.9 | 32% | −0.03R | −0.8R | −0.09 | −11.4R | 10 | 47% | 6/13 |

**ETH**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 50 | 3.8 | 24% | +0.04R | +2.0R | 0.15 | −10.4R | 6 | 34% | 6/13 |
| F1 | 7 | 0.5 | 29% | +0.06R | +0.4R | 0.08 | −4.2R | 4 | 80% | 2/13 |
| F2 | 11 | 0.8 | 36% | +0.11R | +1.2R | 0.21 | −5.2R | 5 | 57% | 3/13 |
| F3 | 18 | 1.4 | 22% | −0.01R | −0.3R | −0.03 | −10.1R | 10 | 50% | 4/13 |

**ZEC**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 40 | 3.1 | 30% | +0.10R | +4.1R | 0.35 | −6.8R | 6 | 50% | 7/13 |
| F1 | 8 | 0.6 | 25% | −0.52R | −4.2R | −2.05 | −4.2R | 4 | 33% | 1/13 |
| F2 | 10 | 0.8 | 30% | −0.26R | −2.6R | −0.72 | −2.6R | 3 | 29% | 2/13 |
| F3 | 20 | 1.5 | 15% | −0.74R | −14.8R | −5.29 | −14.8R | 8 | 59% | 2/13 |

**TAO**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 42 | 3.2 | 38% | +0.48R | +20.2R | 1.51 | −5.6R | 7 | 38% | 7/13 |
| F1 | 5 | 0.4 | 80% | +1.13R | +5.6R | 1.50 | −1.2R | 1 | 100% | 4/13 |
| F2 | 9 | 0.7 | 56% | +0.47R | +4.2R | 0.83 | −2.3R | 2 | 75% | 4/13 |
| F3 | 19 | 1.5 | 47% | +0.49R | +9.3R | 1.25 | −3.6R | 4 | 30% | 6/13 |

**HYPE**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 58 | 4.5 | 22% | +0.00R | +0.3R | 0.02 | −12.4R | 12 | 31% | 5/13 |
| F1 | 7 | 0.5 | 29% | +0.14R | +1.0R | 0.19 | −1.9R | 2 | 0% | 2/13 |
| F2 | 11 | 0.8 | 27% | −0.09R | −1.0R | −0.18 | −3.0R | 3 | 0% | 3/13 |
| F3 | 29 | 2.2 | 31% | +0.49R | +14.1R | 1.10 | −4.8R | 5 | 30% | 6/13 |

**All 5 together (BTC, ETH, ZEC, TAO, HYPE)**

| Variant | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losses: level broke | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| F0 | 243 | 18.7 | 28% | +0.07R | +17.0R | 0.58 | −24.1R | 14 | 38% | 8/13 |
| F1 | 41 | 3.2 | 32% | −0.09R | −3.6R | −0.37 | −12.7R | 8 | 46% | 3/13 |
| F2 | 61 | 4.7 | 31% | −0.14R | −8.4R | −0.74 | −15.4R | 7 | 40% | 3/13 |
| F3 | 111 | 8.5 | 30% | +0.07R | +7.6R | 0.39 | −19.2R | 11 | 44% | 8/13 |

F1 lost on BTC in the blind-year (−0.46R on 14 trades) and on all five coins (−0.09R on 41). Its design-year BTC figure (+0.79R on 16 trades) is the only good number in this round, it comes from a period that had already been looked at, and neither later nor earlier data repeats it.

## 6.3 Forward (from 26 November 2026)

Data to 27 November 2026, 23:59 UTC (two days). Count only:

| Variant | BTC trades | All coins trades | Still open at the end of the data |
|---|---|---|---|
| F0 | 1 | 4 | 1 |
| F1 | 1 | 3 | 1 |
| F2 | 1 | 3 | 1 |
| F3 | 1 | 4 | 1 |

(coins with a forward signal: BTC, ETH, ZEC, TAO)

# 7. Round 1's 13 example losses

The 13 losses of round 1's gallery (round 1, section 2; round 1's step-1 default V01: 15m chart, market entry) with the VWAP verdicts at each one's decision time (the close of its 15m reclaim candle; VWAPs from the 1h candles closed by then):

| # | Entry (Tunis) | Level | Side | Price at decision | Weekly VWAP | Monthly VWAP | R | Reason | F1 | F2 | F3 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2023-11-02 04:00 | 1w high | short | 35,692.5 | 34,648.1 | 34,879.6 | −0.72 | Target too far: reached +1R, then back to the stop | **filtered out** | **filtered out** | **filtered out** |
| 2 | 2023-11-13 13:00 | 1d low | long | 36,838.3 | 37,026.8 | 35,714.3 | −1.23 | Level broke for real | **filtered out** | **filtered out** | taken |
| 3 | 2023-11-24 11:45 | 1w high | short | 37,680.0 | 37,040.6 | 36,185.1 | −1.13 | Chop, no follow-through | **filtered out** | **filtered out** | **filtered out** |
| 4 | 2024-01-28 02:30 | 1d high | short | 42,162.6 | 40,299.9 | 43,378.3 | −1.25 | Stop hunted, then reversed (wick retest) | **filtered out** | **filtered out** | taken |
| 5 | 2024-03-07 15:45 | 1d high | short | 67,257.3 | 65,638.9 | 64,863.9 | −1.05 | Level broke for real | **filtered out** | **filtered out** | **filtered out** |
| 6 | 2024-08-02 21:30 | 1d low | long | 62,520.1 | 65,641.2 | 63,944.3 | −1.14 | Level broke for real | **filtered out** | **filtered out** | **filtered out** |
| 7 | 2024-08-21 20:45 | 1d high | short | 61,147.4 | 59,414.6 | 58,382.2 | −0.90 | Target too far: reached +1R, then back to the stop | **filtered out** | **filtered out** | **filtered out** |
| 8 | 2024-11-29 03:15 | 1d high | short | 96,504.6 | 94,672.4 | 85,493.3 | −1.23 | Level broke for real | **filtered out** | **filtered out** | **filtered out** |
| 9 | 2025-01-28 05:45 | 1d high | short | 103,052.1 | 100,474.1 | 99,751.2 | −0.88 | Target too far: reached +1R, then back to the stop | **filtered out** | **filtered out** | **filtered out** |
| 10 | 2025-04-22 02:30 | 1d high | short | 88,355.8 | 87,179.4 | 82,068.0 | −1.18 | Level broke for real | **filtered out** | **filtered out** | **filtered out** |
| 11 | 2025-08-27 15:45 | 1d high | short | 111,863.6 | 110,912.2 | 115,830.4 | −1.15 | Stop hunted, then reversed (wick retest) | **filtered out** | **filtered out** | taken |
| 12 | 2025-10-16 10:45 | 1d low | long | 111,191.9 | 112,875.1 | 117,293.1 | −1.09 | Level broke for real | **filtered out** | **filtered out** | **filtered out** |
| 13 | 2025-10-30 20:45 | 1w low | long | 106,699.9 | 112,150.7 | 114,094.9 | −0.95 | Target too far: reached +1R, then back to the stop | **filtered out** | **filtered out** | **filtered out** |

Filtered out of 13: F1 13, F2 13, F3 10.

| V01 BTC design trades | All | Losses | Losses: level broke | Wins |
|---|---|---|---|---|
| kept by F0 | 235 | 177 | 79 | 58 |
| kept by F1 | 11 | 10 | 6 | 1 |
| kept by F2 | 20 | 14 | 6 | 6 |
| kept by F3 | 55 | 43 | 18 | 12 |

F1 would have filtered out all 13, mostly shorts at daily highs taken while price was above both VWAPs, and longs at lows while it was below both. That looks good only until the winners are counted. On the same 235 round-1 trades, F1 keeps 11, and only 1 of the 58 winners. F3 keeps 12 of the 58 winners and 43 of the 177 losses: about the same mix as no filter. The 13 charts were picked to illustrate losses, so a rule that blocks nearly every trade blocks them too.

# 8. Recommendation

**F1 fails. SFP stays paused.**

- **Do not start the demo forward test.** It was conditional on F1 passing (0.25% risk per trade, about 100 trades, stop if negative after 50 or beyond an 8R drawdown).
- **Do not build the per-strategy risk setting for SFP.** It is still not needed.
- **Do not run round 4 on top of this filter.** Round 4 (POC and value-area confluence) was conditional on F1 passing.
- **The effect, in plain words:** on Blind A, the BTC trades F1 kept were +0.09R per trade better than the ones it removed, with a standard error of 0.31R. That is about a third of one standard error: no evidence either way. The filter's main measurable effect is to remove about 89% of the candidate signals.
- **What Blind A says about V05 itself:** without any filter, V05 lost 0.14R per trade on 169 BTC trades in 2020-2023 (t −1.17) and 0.04R on 536 trades across BTC, ETH and ZEC. Its design-year +0.29R was a selection effect, as rounds 1 and 2 suspected. Blind A has now been used once. It should not be used to tune new SFP settings.
- **If SFP is looked at again,** the next idea should be scored on data that no round has used yet. The only such data now is forward data from 26 November 2026, which will take years at these trade counts, or other coins never used before. A trend filter that agrees with the higher timeframe removes most SFP signals by construction, since the setup trades against the recent move.

# 9. Limits

- **Binance, not BloFin, prices,** as in rounds 1 and 2.
- **Few trades after filtering.** F1 has 24 BTC trades in 46 months. The pass rule's trade-count condition already rules it out, whatever the R per trade.
- **Start of the data.** Binance's USD-M archives start on 31 December 2019 for BTC and ETH. The first week (30 December to 5 January) is built from six days, so its weekly level and its weekly VWAP start on 31 December. ZEC starts on 5 February 2020.
- **Funding** is applied from Binance's monthly funding files. No November 2026 file was published yet, so trades open in November 2026 carry no funding (as in rounds 1 and 2).
- **Costs are modelled, not observed,** and one minute is the finest detail (stop first inside a minute, limit fills only on a trade-through), as in rounds 1 and 2.
- **The VWAP uses Binance USD-M 1h volume.** The decision is taken at the reclaim close, while the limit order fills later at the swept level. The filter does not re-check the VWAP at the fill.
- **One position per coin,** per variant. A filtered-out signal never blocks the coin.

# 10. Method and reproduction

Scripts are in `research/backtests/sfp_r3/`. They import round 1's modules and round 2's engine and truncation test from `research/backtests/sfp_r2/`, unchanged.

| Script | What it does |
|---|---|
| `vwap.py` | Weekly-open and monthly-open anchored VWAP from 1h candles, and the F0-F3 verdicts |
| `run_r3.py` | Every V05 candidate (round 1's signals, checks and exit E0) on every coin and period, tagged with the F0-F3 verdicts and its loss reason; round 1's V01 BTC trades for section 7 |
| `step0_r3.py` | The look-ahead check and its planted-leak control |
| `tables_r3.py` | The tables in this report |

A variant's trades are its kept candidates after the one-position-per-coin rule (`engine_r2.portfolio`, signals taken in reclaim order as in round 1). Loss reasons are round 1's `classify.py`. The kept-versus-filtered comparison uses every candidate before the one-position rule; the 95% interval comes from 10,000 bootstrap resamples.

```bash
D=/path/outside/the/repo/sfp-data   # klines/, klines1m/, funding/ as in rounds 1 and 2
S=BTCUSDT,ETHUSDT,ZECUSDT
python3 -I research/backtests/download_klines.py $D/klines $S 15m,1h,4h,1d 2020-01 2026-11-28
python3 -I research/backtests/download_klines.py $D/klines1m $S 1m 2020-01 2026-11-28
python3 -I research/backtests/download_klines.py $D/funding $S funding 2020-01 2026-11-28
# TAO from 2024-04 and HYPE from 2025-05, same three commands. BTC and ETH also need the
# daily archives of 2019-12-31 (Binance has no monthly archive for December 2019).
cd research/backtests/sfp_r3
python3 -I run_r3.py $D $D/out3                                         # about 10 s
python3 -I step0_r3.py $D $D/out3 $D/out3                               # about 3 min
python3 -I step0_r3.py $D $D/out3 $D/out3 BTCUSDT,ETHUSDT,ZECUSDT --control leak
python3 -I tables_r3.py $D/out3 > $D/out3/tables.md
```
