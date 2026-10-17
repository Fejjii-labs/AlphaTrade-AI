# BloFin experiment resolver/outcome focused verification

Base: PR241 `6ca6a76201547524275266dfa22fdf12f5932b14`.
Branch: `codex/blofin-experiment-outcomes`. Exact published feature SHA is recorded in
the PR and dedicated handoff; this ledger is included in that commit.

The focused PostgreSQL fixture creates/drops only UUID-named schemas on disposable
local PostgreSQL 17.11. Native account verification reuses the existing MockTransport
manual-account fixture; new canonical commands, manifests and native facts are stored
synthetic fixtures. No actual exchange connection, order, balance change or activation.
All new cases execute on PostgreSQL, including existing a11 append-only guards and
account attribution locking. No SQLite substitution or skipped PostgreSQL evidence.

Original PR245 revision `93af3b27e2a5002b74e3dc38499d081d22dfe772` affected selection:
**179 passed in 54.29s, no skips**, including **71 new
PostgreSQL cases**. Selection covers the resolver/outcome and actual domain consumer,
existing domain/native identity/API/concurrency/migration, and native activity provider/
durability regressions. Full backend CI/release acceptance, deployment and runtime
activation are outside this task.

Run from `backend` with its development environment installed. Both opt-in PostgreSQL
URLs point to the disposable fixture database; schema isolation is provided by tests:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://experiment_fixture@127.0.0.1:55439/experiment_outcomes_test \
BLOFIN_ACTIVITY_TEST_POSTGRES_URL=postgresql+psycopg://experiment_fixture@127.0.0.1:55439/experiment_outcomes_test \
python -m pytest \
  tests/test_blofin_experiment_outcomes_postgres.py \
  tests/test_experiment_domain.py tests/test_experiment_native_identity.py \
  tests/test_experiment_concurrency.py tests/test_experiment_api.py \
  tests/test_experiment_migration.py tests/test_blofin_activity_postgres.py \
  tests/test_blofin_activity_provider.py --tb=short --show-capture=no
```

Changed Python files passed Ruff lint/format (7 files); scoped strict mypy passed on
the 6 new source/example/exporter files. Pure module serialization export `--check`
passed. The existing offline OpenAPI exporter produced bytes identical to the PR241
shared client artifact. No shared generator input/artifact or migration changed.
`git diff --check` passed.

Original PR245 contract SHA256:

- Module JSON Schema: `e568e0fc772d6b381c263485c3a56ebdc59b2bcb770eda68e17d8c9a6fc748ce`.
- Unchanged shared OpenAPI: `6f7f9ece7248442b53f9e3ebd84277053db284d1e52cf97a18a25ac5c4dcf0e3`.

Important capabilities exercised: exact attribution IDs/hashes, manual/echo rejection,
partial terminal entry and multiple exits, fee sign/rebates and unknown fee currency/
profit/methodology, duplicate pages/attestations, conflicting fill IDs, delayed receipts,
future-time refusal, coverage gaps/staleness, same-UID credential rotation, tenant/source
isolation, exclusive exit attribution, PostgreSQL serialization, fresh Validation start,
changed sample revalidation, insufficient sample nulls, multi-outcome Decimal aggregation,
18-place rounded win rate, large monetary values and caller-context independence.

The module-level integration example calls actual ExperimentService sample admission
and reads reconciled native performance. It performs no second collection or IO. Shared
HTTP wiring/generated contracts remain Agent 1's integration step. No trusted runtime
attestation producer is installed; undocumented native monetary semantics and protected
exit ancestry remain explicitly unavailable. Actual signed demo GET connectivity,
permissions, rotation, endpoint coverage/retention, fees/PnL basis and protected parent
lineage are unverified under enforced egress limits. No premium provider or paid data.

## Opening-entry and position-lineage correction

Read-only probes against the original PR245 resolver found that a reducing or unknown
entry and an entry fill reporting nonzero closing PnL all returned available. The resolver
now requires explicit reduceOnly false, rejects nonzero entry closing PnL, and requires
strict, affirmative server attestations of pre-entry flat inventory and complete native
flat-to-open-to-flat position ancestry. Unknown, absent, false or nonboolean claims remain
unavailable. No HTTP resolver installation, attestation producer, exchange action or runtime
activation was added. Fixture assertions are synthetic and establish no native performance.

Focused corrected selection: **87 passed in 70.59s, zero skips**, comprising all 71 existing
PostgreSQL outcome cases and 16 new opening/position regression cases. It used UUID-named
schemas on disposable local PostgreSQL 17.11 and executed:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src python -m pytest -o addopts='' \
  tests/test_blofin_experiment_outcomes_postgres.py -q -rs
```

All 71 existing cases, including partial terminal entries, multiple exits, monetary math,
original receipts, exclusive attribution and PostgreSQL locking, remain in this selection.
No full suite or CI dispatch was run. Original PR245's broader 179-case evidence covers its
original revision; it is not acceptance of this correction or the combined integration SHA.

Changed-file Ruff lint/format passed for the three edited Python files. Scoped strict mypy
passed for the two edited source files. Pure module serialization export/check passed;
corrected schema SHA256:
`0d2ca13605f9542c29e0232c6edf29883186b2cc8a95b0015386b0457c122870`.
Shared HTTP artifacts and historical migrations were not edited. `git diff --check` passed.
The integration handoff records the exact corrective and combined commits.

Remaining native prerequisite: a reviewed server-owned producer must prove exact native
pre-entry inventory and complete position/exit ancestry without manual inventory or cost
basis mixing, and independently verify contract/PnL/fee allocation. Native account checks,
exchange permissions/coverage and monetary semantics remain unverified. HTTP sample admission
must stay unavailable and Strategies performance must stay explicit about unavailable data.

## Bounded origin/entry producer and read facade — 11 October 2026

Base `42e30b866f44e07a83c637764595ebc776c9ba97` on reviewed PR247 `0d3c79ed`.
The integration checkpoint records the corrective producer commit and combined SHA.
No historical migration, generated API artifact or runtime setting was edited by this
worker. a12 remains the sole head. Shared HTTP/client generation belongs to integration.

Final focused command, with an isolated random schema per producer case:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_blofin_native_producer_postgres.py \
  tests/test_experiment_api.py::test_api_requires_auth_roles_exact_revision_and_tenant_scope
```

**32 passed, 0 failed, 0 skipped, 88.50s**: 31 new producer/persistence/HTTP cases and
one existing HTTP authorization/admission case. The advancing-clock regression proves
origin and position timestamps are owned once and survive fresh-session replay. Other
cases prove canonical pre-command ownership, committed-origin requirement, rejection
of historical/manual/foreign/simulation identities, signed UID changes, stale identity,
nonflat/hedged/malformed inventory, native contract mismatch, refusal after dispatch,
caller truth-flag rejection and concurrent origin/binding/diagnostic deduplication.
Same-UID receipt replay after plan expiry performs no new provider IO. Read-only GET
facades preserve ownership and unavailable money; POST native admission remains 503.

Affected resolver/producer compatibility run:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_blofin_native_producer_postgres.py \
  tests/test_blofin_experiment_outcomes_postgres.py
```

**116 passed, 0 failed, 0 skipped, 157.69s** at the earlier producer repair state:
87 unchanged resolver cases and 29 producer cases. The final 32-case run covers the
completed producer guards and two added restart/concurrency cases. There are **119
distinct focused passing cases** across these runs; overlapping executions are not
additional cases. These results are local synthetic qualification, not live exchange
performance, current CI or a complete release gate.

Early repair receipts: collection initially referenced a nonexistent SENT enum;
fixture setup reversed TenantContext's positional identities and leaked its session
on construction failure. Correct enum/keyword identities and unconditional teardown
fixed those fixture errors. The first real canonical case then exposed the genuine
canonical-instrument-ID versus provider-symbol mismatch; producer and resolver now
validate the persisted Candidate identity mapping. Source review found independent
wall-clock reads made receipt timestamps disagree; explicit receipt time ownership
and the advancing-clock PostgreSQL regression corrected that defect. Strict mypy
found two optional-type guards, both repaired. No assertions or timeouts were weakened.

Changed-file Ruff lint and format, `git diff --check` and strict mypy on producer,
resolver, reader and experiment routes passed. The scoped HTTP OpenAPI snapshot is
updated/generated and checked by the integration owner after the combined backend.
No full backend/combined CI was dispatched; no external order or operator mutation.

Progress N1–N5: **3/5 accepted locally (N3–N5)**. N1 is partial: the server origin and
pre-entry services are implemented/tested, but no production experiment coordinator
calls register_plan/bind_entry/reconcile. N2 remains unmet: the existing governed close
resolution lacks complete direct protected parent/child evidence and independently
verified fee/PnL methodology. Its isolated native closing facts, true-valued legacy
flags or client-order resemblance cannot supply those missing sources. Newly registered
producer-owned outcomes keep proof and monetary totals unavailable. No native sample
HTTP adapter, autonomous executor or provider is activated by this contribution.

## Existing governed coordinator connection — 11 October 2026

Isolated branch `codex/blofin-coordinator-evidence`, base reviewed PR248
`4e5dc16782c8f8a30891179de0a886a44c3f0f37`. The integration owner records this
contribution's exact corrective and integrated revisions. No migration, shared client,
HTTP admission, scheduler, production setting or external exchange order was changed.
The previous 119 distinct foundation cases are reused only for their unchanged source;
they are not a new combined CI or full release acceptance result.

Executed focused results, zero skips in every selection:

| Selection | Result | Revision coverage |
| --- | --- | --- |
| New `test_governed_experiment_context_postgres.py` | **15 passed, 83.20s** | Coordinator origin/claim/binding order, original replay, concurrent one-send, cached-effect refresh, armed real entry, disarmed entry, pause/expiry/fence, frozen risk and tenant guards; after the dispatch cache repair, before the final fresh-membership addition |
| Existing `test_phase1_slices_7_9_execution_protocol.py` | **38 passed, 14.96s** | Injected claim timestamps and refreshed locked effect; those source files were unchanged afterward |
| Existing governed worker success, preflight kill and preflight fence | **3 passed, 22.57s** | Actual ordinary unowned Watcher → canonical claim → MockTransport → persisted Journal/restart/protection paths, after the dispatch cache repair |
| New retained-cache membership downgrade cases | **2 passed, 13.01s** | Final guard source: another-session Viewer downgrade refuses at explicit entry and direct dispatch before new origin, command or provider reads |
| Permitted-role armed real entry after membership repair | **1 passed, 5.06s** | Final guard source allows the existing authorized path, preserves original committed receipts and performs zero POSTs |

These selections cover **58
distinct focused cases** (17 new PostgreSQL + 41 existing compatibility cases); repeated
public-entry executions are not additional cases. PostgreSQL fixtures use UUID-named
schemas on the owner's disposable PostgreSQL 17 database. Existing ordinary Watcher
helpers drop `public`, so the three ordinary cases ran against a separately generated
disposable database in the same local cluster, then that database was dropped. No
second server, shared schema reset or external request was used.

New PostgreSQL command, from `backend` with explicit local-loopback tool access:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade@127.0.0.1:55439/alphatrade_test \
PYTHONPATH=src:. .venv/bin/pytest -o addopts='' -q -rs \
  tests/test_governed_experiment_context_postgres.py
```

The final authority repair ran only
`::test_cached_membership_downgrade_refuses_before_new_execution_evidence` (both
parameters) and `::test_explicit_internal_entry_reaches_real_governed_claim_and_committed_receipts`.
It did not rerun unchanged green selections. The armed case uses the actual
FirstSliceEvidenceAssembler against existing synthetic REST fixtures, real
`continue_experiment_setup` → `continue_confirmed_setup`, canonical persisted Candidate/
plan reuse, actual approval/claim and existing dispatcher fences. An independent
session sees committed original origin → command → verified position/entry receipts
before the mocked submit callback. The callback invokes the real final gate and
simulates a lost reply without making a POST. Fresh-session replay retains the original
command/key and performs no second position collection or automatic send.

Repair receipts are retained truthfully. Early fixture runs hit the real capacity
guard because the fixture multiplier made its contract quantity oversized; fixing the
initial fixture's exact native contract multiplier preserved the guard. An advancing
injected clock exposed new ALLOW commands taking model-default wall time; new command
timestamps now use the claim clock. A late expiry correctly stopped dispatch, but a
subsequent stale-account diagnostic masked the retained uncertain response; only that
unavailable identity case now rolls back the diagnostic read transaction. The first
13-case consolidated run had **12 passed, 1 failed, 84.29s**: the concurrent one-send
assertion observed two attempts for one command. SELECT FOR UPDATE had retained stale
effect identity-map state; refreshing that state while locked repaired the race. Its
assertion and independent concurrency remain intact. New fixture diagnostics also caught
a wrong hash-helper call and a quote aging across two experiment starts; existing fresh
REST fixtures fixed input freshness without changing production gates or timeouts.
A local run without loopback permission failed during PostgreSQL connection setup;
the same existing server worked with the authorized tool permission, without a restart.

Independent source review then found stale cached membership across a committed
Owner/Trader → Viewer downgrade. Fresh membership reads were confined to the new
context/registered-dispatch guards; PostgreSQL proves refusal before new evidence and
retains the existing final expiry/fence revalidation. No assertion, refusal, temporal
guard or lease timeout was weakened. Changed-file Ruff lint/format passed for all seven
Python files; scoped strict mypy passed for the five affected source files. `git diff
--check` passed. No full CI was dispatched.

N1 is now an implemented/tested explicit connection to the existing governed
coordinator, conditional on pre-existing arm/credential/approval/risk gates. It does
not choose a running experiment or activate a scheduler/HTTP route. N3–N5 retain their
accepted foundation behavior. **N1, N3–N5: 4/5 accepted locally; N2 remains unmet.**
No native performance, full backend acceptance, deployed or live-accepted result is
claimed. N2's exact absent venue-owned parent/child and historical money declarations,
the HTTP 403 official-source limit and corroborating mirror/CCXT hashes are documented
in the existing outcome contract. Admission stays closed and unknown values stay null.
