# Staging Deployment (Slice 54)

> **Historical / release-specific record.** Original decisions, procedures and results below are preserved for their stated date/base. They are not current runtime observations or complete MVP acceptance. Read the [current status and pending acceptance](current_status.md), [architecture](architecture.md) and [deployment entry point](deployment.md) first; recheck commit-specific environment, migration and activation values before using older procedures.


Public staging for **AlphaTrade AI** — paper-only execution, no live trading, no live Stripe.
This document records live URLs, Vercel/Render configuration, smoke commands, browser demo flow,
and known gaps after Slice 54 infrastructure validation (baseline commit `adf5aff`).

> **Never commit secrets.** Store credentials only in Render / Vercel / Upstash dashboards.

---

## Live URLs (Slice 49)

| Service | URL | Status (2026-06-22) |
|---------|-----|---------------------|
| **Backend API** | https://alphatrade-api-staging.onrender.com | Live — `environment=staging`, paper mode, CORS OK |
| **Frontend (production alias)** | https://alpha-trade-ai-eight.vercel.app | **Next.js app** — `/login` + `/register` 200, Render API in bundle |
| **Frontend (git-main alias)** | https://alpha-trade-ai-git-main-alphatrade-ai.vercel.app | Same deployment family |
| **Blocked / wrong** | https://alpha-trade-ai.vercel.app | Unrelated Vite placeholder (`Your Project`) — **do not use** |
| **Legacy / wrong** | https://alphatrade-ai.vercel.app | Unrelated static app — **do not use** |

`https://alpha-trade-ai.vercel.app` is owned by another Vercel account and cannot be aliased to this project. Use **`alpha-trade-ai-eight.vercel.app`** for demos until that domain is reclaimed.

---

## Vercel project configuration (alpha-trade-ai)

Verified via Vercel CLI (Slice 48):

| Setting | Value |
|---------|--------|
| Root Directory | `frontend` |
| Framework | Next.js 15 (detected at build) |
| Build Command | `npm run build` |
| Install Command | `npm ci` |
| Output Directory | `.next` (via `frontend/vercel.json`) |
| Production Branch | `main` |

### Frontend environment variables (Production + Preview)

| Variable | Value |
|----------|--------|
| `NEXT_PUBLIC_API_URL` | `https://alphatrade-api-staging.onrender.com` |
| `NEXT_PUBLIC_AUTH_COOKIE_MODE` | `true` |
| `NEXT_PUBLIC_EXECUTION_MODE` | `paper` |
| `NEXT_PUBLIC_PROVIDER_MODE` | `fallback` |
| `NEXT_PUBLIC_APP_NAME` | `AlphaTrade AI` |

Templates: [`frontend/.env.staging.example`](../frontend/.env.staging.example)

Redeploy after changing `NEXT_PUBLIC_*` (build-time vars):

```bash
npx vercel link --yes --project alpha-trade-ai
npx vercel --prod --yes
```

---

## Render backend environment (alphatrade-api-staging)

Apply in Render Dashboard → **Environment** → **Save** → **Manual Deploy**.

| Variable | Required staging value |
|----------|------------------------|
| `ENVIRONMENT` | `staging` |
| `EXECUTION_MODE` | `paper` |
| `ENABLE_REAL_TRADING` | `false` |
| `CORS_ORIGINS` | `https://alpha-trade-ai-eight.vercel.app,https://alpha-trade-ai-alphatrade-ai.vercel.app,https://alpha-trade-ai-git-main-alphatrade-ai.vercel.app` |
| `BILLING_ENABLED` | `false` |
| `PROVIDER_MODE` | `fallback` |
| `ALERT_DELIVERY_ENABLED` | `false` |
| `ALERT_WEBHOOK_ENABLED` | `false` |
| `TELEGRAM_ALERTS_ENABLED` | `false` |
| `EMAIL_PROVIDER` | `mock` |
| `MARKET_WATCHER_ENABLED` | `false` |
| `MARKET_WATCHER_BRIDGE_ENABLED` | `false` |
| `WATCHER_ORCHESTRATION_ENABLED` | `false` |
| `REQUIRE_EMAIL_VERIFIED` | `false` (frictionless demo login) |
| `DEMO_SEED_ENABLED` | `true` (enables owner-only `POST /demo/seed` on staging) |
| `DEMO_SEED_PASSWORD` | Private demo password on Render (min 12 characters; never commit). Sets `demo@alphatrade.ai` on seed. |
| `RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK` | `true` (when Redis URL invalid on staging) |
| `REDIS_URL` | Valid Upstash URL: `rediss://default:<token>@<host>.upstash.io:6379` — **not** a `redis-cli` command (app also normalizes common `redis-cli --tls -u` mistakes) |
| `QDRANT_URL` | Reachable HTTPS endpoint **or** empty (in-memory RAG fallback) |

### BloFin demo exchange (optional — staging validation only)

Keep **`EXCHANGE_MODE=paper_internal`** until you are ready for controlled demo connectivity.
When enabling BloFin demo mirroring on staging:

| Variable | Staging value |
|----------|---------------|
| `EXCHANGE_MODE` | `paper_exchange_demo` (staging only; **never** production) |
| `EXECUTION_MODE` | `paper` (required) |
| `ENABLE_REAL_TRADING` | `false` (required) |
| `BLOFIN_DEMO_ENABLED` | `true` |
| `BLOFIN_DEMO_REST_BASE_URL` | `https://demo-trading-openapi.blofin.com` |
| `BLOFIN_DEMO_WS_URL` | `wss://demo-trading-openapi.blofin.com/ws/public` (optional) |
| `BLOFIN_API_KEY` | Demo API key (**read + trade only**; no withdraw/transfer) |
| `BLOFIN_API_SECRET` | Demo secret (Render secret store only) |
| `BLOFIN_API_PASSPHRASE` | Demo passphrase (Render secret store only) |
| `WORKER_ENABLED` | `false` (default; worker is read-only and never places orders) |
| `TELEGRAM_ALERTS_ENABLED` | `false` (Telegram is outbound-only) |

Safety invariants (enforced at startup):

- Production BloFin hosts (`openapi.blofin.com`, etc.) are **blocked**
- Plain HTTP / non-TLS URLs are **blocked**
- Keys with withdraw or transfer scope **refuse startup**
- Internal paper orders remain the source of truth; demo mirroring is best-effort
- Owner-only **`GET /exchange/status`** returns booleans + provider health only (no keys, URLs, or signed payloads)

Blueprint defaults: [`render.yaml`](../render.yaml) · template: [`.env.staging.example`](../.env.staging.example)

Validate locally before saving platform env:

```bash
ENV_FILE=.env.staging ./scripts/check-env.sh
```

---

## Redeploy

### Backend (Render)

1. Render Dashboard → **alphatrade-api-staging** → branch **`main`**
2. Set env vars from table above (especially `CORS_ORIGINS`, `ENVIRONMENT=staging`)
3. **Manual Deploy** → Deploy latest commit
4. Pre-deploy: `alembic upgrade head` · health path: `/health`

### Frontend (Vercel)

1. Confirm Root Directory = `frontend` (already set)
2. Confirm env vars from table above
3. `npx vercel --prod --yes` from repo root (linked project)
4. Use production alias URL in docs and Render `CORS_ORIGINS`

---

## Live smoke checklist

```bash
# Safety only
BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/verify-safety.sh

# Full auth + chat + cookie mode
FRONTEND_URL=https://alpha-trade-ai-eight.vercel.app \
COOKIE_MODE=true \
ALLOW_DEGRADED_READY=true \
BACKEND_URL=https://alphatrade-api-staging.onrender.com \
./scripts/staging-smoke.sh

# Slice 52 — reseed with Render DEMO_SEED_PASSWORD (no local password)
DEMO_SEED_USE_SERVER_PASSWORD=true \
BACKEND_URL=https://alphatrade-api-staging.onrender.com \
./scripts/seed-demo.sh --api

# Or pass password locally (also sent in body when server env unset)
DEMO_SEED_PASSWORD='your-chosen-demo-password' \
BACKEND_URL=https://alphatrade-api-staging.onrender.com \
./scripts/seed-demo.sh --api

# Validate demo login + data (requires DEMO_SEED_PASSWORD in env — never logged)
./scripts/validate-demo-staging.sh

# BloFin demo exchange (read-only, no orders) — requires EXCHANGE_MODE=paper_exchange_demo
BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/validate-exchange-demo-staging.sh

# Slice 66b — seed path smoke only (no order); see docs/slice_66b_demo_venue_validation.md
./scripts/test-seed-path-staging.sh

# Slice 66b — controlled demo venue order (ONE order per review; new idempotency key required)
# BACKEND_URL=https://alphatrade-api-staging.onrender.com IDEMPOTENCY_KEY=slice66b-demo-limit-NNN ./scripts/run-slice66b-controlled-demo-order.sh

# AI workspace chat safety prompts
./scripts/validate-demo-chat-staging.sh

# Slice 48 extended live smoke
FRONTEND_URL=https://alpha-trade-ai-eight.vercel.app \
BACKEND_URL=https://alphatrade-api-staging.onrender.com \
COOKIE_MODE=true \
./scripts/staging-live-smoke.sh

# Focused smokes
BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/notifications-smoke.sh
BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/market-watcher-smoke.sh
```

**Expected backend invariants:**

- `GET /health` → `execution_mode: paper`, `real_trading_enabled: false`
- `GET /providers/status` → exchange mock/paper-only, billing mock/disabled
- `GET /exchange/status` (owner auth) → booleans only when demo mode configured; no secrets
- Authenticated routes return paper-only safety fields

**Slice 49 live results:**

| Check | Result |
|-------|--------|
| `/health` | OK — `environment=staging`, `execution_mode=paper`, `real_trading_enabled=false` |
| `/health/ready` | OK — `ready=true` |
| `verify-safety.sh` | Passed |
| `staging-live-smoke.sh` | Passed — auth, dashboard, notifications, CORS |
| CORS preflight | OK — HTTP 200 from `alpha-trade-ai-eight.vercel.app` |
| Frontend `/` + `/login` + `/register` | OK on `alpha-trade-ai-eight.vercel.app` (Next.js, title AlphaTrade AI) |
| Production JS API URL | `alphatrade-api-staging.onrender.com` (no `localhost:8000`) |
| Browser register/login | OK — cookie auth; redirects to `/verify-email` (mock email) |
| Browser dashboard | OK — via **Go to dashboard** on verify-email page |
| Auth persistence | OK — session survives page refresh |
| Logout / login again | OK |
| `notifications-smoke.sh` | Passed — `effective_external_enabled=false`, `paper_only=true` |
| `market-watcher-smoke.sh` | Passed — watcher/bridge env off |

---

## Browser demo checklist (staging)

Open **https://alpha-trade-ai-eight.vercel.app**

1. Log in as **`demo@alphatrade.ai`** (after running demo seed) or register a new user
2. With `REQUIRE_EMAIL_VERIFIED=false`, login/register goes directly to the dashboard
3. Confirm **Paper mode active** and **Real trading disabled** badges
4. Dashboard → Today's discipline, workflow stepper
5. Risk Settings → limits and save
6. Strategy Lab → backtest / paper eligibility
7. Paper Validation → scan/tick (simulated)
8. Market Watcher → manual scan (env off = safe status)
9. Alerts → delivery summary; external skipped
10. Notifications Settings → webhook/Telegram off; test notification safe
11. Lessons → pending / accept flow
12. AI Workspace → read-only question; mutation requires confirmation
13. Confirm no real trade path

**CORS / auth cookie checks (Slice 49 — verified):**

- OPTIONS preflight returns 200/204 (not 400)
- Register/login from browser works (httpOnly refresh cookie set)
- Authenticated requests persist across page refresh
- Logout clears session; login again works
- No mixed content; API URL is `https://alphatrade-api-staging.onrender.com` (not localhost)

---

## Notification safety (external delivery off)

- Preferences show webhook/Telegram disabled
- Test notification skips external with clear reason
- No Telegram/webhook calls when `ALERT_DELIVERY_ENABLED=false`
- Alerts page shows skipped/disabled counts; `paper_only: true`

---

## Known staging limitations (Slice 54)

| Gap | Impact | Fix |
|-----|--------|-----|
| `alpha-trade-ai.vercel.app` blocked | Intended short URL unavailable | Use `alpha-trade-ai-eight.vercel.app`; reclaim domain separately |
| Mock email verification UX | Optional on staging when `REQUIRE_EMAIL_VERIFIED=false` | Login/register skip verify-email; production still enforces verification |
| Qdrant unreachable | In-memory vector fallback; demo AI Workspace still works | Fix `QDRANT_URL`/API key or accept staging fallback |
| Legacy bootstrap seed owner | `seed-bootstrap-*@example.com` may remain (FK constraints) | Harmless; cleanup skips safely on reseed |
| Bootstrap org name collision | `Demo Seed Bootstrap` org name is unique in DB | `seed-demo.sh` uses timestamped org names for new bootstrap owners |
| Preview deploy SSO | Automated preview checks blocked | Use production alias for smoke |
| Demo data | Run seed after deploy | `DEMO_SEED_USE_SERVER_PASSWORD=true ./scripts/seed-demo.sh --api` |
| Local demo validation | Scripts need `export DEMO_SEED_PASSWORD` matching Render | Store in gitignored `docs/staging_ops.local.md` only |

**Real trading remains disabled.** All execution is paper-only.

---

## Notification provider defaults

| Provider | Default | Notes |
|----------|---------|-------|
| In-app alerts | Enabled | Always safe |
| Webhook | Off | `ALERT_WEBHOOK_ENABLED=false` |
| Telegram | Off | `TELEGRAM_ALERTS_ENABLED=false` |
| Email | Mock | `EMAIL_PROVIDER=mock` |
| External master | Off | `ALERT_DELIVERY_ENABLED=false` |

See [notifications.md](notifications.md) and [alerts.md](alerts.md).

---

## Related docs

- [staging_deployment_runbook.md](staging_deployment_runbook.md) — full provisioning steps
- [staging_deployment_checklist.md](staging_deployment_checklist.md) — env sign-off
- [staging_execution_checklist.md](staging_execution_checklist.md) — manual click order
- [deployment_command_pack.md](deployment_command_pack.md) — copy-paste commands
- [demo_script.md](demo_script.md) — portfolio walkthrough
- [limitations_roadmap.md](limitations_roadmap.md) — scope boundaries

## Render auto-deploy source (2026-10-24)

- `alphatrade-api-staging` (`srv-d8fvbcd7vvec739mc060`) and
  `alphatrade-paper-worker-staging` (`srv-daqej7o473hc73fsc1k0`) track
  `Fejjii-labs/AlphaTrade-AI` branch `main` with auto-deploy on every commit.
- Push-triggered deploys need Render's GitHub app installed on the
  `Fejjii-labs` organization with access to this repo. Without it, Render gets
  no push events and only API/manual deploys run (trigger `api`); because the
  repo is public, manual deploys still succeed, which hides the gap.
- Verify after a merge: the newest deploy on both services should show trigger
  `new_commit` for the merge SHA. If it shows only `api`, check the app install.
- `ai-alphatrade-api-staging` (`srv-d8fvblmrnols73d2j1j0`) is a legacy
  duplicate with auto-deploy off and incomplete env; do not deploy it.
