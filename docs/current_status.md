# Current status and evidence

**Refreshed:** October 24, 2026, 22:50 Europe/Berlin. **Repository:** [Fejjii-labs/AlphaTrade-AI](https://github.com/Fejjii-labs/AlphaTrade-AI) (moved from `Fejjii/AlphaTrade-AI`; older links below point at the original repository and are kept as historical evidence).

## Status as of October 24, 2026

**Main:** [`df09c22d`](https://github.com/Fejjii-labs/AlphaTrade-AI/commit/df09c22d91be5de853098afd36504d26ae106296) (#324). Recent merges: #310 journal owner checks, #321 bounded chat market retrieval and readiness alerting, #322 voice quota lazy init, #323 autonomous switch gated on org membership role.

| Area | Observation (direct checks on Oct 24 unless noted) | Remaining limit |
| --- | --- | --- |
| Render staging API `alphatrade-api-staging` | Live on `df09c22d`, deploy trigger `new_commit` (push auto-deploy proven after the Render GitHub app was installed on Fejjii-labs). | Legacy duplicate `ai-alphatrade-api-staging` was deleted on 2026-10-29; `alphatrade-api-staging` is the only staging API. |
| Render paper worker | Live on `df09c22d` (`new_commit`). `/health/paper-worker`: fresh, `autonomy_mode=internal_paper`, `real_trading_enabled=false`, org kill switch inactive. | No automatic paper trade observed yet after arming. Three AT014 test orgs still hold active kill switches and make the top-level `kill_switch` block read active. |
| Readiness | `/health/ready`: `status=ready`, `degraded_providers=[]`. Staging scripts exit non-zero on degraded status ([ready_health.py](../scripts/lib/ready_health.py), #321). | Scripts are run manually; there is no scheduled alerting job. |
| Market data | Binance public and Binance USD-M perpetual healthy. | Bybit secondary source configured but not reported on `/providers/status`; failover not observed. |
| Models | `openai-llm` healthy (gpt-6-luna); embeddings `degraded` until generation is verified. | Model-quality evaluation beyond offline suites. |
| Voice | GPT Live voice merged disabled (#315); $1 staging smoke passed Oct 23. New-org quota fix (#322). | Follow-ups in review: cancelled Live children and bounded shielded close (#326). Device/Safari/iOS acceptance unknown. |
| BloFin demo | Read-only sync OK. | Daily trade cap behaved as lifetime count ([#318](https://github.com/Fejjii-labs/AlphaTrade-AI/issues/318), fix in #325). Native order/fill/protection acceptance pending. |
| CI | Six jobs (backend, frontend, e2e-smoke, evaluation, docker-build, deployment-safety). #321 run 40099208349 executed e2e-smoke (58 passed) and evaluation (agent 16/16, RAG 5/5, guardrail 7/7). | [#316](https://github.com/Fejjii-labs/AlphaTrade-AI/issues/316): pre-existing red full backend suite (18 failures). Proposal Postgres tests skipped in CI until #327. |
| Real trading | Refused by [paper safety](../backend/src/app/core/paper_safety.py). | Unchanged, by design. |

## Historical baseline (October 7, 2026)

**Documentation inspection date:** October 7, 2026. **GitHub evidence check:** 15:32:19 Europe/Berlin (13:32:19 UTC).

**Main/source baseline:** [`b58bedae1baad82b36fd32b04d056372cac3c733`](https://github.com/Fejjii/AlphaTrade-AI/commit/b58bedae1baad82b36fd32b04d056372cac3c733), the merge of [PR220](https://github.com/Fejjii/AlphaTrade-AI/pull/220). The documentation branch starts from that commit. Local tree `9b36f0e1c681a2b3f5ec307e2da17bc124b5eba6` was verified against GitHub before editing.

## What each kind of evidence means

| Label | What it establishes |
| --- | --- |
| Implemented | The inspected source contains the path and its wiring. |
| Configured | A manifest/default declares an option; this does not establish active runtime state. |
| Deployed, supplied observation | The release supervisor reports a direct service/database check on the stated commit. This documentation pass did not repeat the live probe. |
| Demonstrated, supplied observation | The user reports an actual received notification, stored paper record or account-sync result. No private identifiers or raw payloads are reproduced. |
| Independently checked GitHub | Repository, PR, status or Actions metadata was read through GitHub during this task. |
| Recorded development evidence | A checked-in acceptance record or PR summary describes tests on its own base; these are not fresh live acceptance. |
| Pending / unknown / planned | Acceptance, runtime detail or broader product behavior is not established. |

## Deployment and demonstrated evidence

| Area | Evidence and provenance | Supported conclusion / remaining limit |
| --- | --- | --- |
| Current repository | GitHub main points to `b58beda`; PR220 is merged. | The owner-confirmed manual-demo implementation is part of main. |
| Render API | Supplied release observation: directly verified live on `b58beda`. | Reported deployed API baseline; no fresh runtime probe by this documentation task. |
| Render paper worker | Supplied release observation: directly verified live on `b58beda`. | Reported deployed worker baseline; worker arming and current dependency health remain separate observations. |
| PostgreSQL | Supplied release observation: migration `a6manualdemo001` applied. [Source migration](../backend/src/app/db/migrations/versions/a6manualdemo001_manual_demo_origin.py). | Reported schema baseline; current server version and restore readiness are not established. |
| Vercel | GitHub commit status is `success`, updated October 7 at 14:32:42 Europe/Berlin. [Deployment status target](https://vercel.com/alphatrade-ai/alpha-trade-ai/5ww5tiQg1Zv26h9YQJxhGnrCYEJC). | Deployment success for `b58beda`; not a browser end-to-end acceptance result. |
| Full release CI | [Run #783](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/37622005520), exact SHA `b58beda`, `workflow_dispatch`, still **in progress** at this check. | Frontend, deployment-safety and Docker build jobs succeeded. Backend Ruff/format succeeded; complete backend acceptance step remains in progress. No full-suite pass claimed. |
| Ordinary CI | Runs [#781](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/37621545658) and [#782](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/37621569938) succeeded on `b58beda`. | Ordinary workflow success is distinct from complete backend acceptance. |
| Telegram | User-supplied operational evidence: a Watcher-generated notification was received. | Actual notification receipt was reported; universal notification reliability is not established. |
| Historical trade | User-supplied operational evidence: Nested BTC short has an internal paper fill and Journal. | A historical simulation record exists; it is not a native BloFin fill, a completed exchange exit or a profitability result. |
| BloFin account context | User-supplied operational evidence: demo account sync succeeded. | Read/account-context path demonstrated; native demo order execution acceptance is **not yet demonstrated**. |

The supplied operational observations have no separate timestamped capture in this checkout. Their inclusion records the evidence supplied for the October 7 package, not a claim that all events occurred on October 7. GitHub metadata is independently checked; Render/database/trade/Telegram/account-sync observations retain their supplied provenance. A compact [machine-readable snapshot](evidence/reviewer_status_2026-10-07.json) records the distinction.

## Implemented capability and current boundary

| Area | Source path | Boundary |
| --- | --- | --- |
| Six workspaces | [Navigation](../frontend/src/components/layout/navigation-config.ts). | Current authenticated browser/device acceptance needs actual captures. |
| Canonical paper continuation | [Runtime](../backend/src/app/runtime/canonical.py), [Watcher](../backend/src/app/workers/watcher_paper.py), [internal loop](../backend/src/app/services/automated_paper_loop.py). | Supported approved strategy, genuine setup, account, eligibility, risk and explicit authority required. |
| Entry policy | [Planned reward/risk](../backend/src/app/services/planned_reward_risk.py). | Prospective allocation-weighted gross 1R minimum; historical plans are not rewritten. |
| Agent grounding | [Interactive Agent](../backend/src/app/interactive_agent/), [recorded-trade guide](agent_recorded_trade_grounding.md), [historical setup guide](agent_historical_setup_explanation.md). | Bounded facts/context; exact historical lineage; prose cannot confirm or execute. |
| Knowledge | [Retrieval](../backend/src/app/interactive_agent/retrieval.py), [RagService](../backend/src/app/services/rag_service.py), [file import](knowledge_file_import.md). | Default Agent retrieval is lexical; Qdrant indexing/search is a separate path. |
| SFP | [Canonical evaluation policy](AT067_canonical_strategy_evaluation_policy.md), [recovery guide](sfp_candle_finalization_recovery.md). | Structural detection/research supported; authorized automatic SFP execution plan unsupported; fresh recovery acceptance pending. |
| Journal targets | [Projection/repair guide](journal_plan_target_repair.md). | Plan targets and Journal projection are separate; existing affected-row repair acceptance remains pending. Agent reads can expose a discrepancy without repairing it. |
| Manual BloFin demo | [Service](../backend/src/app/services/manual_demo_service.py), [acceptance procedure](manual_blofin_demo_acceptance.md). | Default-off, staging-only, owner-confirmed BTC MARKET, one 100% target. No limit orders, manual exit, complete exit/PnL/funding reconciliation or repeat-entry release. |
| Learning | [Promotion service](../backend/src/app/services/strategy_promotion.py). | Exact validation/paper evidence and human promotion; no silent self-modification. Manual demo tests are excluded from strategy validation/learning. |
| Voice | [Browser provider](../frontend/src/lib/voice/browser-voice-provider.ts). | Browser dictation/playback implemented; physical microphone/service/Safari/iOS acceptance unknown; server voice/image-analysis contracts unimplemented. |
| Real trading | [Permanent paper safety](../backend/src/app/core/paper_safety.py). | Real mode and enablement are refused by the inspected source. |

## Unknowns and final acceptance updates

Current resolved model identities, managed PostgreSQL/Redis/Qdrant versions, authoritative provider health, active Bybit failover, precise watchlist/strategy runtime state and comprehensive device acceptance were not observed here. A deployment status cannot resolve those questions.

Before presenting, read the existing CI #783 result and update its status **only from the result**. Fresh SFP recovery, the historical Journal target repair and native demo fill/protection acceptance need their own scoped records. Complete demo exit/outcome/funding support is an implementation gap, not merely a missing screenshot.

## Visual evidence and historical records

The main showcase uses rendered, source-verified diagrams. Existing screenshot sets are dated local/synthetic fixtures, sometimes with obsolete navigation or unavailable providers. They remain preserved but are not current product proof. [Authentic capture instructions](screenshots_checklist.md).

[Earlier closed-loop acceptance](evidence/full_paper_closed_loop_acceptance_001.json), its [verification campaign](evidence/full_paper_closed_loop_verification_001.json), [product acceptance](evidence/final_product_acceptance_001.json), historical release notes and exact-base handoffs retain their original verdicts and scope. Do not combine tests from different commits into a claim that today's full product passed.

Current reading order: [README](../README.md) → [presentation](reviewer_presentation.md) / [architecture](architecture.md) → [Agent](agent_workflow.md) / [retrieval](rag_system.md) → [deployment](deployment.md). [Reviewer evidence checklist](reviewer_evidence.md).
