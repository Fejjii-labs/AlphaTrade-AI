# Consolidation verification ledger

The a9 head recorded below is historical evidence at the named revisions. Current
PR237 requires a8 → a9 → a10, with one final `a10blofinactivity001` head. Current
rollout/rollback qualification is recorded in [release qualification](release_qualification.md).

The latest narrow PR237 correction is recorded in **Local validation completion**
below. The earlier integration results retain their original revision coverage.

Runtime candidate: `17ad41a0577722db16a9e5a8a017e93ac5f80a63`.
PostgreSQL authoring/selection follow-up:
`7dd06c6745dd019b088e7e417a69f2705c529bb5` changes only two test files and the
focused CI selector; runtime/frontend/evaluation bytes are unchanged from 17ad41a.
The final documentation commit does not change implementation or tests. PR CI is
reported separately against its actual head in the PR description and canonical
handoff. Results from earlier source branches are not combined acceptance.

## Exact integrated sources

Main b165b92276346f0e0fe3ccdbd2bec3443dc75d40;
PR234 69778070cbc9f7431e3af6224e393722cf5eafb9;
PR235 d1bf70babc8db4a7219fa32fe7af229b4b466031;
PR236 44d68013477c06a44e03f6e8f49a584906d4a9e4.
All four refs were refreshed again before publication and remained unchanged.
Merge commits preserve all source history; no other branch was overwritten.

## Local checks at 17ad41a

Commands use `backend/.venv` installed from the lockfile and frontend `npm ci`.
PostgreSQL 17.11 is disposable loopback on port 55432. Test providers are deterministic.
Each row is a separate selection; counts overlap and must not be added together.

| Check | Exit | Result |
| --- | --- | --- |
| Backend selection below | 0 | 150 passed, 0 skipped, two Qdrant compatibility warnings, 93.42s |
| Ruff check `.` and focused selector | 0 | All checks passed |
| Ruff format check `.` and focused selector | 0 | 1,180 files formatted |
| Eleven-file strict typing command below | 0 | No issues |
| `npm run lint` | 0 | No warnings/errors |
| `npm run typecheck` | 0 | Passed |
| `npm test -- --maxWorkers=4` | 0 | 232 files, 1,481 passed, no skips/errors, 112.14s |
| Production build command below | 0 | 61 prerendered pages |
| Production browser selection below | 0 | 17 passed, one optional skip, no retries/failures, 35.3s |
| `python ../evaluation/evaluate_agent.py` | 0 | 16/16 |
| `python ../evaluation/evaluate_rag.py` | 0 | 5/5 |
| `python ../evaluation/evaluate_guardrails.py` | 0 | 7/7 |
| `UV_CACHE_DIR=/tmp/agent2-uv-cache npm run api:check` | 0 | Drift check passed |
| `alembic heads` | 0 | One head: a9knowledgeoutbox001 |
| Diff whitespace and secret-pattern scan | 0 | Passed; no deployment/credential/activation changes |

Backend (working directory `backend`):

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent2@127.0.0.1:55432/postgres \
INDEXING_MIGRATION_POSTGRES_URL=postgresql+psycopg://agent2@127.0.0.1:55432/agent3_migrations \
.venv/bin/pytest -o addopts='' -q \
 tests/test_knowledge_indexing_migration.py tests/test_reviewer_integration_postgres.py \
 tests/test_agent_vector_retrieval.py tests/test_turn_coordinator_postgres.py \
 tests/test_shared_turn_policy.py tests/test_usage_quota.py tests/test_knowledge_indexing.py \
 tests/test_qdrant_dimensions.py tests/test_strategy_conversation_foundation.py \
 tests/test_interactive_agent_foundation.py tests/test_schemas.py tests/test_backend_ci_scope.py
.venv/bin/ruff check . ../scripts/run_backend_focused.py
.venv/bin/ruff format --check . ../scripts/run_backend_focused.py
.venv/bin/mypy src/app/rag/indexing.py src/app/workers/knowledge_indexing.py \
 src/app/providers/qdrant.py src/app/repositories/usage.py src/app/services/rag_service.py \
 src/app/services/quota_service.py src/app/services/turn_coordinator.py \
 src/app/agents/phase_runtime.py src/app/schemas/common.py \
 src/app/interactive_agent/vector_adapter.py src/app/services/strategy_proposal_service.py \
 --follow-imports=silent
```

Full `mypy src/ --no-error-summary` remains red (exit 1): main has 495 errors in
100 files; candidate has 489 in 98 files. Same environment, normalized file/message
diagnostics: zero additions and six removals. This is not a full typing pass.

Frontend (working directory `frontend`):

```sh
NEXT_FONT_GOOGLE_MOCKED_RESPONSES="$PWD/test-fixtures/offline-fonts.cjs" npm run build
PLAYWRIGHT_PRODUCTION=true CI=true UV_CACHE_DIR=/tmp/agent2-uv-cache \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npx playwright test \
 e2e/strategy-conversation.spec.ts e2e/blofin-repair.spec.ts \
 e2e/workspace-redesign.spec.ts e2e/smoke.spec.ts \
 ui-tests/reviewer-wave.spec.ts ui-tests/reviewer-api-persistence.spec.ts \
 --project=chromium --reporter=line
```

Build uses the explicit test-only offline font fixture because Google Fonts is
unavailable in this cloud environment. It does not verify real font delivery.
Browser skip: optional `Browser happy path (local optional)` registration/workspace/
logout test is gated by its existing environment opt-in. Actual authoring/import,
save/reload/compile/approve, acknowledgment latency, private context, billing routes,
recorded BloFin rendering, history/receipts and journal retrieval all executed.
Browser persistence in this selection uses SQLite; PostgreSQL evidence is separate.

## Local follow-up at 7dd06c6

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent2@127.0.0.1:55432/postgres \
.venv/bin/pytest -o addopts='' -q tests/test_strategy_conversation_postgres.py \
 tests/test_backend_ci_scope.py
```

Exit 0: 15 passed, no skips, 4.72s. This includes the real PostgreSQL API authoring
journey, canonical confirmation, setup type and conversation binding reload, replay
without another version, existing concurrent confirmation, and CI selection assertions.
The focused selector includes PostgreSQL authoring for future Agent/API changes.

## Repair history (development evidence only)

Initial combined retrieval: two failures/one pass. Initial RAG evaluation: 0/5.
The first new retry fixture used the wrong provider error attribute; correcting it
produced 10/10 new PostgreSQL tests. The migration fixture initially lacked its
separate disposable database; after creating it, focused migration passed, then the
candidate selection passed 150/150. Frontend checks caught a misplaced readiness
control and generated nested-response optional fields; the final unit/type/build
results above cover their repairs. No complete backend suite ran during repairs.

First development browser selection: 11 failed, six passed, one skipped. Contract
fixtures, route UUIDs and cookie host were corrected. The next selection passed 15,
failed two, skipped one: upload/action separation and development Strict Mode request
counts. Authoring then passed both journeys. Production browser verification preserved
the request-count assertions and passed 17/17 executed tests. CI downloads the frontend
job's verified production build and runs the registered reviewer tests against it.

## CI, staging and production

Source CI links and exact source revisions are in integration_contract.md; their
failures were refreshed and inspected. Final consolidated PR CI runs the existing
six required jobs, focused backend selection, schema drift, production browser tests,
and all three deterministic evaluations. Its exact run/head/job results are recorded
in the PR description and canonical handoff after completion, without an evidence-only
product push that would trigger another unnecessary Actions run.

No staging deployment, external model/Qdrant acceptance, Mac/iCloud sync or production
verification occurred. The manual BloFin incident remains undiagnosed; required owner
request/error/request ID, existing command and native read-only evidence are listed in
manual_order_incident.md. No external order was submitted to reproduce it.

The full backend CI pipeline was not dispatched: `.ai/RELEASE.md` requires supervising
review, approved consolidated staging deployment and fresh diagnostics/evaluations first.
After those prerequisites, run exactly one full dispatch on the reviewed release ref.
Local checks and focused PR CI do not constitute complete release acceptance.

Readiness: local integration verification complete; inspect exact-head PR CI before
supervising review. Full release acceptance and approved rollout remain pending.

## First PR CI and focused repair

PR237 first run [38007546047](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38007546047)
covers `a93d8c443743414ceae123bb44c90a85fdab8614`: Docker and deployment-safety
passed. Backend failed formatting of the shared selector; frontend passed schema
export/drift/lint/types but its unit selection had 1,480 passes and one failure.
Evaluation and browser jobs were skipped by failed dependencies. This run is not
acceptance of this or a later revision.

Repair `f2cbf3b756ca08a5c5452fccff7a1b7b60dcdd5d` changes only shared selector
formatting and a frontend test clock. Ruff must run from `backend` with that
configuration. The setup detail fixture expired at UTC midnight on October 10;
its expected CONFIRMED state now runs with a fixed October 2 Date. Runtime expiry
checks and the existing assertions remain intact. Focused setup test, frontend
lint/types and complete Ruff/format checks pass. Runtime bytes remain unchanged.
The next push consolidates both repairs; required CI must cover its exact new head.

## Second PR CI and focused browser repair

[Run 38007952825](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38007952825)
covers `038983f8ce85e4b5ce826551504cb16e0bdb83fe`. Five jobs passed: backend
519 tests/no skips (283.12s), Ruff/format; frontend 232 files/1,481 tests
(143.97s), drift/lint/types and normal production build (61 pages); Docker;
deployment safety; Agent 16/16, RAG 5/5 and guardrails 7/7. Browser failed:
53 passed, one failed, one retry pass, 13 existing environment-gated skips.
The failed import path persisted its document and conversation reference but
omitted the original source from the capture-failure receipt. The intermittently
missing exact-document notice was gated by unrelated library requests/polling.
This run is not acceptance of a later revision.

Repair `a63abe1d47e6827d7cd915d39f83b9064e0c1b9f` changes frontend presentation,
source acknowledgment metadata and exact-source loading. Backend/runtime/schema
and rollout flags are unchanged. The working tree tested below was committed
without changing the tested implementation. Capture failures remain visible;
the source link now remains visible after reload. Exact source loading does not
await unrelated listings, with a regression that leaves both lists stalled.
Existing browser assertions remain and the import test adds reload/link assertions.

Focused commands (`frontend`), each exit 0:

```sh
npm test -- --maxWorkers=4 src/components/agent/AgentWorkspace.test.tsx \
 src/components/agent/acknowledged-turn.test.ts 'src/app/(app)/knowledge/page.test.tsx'
npm run lint
npm run typecheck
NEXT_FONT_GOOGLE_MOCKED_RESPONSES="$PWD/test-fixtures/offline-fonts.cjs" npm run build
PLAYWRIGHT_PRODUCTION=true CI=true UV_CACHE_DIR=/tmp/agent2-uv-cache \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
SIMPLIFIED_UI_SHOTS=/tmp/integration-browser-evidence npx playwright test \
 e2e/simplified-ui-smoke.spec.ts e2e/readiness-validation.spec.ts \
 --project=chromium --retries=0 --reporter=line
```

Unit: 49 passed/three files, 5.92s. Build: 61 pages. Browser: five passed,
zero skips/retries, 20.1s. Earlier local reproduction at 038983f failed the import
receipt and passed four other cases (35.9s), so the receipt defect reproduced.
The complete backend suite was not run during these frontend repairs.

Local focused-selector diagnostic during the first CI repair, implementation
`f2cbf3b`: `run_backend_focused.py --base b165b92276346f0e0fe3ccdbd2bec3443dc75d40
--head a93d8c443743414ceae123bb44c90a85fdab8614` selected 38 files/nodes.
Exit 1: 518 passed and one migration database-name guard failed (219.05s).
The local URL pointed to disposable `postgres`, while that test requires
`alphatrade_knowledge_test` or `alphatrade_test`; the guard was preserved.
After creating the correctly named disposable database, at 038983f:

```sh
KNOWLEDGE_POSTGRES_URL=postgresql+psycopg://agent2@127.0.0.1:55432/alphatrade_knowledge_test \
 .venv/bin/pytest -o addopts='' -q tests/test_knowledge_file_migration.py
```

Exit 0: two passed, 0.22s. Do not add these overlapping counts to another selection.
The successful 519-test CI result above uses PostgreSQL's guarded database name.

The final documentation follow-up does not change tested code. Required PR CI
must cover its actual published head; record that final run in the PR description
and canonical handoff, without another evidence-only product push.

## Local validation completion

Correction implementation: `a91d25f9666235eb25fd872aa20e1fe62483fc57`.
The reviewed integration branch was refreshed before editing and still matched
`4e515ddb8de20f4b2cfb7498ed6ce9bbbd2d2f2f`. The correction was committed from the
tested working tree without changing its implementation/test bytes. The following
ledger/contract commit changes documentation only; the PR description and handoff
record the exact consolidated published head. Both commits are published in one
product push, with no manual full-backend dispatch during this repair.

AgentWorkspace now validates the complete body with `agentTurnRequest`, the same
generated validator used by the client, before generating/storing a pending request.
Local rejection keeps the draft, attachment preview and imported document reference;
correction can submit normally without importing again. Catch/recovery behavior for
ambiguous outcomes is unchanged, including status-zero contract failures.

Focused commands (`frontend`), each exit 0:

```sh
npm test -- --maxWorkers=4 src/components/agent/AgentWorkspace.test.tsx \
 src/components/agent/turn-recovery.test.ts src/lib/api/generated-contracts.test.ts \
 src/lib/api/client.test.ts
npm run typecheck
npm run lint -- --file src/components/agent/AgentWorkspace.tsx \
 --file src/components/agent/AgentWorkspace.test.tsx \
 --file src/lib/api/generated-contracts.test.ts
```

Result: **53 tests passed in four files**, no skips, 34.06s. Type checking passed;
changed-file lint reported no warnings/errors (the existing Next lint deprecation
notice remains). No backend, complete frontend suite, build, browser suite or
evaluation suite was run locally during this narrow repair. Interfaces/schema and
workflow checks remain unchanged.

The component regressions invoke the real generated `agentTurn` and authenticated
transport with only `fetch` stubbed. They verify 8,001-character rejection before
network I/O, no uncertain recovery lock, editable draft/corrected normal submission,
attachment/reference retention, timeout survival across remount, malformed contract
and invalid-JSON HTTP 200 responses, and explicit recovery with byte-identical body
and the original key without an automatic duplicate. The generated boundary also
rejects 8,001 before transport and accepts exactly 8,000. Existing fixture IDs were
made schema-valid UUIDs where request validation now applies; assertions remain.

Reproduction before the fix: the isolated local-rejection regression failed with
zero fetch calls and the uncertain recovery panel still present. First corrected
selection: 52 passed, one attachment test failed because its panel had not been
opened. After correcting that test setup, the final 53-test selection passed above.

Earlier [CI38009093554](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38009093554)
passed all six jobs at **4e515dd**, with focused backend selection. It does **not**
accept a91d25f or the new published head. The new automatic PR run and exact head
are recorded separately in the PR/handoff; no prior green status is carried forward.
Workflow inspection confirms complete backend pytest remains exclusively behind
`workflow_dispatch` with `full_backend=true`; required PR jobs/checks are preserved.

Quota wording now agrees with `UsageRepository.count_requests_since`: `rag_indexing`
batches/retries do not add admissions, while usage/token/cost accounting and the
worker's pre-call budget check remain. No quota implementation changed.

The concrete [staging smoke sequence](staging_smoke_sequence.md) covers Agent local
rejection/conversation/recovery, strategy draft/confirm/persist/reload, SQL import and
current-generation indexing/retrieval, and existing BloFin mirror presentation.
It is prepared, not executed. The manual order incident remains undiagnosed without
the actual failing request and authorized server/native evidence. No external orders,
deployment, merge, activation, exchange setting or credential changes occurred.

Readiness: focused correction verification complete; ready for supervising release
review with current-head required PR checks tracked separately. Approved coordinated
staging, fresh diagnostics/evaluations and the one exact-ref complete release gate
under `.ai/RELEASE.md` remain prerequisites for final acceptance.
