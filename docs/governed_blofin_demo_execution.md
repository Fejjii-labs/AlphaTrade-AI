# Governed BloFin demo execution

This integration permits automatic **demo entries** from an approved compiled
strategy's genuine Watcher `CONFIRMED_SETUP` Candidate. Real trading remains
permanently disabled. No network orders were submitted during implementation;
all execution acceptance here uses simulated venue responses and a disposable
local PostgreSQL database.

`tests/test_governed_blofin_demo.py` passes all 20 focused cases on the combined
Agent/execution branch, including actual durable Agent reads and turns. The
existing internal-loop and supervisor regressions pass all 21 cases. Ruff checks,
strict mypy on the 13 changed production modules and diff checks pass. These
results do not establish real staging acceptance.

## Canonical authority and evidence

The flow remains Watcher → Candidate → ActionEligibility → deterministic sizing
and risk → immutable canonical TradePlanRevision → exact hash authorization →
durable reservation/claim → fenced dispatch → venue fill reconciliation →
Journal → learning attribution → read-only Agent explanation. The explicit
operator arm authorizes the automatic demo continuation; generated Agent prose
and Telegram never authorize orders. Legacy order placement and mirror paths
remain tombstoned.

The demo worker checks current Read + Trade permissions, refuses withdrawal or
transfer permissions, verifies NET/cross and existing leverage 1, requires a
flat account, reads actual linear USDT instrument constraints and a venue-timed
quote under ten seconds old, and retains the canonical 20 bps cross-venue basis
limit. Quantity is rounded down in contracts using the venue contract value,
lot/minimum/maximum and conservative 1% equity risk / 10% notional caps including
fee/slippage allowances. Stops and targets use venue tick precision. Journal
size uses the corresponding base quantity; immutable fill facts retain contracts.

The quote's `observed_at` is the venue's order-book `ts`, not local receipt
time. At receipt it must be 0–10 s old on the local clock, so a local clock
behind the venue is refused as future dated. Right before eligibility the loop
reads the same GET-only snapshot again when the preflight book was received
2 s or more earlier by the local monotonic clock. Clock skew alone does not
trigger the re-read, because a re-read cannot fix it. The action window is the
demo book's `observed_at` + 10 s and no longer takes the scan quote's expiry;
the candidate and assessment TTLs still bound it, and the scan quote stays the
evidence price for the basis check. This loosening is demo-only and needs the
owner's explicit acceptance before merge.

An entry POST includes its exact approved stop and target as market TP/SL exits.
Dispatch authorization commits, then an ambiguous marker commits **before** one
POST. A final safety epoch, fence and plan TTL check runs after read preflight,
immediately before POST. The client never retries POST. A lost response or
restart only performs order-detail lookup by the durable client ID. An absent
order remains held; it never permits an automatic resend.

Neither acknowledgment nor planned price produces a fill or Journal trade.
Identity-checked fill history must have unique trade IDs, actual occurrence
timestamps, matching totals, sizes and prices. Fill fees are recorded in immutable
Journal event payloads and the receipt projection. Missing/mismatched protection
or an unavailable protection query preserves those real fill facts and activates
the persistent organization kill switch. This stops new risk; it does not close
or protect an already open position. Read reconciliation continues while that
kill switch is active and precedes the market scanning activation check, while
the scoped demo capability remains armed.

Protection is accepted only when pending TPSL evidence has the same durable
client ID, a TPSL ID, the correct instrument, opposite exit side, NET/cross mode,
approved trigger prices, market exit prices, adequate size and appropriate
creation time. The latest durable effect disposition is `DEMO_PROTECTED`,
`DEMO_PROTECTION_MISSING`, `DEMO_PROTECTION_UNAVAILABLE` or `DEMO_UNFILLED`.
Each immutable Journal fill event also records `demo_protection`. Unrelated
same-price protective orders do not count as protection.

Wire contracts were checked against the official
[BloFin MCP trading tools](https://github.com/blofin/blofin-mcp/blob/25d296062075329b743630cb9a969dfe09dd3a27/src/tools/trading.ts)
(order fields, contract size, order-detail and fill-history endpoints), with
response shapes cross-checked against the
[public API reference](https://github.com/Anonymous-fe/blofin-api-docs/blob/167da57aec7751207d6b53248d002435a397b234/index.md).
These are implementation references, not live venue acceptance. In particular,
attached TPSL client-ID lineage must be verified in staging before acceptance.

## Restricted scope

- SFP still returns `sfp_execution_plan_not_authorized`. Approved strategy
  lifecycle and confirmed SFP setups do not grant an executable SFP plan path.
- One execution account permits one governed ALLOW claim in its preserved
  history. Account safety-epoch locking prevents competing workers from claiming
  a second Candidate. Restart/replay of that same command is supported.
- NET, cross margin, existing leverage 1, linear USDT contracts, market entry,
  one full-size target and no runner are supported. No account setting is changed.
- Cancellation with a verified partial fill records that fill and releases only
  the unfilled reservation remainder. Ambiguous absence keeps reservations held.
- Automatic exit-fill/position-flat Journal close, funding/PnL attribution and
  automatic account reuse are not implemented. Synthetic fill and close APIs
  refuse governed demo plans. An open Journal is not a completed trade or proof
  of realized performance. Do not delete execution history to reset the gate.
- Reconciliation intentionally refuses a potentially incomplete 100-row fill
  page. An operator must resolve incomplete history, conflicts and uncertain
  submissions; a new order is never the recovery mechanism.

## Staging activation procedure

The supervising session/operator owns deployment and activation. This change
sets no infrastructure or environment values. Review the deployed changes and
the registration PR's automatic CI gate before activation.

1. Record the deployed commit, healthy migration head, current kill-switch state,
   approved selected Nested versions and scoped execution account ID. As the
   authenticated organization owner, open **Settings → Account and system** and
   select **Set up paper account**. Copy the displayed account UUID for
   `GOVERNED_BLOFIN_DEMO_ACCOUNT_ID` below. This registers an internal PAPER/NET
   identity; registration does not activate demo execution, grant permissions,
   approve a strategy, or change credentials or risk/real-trading settings.
   The authenticated API is `GET /execution/accounts/paper` for status and
   `POST /execution/accounts/paper` with JSON `{}` for explicit setup. Tenant and
   owner IDs are resolved from the access token; do not supply IDs in the body.
   Repeated/concurrent setup returns the same UUID, including when a valid
   account already exists. Creation and its `execution_account_registered`
   audit event commit together. Disabled or multiple scoped accounts return
   HTTP 409 and require operator review; do not create/reset an account or delete
   execution history to bypass the one-ALLOW restriction. The normal paper
   continuation can resolve this identity after registration, but its existing
   eligibility, risk, authorization, permissions and kill-switch checks remain.
   Confirm no demo positions or pending orders exist. Keep access to the BloFin **demo**
   account UI throughout acceptance. Verify NET/cross and existing leverage 1
   using reads. A mismatch is a blocker; the worker will not change it.
2. Use the existing Read + Trade credential set already configured for the demo
   account through the existing secret-manager binding. No additional API key
   is required. Credential values must never enter logs, command output, tickets
   or this document. Keep the allowlisted demo REST host configured.
3. On the supervised paper worker, preserve the established staging dependency,
   public market evidence and read-only Binance origin settings. Set exactly:

   ```text
   ENVIRONMENT=staging
   EXECUTION_MODE=paper
   ENABLE_REAL_TRADING=false
   EXCHANGE_MODE=paper_exchange_demo
   BLOFIN_DEMO_ENABLED=true
   BLOFIN_LIVE_EVIDENCE_DEMO_ENABLED=true
   GOVERNED_BLOFIN_DEMO_ENABLED=true
   GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID=<existing scoped organization UUID>
   GOVERNED_BLOFIN_DEMO_USER_ID=<existing scoped user UUID>
   GOVERNED_BLOFIN_DEMO_ACCOUNT_ID=<existing enabled execution account UUID>
   WATCHER_PAPER_ORGANIZATION_ID=<same organization UUID>
   WATCHER_ORCHESTRATION_ENABLED=true
   WATCHER_PAPER_STAGING_ACTIVATION=true
   PERPETUAL_EVIDENCE_SOURCE=binance_usdm
   ```

   Preserve the API's established demo sync profile independently. Leave legacy
   scanner/scheduler flags off. This execution feature grants no Telegram or
   notification activation; retain the separately reviewed notification posture.
4. Start/restart the existing `python -m app.workers.paper_worker` process only
   after the normal staging Watcher preflight clears. Verify the observed unique
   worker identity, PostgreSQL lease/fence, fresh heartbeat, live evidence and
   correct tenant pin. An arm alone is not readiness. Natural setup confirmation
   is required; do not seed a staging Candidate or bypass detection/risk.
5. Observe the first confirmed eligible Nested setup. Keep a supervising operator
   present until order, actual fill, protection and Journal linkage are proven.
   If any protection/identity/permission/freshness check fails, use the rollback
   and exposure-management procedure immediately.

## Acceptance evidence

Capture redacted evidence for one command, with no credential or financial
account dump:

1. Watcher report references an actual confirmed assessment and Candidate with
   approved compiled/version hashes. Eligibility is current and the immutable
   plan uses `BLOFIN_DEMO`, `governed-blofin-demo/v1`, the correct account,
   contract unit/rules, basis evidence, risk allowance and protective prices.
2. Authorization matches the exact revision/hash. One durable claim, receipt,
   reservation and client ID exist. Dispatch fence/safety epoch are recorded.
3. BloFin demo UI/order-detail shows that same client ID, exchange order ID,
   direction and contract quantity. Acknowledgment alone is insufficient.
4. Actual fill-history trade IDs, contract quantities, prices, timestamps and
   fees exactly correspond to the immutable execution fill facts. The sum does
   not exceed the approved quantity. No internal simulated fill is present.
5. Pending TPSL evidence proves exact linked stop/target protection as defined
   above, with actual TPSL identity and adequate quantity. The effect records
   `DEMO_PROTECTED`; an unverified child client ID is a blocker to acceptance.
6. One open Journal references the same execution command, Candidate, assessment,
   strategy version and plan revision. Its actual base size, weighted entry,
   occurrence time and fees match the venue fills. Learning attribution uses
   `paper_exchange_demo`, separated from internal paper measurements.
7. Ask the Agent to explain that execution with its recorded command identity.
   Use `Explain paper execution <command UUID>`. It reports demo status, actual
   recorded fills, the latest recorded protection observation,
   Journal/learning linkage and any restriction without inventing PnL or a close.
8. Restart the worker and repeat the read/Agent explanation. IDs, unique fill
   count and Journal count remain unchanged; no second entry POST occurs.
   Activate the scoped kill switch and verify read reconciliation remains
   available while new entries are refused. Do not clear a protection-failure
   kill switch merely to resume trading.

The supervising session records real staging results separately. Mock tests,
this document and API-key permission sync do not prove a natural demo fill.

## Rollback and existing exposure

1. Activate the organization/global kill switch through the established
   authenticated confirmed control. This immediately blocks new dispatch.
2. Inspect the actual demo position and its pending protective orders in the
   BloFin demo UI. If protection is missing or cannot be verified, the supervising
   operator must place/verify an appropriate demo protective exit or close the
   demo exposure in that UI under the acceptance authorization. Confirm the
   position is flat before removing residual protective orders. Do not assume a
   kill switch cancels entries, closes positions, or repairs protection.
3. Preserve command, client ID, reservations, fill/Journal/learning evidence and
   audit history. Continue read reconciliation while the worker demo capability
   remains armed and the kill switch prevents new risk. An unresolved order
   lookup is an operator hold; do not resend it or discard its reservation.
4. When existing exposure is verified safe/flat and evidence is preserved, stop
   the worker, set `GOVERNED_BLOFIN_DEMO_ENABLED=false`, and restore its previous
   paper profile (`EXCHANGE_MODE=paper_internal`, `BLOFIN_DEMO_ENABLED=false`).
   Restore previous Watcher arm values or keep both false. Keep
   `EXECUTION_MODE=paper` and `ENABLE_REAL_TRADING=false`. API demo read sync may
   retain its separately configured profile and the existing stored credentials.
5. Keep the kill switch active pending review. This version leaves the recorded
   demo Journal open after a manual venue exit because actual exit reconciliation
   is outside its scope. Do not use the synthetic paper-close endpoint to claim
   a realized demo result or activate a second account as a reset workaround.
