# Agent 3 verification and delivery ledger

Historical Agent 3 results below cover its a9 feature head. Current PR237 requires
the single `a10blofinactivity001` head, following a8 → a9 → a10. Use
[current release qualification](release_qualification.md) for rollout and rollback;
these earlier results are not acceptance of a newer candidate.

Baseline: PR233/main `b165b92276346f0e0fe3ccdbd2bec3443dc75d40`, verified
as the isolated branch ancestor. Original checkout preserved. Product branch is
`codex/reviewer-wave-data`; generated HANDOFF/CHANGELOG are published separately
on `cursor/reviewer-wave-agent3-handoff`, without a handoff PR.

## Completed

- Bounded manual incident trace and baseline fake-venue evidence. Root cause stays
  unknown; no manual production/widget fix was justified. Baseline tests reproduced
  the knowledge rollback window and same-org/different-user linked URI collision.
- Durable SQL content/outbox transaction; scoped generation-aware indexing,
  bounded retry/claim/restart/fencing, replacement/deletion cleanup, reconciliation,
  accurate readiness receipts and explicit owner retry/delete API.
- Supported opt-in worker integration with independent health and graceful stop,
  tested while Watcher/Telegram are disarmed. Runtime flags remain unchanged.
- Additive Alembic revision generated from actual disposable PostgreSQL, upgrade
  from `a8agentcapture001`, downgrade/reupgrade, legacy content preserved and one
  final head `a9knowledgeoutbox001`. Existing migration consumers follow that head.
- Sibling manifests read: Agent 2 at `daa4f384`, Agent 1 at `d24b45e0`. No Agent 2
  schema addition is needed. Agent 1 owns generated types and readiness UI.
- Clearly synthetic, equivalent-interface local Qdrant/pgvector experiment and
  measured retain-Qdrant decision. No production vector migration.

## Focused validation

All database URLs below target disposable loopback PostgreSQL 17.11 created only
for this task. Tests reset those schemas. No shared database was touched. Backend
commands run from `backend`; use the locked local `.venv`.

Combined knowledge/provider/worker/capture/Agent/journal/manual selection:
**350 passed, zero skips, exit 0** (156.76 seconds). This preceded the final
contention-budget and atomic storage-metering adjustments, whose affected
selection is recorded below. The combined command is:

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent3@127.0.0.1:55439/agent3_test \
INDEXING_MIGRATION_POSTGRES_URL=postgresql+psycopg://agent3@127.0.0.1:55439/agent3_migrations \
UV_CACHE_DIR=/tmp/agent3-uv-cache .venv/bin/pytest \
  tests/test_knowledge_indexing.py tests/test_knowledge_indexing_migration.py \
  tests/test_rag.py tests/test_knowledge_file_import.py \
  tests/test_agent_knowledge_passages.py tests/test_qdrant_dimensions.py \
  tests/test_at013_provider_fail_closed.py tests/test_agent_capture.py \
  tests/test_paper_worker_supervisor.py tests/test_disarmed_render_worker_boot.py \
  tests/test_worker_memory_diagnostics.py tests/test_phase4_canonical_journal.py \
  tests/test_mvp_workflow.py tests/test_trading_analytics.py \
  tests/test_agent_action_application.py tests/test_manual_blofin_demo.py \
  tests/test_manual_demo_reconciliation.py tests/test_manual_demo_native_pagination.py \
  --tb=short
```

Fault cases cover SQL rollback/before-commit failure, outage/exhaustion, duplicate
delivery, lost acknowledgment, crash after write, expired claims/restart,
concurrent claims/document serialization, replacement/deletion/stale jobs,
replacement during write, missing/obsolete inventory, private/shared/template
visibility, absent SQL, spoofed payload filters, uncommitted duplicate receipts,
owner retry, transaction-free provider calls and worker health/shutdown/builder.
The extended contention case verifies that seven busy document-lock claims do
not exhaust the provider attempt budget. Atomic admission metering and rollback,
duplicate counts, and worker budget rejection without embedding calls are covered
in the final affected selection.
Deterministic fault tests use memory vectors. Fake manual tests use
`httpx.MockTransport`. Neither establishes live Qdrant or exchange availability.

Existing migration/learning/worker consumers: **64 passed, zero skips, exit 0**:

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent3@127.0.0.1:55439/alphatrade_test \
AT028_POSTGRES_URL=postgresql+psycopg://agent3@127.0.0.1:55439/alphatrade_test \
KNOWLEDGE_POSTGRES_URL=postgresql+psycopg://agent3@127.0.0.1:55439/alphatrade_test \
UV_CACHE_DIR=/tmp/agent3-uv-cache .venv/bin/pytest \
  tests/test_release_wave002_migrations.py tests/test_knowledge_file_migration.py \
  tests/test_manual_demo_migration.py tests/test_phase2_4_alembic_postgres.py \
  tests/test_phase8_learning_persistence.py \
  tests/test_journal_trades_alembic_empty_tenant.py tests/test_watcher_paper_activation.py \
  --tb=short
```

Lint/format: `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`, both
exit 0; 1,163 files formatted. `git diff --check` passes. Relevant strict typing:

```sh
.venv/bin/mypy src/app/rag/indexing.py src/app/workers/knowledge_indexing.py \
  src/app/providers/qdrant.py src/app/repositories/documents.py \
  src/app/services/rag_service.py src/app/schemas/rag.py --follow-imports=silent
```

Exit 0, six source files. A separate full `mypy src/` comparison remains red:
baseline 495 errors in 100 files vs branch 494 in 99 files. Normalizing diagnostics
by file/message (excluding shifted line numbers) found zero additions and one
existing Qdrant client annotation error removed. This is not a full typing pass.

Retained manual widget/browser checks (from `frontend`):

```sh
npm ci --ignore-scripts --cache /tmp/agent3-npm-cache
npm test -- --run src/components/settings/ManualDemoTest.test.tsx \
  src/components/settings/ManualDemoHistory.test.tsx
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
UV_CACHE_DIR=/tmp/agent3-uv-cache npx playwright test \
  e2e/manual-demo-recovery.spec.ts --project=chromium
```

Widget exit 0: **25 passed**. Browser exit 0: **2 passed**, no skips/retries.
Fake API/history/logout/reconciliation preserves command identity on lost response.
No external order acceptance was tested. Playwright download was blocked (403);
installed system Chromium provided the successful run. Google Fonts was blocked,
so Next used fallback fonts; no hosted font/rendering claim. No production frontend
files or dependency manifests changed. Broader frontend verification belongs to Agent 1.

The local benchmark command/results/plan and limits are in `pgvector_decision.md`
and `pgvector_synthetic_results.json`. PostgreSQL used filtered exact search, not
HNSW; random vectors do not measure semantic retrieval. No full backend CI was run.

Final affected selection after the last contention/accounting adjustments:
**138 passed, zero skips, exit 0** (37.43 seconds). Same local PostgreSQL
environment as the combined command:

```sh
.venv/bin/pytest tests/test_knowledge_indexing.py tests/test_rag.py \
  tests/test_knowledge_file_import.py tests/test_at013_provider_fail_closed.py \
  tests/test_mvp_workflow.py tests/test_trading_analytics.py \
  tests/test_agent_action_application.py --tb=short
```

The admission event reserves the ingestion request count without claiming any
provider tokens; `rag_indexing` records subsequent provider work separately.
Monthly/daily budgets are rechecked before remote batches. This preserves feature
quota enforcement while uploads wait for indexing; daily request usage counts
both admission and actual indexing events. No quota service or Agent modules changed.

Additional quota/provider-mode/capture compatibility after accounting changes:
**37 passed, zero skips, exit 0** (11.05 seconds):

```sh
UV_CACHE_DIR=/tmp/agent3-uv-cache .venv/bin/pytest tests/test_usage_quota.py \
  tests/test_at015_provider_mode_quotas.py tests/test_agent_capture.py --tb=short
```

Intermediate failures were resolved: the baseline regressions failed as intended;
an inherited migration-parent assertion was updated; the first named test DB was
not yet created; one test command named a nonexistent file; four cross-module
assertions expected synchronous vectors and now advance indexing explicitly or
assert pending during outage. Final results above supersede those intermediate runs.

## Pending at integration

- Consolidate the three branches and preserve one migration head. Agent 1 must
  regenerate API/client unions, show SQL storage separately from indexing readiness,
  poll document status and expose failed retry without fabricating search success.
- Agent 2 must verify its own/shared/template adapter and final SQL parent/chunk
  checks on the consolidated branch. Composed Agent application receipts are made
  before their outer commit and can retain `sql_chunks_stored=false`; refresh the
  document status after confirmation. No Agent production modules were edited here.
- Re-run the focused ledger with sibling provider/Agent changes, generated contract
  checks, dedicated Knowledge UI, capture, journal/lesson sync, quota and fail-closed
  provider regressions. Review mixed-writer/schema rollback and worker resource budget.
- Complete the supervising exact-ref gate in `.ai/RELEASE.md`. This local evidence
  does not authorize merge, deploy, runtime flag changes or external acceptance.

## Deferred

Production pgvector/backfill/dual writes/cutover/decommissioning; real retrieval
quality/concurrency/cost evaluation; independently authorized historical orphan
inventory where SQL rollback left no document. Keep Qdrant and the outbox useful
independently. Full inherited typing cleanup is outside this ownership.

## Blocked / unverified

Manual incident root cause needs sanitized failing stage, timestamp, HTTP
status/error/request ID and existing command/native read-only evidence if submission
began. Owner account/session, pins, funds and deployed revision remain unverified.
No authorized logs or credentials were supplied. No external order was sent.

Deployed pgvector extension privileges, real corpus/model/dimensions, hosted Qdrant
availability/utilization and actual provider/infrastructure cost remain unknown.
Mac `~/.local/bin/sync-alphatrade-ai-handoff.sh` is absent; cloud copies and normalized
self-hashes are verified, but iCloud destination SHA256/cmp and LaunchAgent state
cannot be verified from this host. Dedicated handoff branch publication is recorded
in the generated artifacts; Mac verification is the supervisor's remaining step.
