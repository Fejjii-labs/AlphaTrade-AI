# Agent runtime and provider reliability review

Baseline: PR233 merge `b165b92276346f0e0fe3ccdbd2bec3443dc75d40` on current main.
Contract: [agent2_contract.md](agent2_contract.md). No database migration is added.

## Result and review boundaries

The official OpenAI Python SDK 3.28.0 and HTTPX2 2.13.1 replace manual provider
transport. SDK retries are disabled. Application policy permits one retry for
explicit 408/429/5xx rejection; ambiguous timeouts/connections are not repeated.
Every actual attempt has a separate ModelCallAttempt and linked UsageEvent.
Responses and classic Chat Completions remain supported, with reasoning/output
limits, strict output request formats, JSON validation, domain schema validation,
refusal/incomplete handling and sanitized errors. Embeddings validate batch indices,
ordering, dimensions and finite numeric values. Models listing establishes connectivity
and authorization; it does not establish embedding generation. No paid embedding
request is added to polling.

SDK documentation verified on 2026-10-09: [official README](https://github.com/openai/openai-python),
[Responses reference](https://developers.openai.com/api/reference/resources/responses),
[pinned release](https://pypi.org/project/openai/3.28.0/).

The interactive capability engine and the LangGraph workflow remain distinct.
Their HTTP adapters and model capture retry share admission, rate limits,
correlation and durable accounting. Admission is a zero-token `agent_chat` event;
actual attempts keep the existing feature mapping and request/token quota semantics.
Unknown prices remain `unavailable`, with no monetary price invented.

Generic Agent retrieval now calls RagService through an Agent-owned adapter.
Every vector hit and citation is rebuilt from authorized SQL chunk/document data.
Private own and organization-shared content and strategy templates are retained.
Named/full passages continue through the original SQL context builder. Authorized
fallback filters the whole scoped store before deterministic ranking of at most
200 matching candidates; fail-closed policy refuses that fallback.

## Durable phases and concurrency

```mermaid
flowchart LR
  A[Admission and reservation commit] --> B[Prepare snapshot and commit]
  B --> C[Provider work with no SQL Session]
  C --> D[Guard and commit reply plus replay response]
  D --> E[Capture snapshot then close Session]
  E --> F[Capture provider work]
  F --> G[Guard revisions and commit canonical capture]
  G --> H[Commit receipt and replay response]
```

Reservation identity is UUID5 of organization, principal and normalized client
UUID. A SYSTEM transcript row stores route/payload digest, state and durable
response; repository history/listing excludes operational reservation rows.
Brief organization admission locks serialize applicable quota admission. Brief
conversation locks protect reservations/final writes. Provider work runs in a
threadpool and receives a session factory, never the HTTP Session or its Connection.
Each LangGraph SQL node owns a session; retrieval, market and narrative nodes have
sessionless runtimes with short detached storage ports. Canonical runtime binding
and canonical evidence lifetime storage use the current SQL phase.

| Situation | Behavior |
| --- | --- |
| Same key and original payload after completion | Replay saved response; no admission/model repeat |
| Same key with another payload/route | 409 `turn_key_conflict` |
| Same key while running | 409 `turn_running`, with durable identities |
| Reply committed while capture is pending | Replay reply with explicit pending capture/retry state |
| Different key in the same active conversation | 409 `conversation_turn_in_progress` |
| Unrelated conversation | Independent work can complete during provider waits |
| Transcript changes during model work | 409 `turn_stale_snapshot`; actual usage remains committed |
| Expired running request/process interruption | Same key reports `turn_interrupted`; no automatic repeat |
| New intentional key after 360s lease expiry | Marks previous reservation interrupted; old finalizer is barred |
| Capture fails | Committed reply remains; durable failed/pending receipt supports retry |
| Correction, Undo or later capture retry | Replays hydrate current private canonical entries/receipt |

Membership and ownership are reread at admission and final writes. Capture snapshots
entry revisions before reasoning, then verifies them under a brief user lock before
applying changes. Responses that contain saved receipts follow canonical commit.
LangGraph proposal/risk/approval tools, confirmation identity, canonical evidence,
recorded-trade grounding and decimal/hash behavior remain under their existing
domain authorities. Model prose receives no execution authority.

Legacy transaction-bound live-model convenience calls are refused before I/O:
`CaptureService.capture` supports pure injected planners; production capture callers
use `prepare` → close Session → `CaptureModel.plan` → fresh guarded `apply`, as
`capture_in_phases` demonstrates. A Session-bound `AgentService.run` with the real
OpenAI adapter must use `TurnCoordinator.run`/`run_chat`; direct transaction-bound
conversational responders likewise report unavailable before live I/O. Synchronous provider APIs
remain available to ingestion/workers. No production source caller still invokes
the old convenience capture path.

Client budget is 360s, superseding the frontend manifest's current 240s.
Optional request headers and concrete 409 recovery schemas are now in OpenAPI;
200 result schemas stay compatible. Client keeps the original key and payload, not
the separately derived server turn_id. Frontier model, effort and output limits are preserved. Reply
and capture remain sequential reasoning calls, potentially with one retry each.
Cancellation stops client waiting; accepted work may still finish. Process death
can leave upstream usage unknown until reconciliation; the interrupted reservation
prevents an automatic billable repeat. Database outage after provider I/O surfaces
telemetry persistence failure rather than claiming durable accounting or zero cost.

## Measured evidence and validation

Disposable PostgreSQL 17.11, independent connections and NullPool were used.
In the stalled interactive test, both reply and capture providers wait on explicit
threading events. Another connection takes `FOR UPDATE NOWAIT` on the conversation,
`pg_stat_activity` reports no other idle transaction, and another conversation
completes during each wait. A second test exercises the retained LangGraph narrative
provider and asserts the same absence of a conversation lock/idle transaction.

One recorded interactive run: total 482.72ms, reply provider stage 192.65ms,
capture provider stage 206.22ms, capture total 224.49ms, retrieval 0.31ms.
Recorded coordinator database phases were 23.70/34.66/9.18/4.32ms; capture read/write
phases were 9.87/8.38ms. Provider stage timing includes short quota/ledger bookkeeping;
these are controlled waits and local timings, not live provider performance claims.
Structured logs emit retrieval, provider, capture, per-node network, database phase
and total timing, with identities rather than private prompts or credentials.

Focused verification runs (not the consolidated release gate):

- Provider transport baseline: 142 passed, 8 local Qdrant connectivity warnings.
- Broad explicitly selected cross-module run: 354 passed, 6 skipped, 8 warnings,
  81.74s. The six skips were the proposal fixture's hard-coded PostgreSQL URL.
- Configured PostgreSQL/affected follow-up: 57 passed, zero skipped, 34.08s;
  includes all six formerly skipped proposal concurrency tests and LangGraph stall.
- Final affected provider/router/capture/conversation selection: 122 passed,
  zero skipped, 38.28s.
- Shared/private retrieval also passes with Agent 3's schema-only include_shared
  publication; that field alone never activates unsupported own-only search.
- Final boundary and committed-policy checks: 79 passed, 2 warnings, 41.98s.
  The prior canonical selection found one test-only uncommitted quota update; the
  test now commits it before HTTP, and the complete usage-quota module passes.
- Final committed-head policy/retrieval/continuity/PostgreSQL check: 38 passed,
  zero skipped, 2 warnings, 23.27s.
- Final additive recovery-contract and PostgreSQL coordination check: 16 passed,
  zero skipped, 2 warnings, 7.83s; validates the actual 409 error envelope.
- Final canonical paper authority/phase8/order idempotency selection: 89 passed,
  zero skipped, 60.60s.
- Full source type comparison: baseline 495 errors in 100 files; current 491 errors
  in 100 files, zero introduced categories after ignoring line shifts; four existing
  wrapper annotation errors removed. Affected 18-file check, including the additive
  recovery-contract module, passes with imports silent.
- Backend Ruff check and format check pass (1169 files). No unrelated type cleanup.

Exact broad selection used:

```sh
PHASE1_POSTGRES_URL=<disposable-postgres-url> .venv/bin/pytest -o addopts='' -q \
  tests/test_openai_llm_responses.py tests/test_embedding_sdk_reliability.py \
  tests/test_embedding_dimensions.py tests/test_provider_integration.py \
  tests/test_at013_provider_fail_closed.py tests/test_at015_provider_mode_quotas.py \
  tests/test_phase2_model_router.py tests/test_providers_status.py \
  tests/test_shared_turn_policy.py tests/test_agent_vector_retrieval.py \
  tests/test_turn_coordinator_postgres.py tests/test_agent_capture.py \
  tests/test_interactive_agent_foundation.py tests/test_agent_conversation_continuity.py \
  tests/test_agent_capture_postgres.py tests/test_strategy_conversation_foundation.py \
  tests/test_narrative.py tests/test_agent_recorded_trade.py \
  tests/test_interactive_agent_proposal_postgres.py tests/test_rag.py \
  tests/test_guardrails.py tests/test_risk_engine.py tests/test_live_hash_stability_and_expiry.py \
  tests/test_at012_paper_risk_at_execution.py tests/test_planned_reward_risk.py \
  tests/test_planned_reward_risk_postgres.py --tb=short
```

## Integration and delivery ledger

| Status | Work |
| --- | --- |
| Completed | Current main/PR233 ancestry, isolated branch, early contract, official SDK, shared policy/retrieval/accounting, durable turn/capture phases, PostgreSQL concurrency and focused regressions |
| Pending integrator | Agent 1 header persistence, 360s client budget and generated OpenAPI drift; Agent 3 shared vector filters and generation-aware indexing; one consolidated release gate |
| Deferred | Supervised paid model acceptance, deployment/merge/trading activation |
| Blocked environment | Mac sync script/iCloud mirror unavailable on Linux; cloud handoff branch is used and source hashes are verified |

Agent 3 manifest read at `3ab96b0` (previous contract `361d493b`) on
`codex/reviewer-wave-data`. Adapter uses `RagQuery.include_shared` when RagService advertises
  `supports_shared_search = True`.
Until that dependency lands, bounded organization overfetch plus SQL checks is
safe, but another user's private results can crowd permitted relevance out of the
50-hit window. This limitation is exposed; integrator must verify combined branch
visibility, SQL authority and indexing generation freshness. No RagService/schema,
outbox, migration, manual order module, frontend/generated contract or shared workflow
is edited here.

No orders, Telegram messages, merge, deployment, credential or runtime flag changes.
