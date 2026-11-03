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

## 8. Go-live checklist — each step needs Sofien's explicit go

Nothing below happens without a written "go" from Sofien for **that step**.
Record who said go and when next to each box.

- [ ] **G0. Merge** PR #411 after Griller GO and green CI (incl. e2e-smoke).
- [ ] **G1. Create production services** (API, worker, Postgres) from
      `deploy/render.production.draft.yaml` or the dashboard. Costs money. *Go: ____*
- [ ] **G2. Secrets.** New `JWT_SECRET`, OpenAI, Qdrant, Redis set by hand.
- [ ] **G3. BloFin demo keys + Telegram bot for production.** *Go: ____*
      Decision needed: **separate BloFin demo account (recommended)** vs reusing
      the staging demo account. The #403 flat-account checks (reconciliation,
      stale-entry and demo-snapshot pins) assume one runtime owns an account;
      two environments trading the same demo account would make each see the
      other's positions as foreign and refuse or reconcile against them. Keys:
      demo only, trade permission, no withdraw/transfer. Telegram: a separate
      bot + chat so staging and production alerts can't be confused.
- [ ] **G4. Boot API + worker disarmed**; `alembic upgrade head` as pre-deploy;
      `/health` and the #410 boot settings report show healthy.
- [ ] **G5. Settings mirror.** Staging one-off job: `export`; production
      one-off job: `import` (dry run) → Sofien reviews the plan → `import --apply`. *Go: ____*
      Import refuses unless the target is `ENVIRONMENT=production` with
      `production_demo` active and the bundle came from staging.
- [ ] **G6. Apply the risk-policy proposal** in the production app (Sofien,
      step-up). Check leverage/size values match staging. *Go: ____*
- [ ] **G7. Kill-switch check before arming:** in production, engage the kill
      switch, confirm the API and worker report it active, release it. Must
      pass before G8.
- [ ] **G8. Arm Watcher / autonomous** in the dashboard
      (`WATCHER_ORCHESTRATION_ENABLED`, `WATCHER_PAPER_STAGING_ACTIVATION`,
      `AUTONOMOUS_BLOFIN_DEMO_ENABLED`, Telegram flags) and the in-app switch. *Go: ____*
      Production scan scope stays capped at 20 symbols (same as staging).
- [ ] **G9. Vercel production domain** → production API (`NEXT_PUBLIC_API_URL`,
      `CORS_ORIGINS`), smoke-test login and kill switch. *Go: ____*
- [ ] **G10. 48 h soak**, then decide staging's future.

**Shared Binance IP.** Production workers on Render get the same shared
outbound IPs as staging's region, so Binance rate limits/bans (23 and 27 Oct)
can hit both at once, and running both doubles our request weight. Keep the
production scope ≤20, keep the Bybit fallback on, and watch the #410 ban alert.
A dedicated egress IP is a separate cost decision.

**Dashboard-only keys are safe from Blueprint syncs.** Render's Blueprint spec:
"Render preserves existing environment variables, even if you omit them from
the Blueprint file", and `sync: false` keys are ignored on later syncs. So
`WATCHER_PAPER_STAGING_ACTIVATION` (not in the file) and every `sync: false`
key keep their dashboard values. Only keys with a `value:` are overwritten,
which is why only safety pins have values. Keep Blueprint Auto Sync off.

## 9. Rollback

- Code: revert the merge commit on main; staging auto-redeploys.
- Production: Render "rollback to previous deploy" (Hobby keeps 5 builds).
- Infra: suspend prod services (stops compute billing) — deletion needs Sofien.
- Data: Render PITR (3 days on Hobby, 7 on Pro) + logical backups
  (docs/backup_restore_runbook.md).
- Kill switch stays available at all times; real trading cannot be enabled by
  config (boot refuses).
