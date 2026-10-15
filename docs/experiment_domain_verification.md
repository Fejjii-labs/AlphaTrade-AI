# Experiment domain verification and adoption handoff

Draft PR241: https://github.com/Fejjii/AlphaTrade-AI/pull/241.
The exact final product SHA is recorded in the PR description. The first interface
was published at `401b4eb576319d9dd8dde4801f9f7f500a8b28f2`, before implementation.
Refreshed PR237/frozen feature base: `619c15fe63ffc23288c2866781e08f9cb44a2ab9`.
Integrated activity revision `769e78a46fbce888810060f8d09cbe4d93f81c31` remains an
ancestor. Head branch `codex/generic-experiment-domain` targets the separate frozen
`codex/experiment-domain-base`, not the current release candidate or main.

## Contract and evidence

- [Domain/API contract](experiment_domain_contract.md).
- [Scoped OpenAPI](contracts/experiments_v1.openapi.json), SHA256
  `24eb9f02f504dd3d0f728a3e3a62033a0d389990caa97f4192df846898ba81f7`.
- One generated additive migration: `a11experiments001` after `a10blofinactivity001`.
  Four new tables; no historical migration, existing fact or checkpoint edits.
  Current-head expectations advance only on this feature branch; historical
  ancestry, rollback/data-preservation and missing/multiple-head refusal remain.
- **71 new tests passed in 11.63s**: lifecycle/revisions, bounded Owner approval,
  immutable configuration and append-only facts at ORM/PostgreSQL boundaries,
  tenant/account/UID/source/strategy/variant isolation, verified credential rotation,
  API roles/schema, sample interval/cap/reuse rules, BASE/CONTRACTS floor rounding,
  manual exposure and loss limits, fresh promotion and concurrent convergence.
- **102 preservation tests passed in 27.58s**: integrated activity worker isolation
  and shutdown, snapshot identity, pagination/provider coverage, supervisor,
  watcher activation refusal and migration guard/roundtrip checks.
- Final combined selection: **173 passed in 64.09s; zero failures or skips**.
- Changed source/tests Ruff and format checks pass. Scoped strict mypy with
  `--follow-imports=silent` passes for all ten new source files; this does not claim
  repository-wide type cleanliness. Scoped OpenAPI equality and `git diff --check`
  pass. The integration correction below also regenerates shared API artifacts
  and supplies the existing disposable CI PostgreSQL service to these fixtures.

## PR241 integration correction from bed8994

The original automatic focused CI at `bed8994634eee1fffab578c57edb7126db5fab2f`
([run 38063897311](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38063897311))
reported one failure, 179 passes and 68 skips. The manual-demo roundtrip upgraded
to a11 but still asserted a10. Shared API drift failed for `openapi.json` and
`hashes.json`; experiment PostgreSQL fixtures had no CI URL.

One correction batch advances the roundtrip expectation, explicitly preserves
a11 → a10 → a9 ancestry and every historical data assertion, regenerates the
shared artifacts, and sets `EXPERIMENT_TEST_POSTGRES_URL` to the existing
PostgreSQL 16 CI service. The focused selector, skip reporting, full-suite gate
and disposable service are unchanged. No new service or migration is added.

Local reproduction of the old roundtrip: one failure in 6.74s. Corrected affected
selection: **75 passed in 27.37s, zero skips**, using a unique loopback PostgreSQL
16 fixture. Ruff and formatting pass; scoped mypy passes all ten domain source
files. Shared `npm run api:check` passes with schema SHA256
`6f7f9ece7248442b53f9e3ebd84277053db284d1e52cf97a18a25ac5c4dcf0e3`.
Frontend lint, typing, generated contract tests and focused CI policy evidence
are recorded with the exact correction SHA in the PR description.

Reproduction from `backend/`, after installing the locked development environment:

```sh
export EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:55439/alphatrade_test
export PHASE1_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
.venv/bin/pytest -o addopts='' -q tests/test_manual_demo_migration.py \
  tests/test_experiment_domain.py tests/test_experiment_risk.py \
  tests/test_experiment_api.py tests/test_experiment_native_identity.py \
  tests/test_experiment_concurrency.py tests/test_experiment_migration.py --tb=short
.venv/bin/pytest -o addopts='' -q tests/test_backend_ci_scope.py
.venv/bin/ruff check src/app/experiments src/app/schemas/experiments.py \
  src/app/api/routes/experiments.py tests/test_manual_demo_migration.py
.venv/bin/ruff format --check src/app/experiments src/app/schemas/experiments.py \
  src/app/api/routes/experiments.py tests/test_manual_demo_migration.py
.venv/bin/mypy --follow-imports=silent src/app/experiments \
  src/app/schemas/experiments.py src/app/api/routes/experiments.py
```

From `frontend/`: `npm run api:generate`, `npm run api:check`, `npm run lint`,
`npm run typecheck`, and
`npx vitest run src/lib/api/generated-contracts.test.ts`.

## Exact focused reproduction

Run from `backend/`. The tools used here were installed at
`/workspace/blofin-native-activity/backend/.venv/bin`; another checkout can use its
equivalent environment. All three URLs below selected the same unique disposable
loopback PostgreSQL database. Never point these test fixtures at a shared database.
New experiment fixtures create/drop only their UUID-named schemas; existing
supervisor fixtures also reset the disposable public schema.

```sh
TEST_TOOLS=/workspace/blofin-native-activity/backend/.venv/bin
export EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://postgres:experiment-fixture@127.0.0.1:55439/experiment_test
export BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
export PHASE1_POSTGRES_URL="$EXPERIMENT_TEST_POSTGRES_URL"
"$TEST_TOOLS/pytest" -o addopts='' -q \
  tests/test_experiment_domain.py tests/test_experiment_risk.py \
  tests/test_experiment_api.py tests/test_experiment_native_identity.py \
  tests/test_experiment_concurrency.py tests/test_experiment_migration.py \
  tests/test_blofin_activity_worker.py tests/test_blofin_snapshot_identity.py \
  tests/test_blofin_activity_provider.py tests/test_paper_worker_supervisor.py \
  tests/test_watcher_paper_activation.py tests/test_blofin_activity_migration.py \
  tests/test_knowledge_file_migration.py::test_knowledge_metadata_revision_precedes_the_single_migration_head \
  tests/test_manual_demo_migration.py::test_single_head_extends_verified_demo_lifecycle \
  tests/test_phase2_4_alembic_postgres.py::test_alembic_single_head \
  tests/test_phase8_learning_persistence.py::test_alembic_single_head_includes_phase8 \
  --tb=short
"$TEST_TOOLS/mypy" --follow-imports=silent src/app/experiments \
  src/app/schemas/experiments.py src/app/api/routes/experiments.py
```

The URLs contain disposable fixture values, not real credentials. No native
exchange calls occur: identity positives use `httpx.MockTransport`; sample positives
use an explicitly trusted fixture resolver. These tests prove domain fences, not
native venue performance or completed-trade reconciliation.

## Adoption prerequisites and limits

Keep this draft outside PR237's current release candidate. Integration must reserve
a11 after a10, or regenerate/resequence this one revision against any intervening
approved migration, preserving one head and historical data. Review the published
OpenAPI and regenerated shared artifacts before adding Dashboard/Journal presentation.
Provide `EXPERIMENT_TEST_POSTGRES_URL` for the experiment fixture selection; skips
without a disposable database do not establish PostgreSQL acceptance.

The HTTP sample endpoint intentionally returns 503 with no trusted source adapter.
Performance is null. No runtime, scheduler, execution engine, demo replenishment or
model caller is installed. No deployable native closed-trade attribution or simulator
record resolver is claimed. Adapters must verify immutable source records, exact
version/hash/account/variant/sample lineage and experiment management ownership.
Manual orders can establish account identity but cannot become experiment samples.
Do not combine internal simulation with BloFin performance.

Approval seals a finite domain envelope and expires within 30 days. It is not a
canonical venue dispatch authorization. Future execution must verify current native
execution UID after reconnect/rotation, reserve account-wide positions/notional and
trade/loss budgets atomically, include all manual native exposure, and preserve
existing canonical strategy, sizing, precision, safety and final dispatch gates.
Readers and mutations remain scoped to the authenticated organization/user; this
batch adds no cross-user approval delegation.

Nested semantics/targets are preserved. SFP is structural observation-only until an
authorized execution adapter exists. The separate future
TrendPulse1R branch publishes a deterministic research adapter and exact two-timeframe
authored spec recognition. Its [contract](trendpulse_1r_adapter_contract.md) still
requires compiler/plan/runtime integration; no execution or performance is claimed.

No full backend CI, manual CI dispatch, deployment, external orders, credential
changes, account mutations, Telegram messages or runtime activation were performed.
