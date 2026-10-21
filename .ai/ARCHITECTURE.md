# AlphaTrade AI — Architecture (verified)

> **Earlier architecture/project snapshot preserved below.** Statements about slice completion, per-order approvals, disabled Telegram and live URLs apply to the earlier phase/defaults, not observed current runtime. The current entry points are [product overview](../README.md), [architecture](../docs/architecture.md), [Agent](../docs/agent_workflow.md), [security](../docs/security.md) and [dated status](../docs/current_status.md). Current source permanently refuses real trading; explicit approved-strategy worker arming and separately governed demo capability do not weaken that boundary.


## Stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.0,
  Alembic, LangGraph + langchain-core, structlog, PyJWT, bcrypt, redis, httpx,
  qdrant-client. Managed with `uv`. Lint/format `ruff` (line-length 100, py312),
  types `mypy --strict`, tests `pytest` (`asyncio_mode=auto`, `pythonpath=src`).
- **Frontend:** Next.js 15, TypeScript, Tailwind CSS, Vitest, Playwright.
- **Data:** PostgreSQL, Redis, Qdrant.
- **CI:** GitHub Actions (`.github/workflows/ci.yml`).

## Repository layout

```
backend/            FastAPI app, tests, prompts, Dockerfile, pyproject
  src/app/
    api/routes/      HTTP endpoints (health, providers, knowledge, performance, ...)
    core/            config.py, deployment_safety.py, exchange_safety.py, auth, deps
    providers/       llm, embeddings, qdrant, market_data, exchange, billing, email,
                     factory.py, registry.py, embedding_dimensions.py
    services/        rag_service, quota_service, performance, journal, strategy, ...
    agents/          LangGraph runtime, nodes, response builder
    guardrails/      moderation, output validation
    db/              models, migrations (Alembic)
  scripts/           seed_demo.py, recreate_rag_collection.py, reingest_knowledge_base.py
  tests/             ~89 test modules
frontend/           Next.js 15 app, e2e (Playwright), components, lib
docs/               ~50 docs (architecture, security, staging, rag, etc.)
evaluation/         evaluate_agent.py, evaluate_rag.py, evaluate_guardrails.py, datasets
scripts/            ~48 deploy/smoke/validation shell scripts
render.yaml         Render blueprint (API + worker, paper-safe defaults)
```

## Request / agent flow

```
Next.js (JWT) → FastAPI → LangGraph agent
  guardrails → RAG retrieval (org/source scoped) → deterministic strategy signals
  → risk engine (ALLOW/WARN/BLOCK; BLOCK final) → optional LLM narrative (explanation only)
  → schema-validated response
Persistence: PostgreSQL (workflow), Redis (rate limit/cache), Qdrant (vectors)
```

The LLM layer only **explains**; it cannot change risk decisions or approval state.

## Providers & fallbacks

- LLM: `openai-llm` when `OPENAI_API_KEY` set, else `mock-llm`.
- Embeddings: `openai-embeddings` when keyed, else `mock-embeddings`. Dimension resolved
  by `providers/embedding_dimensions.py` (384 mock / 1536 for `text-embedding-3-small`).
- Vector store: `qdrant` (with `QDRANT_API_KEY` support + collection dimension guard),
  else in-memory fallback. Collection: `alphatrade_knowledge`.
- Market data: `binance-public` (read-only) or mock; provenance labels on responses.
- Exchange: mock / paper by default; optional BloFin **demo** (read-only) under strict gating.

## Key safety modules

- `core/deployment_safety.py`: `validate_deployment_settings`, `deployment_posture`
  (redaction-safe; booleans only for secrets).
- `core/exchange_safety.py`: exchange-mode gating; `trade_live` refuses startup.
- `core/config.py`: trading-mode validators (`execution_mode=trade` requires explicit enable).
  `telegram_interaction_enabled` defaults false (AT-043/AT-074; inbound Telegram
  protocol is not mounted on HTTP).
- `telegram_security/`: isolated enrollment/nonce/receipt/outbox protocol. No execution path.
  Persistence interfaces + in-memory test store; PostgreSQL adapter + Alembic from later
  slices. Exact replay binds an inbound semantic fingerprint (`REPLAY_CONFLICT` on mismatch).
  Bound private-chat messages use `receive_private_message`. See `docs/telegram_security_protocol.md`.
- `candidate_alerts/`: composes canonical Phase 6 `Candidate` onto that protocol. Candidate is
  the only alert authority. APPROVE never executes. Telegram remains disabled. No webhook.
  See `docs/phase6_candidate_telegram_alerts.md`.
- `telegram_paper_agent/` (AT-074): paper-only Watcher/Candidate/journal notifications and
  bound discussion. Mutating paper actions require identity-bound confirmation. Telegram
  never executes, never mints a Candidate, never overrides SetupAssessment or risk, and
  never enables live trading. See `docs/telegram_paper_agent.md`.
- Internal paper exit monitor (`app.workers.paper_exit_monitor`): optional component
  of the paper worker. It closes canonical `PAPER_INTERNAL` /
  `paper-internal-entry/v1` trades when a closed candle touches the stop or
  the single full-size target. Live trades read the Binance USD-M (or Bybit)
  public adapter that matches their evidence identity; replay stays on the
  fixture. Both-in-bar
  uses the stop. Writes go through `ExecutionService.close_canonical_paper_plan`.
  `PAPER_EXIT_MONITOR_ENABLED` defaults false. `PAPER_EXIT_MONITOR_DRY_RUN` defaults
  true, so an unset variable does not close a trade. Live closes require an
  explicit false.
  Production refuses the enabled flag. BloFin demo trades are not in scope.
  See `docs/paper_exit_monitor.md`.
- Paper Watcher runtime (`app.workers.watcher_paper`): continuous paper-only
  monitoring. Approved compiled strategy → live/read-only evidence →
  `WatcherOrchestrator` → `evaluate_canonical_strategy` → Candidate only on
  `CONFIRMED_SETUP`. PR126 freshness contracts stay authoritative: quote,
  trade-stream, closed-candle finality, historical evidence validity, and
  setup lifetime are separate clocks. OHLCV availability is not closed-candle
  finality. Trade coverage is not historical validity. Live quote stale is
  10 seconds; the 60-minute scanner stale window is legacy, not Watcher.
  Worker instance ids are unique. Renewing a lease requires the current
  fencing token. Missing monitor and live/replay mismatch fail closed.
  `build_watcher_paper_runtime` shares the worker clock with the candidate
  fence. The scan hook stays unset unless a caller supplies one. `main()`
  does not install Telegram. `WATCHER_ORCHESTRATION_ENABLED` stays false in
  staging and production. No orders.
- Canonical TradePlanRevision: `CanonicalTradePlanService` is the only first-slice
  plan authority. PostgreSQL binding uses `plan_authority` so legacy PVC-backed
  rows stay distinct from canonical Candidate ids. See
  `docs/phase7_canonical_trade_plan_binding.md`.
- `learning_attribution/`: record-only lineage from SetupAssessment → Candidate →
  TradePlan → paper execution → JournalTrade → learning facts. Reuses
  `JournalLifecycleProjector`; REJECT/SKIP never create executed outcomes.
  Phase 8 persists records/events in PostgreSQL (`d4f7a2c8e901`) and exposes
  `LearningQueryService` for strategy/pattern stats and RAG fact documents.
  See `docs/phase7_learning_attribution.md` and
  `docs/phase8_learning_persistence.md`.
- `paper_evaluation/`: continuous paper measurement over Watcher →
  SetupAssessment → Candidate → paper decision → Journal → attribution →
  strategy statistics → refinement *suggestion*. Query-time merge; not a
  second trading authority. AI may suggest a refinement and must not activate
  it. Alembic `e3f4a5b6c7d8`. See `docs/phase8_paper_evaluation.md`.
- `paper_interaction/`: composes those measurement facts into Telegram learning
  text and turns a Watcher scan report into an optional durable notification.
  The paper worker does not install the hook. Telegram cannot write market
  truth or strategy authority. Learning replies can load the same eligibility
  rows the canonical summary uses; the loader defaults to none.
  Alembic `d9e0f1a2b3c4` revises `e3f4a5b6c7d8`. See
  `docs/telegram_evaluation_integration.md` and `docs/final_paper_system.md`.
- `telegram_activation/`: controlled paper activation preflight, outbox
  delivery, polling/webhook intake, and rollback. Disarmed by default.
  `create_app` does not mount a webhook. Alembic head `a8c3e1b94d20`. See
  `docs/telegram_paper_activation.md`.

## Endpoints of note (backward-compatibility anchors)

- `GET /health`, `GET /health/ready`
- `GET /providers/status`
- `GET /performance/report`, `GET /performance/portfolio`
- `POST /knowledge/ingest`, `POST /knowledge/search`
- `GET /research-validation/evidence`, `POST /research-validation/promote` (AT-035 — advisory paper-queue promotion; paper-only)
- `POST /webhooks/tradingview`, `GET /tradingview/signals`, `POST /tradingview/signals/{id}/create-candidate` (AT-037 — signed intake + optional paper candidate; paper-only)
- `POST /exchange/blofin/sync`, `GET /exchange/blofin/sync/latest` (AT-037 — BloFin demo read-only snapshots; no order mutation)
- `GET/POST /paper-signal-orchestration/*` (AT-038 — deterministic paper-signal orchestration; paper-only; no order placement)
- `GET /canonical/market-status` (AT-069 — live read-only perpetual monitor; process default replay; staging intended source `binance_usdm`; fixtures never `live_mark`)
- `GET /health` perpetual evidence activation (AT-077 — `inactive` replay/rollback or `active` public USD-M; no credentials; Watcher/Telegram/live trading stay off)
- `GET /canonical/paper-evaluation/summary` (AT-073 — continuous paper evaluation measurement; Watcher stays off; refinements cannot activate)
- `GET /watcher/paper-runtime/status` (AT-070 — paper Watcher monitoring status; disabled by default; no scans from HTTP)
- `GET /market-watcher/monitoring` (AT-071/AT-072 — operator paper-monitoring snapshot; RUNNING requires fenced lease + fresh heartbeat; replay never live_mark)

## CI jobs

`backend` (ruff check, ruff format --check, pytest) · `deployment-safety` ·
`frontend` (lint, typecheck, test, build) · `evaluation` · `docker-build` · `e2e-smoke`.
