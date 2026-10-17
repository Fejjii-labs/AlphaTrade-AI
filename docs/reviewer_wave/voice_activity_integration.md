# PR237 voice and native activity integration

## Candidate and ownership

Continue `codex/reviewer-wave-integration` from reviewed baseline
`bda597c2fffbf1a49beadc64d757100808094ca2`. The current PR237 head is the
publication revision; record it verbatim in the supervising review. Included feature
revisions, preserved by merge commits:

- PR238: `af672dffb4a20fa0a8783cf7b0870cc59f6e3fff`, reviewed with
  [its handoff](../voice_conversation_foundation.md).
- PR239: `769e78a46fbce888810060f8d09cbe4d93f81c31` (includes the preceding
  `18e66dacfc0eb81f897c3f5a6eefbe0f4d29923f` account/snapshot/worker correction), reviewed with
  [its contract/rollout](../blofin_native_activity.md) and
  [revision verification ledger](../blofin_native_activity_verification.md).

The voice owner retains AgentWorkspace, recovery, controller and voice implementation.
The BloFin owner retains native backend activity, identity, snapshots, worker and
migrations. Integration edits own CI/release policy, generated client, Dashboard and
Journal presentation, and existing integration smoke expectations.

## Implemented and integrated behavior

Dashboard and Journal read only `GET /exchange/blofin/activity`. Native fills use
organization/environment/UID/trade identity for deduplication; linked commands never
add trades. Completed orders are separate context. Internal simulator records are
excluded from BloFin activity. Statistics stay on Dashboard and explicitly describe
only the loaded native facts; no portfolio-return reconstruction is attempted.

Compact rows expand into exact contract quantities/prices, signed fees, nullable PnL,
metadata, freshness and incomplete coverage. Unknown currency/funding remain unknown.
IDs and technical URLs are absent from the main presentation; a matched command is a
link in details. Back closes details and restores focus. Account/session changes fence
late responses and clear private caches; native UID changes invalidate continuation
and require a fresh read. No browser-persistent native history cache is introduced.

The actual Agent page uses the corrected owner's single typed/spoken admission path.
Local validation precedes pending retention; ambiguous transport retains the complete
body, attachment references and original key for explicit recovery. Voice defaults to
review; conversation mode is opt-in. This integration does not activate a provider.

## Focused evidence and limitations

The frontend selection at `94833dab4037109b3cdf28428c74f0cd672799c6` has
**232 passing cases in 16 files, no skips** (162 voice/shared Agent cases plus
70 client/presentation/account/DailyReview cases). Its full frontend tree is unchanged
by the subsequent worker test/documentation correction. Repository TypeScript checking,
changed-file ESLint, standard API generation/drift checks and diff checks passed.
Application and test content is committed before publication; the final handoff records
its exact tested revision. No full frontend/backend suite or production build was run.

The first consolidated development run
[38049647846](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38049647846)
at `e019a214d8c264780a1346cb088ab5de945b8487` exposed one existing DailyReview
test rendering Dashboard without its now-required auth context: **992 passed, one
failed across 135 selected files**. Its fixture now explicitly supplies an absent
account context; all DailyReview assertions remain, and separate native Dashboard
tests still exercise authenticated account changes. The affected four-file selection
passes **49 cases**. This is a test-fixture correction, with no application source
change. The final handoff records its revision and subsequent focused results; the
earlier run is not green acceptance of the corrected revision.

### Migration-head correction after 7fc21db

The subsequent published head `7fc21db00a0527ee018f3fd1c478da64bcc713c0`
only adds the autonomous operating contract. Its automatic development run
[38053185017](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38053185017)
reported **785 passes and 15 failures** in seven modules whose current-head
expectations still named a9. This correction changes those expectations to
`a10blofinactivity001` and explicitly checks a10's a9 parent and a9's a8 parent.
Historical revision checks, receipt/plan/document preservation, refusal assertions,
and upgrade/downgrade/re-upgrade paths remain. No historical migration or production
source changes. The staging smoke instructions remain unchanged.

The corrected test files, committed with this section, executed on a fresh disposable
loopback **PostgreSQL 17.11** cluster: **32 passed in 120.72 seconds; zero failures,
skips or deselections**. This includes the previously failing 15 cases. All seven
changed files pass Ruff and `ruff format --check`. The PR description and operational
handoff record the exact publication SHA; test-file hashes are checked against it.

```sh
cd backend
.venv/bin/pytest -q -o addopts='' -ra \
  tests/test_journal_trades_alembic_empty_tenant.py \
  tests/test_knowledge_file_migration.py tests/test_knowledge_indexing_migration.py \
  tests/test_manual_demo_migration.py tests/test_phase2_4_alembic_postgres.py \
  tests/test_phase8_learning_persistence.py tests/test_release_wave002_migrations.py
```

`PHASE1_POSTGRES_URL`, `AT028_POSTGRES_URL`, `KNOWLEDGE_POSTGRES_URL` and
`INDEXING_MIGRATION_POSTGRES_URL` all pointed to that isolated `alphatrade_test`
database. Counts by module in the command's order: **2, 2, 1, 4, 6, 15, 2**.
The cluster was stopped after verification. This is focused repair evidence;
it does not establish complete backend or hosted release acceptance.

Standard generated full-schema SHA256:
`250caeb28a2088d71e05269837f98f8ce864bc2811e577d793acaf4dac68fe4e`.
The scoped PR239 contract hash matches
`14b14a34c77f598793207a1fa2485e5572b2afd8eee92f86bc2c1e2f5cb76307`;
all stored scoped route/component schemas equal the combined generated export.

Local backend selection:

```sh
cd backend
.venv/bin/pytest -q -o addopts='' tests/test_backend_ci_scope.py \
  tests/test_blofin_activity_provider.py tests/test_blofin_activity_worker.py
```

Historical `18e66dac` result: **70 passed, one failed**. An isolated six-case worker
run also failed its watcher-progress assertion. The diagnostic measured 2.045 seconds
inside the watcher allocator collection against a one-second wait; that earlier
reported owner success did not accept the observed integration failure.

The BloFin owner supplied `769e78a46fbce888810060f8d09cbe4d93f81c31` and
[its correction handoff](../blofin_activity_worker_integration_handoff.md).
Its test controls allocator cost while preserving one-second progress waits, checking
two completed watcher cycles and one activity thread while both cycle/cleanup can
remain blocked. Separate tests exercise real memory cleanup. No production worker
source, poll interval or retry/shutdown behavior changes. Before integration, the
31-case owner selection independently passed here, with two unrelated PostgreSQL lease
cases explicitly deselected and no skips. An independent shared-lock negative control
failed both variants at watcher_progress.wait(1), as expected; the correction does
not accept serialized worker behavior. This resolves the timing-test blocker without
skipping it or weakening its coordination assertions. The final handoff binds the
integrated focused backend result to the exact candidate revision.

PR239's reported **371 distinct focused passes** were reconciled as 107 correction
cases plus 264 existing regressions, with repeated cases not counted again. Local
collection independently reports **371 cases**. This verifies the ledger inventory,
not independent PostgreSQL execution. During that earlier integration, no disposable
PostgreSQL was available and Docker socket access was denied. The subsequent scoped
migration repair uses a new locally extracted PostgreSQL cluster; it does not rerun
the full 371-case identity/snapshot/activity inventory. CI supplies
`BLOFIN_ACTIVITY_TEST_POSTGRES_URL` to the existing
PostgreSQL fixture so those cases execute rather than silently skipping.

Chromium fixtures exercise the actual Dashboard/Journal routes and actual Agent page
using deterministic API/Web Speech responses. They do not prove native microphone,
Safari/physical iPhone, live BloFin coverage or authenticated hosted behavior. The
final handoff records browser counts and any integration-smoke failures separately.

## CI trigger policy

Ordinary PR/push and manual dispatch with both booleans false run focused backend,
frontend drift/lint/type/focused unit, and deployment-safety checks. All six job names
are retained; failures propagate. Twelve event/input combinations are tested against
the actual workflow expressions. Superseded PR runs cancel through concurrency;
manual runs use separate groups and are never displaced by development runs.

Only explicit manual `combined_validation=true` or `full_backend=true` enables the
complete frontend unit/build, evaluations, Docker and production-browser jobs.
Only `full_backend=true` runs complete backend acceptance. Both inputs default false.
Skipped combined jobs are not acceptance. No dispatch/rerun was requested or executed.
No branch-protection configuration was changed.

Read-only inspection found the corrected feature runs already completed:
PR239 [38047672083](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38047672083)
failed generated drift; PR238
[38048249178](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38048249178)
failed two legacy browser assertions. Combined regeneration resolves drift locally;
existing integration assertions now use the mounted voice region and explicitly open
Strategy options. These feature runs do not accept the combined candidate.
GitHub Actions cancellation is unavailable through current tools/API network access;
no active superseded run was observed among the inspected feature revisions.

## Migration order, preparation and coordinated rollback

Static Alembic inspection: one base, 78 reachable revisions, one current head
`a10blofinactivity001`, with continuation
`a8agentcapture001 -> a9knowledgeoutbox001 -> a10blofinactivity001`.
Historical migrations are unchanged; a10 adds three activity tables and no backfill.
The earlier activity-specific database evidence remains owner-reported. The scoped
migration-head repair separately executes the seven affected regression modules on
disposable PostgreSQL, including historical upgrades, downgrades and re-upgrades.

Before staging, review this exact combined head and the consumer/worker corrections.
In the approved coordinated window, drain old synchronous ingestion writers before
introducing the a9 API/outbox consumer. Apply a9 then a10 once; align API, frontend
and existing paper worker to the same reviewed commit. Preserve actual operator
settings/credentials, never substitute render.yaml defaults. Knowledge indexing and
native activity opt-in require their separate authorization; do not run a separate
activity service alongside the existing worker component. Disabled indexing leaves
pending documents and cannot establish indexing-readiness acceptance.

Rollback sequence:

1. Stop/drain the optional activity cycle and knowledge consumer, and drain new writes.
   Preserve existing Watcher, Telegram, kill-switch and exchange settings through the
   coordinated worker window; do not reset operator configuration.
2. Export/checkpoint newly admitted indexing jobs and native history. Retain additive
   a9/a10 schema and data while rolling application revisions back together.
3. Use a separately qualified migration-aware rollback image/startup. Baseline b165b92
   automatically runs `alembic upgrade head` but lacks a9/a10; it cannot safely boot
   against the new migration revision. Do not bypass its migration guard.
4. Align API/frontend/worker rollback revisions and API binding, run health/read-only
   gates, then restore only the approved consumer posture. Never combine old synchronous
   ingestion writers and the new indexing flow.
5. Destructive a10 downgrade to a9 requires separate authorization and an activity-data
   export; it drops those three tables. Rolling back further must also preserve the a9
   outbox/history. No hosted upgrade or rollback has been executed.

The [staging smoke sequence](staging_smoke_sequence.md) deployment prerequisite
now explicitly requires the a10 head; existing behavioral
smoke assertions remain unchanged. See [release qualification](release_qualification.md)
for the concrete migration-aware rollback and staging sequence.

## Hosted blockers and readiness

| Workstream | Implemented | Integrated | Verified here | Deployed/activated |
| --- | --- | --- | --- | --- |
| CI cost controls | Yes | Yes | Policy/event tests | Published workflow only; no manual run |
| Browser voice | Owner complete | Corrected af672dff | Focused components and simulated Chromium | No new deployment; native microphone unverified |
| Native backend activity | Owner complete | Corrected 769e78a4 | Provider/worker/static contract; PostgreSQL inventory only | No new deployment or activity opt-in |
| Dashboard/Journal native views | Yes | Yes | Focused component/account/browser fixtures | No |
| Generated client | Yes | Yes | Generation, drift, strict validation/type checks | No |
| Release acceptance | Preparation only | Candidate assembled | Staging/full acceptance unexecuted | No |

Known existing frontend target: Vercel project `alpha-trade-ai`, project
`prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ`, team `team_LNgcEGBkqntUnTrktNjFzh7a`,
alias `https://alpha-trade-ai-eight.vercel.app`. Its staging API binding is unverified.
The prior observed API/worker/frontend revision remains
`b165b92276346f0e0fe3ccdbd2bec3443dc75d40`; this task does not refresh or deploy it.

Missing hosted access:

- Render exact-commit deploy and consumer-drain/settings access for existing API
  `srv-d8fvbcd7vvec739mc060` and worker `srv-daqej7o473hc73fsc1k0` in workspace
  `tea-d7hn0fvavr4c73f62c70`; configured-branch-only deployment cannot prove this head.
- Vercel project/team authorization (prior 403) and frontend/API binding visibility.
- HTTPS access to api.render.com, staging API, api.vercel.com and verified frontend.
- Authenticated synthetic staging tenant/session and scoped record-read evidence.
- GitHub Actions write/cancel API access; git transport and connector read access alone
  do not allow explicit cancellation.

**Not release-ready.** The published `7fc21db00a0527ee018f3fd1c478da64bcc713c0`
development run [38053185017](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38053185017)
reported **785 passes and 15 failures** from stale a9 current-head expectations.
The corrective candidate's affected migration regressions pass locally. Inspect its
own automatic focused CI; earlier results do not accept it. Complete supervising review
and authenticated aligned staging smoke, qualify rollback,
and obtain fresh required SFP diagnostics/evaluations. Only then explicitly dispatch
one full acceptance run under [.ai/RELEASE.md](../../.ai/RELEASE.md) with
`full_backend=true`, recording the exact run SHA and all executed/skipped results.
Earlier baseline CI38042313649 accepts only bda597c. No full backend acceptance link
exists for this candidate. The manual BloFin order incident remains undiagnosed.
No main merge, deployment, infrastructure, billing/provider activation, external order,
Telegram message or operator-setting change occurred.
