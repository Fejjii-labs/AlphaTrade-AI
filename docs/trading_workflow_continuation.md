# Trading workflow continuation from PR249

Feature base: reviewed `df0a12d3b9c03b73ae8a1109adad66e4cb900885`.
Branch: `codex/trading-workflow-completion`; isolated worktree. The release
handoff on `cursor/experiments-market-handoff` remains authoritative for PR249.
This feature does not advance, redispatch, deploy or rewrite that candidate.

## Implemented acceptance and remaining acceptance

| Requested workflow | Completed local criteria | Remaining native/runtime acceptance |
| --- | --- | --- |
| TrendPulse screening → strategy/plan/execution | Fixed authored v1 and causal receipts compile into the existing canonical strategy authority; `evaluate_setup` re-evaluates original receipts. Authenticated explicit Candidate consumer and existing screening UI review action. Existing eligibility, floor sizing, plan, approval and internal execution claim compose with original stop/full gross1R and no runner. | Native entry requires current verified BloFin UID/account, native quote/rules/basis, account-wide atomic experiment reservations/loss counters and final dispatcher context. No native TrendPulse execution adapter is activated. |
| Exploration/Validation | Bounded deterministic variant selection; Validation's single frozen variant, fresh sample group, exact account/source and risk envelope preserved. Running/expiry/current-role/account gates; latest closed5m only;5–60s window; stable request IDs; cap every acquisition attempt per variant at its declared maximum. | Native/simulator closed-trade resolvers and accepted sample admission remain separately gated. Scheduling is implemented for the existing TrendPulse acquirer; Nested/SFP retain their existing Watcher acquisition, not a fabricated generic experiment executor. |
| BloFin reconciliation | Existing governed and manual reconciliation reused; raw signed fees/currency/protection pointers preserved; supported closure independent of unknown money. Unsupported currency/conventions, funding and gross/net attribution withheld. Legacy receipts are retained. | Venue-confirmed entry→protection→generated-child edge, complete flat→open→flat position/fill ancestry, historical fee currency/sign and `fillPnl` fee/funding basis. Native sample/performance admission remains unavailable. |
| Nested/SFP notification gaps | Existing Agent reads bounded tenant/version scan/acquisition/evaluation, detection, Candidate/paper-decision and outbox evidence. Precise rejection/delivery codes and missing/truncated receipts; no threshold or notification-policy changes. | Actual installed revision and authenticated tenant/version/time-window receipts. No live cause is claimed from local fixtures or an empty alert list. |
| OI/flow/CVD/book | Existing Agent context retains provider, identities, methodology, clocks and unavailable reasons. Existing independent probe adds book verification, independent of core CVD availability. | Actual Binance/Bybit public access was rejected by proxy CONNECT403 before providers responded. Native OI/flow/CVD/book remain unverified. |

The five implementation workstreams are reviewable. Native end-to-end trading,
actual missing-alert attribution and actual provider acquisition remain **0/3
accepted** here. No simulated result supplies provider evidence. Missing native
admission or account authority never switches a BloFin experiment to simulation.
BloFin remains authoritative for balances, fills and replenishment.

## Connected interfaces and authority

- Existing strategy compiler binds the entire TrendPulse spec. Old Nested/SFP/first
  slice serialization omits the new nullable metadata field, preserving old hashes.
  A strategy still needs its existing explicit approval/lifecycle prerequisites.
- `POST /trendpulse-screenings/{record_id}/candidate` requires current Trader/Owner
  membership, original tenant-owned receipt, approved immutable compiled strategy,
  active approved experiment interval and exact account proof. It re-evaluates fixed
  rules and uses `CandidateLifecycleService`. Expiration is not extended. No order
  or sample is requested. The UI offers this only for a running public screening;
  synthetic fixture rows remain research presentation.
- `TrendPulsePipeline.prepare_internal_plan` is a **server-only composition seam**.
  It requires trusted `ActionEligibilityCommand`, `ExperimentRiskState` and a fresh
  actual quote time; callers cannot submit these financial values over HTTP. It
  uses existing sizing/eligibility/plan services. Exact entry price and original
  structural stop/full1R must match. Mock/fallback evidence and native-source plans
  are refused by this internal adapter. Its test exercises existing approval and
  canonical internal execution claim; it grants no automatic send permission.
- `POST /experiments/research/tick?limit=1..2` returns at most two small persisted
  screening summaries, `execution_requested=false`, `samples_admitted=0`. No
  hidden retry, catch-up, backdating, model call or performance admission. Failures
  and no-setups consume finite acquisition capacity, not accepted samples.
- The existing worker's Watcher component runs the same research tick only after
  its existing armed scanning path returns running/degraded and explicit screening
  opt-in remains enabled. It adds no component/thread or execution engine. A denied
  activation preflight stays refused. One blocked version/provider does not stop
  independent approved research. At most16 owner scopes/32 versions are examined,
  and two acquisitions run per tick. Original screening transport/time/weight and
  PostgreSQL single-flight limits remain authoritative.
- Legacy first-slice Watcher acquisition gives the precise refusal
  `trendpulse_screening_receipts_required` for TrendPulse. It cannot silently treat
  the5m/15m family as the old15m/4h detector. Approved TrendPulse uses its explicit
  screening consumer until native account/market composition is qualified.
- Existing sample endpoints still refuse unsupported source proof. Screening
  records, Candidates and approval receipts never become native trade performance.

## Focused evidence

All monetary/provider test inputs are synthetic or `httpx.MockTransport`. Actual
PostgreSQL17 persistence uses disposable loopback databases/schemas; no hosted DB.

- Root canonical/risk/screening/strategy/plan/worker selection: **262 passed, no
  skips**, including the two existing PostgreSQL worker lease cases. Later
  receipt/UI/API refinements have separate affected checks below.
- Final current workflow/API selection: **24 passed, no skips**, including HTTP
  RBAC, exact-receipt Candidate convergence, bounded disabled tick, original
  geometry, approved canonical internal execution claim and precise legacy-port refusal.
- Existing evidence/worker selection after precise acquisition refusal and worker
  hook: **60 passed, two explicitly deselected PostgreSQL cases**; those lease
  cases were included in the262 selection. Strict mypy:11 touched root modules.
- Notification/market selection:140 passed/no skips; final diagnostic/probe14
  passed/no skips. See [evidence report](workflow_notification_market_evidence.md).
- Governed native attribution:114 distinct focused cases/no skips. The final
  manual/governed supplement independently rerun by the feature owner passed
  **48 cases, no skips**,23.46s. The earlier157 green manual cases are reused;
  their single failing currency fixture is corrected and included in the48.
  Detailed commands are in [BloFin report](blofin_workflow_remaining_evidence.md).
- Final frontend screening/generated/experiment/BloFin-boundary selection:
  **36 passed**, affected ESLint/typecheck and API drift passed after the manual
  schema change. Full schema SHA256:
  `da960192a22014d6d443b56add2d85c7fcba4d1842032b618a51022406278577`.
  No full backend CI is dispatched by this task.

Commands use `.venv/bin/pytest -o addopts='' -q -rs` with explicit disposable
`EXPERIMENT_TEST_POSTGRES_URL` and, for public-schema-reset fixtures, a separate
`PHASE1_POSTGRES_URL`. Affected modules are listed in the PR. Earlier development
failures were fixed: typed geometry direction, allowed presentation fields/hash,
fixture roles and top-level tick response shape. Final counts apply to their
stated source coverage, not to an older green CI badge.

## PR251 focused CI correction

The original focused run at `03be2d3` did **not pass**: run
`38249795179`, backend job `114812825333`, failed collected case 840 before
its 20-minute cancellation. The two-case reproduction identified an in-process
Alembic logging side effect in the BloFin activity migration fixture. The fixture
now uses the existing fileless Alembic configuration pattern and asserts that root,
HTTPX and HTTPCore logging state survives its real migration round-trip.
No redaction assertion, production logging setting or migration changes.

See [exact collection, reproduction and repair evidence](pr251_focused_ci_repair.md).
The historical local checks above establish their stated coverage only. They never
established a passing PR251 CI run or native trading acceptance. Automated research
continues to return `execution_requested=false` and `samples_admitted=0`; canonical
internal simulated execution checks do not enable autonomous exchange trades.
Shared CI isolation, selection and duration changes remain with the PR249 release
owner. No full CI was launched.

## Concrete blockers and next executable step

A read-only staging `/health` request on12October returned HTTP200, status
`ok`, version0.1.0 and API `git_sha=b165b92276346f0e0fe3ccdbd2bec3443dc75d40`.
This verifies the observed API revision only; worker/frontend and provider receipt
identity remain unverified. This feature is not deployed.

1. Review this feature PR against `df0a12d3`; keep the release owner's exact-ref CI,
   rollout and operator settings untouched. No feature merge/deployment is implied.
2. In an approved read-only environment, use the existing BloFin collector/contract
   resolver to obtain exact UID/rules/quote and native protection/position/fill
   receipts, including declared fee currency/sign and PnL/funding semantics. The
   current read-only client gate refused absent dedicated bindings; permitted host
   policy also omits official BloFin docs/native demo access. Do not infer absence
   of credentials elsewhere or supply them in chat. Native order activation remains
   separately governed; local fixture execution is no substitute.
3. Collect installed revision and existing Nested/SFP scoped receipt packet, then
   run `read_notification_diagnostics` against that authorized read-only session.
4. With approved exchange egress, run the existing independent probe for
   `binance_usdm` and `bybit_usdt_perpetual`; retain timestamps/hashes/coverage and
   unavailable reasons in the existing Agent context. The task's actual failed OI
   probes establish proxy access refusal only.

No deployment/settings/credentials/migrations, notification sends, native orders,
local balance resets/top-ups, main merge or full backend gate. Feature
checkpoints are retained under the existing ignored `.ai/local/trading-workflow/`;
the inherited root HANDOFF/CHANGELOG are preserved. Publishing another newest root handoff
would compete with the active release owner's checkpoint, so this PR keeps its
scoped continuation here. Mac/iCloud sync is unavailable and unverified.
