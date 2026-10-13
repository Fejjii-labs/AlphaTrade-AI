# BloFin workflow attribution and remaining provider evidence

Feature base: reviewed PR249 candidate
`df0a12d3b9c03b73ae8a1109adad66e4cb900885`. The feature owner records the
published combined revision. These results apply to the scoped working-tree
changes described here; no deployment or live account acceptance is claimed.

## Supported existing workflow

The existing governed reconciliation, canonical fill projection and lifecycle
resolution remain the execution path. No new engine, collector, order route,
permission, account setting or migration was introduced.

| Acceptance area | Implemented and focused-tested behavior |
| --- | --- |
| Native entry/exit fee provenance | Original signed fee values retain explicit provider currency, or null when omitted. Omitted currency is not replaced by USDT in the canonical governed path. Missing exit fees remain null. |
| Supported governed closure | Exact terminal orders, complete quantities/fills, effective protection checks and account-wide flat/idle checks still gate the existing close. Monetary absence does not manufacture money or prevent that supported close from releasing only its already-owned exposure. Existing conservative daily loss/trade-count accounting remains. |
| Immutable protection and money evidence | First-entry protection IDs survive in immutable FILL events and `recorded_entry_protection_ids`. A closing order's returned `tpslId` is retained as a raw pointer. No parent/child or position-lineage assertion is inferred from it. Raw `fillPnl` is retained separately; gross/net PnL, funding and money semantics remain null. |
| Reported fee sum and execution reads | Same-currency fee sums use immutable entry receipts and native exit facts, not editable Journal fee totals. Unknown fee currency/amount yields null Journal and canonical governed execution-view fees. Native funding is always null. Internal simulation retains its defined zero values; historical nonnullable storage defaults are unchanged. |
| Precise attribution blockers | Existing producer reconciliation persists v2 diagnostics listing missing sources and exposes them through existing outcome limitations. Legacy v1 receipt hashes remain readable. A raw pointer, closing numbers or legacy true flags cannot supply native sample eligibility. |
| Manual monetary provenance | The same native entry parser now retains actual currencies, including foreign currencies. Manual receipts preserve signed raw fees/currency; omitted, mixed or unmatched settlement currency withholds totals. Manual gross/net PnL, funding and fee convention remain null; raw `fillPnl` remains separate. Original manual audit bytes/hashes and fill identities are preserved across additive currency observations. |

The reported signed fee sum is not a normalized cost or net return. The verified
fee convention still required by `BloFinExperimentSourceResolver` is unchanged.
Producer-owned experiment outcome proof and performance totals stay unavailable
until complete closing ancestry and provider monetary methodology are qualified.
The native sample HTTP adapter remains disarmed.

## Exact missing native sources

The existing producer reports these independent requirements:

- Exact exit execution-command ownership, or the venue-confirmed entry → protection
  → generated closing-order edge. Echoed client IDs, triggers, matching quantities,
  a raw `tpslId`, or account flatness alone do not establish that edge.
- Complete native flat-to-open-to-flat position/fill lineage for the original
  verified account UID and instrument, excluding unrelated/manual inventory/fills.
- Explicit historical entry and exit fee currencies, every exit fee and every
  closing `fillPnl`. Diagnostics distinguish absent fields from fields actually
  retained in the same-currency closing evidence.
- A venue-owned or independently verified native fee sign convention, and the
  exact fee/funding inclusion basis of `fillPnl`. The producer does not attest
  `pnl_excludes_fees`, a fee convention or a methodology version from examples.

Missing entry receipts, missing complete closing receipts and corrupt lifecycle
evidence remain separate reason codes. No monetary zeros, price-derived profit,
balance-derived profit, replenishment or artificial exit attestation substitutes
for the missing evidence.

The manual supplement removes the previous USDT defaults from native entry/exit
fee evidence and removes the prior `fillPnl` → gross PnL assumption. New entry
audits use `manual-demo-native-fee/v2` to distinguish actual native currency from
the old assumed field. Current history refuses legacy assumed currency totals
until an actual same-order read appends source evidence. Legacy closing facts can
receive an additive `manual_demo_exit_fill_currency_evidence` receipt; every
consumer deduplicates the original and supplement by the exact fill identity.
Original facts/hashes are never rewritten. Contradictory later currency observations
still fail closed. Manual receipts remain excluded from experiment ownership.

Manual account and Agent reads retain the reported closing PnL separately and
name its unverified fee/funding basis. They do not label it gross PnL, infer its
currency, normalize fee cost or calculate net profit. Proven closure and explicit
owned-exposure release can proceed with missing monetary fields; original risk,
account-claim and global-safety checks remain.

## Actual access check on 12 October 2026

The selected executor's `/etc/codex/network-policy.json` lists restricted outbound
hosts; `docs.blofin.com` and `demo-trading-openapi.blofin.com` are absent. Managed
environment observations are current, but their reported network state and
configured credential readiness are `unknown`; they are not an exchange grant.

One non-secret runtime check used actual application `Settings()` and the existing
`get_readonly_client(activity_history=True)` gate. Dedicated read-only API key,
secret and passphrase presence were all false. The gate refused with
`Dedicated BloFin read-only sync credentials are missing.` Runtime posture was
paper/internal, real trading false and read-only sync false. No settings changed,
secret value was printed, or exchange request/order was sent.

An authorized GitHub repository search returned public documentation mirrors;
it supplied no venue-confirmed monetary or generated-child contract. Reuse the
prior pinned mirror/CCXT review and official HTTP 403 receipt in
[the existing outcomes contract](blofin_experiment_outcomes_contract.md#exact-remaining-native-source-requirements).
They remain source leads, not fresh official or actual-account evidence. No denied
official endpoint was repeatedly retried or bypassed.

Next executable provider step: in an already authorized read-only demo environment,
collect a sanitized, exact-UID closed round-trip receipt containing entry,
protection, generated-child orders and complete individual fills; obtain the
venue's explicit historical fee currency/sign and `fillPnl` inclusion declaration.
Use the existing collector/reconciliation and producer contracts to qualify those
sources. The currently missing runtime access belongs in the managed environment's
dedicated `BLOFIN_READONLY_API_KEY`, `BLOFIN_READONLY_API_SECRET` and
`BLOFIN_READONLY_API_PASSPHRASE` bindings and approved outbound-host policy, never
in chat. This evidence operation does not require order submission or a change to
operator trading permissions.

## Focused local evidence

All provider IO below is synthetic `httpx.MockTransport`; PostgreSQL operations
use local disposable PostgreSQL 17. The existing governed fixtures reset only the
separate disposable database `alphatrade_blofin_workflow_tests`; native producer
fixtures use UUID-named schemas in `alphatrade_test`. No hosted database is used.

From `backend`, with the shared `.venv` and explicit loopback tool access:

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_blofin_workflow_tests \
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_phase1_slices_7_9_execution_protocol.py \
  tests/test_governed_demo_lifecycle.py \
  tests/test_governed_blofin_demo.py \
  tests/test_blofin_native_producer_postgres.py::test_missing_exit_remains_unavailable_and_diagnostics_deduplicate_after_restart \
  tests/test_blofin_native_producer_postgres.py::test_existing_v1_diagnostic_receipt_hash_remains_readable \
  tests/test_blofin_native_producer_postgres.py::test_raw_closing_pointer_and_numbers_cannot_supply_missing_provider_contracts
# 78 passed, 0 failed/skipped, 68.19s.

PHASE1_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_blofin_workflow_tests \
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_blofin_native_producer_postgres.py \
  tests/test_blofin_experiment_outcomes_postgres.py::test_unknown_money_is_unavailable_and_never_zero \
  tests/test_blofin_experiment_outcomes_postgres.py::test_signed_cashflow_fees_and_rebates_are_explicit
# 39 passed, 0 failed/skipped, 43.14s. Three producer cases overlap the first command.
```

These canonical contribution selections cover **114 distinct focused cases**,
before the manual supplement. The new cases cover
missing entry/exit currency, mismatched exit currency, omitted exit fee, signed
fees/raw protection pointers, editable Journal fee rejection, old diagnostic-hash
compatibility and precise unsupported-contract blockers. Strict mypy on nine changed
source files, changed-file Ruff lint/format and `git diff --check` passed. Shared
OpenAPI/client regeneration belongs to the feature owner after integration of the
nullable execution and manual view changes.

The first repair selection had 18 passed/2 failed: an attempted null write exposed
legacy nonnullable projection storage, and a fixture wrongly expected a protection
ID from the now-empty closing pending-order response. The storage schema remains
unchanged; nullable native reads and original immutable entry protection receipts
address those defects. No thresholds, timeouts, skips or risk controls were weakened.
An earlier 5-case compatibility run passed the original three governed close variants
and two manual signed-fee regressions; it is not additional final-suite coverage.

### Manual supplement and corrected shared parser

Same isolated loopback database and synthetic provider boundary as above:

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_blofin_workflow_tests \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_manual_blofin_demo.py \
  tests/test_manual_demo_reconciliation.py \
  tests/test_manual_demo_lifecycle_workflow.py \
  tests/test_blofin_presentation_evidence.py
# 157 passed, 1 failed, no skips, 141.37s before the known-currency fixture correction.

PHASE1_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_blofin_workflow_tests \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_governed_demo_exit_evidence.py \
  tests/test_manual_demo_reconciliation.py::test_native_partial_fills_smaller_than_submission_lot_converge_without_duplication \
  tests/test_manual_demo_reconciliation.py::test_unknown_entry_settlement_fee_preserves_raw_signed_fee_and_currency \
  tests/test_manual_demo_reconciliation.py::test_legacy_assumed_entry_currency_is_unavailable_until_actual_refresh \
  tests/test_manual_demo_lifecycle_workflow.py::test_unknown_manual_fee_money_retains_native_facts_and_proven_owned_release \
  tests/test_manual_demo_lifecycle_workflow.py::test_legacy_exit_currency_receipt_is_additive_and_never_counts_fill_twice \
  tests/test_governed_demo_lifecycle.py::test_unknown_native_fee_money_does_not_invent_totals_or_block_proven_close \
  tests/test_blofin_presentation_evidence.py::test_account_performance_uses_exact_closed_lifecycle_and_retains_unknown_funding \
  tests/test_blofin_presentation_evidence.py::test_manual_agent_has_one_concise_reply_and_separate_exact_evidence
# 46 passed, 0 failed/skipped, 23.96s, on the final source and corrected fixture.

PHASE1_POSTGRES_URL=postgresql+psycopg://agent@127.0.0.1:55439/alphatrade_blofin_workflow_tests \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_manual_demo_reconciliation.py::test_native_entry_currency_change_cannot_overwrite_original_fee_receipt \
  tests/test_manual_demo_lifecycle_workflow.py::test_native_exit_currency_change_cannot_overwrite_original_close_fact
# 2 passed, 0 failed/skipped, 2.85s.
```

The broad manual run's sole failure was a fixture supplying USDT currency for its
first partial fill, then omitting it on replay while expecting known unchanged
fees. The new immutable source guard correctly refused that contradiction. Its
known-currency replay fixture now supplies the same explicit synthetic currency
on both snapshots, and the final focused correction passes. The two additional
negative cases verify that actual entry/exit currency contradictions preserve
original receipts and do not overwrite monetary fields or create extra orders.
The direct governed-exit module retains all order, identity, quantity, chronology
and account-state rejections while treating foreign/missing fee currency as raw
evidence with unavailable money. No identity or strategy threshold was loosened.

The final 48-case supplement selection includes the previously failing case,
all new unknown/foreign/absent-fee and legacy-receipt scenarios, supported close/
release, immutable conflict refusal and account/Agent presentation. Earlier
unchanged green manual cases are reused rather than represented as a new fully
rerun suite. Ruff lint/format on 12 supplement-related files, strict mypy on seven
source files and `git diff --check` passed. Provider IO remains synthetic, and
actual native fee sign, PnL inclusion semantics and experiment closing lineage
remain the independent provider blockers above.

The feature owner independently reran the combined48-case final supplement
against the frozen current source: **48 passed, no skipped/failed**,23.46s.

No complete backend CI, merge, deployment, migration, native sample admission,
provider activation, Telegram delivery or financial action was performed. Actual
native exit attribution and performance acceptance remain blocked by the precise
provider/access requirements above; independent feature work can continue.
