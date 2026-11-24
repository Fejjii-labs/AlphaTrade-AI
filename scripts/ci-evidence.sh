#!/usr/bin/env bash
# Print the local full-gate evidence block for a PR body from the last
# `make ci-full` run (see docs/ci-local.md).
set -euo pipefail

# The pytest summary line ("== N passed, M skipped ... in Xs =="), without colour
# codes. Log lines from background threads can follow it, so never use the last line.
pytest_summary() {
  sed -E 's/\x1b\[[0-9;]*m//g' "$1" \
    | grep -E '^=+ .*[0-9]+ (passed|failed|errors?|skipped).* in [0-9.]+s.* =+$' \
    | tail -n 1 | sed -E 's/^=+ //; s/ =+$//' || true
}
if [[ "${1:-}" == --summary ]]; then
  if [[ $# -ne 2 || ! -r "$2" ]]; then
    printf 'Usage: %s --summary <readable pytest log>\n' "$0" >&2
    exit 2
  fi
  pytest_summary "$2"
  exit 0
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."
log=.ci-local/backend-full.log
if [[ ! -s "$log" || ! -s .ci-local/full-gate.passed ]]; then
  printf 'No passing full gate on record. Run make ci-full first.\n' >&2
  exit 1
fi
ran=$(cat .ci-local/full-gate.passed)
head=$(git rev-parse HEAD)
if [[ "$ran" != "$head" ]]; then
  printf 'The last full gate ran on %s, not on HEAD %s. Run make ci-full again.\n' "$ran" "$head" >&2
  exit 1
fi
summary=$(pytest_summary "$log")
if [[ -z "$summary" ]]; then
  printf 'No pytest summary line in %s.\n' "$log" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  printf 'The working tree has changes; the evidence certifies a clean HEAD.\n' >&2
  exit 1
fi
printf '## Local full gate\n'
printf -- '- Head SHA: `%s`\n' "$ran"
printf -- '- Command: `make ci-full` (`scripts/ci-local.sh --full`, PostgreSQL 16 `alphatrade_test`)\n'
printf -- '- Backend: %s\n' "$summary"
printf -- '- Also passed: backend ruff check and format; frontend lint, typecheck, unit tests, API drift check and build\n'
