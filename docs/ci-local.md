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

The full suite is about 8,700 tests and takes about 1 h 55 min single-process
on Linux. It needs a disposable PostgreSQL 16 server on loopback with a
superuser role `alphatrade`/`alphatrade` (the suite drops and recreates
schemas) and a database named `alphatrade_test`. Some tests assert that exact
name, so the script refuses any other database.

With Docker:

```sh
docker run --detach --rm --name alphatrade-ci-postgres \
  -p 127.0.0.1:5433:5432 \
  -e POSTGRES_USER=alphatrade -e POSTGRES_PASSWORD=alphatrade \
  -e POSTGRES_DB=alphatrade_test postgres:16-alpine

# Run this check before the suite. Retry it if Postgres is still starting.
docker exec alphatrade-ci-postgres pg_isready -U alphatrade -d alphatrade_test
```

With a local PostgreSQL 16 instead (Linux package or Homebrew), as a superuser:

```sh
psql -d postgres -c "CREATE ROLE alphatrade LOGIN SUPERUSER PASSWORD 'alphatrade'"
psql -d postgres -c "CREATE DATABASE alphatrade_test OWNER alphatrade ENCODING 'UTF8' TEMPLATE template0"
```

The database must be UTF-8. A cluster created with the `C`/`POSIX` locale
defaults to `SQL_ASCII`, and psycopg then cannot connect (`TypeError: cannot
use a string pattern on a bytes-like object`).

Then point every fixture URL at it (use port 5432 for a local server) and run
the gate:

```sh
ci_postgres_url=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:5433/alphatrade_test
export AT028_POSTGRES_URL="$ci_postgres_url"
export EXPERIMENT_TEST_POSTGRES_URL="$ci_postgres_url"
export BLOFIN_ACTIVITY_TEST_POSTGRES_URL="$ci_postgres_url"
export PR237_ROLLBACK_POSTGRES_URL="$ci_postgres_url"
make ci-full
# Equivalent without make: bash scripts/ci-local.sh --full
```

The full target replaces focused pytest with the full backend suite and also
runs the other local checks. The focused target skips PostgreSQL fixtures.

Guards in `--full`:

- **Clean HEAD.** The evidence certifies HEAD, so the gate refuses to start with
  uncommitted or untracked changes and writes no evidence if HEAD or the tree
  changes while it runs.
- **Fixture URLs.** A PostgreSQL fixture whose URL is unset skips instead of
  failing, so a forgotten variable would look green. The gate fails when more
  tests are skipped than `CI_FULL_SKIP_BASELINE` (default 1, the count measured on
  this branch on 24 Nov). The log lists skipped tests (`-rs`), so a swapped
  skip can be spotted.
- **One run per database.** Each test's fixture truncates tables and ends other
  sessions on the database. A second run on the same `alphatrade_test` fails
  with `psycopg.errors.AdminShutdown` setup errors. The gate refuses to start
  while another session is connected (`CI_LOCAL_ALLOW_SHARED_DB=1` overrides).
  To run two gates at once, give each its own PostgreSQL server.
- **Shallow clones.** `tests/test_pr237_migration_aware_rollback.py` archives
  three historical commits. The gate fetches any that are missing and stops if
  it cannot.

Dependencies come from `uv sync --locked --extra dev`, the only extra the
backend defines. If your network needs the system certificate store, set
`UV_SYSTEM_CERTS=1` (`UV_NATIVE_TLS` is deprecated).

### Evidence in the PR body

The pytest output is saved to `.ci-local/backend-full.log`. A marker is written
only when every step passed. `make ci-evidence` prints the block to paste into
the PR body: head SHA, command and the pass/fail/skip summary. It refuses when
the last passing run was on another commit.

The `Full-Backend: true` commit trailer records that this local full gate
passed on that commit. It does not start any hosted job.

## Hosted CI

The workflow runs on PR activity and manual dispatch. Jobs use their
relevant paths so an unrelated PR can still receive the existing check
contexts. A push to a branch with an open PR can still trigger a PR synchronize
run. The hosted full backend job (`backend-full`, the only new check name) runs
only when `full_backend` is selected on a manual dispatch. A commit trailer
never starts it, so recording local evidence spends no Actions minutes.

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
