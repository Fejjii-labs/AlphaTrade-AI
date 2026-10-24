# Limitations and roadmap

Refreshed October 24, 2026 against main `df09c22d` in [Fejjii-labs/AlphaTrade-AI](https://github.com/Fejjii-labs/AlphaTrade-AI); rows below marked (Oct 7) are the original baseline. Original baseline: main `b58beda`, October 7, 2026. “Implemented” means source exists; “accepted” needs evidence for the relevant runtime and release. [Current status](current_status.md) records supplied supervising evidence and unknowns.

## Current boundaries

| Area | Implemented foundation | Limit or outstanding acceptance |
| --- | --- | --- |
| Paper lifecycle | Approved strategy/evidence → Candidate → risk/plan authority → paper fill → Journal/attribution. | User reports a historical Nested/internal-paper fill and Journal; full CI #783 is pending at check, and all strategy/symbol paths are not established. |
| Nested strategy | Supported compiled structural family and canonical paper continuation. | Supported timeframes/source constraints apply; a generic NL strategy is not automatically executable. |
| SFP | Structural detection/research lifecycle, reusable immutable receipts and frontend surface. | Existing structural research support does not provide an authorized SFP execution plan or trade-return promotion evidence. Fresh live recovery after PR209 remains pending. |
| Journal | Canonical lineage and preservation of approved plan targets in PR208. | Repair/acceptance for existing affected Journal targets remains pending. Deployment does not rewrite old rows automatically. |
| Binance REST finalization | PR209 guards/compares eligible REST candles and versions the application policy without rewriting v1 history. | This is not a provider finality guarantee; bounded fresh SFP acceptance remains pending. |
| BloFin demo | Demo account context, separately governed dispatch and native evidence reads; PR220 owner-confirmed BTC MARKET test with one full target. | Account sync succeeded in supplied evidence; native order/fill/protection acceptance is pending. No LIMIT entry, manual exit, complete exit/PnL/funding reconciliation or repeat-entry release in the manual capability. |
| Agent | Model conversation, scoped reads, structured proposals and explicit confirmation. | Models can err; bounded context/retrieval and provenance do not establish perfect answers. |
| Retrieval | Knowledge indexing/search; default Agent bounded lexical SQL retrieval. | No default vector lookup on every Agent turn; ranking/citation is not truth. SQL/vector commit consistency has external-store limits. |
| Learning/behavior | Journal review, deterministic analytics/attribution and governed version promotion. | No silent online training, automatic strategy self-modification or proven behavior/performance improvement. Samples and strategy-specific support constrain conclusions. |
| Voice | Browser dictation/transcript review and optional playback. | Server voice contracts unimplemented; real mic/service/Safari/iOS acceptance UNKNOWN here. Continuous richer voice is future work. |
| Screenshots | Authentic watermarked fixtures and older local captures. | Several show historical UI. No image proves current hosted completeness, live balance or trading results. |
| Operations/security | Hosted fail-closed provider policy, roles, scoped records, audit and release procedures. | Deployment/health/restore/device/security assurance require actual evidence. No certification/pentest/comprehensive readiness claim. |
| Billing | Provider/usage/quota scaffold; charging disabled by default. | Live subscription/payment acceptance is not established by this documentation task. |

Real-money execution is permanently refused by the inspected source. It cannot be enabled as an ordinary feature flag or Agent command. Demo and paper performance do not guarantee real-world results.

## Open items as of October 24, 2026

| Item | Reference |
| --- | --- |
| Demo daily trade cap is a lifetime count | [#318](https://github.com/Fejjii-labs/AlphaTrade-AI/issues/318), PR #325 |
| Live voice cancel handling and 30s bounded shielded close | PR #326 |
| Proposal Postgres tests never ran in CI | PR #327 |
| Pre-existing red full backend suite (18 failures) | [#316](https://github.com/Fejjii-labs/AlphaTrade-AI/issues/316) |
| $100/month default org quota vs $1 voice session ceiling (product decision) | Griller review of #322 |
| No Postgres test for two concurrent first voice reserves | Griller review of #322 |
| Abandoned GETs can occupy all 8 API threads | Griller review of #321 |
| AT014 staging test orgs keep active kill switches | staging DB `kill_switch_states` |
| Legacy `ai-alphatrade-api-staging` missing env (REDIS_URL, OPENAI_API_KEY, TRUSTED_PROXY_HOPS) | Render |
| Autonomous paper loop armed on staging; first automatic trade not yet observed | `/health/paper-worker` |
| SFP / Nested 1-2 tuning with the owner | — |
| UI walkthrough → prioritized fix list | — |
| Bybit failover not observed | `/providers/status` |

## Priority: finish current evidence (Oct 7)

1. Complete bounded fresh SFP recovery acceptance on the intended deployed policy/SHA while preserving historical receipts.
2. Complete and verify the separately supervised existing Journal target repair without inventing/backdating outcomes.
3. If explicitly authorized, complete governed BloFin demo protection/fill/restart acceptance; keep actual demo exposure supervision separate from internal paper checks.
4. Read the existing full acceptance CI #783 result on `b58beda`, then finish the relevant exact-SHA release/product acceptance gates. A focused CI pass, page loading report or one paper event is insufficient.
5. Record observed model/provider/hosting versions and device/voice acceptance instead of promoting defaults or fixture results to runtime facts.

These are documentation priorities, not authorization to deploy, repair databases, arm services or place orders.

## Product direction beyond the foundation

| Direction | What exists already | Still planned or incomplete |
| --- | --- | --- |
| Personal trading assistant | Stored conversations, selected strategy context, Journal and knowledge reads. | Richer continuous context/voice interaction and evaluated long-term assistance. |
| Behavior learning | Review/attribution, coaching and governed proposals. | Validated continuous behavior support and demonstrated improvement; no online model-training claim. |
| Wider orchestration | Typed tools, Strategy Brain, Watcher, supervised worker and explicit authority handoffs. | Broader cross-workspace agent behavior with proved end-to-end acceptance; no unbounded model controller. |
| Additional strategies | Nested and SFP plus legacy research/backtest tools. | More canonically compiled/evaluated strategy families and their separate evidence/execution support. |
| Evaluation/observability | Offline regressions, deterministic analytics, audit/logs and optional RED metrics. | Wider model-quality, reliability, restore and statistical validation; distributed tracing is not established by a configured key. |

Design PDFs and redesign blueprints express intent. Exact-base handoffs express development evidence. Current code/configuration defines implemented behavior; deployed acceptance requires its own dated evidence. [Architecture](architecture.md) · [Interview material](interview_package.md) · [Testing](evaluation.md).
