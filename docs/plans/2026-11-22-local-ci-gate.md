# Local CI gate

1. Add root Make targets for backend Ruff, focused pytest, frontend checks, and API contract drift. Use the local origin/main merge base for focused selection and account for working-tree changes in direct local runs. Keep dependency setup reproducible with the existing lockfiles. Use POSIX-compatible shell commands and verify on Linux and Intel macOS where available.
2. Add the full backend target with the existing pytest release options. Document how to start a disposable localhost PostgreSQL 16 container and configure all four test fixture URLs. Keep full PostgreSQL validation outside this session under the user's DB restriction.
3. Add an opt-in pre-push hook. Preserve existing hook configuration unless the operator explicitly changes it. Make sure the hook validates the revision being pushed or fails with a clear instruction when it cannot do so.
4. Keep the CI workflow and existing required check names. Remove push triggers, retain PR and manual triggers, and set workflow/ref concurrency cancellation. Scope expensive steps using path checks so required jobs can still report successful status on unrelated PRs. Keep deployment-safety coverage. Split off full backend into a job with PostgreSQL and a 120-minute limit, gated by manual input only (owner decision, 24 Nov: the Full-Backend trailer records a local full gate and never starts a hosted job). Limit focused backend to 30 minutes and set timeouts for all other jobs. Verify with the repo owner how zero spend is enforced while retaining hosted PR runs. Trigger reductions alone do not guarantee zero billing.
5. Inspect the failed Chromium logs and artifacts from November 20 onward. Match failing test titles, selectors, and timeouts to the app behavior, then reproduce the relevant tests if permitted. If a verified repair is unavailable, make e2e-smoke manual-only. Confirm its required-check status before making it manual-only, since a skipped workflow can leave a required status pending.
6. Run make ci and capture output under /workspace/at-notes/r6/ci/. Validate workflows with actionlint or a YAML parser. Record check names, trigger and timeout changes, and the E2E evidence in the report. Commit locally with the requested author identity after validation.

## Review

Request Griller GO and Sofien approval before implementation. The task prohibits pushing, so the plan stays local unless the user authorizes a draft PR. Keep GitHub Actions dispatch, credentials, Render, and database operations outside this session.

## Implementation notes

- The coordinator authorized implementation and will open the draft PR for review. The working copy remains local.
- Bash is the canonical entry point and Make delegates to it. Linux verification uses Bash because make is unavailable in this environment.
- The hosted Chromium log showed multiple UI-contract and data failures. The existing E2E job ID is retained with a manual input, a non-blocking result, and trace upload on failure.
