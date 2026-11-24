#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

postgres_vars=(
  AT028_POSTGRES_URL
  EXPERIMENT_TEST_POSTGRES_URL
  BLOFIN_ACTIVITY_TEST_POSTGRES_URL
  PR237_ROLLBACK_POSTGRES_URL
)

# These fixture-specific overrides fall back to the URLs above when unset.
unset PHASE1_POSTGRES_URL KNOWLEDGE_POSTGRES_URL INDEXING_MIGRATION_POSTGRES_URL

case "$#:${1:-}" in
  0:)
    base=$(git merge-base origin/main HEAD)
    unset "${postgres_vars[@]}"
    export CI=false
    printf 'Running focused CI. Full backend PostgreSQL coverage requires make ci-full.\n'
    ;;
  1:--full)
    for name in "${postgres_vars[@]}"; do
      if [[ ! "${!name:-}" =~ ^postgresql\+psycopg://[^/@]+@(localhost|127\.0\.0\.1):[0-9]+/alphatrade_test$ ]]; then
        printf '%s must point to the disposable local alphatrade_test database. See docs/ci-local.md.\n' "$name" >&2
        exit 2
      fi
    done
    # test_pr237_migration_aware_rollback archives these historical trees; a
    # shallow clone must fetch them or the gate cannot pass honestly.
    for sha in a7a843f7c27e8bdcea5f89c802c808472477a969 \
      b165b92276346f0e0fe3ccdbd2bec3443dc75d40 bda597c2fffbf1a49beadc64d757100808094ca2; do
      if ! git cat-file -e "${sha}^{tree}" 2>/dev/null; then
        git fetch --quiet --depth=1 origin "$sha" || {
          printf 'Historical commit %s is missing and could not be fetched.\n' "$sha" >&2
          exit 2
        }
      fi
    done
    ;;
  *)
    printf 'Usage: %s [--full]\n' "$0" >&2
    exit 2
    ;;
esac

export UV_CACHE_DIR="${UV_CACHE_DIR:-$(uv cache dir)}"
(
  cd backend
  uv sync --locked --extra dev
  uv run --no-sync ruff check . ../scripts/run_backend_focused.py
  uv run --no-sync ruff format --check . ../scripts/run_backend_focused.py
  if [[ "${1:-}" == --full ]]; then
    # Two full runs on one database kill each other's connections (AdminShutdown).
    if [[ "${CI_LOCAL_ALLOW_SHARED_DB:-}" != 1 ]]; then
      others=$(uv run --no-sync python -c '
import os, psycopg
url = os.environ["AT028_POSTGRES_URL"].replace("+psycopg", "")
with psycopg.connect(url) as conn:
    print(conn.execute(
        "select count(*) from pg_stat_activity"
        " where datname = current_database() and pid <> pg_backend_pid()"
    ).fetchone()[0])
')
      if [[ "$others" != 0 ]]; then
        printf '%s other connection(s) to alphatrade_test. Run one full gate per database (CI_LOCAL_ALLOW_SHARED_DB=1 overrides).\n' "$others" >&2
        exit 2
      fi
    fi
    mkdir -p ../.ci-local
    rm -f ../.ci-local/full-gate.passed
    log=../.ci-local/backend-full.log
    git rev-parse HEAD > ../.ci-local/backend-full.head
    set +e
    uv run --no-sync pytest -o addopts= --timeout=300 --timeout-method=thread -rfE --durations=50 2>&1 | tee "$log"
    status=${PIPESTATUS[0]}
    set -e
    [[ "$status" == 0 ]] || exit "$status"
    # A missing PostgreSQL URL skips fixtures instead of failing them.
    skipped=$(tail -n 1 "$log" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' || echo 0)
    if (( skipped > ${CI_FULL_SKIP_BASELINE:-1} )); then
      printf '%s tests skipped; the baseline is %s. Check the PostgreSQL URLs.\n' "$skipped" "${CI_FULL_SKIP_BASELINE:-1}" >&2
      exit 1
    fi
  else
    uv run --no-sync python ../scripts/run_backend_focused.py --base "$base" --head "$(git rev-parse HEAD)" --worktree
  fi
)

(
  cd frontend
  npm ci
  npm run lint
  npm run typecheck
  npm run test
  npm run api:check
  npm run build
)

if [[ "${1:-}" == --full ]]; then
  # Written only after every step above passed; scripts/ci-evidence.sh needs it.
  cp .ci-local/backend-full.head .ci-local/full-gate.passed
fi
