# Staging Deployment Runbook

> **Historical / release-specific record.** Original decisions, procedures and results below are preserved for their stated date/base. They are not current runtime observations or complete MVP acceptance. Read the [current status and pending acceptance](current_status.md), [architecture](architecture.md) and [deployment entry point](deployment.md) first; recheck commit-specific environment, migration and activation values before using older procedures.


Step-by-step guide to deploy **AlphaTrade AI** to a public staging URL with **paper-only**
execution, **fallback/mock providers**, and **no live Stripe or exchange orders**.

## Recommended path (Slice 29B)

| Layer | Provider | Why |
|-------|----------|-----|
| **Frontend** | [Vercel](https://vercel.com) | Native Next.js 15, HTTPS, preview deploys, simple env UI |
| **Backend API** | [Render](https://render.com) | Docker deploy from `backend/Dockerfile`, release command for migrations, managed Postgres/Redis add-ons |
| **Postgres** | Render Postgres (or [Neon](https://neon.tech)) | Alembic migrations; SSL connection string |
| **Redis** | [Upstash](https://upstash.com) or Render Redis | Rate limits + JWT denylist; serverless-friendly |
| **Qdrant** | [Qdrant Cloud](https://cloud.qdrant.io) free tier **or** degraded fallback | Hosted URL satisfies startup checks; app falls back to in-memory vectors if cluster is down |

**Why Render over Railway for this repo:** The backend already ships a production `Dockerfile` and entrypoint migrations—Render’s Docker web service + `preDeployCommand` maps cleanly without monorepo plugin guesswork. Railway remains a valid alternative (see [railway_deployment.md](railway_deployment.md)).

**First staging without Qdrant Cloud:** Hosted `QDRANT_URL` is **required** on
staging and production (AT-013 fail-closed). Empty `QDRANT_URL` is rejected at
startup. Use a Qdrant Cloud cluster (or equivalent HTTPS endpoint) before deploy.

---

## Before you start

1. Fork/clone [Fejjii/AlphaTrade-AI](https://github.com/Fejjii/AlphaTrade-AI).
2. Copy templates (do **not** commit secrets):
   - `.env.staging.example` → local ` .env.staging` (gitignored)
   - `frontend/.env.staging.example` → note values for Vercel UI
3. Generate secrets:
   - `JWT_SECRET`: 32+ random bytes (`openssl rand -base64 32`)
4. Run local validation:

```bash
ENV_FILE=.env.staging ./scripts/check-env.sh
docker compose up --build
./scripts/docker-validate.sh
./scripts/staging-smoke.sh
```

---

## 1. Create managed Postgres

### Render Postgres

1. Render Dashboard → **New** → **PostgreSQL**.
2. Name: `alphatrade-staging-db`, region near backend.
3. Copy **External Database URL** (use `postgresql+psycopg://` form if the UI gives `postgres://`, adjust driver prefix).
4. Save as `DATABASE_URL` (secret).

### Neon (alternative)

1. Create project + database.
2. Enable SSL; copy connection string.
3. Set `DATABASE_URL=postgresql+psycopg://...?sslmode=require`.

---

## 2. Create managed Redis

### Upstash (recommended for solo staging)

1. Create Redis database (REST or classic TCP).
2. Copy `rediss://` or `redis://` URL → `REDIS_URL`.
3. Ensure TLS URL if required by provider.

### Render Redis

1. **New** → **Redis** → link to same region as API.
2. Copy internal/external URL → `REDIS_URL`.

---

## 3. Create Qdrant (optional but recommended)

1. [Qdrant Cloud](https://cloud.qdrant.io) → cluster → copy HTTPS URL.
2. Create API key if required → `QDRANT_API_KEY`.
3. Set `QDRANT_URL=https://....qdrant.io`.

If the cluster is unreachable, the API still starts; RAG uses in-memory fallback. Smoke tests can allow degraded readiness (see §10).

---

## 4. Create backend service (Render)

1. **New** → **Web Service** → connect GitHub repo.
2. **Root directory:** leave repo root OR set Docker context (see [render.yaml](../render.yaml)).
3. **Environment:** Docker  
   **Dockerfile path:** `backend/Dockerfile`  
   **Docker context:** `backend`
4. **Instance type:** Starter is enough for staging.
5. **Health check path:** `/health`
6. **Pre-deploy / release command:**

```bash
alembic upgrade head
```

(Also runs on container start via `docker/entrypoint.sh`—prefer release command to migrate before traffic.)

---

## 5. Set backend environment variables

Paste from [`.env.staging.example`](../.env.staging.example). Required staging values:

| Variable | Value |
|----------|--------|
| `ENVIRONMENT` | `staging` |
| `EXECUTION_MODE` | `paper` |
| `ENABLE_REAL_TRADING` | `false` |
| `DATABASE_URL` | Managed Postgres URL |
| `REDIS_URL` | Managed Redis URL |
| `QDRANT_URL` | Qdrant Cloud HTTPS URL |
| `JWT_SECRET` | 32+ byte secret |
| `CORS_ORIGINS` | `https://YOUR-APP.vercel.app` (exact, no trailing slash) |
| `AUTH_REFRESH_COOKIE_ENABLED` | `true` |
| `AUTH_COOKIE_SECURE` | `true` |
| `AUTH_COOKIE_SAMESITE` | `none` |
| `AUTH_OMIT_REFRESH_FROM_BODY` | `true` |
| `PROVIDER_MODE` | `fallback` |
| `BILLING_ENABLED` | `false` |
| `MARKET_DATA_ENABLED` | `true` |
| `RATE_LIMIT_USE_REDIS` | `true` |
| `RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK` | `false` |
| `OPENAI_API_KEY` | Optional — enables `openai-llm` / `openai-embeddings` |
| `QDRANT_API_KEY` | Required for Qdrant Cloud / authenticated clusters |
| `EMBEDDINGS_MODEL` | Default `text-embedding-3-small` (1536-d when key set) |
| `EMBEDDINGS_DIMENSIONS` | Optional override; recreate collection after changing |

Validate locally before save:

```bash
ENV_FILE=.env.staging ./scripts/check-env.sh
./scripts/provider-validation-smoke.sh local
```

Voice (optional): set `AGENT_REALTIME_ENABLED=true`, `AGENT_VOICE_REASONING_MODEL=gpt-6-luna`
and `AGENT_REALTIME_LIMITS_FILE=/app/src/app/agent_voice/receipts/staging_voice_limits_2026-11.json`.
The receipt prices assume OpenAI's **global endpoint** (`api.openai.com`, standard tier, no
data-residency uplift); the voice sockets refuse other endpoints, so a regional/data-residency
endpoint would need a new receipt priced for it.

After enabling OpenAI on a cluster that previously used mock embeddings, recreate
`alphatrade_knowledge` and reingest (see [rag_system.md](rag_system.md)).

Deploy backend; note **HTTPS URL** → `https://alphatrade-api.onrender.com` (example).

---

## 6. Run Alembic migrations

**Automatic:** Render release command or container entrypoint.

**Manual** (against staging DB from laptop):

```bash
cd backend
DATABASE_URL='postgresql+psycopg://...' uv run alembic upgrade head
# or
DATABASE_URL='postgresql+psycopg://...' ../scripts/run-migrations.sh
```

Verify: backend logs show `Running Alembic migrations...` then `Application startup complete`.

---

## 7. Deploy frontend to Vercel

1. [Vercel](https://vercel.com) → **Add New Project** → import repo.
2. **Root Directory:** `frontend`
3. Framework: Next.js (auto)
4. **Environment variables** (Production + Preview):

| Variable | Value |
|----------|--------|
| `NEXT_PUBLIC_API_URL` | Backend HTTPS URL |
| `NEXT_PUBLIC_AUTH_COOKIE_MODE` | `true` |
| `NEXT_PUBLIC_EXECUTION_MODE` | `paper` |
| `NEXT_PUBLIC_PROVIDER_MODE` | `fallback` |

5. Deploy → copy deployment URL → `https://your-app.vercel.app`

See [frontend/vercel.json](../frontend/vercel.json) for optional project hints.

---

## 8. Configure CORS

1. Set backend `CORS_ORIGINS` to the **exact** Vercel URL (scheme + host, no path).
2. Multiple previews: comma-separate origins `https://app.vercel.app,https://app-git-main.vercel.app`
3. **Redeploy backend** after changing CORS.

Test from laptop:

```bash
FRONTEND_URL=https://your-app.vercel.app \
BASE_URL=https://your-api.onrender.com \
./scripts/staging-smoke.sh
```

---

## 9. Configure cookie auth (cross-domain)

Vercel (frontend) and Render (API) are different sites. Required settings:

| Side | Setting |
|------|---------|
| Backend | `AUTH_REFRESH_COOKIE_ENABLED=true` |
| Backend | `AUTH_COOKIE_SECURE=true` |
| Backend | `AUTH_COOKIE_SAMESITE=none` |
| Backend | `CORS_ORIGINS` = Vercel URL |
| Frontend | `NEXT_PUBLIC_AUTH_COOKIE_MODE=true` |

**Browser flow:** Access token in `sessionStorage`; refresh token in **httpOnly** cookie on API host (`alphatrade_refresh`, path `/auth`). Frontend must call API with `credentials: 'include'`.

**Smoke with cookies:**

```bash
COOKIE_MODE=true BASE_URL=https://your-api.onrender.com ./scripts/staging-smoke.sh
```

---

## 10. Run smoke tests

**Mandatory post-deploy gate (AT-005)** — fail-closed; exit `1` is a rollback trigger
([deploy_rollback_runbook.md](deploy_rollback_runbook.md)):

```bash
BASE_URL=https://your-api.onrender.com \
FRONTEND_URL=https://your-app.vercel.app \
COOKIE_MODE=true \
./scripts/post-deploy-smoke-gate.sh
```

Optional individual scripts:

```bash
# Backend invariants + auth + chat
BASE_URL=https://your-api.onrender.com ./scripts/staging-smoke.sh

# Safety only
BASE_URL=https://your-api.onrender.com ./scripts/verify-safety.sh

# Slice 70 — Telegram manual alert delivery (guarded; see slice_70 doc)
DRY_RUN=true ALERT_ID=<uuid> BACKEND_URL=https://alphatrade-api-staging.onrender.com \
  STAGING_DEMO_PASSWORD='...' ./scripts/validate-telegram-delivery-staging.sh
STAGING_DEMO_PASSWORD='...' ./scripts/browser-smoke-alerts-staging.sh

# Optional CORS preflight
FRONTEND_URL=https://your-app.vercel.app \
BASE_URL=https://your-api.onrender.com \
./scripts/staging-smoke.sh

# Allow degraded readiness (Qdrant/OpenAI optional)
ALLOW_DEGRADED_READY=true BASE_URL=... ./scripts/staging-smoke.sh
```

**Manual browser checklist:**

- [ ] Login → dashboard → logout
- [ ] Paper mode banner
- [ ] Provider status: exchange paper-only, billing disabled
- [ ] Workspace chat returns structured response
- [ ] No refresh token in `sessionStorage` (cookie mode)

---

## 11. Verify safety invariants

| Check | Command / expected |
|-------|------------------|
| Paper mode | `GET /health` → `execution_mode: paper` |
| Real trading off | `real_trading_enabled: false` |
| Exchange provider | `GET /providers/status` → exchange mock/paper |
| Billing | `BILLING_ENABLED=false` in env |
| Startup | No `deployment safety check failed` in logs |
| Env file | `ENV_FILE=.env.staging ./scripts/check-env.sh` → OK |

```bash
BASE_URL=https://your-api.onrender.com ./scripts/verify-safety.sh
```

---

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---------|--------|-----|
| Service crashes on boot | `deployment_safety` failed | Run `./scripts/check-env.sh`; fix localhost URLs, JWT, cookies, CORS |
| `jwt_secret must be at least 32 bytes` | Short secret | Regenerate 32+ byte secret |
| `cors_origins must use HTTPS` | `http://` origin | Use `https://` Vercel URL |
| Login 401 after Vercel deploy | CORS or cookie SameSite | `CORS_ORIGINS`, `AUTH_COOKIE_SAMESITE=none`, redeploy both |
| Refresh fails cross-domain | Cookie mode off on frontend | `NEXT_PUBLIC_AUTH_COOKIE_MODE=true` + rebuild |
| `/health/ready` degraded | Qdrant/OpenAI down | Expected with fallback; use `ALLOW_DEGRADED_READY=true` or fix Qdrant URL |
| Migrations fail | Wrong `DATABASE_URL` | SSL params; user permissions |
| CORS error in browser | Origin mismatch | Exact Vercel URL in `CORS_ORIGINS` |
| 429 on chat | Quota / rate limit | Check usage; Redis connectivity |
| Binance rate limit | Public API | Mock fallback; `PROVIDER_MODE=fallback` |

---

### Stale manual BloFin demo entry on operator hold

The demo worker retries an aged, unresolved manual demo entry with backoff
(15m doubling to 6h). After 8 attempts it writes one `stale_entry_operator_hold`
audit event (`operation=stale_manual_demo_entry_reconciliation`) and stops; the
entry keeps blocking new demo entries. To clear it, the owner inspects the BloFin
demo account (no position, no pending orders or TP/SL, exit fills present) and
then calls `POST /execution/manual-demo/{command_id}/resolve` (owner-only,
manual-demo API arm on). A successful resolve writes the lifecycle resolution and
releases the reservation; the hold row is never deleted.

## 13. Rollback

Full procedure (triggers, steps, verification, failure handling):
**[deploy_rollback_runbook.md](deploy_rollback_runbook.md)** (AT-005).

| Layer | Action |
|-------|--------|
| Backend | Render → rollback to last known good deploy |
| Frontend | Vercel Instant Rollback |
| Database | `alembic downgrade -1` only if reversible; else restore snapshot — see [backup_restore_runbook.md](backup_restore_runbook.md) (AT-019) |
| Verify | `./scripts/post-deploy-smoke-gate.sh` must exit `0` |

Data backup/restore RPO/RTO, inventory, and drill evidence: **AT-019** docs linked below.

---

## Related docs

- [deploy_rollback_runbook.md](deploy_rollback_runbook.md) — rollback triggers/steps + smoke gate (AT-005)
- [backup_restore_runbook.md](backup_restore_runbook.md) — RPO/RTO + restore procedures (AT-019)
- [backup_inventory.md](backup_inventory.md) — what to back up (AT-019)
- [backup_restore_drill_plan.md](backup_restore_drill_plan.md) / [backup_restore_drill_evidence.md](backup_restore_drill_evidence.md) — drill plan + sanitized evidence
- [staging_deployment_checklist.md](staging_deployment_checklist.md) — copy-paste env checklist
- [slice_70_telegram_alert_delivery_validation.md](slice_70_telegram_alert_delivery_validation.md) — manual Telegram delivery validation
- [deployment.md](deployment.md) — architecture and monitoring
- [railway_deployment.md](railway_deployment.md) — Railway alternative
- [security_checklist.md](security_checklist.md)
