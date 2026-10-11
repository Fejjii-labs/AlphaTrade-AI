# Trusted BloFin experiment outcomes v1

Feature base: PR241 `6ca6a76201547524275266dfa22fdf12f5932b14`.
Branch: `codex/blofin-experiment-outcomes`. Agent 1 owns next-batch integration,
shared HTTP/OpenAPI/client generation and migration sequencing. PR237 and accepted
PR242 `88ce82ff620c8fd206154a2e8593d242bad83e5c` are unchanged. There is no copy of
PR242's implementation on this feature branch. Detection and configured timeframes,
TrendPulse authoring/detection, competing UI and operator settings are unchanged.

## Consumer and storage

`app.experiments.outcomes.BloFinExperimentReads` is the actual domain consumer:

- `record_sample(experiment_id, version_id, ExperimentSampleCreate)` invokes the
  existing ExperimentService with BloFinExperimentSourceResolver. Existing membership,
  lifecycle, sample-window, configuration, independent sample and cap gates apply.
- `outcome(experiment_id, version_id, variant_key, source_record_id)` returns a typed
  available/unavailable native reconciliation, including partial/missing evidence.
- `performance(experiment_id, version_id, variant_key)` revalidates **persisted admitted
  samples**, their complete proof and evidence hash; it never counts arbitrary activity.

All methods pin organization and user through the existing ExperimentRepository.
`source_record_id` is the canonical lowercase UUID string of the immutable entry
ExecutionCommand. HTTP requests cannot supply profit, quantities, UID or ownership.
[Integration example](../backend/examples/blofin_experiment_outcomes.py) exercises
sample admission and reads without collection, submission or an internal simulator.
The caller owns authentication and transaction commit/rollback. A successful sample
must be committed before ending its request; the account attribution lock lasts until
that transaction ends. Source-adapter errors remain explicit ConflictError codes.

This uses existing a10 activity accounts/facts/cursors, immutable execution and plan
records, a11 experiment versions/events/samples, and AuditLog. No migration, historical
revision, shared schema, route, generated HTTP/client artifact or runtime is changed.
There is no new collector: the existing native activity worker collects once; multiple
consumers reconcile the same stored facts. Runtime collection stays disarmed.
The foundation's [official endpoint and coverage ledger](blofin_native_activity.md#verified-official-contracts-and-coverage-limits)
records [order history](https://docs.blofin.com/index.html#get-order-history),
[trade history](https://docs.blofin.com/index.html#get-trade-history) and
[API-key identity](https://docs.blofin.com/index.html#get-api-key-info). This reader
adds no endpoint; native account behavior remains subject to the checks below.

## Ownership producer boundary

A reviewed server-side experiment executor must persist two typed attestations in
existing AuditLog storage, separately from any manual account-identity audit:

| Stored operation | Contract | Required meaning |
| --- | --- | --- |
| `experiment_native_entry_binding` | NativeEntryBinding, `experiment-native-entry/v1` | Exact org/user, experiment/version/configuration hash, sample group, account/demo UID, variant and immutable strategy hash/ID, command and plan hash, captured before entry creation in an approved running interval; `pre_entry_position_flat=true` proves the exact UID/instrument NET inventory was flat before this opening entry |
| `experiment_native_exit_lineage` | NativeExitLineage, `experiment-native-exit/v1` | Exact entry-binding hash, native entry and **all** owned exit orders, complete disjoint entry/exit fill sets; `position_lineage_verified=true` proves complete flat-to-open-to-flat native position ancestry without unrelated inventory/fills; independently verified monetary semantics when known |

Attestations have `resource_type=experiment_execution`, `resource_id=command UUID`,
`actor_type=SYSTEM`, `actor=experiment_executor`, `result=SUCCESS`, and aware actual
`created_at`/`event_at`. `redacted_metadata` contains the contract's JSON fields plus
`operation` and `evidence_hash`. `payload_hash` equals `evidence_hash`, computed with
ExperimentService's existing `semantic_hash` over the **typed model_dump()**, excluding
operation/hash. Normalize/sort ID sets with the typed model before sealing. Exact
repeated attestations converge using the earliest actual receipt; competing contents,
untrusted actors, future observations and malformed hashes fail closed. Hashes detect
corruption; they are not signatures or a substitute for the trusted server producer.
No HTTP or imported activity path writes these attestations.

The reviewed PR245 foundation supplied no executor or attestation writer. The bounded
11 October supplement adds a server-only **origin and pre-entry receipt producer**,
described below, but no experiment execution coordinator or qualified exit writer.
Before integration enables native ingestion, the integration and execution owners must
establish that the server-only
producer verifies original execution UID, canonical strategy/plan/dispatch authority,
and each exit's experiment command or protected parent lineage. It cannot attest an
ordinary manual entry/exit by instrument, time, quantity or client-order similarity.
Native activity does not provide TPSL/algo parent history; unavailable protected-exit
lineage cannot be guessed. Until those attestations exist, native samples are unavailable.
No execution, management, approval bypass or activation authority comes from this reader.

Both position assertions are strict server-only booleans, defaulting to unknown.
Missing, false or nonboolean claims fail closed, including historical attestations that
lack these fields. They are not inferred from NET mode, equal entry/exit quantities,
reduceOnly exits, zero reported entry PnL or flat current positions. The reviewed producer
must establish actual native pre-entry inventory and complete position/fill ancestry for
the same UID/instrument, withholding attribution if unrelated/manual fills, preexisting
inventory, average-cost mixing or unverified contract/PnL allocation could contaminate
the outcome. The new producer verifies original flat inventory through restricted
native GET requests; no complete closing-position producer is installed. Existing
synthetic resolver fixtures assert closing claims only to exercise the resolver.
They do not prove native position attribution or live performance.

The resolver independently verifies stored current UID/account identity using the
existing account proof, immutable strategy content hash, full canonical plan hash,
command tenant/account and ALLOW outcome, venue/instrument/contracts/configured
symbol/timeframe, command-before-binding and binding-before-entry times. A manual
plan is rejected even with a forged experiment tag. VenueSubmitEffect and immutable
receipt bind the native entry's client echo to one command; the echo alone grants
nothing. This preserves original identity audits across credential rotation: the new
read-only binding must first be reverified for the same UID. A different UID, stale
verification, identity error or unverified replacement credential withholds evidence.
Internal simulation requires a separate resolver/reader and cannot enter these totals.

## Reconciliation and time boundaries

Every native order/fill is scoped by org/demo/UID and checked against its a10 payload
hash, IDs, event milliseconds and **unchanged first_observed_at**. Timestamp conversion
uses integer milliseconds. A provider event after receipt, receipt after evaluation or
future attestation is rejected. Every performance component uses one fixed evaluation
clock. Delayed historical facts keep their original event and actual receipt times;
a read before that receipt cannot use them.

Each owned order must have its terminal native fact and exact individual fill set.
Instrument, side, NET position side, positive contract quantities/prices, creation/fill/
completion ordering and accumulated filled quantities must agree. Entry requested
quantity equals the plan; partial entry or exit fills may qualify only after a terminal
filled/canceled/partially_canceled state closes the remainder. The entry must explicitly
have reduceOnly false; a reducing or unknown entry is unavailable. Nonzero reported
closing PnL on any entry fill also refuses opening attribution rather than ignoring that
profit or loss. A zero/absent entry PnL still requires both verified position assertions.
Exits require the opposite
side and explicit reduceOnly. Total exit contracts must equal entry contracts; interleaved
entry/exit histories remain conservatively unavailable. Missing terminal facts, extra or
missing fills, unmatched quantities or incomplete closure never qualify. An empty
account, balance, positions response or exhausted page does not establish closure.

Required fill coverage has an exhausted, fresh, error-free, gap-free a10 window covering
all entry/exit events. It proves this reconciled lineage within bounded endpoint coverage;
it does **not** prove complete account history. Completed order facts are checked
individually; recurring order sweeps are not historical time coverage. Account retention,
late corrections and sweep latency remain bounded/native limitations.

The latest lifecycle event at binding and first entry must be running; entry must follow
this version's start and precede its approval expiry. Exits may arrive after pause or
expiry for a trade begun under valid authority. Fresh Validation has its own version,
hash/sample group and start. Old Exploration facts fail even if a new tag is attempted.
Independent a11 uniqueness is preserved. A transaction-level PostgreSQL advisory lock
serializes org/UID attribution across competing versions; complete native fill/exit IDs
in NativeExperimentSourceProof prevent reusing one closure for another command.

## Monetary availability and methodology

NativeActivityFact quantities stay **contracts**. Native per-fill reported closing PnL is
summed once; order-level totals are not added again. There is no price-derived or
balance-derived substitute for missing profit. Every entry/exit fee and its explicit
currency must exist and match the immutable plan's settlement currency. Observed
current instrument metadata is retained by a10 and never treated as historical
conversion proof. No quote/base conversion is inferred here.

BloFin history documentation does not establish fee currency for omitted values or
all PnL/fee sign/inclusion semantics. The attestation must explicitly provide verified
`pnl_excludes_fees=true`, `fee_convention=positive_cost|signed_cashflow` and a nonempty
`monetary_methodology_version`. Unknown semantics remain unavailable. Under
`positive_cost`, fee cost equals the signed native fee sum (negative rebates reduce cost);
under `signed_cashflow`, cost is its negation. Fee-adjusted realized PnL is native reported
closing PnL minus that explicit cost. **Funding remains null and excluded**: this is not
full net return, portfolio performance, return percentage, R multiple or drawdown.

Use existing finite base-10 Decimal contracts; floats/booleans are rejected. Native
values have at most 32 supplied digits, 18 fractional places and 31 integer places.
All arithmetic/hashing runs in an explicit 80-digit context. Monetary totals and win
rate round to 18 fractional places with ROUND_HALF_EVEN. No intermediate quantity is
rounded to force closure. Win rate uses strictly positive fee-adjusted outcomes;
breakeven outcomes are not wins. Aggregates sum only attributable, reconciled samples
in the requested version/variant/source and one currency. Any changed/unavailable
sample, incomplete closure or declared minimum shortfall keeps **all performance
totals null**. No selective available subset conceals missing outcomes. Reads have
explicit bounded budgets (100 exits/1000 fills per side; 2000 samples/account claims).

NativeOutcome retains provider/venue/demo UID, org/account/version/strategy/variant,
instrument, methodology, identity/evaluation time, original fact hashes/event/receipt
provenance, contract units, explicit currency, fees/profit, coverage/freshness and
availability/reason. Monetary-unavailable reconciliations retain the closure proof and
native facts, but source admission refuses them. NativeExperimentSourceProof is an
additive JSON proof stored in a11; its full exit/fill sets are covered by the evidence
hash. Existing shared sample HTTP responses remain unchanged.

## Published interface and next integration step

[Serialization JSON Schema](contracts/blofin_experiment_outcomes.v1.schema.json) is a
pure module contract, not a new endpoint. Reproduce or check from backend:

```sh
PYTHONPATH=src python scripts/export_blofin_experiment_outcomes.py --check
```

The HTTP facade now exposes outcome/performance reads under existing Reader tenant
RBAC. The integration owner regenerates shared API/client artifacts once on the combined
candidate. Keep the default sample route unavailable until the complete trusted
producer and its execution coordinator are reviewed and qualified. Preserve
PR242's accepted acquisition/evaluation clock correction when consolidating. No new
migration is needed. The internal simulator must use an independent source resolver.
Native connectivity/permissions/fee semantics/retention/protected parent lineage have
not been exercised against an actual account; fixtures demonstrate code, not native
performance or activation readiness. Enforced exchange egress is unavailable here.

## Bounded server producer supplement — 11 October 2026

`BloFinNativeOutcomeProducer.register_plan` persists an explicit experiment/version/
variant origin for a persisted canonical plan before any command exists. It checks the
frozen experiment, approved running interval, immutable strategy and semantic/envelope
hashes. Canonical Candidate instrument identity supplies the provider symbol used by
the authored experiment universe; instrument IDs are never parsed from client order IDs.
An existing command or another experiment/version cannot be retrospectively assigned.

`bind_entry` requires that origin to be visible in a separate committed session, the
exact ALLOW command and consumed authorization, and an undispatched CREATED effect.
The restricted GET transport must match the current read-only credential selector.
Signed UID verification brackets the account-wide position and instrument reads.
Malformed/truncated, hedged or nonzero inventory refuses a receipt. Native contract
value/type/currencies must match the immutable plan. The receipt owns one explicit
observation timestamp, even with an advancing clock. Caller-supplied truth flags are
not method inputs. Hashes detect corruption; they do not replace verified source IO.

The caller owns all transactions: commit origin before command creation, and commit
entry/position receipts before dispatch. Advisory locks and deterministic receipt IDs
converge on the original origin/binding/diagnostic across restart and concurrent calls.
Replays never contact the venue or replace the original snapshot after plan expiry.
Neither callable service is wired to an autonomous experiment executor: there is no
current production coordinator selecting an experiment origin and calling these methods.
Existing experiment start/approval boundaries and operator settings remain unchanged.

`reconcile` persists scoped diagnostics without admitting a sample or inventing an exit.
The current governed lifecycle resolution retains an isolated closing-fill sequence,
but its stored protection IDs do not establish direct native parent/child ancestry.
Its documented `fillPnl` is not independently declared gross or net of fees/funding;
native history fee currency/sign may also be unavailable. Producer-owned commands
therefore remain unavailable, even if legacy true-valued exit flags are present.
The reader clears sample proof and monetary totals for these unqualified commands.
The missing sources are a reviewed coordinator callsite, complete native protected
parent/child or canonical exit authority and independently verified fee/PnL methodology.

Installed read-only HTTP contracts:

- `GET /experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}`
  returns `NativeOutcome`; `variant_key` defaults to `baseline`.
- `GET /experiments/{experiment_id}/versions/{version_id}/performance` returns
  `ExperimentPerformance` with the same variant selector.

Both reads use stored tenant/user-scoped data. They perform no provider IO, write,
retry, order submission or activation. POST samples remains adapter-unavailable (503).
Internal simulation remains a separate source; unavailable results are never portfolio
returns or measured native strategy performance. No migration is added; a12 remains head.
