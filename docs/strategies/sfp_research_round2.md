---
title: "AlphaTrade: SFP research round 2"
subtitle: "A bigger first take-profit and a break-even stop on round 1's best setup, BTC first"
date: "27 November 2026"
---

# 1. Summary

**Question.** In round 1, 32% of the BTC SFP losers reached +1R and then came back to the stop. Does taking a bigger first profit at +1R and then moving the stop to break-even turn round 1's best setup (V05) positive? This was the only change tested.

**Answer: no. It makes BTC worse.**

- **Step 0, the look-ahead check of round 1: nothing found.** Every decision of the V05 pipeline was re-run on data cut off at the moment the decision is made: 712 trades on five coins, 0 differences. The same test catches round 1's own earlier bug when it is put back, so it can see this kind of problem. Four smaller fill-model questions move the results by at most 0.02R per trade (section 2).
- **On BTC in the design years** (Nov 2023 to Oct 2025), the three new exits made 0.08R, 0.02R and 0.09R per trade, against 0.29R for round 1's exit. On the same signals, they lost 0.18R to 0.23R per trade compared with round 1's exit, about two standard errors. None of them reaches the +0.15R needed for a demo test.
- **Why:** the change did what it was meant to do. Losers that had reached +1R went from 30% to 0%, the win rate went from 36% to 56%, and the worst losing streak went from 12 to 5. But on BTC more trades went on to win after +1R (56) than came back to lose (34). Taking 50% at +1R cut those winners by more (−74R) than it saved on the losers (+44R).
- **All five coins (design years):** −0.09R to −0.13R per trade with the new exits, against −0.07R with round 1's exit. On these coins the exit made no real difference (−0.02R to −0.05R per trade, well inside the noise). The setup itself loses there.
- **The last year** (Nov 2025 to Nov 2026, already seen in round 1, never used to choose): BTC −0.10R to −0.14R per trade with the new exits, against −0.18R. The new exits lost a little less but still lost.

**Recommendation: keep SFP paused.** No exit qualifies for the demo test, so the per-strategy risk setting is not needed yet. Round 3 should test new information instead of changing exits again: a confluence score from volume profile (POC, VAH, VAL, naked POCs) and anchored VWAP (weekly open, monthly open, major swings), from Sofien's brief. It should be fixed in advance and scored on data from 26 November 2026 on, which no round has used (section 5).

All results are in **R** (one R is the amount risked on a trade), after fees, slippage and funding, on Binance USD-M prices.

# 2. Step 0: independent look-ahead check of round 1

Round 1's author found and fixed one look-ahead bug (commit be25c52: the take-profit check for untaken levels read the 1h candle that was still open at entry). This step reviewed the rest of round 1's V05 path from scratch.

## 2.1 Reading the code

Paths are on the round-1 branch (`research/backtests/sfp_r1/`). The modules are copied unchanged into `research/backtests/sfp_r2/` (same line numbers); the simulator is `research/backtests/bt3y_common.py`.

| What | Where | What was checked | Verdict |
|---|---|---|---|
| Daily, weekly and monthly candles | `data.py:56-77` | Weeks start on Monday (UTC). A weekly or monthly level becomes known when its candle closes, which is the next period's start | OK |
| Level start and life | `levels.py:49-64` | A level exists only from its candle's close (`start = close[k]`) and is tradable during the next candle only (V05: previous level) | OK |
| First sweep | `signals.py:33-46` | It searches only execution candles that open after the level is known | OK |
| Reclaim | `signals.py:63-69` | Uses the close of the sweep candle or the next candle. The setup is complete only once that candle closes | OK |
| Confluence | `signals.py:72-75`, `levels.py:103-108` | Counts levels already known at the sweep candle's open and not swept before it | OK |
| Volume ratio | `signals.py:76` | The mean is over the 20 candles before the sweep (V05 does not use the volume filter) | OK |
| Limit entry | `entries.py:47-65` | The order is placed at the open of the candle after the reclaim close and is valid for 6 candles. It fills only when a 1-minute low trades strictly below the level, at the level or at that minute's open if lower | OK; see (a) |
| Pattern extreme | `entries.py:21-22` | The lowest low of the sweep to reclaim candles, all closed | OK |
| Stop and ATR | `stops.py:21-31`, `engine.py:91`, `bt3y_common.py:100-112` | The ATR at index *k* includes candle *k*. The stop uses the reclaim candle's ATR, which is closed before the order goes in | OK |
| TP2/TP3 levels | `exits.py:26-50` | Only candles of each level timeframe closed by the fill, and only 15m candles closed by the fill to judge "untaken" (the be25c52 fix) | OK |
| Live checks | `bt3y_common.py:142-163`, `engine.py:98` | Use only the entry, stop and ladder | OK |
| One position per coin | `engine.py:115-125` | Uses the exit time of the trade already open, which is known when the next signal comes | OK; see (b) |
| 1-minute simulation | `bt3y_common.py:166-266` | Stop checked before targets in each minute, and only the stop in the fill minute. Break-even and the trailing stop apply from the next minute. The trail uses only closed candles (`k - trail .. k - 1`) | OK; see (c) |
| Funding | `bt3y_common.py:256-264` | Only funding times between entry and exit, on the part of the position still open | OK |

## 2.2 Testing it on the data (truncation test)

Reading code can miss things, so every V05 trade (design and last year, all five coins) was decided a second time on data cut off at two moments (`sfp_r2/step0.py`):

- **at the order time** (the reclaim candle has just closed): the signal (side, sweep and reclaim candles, level, confluence), the pattern extreme and the stop;
- **one minute after the fill:** the fill time and price, the TP1/TP2/TP3 ladder and the live-check verdict.

A cut keeps only candles closed by then. The candle still open is rebuilt from the 1-minute bars up to that moment, which is what a live bot sees. If any step read a later candle, a later high or low, or a later average, it would give a different answer on the cut data.

| Coin | Trades decided again | Differences at the order time | Differences at the fill |
|---|---|---|---|
| BTC | 174 | 0 | 0 |
| ETH | 150 | 0 | 0 |
| ZEC | 169 | 0 | 0 |
| TAO | 130 | 0 | 0 |
| HYPE | 89 | 0 | 0 |

**The test can catch this kind of bug** (`sfp_r2/step0_controls.py`). With round 1's original bug put back (the take-profit check before be25c52), it flags 1 of the 174 BTC trades (a different ladder). That is also how small that bug was for V05. With a stop that uses the next candle's ATR, it flags all 174.

## 2.3 Smaller modelling points (not look-ahead), and how much they matter

- **(a) Limit fills better than the limit.** A limit order that fills on a minute opening beyond the level gets that minute's open price. This happened 4 times in 918 limit fills (ZEC 3, TAO 1, none on BTC). Forcing every fill to the limit price changes nothing on BTC and at most 0.01R per trade on all five coins.
- **(b) Order of overlapping signals.** When two limit orders on one coin overlap, round 1 takes the signal whose reclaim came first, even if the other order filled first. Taking the first fill instead changes nothing on BTC and moves all five coins in the design years from −0.07R to −0.09R per trade (470 trades instead of 469).
- **(c) Take-profits fill on a touch, the limit entry only on a trade-through.** Requiring price to trade one tick beyond each target changes nothing on BTC and at most 0.01R per trade on all five coins.
- **(d) V05 was chosen from 882 settings on the BTC design years.** This is selection, not look-ahead. Its +0.29R design result is the best of many tries and is probably too high; the last year (−0.18R) points the same way. Every exit in this round is measured on that selected setup, so its design-year numbers flatter all four exits equally.

**Step 0 result: no real bug found, nothing to fix, round 1's numbers stand.** E0 below reproduces round 1's V05 exactly (BTC design 121 trades, +0.29R; last year 53 trades, −0.18R).

To be complete: while checking the round-2 code ran correctly, BTC results for all four exits were printed once before the truncation test had finished. The four exits were fixed in the brief and nothing was chosen or changed afterwards.

# 3. The exit test

**Setup (round 1's V05, unchanged).** 1h chart, previous daily and weekly highs and lows, confluence of at least 2 levels within 0.2%, limit entry at the swept level (valid 6 candles, maker fee), stop beyond the wick by max(0.7%, 1 ATR14). Costs: 0.06% taker and 0.02% maker per side, 5 bps slippage on market orders and on every stop, and Binance funding. Simulated on 1-minute bars, one position per coin.

**Exits (fixed in the brief, no others):**

| Exit | TP1 at +1R | After TP1 | Rest |
|---|---|---|---|
| E0 (round 1, control) | 15% | stop unchanged | TP2 50% at the next level, TP3 20% at the one after, 15% runner |
| E1 | 50% | stop to break-even | TP2 30% at the next level, 20% runner |
| E2 | 50% | stop to break-even | 50% runner |
| E3 | 33% | stop to break-even | TP2 33% at the next level, 34% runner |

"Next level" is round 1's TP2 price: the next untaken daily or weekly high or low beyond TP1, or 2R when there is none. Break-even means entry plus the round-trip taker fee (0.12%), as the live bot's `breakeven_stop` does, from the minute after TP1 fills. The runner trails the last 5 closed 1h candles.

**Same signals for every exit.** Which signals are traded is decided by round 1's live checks on round 1's ladder, so E0 to E3 trade the same signals and differ only in how they exit. The one-position-per-coin rule still applies to each exit, so an exit that closes trades sooner can take a few more signals. (The bot's reward check counts a runner as zero, so E2's own ladder, 50% at +1R and the rest trailing, could never pass it. See section 3.4.)

## 3.1 BTC, design years (the main evidence)

| Exit | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losers that reached +1R | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| E0 | 121 | 5.0 | 36% | +0.29R | +35.2R | 1.68 | −12.9R | 12 | 30% | 18/24 |
| E1 | 140 | 5.8 | 56% | +0.08R | +11.0R | 0.78 | −12.3R | 5 | 0% | 15/24 |
| E2 | 149 | 6.2 | 56% | +0.02R | +3.4R | 0.25 | −11.8R | 5 | 0% | 13/24 |
| E3 | 140 | 5.8 | 56% | +0.09R | +12.5R | 0.82 | −13.1R | 5 | 0% | 14/24 |

**How big the change is compared with the noise.** On the same 161 BTC signals (each simulated with both exits, before the one-position rule):

| Exit | Change vs E0 (R per trade) | Standard error | t | 95% interval (bootstrap) |
|---|---|---|---|---|
| E1 | −0.19 | 0.10 | −1.92 | −0.38 to −0.00 |
| E2 | −0.23 | 0.11 | −2.17 | −0.43 to −0.03 |
| E3 | −0.18 | 0.10 | −1.90 | −0.37 to −0.00 |

All three new exits are worse than round 1's exit on BTC, by about two standard errors. With only E0 to E3 tested there was little room for luck in choosing. The direction is clear, but the exact size is not: the intervals run from almost zero to about −0.4R.

**Where the change comes from** (BTC design, same signals, total R of new exit minus E0):

| Exit | E0 never reached +1R | E0 reached +1R, then lost | E0 reached +1R and won | Total |
|---|---|---|---|---|
| E1 | 71 signals, 0.0R | 34 signals, +43.6R | 56 signals, −73.9R | −30.3R |
| E2 | 71 signals, 0.0R | 34 signals, +48.6R | 56 signals, −85.6R | −37.0R |
| E3 | 71 signals, 0.0R | 34 signals, +38.3R | 56 signals, −67.7R | −29.4R |

- Trades that never reached +1R are the same under every exit.
- **The 34 losers that had reached +1R were saved**: about +1.3R each (from about −0.8R to about +0.45R with E1).
- **The 56 trades that went on to win after +1R lost about the same amount each**, because 50% (or 33%) of the position closed at +1R instead of at TP2 or later. With E1, 51 trades hit TP1 and then the break-even stop. In 17 of them, round 1's exit had gone on to reach TP2.
- On BTC, a trade that reached +1R went on to win 62% of the time (56 of 90). The bigger TP1 only pays when that share is below about 50%. On BTC in the design years it was not.

**By year** (trades / R per trade; November to October, then the already-seen year):

| Exit | Nov 23-Oct 24 | Nov 24-Oct 25 | Nov 25-Nov 26 (already seen) |
|---|---|---|---|
| E0 | 64 / +0.23R | 57 / +0.35R | 53 / −0.18R |
| E1 | 72 / −0.03R | 68 / +0.19R | 64 / −0.11R |
| E2 | 75 / −0.05R | 74 / +0.09R | 70 / −0.14R |
| E3 | 72 / −0.03R | 68 / +0.21R | 64 / −0.10R |

E1 and E3 were positive in one design year only.

## 3.2 The other coins and all five, design years

**Other four (ETH, ZEC, TAO, HYPE)**

| Exit | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losers that reached +1R | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| E0 | 348 | 14.5 | 19% | −0.20R | −69.9R | -2.20 | −94.6R | 21 | 35% | 8/24 |
| E1 | 380 | 15.8 | 47% | −0.16R | −59.4R | -2.77 | −85.2R | 9 | 0% | 8/24 |
| E2 | 395 | 16.5 | 48% | −0.18R | −71.7R | -3.67 | −89.7R | 9 | 0% | 8/24 |
| E3 | 380 | 15.8 | 47% | −0.17R | −65.6R | -2.85 | −91.0R | 9 | 0% | 8/24 |

**All five**

| Exit | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losers that reached +1R | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| E0 | 469 | 19.5 | 23% | −0.07R | −34.7R | -0.90 | −95.7R | 19 | 34% | 11/24 |
| E1 | 520 | 21.7 | 50% | −0.09R | −48.4R | -1.88 | −84.1R | 8 | 0% | 10/24 |
| E2 | 544 | 22.7 | 50% | −0.13R | −68.4R | -2.89 | −91.2R | 9 | 0% | 9/24 |
| E3 | 520 | 21.7 | 50% | −0.10R | −53.1R | -1.92 | −90.4R | 8 | 0% | 11/24 |

Change vs E0 on the same signals: other four +0.02R to +0.05R per trade (t 0.3 to 0.9); all five −0.02R to −0.05R (t −0.3 to −0.9). Neither is distinguishable from zero. On the other coins, losers that reached +1R and winners after +1R roughly balance (156 against 139 signals on all five), so the exit barely matters there. The setup loses before the exit comes into play.

## 3.3 The last year (already seen; reported, not used to choose)

Round 1 already looked at this year once, so it is no longer a blind test.

| Coins | E0 | E1 | E2 | E3 |
|---|---|---|---|---|
| BTC | 53 / −0.18R | 64 / −0.11R | 70 / −0.14R | 64 / −0.10R |
| Other four | 190 / +0.14R | 243 / +0.06R | 252 / +0.06R | 243 / +0.06R |
| All five | 243 / +0.07R | 307 / +0.02R | 322 / +0.02R | 307 / +0.03R |

BTC in full:

| Exit | Trades | Per month | Win rate | R per trade | Net | t-stat | Max drawdown | Worst losing streak | Losers that reached +1R | Green months |
|---|---|---|---|---|---|---|---|---|---|---|
| E0 | 53 | 4.1 | 28% | −0.18R | −9.5R | -0.87 | −18.7R | 13 | 29% | 6/13 |
| E1 | 64 | 4.9 | 48% | −0.11R | −7.1R | -0.75 | −16.8R | 4 | 0% | 7/13 |
| E2 | 70 | 5.4 | 50% | −0.14R | −10.0R | -1.13 | −16.9R | 4 | 0% | 4/13 |
| E3 | 64 | 4.9 | 48% | −0.10R | −6.7R | -0.64 | −18.5R | 4 | 0% | 7/13 |

On BTC this year, fewer trades went on after +1R (20 winners after +1R against 16 losers), so the new exits lost a little less than E0 (+0.03R to +0.09R per trade on the same signals, t below 1.1). They still lost money. On the other four coins the new exits did worse than E0. Nothing here changes the conclusion, and nothing here was used to choose.

## 3.4 Checks on the result

| Check | Coins | Period | E0 | E1 | E2 | E3 |
|---|---|---|---|---|---|---|
| Base (as above) | BTC | design | 121 / +0.29R | 140 / +0.08R | 149 / +0.02R | 140 / +0.09R |
| Targets fill only when price trades through them | BTC | design | 121 / +0.29R | 140 / +0.08R | 149 / +0.02R | 140 / +0.09R |
| Limit entry never better than its price | BTC | design | 121 / +0.29R | 140 / +0.08R | 149 / +0.02R | 140 / +0.09R |
| Each exit's own ladder must pass the bot's reward check | BTC | design | 121 / +0.29R | 105 / +0.15R | 0 | 92 / +0.19R |
| Each exit's own ladder must pass the bot's reward check | BTC | already seen | 53 / −0.18R | 43 / −0.23R | 0 | 38 / −0.17R |
| Each exit's own ladder must pass the bot's reward check | All five | design | 469 / −0.07R | 450 / −0.09R | 0 | 422 / −0.09R |

- The fill-model checks change nothing on BTC.
- **The bot's reward check would refuse every E2 trade.** It counts a runner as zero, and 50% at +1R is only half the risk. If the bot ran E1 or E3 with its own checks, it would also refuse 16% to 44% of the signals (the ones whose TP2 is close; 31% to 44% on BTC).
- That filter happens to lift E3 to +0.19R on BTC in the design years, but on only 3.8 trades a month, −0.17R in the last year, and no better than E0 on all five coins. It filters signals rather than changing the exit, it was not one of the pre-registered tests, and it is not a candidate.

# 4. Recommendation

**The demo-test condition** (brief): E1, E2 or E3 clearly positive on BTC in the design years, at least +0.15R per trade after costs, with at least 4 trades a month on BTC alone or on all coins.

| Exit | BTC design R per trade | Trades a month (BTC / all five) | Meets the condition |
|---|---|---|---|
| E1 | +0.08R | 5.8 / 21.7 | no |
| E2 | +0.02R | 6.2 / 22.7 | no |
| E3 | +0.09R | 5.8 / 21.7 | no |

- **Keep SFP demo orders paused.** No exit qualifies for the small demo test (0.25% risk per trade, about 100 trades, stop if negative after 50 trades or beyond an 8R drawdown). The per-strategy risk setting that test would need is still not needed for SFP. It was not built.
- **Keep round 1's exit if SFP is ever revived.** On BTC, the small 15% TP1 with no break-even did better than a 33% or 50% TP1 with break-even. The 30% of losers that "gave back" +1R are the cost of letting winners reach TP2; on BTC that cost was worth paying.
- **Do not test more exit variants on this setup.** The trouble is the entry. On the four other coins V05 loses before the exit comes into play, and on BTC the design-year result comes from picking the best of 882 settings and did not repeat in the last year.

**What round 3 could test** (from Sofien's brief, still untested):

- **A confluence score from volume profile and anchored VWAP.** Volume profile: POC, VAH, VAL and naked POCs of the previous day and week. Anchored VWAP: from the weekly open, the monthly open and major swings. A swept daily or weekly level would count only when one of these sits within 0.2%, or the score would rank signals.
- **A higher-timeframe trend filter**, because "the level broke for real" is still the largest loss group (47% of V05's BTC losses in the design years).
- **Rules for an honest round 3:** fix the variants in advance (at most a handful), keep round 1's exit, and score them on data from 26 November 2026 on, which no round has used. Wait until there are enough trades: at about 5 BTC trades a month, 50 trades take about 10 months. If that is too slow, score on all five coins together with the gate's 50%-of-profit limit per coin.

# 5. Limits

- **Binance, not BloFin, prices,** as in round 1.
- **Few BTC trades:** 121 to 149 in the design years and 53 to 70 in the last year. The paired comparison helps (each signal is compared with itself), but the difference between exits is still only about 2 standard errors.
- **The design-year baseline is selected** (section 2.3 d). Selection inflates E0, but E1 to E3 run on the same signals, so the comparison between exits is fair. The absolute levels are probably too high.
- **Break-even = entry + 0.12%.** That is the round-trip taker fee; the live rule adds the actual costs per unit. A stop at break-even still pays 5 bps of slippage and the exit fee, so it ends close to 0R, not exactly at 0R.
- **One minute is the finest detail.** Inside a minute the stop is assumed to fill before any target, and break-even starts the minute after TP1.
- **No November 2026 funding file** had been published, so trades open in November 2026 carry no funding (as in round 1).

# 6. Method and reproduction

Scripts are in `research/backtests/sfp_r2/`. Round 1's modules (`data`, `levels`, `signals`, `entries`, `stops`, `exits`, `engine`, `stats`, `classify`) are copied there unchanged. `research/backtests/bt3y_common.py` gets the same small change round 1 made (the simulator also returns its fills).

| Script | What it does |
|---|---|
| `step0.py` | Truncation test: every V05 trade decided again on data cut off at the order time and at the fill |
| `step0_controls.py` | Negative controls: puts a known look-ahead bug back and checks that `step0.py` catches it |
| `engine_r2.py` | Exits E0 to E3 on round 1's signals; one position per coin |
| `run_r2.py` | Every coin, both periods, all exits, with the base run and three checks |
| `tables_r2.py` | The tables in this report |

```bash
D=/path/outside/the/repo/sfp-data   # same layout as round 1 (klines/, klines1m/, funding/)
S=BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT
python3 -I research/backtests/download_klines.py $D/klines $S 15m,1h,4h,1d 2023-06 2026-11-25
python3 -I research/backtests/download_klines.py $D/klines1m $S 1m 2023-06 2026-11-25
python3 -I research/backtests/download_klines.py $D/funding $S funding 2023-06 2026-11-25
cd research/backtests/sfp_r2
python3 -I step0.py $D $D/out2                                  # step 0 (about 1 minute)
python3 -I step0_controls.py $D $D/ctl targets                  # expect 1 of 174 flagged
python3 -I step0_controls.py $D $D/ctl atr                      # expect 174 of 174 flagged
python3 -I run_r2.py $D $D/out2                                 # the exit test
python3 -I tables_r2.py $D/out2 > $D/out2/tables.md
```

Periods: design = entries from 1 November 2023 to 31 October 2025; already seen = 1 November 2025 to 25 November 2026. The paired change uses every signal that passes round 1's checks, before the one-position rule, so each signal is compared with itself. The 95% interval comes from 10,000 bootstrap resamples of those per-signal differences.
