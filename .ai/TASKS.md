# AlphaTrade AI — Tasks

Persistent backlog. IDs: `AT-XXX`. Fields: Priority, Status, Dependencies, Risk,
Validation, Recommended model. Gap-analysis items (Phase 7) are **not implemented** here.

Legend — Priority: P0 (critical) … P3 (low). Status: TODO / IN_PROGRESS / DONE / BLOCKED.

## AT-117 — BloFin reconciliation and presentation repair

- Priority: P0 · Status: DONE (implementation; review/live acceptance pending).
- Base: main `642d2d4` including merged PR229/230/231.
- Scope: Independent entry/current-account/protection/exit confidence; documented
  pagination; signed fees and gross PnL; exact Journal detail/reflections; one
  native-account Dashboard and concise Agent/manual detail with collapsed evidence.
- Validation: Focused lifecycle/Journal/account/Agent checks and desktop/mobile
  browser fixtures; one final full backend gate. Exact results recorded in PR.
  Align stale baseline diagnostic/risk fixtures and migration-head assertions
  without relaxing checks; report complete-gate failures separately from fixes.
- Limits: No credentials bound for authenticated native acceptance. No new orders,
  cancellation, deployment, kill reset, unrelated hold release or migration.
- Gate: Review/merge, verify deployed commit/head, refresh same original command,
  inspect exact linked Journal, compare account values and Agent explanation.
- Reference: `docs/blofin_reconciliation_presentation_repair.md`; AT-ADR-097.

## AT-116 — BloFin demo quote timing diagnosis

- Status: Implementation complete; live root cause and preview recovery pending.
- Base: main `2ff829a` including PR225. Existing receipt-time clock and documented
  Unix-millisecond parsing agree; supplied live error lacks raw timing evidence.
- Scope: Bounded public raw/parsed/receipt/age diagnostics, exact millisecond
  parsing, UTC handling and strict fresh/stale/future/malformed regressions.
- Limit: Executor HTTP policy excludes BloFin; no live stale/caching/skew claim.
  Same-demo-host order book is a documented candidate, not an enabled fallback.
- Gates: Review/deploy, preview-only timing capture, evidence-driven correction
  if needed, then consolidated release CI. No orders or execution activation.

## AT-115 — Presentation preflight and Dashboard consistency

- Priority: P0 · Status: DONE (implementation; review/deployment/live acceptance pending).
- Base: main `e4a781679ccda9258c28d5330671d3aaa54376d8`; preserves PR220–224.
- BloFin: owner-reported preview403 at 2026-10-07T16:11:33Z; live root cause
  unverified. Stage/endpoint, fixed reason/type and bounded numeric protocol fields
  replace the blanket diagnostic silence; no private response/error text is logged.
- Dashboard cause: stale Position/TradeJournal projections differ from canonical
  JournalTrade used by Attention and Agent. Align open positions/counts and recent
  trades; count beyond page limits; label account/venue/date and portfolio scopes.
- Validation: 139 PostgreSQL/simulated venue, 41 Dashboard/Attention, 52 existing
  BloFin provider cases; focused frontend, Ruff/format and six-source mypy.
  Final frontend counts and exact commands recorded in the delivery handoff.
- Boundaries: no orders, account mutations, history rewrites, full backend runs,
  deployment, exchange/worker activation or safety relaxation.
- Release: one consolidated PR; review, one exact-SHA full_backend=true CI gate,
  deploy disarmed and repeat the original preview only. Live provider root cause
  awaits structured deployed evidence; no credential recreation requested.
- Reference: `docs/presentation_repairs_20261007.md`; AT-ADR-096.

## AT-111 — Agent Journal target evidence

Queued after completed PR220. Read-only follow-up from main 8bab505: expose bounded
Journal planned target prices and allocations, explicitly compare them with the
linked immutable canonical plan, and preserve missing/mismatch evidence. Matching
planned values never establish venue protection or exit fills. Owner reports the
historical target repair independently verified; this task does not repair live DB.
Focused PostgreSQL and Agent context/followup checks only; final evidence in PR and
handoff. Preserve running full backend CI, history, risk and execution authority.

## AT-109 — Recorded Nested qualification routing and current minimum evidence
- Priority: P0 · Status: DONE (implementation; review/live acceptance pending) ·
  Dependencies: main `c9df311` including PR216–218 · Risk: Medium (scoped reads).
- Root cause: Exact question lacks literaltrade noun and fell into current setup
  retrieval; same-plan followup was unsupported. Policy lacked explicit source context.
- Scope: Historical qualification grammar, scoped same-plan context, immutable
  decision lookup, current deterministic policy reference and bounded candle hashes.
  Explicit current-setup requests remain current; no invented OHLCV values.
- Validation: Focused routing/PostgreSQL/Agent selection/isolation/evidence/reply
  regressions, scoped Ruff/format/mypy. No full suite or live quality claim.
- Reference: `docs/agent_historical_setup_explanation.md`; AT-ADR-094.
- Release: Review/deploy/repeat exact question and contrast, then consolidated CI.
  No strategy thresholds, historical records or execution flags changed.

## AT-112 — Repair two confirmed full backend CI failures
- Status: DONE (implementation; review/release acceptance pending).
- Base: main `8bab505`; CI 37599185453 / backend job 112719114802
  (4,452 passed, 2 failed, 6 skipped).
- Agent cause: conversation creation precedes typed action and persisted membership
  validation; order.submit is the first failing action. Fresh fixtures and module
  reproduce it. Reject before new transcript or existing conversation mutation.
- Inverse cause: earlier linear gross 1R authorization correctly refuses the old
  claim fixture. Cover fresh authorization refusal and historical authorization
  claim refusal separately; no inverse support or risk policy relaxation.
- Validation: 142 affected/adjacent cases + 19 continuity cases passed; relevant
  Ruff/format, scoped mypy and diff checks passed. Foreign conversations still
  receive scoped not-found refusal before any conversation mutation.
- Scope: focused affected and adjacent regressions only. PR220/221 preserved; no
  full CI rerun, merge, deploy, activation or exchange orders.

## AT-114 — CI #783 migration head and capacity fixture repair

- Priority: P0 · Status: DONE (implementation; release review pending).
- Base: main `b58bedae`; exact CI `37622005520`, backend job `112794311174`.
- Root causes: Twelve stale head expectations; capacity fixtures used demo plans,
  so the valid demo account history lock preceded the exposure capacity predicate.
- Scope: Update expected head to `a6manualdemo001`, preserve ancestry and migration
  cycles/data assertions. Use internal paper for capacity, equal-sized plans for
  the PostgreSQL race, and verify demo lock with worker flag enabled/disarmed.
- Validation: 101 affected migration/protocol/manual tests and 51 PostgreSQL
  protocol/readiness tests passed (three overlap); scoped Ruff/format passed.
- Release: Review and consolidate, then one complete acceptance CI. No application
  safeguards or migration code changed; documentation PR223 remains untouched.

## AT-108 — Presentation MVP 1R policy and linked evidence release
- Priority: P0 · Status: DONE (implementation; review/live acceptance pending) · Dependencies:
  main `e75e8bf` including merged PR215 · Risk: High (new entry refusal).
- Scope: Exact allocation-weighted gross1R minimum at deterministic plan,
  authorization, atomic claim and final demo dispatch; history/targets unchanged.
  Owner-scoped setup/assessment reads and unique material evidence warnings.
- Known short: 397.30 reward /609.40 risk≈0.65R; previous policy lacked the floor.
  Existing audited single-trade Journal repair is documented, not run live.
- Validation: Focused unit/PostgreSQL/Agent/canonical/venue/repair regressions and
  scoped lint/types only. Final exact-SHA full backend CI remains supervisor gate.
- Submission: Destination read denied403/404; no blind sync or reviewer-file writes.
- Limits: No merge/deploy/activation/strategy approval/orders. Expansion isolated.
- Reference: `docs/minimum_planned_reward_risk_release.md`; AT-ADR-093.

## AT-107 — Agent trade continuity and document import entry
- Priority: P1 · Status: DONE (implementation; review/live acceptance pending) ·
  Dependencies: main `9306410` including PR210–214 · Risk: Medium (scoped financial reads).
- Root cause: New messages lost selected trade identity; synthesis saw only short
  user excerpts; model prose capped at 2,000 characters; import was outside Agent.
- Scope: Scoped persisted Journal/account selection, explicit overrides and invalidation,
  current record reads, bounded user/assistant context and readable trader presentation.
  Full explanation/evidence/references stay expandable; missing facts stay visible.
- UI: Existing Knowledge file preview/explicit save from Agent; paste ingestion retained;
  unsupported screenshot controls hidden. No new storage or authority.
- Validation: Focused PostgreSQL/API/Agent and UI regressions plus scoped lint/type checks.
  Exact final evidence in PR/handoff. No full CI or deployed/live-model acceptance claimed.
- Acceptance: Review/deploy and repeat latest BTC short question followed by “Explain
  that trade”; verify isolation/selection changes and Knowledge preview/save. Existing
  running full CI left untouched. `docs/agent_conversation_continuity.md`, AT-ADR-091.
- Release-blocker follow-up: Grouped/reproduced CI37480490124's 47 failures;
  refreshed literal migration lineage, settled Binance read assertions and synthetic
  adapter identities while preserving strict validation and memory bounds. Browser
  smoke proves authenticated preview-only then explicit Knowledge save.
- Watcher diagnosis: CWD-independent migration script resolution; startup refusal
  can heartbeat indefinitely without scans or rechecking recovery. Existing authority
  and restart policy retained; live cause awaits same-container posture/log/preflight
  checks. `docs/pr215_release_blockers.md`, AT-ADR-092. Focused checks only.

## AT-105 — Five-market Nested demo readiness and repeatable lifecycle
- Priority: P1 · Status: DONE (implementation; supervised acceptance pending) · Dependencies: main `ff90d0c`; independent
  Agent grounding PR211 delivered first · Risk: High.
- First-entry slice: Venue-specific immutable ALLOW history; internal-paper risk
  remains charged. Atomic account-epoch competing demo claims, existing-plan venue/
  policy refusal, account-wide flat/pending-order bounded proof before planning/send.
- Validation: 72 distinct focused passes: 34 readiness (four disposable PostgreSQL
  cases), 20 existing simulated demo pipeline, 12 advancing quote/expiry and six
  authority/idempotency/risk cases. Scoped Ruff/format and four source mypy checks.
- Separate delivery: PR213 read-only idempotent ten-subscription preview from exact
  provisional Nested baseline; no automatic creation/approval/activation.
- Lifecycle: Bounded actual closing fills, two flat/no-pending snapshots, exact
  parent/trigger lineage, immutable audited resolution under the account epoch,
  owned exposure release, canonical Journal/learning/Agent references and repeat
  entry on the same account. Existing Telegram policy/deduplication retained.
- Lifecycle validation: 111 distinct focused passes: 27 exit-protocol checks, 13
  PostgreSQL lifecycle/Nested cases, 20 existing demo, 12 quote/expiry, 34 readiness,
  three PostgreSQL fill races and two Telegram policy/deduplication regressions.
  Scoped Ruff/format15files and strict mypy7sources passed; single Alembic head and
  PostgreSQL migration roundtrip/protection verified. No full suite or live acceptance.
- Limits: Unfilled/ambiguous commands and unsupported history/outcomes stay held;
  actual fees/native fillPnl persisted, gross/net/funding/win-loss explicitly missing.
  Existing daily allocations/counts retained; no risk budget reset.
- Configuration/acceptance: Live instruments/credentials/access unverified; explicit
  authored/compiled/approved ten Nested 15m scopes, enabled verified slots, scoped
  demo arm and supervised natural protected demo fill. SFP execution stays refused.
- Release: Review independent PRs, one exact-SHA `full_backend=true` release gate,
  deployment and supervised preflight/approval/activation. No merge/deploy/orders or
  live approval in this task. `docs/five_market_demo_readiness.md`,
  `docs/governed_demo_lifecycle.md`; AT-ADR-088/090.

## AT-104 — Agent recorded paper trade grounding
- Priority: P1 · Status: DONE (implementation; supervising live acceptance pending) ·
  Dependencies: main `ff90d0c` · Risk: Medium (historical financial record scope).
- Root cause: Natural trade questions read legacy journal notes/title references,
  not canonical Journal/plan/fill records; explicit internal read requires UUID/capture.
- Scope: Read-only scoped latest selection and exact immutable lineage, planned versus
  filled prices, venue evidence, truthful target discrepancy and missing risk narrative.
  No playbook authorization, record repair, authority change or new database.
- Validation: 99 distinct focused cases pass (27 recorded/internal paper, 71 existing
  routing/presentation/refusal, 1 actual simulated demo pipeline); scoped Ruff/format
  and strict mypy five modules pass. No full backend suite or live model quality claim.
- Release: Review, supervising consolidated release gate, deploy and repeat original
  live Agent question. No full backend run, merge, deploy, activation or exchange order.
- Follow-up: Five-market Nested/demo readiness and repeat lifecycle start on a new
  branch only after this PR opens; leave documentation/submission work untouched.
- Reference: `docs/agent_recorded_trade_grounding.md`; AT-ADR-087.

## AT-106 — Explicit provisional Nested five-market subscription preview
- Priority: P1 · Status: DONE (implementation; supervised approval pending) ·
  Dependencies: main `ff90d0c`; expansion AT-105 / PR212 reviewed independently · Risk: Medium.
- Scope: Authenticated owner/tenant read-only preview of ten Nested long/short 15m
  scopes from an explicit stored provisional baseline. Stable source/spec hashes,
  exact existing-version reuse/approval observations and visible selected-version conflicts.
- Authority: No creation, approval, watchlist/config change, provider IO or execution
  authority. Explicit draft creation reuses the existing template endpoint; each new
  version needs separate compile/review. Source approval never transfers to copies.
- Validation: Nine distinct focused passes, including eight PostgreSQL preview/API/
  isolation/version cases and the existing library approval regression. Ten approved
  independent exact-symbol targets and scope cap are proven locally. Scoped Ruff/format
  and two service strict mypy checks; existing route typing debt remains unchanged.
- Release: Integrate independently of first-entry readiness, consolidated exact-SHA
  full CI, supervising live instrument checks, explicit version approval and activation.
  No full suite, merge/deploy, live strategy approval or exchange orders in this task.
- Reference: `docs/nested_subscription_preview.md`; AT-ADR-089.

## AT-103 — SFP REST candle finalization and immutable-policy recovery
- Priority: P1 · Status: DONE (implementation; supervised staging acceptance pending) ·
  Dependencies: main `60fed16` / merged PR208 ·
  Risk: Medium.
- Scope: Reproduce exact supplied receipt UUID and genuine volume/count/hash conflict.
  Live REST v2 admits closed candles only after five seconds and two matching pinned
  reads, validates close/count metadata, and records new receipts after acquisition.
- History: Preserve v1 receipt keys, values, clocks, hashes and all references. The
  actual application adapter policy has a distinct identity; provider revision stays
  1. No same-policy conflict bypass, deletion, relabeling or automatic correction.
- Verification: 248 distinct focused cases pass: 216 core cases, 66 affected/related
  cases with overlap, then 26 final source cases repeated. Covers source metadata,
  SQLite/PostgreSQL immutable recovery, both SFP scopes, restart/known-at, replay,
  Nested and governed-demo/Watcher safety. Scoped Ruff/format and three contract
  modules' strict mypy pass; assembly has the same 36 existing errors as main.
  New-policy warm-up is causal; live upstream attribution remains unconfirmed.
- Release: Supervising review/deployment, public payload comparison and successful
  both-scope evaluations within two scheduled 15m boundaries. No full backend run,
  merge, deploy, strategy/authority changes, activation or exchange orders here.
- Reference: `docs/sfp_candle_finalization_recovery.md`; AT-ADR-086.

## AT-102 — Approved plan targets in Journal and scoped projection repair
- Priority: P1 · Status: DONE (implemented; supervised repair pending) ·
  Dependencies: main `510dc2c` · Risk: Medium.
- Scope: Copy exact approved target order, prices and allocation into canonical
  approval/fill event payloads and the existing Journal schema. Preserve historical
  approved/fill payloads on replay; repair only empty derived projections explicitly.
- Repair: Exact tenant/owner/account/trade/revision, plan and envelope hash checks,
  consumed PAPER authorization, ALLOW command and canonical event lineage; default
  dry-run, explicit apply, lifecycle/row locks and atomic strict audit. No bulk mode.
- Validation: 79 distinct focused regressions pass: 19 PostgreSQL target projection,
  replay, tenant/owner/account isolation, concurrent repair and audit failure;
  10 canonical execution, 18 simulated demo fill/protection and 32 shared Journal
  projector/lineage cases. Scoped Ruff/format and five production/script mypy checks
  pass. No complete backend suite or live repair was run.
- Delivery: Separate reviewable PR and supervisor repair instructions; deployment,
  reviewed dry-run/apply and verification of trade `83a7063d-c83f-4a91-a343-0f231f9585d3`
  remain supervised. Demo stays disarmed; real trading remains disabled.
- Reference: `docs/journal_plan_target_repair.md`; AT-ADR-085.

## AT-099 — Notification fixture and clean bounded Agent prose
- Priority: P1 · Status: DONE · Dependencies: main `c70d4bd` / merged PR205 · Risk: Low.
- Scope: Intercept only exact GET `/execution/accounts/paper` in the notification
  Playwright fixture; preserve production authentication and session assertions.
  Bound model/plain/JSON and visible prose at complete sentence/citation units
  with an explicit shortening notice. Keep 2000/4000 limits and full recorded evidence.
- Safety: Source grounding and document/approved-setting distinctions retained;
  no strategy, credentials, execution authority or activation changes.
- Validation: All 3 targeted Chromium notification cases and 33 focused Agent
  reply/grounding regressions pass. Scoped Ruff, strict changed-source mypy,
  TypeScript and fixture ESLint pass; no manual broad suite/CI rerun.
- Delivery: One reviewable PR; supervising review/deploy and completed automatic
  CI precede supervised demo proof. Local mocks do not establish live acceptance.

## AT-098 — Authenticated canonical paper execution account setup
- Priority: P1 · Status: DONE · Dependencies: main `4898b0f` / merged PR204 · Risk: Medium.
- Scope: Authenticated self-scoped status and owner-only empty-body PAPER/NET setup,
  one identity under concurrent PostgreSQL requests, valid existing UUID reuse,
  disabled/ambiguous refusal, atomic creation audit and existing Account settings button.
- Safety: Setup is identity registration only. No credentials, strategy, risk,
  execution permissions, activation flags or real trading changes; no reset account
  or history deletion. Demo remains disarmed; no merge, deployment or exchange orders.
- Validation: 18 PostgreSQL/API cases pass: concurrency, authenticated isolation/role/input,
  disabled/ambiguous refusal, audit rollback and canonical paper identity resolution.
  All 24 focused component/client/Account settings cases pass; scoped Ruff,
  changed-module strict mypy, TypeScript, ESLint and diff checks pass.
- Delivery: Independent reviewable PR; use its automatic CI as the consolidated gate.
  Supervising review/deploy, owner registration and UUID pin precede venue acceptance.
- Reference: `docs/governed_blofin_demo_execution.md`; AT-ADR-083.

## AT-101 — Focused development CI and complete release dispatch
- Priority: P1 · Status: DONE (implemented; review/full acceptance pending) ·
  Dependencies: PR207 logging correction · Risk: Medium.
- Scope: Push/PR backend checks select a safety/workflow baseline, changed test
  files, direct module matches and SFP neighbors. Keep existing quality, evaluation,
  browser smoke, job names and dependencies. Explicit manual full_backend=true
  dispatch runs the unfiltered backend suite and all existing checks.
- Validation: Ten focused selector/workflow regression cases pass; strict script
  mypy and scoped Ruff pass. Focused results are development evidence only.
- Release: One exact-SHA complete run after supervising review, staging diagnostics
  and fresh evaluations. Do not launch it now or change branch protection rules.
- Safety: Demo stays disarmed, real trading disabled. No merge/deployment/orders.
- Reference: `.ai/RELEASE.md`; AT-ADR-084.

## AT-100 — SFP persisted evidence reuse and rejection diagnostics
- Priority: P1 · Status: DONE (implemented; live attribution pending) · Dependencies:
  main `c70d4bd` · Risk: Medium.
- Scope: Reproduce wrapped contract rejection across persisted receipts, rolling
  windows, repeated SFP scopes and restart. Accept canonical-equivalent decimal
  encodings using complete typed candle equality; log bounded refusal diagnostics.
- Safety: Original immutable rows/clocks, closed candles, hashes and revision
  validation remain authoritative. True value conflicts still fail closed.
  No execution activation, strategy/credential changes, merge, deployment or orders.
- Validation: Original receipt-fix commit `4395924`: 121 focused cases pass,
  covering SQLite/PostgreSQL reuse, conflict, revision,
  sanitized logs, existing SFP detector/runtime and Watcher refusal regressions.
  Scoped Ruff and receipt DAO strict mypy pass. Assembly/watcher have the same
  38 pre-existing strict mypy errors as untouched main, with no added messages.
- Logging follow-up: CI37380642481's empty diagnostic capture reproduces after
  cached logger initialization and configuration replacement. Test-only scoped
  capture covers cold/cached states; 16 affected tests pass, including PostgreSQL.
  Exact diagnostic and secret-exclusion assertions remain; production caching unchanged.
- Attribution: The decimal-encoding false conflict is reproduced locally with
  the reported wrapper code. Exact October 5 live cause remains unconfirmed;
  Render requires workspace confirmation before reading logs or stored rows.
- Delivery: Separate PR. Preserve PR206 and its supervisor-restarted CI.
  Focused PR CI is development evidence; AT-101's explicit full dispatch is the
  final consolidated release gate. Fresh live SFP evaluations are supervised.
- Reference: `docs/sfp_evidence_reuse.md`.

## AT-097 — Governed BloFin quote freshness at receipt
- Priority: P1 · Status: DONE · Dependencies: merged PR203 (`fba79a4`) · Risk: Medium.
- Scope: Compare ticker timestamps with the injected clock after receipt instead of
  the caller's preflight timestamp. Preserve strict ten-second and future-date bounds.
- Safety: Demo stays disarmed; real trading false. Preserve final dispatch expiry,
  idempotency, risk, protection, permission, host, kill-switch and authorization gates.
- Validation: Reproduced the advancing-clock failure on main with simulated HTTP;
  71 focused cases pass, including 12 new receipt-time/boundary/final-expiry cases
  and existing governed idempotency, risk, protection and real refusal regressions.
  Scoped Ruff lint/format, strict changed-module mypy and diff checks pass.
- Delivery: Separate focused PR; supervising release review. No merge, deployment or orders.

## AT-094 — Agent presentation and canonical strategy grounding
- Priority: P1 · Status: DONE · Dependencies: PR200 on current main · Risk: Medium.
- Scope: Reserve model explanation space, collapse stored evidence, distinguish selected
  version lifecycle approval from research validation, and include both Nested/SFP rules.
  Resolve named stored documents and pass substantive topic-balanced passages with
  neighboring context and citations; keep document guidance separate from approved settings.
- Safety: Read-only grounding; generated prose cannot approve, confirm or execute.
  Real trading remains disabled. No deployment or infrastructure changes.
- Validation: Focused transcript, lifecycle, both-family, tenant and UI regressions;
  backend Ruff/format/types and frontend lint/types; consolidated CI after implementation.
- Reference: `docs/agent_presentation_grounding.md`.
- Result: Focused stored/synthetic grounding, UI and canonical read regressions pass.
  PR201 is open. Stored-playbook context regressions and Agent/foundation/Daily Review
  checks pass (71 focused cases); live model quality on the existing playbook is unverified.
  Real staging acceptance is pending PR review; no deployment occurred.

## AT-095 — Governed BloFin demo execution integration
- Priority: P1 · Status: DONE (implemented entry slice) · Dependencies: canonical execution
  protocol and AT-094 Agent grounding · Risk: High.
- Scope: Separate reviewable execution branch; explicit demo activation, deterministic
  risk and authorization, durable dispatch, actual fill/protection reconciliation,
  Journal and learning linkage. Keep legacy paths tombstoned and SFP restricted.
- Safety: Existing demo Read + Trade key only; no network exchange orders from Codex;
  real trading disabled. No merge, deployment or infrastructure changes.
- Validation: Simulated success/rejection/timeout/idempotency/restart/protection tests
  over the canonical worker workflow and disposable local PostgreSQL, including late
  fence/kill changes, malformed protection, partial cancellation and actual-fill Agent reads.
- Restrictions: One ALLOW claim in the account's preserved history; automatic entries
  only. Exit-fill close, funding/PnL attribution and automatic account reuse are excluded.
  Synthetic demo fill/close remains forbidden; SFP remains non-executable.
- Acceptance: Real staging activation and natural entry/fill/protection acceptance are
  pending review and the consolidated CI gate. No network orders were submitted here.
- Result: 20 simulated demo integration cases pass on the stacked Agent branch; 21
  internal-loop/supervisor regressions pass. Changed-source strict mypy (13 modules),
  Ruff lint/format and diff checks pass. Earlier focused protocol/activation checks pass.
- Reference: `docs/governed_blofin_demo_execution.md`; AT-ADR-GOVERNED-DEMO-20261005.

---

## AT-096 — Knowledge file preview and explicit import
- Priority: P1 · Status: DONE · Dependencies: canonical Document/Chunk ingestion · Risk: Medium.
- Branch: `codex/knowledge-file-import`.
- Scope: TXT, Markdown, DOCX body/table text and selectable-text PDF preview before
  explicit save; bounded parsing; durable filename/raw hash/provenance and truthful
  SQL/vector observations; principal-scoped duplicates. Preserve pasted ingestion.
- Safety: No Agent, strategy, risk or execution mutation. Existing Master Playbook
  is already stored and will not be reimported. No deployment or infrastructure changes.
- Validation: 81 focused backend cases pass, including isolated PostgreSQL migration
  upgrade/downgrade; 18 focused frontend cases pass. Changed-file Ruff lint/format,
  strict mypy for nine changed modules, TypeScript and ESLint checks pass. The
  shared models file retains its existing 94 unrelated missing-generic errors.
- Result: Existing Document/Chunk ingestion and scoped search store extracted text,
  file provenance and recorded indexing observations. Preview performs no storage.
  Six Agent-owned import integration cases pass for substantive passages and
  filename/hash/document/chunk references, including foreign-principal isolation.
  Combined focused backend/frontend checks pass (50/35 cases). Migration-head
  pin integration passes 105 safety and 14 PostgreSQL migration cases; no skips.
- Limitations: No OCR, raw binary storage or full DOCX layout. Index acknowledgement
  is a recorded observation, not a live health claim. Staging acceptance and live
  Agent answer quality remain pending review and the combined CI gate.
- Reference: `docs/knowledge_file_import.md`.

---

## AT-093 — Nested timeframe independence and SFP frontend surface
- Priority: P1 · Status: DONE · Dependencies: accepted Nested/SFP canonical
  backend on main `abfd71b9f34f3b7a5bec38f6a933aaf93d8b4229` · Risk: Medium (evidence identity).
- Branch: `codex/nested-timeframes-sfp-ui`.
- Scope: All 13 canonical Nested timeframes; native provider interval mappings;
  explicit SFP draft creation and immutable compile/approval UI. Preserve one
  Nested detector, existing research parameters and timeframe-separated state.
- Safety: Real trading false; execution paper; exchange paper_internal. Closed
  final evidence and existing freshness/continuity/completeness/risk gates remain.
  No D Line / Break of Trend implementation, parameter optimization or deployment.
- Validation: Focused detector/replay/assembly/Watcher/provider and frontend tests;
  lint/types/format checks on changed files. Full suites excluded by task scope.
- Result: 173 focused backend passed / 14 PostgreSQL-only skipped; 25 focused
  frontend passed. TypeScript, scoped ESLint, Ruff lint/format and diff checks pass.
- Evidence: `docs/nested_timeframes_sfp_surface.md`.
- Limitations: Bybit has no native 3d candles; enough complete final provider history
  is still required. No live provider availability or timeframe profitability claim.

---

## AT-092 — Full governed paper closed loop acceptance 001
- Priority: P1 · Status: DONE · Dependencies: pinned release baseline
  `8673d8f69779ea516ca97456baea7b3064daf089` · Risk: Medium (paper Journal projection).
- Branch: `codex/full_paper_closed_loop_acceptance_001`.
- Goal: One deterministic supported strategy through canonical evidence, Watcher,
  assessment/Candidate, eligibility/Risk, immutable plan, explicit confirmation,
  internal paper fill, Journal, attribution/Analytics, Daily Review, Attention
  and a canonical read-only Agent explanation; notification outbox without delivery.
- Safety: Real trading false; execution paper; exchange paper_internal; Telegram
  network disabled. No diagnostic branch dependency, deployment or transfer paths.
- Validation: Twelve new PostgreSQL acceptance cases; all requested lifecycle,
  authority, review, Agent, notification, isolation/idempotency and safety suites;
  full backend/frontend, evaluation and deployment self-checks.
- Result: All 3,788 backend cases verified passing with zero skips after harness
  retries; 12 acceptance + four rehearsal cases pass. Frontend 1,316 cases,
  evaluations 16/5/7, lint/types, fixture-font build and nine safety checks pass.
- Evidence: `docs/full_paper_closed_loop_acceptance_001.md` and captured proof /
  verification JSON reports. Draft PR targets the exact pinned release branch.

---

## AT-091 — Proactive attention queue 001
- Priority: P1 · Status: DONE · Dependencies: exact base
  `5f4f0a467ce8f68f9c7a82d3b32307d559a9f092` · Risk: Low (read-only projection).
- Branch: `codex/proactive_attention_queue_001`.
- Goal: Deterministic tenant/user attention from recorded Watcher/setup, risk,
  paper positions, review lessons, validation/replay, evidence, delivery and
  pending strategy proposal records, with semantic deduplication and expiry.
- Safety: No trade execution, strategy approval, risk bypass, Telegram send,
  worker activation, migration or deployment. Agent integration deferred.
- Validation: Attention reducer/reader/API cases, Dashboard card/API tests,
  existing Daily Review/Brain regressions, lint and type checks.
- Result: 94 focused backend passed / eight PostgreSQL-only skipped; all 1,306
  frontend cases passed. Full backend completed with one read-only uv-cache
  self-check failure that passed when rerun using `/tmp` cache; details in handoff.
- Draft PR: https://github.com/Fejjii/AlphaTrade-AI/pull/182.
- Handoff: `docs/proactive_attention_queue_001.md`, branch root `HANDOFF.md` and
  `CHANGELOG_SESSION.md`; draft PR targets the exact release baseline branch.

---

## AT-000 — Bootstrap + install Master Workflow v2.0
- Priority: P1 · Status: DONE · Dependencies: none · Risk: Low
- Validation: `.ai/MASTER_WORKFLOW.md` tracked and authoritative; five-status model; normalized
  self-hash; `.gitignore` ignores handoffs + `.ai/local/` + `.ai/private/`; sync validated.
  Committed `057ef11`, CI run 29669825825 success.
- Recommended model: Opus 4.8

---

## AT-009 — Staging OpenAI + Qdrant provider activation (paper-only)
- Priority: P0 · Status: DONE · Dependencies: none · Risk: Medium (ops + provider config)
- Validation: Staging OpenAI + Qdrant active (paper-only). Commit `5f2d7cf` deployed;
  `/knowledge/search` returns semantic chunks; `provider-validation --remote --ingest` OK;
  verify-safety / portfolio / exchange-demo smokes OK. Providers: `gpt-5.6-sol`,
  `text-embedding-3-large` 1536-d, Qdrant healthy; `execution_mode=paper`,
  `real_trading=false`, `EXCHANGE_MODE=paper_exchange_demo`.
- Recommended model: Opus 4.8

---

## AT-010 — Readiness audit + real-money safety architecture roadmap (design only)
- Priority: P0 · Status: DONE · Dependencies: AT-009 · Risk: Low (docs/audit only)
- Goal: Full current-version readiness audit (repo + staging read-only) and Mode D
  safety architecture roadmap without implementing or enabling live trading.
- Validation: Deliverables present — `docs/AT010_readiness_audit.md`,
  `docs/AT010_risk_register.md`, `docs/AT010_real_money_safety_roadmap.md`;
  staging `verify-safety.sh` pass; local ruff pass; local pytest exit 0;
  paper posture unchanged (`execution_mode=paper`, `real_trading=false`,
  `EXCHANGE_MODE=paper_exchange_demo`). No live-trading code.
- Recommended model: Grok 4.5
- Completion evidence: Session AT-SESSION-20260721-001203; commit baseline `e123100`;
  staging API `git_sha=5f2d7cf`.

---

## Paper hardening (from AT-010) — implement before any sandbox/live program

### AT-011 — Authz for compute surfaces (`/tools`, `/risk/*`, strategy evaluate) + gate `/docs`
- Priority: P0 · Status: DONE · Dependencies: AT-010 · Risk: Medium
- Safety classification: Security / paper-safe
- Goal: Require auth on `/tools` (incl. execute), `/risk/*`, strategy evaluate; gate
  OpenAPI `/docs` outside local; keep paper-only.
- Branch: `feat/at-011-authz-tools-risk`
- Validation: Merged PR #1 (`3217c18`). CI run 29794325773 success. Unauth → 401;
  VIEWER → 403 on trader compute; trader/owner → 200; docs gated outside local;
  `/tools/execute` binds JWT tenant. Paper defaults unchanged.
- Recommended model: Composer 2.5 (impl) · Grok 4.5 (review)
- Completion evidence: commit `6908124`, merge `3217c18`, PR https://github.com/Fejjii/AlphaTrade-AI/pull/1

### AT-012 — Fresh risk + eligibility at paper execution; bind size/price; fail-closed zero stop
- Priority: P0 · Status: DONE · Dependencies: AT-011 · Risk: Medium (safety-critical)
- Safety classification: Trading safety / paper-only
- Goal: Re-evaluate RiskEngine at `place_paper_order` with DailyRiskState + settings +
  kill switch; refuse missing risk_result; call eligibility; bind order size/price to
  proposal (or re-risk); BLOCK on zero stop distance (no `0.001` fail-open).
- Branch: `feat/at-012-paper-risk-at-execution`
- Validation: Merged PR #2 (`992e954`). CI run 29799284663 success on `ffa975e`.
  Fresh risk at place_paper_order; DailyRiskState portfolio sync; proposal binding;
  sequential exposure/daily-loss regression tests; paper-only unchanged.
- Recommended model: Grok 4.5
- Completion evidence: commit `7ebe3b0`, merge `992e954`, PR https://github.com/Fejjii/AlphaTrade-AI/pull/2

### AT-013 — RAG fail-closed (no mock embeddings into Qdrant; no split-brain ingest)
- Priority: P0 · Status: DONE · Dependencies: AT-010 · Risk: Medium (data integrity)
- Safety classification: Provider / knowledge
- Goal: Fail ingest when embeddings `fallback_used` or Qdrant degraded; never upsert mock
  vectors to remote; avoid Postgres-success / Qdrant-miss silent success; delete orphans.
- Branch: `feat/at-013-rag-provider-fail-closed` (merged via PR #4)
- Validation: Forced provider failures → ingest errors; search exposes degraded flags.
- Recommended model: Grok 4.5
- Completion evidence: merge `b523c70`, commit `92d48bf`, PR https://github.com/Fejjii/AlphaTrade-AI/pull/4

### AT-013B — GPT-5.6 Sol Responses API + staging chat reliability
- Priority: P0 · Status: DONE · Dependencies: AT-013 · Risk: Medium (provider)
- Safety classification: Provider / chat
- Goal: Route `gpt-5.6-sol` through OpenAI Responses API; generation health probe must
  reflect real generation; `/chat/message` must not 503 on staging.
- Branch: `feat/at-013b-gpt56-sol-responses-api` (merged via PR #5)
- Validation: PR #5 CI run 29922248754 success; merged `19d53a4`; post-merge main CI
  29930805870 success; staging deploy `4956aa4`; `/health/ready` ready=true;
  LLM `openai-llm` healthy via responses (no mock); embeddings + Qdrant healthy;
  provider-validation `--remote` + `--remote --ingest` OK; verify-safety / portfolio /
  validate-exchange-demo-staging OK; chat HTTP 200 (5/5 flake retest); paper posture
  preserved (`execution_mode=paper`, `real_trading_enabled=false`,
  `EXCHANGE_MODE=paper_exchange_demo`, `LLM_MODEL=gpt-5.6-sol`).
- Recommended model: Composer 2.5
- Completion evidence: merge `19d53a4`, PR https://github.com/Fejjii/AlphaTrade-AI/pull/5;
  follow-up main commits `5c7c9a7`, `d71bd20`, `4956aa4` (probe + usage-tracking token
  budgets for reasoning models); staging API `git_sha=4956aa4`.

### AT-014 — Server-side kill switch (persist + enforce + UI wire-up)
- Priority: P0 · Status: DONE · Dependencies: AT-012 · Risk: Medium (safety-critical)
- Safety classification: Trading safety
- Goal: Persist org kill switch; enforce in RiskEngine + execution; replace cosmetic
  frontend toggle with API-backed control.
- Branch: `feat/at-014-persistent-kill-switch` (merged via PR #3)
- Validation: Toggle → paper place refused; UI reflects server; agent cannot hardcode false.
- Recommended model: Grok 4.5

### AT-015 — Honor PROVIDER_MODE for LLM/embeddings + wire narrative quota + search opacity
- Priority: P1 · Status: DONE · Dependencies: AT-013 · Risk: Low
- Branch: `feat/at-015-provider-mode-quotas` (merged via PR #6)
- Validation: `PROVIDER_MODE=mock` with key set → mock only (local); staging rejects
  `provider_mode=mock`; narrative `agent_narrative` hard block → deterministic fallback
  (`provider=quota`, no LLM); search exposes `degraded` / `fallback_used` / `vector_backend`.
- Recommended model: Composer 2.5 (impl) · Grok 4.5 (architecture/safety review)
- Completion evidence: PR #6 CI run 29944975929 success; merged `1f3dde0`; post-merge main
  CI run 29949893898 success; staging API `git_sha=1f3dde0`; `/health` + `/health/ready`
  pass; `/providers/status` openai-llm (gpt-5.6-sol), openai-embeddings, qdrant healthy
  (`is_mock=false`, no fallback); verify-safety / provider-validation `--remote` +
  `--remote --ingest` / portfolio-smoke / validate-exchange-demo-staging (17/17) OK;
  isolated staging narrative quota test (limit_agent_narrative=0 → quota fallback, restore →
  LLM path); staging RAG search `degraded=false`, `fallback_used=false`, `vector_backend=qdrant`;
  local pytest `test_at015_provider_mode_quotas.py` + `test_deployment_safety.py` +
  `test_at013_provider_fail_closed.py` pass; paper posture preserved
  (`EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_exchange_demo`,
  `LLM_MODEL=gpt-5.6-sol`).

### AT-025 — Wire narrative quota for sessionless AgentRuntime
- Priority: P2 · Status: TODO · Dependencies: AT-015 · Risk: Low
- Goal: Sessionless `AgentRuntime` (no DB session) currently skips narrative quota — wire
  quota for off-session graphs or document intentional skip.
- Recommended model: Composer 2.5

### AT-026 — Expose soft narrative quota warnings in metadata/UI
- Priority: P2 · Status: TODO · Dependencies: AT-015 · Risk: Low
- Goal: Soft narrative quota warnings are audited but not exposed in `narrative_metadata`
  or workspace UI.
- Recommended model: Composer 2.5

### AT-027 — Require RAG opacity fields in frontend RagSearchResponse
- Priority: P2 · Status: TODO · Dependencies: AT-015 · Risk: Low
- Goal: Frontend `RagSearchResponse` opacity fields are optional — tighten to required
  booleans when API contract is stable.
- Recommended model: Composer 2.5

### AT-016 — Audit unit-of-work + baseline metrics
- Priority: P1 · Status: DONE · Dependencies: AT-010 · Risk: Low
- Branch: merged via PR #7 → `main` @ `bf7f78b`
- Validation: No mid-request audit commit splitting business txn; RED metrics scrapeable.
- Recommended model: Grok 4.5 (architecture/safety) · Composer 2.5 (tests/PR)
- ADR: AT-ADR-008
- Completed: 2026-07-23 — merged to main; post-merge staging validation recommended separately.
- Follow-up (usage metering on replay): **DONE** — merged via PR #8 → `main` @ `5bac87e`
  (`PaperOrderPlacementResult.created_new` gates route usage; sequential replay does not
  double-count). Concurrent first-writer unique-conflict recovery: **DONE** via AT-028.

### AT-028 — Server-side concurrent paper-order idempotency convergence (Postgres)
- Priority: P1 · Status: DONE · Dependencies: AT-016 · Risk: Medium
- Safety classification: Paper accounting / concurrency
- Goal: On concurrent identical `idempotency_key` first-writers, recover from unique
  conflicts with a bounded savepoint/unique-conflict path so the losing request converges
  to the existing order (`created_new=False`) without client retry, and never double-meters
  usage or creation audits. Target Postgres; keep SQLite test coverage honest.
- Validation: Concurrent identical requests (no client retry) → one order, one
  `PAPER_ORDER_CREATED`, one `paper_execution` usage; no service-level commits;
  AT-ADR-008 UoW preserved; paper-only posture unchanged.
- Recommended model: Composer 2.5 · Grok 4.5 (transaction review)
- Completed: 2026-07-23 — merged via PR #9 → `main` @ `1225b49` (feature commit `ee573c3`);
  CI run 30020394617 green (1173 passed, 1 skipped; PostgreSQL 16 concurrency tests pass).
- Hotfix: 2026-07-23 — concurrent-loser HTTP 500 fixed via PR #10 → `main` @ merge `9d5b7c5`
  (commit `48846cd`); quota dependency savepoint convergence + loser/replay no-commit route;
  CI run 30032319345 green (1180 passed, 1 skipped). Staging validation pending separately.

### AT-029 — Fix pre-existing mypy Depends typing on `/execution/paper` route
- Priority: P3 · Status: DONE · Dependencies: none · Risk: Low
- Goal: `backend/src/app/api/routes/execution.py` reports a pre-existing strict-mypy
  `list-item` error: `require_quota(...)` typed as `Callable[..., QuotaCheckResult]`
  where FastAPI `dependencies=` expects `Depends`. Do not suppress or broaden typing
  rules; fix the dependency typing helper / annotation properly.
- Validation: `uv run mypy --strict src/app/api/routes/execution.py` clean.
- Recommended model: Composer 2.5
- Completed: 2026-07-24 — merged via PR #12 → `main` @ merge `cfdfe48` (commit `fb33f66`);
  CI run 30050995688 green. `require_quota` now returns `fastapi.params.Depends`
  (`DependsMarker`); typing-only, no runtime change. Pre-merge: scoped strict mypy + ruff
  clean, 23 quota tests passed.

### AT-017 — Frontend auth boundary + security headers
- Priority: P1 · Status: DONE · Dependencies: AT-011 · Risk: Medium
- Branch: `feat/at-017-frontend-auth-headers`
- Validation: Unauth app routes redirect via middleware; CSP/headers present; paper banners
  follow `/health` truth (no hardcoded “paper active” when real would be on).
- Recommended model: Sonnet 4.6
- Completed: 2026-07-23 — merged via PR #11 → `main` @ merge `1946471` (commit `47f891f`);
  pre-merge CI run 30040774513 green; post-merge CI run 30042962867 green (1180 passed,
  1 skipped). Edge middleware marker-cookie auth boundary, CSP + security headers,
  health-truth paper banners, fail-closed app layout, single-flight refresh.

### AT-018 — Proxy trust + Redis-required rate limits in staging/prod
- Priority: P1 · Status: DONE · Dependencies: AT-010 · Risk: Medium
- Branch: `feat/at-018-proxy-trust-redis` (operator lane name; backlog alias was
  `feat/at-018-rate-limit-proxy-trust`)
- Validation: Spoofed XFF ignored; memory fallback false outside local; denylist fail-closed.
- Recommended model: GPT-5.4 (backlog); implemented via Fable 5 (operator assignment)
- ADR: AT-ADR-009
- Completed: 2026-07-24 — merged via PR #13 → `main` @ merge `22afcda` (commit `265348e`);
  CI run 30053223730 green. Rightmost-hops `TRUSTED_PROXY_HOPS` client-IP trust (default 0),
  uvicorn forwarded-ips no longer `*`, staging/prod reject in-memory rate-limit fallback,
  fail-closed token denylist (503 on unpersistable revocation writes). Pre-merge (after
  rebase onto `cfdfe48`): ruff + scoped strict mypy clean; full backend suite exit 0
  (includes 25 new AT-018 tests); targeted rerun 88 passed. Deploy note: staging boot now
  fails fast if `REDIS_URL` unreachable (intended; `render.yaml` carries the new flags).

### AT-019 — Backup/restore runbook + restore drill evidence
- Priority: P1 · Status: DONE · Dependencies: AT-005 · Risk: Medium (ops)
- Branch: `feat/at-019-backup-restore-drill` (operator lane name; backlog alias was
  `feat/at-019-backup-restore-runbook`)
- Validation: Documented RPO/RTO; successful restore drill recorded (no secrets in docs).
- Recommended model: Grok 4.5 (operator assignment; backlog previously Sonnet 4.6)
- ADR: AT-ADR-010 (drafted in-lane as AT-ADR-009; renumbered — AT-018 landed AT-ADR-009)
- Completed: 2026-07-24 — merged via PR #14 → `main` @ merge `a31a05c` (commit `ca4ff70`);
  CI run 30054203698 green. Runbook (RPO/RTO), inventory, drill plan + sanitized Tier A
  local Compose drill evidence (passed 2026-07-23); local-only backup/restore/drill
  scripts (`CONFIRM=yes` gate, no remote targets). RR-13 moved to Partial. Managed/staging
  Tier B restore remains approval-gated. AT-005 deploy rollback + smoke gate merged via PR #15.

---

## Journal intelligence program

### AT-030 — Journal Intelligence Foundation (canonical journal domain, slice 1)
- Priority: P1 · Status: DONE · Dependencies: none · Risk: Low (record-only, no execution path)
- Safety classification: Paper-safe / record-only
- Goal: Canonical tenant-scoped journal domain (`journal_trades` + evidence, rule-check,
  observation children) unifying manual, paper, imported, backtest, and system trades;
  links (never duplicates) positions, paper trades, proposals, orders, backtest trades,
  legacy journal entries, and immutable setup/strategy versions; plan (thesis, trigger,
  entry, invalidation, stop, targets, runner), execution (leverage, fees, funding,
  slippage), MFE/MAE + available-vs-realized profit, market regime.
- Branch: `cursor/at-030-journal-intelligence-foundation-b68a` (merged)
- Validation: migration `i5d6e7f8a9b0` upgrade/downgrade/upgrade on Postgres 16;
  `tests/test_at030_journal_trades.py` (13 tests); full backend suite exit 0; ruff clean;
  scoped strict mypy clean on new modules (`db/models.py` stays at its pre-existing
  62-error strict baseline); paper posture unchanged.
- Recommended model: Fable 5
- ADR: AT-ADR-012 · Docs: `docs/journal_intelligence_foundation.md`
- Completion evidence: commit `1674dfd`, merge `1e9f5c5`, PR https://github.com/Fejjii/AlphaTrade-AI/pull/16;
  CI run 30064982141 success (backend 1223 passed / 1 skipped; deployment-safety, frontend,
  evaluation, e2e-smoke, docker-build all green). No deploy.
- Follow-up slices (see docs roadmap): journal completion (import/backfill/auto-journal),
  replay (AT-032), human-vs-system journal endpoint, backtesting integration.

### AT-031 — Journal Statistics & Setup Analytics v1 (journal domain, slice 2)
- Priority: P1 · Status: DONE · Dependencies: AT-030 · Risk: Low (read-only aggregates, no execution path)
- Safety classification: Paper-safe / record-only / read-only endpoint
- Goal: Deterministic tenant-scoped statistics over canonical `journal_trades`, grouped and
  filterable by setup/setup version, strategy/strategy version, symbol, timeframe, market
  regime, source, rule compliance (worst-assessment classification), and human-vs-system
  execution; metrics: trade count, W/L/BE, win rate, expectancy, average R, average
  winner/loser, profit factor, net PnL, fees/funding/slippage impact, MFE/MAE aggregates
  (recorded values only), available-vs-realized profit; per-family sample counts,
  confidence labels, warnings, date-range filter, bucket pagination, bounded scans
  (`journal_stats_max_rows`).
- Branch: `cursor/at-031-journal-statistics-fea2` (merged)
- Deliverables: `schemas/journal_statistics.py`; statistics queries on
  `repositories/journal_trades.py`; `services/journal_statistics_service.py`;
  `GET /journal/statistics` (ReaderDep); migration `j6e7f8a9b0c1` (indexes only);
  frontend `/journal/statistics` page + API client/types + nav entry;
  `tests/test_at031_journal_statistics.py` (19 tests); docs section + roadmap update.
- Validation: migration `j6e7f8a9b0c1` upgrade/downgrade/upgrade on Postgres 16 (scratch DB);
  `tests/test_at031_journal_statistics.py` (19 tests); full backend suite exit 0; ruff clean;
  strict mypy clean on new modules; frontend lint/typecheck/tests/build green.
- Recommended model: Fable 5
- ADR: AT-ADR-013 · Docs: `docs/journal_intelligence_foundation.md` (§4)
- Completion evidence: commits `2412c56`, `99d7f7f` (CI flake fix), merge `8020558`, PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/17; CI run 30092110777 success (backend
  1242 passed / 1 skipped; deployment-safety, frontend, evaluation, e2e-smoke,
  docker-build all green). No deploy.
- Follow-up slices: replay (AT-032 DONE), journal completion (import/backfill/auto-journal),
  human-vs-system journal endpoint, backtesting integration (see docs roadmap §6).

### AT-032 — Journal Excursion Replay (deterministic MFE/MAE from HistoricalCandle)
- Priority: P1 · Status: DONE · Dependencies: AT-030, AT-031 · Risk: Low
  (record-only replay; no execution path)
- Safety classification: Paper-safe / record-only
- Goal: Deterministic in-trade MFE/MAE, available profit, and profit-capture for
  canonical `journal_trades` from read-only `HistoricalCandle`;
  `excursion_source="replay"` with data-source/freshness provenance; never overwrite
  manual/system without explicit `overwrite_policy=force`; optional post-exit runner
  analysis via `RunnerAndMissedProfitAnalyzer`; feed AT-031 statistics; handle missing
  candles, gaps, incomplete windows, invalid trade windows safely; tenant isolation +
  RBAC + audit; bounded candle/batch queries.
- Branch: `feat/at-032-journal-excursion-replay` (merged)
- Deliverables: migration `k7f8a9b0c1d2`; calculator + replay service; schemas; routes
  `POST /journal/trades/{id}/replay-excursions` and batch
  `POST /journal/trades/replay-excursions`; config bounds; tests; docs §5; ADR-014.
- Validation: migration `k7f8a9b0c1d2` upgrade/downgrade/upgrade on Postgres 16 (scratch DB);
  `tests/test_at032_journal_excursion_replay.py` (17 tests); ruff clean; strict mypy clean
  on new modules; AT-030/031 regression green; CI run 30105918952 success (backend 1259
  passed / 1 skipped; deployment-safety, frontend, evaluation, e2e-smoke, docker-build all
  green). No deploy.
- Recommended model: Fable 5
- ADR: AT-ADR-014 · Docs: `docs/journal_intelligence_foundation.md` (§5)
- Completion evidence: commits `460a994`, `e237d96` (ruff format), merge `b164c14`, PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/18; CI run 30105918952 success. No deploy.
- Follow-up slices: journal completion (import/backfill/auto-journal), human-vs-system
  journal endpoint, backtesting integration (see docs roadmap §6).

### AT-033 — Journal Completion (bulk import, backfill, auto-journal, attachments)
- Priority: P1 · Status: DONE · Dependencies: AT-030, AT-031, AT-032 · Risk: Low
  (record-only; no execution path; new flags default off)
- Safety classification: Paper-safe / record-only
- Goal: Bulk journal import (`source=imported`, `entry_method=import`) with
  `(org, external_ref)` dedup via partial unique index + deterministic fingerprints;
  dry-run/commit modes with per-row reconciliation and all-or-nothing commits;
  `TradeJournal` → `journal_trades` backfill CLI (idempotent, dry-run default);
  opt-in auto-journal hooks on paper position / paper-validation close (default off,
  savepoint-isolated, never blocks the close); DB-backed attachment storage behind
  `AttachmentStorage` with size/MIME/quota caps and evidence auto-link;
  `entry_method` human-vs-system statistics dimension; frontend `/journal/import`
  with CSV mapping, dry-run preview, and batch history; tenant isolation + RBAC +
  audit throughout.
- Branch: `feat/at-033-journal-completion`
- Deliverables: migration `l8a9b0c1d2e3`; `journal_import_service` +
  `journal_backfill_service` + `journal_attachment_service`/`_storage`;
  `scripts/backfill_journal_entries.py`; routes `POST /journal/trades/import`,
  `GET /journal/imports[/{id}]`, attachment endpoints; auto-journal hooks in
  `position_service` + `paper_validation_runtime_service`; settings flags + limits;
  frontend import page + api client + tests; docs §6; ADR-015.
- Validation: migration `l8a9b0c1d2e3` upgrade/downgrade/upgrade round-trip on
  disposable Postgres 16 (docker) clean; partial unique index verified on Postgres
  (duplicate rejected, NULL refs unconstrained); 44 new backend tests
  (`test_at033_journal_import.py` 14, `test_at033_journal_backfill.py` 6,
  `test_at033_journal_attachments.py` 12, `test_at033_auto_journal.py` 9,
  `test_at033_integration.py` 3); AT-030/031/032 regression green (49 tests);
  frontend 267 passed + lint + typecheck + build green; ruff check/format clean.
  Full-suite result recorded in the PR. No deploy; no live trading.
- Recommended model: Fable 5
- ADR: AT-ADR-015 · Docs: `docs/journal_intelligence_foundation.md` (§6)
- Completion evidence: commits `a2e09a0`…`1e65185`, merge `ad66dca`, PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/19; CI run 30114440476 success
  (backend, frontend, deployment-safety, docker-build, evaluation, e2e-smoke,
  Vercel preview green). No deploy; auto-journal flags remain default off.
- Follow-ups: attachment upload UI (needs trades detail page), per-user auto-journal
  preference, failed/dry-run import batch persistence, human-vs-system endpoint.

### AT-034 — Deterministic Backtesting v2 (engine, orchestration, frontend, tests/docs)
- Priority: P1 · Status: DONE · Dependencies: AT-030, AT-031, AT-032, AT-033,
  Slice 35 · Risk: Low (record-only; no execution path; advisory tiers only)
- Safety classification: Paper-safe / record-only
- Goal: Deterministic backtest v2 with frozen config/dataset snapshots,
  `result_hash` reproducibility, walk-forward holdout/rolling splits, long+short
  entry modes (pullback_ema/breakout/liquidity_sweep), funding accrual, in-loop
  MFE/MAE/capture, bounded orchestration (sync vs queued, cancel, idempotency,
  active-run cap), bulk journal-from-backtest, journal comparison cohorts, advisory
  setup evidence tiers, frontend backtest UI, integration tests and v2 docs.
- Branches: `feat/at-034-backtest-domain-engine`, `feat/at-034-backtest-api-orchestration`,
  `feat/at-034-backtest-frontend`, `feat/at-034-backtest-tests-docs`
- Deliverables: migration `m9b0c1d2e3f4`; `backtest_engine_service` (v2),
  `backtest_dataset_service`, `backtest_hashing`, `backtest_service`,
  `backtest_journal_service`, `setup_evidence_service`; routes
  `POST/GET /strategies/{id}/backtests`, `GET/POST /backtests/{id}/*`,
  `GET /journal/comparison`, `GET /journal/setup-evidence`; settings `backtest_*`;
  frontend backtest pages; `tests/test_at034_engine.py`, `tests/test_at034_api.py`,
  `tests/test_at034_integration.py`; `docs/backtesting.md` v2; ADR-016.
- Validation: `tests/test_at034_integration.py` green; full backend pytest + ruff
  clean; frontend tests/typecheck/build (WS3); Status DONE after final merge. No deploy; no live trading.
- Recommended model: Grok 4.5 (WS1/WS2) + Composer 2.5 (WS3/WS4)
- ADR: AT-ADR-016 · Docs: `docs/backtesting.md`
- Completion evidence: merged PR #20 (`095e490`, CI 30126077064), PR #21
  (`a46d863`, CI 30126107532), PR #22 (`fb26dea`, CI 30130869273), PR #23
  (`8f9a84b`, CI 30130870524) → `main` @ `8f9a84b` (governance tip `79971fa`). Post-merge local validation:
  AT-034 + slice-35 pytest 43 passed; frontend page tests 7 passed; ruff clean.
  No deploy; paper posture unchanged.

### AT-035 — Research validation loop (backtest evidence → paper candidate queue)
- Priority: P1 · Status: DONE · Dependencies: AT-034, Slice 80
  (paper validation candidate queue) · Risk: Low (advisory only; no execution path)
- Safety classification: Paper-safe / record-only
- Goal: Advisory promotion of completed backtest evidence (tier1/tier2) into the
  existing paper-validation candidate queue with frozen provenance, synthetic
  research-origin alert/draft scaffolding, idempotent per org+backtest run,
  tenant isolation, and RBAC (reader GET, trader POST promote).
- Branch: `feat/at-035-research-validation-loop`
- Deliverables: migration `n0c1d2e3f4a5`; `research_validation_service`,
  routes `/research-validation/*`; candidate provenance fields on
  `PaperValidationCandidateItem`; frontend `/research-validation` page;
  `tests/test_at035_research_validation.py`; `docs/research_validation.md`; ADR-017.
- Validation: disposable Postgres 16 migration upgrade/downgrade/upgrade cycle;
  partial unique index `uq_pvc_org_backtest_active` verified; duplicate active
  promotion blocked at DB layer; targeted `test_at035_research_validation.py` (10)
  green; integrated AT-035 + slice80/81 + at034_api (46) green; ruff + strict mypy
  on touched modules; frontend lint/typecheck/research-validation tests (8) green.
  No deploy; no live trading; risk/execution unchanged.
- Recommended model: Grok 4.5 (WS1) + Composer 2.5 (WS2/WS3)
- ADR: AT-ADR-017 · Docs: `docs/research_validation.md`
- Completion evidence: PR https://github.com/Fejjii/AlphaTrade-AI/pull/24 merged
  (`2f46111`, CI run 30136769341 all green: backend, frontend, deployment-safety,
  docker-build, evaluation, e2e-smoke, Vercel preview). Disposable Postgres 16
  migration upgrade/downgrade/upgrade verified; partial unique index
  `uq_pvc_org_backtest_active` and duplicate active promotion blocked at DB layer.
  No deploy; paper posture unchanged.

### AT-036 — Human-vs-system decision quality (aggregate journal comparison)
- Priority: P1 · Status: DONE · Dependencies: AT-034, AT-031 ·
  Risk: Low (advisory only; no execution path)
- Safety classification: Paper-safe / record-only
- Goal: Extend `GET /journal/comparison` with AT-036 decision-quality metrics,
  human/system actor scorecards, dimension buckets, setup/regime breakdowns, and
  frontend `/journal/comparison` page. Backward compatible with AT-034 three-cohort
  response. Tenant isolation + `ReaderDep` RBAC unchanged.
- Branch: `feat/at-036-human-vs-system-decision-quality` (merged)
- Deliverables: extended `journal_statistics_service.compare_cohorts`; schemas in
  `backtest.py` / `journal_statistics.py`; frontend comparison page + nav;
  `tests/test_at036_journal_comparison.py`; `journal/comparison/page.test.tsx`;
  docs + ADR-018.
- Validation: PR #25 CI run 30139404763 success (backend, frontend,
  deployment-safety, docker-build, evaluation, e2e-smoke, Vercel preview green).
  Local pre-merge targeted: AT-036 + AT-031 + AT-034 API pytest 47 passed;
  frontend comparison + backtest page tests 9 passed. No deploy; no live trading;
  risk/execution unchanged. Per-trade `/human-vs-system/{id}` remains Slice 36.
- Recommended model: Grok 4.5 (WS1) + Composer 2.5 (WS2/WS3)
- ADR: AT-ADR-018 (Accepted) · Docs: `docs/journal_intelligence_foundation.md` §7,
  `docs/human_vs_system.md`, `docs/backtesting.md`, `docs/limitations_roadmap.md`
- Completion evidence: commit `847d85d`, merge `3ff1eb3`, PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/25; CI run 30139404763 success.
  No deploy; paper posture unchanged.

### AT-037 — TradingView Signal Intake and BloFin Read-Only Synchronisation v1
- Priority: P1 · Status: DONE · Dependencies: AT-035, Slice 80, BloFin demo
  account provider · Risk: Medium (webhook surface; exchange read path)
- Safety classification: Paper-safe / demo read-only (no order mutation)
- Goal: Secure TradingView signed webhook intake with idempotent signal lifecycle,
  optional paper-validation candidate routing, plus BloFin demo read-only
  account/position/market-context sync. No order placement.
- Branch: `feat/at-037-tradingview-blofin-sync` (merged)
- Deliverables: migration `o1d2e3f4a5b6`; `tradingview_signals` +
  `blofin_demo_sync_snapshots`; `tradingview_signal_service`, `blofin_sync_service`,
  signature helper; routes `POST /webhooks/tradingview`,
  `GET/POST /tradingview/signals*`, `POST/GET /exchange/blofin/sync*`;
  frontend `/tradingview-signals` + BloFin sync panel; tests
  `test_at037_tradingview_blofin.py` + frontend tests; `docs/tradingview_blofin_sync.md`;
  ADR-019.
- Validation: disposable Postgres 16 migration upgrade/downgrade/upgrade cycle for
  `o1d2e3f4a5b6` verified; PR #26 CI run 30144938569 all green (backend 1371 passed,
  1 skipped; frontend, deployment-safety, docker-build, evaluation, e2e-smoke, Vercel
  preview). Local pre-merge: `test_at037_tradingview_blofin.py` (9) + integrated
  AT-035/blofin (71) + frontend vitest (9) + mypy strict on AT-037 modules. No deploy;
  no live trading.
- Recommended model: Cursor Grok 4.5
- ADR: AT-ADR-019 (Accepted) · Docs: `docs/tradingview_blofin_sync.md`
- Completion evidence: PR https://github.com/Fejjii/AlphaTrade-AI/pull/26 merged
  (`1d52cb3`, CI run 30144938569). Commits `650097a`, `9e09a85`. No deploy; paper
  posture unchanged.

### AT-038 — Automated Paper-Signal Orchestration v1
- Priority: P1 · Status: DONE · Dependencies: AT-037, Slice 80/81,
  ProposalService, KillSwitchService · Risk: Medium (orchestration surface;
  must remain paper-only)
- Safety classification: Paper-safe (no order mutation; no live mode)
- Goal: Connect validated TradingView signals to paper-validation candidates/run
  plans and optional approval-gated paper proposals through a deterministic,
  reviewable orchestration workflow with observe_only / candidate_only /
  approval_required modes.
- Branch: `cursor/at-038-paper-signal-orchestration`
- Deliverables: migration `p2e3f4a5b6c7`; `paper_signal_orchestration_decisions`;
  eligibility + orchestration service; routes under `/paper-signal-orchestration/*`;
  frontend `/paper-signal-orchestration`; tests
  `test_at038_paper_signal_orchestration.py` + frontend page tests;
  `docs/paper_signal_orchestration.md`; ADR-020.
- Validation: PR #27 CI run 30160911227 success (backend, frontend, docker-build,
  deployment-safety, evaluation, e2e-smoke). Frontend flake fix: await
  `paper-draft-candidate-link` inside `waitFor` after draft queue
  (`4c2e49d`). No deploy; `PAPER_SIGNAL_ORCHESTRATION` not enabled on staging;
  paper posture unchanged.
- Recommended model: Cursor Grok 4.5
- ADR: AT-ADR-020 (Accepted) · Docs: `docs/paper_signal_orchestration.md`
- Completion evidence: PR https://github.com/Fejjii/AlphaTrade-AI/pull/27 merged
  (`05b79ea`, CI run 30160911227). Head commit `4c2e49d`.

### AT-043 — Telegram security and interaction protocol foundation
- Priority: P1 · Status: IN_PROGRESS · Dependencies: agentic redesign Phase 8 design
  (`docs/redesign/agentic_redesign_target_architecture.md` §10) · Risk: Medium
  (security-critical; must remain disconnected from execution)
- Safety classification: Security / paper-safe / Telegram disabled
- Goal: Isolated Telegram enrollment, verified private-chat identity, nonce,
  receipt, replay, rate-limit, outbox, delivery-ack, and action-authorization
  contracts. Telegram stays disabled. APPROVE never executes. CLOSE unavailable.
  No FastAPI webhook, no ORM/Alembic, no execution wiring.
- Branch: `cursor/telegram-security-foundation-5bf5`
- Deliverables: `app.telegram_security`; in-memory store; fake transport;
  `docs/telegram_security_protocol.md`; tests
  `test_telegram_security_protocol.py`; `TELEGRAM_INTERACTION_ENABLED=false`.
  Final hardening (PR 79): exact-replay fingerprint +
  `TelegramInboundUpdate.body_size`; closure binds full transport identity and
  resolves exact/conflicting replay before rate-limit charging.
- Validation: targeted protocol tests + full backend pytest + ruff + mypy on
  the new package + GitHub CI. No merge in this task.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-023

### AT-039 — Premium UI/UX blueprint + screen inventory (planning only)
- Priority: P1 · Status: DONE · Dependencies: AT-038 · Risk: Low (docs only)
- Safety classification: Product/design planning; no runtime change
- Goal: Author AlphaTrade-specific premium UI/UX blueprint and full frontend
  screen inventory to guide Phases A–F redesign without changing app behavior.
- Branch: `plan/at-039-premium-ui-ux-blueprint-cloud`
- Deliverables: `docs/product/at039_premium_ui_ux_blueprint.md`,
  `docs/product/at039_screen_inventory.md` (53 routes audited).
- Validation: Docs-only PR #28; no frontend/backend/API/migration/dependency
  changes. Paper-first invariants encoded in blueprint (risk BLOCK final, no UI
  override; provenance/freshness required).
- Recommended model: Cursor Grok 4.5
- Completion evidence: PR https://github.com/Fejjii/AlphaTrade-AI/pull/28 merged
  (`853d96b`). Head commit `7ebc8df`.

### AT-040 — Premium design-system foundation (Phase A) + navigation/app shell (Phase B) + Phase C daily workflows + Phase D analytics planning
- Priority: P1 · Status: IN_PROGRESS (Phase A + B + C1 + C2 + C3A + C3B1 + C3B2 DONE; Portfolio/Risk command centre DONE; Phase D Analytics & Charts blueprint DONE) · Dependencies: AT-039 · Risk: Low (frontend-only / docs)
- Safety classification: UI foundation / shell IA / daily workflow UX; no trading/execution/risk-authority change
- Goal: Introduce dark-first semantic tokens, typography utilities, shared UI
  primitives (incl. PageHeader, FreshnessPill, StatusBadge, Skeleton, Empty/Error/
  Stale/Blocked states, RiskBlock with no UI override, PaperModeIndicator,
  DataNumber), and adopt them on a small representative set of screens without
  route or nav IA changes (Phase A). Then implement AT-039 Phase B navigation and
  app shell (eight destinations, desktop sidebar, mobile bottom nav + Menu sheet,
  StatusStrip advice/execution/risk truth, TopBar page identity + freshness shell
  interface + account control, Settings Billing & Usage consolidation). Phase C1
  implements Dashboard attention queue, Signals inbox, and Plan hub daily loop.
  Phase C2 redesigns Validate as one coherent pipeline (Draft → Candidate →
  Run plan → Run session → Observation → Outcome).
- Branch (Phase A): `feat/at-040-premium-design-system-foundation`
- Branch (Phase B): `cursor/at040-phase-b-nav-shell-ae93`
- Branch (Phase C1): `feat/at040-phase-c1-daily-decision-loop`
- Branch (Phase C2): `cursor/at040-phase-c2-validate-pipeline-53f5`
- Branch (Phase C3A): `cursor/at040-phase-c3a-journal-quick-entry-a54b`
- Deliverables (Phase A): `frontend/src/styles/tokens.css`, tokenized Tailwind + globals,
  `frontend/src/components/ui/*` primitives, states updates, representative page
  adoption (Dashboard, TradingView signals, paper-signal orchestration, journal
  statistics, portfolio), guide `docs/product/at040_design_system_foundation.md`,
  vitest `design-system.test.tsx`.
- Deliverables (Phase B): centralized `navigation-config.ts`, DesktopSidebar /
  MobileBottomNavigation / MobileMenuSheet / SecondaryNavigation / StatusStrip /
  TopBar / CommandMenu / `ShellFreshnessContext`, Billing & Usage at
  `/settings/billing`, Portfolio ownership of `/risk`, session-dismissible advice
  truth, e2e logout via account menu.
- Validation: PR #29 CI run 30165820718 success (backend, frontend, docker-build,
  deployment-safety, evaluation, e2e-smoke). No `package.json` dependency adds;
  no routes removed; `nav-items.ts` unchanged; zero backend/API/migration files.
  Phase A hardening PR #30 CI run 30174563899 success; post-merge main CI run
  30178168238 success (backend, frontend, docker-build, deployment-safety,
  evaluation, e2e-smoke). Phase B PR #31 CI run 30200035610 success; post-merge
  main CI run 30201145776 success (frontend, backend, docker-build,
  deployment-safety, evaluation, e2e-smoke). No deploy; live trading unchanged.
- Recommended model: Cursor Grok 4.5
- Completion evidence: PR https://github.com/Fejjii/AlphaTrade-AI/pull/29 merged
  (`c414378`, CI run 30165820718). Head commit `34135be`. Phase A hardening PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/30 merged (`d988576`, CI run
  30174563899; post-merge main CI 30178168238). Hardening head `e9e930c`
  (TabsRoot shared id prefix + fail-closed paper-mode / limitations hardening).
  Phase B PR https://github.com/Fejjii/AlphaTrade-AI/pull/31 merged (`fc148ff`,
  pre-merge head `7f7818c`, CI run 30200035610; post-merge main CI 30201145776).
  Phase C1 PR https://github.com/Fejjii/AlphaTrade-AI/pull/32 merged
  (`f7fafd4`; pre-merge head `95d669a`; validated implementation commit
  `7e42fe8`; pre-merge CI 30211212052 success; post-merge main CI 30212388146
  success — frontend, backend, docker-build, deployment-safety, evaluation,
  e2e-smoke). Correction pass recorded: SourceResult availability, runtime
  safety-truth matrix (`isPaperModeConfirmed`), conservative shell freshness
  aggregation (available sources with missing/invalid/future timestamps
  contribute unavailable; live + unknown cannot yield page-level Live), signal
  deep-link honesty, Plan signal context query, and session-only dismiss
  labeling. Phase C1 complete; Phase C2 Validate pipeline complete; Phase C3A Journal
  hub + quick-entry complete.
- Phase C1 deliverables: Dashboard attention queue; Signals inbox on
  `/tradingview-signals`; Plan hub on `/workspace`; `WorkflowFreshnessAdapter`;
  unknown-route identity `AlphaTrade`; account-menu Escape focus restore;
  SourceResult partial-data honesty; confirmed-paper-only safety badges;
  conservative multi-source shell freshness with unknown-timestamp contribution.
- Phase C2 deliverables: Validate hub at `/paper-validation`;
  pipeline components (`ValidationPipeline`, stage/summary cards, attention queue,
  source availability, `OutcomeSummary`); SourceResult honesty on stage lists;
  preserved detail routes with related-stage links; confirmed-paper posture;
  Risk BLOCK no override; no auto-promote/start; no backend/API/migration changes.
  Honesty correction pass: independent session observation/outcome SourceResult
  loads (404 = not recorded; other failures = unavailable); Setup Alert Review
  `?alert=` deep-link highlight/focus without mutation; candidate→run-plan
  active preference + accessible plan links with partial-data Retry when run
  plans fail. Final coverage/freshness pass: typed outcome coverage statuses
  (`not_applicable` / `complete` / `partial` / `unavailable`) with separate
  `renderable` / `fullyAvailable` / `errorCount`; partial Outcomes in
  partial-data warning without Live freshness; session outcome UI states
  (`loading` / `recorded` / `confirmed_not_recorded` / `unavailable`) gate the
  recording form. Loading-versus-unavailable pass: explicit observation source
  states (`loading` / `available` / `unavailable`) and outcome source states in
  `OutcomeSummary`; initial load shows neutral loading copy (no premature
  unavailable/Retry/zero); retry shows retrying/refreshing without unavailable;
  completed sessions use historical missing-outcome wording.
  Phase C2 PR https://github.com/Fejjii/AlphaTrade-AI/pull/33 merged
  (`cd77790`; pre-merge head `0e0a1af`; validated implementation commit
  `921929e`; pre-merge CI 30224234541 success; post-merge main CI 30225088594
  success — frontend, backend, docker-build, deployment-safety, evaluation,
  e2e-smoke). Phase C2 complete.
- Phase C3A deliverables: Journal hub at `/journal`; needs-journaling queue with
  `journalCoverage`/`positionsCoverage` honesty; recent entries; quick-entry using
  existing journal create/prefill fields; prefill relationship reset on context
  change/loading/invalid/cleared; SourceResult multi-source honesty; confirmed-PAPER
  posture; preserved Import/Lessons/Knowledge/Statistics/Comparison reachability;
  no backend/API/migration changes.
  Phase C3A PR https://github.com/Fejjii/AlphaTrade-AI/pull/34 merged
  (`dc19bc8`; pre-merge head `8b57a68`; validated implementation commit
  `d2aa71e`; pre-merge CI 30229681876 success; post-merge main CI 30231014521
  success — frontend, backend, docker-build, deployment-safety, evaluation,
  e2e-smoke). Phase C3A complete.
- Phase C3B1 deliverables (review hub only — no Knowledge redesign):
  Lessons review hub at `/lessons`; attention queue (`pending_review` only);
  recently reviewed (accepted + rejected); source context + next-action guidance;
  SourceResult multi-source honesty; preserved accept/reject mutations + typed
  confirmations; journal/strategy/validation relationship links from stored fields only;
  `?candidate=` deep-link verification; coaching source filter; confirmed-PAPER posture;
  correction passes: pagination coverage honesty (complete/truncated), all-status
  deep-link rendering, global mutation lock, confidence 0 display, deep-link +
  coaching-filter visibility, corrected truncated count wording;
  no backend/API/migration changes.
  Phase C3B1 PR https://github.com/Fejjii/AlphaTrade-AI/pull/35 merged
  (`edbc038`; pre-merge head `7ccc005`; validated implementation commit
  `219f2c8`; pre-merge CI 30257024612 success; post-merge main CI 30258584822
  success — frontend, backend, docker-build, deployment-safety, evaluation,
  e2e-smoke). Phase C3B1 complete.
- Phase C3B2 deliverables (Knowledge hub only — no Portfolio/Risk/Analytics):
  Premium Knowledge hub at `/knowledge`; list documents/chunks via existing APIs;
  loaded-page library search + semantic search; source_type filters; recently added
  (created_at desc); category honesty (definitive totals only for unfiltered complete
  coverage); source context; `source_uri` relationship links only
  (`journal://` / `lesson://` / `strategy://…/vN`); `?document=` deep-link verification
  within loaded coverage; chunk detail retry; confirmed-PAPER posture; correction pass
  for category/search/URL-sync/deeplink/retry/lockfile/mobile wrap;
  no backend/API/migration changes.
  Phase C3B2 / Knowledge Hub PR https://github.com/Fejjii/AlphaTrade-AI/pull/36 merged
  (`041f0c4`; pre-merge head / implementation head
  `a4aed6fc9913ae2c5bc6cf8d300ff56eab4b2495`; pre-merge CI 30264373051 success;
  post-merge main CI 30267507921 success — frontend, backend, docker-build,
  deployment-safety, evaluation, e2e-smoke). Phase C3B2 / Knowledge Hub DONE.
- Portfolio/Risk command centre deliverables:
  `/portfolio` owns paper account overview, exposure, history, and risk posture
  (daily discipline + kill-switch fail-closed); `/risk` remains configuration-only.
  Honesty corrections: unresolved/stale kill-switch never means clear; cached BLOCK
  remains authoritative after refresh failure; drawdown/daily P&L wording distinguishes
  today vs selected-range; journal `?entry=` deep links; empty equity is confirmed empty
  (not truncated); positions link labeled "View positions"; separate Daily discipline /
  Kill switch source rows. Frontend-only; no Knowledge/nav/backend changes.
  Portfolio/Risk command centre PR https://github.com/Fejjii/AlphaTrade-AI/pull/37
  merged (`7788320`; pre-merge / implementation head
  `0a2fcad698b7997b74ef3402006e73cedbf1a35a`; pre-merge CI 30277821355 success;
  post-merge main CI 30279764094 success — frontend, backend, docker-build,
  deployment-safety, evaluation, e2e-smoke). Portfolio/Risk command centre DONE.
- Critical data-honesty and Portfolio RiskBlock fixes deliverables:
  `/settings/audit` loading/error honesty (EmptyState only after successful empty
  response); `/settings/team` explicit loading/loaded/failed list state (empty wording
  only after successful empty response; create/revoke preserved); Portfolio
  `buildRiskPosture` kill-switch BLOCK precedence when Daily discipline is
  loading/absent, failed, or missing snapshot (`showRiskBlock=true`, stored reason,
  discipline values unavailable, limitations explain source condition). Frontend-only;
  no `/analytics`, `useAsyncData.ts`, Knowledge, nav, backend, API, migration, or
  deployment changes.
  Critical data-honesty / RiskBlock fixes PR https://github.com/Fejjii/AlphaTrade-AI/pull/44
  merged (`aaa33f4`; pre-merge / implementation head
  `0ab606984edab99550d70d77b70a51111f8a861d`; pre-merge CI 30287687997 success;
  post-merge main CI 30291493531 — frontend, backend, docker-build, deployment-safety,
  evaluation, e2e-smoke). Critical data-honesty / RiskBlock fixes DONE.
- Analytics PR 1 deliverables (foundation, filters, Overview + Performance):
  `/analytics` with Overview + Performance tabs; URL-synced filters; honest SourceResult
  loading per widget; Overview stats with journal/portfolio provenance; Performance charts
  (daily P&L, cumulative realised P&L) via lazy-mounted Recharts; freshness gating;
  ISO-week roll-up; chart transforms with malformed-value honesty; no changes to audit,
  invitations, portfolio, or `useAsyncData.ts`.
  Analytics PR 1 PR https://github.com/Fejjii/AlphaTrade-AI/pull/43 merged
  (`257f3a2`; pre-merge / implementation head
  `82202e6fd7d3c527b958b6622dc3ec8f2a58c60e`; pre-merge CI 30294572981 success;
  post-merge main CI 30296686361 success — frontend, backend, docker-build,
  deployment-safety, evaluation, e2e-smoke). Analytics PR 1 DONE.
- Analytics PR 2 deliverables (Setup and Strategy Analytics):
  `/analytics?tab=setups` with win-rate and expectancy charts, paged bucket table,
  grouping toggle, URL-synced journal setup_id deep links, setup-identity integrity
  (journal UUIDs never routed to portfolio), Setups journal source filter, honest
  setup-evidence provenance, and compact chart labels; no reopen of Portfolio/Risk
  or Analytics PR 1 scope.
  Analytics PR 2 PR https://github.com/Fejjii/AlphaTrade-AI/pull/47 merged
  (`1c3077b03737fa8f60c15cb673ff58e6d14b58ea`; pre-merge / implementation head
  `e4bfdacd90c9965f738f7c13c88d14d523d4c642`; pre-merge CI 30313626587 success —
  frontend, backend, docker-build, deployment-safety, evaluation, e2e-smoke;
  post-merge main CI 30315387747 success — same six jobs). Analytics PR 2 DONE.
- Analytics PR 3 deliverables (Behaviour and human-versus-system comparison):
  `/analytics?tab=behaviour` with rule-compliance chart, dual discipline score cards
  (proposal-flow vs validation-session, distinctly labeled and linked), risk-behaviour
  warning counts (counts, not performance), independent per-widget source slots and
  freshness presentation; `/analytics?tab=comparison` with human-vs-system paired bars
  and decision-quality tiles; all five Analytics tabs functional; no backend/API changes;
  no reopen of Portfolio/Risk, Analytics PR 1, or Analytics PR 2 scope.
  Analytics PR 3 PR https://github.com/Fejjii/AlphaTrade-AI/pull/46 merged
  (`8fffc0ff0e861f4b553f4ae3babd384d006f3b83`; pre-merge / implementation head
  `1ae5b3e43297ba4cbffb881a897315fe6ae1a564`; pre-merge CI 30350773804 success —
  frontend, backend, docker-build, deployment-safety, evaluation, e2e-smoke;
  post-merge main CI 30352734328 success — same six jobs). Analytics PR 3 DONE.
- Phase D Analytics & Charts blueprint deliverables (documentation only — no
  frontend/backend/chart implementation): authoritative implementation plan at
  `docs/product/at040_analytics_and_charts_blueprint.md`; verified capability
  inventory + metric availability matrix; six-tab Analytics IA; chart specs;
  filter model with setup-identity integrity; currency-agnostic monetary display;
  App Router URL-history contract (`router.push` for committed actions;
  `router.replace` only for cleanup/canonicalisation); four-PR sequence;
  complete-CI merge evidence; Composer 2.5 for PRs 1–4.
  Analytics & Charts Blueprint PR https://github.com/Fejjii/AlphaTrade-AI/pull/38
  merged (`ee7cf9d`; pre-merge / implementation head
  `e82d4fc3287a693ea755901e01e2b5d868ee8ecb`; pre-merge CI 30271331488 success;
  post-merge main CI 30273039335 success — frontend, backend, docker-build,
  deployment-safety, evaluation, e2e-smoke). Analytics & Charts blueprint DONE.
  Governance record PR https://github.com/Fejjii/AlphaTrade-AI/pull/40 merged
  (`9d42d10`).
- Phase C remaining: none (Portfolio/Risk merged).
- Analytics implementation roadmap: Analytics PR 1 complete; Analytics PR 2 complete;
  Analytics PR 3 complete; **Analytics PR 4** (validation analytics and final chart polish)
  is the immediate next implementation — fresh Composer 2.5 agent chat from latest `main`;
  do not reopen Portfolio/Risk or Analytics PRs 1–3 scope in that chat. Final readiness
  audit follows Analytics PR 4 and final polish (see blueprint §8 PR 4 and
  `docs/product/at040_final_polish_and_readiness_audit.md` when merged).

### AT-042 — Watcher orchestration foundation (Phase 7 worker; isolated)
- Priority: P1 · Status: IN_PROGRESS · Dependencies: agentic redesign Phase 7
  contracts; Agent 1 source freshness later · Risk: Medium
- Safety classification: Paper-safe; watcher remains disabled; no execution,
  journal, Telegram, candidates, or ORM/migrations
- Goal: Isolated worker architecture — scan request / watchlist policy contracts,
  leases, fencing, retry, scan lineage, dedupe, idempotent scheduling, manual
  and worker evaluation-boundary parity, health, failure reporting, observability,
  and deterministic test repositories.
- Branch: `cursor/watcher-orchestration-foundation-4364`
- Validation: Targeted foundation tests plus full backend, ruff, mypy, GitHub CI.
  Tenant isolation: two organizations with identical `scan_scope` acquire independent
  leases/fences/heartbeats/health/lineage. `WATCHER_ORCHESTRATION_ENABLED` default
  false; no Alembic migrations.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-022

---

## Live-trading program (design → gated implementation; do NOT start before paper Criticals)

### AT-020 — Phase 0: Mode D safety specification (docs/ADRs only)
- Priority: P1 · Status: TODO · Dependencies: AT-010, AT-011 · Risk: Low
- Safety classification: Architecture / no live code
- Branch: `docs/at-020-live-safety-spec`
- Validation: ADRs for order FSM, limits, credentials, promotion gates, incident SEVs;
  no app behavior change; paper defaults unchanged.
- Recommended model: Grok 4.5

### AT-021 — Phase 1: Execution port + sandbox/testnet adapter (no real trading)
- Priority: P2 · Status: TODO · Dependencies: AT-020, AT-007, AT-012, AT-014 · Risk: High
- Safety classification: Mode C sandbox only; `ENABLE_REAL_TRADING` stays false
- Branch: `feat/at-021-execution-port-sandbox`
- Validation: Sandbox contract tests; freshness/idempotency/partial-fill chaos; host allowlist;
  verify-safety still paper-only.
- Recommended model: Grok 4.5 / Opus 4.8

### AT-022 — Phase 2: Approval-gated sandbox execution + circuit breakers
- Priority: P2 · Status: TODO · Dependencies: AT-021, AT-008 · Risk: High
- Branch: `feat/at-022-approval-gated-sandbox`
- Validation: Dual-control E2E; breaker + kill drills; no real credentials.
- Recommended model: Opus 4.8

### AT-023 — Phase 3: Tiny-capital pilot (authorization-gated; separate program)
- Priority: P3 · Status: TODO · Dependencies: AT-022 + explicit human Mode D authorization · Risk: Critical
- Safety classification: Mode D — cannot proceed via ordinary impl task
- Branch: short-lived after written authorization (never long-lived live branch)
- Validation: Written approval; trade-only keys; tiny notional; reconcilation clean; kill proven.
- Recommended model: Opus 4.8

### AT-024 — Phase 4: Controlled scale-up (authorization-gated)
- Priority: P3 · Status: TODO · Dependencies: AT-023 · Risk: Critical
- Safety classification: Mode D ladder — each step REVIEW_REQUIRED
- Validation: Promotion checklist evidence; limit ladder tests; no auto-promote.
- Recommended model: Opus 4.8

---

## Gap analysis (Phase 7) — queued, do NOT implement in bootstrap task

Baseline: verified repo already has strong coverage (deterministic risk engine, guardrails,
provider fallbacks, auth/RBAC, audit + usage quotas, evaluation harness, CI with 6 jobs,
paper-only enforcement, staging deploy). Gaps below are incremental hardening.

### AT-001 — Type-checking (mypy --strict) in CI
- Priority: P1 · Status: TODO · Dependencies: none · Risk: Low
- Gap: `mypy` is configured (`pyproject.toml`, strict) but CI runs only ruff + pytest for backend.
- Validation: CI job runs `uv run mypy src` green; no runtime behavior change.
- Recommended model: GPT-5.4 / Sonnet 4.6

### AT-002 — LangSmith tracing / structured LLM observability
- Priority: P2 · Status: TODO · Dependencies: none · Risk: Low
- Gap: `LANGSMITH_API_KEY` exists but tracing provider is a mock placeholder (per docs).
- Validation: opt-in tracing behind env flag; disabled by default; no secrets logged.
- Recommended model: GPT-5.4

### AT-003 — Scale AI evaluation beyond deterministic fixtures
- Priority: P2 · Status: TODO · Dependencies: AT-002 · Risk: Medium
- Gap: eval harness is deterministic/mock; no scored LLM eval or regression thresholds in CI gating.
- Validation: eval runs with thresholds; env-guarded for real providers; deterministic default.
- Recommended model: Opus 4.8

### AT-004 — Supply-chain security (dependency + secret scanning, pinned actions)
- Priority: P1 · Status: TODO · Dependencies: none · Risk: Low
- Gap: no automated dependency/secret scanning or SBOM in CI; actions not SHA-pinned.
- Validation: CI adds dependency audit + secret scan; build still green; no code behavior change.
- Recommended model: GPT-5.4

### AT-005 — Deploy rollback runbook + smoke gating on deploy
- Priority: P2 · Status: DONE · Dependencies: none · Risk: Low
- Branch: `feat/at-005-deploy-rollback-smoke-gate` (merged via PR #15)
- Goal: Document exact rollback triggers/steps/verification/failure handling; automate
  post-deploy smoke gate (`verify-safety.sh` + staging smoke) wired into deploy checklists.
- Validation: `docs/deploy_rollback_runbook.md` present; `scripts/post-deploy-smoke-gate.sh`
  `--self-check` exit 0; gate wired into staging checklist/runbook/`RELEASE.md`; CI
  deployment-safety self-check; paper-only posture unchanged; no staging deploy performed.
- Recommended model: Sonnet 4.6 (backlog) · Grok 4.5 (this lane)
- ADR: AT-ADR-011
- Completed: 2026-07-24 — merged via PR #15 → `main` @ merge `f145599` (commit `4d2617c`);
  CI run 30057647347 success (backend 1210 passed, 1 skipped; deployment-safety,
  frontend, docker-build, evaluation, e2e-smoke all green). Gate profiles: safety /
  standard / extended; exit `1` documented as rollback trigger. Live staging gate run
  deferred to next authorized deploy.

### AT-006 — Cost/usage guardrail alerting
- Priority: P2 · Status: TODO · Dependencies: AT-002 · Risk: Low
- Gap: org quotas exist; no proactive alert when approaching token/cost thresholds.
- Validation: threshold alerts (in-app only; external delivery stays disabled); tests for limits.
- Recommended model: GPT-5.4

### AT-007 — Data freshness/degradation conservative-mode audit
- Priority: P1 · Status: TODO · Dependencies: none · Risk: Medium (safety-critical)
- Gap: confirm every consumer of market/vector data enforces conservative behavior on
  stale/degraded/conflicting inputs (Qdrant degraded fallback, Binance rate-limit fallback).
- Validation: tests asserting conservative paths; no real trading; provenance preserved.
- Recommended model: Opus 4.8

### AT-008 — Frontend E2E coverage for approval/refusal safety paths
- Priority: P2 · Status: TODO · Dependencies: none · Risk: Low
- Gap: expand Playwright coverage of real-trading refusal and approval gating in UI.
- Validation: e2e specs pass in CI; paper-only asserted.
- Recommended model: Sonnet 4.6

---

## Agentic redesign — market evidence

### AT-041 — Phase 5 market source contracts and evidence foundations
- Priority: P0 · Status: DONE · Dependencies: Phases 1–4 on `main` · Risk: Medium
- Safety classification: Paper-safe / read-only market evidence; no execution path
- Goal: Contract-test Binance USD-M perpetual OHLCV and aggregate trades for the first
  BTCUSDT 15m/4h slice; freeze identity, finality, freshness, cursor/gap, CVD, signed
  quote flow, provenance, and replay fixtures. No watcher, Telegram, candidates,
  execution, or frontend.
- Branch: `cursor/phase5-market-contracts-852a`
- PR: https://github.com/Fejjii/AlphaTrade-AI/pull/80 (draft; do not merge)
- Validation: 62 Phase 5 tests cover closed/forming candles, wrong market/instrument/source,
  immutable full-window coverage, missing prefix/suffix, gap/duplicate/out-of-order/cursor
  recovery, freshness/stale, CVD exact arithmetic, proven signed quote flow, aggressor
  semantics, content-hash stability, replay determinism, provider provenance, regional
  failure, HTTPS host binding, aggTrades pagination, and no spot fallback. Local Ruff clean;
  mypy `--strict` on `src/app/market_contracts` Success (23 files). Full backend and all
  GitHub CI jobs passed on closure revision `d73ed26602e01f438707717d184be0b798c74ea9`
  (1723 backend tests; run 35146515639). Final exact provider/source-boundary hardening is
  included in the follow-up PR #80 HEAD validation. Paper posture unchanged.
- Recommended model: Grok 4.6
- ADR: AT-ADR-021 · Docs: `docs/market_source_contracts.md`

### AT-044 — Phase 6 contract freeze (observations, fusion, candidates)
- Priority: P0 · Status: DONE · Dependencies: AT-041 Phase 5 market
  contracts; Phase 3 compiled setup identity · Risk: Medium (identity authority)
- Safety classification: Paper-safe / contracts only; no evaluator, persistence,
  watcher, Telegram, execution, or live trading
- Goal: Freeze typed immutable Phase 6 domain contracts and
  `CanonicalEvidenceWindowV1` hashing so parallel agents share one identity
  system. Reuse Phase 5 `PublicMarketObservation` and Phase 3
  `CompiledSetupDefinition` / `TradeDirection` / `Timeframe`.
- Branch: `cursor/phase6-contract-freeze`
- Deliverables: `app.signal_fusion`; tests in
  `backend/tests/test_phase6_signal_fusion_contracts.py` and
  `backend/tests/test_phase6_contract_hardening.py`. Hardening covers
  market-identity integrity, policy-selected tenant assertions, canonical
  set semantics, and revision-aware observation identity. No Alembic, no
  evaluator, no adapter implementations.
- Validation: Merged to `main` via PR #84 (`d8193ec`) and golden fixture
  corpus PR #82 (`addf5ef`). Paper posture unchanged.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-024

### AT-045 — Phase 6 deterministic fusion evaluator (setup truth only)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-044 Phase 6 contract
  freeze · Risk: Medium (setup-truth authority)
- Safety classification: Paper-safe / evaluator only; no PostgreSQL, Alembic,
  watcher, Telegram, execution, or live trading
- Goal: Implement the deterministic first-slice fusion evaluator that consumes
  frozen `FusionPolicy`, `AssessmentCommand`, `CanonicalEvidenceWindowV1`, and
  Phase 5 evidence and emits authoritative `SetupAssessment` for
  “Bearish Liquidity Sweep with CVD Divergence and Aggressive Sell Imbalance
  at 4h Resistance” on BTCUSDT perpetual 15m/4h.
- Branch: `cursor/phase6-deterministic-evaluator-0069` (source PR #87);
  integrated on `cursor/phase6-evaluator-candidate-integration`
- Deliverables: `app.signal_fusion.evaluator.evaluate_setup`; synthetic matrix
  in `backend/tests/test_phase6_fusion_evaluator.py`. No Candidate persistence,
  no PR 84 contract semantic changes.
- Validation: evaluator suite + Phase 5 + Phase 6 contract tests; full backend
  pytest; ruff; mypy `--strict` on `src/app.signal_fusion`; GitHub CI.
  Draft integration PR only; do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-025

### AT-046 — Phase 6 candidate lifecycle service (in-memory authority)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-044 Phase 6 contract
  freeze · Risk: Medium (identity authority)
- Safety classification: Paper-safe / in-memory application service; no
  PostgreSQL, Alembic, watcher, Telegram, execution, or live trading
- Goal: Implement canonical Candidate authority: idempotent creation from
  CONFIRMED_SETUP + exact CanonicalEvidenceWindowV1 + tenant-owned
  CompiledSetupDefinition; uniqueness via CandidateUniquenessTuple;
  append-only transitions; terminal non-resurrection; tenant isolation;
  organization-scoped creation idempotency and candidate-scoped transition
  idempotency. PaperValidationCandidate remains a downstream consumer only.
- Branch: `cursor/phase6-candidate-lifecycle-service-1c4d` (source PR #86);
  integrated on `cursor/phase6-evaluator-candidate-integration`
- PR: https://github.com/Fejjii/AlphaTrade-AI/pull/86 (source; do not merge)
- Deliverables: `app.signal_fusion.lifecycle`, `ports`, `memory`; tests in
  `backend/tests/test_phase6_candidate_lifecycle.py`. No fusion evaluator
  coupling, no Alembic, no PostgreSQL adapter.
- Validation: lifecycle tests + Phase 6 contract tests; full backend pytest;
  ruff; mypy `--strict` on `src/app.signal_fusion`; GitHub CI.
  Draft integration PR only; do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-026

### AT-047 — Phase 6 evaluator + candidate runtime foundation integration
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-044, AT-045, AT-046,
  golden fixture corpus on `main@addf5ef` · Risk: Medium (identity composition)
- Safety classification: Paper-safe / in-memory runtime foundation; no
  PostgreSQL, Alembic, watcher, Telegram, eligibility, TradePlan, execution,
  frontend, or live trading
- Goal: Produce one clean Phase 6 runtime foundation containing deterministic
  setup evaluation, canonical candidate lifecycle authority, and the already
  merged golden fixtures. Combined flow: canonical evidence → CONFIRMED_SETUP
  → exactly one ACTIVE candidate; duplicate semantic evaluation converges.
- Branch: `cursor/phase6-evaluator-candidate-integration`
- Deliverables: integrated `app.signal_fusion` evaluator + lifecycle; combined
  tests in `backend/tests/test_phase6_evaluator_candidate_flow.py`. Do not
  overwrite `backend/tests/fixtures/phase6_first_slice/`.
- Validation: golden fixture suite, Phase 5 market suite, Phase 6 contract
  suite, evaluator suite, candidate lifecycle suite, combined flow tests,
  full backend pytest, ruff, mypy `--strict` on `src/app.signal_fusion`,
  GitHub CI. Draft PR only; do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-027

### AT-048 — Phase 6 deterministic ActionEligibility (paper action gate)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-047 evaluator +
  candidate runtime foundation · Risk: Medium (safety-critical gating)
- Safety classification: Paper-safe / in-memory application service; no
  PostgreSQL, Alembic, watcher, Telegram, TradePlan, execution, or live trading
- Goal: Implement the deterministic ActionEligibility service. SetupAssessment
  remains market truth. Eligibility decides whether a confirmed canonical
  Candidate may proceed toward paper TradePlan creation from account, portfolio,
  risk, safety, stale action evidence, paper configuration, and a first-slice
  20 bps cross-venue basis gate. Kill switch dominates. Live trading cannot
  make a result executable.
- Branch: `cursor/phase6-action-eligibility-078e`
- Deliverables: `app.signal_fusion.action_eligibility`; tests in
  `backend/tests/test_phase6_action_eligibility.py`. Frozen
  `ActionEligibilityState` remains `ELIGIBLE | BLOCKED | EXPIRED`. No TradePlan,
  no venue APIs, no Alembic.
- Validation: Phase 1 risk/safety regressions, Phase 6 contracts, evaluator,
  candidate lifecycle, new ActionEligibility tests, full backend pytest, ruff,
  mypy `--strict` on `src/app.signal_fusion`, GitHub CI. Draft PR to main;
  do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-028

### AT-049 — Watcher to Phase 6 fusion wiring (first slice)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-042, AT-047 / PR #88
  head `046929cbe612e1bafc2e87ebe794d9b59300ec35` · Risk: Medium
  (orchestration identity)
- Safety classification: Paper-safe / orchestration wiring only; watcher
  remains disabled; no scheduler, Telegram, TradePlan, execution, Alembic,
  or live trading
- Goal: Connect Watcher scan → canonical market evidence →
  CanonicalEvidenceWindowV1 → evaluate_setup → SetupAssessment → canonical
  Candidate only when CONFIRMED_SETUP. Manual and worker evaluation must
  converge for identical semantic evidence. First slice: Bearish Liquidity
  Sweep with CVD Divergence and Aggressive Sell Imbalance at 4h Resistance,
  BTCUSDT perpetual, 15m trigger, 4h context.
- Branch: `cursor/phase6-watcher-fusion-wiring`
- Deliverables: `WatcherFusionEvaluationService` as the single evaluation
  boundary; integration tests for parity, gating, fencing, tenant isolation,
  and crash/retry convergence. Do not redesign WatcherStore, Phase 5/6
  contracts, the evaluator, or candidate lifecycle authority.
- Validation: watcher tests, Phase 5, Phase 6 contracts/evaluator/lifecycle,
  new integration tests, full backend pytest, ruff, mypy `--strict` on
  `src/app/watcher` and `src/app/signal_fusion`, GitHub CI. Draft PR only;
  do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-029
  Wave B integration note: original source PR claimed `AT-048` / `AT-ADR-028`;
  those IDs were already used by ActionEligibility, so this task is `AT-049`.

### AT-050 — Phase 6 Candidate Telegram alert foundation
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-043 Telegram security
  protocol; AT-046/AT-047 canonical Candidate · Risk: Medium (identity +
  authorization boundary)
- Safety classification: Paper-safe / Telegram disabled; no webhook, no
  execution, no PostgreSQL adapter, no Alembic
- Goal: Bind canonical Candidate events to deterministic CandidateAlertIntent
  identity and the existing Telegram outbox/security contracts. APPROVE is
  authorization intent only and never executes. REJECT/SKIP use typed
  Candidate transitions. REDUCE_RISK must not mutate Candidate. CLOSE stays
  unavailable. EXECUTE_PAPER_PLAN stays outside Telegram.
- Branch: `cursor/phase6-telegram-candidate-alerts-0960`
- Deliverables: `app.candidate_alerts`; tests in
  `backend/tests/test_phase6_candidate_telegram_alerts.py`; docs
  `docs/phase6_candidate_telegram_alerts.md`.
- Validation: Candidate alert tests, Telegram protocol tests, Phase 6
  Candidate tests, full backend pytest, ruff, mypy `--strict`, GitHub CI.
  Draft PR to main only; do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-030
  Wave B integration note: original source PR claimed `AT-048` / `AT-ADR-028`;
  those IDs were already used, so this task is `AT-050`.

### AT-051 — Phase 6 canonical Candidate PostgreSQL persistence
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-046/AT-047 Candidate
  authority; AT-049 Watcher fusion wiring; PR #92 remaining fencing race
  · Risk: Medium (identity + fencing)
- Safety classification: Paper-safe / PostgreSQL adapter only; Watcher,
  Telegram, and live trading remain disabled; not wired into FastAPI or workers
- Goal: Production-grade PostgreSQL `CandidateRepository` matching the existing
  port. Persist canonical Candidate projections and append-only transitions.
  Preserve deterministic identity, uniqueness, tenant isolation, idempotency,
  terminal non-resurrection, replay convergence, and conflict detection.
  Worker-originated Candidate persistence must be protected by current lease
  and fencing authority in the same database transaction.
- Branch: `cursor/phase7-candidate-persistence-5115`
- Deliverables: `app.persistence.candidate_postgres`, Candidate ORM,
  Alembic `4fd8c1a90b27`, Watcher persist fence bind, tests in
  `backend/tests/test_phase6_candidate_postgres.py`.
- Validation: focused Candidate/Postgres/fencing tests; full backend pytest;
  ruff; mypy `--strict`; Alembic upgrade/downgrade and single head; GitHub CI.
  Draft PR only; do not merge.
- Recommended model: Cursor Grok 4.6
- ADR: AT-ADR-031

### AT-052 — Phase 7 canonical TradePlanRevision application layer
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-046/AT-047 Candidate
  authority; AT-048 ActionEligibility; AT-051 Candidate PostgreSQL; Phase 1
  TradePlanRevision hash contract
  · Risk: Medium (plan identity + immutability)
- Safety classification: Paper-safe / application service; Watcher, Telegram,
  Journal, execution dispatch, frontend, and live trading remain disabled
- Goal: Canonical flow Candidate → ActionEligibility → TradePlanRevision →
  approval → paper execution, with this slice owning plan creation only. Only
  ACTIVE + ELIGIBLE may insert; lineage and tenant scope must match; semantic
  content immutable; identical requests converge; conflicting idempotency
  fails closed; PLAN_CREATED only after successful insert; PVC cannot mint
  canonical plans; approval cannot change executable semantics.
- Branch: `cursor/phase7_tradeplan_canonical` (source PR #93); integrated on
  `cursor/phase7_integration`
- Deliverables: `CanonicalTradePlanService`, `CanonicalTradePlanStore`,
  lineage envelope. Source PR claimed `AT-051` / `AT-ADR-031`; those IDs were
  already used by Candidate PostgreSQL, so this task is `AT-052`.
- Validation: focused canonical tests, Phase 1 planning/approval tests,
  Phase 6 candidate/eligibility tests, full backend pytest, ruff, mypy
  `--strict`, GitHub CI. Draft PR to main; do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-032

### AT-053 — Phase 7 learning attribution (canonical lifecycle → learning)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: Phase 4 canonical journal
  projector; Phase 6 Candidate / SetupAssessment contracts · Risk: Medium
  (lineage / learning integrity)
- Safety classification: Paper-safe / record-only; no execution, Watcher,
  Telegram, frontend, Alembic, or live trading
- Goal: Connect SetupAssessment → Candidate → TradePlan → paper execution →
  JournalTrade → outcome → strategy/pattern stats → learning evidence without a
  second trading authority. Reuse `JournalLifecycleProjector`. REJECT/SKIP never
  create executed trade outcomes. Distinguish planned setup quality from
  execution quality and trader behavior. LLMs may explain, not rewrite facts.
- Branch: `cursor/phase7_learning_attribution` (source PR #95); integrated on
  `cursor/phase7_integration`
- Deliverables: `app.learning_attribution`;
  `JournalLifecycleLearningService`; sticky `payload.lineage`; lesson/analytics/RAG
  adapters; tests in
  `backend/tests/test_learning_attribution.py` and
  `backend/tests/test_journal_lifecycle_lineage.py`; docs
  `docs/phase7_learning_attribution.md`. Source PR claimed `AT-051` /
  `AT-ADR-031`; those IDs were already used by Candidate PostgreSQL, so this
  task is `AT-053`.
- Validation: attribution tests, journal lifecycle tests, learning tests, full
  backend pytest, ruff, mypy `--strict` on the new package plus journal lifecycle
  modules, GitHub CI. Draft PR only; do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-033

### AT-054 — Phase 7 canonical TradePlan / ActionEligibility PostgreSQL binding
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-051 Candidate PostgreSQL;
  AT-052 canonical TradePlan application layer
  · Risk: Medium (identity, FKs, append-only history)
- Safety classification: Paper-safe / PostgreSQL adapter only; Watcher, Telegram,
  and live trading remain disabled; not wired into FastAPI or workers
- Goal: Close the PR 93 persistence gap after Candidate migration `4fd8c1a90b27`.
  Durable ActionEligibility with deterministic identity and append-safe revision
  history. Bind canonical TradePlanRevision to canonical Candidate authority
  without reinterpreting legacy PaperValidationCandidate ids. Bind setup identity
  to tenant-owned CompiledSetupDefinition. Persist canonical lineage without
  changing CanonicalTradePlanContentV1. Organization-scoped uniqueness and
  idempotency. Deterministic candidate-based plan root. Approval still binds
  exact immutable revision + content hash. Execution remains outside this wave.
- Branch: `cursor/phase7_integration`
- Deliverables: Alembic `c9e2b4a1d078`, `PostgresActionEligibilityStore`,
  `PostgresCanonicalTradePlanStore`, discriminator columns, lineage side table,
  tests in `backend/tests/test_phase7_*_postgres.py`.
- Validation: focused eligibility/plan/alembic tests; full backend pytest; ruff;
  mypy `--strict`; Alembic upgrade/downgrade/reupgrade and single head; GitHub CI.
  Draft PR only; do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-034

### AT-055 — Phase 8 learning persistence (canonical attribution → queryable intelligence)
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-053 Phase 7 learning
  attribution; Phase 4 canonical journal projector
  · Risk: Medium (lineage / learning integrity / schema)
- Safety classification: Paper-safe / PostgreSQL adapter and query services;
  FastAPI/runtime wiring owned by later Phase 8 slices
- Goal: Persist canonical learning attribution in PostgreSQL. Preserve immutable
  lineage SetupAssessment → Candidate → TradePlan → paper execution →
  JournalTrade → outcome. Duplicate facts converge; conflicting identities and
  cross-tenant writes fail closed. REJECT/SKIP never become executed outcomes.
  Separate setup quality, execution quality, risk adherence, trader behavior,
  and outcome. Produce deterministic strategy/pattern statistics and
  human-versus-system comparison. Support future demo trade learning as a
  separate venue cohort. Learning never rewrites historical market truth. LLM
  narrative remains explanation only. Facts are consumable by analytics and RAG
  without competing journal/lesson authorities.
- Branch: `cursor/phase8_learning_persistence-843e` (source); integrated on
  `cursor/phase8_final_integration`
- Deliverables: Alembic `d4f7a2c8e901`, `PostgresAttributionStore`,
  `LearningQueryService`, journal lineage query columns, tests in
  `backend/tests/test_phase8_learning_persistence.py`, docs
  `docs/phase8_learning_persistence.md`.
- Validation: Migration cycle, idempotency, conflicts, tenant isolation,
  attribution correctness, strategy aggregation, RAG fact boundaries; full
  backend pytest; ruff; mypy `--strict`; GitHub CI. Source PR #97.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-035

### AT-056 — Phase 8 canonical PAPER runtime execution
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-051 Candidate PostgreSQL;
  AT-052 canonical TradePlan application layer; AT-054 PostgreSQL binding;
  AT-055 learning persistence
  · Risk: Medium (execution safety, lineage, idempotency)
- Safety classification: Paper-safe / runtime wiring; Watcher, Telegram, and
  live trading remain disabled; no real exchange mutation
- Goal: Wire Phase 7 PostgreSQL Candidate, ActionEligibility, and canonical
  TradePlan adapters into FastAPI and workers. Complete the canonical PAPER
  lifecycle: Evidence → SetupAssessment → Candidate → ActionEligibility →
  TradePlanRevision → approval → EXECUTE_PAPER_PLAN → journal. CandidateLifecycleService
  remains sole Candidate authority. Execution requires the exact approved
  immutable TradePlanRevision. Duplicate requests converge. Stale/rejected/
  expired/mismatched/modified plans fail closed. Risk engine BLOCK and kill
  switch stay final. `canonical_plan_root` is never ProposalService trading
  authority.
- Branch: `cursor/phase8_runtime_execution` (source); integrated on
  `cursor/phase8_final_integration`
- Deliverables: `app.runtime.canonical`, `CanonicalPaperExecutionService`,
  `POST /execution/paper-plan`, ProposalService filter, journal projection
  source `canonical_paper_execution`, tests
  `backend/tests/test_phase8_*.py`, docs `docs/phase8_runtime_execution.md`.
  No Alembic.
- Validation: adversarial execution tests (restart/idempotency, tenant
  isolation, stale state, kill switch, risk rejection, duplicates, journal);
  full backend pytest; ruff; scoped `mypy --strict`; GitHub CI. Source PR #99
  claimed AT-055; remapped to AT-056 on the RC branch.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-036

### AT-057 — Canonical paper decision frontend
- Priority: P1 · Status: DONE · Dependencies: AT-040 design system;
  AT-055 learning persistence; AT-056 runtime execution · Risk: Medium
  (UX honesty vs unbound canonical HTTP)
- Safety classification: Frontend / paper-only; no live execution control
- Goal: User-facing canonical decision workflow: market assessment → candidate
  → eligibility → TradePlan → human approval → paper execution → outcome →
  learning. Reuse design system. Do not invent backend authority. Bind
  canonical TradePlan execution to `POST /execution/paper-plan`.
- Branch: `cursor/phase8_canonical_frontend` (source); integrated on
  `cursor/phase8_final_integration`
- Deliverables: `/decision` screens, typed contracts for canonical HTTP,
  frontend API clients for paper-plan + canonical reads + learning queries,
  tests, docs `docs/redesign/phase8_canonical_frontend.md`.
- Validation: frontend lint, typecheck, unit tests, build, relevant e2e,
  GitHub CI. Source PR #98 claimed AT-055; remapped to AT-057 on the RC
  branch.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-037

### AT-058 — Phase 8 final integration release candidate
- Priority: P0 · Status: DONE · Dependencies: AT-055, AT-056, AT-057
  · Risk: Medium (execution binding, learning durability, authority isolation)
- Safety classification: Paper-only integration; Watcher, Telegram, and live
  trading remain disabled; no deploy
- Goal: Independently review and integrate PR97 + PR99 + PR98. Bind frontend
  canonical TradePlan execution to `POST /execution/paper-plan`. Wire durable
  Postgres learning attribution and canonical read/query APIs. Keep one Alembic
  head. Do not merge `main`. Do not deploy.
- Branch: `cursor/phase8_final_integration`
- Deliverables: remapped governance IDs, `/canonical/*` reads,
  `PostgresAttributionStore` runtime hook, frontend paper-plan binding,
  `backend/tests/test_phase8_canonical_workflow.py`.
- Validation: full backend pytest, frontend tests, lint, typecheck, build,
  ruff, scoped mypy, Alembic cycle, deployment-safety, evaluation, e2e,
  Docker build, GitHub CI.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-038

### AT-059 — Final backend hardening (release residues)
- Priority: P0 · Status: DONE · Dependencies: AT-058 Phase 8 final integration
  · Risk: Medium (learning integrity, claim-time risk, deployment safety)
- Safety classification: Paper-only; Watcher, Telegram, and live trading remain disabled;
  no deploy
- Goal: Close remaining medium backend residues before release. Canonical learning
  attribution must not silently skip after ALLOW. Claim-time risk stays
  `evaluate_claim_predicate` plus persisted ActionEligibility (do not wire unused
  `PaperExecutionRiskGate` as a second authority). Approval/execution/journal/learning
  stay one unit of work under failure. Restart/duplicate converge. Canonical reads stay
  tenant-safe. Paper-only runtime and deployment safety keep accidental real trading
  impossible.
- Branch: `cursor/final_backend_hardening` (source); integrated on
  `cursor/final_release_integration-c461`
- Validation: adversarial hardening tests; source GitHub CI run 35464006608
  success at `60d5ef9` (backend, frontend, deployment-safety, evaluation,
  docker-build, e2e-smoke). Draft source PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/102 — do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-039

### AT-060 — Finish canonical decision UX (bind GET APIs, paper-plan only)
- Priority: P0 · Status: DONE · Dependencies: AT-057, AT-058 · Risk: Medium
  (UX honesty / tenant isolation)
- Safety classification: Frontend + ProposalService firewall; paper-only
- Goal: Bind `/decision` to canonical GET APIs (candidates, setup assessments,
  eligibility, execution receipts, learning records, strategy stats). Remove
  stale unbound copy. Canonical decision execution uses
  `POST /execution/paper-plan` only. PVC/legacy proposals stay compatibility
  views. ProposalService checks tenant scope before `canonical_plan_root`
  rejection (no existence oracle).
- Branch: `cursor/final_canonical_ux-1b0c` (source); integrated on
  `cursor/final_release_integration-c461`
- Validation: frontend lint/typecheck/1155 tests/build; Chromium e2e 24 passed /
  13 skipped; source PR #101 exact-head CI run 35462481756 success at `3fd8a0a`.
  Integrated on `cursor/final_release_integration-c461`. Source PR #101 claimed
  AT-059; remapped to AT-060. Draft PR only; do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-037

### AT-061 — Final synthetic staging readiness
- Priority: P0 · Status: DONE · Dependencies: AT-058 Phase 8 RC on `main@c39dca6`
  · Risk: Medium (ops + safety locks)
- Safety classification: Paper-only staging readiness; Watcher, Telegram, and live
  trading remain disabled; no real exchange credentials; no merge
- Goal: Confirm the canonical architecture can deploy to paper staging. Document
  required env vars. Enforce paper mode, Watcher off, Telegram off, real trading
  impossible. Add synthetic HTTP smoke for auth, Candidate reads, eligibility,
  TradePlan approval, paper execution, journal, learning, strategy stats, decision
  frontend, kill switch, risk BLOCK, and cross-tenant rejection. Deploy only if
  cloud credentials already exist; otherwise stop at the human boundary.
- Branch: `cursor/final_staging_readiness` (source); integrated on
  `cursor/final_release_integration-c461`
- Deliverables: staging safety locks, `/health` posture flags, canonical smoke
  script + pytest, `docs/RELEASE_READINESS.md`, rollback chain notes.
- Validation: source PR #103 exact-head CI run 35465464233 success at `24133a1`.
  Staging deploy remains an operator/credential step, not this task. Source PR
  #103 claimed AT-059 / AT-ADR-039; remapped to AT-061 / AT-ADR-040. Draft PR
  only; do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-040

### AT-062 — Final paper-release candidate integration
- Priority: P0 · Status: DONE · Dependencies: AT-059, AT-060, AT-061
  · Risk: Medium (overlap of safety locks, UX binding, staging smoke)
- Safety classification: Paper-only integration; Watcher, Telegram, and live
  trading remain disabled; no deploy; no merge to main
- Goal: Independently review and integrate PR102 + PR101 + PR103 on
  `main@c39dca6`. Preserve fail-closed learning, canonical GET/paper-plan UX,
  and synthetic staging smoke. Resolve overlapping `deployment_safety` and
  governance IDs semantically. Produce `docs/FINAL_RELEASE_READINESS.md`.
- Branch: `cursor/final_release_integration-c461`
- Validation: backend pytest 2271 collected, exit 0; ruff check/format 765 files;
  mypy `--strict` 29 affected files; Alembic single head `d4f7a2c8e901` with
  upgrade/downgrade/reupgrade; evaluation 16/16 + 5/5 + 7/7; frontend lint/
  typecheck/1155 tests/build; Chromium e2e 24 passed / 13 skipped; canonical
  smoke self-check + focused 3 smoke tests; GitHub CI run 35466201940 success
  (backend, frontend, deployment-safety, evaluation, docker-build, e2e-smoke).
  Draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/104 — do not merge;
  do not deploy. Report `docs/FINAL_RELEASE_READINESS.md`.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-041

### AT-063 — Intelligence integration of PR109, PR110, and PR111
- Priority: P0 · Status: IN_PROGRESS · Dependencies: PR #109, PR #110, PR #111
  on `main@20d2cac` · Risk: High (evaluation, evidence identity, confirmation)
- Safety classification: Paper-only integration; Watcher, Telegram, and live
  trading remain disabled; no deploy; no merge to main
- Goal: Independently review and integrate PR109 (canonical strategy policy),
  PR110 (canonical evidence pipeline), and PR111 (strategy conversations).
  Close verified correctness gaps: evidence identity, Watcher
  `executable_policy`, freshness vs candle close, conversation-to-pattern
  preview, confirmation safety. Keep one Alembic head. Preserve journal
  `account_id` from PR #106.
- Branch: `cursor/intelligence_integration-1ea1`
- Validation: focused regressions, then full backend/frontend suites, lint,
  types, build, Alembic/PostgreSQL, safety, evaluations, relevant browser
  tests, exact-HEAD GitHub CI. Draft PR only; do not merge; do not deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-046

### AT-064 — Canonical live read-only USD-M evidence pipeline
- Priority: P0 · Status: DONE · Dependencies: PR #108 market contracts on
  `main@20d2cac` · Risk: Medium (freshness honesty)
- Safety classification: Paper-only read path; Watcher, Telegram, and live
  trading remain disabled; no exchange mutation; no merge
- Goal: Assemble CanonicalEvidenceWindowV1 from existing Binance USD-M contracts
  (BTCUSDT first, multi-symbol catalog). Expose truthful current price +
  freshness on GET `/canonical/evidence`. Stop canonical UI from presenting
  frozen compatibility prices as live marks. Preserve replay fixtures.
  Do not activate Watcher.
- Branch: `cursor/live_evidence_pipeline-5b0d` (source); integrated on
  `cursor/intelligence_integration-1ea1`
- Deliverables: `app.evidence_pipeline`, catalog, canonical evidence HTTP,
  `/decision/market` honesty, fail-closed tests (fresh/stale/partial/outage/
  wrong-symbol/duplicate/restart/source/CVD/tenant).
- Validation: source PR #110 exact-head CI run 35527904094 SUCCESS at `afd4d2a`.
  Draft source PR https://github.com/Fejjii/AlphaTrade-AI/pull/110 — do not merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-042
- Note: Source PR #110 claimed AT-064 / AT-ADR-042; kept. Source PR #111 also
  claimed AT-064 / AT-ADR-042; remapped to AT-065 / AT-ADR-043.

### AT-065 — Persistent strategy conversation + discussion context
- Priority: P0 · Status: DONE · Dependencies: PR #107 audit · Risk: Medium
  (transcript vs strategy authority)
- Safety classification: Paper-only; no Watcher, Telegram, live trading, or
  autonomous activation
- Goal: Durable tenant-scoped conversations and messages. History survives
  restart. Chat must not become a second memory authority. Discussions may
  reference strategies, versions, journal, lessons, learning attribution, and
  statistics via existing services + RAG.
- Branch: `cursor/strategy-conversation-foundation-5d46` (source); integrated on
  `cursor/intelligence_integration-1ea1`
- Validation: `backend/tests/test_strategy_conversation_foundation.py`;
  persistence + restart + tenant 404; RAG source-type boundary tests.
  Source PR #111 exact-head CI run 35529286291 SUCCESS at `057b89b`.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-043
- Note: Source PR #111 claimed AT-064 (persistence) and AT-065 (discussion
  context); remapped to AT-065 on integration because AT-064 is the evidence
  pipeline.

### AT-066 — Structured proposals stay drafts until explicit confirmation
- Priority: P0 · Status: DONE · Dependencies: AT-065 · Risk: High
  (silent mutation of strategy authority)
- Safety classification: Confirmation-gated version fork; no compile/activation
  on confirm
- Goal: AI may explain, challenge, compare, and propose. Every mutation needs
  explicit confirmation. Provenance links conversation → proposal → version.
  Strategy Lab conversational UI + API. Confirmation safety must check proposal
  identity, content hash, target strategy, and captured parent version.
- Branch: `cursor/strategy-conversation-foundation-5d46` (source); integrated on
  `cursor/intelligence_integration-1ea1`
- Validation: confirm, reject, duplicate confirm, lineage, prompt-injection
  mutation attempts; concurrent confirmation races.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-044
- Note: Source PR #111 claimed AT-066 / AT-ADR-042; ADR remapped to AT-ADR-044.

### AT-067 — Canonical strategy evaluation policy (first-slice adapter)
- Priority: P0 · Status: DONE · Dependencies: AT-066, PR #107, PR #108
  · Risk: High (evaluation authority)
- Safety classification: Paper-only; no Watcher enablement; no Telegram; no live trading
- Goal: One deterministic evaluation policy boundary: approved immutable
  `UserStrategyVersion` → `CompiledSetupDefinition` → canonical evidence →
  `evaluate_canonical_strategy` → `SetupAssessment`. `evaluate_setup` remains
  sole market-truth function. First-slice predicates become a compatibility
  adapter. Drafts and unsupported rules fail closed. Watcher (disabled) and
  paper-validation canonical entry call the same boundary. No Candidate mint
  from read-projection placeholder IDs.
- Branch: `cursor/canonical-strategy-policy-82f1` (source); integrated on
  `cursor/intelligence_integration-1ea1`
- Validation: 18 AT-067 tests (determinism, version change, unsupported rule,
  stale evidence, strategy/evidence mismatch, duplicate evaluation, lineage,
  compatibility parity, tenant isolation, no-LLM, Watcher/paper boundary).
  Source PR #109 exact-head CI run 35528565992 SUCCESS at `872f5de`. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/109 — do not merge independently.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-045
- Completion evidence: feat commit `4d5bfb9`; source PR #109; remapped from
  source AT-ADR-043. Source PR #111 left AT-067 as TODO; closed by integrating
  PR #109.

### AT-069 — Live read-only perpetual market monitoring
- Priority: P0 · Status: DONE · Dependencies: AT-064, Phase 5 contracts
  · Risk: Medium (freshness honesty + stream identity)
- Safety classification: Paper-only read path; Watcher, Telegram, and live
  trading remain disabled; no exchange mutation; no merge
- Goal: Continuously provide trustworthy Binance USD-M perpetual evidence
  (BTCUSDT first, catalog-extensible) with current price, OHLCV, trade stream,
  CVD, coverage, freshness, source identity, provider status, reconnect/backoff,
  rate-limit handling, and gap detection. Never present replay, demo-seed, or
  compatibility snapshots as the current live price.
- Branch: `cursor/live-market-monitoring-cc4d`
- Deliverables: `app.market_monitor`, `GET /canonical/market-status`,
  decision/market + /market honesty, fail-closed stream tests.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-048
- Completion evidence: HEAD `69371ee` on `cursor/live-market-monitoring-cc4d`;
  draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/118. Local: monitor
  25/25; full backend 2219 passed / 181 skipped; frontend lint/typecheck;
  1171 unit tests; Next build; E2E 26 passed / 13 skipped. Watcher, Telegram,
  and live trading stay off. Do not merge or deploy.

### AT-068 — Durable setup lifetime + canonical AUTO_PAPER authority
- Priority: P1 · Status: IN_PROGRESS · Dependencies: AT-067, intelligence
  acceptance review P1s · Risk: High (lifetime identity + paper mint authority)
- Safety classification: Paper-only; no Watcher enablement; no Telegram; no live trading
- Goal: Persist setup-lifetime pins so restart reconstructs the same trigger
  and expiry; remove `PaperBotEngine` as AUTO_PAPER minting authority. Automated
  paper trades consume persisted APPROVED/ACTIVE compiled policy → canonical
  evidence → `evaluate_canonical_strategy` → `CONFIRMED_SETUP` only.
- Branch: `cursor/intelligence-acceptance-final-fix-5138`
- Validation: PostgreSQL restart/migration tests; non-vacuous CONFIRMED_SETUP
  paper mint; fail-closed draft/stale/expired/wrong-tenant/wrong-hash; ruff;
  mypy; full backend; frontend; e2e; exact-head CI. Draft PR only; no merge.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-047

### AT-070 — Paper-only continuous Watcher runtime
- Priority: P0 · Status: DONE · Dependencies: AT-042, AT-064, AT-067, AT-068, AT-069
  · Risk: High (continuous worker, leases, Candidate persist)
- Safety classification: Paper monitoring only; staging/production Watcher flags stay
  false; no Telegram; no live orders; no real exchange credentials
- Goal: Wire the missing continuous worker: tenant-scoped approved compiled strategy
  → live read-only market evidence → `WatcherOrchestrator` scan →
  `evaluate_canonical_strategy` → persist Candidate only on `CONFIRMED_SETUP`.
  Reuse existing orchestrator, Postgres store, leases/fencing, canonical assembler,
  `resolve_executable_strategy_policy`, CandidateLifecycleService, and durable
  setup lifetime. BTCUSDT first; configurable symbols; bounded polling; single
  active worker per scan scope.
- Branch: `cursor/watcher_paper_runtime-5455` (source); remapped from source AT-069
- Validation: 22 paper-runtime tests (concurrent, lease takeover, restart replay,
  duplicate scan, stale, outage, wrong tenant, wrong lineage, CONFIRMED_SETUP,
  WATCH/NO_SETUP, expiry, kill switch); ruff check/format; full backend pytest
  exit 0 (Postgres-backed tests skipped locally — no local Postgres; GitHub CI
  has the service); frontend lint/typecheck/test/build; evaluation 16/16, 5/5,
  7/7. Draft PR only; do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-049
- Note: Source PR #120 claimed AT-069 / AT-ADR-048; remapped because AT-069 is
  the live market monitor (PR #118).
- Completion evidence: feat `1de8b60`; draft PR #120. GitHub CI run 35614998968
  failed on `test_concurrent_workers_single_lease` (StaticPool SQLite shared
  across worker threads). Follow-up isolates that test from SQLite and surfaces
  ThreadPoolExecutor exceptions; do not merge until that head is green.

### AT-071 — Watcher PAPER MONITORING operator UX + observability
- Priority: P1 · Status: DONE · Dependencies: AT-ADR-040, AT-ADR-022,
  AT-067, AT-070 · Risk: Medium (honesty of runtime status; no authority change)
- Safety classification: Paper-only observability; no Watcher enablement; no
  Telegram; no live trading; no evaluator/strategy-authority change
- Goal: Operator-facing Watcher paper-monitoring surface showing runtime
  status (`RUNNING`/`STOPPED`/`DEGRADED`/`STALE`/`BLOCKED`), symbols,
  approved strategies, last/next scan, market freshness, provider health,
  SetupAssessment lineage, candidates, block reasons, lease/worker health,
  recent errors, and paper-only posture. Typed API only; never infer
  RUNNING from frontend config; no fake activity/prices/candidates.
- Branch: `cursor/watcher_monitoring_ux-c026` (source); remapped from source AT-069
- Validation: backend projection + `/market-watcher/monitoring` 27 tests passed;
  frontend lint+typecheck+1189 tests+build pass; Watcher monitoring E2E 4/4;
  full Chromium E2E 30 passed / 13 skipped; mypy on new modules clean.
  Draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/119 — do not merge
  or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-050
- Note: Source PR #119 claimed AT-069 / AT-ADR-048; remapped because AT-069 is
  the live market monitor (PR #118) and AT-070 is the paper runtime (PR #120).
- Completion evidence: feat `6572f71`, fix `9d36ee1`; Watcher stays disabled.

### AT-072 — Integrate paper Watcher stack (live market → runtime → monitoring UX)
- Priority: P0 · Status: DONE · Dependencies: AT-069, AT-070, AT-071 · Risk: High
  (evidence authority, worker safety, monitoring honesty)
- Safety classification: Paper-only integration; Watcher/Telegram/live trading stay off;
  no deploy; no source-PR merge; no Watcher activation
- Goal: Reconcile PR #118 live monitor, PR #120 paper runtime, and PR #119 monitoring UX
  onto `main@b4244f0` without merging those PRs. One evidence authority (monitor gate +
  canonical assembler). One evaluator path to CONFIRMED_SETUP Candidates. Monitoring
  reports real heartbeat/lease evidence. Freshness clocks stay separated. Fail closed on
  stale, outage, wrong tenant, wrong lineage, expired setup, and in-memory policy.
- Branch: `cursor/watcher_integration-b74b`
- Validation: focused stack 115 passed; full backend with PostgreSQL 2464 passed / 0 skipped;
  frontend lint+typecheck+1193 tests+build; Ruff check/format; strict mypy on affected
  modules; Alembic single head `c8d9e0f1a2b3`; evaluation 16/16, 5/5, 7/7; Chromium E2E
  30 passed / 13 skipped; deployment safety 60 passed; Docker image build; GitHub CI
  run 35627618727 success on `cc226f7`. Draft PR only; do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-051
- Completion evidence: feat `2bb9c40`, fix `cc226f7`; draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/121. Watcher, Telegram, and live trading
  stay off. Do not merge, deploy, or activate Watcher.

### AT-073 — Continuous paper evaluation and learning measurement
- Priority: P0 · Status: DONE · Dependencies: AT-072 · Risk: Medium
  (measurement honesty; must not become a second trading authority)
- Safety classification: Paper-only measurement; Watcher/Telegram/live trading stay off;
  refinements cannot auto-activate
- Goal: Connect Watcher → SetupAssessment → Candidate → paper decision → paper trade →
  outcome → Journal → attribution → strategy statistics → learning evidence → refinement
  suggestion. Operator-visible evaluation summaries. Deterministic facts stay separate
  from AI narrative. AI may suggest a refinement and must not activate it.
- Branch: `cursor/continuous_paper_evaluation-80e2`
- Validation: GitHub CI run 35661040375 success on `416b61e` — backend 2480 passed /
  0 skipped; frontend lint+typecheck+1195 tests+build; e2e-smoke 30 passed;
  evaluation 16/16, 5/5, 7/7; deployment-safety; docker-build. Local full pytest
  2298 passed / 181 skipped (Postgres unavailable). Strict mypy on paper_evaluation
  and canonical reads. Alembic head `e3f4a5b6c7d8`. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/122 — do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-052
- Completion evidence: feat `a90ab93`, mypy `b1fdab9`, CI fix `416b61e`. Watcher,
  Telegram, and live trading stay off. Refinements cannot auto-activate. Do not
  merge, deploy, or activate Watcher.

### AT-074 — Telegram paper interaction layer
- Priority: P0 · Status: DONE · Dependencies: AT-043, AT-072, candidate-alert
  foundation · Risk: High (Telegram must never become trading authority)
- Safety classification: Paper-only interaction; Telegram stays disabled by default;
  no webhook; no Watcher activation; no live trading; no deploy; no merge
- Goal: Watcher meaningful event → durable notification → Telegram alert → bound
  discussion of evidence/strategy/Candidate/risk. Mutating paper actions stay
  identity-bound confirmation gated. Support Watcher alerts, Candidate alerts,
  strategy discussion, market context, paper trade status, journal outcome, and
  learning summary. Deduplicate, persist delivery, retry safely, isolate tenants,
  rate-limit, audit, recover after restart.
- Branch: `cursor/telegram_paper_agent-aac1` (cloud suffix; requested
  `cursor/telegram_paper_agent`)
- Validation: focused Telegram/paper-agent + protocol + Candidate-alert tests
  passed; full backend pytest 2497 passed / 0 skipped; ruff check/format;
  strict mypy 21 affected files; frontend lint+typecheck+1193 tests+build;
  evaluation 16/16, 5/5, 7/7; Chromium E2E 30 passed / 13 skipped;
  deployment-safety 60 passed; smoke-gate self-checks; GitHub CI run
  35658947838 success on `aa75c04`. Source Alembic head was `d9e0f1a2b3c4`
  directly after `c8d9e0f1a2b3`. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/124 — do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-053
- Completion evidence: feat `aa75c04`. Telegram, Watcher, and live trading stay
  off. Do not merge, deploy, or enable Telegram.
- Note: Source PR #124 claimed AT-073 / AT-ADR-052. Remapped because AT-073 is
  continuous paper evaluation (PR #122). Does not enable
  `TELEGRAM_INTERACTION_ENABLED`, Watcher `PERSIST_AND_NOTIFY`, or live trading.
  `EXECUTE_PAPER_PLAN` remains unavailable on Telegram.

### AT-075 — Integrate continuous evaluation and Telegram paper interaction
- Priority: P0 · Status: DONE · Dependencies: AT-072, AT-073, AT-074 · Risk: High
  (one trading authority; Telegram and evaluation must not mutate it)
- Safety classification: Paper-only composition; Watcher/Telegram/live trading stay off;
  no deploy; no source-PR merge; no strategy auto-activation; no Telegram orders
- Goal: One linear Alembic chain. Watcher scan → durable notification → Telegram
  discussion, and Watcher/Candidate/paper outcome → Journal → attribution →
  evaluation → refinement suggestion. Facts stay separate from AI narrative.
- Branch: `cursor/telegram_evaluation_integration-b2d5`
- Validation: local backend pytest 2517 passed / 0 skipped / 0 failed with
  PostgreSQL; ruff check and format; strict mypy on paper_interaction, watcher
  paper worker, and fusion evaluation; frontend lint, typecheck, 1195 tests,
  and build; evaluation 16/16, 5/5, 7/7; deployment-safety 60 passed; smoke-gate
  and canonical staging smoke self-checks. Alembic single head `d9e0f1a2b3c4`.
  GitHub CI pending on this integration HEAD. Draft PR only; do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-054
- Note: Does not enable Watcher, Telegram, or live trading. Do not merge or deploy.
  Source PRs #122 and #124 stay unmerged. The final paper loop on the remediated
  Watcher is AT-076; this task's source PR #125 stays unmerged.

### AT-076 — Final integrated intelligent paper system
- Priority: P0 · Status: DONE · Dependencies: AT-072, AT-073, AT-074, AT-075,
  PR #126 Watcher remediation · Risk: High (one authority per decision; no activation)
- Safety classification: Paper-only composition; Watcher/Telegram/live trading stay off;
  no deploy; no source-PR merge; no strategy auto-activation; no Telegram orders
- Goal: Integrate PR #125 evaluation and Telegram onto PR #126 Watcher remediation.
  Live read-only market → approved compiled strategy → Watcher → canonical
  SetupAssessment → Candidate → paper decision / TradePlan → paper execution →
  Journal → attribution → evaluation → learning suggestion, with a parallel
  durable Telegram notification and discussion. Preserve one authority each for
  market evidence, strategy, SetupAssessment, Candidate, and automated paper
  decision. Telegram and AI stay advisory.
- Branch: `cursor/final_paper_system_integration`
- Base: PR #126 `75bee6d74c71edb39a73a5965784b6d388b7772d`. Semantic source:
  PR #125 `189d9752ec3daaa512fbde42127ed321f11f5f61`. Neither source PR is merged.
- Alembic: single head `d9e0f1a2b3c4` via `c8d9e0f1a2b3` → `e3f4a5b6c7d8` →
  `d9e0f1a2b3c4`. No new revision.
- Validation: implementation `9b1d8e1`. Local PostgreSQL pytest 2532 passed /
  0 skipped; ruff check and format clean; strict mypy 29 affected files clean;
  Alembic single head `d9e0f1a2b3c4`, upgrade / downgrade base / upgrade on
  `alphatrade_migrate`; evaluation 16/16, 5/5, 7/7; frontend lint, typecheck,
  1195 tests, build; Chromium E2E 30 passed / 13 skipped (optional staging and
  browser tours; `PLAYWRIGHT_STAGING_*` and `PLAYWRIGHT_BROWSER_E2E` unset);
  deployment-safety pytest 60 passed plus smoke self-checks; Docker image
  `alphatrade-backend:ci` built. GitHub CI run 35711151145 success on `9b1d8e1`
  (backend 2532 passed, e2e 30 passed / 13 skipped, evaluation, deployment-safety,
  frontend, docker-build). Draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/127.
  Do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-055
- Note: Does not enable Watcher, Telegram, or live trading. Do not merge, deploy,
  or activate. Source PRs #125 and #126 stay unmerged.

### AT-077 — Controlled staging read-only Binance USD-M evidence
- Priority: P0 · Status: DONE · Dependencies: AT-064, AT-069 · Risk: High
  (market-evidence honesty; must not become trading or Watcher activation)
- Safety classification: Public read-only USD-M evidence configuration;
  Watcher/Telegram/live trading stay off; no credentials; no deploy
- Goal: Make `binance_usdm` the intended staging evidence source through the
  existing read-only provider. Preserve replay for tests and rollback. Fail
  closed on outage, stale evidence, gaps, wrong symbol, and wrong source.
  Expose activation on health/market status and add a staging validator.
- Branch: `cursor/activation_live_market`
- Base: `main@894e9e4`
- Validation: implementation `ed17355`. Local activation suite 17 passed.
  Local `uv run pytest -q` exit 0 (2549 collected; Postgres was not
  listening, so postgres-gated tests skipped and the retained log dropped
  the pass/skip split). Local ruff check and format clean. Strict mypy on
  the activation, health, monitor, deployment-safety, factory, and config
  modules clean. Local frontend lint, typecheck, 1196 tests, and build
  passed. Local evaluation 16/16, 5/5, 7/7. GitHub CI run 35740379070
  success on `ed17355`: backend 2549 passed, 236 warnings, 1693.63s;
  deployment-safety 60 tests at 100% plus live-market staging self-check;
  frontend 1196 passed and build; evaluation 16/16, 5/5, 7/7; e2e 30 passed
  / 13 skipped; docker image `alphatrade-backend:ci` built. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/128. Do not merge or deploy.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-056
- Note: Does not modify the live staging environment and does not deploy.
  Exact env changes, activation, and rollback are in
  `docs/live_market_staging_activation.md`. Do not enable Watcher, Telegram,
  or live trading.

### AT-078 — Prepare controlled staging Watcher paper activation
- Priority: P0 · Status: DONE · Dependencies: AT-076 · Risk: High
  (activation path must stay disarmed and paper-only)
- Safety classification: Paper monitoring only; Telegram off; live trading off;
  no deploy; no staging environment edits; no activation
- Goal: Smallest safe arm on the existing Watcher. Preflight, runtime health
  gate, rollback plan, post-activation smoke, and monitoring checklist.
  Prove the worker cannot start on replay evidence, provider outage, invalid
  lineage, real trading, or an unhealthy migration.
- Branch: `cursor/activation-watcher-paper-5263`
- Base: `main` `894e9e4`
- Alembic: unchanged single head `d9e0f1a2b3c4`. No new revision.
- Validation: local PostgreSQL pytest 2567 passed / 0 skipped (exit 0);
  `tests/test_watcher_postgres_store.py`, ownership, product proof, final paper
  loop, Alembic postgres, and activation tests included. `ruff check` and
  `ruff format --check` clean. Strict mypy clean on the activation modules.
  Evaluation 16/16, 5/5, 7/7. Frontend lint, typecheck, 1195 tests, and build
  passed. Activation, rollback, health, and smoke self-checks passed.
  `WATCHER_PAPER_STAGING_ACTIVATION` remains false. Not armed. Staging env files
  unchanged. GitHub CI run 35744229457 success on `557ce32` (backend,
  deployment-safety, docker-build, frontend, e2e-smoke, evaluation, and the
  existing Vercel preview checks). Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/130. Do not merge, deploy, or
  activate.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-057
- Note: Preparation only. Source PR #130 labeled this AT-077 / AT-ADR-056.
  The integration branch assigns AT-078 / AT-ADR-057 because AT-077 is the
  live-market workstream. `WATCHER_PAPER_STAGING_ACTIVATION` stays false.
  Do not deploy or activate.

### AT-079 — Controlled Telegram paper activation
- Priority: P0 · Status: DONE · Dependencies: AT-074, AT-076 · Risk: High
  (Telegram must never become trading authority)
- Safety classification: Paper-only activation machinery; Watcher stays off;
  Telegram stays disarmed; no live trading; no staging env changes; no deploy
- Goal: Production-shaped path from a Watcher/Candidate event through the durable
  outbox to a Telegram alert and an identity-bound paper discussion, plus
  preflight, smoke, and rollback. Do not activate.
- Branch: `cursor/activation_telegram_paper-a361` (requested
  `cursor/activation_telegram_paper`)
- Validation: `uv run ruff check .` and `uv run ruff format --check .` passed.
  `uv run pytest -q --tb=line` failed only
  `test_blackbox_telegram_cannot_place_orders` and
  `test_no_inbound_telegram_route_exists` because
  `GET /health/telegram-paper-activation` was outside the known route set.
  Those allowlists were updated and the two tests plus
  `tests/test_telegram_paper_activation.py`,
  `tests/test_telegram_paper_activation_postgres.py`, and
  `tests/test_phase2_4_alembic_postgres.py` passed (20). Frontend lint,
  typecheck, 1195 unit tests, and build passed. Evaluation 16/16, RAG 5/5,
  guardrails 7/7. Preflight `--expect-disabled` exit 0 (`NOT_ARMED`).
  Rollback `--apply` exit 2. Docker image build was not run (no Docker daemon).
  No live Telegram send. Not deployed. Not activated.
- Recommended model: Cursor Grok 4.6 Extra High
- ADR: AT-ADR-058
- Note: Source PR #129 labeled this AT-077 / AT-ADR-056. The integration
  branch assigns AT-079 / AT-ADR-058. Does not enable Watcher, Telegram, or
  live trading. Does not change `render.yaml`. Do not deploy or activate.

### AT-080 — Integrate controlled paper activation
- Priority: P0 · Status: DONE · Dependencies: AT-077, AT-078, AT-079 · Risk: High
  (one staging package must stay paper-only and fail closed)
- Safety classification: Paper execution; public read-only USD-M evidence;
  no exchange credentials; no exchange mutation; Telegram cannot trade;
  no deploy; no live staging environment edits
- Goal: One package from real read-only Binance USD-M evidence through the
  Watcher, canonical SetupAssessment, Candidate, paper workflow,
  Journal/evaluation/learning, and a verified Telegram projection, with one
  activation order and one fail-closed rollback.
- Branch: `cursor/controlled_paper_activation_integration`
- Base: `main` `894e9e4`
- Head: `9535a10`
- Alembic: single head `e0f1a2b3c4d5` (revises `d9e0f1a2b3c4`)
- Inputs not merged: PR #128 `fe705a2`, PR #129 `4f244d6`, PR #130 `99af425`
- Validation: `uv run ruff check .` and `uv run ruff format --check .` exit 0.
  Strict mypy on the 12 affected modules exit 0. `uv run pytest -q --tb=line`
  exit 0: 2613 passed, 0 failed, 0 skipped (progress marks match
  `pytest --collect-only` of 2613; the short summary line was absent from the
  redirected log). That run includes Alembic upgrade/downgrade tests and
  `test_alembic_single_head`. Offline `alembic heads` prints `e0f1a2b3c4d5 (head)`.
  Evaluation agent 16/16, RAG 5/5, guardrails 7/7. Frontend lint, typecheck,
  1196 unit tests, and build exit 0. Chromium Playwright `CI=true npm run test:e2e`
  exit 0 (30 passed, 13 skipped; the skips are staging specs that require a
  staging demo password). Local `docker build -t alphatrade-backend:ci ./backend`
  exit 0. Rollback `--self-check` exit 0 and `--apply` exit 2. Telegram
  preflight `--expect-disabled` exit 0, verdict `NOT_ARMED`. Watcher health,
  live-market staging, and watcher-rollback self-checks exit 0. GitHub CI run
  35756546952 success on `9535a10dd09b873b66118eb94ad06f3e181734f6`
  (backend, deployment-safety, docker-build, frontend, e2e-smoke, evaluation).
  Draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/131.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-059
- Note: Verdict is READY FOR FINAL ACTIVATION AUDIT. Do not merge, do not
  deploy, and do not edit the live staging environment. `render.yaml` is still
  the step-3 evidence blueprint only. Procedures are in
  `docs/controlled_paper_activation.md`.

### AT-081 — Frontier AT-080 P0/P1 remediation
- Priority: P0 · Status: DONE · Dependencies: AT-080 · Risk: High
  (Telegram and Watcher must stay paper-only and advisory)
- Safety classification: Paper execution; public read-only USD-M evidence;
  no exchange credentials; no exchange mutation; Telegram cannot trade;
  no deploy; no activation; live trading remains impossible
- Goal: Close every Frontier AT-080 P0 and P1 without redesigning canonical
  Candidate authority.
- Branch: `cursor/activation_frontier_remediation`
- Base: `070169cd92f1258be337f6fe5ef6230368768d5a` (PR #131 head)
- Alembic: single head `f1a2b3c4d5e6` (revises `e0f1a2b3c4d5`)
- Validation recorded on this run:
  - `uv run ruff check .` and `uv run ruff format --check .` exit 0 (909 files).
  - `uv run alembic heads` prints `f1a2b3c4d5e6 (head)`.
  - `uv run pytest --tb=line` exit 0: 2629 passed, 237 warnings, 1378.63s.
    That process imported modules at collection, before a later one-line
    change that keeps `last_delivery_at` across idle Telegram cycles.
  - After that change, `uv run pytest tests/test_frontier_remediation.py::test_postgres_runtime_retries_duplicate_delivery_and_survives_restart -q --tb=short` exit 0.
  - Strict mypy on 13 changed modules exit 0. Full-package mypy was not run.
  - Frontend `npm run lint`, `npm run typecheck`, `npm run test` (200 files,
    1196 tests), and `npm run build` exit 0.
  - `CI=true npm run test:e2e` exit 0: 30 passed, 13 skipped. The first
    attempt failed because Playwright's apt install raced with another apt
    and Chromium was missing. The rerun after `npx playwright install chromium --with-deps` (INSTALL:0) is the result above.
  - Evaluation: agent 16/16, RAG 5/5, guardrails 7/7. Redis connection
    refused was logged and tolerated.
  - Deployment self-checks exit 0: watcher rollback, watcher smoke, watcher
    health, controlled rollback `--self-check`, watcher activation
    `--self-check`, Telegram preflight `--expect-disabled` verdict
    `NOT_ARMED`. Controlled rollback `--apply` exit 2.
  - Local `sudo docker build -t alphatrade-backend:ci ./backend` exit 0
    (image `2e0398476c2b`). `COPY src` was not cached.
  - Exact-head GitHub CI run 35799241936 success on
    `00b4e20f6bfc83d835c17cad6db7d7d2f14b0900`: backend, deployment-safety,
    docker-build, e2e-smoke, evaluation, frontend, plus Vercel and Vercel
    Preview Comments. https://github.com/Fejjii/AlphaTrade-AI/actions/runs/35799241936
  - Live Binance aggTrade weight from Frankfurt remains UNKNOWN. Not deployed.
    Not activated.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-060
- Note: Staging inbound is polling. Webhook is not a staging activation path.
  Do not merge, deploy, or activate.

### AT-082 — Close the three open PR132 activation findings
- Priority: P0 · Status: DONE · Dependencies: AT-081 · Risk: High
  (shared market cache and worker health must stay paper-only)
- Safety classification: Paper execution; public read-only USD-M evidence;
  no exchange credentials; no exchange mutation; workers stay disarmed;
  no deploy; no activation; live trading remains impossible
- Goal: Close Binance evidence reuse, Render worker Settings boot, and
  worker heartbeat freshness. No redesign.
- Branch: `cursor/final_three_activation_fixes`
- Base: PR #132 head `d4e4e8daff9e42d2d8c7efb541ab57e52d05092d`
- Behavior commit: `d778a4f2ade94a62a9a3081e8b6d11862ad53f65`
- Alembic: single head `f1a2b3c4d5e6` (unchanged; no new revision)
- Validation recorded on this run, against `d778a4f` unless noted:
  - `uv run pytest tests/test_final_three_activation_fixes.py -q --tb=short`
    exit 0: 12 passed, including PostgreSQL lease takeover and worker
    health freshness.
  - `uv run ruff check .` and `uv run ruff format --check .` exit 0
    (913 files).
  - Strict mypy on the 12 affected modules exit 0 after the idle-lock fix.
  - `uv run alembic heads` prints `f1a2b3c4d5e6 (head)`.
  - `uv run pytest -q --tb=line` exit 0. Progress marks: 2641 passed,
    0 failed, 0 skipped. PostgreSQL was accepting connections at
    `postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test`.
    The short summary line was absent from the redirected log.
  - Deployment-safety pytest
    `tests/test_deployment_safety.py tests/test_deployment_scripts.py tests/test_config.py tests/test_watcher_paper_activation.py -q --tb=line`
    exit 0: 102 passed, 0 failed, 0 skipped.
  - Script self-checks exit 0: post-deploy smoke gate, canonical staging
    smoke, live-market staging validation, watcher rollback, watcher
    activation smoke, watcher paper health, watcher activation module,
    Telegram preflight verdict `NOT_ARMED`, controlled rollback
    `--self-check`. Controlled rollback `--apply` exit 2.
  - Evaluation: agent 16/16, RAG 5/5, guardrails 7/7.
  - Frontend `npm ci`, `npm run lint`, `npm run typecheck`, `npm run test`
    (200 files, 1196 tests), and `npm run build` exit 0.
  - Chromium `CI=true npm run test:e2e` exit 0: 30 passed, 13 skipped.
  - Local `sudo docker build -t alphatrade-backend:ci ./backend` exit 0
    (image `8b8a7bff546e`).
  - Exact-head GitHub CI run 35837052105 success on
    `d778a4f2ade94a62a9a3081e8b6d11862ad53f65`: backend,
    deployment-safety, docker-build, frontend, evaluation, e2e-smoke.
    https://github.com/Fejjii/AlphaTrade-AI/actions/runs/35837052105
  - Not deployed. Not activated. Live Binance aggTrade weight from
    Frankfurt remains UNKNOWN.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-061
- Note: Draft PR https://github.com/Fejjii/AlphaTrade-AI/pull/133 targets
  `cursor/activation_frontier_remediation`. Do not merge, deploy, or activate.

### AT-083 — Disarmed Render workers boot without operational secrets
- Priority: P0 · Status: DONE · Dependencies: AT-082 · Risk: High
  (staging validation must stay fail-closed for the API and for armed workers)
- Safety classification: Paper execution; workers stay disarmed; no exchange
  credentials; no deploy; no activation; live trading remains impossible
- Goal: `alphatrade-watcher-paper-staging` and
  `alphatrade-telegram-paper-staging` construct Settings and idle disarmed
  from the literal `render.yaml` environment. No secret is required only to
  boot disarmed. Armed workers still require PostgreSQL, Redis, JWT, provider
  dependencies, and the Telegram bot token where the arm applies.
- Branch: `cursor/final_render_worker_boot_fix`
- Base: `5ff0eb8c4a7d171118dfde921259a6da13b60abb`
- Alembic: unchanged. No new revision.
- Local validation on this run:
  - `uv run ruff check .` and `uv run ruff format --check .` exit 0
    (915 files).
  - Strict mypy on `disarmed_worker_boot.py`, `deployment_safety.py`,
    `watcher_paper.py`, and `telegram_activation/__main__.py` exit 0.
  - Targeted pytest exit 0: 129 passed, 0 failed. Includes
    `tests/test_disarmed_render_worker_boot.py`, deployment safety, deployment
    scripts, config, Watcher paper activation, Telegram paper activation, and
    the literal blueprint boot test.
  - `tests/test_final_three_activation_fixes.py` plus the frontier blueprint
    and live-market config tests: 12 passed, 2 skipped. The skips are
    PostgreSQL tests; PostgreSQL was not reachable in this environment.
  - Deployment-safety script self-checks exit 0. Telegram preflight verdict
    `NOT_ARMED`. Controlled rollback `--apply` exit 2.
  - Exact-head GitHub CI run 35851723888 success on
    `d6d5492e08621a8c7be13f043eeaa3bb3781eb3e`: backend, deployment-safety,
    docker-build, frontend, evaluation, e2e-smoke. Vercel and Vercel Preview
    Comments also succeeded. 0 failed.
    https://github.com/Fejjii/AlphaTrade-AI/actions/runs/35851723888
  - Not deployed. Not activated. Draft PR
    https://github.com/Fejjii/AlphaTrade-AI/pull/134 targets
    `cursor/final_three_activation_fixes`. Do not merge.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-062
- Note: Do not merge, deploy, or activate.

### AT-084 — Final Render Blueprint cleanup
- Priority: P0 · Status: DONE · Dependencies: AT-083 · Risk: Medium
  (Blueprint association can apply config to the existing API; this change
  does not sync Render)
- Safety classification: Paper execution; Watcher and Telegram stay disarmed;
  no secrets; no live trading; no deploy; no activation
- Goal: Decide from repository evidence whether `alphatrade-worker-staging`
  is required, and leave `render.yaml` as the API plus the two disarmed
  paper workers.
- Result: Not required. Removed from `render.yaml`. ADR AT-ADR-063.
- Branch: `cursor/render_blueprint_final_cleanup`
- Base: `6314a2021441384b9a1bb20f6c0b82916b15f6bd`
- Alembic: unchanged. No application runtime change.
- Local validation on this run:
  - `uv run ruff check` and `uv run ruff format --check` on the two edited
    tests, exit 0.
  - `uv run pytest` exit 0: 122 passed, 0 failed, 0 skipped. Files:
    `tests/test_disarmed_render_worker_boot.py` (17),
    `tests/test_deployment_safety.py` (51),
    `tests/test_deployment_scripts.py` (14),
    `tests/test_config.py` (8),
    `tests/test_watcher_paper_activation.py` (29),
    `tests/test_live_market_activation.py::test_declared_config_files_match_the_activation_contract`,
    `tests/test_frontier_remediation.py::test_partial_deployment_and_rollback_order_keep_live_trading_impossible`,
    `tests/test_final_three_activation_fixes.py::test_render_worker_blueprint_constructs_disarmed_staging_settings`.
  - Not deployed. Not provisioned. Not activated.
- Recommended model: Grok 4.6 Extra High
- ADR: AT-ADR-063
- Note: Do not deploy, provision, or activate.

### AT-085 — Binance USD-M HTTP 418 recovery
- Priority: P0 · Status: IN PROGRESS · Dependencies: AT-065 evidence adapter
  · Risk: Medium (read-only market data; no execution path)
- Safety classification: Paper execution; Watcher and Telegram stay disarmed;
  no secrets; no live trading; no deploy; no activation
- Goal: Treat Binance HTTP 418 as a temporary ban with bounded backoff, and
  let a failed backfill recover later without exposing a price or latching
  the monitor until process restart.
- Branch: `cursor/binance-418-recovery-11ce`
- Base: `main` at `697cc13`
- Alembic: unchanged.
- ADR: AT-ADR-066
- Note: Do not deploy or activate. Exact-head CI is recorded when the PR
  run finishes.

### AT-086 — Telegram paper MVP discussion and activation procedure
- Priority: P0 · Status: DONE · Dependencies: AT-079, AT-084 · Risk: High
  (Telegram must stay advisory)
- Safety classification: Paper execution; Telegram stays disarmed in
  `render.yaml`; no deploy; no activation; live trading remains impossible
- Goal: Durable journal, learning, and Candidate discussion on the combined
  paper worker, numeric bot-id check, and the exact Render variables to
  activate Telegram after Watcher is healthy.
- Branch: `cursor/telegram-paper-mvp-6d2e`
- Alembic: unchanged. No new revision.
- ADR: AT-ADR-067
- Note: Do not deploy or arm staging. Activation steps are in
  `docs/telegram_paper_mvp_activation.md`.

### AT-087 — Automatic internal paper loop after CONFIRMED_SETUP
- Priority: P0 · Status: DONE · Dependencies: AT-076, AT-069 · Risk: High
  (paper execution must stay internal)
- Safety classification: Paper execution; `ENABLE_REAL_TRADING=false`;
  `EXCHANGE_MODE=paper_internal`; Watcher and Telegram stay disarmed; no
  deploy; no activation; no exchange mutation
- Goal: After a genuine Watcher `CONFIRMED_SETUP` Candidate, sequence existing
  ActionEligibility, canonical TradePlan, and internal paper fill into one
  open Journal trade. Duplicate scans and restarts converge. Risk BLOCK, the
  kill switch, WATCH, NO_SETUP, stale evidence, and provider outage create no
  fill.
- Branch: `cursor/automated-paper-loop-e411`
- Alembic: unchanged. No new revision.
- ADR: AT-ADR-069
- Note: Do not deploy, arm Watcher, or activate Telegram.

### AT-088 — Interactive agent foundation
- Priority: P1 · Status: DONE · Dependencies: conversation, journal, strategy,
  and knowledge stores · Risk: Medium (chat must not gain a live-trading path)
- Safety classification: Paper execution; `ENABLE_REAL_TRADING=false`; no
  deploy; no Render change; no Watcher or Telegram activation
- Goal: Backend orchestration for an Agent surface that proposes structured
  actions and reads existing authorities.
- Branch: `cursor/interactive-agent-foundation-4807`
- Alembic: unchanged. No new revision.
- ADR: AT-ADR-074
- Remaining: screenshot and voice implementations, and default Qdrant retrieval.
  The simplified UI is integrated on the release-candidate branch. Voice and
  screenshot stay unimplemented.
- Note: Do not deploy or enable real trading.

### AT-089 — Reviewed paper release candidate
- Priority: P0 · Status: IN_PROGRESS · Dependencies: AT-088 and the enrollment
  and worker-memory branches · Risk: Medium (integration must not widen
  trading authority)
- Safety classification: Paper execution; `ENABLE_REAL_TRADING=false`; no
  deploy; no Render change; no additional symbols; no Telegram arm
- Goal: Integrate PR 146, PR 149, PR 145, and PR 147. Exclude PR 148.
- Branch: `cursor/release-candidate-146-149-145-147-5354`
- Alembic: head `a8c3e1b94d20` must be applied before the paper worker starts.
  Staging is not migrated by this task.
- ADR: AT-ADR-072, AT-ADR-073, AT-ADR-074, AT-ADR-075
- Note: Do not merge to main, deploy, or enable real trading.

### AT-090 — Configurable five-symbol paper Watcher
- Priority: P1 · Status: DONE · Dependencies: PR 150 head
  `33287b6a872beed9846828853930e867d1c892c2` · Risk: Medium (market identity)
- Safety classification: Paper only. Real trading stays disabled. No Render
  deploy, no Telegram arm, no Alembic migration.
- Goal: One Watcher process with five configurable slots. BTC is no longer the
  only valid market identity. Unsupported symbols fail closed.
- Branch: `cursor/watchlist_v1`
- ADR: AT-ADR-076
- Validation: `tests/test_watcher_five_symbol_watchlist.py` (15 passed),
  `tests/test_watcher_paper_runtime.py`, `tests/test_bybit_usdt_perpetual_evidence.py`,
  `tests/test_phase6_fusion_evaluator.py::test_wrong_instrument_fail_closed`,
  `tests/test_phase6_fusion_evaluator.py::test_stale_evidence_fail_closed`,
  frontend `WatcherWatchlistSection.test.tsx` and settings page tests (8 passed).
  mypy on the touched modules reported no issues. `tsc --noEmit` passed.
- Blocker: Bybit `instruments-info` returned HTTP 403, so Bybit listings for
  the non-BTC symbols are not proven. Binance USD-M rows for all five symbols
  were read from `www.binance.com/fapi/v1/exchangeInfo` after `fapi.binance.com`
  returned HTTP 451. Live kline reads remain on `fapi.binance.com`.
- Note: Do not merge automatically. Do not deploy.


## AT-110 — Supervised manual BloFin demo acceptance

Owner request: add explicit manual demo test preview and exact confirmation without
fabricating strategy/Watcher/Candidate authority. Isolated branch from main 8bab505.
Implementation: separately disarmed owner-pinned staging API, BTC market-only plan
origin, existing deterministic 1R/risk/account claim/dispatch fences, native fills
and protection reads, partial remainder cancellation, distinct Journal source and
no strategy learning attribution. Preserve real trading refusal and immutable history.
Validation: focused PostgreSQL, authenticated API, simulated venue, migration and UI
regressions; final result recorded in PR/handoff. Full release CI and live acceptance
remain supervising gates. No deployment, arming or exchange orders in coding task.
Runbook: docs/manual_blofin_demo_acceptance.md. Limits: no limit entry, manual exit,
realized outcome or repeat-entry release; unresolved history remains held.

## AT-111 — BloFin executable demo quote and Chrome voice repair
- Priority: P0 quote / P1 voice; Status: implementation complete; PR review and live acceptance pending; dependency: merged PR226 (`d8ef205`).
- Goal: demo-only side/depth/quantity quote validation in preview and confirmation; actionable safe errors and separate browser capture diagnostics.
- Risk: High (execution preflight); preserve strict <10s venue timestamps, strategy minimum 1R, explicit hash-bound manual connectivity exception, exact plan confirmation, risk/protection/idempotency gates and disabled real trading.
- Validation: focused mocked quote/provider/preview/refusal and voice lifecycle tests; live demo and affected Chrome acceptance separate. No orders, merge, deployment, credentials/account/activation changes or full backend CI dispatch.
- Recommended model: strongest available coding model, high reasoning.

## AT-UI-AGENT — Consolidated workspace and conversational capture
Status: REVIEW_REQUIRED (implementation and stated development checks complete). Accepted PR232 head9d44dec/main merge d175613 integrated; PR233 targets main. Scope: five destinations, compact status/dialogs, private durable capture/retrieval and structured drafts. Validation: frontend1,420; PostgreSQL-backed backend228 passed/no skips, including16 capture contract/concurrency cases; safety108; full Chromium47 passed/13 staging skips/no retries; deterministic evaluations16/16,5/5,7/7. Ruff/format1,157 files pass; same495-error mypy baseline with zero introduced. Local offline-font build passes; ordinary CI run814 passed normal build at0dcd847. Final published-ref CI recorded in PR233. Remaining: product review, live model/native/physical checks, reviewed migration rollout and one supervised complete exact-ref release gate; Mac mirror unverified. See docs/alphatrade_workspace_redesign.md. No PR233 merge/deploy or runtime/enforcement changes.


## AT-REVIEWER-WAVE-1 — Frontend reliability and API contracts

Status: REVIEW_REQUIRED (independent implementation complete; dependent schemas blocked).
Owner: Agent 1, `codex/reviewer-wave-frontend`, baseline PR233/main
`b165b92276346f0e0fe3ccdbd2bec3443dc75d40`. Scope, shared requests, schema hashes and
completed/pending/deferred/blocked ledger: `docs/reviewer_wave/agent1_contract.md`.
No Python Agent/RAG/provider, migration, dedicated manual-order widget or CI ownership changes.
Development checks only; consolidated release and authenticated live acceptance remain supervised.

## AT-118 — Account-scoped BloFin native activity mirroring

Status: READY (PR239 review corrections and focused verification complete; consolidated update). Risk: Medium (account identity and exact history data).
Base: refreshed PR237 bda597c2fffbf1a49beadc64d757100808094ca2.
Own additive activity provider/schema/repository/service/API/worker and migration.
Focused deterministic and isolated PostgreSQL verification only.
Continue from 85adbde2b242340cac29b45b8673eb8296385903: verified UID/execution-account
linkage, snapshot selection, long-lived order reconciliation, existing worker wiring
with disabled defaults and current migration-guard assertions.
No execution authority, frontend, Agent turn, CI, deployment or runtime activation changes.
See docs/blofin_native_activity.md for contract, milestones and integration.

## AT-119 — Generic Exploration and Validation experiment domain

Status: INTEGRATED in next-batch PR244 at reviewed PR2416ca6a762; not deployed.
The frozen PR237 candidate remains separate. Current next-batch migration head is
a12trendpulsescreen001, preserving immutable a11 and its ancestry. Domain approval
and lifecycle remain distinct from execution; current milestone evidence and
accepted/total criteria are in docs/experiments_market_integration.md.
Integration correction from bed8994: a11 roundtrip expectation and preserved ancestry,
shared OpenAPI/hash regeneration, existing disposable CI PostgreSQL URL. Local
affected selection 75 passed/no skips; CI policy 28 passed; frontend generated
contracts 7 passed; drift/lint/typing pass. Exact final SHA and automatic focused
CI evidence in PR241 and docs/experiment_domain_verification.md.
Future feature branch based on PR237 619c15f; excluded from
the current release candidate. Own backend, one additive a11 migration and API
contract; publish the interface early. Preserve Nested/SFP and document the future
deterministic TrendPulse1R adapter. Finite approved envelopes, immutable running
configuration, tenant/verified account/source fences and independent promotion
samples. Focused disposable-fixture verification only. No runtime activation,
replacement engine, external orders, credential changes, deployment or full CI.
Contract: docs/experiment_domain_contract.md.

## AT-MARKET-EVIDENCE — Urgent public market evidence foundation

Status: READY (implementation and focused development verification; draft integration review).
Baseline: PR237 7fc21db00a0527ee018f3fd1c478da64bcc713c0, refreshed through
619c15fe63ffc23288c2866781e08f9cb44a2ab9 (migration test/docs correction only).
Branch: codex/market-evidence-foundation. Own provider adapters, normalization,
coverage and evidence contracts; reuse canonical OI and executed-flow/CVD.
Add resting depth snapshots, causal process caches and actual Agent market consumer.
Required historical book coverage remains unavailable and blocks qualification.
Schema coordination completed by the accountable next-batch owner: reviewed PR242
88ce82ff is integrated in PR244 with shared generation. PR237 remains frozen.
Native exchange API/WS acceptance unverified under current egress policy.
No full backend CI, deployment, paid activation, orders or operator-setting changes.
Contract/results/next consumer step: docs/market_evidence_foundation.md.
PR242 continuation: corrected required OI/funding acquisition timing with a fixed
event/candle cutoff and independent final evaluation clock; actual receipt remains
in collected_at and canonical receive_time. Stable v1 context schema and read-only
experiment integration example: docs/market_evidence_context_contract.md.
Four delayed OI/funding cases fail on d4b98087 and pass after correction;
30 new assembler/provider timing regressions and 6 contract/example cases pass.
Affected development selection: 433 passed, 11 disposable-PostgreSQL-gated skips;
13 scoped strict source type checks and 15 frontend API tests pass. No full CI.
## AT-120 — Pure deterministic TrendPulse1R research adapter

Status: READY (separate draft feature branch codex/trendpulse-1r-adapter).
Focused combined selection: 261 passed/no skips; 80 new adapter/contract/domain
cases; scoped Ruff/format/mypy(6 source files), shared api:check and schema guard
pass. Exact final SHA/automatic focused evidence in draft PR and
docs/trendpulse_1r_verification.md.
Dependency: PR241 correction 6ca6a76201547524275266dfa22fdf12f5932b14; automatic
focused CI run38066879849 passed (472 backend/no skips,143 frontend). Implement
closed causal 15m trend/5m entry, fixed conservative authored v1 rules, structural
stop and rounded gross 1R; pure experiment-config/v1 research attribution. Preserve
Nested/SFP. Focused synthetic/disposable PostgreSQL tests and schema/lint/typing.
No replacement engine, execution/performance runtime, migration, operator setting
changes, full backend CI or activation. Contract: docs/trendpulse_1r_adapter_contract.md.

## AT-121 — Receipt correction then bounded TrendPulse research screening

Status: INTEGRATED in next-batch PR244 at reviewed PR24662a20bb; not deployed.
The real Strategies consumer now shows persisted screening/rejection history;
Agent/document confirmation seeds a bounded draft. HTTP native sample admission
stays closed, models stay disabled, and unavailable performance remains explicit.
Current focused evidence/acceptance and hosted blockers are in the existing
docs/experiments_market_integration.md checkpoint. Historical builder evidence:
Correction 8b9d1b8:140 local passes,
automatic focused CI305 backend/no skips and12 frontend. Screening:36 new and329
combined focused passes/no skips, shared drift, Ruff/format/strict typing and
frontend generated-contract/lint/typecheck pass. Untuned four-regime synthetic
replay192 decisions/zero signals; genuine original-arrival market replay remains
unverified. Migration compatibility follow-up33 passes/no skips, all legacy
current-head guards updated with history/merge assertions preserved. One reserved
a12 aftera11; sequencing is adopted in PR244 with immutable ancestry and retained
schema rollback through the actual a12 head. Exact continuation SHA/automatic
focused CI in draft PR and docs/trendpulse_screening_verification.md.
Continue PR243 bab0e9ce; publish the receipt eligibility
correction first. Test positive delayed arrivals at all three 5m positions in a
15m interval, both directions, missing/future evidence and expiration. Then a
separate future feature branch owns bounded acquisition/detector invocation,
durable restart-safe dedupe/rejections and an authenticated application read
consumer. Coordinate one additive migration with integration. Report honest
representative replay coverage/counts without threshold tuning. No trade activation,
orders, deployment, operator/credential changes or full CI.

## AT-122 — Continue trading workflow from reviewed PR249 without release mutation

Status: REVIEW_REQUIRED (isolated feature PR; provider/native acceptance blocked).
Base: df0a12d3; feature branch codex/trading-workflow-completion. Five requested
workstreams and exact remaining provider/runtime acceptance are recorded in
docs/trading_workflow_continuation.md. Reuse canonical strategy/Candidate/plan/
approval/claim authorities, bounded research selection and existing reconciliation.
Preserve all operator settings, distinct account sources/samples and release owner.
Focused simulated transport/disposable PostgreSQL evidence only; no full backend
CI dispatch, deployment, migration, provider activation, native financial action
or unsupported monetary values. Native end-to-end acceptance remains explicitly
blocked on source contracts/access; public CONNECT403 is not a provider response.

PR251 correction: original focused run 38249795179 at 03be2d3 failed case 840
(Telegram log redaction) before cancellation. A real two-case reproduction isolates
Alembic CLI logging pollution from the activity migration fixture; repair and exact
collection evidence are in docs/pr251_focused_ci_repair.md. Shared CI/duration
changes belong to the PR249 release owner; automated research grants no trades.

## AT-GPT-LIVE — disabled interactive Live integration

Separate branch from reviewed PR249 de5c010c. Reuse Agent, voice, conversations,
quotas and recovery; verify official provider interface. No workflows/full suite,
deployment, paid activation or trading. Fixed six-item implementation checklist
and exact microphone acceptance: `docs/gpt_live_integration.md`. REVIEW_REQUIRED
following focused mocked checks/publication; activation and live acceptance remain separate.

AT-GPT-LIVE PostgreSQL continuation: four verification items accepted 4/4. Seven
formerly skipped quota cases plus eleven missing LiveLedger cases passed on
disposable PostgreSQL 16.15 (18 passed/zero skips); 13 affected mocked checks passed.
Corrected final receipt identity/reason replay and atomic close/hold classification.
Exact receipts: docs/evidence/pr257-postgres/. Reused unaffected mocked evidence.
Disposable schemas/container removed; activation and microphone acceptance remain separate.
