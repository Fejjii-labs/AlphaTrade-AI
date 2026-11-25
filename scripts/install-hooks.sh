#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

current=$(git config --get core.hooksPath || true)
default_hooks=$(git rev-parse --git-path hooks)
if [[ -n "$current" && "$current" != .githooks ]]; then
  printf 'A custom hooks path already exists. Integrate scripts/ci-local.sh into it.\n' >&2
  exit 1
fi
if [[ -z "$current" ]]; then
  for hook in "$default_hooks"/*; do
    case "$hook" in
      *.sample) ;;
      *)
        if [[ -f "$hook" && -x "$hook" ]]; then
          printf 'An active Git hook already exists. Integrate scripts/ci-local.sh into your hooks.\n' >&2
          exit 1
        fi
        ;;
    esac
  done
fi
git config --local core.hooksPath .githooks
printf 'Enabled local CI before pushes.\n'
