# SFP research round 3: an anchored-VWAP trend filter on round 1's V05

Report: `docs/strategies/sfp_research_round3.md` (PDF in `docs/strategies/pdf/`). Research
only: reads CSV files, places no orders, imports nothing from `backend/`.

Round 1's modules and round 2's engine and truncation test are imported from
`../sfp_r2/` (unchanged). This folder only adds the filter and the round-3 runs.

| Script | What it does |
|---|---|
| `vwap.py` | Weekly-open and monthly-open anchored VWAP from 1h candles, and the F0-F3 verdicts |
| `run_r3.py` | V05 candidates (exit E0) on every coin and period, each tagged with the F0-F3 verdicts and its loss reason; round 1's V01 BTC trades for the 13 example losses |
| `step0_r3.py` | Look-ahead check: round 2's truncation test on every F1-F3 trade, the VWAP verdict of every candidate on cut data, and a planted-leak control |
| `tables_r3.py` | The report's tables |

Commands are in the report's method section.
