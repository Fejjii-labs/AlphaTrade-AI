#!/usr/bin/env bash
# Decide whether ordinary CI should run app jobs and heavy jobs.
# Fail open: uncertainty runs the work. Explicit false is the only skip signal.
# Outputs app, heavy, and reason on GITHUB_OUTPUT when that file is set.

set -u

app=true
heavy=true
reason=fail-open

emit() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      printf 'app=%s\n' "$app"
      printf 'heavy=%s\n' "$heavy"
      printf 'reason=%s\n' "$reason"
    } >>"$GITHUB_OUTPUT"
  fi
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf -- '- CI scope: app=%s heavy=%s (%s)\n' "$app" "$heavy" "$reason" >>"$GITHUB_STEP_SUMMARY"
  fi
  printf 'ci-scope app=%s heavy=%s reason=%s\n' "$app" "$heavy" "$reason"
}

on_err() {
  app=true
  heavy=true
  reason=fail-open
  emit
  exit 0
}

has_run_ci_label() {
  case ",${CI_SCOPE_PR_LABELS:-}," in
    *,run-ci,*) return 0 ;;
  esac
  [[ "${CI_SCOPE_PR_ACTION:-}" == "labeled" && "${CI_SCOPE_PR_LABEL:-}" == "run-ci" ]]
}

# Return 0 when every changed path is docs, .ai, or root-level markdown.
docs_only_diff() {
  local base="$1"
  local head="$2"
  local files=""
  local path=""

  if [[ -z "$base" || -z "$head" || "$base" =~ ^0+$ ]]; then
    return 1
  fi
  if ! git cat-file -e "${base}^{commit}" >/dev/null 2>&1; then
    git fetch --no-tags origin "$base" >/dev/null 2>&1 || true
  fi
  if ! git cat-file -e "${base}^{commit}" >/dev/null 2>&1; then
    return 1
  fi
  if ! git cat-file -e "${head}^{commit}" >/dev/null 2>&1; then
    return 1
  fi
  # --no-renames keeps the old path, so a rename into docs/ or .ai/ is not docs-only.
  files="$(git diff --name-only --no-renames "$base" "$head")" || return 1
  if [[ -z "$files" ]]; then
    return 1
  fi
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    case "$path" in
      docs/* | .ai/*) ;;
      *.md)
        [[ "$path" == */* ]] && return 1
        ;;
      *) return 1 ;;
    esac
  done <<<"$files"
  return 0
}

decide() {
  local event="${CI_SCOPE_EVENT_NAME:-}"
  local action="${CI_SCOPE_PR_ACTION:-}"
  local draft="${CI_SCOPE_PR_DRAFT:-}"

  app=true
  heavy=true
  reason=run

  # Manual dispatch only. No schedule runs the full suite.
  if [[ "$event" == "workflow_dispatch" ]]; then
    reason=workflow-dispatch
    return 0
  fi

  if [[ "$event" == "pull_request" ]]; then
    case "$action" in
      opened | synchronize | reopened | ready_for_review) ;;
      *)
        # The workflow does not subscribe to labeled. An unexpected activity
        # must not skip work: a green skip would replace a real result.
        app=true
        heavy=true
        reason=unexpected-activity
        return 0
        ;;
    esac
  elif [[ "$event" != "push" ]]; then
    reason=unknown-event
    return 0
  fi

  if docs_only_diff "${CI_SCOPE_BASE_SHA:-}" "${CI_SCOPE_HEAD_SHA:-}"; then
    app=false
    heavy=false
    reason=docs-or-ai-only
    return 0
  fi

  if [[ "$event" == "pull_request" && "$draft" == "true" && "$action" != "ready_for_review" ]]; then
    if has_run_ci_label; then
      heavy=true
      reason=draft-run-ci-label
    else
      heavy=false
      reason=draft
    fi
    return 0
  fi

  reason=code-change
  return 0
}

self_test() {
  local tmp root fail=0 script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  root="$(cd "$script_dir/../.." && pwd)"
  tmp="$(mktemp -d)"
  cd "$tmp"
  git init -q
  git config user.email "ci-scope@example.com"
  git config user.name "ci-scope"
  mkdir -p docs .ai backend
  printf 'docs\n' >docs/readme.md
  printf 'ai\n' >.ai/NOTE.md
  printf 'code\n' >backend/app.py
  printf 'root\n' >README.md
  git add docs .ai backend README.md
  git commit -q -m base
  local base head
  base="$(git rev-parse HEAD)"
  printf 'docs2\n' >docs/more.md
  git add docs/more.md
  git commit -q -m docs
  head="$(git rev-parse HEAD)"

  run_case() {
    local name="$1" expect_app="$2" expect_heavy="$3"
    shift 3
    local out
    out="$(env "$@" CI_SCOPE_BASE_SHA="$base" CI_SCOPE_HEAD_SHA="$head" bash "$root/.github/workflows/ci-scope.sh")"
    if [[ "$out" != *"app=${expect_app} heavy=${expect_heavy}"* ]]; then
      printf 'FAIL %s\n%s\n' "$name" "$out" >&2
      fail=1
    else
      printf 'ok %s\n' "$name"
    fi
  }

  run_case docs-pr false false \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=false
  run_case docs-push false false \
    CI_SCOPE_EVENT_NAME=push CI_SCOPE_PR_ACTION= CI_SCOPE_PR_DRAFT=
  run_case dispatch-keeps-full true true \
    CI_SCOPE_EVENT_NAME=workflow_dispatch CI_SCOPE_PR_ACTION= CI_SCOPE_PR_DRAFT=

  printf 'code\n' >>backend/app.py
  git add backend/app.py
  git commit -q -m code
  head="$(git rev-parse HEAD)"

  run_case draft-code true false \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=true
  run_case ready-code true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=ready_for_review CI_SCOPE_PR_DRAFT=false
  run_case nondraft-sync true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=false
  run_case draft-run-ci true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=true \
    CI_SCOPE_PR_LABELS=run-ci
  run_case labeled-run-ci-does-not-skip true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=labeled CI_SCOPE_PR_DRAFT=true \
    CI_SCOPE_PR_LABEL=run-ci CI_SCOPE_PR_LABELS=run-ci
  run_case labeled-other-does-not-skip true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=labeled CI_SCOPE_PR_DRAFT=false \
    CI_SCOPE_PR_LABEL=docs CI_SCOPE_PR_LABELS=docs
  run_case push-code true true \
    CI_SCOPE_EVENT_NAME=push

  printf 'only\n' >README.md
  git add README.md
  git commit -q -m readme
  base="$(git rev-parse HEAD^)"
  head="$(git rev-parse HEAD)"
  run_case root-markdown false false \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=false

  git mv backend/app.py docs/from-backend.py
  git commit -q -m 'rename into docs'
  base="$(git rev-parse HEAD^)"
  head="$(git rev-parse HEAD)"
  run_case rename-into-docs true true \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=false

  git mv docs/readme.md docs/guide.md
  git commit -q -m 'rename within docs'
  base="$(git rev-parse HEAD^)"
  head="$(git rev-parse HEAD)"
  run_case rename-within-docs false false \
    CI_SCOPE_EVENT_NAME=pull_request CI_SCOPE_PR_ACTION=synchronize CI_SCOPE_PR_DRAFT=false

  cd "$root"
  rm -rf "$tmp"
  if [[ "$fail" -ne 0 ]]; then
    printf 'ci-scope self-test failed\n' >&2
    exit 1
  fi
  printf 'ci-scope self-test passed\n'
}

if [[ "${1:-}" == "--self-test" ]]; then
  self_test
  exit 0
fi

trap on_err ERR
set -e
decide
trap - ERR
emit
