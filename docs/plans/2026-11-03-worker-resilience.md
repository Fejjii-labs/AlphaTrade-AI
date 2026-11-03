# Staging paper worker resilience / self-healing (2026-11-03)

Branch: `claude/worker-resilience` (local only, not pushed). Agent: Claude Code.

> Process deviation: `AGENTS.md` asks for a plan-only draft PR and a Griller GO
> before any code. In this task Sofien asked for the code, the tests and this plan
> together, committed locally only. This file records the plan and what was built.
> It still needs Griller review before anything is pushed.

## Problem

The staging paper worker (`alphatrade-paper-worker-staging`, `python -m app.workers.paper_worker`)
has had four kinds of outage:

1. **Crash loop on a Telegram config conflict** (2026-11-01, fixed for the BloFin demo case in
   #400 / 5139d23c). Telegram flag conflicts are refused inside `Settings` model validators
   (`core/deployment_safety._telegram_activation_errors`,
   `market_activation/profile._live_profile_errors`). The process exits before boot and Render
   restarts it forever. #400 only widened `_paper_staging_market` for
   `EXCHANGE_MODE=paper_exchange_demo`. Any other Telegram-only conflict, for example an armed
   projection with no `TELEGRAM_BOT_TOKEN`, still crash-loops the whole worker, including the
   Watcher.
2. **Silent disarm / degrade.** A Blueprint sync or an operator edit can leave staging disarmed.
   Nothing reports this except reading the logs.
3. **Transient Postgres errors** (connection dropped, `PendingRollbackError`) fail a component
   cycle. The next cycle comes only after the full poll interval.
4. **Binance 418 IP ban** (2026-10-27). The process-wide `BinanceRestGate` (#384, #404) stops
   the requests, but health shows nothing and nobody is paged.

Also checked: the indexing-thread `KeyError` in `observability/worker_memory.py` was already
fixed in 58ef6146 (PR #274), with tests in `tests/test_worker_memory_diagnostics.py`. No change
was made for it.

## Hard invariant

Trade-safety gates stay fail-closed: kill switch, leverage, real-money block, exposure, and
execution mode. Every degradation turns a feature off or refuses. Nothing added here can make
a trade more likely.

## Changes

| # | Change | Files |
|---|--------|-------|
| 1 | Boot config report: a pure function over `Settings` that checks 12 staging flags (expected, actual, ok). Overall status is `ok`, `degraded`, `disarmed_unexpected`, or `not_applicable` outside staging. Values are booleans or public mode tokens only. It is published on the `worker_config` runtime-status row (no migration; it reuses existing columns plus a failure bitmask) and logged at error level when not ok. One `worker_config_degraded` Telegram ops alert is sent, deduplicated for 6 h across restarts by `RuntimeStatusHeartbeatLedger`. It never raises. | `services/worker_config_report.py` (new), `workers/paper_worker.py`, `services/safety_alerts.py` |
| 2 | Telegram fail-soft: `load_worker_process_settings(PAPER_WORKER)` in staging retries once with every Telegram enable flag forced off (process-local env), and the full validator chain runs again. If the retry still fails (a trade-path or dependency error), the original error is raised. The bot token and chat id are kept, so safety alerts still go out. The marker `telegram_disabled_config_conflict` makes the report `degraded`. The dedicated `TELEGRAM_PAPER` worker still fails closed. Opt-in memory diagnostics that fail to start now log an error and are disabled instead of aborting the supervisor. | `core/disarmed_worker_boot.py`, `workers/paper_worker.py` |
| 3 | Transient DB retry: in `PaperWorkerSupervisor._step`, `OperationalError`, `PendingRollbackError`, or `DBAPIError(connection_invalidated)` (also when wrapped up to 3 causes deep) retries the cycle 2 times (0.5 s, then 2 s, interrupted by stop). After that the component is marked failed as before. Runtimes already open and close their sessions per cycle (`with factory()` / `finally: close()`; per-scan `rollback()` in `watcher_paper._scan_one`), so a failed attempt has already rolled back. A retry is the next cycle sooner, and every gate is evaluated again. | `workers/paper_worker.py` |
| 5 | Ban visibility: `BinanceRestGate.state()` is a read-only snapshot (banned, closed kind, until, last status 418/429, ban start). After each watcher cycle, `MarketBanPublisher` writes the `market_data_gate` row when the state changes and sends one `market_data_banned` alert per ban start. `/health/paper-worker` gains `worker_config` and `market_data_ban`. A ban whose deadline has passed reads `open`. | `market_contracts/adapters/binance_rest_gate.py`, `services/market_ban_visibility.py` (new), `schemas/health.py`, `api/routes/health.py`, generated OpenAPI |

### Config report checks

Critical checks (a failure means `disarmed_unexpected`): `watcher_orchestration_enabled`,
`watcher_paper_staging_activation`, `controlled_watcher_arm`, `exchange_mode` (`paper_internal`,
or `paper_exchange_demo` with the governed gate), `perpetual_evidence_activation=active`, and
`real_trading_disabled`.

Auxiliary checks (a failure means `degraded`): `perpetual_evidence_source=binance_usdm`,
`autonomy_mode` (`internal_paper` or `blofin_demo`), `telegram_projection=projection`,
`telegram_boot_conflict=none`, `telegram_bot_token_present`, and `telegram_chat_id_present`.

Known limit: a staging worker that boots *disarmed* has no bot token by definition, so it
cannot page through Telegram. The error log and `/health/paper-worker` are its signal. An
intentional rollback (for example `PERPETUAL_EVIDENCE_SOURCE=replay`) is also reported as
`disarmed_unexpected`, at most once per 6 h. A follow-up could add an explicit
"expected posture" operator flag.

## Acceptance criteria

- [x] Staging boot with a Telegram-only conflict runs the Watcher with Telegram off, and the report is `degraded` (`test_paper_worker_boots_with_telegram_off`, plus the updated `test_armed_telegram_worker_fails_closed_without_bot_token`, which runs a subprocess with the Blueprint env).
- [x] A trade-path conflict together with a Telegram conflict still raises, and the env is restored (`test_trade_path_conflict_still_raises`). The dedicated Telegram worker still fails closed.
- [x] The report is `not_applicable` outside staging, contains no secret values, and round-trips through the runtime-status row.
- [x] A report persist or alert failure never raises, and the alert is sent once.
- [x] A transient DB error retries at most 2 times and then fails. Non-transient errors do not retry.
- [x] The ban snapshot keeps the ban start across repeated 418s. The publisher writes only on change and alerts once per ban. An expired ban projects as `open`.
- [x] `npm run api:check` passes (OpenAPI change is additive only).
- [ ] Full backend gate (`uv run pytest`) before merge review. Not run here (`Full-Backend: false`).
- [ ] Griller review on the final head SHA.

## render.yaml `sync: false` proposal (not edited in this task)

Operator-tunable keys on `alphatrade-paper-worker-staging` that a Blueprint sync currently
resets to literal values:

- `PERPETUAL_EVIDENCE_SOURCE`: the documented rollback is `replay`. A sync silently undoes an operator rollback, or re-arms Binance during a ban.
- `PERPETUAL_EVIDENCE_SECONDARY_SOURCE`: the operator's Bybit fallback toggle.
- `MARKET_DATA_ENABLED`: the operator's market-data kill toggle.
- `PERPETUAL_EVIDENCE_TIMEOUT_SECONDS`: an operator tuning value during provider incidents.
- `MARKET_WATCHER_BRIDGE_AUTO_TICK`: this key is missing from the worker block. The disarmed-flags list in `core/disarmed_worker_boot.py` reads it, so add it as `sync: false`, like the other `MARKET_WATCHER_*` keys.

These should stay **synced literals**, because a sync must pull them back to their safe values:
`ENVIRONMENT`, `EXECUTION_MODE`, `ENABLE_REAL_TRADING`, `MARKET_DATA_FUTURES_BASE_URL`,
`BYBIT_PERPETUAL_BASE_URL`, `DEBUG`, `LOG_JSON`, and all auth, cookie, denylist, CORS,
rate-limit, and proxy keys.

Keys the worker reads that are not in `render.yaml` (dashboard-only, for example
`AUTONOMOUS_*`, `PAPER_EXIT_MONITOR_ENABLED`, `GOVERNED_BLOFIN_DEMO_*`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_BOT_ID`, `TELEGRAM_CHAT_ID`, `KNOWLEDGE_INDEXING_ENABLED`) should be listed as
`sync: false` so the Blueprint documents them. Whether Render keeps dashboard-only keys that a
Blueprint does not declare is UNKNOWN; verify this in the Render docs before relying on it.
`tests/test_disarmed_render_worker_boot.py::test_operator_flags_are_not_synced_by_blueprint`
must be updated in the same PR.

## Binance fallback proposal

What exists today: `PERPETUAL_EVIDENCE_SECONDARY_SOURCE=bybit_usdt_perpetual`, with
`BYBIT_PERPETUAL_BASE_URL=https://api.bybit.com`. It is already set in `render.yaml` for the
worker, and `market_contracts/adapters/failover.py` implements it (public USD-T perpetual
klines and trades, read-only, no credentials, no spot or fabricated fallback). The primary
can also be switched to `bybit_usdt_perpetual`.

Proposed next steps, in order of cost:

1. **Use the existing Bybit secondary during a ban.** `failover.py` already switches the whole evidence identity on `UpstreamBanError` and `RateLimitedError`. Confirm from staging logs that a closed `BinanceRestGate` raises one of these, so failover engages during a ban. If it does not, map the closed gate to `UpstreamBanError`. Add the active source to `market_data_ban` on health.
2. **OKX public klines as a third source.** Only if Bybit also proves unreliable from Render egress. It needs a new adapter under the provider factory, with the same freshness, symbol-mapping (`BTC-USDT-SWAP`), and no-credential checks as the Bybit adapter.
3. **Dedicated static egress** (Render static outbound IP or a small proxy). This stops one shared Render egress IP from inheriting someone else's ban. It costs money and changes infra, so it needs Sofien's approval.
4. **Websocket market streams** to replace aggTrades REST polling. This is the structural fix for request weight (noted in `binance_rest_gate.py`).

Stale or missing data stays fail-closed throughout: no data means no paper decision.

## Risks

- **Fail-soft Telegram reverses an explicit invariant** ("a partial Telegram flag cannot start") for the supervised paper worker role only. Mitigations: staging only; the retry re-runs every validator; it only turns flags off; the dedicated Telegram worker still fails closed; the report is `degraded` and an alert is sent. A reviewer must agree that Telegram is off the trade path. Approvals and projection only reduce capability when off, and the autonomous mode does not depend on Telegram.
- **Process env mutation** (`os.environ`) in the fail-soft retry is process-local, limited to the 7 Telegram enable keys, and restored if the retry fails.
- **DB retry re-runs a cycle** that failed partway. Its effect is the same as the next scheduled cycle, which already runs after any failure. Leases and fencing guard the scans. Idempotency of every write path in the cycle was not re-audited here, so a reviewer should confirm it.
- **The runtime-status row is overloaded**: the config report reuses existing columns (`request_count` holds the failure bitmask). The check order is append-only.
- **Ban dedup**: two separate bans within the 15 min ledger cooldown page once.
- **Disarmed staging cannot page** (no bot token). It is visible on health and in error logs only.
