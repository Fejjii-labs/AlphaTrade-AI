# Workflow: IMPLEMENT (paper-safe feature work)

> Authoritative rules: `.ai/MASTER_WORKFLOW.md`. Statuses: `IN_PROGRESS`, `REVIEW_REQUIRED`,
> `BLOCKED`, `FAILED`, `READY` (no `DRAFT`). Use existing HANDOFF.md/CHANGELOG_SESSION.md at
> meaningful milestones, blockers and interruption/completion boundaries. Preserve standing
> task authority; unavailable mobile sync does not stop independent cloud work.

## Preconditions
- The existing task/handoff records the outcome, scope, owner, authority, risk and a small
  named acceptance checklist fixed before implementation. Reuse the current AT-XXX task;
  do not create another delivery ledger.
- Change must not enable real trading or external account actions.
- Confirm the branch, relevant changes and checkpoint; set `IN_PROGRESS`. Reuse valid evidence
  and inspect only prerequisites needed for the next operations.

## Rules
- Small, focused, typed changes. Follow existing project conventions.
- Separate concerns: business logic / IO / data access / external services.
- No hardcoded secrets; use config. Validate inputs at boundaries.
- Preserve all existing APIs and paper-only behavior unless the task explicitly changes them.
- Authorized routine commits, batched pushes and integration continue under the existing
  task grant; do not request approval again at each phase. Protected operations outside
  that grant still require concrete review and authorization.

## Procedure
1. Inspect related code first; confirm the simplest robust approach.
2. Connect the smallest complete user path, reusing published implementations. Implement
   with type hints; add/adjust meaningful behavior and failure-path tests.
3. Keep deterministic risk logic deterministic and tested.
4. Run local validation (see below). Fix lint/type/test failures.
5. Update docs if behavior/config changed.
6. Record accepted/total against the fixed checklist, exact evidence and the next operation.
   Separate implemented/integrated/deployed/live acceptance. Sync when available per the
   master workflow; batch authorized publication. Continue independent work if one operation
   needs external access, recording one precise action packet.

## Local validation (backend, from `backend/`)
```
uv run ruff check <changed-python-files>
uv run ruff format --check <changed-python-files>
uv run pytest tests/<affected-test>.py
```
Frontend (from `frontend/`): changed-file lint, relevant type checking, and
`npx vitest run <affected-test-files>`. Run reachable browser/API checks for changed user
journeys. Broaden only for a concrete adjacent risk or required gate. Full backend CI and
combined frontend validation follow .ai/RELEASE.md; no complete suite after each fix.

## Definition of done
- Agreed acceptance passes at its stated evidence level, focused checks pass, unexpected
  skips are explained, docs are current and safety invariants remain intact.
- The owner has integrated scoped worker results directly and closed material findings.
- HANDOFF.md records exact revision, accepted/total, deployment state, remaining prerequisites
  and next operation with normalized self-hash. Set READY only for completed stated scope;
  pause only genuinely blocked/protected operations. Record actual sync equality or its
  unavailable dependency; do not claim mobile publication or deployment without proof.
