# Post-merge staging validation — `b4244f0`

Read-only validation of the currently deployed AlphaTrade staging release after
PR #116 (`cursor/intelligence-acceptance-final-fix-5138`) merged to `main`.

This document records evidence only. No product code was changed. No deploy,
merge, Watcher enablement, Telegram delivery, or live-trading change was
performed.

**Date (UTC):** 2026-09-21  
**Validator:** Cursor Cloud Agent  
**Local clone SHA:** `b4244f0a2cfcdee7a665c8200c24bb56566b6a37`  
**Branch for this report:** `cursor/post_merge_staging_validation-8c76`

## URLs

| Layer | URL |
| --- | --- |
| Backend | https://alphatrade-api-staging.onrender.com |
| Frontend | https://alpha-trade-ai-eight.vercel.app |

## FINAL VERDICT

**STAGING ACCEPTED**

Paper-only posture is intact. Deployed `/health` `git_sha` matches expected
`b4244f0`. The strongest existing safe post-deploy profile
(`GATE_PROFILE=extended` + analytics + strategy quality + cookie + CORS)
exited 0. Alembic head `c8d9e0f1a2b3` is operational via product behavior
(canonical evidence assembly persists/reads setup-lifetime pins without 500s).
Skipped checks are listed separately and are **not** treated as passed.

---

## DEPLOYED SHA

Expected: `b4244f0a2cfcdee7a665c8200c24bb56566b6a37`  
Observed `/health.git_sha`: `b4244f0a2cfcdee7a665c8200c24bb56566b6a37`  
Match: **yes**

GitHub CI on this SHA: run
[35603819034](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/35603819034)
`success` (backend, frontend, docker-build, deployment-safety, evaluation,
e2e-smoke).

## HEALTH

`GET /health` HTTP 200 at 2026-09-21T13:46:10Z.

| Field | Expected | Observed |
| --- | --- | --- |
| status | ok | ok |
| environment | staging | staging |
| execution_mode | paper | paper |
| real_trading_enabled | false | false |
| exchange_mode | paper_internal | paper_internal |
| git_sha | b4244f0a2cfcdee7a665c8200c24bb56566b6a37 | b4244f0a2cfcdee7a665c8200c24bb56566b6a37 |
| watcher_orchestration_enabled | false | false |
| market_watcher_enabled | false | false |
| telegram_alerts_enabled | false | false |
| telegram_interaction_enabled | false | false |
| automatic_telegram_delivery_enabled | false | false |

Additional observed (not in the required matrix):
`market_watcher_bridge_enabled=false`, `must_verify_email=false`,
`demo_seed_enabled=true`.

## READINESS

`GET /health/ready` HTTP 200:

- `status=ready`
- `ready=true`
- `providers_total=12`
- `providers_unavailable=0`

Providers (names/health only; no secrets): `openai-llm` healthy,
`openai-embeddings` healthy, `qdrant` healthy, Redis tracing healthy,
`binance-public` market data healthy, `mock-exchange` paper-only,
billing/email/news/notifications/tracing mocks healthy. Exchange detail
includes paper/real-trading-disabled wording.

## MIGRATION

Target revision: `c8d9e0f1a2b3` (`setup_lifetime_pins`).

No staging `DATABASE_URL` was available, so Alembic version was **not**
queried from Postgres directly. Operational evidence:

1. Deployed image SHA is the merge that introduced the revision; backend
   `docker/entrypoint.sh` runs `alembic upgrade head` on start.
2. `GET /canonical/evidence` (authenticated) returned HTTP 200 with
   `presentation=replay_fixture` and `live_executable=false`. That path uses
   `CanonicalEvidenceService(..., session=session)` →
   `SqlAlchemySetupLifetimeStore` → `SELECT`/`INSERT` on
   `setup_lifetime_pins`. A missing table would surface as HTTP 500 /
   `UndefinedTable`.
3. Strategy conversation confirm → compile (`executable`) → approve
   (`new_state=approved`) succeeded with no schema errors.
4. No response bodies contained Alembic/`setup_lifetime_pins`/`does not exist`
   errors.

Paper-validation `scan` (the other writer of pins) was **skipped** because a
fresh compiled strategy was not paper-eligible (HTTP 422 fail-closed). That
skip is not used as migration proof.

## STANDARD SMOKE

Invoked as part of the extended gate (`scripts/staging-smoke.sh` with
`COOKIE_MODE=true`, `FRONTEND_URL` set, `INCLUDE_ANALYTICS=true`,
`INCLUDE_STRATEGY_QUALITY=true`).

Result: **passed** (health, ready, providers, CORS 200, register, refresh
cookie present, login, protected chat, cookie refresh rotation, analytics,
strategy quality, logout, `verify-safety.sh`).

## EXTENDED SMOKE

`scripts/staging-live-smoke.sh` (gate step 3/4): **passed**.

Covered: health, ready, safety, frontend `/login` 200, CORS 200, register,
dashboard paper-only, risk settings, notification preferences with Telegram
disabled, delivery status `effective_external_enabled=false`, notifications
test (no external send), alerts delivery-summary, market-watcher
`env_enabled=False`, bridge `env_enabled=False`, protected chat, paper-only
recheck.

Note (not a failure): unauthenticated `GET /` on the Vercel host returns
**HTTP 307** `Location: /login`. The live-smoke script warns because it does
not follow the redirect. Following `/login` returns HTTP 200, title
`AlphaTrade AI`, Next.js `_next/static` assets. CSP `connect-src` includes
the staging API. This is expected unauthenticated app routing, not a 500.

## CANONICAL SMOKE

`scripts/canonical-staging-smoke.sh` (gate step 4/4): **passed** for the
unseeded synthetic path.

Passed: health paper + watcher/Telegram disabled; unauthenticated canonical
reads 401; tenant A register; empty Candidate list (`total=0` allowed);
canonical evidence replay/fail-closed (not a live mark); paper-plan
executable-field 422; risk `action=block`; kill-switch GET
`execution_blocked=False` (no activate); tenant B isolation (non-overlapping
lists); strategy statistics + journal list.

See SKIPPED CHECKS for the seeded happy path.

## ANALYTICS

`INCLUDE_ANALYTICS=true` inside staging-smoke: **passed**.

Watchlist create, workspace chat, journal entry create, `/analytics/setups`
(1 setup), `/analytics/trade-review` (`total_journaled_trades=1`),
`/analytics/discipline` score 100 grade A, `/analytics/risk-behavior`
`journal_completion_rate=1.0`.

## STRATEGY QUALITY

`INCLUDE_STRATEGY_QUALITY=true` inside staging-smoke: **passed**.

`/strategy-quality/summary` read-only note present; 5 detector reports;
`/strategy-quality/detectors/liquidity_sweep/explain` returned report +
timeframes.

## AUTH

Passed via staging-smoke cookie mode:

- Register sets `alphatrade_refresh` cookie
- Login OK (`must_verify_email=false`)
- Cookie `POST /auth/refresh` rotated access token
- Logout OK
- CORS preflight `access-control-allow-origin` exact frontend origin,
  `access-control-allow-credentials: true`
- Unauthenticated protected APIs 401 (`/conversations`, `/canonical/evidence`,
  `/journal/trades`, `/canonical/learning/strategy-stats`,
  `/paper-validation/scheduler/status`)

Demo-user password login was **not** attempted (credential unset). That is a
skip, not a pass.

## TENANT ISOLATION

Passed:

- Canonical smoke: tenant A/B candidate lists do not overlap
- Extra checks: conversation GET/messages 404 for the other tenant
- Extra checks: tenant B compile of tenant A version HTTP 403

Seeded cross-tenant candidate 404 (requires `CANONICAL_CANDIDATE_ID`) was
**skipped**.

## SAFETY

Passed and unchanged:

- `execution_mode=paper`, `real_trading_enabled=false`,
  `exchange_mode=paper_internal`
- Watcher / market-watcher / bridge / Telegram flags false
- Paper scheduler `env_enabled=false`, `effective_enabled=false`
- Kill switch GET `active=false`, `execution_blocked=false` (no mutation)
- Risk engine synthetic check `action=block` (no stop loss)
- `verify-safety.sh` passed (mandatory gate step; also re-run inside smokes)
- Alerts: `paper_only=true`, `effective_external_enabled=false`,
  `telegram_enabled=false` (configuration presence is not delivery)
- No Watcher start, no Telegram send, no live orders, no feature-flag changes

## SKIPPED CHECKS

These did **not** run, or ran only far enough to fail closed. They are **not**
passed.

| Check | Why skipped |
| --- | --- |
| Seeded canonical Candidate/eligibility/paper-plan happy path | `CANONICAL_CANDIDATE_ID` / `CANONICAL_ACCOUNT_ID` / `CANONICAL_AUTHORIZATION_ID` / `CANONICAL_REVISION_ID` unset |
| Kill-switch activate/deactivate round-trip | `CANONICAL_ALLOW_KILL_SWITCH` not set (correct for this task) |
| Demo tenant login / browser Playwright staging packs | `STAGING_DEMO_PASSWORD` unset; not part of the post-deploy gate |
| Telegram delivery / preview scripts | Telegram remains disabled; this task forbids enabling it |
| Watcher / market-watcher scan activation | Flags false; this task forbids enabling them |
| Paper-validation `start` + `scan` on a freshly compiled strategy | HTTP 422 `Strategy is not paper eligible` (fail-closed). Evidence GET already exercised pins |
| Direct Postgres `alembic current` | No staging `DATABASE_URL` in this environment |
| Browser e2e against staging (`browser-smoke-*-staging.sh`) | Not in `post-deploy-smoke-gate.sh`; demo password unset |

## FAILURES

None that fail the gate or the required health/readiness/safety matrix.

Non-blocking live-smoke WARNs: frontend `/` HTTP 307 to `/login` (see
EXTENDED SMOKE).

## Extra lifecycle (synthetic, paper-only)

Not part of the shell gate; run after it against the same URLs.

- Conversation create / list / get: pass
- Proposal preview (`is_preview=true`, `mutates_strategy_authority=false`,
  pattern spec present): pass
- Non-authorizing confirm (`looks good`): HTTP 422
- Explicit `I confirm`: status `confirmed` with resulting version
- Compile/review: `status=executable`, compiled row present, 0 failures
- Quoted approve (`> I confirm`): HTTP 422
- Explicit approve: `new_state=approved`
- Journal list + canonical strategy-stats: HTTP 200
- No HTTP 500 on dashboard, kill-switch GET, delivery-status,
  strategy-quality summary, analytics setups

## Gate command (no secrets)

```bash
BASE_URL=https://alphatrade-api-staging.onrender.com \
FRONTEND_URL=https://alpha-trade-ai-eight.vercel.app \
COOKIE_MODE=true \
GATE_PROFILE=extended \
INCLUDE_ANALYTICS=true \
INCLUDE_STRATEGY_QUALITY=true \
ALLOW_DEGRADED_READY=false \
./scripts/post-deploy-smoke-gate.sh
```

Exit code: **0**  
Window: 2026-09-21T13:46:43Z → 2026-09-21T13:47:59Z
