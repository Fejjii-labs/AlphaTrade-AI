# AlphaTrade AI: Cursor handover and delivery roadmap

Snapshot refreshed: 13 October 2026, approximately 10:10 Europe/Berlin.
Owner: Sofien Fejji.
Purpose: continue the existing product in Cursor without restarting audits, losing Codex work, or repeating broad tests.
This is a verified snapshot plus an explicitly proposed continuation plan. Refresh running operations once when resuming.

## 1. Executive checkpoint

Main and both Render staging services remain on PR233, commit b165b92276346f0e0fe3ccdbd2bec3443dc75d40. Newer features are not deployed on those services.

PR249 is the release candidate at 43e66531c0f4ed876387ba3b6bbe104ef75a58f6. Its application, frontend, migrations and dependency source remain unchanged from df0a12d3; subsequent corrections concern tests and CI diagnostics. PR250 presentation work is included.

PR251 is a separate workflow feature draft at 9928ce9e0b89213e31167cf9526d1273336bda0e. It has substantial focused local evidence but is neither integrated into the current release candidate nor deployed.

New PR252, cursor/audit-test-ci-cleanup-5016 at65a81739c847d8659c95fa79f593bdb99e91a226, already removes broad backend filename prefix selection. Its reported local selector tests pass31/31. The current branch CI38314136225 is cancelled, with frontend and safety successful, backend cancelled and expensive jobs skipped. This is implemented but not integrated or release accepted. Review this existing correction rather than implementing it again.

Latest combined release run: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38294470908
Final result: cancelled. Frontend, Docker and deployment safety passed; backend was cancelled at 21:03:10 UTC on 12 October. Evaluation and browser smoke were skipped. Artifact inventory contains only the frontend build, with no retained backend diagnostics. The full workflow has six required jobs. Three passed jobs mean 3/6 release jobs accepted, or 50% of this checklist, not 50% of the product.

No defensible aggregate product percentage exists. Use the scoped counts below and separate code completion from runtime acceptance.

## 2. Exact source map

Repository: https://github.com/Fejjii/AlphaTrade-AI

1. Production baseline: main, b165b92276346f0e0fe3ccdbd2bec3443dc75d40.
2. Release owner: PR249, codex/interactive-voice-monitoring, 43e66531c0f4ed876387ba3b6bbe104ef75a58f6.
3. Workflow feature: PR251, codex/trading-workflow-completion, 9928ce9e0b89213e31167cf9526d1273336bda0e.
4. Canonical operational checkpoint: cursor/experiments-market-handoff, HANDOFF.md. The release owner's last checkpoint was 20:59:25 Berlin.
5. Selector correction: PR252, cursor/audit-test-ci-cleanup-5016, 65a81739c847d8659c95fa79f593bdb99e91a226.
6. This migration document: docs/CURSOR_MIGRATION_20261012.md on the handoff branch.

The handoff branch is a documentation channel, not an application integration base.

Open PR inventory relevant to the current stack:

1. PR234: frontend reliability and API contracts, targets main.
2. PR235: order diagnostics and knowledge consistency, targets main.
3. PR236: Agent runtime and provider reliability, targets main.
4. PR237: reviewer wave integration, targets main.
5. PR241: generic Exploration and Validation domain.
6. PR242: public OI, flow/CVD and book evidence.
7. PR243: deterministic TrendPulse1R research adapter.
8. PR244: authored experiments, durable research and compact Strategies.
9. PR245: trusted BloFin source and reconciled outcomes.
10. PR246: bounded TrendPulse screening.
11. PR247: Agent summaries and Knowledge navigation.
12. PR248: Agent navigation and guarded native experiment integration.
13. PR249: interactive voice, monitoring, presentation and release corrections.
14. PR251: bounded workflow completion and evidence preservation.
15. PR252: narrow focused test selection, based on PR249.

Do not merge this list blindly in numeric order. Several branches are stacked or have overlapping integration ancestry. Inspect merge bases and contained commits before selecting the final integration path. Do not close or delete historical PRs until their work is proved incorporated. Older open PRs also exist and were not audited for cleanup. No branches or PRs were deleted or closed.

## 3. Capability progress and evidence limits

These are earlier named local acceptance checklists retained by the release handoff, not freshly rerun tests or percentages of the entire vision.

1. UI presentation slice: 6/6, 100% locally accepted and included in PR249. Final MindPillar inspired design and hosted usability are not accepted.
2. Monitoring assignments: 5/5, 100% locally accepted. Actual notification coverage and provider behavior still need runtime evidence.
3. Interactive Realtime voice: 4/5, 80% locally accepted. Real microphone/provider experience, interruption quality and latency remain open.
4. Agent quality: prior checkpoint 3/4, 75% local criteria. Actual provider evaluation remained 0/16 at the prior checkpoint; this session did not rerun it.
5. Native BloFin integration: prior checkpoint 4/5, 80% local criteria. Protected execution ancestry and trustworthy monetary attribution remain incomplete. This is not 80% live autonomous trading.
6. PR251 implements five reviewable workstreams. Its three actual acceptance outcomes, native trading, notification diagnosis and market acquisition, remain 0/3.
7. D Line strategy: not implemented; chart examples and formal rules remain required.
8. Premium news, economic events, fear and greed, liquidation and heatmap context: deferred.

Do not sum overlapping test counts. Record implemented, integrated, deployed and live accepted as separate states.

## 4. Current blockers and next executable action

### Release backend acceptance

Run38271867111 on af2d8f6 exceeded the unchanged two hour job limit and concluded cancelled. Backend logs were unavailable and no diagnostic artifact survived. The exact timeout location is unknown.

A prior assertion failure was isolated to test setup. Another ordering issue involved Alembic logging configuration disabling an existing HTTPX logger, causing the redaction assertion to see an empty handler. PR249 and PR251 contain related but differently scoped test corrections; review their interaction when integrating.

Current43e6653 preserves the 120 minute job limit but uses a 105 minute step and 6000 second supervisor budget to leave cleanup time. This improves evidence retention; it does not prove the suite duration or blocking cause is fixed.

Next: reconcile the cancelled run with its backend job and available logs. The artifact inventory has no backend diagnostics, so first determine why the supervisor and upload did not leave evidence. Recover the last active case if available; otherwise isolate a bounded diagnostic window. Do not guess the timeout cause or restart the entire suite as diagnosis.

### Focused test selection correction is awaiting integration

The selector can map a changed agent.py to all test_agent*.py files. PR251's selection expanded to 75 entries and 1440 ordered cases. Each push can evaluate the cumulative PR diff.

PR252 now uses an exact module test filename, retains existing explicit boundary groups and adds exact safety dependencies. Source inspection confirms the prefix glob is removed. Review coverage for the changed cross module paths before integration; exact filenames are not complete dependency analysis. Reuse its31 local passing selector checks when inputs match. Do not duplicate this implementation. Its cancelled CI is not acceptance.

### Provider and account acceptance

PR251's Binance and Bybit probes failed at proxy CONNECT403 before reaching the provider. Its BloFin check lacked task bindings. This does not prove provider outage or missing credentials in every environment.

Next: use one approved executor with verified egress and scoped bindings. Run one bounded read only acquisition and reconciliation check. Preserve unavailable reasons rather than inventing market context or retrying unchanged restrictions.

### Private continuation package

The release owner reports a prepared encrypted private continuation package with restore, identity and operational receipts. Transfer outside the old executor remains unconfirmed.

Next: preserve it through approved private storage before deleting that executor. Do not upload it, private download URLs or credentials into the public repository. Public code work can continue independently; do not redo a successful restore or image qualification merely because its detailed receipt is private.

### Frontend deployment visibility

Render revisions were verified again on 13 October. Vercel inspection returned403 for team scope alphatrade-ai. No Vercel CLI is installed in this review workspace, so there was no authenticated CLI fallback. Current frontend revision and API binding remain unknown; this access denial is not evidence of an application outage.

Next: verify the actual frontend deployment revision and API target through authorized project access. Do not assume a preview or successful build is the production version.

## 5. Product vision to preserve

AlphaTrade is an Agent centered trading workspace. The Agent is the main interaction point for text, files and interactive voice. It should reason with current evidence, challenge unsupported ideas, explain tradeoffs and organize discussions automatically.

BloFin is authoritative for account balances, replenishment, orders, fills and actual positions. Trades placed manually on BloFin must appear in AlphaTrade. Approved autonomous exchange execution should flow through the existing controlled execution path. Do not mix internal demonstrations into BloFin performance or silently substitute simulation when native evidence is missing.

Journal and Knowledge should form a compact experience with clear categories: reflection/journaling, news and analysis, strategies, rules, and lessons. The Agent classifies discussion and stores concise summaries with traceable source records. Classification must be correctable.

Dashboard owns statistics and account performance. Strategies owns strategy configuration and risk. Settings focuses on market monitoring, Telegram notifications, account and system. Remove coaching redundancy and duplicated strategy settings.

The interface should use the full page width, concise cards, expandable details, internal back navigation and a compact top panel. Avoid nested scrolling. Hide IDs, raw source URLs, implementation explanations and lengthy histories from primary views while retaining provenance in details. Keep recent history compact and open the full history on demand.

The Agent page should prioritize conversation, send, import and Talk controls. Remove redundant microphone status prose and internal approval labels from the primary experience without removing actual execution protections.

Per coin and timeframe, allow selection of an approved strategy version, configuration and activation/deactivation. Preserve those exact assignments during worker runs. Support multiple timeframes rather than forcing every strategy onto five minutes.

Existing strategy families include Nested 1 to 2 continuation and SFP. TrendPulse1R is the current bounded new research path. D Line needs the user's pictures and explicit rules before implementation; ask only when that batch starts.

## 6. Trading modes and strategy learning

Separate two independent dimensions:

1. Experiment mode: Exploration versus Validation.
2. Execution venue/source: internal simulation versus verified BloFin demo/native evidence.

Exploration samples declared strategy variants under bounded risk and attempt limits. Validation freezes a variant and uses a fresh sample group. Neither mode permits unlimited retries, unconstrained trading, fabricated wins or retrospective changes to an accepted sample.

The original goal is to gather useful trials quickly, compare performance and refine entries, stops and targets. Win rate alone is insufficient: preserve reward/risk, fees, funding, slippage, drawdown, sample size and strategy version.

Nested continuation needs repeated structures, volume and order flow context, and differentiation between healthy continuation and an exhausted blow off move. SFP needs a sweep and reclaim/rejection around a meaningful level with structural invalidation. Diagnose missing notifications through scan, acquisition, detection, rejection and Telegram delivery receipts before lowering thresholds.

A reasoning model may propose a setup or refinement. Deterministic code must validate sizing, stop geometry, exposure, permissions, freshness and execution identity. Model output is not an execution authority.

PR251 specifically schedules research and creates reviewed Candidates. execution_requested remains false and samples_admitted remains zero for research ticks. Autonomous native entry is not finished.

## 7. Agent and voice direction

The current branch implements OpenAI Realtime conversational voice plumbing, canonical history, interruption/recovery and a backend Agent turn path. It is distinct from earlier recording/transcription functionality.

GPT Live was discussed as a possible evolution; it is not established as implemented in this checkpoint. Do not claim the app embeds the complete ChatGPT consumer voice experience. Voice transport and the AlphaTrade reasoning, retrieval and tools are separate capabilities that must be connected and measured.

Immediate choice: accept the current Realtime path against real microphone behavior first, or deliberately replace its transport under one bounded architecture decision. Do not maintain two competing Talk experiences.

Use economical routing for classification and simple retrieval. Escalate strategy reasoning only when justified. Repository examples currently contain several model defaults, including AGENT_REASONING_MODEL=gpt-6-astra and separate routing tiers. These are configuration examples, not proof of available API model names, prices or runtime selection. Verify provider availability and routing policy before changing models. Do not infer API availability from Cursor model picker labels.

The earlier approximately $42 test budget was a planning target, not a verified monthly bill. Configure spending ceilings and measure actual text/audio usage before projecting costs.

## 8. Reviewer improvement ledger

The following implementation evidence was checked in the published PR records and current dependency manifests. Earlier PR bodies are historical checkpoints; use the integrated release branch for current behavior. This session did not rerun their tests.

Delivered reviewer work:

1. PR234: immediate acknowledged reply rendering; tenant/user scoped TanStack Query caches; filter offset reset and empty page recovery; actual usage redirects; generated OpenAPI types/client/AJV validation; setup_type HTTP save and reload with tenant isolation.
2. PR236: official OpenAI SDK adapter; shared turn policy, retrieval and usage accounting; short database phases with blocking provider work moved to worker threads; durable same key recovery and explicit concurrency conflicts.
3. PR235: transactional indexing intent with normalized SQL content; bounded indexing worker, fencing, generations, retry/delete and truthful pending/ready/failed receipts; scoped shared/private retrieval. Retain Qdrant for now. Production pgvector cutover is deferred.
4. PR237: integrated typed/spoken Agent recovery, native BloFin activity and Dashboard/Journal presentation. Its historical migration head is a10; PR252 identifies the current source head as a13voicemonitor001. Confirm the integrated candidate head before rollout and never treat the historical a10 package as automatically sufficient for a13.
5. Current frontend dependencies include TanStack Query, AJV and openapi-typescript. Current backend pins the official OpenAI SDK. Broad TypeScript rewrite and React Compiler remain deferred.

The manual order incident remains undiagnosed. No actual authenticated failing order receipt was captured; successful mocked widget tests do not establish that the live issue was fixed. Next investigation needs a sanitized request ID, error and account context, without placing a new order merely to inspect it.

Remaining acceptance and architectural constraints:

1. One API contract source: preserve generated FastAPI/OpenAPI TypeScript contracts while Python remains. Ensure setup_type survives save and reload. Validate external payloads at runtime; a TypeScript assertion is not validation.
2. Request state: consolidate duplicated loaders using the chosen maintained query solution, deliberate cache keys, cancellation, freshness and retries.
3. Pagination: reset offset for all result changing filters, including date presets. Keep a route back to page one when a page is empty.
4. Agent response: render the returned reply immediately and reconcile history in the background. Keep blocking model work outside async request loops and shorten database transactions.
5. Provider SDK: use the official SDK behind a thin policy/accounting adapter. Authentication and rate limit failures must not count as healthy embeddings.
6. Agent pipeline: converge duplicated turn routes, limits, usage recording and retrieval. Use the existing retrieval service with ownership filtering and named document support.
7. Routes: remove redundant redirects and obsolete wrappers; test the actual user URL.
8. Knowledge storage: PostgreSQL owns documents/chunks. Reconcile nontransactional vector ingestion. Moving embeddings into PostgreSQL is a candidate simplification, not a completed migration; assess existing retrieval and migration requirements before deciding.
9. TypeScript: migrate bounded features only if justified after behavior and calculation compatibility are proved. Preserve decimal strings, rounding and content hashes. Do not start a wholesale rewrite during release repair.
10. React Compiler: optional incremental optimization after behavior and configuration compatibility are established; not a release blocker.
11. Loading experience: reduce avoidable waiting and use reliable optimistic rendering without hiding pending state or failed financial operations.

On takeover, inspect existing tests and implementation only for the next scheduled item. Mark each as accepted with exact evidence or pending; do not restart a repository wide audit.

## 9. Cursor environment and secret handover

Use a fresh checkout or clean isolated worktree. The documented Mac path is ~/Developer/AlphaTrade-AI; verify the actual workspace. Do not place active Git metadata in a syncing folder. Existing iCloud handoff automation is optional convenience and was not verified here.

Read AGENTS.md, relevant .cursor/rules, .ai/MASTER_WORKFLOW.md, .ai/RELEASE.md and the operational handoff. Rules already exist for Git safety, architecture, trading, security, testing, observability, documentation and handoff. Avoid creating a second competing playbook.

Cursor does not inherit Codex's credentials or private files automatically. Repository access, terminal access and provider access are separate. Use Cursor's approved secret mechanism for cloud execution and ignored local configuration for local development. Never paste values into prompts or commit them.

Verified configuration inventory from repository examples:

1. Backend infrastructure: DATABASE_URL, REDIS_URL, QDRANT_URL, QDRANT_API_KEY where required, JWT_SECRET, CORS_ORIGINS.
2. OpenAI: OPENAI_API_KEY, OPENAI_BASE_URL, PROVIDER_MODE, LLM_MODEL, AGENT_REASONING_MODEL, AGENT_REASONING_EFFORT, AGENT_MAX_OUTPUT_TOKENS, LLM_TIER_A_MODEL, LLM_TIER_B_MODEL, EMBEDDINGS_MODEL.
3. Realtime: AGENT_REALTIME_ENABLED and AGENT_REALTIME_LIMITS_FILE are confirmed in core Settings. The loader requires a bound key, live provider route, verified nonexpired limits receipt, matching reasoning model and sufficient session budget. Preserve the private receipt; do not invent one to bypass the check.
4. BloFin: dedicated reads use BLOFIN_READONLY_SYNC_ENABLED, BLOFIN_READONLY_API_KEY, BLOFIN_READONLY_API_SECRET and BLOFIN_READONLY_API_PASSPHRASE. The separate execution/demo path uses BLOFIN_API_KEY, BLOFIN_API_SECRET, BLOFIN_API_PASSPHRASE, BLOFIN_DEMO_REST_BASE_URL and BLOFIN_DEMO_WS_URL as applicable. Reuse dedicated scoped account bindings and confirm exact demo versus production identity.
5. Telegram: TELEGRAM_BOT_TOKEN, existing enrollment, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_BOT_ID and TELEGRAM_CHAT_ID as applicable. Do not rotate a working bot merely for migration.
6. Worker: WATCHER_ORCHESTRATION_ENABLED, MARKET_WATCHER_ENABLED, MARKET_WATCHER_BRIDGE_ENABLED and existing Telegram/activation flags. Preserve actual operator settings; blueprint false defaults are not instructions to reset a running installation.
7. Frontend: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_APP_NAME, NEXT_PUBLIC_EXECUTION_MODE, NEXT_PUBLIC_PROVIDER_MODE. No secret may use the NEXT_PUBLIC prefix.
8. Knowledge indexing: KNOWLEDGE_INDEXING_ENABLED controls the existing worker component. Leaving it disabled keeps new ingestion pending; activation follows compatible schema/code deployment and provider qualification.
9. Test infrastructure: use disposable PostgreSQL and the exact test URL variables declared by CI, including BLOFIN_ACTIVITY_TEST_POSTGRES_URL. Never point migrations or destructive fixtures at staging.

Infrastructure targets:

1. Render API: alphatrade-api-staging, srv-d8fvbcd7vvec739mc060.
2. Render worker: alphatrade-paper-worker-staging, srv-daqej7o473hc73fsc1k0.
3. API URL: https://alphatrade-api-staging.onrender.com
4. Frontend URL: https://alpha-trade-ai-eight.vercel.app
5. Render workspace: tea-d7hn0fvavr4c73f62c70.
6. Vercel project: prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ.
7. Vercel team: team_LNgcEGBkqntUnTrktNjFzh7a.

For deployment operations, Cursor also needs an approved GitHub session or scoped token, Render access to the named workspace/services, and Vercel access to the exact team/project. These are control plane credentials, distinct from application runtime keys. Use the native integration or CLI credential mechanism; do not assume an arbitrary environment variable is consumed. GitHub Actions secrets do not automatically populate a Cursor terminal, and a platform that can deploy may still be unable to export secret values.

The actual PostgreSQL, Redis and Qdrant provider/account endpoints were not inventoried here. Their exact bindings must come from approved existing configuration or the private continuation package, never guessed. No secret values were read or included in this document.

These identifiers are resource references, not credentials. Verify scoped access once. An inventory permission denial does not prove the underlying service secret is absent. Local infrastructure can support coding while hosted access is blocked.

## 10. Delivery playbook

1. One accountable release owner. At most two independent workers when scopes truly do not overlap.
2. Each worker receives a base revision, allowed files, interface, acceptance criteria and prohibited shared changes. The owner retrieves commits directly.
3. Only one writer changes CI, migrations, deployment or runtime settings at a time.
4. Fix a short named acceptance checklist before coding. Maintain a stable denominator and explain scope changes.
5. Make routine compatible technical choices autonomously. Ask only for genuinely missing product inputs or new authority.
6. Read the smallest relevant code path. Reuse existing services and generated contracts. Avoid speculative abstractions and wholesale rewrites.
7. Validate one meaningful behavior with focused local tests. Include adjacent ownership, persistence or financial risks when affected.
8. Stop on the first failure and retain diagnostics. Reuse unchanged evidence. Broaden only to resolve a concrete risk.
9. Batch related changes and pushes. Documentation updates do not justify full backend CI.
10. Use one final reviewed release gate. Failed gates require diagnosis, not blind redispatch. Preserve exact revision validity; an old green run does not certify changed code.
11. Timebox diagnosis to approximately 20 minutes before producing a checkpoint and changing approach. Do not spend that period polling a running job.
12. Report accomplished work, named acceptance percentages, blockers and next executable steps. Distinguish local fixtures from real provider evidence.
13. Keep public checkpoints sanitized. Preserve private packages privately. No cleanup or deletion is included in this handover.
14. Preserve watcher, Telegram, kill switch and account settings. Existing deployment authority does not grant new exchange actions.
15. Reconcile interrupted external operations before retrying. A timeout does not prove nothing happened.

## 11. Ordered roadmap from this checkpoint

### Batch A: release bottleneck and migration continuity

Owner: one Cursor release agent, reasoning High for diagnosis; cheaper model for mechanical documentation if available.
Acceptance: cancelled CI result reconciled; missing diagnostics explained or replaced by a bounded reproduction; smallest repair verified; PR252 selector correction reviewed; private continuity package preserved or explicitly recorded as missing; no duplicate release run.

First action is to resume from cancelled run38294470908 and the existing PR252 correction. Do not ask a new agent to rebuild that correction.

### Batch B: integrate and publish the reviewed baseline

Owner: same release agent.
Reconcile stacked ancestry and PR251/PR252 changes without losing either logger correction. Choose whether PR251 joins the candidate before paying for another final gate. Do not repeatedly certify intermediate candidates without a clear release decision.

Acceptance: exact integrated revision, valid required six job evidence, compatible migration plan, coordinated authorized staging rollout, actual deployed revisions and one short hosted smoke. Reuse valid build and restore receipts. Do not claim historical results certify changed source.

### Batch C: real user paths, two independent scopes

Worker one: interactive voice and Agent reasoning acceptance. Exercise microphone, response, interruption, tool grounding, reconnect and spend accounting. Record actual latency rather than asserting performance.

Worker two: BloFin reflection and market/notification evidence. Confirm account identity, manual order/fill reflection, protected ancestry and monetary semantics through approved read only evidence. Diagnose Nested/SFP missing alerts. Verify OI, CVD and book acquisition in an executor with supported egress.

Keep shared settings and deployment with the owner. Blocked credentials stop only the affected checks.

### Batch D: complete strategy execution and modes

Finish native execution composition, atomic account reservations/loss counters and trusted closed trade admission. Preserve separation of Exploration/Validation and venue source. Apply to strategies through existing authorities; no fabricated generic executor.

Only an explicitly authorized demo execution acceptance may place orders. Test a bounded demo lifecycle and reconciliation, not unrestricted trading. Ask for D Line examples when implementing that strategy, not during unrelated release work.

### Batch E: UI refinement and Agent organization

Use the deployed baseline to refine the compact Agent landing page, merged Journal/Knowledge navigation, concise Strategies and scoped Settings. Validate desktop/mobile routes with a few representative tasks. Preserve current provider evidence and user settings.

### Batch F: deferred expansion

Add news, economic events, fear and greed, heatmap and liquidation sources for Agent reasoning only. No extra dashboard widgets by default. Evaluate pricing, licensing, freshness and fallback before buying subscriptions. Consider bounded TypeScript and vector storage simplifications only after core workflows are reliable.

## 12. Ready to paste Cursor owner prompt

Continue AlphaTrade from the verified migration document and current repository evidence. You are the accountable delivery owner. Use High reasoning for release diagnosis and integration. Read AGENTS.md, applicable Cursor rules, the operational HANDOFF on cursor/experiments-market-handoff, and docs/CURSOR_MIGRATION_20261012.md.

Start a new Cursor owner chat on release PR249 at43e66531c0f4ed876387ba3b6bbe104ef75a58f6, refreshing its current head once. Preserve PR251 at9928ce9e0b89213e31167cf9526d1273336bda0e as a separate feature until reviewed integration. Do not start from main alone and miss the unmerged work.

Reconcile cancelled run38294470908 first. Its evaluation/browser jobs were skipped and no backend diagnostic artifact is listed. Do not duplicate it or poll continuously. Recover logs if available and reduce to the first failed or active case; do not invent a timeout cause. Use focused checks only during repair. Review PR252's already implemented selector correction at65a81739c847d8659c95fa79f593bdb99e91a226, retaining explicit dependency coverage. Do not rebuild it.

Preserve existing working settings, generated contracts, decimal calculations, strategy versions, ownership controls and private evidence. Check environment bindings once without printing values. Missing access blocks only its affected operation. Reuse successful restore and image qualification evidence when inputs match.

Complete Batch A, then make a concrete integration and release decision for Batch B using standing authorization and actual required gates. Do not automatically dispatch a new full suite after each fix. No exchange order, balance mutation, secret rotation, paid activation or service cleanup is authorized by this migration prompt.

Use up to two independent workers only after assigning disjoint scopes. Do not delegate shared CI, migrations or deployment to multiple writers. Do not ask the user routine implementation questions.

At a milestone or blocker, update the existing handoff and session checkpoint with exact SHAs, focused commands/results, passed and pending acceptance counts, deployed revisions and next action. Report briefly. Stop repeating unchanged tests and do not invent a global progress percentage.

## 13. Evidence index and maintenance

Primary release checkpoint: https://github.com/Fejjii/AlphaTrade-AI/blob/cursor/experiments-market-handoff/HANDOFF.md
Release PR: https://github.com/Fejjii/AlphaTrade-AI/pull/249
Workflow PR: https://github.com/Fejjii/AlphaTrade-AI/pull/251
Latest cancelled release run: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38294470908
Cancelled previous release run: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38271867111
Cancelled workflow CI: https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38261419942

Read on the workflow branch:
docs/trading_workflow_continuation.md
docs/pr251_focused_ci_repair.md
docs/blofin_workflow_remaining_evidence.md
docs/workflow_notification_market_evidence.md

This handover corrected PR251's stale CI paragraph, updated the existing operational checkpoints and published this migration document on the existing handoff branch. It did not run tests, dispatch CI, merge product code, deploy, change secrets, reset settings or perform cleanup. Status is a time bounded observation; Cursor must refresh changed heads or operations once on takeover.


## 14. Practical Cursor startup and first session

Use one new Cursor owner chat for release recovery and integration. Select GPT 6.1 Sol with High reasoning if it is available in your Cursor workspace; otherwise use an available strong coding model. Model availability and cost were not verified here. Use the existing PR252 chat only for a specific correction to that PR, not as a second release owner.

1. Open or clone Fejjii/AlphaTrade-AI in Cursor. Inspect uncommitted work before switching branches.
2. Fetch remote branches. Retrieve this document from cursor/experiments-market-handoff even though the application checkout is on the release branch.
3. Create an isolated continuation branch from the current PR249 head. Keep PR251 and PR252 as review inputs until integrated.
4. Read the existing Cursor rules and run the prompt in section12.
5. Restore only the private bindings needed for the selected batch. Confirm access by a bounded read without printing credentials.
6. Use Python3.12, the committed uv lock and the CI Node version. Backend declares Python>=3.12. Install locked backend development dependencies and frontend package lock dependencies; do not upgrade libraries as part of takeover.
7. Provision disposable local test PostgreSQL rather than attaching tests to staging. Match CI PostgreSQL16 when diagnosing CI behavior; prior local PostgreSQL17 evidence is not identical.
8. First session output should be a precise backend cancellation diagnosis or bounded blocker, PR252 review outcome, integration plan and preserved checkpoints. No new full suite is part of initial setup.

Useful commands, run individually in the indicated directory after checking the working tree:

Repository:
```sh
git status
git fetch origin
git show origin/cursor/experiments-market-handoff:docs/CURSOR_MIGRATION_20261012.md
```

Backend:
```sh
uv sync --frozen --extra dev
```

Frontend:
```sh
npm ci
```

Focused verification only when the corresponding code changes:
```sh
uv run pytest tests/test_backend_ci_scope.py -x
```

The selector test command belongs in backend/. It is an example for a changed selector, not an instruction to rerun PR252's unchanged31 cases. Load exact existing runtime configuration through the approved mechanism rather than copying placeholder .env values into hosting.

No delivery date is guaranteed by this handover. Timebox the first diagnosis to20 minutes, then report the evidence and next decision. A remaining runtime dependency should not stop independent feature work.

