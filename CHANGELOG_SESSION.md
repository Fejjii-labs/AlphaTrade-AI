# Session changelog — 2026-10-21

## Watcher M15 boundary fix

- Added `ensure_bar_close_ready` on `SymbolMarketFactory` (Binance settlement wait + optional config delay).
- Watcher paper scans wait before evidence assembly; one retry after `forming_candle`.
- `FormingCandleError` → `reason_code=forming_candle` at watcher evidence port.
- Idempotency keys use `last_closed_interval_end`.
- Tests: 9 passed (`test_watcher_paper_bar_close.py`, `test_watcher_market_settlement.py`).
- PR: https://github.com/Fejjii/AlphaTrade-AI/pull/308 @ `4af9c2ef`.
