# Review remediation: RAG integrity and silent misconfiguration (batches 1+2)

Branch: `claude/review-remediation-rag-integrity` (from `main` @ dce23b2b). Owner: Claude Code.
Source: external AI-engineering review, items (1), (2), (3), (4), (7), (8), (10), verified by the
supervisor on dce23b2b. Supervisor-directed: plan committed first, implementation continues on
the same branch; Griller review happens on the PR.

## Size note

This branch will exceed the ~400 changed-line guideline (mostly tests, the eval corpus and the
regenerated OpenAPI snapshot). If the reviewer wants a split, the natural cut is:

- **Batch 1 (RAG integrity):** (2) eval, (3) min relevance score, (4) citation verification.
- **Batch 2 (silent misconfiguration):** (1) model slug docs, (7) narrative strict schema,
  (8) `embedding_model` on vectors, (10) vector size in readiness.

Each item is its own commit so a split is a cherry-pick.

## Items

1. **Model slug (1).** Canonical default is the code: `LLM_MODEL=gpt-6-luna`
   (`backend/src/app/core/config.py`). Fix `.ai/TASKS.md` and `docs/AT010_readiness_audit.md`
   where they present `gpt-5.6-sol` as current. Add a "Canonical model configuration" section to
   `CLAUDE.md`. Test: `.env.example` `LLM_MODEL` equals the `Settings` default.
2. **Narrative strict json_schema (7).** Add a strict-compatible schema helper
   (`additionalProperties: false` at every object level, every property required, no
   `default`), send `json_schema` + `strict: true` from `NarrativeService`. Keep parse+fallback.
3. **`embedding_model` on vectors (8).** Indexing writes the model id that produced the vector
   (fallback-produced vectors are labelled with the fallback model, not the configured one).
   Search: hits whose `embedding_model` differs from the configured model are refused with a
   typed `embedding_model_mismatch` (degraded response, no mixed-space chunks). Legacy points
   without the field: allowed + structured warning with a count, unless
   `RAG_EMBEDDING_MODEL_STRICT=true`. Payload index for `embedding_model`.
4. **Vector size in readiness (10).** `/health/ready` probes the live collection vector size
   (cached, short timeout) and reports `ready=false` + the vector provider in
   `degraded_providers` on mismatch. Missing collection = fine (created on first write).
   Startup runs the probe once and logs. `/health` (Render liveness) is untouched; boot is not
   refused (would crash-loop staging).
5. **Minimum relevance score (3).** `RAG_MIN_RELEVANCE_SCORE` (normalized cosine [0,1]) applied
   in `RagService.search`, the single caller of `VectorStore.search` (REST, agent tool, interactive
   vector adapter, voice all go through it). Qdrant scores are normalized to the same [0,1] scale
   as the in-memory store. Optional per-request `min_score` can only raise the floor.
   Agent graph sets an explicit `no_relevant_source` outcome and never fabricates citations.
6. **Citation verification (4).** Narrative LLM output gains `cited_chunk_ids`; ids outside the
   retrieved set are stripped and flagged (`citation_not_in_retrieved_set`). Deterministic answers
   derive cited ids from retrieved hits. `output_validation` groundedness = cited ids non-empty and
   all in the retrieved set, OR an explicit no-relevant-source outcome. The session-memory
   pseudo-citation stops being added to `citations`.
7. **RAG eval that can fail (2).** No per-case source-type filter; same-type distractors; recall@k
   and MRR with thresholds and a non-zero exit; a deterministic lexical baseline embedding for the
   offline run; opt-in `--embeddings provider` (costs money, not run here); unit tests proving the
   hash embedding and a shuffled retriever fail.

## Out of scope / deferred

- Interactive chat (`interactive_agent/conversation.py`) free-text `[Kn]` markers: no structured
  output today; converting it is a larger change shared with voice. Tracked as a follow-up.
- No Alembic migrations, no hot files, no workflow edits, no provider calls.
