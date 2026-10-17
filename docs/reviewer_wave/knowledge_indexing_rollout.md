# Durable knowledge indexing and rollout

SQL document/chunk storage now commits with a scoped durable indexing job. Remote
embedding/vector work happens after that commit in the indexing worker, with all
SQL transactions closed. SQL rollback before enqueue commit produces no vector
write. Linked replacements retain the document ID, increment its version and keep
chunk identity/hash semantics. Scoped URI lookup now includes the exact user.

The worker uses PostgreSQL `FOR UPDATE SKIP LOCKED`, token-fenced acknowledgment,
five attempts, exponential retry delays capped at 300 seconds and 300-second leases
renewed between batches. A session advisory lock serializes remote operations for
one document in autocommit mode, without an open database transaction. Restart
recovers expired claims. Lock contention releases the claim without consuming the
provider retry budget. Versioned point IDs make duplicate delivery and a lost
acknowledgment idempotent. Old work cannot become searchable after replacement or
deletion: retrieval reloads both SQL chunk and parent, scope, source filters and
current indexing generation. Missing SQL content never comes from vector payloads.

Successful acknowledgment requires verified scoped inventory equal to the expected
points after obsolete-point cleanup. Deletes store a durable tombstone without a
document foreign key, remove SQL content in the same transaction and clean remote
points later. No operation resets a collection. Rolling reconciliation inspects
one document per cycle, requeues missing/obsolete inventories and skips metadata-only
documents. Reconciliation cannot discover historical rollback orphans whose document
never existed in SQL without a separately authorized scoped vector inventory.

## API and ownership

New uploads return `sql_chunks_stored=true`, `vector_index_status=pending` and
`vector_backend=null`. A composed `ingest(commit=False)` response has SQL storage
false until the caller commits. Stored content remains available as canonical SQL;
vector search readiness is separate. Pending does not mean upsert acknowledgment.
Documents expose the job ID, attempt count, next retry time, timestamp and sanitized
error code in ingestion metadata. Ready requires a verified current-generation
write and committed acknowledgment; failed means the retry budget was exhausted.
Legacy observations remain readable as unknown or their historical acknowledgment.

SQL admission records one zero-token `rag_ingest` usage event atomically with the
content/job, so pending uploads count toward the existing ingestion quota. A
duplicate does not add an event. Embedding batches/retries record actual or
explicitly estimated provider usage under `rag_indexing`; each batch checks the
existing monthly token/cost and daily request budget before its remote call.
Blocked budgets produce `indexing_quota_exhausted` and bounded pending/failure,
without a provider call. The daily request counter counts content/turn admissions;
`rag_indexing` batches and retries do not add request admissions. They still retain
usage event, token and cost accounting, and the worker checks the current daily
admission budget before each remote batch. Attempts covered by a durable turn
admission likewise do not add admissions; historical events without that marker
retain conservative legacy counting. Size worker throughput against provider and
token/cost capacity separately. Estimates do not claim billing-grade cost.

`POST /knowledge/documents/{id}/retry-indexing` retries an exhausted/unknown job in
the exact authenticated owner scope, preserves identity and returns pending.
Existing pending/processing/ready jobs are returned without duplicate provider work.
`DELETE /knowledge/documents/{id}` returns 204 after SQL deletion/tombstone commit.
Wrong-owner retry/delete IDs return 404. Existing authentication/rate limits apply.

Agent 1 owns generated frontend contracts, polling and the pending/ready/failed UI.
Agent 2 owns its retrieval adapter and durable turn storage. Its manifest requests
no new tables, so there is one final migration head. Agent retrieval includes own
private and organization-shared documents and preserves strategy templates; direct
search can opt into shared visibility. Rejected vector hits mark search degraded.

## Exact rollout requirements for supervising review

1. Integrate the three branches, regenerate Agent 1's OpenAPI/client contracts,
   and ensure a pending receipt shows stored content without claiming searchable.
2. Upgrade the reviewed schema in order:
   `a8agentcapture001 -> a9knowledgeoutbox001 -> a10blofinactivity001`.
   PR237's single final head is **a10**, including native BloFin activity. The a9
   indexing revision was generated with Alembic against disposable PostgreSQL, filtering
   only the owned additions; historical migrations were not edited. It adds nullable
   document generation and the outbox table/indexes/checks. Local upgrade,
   downgrade/reupgrade and legacy-content preservation are verified.
3. Deploy the reviewed code in a coordinated window. New ingestion needs the
   outbox schema. An old ingestion writer can still perform premature vector writes;
   do not run mixed writers indefinitely. Downgrading the schema drops jobs and
   generation tracking; preserve content and drain/export jobs before any separately
   reviewed downgrade. Prefer disabling the indexing component for rollback.
4. After integration and schema review, explicitly set `KNOWLEDGE_INDEXING_ENABLED=true`
   on the existing supervised paper worker. It defaults false. No Watcher/Telegram
   or trading arm changes are needed. This task does not set the deployed flag or
   edit Render/CI configuration. Leaving it false keeps new uploads pending.
5. Verify worker PostgreSQL, provider credentials, authoritative Qdrant availability,
   collection dimensions and keyword-index permissions. The adapter adds the scoped
   `document_id` payload index alongside existing tenant/user indexes. Missing
   providers/permissions produce retry/failure, not readiness or memory substitution
   in staging/production. Monitor indexing health and document attempt/error states.
6. Size for one indexing thread, two jobs and one reconciliation per poll interval,
   at most 2,048 chunks/document and 32 embeddings/batch. Inventory is capped at
   10,000 points / 80 pages. Existing provider timeouts govern calls; shutdown stops
   additional batches/jobs, completes the current remote call and leaves interrupted
   work retryable. Review embedding spend, provider capacity, DB connections and
   worker memory before rollout. Model/dimension changes require explicit versioned
   reindexing; this task does not change provider settings.
7. Run cross-module Agent, journal/lesson sync, capture, generated contract/UI,
   provider SDK, scope and worker/migration regressions in the consolidated branch.
   Complete one supervised exact-ref release gate under `.ai/RELEASE.md`; focused
   local passes are development evidence. External order acceptance stays supervised.

## Consolidated compatibility and rollback review

PR237 has exactly one Alembic head, `a10blofinactivity001`, after
`a8agentcapture001 -> a9knowledgeoutbox001`. Apply both additive revisions before
any new API or worker code; stopping at a9 does not qualify this candidate.
New writers require the outbox table and generation column. Old API writers remain
schema-compatible but bypass generation fencing; stop or replace them before enabling
new indexing. A mixed writer window does not establish knowledge consistency.

Deploy API and worker from the same reviewed revision. Leave
`KNOWLEDGE_INDEXING_ENABLED=false` until the supervised deployment review authorizes
activation. This flag, Watcher/Telegram controls, credentials, kill switch and exchange
execution settings are unchanged by this PR. With indexing disabled, uploads remain
stored/pending. No readiness claim follows from SQL storage alone.

Before activation, inventory pending/processing/failed jobs, oldest pending age,
legacy documents with null generation, and recent replacement/deletion volume. Review
provider throughput, quota/token/cost budget and Qdrant collection dimensions and
keyword indexes (`organization_id`, `user_id`, `document_id`). The bounded rolling
reconciler visits legacy documents with chunks and queues a generation; empty metadata
records remain unindexed. Backlog completion requires observing each current generation
as ready, not merely an empty claim batch. Record scoped failed-job retries separately.
Request quotas count content/turn admissions; `rag_indexing` and attempts covered by
a durable turn admission retain usage/token/cost accounting without adding admissions.
Historical events without that durable marker retain conservative legacy counting.

The same worker instance now reconnects after an initial Qdrant failure, with a
five-second probe and exponential reconnect delay capped at 300 seconds. Durable job
retry bounds and generation fences remain in force. Failed probes cannot mark ready.

For rollback, disable the indexing component and stop new writers before changing API
versions. Preserve SQL documents/chunks and export/drain durable jobs before considering
schema downgrade: downgrade removes jobs/generation tracking. Prefer keeping the additive
schema while rolling code back, and do not restart old synchronous vector writers while
new generations remain outstanding. Reconcile and verify scoped inventories after any
subsequent reviewed restart. A plain b165 image cannot resolve a9/a10 at startup.
Use the migration-aware rollback package and coordinated sequence in
[PR237 release qualification](release_qualification.md); keep the a10 native facts,
accounts and cursors as well as a9 jobs and document generations. This document is
a rollout plan; nothing was deployed.

Browser requests go directly to `NEXT_PUBLIC_API_URL`; there is no Next API proxy in
this repository. The browser abort budget and durable lease are 360 seconds. Provider
work uses the existing bounded turn budget. Any external ingress timeout must be checked
in the approved staging environment; its deployed configuration was not changed here.
