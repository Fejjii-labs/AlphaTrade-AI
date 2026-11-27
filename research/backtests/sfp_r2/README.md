# SFP research round 2: one exit change on round 1's V05

Report: `docs/strategies/sfp_research_round2.md` (PDF in `docs/strategies/pdf/`). Brief:
`docs/strategies/briefs/sfp_research_round2.md`. Research only: reads CSV files, places no
orders, imports nothing from `backend/`.

Round 1's modules are copied here unchanged so this folder stands alone:
`data.py`, `levels.py`, `signals.py`, `entries.py`, `stops.py`, `exits.py`, `engine.py`,
`stats.py`, `classify.py` (from `research/backtests/sfp_r1/` on the round-1 branch, PR #538).
`../bt3y_common.py` carries the same small change as round 1 (the simulator returns its fills).

| Script | What it does |
|---|---|
| `step0.py` | Look-ahead check: every V05 trade decided again on data cut off at the order time and one minute after the fill |
| `step0_controls.py` | Negative controls: puts a known look-ahead bug back and checks `step0.py` flags it |
| `engine_r2.py` | Exits E0-E3 on round 1's V05 signals (same signals for every exit), one position per coin |
| `run_r2.py` | All coins, both periods, every exit; base run plus three checks |
| `tables_r2.py` | The report's markdown tables |

Commands are in section 6 of the report.
