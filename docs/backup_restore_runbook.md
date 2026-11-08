# Backup / restore runbook (AT-019)

**Status:** Paper-staging / ops documentation  
**Safety:** Docs and local-only scripts. Do **not** restore into a live managed database
without explicit human approval. Real trading remains disabled
(`EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`).  
**Related:** [backup_inventory.md](backup_inventory.md) ·
[backup_restore_drill_plan.md](backup_restore_drill_plan.md) ·
[backup_restore_drill_evidence.md](backup_restore_drill_evidence.md) ·
[staging_deployment_runbook.md](staging_deployment_runbook.md) §13

---

## 1. Purpose

Define what we back up, recovery time/point targets, and the exact restore sequence for
AlphaTrade AI’s durable stores — so a staging or paper-MVP outage does not rely on tribal
knowledge.

This runbook complements AT-005 (deploy rollback + post-deploy smoke gating).
Application rollback and data restore are complementary:

| Concern | Owner |
|---------|--------|
| Redeploy previous API/frontend revision | [deploy_rollback_runbook.md](deploy_rollback_runbook.md) (AT-005) |
| Restore Postgres / rebuild Qdrant | **This runbook (AT-019)** |
| Trading safety invariants after restore | `post-deploy-smoke-gate.sh` / `verify-safety.sh` + deployment_safety |

---

## 2. RPO / RTO targets

Targets are for **staging / paper-MVP**. They are operational goals, not SLAs with
customers, and not a claim of autonomous profitability or live-trading readiness.

| Asset | RPO (max acceptable data loss) | RTO (max time to restore service) | Mechanism |
|-------|--------------------------------|-----------------------------------|-----------|
| **Postgres (SoR)** | **≤ 24 hours** (stretch: ≤ 1 hour if platform PITR enabled) | **≤ 4 hours** (scratch restore + cutover + smoke) | Daily automated platform snapshot + periodic verified `pg_dump` drill |
| **Qdrant** | **≤ 24 hours** *or* rebuild-from-SoR | **≤ 4 hours** (snapshot restore **or** re-ingest) | Cloud snapshot if available; else `reingest-knowledge-base.sh` after Postgres restore |
| **Redis** | **N/A** (ephemeral) | **≤ 15 minutes** | Recreate empty managed Redis; point `REDIS_URL` |
| **Config / secrets** | **0** for *names* (worksheet kept current); values via platform | **≤ 1 hour** to re-bind env on new services | Platform secret store + gitignored worksheet |
| **App deploy (API/FE)** | N/A (immutable revisions) | **≤ 30 minutes** rollback | Platform rollback — [deploy_rollback_runbook.md](deploy_rollback_runbook.md) (AT-005) |

### Acceptance for “successful restore”

1. Postgres restored to a known Alembic head compatible with the running image.  
2. `/health` shows `execution_mode=paper`, `real_trading_enabled=false`.  
3. `./scripts/verify-safety.sh` passes against the restored environment.  
4. Spot-check: org login path works; one paper portfolio or audit row present if it existed
   in the backup marker set.  
5. RAG: either Qdrant restored **or** re-ingest completed; search not silently mock in staging.

---

## 3. Backup procedures

### 3.1 Postgres — platform snapshots (primary)

1. In the managed Postgres console (Render / Neon / Railway), enable **automatic daily
   snapshots** (or equivalent) with retention ≥ 7 daily / ≥ 4 weekly.  
2. Record **only**: plan name, retention settings, region, and last successful snapshot
   *timestamp* in a gitignored ops note — never connection strings.  
3. Confirm snapshots are for the **correct** database instance (staging vs any scratch).

### 3.2 Postgres — logical dump (drill / portable)

Use the **local Compose** helpers for drills (refuse remote hosts):

```bash
# Local Docker Postgres only
./scripts/backup-postgres-local.sh
# Writes: .ai/local/backups/postgres/alphatrade-YYYYMMDD-HHMMSS.dump (gitignored)
```

For a future **approved** staging logical dump (human-gated):

1. Obtain operator approval (REVIEW_REQUIRED).  
2. Use platform “one-off shell” or laptop with sealed `DATABASE_URL` from env — never echo.  
3. Prefer custom-format dump: `pg_dump -Fc` (or platform export).  
4. Store the dump in an encrypted operator location; **do not** commit.  
5. Sanitize evidence: record dump size, SHA256 of dump file, timestamp — not contents.

### 3.3 Qdrant

| Option | When |
|--------|------|
| Cloud snapshot / backup feature | If the Qdrant plan provides it — enable with same retention intent as Postgres |
| Rebuild | After Postgres restore: recreate collection if needed, then  
  `ORGANIZATION_ID=… ./scripts/reingest-knowledge-base.sh --local` (local) or `--api` (approved staging) |

Do not treat in-memory vector fallback as a backup.

### 3.4 Redis

No backup. Document instance recreate steps in the staging worksheet. After recreate,
rate limits reset; access-token denylist is empty (sessions may remain valid until JWT
expiry — acceptable for paper staging).

### 3.5 Configuration

- Keep `docs/staging_deployment_worksheet.local.md` (gitignored) current with **variable
  names and boolean posture**, not secret values.  
- Platform “env history” / prior deploy revisions cover rollback of config mistakes.  
- After any restore, re-run `ENV_FILE=… ./scripts/check-env.sh` against a sealed local env
  file before opening traffic.

---

## 4. Restore procedures

### 4.0 Decision gate (mandatory)

| Restore target | Allowed without extra approval? |
|----------------|----------------------------------|
| Local Docker (`alphatrade` Compose Postgres) | Yes — use local scripts |
| New **scratch** managed DB (empty) | Requires human approval |
| **Overwrite** existing staging Postgres | Requires explicit dual confirmation; prefer scratch + cutover |
| Production / Mode D | **Forbidden** under ordinary tasks; Mode D program only |

Stop and set handoff `REVIEW_REQUIRED` before any managed-cloud restore action.

### 4.1 Local Docker restore (standard drill)

```bash
# Full local drill (backup → marker verify → destroy logical data → restore → verify)
CONFIRM=yes ./scripts/drill-backup-restore-local.sh
```

Or stepwise:

```bash
./scripts/backup-postgres-local.sh
CONFIRM=yes DUMP_FILE=.ai/local/backups/postgres/<file>.dump \
  ./scripts/restore-postgres-local.sh
```

### 4.2 Managed Postgres restore (approval-gated outline)

1. **Freeze writes** if staging is still serving (scale API to 0 / maintenance mode).  
2. Identify snapshot or dump by **timestamp + size + checksum** only.  
3. Restore into a **scratch** database when possible.  
4. Point a **scratch** API service (or one-off) at the scratch DB; run migrations only if
   the dump predates head — prefer restoring a dump that already matches the image’s
   Alembic head.  
5. Validate: `check-env`, `/health`, `/health/ready`, `verify-safety.sh`, login + one
   read path.  
6. Cut over `DATABASE_URL` only after validation; keep prior DB until soak passes.  
7. Rebuild or restore Qdrant; recreate Redis if needed.  
8. Record sanitized evidence in `docs/backup_restore_drill_evidence.md` (or dated addendum).

### 4.3 Qdrant restore / rebuild

1. If snapshot available: restore to a non-prod collection or cluster, verify point count
   roughly matches pre-incident expectation (order of magnitude only in docs).  
2. Else: ensure Postgres knowledge metadata is present → recreate collection
   (`scripts/recreate-rag-collection.sh` when appropriate) → re-ingest.  
3. Confirm staging search reports non-mock backend when Qdrant is required.

### 4.4 Redis recreate

1. Provision new Redis; update `REDIS_URL` in platform (sealed).  
2. Confirm `RATE_LIMIT_USE_REDIS=true` and provider status shows Redis healthy.  
3. No data restore step.

---

## 5. Post-restore validation checklist

- [ ] `execution_mode=paper`, `real_trading_enabled=false`, exchange mode non-live  
- [ ] Alembic head matches running backend image  
- [ ] `./scripts/verify-safety.sh` exit 0  
- [ ] Auth: login + refresh path (cookie or bearer as configured)  
- [ ] Paper portfolio or audit marker row present (if included in drill)  
- [ ] RAG path: degraded/mock flags understood; re-ingest if needed  
- [ ] No secrets written into git-tracked evidence  

---

## 6. Roles and communications

| Role | Responsibility |
|------|----------------|
| Operator | Approves managed restore; holds sealed credentials |
| Implementer | Runs local drill; authors sanitized evidence |
| On-call (future) | Executes this runbook under incident SEV process (AT-020+) |

Never paste `DATABASE_URL`, API keys, dump contents, or PII into chat, git, or handoffs.

---

## 7. Gaps and follow-ups

| Gap | Tracking |
|-----|----------|
| Automated post-deploy smoke gate + fuller deploy rollback | **AT-005** — [deploy_rollback_runbook.md](deploy_rollback_runbook.md) + `scripts/post-deploy-smoke-gate.sh` |
| Staging/managed restore drill against real Render/Neon snapshot | Requires human approval after this docs lane |
| PITR / sub-hour RPO on Postgres | Enable on platform when available; update targets |
| Qdrant Cloud snapshot automation | Confirm plan features; else rely on re-ingest |
| Automated check that the latest managed Postgres snapshot exists and is recent | Not scripted: the Render backup list needs dashboard/API credentials the repo does not hold. Check it by hand in the Render dashboard before any `--apply` retention run (Fable audit L11). |

---

## 8. Quick reference — safe scripts

| Script | Purpose | Guard |
|--------|---------|-------|
| `scripts/backup-postgres-local.sh` | `pg_dump` via Compose | Local Compose service only |
| `scripts/restore-postgres-local.sh` | Restore dump into Compose Postgres | `CONFIRM=yes`; local only |
| `scripts/drill-backup-restore-local.sh` | End-to-end local drill + JSON summary | `CONFIRM=yes`; writes under `.ai/local/` |
| `backend/scripts/db_retention.py` | Database and largest-table sizes, plus what each runtime-log retention policy would delete | Dry-run unless `--apply`; uses the operator's `DATABASE_URL`; `--apply` on a shared DB needs the owner's OK |

### Runtime-log retention (H5)

The paper worker can prune append-only operational logs (`backend/src/app/services/db_retention.py`).
It is **off and dry-run by default**: `RETENTION_ENABLED=true` attaches an hourly pass that only
counts and logs (`db_retention_table`); deletion also needs `RETENTION_DRY_RUN=false`.

Windows are in hours (`RETENTION_*_HOURS`, minimum 24, maximum 8760). Settings reject a value below
24 at startup. The demo snapshot window is still in days.

> [!WARNING]
> #449 replaced `RETENTION_WATCHER_EVENTS_DAYS`, `RETENTION_WATCHER_ATTEMPT_CHILDREN_DAYS` and
> `RETENTION_PAPER_EVALUATION_DAYS` with the `*_HOURS` settings below. Settings ignore unknown keys, so the
> old names are **silently ignored**, and the defaults fall from 7 or 30 days to 24 hours. This matters
> only once retention is enabled and dry-run is off. `render.yaml` never set the old keys, but a
> dashboard-only value may still exist. Before you enable retention on any environment, remove the old
> keys from that service's environment and set the `*_HOURS` values you want.

| Table | Default window | Kept regardless |
|-------|----------------|-----------------|
| `watcher_observability_events`, non-error | 24 h (`RETENTION_WATCHER_EVENTS_HOURS`) | — |
| `watcher_observability_events`, error | 72 h (`RETENTION_WATCHER_ERROR_EVENTS_HOURS`) | — |
| `watcher_subscription_eval_attempts`, `watcher_source_fetch_attempts` | 24 h (`RETENTION_WATCHER_ATTEMPTS_HOURS`) | — |
| `watcher_scan_attempts` | 24 h (`RETENTION_WATCHER_ATTEMPTS_HOURS`) | a lineage's terminal attempt; every attempt of a lineage that did not succeed or has an attempt inside the window; attempts a child row still references. Lineages are never pruned. |
| `paper_evaluation_observations` | 24 h (`RETENTION_PAPER_EVALUATION_HOURS`) | rows with a journal trade, a fill or an approved plan |
| `telegram_paper_notifications` | 24 h (`RETENTION_TELEGRAM_NOTIFICATIONS_HOURS`) | — (the security outbox, threads, messages and confirmations are never pruned) |
| `blofin_demo_sync_snapshots` | 30 days (`RETENTION_DEMO_SNAPSHOT_DAYS`) | the newest snapshot per organization |

An event counts as an error when its name contains `fail`, `error`, `blocked`, `degraded`, `stale`
or `reject` (the markers of the monitoring "recent errors" panel), or when
`fields.evaluation_status` is `failed`, `degraded` or `blocked`.

Trades, orders, fills, journal, plans, candidates, strategies, audit logs, P&L and notes are never pruned.
The main protection is the static allowlist in `db_retention.py`: only the tables above can be pruned, and no
setting or input can add one. Some plan and candidate tables also have `BEFORE DELETE` triggers in their
migrations (`canonical_candidates` and its key and transition tables, `trade_plan_revisions`, the
`canonical_trade_plan_*` tables). The others, such as `candidates`, have none. A candidate or plan window
would need a migration and the owner's OK first.
Deletes run in batches (`RETENTION_BATCH_SIZE`, `RETENTION_MAX_BATCHES_PER_TABLE` per pass).
