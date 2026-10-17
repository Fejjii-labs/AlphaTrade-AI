# Vector store decision: retain Qdrant pending corpus and operational evaluation

Decision for this change: retain the deployed Qdrant store. The durable SQL outbox
is useful with either backend and does not require a vector-store migration.
No production extension, collection, data, credentials or infrastructure changed.

## Measured local experiment

`backend/scripts/benchmark_pgvector.py` implements an experimental adapter with
the same vector upsert/search/scoped inventory/deletion interface. It refuses a
database outside loopback `agent3_vectors`. The current Qdrant adapter uses the
real SDK's local in-memory client. PostgreSQL 17.11 supports pgvector 0.8.0 here;
extension availability and privileges on deployed PostgreSQL are **unknown**.

The corpus is explicitly synthetic: seed 20261009, 500 documents / 2,000 chunks,
1,536-dimensional normalized random vectors, four organizations, private/shared
ownership, source types and strategy tags. Twenty measured queries follow three
warmups, with 204 eligible points under the tested tenant/user/source filter.
Real corpus size, embedding dimensions/model, query distribution and provider
quality have not been observed. Random vectors do not measure semantic quality.

| Measurement | Local Qdrant adapter | Local pgvector prototype |
| --- | ---: | ---: |
| Filtered query p50 | 30.036 ms | 3.020 ms |
| Filtered query p95 | 50.846 ms | 3.565 ms |
| Exact filtered recall@5 | 1.000 | 1.000 |
| Scope violations | 0 | 0 |
| Initial upsert | 1,192.73 ms | 1,769.99 ms |
| Scoped deletion | 12.807 ms | 0.452 ms |

These are single-process local measurements, not hosted Qdrant latency or an
application SLO. PostgreSQL used a scope B-tree bitmap scan and exact distance
sort. The HNSW index built in 911.27 ms but was **not selected** for these filtered
queries, so this does not establish filtered ANN recall or large-corpus scaling.
The prototype's deletion rolled back with the other SQL work. Both adapters
verified scoped cleanup without deleting another owner's points.

Total PostgreSQL relation size, including indexes/TOAST, was 34,111,488 bytes
(32.53 MiB); raw float32 vectors alone are 11.72 MiB. Python peak RSS was 333,152
KiB (325.34 MiB) across corpus construction and both clients; it is not per-store
memory or PostgreSQL server memory. Separate hosted CPU/IOPS, concurrent load,
backup time and network cost remain unmeasured.

Reproduce using a disposable local database with pgvector installed:

```sh
cd backend
PGVECTOR_PROTOTYPE_URL=postgresql://<local-role>@127.0.0.1:55439/agent3_vectors \
  PYTHONPATH=src .venv/bin/python scripts/benchmark_pgvector.py \
  --count 2000 --dimensions 1536 --queries 20 \
  --output ../docs/reviewer_wave/pgvector_synthetic_results.json
```

Raw measured results and the selected query plan are in
`pgvector_synthetic_results.json`. No benchmark numbers come from production.

## Tradeoffs and decision criteria

Qdrant keeps vector compute and indexing load separate from trading/accounting
PostgreSQL, at the cost of a second persistence boundary, service, credentials,
backup/reconciliation procedure and network transfer. The outbox closes the
confirmed rollback window without replacing that store.

pgvector can make content/vector deletion and indexing acknowledgment atomic
inside PostgreSQL. Embedding calls still belong outside the SQL transaction.
It removes a separate vector service but adds primary-database storage, WAL,
CPU/IOPS, indexing and vacuum load. Actual service tiers, invoices and workload
headroom are unavailable; no monetary saving or production-performance advantage
is claimed from this local comparison.

A migration recommendation requires an authorized read-only corpus inventory,
verified extension support and privileges, embedding model/dimension/version,
tenant/filter distribution and current Qdrant tier/utilization/cost. Evaluate
real labeled queries for citations, private/shared visibility, strategy templates,
named-source coverage and missing/deleted SQL rejection. Proposed review thresholds
are filtered recall@5 at least 0.95 against exact retrieval, no scope violations,
no stale/deleted citations, p95 at most 150 ms under representative concurrency,
and no material regression to execution/accounting DB latency. The supervisor must
approve the final workload and thresholds; synthetic results do not meet this gate.

## Backfill, cutover and rollback plan for a separately authorized migration

1. Keep SQL document/chunk IDs, normalized hashes, provenance and ownership as
   canonical. Pin an embedding model, dimensions, normalization and index version.
   Backfill only current committed generations into a separate candidate index.
   Record backend/index/model version rather than silently mixing generations.
2. Use durable bounded jobs and fenced acknowledgments for candidate writes;
   compare SQL chunk counts and scoped inventories. Continue normal Qdrant writes
   while the candidate catches up. Replay replacement/deletion intents, including
   those committed during backfill; reject superseded generations.
3. Shadow equivalent queries under private/shared tenant filters, load current
   SQL parents/chunks, and compare recall, citations, missing/obsolete point counts,
   latency, WAL/vacuum/backup load and actual monthly operating cost.
4. After review and supervised staging acceptance, switch the retrieval adapter
   by an explicit reviewed configuration change. Keep the Qdrant index and its
   catch-up worker available through the observation/rollback period.
5. If any threshold fails, return retrieval to the preserved Qdrant index and
   reconcile its latest committed generations before declaring recovery. Do not
   destroy a collection or roll back content storage to repair one document.

Production backfill, dual writes, cutover, adapter registration and Qdrant
decommissioning are deferred. The prototype is not a deployed provider option.
