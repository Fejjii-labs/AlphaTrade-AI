# Strategy backtests (research, not production code)

One-off replays of the bot's own strategy detectors on Binance USD-M history, with the
live BloFin demo execution checks and BloFin fees. The results of the first runs are in
`docs/strategies/backtest_report_2026-11.md`; the agreed strategy design is in
`docs/strategies/strategy_spec_v1.md`.

These scripts import the backend (`app.*`), so run them with the backend virtualenv and
`backend/src` on the path. They read CSV klines from a data folder and write JSON results.

```bash
cd backend
# 1. data (untrusted: keep it outside the code folders)
.venv/bin/python ../research/backtests/download_klines.py /tmp/bt-data \
  BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT 15m,30m,1h,2h,4h 2025-11 2026-11-24
.venv/bin/python ../research/backtests/download_klines.py /tmp/bt-data1m \
  BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT 1m 2025-11 2026-11-24
# 2. a study, one coin per process (they are CPU bound; run 4 in parallel on 4 cores)
PYTHONPATH=src .venv/bin/python ../research/backtests/dline_1m_study.py /tmp/bt-data /tmp/bt-data1m out1m_BTCUSDT.json BTCUSDT
PYTHONPATH=src .venv/bin/python ../research/backtests/sfp_nested_study.py /tmp/bt-data /tmp/bt-data1m sn_nested_BTCUSDT.json BTCUSDT nested 15m,30m,1h
PYTHONPATH=src .venv/bin/python ../research/backtests/sfp_levels_study.py /tmp/bt-data /tmp/bt-data1m lvl_BTCUSDT.json BTCUSDT
# 3. summaries (read every out*/sn_*/lvl_* JSON in the folder)
python3 ../research/backtests/analyze1m.py <folder>     # D-line
python3 ../research/backtests/analyze_sn.py <folder>    # Nested / SFP (bot detector)
python3 ../research/backtests/analyze_lvl.py <folder>   # SFP at 4h/8h/daily levels
```

Keep the downloaded data folders outside `backend/` and `research/`; the scripts only read
CSV files from the folders you pass.

| Script | What it does |
|---|---|
| `dline_retest_study.py` | D-line breakout vs retest vs half-half; shared CSV loader (`load_bars`) and tick sizes |
| `dline_1m_study.py` | D-line ladder exits with ambiguous candles settled on 1-minute bars; generic ladder `simulate()` (TP steps, break-even after TP1, 5-candle runner trail) |
| `sfp_nested_study.py` | Nested (rolling 256-bar window, as live) and SFP (bot detector) with every live demo check; SFP fixed-R target variants |
| `sfp_levels_study.py` | SFP at the previous 4h/8h/daily high/low with stepped exits |
| `analyze*.py` | Summaries: net R, win rate, drawdown, per timeframe/stage/stop distance/coin/month |

Known limits of the one-year scripts above: Binance candles stand in for BloFin prices;
funding and slippage are not modelled; one position per coin is applied per strategy, not
across strategies.

## Three-year study with a blind test year (`bt3y_*.py`)

Results: `docs/strategies/backtest_report_2026-11-26.md`. Settings are chosen on entries
before 1 Nov 2025 only and then scored once on the year after it. The `bt3y_*` scripts do
not import the backend: they post-process the JSON written by the studies above (so the
signals still come from the bot's own detectors) and need only `numpy`.

```bash
D=/path/outside/the/repo/bt-data          # klines/ klines1m/ funding/ cache/ out/
cd backend
# 1. data: 3 years of candles, 1-minute candles and funding
.venv/bin/python ../research/backtests/download_klines.py $D/klines \
  BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT 15m,30m,1h,2h,4h,1d 2023-11 2026-11-25
.venv/bin/python ../research/backtests/download_klines.py $D/klines1m \
  BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT 1m 2023-11 2026-11-25
.venv/bin/python ../research/backtests/download_klines.py $D/funding \
  BTCUSDT,ETHUSDT,ZECUSDT,TAOUSDT,HYPEUSDT funding 2023-11 2026-10-31
# 2. signals from the bot's detectors (existing studies). The D-line detector is slow on
#    long histories, so run it on yearly folders that overlap by one month and name the
#    outputs out/dline_<SYMBOL>_A|B|C.json (A = to Oct 2024, B = to Oct 2025, C = the rest).
PYTHONPATH=src .venv/bin/python ../research/backtests/dline_1m_study.py <klines_A> <klines1m_A> $D/out/dline_BTCUSDT_A.json BTCUSDT
PYTHONPATH=src .venv/bin/python ../research/backtests/sfp_nested_study.py $D/klines $D/klines1m $D/out/sn_nested_BTCUSDT_15m.json BTCUSDT nested 15m
SFP_MIN_SWEEP=0.003 PYTHONPATH=src .venv/bin/python ../research/backtests/sfp_nested_study.py $D/klines $D/klines1m $D/out/sn_sfp_BTCUSDT.json BTCUSDT sfp 15m,30m,1h,4h
# 3. replay every signal under every exit scenario (seconds per coin), and SFP at levels
python3 -I ../research/backtests/bt3y_simulate.py $D BTCUSDT
python3 -I ../research/backtests/bt3y_levels.py $D BTCUSDT
# 4. choose in-sample (never reads the blind year), then score both periods once
python3 -I ../research/backtests/bt3y_analyze.py $D select
python3 -I ../research/backtests/bt3y_analyze.py $D lvl-select
python3 -I ../research/backtests/bt3y_analyze.py $D final
python3 -I ../research/backtests/bt3y_analyze.py $D lvl-final
```

| Script | What it does |
|---|---|
| `bt3y_common.py` | Loaders (cached arrays, gap-free 1-minute grid, funding), the live pre-trade checks, market regime, and a minute-by-minute ladder simulator (stop first inside a minute, break-even after TP1, runner trail on closed candles, fees on every exit, funding estimate) |
| `bt3y_simulate.py` | Replays each D-line / Nested / SFP signal under every exit scenario (live exits; TP1 0.75/1/1.5R; break-even on/off; trail 3/5/8; target placement; D-line breakout vs retest) |
| `bt3y_levels.py` | SFP at the previous 4h / 8h / daily / weekly high and low: same- or next-candle reclaim, confirmation candle, tick or ATR stop, market or retest entry |
| `bt3y_analyze.py` | In-sample selection by a fixed rule (`select`, `lvl-select`), then in-sample vs blind tables, breakdowns and all strategies together with one position per coin (`final`, `lvl-final`) |

Limits: Binance candles stand in for BloFin prices; funding is an estimate from Binance
funding rates; slippage is not modelled; D-line on 4h is not covered (the detector accepts
15m, 30m and 1h only).
