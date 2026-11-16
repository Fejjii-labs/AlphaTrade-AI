# Staging autonomous trades not firing — 2026-11-16

## Scope and evidence limits

Repo `Fejjii-labs/AlphaTrade-AI`, worktree `/workspace/at-trades-firing`, branch
`codex/trades-not-firing`, baseline `b319d4fbc02fb7e539bf95d37d45fe3147be1e08`.
Only staging reads were GET `/health` and GET `/health/paper-worker`, captured at
2026-11-16T11:22:14Z. Raw responses are in `/workspace/at-notes/trades-firing/`.
No direct staging DB connection, private API reads, remote writes, environment changes,
push, deployment, credential access or real trading were performed.
Local tests use isolated fixtures; their network guard permits loopback only.

The last-open/close dates and aggregate evaluation/event counts in the request
are supplied evidence, not independently queried here. The permitted endpoints
contain no decision payloads, setup family breakdown, eligibility rows, order
history or raw exception details. Exact attribution of the 100 historical
`paper_decision` rows is therefore UNKNOWN. Reporting all 100 as SFP refusals,
risk blocks, or failures from #458 would be unsupported.

## Findings and ranked causes

1. **Confirmed: internal paper is disarmed by configuration.** The worker reports
   `autonomy_mode=blofin_demo`, internal-paper env ceiling `false`, DB toggle
   `true`, effective `false`. `resolve_autonomous_mode` requires exactly one flag
   and a matching venue (`backend/src/app/services/autonomous_trading.py:91`).
   The worker consults the organization DB toggle only after the env mode resolves
   to internal paper (`services/autonomous_paper_switch.py:81`). A true DB toggle
   cannot override a false env ceiling, and the demo mode ignores this toggle.
2. **Confirmed code behavior; historical applicability UNKNOWN: SFP cannot execute
   on BloFin demo.** `services/governed_blofin_demo.py:689` returns stage `skipped`,
   reason `sfp_execution_plan_not_authorized` before plan creation. This is an
   explicit product guard, covered by
   `tests/test_governed_blofin_demo.py:784`. Any SFP candidate reaching that point
   on the observed mode cannot open a trade. Earlier gates may refuse it first.
   Internal paper has an SFP plan path (`services/automated_paper_loop.py:467`).
3. **Confirmed: some scans fail market-evidence acquisition.** The paper worker's
   latest error is `canonical_contract_invalid_contract`, activation `degraded`.
   The supplied 2-day count is 2430. This prevents those scans reaching confirmed
   candidate creation. The underlying exception class/component and affected
   symbols are UNKNOWN from health alone. This upstream failure does not explain
   every successfully confirmed setup or historical decision.
4. **Unverified remaining causes:** quote freshness, configured strategy/symbol
   mismatch, account availability, experiment selection, risk/eligibility or
   venue reconciliation may stop the surviving candidates. No health payload
   identifies which of these affected the 100 rows. Do not change risk limits.

The API process reports `exchange_mode=paper_internal`, while the worker config
snapshot reports `paper_exchange_demo` (reported at 10:49:35Z) and the fresh
worker-effective mode remains `blofin_demo`. These are distinct process settings,
not proof the worker is internally contradictory. Changing API settings alone
will not reconfigure the worker.

## From confirmed setup to decision to trade

Paths below are relative to `backend/src/app/`.

1. `evidence_pipeline/watcher_port.py:250` assembles OHLCV-family or first-slice
   evidence. `watcher/fusion_evaluation.py:313` loads this evidence and evaluates
   the canonical strategy. `persist_confirmed_setup` at line 256 permits a
   candidate only for a successful `CONFIRMED_SETUP`, in persist-evidence mode,
   with compiled approved authority, matching evidence and a persistence fence.
   WATCH/PARTIAL/EXPIRED/INVALIDATED observations are not trade instructions.
2. `workers/watcher_paper.py:1598` continues or resumes the candidate.
   `_continue_paper_loop` at line 1706 rejects projected markets with
   `market_projection_observation_only`; it also needs a session, canonical
   runtime, discussion, published candidate and last assembly. Global/tenant
   kill switches and mode refusals stop entries. OFF yields
   `autonomous_mode_disarmed`. Demo selects an experiment context and invokes
   `GovernedBloFinDemoLoop`; internal paper invokes `AutomatedPaperLoop`.
3. `services/automated_paper_loop.py:178` is inherited by the demo loop. It checks
   kill switches, fresh action evidence, posture, setup and lineage, then an
   enabled scoped execution account and persisted autonomous strategy/symbol/
   trade-count settings (`services/autonomous_trading_guard.py:36`). A confirmed
   setup means pattern truth, not account or execution permission.
4. **Internal paper:** `_bound` at `services/automated_paper_loop.py:314` evaluates
   ActionEligibility; non-actionable outcomes return their refusal and any
   eligibility id. It then applies profile, exposure, breaker, sizing and reward/
   risk gates and creates a canonical plan. `_execute_existing` at line 512
   rechecks the stored plan, mode, owner toggle and kill switch, records SYSTEM /
   AUTONOMOUS approval, and calls `ExecutionService.execute_paper_plan` at line
   635. The canonical claim is final for reservations, caps, idempotency and
   safety. Only ALLOW proceeds to `apply_paper_plan_fill` at line 722 and the
   journal projection; success returns `filled/open_journal` with real stored ids.
   These are simulated fills, with no venue dispatch.
5. **BloFin demo:** `services/governed_blofin_demo.py:641` checks the separately
   pinned governed-demo/credential gates. The scoped account must match the
   configured organization/user/account. `_bound` at line 677 refuses SFP first;
   other supported families pass venue state, verified ledger, quote, leverage,
   configured account risk, sizing, eligibility and plan gates. The execution
   path at line 1481 claims the authorized plan, then leases and authorizes a
   durable `VenueSubmitEffect`, dispatches only to an allowlisted demo provider,
   and reconciles actual demo evidence. At line 1567 the proof may still be
   `dispatched` without a journal entry. An ALLOW/dispatch alone is not a fill.
   `services/canonical_paper_execution.py:119` deliberately does not fabricate a
   demo journal from authorization; immutable venue fills are required.
6. `strategy_brain/assembly.py:294` records the proof via `attach_paper_result`.
   `strategy_brain/records.py:194` stores `paper_stage`, `risk_state`,
   `reason_codes`, `risk_reason_codes` and candidate/eligibility/plan/command/
   fill/journal ids. At line 224 the event is `paper_trade_opened` only when
   `proof.journal_trade_id` exists; otherwise it is `paper_decision`. This event
   name is not an ALLOW verdict. It can represent skipped, blocked, dispatched,
   or another continuation outcome without a journal id.

Consequently the supplied 100 events prove only that the recording path had no
journal id at those moments. They do not establish 100 approvals, 100 distinct
setups, or 100 attempted exchange orders. Event identity includes candidate,
stage and reason (`strategy_brain/records.py:218`), so counts across event kinds
and evaluation windows cannot be joined arithmetically. Later dispatch
reconciliation also requires separate evidence; absence of this setup event is
not sufficient proof that no demo venue order exists.

To answer the exact historical question, an authorized operator must export the
100 event payloads with occurrence time, setup family, candidate id, paper_stage,
reason_codes, risk_reason_codes and execution/journal ids, and correlate those
ids with existing command/fill records. No such read was attempted here.

## Meaning of canonical_contract_invalid_contract; #458

`evidence_pipeline/watcher_port.py:313` translates an undiagnosed
`MarketContractError` into `canonical_contract_invalid_contract` at line 343.
The public message intentionally hides raw provider/exception content. Typed
component diagnostics take priority at line 275; stale, regional-provider and
forming-candle exceptions have dedicated handling. A plain Pydantic/ValueError
without a typed component diagnostic instead reaches the unexpected-error
fallback; this code is not an LLM schema validator.

Concrete existing triggers include missing verified tick size
(`evidence_pipeline/instrument_tick.py:34`), missing required OHLCV
(`strategy_brain/assembly.py:106`), wrong market/instrument/source or gaps in
closed series (`market_contracts/ohlcv.py:98`), immutable observation/payload
conflicts (`persistence/public_market_observations.py:23`, :39, :75, :98), and
provider revision regression (`strategy_brain/assembly.py:145`). The wrapper can
collapse subclasses such as DuplicateDataError into the same public code.

These rejections are expected safety behavior for genuinely invalid evidence.
The health error alone cannot distinguish bad upstream data, stored receipt
conflict, absent tick metadata or an assembly defect. Do not overwrite receipts,
ignore mismatches, invent ticks, widen freshness, or catch-and-continue.

Existing `tests/test_sfp_receipt_reuse.py:98` covers cold/restarted rolling scans
whose Decimal encodings differ while typed values match; the implementation
already compares typed bars. Line 133 deliberately verifies a real changed
payload is rejected with this exact code and original data preserved. Explicit
new revisions append; regression to an older revision is refused at line 159.

Local history shows that receipt behavior is already present in #376
(`ce7aeeeb`), and the demo refusal predates #458. #458 (`b9201f52`) changed capture
LLM schema construction (`agent_capture/model.py:20`,
`schemas/strict_json_schema.py:61`), prompts, agent graph and telemetry. It did not
change the market-contract wrapper, Brain assembly or receipt persistence.
No reproduction or static call-path evidence ties its strict JSON schema to
this staging error. Its November 13 merge also postdates the supplied October 30
last-open date; it cannot by itself establish why the entire gap began. Do not
claim #458 caused or fixed it.

## Required configuration, observed values and risk

Apply any future change to the **worker service**, with coherent API settings
where appropriate. These are recommendations only, not changes made here.
`UNKNOWN` means not exposed by the permitted health endpoints; defaults are not
substituted for deployed facts. A resolved mode permits inference of its boolean
flags because the resolver fails closed on missing/conflicting values.

| Setting | Current permitted evidence | Internal paper requirement | BloFin demo requirement |
| --- | --- | --- | --- |
| `ENVIRONMENT` | staging API; worker staging config report | staging | staging |
| `EXECUTION_MODE` | paper API; worker controlled arm passes | paper | paper |
| `ENABLE_REAL_TRADING` | false API derived state; worker real-trading-disabled check passes | false | false |
| `EXCHANGE_MODE` | API paper_internal; worker paper_exchange_demo | paper_internal | paper_exchange_demo |
| `AUTONOMOUS_INTERNAL_PAPER_ENABLED` | false worker env ceiling | true | false |
| `AUTONOMOUS_BLOFIN_DEMO_ENABLED` | true inferred from worker blofin_demo resolution | false | true |
| Organization internal-paper DB toggle (not env) | true worker-effective | true, retain | ignored |
| `WATCHER_ORCHESTRATION_ENABLED` | true worker | true | true |
| `WATCHER_PAPER_STAGING_ACTIVATION` | true worker | true | true |
| `BLOFIN_DEMO_ENABLED` | true inferred from passing worker controlled demo arm | false for sealed credential isolation | true |
| `GOVERNED_BLOFIN_DEMO_ENABLED` | true inferred from passing worker controlled demo arm | false | true |
| `BLOFIN_LIVE_EVIDENCE_DEMO_ENABLED` | false API; UNKNOWN worker | false | false for worker; separate API capability is not required |
| `MANUAL_BLOFIN_DEMO_ENABLED` | UNKNOWN | false for autonomous-only isolation | not needed for autonomous worker |
| `GLOBAL_KILL_SWITCH_ACTIVE` and tenant switch | worker observed kill_switch_active=false; global env not separately exposed | verified inactive | verified inactive |
| `PERPETUAL_EVIDENCE_SOURCE` | binance_usdm API and worker config | binance_usdm, active public read-only evidence | same |
| `PERPETUAL_EVIDENCE_SECONDARY_SOURCE` | bybit_usdt_perpetual API; UNKNOWN worker | retain separately validated source | same |
| `WATCHER_PAPER_POLL_INTERVAL_SECONDS` | UNKNOWN; mode's arming check currently passes | positive, <= smallest allowed timeframe in seconds | same |
| `PAPER_SIGNAL_ALLOWED_TIMEFRAMES` | UNKNOWN; arming check currently passes | valid nonempty list | same |
| `WATCHER_APPLY_STRATEGIES_TO_WATCHLIST` | UNKNOWN | no value grants projected scopes execution authority; exact approved market required | same |
| `MARKET_WATCHER_ENABLED` / `MARKET_WATCHER_BRIDGE_ENABLED` / `MARKET_WATCHER_BRIDGE_AUTO_TICK` | first two false API; all false inferred from worker controlled arm | false | false |
| `TELEGRAM_ALERTS_ENABLED` / `AUTOMATIC_TELEGRAM_DELIVERY_ENABLED` | false worker | false | false |
| `TELEGRAM_PAPER_ACTIVATION_ARMED` / `TELEGRAM_INTERACTION_ENABLED` | true worker | retain controlled projection pair | same |
| `TELEGRAM_INBOUND_MODE` / `TELEGRAM_NETWORK_PERMITTED` | API off/false; worker projection implies polling/true | retain worker polling/true with controlled pair | same |
| `PAPER_EXIT_MONITOR_ENABLED` / `PAPER_EXIT_MONITOR_DRY_RUN` | UNKNOWN; monitor heartbeat running | true/false for actual simulated stop/target exits, not required for entry arming | internal monitor not demo exit authorization |
| `GOVERNED_DEMO_AUTO_LEVERAGE_WRITE_ENABLED` | UNKNOWN | false | not required if existing demo leverage matches; do not enable as a trade-firing workaround |

**Exact internal-paper mode change set:** worker `EXCHANGE_MODE=paper_internal`,
`AUTONOMOUS_INTERNAL_PAPER_ENABLED=true`, `AUTONOMOUS_BLOFIN_DEMO_ENABLED=false`,
`BLOFIN_DEMO_ENABLED=false`, `GOVERNED_BLOFIN_DEMO_ENABLED=false`. Explicitly keep
`BLOFIN_LIVE_EVIDENCE_DEMO_ENABLED=false`, `MANUAL_BLOFIN_DEMO_ENABLED=false`,
`GOVERNED_DEMO_AUTO_LEVERAGE_WRITE_ENABLED=false`, `EXECUTION_MODE=paper` and
`ENABLE_REAL_TRADING=false`; retain the existing true organization switch and
watcher arm. Do not turn on both autonomy flags. The API already reports the
internal exchange mode; its autonomy flags remain UNKNOWN. No database toggle
write is needed based on the current worker observation.

Risk: this arms simulated entries and writes paper accounting/journal data when
all other gates pass. It does not authorize exchange execution; the isolated
paper profile seals BloFin execution credentials
(`core/execution_credentials.py:51`). Read-only BloFin sync is separate and
need not be enabled to run internal paper. An enabled scoped execution account,
approved compiled strategy, fresh evidence and allowed risk still must exist.
Flags cannot guarantee a fill and do not repair invalid market evidence.

**If the intended destination remains BloFin demo:** keep the demo column,
plus valid `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`,
`GOVERNED_BLOFIN_DEMO_USER_ID`, `GOVERNED_BLOFIN_DEMO_ACCOUNT_ID`, an enabled
matching scoped account, demo-only `BLOFIN_API_KEY`, `BLOFIN_API_SECRET`,
`BLOFIN_API_PASSPHRASE`, and `BLOFIN_DEMO_REST_BASE_URL` on the allowlist
(e.g. `https://demo-trading-openapi.blofin.com`); optional `BLOFIN_DEMO_WS_URL`
must also be allowlisted. Values/presence for these worker credentials and pins
are not individually exposed by health; the passing controlled arm implies the
aggregate gate passed at report time. Account/ledger/venue readiness is not
proven by that settings check. `core/execution_credentials.py:92` and :134 define
these gates. Demo mode permits demo account/order mutations, with no live-host
path. There is **no env flag that authorizes SFP demo plans** in current code.
Use an already approved supported strategy or choose internal paper; do not
remove the SFP refusal as a smallest-fix shortcut.

## Changes, validation and next action

No application-code defect was reproduced. No regression test was added and no
execution, schema, safety or risk code was changed. The smallest supported
remediation is a separately approved operator mode correction if internal paper
is intended, followed by evidence review. The local commit records this
investigation only. Exact local head SHA and final validation counts are in the
external companion `/workspace/at-notes/trades-firing/report.md` to avoid a
self-referential commit hash.

Validation (from `backend/`):

- `env -u CI uv run --extra dev python -m pytest tests -k "paper or canonical or autonomy or decision" -q`
  exited 0: **1049 passed, 182 skipped, 0 failed/errors** (1231 selected).
  Repo addopts plus command `-q` suppress the numerical summary; counts were
  recovered from the complete pytest progress output, preserved externally in
  `requested-tests-output.txt`. Warnings included test JWT key lengths,
  Qdrant compatibility probing, and teardown Binance gate/lease TypeErrors;
  none changed the zero exit status.
- `env -u CI uv run --extra dev python -m pytest tests/test_governed_blofin_demo.py::test_governed_demo_loop_refuses_sfp_before_planning tests/test_sfp_receipt_reuse.py -q`
  exited 0: **8 passed, 5 skipped**. This verifies the deliberate demo SFP
  refusal, SQLite receipt reuse/conflict/revision behavior and sanitized errors.
- PostgreSQL persistence coverage was skipped without a disposable local fixture
  database (`tests/support/postgres_persistence.py:28`,
  `tests/test_sfp_strategy_brain_runtime.py:505`). No staging DB workaround was
  attempted. Mock/SQLite success does not prove staging data consistency.
- `git diff --check` passed. No application/risk/settings changes.

No PR or full merge gate is requested; no full backend gate or workflow dispatch
is used. Commit trailer: `Full-Backend: false`.

Next action: review the internal-paper configuration set and obtain an authorized
historical decision/market-error export. If later evidence reproduces a real
code defect, add a failing focused regression before the smallest patch. This
investigation does not authorize any staging change.
