# Agent 1 — frontend reliability and API contracts

Branch: `codex/reviewer-wave-frontend`. Baseline/current main at inspection:
`b165b92276346f0e0fe3ccdbd2bec3443dc75d40` (PR233 merge, verified ancestor).
Original `/workspace/AlphaTrade-AI` checkout remains clean. Isolated worktree:
`/workspace/reviewer-wave-frontend`. Scope/dependencies were published in `33d3f3c`,
then revised estimates and baseline hashes in `d24b45e`.

## Completed owned work

- Agent acknowledgments render immediately with required backend IDs, freeing the
  composer before independent history reconciliation. Exact-ID deduplication retains
  full replies, evidence, receipts and proposal decisions through stale/incomplete/failed
  history. Navigation/logout/unmount/out-of-order guards prevent cross-context updates.
  No automatic mutation resend or token streaming claim.
- All supported result filters reset offset. Empty later setup pages offer first-page
  recovery outside hidden chart children; failed/unavailable evidence stays distinct
  from successful empty data. Unrelated query parameters and browser history persist.
- OpenAPI generates types/client/runtime validators together for strategy PATCH,
  Agent turn/confirm/reject, attention and daily review. `apiFetch` remains the only
  authenticated transport. Decimal strings, enums, omitted/null distinctions and Boolean
  authority are validated without coercion/defaults/property stripping.
- Independent Behaviour/Validation TanStack Query sources include organization, user,
  endpoint and normalized filters in keys. Requests pass AbortSignal. Logout/identity
  changes cancel and clear private caches; refresh flights cannot overwrite another login.
  Freshness 30s, GC 5min; one transient network/5xx/429 read retry; no contract/auth/client
  retries, focus/reconnect refetch, mutation retries or browser cache persistence.
  Cached content remains during refresh, with explicit failed-refresh notices and original
  evidence timestamps. Validation summary alone prefetches on tab hover/focus.
- `/usage` and `/settings/usage` redirect to `/settings/billing#usage`; `/billing` to
  `/settings/billing`, preserving query. `/strategy-lab/new` redirects to Agent intent;
  `/strategy-lab/:id/edit` carries `strategy_id`. Manual authoring entry points are retired;
  detail/version/approval/paper views and back navigation remain. An authorized strategy
  GET precedes context use; navigation never sends/applies/activates. Useful billing/usage
  tests target shared views. Dedicated manual-order widgets are untouched.
- Generated strategy PATCH persisted `setup_type` through real local HTTP and disposable
  PostgreSQL, with independent GET reload and a second tenant receiving 404.

## Baseline schema and regeneration

Full OpenAPI SHA256:
`7696544c1abb69b05b694ea0f56ffd985d3a69003399b4fad379f81bc109e33c`.
Exact pilot/component hashes: `frontend/src/lib/api/generated/hashes.json`.
Install locks: `cd backend && uv sync --extra dev`; `cd frontend && npm ci`.
Regenerate: `cd frontend && npm run api:generate`; drift: `npm run api:check`.

The generator uses `uv run --offline --frozen`; export clears inherited configuration,
ignores `.env`, uses mock/replay paper settings and rejects socket connections.
No lifespan/database sessions/provider resolution. AJV standalone validators and
openapi-typescript types use the same schema closure; artifacts are committed.
Added locks: Query 5.104.1, AJV 8.20.0, ajv-formats 3.0.1, openapi-typescript 7.13.0 (dev).
No parallel handwritten pilot schemas or CI workflow changes.

## Dependencies and narrow shared requests

Sibling manifests read at Agent 2 `daa4f384` and Agent 3 `361d493b`;
remote branches contained manifests only at the last check.

- **Agent 2:** frontend follows the requested 240-second wait and sends a fresh
  `Idempotency-Key` per intentional turn. Baseline ignores the header. Publish durable
  replay/conflict/recovery response schemas and semantics before timeout recovery UI.
  Current timeout retains the draft, explains uncertain completion and directs checking
  history; it does not resend. Preserve required message IDs and Boolean authority.
- **Agent 3:** publish concrete ingestion/document-listing response models/endpoints
  for indexing status, SQL commit acknowledgment, generation/version/hash and
  attempts/retry/error metadata. Then extend generated boundaries and render stored
  versus searchable with pending/ready/failed/unknown states through document listing.
  A prose union is insufficient to invent response validators or refresh endpoints.
  Indexing UI is explicitly blocked on that code/schema publication.
- **Backend owner/integrator:** SQLite strategy responses return `created_at` without
  a timezone, violating OpenAPI `date-time`. Please normalize timestamps or publish a
  revised supported contract. PostgreSQL passed; validators stay strict. This is a local
  compatibility limitation, not a deployed finding.
- **Integrator:** regenerate after Agents 2/3 merge, add `npm run api:check` to CI,
  review consolidated migration/schema order and own `.ai/RELEASE.md` acceptance.

No independent Python Agent/provider/RAG/schema/database/manual-order edits.

## Validation and final ledger

Exact commands/counts/reproduction: `docs/reviewer_wave/verification.md`.
Measurements: `browser_latency.json`; relevant screenshots: `screenshots/`.
These are development checks. PostgreSQL used ORM `create_all`, not migration upgrade
or staging/live acceptance. Normal build could not fetch Google Fonts; the repository's
test-only offline-font production build passed, without claiming real font delivery.

| State | Work |
| --- | --- |
| Completed | Agent guards/responsiveness, pagination, baseline generated pilots, private Query sources, narrow prefetch, routes/authoring retirement, PostgreSQL persistence journey, development checks |
| Pending | Supervising review and integrator regeneration after sibling integration |
| Blocked | Agent 3 indexing schema/UI; Agent 2 durable recovery schema/UI; SQLite timestamp compatibility; Mac iCloud mirroring/byte and hash verification |
| Deferred | Broad redesign, React Compiler, integrated release dispatch, authenticated live acceptance, merge/deploy |

Independent Agent 1 implementation is complete. Dependent UI work has no reliable
estimate until owners publish concrete schemas. Local/cloud handoff is published;
Mac mirror status is **UNKNOWN**. Paper mode, real trading disabled, decimal/hash
contracts, tenant privacy, risk/approval controls and runtime settings are preserved.


## Integration contract update
Source 69778070 remains preserved. Integration retains original UUID/body in authenticated tab storage until terminal acknowledgment or explicit terminal recovery, clears scope on logout, and exposes typed 409 reasons and explicit same-key recovery. Acknowledged history pairs preserve order and metadata across partial reads. Conversation strategy binding is loaded from SQL-backed conversation GET; it takes precedence over the entry URL. Browser requests are direct to the API, with a 360-second abort budget. Draft authoring/import uses explicit strategy actions, then the canonical preview/confirm/version/compile/approve endpoints. Imports remain separate from tool-action envelopes. Setup type is saved and reloaded; confirmation does not grant execution. Generated contracts include turns, chat/recovery and knowledge indexing states with strict date-time validation. CI checks drift and registers reviewer tests against the verified production build.
### Consolidation browser follow-up

Integration repair a63abe1 retains an uploaded document's original reference in
acknowledged user-message metadata and its receipt even when capture fails. The
same reference is read from persisted history after reload; the capture error
remains visible. Exact saved-record/document navigation reads its target without
unrelated library listings or readiness polling. The original import browser
assertions remain, with additional reload/link assertions. Focused verification:
49 unit tests, lint/types, 61-page production build and five production browser
cases passed without retries. Final exact-head PR CI belongs to the integration
ledger, PR237 description and canonical handoff.
