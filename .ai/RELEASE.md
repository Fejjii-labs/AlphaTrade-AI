# Workflow: RELEASE / DEPLOY validation

> Authoritative rules: `.ai/MASTER_WORKFLOW.md`. Current task and standing grants persist
> within their scope: authorized routine commits, batched pushes and integration need no
> repeated approval. Deployment/main merge/activation retain separate authority and gates.
> Missing access pauses the affected operation while independent qualification continues.

## Development checks
```
# backend/
uv run ruff check .
uv run ruff format --check .
# Run tests relevant to the changed behavior (not the complete suite each iteration).
uv run pytest tests/<affected-test>.py
# frontend/
npm run lint && npm run typecheck
npx vitest run <affected-test-files>
# Full frontend unit/build/browser validation requires an explicit combined request.
```
- Inspect `git status` and full diff; confirm every changed file is intentional.
- Secret scan the diff; confirm no secrets, keys, or private URLs.
- Confirm `render.yaml` preserves paper-safe defaults: `PROVIDER_MODE=fallback`,
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_internal`,
  Watcher/Telegram flags `false`. Defaults are not an instruction to reset existing operator
  settings: preserve watcher, Telegram, kill switch, account and provider configuration.

Ordinary push/PR CI keeps the six existing job names and required development quality
checks. PR runs for the same PR cancel superseded runs; main/master pushes and manual
runs have separate concurrency groups and are not cancelled by PR updates. Read-only
workflow permissions and job timeouts bound routine execution.
Backend pytest runs `scripts/run_backend_focused.py`: a small config/deployment/
workflow baseline, changed backend test files, the exact `tests/test_<module>.py`
file when it exists, and explicit source mappings (safety modules, the
SFP/receipt/logging group, reviewer boundaries, and native-activity boundaries).
Filename prefixes such as `test_agent*.py` are not used.

Development CI partitions that exact selection into three whole-file batches, each on its
own PostgreSQL service. The required `backend` check keeps Ruff and requires all batches
to succeed. Existing diagnostics retain each selection, test identity/phase, log, JUnit and
exit receipt. Each batch has a 16-minute inner budget, 17-minute launcher budget and
18-minute step within the existing 20-minute job limit. Full release execution retains
its separate unfiltered command, deadlines and checkpoint uploads. Unchanged fixture
profiling remains available by explicit workflow dispatch and is skipped on ordinary PRs.

This is a development selection, not exhaustive dependency analysis. For other cross-module changes,
include their focused tests explicitly in the PR; the complete gate below remains
required. A successful focused run is **not complete backend acceptance**.
Native activity/schema/client changes include scoped provider, account and migration
regressions. `BLOFIN_ACTIVITY_TEST_POSTGRES_URL` uses the existing disposable CI
PostgreSQL service so the activity durability/migration cases actually execute.

The frontend keeps generated drift, lint and type checks, then runs
`scripts/run_frontend_focused.py`: contract/cache baselines, changed tests and
module/domain neighbors, including Agent/API/account boundaries. This heuristic is
not exhaustive dependency analysis; owners must record focused local checks for their
actual changes. Preserve backend, frontend and deployment-safety merge checks and
failure propagation. No branch-protection settings are changed by this policy.

Expensive combined validation is manual only: full frontend unit/build, deterministic
evaluations, Docker build and production-browser smoke. Their jobs/steps are skipped
on routine events, and those skips do **not** establish acceptance. Both manual
booleans default to false; `full_backend=true` also enables combined validation.
Only when explicitly requested, run development combined validation with:

```sh
gh workflow run ci.yml --ref <reviewed-ref> -f combined_validation=true -f full_backend=false
```

This still uses the focused backend selection and is not the full release gate.
Manual development selection compares the branch to its `origin/main` merge base,
so a final evidence-only commit cannot omit the accumulated feature changes.
Do not repeat green runs merely to show progress. Record the exact tested SHA,
selected tests, failures and skips; batch related fixes before pushing. Cancel older
active PR runs where authorized access permits. Never cancel the current release
acceptance run as a development cost measure.

## Final consolidated release acceptance

Freeze the exact consolidated candidate after supervising review, coordinated staging
deployment and fresh SFP diagnostics/evaluations. Only with the explicit full-acceptance
request and these prerequisites satisfied, dispatch **one** complete CI run on that ref:

```sh
gh workflow run ci.yml --ref <reviewed-release-ref> -f full_backend=true
```

The Actions UI exposes the same `full_backend` boolean, defaulting to false for
manual dispatch. Ordinary push/PR events always use development mode. A manual false
value without `combined_validation=true` requests development checks only. Do not dispatch a full run during routine
implementation or call a focused green check the complete release gate.

The full dispatch runs unfiltered `uv run pytest` with PostgreSQL and all existing
Ruff, frontend lint/type/unit/build, deployment-safety, Docker build, deterministic
Agent/RAG/guardrail evaluation and Playwright browser smoke checks. Record the run
URL, exact `head_sha`, full backend counts/skips and successful results for all
six jobs. Failed, skipped or cancelled evaluation/browser jobs do not establish
acceptance. Investigate skips and do not attribute counts from another commit.
If the reviewed commit changes, its earlier full result is not final acceptance.
If the run fails, diagnose and repair the specific failures with focused verification. Rerun
failed jobs where valid; broaden only for concrete new risk. Do not repeat already green
jobs merely for progress or attribute an earlier revision's acceptance to repaired code.

No branch protection configuration, credentials, deployment or activation settings
change through this workflow. Demo stays disarmed and real trading stays disabled.
Live acceptance remains supervised; local/mock checks do not prove it.

## Deploy validation (staging, paper-only)
- Perform one focused prerequisite pass for the actual API, worker, frontend and database
  targets. Reuse valid receipts for unchanged inputs; verify exact-commit deployment access,
  authenticated synthetic smoke access, migration order and exercised rollback compatibility.
  No merge, deployment or new infrastructure is implied by this document.
- Coordinate API/worker/frontend revisions and ingestion consumers; preserve immutable
  migration ancestry and existing operator settings. Record deployment receipts and exact
  installed revisions; a merge or hosting read check does not prove deployment.
- `ENV_FILE=.env.staging ./scripts/check-env.sh`
- `BASE_URL=<api> ./scripts/post-deploy-smoke-gate.sh` (AT-005 mandatory gate; exit 0)
- Canonical path: `INCLUDE_CANONICAL=true BASE_URL=<api> ./scripts/post-deploy-smoke-gate.sh`
  or `BASE_URL=<api> ./scripts/canonical-staging-smoke.sh`
- On gate exit 1 → follow `docs/deploy_rollback_runbook.md` before further work
- `BASE_URL=<api> ./scripts/verify-safety.sh` (included in the gate; may run alone)
- `BACKEND_URL=<api> ./scripts/validate-exchange-demo-staging.sh`
- Confirm `/health` paper posture and `real_trading_enabled=false`; verify watcher, Telegram,
  kill switch and account settings against their recorded existing values without changing them.
- Preserve configured providers and activation boundaries; do not activate a new paid model.
- Never enable real trading during deploy or rollback.

## Handoff (mandatory end of task)
1. Regenerate `HANDOFF.md` + `CHANGELOG_SESSION.md` from the `.ai/` templates; set the correct
   status (`READY`, or `REVIEW_REQUIRED`/`BLOCKED`/`FAILED` as applicable — never `DRAFT`).
2. Recompute the normalized `Source File SHA256` (hash of each doc with its own
   `Source File SHA256:` line removed) and write it back.
3. Record named accepted/total criteria, implemented/integrated/deployed/live-accepted states,
   exact candidate and installed revisions, focused/full evidence, unexplained skips and the
   exercised rollback reference. Keep the checkpoint compact and reuse existing templates.
4. Run `~/.local/bin/sync-alphatrade-ai-handoff.sh` when the Mac worktree is available and
   verify destination bytes via SHA256 and `cmp`/`diff`, exit 0. For cloud publication, batch
   authorized handoff pushes and verify the published source equality per the master workflow.
   Record unavailable Mac/iCloud access explicitly; do not claim downstream sync or require
   repeated extra pushes at every edit.
5. Bundle genuinely missing release access in one packet: blocked operation, exact service/
   target, executor, missing permission or identity reference, verified configuration destination,
   completed preparation and next executable action. Keep secrets out of the packet and
   continue independent qualification work. Do not substitute a generic access roadmap.
