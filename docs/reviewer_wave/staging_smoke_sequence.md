# PR237 supervising staging smoke sequence

Prepared for the corrected reviewer candidate. This sequence has **not been run**.
Supervising review and explicit approval of the coordinated staging deployment
precede execution. Record the exact API, frontend and worker commit, migration head,
operator, UTC timestamps and sanitized evidence for every result. A deployment of
an older revision does not accept the corrected candidate.

## Prerequisites and gate

1. Review PR237, its revision-scoped verification ledger and
   [indexing rollout/rollback plan](knowledge_indexing_rollout.md). Use an isolated
   staging tenant and non-sensitive synthetic documents; record the initial strategy
   versions and existing BloFin command/trade IDs. Keep credentials in the existing
   secret mechanism, outside screenshots and saved network traces.
2. In the approved coordinated window, apply
   `a8agentcapture001 -> a9knowledgeoutbox001 -> a10blofinactivity001`;
   require the single final head **a10**. Retire old synchronous ingestion writers,
   and deploy API,
   frontend and worker from the same reviewed revision. Verify the API URL, CORS,
   cookie/auth behavior and ingress timeout against the 360-second turn budget.
   Indexing activation requires its separate rollout approval; leaving the flag
   false means pending uploads, not a failed smoke assertion of readiness.
   Follow the exact target/order and retained-schema rollback in
   [release qualification](release_qualification.md). The smoke assertions below
   remain unchanged.
3. Preserve the existing Watcher, Telegram, kill switch, credentials and exchange
   settings. Verify paper posture and `real_trading_enabled=false`. This sequence
   never previews/confirms an exchange order or invokes canonical execution.
4. Run the repository gate using approved staging URLs and operator-provided auth:

   ```sh
   ENV_FILE=.env.staging ./scripts/check-env.sh
   BASE_URL=<approved-api-url> FRONTEND_URL=<approved-frontend-url> \
     GATE_PROFILE=standard INCLUDE_CANONICAL=false \
     ./scripts/post-deploy-smoke-gate.sh
   ```

   Select the deployment's existing cookie mode through `COOKIE_MODE` if required.
   Require exit 0 and retain the sanitized report. A gate failure follows
   `docs/deploy_rollback_runbook.md`; do not continue and label it accepted.
   Any separately required canonical diagnostics must stay on their approved
   synthetic, disarmed path, with no kill-switch mutation or order execution.

## Agent conversation and recovery

1. Open `/agent` with the staging tenant. Send a unique harmless note; record the
   `POST /agent/turns` key, body, request ID, conversation and both message IDs.
   Verify one acknowledged pair appears immediately and remains after reload.
2. In a new conversation, enter exactly 8,001 ASCII characters and click **Send**.
   Expect the existing invalid-request error, zero turn HTTP requests and no
   uncertain recovery panel. Draft stays editable. Replace it with a short note;
   Send enables and exactly one normal turn succeeds. Repeat with a previewed
   synthetic attachment: the preview/reference survives rejection and correction
   does not import the same file again.
3. On a harmless read/conversation turn, use an approved browser/test-proxy fault
   to withhold the response **after forwarding the request**. Capture the original
   body and key before inducing the fault. Let the 360-second browser timeout fire.
   Verify recovery is retained, editing does not replace the original intent, and
   no automatic duplicate turn is sent. Reload the same tab and conversation;
   the original pending request remains recoverable.
4. Restore transport and click **Recover original request** once. Verify byte-identical
   serialized body and the same `Idempotency-Key`. If the server reports a typed
   running/capture conflict, retain that state and wait for an explicit later
   recovery. Once completed, confirm the same message IDs/pair, no duplicate durable
   turn or admission, and preserved separate edited draft. Correlate with authorized
   server evidence; network loss alone does not prove completion.
5. On another harmless turn, let the server respond successfully, then replace its
   response in the approved test proxy with `{ "reply": "malformed fixture" }`.
   Expect the contract error and retained original recovery. Restore the response
   path and recover explicitly with the original body/key. Repeat with invalid JSON
   if the staging ingress transforms bodies. Do not clear recovery based on status
   zero or claim that a malformed response means no server work occurred.

## Strategy draft, confirmation, persistence and reload

1. Open `/strategy-lab/new`; verify it opens Agent without sending automatically.
   Select `liquidity_sweep_reversal`. Attach a synthetic rules file, preview it, and
   use **Review strategy draft**. The executable sample text in
   `frontend/e2e/strategy-conversation.spec.ts` (`FIRST_SLICE_PREVIEW`) supplies a
   reproducible fixture for the existing compile path.
2. Inspect the draft's rules, evidence references, setup type and limitations.
   `GET /conversations/{conversation_id}/proposals` must show no resulting version
   before confirmation. Ordinary conversation must not create an authoritative
   strategy version.
3. Click **Confirm and save strategy version** once. Record the proposal/content
   hash, resulting strategy/version and source document. Confirm the generated
   request uses that proposal's hash and canonical confirmation statement. Reload
   the conversation; **Open saved strategy** still points to the same strategy.
4. Read `GET /strategies/{id}` and `GET /strategies/{id}/versions` independently.
   Verify the selected setup type, saved rules/evidence and conversation binding.
   Record the version count after confirmation; replay the same confirmation only
   in the approved smoke tenant and verify that count does not increase. Creation
   may include an initial version; do not equate one confirmation to one total row.
5. On the fixture that supports compilation, use **Compile saved version**, inspect
   its executable/diagnostic result, then explicitly **Approve compiled policy**.
   Verify lifecycle and reload. This grants no order-execution authority. Do not run
   a strategy, scanner, Watcher or exchange submission to demonstrate approval.

## Document import, indexing readiness and retrieval

1. Import a unique synthetic note through Agent attachment preview/send. Record
   document ID, receipt, conversation and source message. Initial SQL storage may
   be pending; the UI must distinguish stored content from search readiness.
   Navigate via **Open original document** and reload. Canonical SQL content remains
   available even when capture or vector indexing is unavailable.
2. Read `GET /knowledge/documents` and `GET /knowledge/chunks?document_id={id}` in
   the authenticated owner scope. Record `ingestion_metadata.indexing` generation/job,
   attempts, retry time, status and sanitized error. With separately approved
   indexing activation/providers, observe current-generation `ready` only after
   verified remote inventory and committed worker acknowledgment. Pending/failed
   or provider fallback is not native Qdrant readiness.
3. Search the unique phrase via `POST /knowledge/search` using the current generated
   query schema, then ask Agent about that phrase. Verify exact source/chunk identity
   and current generation, truthful readiness/degradation and SQL-authoritative
   content. An unrelated owner's private document must not appear. Use a separately
   prepared organization-shared fixture to verify Agent shared visibility, and a
   second tenant to verify isolation; direct search's shared option is explicit.
4. If a job exhausts retries, retain its error evidence, fix the approved provider
   prerequisite, then explicitly retry that exact owned document. Pending/processing/
   ready retry must not create duplicate provider work. Do not reset a vector
   collection, change dimensions or declare backlog completion from an empty batch.
5. Compare sanitized usage before/after: content admission counts once; indexing
   batches/retries retain token/cost/events without new daily request admissions.
   An exhausted admission or token/cost budget may block indexing before a remote
   call; record it as a prerequisite failure, not successful retrieval acceptance.

## Existing BloFin mirror presentation

1. Use the owner's **existing** command and linked journal trade, with read-only
   `GET /execution/manual-demo/commands/{command_id}` and
   `GET /journal/trades/{trade_id}` evidence. Inspect the UI at desktop and phone
   widths, then reload the same records.
2. Verify command/client/native order identity, contract units versus base quantity,
   fills, fees, protection state, observation time and stale/missing evidence match
   the records. Account flatness must not prove closure; historical configured
   protection must not imply a verified trigger or realized PnL.
3. Follow the linked Journal/Agent presentation and verify it describes those same
   records without introducing a submission. Do not use the manual order form,
   resolve an uncertain order, change settings or submit another external order.
   Missing owner records block native mirror acceptance; local browser fixtures
   verify presentation only.
4. The reported manual BloFin incident remains **undiagnosed** until the failing
   request/stage, sanitized response/request ID, existing command and authorized
   server/native read-only evidence establish its cause. Use
   [the incident checkpoint](manual_order_incident.md); do not infer a cause from
   healthy `/health`, a kill-switch observation or this frontend correction.

## Release review handoff

Record pass/fail/blocker for each sequence against the deployed revision. Retain
sanitized request/server correlations and exact document/proposal/command identities;
exclude credentials and private payloads. Complete the separately required fresh
SFP diagnostics and Agent/RAG/guardrail evaluations. After supervising review,
approved staging and those prerequisites, follow `.ai/RELEASE.md` for **one** manual
`ci.yml` dispatch with `full_backend=true` on the exact reviewed release ref. Inspect
all six jobs, counts and skips before final acceptance. This document prepares that
work; neither staging nor the full release gate is accepted by focused local tests
or the previous run `38009093554` at `4e515dd`.
