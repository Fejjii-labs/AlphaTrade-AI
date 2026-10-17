# PR239 review-correction verification — AT-118

The subsequent activity-worker timing correction and PR237 integration handoff are
recorded in [the worker verification handoff](blofin_activity_worker_integration_handoff.md).
The results below are historical evidence for `18e66da`; they do not supersede the
integration failure reported against its original worker isolation test.

Continuation baseline: `85adbde2b242340cac29b45b8673eb8296385903`.
PR base remains `bda597c2fffbf1a49beadc64d757100808094ca2` on
`codex/reviewer-wave-integration`. Tests ran October 10, 2026 from the isolated
`/workspace/blofin-native-activity/backend` worktree.

## Disposable database and scope

PostgreSQL 17 runs in the disposable local `alphatrade-activity-pg` container,
`activity_test` database on localhost port 55437. `BLOFIN_ACTIVITY_TEST_POSTGRES_URL`
and `PHASE1_POSTGRES_URL` selected that fixture, with dummy fixture credentials.
New activity/snapshot tests create and drop unique schemas. The migration test starts
from its own empty schema; existing PostgreSQL regression fixtures may reset only this
throwaway database's public schema. No deployed database was accessed.

All exchange requests in tests use deterministic mock transports. Simulated manual
submissions exercise existing command/receipt paths; they do not contact BloFin.
Worker lifecycle/signal tests use fixtures, and Telegram delivery tests use fake transports.
No full backend suite or manual CI workflow was run.

## Correction selection

```sh
BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$ACTIVITY_FIXTURE_URL" .venv/bin/pytest -o addopts='' -q \
  tests/test_blofin_activity_provider.py tests/test_blofin_activity_postgres.py \
  tests/test_blofin_snapshot_identity.py tests/test_blofin_activity_worker.py \
  tests/test_blofin_activity_migration.py tests/test_watcher_paper_activation.py
```

**106 passed, no skips, 39.99 seconds.** Coverage includes signed UID identity,
identical client/order echoes in two UIDs of one organization, a proven positive link,
missing original account proof, incompatible account/order/UID receipts and corrupt
hashes; snapshot rotation, switching UIDs, unverified replacement keys, legacy rows,
identity failure and historical retention; paginated later completion of an order
created 90 days earlier; page atomicity/replay, interruption/deadline, rate-limit backoff,
advisory-lock contention, API tenant/cursor isolation, worker defaults/wiring/thread
isolation/retry caps/shutdown, real migration roundtrip and activation migration guards.

A final worker isolation correction moved missing-pin validation into its supervised
cycle, before database access. The resulting additional case and the strengthened
migration ancestry assertions passed in this final targeted check:

```sh
BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$ACTIVITY_FIXTURE_URL" .venv/bin/pytest -o addopts='' -q \
  tests/test_blofin_activity_worker.py tests/test_blofin_activity_migration.py
```

**7 passed, no skips, 6.95 seconds.** Six worker cases and one migration roundtrip;
six overlap the 106-case selection, so the correction selection has **107 distinct
passing cases**, not a single 107-case command.

After requiring revision/command plan-hash equality, the eight linkage scenarios were
rechecked: **8 passed, 19 deselected, 26.32 seconds** with
`tests/test_blofin_activity_postgres.py -k verified_alphatrade_echo` (the migration path
was also supplied but deselected by that filter; its final seven-case run above executes it).

## Existing focused regressions

```sh
PHASE1_POSTGRES_URL="$ACTIVITY_FIXTURE_URL" .venv/bin/pytest -o addopts='' -q \
  tests/test_dashboard_demo_account.py tests/test_external_integrations_acceptance.py \
  tests/test_paper_worker_supervisor.py tests/test_manual_demo_native_pagination.py \
  tests/test_blofin_presentation_evidence.py tests/test_blofin_provider.py \
  tests/test_blofin_execution.py tests/test_manual_blofin_demo.py
```

**263 passed, no skips, 291.00 seconds.** This verifies existing account reads,
credential isolation, provider/execution behavior, manual native pagination and
presentation evidence, worker failure isolation, single-thread starts, signal shutdown
and real PostgreSQL lease behavior.

The final original-account audit and hidden-provenance changes were additionally checked:

```sh
PHASE1_POSTGRES_URL="$ACTIVITY_FIXTURE_URL" .venv/bin/pytest -o addopts='' -q \
  tests/test_manual_blofin_demo.py tests/test_dashboard_demo_account.py \
  tests/test_at037_tradingview_blofin.py::test_blofin_sync_read_only_contract
```

**107 passed, no skips, 182.43 seconds.** This is a targeted recheck, with one additional
legacy snapshot contract case; it does not add another 107 distinct cases. Together
there are **264 distinct existing regression cases** and **371 distinct passing focused
cases** across all selections. Earlier development runs and the original PR's 241-case
ledger are not counted again.

## Static, migration and contract evidence

Changed-file Ruff check and format check: **21 Python files, exit 0**.
Focused mypy with `--follow-imports=silent`: **13 changed source files, no issues**.
`git diff --check`: exit 0. Default `python -m app.workers.blofin_activity` reports
synchronization disabled and does no sync IO.

`alembic heads` returns only `a10blofinactivity001`. Script inspection confirms one
base, 78 reachable revisions, no revision dependencies, and exactly
`a9knowledgeoutbox001 -> a10blofinactivity001` as the current continuation.
The pre-existing historical merge `a3release002` joins `a2tgpolicy002` and `a2sfp002`;
no historical migration was edited or new branch introduced. A diagnostic assertion
that every historical revision had a single parent failed on that existing merge;
checking the actual graph and the current continuation established the correct guard.
The two activation tests now expect a10 while preserving missing/multiple-head refusal
and working-directory-independent discovery assertions.

The scoped OpenAPI contract was regenerated from the backend router and checked against
its stored bytes. SHA256:
`14b14a34c77f598793207a1fa2485e5572b2afd8eee92f86bc2c1e2f5cb76307`.
It exposes `selection=cursor_sweep` for orders and `time_window` for fills, with null
order time-coverage bounds. Frontend/generated-client and CI files remain untouched.
Development lint/type issues were corrected before the passing final checks.

## Remaining coverage and activation prerequisites

Official order-history documentation does not specify the begin/end filter timestamp
basis or venue retention. Recurring unfiltered order cursor sweeps capture later
completion if the record remains available through a subsequent sweep. Discovery
latency, collection churn, outages and retention still limit coverage. A scan guard
stops at 1,000 distinct nonempty frontiers; conflicts/guard failures require review.
Fills retain a 1–30 day configured lookback (default seven), overlap and explicit outage
gaps. Pending orders, separate TPSL/algo ancestry, spot/copy-trading and funding remain
outside coverage; unavailable fee currency/PnL and historical conversion remain unknown.
`partial_coverage` is always true. Historical commands without verified original
execution UID evidence stay native without a command link.

Before activation: integration-owner client regeneration and Dashboard/Journal counting
integration; reviewed a10 schema upgrade and migration-aware rollback image; correct
organization and UID pins; separately authorized dedicated demo read-only credentials;
safe paper posture, aggregate UID/IP rate budget and running existing paper worker;
then explicit activity opt-in and supervised read-only acceptance. Existing balance
snapshot rotation requires verification of its own current credential source.

No deployment, exchange activation, order, account mutation, Telegram message,
credential change, runtime flag change, full backend suite, manual CI dispatch or merge.
Live venue acceptance and the separate manual-order incident remain unverified.
