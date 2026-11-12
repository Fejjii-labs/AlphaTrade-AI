# Knowledge retrieval, memory and learning

Retrieval brings recorded playbooks, policies and lessons into a conversation with source references. It does not turn a document into market truth or an execution instruction. This guide reflects main `b58beda`, inspected October 7, 2026; [current status](current_status.md) records runtime unknowns.

## Two implemented retrieval paths

| Path | How it works | Important limit |
| --- | --- | --- |
| Knowledge service / legacy graph RAG | [RagService](../backend/src/app/services/rag_service.py) chunks and embeds content, stores SQL document/chunk rows and indexes/searches vector points with scoped metadata. | Provider/indexing observation and citation are not proof that the content is correct. |
| Current `/agent/turns` | [Interactive retrieval](../backend/src/app/interactive_agent/retrieval.py) searches existing SQL documents/chunks lexically, scanning at most 200 chunks and returning five hits by default. | Qdrant is not queried unless a vector retriever is injected. A configured Qdrant service does not imply vector retrieval on every Agent turn. |

Optional vector hits are reloaded from organization/user-scoped SQL chunks/documents before presentation. Organization-shared and user-owned content have different visibility. The [knowledge context builder](../backend/src/app/interactive_agent/knowledge_context.py) carries titles, source labels, chunk references and limitations into bounded model context. Stored guidance remains reference data; current strategy approval/risk settings need canonical application evidence.

The actual [Agent route composition](../backend/src/app/api/routes/interactive_agent.py) injects a market reader and conversational responder, but no vector retriever. Some typed reads bypass generic retrieval entirely, including recorded execution explanation and learning status. The [architecture diagram](architecture.md#agent-conversation-grounding-and-human-confirmation) shows the two paths separately.

## Storage and provider options

```mermaid
flowchart LR
  Input["Pasted text or explicitly saved file preview"] --> Service["Scoped knowledge ingestion"]
  Service --> SQL["PostgreSQL documents and chunks"]
  Service --> Embed["Configured embeddings provider"]
  Embed --> Vector["Qdrant index or local mock store"]
  SQL --> Agent["Default Agent lexical retrieval"]
  Vector --> Search["Knowledge vector search"]
  Agent --> Context["Cited context; no trading authority"]
  Search --> Context
```

| Option | Inspected default/policy |
| --- | --- |
| Local mock embeddings | Deterministic hash vectors; auto dimensions 384. Suitable for development, not evidence of semantic model quality. |
| Configured OpenAI embeddings | `text-embedding-3-small` by default; auto native dimensions 1536. `EMBEDDINGS_DIMENSIONS` can explicitly set supported dimensions. |
| Vector collection | `alphatrade_knowledge`; dimensions must match the selected embedding contract. |
| Local vector store | Process-memory option; content/index persistence and replica consistency differ from Qdrant. |
| Hosted policy | Staging/production require authoritative Qdrant and configured OpenAI; silent mock/in-memory substitutes are prohibited. |

Sources: [dimension resolution](../backend/src/app/providers/embedding_dimensions.py), [provider factory](../backend/src/app/providers/factory.py), [provider policy](../backend/src/app/core/provider_policy.py), [Qdrant adapter](../backend/src/app/providers/qdrant.py). Model/collection choices in configuration are not fresh deployed observations. A mismatch or outage must be reported as unavailable under hosted policy, not hidden as healthy retrieval.

Ingestion records SQL chunk count, vector backend, upsert acknowledgment, fallback flag and observation time. That is the last recorded indexing result, not a continuous health guarantee. Vector failure rolls back SQL ingestion; a subsequent SQL commit failure can leave orphan vector points because the two stores are not a distributed transaction. Retrieval will not expose those points without matching scoped SQL records. Changing embedding dimensions requires an explicitly reviewed reindex/restore plan; do not delete a shared collection as demo preparation.

## Relevance floor, embedding provenance and readiness

Added 2026-11-10 (review remediation, branch `claude/review-remediation-rag-integrity`).

- **Score scale.** Every vector store reports normalized cosine in [0, 1] (`(raw + 1) / 2`; 0.5 = orthogonal). Qdrant used to return raw cosine while the in-memory store normalized; both now normalize.
- **Relevance floor.** `RAG_MIN_RELEVANCE_SCORE` (default `0.55` = raw cosine 0.10) drops hits below the floor in `RagService.search`, the only caller of `VectorStore.search` (REST `/knowledge/search`, the graph `rag_retriever` tool, the interactive vector adapter and voice all use it). OpenAI `text-embedding-3` relevant passages typically score raw ~0.25-0.6 (normalized ~0.62-0.8) and unrelated text ~0.0-0.15 (~0.50-0.58), so the default only removes near-orthogonal noise. A floor above ~0.6 risks emptying staging retrieval; tune per environment from the `rag_relevance_filter` log (`dropped`, `kept`, `best_dropped_score`). `RagQuery.min_score` can raise the floor for one request, never lower it. Responses report `min_score`, `below_min_score` and `no_relevant_source`.
- **No relevant source.** When nothing survives, the graph sets `rag_outcome=no_relevant_source`, says so in the reply and cites nothing; the interactive path records "No relevant source" as a limitation. SHA-256 mock embeddings have no semantic signal, so in local mock mode retrieval usually finds nothing above the floor (identical text still matches); this is intended.
- **Citations.** Citations come only from retrieved hits. The narrative LLM returns `cited_chunk_ids`; ids outside the retrieved set (`citation_not_in_retrieved_set`) or ignoring retrieved sources make the service fall back to the deterministic narrative, which cites retrieved chunks by construction. Output validation is grounded only when cited ids are non-empty and all retrieved, or retrieval reported `no_relevant_source` / `retrieval_failed` (disclosed in the reply). The current `/agent/turns` conversational path still uses free-text `[Kn]` markers without id verification (follow-up).
- **Embedding provenance.** Indexing writes `embedding_model` (the model that produced the vector; fallback hash vectors are labelled `mock-embeddings`) into every point, with a payload index. Search withholds hits from another model; when every labelled hit mismatches it refuses with `embedding_model_mismatch` instead of answering "no relevant source". Points indexed before this change have no label: allowed with a `rag_hits_without_embedding_model` warning (count) unless `RAG_EMBEDDING_MODEL_STRICT=true`. Reindex path: retry indexing per document (`POST /knowledge/documents/{id}/retry-indexing`) or the reviewed collection recreate + reingest procedure; once all points carry the label, strict mode can be enabled.
- **Readiness.** `/health/ready` compares the live `alphatrade_knowledge` vector size with the configured embeddings size (cached 60 s; Qdrant client timeout bounds a cold probe). A mismatch makes it `ready=false`, `status=degraded`, names the vector provider in `degraded_providers`, and degrades `/providers/status` (no URL in the detail). A missing collection is fine: it is created on first write. The app runs the probe once at startup and logs `vector_collection_startup_check`. Boot is never refused and `/health` (Render's liveness check) is unaffected, so a mismatch is discovered at deploy without a crash loop.

## File import and source provenance

[Knowledge file preview/import](knowledge_file_import.md) supports UTF-8 (optional BOM) or BOM-marked UTF-16 text, Markdown, DOCX body/table text and selectable-text PDF. Preview saves/indexes nothing. Explicit save validates the exact preview receipt and parses within resource bounds. The stored result contains extracted text, chunks and file provenance; raw binaries and preview receipts are not persisted. Scanned images have no OCR support.

Source type, filename, document/chunk IDs and provenance help a reader locate evidence. A search similarity score is a retrieval ranking, not a truth/confidence score or trading signal.

## Journal and learning memory

[JournalRagSyncService](../backend/src/app/services/journal_rag_sync_service.py) can sync legacy Journal entries as `trade_journal`, using stable references and sanitization. `JOURNAL_RAG_SYNC_ENABLED` defaults true. This does not mean every canonical Journal trade, analytics summary or learning event is automatically embedded; each integration has its own authority and wiring.

Canonical Journal and learning attribution preserve trade/strategy/version lineage and separate planned quality, execution, risk adherence, behavior and outcome. [Learning persistence](phase8_learning_persistence.md) and [governed promotion](governed_learning_promotion_001_handoff.md) describe those boundaries on their stated implementation bases. Facts, user observations, inference and suggestions remain distinguishable. Narrative is not a fact hash.

The current Agent can read recorded learning status and draft supported changes. Promotion requires exact baseline/candidate validation, recorded paper evidence and explicit human approval; conversation confirmation cannot bypass those gates. This is stored context and governed adaptation, not online model training, autonomous self-improvement or proven behavior change.

## Verify a retrieval claim

For a separately authorized test/demo, use disposable content with a distinctive phrase. Establish its SQL record and ownership, recorded index observation and retrieved citation, then test wrong-tenant/user exclusion. Check the route's actual retrieval mode and provider status. Do not infer retrieval success from “Qdrant configured,” from a document count, or from a generic model reply.

The [evaluation guide](evaluation.md) distinguishes mock regressions, integration checks and runtime acceptance. No document import, collection recreation or live provider call was performed for this documentation change.
