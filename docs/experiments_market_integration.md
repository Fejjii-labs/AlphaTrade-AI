# Next batch: authored experiments and durable research

PR244 uses `codex/experiments-market-integration`. PR237 remains frozen at
`3600ec89e48a6f3cf2f29b4468ab124d1219f627`; its staging smoke instructions are unchanged.
This milestone resumes published PR244 `ba627c69d8d08d266428e68a85b1aab80efda5e5`.
The final exact candidate is recorded in PR244 and the existing cloud handoff;
this document does not embed a self-referencing commit SHA.

| Included work | Reviewed feature SHA | Integrated scope |
| --- | --- | --- |
| PR241 | `6ca6a76201547524275266dfa22fdf12f5932b14` | Immutable bounded domain, approval/lifecycle API, a11 |
| PR242 | `88ce82ff620c8fd206154a2e8593d242bad83e5c` | Canonical public evidence and corrected acquisition clocks |
| PR243 | `8b9d1b83ee5c4082d87005afbfdfa8343d286a7e` | Published pure receipt-timing correction |
| PR245 | `93af3b27e2a5002b74e3dc38499d081d22dfe772` | Trusted native resolver foundation; admission stays closed |
| PR246 | `62a20bb3759b3972fe935b8f05e25c21a84e8224` | Bounded durable screening, authenticated reads, immutable a12 |

Both new contributions were fetched, reviewed against their contracts/handoffs and
qualified on disposable PostgreSQL before integration. No sibling branch was
rewritten. Integration corrections additionally close opening/position lineage,
local authoring validation and request recovery issues; they grant no runtime authority.

## Connected product path

The recovery lifecycle correction resumes reviewed `48ca2e68`. Its fixed acceptance
denominator is six: L1 actual login establishes the recovery session; L2 both draft
and screening retain requests after server commit with lost/malformed replies; L3
reload retains the exact body/key; L4 explicit recovery resolves the original record
without duplication or automatic retries; L5 logout off-panel and reauthentication/
account change invalidate old pending state; L6 storage denial cannot prevent auth
cleanup. **L1–L6: 6/6 accepted locally**, implemented and integrated; deployment and
live acceptance remain unexecuted. The older injected-token browser evidence did not
qualify L1/L3/L5; two actual-login browser regressions now close those gaps. This is
overlapping repair acceptance, not six additional independent product features.

The existing Agent/document capture is the authoring entry. A complete typed research
spec can be captured as JSON or a fenced JSON block. Every binding and parameter must
be explicit. Incomplete, conflicting or malformed recognized contracts remain invalid
through persisted proposal confirmation: generic keyword rules cannot conceal the
failure. Correction creates a new reviewable proposal. Existing explicit confirmation,
content hashes, tenant identity and immutable strategy versions remain authoritative.
A new TrendPulse strategy is `manual_review`; no executable compiler is registered.

The saved strategy page offers a collapsed bounded experiment draft using actual
`GET /strategies/{id}/versions`, `GET /execution/accounts/paper` and `POST /experiments`.
It requires an existing enabled paper account and a supported immutable authored
version. It takes explicit USDT bounds, Exploration/Validation and setup-observation
limits. One baseline copies the exact authored parameters/symbol/timeframes. Models
remain disabled. It neither registers an account nor invents a native proof or risk
envelope. Creating or starting a domain experiment does not activate trading.

The actual `/strategies` route opens the created experiment, shows compact cards,
latest lifecycle, recent activity and unavailable performance, and renders durable
TrendPulse screenings with status, rejection reason, receipt provenance and time.
Recent history is bounded to five rows; details contain technical IDs and evidence.
Back returns to screening history or experiment cards. Native samples, signals and
internal simulation remain separate; signals never become trades or portfolio returns.
Existing approval/lifecycle operations use exact versions and expected revisions.
Owner approval remains a server boundary; no UI approval bypass was introduced.

Draft and screening requests validate complete generated bodies before storing pending
state. Definite first-attempt application refusals release the editable form. Timeouts,
interrupted connections, malformed successful responses and unresolved concurrency
conflicts retain exact original payloads and keys. Explicit recovery never automatically
reissues transport. Rejection of a recovery attempt cannot prove the original did not
commit, so it retains that identity. Pending state survives a tab reload and is scoped
to organization/user/session/version. Authentication binds a non-secret persisted
UUID only after login or verified `/auth/me`; reload restores it for the same identity,
and token refresh preserves it. Fresh login, logout and confirmed account changes
centrally invalidate both recovery namespaces even with no panel mounted. The memory
generation remains exclusively a cancellation/remount fence. A recovery scope requires
successful identity persistence; failure cannot permit an unrecoverable mutation.
Storage denial or a failing listener cannot interrupt local token/marker/identity
cleanup or subsequent listeners. Foreign-tenant reads/writes return404.

Shared generation adds three screening operations plus authored versions/paper account
to the existing pilot clients. Full combined OpenAPI SHA256:
`1955b672b6ed3099a3043f4fe4d23419182880b5bd3d19e70b848de9cff279bb`.
Pilot SHA256: `2acfa70b817e7665fb05afa26eccd374e0a90478f62d7bc46795ab63f2bff681`.
Multiple path parameters and 201 success handling preserve existing signatures.

## Native trust and performance boundary

PR245 is a trusted-resolver foundation, not an attestation producer. Integration
requires a server-owned literal-true proof of a flat position before the opening
entry and a complete flat→open→flat exit ancestry with no unrelated inventory/fills.
The resolver rejects reducing/unknown entries, nonzero opening realized PnL, unproved
position lineage, foreign/manual activity and receipt/source mismatches. Existing
closing-fill aggregation counts each owned native fill once, preserves contracts,
known fee currency and unknown costs/PnL. Simulation never supplies native proof.
The pure contract SHA256 is
`0d2ca13605f9542c29e0232c6edf29883186b2cc8a95b0015386b0457c122870`.

No verified server-owned producer is installed. HTTP sample admission remains503,
no native performance endpoint is enabled and performance stays unavailable. Real
opening position, exit ancestry, contract metadata, fee/funding and PnL reconciliation
must be accepted from actual native evidence before admission can open. No exchange
order/read credential or operator setting was used for local qualification.

PR246's published untuned four-regime synthetic replay records192 no-setups/zero
signals. That report proves no strategy performance. The local browser separately
uses a tagged positive synthetic fixture to exercise persistence and recovery;
its research signal grants no execution authority or admitted sample.

## Migration order and retained-schema rollback

There is one head, `a12trendpulsescreen001`:

```text
a8agentcapture001 -> a9knowledgeoutbox001 -> a10blofinactivity001
                 -> a11experiments001 -> a12trendpulsescreen001
```

The a11 and a12 files are byte-identical to the reviewed feature revisions. Earlier
migration files are unchanged. Do not stamp the database or create a parallel head.
Hosted read-only metadata on10October verified Render PostgreSQL
`dpg-d8fuok57vvec739lm0u0-a` (`alphatrade-postgres-staging`, PostgreSQL18) at
`a8agentcapture001`: documents exist; outbox, native facts, experiments and screenings
do not yet exist. This is database metadata, not proof of the API's secret connection
binding; verify that binding securely inside the authorized release executor.

The disposable PostgreSQL17 retained-schema probe creates five documents/chunks,
pending/processing/ready/failed jobs and a deletion tombstone; native account/facts/
cursors; experiment versions/events/sample; and actual qualified, duplicate and
refused screening rows. Both plain b165 and bda packages refuse unknown a12. An
archived bda package with byte-identical a10/a11/a12 migration overlays performs a
no-op upgrade, passes mock API startup/health and disarmed worker probes twice,
and preserves exact row snapshots. Returning to the integrated source preserves
those snapshots. Retained snapshot SHA256:
`d1423d9f61b9b6ce6227e08ce2cc61f945d37ba624aef3be242079aed2ec7a31`.
This is local source/startup proof, not a built hosted image or armed-worker acceptance.

Coordinated rollback sequence:

1. Use the separately authorized existing write hold. Stop new writes, drain requests
   and current consumers, preserving captured operator settings and checkpoints.
   Prevent old synchronous ingestion writers from overlapping the outbox consumer.
2. Verify a restorable database/provider snapshot and tenant-scoped inventories.
   **Retain populated a12.** Its downgrade deletes screening history; a11 deletes
   experiment state and a10/a9 delete native/outbox state. Never use plain b165 as
   rollback compatibility proof, and never stamp or downgrade to hide a mismatch.
3. Select separately built/tested migration-aware API and worker images carrying
   every retained revision through a12, using the qualified bda overlay manifest.
   The worker must remain disarmed unless its existing authorized behavior has
   separately passed compatibility checks. Retain the schema during image rollback.
4. Coordinate API, worker and a verified compatible frontend artifact/API binding.
   Check exact installed commits, expected/database a12, paper health, ownership
   reads and retained row/cursor inventories before reopening writes.
5. Restore only the previously approved settings and one compatible indexing consumer.
   Resume existing jobs with their original identities, not duplicate ingestion.
   No order, Telegram message, provider activation or configuration reset is implied.

## Focused evidence and revision coverage

| Selection | Executed result | Revision/source coverage |
| --- | --- | --- |
| PR245 resolver qualification |71 passed,0 skipped|Exact93af3b27 before integration; superseded by hardened87-case selection for resolver behavior|
| Hardened native resolver |87 passed,0 skipped|Worker5681db9 → integrated1ff832a; source/tests/pure schema unchanged afterwards|
| PR246 screening/acquisition/API/migration |36 passed,0 skipped|Exact62a20bb before integration; screened backend source unchanged|
| Bybit evidence/CVD cache |118 passed,0 skipped|Worker0528b1e → integrated6c8da2a; deterministic test-only correction|
| AgentWorkspace voice/recovery component |45 passed,0 skipped|Workerd25e60f → integrated3f64c00; production voice unchanged|
| Captured authoring + existing confirmation |31 passed,0 skipped|Final root implementation:12 authoring cases +19 existing cases,79.15s|
| Retained a12 PostgreSQL rollback |1 passed,0 skipped|Final a12 probe,65.73s; no hosted-image claim|
| Research draft/screening components |20 passed,0 skipped|7 draft +13 screening cases after independent recovery review|
| Authored-strategy route |8 passed,0 skipped|Actual route component with complete typed prerequisites|
| Research generated client |9 passed,0 skipped|Final combined client; final summary fixture removes extra fields|
| Existing view/contracts/transport |28 passed,0 skipped|7 view +6 experiment +7 generated +8 transport cases; reuse valid unchanged-path evidence|
| Actual Chromium API/browser |2 passed,0 skipped;24.8s +17.3s|Synthetic document→confirmation→bounded draft→malformed-success recovery; real API restart; history/reload/tenant404/sample503|
| Auth-owned recovery correction |49 passed,0 skipped;7 files,9.72s|5 token,9 recovery-session,7 AuthContext,7 draft,13 screening and8 transport cases; new binding/panels supersede prior recovery coverage|
| Actual-login Chromium recovery |2 passed,0 skipped;51.3s|Real `/login`; committed malformed draft/lost screening; reload exact byte-for-byte bodies/keys; explicit recovery keeps counts/DTOs; off-panel logout/same-account and other-account login; foreign reads404|
| Empty-tenant safety bootstrap |19 passed,0 skipped|4 real PostgreSQL bootstrap/protection cases plus15 existing AT014 cases; synchronous status route owns commit/rollback; original route fails the bounded contention negative control|

These are focused results, not full backend acceptance. Initial PR244 CI38083209763
had928 backend passes and one cache failure; PR246 CI38084766737 completed418 backend
passes but one voice frontend failure. Their green jobs were inspected, not rerun or
attributed as acceptance of newer code. The two repaired regressions preserve actual
TTL freshness, concurrent collection/copy independence and actual voice readiness.
Strict mypy on the two authoring services and hardened resolver sources, changed-file
Ruff/format, frontend typecheck/ESLint, generated API/pure-schema drift and one-head
checks pass. Late authoring annotations/local variable renames preserve tested behavior.
Automatic focused CI38088701542 at e81bcb28 reported354 frontend passes and one
new recovery-test failure: the test clicked the pending Recover control before the
first request finished and it became enabled. Both analogous tests now await actual
readiness; all20 affected component cases pass. No product behavior changed. The
backend result and corrective candidate's focused CI are recorded separately in the
PR/checkpoint. No earlier run is acceptance of the corrected revision.

Reproduction uses explicitly disposable loopback PostgreSQL `alphatrade_test`:

```sh
# backend; never a hosted DATABASE_URL
EXPERIMENT_TEST_POSTGRES_URL="$disposable_url" .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_kill_switch_bootstrap_postgres.py tests/test_at014_persistent_kill_switch.py
EXPERIMENT_TEST_POSTGRES_URL="$disposable_url" .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_research_authoring_integration.py tests/test_strategy_conversation_foundation.py
PR237_ROLLBACK_POSTGRES_URL="$disposable_url" .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_pr237_migration_aware_rollback.py
# frontend
npx vitest run src/components/strategies/ExperimentDraftPanel.test.tsx \
  src/components/strategies/ScreeningsPanel.test.tsx src/lib/api/research.test.ts \
  'src/app/(app)/strategy-lab/[id]/page.test.tsx'
```

`backend/scripts/research_browser_fixture.py` refuses non-loopback/disarmed/mock
settings and overrides only the real screening service's acquisition dependency
with tagged synthetic receipts. It never changes production operator flags or makes
public-provider calls. Seed short-lived private auth using the existing loopback-only
seeder, start this fixture API plus the actual frontend, and run only the authoring
phase of `frontend/ui-tests/research-api.spec.ts`. Stop that owned API process, retain
PostgreSQL, restart the same source in a new process, then run only the restart phase.
An explicit fixture header proves the process changed; lack of prerequisites fails
these selected tests. Browser state restores once, so reload cannot resurrect a
successfully resolved pending request. Screenshots contain synthetic content only.
The existing lifecycle browser evidence on ba627 is retained for unchanged lifecycle
operations; it is not current hosted or full-suite acceptance.

For the recovery correction, reseed with the existing guarded seeder, then run only
`ui-tests/auth-recovery-api.spec.ts` using those same loopback API/frontend URLs and
`EXPERIMENT_BROWSER_AUTH_FILE`. It never injects tokens, cookies or recovery state.
Authentication waits for protected-shell readiness before subsequent navigation.
Both synthetic accounts start with persisted **blocked** safety rows; only newly
created disposable tenants are initialized, with no operator row changed. During
initial qualification, concurrent lazy `GET /risk/kill-switch` inserts for an empty
synthetic organization caused a transaction-ID lock and blocked the local event loop.
The fixture represents existing initialized account settings; production risk code
was unchanged at reviewed revision `11dc274c`. The subsequent bounded bootstrap repair
is qualified separately below, without preinitializing its test tenant. Verify the
smoke tenant's existing persisted safety
state through the authorized executor; never reset settings to manufacture a pass.
The initial private-file writer error and one session-loading/blocked-API browser
failure were diagnosed; the final selected run executes both cases with no skips.
Frontend typecheck/changed ESLint, seeder Ruff/format and diff whitespace checks pass.
At `11dc274c`, production backend, combined generated contracts and immutable migration
tree were byte-identical to `48ca2e68`. The new status-read correction changes only its
route; unchanged recovery, authoring, contracts, migrations and retained-schema rollback
evidence are reused, not rerun or attributed as new full acceptance.

## Hosting receipt and one release access packet

The existing read-only receipts below were last verified on 10 October 2026 in
Render workspace `tea-d7hn0fvavr4c73f62c70`. They are not a new live revision check:

| Target | Verified configuration and live revision |
| --- | --- |
|API `alphatrade-api-staging` / `srv-d8fvbcd7vvec739mc060`|Tracks main; Docker backend; predeploy `alembic upgrade head`; liveb165b92276346f0e0fe3ccdbd2bec3443dc75d40, deploydep-db4gdkeq1p3s73a395u0|
|Worker `alphatrade-paper-worker-staging` / `srv-daqej7o473hc73fsc1k0`|Tracks main; `python -m app.workers.paper_worker`; same liveb165, deploydep-db4gdkeq1p3s73a3975g|
|PostgreSQL `alphatrade-postgres-staging` / `dpg-d8fuok57vvec739lm0u0-a`|Available PG18; read-only head a8 and table-presence metadata verified|
|Frontend|Current target/artifact/revision/API binding **unknown**. Historical projectprj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ, teamteam_LNgcEGBkqntUnTrktNjFzh7a and aliashttps://alpha-trade-ai-eight.vercel.app are evidence to verify, not current deployment proof|

Never target the separate `ai-alphatrade-api-staging`. No deployment was attempted.
The available Render deploy connector accepts no commit; using it would deploy main,
not this candidate. No Render CLI/API credential, Vercel CLI/token or authenticated
synthetic staging token is bound to this executor. HTTPS probes to api.render.com,
the staging API and historical frontend returned proxy403. Vercel project listing
returned403 requiring reauthentication to scope `alphatrade-ai`; it does not establish
that the project/secret is absent. Read-only database access is available; write-hold,
container execution, backup/restore and migration execution are unqualified.

One concrete action packet for the existing release/access administrator:

- Bind the already established Render exact-commit executor (API/CLI or existing
  authorized release runner) to this workspace and the two exact service IDs, with
  HTTPS to api.render.com and https://alphatrade-api-staging.onrender.com. Current
  service-scoped MCP read access cannot select a deployment commit. Do not change
  tracked branches, merge main or create infrastructure to work around this.
- Reauthenticate the existing Vercel connector/CLI for the historical team/project
  scope; allow api.vercel.com and the verified frontend/protection path. Resolve
  the actual staging project/artifact/API binding before any promotion. Bind the
  existing protected-deployment access where required; never assume an alias is staging.
- Bind a scoped synthetic staging tenant/token through the executor's existing secure
  secret configuration, plus existing container/write-hold/backup/restore permissions
  for the verified API/worker/database. Record exact identity references securely;
  do not paste secret values into chat. Database reads already work and need no new
  generic credentials request.

This packet enables the blocked exact-ref deploy, frontend identity/binding verification,
coordinated backup/drain/migration and authenticated hosted smoke. It grants no runtime,
provider, exchange, Telegram, billing or kill-switch changes.

The 11 October bootstrap repair found no changed executor access: the scoped Render,
Vercel and staging-smoke environment credentials remain unbound, their CLIs are
unavailable, and the Render deploy connector still has no exact-commit parameter.
No failed HTTPS or Vercel probe was repeated. The remaining executable access action
is the same packet above; do not trigger a main-tracking deploy to substitute for it.

## Prepared staging order and workstream status

1. Supervising review freezes the exact PR244 candidate and accepts the rollback
   source/image manifest. Preserve frozen PR237 independently. Capture current operator
   flags/account identity/providers securely without resetting render.yaml defaults.
2. Through the established authorized release executor, verify API/worker database
   binding, restorable snapshot, existing write hold and tenant-scoped inventories.
   Drain old ingestion/API writers and worker consumers before migration. Stop at a
   concrete blocker if the existing process cannot select this SHA without main merge.
3. Exact-commit deploy API `srv-d8fvbcd7vvec739mc060`; its predeploy upgrades a8→a9→a10→a11→a12.
   Require one database head a12 and matching packaged expected head, paper health and
   exact installed SHA. Retire old synchronous ingestion writers before resuming writes.
4. Exact-commit deploy worker `srv-daqej7o473hc73fsc1k0` at that same SHA with captured
   existing command/settings. Verify one compatible outbox consumer and no old writer
   overlap. Previously disabled consumers/screening remain disabled; do not activate
   them merely to manufacture acceptance.
5. Build/deploy the verified existing frontend from the same SHA and existing approved
   API binding; verify team/project/deployment/environment, CORS/cookies and turn
   budget. Require API commit = worker commit = frontend Git source = frozen candidate,
   with database/code a12. No new project, alias guess or unreviewed main merge.
6. Execute the preserved [authenticated staging smoke](reviewer_wave/staging_smoke_sequence.md),
   plus this actual authoring/draft/history/recovery/tenant journey with synthetic content
   and read-only native checks. Disabled screening/indexing means that operation remains
   unaccepted; this task grants no activation. Keep the manual BloFin order incident
   undiagnosed absent actual request/server evidence.
7. Complete fresh required SFP diagnostics and naturally scheduled successful evaluations
   for approved scopes under existing settings. Neither local synthetic signals nor
   a startup heartbeat prove live scheduling or native performance.
8. After review, aligned staging acceptance and fresh SFP prerequisites, freeze the ref
   and dispatch the **single** final `.ai/RELEASE.md` gate with `full_backend=true` only
   under the explicit final acceptance request. Record exact SHA, all jobs/counts/skips.
   No such dispatch is justified while these hosted prerequisites are blocked.

Routine required PR checks remain focused; superseded PR runs cancel, and expensive
frontend build/evaluation/Docker/browser/full backend remain explicit manual gates.
No workflow protection is weakened. The attached playbook reconciles the existing
AGENTS/workflows/checkpoint: standing authority persists, routine implementation and
batched publication do not require approval loops, the owner collects up to two
workers directly, and missing access pauses only its operation. Protected release
and financial boundaries remain intact. Mac/iCloud access is unavailable here;
cloud publication source equality is verified separately without a downstream sync claim.

| Named milestone | Implemented | Integrated/local accepted | Deployed | Live accepted |
| --- | --- | --- | --- | --- |
| Focused CI repairs |2/2|2/2|Unexecuted|Unexecuted|
| Authoring-to-research journey |6/6|6/6|0/6|0/6|
| Trust and privacy |4/4 safeguards|4/4 safeguards; native performance unqualified|0/4|0/4|
| Migration/release preparation |4/4|4/4|0/4|0/4|

All local capabilities remain undeployed. Remaining external dependencies are the
single access packet, verified rollback images/armed-worker compatibility, supervising
review, aligned staging smoke/fresh SFP, final full gate and a separately verified
native attestation producer before any sample/performance activation.

## Empty-tenant safety bootstrap repair — 11 October 2026

Baseline: `11dc274ccdb1207d9fe8ded614f54007c34592bb`. The reviewed authentication
recovery correction and its unchanged evidence remain accepted. This bounded repair
owns the status-read transaction and synchronous database scheduling; it changes no
operator defaults, execution authority or historical migration. Fix these four proof
requirements before publishing, using authenticated requests and disposable PostgreSQL:

1. Concurrent status reads for a tenant with no kill-switch row complete within a
   bounded timeout, including actual PostgreSQL unique-insert contention.
2. Unrelated API requests stay responsive while that database contention exists.
3. Initialization commits the existing intended state, observed through a fresh
   session, with exactly one organization row; failures release their transaction.
4. Existing operator state, optimistic versions, tenant boundaries, mutation audits
   and fail-closed execution protections remain intact.

These are repair verification requirements within the unchanged original 16-criterion
milestone, not additional product features. No fixture preinitialization or increased
production timeout qualifies this repair. Deployment/live acceptance and the single
final full backend gate remain unexecuted until their existing prerequisites pass.

Retained-a12 rollback evidence still proves schema/data compatibility for its exact
tested source. It does not prove empty-tenant status responsiveness in an older API
image. Reverting to the old asynchronous status handler would reintroduce this defect:
a qualified API rollback image must retain this correction or an equivalent reviewed
transaction boundary and pass the empty-tenant proof. Preserve populated a12 and the
existing coordinated API/worker/frontend image prerequisites; never downgrade or stamp
the database to make an old image start.

**Result:4/4 repair requirements locally accepted; implemented and integrated.** The
four new PostgreSQL cases and15 existing AT014 cases passed (19 executed,0 skipped).
The first authenticated GET owns the initially absent row; a test-only insert hook
holds its transaction. A second authenticated GET must show an actual PostgreSQL
`Lock` wait in `pg_stat_activity`, while authenticated `/auth/me` finishes before
release. Both reads then return identical default state; fresh sessions see one row
and its unchanged ID. A synthetic commit failure rolls back and permits a later
normal read to initialize. Existing operator activation/deactivation, optimistic
conflicts, owner-only changes, tenant isolation, audit events, global blocking and
execution refusal remain covered. A separate service test proves the caller can
still roll back bootstrap; no service-wide commit was introduced.

The original handler produced one **expected negative-control failure**: the second
request could not start on the ASGI loop. An independent watchdog bounded that
failure. PostgreSQL lock/statement limits and synchronization hooks exist only in the
disposable tests; no production timeout was increased. Changed-file Ruff lint/format,
route-only mypy and diff checks pass. Public request/response contracts and the API
description are preserved. The shared service, authentication recovery, migrations,
watcher, Telegram and operator configuration remain unchanged. No hosted operation or
full backend acceptance was executed.

## Active milestone contract — 10 October 2026

Accountable owner: current integration agent; two isolated repair workers maximum.
Starting candidate: ba627c69d8d08d266428e68a85b1aab80efda5e5. Published contributions
under review: PR245 93af3b27e2a5002b74e3dc38499d081d22dfe772 and PR246
62a20bb3759b3972fe935b8f05e25c21a84e8224. PR237 stays frozen at
3600ec89e48a6f3cf2f29b4468ab124d1219f627.

The following denominator is fixed before implementation. Report each milestone's
accepted/total separately for focused implementation, integrated local journey,
staging deployment and live acceptance. A local pass establishes no hosted pass.

- **Focused CI repairs (2):** R1 within-TTL Bybit consumers share one collection and
  TTL expiry still refreshes; R2 voice recording waits for actual conversation
  readiness and uses the same composer with an observable transcript.
- **Authoring-to-research journey (6):** J1 existing Agent/document capture persists
  an authored strategy; J2 that strategy can seed a bounded Exploration/Validation
  draft through actual APIs without an alternative authoring wizard; J3 existing
  approval/lifecycle boundaries persist and domain start grants no execution;
  J4 actual Strategies renders durable screening status/reasons and compact recent
  history with Back and technical evidence in details; J5 screening request identity
  survives an ambiguous reply and explicit recovery without automatic duplicate
  acquisition; J6 reload/fresh server sessions preserve screening outcomes and
  deduplicate the same request across restart.
- **Trust and privacy (4):** S1 account/tenant changes clear scoped UI state and
  foreign tenants cannot read/write these experiments or receipts; S2 PR245 stays a
  trusted-resolver foundation with HTTP native admission closed absent a verified
  server-owned attestation producer; S3 opening-entry/exit lineage, contract units,
  native position/monetary attribution and simulation separation are verified in
  focused contracts, with unavailable performance kept explicit; S4 synthetic replay
  with zero signals is labelled research evidence and never performance.
- **Migration and release preparation (4):** M1 reviewed contributions integrate
  with one immutable a8→a9→a10→a11→a12 ancestry and combined generated contracts;
  M2 disposable PostgreSQL retained-schema rollback preserves documents, jobs,
  native activity, experiments and actual screening history while plain old code
  refuses the new head; M3 existing instructions/checkpoint adopt autonomous delivery
  without routine approval/manual relay loops while retaining protected operations;
  M4 one focused hosting pass identifies exact targets, blocked operations and the
  coordinated staging/rollback sequence without changing settings.

Authority: focused implementation, isolated worker branches, integration and batched
publication to PR244. No main merge, deployment, provider activation, financial
transaction, external message, credential/operator mutation or full CI dispatch.
Final full backend gate remains after supervising review, staging and fresh SFP
prerequisites under .ai/RELEASE.md. Native attestations/execution are not authorized
by this milestone. No new paid model/resource, no new management system.

## Parallel delivery supplement — 11 October 2026

Stabilization PR244 stays at `ba47a228c426da5d2fe8ab38e5a15cba2aa54620`;
its19 local and1072 backend/399 frontend focused CI passes remain evidence for that
revision. Frozen PR237 remains3600ec89. New features live on the separate
`codex/agent-knowledge-ui-integration` branch; no deployment authority is added.
No existing Agent/compact UI assignment is active in the available worker/thread
snapshot. Two isolated workers begin from the shared contract commit below.

Ownership before edits: A owns AgentWorkspace, Agent/voice components and supporting
Agent/capture services and tests. B owns navigation, Dashboard, Strategies,
Journal/Knowledge and Settings routes/components/tests, excluding A's files.
Owner owns shared generated API artifacts, shared primitives, migration sequencing,
real API/browser integration, publication and this checkpoint. Worker branches are
`codex/agent-voice-records` and `codex/compact-workspace`; only the owner integrates
reviewed commits. Workers do not edit shared checkpoints, migrations, workflows,
operator configuration or each other's components.

Agreed existing contracts: `/agent/turns` remains the shared coordinated turn path;
use existing model routing and explicit approval/recovery behavior. Saved records use
`/agent/saved` and the existing journal/rules/strategies/news_analysis/lessons categories,
revision checks, private source ancestry and mixed-content bounds. No new parallel
persistence system, provider activation or automatic ambiguous retry. Journal links
use `/journal?saved=<id>`; other categories use
`/journal?tab=knowledge&category=<category>&saved=<id>`. These are stable links to
owned persisted records, not proof of strategy approval. Strategies may show captured
strategy notes but must keep authored configuration/experiment approval separate.
Lessons stay within Knowledge; technical evidence belongs in expandable details.
Browser speech controls are browser-dependent and must offer actual errors/text
fallback; do not relabel backend contract-only audio endpoints as transcription.
BloFin native facts and internal simulation remain separate with truthful freshness,
units and unavailable monetary/performance values.

Fixed supplement acceptance (scope explicitly added by the current user request):

- **Agent/voice A1–A5 (5):** A1 compact text/upload/voice controls use one composer;
  A2 acknowledged replies render immediately and persist across reload; A3 actual
  transcription errors and unavailable speech allow text fallback; A4 mixed content
  creates durable category summaries and truthful saved-record links; A5 ownership,
  exact ambiguous recovery/deduplication and existing routing/approval bounds hold.
- **Compact UI B1–B5 (5):** B1 page width, compact controls, one heading and parent
  navigation without nested scrolling/visible IDs; B2 statistics stay on Dashboard,
  native activity separate from simulation; B3 strategy/risk configuration belongs in
  Strategies; B4 Settings contains monitoring/notifications/account/system and
  preserves values; B5 Journal/Knowledge share navigation while retaining categories,
  Lessons inside Knowledge and expandable details with clear Back navigation.
- **Owner integration I1–I4 (4):** I1 stabilization and feature branches remain
  separate; I2 existing shared contracts and immutable migration ancestry align;
  I3 focused reachable browser/API/persistence journeys cover changed paths; I4
  reviewed contribution references/exact integration SHA/checkpoint are published.

Supplement implementation and integrated local acceptance: **A 5/5, B 5/5,
I 4/4**. The unchanged original milestone remains 2/2, 6/6, 4/4, 4/4; these are
separate fixed checklists, not added claims of hosted acceptance. Deployment and
live acceptance are **unexecuted**. The feature candidate is ready for source review,
not release-ready. Existing access receipts are reused without repeating failed probes.

Reviewed contributions (collected directly by the owner):

- A: `codex/agent-voice-records`, `545253841c098516f22d17071f6800ed87cb15a7`,
  integrated as `185f7ff`. Compact Agent thread/composer and durable category
  receipts; summary/link first, corrections inside details. Existing upload,
  browser recognition, immediate reply, routing and recovery behavior is reused.
- B: `codex/compact-workspace`, `de9c6857a892cf15c5b0a528b89a46b84aa37077`,
  integrated as `6b9deec`. Captured strategy notes are separate from configuration
  and approval; category links and parent navigation are consistent. Strategies,
  Settings and Risk remount their local data on account/session invalidation.
  Dashboard statistics and native/simulation separation remain intact.

Focused evidence, with no unexpected skips:

| Scope | Executed result | Revision/source covered |
|---|---|---|
| A Agent/voice/receipt components | 140 distinct passing cases | `5452538`; 139 initial passes plus one new lifecycle case, final targeted 3 passes with 45 intentionally filtered |
| A capture persistence | 18 distinct passing cases | `5452538`; 17 unchanged cases plus one corrected real PostgreSQL HTTP journey |
| B routes/navigation/native activity | 77 passing cases | `de9c685`; 75 route/navigation/native cases plus 2 document-card cases |
| Actual login and committed ambiguous turn | 1 passing Chromium case | Combined application at `20ba4e5` |
| Real API process restart and category routes | 1 passing Chromium case | Combined application at `20ba4e5` |
| Loaded desktop/mobile views and account change | 1 passing Chromium case | Same application, final browser/artifact-only follow-up |

The first browser fixture run omitted the login redirect wait and failed before
the Agent journey. It was corrected to await the actual route transition, without
increasing product timeouts. Initial screenshot capture caught loading placeholders;
the final dedicated case awaits persisted records before capture. Capture-test fixture
corrections supplied real membership rows, a required StrategyDraft and the existing
private reservation marker; production validation/deduplication assertions remain.

The browser uses actual login forms, application routes, generated turn client and
PostgreSQL. It does not inject authentication tokens into the browser. Only the
guarded loopback harness supplies deterministic model completions; production
CaptureModel, ModelRouter, validation and turn/persistence services still run.
The first turn saves three categories before its reply is deliberately malformed;
reload makes no POST, explicit recovery retains the exact body/key, and the second
turn saves the other two categories. A real API process restart preserves all five
summaries; private reads return404, explicit replay creates no extra record, category
details have Back links, and actual logout/login finishes with the other account's
empty strategy-note history. PostgreSQL HTTP coverage additionally proves document
ancestry, same-tenant different-user privacy, versioned changes and zero orders.

Browser reproduction uses the existing disposable seed/harness, ports8013/3013,
`SYNTHETIC_CAPTURE_BROWSER=true`, disarmed local/mock settings and a mode0600
synthetic auth file. Select only these phases in order, restarting the API between
the first two (same database):

```sh
PLAYWRIGHT_SKIP_WEBSERVER=true \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
PLAYWRIGHT_API_URL=http://127.0.0.1:8013 \
PLAYWRIGHT_BASE_URL=http://127.0.0.1:3013 \
EXPERIMENT_BROWSER_AUTH_FILE=/tmp/alphatrade-next-postgres/delivery-auth.json \
npx playwright test ui-tests/agent-knowledge-api.spec.ts \
  --project=chromium --workers=1 --grep '<selected phase>'
```

Phase selectors: `actual login saves`, then `fresh API process`, then
`compact screenshots`. All three exit0. Physical microphone/live ASR and live paid
classification remain unexecuted; deterministic adapter errors/transcripts prove
software fallback, not external speech service performance. Synthetic experiment
samples do not establish strategy returns. Screenshots in
`docs/screenshots/experiments/delivery-{knowledge,strategies,strategies-mobile}.png`
show synthetic data only, explicit unavailable performance and preserved paper pause.

Combined typecheck, changed-file ESLint/Ruff/format and diff checks pass. The API
generator drift check regenerates the combined contract in memory and finds no
change: full OpenAPI SHA256
`1955b672b6ed3099a3043f4fe4d23419182880b5bd3d19e70b848de9cff279bb`.
One head remains `a12trendpulsescreen001`. Production backend and immutable migration
files are byte-identical to frozen ba47; retained-schema rollback evidence is reused
for those unchanged paths. Qualified rollback API images must still retain the safety
bootstrap correction. No old-image compatibility or hosted deployment is inferred.

CI policy is unchanged: required focused backend/frontend/deployment-safety checks;
superseded PR cancellation; expensive combined checks and full backend are manual
only. No manual dispatch or green job repeat. Frozen ba47's CI is not acceptance of
this new feature candidate; any automatic result must name its exact head.
The operation-specific Render/Vercel/synthetic-tenant access packet and coordinated
API→worker→frontend sequence above still apply. Frontend current target/revision/API
binding remains UNKNOWN. PR237 smoke instructions, operator settings and trading
boundaries are preserved. Next: review the published integration revision, bind that
single existing access packet, then qualify authorized staging and fresh SFP before
the single explicitly requested full backend release gate.

## Agent quality, native outcomes and viewport completion — 11 October 2026

This bounded next batch starts at reviewed PR247 `0d3c79ed90c00ce57da4e0e74bbfeb298e5408b9`
(on accepted PR244 `ba47a228`). Both reviewed branches remain fixed; work is isolated
on `codex/agent-outcomes-integration`. Existing passing evidence is reused.

Ownership agreed before edits: owner owns frontend AgentWorkspace, its viewport
helper/tests, mobile experiment cards and Journal/Knowledge filters, shared API
artifacts, migration sequencing, integration browser checks and existing checkpoint.
Worker A owns backend Agent/capture services, routing/classification evaluations and
related backend tests; no owner frontend, native outcome, shared migrations or
checkpoint files. Worker B owns native outcome producer/resolver/reader, related
backend HTTP integration and tests; no Agent/frontends, shared migrations or checkpoint.
Workers propose any shared schema/migration needs before edits; owner serializes them.

Existing shared contracts remain authoritative: five saved categories and at most
three independent capture entries; natural language evaluation has no category tags;
existing configurable ModelRouter, validation and usage accounting are reused.
Synthetic or oracle completions are not measured model quality. No invented model ID,
paid-provider activation, new spending budget or silent fallback quality claim.
Native evidence must derive from verified UID/account, canonical server command/plan
and immutable strategy/experiment bindings, complete position/order/fill ancestry and
verified monetary semantics. Echoed client IDs, retrospectively assigned experiment
ownership or caller-supplied truth flags never qualify evidence. Unknown/incomplete
facts remain unavailable; native facts/samples deduplicate separately from simulation.
Producer implementation does not activate trading. Existing reader contracts should
be reused; owner regenerates combined public contracts. Historical migrations are
immutable; a12 remains head unless a justified additive migration is coordinated.

Fixed next-batch acceptance (prior checklists remain unchanged):

- **Owner UI U1–U3 (3):** U1 long desktop/mobile conversations follow latest while
  pinned, preserve intentional older reading, offer clear Latest navigation and a
  reachable composer; U2 mobile experiment cards remain compact without hiding
  lifecycle/source/unavailable performance; U3 Knowledge filters are compact and
  irrelevant pagination is hidden without losing paged data access.
- **Agent quality Q1–Q4 (4):** Q1 realistic untagged journal/strategy/rule/news/
  analysis/lesson/mixed/ambiguous evaluation corpus and existing pipeline; Q2
  demonstrated capture gaps corrected with faithful source/uncertainty validation;
  Q3 routing, usage, ownership and durable mixed records preserve existing bounds;
  Q4 measured configured approved-provider classification results under existing
  spending limits (unavailable measurement remains an explicit unmet criterion).
- **Native outcomes N1–N5 (5):** N1 server-owned verified account/command/plan/
  immutable strategy binding; N2 complete opening/exit/position ancestry, partial
  fills and verified fee/PnL semantics; N3 existing reader consumes qualified evidence
  and incomplete/missing facts remain unavailable; N4 PostgreSQL durability,
  tenant isolation and restart/concurrency deduplication; N5 no client-ID ownership
  inference, simulation mixing or execution/operator activation.
- **Integration/release I1–I3 (3):** I1 reviewed worker refs integrate separately from
  stabilization with aligned contracts and one migration head; I2 affected real
  browser/API/persistence checks and exact revision evidence; I3 checkpoint/batched
  publication and one precise release setup packet, advancing only executable access.

Current local acceptance is **13/15**: U3/3, Q3/4, N4/5, I3/3.
The4e5 baseline was12/15; N1 is now locally qualified by the reviewed coordinator
continuation below. Q4 and N2 remain unmet.
Implemented, integrated, deployed and live acceptance are distinct. No full backend CI during iteration; no main merge,
deployment workaround, exchange order, Telegram message, provider activation or
operator-setting change. Existing inaccessible hosting operations are not re-probed.


### Collected contributions and focused acceptance

Both workers were collected directly, reviewed and integrated on the isolated branch:

- Agent quality `95fb5ec9ff8772a5c92cd6d2996025420e2af01e` on
  `codex/agent-natural-quality` → `dae7c66`. Fourteen realistic untagged oracle inputs
  use the actual CaptureModel/ModelRouter, source checks and durable capture pipeline.
  Duplicate suggestions and repeated revisions to one target now fail before writes;
  independent mixed entries still save. **37 distinct focused passes**, zero unexpected
  skips: 17 quality cases, 19 unchanged affected persistence passes and the corrected
  one-case HTTP telemetry/usage replay proof. The initial two behavioral failures
  reproduced the defects; one later test-field lookup error was repaired and its case
  passed. `/tmp/agent-quality-{before,after,persistence,persistence-repair,strategy-source}.xml`
  records exact runs. Q1–Q3 locally accepted; **Q4 unmet**. Human-authored oracle
  completions establish no measured LLM classification accuracy. Mock local settings
  have no securely bound approved route credential. Routine measurement within the
  existing approved test budget is authorized; a verified balance/price/context receipt
  is still required before transport.
- Native outcomes `adfaf69b1946fcf3b80faa64ebd0d95241fa14c4` on
  `codex/blofin-trusted-outcomes` → `84b2f96`. Guarded server-only services register a
  canonical plan origin before commands and capture signed verified UID, account-wide
  flat inventory and matching contract metadata before dispatch. Explicit caller-owned
  transactions, independent committed-origin reads, immutable strategy/plan hashes,
  original timestamps and PostgreSQL locks preserve restart/concurrency identity.
  **119 distinct focused passes**, zero skips: earlier affected selection116 (87
  unchanged resolver +29 producer), final selection32 (31 producer +1 HTTP auth/admission).
  Source review corrected wall-clock timestamp ownership; an advancing-clock PG case
  proves the repair. Full commands and initial fixture/mapping repairs are in
  `docs/blofin_experiment_outcomes_verification.md`. **At adfaf69b: N3–N5 accepted; N1 partial,
  N2 unmet**. At that baseline there was no experiment coordinator callsite, complete direct
  protected exit ancestry or independently verified fee/PnL basis. Reader GETs are
  wired; newly producer-owned results clear eligible proof and money. Native HTTP
  admission stays503. No autonomous executor, producer schedule or exchange is activated.
- Owner `9edae66` plus shared-contract/viewport completion: page-level tail following,
  explicit Latest and Earlier navigation; messages beyond the first100 now load at
  the current tail. Deliberate older reading and earlier-page anchors survive replies.
  Saved receipts stay beside their own messages. Parent/composer observation accounts
  for status/recovery layout; queued scrolling checks current reading intent, including
  wheel, touch and keyboard input. Compact mobile experiment cards keep mode, lifecycle,
  source and unavailable performance. Knowledge search/category/refresh uses two compact
  rows; zero/single-page controls hide, but stale page and independent source access remain.
  **74 affected component passes** and **17 distinct generated-boundary passes**.
  After the mobile DOM-commit repair, three affected Agent history/cancellation cases
  passed again (46 intentionally filtered); these repeats do not increase the74 count.
  Client selection initially16passed/1fixture assertion failed; the repaired query-object
  assertion passed alone with3 intentionally filtered cases. No unexpected skips.
  Generator conditional guards preserve core availability fields, proof/lineage, exact
  decimals and unknown totals. Semantic identity, timing and count equality remain server
  checks; synthetic Pydantic-valid fixtures establish transport interoperability only.

Focused real Chromium checks use loopback PostgreSQL, real password login and persisted
130+ message history. Four distinct journeys are locally accepted: desktop/mobile
current tail, in-flight composer geometry, delayed committed acknowledgment while
reading older messages, Latest return, pinned reply and Earlier anchor; compact mobile
cards/Knowledge filters; and combined API readers with actual login, foreign-tenant404,
anonymous401 and simulation409. The final combined API process is checked by its
`X-Synthetic-Fixture-Process` identity. Initial browser assertions incorrectly expected
Send enabled after clearing the draft, matched full multiline text instead of a stable
message identity, and missed the Sending… label. Those test errors were corrected;
no timeout or behavior assertions were weakened. The final mobile case exposed an
Earlier-page restoration frame preceding React DOM commit; restoration now runs in
a layout effect after the prepended messages commit, with conversation/generation guards. An old local API process initially
prevented rebinding and returned404 for new routes; it was stopped and the combined
process explicitly verified. This is synthetic local acceptance, not model quality,
physical microphone/ASR or live exchange performance. Mock model-unavailable replies
and capture-unavailable status remain truthful in conversation screenshots.

Browser commands from frontend, using private loopback-only auth:

```sh
PLAYWRIGHT_SKIP_WEBSERVER=true \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium \
PLAYWRIGHT_API_URL=http://127.0.0.1:8014 \
PLAYWRIGHT_BASE_URL=http://127.0.0.1:3014 \
EXPECTED_SYNTHETIC_API_PROCESS=<verified-current-process> \
EXPERIMENT_BROWSER_AUTH_FILE=/tmp/alphatrade-next-postgres/viewport-auth.json \
npx playwright test ui-tests/conversation-viewport-api.spec.ts \
  --project=chromium --workers=1 --grep '<selected affected cases>'
```

Evidence receipts: `/tmp/alphatrade-next-postgres/viewport-browser-final.log` (earlier
long-thread qualification), `combined-browser-corrected.log` (new reader case accepted),
`combined-browser-viewport.log` (desktop passed; mobile exposed the anchor defect),
`combined-browser-anchor.log` (both corrected viewport cases passed,46.0s);
`/tmp/quality-outcomes-agent-final.log` (three affected component rechecks passed).
The compact case terminal receipt
records1passed. Screenshots: `conversation-{desktop,mobile}.png`,
`compact-experiments-mobile.png`, `compact-knowledge-mobile.png` in existing screenshot
folder. Backend scoped OpenAPI test1pass; client XML `/tmp/quality-outcomes-client*.xml`;
contract XML `/tmp/quality-outcomes-contract.xml`. Generated full API SHA256 is
`56b2cbea880a93b6e5397fa9a077e9e9846cf07854e357d20af580204497a77b`.
Typecheck, changed-file ESLint/Ruff/format and generated drift check pass.

### Release boundary and the one setup action

**Not release-ready.** U/Q/N/I local counts do not include deployment or live acceptance.
Q4 and N2 remain as named gaps; N1 is now locally qualified below. Prior PR24714/14 and stabilization/bootstrap evidence
remain unchanged, overlapping evidence. Frozen PR244ba47, PR2470d3 and PR2373600 are
not moved. The current candidate/automatic focused CI receipt is recorded in the PR and
existing operational checkpoint; older green CI does not accept this revision.

Migration modules, database model tables, deployment/workflow configuration and operator
settings are byte-identical to reviewed0d3. One immutable head remains
`a12trendpulsescreen001`, ancestrya8→a9→a10→a11→a12. New origin/pre-entry/diagnostic receipts
use existing opaque AuditLog JSON and the existing audit action, with no new writer or
consumer installed. Retained-schema evidence above is reused for unchanged migrations;
it is not newly built image acceptance. Qualified rollback API must retain the safety
bootstrap fix and all a12 metadata; never downgrade/stamp populateda12 or start plainb165.

No access change was supplied; unchanged failed hosting probes are not repeated.
**One setup action:** provision the existing secure release/evaluation executor with
this complete scoped packet, without placing secrets in chat:

1. Exact-commit Render API/CLI plus HTTPS for workspace`tea-d7hn0fvavr4c73f62c70`,
   API`srv-d8fvbcd7vvec739mc060` and worker`srv-daqej7o473hc73fsc1k0`; preserve existing
   service settings. The existing connector cannot select a commit. Last verified
  10October API/worker revision is`b165b92276346f0e0fe3ccdbd2bec3443dc75d40`.
2. Existing container/database binding inspection, write hold/drain, restorable snapshot
   and backup/restore permissions for DB`dpg-d8fuok57vvec739lm0u0-a`; last observed heada8
   does not verify the services' actual secret connection. No hosted migration executed.
3. Reauthenticate Vercel scope`alphatrade-ai`, team`team_LNgcEGBkqntUnTrktNjFzh7a` /
   project`prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ`, with protected HTTPS access to establish
   the existing frontend target, source revision and API binding. Current staging frontend
   is **UNKNOWN**; historical alias/preview metadata is a lead, not alignment evidence.
4. Scoped synthetic staging identity and authorized read-only fresh SFP diagnostics;
   existing approved model route credential/entitlement and verified remaining-budget/
   price/context receipt for Q4; routine work within that budget is already authorized. No new provider or spending activation is implied.

After source review and executable access: freeze selected exact candidate and qualified
rollback images→capture existing operator values/DB binding/snapshot/write hold→drain
old API/ingestion writers and worker→same-SHA API upgradea8→a12/paper health→same-SHA worker
with compatible consumers and preserved flags→verified existing same-SHA frontend/API
binding→preserved PR237 smoke plus changed journeys→fresh natural SFP→the single explicitly
requested `full_backend=true` release gate. Failure uses coordinated qualified API/worker/
frontend rollback while retaininga12 and all documents/jobs/native facts/receipt metadata.
No main merge, replacement infrastructure, exchange orders, Telegram tests, paid-provider
activation or operator changes. The manual BloFin incident remains undiagnosed.

### PR248 completion continuation — fixed checklist, 11 October 2026

Continue from `4e5dc16782c8f8a30891179de0a886a44c3f0f37` on the same PR248 integration
branch. Its CI38136849882 passed364 backend/379 frontend and deployment-safety, with
zero test skips; evaluation/Docker/e2e were manual-policy skipped. Reuse unchanged
evidence. Reviewed PR244ba47, PR2470d3 and PR2373600 remain frozen. The U/Q/N/I
denominator stays15. Routine Q4 evaluation within the existing approved test budget is
authorized; missing credentials are a separate blocker, not another approval loop.

Ownership is isolated before edits: owner has Agent history/loading and browser tests,
shared artifacts/migrations and existing checkpoints. Worker A has private actual-provider
evaluation/corpus/accounting and Agent routing support, base4e5 on
`codex/agent-provider-quality`. Worker B has the canonical governed coordinator, explicit
server-origin context, native evidence and affected tests, base4e5 on
`codex/blofin-coordinator-evidence`. Neither worker edits owner UI, public generated
artifacts, migrations or operational checkpoints. Contributions are collected directly.

Owner request ownership: one history-loading token identifies generation and request
kind. Reconciliation replaces/aborts earlier paging; only the owning completion releases
the current indicator. Every response checks conversation, account, session and generation.
Account changes clear retained history/cursors/anchors before paint; superseded completions
cannot release a new request or restore private content. The initial negative control
reproduced the permanently busy earlier button. Three new overlap/conversation/account
cases passed, plus six affected history/logout/unmount cases. Initial positive fixtures
exceeded the existing five-second per-case limit and used an exact text locator against
a prefixed error; reducing the fixture to130 (still more than one page) and matching the
actual error resolved the test issues. No assertions/timeouts were weakened.
Receipts: `/tmp/quality-overlap-{before,after,repair,neighbors}.log`;49 and46 intentional
filters are separate from executed passes. Desktop/mobile actual-login/API overlap journeys
passed2/2 in2.1m, including delayed committed turn, aborted earlier read, completed
reconciliation, successful repaging, uniqueness and reading-anchor delta below8px.
Receipt: `/tmp/alphatrade-next-postgres/overlap-browser.log`.
A third actual-login browser journey passed1/1 in1.2m: delayed earlier history survives
same-page conversation switching without stale content; navigation away then actual
logout/other-account login excludes both prior transcripts and loading state, with a
real foreign-tenant404. Receipt:`overlap-identity-browser.log` in the same folder.
These three new journeys supplement the unchanged four, rather than rerunning them.
Changed-file ESLint, frontend typecheck and diff check pass.

Executable release metadata was refreshed through the previously working Render connector,
with no failed HTTPS/auth probes repeated. On11October the exact API/worker deploys remain
live at `b165b92276346f0e0fe3ccdbd2bec3443dc75d40`: API receipt
`dep-db4gdkeq1p3s73a395u0`, worker receipt`dep-db4gdkeq1p3s73a3975g`. No current candidate
was deployed. Frontend target/revision and actual hosted database binding remain unknown.

The single concrete access setup action now has an exact destination: update the existing
managed environment configuration
`d8e448e9-266c-4462-9846-dc4819fb0132~cecfg_6abe152de4548195ae254bdd52afc138`
(bound environment
`ccarenv_b64_Y2NhcmVudl80MThhNjYxNDk2ZDg4MTkxYWY1ODVjZWUyMmM2NGIzOA`).
Safe status reports current spec57, no secret or outbound-identity bindings, and enforced
package-manager-only HTTP policy. Bind `OPENAI_API_KEY` for the existing approved route,
`RENDER_API_KEY` scoped to the two named staging services and existing DB snapshot/restore
operations, scoped `VERCEL_TOKEN` for the existing team/project, and existing synthetic
smoke identity (`SMOKE_ACCESS_TOKEN`/private real-login credential file). Permit
`api.openai.com`, `api.render.com`, official `docs.blofin.com`, the existing staging API and the verified existing
Vercel frontend/protection endpoints through the supported configuration workflow. No
policy bypass, new infrastructure, application-provider activation or operator reset.
This refines the earlier target-specific packet; it does not start another access checklist.
Q4's finite price-verified cap/usage runner and native supported-evidence receipt are
recorded below when collected; absent provider lineage/money declarations remain unavailable.


Worker A's reviewed completion contribution is
`e28d4fb5bfd667d027962da345ea0630fc028de0` on `codex/agent-provider-quality`
(base4e5), integrated as `f74fbe3`. The standalone runner reuses CaptureModel/ModelRouter
and the durable capture/usage pipeline with a private optional provider wrapper; runtime
routing defaults are unchanged. Sixteen untagged cases cover journal, strategies, rules, news/analysis and lessons,
mixed topics, ambiguity, correction against an existing record, questions, confirmations,
execution requests and explicit do-not-save content. Reports distinguish actual provider
transport from deterministic fixtures and accounting-only refusals. Category results,
false captures, exact-source quotes, qualifiers, correction targets, provider/end-to-end
latency, reported usage, failures and unexecuted cases are retained privately. Semantic
faithfulness still needs reference review; metrics are not automatically acceptance.

**34 distinct new runner/accounting checks passed, zero skips.** Exact XML union verified
by the owner: first25passed/3fixture failures (output ceiling1000 below Settings minimum1024),
corrected3 PostgreSQL passes/25 filtered, next8passes/25 filtered, final5passes/29 filtered.
Receipts:`/tmp/agent-provider-evaluation-{first,postgres,final-new,review}.{log,xml}`.
Real disposable PostgreSQL proves durable ModelCallAttempt/UsageEvent matching, one
existing retry, no timeout retry, cap exhaustion and no same-report/schema automatic
repeat. Mypy two source files, Ruff/format four files and diff check pass. This runner
verification adds no measured quality and does not rerun Q1–Q3. **Q4 stays0/16 measured**;
quality, latency and usage are null. No provider calls or runtime settings changed.

The runnable measured command, from the integrated backend, is:

```sh
PYTHONPATH=src:. .venv/bin/python scripts/evaluate_agent_provider.py \
  --limits-file /workspace/agent-quality-approved-limits.json \
  --spend-cap-usd 1.00 \
  --database-url-env AGENT_EVAL_DATABASE_URL \
  --run-id 397c0587-a64a-44b2-a013-27451e1476a2 \
  --output /workspace/agent-quality-q4-report.json
```

Use the existing approved provider configuration in this evaluation process, not mock.
The one missing provider credential binding is `OPENAI_API_KEY`; supported HTTPS egress
to `api.openai.com` is also required in the exact managed configuration above.
`AGENT_EVAL_DATABASE_URL` selects disposable PostgreSQL, not hosted application data.
The receipt must name the actual existing budget reference/remaining balance, exact
configured model, verified HTTPS price source and expiry, input/output prices, and
provider-enforced input context ceiling. Placeholder repository prices establish none
of these facts. USD1.00 is only the chosen job ceiling and is refused above the actual
approved remaining balance. Each case reserves two worst-case attempts before any
transport; unused retry allowance is released only after completion. Ambiguous attempts
retain their reservation and private receipt. A cap-stopped corpus lists unexecuted
cases and cannot pass Q4. Existing receipt/schema IDs must be inspected, never retried
automatically. No new model identifier, budget approval loop or provider activation.


Worker B's reviewed completion contribution is
`b2c6d90468d673664ac30ddafe97761b69d3661f` on `codex/blofin-coordinator-evidence`
(base4e5), integrated as `7051567`. The existing governed coordinator has an explicit
internal experiment entry: commit original server origin → existing governed approval/
ALLOW command → signed UID/flat-position/contract evidence and committed entry binding →
existing lease, authorization and final dispatch fences. Root/version locks, immutable
strategy/configuration/plan bindings and scoped current membership prevent inference
from similar strategies or client IDs. Domain start does not invoke this entry; no
scheduler, HTTP execution path or activation is added. Native admission stays closed.

**58 distinct focused cases passed, zero skips:**17 new PostgreSQL cases and41 existing
compatibility cases. New module15passed83.20s after the locked-effect correction; existing
claim/protocol38passed14.96s and ordinary governed success/kill/fence3passed22.57s.
Independent source review then found cached membership could authorize durable origin/
command work after a committed role downgrade, although final POST revalidation blocked
the order. The correction remains inside the new context/dispatch boundary:2 retained-
cache entry/dispatch downgrade cases passed13.01s, and the armed permitted-role real
entry passed again5.06s. That repeat adds no distinct case. Earlier compatible source
results are reused with their exact revision scope, not presented as a new full run.

The initial concurrency proof failed12passed/1failed: a locked read reused stale ORM
effect state and allowed two submit entries for one command. Refreshing under that
lock repaired the defect without weakening the one-send assertion; the original race
and an independent cached-effect regression passed. The armed explicit entry uses the
real FirstSliceEvidenceAssembler, persisted Candidate/canonical plan, original
continue_confirmed_setup and claim, and independently visible receipts before the real
final fence. The new positive path makes zero POSTs. Signed reads stay in MockTransport;
restart/duplicates retain the original command/key and do not recollect or resend.
Fresh quote/multiplier fixture corrections and the initially omitted loopback permission
are recorded in `docs/blofin_experiment_outcomes_verification.md`; gates/timeouts were
not weakened and the cluster was not reset. Ruff/format seven Python files, mypy five
source files and diff check pass. Independent read-only review confirmed the membership
finding resolved on exact b2c6d90. No runtime/operator setting changes.

**N1/N3–N5 locally accepted4/5; N2 unmet.** Official protected entry → protection →
generated closing-order lineage and historical fee currency/sign and fillPnl fee/funding
basis remain absent. The pinned Anonymous-fe mirror and CCXT adapter are corroborating
leads, not venue-owned attestations or live monetary proof. Exact source hashes and
missing declarations are in the existing outcome contract. No unsupported native money
mapping, eligible sample, closing proof or portfolio performance is invented.

Combined shared-contract qualification: generated API drift check passed with full
schema SHA256 `56b2cbea880a93b6e5397fa9a077e9e9846cf07854e357d20af580204497a77b`;
one offline deterministic OpenAPI export test passed with zero skips. Receipts:
`/tmp/pr248-combined-{generated,contract}.log` and `contract.xml`. No public schema or
migration changed; all five Agent contribution and nine native contribution blobs are
compared with the integrated candidate before publication. One actual immutable chain is
`a8agentcapture001` → `a9knowledgeoutbox001` → `a10blofinactivity001` →
`a11experiments001` → sole head`a12trendpulsescreen001`. Table/migration/workflow/deployment
files remain unchanged against reviewed0d3. Existing retained-schema rollback evidence
is reused for that unchanged shape; built rollback images and hosted acceptance remain
unexecuted. Product/worker publication is batched; the exact candidate and current
automatic focused CI are recorded in PR248 and the existing operational checkpoint.
The green4e5 run covers4e5 only. Full release acceptance remains gated by review,
qualified rollback images, aligned authenticated staging and fresh natural SFP evidence.
