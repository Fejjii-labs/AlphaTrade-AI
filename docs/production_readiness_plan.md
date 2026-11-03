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

## 4. P1 decided — production mirrors staging (`production_demo`)

Sofien decided (3 Nov): production mirrors staging. Demo/paper trading, the
Watcher, Telegram and live public market data are on; real money is blocked.

Implemented as `DEPLOYMENT_PROFILE=production_demo` (config `deployment_profile`):
- Only valid with `ENVIRONMENT=production`; boot refuses it anywhere else.
- `Settings.production_demo_active` / `paper_runtime_environment` replace the
  staging-only checks for: BloFin demo exchange, autonomous demo/paper, exit
  monitor, Watcher arm + orchestration, Telegram projection/enrollment, live
  perpetual evidence, governed/manual demo access. Each call site still runs
  every other check it ran on staging (preflight, kill switch, leverage, caps).
- Still refused under the profile (tests in `test_production_demo_profile.py`):
  `ENABLE_REAL_TRADING`, `execution_mode=trade`, `trade_live`, a BloFin live
  host or any non-allowlisted host, legacy scanner/bridge/auto-tick, legacy
  Telegram alerts and automatic delivery, webhook secret, replay evidence on an
  armed Watcher, mock provider, weak JWT, debug, in-memory rate-limit fallback,
  denylist fail-open, proxy hops 0, non-HTTPS CORS, insecure cookies, localhost DB.
- `DEPLOYMENT_PROFILE=standard` (default) keeps the old API-only production.
- Not covered: the disarmed-worker boot path (#410) stays staging-only, and
  demo seed stays refused in production.

### Copying staging settings into production

`backend/scripts/settings_mirror.py` (logic in `app/operations/settings_mirror.py`):
1. Staging one-off job: `python scripts/settings_mirror.py export --org "<org>" --email <owner> > bundle.json`
2. Production one-off job: `python scripts/settings_mirror.py import --org "<org>" --email <owner> bundle.json`
   (dry run: prints the plan, writes nothing). Re-run with `--apply` to write.
3. In the production app, apply the filed risk-policy proposal (step-up), as on staging.

It copies strategies (selected version content, forked as a new version when it
differs), risk-policy profiles (filed as proposals, never applied), autonomous
settings (loss limit, max trades, risk, strategy, symbol) and the Watcher
watchlist. It never copies ids, credentials, BloFin keys, Telegram bindings,
kill-switch state, autonomous arming switches or trades. A secret-like key in
the bundle refuses the run. The target org and user must already exist.
Re-running the same bundle reports 0 changes.

## 5. Production env checklist (both services)

- [ ] `ENABLE_REAL_TRADING=false` (pinned in Blueprint; boot refuses otherwise)
- [ ] `EXECUTION_MODE=paper` (pinned)
- [ ] `ENVIRONMENT=production` and `DEPLOYMENT_PROFILE=production_demo` (both pinned, Griller B1)
- [ ] `EXCHANGE_MODE` demo/paper only, never `trade_live` (rejected globally)
- [ ] No BloFin live keys anywhere; demo keys only, no withdraw/transfer perms
- [ ] New `JWT_SECRET` (never reuse staging), new DB, new Redis DB, new Qdrant collection/cluster
- [ ] `DATABASE_URL` via `fromDatabase` (production DB internal URL); DB `ipAllowList: []`
- [ ] `REQUIRE_EMAIL_VERIFIED=true` (pinned)
- [ ] `WATCHER_PAPER_STAGING_ACTIVATION` set in the dashboard after first healthy boot + settings import (not in the Blueprint)
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

### Database access with `ipAllowList: []`

The production DB accepts no external connections. For backups, the settings
import, `psql` checks or migrations from outside Render:
- Preferred: a Render one-off job on the production worker image
  (`render jobs create` / dashboard "Jobs"), which uses the internal URL.
- Otherwise: add a temporary `/32` for the operator's IP, do the task, then
  remove it the same session and note it in the ops log.
- Scheduled logical backups: a Render cron job on the internal URL
  (see `docs/backup_restore_runbook.md`); PITR stays on.

## 7. Kill-switch 401s (fixed in this PR)

Evidence: staging API returns ~45–120 HTTP 401/day (Render metrics,
27 Oct–3 Nov). Root cause: access tokens live 15 min
(`access_token_expire_minutes=15`) while the app shell polls
`GET /risk/kill-switch` every 60 s (`POSTURE_REFRESH_INTERVAL_MS`). The poll
always sent the expired token first, got a 401, then refreshed and retried, so
each expiry logged a 401 per open tab, usually on the kill-switch path. Not a
security fault; the retry worked. Fix: `apiFetch` refreshes proactively before
expiry (single-flight refresh shared with the 401 path); 401 retry stays as
fallback. Griller C1: tokens from `/auth/refresh` are scheduled from the
server's `expires_in` against local receipt time (no clock skew), and a token
received <60 s ago is never proactively refreshed, so a fast client clock can't
storm `/auth/refresh`. C2: tests pin the storm guard and logout mid-refresh. Request-path logs were unavailable (Render log API
503), so the per-path split is inferred from code + counts; verify after merge
by watching the daily 401 count drop.

## 8. Steps to go live (after approval)

1. Griller review of this PR → merge (Rule 3).
2. Sofien: P1 decided (production_demo); approve the monthly cost.
3. Create prod DB + services from the draft (or dashboard), set secrets by hand.
4. Run `alembic upgrade head` (pre-deploy), boot API, check `/health`.
5. Boot worker; check boot settings report (PR #410) shows healthy.
5a. Run the settings mirror (dry run, then `--apply`); apply the risk proposal in the app; arm the Watcher in the dashboard.
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
