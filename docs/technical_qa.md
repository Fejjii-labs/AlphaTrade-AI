# Technical interview questions and answers

Answers reflect main `b58beda`, inspected October 7, 2026. The [status record](current_status.md) distinguishes deployed reports, unknowns and pending acceptance. Use these to explain the work you can substantiate.

## What does the user gain?

A linked record of strategy rules, evidence, risk, paper execution and review. The Agent helps interpret context and propose supported changes. Better discipline, commercial adoption and returns need evaluation; the repository does not establish those outcomes.

## Does the model decide or execute trades?

Application services own setup/Candidate lifecycle, risk, permissions, freshness and execution authority. The model produces conversation from supplied facts. Typed actions create structured proposals, and separate confirmation calls existing services. A worker can continue an approved strategy under explicit operator arming. The Agent and Telegram do not gain general order authority from text.

## Why FastAPI, PostgreSQL and a separate worker?

Python fits schema validation, deterministic strategy/risk services and model integrations. PostgreSQL provides durable relational records and transaction boundaries. A separate worker handles background Watcher/Telegram supervision; each component retains its own health/failure state. The modular backend is practical at this scope, but API size, synchronous work and resource limits still need workload-specific review.

## Where does LangGraph fit?

The earlier `/chat/message` workspace uses an explicit graph of guardrails, retrieval, tools and structured/narrative response stages. The current `/agent/turns` path has its own typed read/proposal/conversation service. LangGraph's presence in the manifest is not evidence that every current Agent turn runs through that older graph.

## Which model is active?

UNKNOWN without current runtime evidence. Settings default the base provider to `gpt-6-luna`, routed Tier A to `gpt-6.1-sol` and Tier B to `gpt-6-luna` (canonical in `backend/src/app/core/config.py`); current general Agent synthesis is Tier A. Requested/selected/resolved model are separate facts. Environment overrides and provider results can differ from defaults. [Model routing](agent_workflow.md).

## Is the Agent always using vector RAG?

No. Its default retrieval is bounded lexical SQL search over documents/chunks: at most 200 scanned chunks, five hits by default. An injected vector retriever can provide hits that must pass scoped SQL reload. The Knowledge service separately indexes/searches vectors. A Qdrant setting does not establish the mode of a particular turn. [Retrieval](rag_system.md).

## What happens when a provider fails?

Local mock/permissive paths and hosted policy differ. Staging/production require configured OpenAI, authoritative Qdrant and Redis security backends; they prohibit silent mock/in-memory substitutions. Canonical market evidence has its own fail-closed source/freshness rules and may use configured whole-source Bybit failover without mixing venue evidence. A conversational reply can become explicitly unavailable while factual records remain. [Security](security.md).

## How is memory different from learning?

Conversations, strategy context, knowledge and Journal/attribution are stored application memory. Governed learning creates an explicit hypothesis/candidate, compares version-bound evidence, requires paper validation and human promotion. It is not model fine-tuning or silent strategy activation. The product has behavior-review foundations but no proven continuous behavior-change engine.

## How are approval and risk enforced?

Research validation, canonical strategy approval, setup confirmation and execution eligibility are distinct states. User paper commands bind authorization to an immutable plan revision/hash; armed worker continuation has scoped operator authority. Deterministic risk `BLOCK`, kill switch, permissions and freshness checks still apply. A generic approval badge or model sentence cannot replace those gates.

## How do you handle duplicate workers and provider corrections?

Worker leases/fences reject obsolete owners; operation identities and durable claims/receipts support restart/idempotency. Immutable observation reuse rejects a same-policy conflict. PR209's real acquisition policy v2 appends new policy identities while preserving v1 receipts and clocks. Its guard/confirmation does not promise Binance never revises a candle; fresh live SFP acceptance remains pending. [Recovery boundary](sfp_candle_finalization_recovery.md).

## How are tenants, uploads and prompt injection handled?

The backend resolves persisted membership and scopes resources; reader/trader/owner dependencies vary by route. File previews use bounded parsing and signed principal/content-bound receipts before explicit save. Documents remain reference data. Typed actions, scope checks, provenance, guardrails and separate authority reduce exposure, but no penetration test, certification or comprehensive prompt-injection proof is claimed. [Security details](security.md).

## Can it send real exchange orders?

Real-money trading is permanently refused by the inspected source in every environment. Internal paper simulation is the default. A separately gated BloFin demo integration includes native evidence reads and PR220's owner-confirmed BTC MARKET test with one full target. Native demo acceptance remains pending; the manual capability has no limit entry, manual exit or complete exit/PnL/funding reconciliation. Neither internal paper success nor Telegram receipt proves BloFin execution.

## How far does voice go?

Browser dictation, transcript review/send and optional reply playback are implemented through the existing Agent flow. Backend voice I/O and screenshot analysis remain unimplemented contracts. Earlier speech fixtures do not establish real microphone/service/Safari/iOS behavior; richer continuous conversational voice is future scope.

## What has actually been verified?

This documentation task checked source/manifests, GitHub main/PR220, Vercel commit status and CI #783 metadata, then validated documentation links and rendered diagrams. Existing captures have historical fixture provenance, so the main showcase uses diagrams. Supplied API/worker/migration, received notification, internal-paper/Journal and demo-account-sync observations are recorded separately. Prior exact-base documents retain their own tests. Fresh SFP recovery, existing Journal target repair, BloFin demo acceptance and complete MVP acceptance remain open. [Evidence](current_status.md).

## What would you improve next?

Finish bounded operational acceptance and pending repairs, improve retrieval/observability/device evidence where limited, and extend strategy/voice/orchestration only behind existing validation/authority boundaries. Evaluate behavior/usability and statistically meaningful paper outcomes separately from engineering checks. Real-money enablement is not a routine roadmap toggle. [Roadmap](limitations_roadmap.md).
