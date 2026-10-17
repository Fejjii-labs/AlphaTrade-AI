# Autonomous BloFin demo execution — review and supervised acceptance

This implementation starts from reviewed PR249 candidate
`de5c010c7c85dfe5cde1ec8fabf60b6262500fe0` (tree
`2ebed239ecaeacbe32c4a4d50a1e8459c4ef4122`). Implementation source commit: `73765026d1b39e3564005a7f16deac838edc4f00`, source/test
tree `0b9051f8f4305de88843ae4a695e3111194eb73e`.

The controlling continuation was read
from `cursor/experiments-market-handoff:HANDOFF.md`, updated 13 October 2026.
PR249 subsequently advanced to `3b058b2c02e487cd8ebe5844c2bca581b021f0d4`
with only a release workflow/plugin-import fixture fix. That disjoint change is
not copied into this branch. The release/CI continuation stays separate. Execution remains disarmed; this work
has not changed runtime/account settings, deployed anything, or contacted BloFin.

## Kill switch and open demo positions

The kill switch stops new entries only. It does not close an open BloFin demo
position, cancel a resting protective order, or repair a missing stop. After it
is on, an operator closes the open demo position by hand on the BloFin demo
account, confirms the account is flat, and only then disarms or reviews the
remaining orders. Real-money trading stays blocked. Neither autonomous flag is
turned on by this note.

## Implemented path

The existing Watcher target, compiled strategy version and confirmed Candidate
remain the setup authority. For an already started native experiment, selection
requires the exact owner, pinned execution account, strategy, frozen variant
strategy-version UUID, symbol and timeframe. Exactly one currently running,
unexpired match is required. Paused, expired, completed and ambiguous enrollment
cannot fall through to an ordinary unbounded entry. A draft or start alone does
not activate the worker or arm execution. Exploration/Validation retain their
frozen versions and independent sample groups. No strategy-similarity attribution
or new HTTP execution/sample authority was introduced. The native adapter retains
its existing supported compiled first-slice/Nested execution scope. SFP keeps its
execution-plan refusal; TrendPulse screening/simulation is not promoted into
native execution by this change.

The canonical plan, approval, idempotency binding, account safety epoch,
RiskReservation and fenced dispatcher remain authoritative. The claim now also
checks the sealed experiment's account-wide trade, daily/weekly loss, drawdown,
position and exposure bounds before charging capacity. Pending and uncertain
commands count before there are fills or samples. Filled maximum-loss allocations
remain conservatively charged; neither profits nor unknown fee/funding amounts
replenish them. History queries have explicit budgets and refuse on exhaustion.
Reservation, safety-epoch, idempotency and fill-projection mutation reads refresh
cached ORM objects while holding their existing PostgreSQL locks. A long-lived
worker therefore observes another session's committed fills/capacity before its
next conversion or claim. A two-session test deliberately retains all four stale
objects across commit and verifies the cumulative quantities, weighted price,
notional and once-only slot conversion.

The generic account ledger can remain stricter than the experiment limits and
continues to retain consumed trade/loss allocations; no automatic daily reset or
risk-setting mutation was added.

Reservations use the upper authorized executable price. One native entry POST
still attaches the approved stop and target. The execution connection must report
the experiment's verified native UID before entry preflight proceeds. Lost replies
and process restarts reconcile the durable client order ID; they cannot create a
second native send. Unique fills cannot exceed the authorized remaining quantity.
A verified terminal zero-fill order releases only its unused reservation and no
longer permanently blocks the account. Uncertain orders and filled positions keep
capacity until authoritative reconciliation.

Order/fill/protection reads remain strict and bounded. Unexpected instrument,
NET direction, duplicate inventory or position quantity beyond owned fills enters
an operator hold while preserving actual entry facts. A partial exit keeps
capacity charged. Closure still requires terminal entry, exact closing fills,
account-wide flat inventory and no pending orders/protection, checked again after
history reads. Exit reads verify connection UID before and after the read sequence.

## Admission and statistics

A server-owned closure receipt binds the committed plan origin and pre-entry
position to the governed close, exact native UID, immutable entry and exit fills,
Journal events, native activity hashes and protective parent/child pointers.
Effective protection must have been recorded for this entry; each TP/SL child
must carry that exact parent ID. A normal/manual venue close can resolve exposure
but cannot become an experiment-owned protective sample.

Complete, fresh **account-wide** mirrored fill coverage must span pre-entry through
the final flat observation. Foreign activity, missing/overlapping identities,
tampered facts, changed receipts, stale coverage or absent protective ancestry
refuse admission. Native facts are compared with execution/Journal/close receipts,
including raw signed fees, explicit currencies and reported `fillPnl`. Ordinary
manual BloFin mirroring remains intact. Simulated samples use their existing,
separate source/account scope and cannot enter native statistics.

The existing experiment root/version locks and native attribution lock serialize
sample admission. Replay returns the existing sample. The worker also retries
stored admission after restart or late activity arrival, after the close and risk
release have been committed. Read-only HTTP callers cannot submit lineage or money.

`price_pnl_excluding_fees_and_funding`, `price_pnl_win_rate`,
`price_pnl_sample_count`, `price_pnl_currency` and `price_pnl_methodology` expose
statistics only for sufficient verified closures. The methodology is
`verified-linear-fill-prices/v1`:

- long: `(sum(exit contracts × exit price) − sum(entry contracts × entry price)) × base contract multiplier`;
- short: the negative of that difference;
- decimal arithmetic with 18-place half-even output, from verified native fills and the original linear contract receipt.

This is observed execution price PnL, not a simulated result or a normalization of
BloFin `fillPnl`. Fees and funding are explicitly excluded. Raw signed native fees
and `fillPnl` remain unchanged. Fee-adjusted/native monetary `status`, `fee_cost`,
`win_rate` and `realized_pnl_after_fees_excluding_funding` remain unavailable for
producer-owned trades without independently verified monetary semantics. Missing
historical currency never becomes inferred USDT. A verified closure/sample count
can therefore coexist with unavailable fee-adjusted performance. No account-wide
history completeness or net-after-funding result is claimed.

## Fixed acceptance checklist

1. Serialized account reservation bounds include pending/uncertain and prior filled native activity.
2. Idempotent entry, committed origin, UID ownership and dispatch fences prevent duplicate/foreign sends.
3. Attached protection and exact effective parent/child lineage govern native sample eligibility.
4. Order, position and fill reconciliation preserves facts, releases only proven capacity and refuses inconsistent inventory.
5. Verified owned closure admission converges across restart/concurrency and produces explicitly labeled price statistics.
6. Manual native mirroring, tenant isolation, simulated source separation and disarmed defaults remain intact.

Evidence levels are mocked IO + disposable PostgreSQL, not deployed or supervised
venue acceptance. Exact final commands/results and tested source revision follow. All code/test/
generated files are hashed in `evidence/autonomous-blofin-demo/source-manifest.json`.

## Remaining supervised demo acceptance

These steps require a separately authorized supervised session; none were run here.

1. Review the exact PR head and confirm the existing release candidate/integration
   process separately. Keep execution disarmed while verifying owner membership,
   sealed Exploration/Validation version, approved compiled Watcher enrollment,
   account registration, matching execution/read-only UID, read/trade-only demo
   key scope, existing NET/cross/1x venue posture, fresh depth and account inventory.
   Do not change account mode/leverage as part of a readiness probe.
2. With separate explicit demo activation authority, run one minimum-sized bounded
   owned entry. Retain plan/approval hashes, command/reservation/client ID,
   committed origin/pre-position, send fence, actual order and fill receipts, and
   pending exact stop/target evidence. A lost acknowledgment must trigger reads,
   never a second POST. Confirm kill-switch/expiry/pause refusal of new entries.
3. Supervise an actual protective exit. Capture the native entry TPSL identity,
   effective TPSL history and child order parent pointers. Confirm all fill
   quantities, original currency/fee fields, final flat account and absent pending
   orders. If BloFin omits an edge, keep sample admission unavailable and retain
   the diagnostic; matching prices/side/client-ID similarity cannot replace it.
4. Allow the existing read-only activity worker to cover the entire owned interval.
   Restart reconciliation and verify exactly one sample, correct independent
   version/sample group, and the derived price statistics against native receipts.
   Check missing pages, manual intervention, partial fills/exits and another
   instrument's activity remain holds and visible native mirror activity.
5. Establish independently verified fee-sign, `fillPnl` fee/funding basis and
   historical fee-currency evidence before claiming fee-adjusted native results.
   Funding collection remains absent. Do not clear these limitations by inventing
   gross/net conventions or treating unknown fields as zero.
6. Disarm after the supervised observation and follow the existing operator
   rollback checklist in `governed_blofin_demo_execution.md`. A kill switch does
   not close positions or repair missing protection. Review actual demo inventory
   and protective orders before removing any residual orders. Release/deployment
   and the single authorized full CI gate remain owned by the release chat.

## Final focused evidence

All final batches used implementation source `73765026d1b39e3564005a7f16deac838edc4f00`.
The publication checkpoint adds documentation and retained evidence only. Both
backend batches exited 0; **235 distinct tests passed, no skips**. No full CI ran.

Disposable PostgreSQL 17.11 ran in `postgres:17-alpine` on loopback port 55432.
Fixture schemas were created/dropped only in that disposable server. The container
was removed after verification. No deployed database or supplied DATABASE_URL was
used. Venue reads and mutations stayed inside `httpx.MockTransport`.

From `backend/`, the command-scoped fixture variables were:

```sh
PHASE1_POSTGRES_URL=postgresql+psycopg://alphatrade:disposable@127.0.0.1:55432/alphatrade_test
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade:disposable@127.0.0.1:55432/alphatrade_test
BLOFIN_ACTIVITY_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade:disposable@127.0.0.1:55432/alphatrade_test
```

All three were supplied to the final remaining-regression batch. The native/lock
batch used the first two. They select disposable fixtures, not application settings.

```sh
.venv/bin/pytest tests/test_phase1_slice10_postgres_concurrency.py tests/test_autonomous_blofin_demo_postgres.py tests/test_governed_demo_lifecycle.py --tb=short -x
# exit 0: 54 passed in 246.12 seconds

.venv/bin/pytest tests/test_governed_demo_exit_evidence.py tests/test_governed_blofin_demo.py tests/test_governed_experiment_context_postgres.py tests/test_governed_demo_readiness.py tests/test_manual_demo_reconciliation.py tests/test_blofin_activity_postgres.py --tb=short -x
# exit 0: 181 passed in 403.82 seconds

.venv/bin/mypy --follow-imports=silent src/app/experiments/native_closure.py src/app/experiments/execution_risk.py src/app/experiments/execution_selection.py src/app/experiments/native_producer.py src/app/experiments/outcomes.py src/app/experiments/outcome_contract.py src/app/repositories/execution_protocol.py src/app/services/demo_lifecycle_resolution.py
# exit 0: no issues in these 8 files; this is not a whole-repository typing claim
```

Changed Python Ruff lint and format checks exited 0 (20 files); `git diff --check`
exited 0. From `frontend/`, `UV_CACHE_DIR=/tmp/alphatrade-uv-cache npm run api:check`,
`npx vitest run src/lib/api/native-outcomes.test.ts` (5 passed), and
`npm run typecheck` all exited 0. The frozen backend development dependencies and
frontend lockfile were installed without dependency/source changes.

Retained receipts: `evidence/autonomous-blofin-demo/`. `SHA256SUMS` covers each
receipt and source manifest. The six implementation acceptance items above are
6/6 at mocked/PostgreSQL evidence level; deployment and supervised venue acceptance
remain unperformed. Default BloFin/governed/real-trading arms remain false and the
configuration, voice source, `.ai` rules and `.github` workflows are unchanged.
