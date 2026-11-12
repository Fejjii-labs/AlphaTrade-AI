# AT-010 — Current-version readiness audit

> **Historical / release-specific record.** Original decisions, procedures and results below are preserved for their stated date/base. They are not current runtime observations or complete MVP acceptance. Read the [current status and pending acceptance](current_status.md), [architecture](architecture.md) and [deployment entry point](deployment.md) first; recheck commit-specific environment, migration and activation values before using older procedures.


**Task:** AT-010  
**Date:** 2026-07-21  
**Commit audited:** `e123100` (main); staging API `git_sha=5f2d7cf`  
**Scope:** Repository + read-only staging verification  
**Safety posture preserved:** `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_exchange_demo`  
**Live trading:** Not implemented and not enabled.

---

## Executive verdict

AlphaTrade AI is **paper-MVP / staging ready** with strong exchange fail-closed gates, deterministic risk-rule cores, approval-gated paper execution, provider health visibility, and broad automated tests.

It is **not ready for real-money exposure**. Critical API surface gaps (unauthenticated tools), soft market-data/RAG degradation paths, and proposal-flow risk under-enforcement must be closed before any sandbox or live-execution program.

---

## Evidence baseline

| Check | Result |
|-------|--------|
| Branch / commit | `main` @ `e123100`, clean working tree |
| Backend ruff | Pass (`All checks passed!`, 476 files formatted) |
| Backend pytest | Pass (`exit:0`, ~23 min, session AT-SESSION-20260721-001203) |
| Staging `/health` | `execution_mode=paper`, `real_trading_enabled=false`, `environment=staging`, `git_sha=5f2d7cf…` |
| Staging `/health/ready` | `ready=true`, `providers_unavailable=0` |
| Staging `verify-safety.sh` | Pass (2026-07-21): paper, real trading false, blofin-demo-account, mock billing |
| Staging providers | openai-llm healthy (`gpt-5.6-sol`, the staging model on 2026-07-21; current default is `gpt-6-luna`); embeddings healthy (`text-embedding-3-large`, 1536-d); qdrant healthy |
| Unauth probes | `GET /tools` → **200**; `GET /docs` → **200**; `POST /risk/check` → **422** (no auth) |
| Pytest pass count | UNKNOWN (suite exited 0; exact count truncated in log) |
| Full CI re-run this session | Not re-run (last known green after AT-009) |

---

## Area summaries (1–20)

### 1. Backend architecture and critical routes
**Status:** Strong composition; large surface.  
Routers mounted from `backend/src/app/api/routes/*` via `main.py` (~226 routes). Clear Settings + DI + provider registry. OpenAPI always enabled. Sync SQLAlchemy in async routes is an intentional scale tradeoff.

### 2. Frontend workflows and navigation
**Status:** Paper-first IA present; mobile nav still elevates legacy paths.  
Next.js 15 / React 19 App Router. Shell banners and NFA copy are pervasive. Auth is client-only (no `middleware.ts`).

### 3. Authentication and authorization
**Status:** Solid JWT + refresh rotation + RBAC; gaps on compute endpoints.  
HS256 access JWT, hashed refresh with rotation/reuse kill, org-scoped `TenantContext`, OWNER/TRADER/VIEWER. Unauthenticated `/tools`, `/risk/*`, `/strategies/evaluate` remain open.

### 4. Provider fallbacks
**Status:** Visible flags; some silent success paths.  
Factory/registry expose `using_fallback` / `is_mock`. `PROVIDER_MODE` gates market/Qdrant more strictly than OpenAI (key presence alone selects live LLM/embeddings).

### 5. Qdrant and knowledge retrieval
**Status:** Staging healthy; corruption/split-brain risks remain.  
Tenant filters + payload indexes present. Upsert failure can fall back to in-memory while Postgres commits; mock embeddings can pollute a live collection if OpenAI fails during ingest.

### 6. Market-data freshness and degraded behavior
**Status:** Metadata present; enforcement uneven (AT-007 still open).  
Ticker staleness often ineffective (timestamp ≈ now). Market watcher can still emit candidates on stale/fallback bars. Agent soft-penalizes confidence; risk engine has no freshness gate.

### 7. Portfolio and performance calculations
**Status:** Deterministic Decimal core; dual-lane caveats.  
`PerformanceCalculator` and paper portfolio services are tested. Proposal-flow PnL ignores fees; open validation unrealized PnL omitted by design.

### 8. Paper trade lifecycle
**Status:** Two lanes — validation bot (stronger) vs proposal→paper order (weaker).  
Proposal orders fill immediately at request price; positions store SL/TP without auto-enforcement. Validation bot enforces SL/TP/runner on ticks.

### 9. Validation sessions and observations
**Status:** Record-only, confirmation-gated; good isolation from execution.  
Manual outcomes may diverge from automated `PaperTrade` PnL (learning bias risk).

### 10. Coaching, learning analytics, lessons, strategy quality
**Status:** Deterministic / non-LLM authority; lesson accept never auto-promotes rules.  
Narrative path uses external prompts + guardrails. Residual cost/noise from unconstrained usage-node LLM call.

### 11. Human-versus-system comparison
**Status:** Coaching-grade heuristics; explicitly not execution authority.  
Some stop-behavior flags hardcoded false.

### 12. Stop-loss, take-profit, runner, leverage, sizing
**Status:** Enforced in validation bot; advisory/manual on proposal-flow positions.  
Zero stop distance can fail-open to tiny size (`0.001`) in validation sizing. Order size not bound to proposal risk-checked size.

### 13. Exchange-demo safety
**Status:** Strong fail-closed.  
Host allowlist, production host denylist, `trade_live` tombstone, withdraw/transfer scope refused, staging/prod forbid `enable_real_trading`. Demo may mutate BloFin **demo** only — accepted for `paper_exchange_demo`.

### 14. Audit logs and observability
**Status:** Rich audit taxonomy + structlog; weak metrics.  
No Prometheus/OTel. Audit `commit` mid-request can split transactions. Audit API scoped to acting user only.

### 15. Error handling, retries, idempotency
**Status:** Order idempotency keys exist; races and silent swallows remain.  
Billing webhook dedupe good. Idempotency race can surface as 500. Limited retries on OpenAI/Binance.

### 16. Security and secret handling
**Status:** Env-driven secrets; staging blueprint uses `sync: false`.  
No secrets found in tracked source. Risks: XFF + `forwarded-allow-ips=*`, staging in-memory rate-limit fallback, always-on `/docs`, sessionStorage access tokens, missing CSP/headers.

### 17. CI, deployment, rollback, recovery
**Status:** Six CI jobs; staging Render + Vercel.  
Gaps: no mypy in CI, no dependency/secret scan, actions not SHA-pinned, manual rollback, backup/restore RPO/RTO UNKNOWN.

### 18. Performance and scalability risks
**Status:** MVP-scale.  
Blocking sync HTTP/DB in async routes; in-memory fallbacks break multi-replica correctness (rate limit, denylist, vector fallback, market cache).

### 19. Missing tests
**Status:** Broad unit coverage; E2E thin by default.  
~86 backend test files, ~74 frontend unit files. Default Playwright is mostly API smoke; authenticated browser paths opt-in. Approval/refusal UI E2E still AT-008.

### 20. Paper-evaluation readiness
**Status:** Deterministic CI harness ready; scored live-LLM eval not ready.  
`evaluation/` scripts gate CI at 100% pass with mocks. Suitable for safety smoke, not for promoting strategies to live capital.

---

## Classified findings

For each finding: **Evidence · Risk · Affected files · Recommended fix · Validation criteria · Recommended model**

### Critical

#### AT010-C1 — Unauthenticated `/tools` API
- **Evidence:** Staging `GET /tools` → 200 without auth. `tools.py` L22–29 has no `TenantDep`.
- **Risk:** Abuse of market/indicator/RAG/risk tools; cost/data leakage; reconnaissance.
- **Affected files:** `backend/src/app/api/routes/tools.py`, `backend/src/app/main.py`, `backend/src/app/tools/registry.py`
- **Recommended fix:** Require `TenantDep`/`TraderDep`; bind org/user from tenant; never accept client-supplied tenant IDs for mutations.
- **Validation:** Unauth → 401/403; authed happy path; no live trading.
- **Recommended model:** Composer 2.5 (impl) + Grok 4.5 (review)

#### AT010-C2 — Paper order without fresh risk / missing `risk_result`
- **Evidence:** `execution_service.py` L103–116: BLOCK honored when present; missing `risk_result` only refused for demo mirror, not paper_internal.
- **Risk:** Under-enforced risk at execution time; false confidence that “BLOCK is final.”
- **Affected files:** `backend/src/app/services/execution_service.py`, `execution_eligibility.py`, `services/risk/*`, `agents/nodes.py`
- **Recommended fix:** Re-evaluate `RiskEngine` at place time with `DailyRiskState` + user settings + kill switch; refuse if missing/BLOCK; call eligibility inside execution.
- **Validation:** Unit tests: missing risk_result → refuse; kill switch BLOCK; settings limits apply.
- **Recommended model:** Grok 4.5

#### AT010-C3 — Mock embeddings can write into live Qdrant
- **Evidence:** Embeddings fallback to hash-mock vectors; RAG ingest does not reject `fallback_used` before upsert.
- **Risk:** Index pollution; silent bad semantic search on staging/prod.
- **Affected files:** `backend/src/app/providers/embeddings.py`, `backend/src/app/services/rag_service.py`, `backend/src/app/providers/qdrant.py`
- **Recommended fix:** Fail ingest when embeddings `fallback_used` or vector store degraded; never upsert mock vectors to remote Qdrant.
- **Validation:** Force embedding failure → ingest errors; collection unchanged.
- **Recommended model:** Grok 4.5

#### AT010-C4 — Qdrant/Postgres upsert split-brain
- **Evidence:** Vector upsert failure falls back to in-memory while Postgres chunk commit can still succeed.
- **Risk:** DB claims document ingested; Qdrant misses it (or reverse inconsistency across workers).
- **Affected files:** `backend/src/app/providers/qdrant.py`, `backend/src/app/services/rag_service.py`
- **Recommended fix:** Transactional outbox or fail the whole ingest; surface degraded status to API.
- **Validation:** Simulated Qdrant down → ingest fails closed (or queued) with no silent success.
- **Recommended model:** Grok 4.5

### High

#### AT010-H1 — Unauthenticated `/risk/*` and `/strategies/evaluate`
- **Evidence:** Staging `POST /risk/check` reachable without auth (422 on empty body).
- **Risk:** Compute abuse; strategy/risk reconnaissance.
- **Affected files:** `backend/src/app/api/routes/risk.py`, strategy evaluate routes
- **Recommended fix:** Auth + rate limits.
- **Validation:** Unauth → 401; authed OK.
- **Recommended model:** Composer 2.5

#### AT010-H2 — Proposal-flow SL/TP not auto-enforced; size/price not bound to proposal
- **Evidence:** Positions store SL/TP; close is manual with caller price; order size from request.
- **Risk:** Paper PnL/discipline metrics misleading; sizing bypass of prior risk check.
- **Affected files:** `execution_service.py`, `position_service.py`
- **Recommended fix:** Bind size/price to proposal (or re-risk); document advisory-only OR add monitor worker.
- **Validation:** Oversize/mismatch order rejected.
- **Recommended model:** Grok 4.5

#### AT010-H3 — Kill switch not wired end-to-end
- **Evidence:** Risk rule exists; agent hardcodes `kill_switch_active=False`; frontend kill switch is local React state.
- **Risk:** Operators believe trading is halted when backend paths continue.
- **Affected files:** `frontend/src/contexts/AppContext.tsx`, `KillSwitchButton.tsx`, `services/risk/rules.py`, `agents/nodes.py`
- **Recommended fix:** Persist org/user kill switch; enforce in `RiskEngine` + execution; UI reads/writes API.
- **Validation:** Toggle → paper place refused; UI reflects server state.
- **Recommended model:** Grok 4.5

#### AT010-H4 — Market freshness soft-only (watcher/agent/ticker)
- **Evidence:** Ticker timestamp≈now; watcher still detects on stale; historical bars force `is_stale=False`.
- **Risk:** Decisions/alerts on degraded data.
- **Affected files:** `providers/market_data.py`, `market_watcher_service.py`, `historical_candle_service.py`, `agents/nodes.py`
- **Recommended fix:** Complete AT-007 — hard conservative mode for stale/fallback/mock on decision paths.
- **Validation:** Unit/property tests for every consumer.
- **Recommended model:** Grok 4.5

#### AT010-H5 — `PROVIDER_MODE=mock` does not force mock LLM/embeddings
- **Evidence:** `providers/factory.py` selects OpenAI by key presence.
- **Risk:** Unexpected live API cost/calls when operators believe mock mode.
- **Affected files:** `backend/src/app/providers/factory.py`, `core/config.py`
- **Recommended fix:** Honor `provider_mode` for LLM/embeddings.
- **Validation:** `PROVIDER_MODE=mock` with key set → mock providers only.
- **Recommended model:** Composer 2.5

#### AT010-H6 — Audit mid-transaction commits + weak metrics
- **Evidence:** `AuditService.record` commits immediately; no Prometheus/OTel.
- **Risk:** Partial commits; ops blindness under incident.
- **Affected files:** `audit_service.py`, observability stack
- **Recommended fix:** Flush audit with UoW; add RED metrics + deploy alerts.
- **Validation:** Failed business txn leaves no orphan audit/business split; metrics scrape OK.
- **Recommended model:** GPT-5.4 / Sonnet 4.6

#### AT010-H7 — Client-only auth + access token in sessionStorage
- **Evidence:** No Next middleware; `frontend/src/lib/auth/session.ts`.
- **Risk:** XSS token theft; brief protected UI flash.
- **Affected files:** `frontend/src/lib/auth/session.ts`, `(app)/layout.tsx`
- **Recommended fix:** Edge middleware guard; CSP; prefer httpOnly access strategy where feasible.
- **Validation:** Unauth `/portfolio` redirects before shell content; CSP headers present.
- **Recommended model:** Sonnet 4.6

#### AT010-H8 — Proxy/IP trust + staging in-memory rate-limit fallback
- **Evidence:** `X-Forwarded-For` trusted; uvicorn `--forwarded-allow-ips=*`; staging allows memory fallback.
- **Risk:** Rate-limit bypass; inconsistent multi-replica limits/denylist.
- **Affected files:** `security/rate_limit.py`, `docker/entrypoint.sh`, `render.yaml`
- **Recommended fix:** Trusted proxy CIDRs; Redis-required in staging/prod; align runbook.
- **Validation:** Spoofed XFF ignored; Redis down → fail closed outside local.
- **Recommended model:** GPT-5.4

#### AT010-H9 — CI gaps (mypy, supply chain) + backup/restore UNKNOWN
- **Evidence:** `.github/workflows/ci.yml`; AT-001/AT-004; deployment docs lack verified restore drill.
- **Risk:** Type regressions; compromised deps; unrecoverable data loss.
- **Affected files:** CI workflows, `docs/deployment.md`, runbooks
- **Recommended fix:** Execute AT-001/AT-004; document and drill DB restore with RPO/RTO.
- **Validation:** CI green with mypy+audit; restore drill recorded.
- **Recommended model:** GPT-5.4 / Sonnet 4.6

#### AT010-H10 — Narrative quota not enforced; search opacity on fallback
- **Evidence:** `limit_agent_narrative` unused; knowledge search does not expose vector fallback health.
- **Risk:** Cost overrun; silent degraded RAG answers.
- **Affected files:** `quota_service.py`, `api/routes/knowledge.py`, `schemas/rag.py`
- **Recommended fix:** Wire quota; return `fallback_used` / degraded flags on search responses.
- **Validation:** Quota blocks excess; API shows degraded when fallback active.
- **Recommended model:** Composer 2.5

### Medium

| ID | Finding | Risk | Fix direction | Model |
|----|---------|------|---------------|-------|
| AT010-M1 | Always-on `/docs` + unauth `/providers/status` | Surface disclosure | Gate docs outside local; auth/redact status | Composer 2.5 |
| AT010-M2 | Order idempotency TOCTOU → 500 | Duplicate confusion | Catch IntegrityError → return existing | Composer 2.5 |
| AT010-M3 | Validation size fail-open on zero stop distance | Bad paper trades | BLOCK instead of `0.001` | Grok 4.5 |
| AT010-M4 | Frontend kill-switch / paper banner hardcoding | Misleading ops UX | Bind banners to `/health` truth | Composer 2.5 |
| AT010-M5 | Missing tenant/time indexes on hot tables | Scale latency | Add indexes via Alembic | Composer 2.5 |
| AT010-M6 | No CSP/security headers | Amplifies XSS | Headers in Next/Vercel + API | Sonnet 4.6 |
| AT010-M7 | Dual paper lanes undocumented for operators | Misread metrics | Ops note + UI labels | Sonnet 4.6 |
| AT010-M8 | Default e2e thin on authenticated UI | Regressions slip | Expand Playwright (AT-008+) | Sonnet 4.6 |
| AT010-M9 | Blocking sync I/O in async routes | Event-loop stall | Threadpool or async clients | Grok 4.5 |
| AT010-M10 | Orphan Qdrant points on re-ingest | Stale hits | Delete-by-filter before upsert | Composer 2.5 |
| AT010-M11 | Primary-org-only membership | Wrong tenant context | Org switch API | Sonnet 4.6 |
| AT010-M12 | Risk schema “default deny” vs empty→ALLOW | Spec drift | Align docs/engine | Grok 4.5 |

### Low

| ID | Finding | Fix direction |
|----|---------|---------------|
| AT010-L1 | Password length-only policy | Add complexity/HIBP optional |
| AT010-L2 | Exact-match sidebar active state | Prefix match for nested routes |
| AT010-L3 | Mobile nav not paper-first aligned | Reorder primary tabs |
| AT010-L4 | Score scale differs memory vs Qdrant | Normalize or document |
| AT010-L5 | `.env.production.example` thinner than staging | Align templates |

### Informational

| ID | Finding |
|----|---------|
| AT010-I1 | No live order path found; flipping real trading fails closed in staging/prod |
| AT010-I2 | Demo exchange mutations are intentional under `paper_exchange_demo` |
| AT010-I3 | Coaching/HvsS/strategy-quality correctly disclaim execution authority |
| AT010-I4 | Evaluation harness is deterministic mock CI gate — appropriate for paper MVP |
| AT010-I5 | Staging cold-start latency observed (~30s first health) — ops note |

---

## Paper-evaluation readiness scorecard

| Criterion | Ready? |
|-----------|--------|
| Paper-only defaults | Yes |
| Deterministic risk unit tests | Yes (partial wiring gaps) |
| Portfolio/performance math tests | Yes |
| Staging providers healthy | Yes |
| Semantic search working | Yes (AT-009) |
| Conservative data degradation everywhere | **No** (AT-007 / H4/C3/C4) |
| Kill switch operational | **No** (H3) |
| Authz on all compute surfaces | **No** (C1/H1) |
| Scored LLM eval for promotion | **No** (AT-003) |
| Real-money gates | N/A — design only in companion roadmap |

**Verdict:** Ready to continue **paper evaluation and hardening**. Not ready for sandbox capital or live capital.

---

## Related deliverables

- Risk register: `docs/AT010_risk_register.md`
- Architecture + phased roadmap: `docs/AT010_real_money_safety_roadmap.md`
- Task backlog: `.ai/TASKS.md` (AT-010 DONE; AT-011+)
