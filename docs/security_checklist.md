# Security Checklist

Use this checklist before **staging** or **production** deployment. All items
should pass. Real trading must remain disabled.

## Pre-deploy checklist

| # | Item | How to verify |
|---|------|---------------|
| 1 | **Strong JWT secret** (32+ bytes, random) | `ENVIRONMENT=staging ./scripts/check-env.sh` |
| 2 | **HTTPS-only cookies** | `AUTH_COOKIE_SECURE=true`, `AUTH_REFRESH_COOKIE_ENABLED=true` |
| 3 | **CORS configured to frontend domain** | `CORS_ORIGINS=https://your-app.vercel.app` (exact match) |
| 4 | **Real trading disabled** | `ENABLE_REAL_TRADING=false`, `EXECUTION_MODE=paper` |
| 5 | **No withdrawal keys** | No exchange API keys with withdraw permissions (not used in scaffold) |
| 6 | **No exchange execution** | `./scripts/verify-safety.sh` — exchange provider mock/paper |
| 7 | **Secrets in managed secret store** | Render/Railway/Vercel env — no secrets in git |
| 8 | **Redis required in staging** | `REDIS_URL` points to managed Redis; `RATE_LIMIT_USE_REDIS=true` |
| 9 | **Postgres migrations applied** | `alembic upgrade head` in release command |
| 10 | **Rate limits enabled** | Redis-backed rate limiter active (see startup log `rate_limit_backend=redis`) |
| 11 | **RBAC enabled** | Default roles OWNER/TRADER/VIEWER enforced on mutations |
| 12 | **Audit logs enabled** | `GET /audit/events` returns records after auth actions |
| 13 | **Provider fallback visible** | `GET /providers/status` shows mock/fallback status |
| 14 | **Debug off in production** | `DEBUG=false` when `ENVIRONMENT=production` |

## Cookie mode (cross-domain staging)

When frontend (Vercel) and API (Render) are on different domains:

```bash
AUTH_REFRESH_COOKIE_ENABLED=true
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=none
CORS_ORIGINS=https://your-frontend.vercel.app
NEXT_PUBLIC_AUTH_COOKIE_MODE=true   # frontend
```

SameSite `none` **requires** Secure cookies (HTTPS).

## Post-deploy verification

```bash
# Smoke scripts exit unless SMOKE_PASSWORD (12+ chars) is set; there is no fallback.
# Export it from your secret store first. Never type it inline or commit it.
BASE_URL=https://your-api.example.com ./scripts/staging-smoke.sh
BASE_URL=https://your-api.example.com ./scripts/verify-safety.sh
```

Expected `/health` response includes:

```json
{
  "execution_mode": "paper",
  "real_trading_enabled": false
}
```

## Secrets rotation

| Secret | Rotation impact |
|--------|-----------------|
| `JWT_SECRET` | Invalidates all access tokens immediately |
| Refresh token DB | Logout all users if truncating refresh_tokens |
| `OPENAI_API_KEY` | LLM falls back to mock until updated |

## What this scaffold does NOT do

- Real order placement on exchanges
- Broker connectivity or fund movement
- Automated trading without human approval
- Storage of exchange withdrawal credentials

See [security.md](security.md) for auth flow, RBAC, rate limiting, and redaction details.
