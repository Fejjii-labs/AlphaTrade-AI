# PR237 release qualification and coordinated staging worksheet

## Candidate boundary and evidence

Implementation freeze: **619c15fe63ffc23288c2866781e08f9cb44a2ab9** on
`codex/reviewer-wave-integration`. This qualification addendum changes release
documentation and adds a rollback regression only; API, worker, frontend, generated
contracts and historical migrations remain byte-identical to that implementation.
Record the exact published addendum SHA in PR237 and the deployment manifest; review
and freeze that whole revision before staging and final acceptance. **PR241 and
PR242 are excluded.** There is no main merge or deployment in this qualification.

Included feature heads remain PR238 `af672dffb4a20fa0a8783cf7b0870cc59f6e3fff`
and PR239 `769e78a46fbce888810060f8d09cbe4d93f81c31`. The sole schema head is
**a10blofinactivity001**, with explicit `a8agentcapture001 -> a9knowledgeoutbox001
-> a10blofinactivity001` ancestry. Historical a9 results in the integration ledger
retain their original revision labels.

| Check | Executed result and revision |
| --- | --- |
| Existing automatic focused CI | [38059822936](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38059822936) at **619c15fe**: 800 backend and 993 frontend passes; deployment-safety passed. Retained evidence, not rerun or acceptance of a later commit. |
| Retained-schema rollback regression | **1 passed, zero failures/skips/deselections, 143.13 seconds** on disposable PostgreSQL 17.11. Qualification working tree based on 619c15fe; the committed regression is the executed file, with unchanged implementation. |
| PostgreSQL SFP diagnostics | **7 passed, zero failures/skips/deselections, 141.90 seconds**, against unchanged implementation 619c15fe. Five receipt-reuse cases plus two candle-finalization cases listed below. |
| Staging deployment and authenticated smoke | **Unexecuted**; exact-commit deploy, frontend authorization and authenticated HTTPS are blocked as detailed below. |
| Fresh naturally scheduled staging SFP/Nested evaluations | **Unexecuted** for this candidate. Baseline refusal logs are diagnostics only. |
| Full backend / combined acceptance | **Unexecuted**, no dispatch or acceptance link. Review, staging and fresh diagnostics/evaluations must precede the single `full_backend=true` gate. |

Focused local commands (from `backend/`, URLs refer only to disposable loopback
databases; no hosted connection or provider credentials):

```sh
PR237_ROLLBACK_POSTGRES_URL="$disposable_rollback_url" \
  .venv/bin/pytest tests/test_pr237_migration_aware_rollback.py \
  -q -o addopts='' -rA

PHASE1_POSTGRES_URL="$disposable_sfp_url" AT028_POSTGRES_URL="$disposable_sfp_url" \
  .venv/bin/pytest -q -o addopts='' -rA \
  'tests/test_sfp_receipt_reuse.py::test_rolling_encoding_reuse_preserves_original_receipts_for_both_scopes_and_restart[postgres]' \
  'tests/test_sfp_receipt_reuse.py::test_true_payload_change_keeps_original_evidence_and_strict_refusal[postgres-cold]' \
  'tests/test_sfp_receipt_reuse.py::test_true_payload_change_keeps_original_evidence_and_strict_refusal[postgres-cached_then_reconfigured]' \
  'tests/test_sfp_receipt_reuse.py::test_explicit_revision_appends_and_provider_revision_regression_is_refused[postgres-cold]' \
  'tests/test_sfp_receipt_reuse.py::test_explicit_revision_appends_and_provider_revision_regression_is_refused[postgres-cached_then_reconfigured]' \
  'tests/test_binance_candle_finalization.py::test_v2_rebootstrap_preserves_v1_history_and_refuses_new_policy_conflicts[postgres]' \
  'tests/test_binance_candle_finalization.py::test_both_sfp_scopes_resume_with_actual_post_confirmation_receipt_clocks[postgres]'

.venv/bin/ruff check tests/test_pr237_migration_aware_rollback.py
.venv/bin/ruff format --check tests/test_pr237_migration_aware_rollback.py
```

The seven SFP cases use deterministic provider fixtures and real PostgreSQL. They
cover both bullish/bearish scopes, reuse across process restart, original receipt
immutability, true-payload conflict and revision-regression refusal, explicit
revision append, v1 history retained during v2 rebootstrap, and receipt clocks after
confirmation. They do not establish live provider quality or scheduled acceptance.

The rollback case checks the actual `alembic upgrade head` refusal of plain b165
and plain bda, no-op upgrade of the migration-aware bda package, two separate API
lifespan/disarmed-worker restarts, and a no-op upgrade plus API/worker boot on forward
return to the candidate. It compares **every column** in nine scoped tables after
each transition: one revision, one organization, one user, **5 documents, 5 chunks,
5 jobs** (pending/processing/ready/failed and a deletion tombstone), **1 native
account, 3 native facts** (one order/two fills) and **2 incomplete cursors**. Null
legacy generations, leases/claim tokens, failures, exact units, unknown fee/PnL and
coverage checkpoints survive. Retained snapshot SHA256 for this synthetic run:
`2a7d6749ddda360dfcf1df648e5974ae9e455a6396a133b94879365629a1c0c4`.
Changed-file Ruff/format and diff checks passed. No historical migration changed.
Executed rollback regression SHA256:
`37102d133c0efdf041a53c27d4fbc2b6f29bfddb4f55067a503bef98de32fb07`.

## Qualified rollback package and limits

Plain **b165b92276346f0e0fe3ccdbd2bec3443dc75d40** and plain
**bda597c2fffbf1a49beadc64d757100808094ca2** do not know a10. Their startup
`alembic upgrade head` must refuse a retained a10 database. Reading additive columns
is insufficient. Never stamp the database or substitute a destructive downgrade.

The migration-aware rollback source is the previously reviewed **bda597c2**
application plus exactly one byte-identical a10 migration from **619c15fe**:
`backend/src/app/db/migrations/versions/a10blofinactivity001_account_scoped_native_blofin_activity.py`.
The base already implements a9 outbox/generation fencing; b165's synchronous
ingestion writers are excluded. Its dependency lock, Dockerfile and entrypoint are
unchanged from the candidate. This package removes the subsequent voice/activity
application features while retaining their schema and native data for forward return.

Package provenance:

- Base files: **2,376**, all verified byte-identical to bda597c2; one added migration.
- a10 migration SHA256: `9a2f15cda23242615ecb33c77ff88d1bad9732c8aea020980f1ce26e3c840341`.
- Normalized source tar SHA256: `8801de2b66153a8561e7807bf1f6a57450ac58c390d9de9bd974cf58e1d8a032` (33,638,400 bytes).
- Container image/digest and hosted startup: **not built or qualified**. The local
  proof uses archived source with the identical Python dependency lock, actual API
  lifespan and the actual disarmed worker entrypoint. It does not qualify an armed
  worker, live vector store, remote consumer or Docker packaging.

Reproduce the source package in a new disposable directory (never inside a live
checkout). `rollback_root` is an operator-selected temporary directory:

```sh
rollback_base=bda597c2fffbf1a49beadc64d757100808094ca2
migration_source=619c15fe63ffc23288c2866781e08f9cb44a2ab9
rollback_migration=backend/src/app/db/migrations/versions/a10blofinactivity001_account_scoped_native_blofin_activity.py
mkdir -p "$rollback_root/source"
git archive "$rollback_base" | tar -xf - -C "$rollback_root/source"
git show "$migration_source:$rollback_migration" > "$rollback_root/source/$rollback_migration"
sha256sum "$rollback_root/source/$rollback_migration"
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
  -cf "$rollback_root/rollback-package.tar" -C "$rollback_root/source" .
sha256sum "$rollback_root/rollback-package.tar"
```

Before staging, build and verify the API/worker image from that source through the
existing approved artifact path, record its digest and provenance, and establish
how the two existing services select it without merging main. That hosted artifact
selection is still blocked; a local source tar is not a Render deployment revision.

## Existing targets and observed staging state

Read through authorized Render connector access on **2026-10-10**:

| Component | Existing target | Observed live revision |
| --- | --- | --- |
| API | `alphatrade-api-staging`, `srv-d8fvbcd7vvec739mc060`, workspace `tea-d7hn0fvavr4c73f62c70`; `https://alphatrade-api-staging.onrender.com` | b165b92276346f0e0fe3ccdbd2bec3443dc75d40, deploy `dep-db4gdkeq1p3s73a395u0` |
| Worker | `alphatrade-paper-worker-staging`, `srv-daqej7o473hc73fsc1k0`, same workspace; command `python -m app.workers.paper_worker` | b165b92276346f0e0fe3ccdbd2bec3443dc75d40, deploy `dep-db4gdkeq1p3s73a3975g` |
| Frontend | Historical project evidence identifies Vercel `alpha-trade-ai`, project `prj_y7vFqwbFvdpHvf2nMaDkKQauBhZQ`, team `team_LNgcEGBkqntUnTrktNjFzh7a`, alias `https://alpha-trade-ai-eight.vercel.app` | **Current revision and API binding unknown**; project refresh returned 403 forbidden. Alias environment/promotion target must be verified before use. |

Both Render services track **main**, auto-deploy enabled, Docker context `./backend`.
API pre-deploy is `alembic upgrade head`; worker command bypasses the API entrypoint's
migration command. Do not deploy the separate `ai-alphatrade-api-staging` service.
Do not use a branch-trigger-only deploy: it would select main, not this frozen candidate.
Coordinate against concurrent main auto-deploys; refresh service/deploy state before
each step and stop if another revision supersedes the intended candidate.

Bounded baseline worker logs show startup `migration_unhealthy` refusals on October
9 at 15:23:52, 15:24:09 and 15:24:27 UTC. Later canonical evidence refusals include
`forming_candle`, `upstream_ban` (October 10 04:45:09 UTC) and `rate_limited`
(October 10 10:30:14 UTC). The later-log selection is partial (`hasMore=true`).
These are observed reason codes on b165, not a diagnosed root cause or successful
candidate evaluation. Preserve evidence; do not rewrite receipts or relax validation.

## Exact coordinated staging order (prepared, not executed)

1. Supervising review freezes the **whole PR237 qualification SHA**. Record the
   candidate, included feature heads, rollback image/source manifest and service
   deployment IDs. Keep PR241/PR242 out. Record existing operator settings securely,
   including watcher, Telegram, kill switch, account identity, providers and both
   consumer flags. Do not reset them from `render.yaml` or activate anything new.
2. In the coordinated window, obtain a restorable PostgreSQL snapshot and scoped
   document/job/native cursor inventories. Use the existing authorized write-hold
   mechanism; its actual operator control has not been verified here. Drain API
   ingestion/turn requests and stop old worker/consumer processes gracefully before
   migration. Preserve pending request keys, claims and checkpoints. If the existing
   environment cannot hold writes/stop old writers, stop qualification at that blocker.
3. Deploy the exact candidate to **srv-d8fvbcd7vvec739mc060**, preserving the API's
   existing configuration. Its pre-deploy applies a8 -> a9 -> a10 before new API code;
   require exactly one `alembic_version` row at a10 and one expected code head at a10.
   The API entrypoint's repeat upgrade is a no-op. Keep old synchronous ingestion
   writers retired. Verify the live deploy commit equals the frozen candidate and
   `/health` remains paper-only. Do not stamp or change migration history.
4. Deploy the same exact candidate to **srv-daqej7o473hc73fsc1k0**, with its existing
   command/settings. Verify database and packaged expected heads together from the
   actual worker container, health/refusal reason codes and absence of overlapping
   old writers/consumers. Do not toggle authority to make a refusal disappear. Keep
   existing approvals; previously disabled indexing/activity remains disabled.
5. Build/deploy the existing Vercel project from a clean checkout of that exact
   candidate with the existing approved API binding. Verify project/team, environment,
   CORS, secure cookie behavior and the 360-second turn budget before assigning the
   existing staging alias. Record frontend Git source SHA and deployment ID. Do not
   create a new project or infer its target environment from the alias name.
6. Require **API commit = worker commit = frontend Git source = frozen candidate**,
   a10 code/database agreement, correct API binding and preserved operator settings.
   Only then release the approved write hold. Follow the preserved
   [authenticated staging smoke sequence](staging_smoke_sequence.md): Agent/recovery,
   attachments, strategy save/reload, indexing/ownership and read-only BloFin mirror.
   Add the native activity assertions in the integration handoff. Disabled indexing
   means readiness acceptance is blocked; no provider/consumer activation is granted.
7. Run the bounded same-worker read-only migration/preflight and tenant-scoped SQL
   in [PR215 diagnostics](../pr215_release_blockers.md), keeping its tenant filters.
   Observe fresh **naturally scheduled** successful evaluations for both currently
   approved SFP BTCUSDT 15-minute scopes and continued Nested evaluations. Review
   original v1 receipt identities and new v2 evidence without mutation. Neither a
   public-market probe nor a boot heartbeat accepts scheduling; retain each refusal.
8. Once supervising review, aligned staging smoke and fresh required diagnostics/
   evaluations are accepted, dispatch **one** `ci.yml` run with `full_backend=true`
   on the frozen exact ref under [.ai/RELEASE.md](../../.ai/RELEASE.md). Record run
   URL, `head_sha`, all six jobs, counts and unexpected skips. No such dispatch is
   justified while the prerequisites below remain blocked. Later changes invalidate
   acceptance of the earlier SHA.

The established exact-commit Render command, when CLI authorization/HTTPS becomes
available and steps 1-2 have completed, is:

```sh
render deploys create srv-d8fvbcd7vvec739mc060 --commit "$release_sha" --wait --confirm -o json
# Verify API migration/health and commit before continuing.
render deploys create srv-daqej7o473hc73fsc1k0 --commit "$release_sha" --wait --confirm -o json
```

Do not merge main, change tracked branches or use `render_trigger_deploy` as a
substitute. That available connector has no commit argument. Frontend exact-source
deploy/promotion remains conditional on verified project access and environment.

## Migration-aware rollback order

1. Hold writes and drain candidate indexing/activity consumers and API requests;
   snapshot/export SQL documents, chunks, generations, every job status/tombstone,
   native accounts/facts/cursors and current operator settings. Retain an immutable
   record of the candidate deployment and rollback package/image digest.
2. Keep the database at **a10**. Select the qualified migration-aware **bda + a10**
   API/worker artifact through the established exact artifact deployment path. Its
   `alembic upgrade head` must be a no-op. Plain b165 or plain bda is forbidden.
   Validate image packaging before use; this task qualified only the source package.
3. Restart API, then the matching worker package under the same write hold; validate
   expected/database a10, paper health and retained row/checkpoint inventories.
   Keep consumers drained until separately reviewed resumption; the local disarmed
   probe does not qualify existing armed operator settings. Never toggle watcher,
   Telegram, account, kill switch or provider settings as a rollback workaround.
4. Restore the existing frontend to the bda application revision after verifying its
   project/environment. Record frontend SHA plus the API/worker package's base,
   migration overlay and digest, rather than falsely labeling the overlaid image bda.
   Native activity UI may be unavailable; native facts remain retained for forward
   return. Resume only after the approved post-rollback read-only gate.
5. Forward return uses the frozen candidate, API -> worker -> frontend, with a10
   upgrade again a no-op and generation/job/native data preserved. The source-level
   rollback test checks this return. Do not clear jobs, reset Qdrant, rewrite account
   identity or downgrade a9/a10 to make an old image start.

## Exact remaining blocked operations

| Operation | Available evidence / missing capability |
| --- | --- |
| Pin candidate API/worker deploys | Render service/deploy/log reads work. Connector deploy only selects tracked branch; no exact-commit field. Environment has no Render CLI credential/configuration and no HTTPS allowance to `api.render.com`. |
| Capture operator settings, drain writers, inspect live SQL/preflight, qualify rollback image | No authenticated Render CLI/SSH/container execution, database execution or existing artifact-selection access is bound. Docker socket is unavailable locally. These operations were not attempted on hosted resources. |
| Verify/promote existing frontend | Vercel project read returned **403** for the identified team/project; no authorized CLI token/session is bound. Current frontend revision, API binding and alias environment remain unknown. |
| Authenticated Agent/strategy/document/native smoke | Staging API/frontend HTTPS is outside the environment's allowed hosts; no authenticated synthetic staging tenant/session (`SMOKE_ACCESS_TOKEN`) and approved scoped fixtures are bound. Baseline Render logs do not substitute for these checks. |
| Fresh candidate SFP/Nested scheduled acceptance | Candidate is not deployed; same-worker preflight/SQL and authenticated tenant reads unavailable. Baseline reason codes above are not successful evaluations. |
| Single final full acceptance | Supervising addendum review, rollback image/staging and fresh diagnostics/evaluations remain unmet. Actions dispatch access is also unavailable; **do not dispatch prematurely**. |

**Recommendation: not release-ready.** Independent local qualification and concrete
rollout preparation can be reviewed now. Next executable release action is review
and freeze of the published qualification SHA, followed by verified rollback image
packaging and the existing-service write-hold/exact-commit API deployment in step 3
when that specific hosting capability is available. The manual BloFin order incident
remains undiagnosed; no orders, Telegram messages, activation or settings changes
were performed.
