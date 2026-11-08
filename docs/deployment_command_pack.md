# Deployment Command Pack

> **Historical / release-specific record.** Original decisions, procedures and results below are preserved for their stated date/base. They are not current runtime observations or complete MVP acceptance. Read the [current status and pending acceptance](current_status.md), [architecture](architecture.md) and [deployment entry point](deployment.md) first; recheck commit-specific environment, migration and activation values before using older procedures.


Copy-paste commands for staging prep and post-deploy verification.  
Replace placeholders — **never paste real secrets into committed files**.

| Placeholder | Example |
|-------------|---------|
| `<BACKEND_URL>` | `https://alphatrade-api-staging.onrender.com` |
| `<FRONTEND_URL>` | `https://alpha-trade-ai-eight.vercel.app` (Vercel Root Directory = `frontend`) |

---

## 1. Generate JWT secret

```bash
openssl rand -base64 32
```

Store in local `.env.staging` or `docs/staging_deployment_worksheet.local.md` only.

---

## 2. Validate staging env locally

```bash
cp .env.staging.example .env.staging
# Edit .env.staging with placeholders from docs/staging_deployment_worksheet.template.md

ENV_FILE=.env.staging ./scripts/check-env.sh
```

---

## 3. Run migrations

Against **local** Postgres (Docker):

```bash
docker compose up -d postgres
DATABASE_URL='postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade' \
  ./scripts/run-migrations.sh
```

Against **staging** Postgres (from laptop, after Render DB exists):

```bash
DATABASE_URL='postgresql+psycopg://USER:PASS@HOST:5432/DB?sslmode=require' \
  ./scripts/run-migrations.sh
```

Render pre-deploy (on platform):

```bash
alembic upgrade head
```

---

## 4. Post-deploy smoke gate (mandatory — AT-005)

```bash
BASE_URL=<BACKEND_URL> \
FRONTEND_URL=<FRONTEND_URL> \
COOKIE_MODE=true \
./scripts/post-deploy-smoke-gate.sh
```

Exit `0` = keep deploy. Exit `1` = rollback
([deploy_rollback_runbook.md](deploy_rollback_runbook.md)). Exit `2` = fix operator inputs.

Safety-only profile:

```bash
GATE_PROFILE=safety BASE_URL=<BACKEND_URL> ./scripts/post-deploy-smoke-gate.sh
```

Expect via `verify-safety.sh` (always run by the gate): `execution_mode=paper`,
`real_trading_enabled=false`, exchange mock/paper-only.

---

## 5. Staging smoke (backend + optional frontend CORS)

Individual scripts (also invoked by the standard gate):

```bash
# Smoke scripts exit unless SMOKE_PASSWORD (12+ chars) is set; there is no fallback.
# Export it from your secret store first. Never type it inline or commit it.

BASE_URL=<BACKEND_URL> ./scripts/verify-safety.sh

FRONTEND_URL=<FRONTEND_URL> \
COOKIE_MODE=true \
ALLOW_DEGRADED_READY=true \
BASE_URL=<BACKEND_URL> \
./scripts/staging-smoke.sh

# Slice 47 extended live smoke (dashboard, risk, notifications, market watcher, CORS)
FRONTEND_URL=https://alpha-trade-ai-eight.vercel.app \
COOKIE_MODE=true \
BACKEND_URL=https://alphatrade-api-staging.onrender.com \
./scripts/staging-live-smoke.sh
```

### Demo seed (Slice 50 — synthetic paper-only tenant)

Render shell or local with staging `DATABASE_URL`:

```bash
cd backend
DEMO_SEED_PASSWORD='your-chosen-demo-password' uv run python scripts/seed_demo.py
```

Owner API seed (requires `DEMO_SEED_ENABLED=true` and deployed backend):

```bash
DEMO_SEED_PASSWORD='...' BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/seed-demo.sh --api
```

Demo login: `demo@alphatrade.ai` · Frontend: https://alpha-trade-ai-eight.vercel.app

### Slice 70 — Telegram manual alert delivery (guarded)

Doc: [slice_70_telegram_alert_delivery_validation.md](slice_70_telegram_alert_delivery_validation.md)

```bash
# Preflight only (recommended)
DRY_RUN=true ALERT_ID=a1000061-0000-4000-8000-000000000061 \
  STAGING_DEMO_PASSWORD='...' \
  BACKEND_URL=https://alphatrade-api-staging.onrender.com \
  ./scripts/validate-telegram-delivery-staging.sh

# Read-only browser smoke (no send)
STAGING_DEMO_PASSWORD='...' ./scripts/browser-smoke-alerts-staging.sh
```

### Slice 71 — Automatic Telegram delivery preview (read-only)

```bash
DRY_RUN=true PREVIEW_LIMIT=5 STAGING_DEMO_PASSWORD='...' \
  BACKEND_URL=https://alphatrade-api-staging.onrender.com \
  ./scripts/preview-telegram-delivery-staging.sh
```

Live automatic delivery remains disabled (`AUTOMATIC_TELEGRAM_DELIVERY_ENABLED=false`).

Local Docker (cookie mode):

```bash
COOKIE_MODE=true FRONTEND_URL=http://localhost:3000 ./scripts/staging-smoke.sh
```

---

## 6. Test provider status

```bash
curl -fsS <BACKEND_URL>/providers/status | python3 -m json.tool
```

Check: `exchange` is mock/paper; `billing` is mock/disabled; `fallback_used` where expected.

---

## 7. Test health and readiness

```bash
curl -fsS <BACKEND_URL>/health | python3 -m json.tool
curl -fsS <BACKEND_URL>/health/ready | python3 -m json.tool
```

Readiness may be **degraded** if Qdrant/OpenAI are down — use `ALLOW_DEGRADED_READY=true` in smoke.

---

## 8. Stop local Docker stack

```bash
docker compose down
```

Remove volumes (destructive):

```bash
CONFIRM=yes ./scripts/docker-reset-db.sh
docker compose down -v
```

---

## 9. Rebuild Docker stack

```bash
docker compose up --build -d
./scripts/docker-validate.sh
```

---

## 10. Run E2E locally

```bash
docker compose up --build -d
# Smoke scripts exit unless SMOKE_PASSWORD (12+ chars) is set; there is no fallback.
# Export it from your secret store first. Never type it inline or commit it.
./scripts/e2e-smoke.sh

cd frontend
npm run test:e2e
```

Backend-only smoke (no Playwright browser tour):

```bash
# needs SMOKE_PASSWORD exported, as above
BASE_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 ./scripts/e2e-smoke.sh
```

---

## CLI availability (optional later)

Check without installing:

```bash
for cmd in render vercel upstash gh docker openssl; do
  command -v "$cmd" >/dev/null && echo "$cmd: OK" || echo "$cmd: MISSING"
done
```

Cloud deploy can use web dashboards if CLIs are missing.

---

## Related docs

- [pre_deployment_checklist.md](pre_deployment_checklist.md)
- [staging_execution_checklist.md](staging_execution_checklist.md)
- [staging_live_deployment_notes.md](staging_live_deployment_notes.md)
