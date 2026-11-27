# SFP refinement brief for Claude Code (from Sofien, 26 Nov 2026)

## Decision
SFP stays. We are **refining** it, not dropping it. I trade SFPs manually with success, mainly on BTC, on higher-timeframe levels. The current bot version (15m, levels from the last 6 candles, stop just past the wick, target at the nearest opposite level) does not represent how I trade it. The goal of this round is to find the best version: which levels, which timeframe, entry, stop, take-profit.
Demo orders stay paused; keep recording every signal.

## Answers to your open points
1. **Pause:** keep the demo paused until a version passes the gate below.
2. **Research round:** yes. Build it around the levels I actually trade (section below), with the variants fixed in advance.
3. **Candidates from your doc:**
   - #1 (older levels, 96 candles): keep.
   - #2 (1h/4h chart): keep, but with real HTF levels (see below), not the bot's own short-lookback detector.
   - #3 (BTC first): keep as the primary market, because that's where I see SFPs respected. Report the other coins separately. BTC trade counts are small, so judge BTC across the full 3 years, not on one period alone.
   - #4 (retest limit entry at the swept level): keep. Model non-fills honestly, since limit orders fill more often on the losers.
   - #5 (stop at least 0.7% / 1 ATR beyond the wick): keep, and test it as one of the stop options below.
   - #6 (TP1 1R, 5-candle trail, no break-even): keep as the default exit.
4. **Inconsistency:** the "97% refused with a 1% minimum sweep" figure doesn't match the live 0.3% minimum sweep. Please say which version it comes from.

## Levels I want tested (the core of this round)
- **Higher-timeframe highs and lows:** previous 1h, 4h, daily, weekly and monthly highs/lows; equal highs/lows on those timeframes.
- **Volume profile:** daily POC, monthly POC, value area high (VAH) and value area low (VAL), including naked/untested POCs from previous sessions.
- **Anchored VWAP:** anchored to the weekly open, the monthly open, and major swing highs/lows.
- **Confluence:** a level scores higher when 2+ of the above sit within ~0.2% of each other.
- **Volume on the sweep:** require (or score) above-average volume on the sweep or reclaim candle, e.g. at least 1.5x the 20-candle average, as an evidence-of-trapped-traders filter.

## Structure per trade to test
- **Level timeframe vs execution timeframe:** levels from 1h/4h/daily; sweep and reclaim judged on 15m and on 1h.
- **Entry options:** (a) market after the reclaim close, (b) market after confirmation, (c) limit retest of the swept level.
- **Stop options:** (a) beyond the sweep wick plus buffer, (b) beyond the wick plus max(0.7%, 1 ATR), (c) beyond the higher-timeframe candle that did the sweep.
- **Take-profit options:** (a) TP1 at 1R plus a trail, (b) the next HTF level, POC or VWAP in the trade direction, (c) the opposite side of the value area (VAL to VAH or back).

## Show me how it fails (required before any tuning)
For the last 2–3 years (Nov 2023 to Nov 2026), BTC first:
- Pick 10–15 losing SFP trades and 5 winners, each with the date/time (Tunis time), level type, entry, stop, exit, R result, and a chart image (PNG) marked with the level, sweep wick, entry, stop and exit.
- Classify every loss: level broke for real; stop hunted twice (wick retest); target too close / RR refused; fees too large vs the stop; chop/no follow-through; other.
- Give a table of loss reasons by % for the current version and for the HTF-level version.
I want to see with my own eyes whether these losses are trades I would have taken manually.

## Rules to avoid fooling ourselves
- Split: Nov 2023 to Oct 2025 for design, Nov 2025 to Nov 2026 blind. Fix the variant list before looking at the blind year.
- At most ~12 pre-registered variants; report every one, not just the best.
- Costs: 0.06% per side taker / 0.02% maker, plus 5 bps of slippage per side on market orders, plus funding.
- **Gate to go back on the demo:** positive in both periods; blind year at least 50 trades (BTC-only allowed at least 25 if all 3 years are positive); at least +0.15R per trade after costs; no single coin above 50% of profit.
- **Demo after passing:** about 100 trades at 0.25% risk; stop if it's negative after 50 trades or the drawdown passes 8R.

## Deliverables
1. A short report (markdown) with the loss-reason tables, the variant results table (both periods, trades, R/trade, t-stat, max DD), and a recommendation.
2. A chart folder for the failure and winner examples.
3. Committed, reproducible scripts. Don't push anything that changes live trading behaviour.

## Note
I may refine this further with AI Nerd 2 or with other trading agents (Trade Hunter, Trading Council). Keep the level/entry/stop/target logic modular so new variants are easy to add.
