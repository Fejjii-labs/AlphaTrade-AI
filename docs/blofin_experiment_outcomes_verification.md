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

## N2 publisher-source and existing receipt inspection — 11 October 2026

Documentation-only branch `codex/blofin-native-source-evidence`, based on accepted PR248
`a7a843f7c27e8bdcea5f89c802c808472477a969`. The published coordinator contribution
`b2c6d90468d673664ac30ddafe97761b69d3661f` and its branch are unchanged. The source and
test evidence above is reused for unchanged implementation; it was not rerun. **N1,
N3–N5 remain 4/5 accepted locally; N2 remains unmet.** This inspection adds no outcome
writer, monetary assertion, schema change, native sample admission or activation.

The supported GitHub connector supplied repository metadata, pinned commit references
and public files from the `blofin` publisher organization. The SDK README links BloFin
and its official documentation. These are publisher contracts and developer recipes,
not authenticated venue receipts or proof of historical account methodology. External
`SKILL.md` files were read as source evidence, not adopted as operational instructions.
No SDK, MCP server or CLI was installed or executed. The previously rejected official
documentation HTTP request was not repeated, and no network restriction was bypassed.

Every fetched file was checked against its Git blob identity using
`SHA1("blob " + byte_length + NUL + file_bytes)`: **7/7 matched**. The SHA256 values below
cover the exact fetched UTF-8 source bytes, independently of the Git blob checks.

| Pinned publisher source | SHA256 |
| --- | --- |
| [SDK REST trading contract](https://github.com/blofin/blofin-sdk-python/blob/df75d514292d90fd6ad67eb12a9f0ca6db738a73/src/blofin/rest_trading.py) | `efe31042066c9776fb3618d315fe8e161d638597a6ac16e3ad1c279b23583277` |
| [SDK README](https://github.com/blofin/blofin-sdk-python/blob/df75d514292d90fd6ad67eb12a9f0ca6db738a73/README.md) | `4f5c6aea5d373a5c19937b6e7f897120cd3c9df6f7ce5372b2fc6286aa3821a6` |
| [MCP trading tools](https://github.com/blofin/blofin-mcp/blob/25d296062075329b743630cb9a969dfe09dd3a27/src/tools/trading.ts) | `fe57f56ee8ca135ba27d297756ba9b09e511f34eba4040e2fca2bf397eb121d8` |
| [Trader recipe](https://github.com/blofin/blofin-skills-hub/blob/64f4a0ba6b43b8e094a7b6d7eb28a56feb493311/skills/blofin-trader/SKILL.md) | `31e3980c993c8e3106f7b9ad3770a751fd445c3d8410584cc45c30cb476ca52e` |
| [Portfolio recipe](https://github.com/blofin/blofin-skills-hub/blob/64f4a0ba6b43b8e094a7b6d7eb28a56feb493311/skills/blofin-portfolio-analyst/SKILL.md) | `2589b43052d2f962381cf51050a14b9d0d70bc23e324a51a2bfd061be163ed22` |
| [CLI commands](https://github.com/blofin/blofin-cli/blob/e729a8986d8e320559ec65384dc2e7dd16690fdb/src/commands.ts) | `3e169c3925aa9a0ecb655bf168fb29f19d5e38f75f747c3a9914b71318399763` |
| [CLI test routes](https://github.com/blofin/blofin-cli/blob/e729a8986d8e320559ec65384dc2e7dd16690fdb/src/test-utils/routes/trading.ts) | `0aecd13bc8fa84a041233fa660fc564d67a64eb95e09518a86da69a9ea708fc5` |

### Separate ancestry and money findings

| Required fact | Supported source finding | Remaining qualification gap |
| --- | --- | --- |
| Historical algo fields | SDK `getOrdersHistory`, lines 483–564, documents `algoId` and `algoClientOrderId` on historical orders. MCP `get_order_detail`, lines 173–190, accepts the client algo ID for lookup. | These are relation candidates. Neither field alone proves TPSL ownership, canonical experiment ownership or a protected parent binding. Client-selected IDs grant no authority. |
| TPSL → generated closing child | SDK `getOrdersTpslHistory`, lines 566–625, documents `tpslId`, trigger fields and effective state; separate `getOrdersAlgoHistory`, lines 627–693, documents trigger algo fields. | No declared `tpslId` ↔ historical `algoId` equality, generated closing-order IDs or direct canonical entry → protection → child chain. Attached trigger prices do not establish that chain. |
| Complete partial fills | SDK documents terminal `partially_canceled`, accumulated `filledSize`, native `tradeId`/`orderId` and paged fills with before/after trade-ID cursors, time bounds and a 100-record limit. | No observed, verified-UID, complete page sweep for an attributed entry/exit exists in this inspection. A terminal state or one page is not complete lineage; retention and missing-page coverage still require evidence. |
| Futures fee currency | SDK `getFillsHistory`, lines 695–749, documents `fee` without futures `feeCurrency`. | Currency remains unknown when absent. The SDK withdrawal `feeCurrency` field is a different contract and cannot supply futures currency. Settlement currency is not a substitute. |
| Fee sign and rebates | Historical order `fee` is described as “Fee and rebate”; the fill field is only “Fee.” | No explicit signed debit/rebate convention is declared by these sources. Preserve native strings; do not normalize sign from a recipe or example. |
| Closing PnL and fee inclusion | SDK describes `pnl` and `fillPnl` as closing profit/loss. Portfolio recipe lines 91–94 sums closing `fillPnl`, sums fees separately and subtracts total fees for its presentation. | The recipe suggests separated reporting, but does not independently declare the historical fill-level gross/net accounting contract, allocation or sign convention. Do not set a verified “excludes fees” flag from the recipe. |
| Closing PnL and funding inclusion | No funding inclusion declaration accompanies those PnL fields or the portfolio recipe. | Funding inclusion/exclusion remains independently unknown; fee treatment cannot establish it. |

The MCP tools return raw JSON and supply no additional verified parent/child decoder or
monetary attestation. CLI fixture responses are mocks, not account observations. Earlier
third-party documentation mirrors and CCXT remain corroborating leads; none closes N2.

At the accepted candidate, `NativeActivityFact` in
`backend/src/app/schemas/blofin_activity.py` has no normalized algo relation fields.
`backend/src/app/providers/exchange/blofin_activity.py::parse_fact` selects declared
fields and drops wire `algoId`/`algoClientOrderId`; the activity service persists the
normalized model, not the complete raw response. These fields are **not already
preserved** in immutable native facts. Retaining supported future relation candidates
requires a coordinated shared contract change with old payload/hash compatibility and
generated artifacts; it would still not prove TPSL ancestry. That change is deferred,
and the accepted contracts/hashes remain unchanged during staging qualification.

### Existing hosted evidence: executed read-only checks

Two supported Render PostgreSQL query calls completed for workspace
`tea-d7hn0fvavr4c73f62c70`, database `dpg-d8fuok57vvec739lm0u0-a`. The first returned
only selected column names/types for existing governed execution, audit and lifecycle
tables. The second returned only aggregate proof-record and JSON-key-presence counts:

| Selected proof class | Records | Protection keys | Closing facts keys | Relation/methodology keys |
| --- | ---: | ---: | ---: | ---: |
| `governed_demo_lifecycle_resolutions` | 0 | 0 | 0 | 0 |
| `audit_logs` where `resource_type = 'experiment_execution'` | 0 | 0 | 0 | 0 |

The first group inspected `protection_ids`, `exit_fills` and direct relation candidates
`algoId`/`algoClientOrderId`/`generated_order_ids`; the second inspected `protection_ids`,
`exit_orders` and `monetary_methodology_version`. No account IDs, private payloads,
credentials, user records or monetary values were returned. These zeros mean the
selected database supplied no existing close/experiment attestation for this proof.
They do not prove the API's database binding, absence of exchange activity, strategy
performance or the cause of the undiagnosed manual BloFin incident.

Public source bytes, pinned Git blobs and digests are retained locally in
`/tmp/alphatrade-n2-sources/sources.json` and `manifest.json`; the aggregate-only hosted
receipt is `hosted-redacted-receipts.json`, SHA256
`6a81902ac954efdb7049598c4c37c2509f569f1bb0cc6d0b791f70110d52fb35`.
No external source dumps or hosted payloads are committed. The owner collects the
receipt files directly. `git diff --check` passed for this documentation contribution;
no backend tests, green CI jobs or full acceptance gate were rerun for a prose-only change.

Next bounded N2 action: obtain a venue-owned declaration or authenticated read-only
receipt proving the exact server-acknowledged TPSL parent → generated child identity,
then collect its complete paged order/fill lineage under the verified account binding.
Separately establish historical futures fee currency, signed fee/rebate conventions,
and each closing PnL field's fee and funding inclusion/allocation basis. The permitted
publisher-source connector and selected database cannot supply those missing facts;
the existing access packet needs supported official contract/support access and a
scoped read-only venue identity for that evidence, without exposing secrets in chat.
Until then the trusted closing producer remains unqualified, money remains unavailable
and native HTTP sample admission stays closed. This gap blocks N2 activation only;
independent UI staging qualification can proceed under its existing release controls.
