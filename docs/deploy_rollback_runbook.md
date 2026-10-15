# Deploy rollback runbook (AT-005)

**Status:** Paper-staging / ops documentation  
**Safety:** Rollback and smoke gating only. Do **not** enable real trading, live billing,
or external notification delivery as part of recovery.
(`EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.)  
**Related:** [staging_deployment_runbook.md](staging_deployment_runbook.md) ·
[staging_deployment_checklist.md](staging_deployment_checklist.md) ·
[backup_restore_runbook.md](backup_restore_runbook.md) (AT-019 data restore) ·
[deployment.md](deployment.md)

---

## 1. Purpose

Define **when** to roll back a staging (or paper-MVP) deploy, **how** to reverse
API/frontend revisions, and **how** to verify the environment afterward with the
automated post-deploy smoke gate.

| Concern | Owner |
|---------|--------|
| Redeploy previous API/frontend revision | **This runbook (AT-005)** |
| Restore Postgres / rebuild Qdrant | [backup_restore_runbook.md](backup_restore_runbook.md) (AT-019) |
| Trading safety invariants | `scripts/verify-safety.sh` + `deployment_safety` |

**RTO target (app deploy):** ≤ 30 minutes to previous healthy revision + gate pass  
(see AT-019 RPO/RTO table for data-layer targets).

---

## 2. Post-deploy smoke gate (mandatory)

After **every** staging API or frontend deploy that could affect the API contract,
auth, providers, or safety posture, run:

```bash
# Standard gate (verify-safety + staging-smoke)
BASE_URL=https://YOUR-API.onrender.com \
FRONTEND_URL=https://YOUR-APP.vercel.app \
COOKIE_MODE=true \
./scripts/post-deploy-smoke-gate.sh

# Safety-only (faster; still mandatory minimum if full smoke is blocked)
GATE_PROFILE=safety BASE_URL=https://YOUR-API.onrender.com \
  ./scripts/post-deploy-smoke-gate.sh

# Extended (includes staging-live-smoke)
GATE_PROFILE=extended \
BASE_URL=https://YOUR-API.onrender.com \
FRONTEND_URL=https://YOUR-APP.vercel.app \
COOKIE_MODE=true \
./scripts/post-deploy-smoke-gate.sh
```

| Exit code | Meaning | Operator action |
|-----------|---------|-----------------|
| `0` | Gate passed | Keep deploy; record git SHA from `/health` |
| `1` | Safety or smoke failed | **Rollback trigger** — follow §4–§6 |
| `2` | Misconfiguration | Fix `BASE_URL` / scripts; do not treat as green |

Local wiring check (CI / no network):

```bash
./scripts/post-deploy-smoke-gate.sh --self-check
```

The gate **does not** deploy, promote, or change platform env vars.

---

## 3. Rollback triggers

Roll back **immediately** (do not “wait and see”) when any of the following is true
after a deploy:

### 3.1 Hard triggers (always rollback)

1. `./scripts/post-deploy-smoke-gate.sh` exits **1**.  
2. `./scripts/verify-safety.sh` fails (non-paper mode, real trading true, unexpected
   exchange/billing posture).  
3. Service will not become healthy: `/health` unreachable after platform health window
   (Render/Vercel mark deploy failed or stuck).  
4. Startup crash attributed to `deployment_safety` (boot rejected unsafe config).  
5. `real_trading_enabled` is anything other than `false`, or `execution_mode` is not
   `paper`, on `/health`.  
6. Migrations leave the API unable to serve (`alembic upgrade` failed and release
   aborted, or app boots against incompatible schema with sustained 5xx).

### 3.2 Soft triggers (rollback unless a named owner accepts risk in writing)

1. `/health/ready` not ready and `ALLOW_DEGRADED_READY` was **not** pre-approved for
   this change.  
2. Auth/CORS broken for the production staging frontend URL (login/refresh fails in
   `staging-smoke` with `COOKIE_MODE=true`).  
3. Provider registry shows unexpected live billing (`stripe` non-mock) while
   `BILLING_ENABLED` must remain false.  
4. Sustained elevated 5xx on core paths (`/health`, `/auth/login`, `/chat/message`)
   after the gate or in the first 15 minutes post-deploy.  
5. Git SHA on `/health` does not match the intended release revision.

### 3.3 Do **not** treat as deploy-rollback alone

| Symptom | Prefer |
|---------|--------|
| Redis empty after recreate | Expected (ephemeral); re-login / warm caches |
| Qdrant empty after new cluster | Re-ingest (AT-019), not app revision rollback |
| Single flaky LLM 503 with healthy embeddings | Provider incident; re-run gate; rollback only if sustained |
| Need prior Postgres data | Data restore runbook (AT-019), may combine with app rollback |

---

## 4. Pre-rollback checklist

For PR237's a10 schema, use [the qualified retained-schema sequence](reviewer_wave/release_qualification.md).
Plain b165/a8 and bda/a9 packages cannot resolve the database's a10 revision at
startup. Do not use the generic previous-image rollback below until a migration-aware
package is prepared and qualified. Preserve the a9 document generations/jobs and
a10 native account/fact/cursor data; no schema downgrade is part of that path.

- [ ] Note **failed** deploy revision (Render deploy id / Vercel deployment URL) and
      `/health` → `git_sha` if reachable.  
- [ ] Note **last known good** revision (previous deploy that passed the smoke gate).  
- [ ] Confirm this is **staging / paper** — not a Mode D environment (none should exist).  
- [ ] Decide whether DB migration is involved:
  - **No migration / additive only:** app rollback alone usually sufficient.  
  - **Breaking migration applied:** plan AT-019 restore or careful `alembic downgrade`
    (see §5.3) — do not blindly downgrade production data.  
- [ ] Keep secrets out of chat/logs; record only booleans and revision ids.

---

## 5. Rollback steps

### 5.1 Backend (Render)

1. Open the **alphatrade-api-staging** (or equivalent) service → **Deploys**.  
2. Select the **last known good** deploy → **Rollback** / redeploy that image.  
3. Wait until platform health check on `/health` is green.  
4. Confirm release command behavior: do **not** re-run a forward migration that was
   the failure cause unless intentional; prefer the image that matches the DB head
   you are keeping.  
5. Do **not** set `ENABLE_REAL_TRADING=true` or `EXECUTION_MODE=trade` while recovering.

### 5.2 Frontend (Vercel)

1. Open the staging project → **Deployments**.  
2. Use **Instant Rollback** to the last known good deployment (or redeploy that commit).  
3. Confirm `NEXT_PUBLIC_API_URL` still points at the staging API you rolled back to.  
4. Spot-check: paper banner / login page loads over HTTPS.

### 5.3 Database (only if schema break)

Prefer **restore-to-scratch** (AT-019) over in-place destructive downgrade.

| Situation | Action |
|-----------|--------|
| Migration not applied (pre-deploy failed) | App rollback only |
| Additive migration applied; old code compatible | App rollback only; leave DB forward |
| Breaking migration; old code incompatible | Scratch restore to pre-deploy snapshot **or** reversible `alembic downgrade -1` after approval |
| Data corruption unrelated to app revision | AT-019 restore; then re-run smoke gate |

Never commit connection strings or dump contents into git.

### 5.4 Config / secrets mistakes

1. Revert the bad env var in the platform UI to the last known good value.  
2. Redeploy or restart so the process picks up env.  
3. Run the smoke gate.  
4. Rotate `JWT_SECRET` only with an explicit session-invalidation plan.

### 5.5 Canonical schema chain (Phase 7/8)

The historical Phase 7/8 portion of the migration chain is:

`4fd8c1a90b27` (canonical Candidate) → `c9e2b4a1d078` (TradePlan + ActionEligibility)
→ `d4f7a2c8e901` (learning attribution) → `e8f1c4a9b702` (`journal_trades.account_id`)
→ `b7c8d9e0f1a2` (strategy conversations, proposals, provenance links)
→ `c8d9e0f1a2b3` (durable setup-lifetime pins)
→ `e3f4a5b6c7d8` (paper evaluation observations)
→ `d9e0f1a2b3c4` (paper Telegram notification/thread identity)
→ `e0f1a2b3c4d5` (paper Telegram activation cursor and send ledger)
→ `f1a2b3c4d5e6` (controlled runtime status)
→ `a8c3e1b94d20` (worker process RSS columns).

These revisions ship non-empty `downgrade()`. Prefer leaving additive schema
forward when rolling back a qualified migration-aware API/worker image. PR237's
continuation includes the historical a3 merge and ends
`a8agentcapture001 -> a9knowledgeoutbox001 -> a10blofinactivity001`, one head.
Old code's ability to read added columns does not prove its startup can resolve the
new revision. Do **not** blindly `alembic downgrade -1` on staging data without
an explicit restore plan.

| Head to reverse | Safer action |
|-----------------|--------------|
| App-only defect after `a8c3e1b94d20` applied | Roll back the Render image; leave DB at head |
| Need to undo paper Telegram activation cursor/ledger only | `alembic downgrade d9e0f1a2b3c4` after written approval |
| App-only defect after `d9e0f1a2b3c4` applied | Roll back the Render image; leave DB at head |
| Need to undo paper Telegram identity tables only | `alembic downgrade e3f4a5b6c7d8` after written approval |
| App-only defect after `e3f4a5b6c7d8` applied | Roll back the Render image; leave DB at head |
| Need to undo paper evaluation observations only | `alembic downgrade c8d9e0f1a2b3` after written approval |
| App-only defect after `c8d9e0f1a2b3` applied | Roll back the Render image; leave DB at head |
| Need to undo setup-lifetime pins only | `alembic downgrade b7c8d9e0f1a2` after written approval |
| App-only defect after `b7c8d9e0f1a2` applied | Roll back the Render image; leave DB at head |
| Need to undo conversation tables only | `alembic downgrade e8f1c4a9b702` after written approval |
| App-only defect after `e8f1c4a9b702` / `d4f7a2c8e901` applied | Roll back the Render image; leave DB at head |
| Need to undo `journal_trades.account_id` only | `alembic downgrade d4f7a2c8e901` after written approval |
| Need to undo learning tables only | `alembic downgrade c9e2b4a1d078` after written approval |
| Need to undo TradePlan/eligibility | `alembic downgrade 4fd8c1a90b27` after written approval **or** AT-019 restore |
| Need to undo Candidate persistence | AT-019 restore preferred over in-place downgrade |

Re-run `./scripts/post-deploy-smoke-gate.sh` and, when validating the canonical
path, `INCLUDE_CANONICAL=true ./scripts/post-deploy-smoke-gate.sh` or
`./scripts/canonical-staging-smoke.sh`.

---

## 6. Post-rollback verification

```bash
BASE_URL=https://YOUR-API.onrender.com \
FRONTEND_URL=https://YOUR-APP.vercel.app \
COOKIE_MODE=true \
./scripts/post-deploy-smoke-gate.sh
```

Must observe:

1. Gate exit code **0**.  
2. `/health` → `execution_mode=paper`, `real_trading_enabled=false`,
   watcher/Telegram flags false.  
3. `/health` → `git_sha` equals the **last known good** revision.  
4. Manual: staging frontend loads; paper banner visible; no secrets in screenshots.

If the gate still fails after rollback → escalate (platform outage or data-layer
issue); consider AT-019 restore path; set HANDOFF to `BLOCKED` with evidence.

---

## 7. Failure handling

| Failure | Immediate action | Escalation |
|---------|------------------|------------|
| Gate exit 1 on new deploy | Rollback API (± FE) per §5; re-run gate | If gate still fails → BLOCKED |
| Gate exit 2 | Fix operator inputs; no rollback yet | N/A |
| Rollback control unavailable | Redeploy previous git SHA manually from CI artifact / known tag | Platform support |
| Migration + app mismatch | Stop deploys; scratch DB restore or approved downgrade | Human approval (REVIEW_REQUIRED) |
| Safety invariant violated (real trading / non-paper) | Kill traffic if possible; fix env; rollback; run `verify-safety.sh` | Treat as SEV safety incident |
| Suspected secret leak during incident | Rotate affected credentials; do not paste secrets into HANDOFF | Security checklist |

**Honesty rule:** never mark a deploy successful if the smoke gate did not pass.
Unfinished or failed recovery stays `BLOCKED` / `FAILED` in HANDOFF until verified.

---

## 8. Deploy checklist wiring

Copy into the staging deploy worksheet after each promote:

- [ ] Deploy API (and FE if needed)  
- [ ] Wait for `/health` liveness  
- [ ] Run `./scripts/post-deploy-smoke-gate.sh` (record exit code + `git_sha`)  
- [ ] On exit 1 → execute this runbook §4–§6 before any further feature work  
- [ ] On exit 0 → record revision; done  

Full env checklist: [staging_deployment_checklist.md](staging_deployment_checklist.md) §7–§9.

---

## 9. Safety invariants (unchanged by rollback)

| Setting | Required |
|---------|----------|
| `EXECUTION_MODE` | `paper` |
| `ENABLE_REAL_TRADING` | `false` |
| `BILLING_ENABLED` | `false` |
| `PROVIDER_MODE` | `fallback` (staging default) |
| Alert / Telegram delivery | disabled unless separately approved |
| `PERPETUAL_EVIDENCE_SOURCE` | `replay` to roll market evidence back; `binance_usdm` is staging read-only only |
| Mode D real execution | **out of scope** — not enabled by this runbook |

---

## 10. Related commands

```bash
# Mandatory safety-only
BASE_URL=https://YOUR-API.onrender.com ./scripts/verify-safety.sh

# Bundled API smoke (also invoked by the gate in standard profile)
COOKIE_MODE=true FRONTEND_URL=https://YOUR-APP.vercel.app \
  BASE_URL=https://YOUR-API.onrender.com ./scripts/staging-smoke.sh

# Gate self-check (no network; used in CI)
./scripts/post-deploy-smoke-gate.sh --self-check

# Live USD-M evidence activation / rollback (no platform writes)
./scripts/validate-live-market-staging.sh --self-check
BASE_URL=https://YOUR-API.onrender.com \
  ./scripts/validate-live-market-staging.sh --remote --expect inactive
```
