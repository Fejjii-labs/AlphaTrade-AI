# TrendPulse receipt correction and screening verification handoff

Correction PR: [PR243](https://github.com/Fejjii/AlphaTrade-AI/pull/243).
Correction SHA: `8b9d1b83ee5c4082d87005afbfdfa8343d286a7e`, published separately
before the continuation. Screening branch: `codex/trendpulse-1r-screening`, based
on that SHA; exact continuation SHA and draft PR are in the PR description.
[Screening API/source/migration contract](trendpulse_screening_contract.md).

## Correction evidence

Original `bab0e9ce646a948789ff606b8e4c81e68e1a3988` reproduced **6/6** receipt
latency failures against positive-delay long/short fixtures at :05/:10/:15.
Corrected pure adapter selection: **140 passed in15.65s, zero skips/warnings**.
Receipt delays were observed+2s, received+3s, recorded+4s, decision+5s; no
zero-latency history was manufactured. Late receipts after an evaluation cannot
alter it; later decisions can qualify until the strict60s expiry, preserving all
original arrival timestamps and fixed M15/M5 boundaries. Pure specification,
thresholds, structural stop and rounded gross1R geometry are unchanged.

Automatic focused [CI38081667069](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38081667069)
on that exact correction succeeded: **305 backend passes in49.81s, zero skips**;
**12 frontend passes**, shared contract drift, lint/types and deployment-safety
checks passed. Complete backend acceptance, complete frontend unit/build, Docker,
evaluations and e2e gates were skipped by focused policy. No manual dispatch.

## Continuation local evidence

Locked Python3.12 dependencies and an independent worktree environment.
PostgreSQL16-alpine was a task-owned loopback disposable container; tests create
and drop UUID schemas. No shared/runtime database, external exchange or native
account was used. Explicit environment variables were set before collection;
no skipped PostgreSQL cases are counted as passing.

- New acquisition, screening persistence, API consumer and migration tests:
  **36 passed in21.96s, zero skips**.
- Combined focused selection below: **329 passed in96.30s, zero skips/warnings**.
  Includes delayed receipt correction, exact experiment/account/source attribution,
  lifecycle/promotion/immutability, precision/risk, concurrent identities, real
  migration round trips, existing native-activity worker isolation/shutdown,
  watcher ownership, and provider finality.
- Migration compatibility follow-up: **33 passed in55.05s, zero skips** across
  nine affected migration/learning/Journal files. This includes four cases already
  present in the329 selection; these counts are not an additional33 unique cases.
  The first screening push missed legacy current-head guards. A disposable local
  run reproduced **5 failed /1 passed** solely on a11-versus-a12 expectations.
  All current-head consumers now explicitly expect a12 while preserving a11→a10
  ancestry, accepted merge topology and history-preservation assertions. No
  historical migration or production screening code changed in this follow-up.
- Repository Ruff check and format check: pass (1233 files already formatted).
  Scoped strict mypy: pass, seven new source files. This does not claim repository
  wide mypy cleanliness.
- Full shared `npm run api:generate` and `npm run api:check`: pass. Schema SHA256:
  `5c6de2777b7af12e27abd1dbf901076bd7909e078bf80b0e895e4c5543202003`.
  Pilot generated client selection remains unchanged.
- Focused frontend generated-contract verification: **7 passed**, one file.
  Frontend lint and typecheck pass; Next reports its existing lint-command
  deprecation notice, with no ESLint warnings or errors.

Raw Binance REST responses are exercised through httpx mock transport and the
production GET/closed-confirmation/acquisition/detector code, including compression,
row/byte/time limits, missing/changed confirmation and429 without retries. Six
positive cases cover all three 5m positions in both directions; each confirmed
stream arrives8s after trigger close. This verifies integration mechanics, not
live Binance connectivity or market signal frequency.

Persistence tests commit, reopen a new session/connection, and prove original
request idempotency without acquisition plus natural-ID dedupe on new requests.
Later receipts retain their actual times, while the original accepted evidence
stays unchanged. Changed semantic derivation cannot overwrite the first signal.
SQL UPDATE/DELETE guards and unique constraints are exercised independently of
the service. A blocked acquirer is synchronized by events with bounded5s waits;
an independent read completes and a competing request fails immediately without
a second acquisition while the first is still blocked. Capacity/locks release on
completion/failure. RBAC, tenant/user isolation, read pagination, read bounds,
status filtering, disabled/source configuration and untrusted HTTP inputs are
covered. The actual a11→a12 migration is used for a real screening write, repeated
head upgrade and reversible fixture round trip preserving prior domain/native data.

## Representative replay coverage and counts

[Machine-readable report](contracts/trendpulse_screening_replay_report.v1.json).
This is an explicitly synthetic regime replay, not exchange performance evidence.
Four fixed-seed regimes: uptrend, downtrend, range and volatile, seeds24300..24303.
Each has900 closed M5 candles and300 aggregated closed M15 candles (75h data,
250-M15 warmup), followed by24 consecutive trigger windows × long/short.
Original synthetic envelopes retain observed+2s, receive+3s, recorded+4s; decision
+8s. Every regime covers all three M5 positions (16 decisions per position).
Thresholds were fixed before the run and were not adjusted after seeing counts.

| Coverage | Count |
| --- | ---: |
| Decisions |192 (48 per regime;24 per direction in each) |
| Persisted no-setup records |192 |
| `trend_not_confirmed` |186 |
| `pullback_not_confirmed` |6 |
| Signals |0 |
| Idempotent records after independent connection restart |192 |
| Experiment samples/native trades |0 /0 |
| Genuine real-market receipt replays |0 |

Manifest SHA256:
`80cd25491e8ac644b4b9b9e6beda6ff03a682a18cb2d2d3103974605e3b38717`.
The positive unit fixtures prove signal plumbing; they are separate from these
untuned regime counts. No win rate, PnL, fill or native performance is claimed.

This managed environment allows GitHub/package traffic, not the exchange/archive
hosts. No genuine archived dataset with original receipt metadata was available;
a historical OHLCV archive alone cannot supply causal arrival proof. Genuine
market replay and public-network acquisition remain unverified dependencies.
Synthetic counts do not satisfy a real-market performance or activation gate.

## Exact focused reproduction

From `backend/`, set the URL only to a disposable PostgreSQL fixture. The example
is the loopback container used for this task; never substitute a production URL.

```sh
export EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:55439/alphatrade_test
export PHASE1_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
export AT028_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
export BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
.venv/bin/pytest -o addopts='' -q \
  tests/test_trendpulse_1r_adapter.py tests/test_trendpulse_1r_experiment_contract.py \
  tests/test_trendpulse_1r_domain.py tests/test_trendpulse_screening_acquisition.py \
  tests/test_trendpulse_screening.py tests/test_trendpulse_screening_api.py \
  tests/test_trendpulse_screening_migration.py tests/test_experiment_domain.py \
  tests/test_experiment_api.py tests/test_experiment_native_identity.py \
  tests/test_experiment_risk.py tests/test_experiment_concurrency.py \
  tests/test_experiment_migration.py tests/test_manual_demo_migration.py \
  tests/test_watcher_paper_activation.py tests/test_blofin_activity_worker.py \
  tests/test_watcher_worker_ownership.py tests/test_binance_candle_finalization.py --tb=short
.venv/bin/pytest -o addopts='' -q \
  tests/test_blofin_activity_migration.py tests/test_knowledge_indexing_migration.py \
  tests/test_knowledge_file_migration.py tests/test_release_wave002_migrations.py \
  tests/test_phase2_4_alembic_postgres.py tests/test_phase8_learning_persistence.py \
  tests/test_journal_trades_alembic_empty_tenant.py \
  tests/test_trendpulse_screening_migration.py tests/test_experiment_migration.py \
  --tb=short --show-capture=no
.venv/bin/ruff check . ../scripts/run_backend_focused.py ../scripts/run_frontend_focused.py
.venv/bin/ruff format --check . ../scripts/run_backend_focused.py ../scripts/run_frontend_focused.py
.venv/bin/mypy --strict --follow-imports=silent \
  src/app/strategy_brain/trendpulse_screening \
  src/app/schemas/trendpulse_screening.py src/app/api/routes/trendpulse_screening.py
PYTHONPATH=src:. .venv/bin/python -m tests.support.trendpulse_screening_replay \
  --output ../docs/contracts/trendpulse_screening_replay_report.v1.json
```

From `frontend/`:

```sh
npm run api:generate
npm run api:check
npm run test -- src/lib/api/generated-contracts.test.ts
npm run lint
npm run typecheck
```

The replay helper refuses a non-loopback fixture URL and creates/drops its own
schema. It never installs a replay clock/source in HTTP. Preserve original
recorded envelopes and decision time when testing a future genuine receipt archive.

## Integration and activation handoff

One additive a12 follows a11; migration coordination is published on PR237.
Inspected future integration head1ebf853 contains a11/no competing a12; explicit
integration-owner acknowledgment is not yet evidenced. Merge sequencing and
reservation reconciliation remain prerequisites. RC head3600ec89 is untouched.
The producer is disabled by default, and existing source settings must explicitly
permit Binance with no fallback. No setting was changed or exchange GET activated.

The [contract](trendpulse_screening_contract.md#authority-and-remaining-runtime-dependencies)
identifies remaining public-budget/network checks, automatic scanning/presentation,
compiler/execution bridge, fresh native UID/account/rules/quote proof, deterministic
account-wide risk reservations, approvals/kill switches, native lifecycle/outcomes
and trusted source/sample resolvers. Screening never creates a TradePlan, quantity,
order or ExperimentSample. Promotion does not turn research into a fresh Validation
sample. Trading and performance tracking remain absent.

No full backend CI, manual CI dispatch, deployment, orders, account/credential
mutations, operator changes or Telegram messages. Only automatic focused PR CI
will be inspected after the batched continuation push; exact-SHA results are
published in the PR. Local operational handoff mirror is verified separately;
Mac/iCloud synchronization remains unverified in the managed workspace.
