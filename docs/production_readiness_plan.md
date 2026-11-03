# Production readiness plan (draft, 2026-11-03)

Status: **prepared, not deployed.** No Render/Vercel resources were created, no
env vars changed, nothing merged. Production creation needs Sofien's written go
(it costs money). Real trading stays off in every option.

## 1. Inventory (read via Render MCP / Vercel MCP on 2026-11-03)

| Resource | ID | Plan | Region | List price/mo |
|---|---|---|---|---|
| alphatrade-api-staging (web) | srv-d8fvbcd7vvec739mc060 | starter (0.5 CPU / 512 MB) | frankfurt | $7 |
| alphatrade-paper-worker-staging (worker) | srv-daqej7o473hc73fsc1k0 | starter | frankfurt | $7 |
| alphatrade-postgres-staging | dpg-d8fuok57vvec739lm0u0-a | basic_256mb, 1 GB disk, PG 18 | frankfurt | $6 |
| Render Key Value | — | none (Redis is external, `REDIS_URL`) | — | $0 on Render |
| Vercel project `alpha-trade-ai` | prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ | team plan not exposed by MCP | — | $0 if Hobby / $20 per seat if Pro |
| Redis (Upstash per runbook), Qdrant Cloud | external | not visible to MCP — confirm free tier | — | likely $0 |

Render workspace plan (Hobby $0 vs Pro $25) is not exposed by the MCP; confirm
in the dashboard. Prices from render.com/pricing on 2026-11-03.

**Staging today ≈ $20/mo Render compute** (+ workspace plan, + any external).

## 2. Why production costs more when it is "the same service"

It does not cost more *per service*. A production API on the same starter plan
is the same $7. Cost only rises because production is **extra copies**: a
second API, second worker, second database, running *alongside* staging. If
production *replaces* staging, the compute bill is unchanged.

## 3. Cost comparison

| Item | A: production replaces staging | B: production alongside staging |
|---|---|---|
| API starter | $7 | $7 + $7 |
| Worker starter | $7 | $7 + $7 |
| Postgres basic_256mb | $6 | $6 + $6 |
| **Render compute** | **$20/mo (no change)** | **$40/mo (+$20)** |
| Vercel | same project, $0 extra | same project (prod domain) or a 2nd project, $0 extra on Hobby |
| Redis / Qdrant | reuse | 2nd free-tier DB each, likely $0 |
| Optional: Render Pro workspace | +$25 | +$25 (also unlocks HTTP request logs, isolated envs) |
| Optional: dedicated outbound IP (Binance ban fix) | +$100 (Pro only) | +$100 (Pro only) |
| Optional: Vercel Pro (commercial use is not allowed on Hobby) | +$20/seat | +$20/seat |

Recommendation: **B at +$20/mo** so staging stays the safe test bed for merges
(every merge to main redeploys staging; production only deploys by hand).
Pro / dedicated IP are separate decisions.

## 4. Blocking open item P1 — "production" mode is locked down in code

`backend/src/app/core/deployment_safety.py` and friends refuse, when
`ENVIRONMENT=production`: `paper_exchange_demo` (BloFin demo), autonomous
BloFin demo, autonomous internal paper, exit monitor, Watcher arming /
orchestration, Telegram activation and enrollment, live perpetual evidence,
demo seed. So a production deploy with today's code would boot as an app with
**no scanning and no demo trading**. Options (needs Sofien + Griller):

1. Production runs with `ENVIRONMENT=staging` semantics on separate services
   (no code change; naming only). Fastest, but "production" isn't enforced.
2. Add an explicit `production_demo` profile: allow demo/paper features in
   production behind flags, keep every real-money gate. Code change + Griller.
3. Keep production locked (UI/API only) until real stats exist.

## 5. Production env checklist (both services)

- [ ] `ENABLE_REAL_TRADING=false` (pinned in Blueprint; boot refuses otherwise)
- [ ] `EXECUTION_MODE=paper` (pinned)
- [ ] `ENVIRONMENT` set per P1 decision (sync:false)
- [ ] `EXCHANGE_MODE` demo/paper only, never `trade_live` (rejected globally)
- [ ] No BloFin live keys anywhere; demo keys only, no withdraw/transfer perms
- [ ] New `JWT_SECRET` (never reuse staging), new DB, new Redis DB, new Qdrant collection/cluster
- [ ] `DATABASE_URL` → production DB internal URL; DB `ipAllowList` not 0.0.0.0/0
- [ ] `CORS_ORIGINS` = production frontend domain only
- [ ] `DEMO_SEED_ENABLED=false`, `DEBUG=false`, `AUTH_COOKIE_SECURE=true`
- [ ] Telegram: separate bot/chat for prod, or off
- [ ] `WATCHER_APPLY_STRATEGIES_TO_WATCHLIST` off; projection cap 20
- [ ] Vercel production env `NEXT_PUBLIC_API_URL` → production API

## 6. Blueprint draft and sync risk

`deploy/render.production.draft.yaml` (guarded by
`backend/tests/test_render_production_draft.py`). It is **not** `render.yaml`
and is not linked to any Blueprint. Risks:
- A Blueprint sync overwrites any key that has a `value:` in the file. That is
  how the 30 Oct staging reset happened. All operator/runtime keys are
  `sync: false`; only hard safety values (real trading off, paper) are pinned.
- `sync: false` keys are only prompted on *first* creation; later syncs leave
  them alone, so they must be set by hand once.
- Applying the draft creates paid services (≈ $20/mo). Do not apply without go.
- `autoDeploy: false` on production: deploys are manual, after staging soak.

## 7. Kill-switch 401s (fixed in this PR)

Evidence: staging API returns ~45–120 HTTP 401/day (Render metrics,
27 Oct–3 Nov). Root cause: access tokens live 15 min
(`access_token_expire_minutes=15`) while the app shell polls
`GET /risk/kill-switch` every 60 s (`POSTURE_REFRESH_INTERVAL_MS`). The poll
always sent the expired token first, got a 401, then refreshed and retried, so
each expiry logged a 401 per open tab, usually on the kill-switch path. Not a
security fault; the retry worked. Fix: `apiFetch` refreshes proactively when
the JWT `exp` is ≤30 s away (single-flight refresh shared with the 401 path);
401 retry stays as fallback. Request-path logs were unavailable (Render log API
503), so the per-path split is inferred from code + counts; verify after merge
by watching the daily 401 count drop.

## 8. Steps to go live (after approval)

1. Griller review of this PR → merge (Rule 3).
2. Sofien: decide P1 and option A/B; approve the monthly cost.
3. Create prod DB + services from the draft (or dashboard), set secrets by hand.
4. Run `alembic upgrade head` (pre-deploy), boot API, check `/health`.
5. Boot worker; check boot settings report (PR #410) shows healthy.
6. Point Vercel production env at the prod API; smoke-test login, kill switch.
7. 48 h soak; then decide on staging (keep for option B).

## 9. Rollback

- Code: revert the merge commit on main; staging auto-redeploys.
- Production: Render "rollback to previous deploy" (Hobby keeps 5 builds).
- Infra: suspend prod services (stops compute billing) — deletion needs Sofien.
- Data: Render PITR (3 days on Hobby, 7 on Pro) + logical backups
  (docs/backup_restore_runbook.md).
- Kill switch stays available at all times; real trading cannot be enabled by
  config (boot refuses).
