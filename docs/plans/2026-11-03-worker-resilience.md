# Staging paper worker resilience / self-healing (2026-11-03)

Original branch: `claude/worker-resilience` (draft PR #410, head `b13f6a52`). Agent: Claude Code.
After the Griller review of #410 (NO-GO as one PR, 2026-11-03) the work is split
into four branches off `main` (see "Split" below). This file lives on split A.

> Process deviation: `AGENTS.md` asks for a plan-only draft PR and a Griller GO
> before any code. In this task Sofien asked for the code, the tests and this plan
> together. This file records the plan and what was built. Each split PR still needs
> its own Griller verdict on its final head SHA before merge.

## Split (2026-11-03, after Griller review of #410)

| PR | Branch | Base | Scope |
|----|--------|------|-------|
| A | `claude/worker-config-report` | `main` | Changes 1 and 5: boot config report, Binance ban visibility (`RestGateState` + `MarketBanPublisher`), `/health/paper-worker` schema and OpenAPI, `safety_alerts` codes, memory-diagnostics fail-soft, this plan. Griller nit fixed: the ban check, ban-start write and gate close share one lock. No Telegram fail-soft, no DB retry. |
| B | `claude/worker-db-retry` | `main` | Change 3: transient DB retry, exact allowlist `{activity, indexing}`. Adds tests that kill Griller mutants R5 (bound), R6 (authority revert between attempts) and R7 (stop during backoff) without patching the backoff tuple. |
| C | `claude/worker-telegram-failsoft` | A | Change 2, narrowed (see below). Needs Sofien's policy decision. Built on A because it feeds `worker_boot_degradations()` into A's config report. |
| D | `claude/render-sync-false` | `main` | `render.yaml` `sync: false` changes (section below), plus the validation script and tests that pin `render.yaml`. |

C's narrowed fail-soft (Griller B1): the supervised paper worker boots with Telegram off
only when `ENVIRONMENT=staging`, the role is `PAPER_WORKER`, `EXCHANGE_MODE=paper_internal`,
`GOVERNED_BLOFIN_DEMO_ENABLED` is off, and the boot is armed (not the disarmed boot).
`paper_exchange_demo` and governed BloFin demo configs keep failing closed when the token is
missing (`test_armed_telegram_worker_fails_closed_without_bot_token` is restored for them).
A `degraded` or `disarmed_unexpected` config report also makes `GET /health/paper-worker`
return HTTP 503 (same JSON body), so an uptime check sees it without Telegram. `ok`,
`not_applicable` and `unknown` stay 200. No Render health check points at this route
(`healthCheckPath` is `/health`), so the 503 cannot restart a service.

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
| 1 (A) | Boot config report: a pure function over `Settings` that checks 12 staging flags (expected, actual, ok). Overall status is `ok`, `degraded`, `disarmed_unexpected`, or `not_applicable` outside staging. Values are booleans or public mode tokens only. It is published on the `worker_config` runtime-status row (no migration; it reuses existing columns plus a failure bitmask) and logged at error level when not ok. One `worker_config_degraded` Telegram ops alert is sent, deduplicated for 6 h across restarts by `RuntimeStatusHeartbeatLedger`. It never raises. | `services/worker_config_report.py` (new), `workers/paper_worker.py`, `services/safety_alerts.py` |
| 2 (C) | Telegram fail-soft (narrowed in C, see "Split"): `load_worker_process_settings(PAPER_WORKER)` in staging, internal paper, governed demo off, retries once with every Telegram enable flag forced off (process-local env), and the full validator chain runs again. If the retry still fails (a trade-path or dependency error), the original error is raised. The bot token and chat id are kept, so safety alerts still go out. The marker `telegram_disabled_config_conflict` makes the report `degraded`. The dedicated `TELEGRAM_PAPER` worker still fails closed. (A) Opt-in memory diagnostics that fail to start now log an error and are disabled instead of aborting the supervisor. | `core/disarmed_worker_boot.py`, `workers/paper_worker.py`, `api/routes/health.py` (503) |
| 3 (B) | Transient DB retry (allowlisted components only, see the deviation note below): in `PaperWorkerSupervisor._step`, `OperationalError`, `PendingRollbackError`, or `DBAPIError(connection_invalidated)` (also when wrapped up to 3 causes deep) retries the cycle 2 times (0.5 s, then 2 s, interrupted by stop). After that the component is marked failed as before. Runtimes already open and close their sessions per cycle (`with factory()` / `finally: close()`; per-scan `rollback()` in `watcher_paper._scan_one`), so a failed attempt has already rolled back. A retry is the next cycle sooner, and every gate is evaluated again. | `workers/paper_worker.py` |
| 5 (A) | Ban visibility: `BinanceRestGate.state()` is a read-only snapshot (banned, closed kind, until, last status 418/429, ban start). After each watcher cycle, `MarketBanPublisher` writes the `market_data_gate` row when the state changes and sends one `market_data_banned` alert per ban start. `/health/paper-worker` gains `worker_config` and `market_data_ban`. A ban whose deadline has passed reads `open`. | `market_contracts/adapters/binance_rest_gate.py`, `services/market_ban_visibility.py` (new), `schemas/health.py`, `api/routes/health.py`, generated OpenAPI |

### Deviation: DB retry is limited to an allowlist

The first version retried every component. An audit of each cycle's writes and
side effects found that the watcher, Telegram, exit monitor and demo snapshot
can submit an order, reserve or claim notional, write journal or trade rows,
act on the kill switch, or send a message before a transient DB error is
raised. Re-running them at once is not proven idempotent. Only `activity` and
`indexing` retry now (`_DB_RETRY_COMPONENTS`). Every other component, including
any name not listed, fails as before, and its next scheduled cycle runs normally.

| Component | DB writes / external side effects before a mid-cycle transient DB error | Idempotent on immediate re-run? | Retried? | Evidence |
|---|---|---|---|---|
| `watcher` (scan cycle + autonomous internal paper + governed BloFin demo execution, research tick, heartbeat, ban publisher) | Paper/demo order submit and execution-ledger writes through `AutomatedPaperLoop` / `GovernedBloFinDemoLoop` (notional reservation, write-once claims, journal trade ids), scan leases, kill-switch reads and blocks, optional scan notification hook, `tick_existing_worker` research receipts, external heartbeat row, `market_data_gate` row and `market_data_banned` Telegram alert in `finally` | No. Write-once claims and leases protect parts of it, but the cycle can submit an order or reserve notional and then fail; the ban publisher and heartbeat run again on every attempt. Not proven end to end | No | `backend/src/app/workers/watcher_paper.py:1574-1660`, `:1099`, `:1696`; `backend/src/app/workers/paper_worker.py:965-989`; `backend/src/app/services/market_ban_visibility.py:57-62,83-103` |
| `telegram` | Delivers due outbox rows (Telegram `sendMessage`), polls inbound, projection pass (approvals) | No. A message can be sent before the outbox row is marked delivered; a re-run could send it again | No | `backend/src/app/telegram_activation/runtime.py:154-178` |
| `exit_monitor` | Closes canonical internal paper trades at SL/TP through `ExecutionService.close_canonical_paper_plan` (journal/trade writes, one commit per close), degradation streak rows, heartbeat | No. Closes are trade writes; one close can commit before a later close fails. The cycle also catches its own errors and returns `degraded` | No | `backend/src/app/workers/paper_exit_monitor.py:45-70,212-235,279` |
| `demo_snapshot` | BloFin read-only sync snapshot row, hand-trade journal mirror (`BloFinDemoManualMirror`, journal upserts), mirror cycle status row | Snapshot yes; mirror writes journal entries, so excluded by rule. The cycle catches its own errors and backs off, so a retry would rarely trigger anyway | No | `backend/src/app/workers/blofin_demo_snapshot.py:91-99,121-151,153-170`; `backend/src/app/services/blofin_demo_manual_mirror.py:1,137` |
| `activity` | Session advisory lock; BloFin read-only identity/metadata/page reads (GET only); per page, facts keyed by (org, env, uid, kind, native_id) plus the cursor commit together; failure path writes cursor retry fields | Yes. An existing fact with the same hash is skipped and a different hash raises a conflict; the page and the cursor commit atomically; the advisory lock is released in `finally` or when the connection dies. No order, claim, journal, kill-switch or message path | Yes | `backend/src/app/services/blofin_activity_service.py:136-268`; `backend/src/app/repositories/blofin_activity.py:61-120` |
| `indexing` | Job claim (`FOR UPDATE SKIP LOCKED`, `attempts += 1`, 300 s lease, claim token); embedding provider calls; Qdrant upsert/delete by point id; chunk `embedding_ref` and job finish; reconcile re-enqueue | Yes. A job claimed before the error stays leased, so the retry does not re-claim it until the lease expires, the same as the next cycle. Vector writes are keyed by point id. Attempts are bounded by `MAX_ATTEMPTS`. The only extra cost is embedding calls for other jobs. No trade or message path | Yes | `backend/src/app/rag/indexing.py:32,153-192,438,475-516,518-571`; `backend/src/app/workers/knowledge_indexing.py:27-47` |
| any other / future name | Unknown | Not audited | No (default) | `backend/src/app/workers/paper_worker.py:80,541` |

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

- [x] (C) Staging internal-paper boot with a Telegram-only conflict runs the Watcher with Telegram off, and the report is `degraded`. Production, non-staging, `paper_exchange_demo`, governed demo and disarmed boots do not fail soft (B8/T1/T3 scope tests); `test_armed_telegram_worker_fails_closed_without_bot_token` is restored for the demo configs. `/health/paper-worker` returns 503 while the report is `degraded` or `disarmed_unexpected`.
- [x] A trade-path conflict together with a Telegram conflict still raises, and the env is restored (`test_trade_path_conflict_still_raises`). The dedicated Telegram worker still fails closed.
- [x] The report is `not_applicable` outside staging, contains no secret values, and round-trips through the runtime-status row.
- [x] A report persist or alert failure never raises, and the alert is sent once.
- [x] (B) A transient DB error retries at most 2 times and then fails, only for `activity` and `indexing`. Watcher, Telegram, exit monitor, demo snapshot and unknown components do not retry. Non-transient errors do not retry. The bound (exactly 1 + 2 attempts, counted with a zero-delay stop event), the authority revert between attempts, and stop during backoff are tested; Griller mutants R1-R8 are killed.
- [x] (A) The ban snapshot keeps the ban start across repeated 418s, also for two concurrent 418s. The publisher writes only on change and alerts once per ban. An expired ban projects as `open`.
- [x] `npm run api:check` passes (OpenAPI change is additive only).
- [x] Full backend gate on the unsplit head (#410, `b13f6a52`): green locally on isolated UTC Postgres; Griller re-ran it (7239 passed, 5 sandbox-only failures). Each split head ran its targeted tests (see each PR body).
- [ ] Griller review on each split PR's final head SHA.

## render.yaml `sync: false` (split D)

Operator-tunable keys on `alphatrade-paper-worker-staging` that a Blueprint sync currently
resets to literal values:

- `PERPETUAL_EVIDENCE_SOURCE`: the documented rollback is `replay`. A sync silently undoes an operator rollback, or re-arms Binance during a ban.
- `PERPETUAL_EVIDENCE_SECONDARY_SOURCE`: the operator's Bybit fallback toggle.
- `MARKET_DATA_ENABLED`: the operator's market-data kill toggle.
- `PERPETUAL_EVIDENCE_TIMEOUT_SECONDS`: an operator tuning value during provider incidents.
- `MARKET_WATCHER_BRIDGE_AUTO_TICK`: this key is missing from the worker block. The disarmed-flags list in `core/disarmed_worker_boot.py` reads it, so add it as `sync: false`, like the other `MARKET_WATCHER_*` keys.

These should stay **synced literals**, because a sync must pull them back to their safe values:
`ENVIRONMENT`, `EXECUTION_MODE`, `ENABLE_REAL_TRADING`, `MARKET_DATA_FUTURES_BASE_URL`,
`BYBIT_PERPETUAL_BASE_URL`, `BLOFIN_DEMO_REST_BASE_URL`, `BLOFIN_DEMO_WS_URL` (host pins,
the #398/#399 lesson; added in D on both services), `DEBUG`, `LOG_JSON`, and all auth, cookie,
denylist, CORS, rate-limit, and proxy keys.

D also makes the four data-source keys `sync: false` on the API service, so a sync cannot
leave the API and the worker on different evidence sources.

Arm flags the worker reads: D declares `AUTONOMOUS_INTERNAL_PAPER_ENABLED`,
`AUTONOMOUS_BLOFIN_DEMO_ENABLED` and `GOVERNED_BLOFIN_DEMO_{ENABLED,ORGANIZATION_ID,USER_ID,ACCOUNT_ID}`
as `sync: false`. Their safe default is the code default when unset (off / empty), pinned by
`test_declared_arm_keys_default_off_when_unset`. A synced literal `value: false` was not used:
it would disarm staging on every Blueprint sync, which is the silent-disarm problem above.
Other dashboard-only keys (`PAPER_EXIT_MONITOR_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_ID`,
`TELEGRAM_CHAT_ID`, `KNOWLEDGE_INDEXING_ENABLED`) are left for a follow-up.

> **Must verify before merging D:** how Render treats (a) a key that changes from a synced
> literal to `sync: false` (does the current value stay?), and (b) dashboard keys that the
> Blueprint does not declare (are they kept or removed on sync?). Check this against the
> Render Blueprint docs (`render.yaml` spec, `sync: false`) before relying on it. If Render
> drops a value, the code default applies: replay evidence and arm flags off, so the worker
> fails toward disarmed, never toward trading.

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
- **DB retry re-runs a cycle** that failed partway, for `activity` and `indexing` only. Both were audited (table above): keyed facts with an atomic cursor commit, and leased claims with vector upserts by point id. Adding a component to `_DB_RETRY_COMPONENTS` needs the same audit. Trade, journal, kill-switch and message paths stay out.
- **The runtime-status row is overloaded**: the config report reuses existing columns (`request_count` holds the failure bitmask). The check order is append-only.
- **Ban dedup**: two separate bans within the 15 min ledger cooldown page once.
- **Disarmed staging cannot page** (no bot token). It is visible on health and in error logs only. With C, that report also turns `/health/paper-worker` into HTTP 503 for an uptime check.
