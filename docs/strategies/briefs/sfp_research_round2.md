# Brief: SFP research round 2 (one exit change, BTC first)

Agreed with Sofien on 27 Nov 2026. Builds on round 1
(`docs/strategies/sfp_research_round1.md`, PR #538, scripts in `research/backtests/sfp_r1/`).

Read `AGENTS.md`, `CLAUDE.md` and `.cursor/rules/*.mdc` first. **Research only:** nothing
under `backend/src` or `frontend/`, no Render, no orders, never print secrets. Scripts in
`research/backtests/sfp_r2/`, report `docs/strategies/sfp_research_round2.md` (+ PDF).
One pull request.

## Step 0 — independent check of round 1 (do this first)

Round 1's author found and fixed one look-ahead bug (commit be25c52) after the first blind
run. Read the round-1 level, entry, stop and simulation code with fresh eyes and confirm
that no other step uses information that was not known at the decision time (levels from
unfinished candles, targets chosen with later highs/lows, ATR or volume averages that
include the signal candle's future, fills on the signal candle's own open, etc.). Report
what you checked and anything you found, with file:line. If you find a real bug, fix it in
round 2's copy and say how much it moves round 1's numbers.

## The question

Round 1 step 1: 32% of BTC SFP losers reached +1R before hitting the stop. Does taking a
bigger first profit at +1R and then moving the stop to break-even turn round 1's best setup
positive? This is the **only** change tested.

## Setup (from round 1, unchanged)

The round-1 primary variant **V05**: 1h execution, previous daily + weekly highs/lows,
confluence >= 2, limit entry at the swept level, wide stop (beyond the wick by
max(0.7%, 1 ATR)). Same data, costs (0.06% taker / 0.02% maker, 5 bps slippage per side on
market orders and stops, funding), live checks and 1-minute simulation as round 1.

## Exit variants (pre-registered, no others)

- **E0** round 1's exit (TP1 15% at 1R, TP2/TP3 at levels, 15% runner, no break-even): control.
- **E1** TP1 **50%** at 1R, then stop to break-even (entry plus fees), remaining 50% as round 1's
  TP2 (30%) and runner (20%).
- **E2** TP1 50% at 1R, then break-even, remaining 50% on the 5-candle trail only.
- **E3** TP1 **33%** at 1R, then break-even, TP2 33% at the next level, 34% trail.

Report for each: trades, win rate, R/trade after all costs, t-stat, max drawdown, worst
losing streak, and the share of losers that had reached +1R (should fall with E1-E3).

## Periods and honesty

- Design years (Nov 2023 – Oct 2025): the main evidence. BTC first, then the other coins
  separately, and all coins together.
- The last year (Nov 2025 – Nov 2026) **has been looked at once in round 1**, so it is no
  longer blind. Report it, clearly labelled "already seen", never use it to choose.
- With only E0-E3 there is little room to overfit; still, say plainly how big the effect is
  against its noise.

## Recommendation

If E1, E2 or E3 is clearly positive on BTC in the design years (>= +0.15R/trade after costs,
>= 4 trades a month on BTC alone or all coins), recommend it for a **small demo forward
test**: 0.25% risk per trade, about 100 trades, stop if negative after 50 trades or drawdown
beyond 8R. That needs a per-strategy risk setting in the bot; note it, do not build it.
Otherwise say SFP should stay paused, and what round 3 could test (volume profile and
anchored VWAP as a confluence score, from Sofien's brief).

When done, reply to the AlphaTrade handoff session (session_01XvNEQ9YSaU5vLBKTSBU2sb) with the
PR link and a 5-line summary.
