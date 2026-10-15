# Workflow: BUGFIX

> Authoritative rules: `.ai/MASTER_WORKFLOW.md`. Statuses: `IN_PROGRESS`, `REVIEW_REQUIRED`,
> `BLOCKED`, `FAILED`, `READY` (no `DRAFT`). Preserve the existing task/checkpoint and standing
> authority; record meaningful milestones, blockers and interruption/completion boundaries.

## Procedure
1. Fix observable acceptance before repair in the existing task/handoff. Reproduce with exact
   command, input, expected versus actual, revision and environment; reuse valid prior evidence.
2. Write or identify a failing test that encodes the bug (behavior-level).
3. Find root cause; prefer the minimal, correct fix over a workaround.
4. Confirm the new test passes and no regressions appear.
5. Check safety-critical impact (risk/approval/exchange/data freshness).
6. Run affected checks and concrete adjacent regressions; record executed counts, skips and
   tested revision. Use deterministic providers. Do not rerun unrelated green checks or full
   suites during repairs; the single reviewed release gate follows .ai/RELEASE.md.
7. Close actionable review findings with focused evidence and update the existing handoff.
   Separate implementation, integration, deployment and live acceptance; batch related fixes
   and authorized pushes. Preserve mobile sync when available and record equality evidence.

## Rules
- No behavior change beyond the fix scope. Authorized routine Git actions continue under the
  standing task grant; main merge, deployment and other protected actions retain separate gates.
- Never mask errors silently; fail clearly with meaningful messages.
- Diagnose the specific failing behavior and repair it; do not stop merely because a test failed.
  If verification remains unavailable, record the precise blocked check. Pause only the affected
  operation and continue independent milestone work; use FAILED/BLOCKED/REVIEW_REQUIRED
  truthfully. Bundle genuinely missing access or authority into one concrete action packet.
- Before retrying an interrupted external mutation, reconcile its actual state and identity.
  Change the diagnosis after repeated identical failure rather than repeating expensive checks.
- Unavailable Mac/iCloud access is a recorded publication limitation, not a reason to stop code
  repair or demand per-edit pushes. Never claim failed/unexecuted verification succeeded.
