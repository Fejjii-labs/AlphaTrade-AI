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

Known limits: Binance candles stand in for BloFin prices; funding and slippage are not
modelled; one position per coin is applied per strategy, not across strategies.
