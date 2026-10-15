# Reviewer wave integration contract

Owner: `codex/reviewer-wave-integration`. Isolated worktree; no merge to main,
deployment, exchange order, Telegram message, runtime activation or credential change.

## Integrated source revisions

| Source | Revision |
| --- | --- |
| Main / PR233 | `b165b92276346f0e0fe3ccdbd2bec3443dc75d40` |
| PR234 frontend | `69778070cbc9f7431e3af6224e393722cf5eafb9` |
| PR235 data/indexing | `d1bf70babc8db4a7219fa32fe7af229b4b466031` |
| PR236 Agent | `44d68013477c06a44e03f6e8f49a584906d4a9e4` |

Refs refreshed before integration. All three source histories are preserved by merge
commits on this branch. Source branches and unrelated worktrees remain untouched.
Refresh again before final publication to incorporate any remaining published contract.

## Refreshed source CI

| Source | Run | Result |
| --- | --- | --- |
| PR234 | [37999918004](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/37999918004) | Browser failed; other five jobs passed |
| PR235 | [38001263059](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38001263059) | Browser and RAG evaluation failed; four jobs passed |
| PR236 | [38002576268](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38002576268) | Browser failed; other five jobs passed |

These focused source runs do not establish combined application acceptance.
Manual BloFin incident remains undiagnosed; retain the evidence request in
`manual_order_incident.md`. Do not send another external order to reproduce it.

## Required integrated boundaries

- Agent retrieval uses short repository reads for chunk and parent authority,
  exact tenant/private/shared scope, current indexed generation and truthful degradation.
- Every mutating LangGraph phase guards the active reservation, transcript revision
  and principal authority inside its committing transaction. Model calls remain sessionless.
- Indexing observations cannot overwrite a superseding generation; provider outage
  recovery preserves durable job identity, fencing and bounded attempt accounting.
- One intentional turn consumes one request admission. Provider attempts, retries,
  capture stages, tokens and costs remain independently metered. Replay admits nothing.
- Client retains the original UUID key and exact payload across uncertain outcomes
  and reload; recovery is explicit. Complete generated request validation precedes
  pending state, so local rejection leaves an editable draft without recovery lock.
  Browser/proxy/server budgets are coordinated at 360s.
- Conversational strategy authoring remains reachable, with review/confirmation,
  exactly-once persistence and the existing compilation/approval lifecycle.
- Integrated OpenAPI generates turn/recovery/indexing types and strict validators;
  CI checks drift and executes the reviewer browser tests and deterministic outbox flows.

## Verification and readiness

Focused checks during repairs; PostgreSQL for race/persistence claims. Final local
checks include relevant regressions/migrations, frontend lint/types/unit/build,
actual browser journeys and Agent/RAG/guardrail evaluations. Record exact commands,
revision, exit codes/counts/skips and CI separately. Supervising review and approved
staging validation precede the single full release dispatch in `.ai/RELEASE.md`.
This task does not deploy or dispatch that gate prematurely.

Status: integration repairs and local verification complete; draft PR237 published.
Required PR CI and the review-readiness decision are bound to their exact revision
in the PR description and canonical handoff. Full release prerequisites remain pending.

## Findings, fixes and verification

| Finding | Integrated fix | Evidence |
| --- | --- | --- |
| Detached parent reads rejected all hits; shared capability was absent | Repository parent port, published capability, SQL scope/generation/version/hash/readiness checks, final adapter readiness reload | Actual combined adapter PostgreSQL cases: own/shared/other owner/foreign/mismatched/stale/pending/deleted, plus readiness changed after search |
| Expired graph could write before response rejection | Active lease/revision/authority guard at SQL phase entry, flush and commit | Blocked real LangGraph retrieval, six expired/superseded strategy/analysis/paper-write interleavings; no domain writes |
| Old observation could replace newer ready metadata | Conditional SQL update on full current generation identity | PostgreSQL replacement/new acknowledgment race |
| Initial Qdrant failure stranded a worker instance | Bounded same-instance reconnect, preserving runner/jobs/fences | Deterministic SDK failure/recovery with original job ready and one job attempt |
| Retry/capture events consumed additional request quota | Durable admission counting separated from actual usage; positive admitted limit can finish; token/cost/zero limits still checked | Last allowed request retries, completes, replays without new admission; next request blocked |
| Timeout/reload could lose original intent; partial history could reverse pairs | Auth-scoped original key/body persistence, typed explicit recovery, ordered ID reconciliation and persisted conversation binding | Recovery remount/body/key tests and partial user/assistant/multiple-turn tests; production browser acknowledgment journey |
| Generated local request rejection left an uncertain recovery lock despite no transport | Validate the complete generated body before pending state; keep draft/attachment references editable, preserve ambiguous transport recovery | a91d25f: real generated-client component regressions for local rejection/correction, retained attachment, timeout/remount, malformed HTTP 200 and explicit exact body/key recovery; focused four-file selection 53 passed |
| Authoring routes lacked a complete replacement journey | Explicit draft action, imported evidence reference, canonical confirmation/save, reload, compile and approve; preserve setup type and bind newly created strategy conversation | PostgreSQL/API exactly-once confirmation; real browser file-import journey; old panel unit assertions retained |
| Handwritten indexing contracts rejected or mislabeled new states | Generated DTOs/validators/client, strict offset dates with SQLite UTC serialization, status/retry/poll UI and CI drift check | Generated indexing/decimal/date validation, frontend checks and independent HTTP setup-type reload |
| Browser/evaluation fixtures assumed old responses and synchronous vectors | Contract-valid fixtures, real isolated outbox consumer, exact-document readiness wait; reviewer tests registered against production build | RAG 5/5; Agent 16/16; guardrails 7/7; final browser evidence in ledger |
| Persisted uploads disappeared from receipts after capture failure; exact-document navigation waited on unrelated listings | Retain the acknowledged and historical source reference independently of capture, show its original-document link after reload, and load exact sources without library queries or readiness polling | Capture error and original-source link both visible; original import assertion preserved and strengthened after reload; stalled unrelated-list regression |
| New asynchronous writes require coordinated rollout | One migration head; API/worker version compatibility, backlog/index inventory, activation defaults, older writer retirement and rollback plan | Disposable PostgreSQL migration upgrade/downgrade/reupgrade with legacy content retained |

The BloFin browser fixtures validate recorded-evidence rendering and scoped local
behavior. The reported manual order incident remains undiagnosed; they do not prove
external submission or fills. Required evidence remains in manual_order_incident.md.
The broader strategy, exchange, market-data, voice and UI roadmap remains queued.
