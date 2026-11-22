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
    uv run --no-sync pytest -o addopts= --timeout=300 --timeout-method=thread -rfE --durations=50
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
