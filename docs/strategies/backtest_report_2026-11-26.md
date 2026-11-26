---
title: "AlphaTrade: three-year strategy backtest with a blind test year"
subtitle: "D-line, Nested Continuation and SFP on BTC, ETH, ZEC, TAO and HYPE"
date: "26 November 2026"
---

# 1. Summary

We replayed the bot's D-line, Nested and SFP logic on three years of price history (November 2023 to November 2026), simulated every trade the way the BloFin demo bot places it, and charged fees on every fill.

Settings were chosen **only on the first two years** (November 2023 to October 2025). The chosen settings were then run **once** on the last year (November 2025 to November 2026), which the selection never saw. That last year is the "blind year".

Results are in **R**: one R is the amount risked on one trade. All figures are after fees.

**The rules that are live today do not hold up on the two earlier years.** They were picked on the last year (the earlier one-year report), so for them the two earlier years are the honest test.

| Live today | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| D-line (30m + 1h, stop ≥ 1%) | 610 | −29.1R | −0.048R | 452 | −16.8R | −0.037R |
| Nested (30m + 1h, N2 and later) | 626 | −12.1R | −0.019R | 464 | +54.2R | +0.117R |
| SFP (15m) | 100 | −30.2R | −0.302R | 89 | +11.4R | +0.128R |
| All three together, one position per coin | 1155 | −71.5R | −0.062R | 824 | +56.1R | +0.068R |

**Settings chosen on the two earlier years, then run on the blind year**

| Chosen in-sample | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| D-line: 1h, stop ≥ 1.5%, retest entry | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| Nested: 1h, N2 and later, stepped exits | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| SFP (bot detector): none passed; least bad shown | 251 | −1.4R | −0.006R | 144 | +5.7R | +0.039R |
| SFP at levels, all coins: 4h trigger at the weekly level | 120 | +30.6R | +0.255R | 89 | −45.8R | −0.515R |
| SFP at levels, BTC alone | 30 | +17.2R | +0.574R | 23 | −11.7R | −0.511R |

**The main lessons**

1. **Only Nested on the 1-hour chart held up.**
   - Stage N2 and later, 1h: positive in each of the three years, and it got better, not worse, in the blind year (+0.26R per trade on 125 trades).
   - It is still a thin edge: the first two years made only +13R on 148 trades.
2. **D-line is unproven.**
   - The best in-sample setting (1h, stop at least 1.5% away, entry on the retest of the line) made +0.32R per trade in the first two years and +0.04R in the blind year. That blind result cannot be told apart from zero.
   - D-line on 15m lost in both periods. D-line on 30m lost heavily in the first two years.
   - Today's live D-line rules lost in both periods once one position per coin is enforced.
3. **SFP has no setting worth running.**
   - Not one of 570 settings of the bot's SFP was profitable in the first two years.
   - The best "sweep of a previous 4h / 8h / daily / weekly level" design made +31R in the first two years and lost 46R in the blind year.
   - The live SFP's +11R in the blind year follows −30R in the two years before it.
4. **Which exit setting you pick matters much less than which chart and which strategy.**
   - TP1 at 0.75R, 1R or 1.5R, and a 3, 5 or 8 candle trail, differ by about 0.02R per trade or less.
   - Moving the stop to break-even after TP1 did not help: the chosen setting did better without it for all three strategies, in both periods.
5. **In-sample rank tells you almost nothing about the next year.** Across all settings of a strategy, the link between a setting's rank in the first two years and its rank in the blind year was close to zero (section 8). Picking the top of a grid is mostly picking luck.

**What to run on demo next, and what to drop**

| Strategy | Run on demo next | First two years | Blind year | Drop |
|---|---|---|---|---|
| Nested | 1h only, stage N2 and later, stepped exits: TP1 at 0.75R, no break-even move, 5-candle trail | +13.0R, +0.088R per trade, 148 trades | +32.5R, +0.260R per trade, 125 trades | 15m, 30m, 4h, stage N1 |
| D-line | Keep on demo only as a small test: 1h only, stop at least 1.5% away, entry on the retest, no break-even move | +40.3R, +0.317R per trade, 127 trades | +3.7R, +0.041R per trade, 90 trades | 15m, 30m, breakout entry at market |
| SFP | Nothing. Pause it. | none passed the rule | not applicable | the live 15m SFP and every levels design tested |

These are recommendations for the demo account only. Nothing in this study changed the bot.

# 2. Data and method

**Data**

- Binance USD-M perpetual candles from data.binance.vision, with `research/backtests/download_klines.py`.
- 15m, 30m, 1h, 2h, 4h and 1d candles, plus 1-minute candles for the simulation and Binance funding rates for a funding estimate.
- No 1-minute candle is missing for any coin in the period.

| Coin | History used | Length | Price at start | 1 Nov 2025 | 25 Nov 2026 |
|---|---|---|---|---|---|
| BTC | 1 Nov 2023 – 25 Nov 2026 | 36.8 months | 34,650 | 109,600 | 60,430 |
| ETH | 1 Nov 2023 – 25 Nov 2026 | 36.8 months | 1,816 | 3,846 | 1,692 |
| ZEC | 1 Nov 2023 – 25 Nov 2026 | 36.8 months | 28.3 | 404 | 771 |
| TAO | 11 Apr 2024 – 25 Nov 2026 | 31.5 months | 698 | 482 | 182 |
| HYPE | 30 May 2025 – 25 Nov 2026 | 17.9 months | 33.4 | 43.7 | 61.7 |

- TAO and HYPE were listed later, so they have less history. HYPE has only five months before the blind year, so its in-sample numbers are thin.
- Funding rates run to October 2026; November 2026 trades carry no funding estimate.

**The two periods**

- **First two years (in-sample):** entries from 1 November 2023 to 31 October 2025. All choices were made here.
- **Blind year:** entries from 1 November 2025 to 25 November 2026. Run once, after the choices were frozen.
- The first two years were mostly rising for BTC, ETH and ZEC. The blind year was falling for BTC, ETH and TAO and rising for ZEC and HYPE. So the blind year also tests a different market.

**Signals**

- The bot's own detectors, through the existing study scripts, with the parameters stored in the live strategies: `detect_dline_live`, `detect_nested` on a rolling 256-candle window, and `detect_sfp` with a 0.3% minimum sweep.
- "SFP at levels" (section 6) is a separate, simple definition written for this study: price goes through the previous 4h, 8h, daily or weekly high or low and closes back inside.

**Simulation, as the live demo bot trades**

- Market entry at the open of the candle after the signal. The retest entry is a limit order at the line, valid for 6 candles, with the maker fee.
- Live pre-trade checks on every trade, with the trade's own targets:
  - the planned reward must be at least 1× the risk, measured from the worse edge of the 10 bps entry zone, weighted by the size of each step, with the runner counted as zero;
  - fees must be under 20% of the risk (a stop at least about 0.6% away);
  - D-line: reward to TP2 at least 2× the risk at the quote.
- **One position per coin.** A new signal on a coin is skipped while that coin has an open trade. Per-strategy tables apply this within the strategy; section 7 applies it across all three strategies.
- Price moves through the trade on 1-minute candles. If a stop and a target are both inside the same minute, the stop is taken first.
- Stepped exits: TP1 closes 15%, TP2 50%, TP3 20%, and the last 15% trails the low (or high) of the last 3, 5 or 8 closed candles once the targets are filled. With break-even on, the stop moves to the entry plus fees after TP1.
- "Live exits" means what the bot does today: the D-line ladder with break-even and a 5-candle trail; one full-size target for Nested and SFP.
- Fees: 0.06% on a market entry and on every exit, 0.02% on a limit entry (BloFin's taker and maker rates).
- Funding is shown as an estimate only (Binance funding rates applied to the open size). It moved the totals by less than 1R for D-line, Nested and the bot's SFP, and by about 4R for the levels design, so the tables leave it out.

**How settings were chosen (fixed in code before the blind year was run)**

- A setting = charts used × filter (minimum stop distance, or minimum Nested stage) × exit scenario.
- To qualify, a setting needed at least 4 trades a month, a worst drawdown no deeper than 20R, and a positive result in each of the two in-sample years.
- Qualifying settings were ranked by the average result of the setting **and its neighbours** (one step away in each choice), so a wide good area beats one lucky point.
- The top one was frozen and run on the blind year.

**Market regime**

- **Rising:** the coin is up more than 10% over the last 30 days at the signal. **Falling:** down more than 10%. **Sideways:** in between.
- "With the 4h trend" means a long above, or a short below, the 50-period average of the 4h closes.

**What was not tested**

- D-line on 4h: the bot's D-line detector accepts 15m, 30m and 1h only, and this study did not change bot code.
- Slippage beyond the 1-minute stop-first rule.

# 3. D-line

5,958 breakout signals over the three years, 15m, 30m and 1h.

| | Period | Trades | Per month | Win rate | Net | Per trade | Worst drawdown | Worst losing streak | Green months |
|---|---|---|---|---|---|---|---|---|---|
| Live today | 2 earlier years | 610 | 25.4 | 39% | −29.1R | −0.048R | −55.8R | 12 | 10/24 |
| | **blind year** | 452 | 35.3 | 44% | −16.8R | −0.037R | −33.9R | 9 | 6/13 |
| Chosen | 2 earlier years | 127 | 5.3 | 31% | +40.3R | +0.317R | −11.7R | 12 | 14/24 |
| | **blind year** | 90 | 7.0 | 23% | +3.7R | +0.041R | −7.9R | 7 | 5/13 |

- **Live today** = 30m and 1h, stop at least 1% away, market entry at the breakout, live exits.
- **Chosen** = 1h only, stop at least 1.5% away, limit entry at the line (retest), TP1 halfway to the structure high, no break-even move, 5-candle trail, TP2 and TP3 at the structure levels.
- The earlier report gave the live rules +52R for the blind year. That figure let 30m and 1h trades on the same coin overlap. With one position per coin, as the bot really trades, the same rules made −17R. This simulator, without the position rule, gives +35R on the same 555 trades (the old study's own figure on this data is +46R).
- Per year, chosen setting: Nov 2023 – Oct 2024 +21.0R on 49 trades (+0.428 ± 0.328R per trade); Nov 2024 – Oct 2025 +19.3R on 78 trades (+0.248 ± 0.212R per trade); blind year +3.7R on 90 trades (+0.041 ± 0.199R per trade). The "±" is one standard error: the blind-year result is well inside the noise.

**Chosen setting by group**

| Group | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| BTC | 12 | −2.9R | −0.239R | 4 | −0.6R | −0.160R |
| ETH | 26 | +17.4R | +0.670R | 9 | +11.3R | +1.253R |
| ZEC | 42 | +13.1R | +0.312R | 29 | −1.2R | −0.040R |
| TAO | 40 | +17.2R | +0.429R | 21 | −0.4R | −0.018R |
| HYPE | 7 | −4.5R | −0.648R | 27 | −5.3R | −0.198R |
| Long | 53 | +16.8R | +0.317R | 47 | +15.3R | +0.325R |
| Short | 74 | +23.5R | +0.317R | 43 | −11.6R | −0.269R |
| Market rising | 48 | +37.5R | +0.781R | 34 | −1.8R | −0.054R |
| Market falling | 43 | −8.4R | −0.195R | 38 | +13.1R | +0.345R |
| Market sideways | 30 | +4.1R | +0.136R | 18 | −7.6R | −0.420R |
| With the 4h trend | 67 | +15.1R | +0.225R | 45 | +2.3R | +0.051R |
| Against the 4h trend | 59 | +20.3R | +0.343R | 45 | +1.4R | +0.032R |

- ETH carried the result in both periods. BTC and HYPE lost in both.
- Shorts made money in the first two years and lost in the blind year. Regime results flipped between the periods. Neither is a stable filter.

**Scenarios, one change at a time from the chosen setting**

| Setting | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| Chart: 15m only | 146 | −34.1R | −0.234R | 87 | −3.8R | −0.044R |
| Chart: 30m only | 116 | −26.9R | −0.232R | 101 | +1.6R | +0.016R |
| Chart: 1h only | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| No minimum stop | 271 | +23.9R | +0.088R | 205 | +17.2R | +0.084R |
| Stop at least 1% | 214 | +17.5R | +0.082R | 143 | +19.7R | +0.138R |
| Stop at least 1.5% | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| Entry: market at the breakout | 190 | +38.5R | +0.203R | 141 | −6.8R | −0.048R |
| Entry: retest of the line | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| Live exits | 130 | +36.2R | +0.278R | 94 | +1.7R | +0.018R |
| TP1 halfway to the structure | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| TP1 at 0.75R | 127 | +38.6R | +0.304R | 90 | +3.5R | +0.038R |
| TP1 at 1R | 127 | +40.5R | +0.319R | 90 | +4.0R | +0.044R |
| TP1 at 1.5R | 127 | +40.3R | +0.318R | 90 | +3.5R | +0.039R |
| Break-even after TP1: on | 131 | +33.2R | +0.253R | 94 | +0.8R | +0.008R |
| Break-even after TP1: off | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| Trail 3 candles | 127 | +41.3R | +0.325R | 90 | +4.9R | +0.055R |
| Trail 5 candles | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| Trail 8 candles | 127 | +39.5R | +0.311R | 90 | +4.9R | +0.054R |
| TP2/TP3 at the structure levels | 127 | +40.3R | +0.317R | 90 | +3.7R | +0.041R |
| TP2/TP3 at the higher-timeframe swings | 127 | +31.1R | +0.245R | 91 | +9.3R | +0.103R |

- **Timeframe is the big choice.** 1h was positive in both periods; 15m negative in both; 30m lost 27R in the first two years.
- **Entry:** the retest entry beat the market entry in both periods, with about a third fewer trades.
- **Minimum stop:** 1.5% was best in-sample, 1% best in the blind year. Either is better than no minimum in-sample. The evidence does not separate 1% from 1.5%.
- **TP2/TP3 at the structure high or at the higher-timeframe swing:** structure was better in-sample, the swing better in the blind year. No clear winner.
- **TP1 level and trail length:** no real difference.

# 4. Nested Continuation

24,094 signals over the three years, 15m, 30m, 1h and 4h.

| | Period | Trades | Per month | Win rate | Net | Per trade | Worst drawdown | Worst losing streak | Green months |
|---|---|---|---|---|---|---|---|---|---|
| Live today | 2 earlier years | 626 | 26.1 | 39% | −12.1R | −0.019R | −32.6R | 11 | 12/25 |
| | **blind year** | 464 | 36.2 | 43% | +54.2R | +0.117R | −20.5R | 12 | 8/13 |
| Chosen | 2 earlier years | 148 | 6.2 | 41% | +13.0R | +0.088R | −14.2R | 9 | 12/24 |
| | **blind year** | 125 | 9.8 | 51% | +32.5R | +0.260R | −8.0R | 7 | 10/13 |

- **Live today** = 30m and 1h, stage N2 and later, one full-size target.
- **Chosen** = 1h only, stage N2 and later, stepped exits: TP1 at 0.75R, TP2 at whichever comes first of the measured move and the nearest higher-timeframe swing, no break-even move, 5-candle trail.
- Per year, chosen setting: Nov 2023 – Oct 2024 +11.4R on 66 trades (+0.172 ± 0.177R per trade); Nov 2024 – Oct 2025 +1.6R on 82 trades (+0.020 ± 0.142R per trade); blind year +32.5R on 125 trades (+0.260 ± 0.112R per trade).
- The live rules lost a little in the first two years (−12R) and made +54R in the blind year, the year they were picked on.

**Chosen setting by group**

| Group | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| BTC | 28 | +1.1R | +0.038R | 15 | +2.5R | +0.170R |
| ETH | 37 | −1.2R | −0.034R | 16 | +1.2R | +0.077R |
| ZEC | 34 | −3.9R | −0.115R | 35 | +15.7R | +0.448R |
| TAO | 42 | +14.7R | +0.350R | 31 | +7.9R | +0.255R |
| HYPE | 7 | +2.4R | +0.344R | 28 | +5.1R | +0.183R |
| Long | 79 | +3.6R | +0.045R | 52 | +16.0R | +0.308R |
| Short | 69 | +9.4R | +0.137R | 73 | +16.4R | +0.225R |
| Market rising | 51 | −8.2R | −0.161R | 46 | +9.2R | +0.200R |
| Market falling | 40 | +0.6R | +0.016R | 50 | +12.2R | +0.245R |
| Market sideways | 54 | +17.5R | +0.325R | 29 | +11.1R | +0.381R |
| With the 4h trend | 109 | +25.3R | +0.232R | 90 | +23.9R | +0.265R |
| Against the 4h trend | 39 | −12.2R | −0.314R | 35 | +8.6R | +0.245R |
| Stage N2 | 127 | +2.7R | +0.021R | 105 | +33.4R | +0.318R |
| Stage N3 | 17 | +9.1R | +0.536R | 18 | −2.2R | −0.123R |
| Stage N4+ | 4 | +1.2R | +0.300R | 2 | +1.3R | +0.644R |

- Every coin was positive in the blind year; TAO was the steadiest across both periods.
- **With the 4h trend** was positive in both periods (+0.23R and +0.27R per trade); against it lost in the first two years. This was noticed after the fact and was not part of the selection, so treat it as an idea to check on demo, not a result.
- N3 is too rare on 1h to judge (17 and 18 trades).

**Scenarios, one change at a time from the chosen setting**

| Setting | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| Chart: 15m only | 474 | +1.0R | +0.002R | 313 | −39.7R | −0.127R |
| Chart: 30m only | 291 | −10.5R | −0.036R | 206 | −0.2R | −0.001R |
| Chart: 1h only | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| Chart: 4h only | 56 | −14.5R | −0.259R | 38 | −9.9R | −0.261R |
| Stage N1 and later | 535 | −63.9R | −0.119R | 332 | +10.9R | +0.033R |
| Stage N2 and later | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| Stage N3 and later | 35 | +7.1R | +0.203R | 42 | −5.7R | −0.136R |
| Live exits | 255 | −7.5R | −0.029R | 199 | +41.2R | +0.207R |
| TP1 at 0.75R | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| TP1 at 1R | 232 | +1.0R | +0.004R | 189 | +23.2R | +0.123R |
| TP1 at 1.5R | 330 | +6.4R | +0.020R | 236 | +19.3R | +0.082R |
| Break-even after TP1: on | 158 | −5.4R | −0.034R | 133 | +17.4R | +0.131R |
| Break-even after TP1: off | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| Trail 3 candles | 150 | +11.4R | +0.076R | 125 | +33.0R | +0.264R |
| Trail 5 candles | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |
| Trail 8 candles | 148 | +11.7R | +0.079R | 122 | +33.6R | +0.275R |
| TP2 at the measured move | 229 | +4.3R | +0.019R | 177 | +58.9R | +0.333R |
| TP2 at the nearest higher-timeframe swing | 169 | +11.8R | +0.070R | 146 | +30.8R | +0.211R |
| TP2 at whichever comes first | 148 | +13.0R | +0.088R | 125 | +32.5R | +0.260R |

- **Timeframe:** 1h is the only chart positive in both periods. 4h lost in both. 15m lost 40R in the blind year. 30m was about flat or negative.
- **Stage:** adding N1 turns the first two years to −64R. Requiring N3 leaves too few trades and lost in the blind year.
- **Exits:** the stepped exits beat the live single target in-sample; the single target did well in the blind year too (+0.21R). Break-even after TP1 cost about 0.12R per trade in both periods.
- **TP2 placement:** the measured move alone was best in the blind year (+59R) but near zero in-sample. "Whichever comes first" was the most even.

# 5. SFP (the bot's detector)

14,248 signals over the three years, 15m, 30m, 1h and 4h, 0.3% minimum sweep.

| | Period | Trades | Per month | Win rate | Net | Per trade | Worst drawdown | Worst losing streak | Green months |
|---|---|---|---|---|---|---|---|---|---|
| Live today | 2 earlier years | 100 | 4.2 | 32% | −30.2R | −0.302R | −30.7R | 11 | 7/22 |
| | **blind year** | 89 | 6.9 | 51% | +11.4R | +0.128R | −11.8R | 6 | 7/13 |
| Least bad | 2 earlier years | 251 | 10.5 | 35% | −1.4R | −0.006R | −24.0R | 8 | 13/25 |
| | **blind year** | 144 | 11.2 | 37% | +5.7R | +0.039R | −13.5R | 7 | 6/13 |

- **Live today** = 15m, one full-size target.
- **No setting passed the selection rule.** Of 570 settings, none was profitable over the first two years with at least 50 trades.
- The row "least bad" is the top-ranked setting that at least traded 4 times a month (4h, stepped exits, TP1 at 1R, no break-even). It is shown for completeness, not as a pick.

**The least bad setting, one change at a time**

| Setting | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| Chart: 15m only | 1629 | −172.5R | −0.106R | 1123 | −197.0R | −0.175R |
| Chart: 30m only | 1136 | −54.8R | −0.048R | 721 | −45.7R | −0.063R |
| Chart: 1h only | 769 | −85.8R | −0.112R | 467 | −10.8R | −0.023R |
| Chart: 4h only | 251 | −1.4R | −0.006R | 144 | +5.7R | +0.039R |
| Live exits | 34 | −14.3R | −0.420R | 17 | +2.5R | +0.144R |
| TP1 at 0.75R | 210 | −3.7R | −0.018R | 134 | +2.0R | +0.015R |
| TP1 at 1R | 251 | −1.4R | −0.006R | 144 | +5.7R | +0.039R |
| TP1 at 1.5R | 256 | −11.3R | −0.044R | 139 | −1.9R | −0.014R |
| Break-even after TP1: on | 290 | −9.8R | −0.034R | 192 | −16.7R | −0.087R |
| Break-even after TP1: off | 251 | −1.4R | −0.006R | 144 | +5.7R | +0.039R |
| Trail 3 candles | 256 | −4.9R | −0.019R | 145 | +7.7R | +0.053R |
| Trail 5 candles | 251 | −1.4R | −0.006R | 144 | +5.7R | +0.039R |
| Trail 8 candles | 242 | −3.0R | −0.012R | 142 | +6.1R | +0.043R |

- 15m, 30m and 1h with stepped exits lost in both periods.
- The live 15m SFP: −30R on 100 trades in the first two years, then +11R on 89 trades in the blind year. Three years together: −19R.

# 6. SFP at the previous 4h, 8h, daily and weekly levels

108,180 patterns. 14,542 settings: trigger chart (15m to 4h) × level × same-candle or next-candle reclaim × confirmation candle × stop one tick or an ATR buffer beyond the wick × market or retest entry × trend filter × exits; all coins or BTC alone.

**The design from the strategy spec** (same-candle reclaim, one-tick stop, market entry, TP1 at 1R, break-even, 5-candle trail), all coins:

| Trigger and level | Trades (earlier) | Net | Per trade | Trades (blind) | Net | Per trade |
|---|---|---|---|---|---|---|
| 15m trigger at the previous 4h level | 1436 | −261.7R | −0.182R | 910 | −132.2R | −0.145R |
| 15m trigger at the previous 8h level | 918 | −158.8R | −0.173R | 573 | −98.0R | −0.171R |
| 15m trigger at the previous 1d level | 416 | −35.9R | −0.086R | 246 | −56.0R | −0.228R |
| 15m trigger at the previous 1w level | 82 | −6.3R | −0.077R | 54 | −26.2R | −0.486R |
| 30m trigger at the previous 4h level | 2103 | −263.3R | −0.125R | 1389 | −193.1R | −0.139R |
| 30m trigger at the previous 8h level | 1345 | −124.4R | −0.092R | 844 | −157.4R | −0.186R |
| 30m trigger at the previous 1d level | 574 | −52.9R | −0.092R | 359 | −96.3R | −0.268R |
| 30m trigger at the previous 1w level | 106 | −12.0R | −0.113R | 69 | −28.6R | −0.414R |
| 1h trigger at the previous 4h level | 2572 | −432.4R | −0.168R | 1692 | −220.0R | −0.130R |
| 1h trigger at the previous 8h level | 1624 | −175.8R | −0.108R | 1072 | −206.5R | −0.193R |
| 1h trigger at the previous 1d level | 723 | −96.1R | −0.133R | 438 | −93.2R | −0.213R |
| 1h trigger at the previous 1w level | 124 | −28.5R | −0.230R | 78 | −14.7R | −0.189R |
| 4h trigger at the previous 8h level | 1635 | −248.9R | −0.152R | 1097 | −173.4R | −0.158R |
| 4h trigger at the previous 1d level | 832 | −130.4R | −0.157R | 543 | −88.3R | −0.163R |
| 4h trigger at the previous 1w level | 152 | −46.9R | −0.308R | 111 | −42.2R | −0.380R |

Every combination lost in both periods. BTC on its own lost on all 15 combinations in the first two years and on 14 of 15 in the blind year.

**Average effect of each idea in the first two years** (mean R per trade across settings with at least 50 trades):

| Idea | Without | With |
|---|---|---|
| Confirmation candle | −0.145 | −0.112 |
| ATR buffer on the stop (0.5 ATR vs one tick) | −0.138 | −0.114 |
| Retest entry instead of market | −0.121 | −0.149 |
| Only with the higher-timeframe trend | −0.127 | −0.136 |
| BTC alone instead of all coins | −0.151 | −0.092 |

No idea turns the average positive.

**The settings the rule picked, on the blind year**

| | Period | Trades | Per month | Win rate | Net | Per trade | Worst drawdown | Worst losing streak | Green months |
|---|---|---|---|---|---|---|---|---|---|
| All coins | 2 earlier years | 120 | 5.0 | 14% | +30.6R | +0.255R | −18.7R | 17 | 10/25 |
| | **blind year** | 89 | 6.9 | 7% | −45.8R | −0.515R | −50.5R | 30 | 2/13 |
| BTC alone | 2 earlier years | 30 | 1.2 | 33% | +17.2R | +0.574R | −5.2R | 5 | 9/21 |
| | **blind year** | 23 | 1.8 | 26% | −11.7R | −0.511R | −12.2R | 8 | 2/13 |

- All coins: 4h trigger at the weekly level, same-candle reclaim, 0.5 ATR stop buffer, retest entry, TP1 at 1R, no break-even, 8-candle trail.
- BTC alone: the same with next-candle reclaim allowed, TP1 at 1.5R and break-even.
- Both looked good in-sample and lost clearly in the blind year. Only 10% of settings were positive in-sample and 2% in both periods, about what chance alone would give.

# 7. All strategies together

One position per coin across all three strategies, first signal wins.

| | Period | Trades | Per month | Win rate | Net | Per trade | Worst drawdown | Worst losing streak | Green months |
|---|---|---|---|---|---|---|---|---|---|
| Live today, all three | 2 earlier years | 1155 | 48.1 | 38% | −71.5R | −0.062R | −85.1R | 13 | 11/25 |
| | **blind year** | 824 | 64.3 | 45% | +56.1R | +0.068R | −29.4R | 15 | 9/13 |
| of which D-line | 2 earlier years | 520 | 21.7 | 38% | −59.8R | −0.115R | −70.0R | 13 | 8/24 |
| | **blind year** | 352 | 27.5 | 45% | +3.4R | +0.010R | −37.7R | 10 | 5/13 |
| of which Nested | 2 earlier years | 559 | 23.3 | 40% | +16.8R | +0.030R | −30.3R | 11 | 12/25 |
| | **blind year** | 408 | 31.8 | 42% | +39.0R | +0.096R | −20.4R | 10 | 8/13 |
| of which SFP | 2 earlier years | 76 | 3.2 | 29% | −28.5R | −0.375R | −28.5R | 9 | 7/22 |
| | **blind year** | 64 | 5.0 | 55% | +13.7R | +0.214R | −5.4R | 5 | 7/13 |
| Chosen, all three | 2 earlier years | 366 | 15.2 | 36% | +40.1R | +0.110R | −21.1R | 15 | 18/25 |
| | **blind year** | 242 | 18.9 | 40% | +36.8R | +0.152R | −11.2R | 9 | 10/13 |
| of which D-line | 2 earlier years | 69 | 2.9 | 33% | +28.8R | +0.417R | −9.7R | 10 | 12/22 |
| | **blind year** | 40 | 3.1 | 25% | +6.1R | +0.152R | −8.2R | 9 | 6/12 |
| of which Nested | 2 earlier years | 81 | 3.4 | 36% | +0.5R | +0.006R | −12.4R | 9 | 10/22 |
| | **blind year** | 64 | 5.0 | 52% | +16.9R | +0.264R | −3.9R | 4 | 10/13 |
| of which SFP | 2 earlier years | 216 | 9.0 | 38% | +10.9R | +0.050R | −24.0R | 9 | 14/25 |
| | **blind year** | 138 | 10.8 | 38% | +13.8R | +0.100R | −10.7R | 7 | 7/13 |

- The "chosen" set above includes the SFP "least bad" setting. Without SFP: +65.0R on 254 trades in the first two years (worst drawdown −12.6R) and +23.9R on 202 trades in the blind year (worst drawdown −13.0R).
- In the combined run, most of the blind-year profit comes from Nested.

# 8. How much to trust a chosen setting

For each strategy we compared every setting's result in the first two years with its result in the blind year.

| Strategy | Settings compared | Rank link (−1 to +1) | Positive in-sample | Positive in the blind year | Positive in both |
|---|---|---|---|---|---|
| D-line | 2,058 | +0.07 | 17% | 53% | 11% |
| Nested | 2,408 | +0.14 | 8% | 21% | 3% |
| SFP (bot detector) | 568 | +0.41 | 0% | 9% | 0% |
| SFP at levels | 14,542 | +0.12 | 10% | 14% | 2% |

- A rank link near zero means the order of settings in one period says almost nothing about the order in the next.
- So the detail of a setting (TP1 at 0.75R or 1R, a 3 or 5 candle trail) should not be read as a finding. The findings that repeat in both periods are coarse: **which strategy, which chart, which stage**.

**Average effect of each choice** (mean R per trade over all settings on a single chart with at least 30 trades):

*D-line*

| Choice | Value | First two years | Blind year |
|---|---|---|---|
| Chart | 15m | −0.155 | −0.041 |
| Chart | 1h | +0.077 | +0.029 |
| Chart | 30m | −0.183 | +0.042 |
| Entry | market | −0.093 | −0.012 |
| Entry | retest | −0.080 | +0.033 |
| Break-even after TP1 | off | −0.072 | +0.025 |
| Break-even after TP1 | on | −0.101 | −0.004 |
| TP1 | 0.75R | −0.080 | −0.016 |
| TP1 | 1R | −0.085 | +0.019 |
| TP1 | 1.5R | −0.093 | +0.020 |
| TP1 | half | −0.089 | +0.020 |
| Trail | 3 | −0.085 | +0.010 |
| Trail | 5 | −0.086 | +0.009 |
| Trail | 8 | −0.088 | +0.013 |

*Nested*

| Choice | Value | First two years | Blind year |
|---|---|---|---|
| Chart | 15m | −0.075 | −0.130 |
| Chart | 1h | −0.037 | +0.032 |
| Chart | 30m | −0.056 | −0.002 |
| Chart | 4h | −0.178 | −0.060 |
| Break-even after TP1 | off | −0.066 | −0.043 |
| Break-even after TP1 | on | −0.091 | −0.036 |
| TP1 | 0.75R | −0.077 | −0.038 |
| TP1 | 1R | −0.078 | −0.027 |
| TP1 | 1.5R | −0.080 | −0.054 |
| Trail | 3 | −0.076 | −0.038 |
| Trail | 5 | −0.079 | −0.040 |
| Trail | 8 | −0.081 | −0.040 |

*SFP (bot detector)*

| Choice | Value | First two years | Blind year |
|---|---|---|---|
| Chart | 15m | −0.139 | −0.155 |
| Chart | 1h | −0.113 | −0.007 |
| Chart | 30m | −0.071 | −0.074 |
| Chart | 4h | −0.063 | −0.017 |
| Break-even after TP1 | off | −0.079 | −0.058 |
| Break-even after TP1 | on | −0.100 | −0.076 |
| TP1 | 0.75R | −0.079 | −0.066 |
| TP1 | 1R | −0.079 | −0.069 |
| TP1 | 1.5R | −0.112 | −0.066 |
| Trail | 3 | −0.091 | −0.066 |
| Trail | 5 | −0.088 | −0.068 |
| Trail | 8 | −0.090 | −0.068 |


- 1h is the only chart that is positive on average in both periods for D-line. For Nested it is the best chart in both periods, though still slightly negative on average in-sample.
- Break-even off is slightly better on average for D-line and SFP in both periods, and in-sample for Nested.
- The other exit choices are within about 0.02R of each other.

# 9. Recommendation

| Strategy | Settings to run on demo next | First two years | Blind year | Drop |
|---|---|---|---|---|
| Nested | 1h only; stage N2 and later; TP1 15% at 0.75R; TP2 50% at the first of the measured move and the nearest 4h swing; TP3 20% at the next level; 15% runner on a 5-candle trail; no break-even move | +13.0R, +0.088R per trade, 148 trades | +32.5R, +0.260R per trade, 125 trades | 15m, 30m and 4h; stage N1 |
| D-line | Small demo test only: 1h; stop at least 1.5% away; limit entry at the line for 6 candles; TP1 halfway to the structure; no break-even move; 5-candle trail | +40.3R, +0.317R per trade, 127 trades | +3.7R, +0.041R per trade, 90 trades | 15m and 30m; market entry at the breakout |
| SFP | None. Pause it. | none qualified | best levels design: −46R | live 15m SFP; all 4h / 8h / daily / weekly level designs |

**How to read this**

- Nested 1h is the only setting with a positive result in each of the three years. Its blind-year result is about 2.3 standard errors above zero; the two earlier years alone are not. Expect something between the two (+0.09R and +0.26R per trade), at roughly 6 to 10 trades a month over five coins.
- D-line 1h has not shown an edge out of sample. Keeping it on demo costs nothing and adds evidence; it should not be the basis for a decision.
- The break-even move after TP1 is worth switching off on demo for both: it cut results in both periods.
- An optional extra to watch on demo, not selected by the rule: Nested only with the 4h trend.
- Suggested check before trusting any of this: 100 demo trades per strategy, compared with the blind-year per-trade figure and its range.

# 10. Limits

- **Binance prices stand in for BloFin.** Wicks and fills differ between exchanges.
- **Slippage is not modelled** beyond taking the stop first inside a minute. With stops of 1% or more, 2 bps of slippage on the entry and the stop is roughly 0.03R per trade (an estimate, not measured). That is as large as some of the edges here.
- **Funding is an estimate** from Binance rates, through October 2026.
- **Short history for HYPE and TAO.** HYPE has five months in-sample.
- **Many settings were compared.** Even with the neighbour rule, the in-sample figure of a chosen setting is biased upwards. The blind-year figure is the fair one, and it is one year.
- **The blind year was used once** for the frozen choices. The tables that show other settings on the blind year are there to explain, not to choose from. Picking from them now would make the next report's "blind" year no longer blind.
- **Differences from the earlier one-year report:** this simulator steps through 1-minute candles for the whole trade, puts the break-even stop at the entry plus fees, and applies one position per coin across timeframes. On the same trades the two agree closely (correlation 0.99 for D-line, identical for Nested).
- **D-line on 4h was not tested** (see section 2).

# 11. How to reproduce

- Scripts: `research/backtests/bt3y_common.py`, `bt3y_simulate.py`, `bt3y_levels.py`, `bt3y_analyze.py`; run steps are in `research/backtests/README.md`.
- The selection step (`select`, `lvl-select`) drops every trade entered on or after 1 November 2025 before it computes anything.
- Data folders are kept outside the code folders and are not in the repository.
