# PR 249 — backend launch checkpoints

- Source PR: https://github.com/Fejjii/AlphaTrade-AI/pull/249
- Head SHA: `ba7ad23fafba283f2674959d7605a40d5868ea3e`
- Status: tested

## What it does

Bounded backend launch and release runners that keep small checkpoint files while a long pytest process is alive, then upload those artifacts from GitHub Actions if the job times out or is cancelled. `scripts/backend_checkpoints.py` snapshots a fixed allowlist (no environment variables, no workspace walk). `scripts/run_backend_launch.py` wraps a command with a wall-clock limit and provenance. `scripts/run_backend_release.py` is the release entrypoint. `.github/workflows/backend-launch-proof.yml` and `.github/actions/backend-release/` exercise a short synthetic timeout. `scripts/run_backend_focused.py` is this PR's test selector, including `--batch-index`, which current `main` does not have.

The tests under `backend/tests/test_backend_launch.py`, `test_backend_checkpoints.py`, `test_backend_checkpoint_io.py`, and `test_backend_release_diagnostics.py` cover the launcher, checkpoint IO, and diagnostics helper.

## Dependencies and assumptions

- These files are a snapshot of the PR head. They are not wired into the app and must not replace `scripts/run_backend_focused.py` or `.github/workflows/ci.yml` on `main`.
- The PR also edited `.github/workflows/ci.yml` and `backend/tests/test_backend_ci_scope.py`. Those copies were left out: current `main` CI has diverged, and copying the old workflow would mix a stale pipeline with this launcher.
- `package-lock.json` for the action was left out (generated lockfile). `npm ci` in the proof workflow needs that lockfile regenerated from `package.json` (`@actions/artifact` 6.3.1).
- The checkpoint publisher refuses to copy credentials; do not extend the allowlist with env files.
- Tests were part of the source PR. This archive pass did not re-execute them.

## Why keep it

Current `main` has a focused pytest runner, but not this timeout-safe checkpoint and artifact path. It is the reusable piece of PR 249. The voice, watcher, and experiment product code in that PR is already on `main`.
