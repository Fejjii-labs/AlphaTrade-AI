# Workflow: RELEASE / DEPLOY validation

> Authoritative rules: `.ai/MASTER_WORKFLOW.md`. Only commit/push/deploy when the task explicitly
> authorizes it; otherwise stop at `REVIEW_REQUIRED` before the protected action.

## Development checks
```
# backend/
uv run ruff check .
uv run ruff format --check .
# Run tests relevant to the changed behavior (not the complete suite each iteration).
uv run pytest tests/<affected-test>.py
# frontend/
npm run lint && npm run typecheck && npm run test && npm run build
```
- Inspect `git status` and full diff; confirm every changed file is intentional.
- Secret scan the diff; confirm no secrets, keys, or private URLs.
- Confirm `render.yaml` preserves paper-safe values: `PROVIDER_MODE=fallback`,
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_internal`,
  Watcher/Telegram flags `false`.

Ordinary push and pull_request CI keeps six jobs and no job-level `if`:
`backend`, `frontend`, `deployment-safety`, `evaluation`, `docker-build`, and
`e2e-smoke`. `evaluation` needs `backend`. `e2e-smoke` needs `backend` and
`frontend`. The workflow file has no `permissions` block and no `schedule`.
A concurrency group cancels an older push or pull_request run on the same ref.
`workflow_dispatch` uses a unique group, so a manual full run is not queued or
cancelled. The only `workflow_dispatch` input is `full_backend`, and its default
is `true`. There is no `combined_validation` input.

Heavy steps in `backend`, `evaluation`, `docker-build`, and `e2e-smoke` do not
run on a draft pull request. They run for a non-draft `opened`, `synchronize`,
or `reopened` event, for `ready_for_review`, and when the pull request already
has the `run-ci` label on one of those events. The workflow does not subscribe
to `labeled`, so adding an unrelated label does not start CI or cancel a run.
`deployment-safety` still runs on drafts, and it runs `ci-scope.sh --self-test`.
`frontend` still runs for draft changes that are not docs-only. A rename is
classified with `git diff --name-only --no-renames`, so a move into `docs/` or
`.ai/` does not count as docs-only.

`docs/**`, `.ai/**`, and root-level markdown-only changes skip backend,
frontend, Docker, evaluation, and browser work. Those jobs still finish
successfully, so a required check is not left pending or skipped.
`workflow_dispatch` ignores that path filter and the draft filter. The
`full_backend` input still selects only the complete backend pytest step, and
only a manual dispatch can select it. Pull requests and pushes never run that
step. There is no cron.

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
`npm run test`, and `npm run build` when the change is not docs-only or
`.ai`-only. It does not run `scripts/run_frontend_focused.py`. The evaluation,
Docker build, and Playwright smoke jobs run for the same non-draft code events
as the heavy backend steps. They are not gated by a second manual flag.
No branch-protection settings are changed by this policy. A green required
check on a draft or a docs-only change means that heavy work was skipped, not
that the suite passed. Complete acceptance is one manual `full_backend=true`
dispatch on the integration batch SHA, not a nightly run.

## Final consolidated release acceptance

Pull requests get the quick focused checks only. The complete backend suite does
not run on pull request pushes, on pushes to `main`, or on a schedule.

When a batch of pull requests is ready, stack them on an integration branch
named like `integration/<date>`. Dispatch `full_backend` **once** on that
branch. Merge the batch to `main` in order only after that single run passes.
Do not dispatch the full suite after each fix or feature.

After supervising review, consolidated staging deployment and fresh SFP diagnostics/
evaluations, dispatch **one** complete CI run on the exact reviewed release ref:

```sh
gh workflow run ci.yml --ref <reviewed-release-ref> -f full_backend=true
```

The Actions UI exposes the same `full_backend` boolean, defaulting to true for
manual dispatch. Ordinary push and pull_request events always use focused mode.
A manual false value requests development checks only. Do not dispatch a full
run during routine implementation or call a focused green check the complete
release gate.

The full step runs one pytest process:

```sh
uv run pytest -o addopts= --timeout=300 --timeout-method=thread -rfE --durations=50 --junitxml=full-backend-junit.xml | tee full-backend-pytest.log
```

The shell is `bash -eo pipefail`. The step timeout is 140 minutes, inside the
150-minute backend job. JUnit and the pytest log are uploaded as the
`full-backend-pytest` artifact. The same run still executes Ruff, the frontend
lint/type/unit/build steps, deployment-safety, the Docker build, the
deterministic Agent/RAG/guardrail evaluation, and the Playwright browser smoke
job. Nothing in this workflow runs the full suite on a schedule. Record
the run URL, exact `head_sha`, full backend counts/skips and successful results for all
six jobs. Failed, skipped or cancelled evaluation/browser jobs do not establish
acceptance. Investigate skips and do not attribute counts from another commit.
If the reviewed commit changes, its earlier full result is not final acceptance.

No branch protection configuration, credentials, deployment or activation settings
change through this workflow. Demo stays disarmed and real trading stays disabled.
Live acceptance remains supervised; local/mock checks do not prove it.

## Deploy validation (staging, paper-only)
- `ENV_FILE=.env.staging ./scripts/check-env.sh`
- `BASE_URL=<api> ./scripts/post-deploy-smoke-gate.sh` (AT-005 mandatory gate; exit 0)
- Canonical path: `INCLUDE_CANONICAL=true BASE_URL=<api> ./scripts/post-deploy-smoke-gate.sh`
  or `BASE_URL=<api> ./scripts/canonical-staging-smoke.sh`
- On gate exit 1 → follow `docs/deploy_rollback_runbook.md` before further work
- `BASE_URL=<api> ./scripts/verify-safety.sh` (included in the gate; may run alone)
- `BACKEND_URL=<api> ./scripts/validate-exchange-demo-staging.sh`
- Confirm `/health` paper posture, `real_trading_enabled=false`, Watcher/Telegram false.
- Confirm providers remain mock until an operator manually configures keys.
- Never enable real trading during deploy or rollback.

## Handoff (mandatory end of task)
1. Regenerate `HANDOFF.md` + `CHANGELOG_SESSION.md` from the `.ai/` templates; set the correct
   status (`READY`, or `REVIEW_REQUIRED`/`BLOCKED`/`FAILED` as applicable — never `DRAFT`).
2. Recompute the normalized `Source File SHA256` (hash of each doc with its own
   `Source File SHA256:` line removed) and write it back.
3. Run `~/.local/bin/sync-alphatrade-ai-handoff.sh`.
4. Verify the iCloud destination matches source via SHA256 **and** `cmp`/`diff`; confirm exit
   status 0. Sync immediately on any blocker/review/failure — never wait for task end.
