# Park backend leaf features for audit PR 2

## Scope and ownership

Codex claims the audit PR 2 backend parking track on `codex/park-leaf-backend`.
The files in scope are the leaf routes, services, repositories, schemas and
tests named in audit section 3.1, the affected wiring, the research tick in
`paper_worker.py`, and the generated frontend client and its broken imports.
Coordinated frontend page removals and redirects belong to the frontend tracks.

Base: `ba79e0d8`. Recovery tag: `archive/full-app-2026-11-17` at `d905497d`.
Sofien authorized implementation on 2026-11-19. Obtain the Griller GO before
code edits under the repository plan rule.

## Implementation

1. Inventory callers of the section 3.1 routes and modules. Remove the
   independent paper validation, validation and quality diagnostics, analytics
   and coaching, signal ingestion, TrendPulse screening, billing, invitation,
   audit listing, provider grid and legacy worker surfaces. Remove the dead
   confluence and external integrations packages. Unwire dependencies, router
   registrations and legacy tool handlers.
2. Preserve the Telegram alert outbox and its delivery path. Preserve paper
   validation run reads used by canonical strategy evaluation. Remove route
   shells only after accounting for active callers and preserving order and
   approval behavior. Review any conflicts between a listed route removal and a
   retained trading path with Griller before editing the affected code.
3. Remove the research tick and stop writes for the parked features. Keep the
   quota interface backed by an always-allow result and stop recording
   `agent_turn_admission` rows. Preserve database tables and migration history.
   Leave governed BloFin demo, execution, risk and autonomy code outside this
   track. Report any legacy worker reader that requires an out-of-scope edit.
4. Update route, RBAC and OpenAPI export expectations. Verify Telegram alert
   delivery after parking. Regenerate the frontend client and limit frontend
   compatibility changes to imports that break because of removed endpoints.

## Verification and delivery

Run backend Ruff and focused pytest with `uv run --frozen --extra dev`, plus
`pytest --collect-only -q`. Run frontend Vitest for touched areas, typecheck,
lint and `api:check`. The coordinator owns full CI. Record all checks and
counts in `/workspace/at-notes/park/park-report.md`.

Keep commits logical and add `Full-Backend: false`. Push with the authorized
wrapper to the task branch and verify its head with `git ls-remote`. Request
Griller review for the final implementation head before merge.

## Implementation deviations after Griller review

- Agent turns, configured LLM attempts, RAG indexing and realtime voice still
  have billable paths. Keep the quota limits, admission writes, provider usage
  accounting and voice reservations under C1 and C3.
- Keep the approval, proposal, paper-position and pretrade routers. They still
  handle approval or revocation, paper closes, or active agent handoffs. Keep
  the Watcher monitoring read used by the settings and dashboard.
- Keep shared paper validation, Watcher, analytics and coaching code reached by
  retained readers, and the native experiment outcome reader. The deprecated
  worker heartbeat and scan-table readers in autonomy and Watcher stay in their
  owners' scope. Historical ORM mappings and migrations stay in place.
- The coordinator owns the rebase after #473 and the full gate.
