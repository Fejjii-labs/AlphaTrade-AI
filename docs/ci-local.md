# Local CI

Run the development gate from the repository root on Intel macOS or Linux with
Git, Bash, uv, Python 3.12, and Node.js 24 with npm. Keep the local
`origin/main` ref current. No GitHub Actions run is needed for this gate.

```sh
git fetch origin main
make ci
# Equivalent on a machine without make:
bash scripts/ci-local.sh
```

The gate installs locked dependencies, checks backend Ruff lint and formatting,
and runs focused pytest targets. The selector compares the working tree
(including untracked source and tests) with the merge base of HEAD and
`origin/main`. The gate also runs frontend lint, typecheck, unit tests, API
contract drift detection, and a production build. Use a development checkout
without deployment environment settings.

## Optional pre-push hook

```sh
bash scripts/install-hooks.sh
```

The hook runs the same gate before pushing the checked-out commit. Commit or
stash tracked and untracked changes before pushing. The installer preserves
existing hook setups and tells you when to integrate the gate manually.
Disable the hook installed by this script with
`git config --local --unset core.hooksPath`.

## Full backend gate

Start a disposable PostgreSQL 16 server on loopback. The test suite can drop
and recreate its database. The values below are for this temporary container.

```sh
docker run --detach --rm --name alphatrade-ci-postgres \
  -p 127.0.0.1:5433:5432 \
  -e POSTGRES_USER=alphatrade -e POSTGRES_PASSWORD=alphatrade \
  -e POSTGRES_DB=alphatrade_test postgres:16-alpine

# Run this check before the suite. Retry it if Postgres is still starting.
docker exec alphatrade-ci-postgres pg_isready -U alphatrade -d alphatrade_test

ci_postgres_url=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:5433/alphatrade_test
export AT028_POSTGRES_URL="$ci_postgres_url"
export EXPERIMENT_TEST_POSTGRES_URL="$ci_postgres_url"
export BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$ci_postgres_url"
export PR237_ROLLBACK_POSTGRES_URL="$ci_postgres_url"
make ci-full
# Equivalent without make: bash scripts/ci-local.sh --full

docker stop alphatrade-ci-postgres
```

The full target replaces focused pytest with the full backend suite and also
runs the other local checks. The focused target skips PostgreSQL fixtures.

## Hosted CI

The workflow runs on PR activity and manual dispatch. Jobs use their
relevant paths so an unrelated PR can still receive the existing check
contexts. A push to a branch with an open PR can still trigger a PR synchronize run. Full backend runs only when selected on dispatch or requested by
a `Full-Backend: true` trailer on the PR head commit. Before requesting that
trailer, run the full gate locally. The only new check name is
`backend-full`.

Hosted PR and manual runs can consume included Actions minutes. A zero-dollar
Actions budget with usage stopped at the limit is an organization billing
setting. Verify it separately from the workflow changes.
[GitHub Actions billing controls](https://docs.github.com/en/billing/how-tos/set-up-budgets)

### Chromium smoke investigation

`e2e-smoke` is manual-only and non-blocking. Select `e2e_smoke` in the CI
dispatch form. On failure the job uploads available Playwright traces. The
November 22 run reproduced the following errors on retry. Most locator
assertions reached the 15-second assertion limit, and the missing recovery
control consumed the 90-second test limit.
[GitHub Actions run 45061062117](https://github.com/Fejjii-labs/AlphaTrade-AI/actions/runs/45061062117) and [run 45046645840](https://github.com/Fejjii-labs/AlphaTrade-AI/actions/runs/45046645840).

| Failing Chromium spec | Cases and observed cause |
| --- | --- |
| `e2e/blofin-repair.spec.ts:42` | 1280px and 390px. Both expect `Stored identities and history`, renamed to `Stored evidence and history`. |
| `e2e/dashboard-demo-account.spec.ts:7` | 1280px and 390px. The fixture supplies a fresh snapshot. The dashboard now reuses it until due, so the expected immediate refresh never occurs. |
| `e2e/deep-link-contracts.spec.ts:20` | Invalid Journal entry case. The test expects the former `journal-stale-entry` test ID. |
| `e2e/manual-demo-recovery.spec.ts:130` | Recovery after reload. Click waits for the same missing `Stored identities and history` label. |
| `e2e/native-activity.spec.ts:83,107,119` | Drill-down at 320px, actual Journal, and account change. The tests target the previous Journal surface. TradesScreen replaced it and renders BloFin sync and fills separately. |
| `e2e/notification-settings-v2.spec.ts:145` | 390px and 320px. The old watchlist-error text is absent. |
| `e2e/simplified-ui-smoke.spec.ts:27` | Desktop/mobile navigation and preview. The exact level-one `Autonomous Trading` heading is absent. |
| `e2e/smoke.spec.ts:11,136` | API happy path returned zero journal knowledge chunks. Public UI expected a missing `real trading disabled` text badge. |
| `e2e/workspace-redesign.spec.ts:376` | 1280px found five primary links and expected seven. At 390px the actual route was `/knowledge?tab=knowledge` and the spec expected `/agent?tab=documents`. |

The navigation counts, redirects, and missing copy show stale UI assertions.
The slim UI changed the Journal, navigation, alerts and autonomous pages.
Subsequent UI polish renamed the recovery summary, and the performance change
altered refresh scheduling. The knowledge search returned HTTP success with an
empty chunk list after the test saw a ready index. The logs do not establish
why the search returned no match. Chromium started successfully in both runs.
[Slim UI](https://github.com/Fejjii-labs/AlphaTrade-AI/commit/5391d0ba),
[UI polish](https://github.com/Fejjii-labs/AlphaTrade-AI/commit/b5b1b3b7),
[dashboard scheduling](https://github.com/Fejjii-labs/AlphaTrade-AI/commit/1ceaa816).
