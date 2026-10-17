# Agent 3 contract — order diagnostics and knowledge consistency

Baseline: `main` / PR233 merge `b165b92276346f0e0fe3ccdbd2bec3443dc75d40`,
verified identical through GitHub on 2026-10-09. Branch: `codex/reviewer-wave-data`.
No external order, deployment, shared migration, runtime flag or credential change.

## Storage and readiness

`RagService.ingest(..., commit=True)` commits normalized SQL documents, chunks and
one scoped durable indexing intent together. `commit=False` flushes within the
caller transaction; its receipt must set `sql_chunks_stored=false` until that
authority commits. No remote embedding/vector work occurs in this transaction.
Linked replacement and deletion enqueue durable work in the same transaction.

`vector_index_status`: `pending` means durable indexing is queued; `ready` means
the current generation was written, verified and acknowledged; `failed` means
bounded attempts were exhausted; `unknown` means legacy/unobserved state.
Legacy `upsert_acknowledged` remains readable, but new pending receipts never use
it. Readiness belongs to a specific document generation, version and source hash.
`vector_backend` is nullable before observation. Attempts, next retry and sanitized
error codes will be visible in document indexing metadata. SQL content remains the
authority for retrieval and citations; vector payloads cannot supply missing SQL.

## Schema and migration order

Add document indexing generation and a tenant/user scoped `knowledge_indexing_jobs`
outbox with stable job identity, operation, version/hash, claim token/lease,
attempt count, retry time and error code. Deletion intents survive document deletion.
Generate a new additive Alembic revision after `a8agentcapture001` using disposable
PostgreSQL and repository autogeneration. Do not edit historical migrations.
Upgrade schema before starting the bounded indexing runner; do not turn on trading.

Agent 2's published manifest uses existing conversation messages and requests no
new schema. Agent 3's owned addition is `a8agentcapture001 -> a9knowledgeoutbox001`.
Current PR237 additionally includes `a10blofinactivity001` after a9; that is its
single final head. Review the consolidated branch before deployment.

## Compatibility and visibility

Keep RagService method names, normalized text, SQL chunk identity/hash, source
provenance and scoped deduplication. Add `include_shared` retrieval filtering so
an authenticated principal can retrieve own private and organization-shared rows.
Retain strategy templates in Agent retrieval. Agent 2 owns the adapter and final
document/chunk checks; Agent 3 validates SQL scope and current generation at RAG.
Linked source URI lookup must use the exact organization and user, including NULL.
Vector abstraction gains scoped document inventory and deletion, never collection reset.

## Shared file requests to Agent 1

Agent 1 owns frontend transport/types and generated contracts. Update Knowledge
response unions for pending/ready/failed/unknown, render stored separately from
searchable, and refresh readiness through document listing. Do not show successful
indexing for a pending receipt. `sql_chunks_stored=false` indicates an uncommitted
composed receipt, not a successful save. No independent shared transport edits here.

Manual orders use authenticated `/execution/manual-demo/instrument`, `/preview`,
`/confirm`, command detail and `/reconcile`; decimal fields remain strings, quantity
is exchange contracts. Preserve exact revision/content hash and command identity
after a lost response. Preparation, submission, exchange acceptance/rejection,
unknown outcome and fill must remain distinct. No automatic retry with a new order.
Agent 3 owns dedicated widgets only if a defect is reproduced.

## Coordination availability

Update: sibling manifests are now published and were read at Agent 2 `daa4f384`
and Agent 1 `d24b45e0`. Agent 2 requests no new schema or accounting table: durable
turn storage uses existing conversation messages. This leaves a coherent linear
migration continuation `a8agentcapture001 -> a9knowledgeoutbox001`. Subsequent
PR239 integration appends a10, the current PR237 final head; a9 remains the historical
Agent 3 feature head.
The private/shared RAG filter and parent/chunk SQL checks implement Agent 2's
requested interface; `STRATEGY_TEMPLATE` remains in Agent retrieval. Agent 1 owns
the generated contracts and readiness UI after these schemas are published.
Concrete response schemas are in `backend/src/app/schemas/rag.py`; document listing
contains the indexing observation, including job ID, attempts, retry time and error.
Pending uploads return SQL storage true, status pending and null vector backend.
No claim of embedding/vector fallback is made before provider work occurs.
Agent 2 can use `retrieve_for_agent` (shared visibility enabled there) or explicitly
set `RagQuery.include_shared=true`. Direct search defaults to private own scope.
Search fetches at most the requested `top_k` (maximum 50); unverified SQL hits are
discarded and mark the response degraded rather than claiming full coverage.

Scoped failed-job recovery: `POST /knowledge/documents/{id}/retry-indexing`, using
the existing authenticated transport and ingest rate limit. Retry preserves job
and vector identities, returns pending and resets an exhausted attempt budget.
Pending/processing/ready jobs are returned without starting duplicate work.
Wrong-owner IDs are 404. `DELETE /knowledge/documents/{id}` commits a scoped
tombstone and removes SQL content; remote cleanup follows through the same worker.

The indexing worker is opt-in through `KNOWLEDGE_INDEXING_ENABLED`, default false,
in the existing supervised paper worker. Review schema upgrade, Agent 1 UI/contracts
and resource budget before enabling it. This task does not change runtime flags.

No authenticated failing order request, sanitized owner error, account/session or
service log has been supplied. Incident root cause remains unknown; local
fake-venue tests cannot prove external acceptance. Rollout requirements are in
`knowledge_indexing_rollout.md`; the measured store decision is in
`pgvector_decision.md`.

Cross-module compatibility for Agent 2: a composed `application_result` may retain
`sql_chunks_stored=false` after the enclosing confirmation commits, because the
RAG receipt was produced before that commit. Its `pending` state never implies
search readiness. Refresh canonical document status after confirmation. Journal
and Agent application integration tests advance the real local runner explicitly;
provider outages retain committed content/application while indexing retries.
No Agent production modules were edited. Preserve these semantics when rebasing
the shared integration tests against Agent 2's branch.

Quota/accounting compatibility: a zero-token `rag_ingest` admission event commits
with each new content generation, including pending uploads. Duplicates add none.
Provider usage is separately recorded as `rag_indexing`, with existing global
budget checks before each batch; blocked budgets remain pending/failed. Both event
types count in the existing daily request counter. No quota/Agent modules or SDK
dependencies were edited. Agent 1 can label the two feature rows as content storage
and embedding/indexing work without describing pending content as searchable.


## Integration contract update
Source d1bf70ba remains preserved. Observation metadata is written with document ID, tenant/user, generation, version and source hash conditions; an old snapshot cannot overwrite replacement readiness. Qdrant initialization failure is retried by the same worker with bounded backoff; job identity and claim/attempt fences remain unchanged. Deterministic RAG/browser fixtures advance the real outbox and wait for readiness, without synchronous production indexing. The integration request counter supersedes the earlier accounting paragraph: `rag_ingest` counts storage admissions; `rag_indexing` records provider usage/tokens/costs without adding request admissions. Legacy events retain conservative counting. See knowledge_indexing_rollout.md for coordinated API/worker migration, backlog, activation, older writer retirement and rollback. The activation default stays false.
