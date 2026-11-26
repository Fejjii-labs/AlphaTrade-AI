# Brief: repo and safety audit (for a Fable 5.2 session)

You are auditing the AlphaTrade repo (`Fejjii-labs/AlphaTrade-AI`). Read `AGENTS.md`,
`CLAUDE.md` and `.cursor/rules/*.mdc` first and follow them. This is a **read-and-report**
task: do not change production code, do not deploy, do not place orders, never print
secrets (Render, GitHub, BloFin keys, connection strings). If you run tests, follow the
local CI rules (the full gate uses the `alphatrade_test` database; never run other tests
on it at the same time).

## Context

The bot trades D-line, Nested and SFP autonomously on the BloFin **demo** account
(staging on Render: an API service and a worker). Many trading and safety changes landed
in the last weeks (#495–#522): exit reconciliation per coin, attached TP/SL linking,
journal mirror, Telegram alerts, strategy gates, stepped exits (#516), a fair per-coin
scan cap (#518), weekly strategy stats (#519) and a BloFin backup stop for stepped-exit
trades (#521, `GOVERNED_DEMO_LADDER_BACKUP_STOP`, on in staging since 26 Nov). Strategy
design is in `docs/strategies/strategy_spec_v1.md` (v1.1); the three-year backtest is
`docs/strategies/backtest_report_2026-11-26.md`.

## What to check (prioritised)

1. **Trade protection.** Every open demo position must always have a stop that works
   if the worker is down. Stepped-exit (ladder) trades are closed by the bot
   (`services/partial_exits/`, `providers/exchange/governed_blofin_partial_exits.py`) and,
   since #521, also carry a whole-position BloFin stop order (`size` -1) that the bot
   places after entry and only tightens; the exit proof (`governed_blofin_exit.py`)
   accepts a close by it only under the derived client id, at the approved stop or
   tighter. Audit that path closely: the gap between entry and the first placement, a
   race between the bot's own stop close and BloFin's, a failed placement (it only
   alerts), and any other path where a position can be unprotected, double-closed, or
   closed by the wrong trade.
2. **Kill switch, daily loss lock, one position per coin, reconciliation**
   (`services/governed_blofin_demo.py`, `providers/exchange/demo_reconciliation.py`,
   `governed_blofin_exit.py`, `services/risk/`): can any failure leave the bot trading
   when it should stop, or stuck when it should trade?
3. **Real-money guards.** Confirm nothing can reach a live (non-demo) venue without the
   explicit approvals the code and docs describe.
4. **Worker health and resources:** memory growth (512 MB container), restart and
   shutdown behaviour, Binance rate limits (the bot saw `rate_limited` on 30m data; the
   watcher scans every *enabled* strategy, not only the autonomy list, see #518),
   database growth and retention, slow queries.
5. **Correctness of money figures:** journal P&L (BloFin's own figures), Telegram trade
   pages, daily summaries.
6. **Tests and CI:** gaps around the paths above; flaky or skipped tests; anything only
   tested with fakes that never matched BloFin's real responses.
7. **Repo organisation:** stale or contradictory docs, dead code, duplicated logic,
   naming, what a new engineer would trip over.

## Deliverables

- `docs/audits/audit_<date>.md` and a PDF of it: findings ranked by severity
  (critical / high / medium / low), each with file:line, what can go wrong, how likely,
  and a suggested fix. Start with a one-page summary in plain language.
- A pull request with the report only. Fixes come later, one PR each, after review.
