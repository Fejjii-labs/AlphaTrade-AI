---
title: "AlphaTrade: SFP research round 3"
subtitle: "An anchored-VWAP trend filter on round 1's V05, tested on 2020-2023 data no round has used"
date: "28 November 2026"
---

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
