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
```
- Inspect `git status` and full diff; confirm every changed file is intentional.
- Secret scan the diff; confirm no secrets, keys, or private URLs.
- Confirm `render.yaml` preserves paper-safe defaults: `PROVIDER_MODE=fallback`,
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_internal`,
  Watcher/Telegram flags `false`. Defaults are not an instruction to reset existing operator
  settings: preserve watcher, Telegram, kill switch, account and provider configuration.

Ordinary push and pull_request CI keeps six jobs and no job-level `if`:
`backend`, `frontend`, `deployment-safety`, `evaluation`, `docker-build`, and
`e2e-smoke`. `evaluation` needs `backend`. `e2e-smoke` needs `backend` and
`frontend`. The workflow file has no concurrency group and no `permissions`
block. The only `workflow_dispatch` input is `full_backend`, and its default is
`true`. There is no `combined_validation` input.

The backend job uses one PostgreSQL 16 service (`alphatrade_test`) and a
150-minute job timeout. On an ordinary push or pull request, and on a manual
dispatch with `full_backend` false, it runs `scripts/run_backend_focused.py` as
one pytest process. That selector always includes `tests/test_config.py`,
`tests/test_deployment_safety.py`, and `tests/test_backend_ci_scope.py`. It also
includes a changed `backend/tests/test_*.py` file, every
`tests/test_<module stem>*.py` match when a `backend/src/app/**/*.py` file
changes (that is a filename-prefix glob), and the SFP/receipt/logging group
when a changed path is in the script's SFP path list. The diff is
`git diff --name-only` from the pull request base SHA, or `github.event.before`
on a push. This is a development selection, not exhaustive dependency analysis.
A successful focused run is **not complete backend acceptance**. Include focused
tests for other cross-module changes in the PR. The CI database URL is bound to
`AT028_POSTGRES_URL`, `EXPERIMENT_TEST_POSTGRES_URL`,
`BLOFIN_ACTIVITY_TEST_POSTGRES_URL`, and `PR237_ROLLBACK_POSTGRES_URL` so those
disposable-database fixtures execute. `PHASE1_POSTGRES_URL` is left unset.

The frontend job runs `npm ci`, `npm run lint`, `npm run typecheck`,
`npm run test`, and `npm run build` on ordinary events. It does not run
`scripts/run_frontend_focused.py`. The evaluation, Docker build, and Playwright
smoke jobs also run on ordinary events; they are not skipped behind a second
manual flag. No branch-protection settings are changed by this policy.

Do not repeat green runs merely to show progress. Record the exact tested SHA,
selected tests, failures, and skips. Batch related fixes before pushing.

## Final consolidated release acceptance

Freeze the exact consolidated candidate after supervising review, coordinated staging
deployment and fresh SFP diagnostics/evaluations. The complete backend suite runs
when the workflow is scheduled, or when it is dispatched with `full_backend=true`:

```sh
gh workflow run ci.yml --ref <reviewed-release-ref> -f full_backend=true
```

The Actions UI exposes that same boolean, and its default for manual dispatch is
`true`. Ordinary push and pull_request events use the focused backend step above.
A manual dispatch with `full_backend` false also uses that focused step. Do not
dispatch a full run during routine implementation, and do not call a focused
green check the complete release gate.

The full step runs one pytest process:

```sh
uv run pytest -o addopts= --timeout=300 --timeout-method=thread -rfE --durations=50 --junitxml=full-backend-junit.xml | tee full-backend-pytest.log
```

The shell is `bash -eo pipefail`. The step timeout is 140 minutes, inside the
150-minute backend job. JUnit and the pytest log are uploaded as the
`full-backend-pytest` artifact. The same run still executes Ruff, the frontend
lint/type/unit/build steps, deployment-safety, the Docker build, the
deterministic Agent/RAG/guardrail evaluation, and the Playwright browser smoke
job. The schedule is 02:00 UTC and runs from the default branch only. Record
the run URL, exact `head_sha`, full backend counts and skips, and successful
results for all six jobs. Failed, skipped, or cancelled evaluation or browser
jobs do not establish acceptance. Investigate skips and do not attribute counts
from another commit.
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
