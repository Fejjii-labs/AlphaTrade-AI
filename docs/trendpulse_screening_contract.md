# Bounded TrendPulse1R research screening contract

Contract: `trendpulse-screening/v1`. Feature branch:
`codex/trendpulse-1r-screening`, based on the separately published PR243 receipt
correction `8b9d1b83ee5c4082d87005afbfdfa8343d286a7e`. This future feature does not
change PR237 or its release candidate. The pure detector remains
`trendpulse-1r-research/v2` with fixed provisional authored parameter v1.

## Decision and receipt semantics

The [receipt correction](trendpulse_1r_adapter_contract.md) requires original
`max(observed_at, receive_time) <= decision_at`. The decision happens after the
explicit closed 5m trigger. Prior M15 boundaries remain anchored to trigger
opening: `floor_15m(trigger_end - 5m)`. A 15m candle closing with the trigger is
excluded, even if received before evaluation. Required histories remain 250
contiguous closed M15 candles and 60 closed M5 candles including the trigger.
No candle or receipt after the decision can qualify an earlier decision.

The HTTP producer uses the server clock. A new request before trigger close,
before the fixed 5s provider settlement allowance, or at/after trigger close +60s
persists a bounded rejection without acquisition. After acquisition, the detector
uses the actual later decision, and rechecks expiration. This path does not extend
the detector's lifetime to make slow acquisition qualify.

Public REST history is known when the confirmed stream has actually returned.
The envelope's observed/receive times use that captured return time, conservatively
covering both confirmation responses; recorded time is the actual envelope
construction time. Historical bar-close times are never substituted for arrival.
Immutable evidence snapshots retain all these times. Trusted offline replay takes
an original recorded decision and envelopes without changing them. Synthetic
fixtures are explicitly tagged; an OHLCV archive alone cannot establish historical
receipt availability.

## Application producer and read consumer

The registered FastAPI routes use the existing authentication, tenant and RBAC
contracts. Fields below use snake_case. Each record is scoped to organization,
owner, experiment version, sealed configuration, immutable strategy hash and variant.

| Method | Route | Access and behavior |
| --- | --- | --- |
| POST | `/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings` | Owner/Trader; evaluate one variant and one closed trigger; commit and return the immutable receipt/rejection/signal detail |
| GET | Same version route | Reader; durable summaries, optional `status`, `limit=1..50` (default20), `offset=0..10000`; newest decision first |
| GET | `/trendpulse-screenings/{record_id}` | Reader; durable original evidence and optional experiment-bound research geometry |

POST body:

```json
{
  "request_id": "b91ed582-c6d1-4cbb-a542-5853540a955a",
  "variant_key": "baseline",
  "trigger_end": "2026-10-10T18:05:00Z"
}
```

This illustrates the shape, not an instruction to screen the historical timestamp.
Caller-supplied evaluation clock, receipts, specification, rules or signal are
rejected. The service resolves the exact immutable authored TrendPulse spec from
an existing experiment variant, verifies strategy content and configuration hashes,
and rejects changed parameters or symbol binding. Generic strategy-library
`pattern_spec` storage already permits research authoring in a manual-review
version; this does not register TrendPulse with the executable AST compiler.

Summaries expose status/reason, source/provenance, receipt counts, evidence hash,
configuration/strategy hashes, decision/start/trigger times, signal ID and duplicate
link. Detail includes the original canonical bar/envelope arrays, public instrument
rules and optional `ExperimentBoundTrendPulse`. List reads defer large evidence
and signal JSON. These authenticated GETs are the application read consumer;
Dashboard/Journal presentation remains with its owner.

The shared [OpenAPI artifact](../frontend/src/lib/api/generated/openapi.json) and
[hash manifest](../frontend/src/lib/api/generated/hashes.json) include all three
routes. This feature does not alter generated pilot-client selection.

## Bounded acquisition and concurrency

The production acquirer uses the existing Binance USD-M GET-only adapter and
closed-candle confirmation. It has no credentials, order method or account access.
It requests one exact perpetual contract metadata row and two confirmation reads
for each timeframe. Contract symbol/base/quote/settlement/margin, tick/lot/minimum
filters and payload hashes must be usable. Binance public rules describe research
geometry; they do not verify BloFin execution precision or basis.

| Bound | Policy |
| --- | --- |
| Requests | At most5 GETs: exchangeInfo, two M15, two M5 |
| Candles | Request limits252 M15 /62 M5; select250 /60 final contiguous candles |
| Payload | At most2MiB decoded bytes per response; metadata at most5000 symbol rows |
| Time | 15s monotonic acquisition budget, checked before requests and during reads; each HTTP operation timeout at most3s. An in-flight read can take up to3s to observe the deadline; this is not a hard15s end-to-end CPU bound |
| Retries/fallback | Zero retries/backoff/sleep or secondary provider |
| Weight | Shared process-local fail-fast120-weight/minute budget, distinct from watcher |
| Capacity | Two acquiring requests per process; fail-fast exhaustion |
| Single flight | PostgreSQL nonblocking transaction advisory lock per immutable experiment version, across independent connections/processes |

The producer creates no activity thread, scheduler, watcher component or additional
service. Advisory locks release at commit/rollback/disconnect; capacity releases
in `finally`. Read requests and conflicting requests can progress while another
acquisition remains blocked. Existing worker behavior is unchanged. Aggregate
provider/IP limits across processes still require integration-owned coordination.
HTTP5xx, rate limits, changed confirmation, bounds and partial stream failures
persist safe reasons and available original evidence, without upstream bodies,
headers or sensitive URLs.

## Durable deduplication and rejection attribution

One additive table `trendpulse_screening_runs` stores append-only evidence/outcome
records. Outcomes are `unavailable`, `refused`, `no_setup`,
`qualified_research_signal` or `duplicate`; the detector's exact bounded reason is
retained. Normal operational rejections are records. Authentication, disabled/source
configuration, invalid tenant/config binding, request-ID conflicts and busy locks
use explicit HTTP errors before acquisition.

`(organization_id, user_id, request_id)` is unique. The same request after restart
returns the original result without another acquisition, including when the feature
is subsequently disabled. Reusing the ID with different inputs returns409.

Natural signal uniqueness is enforced independently by PostgreSQL on
`(experiment_version_id, variant_key, dedupe_key)`, where the key includes stable
adapter signal identity and the complete public market identity. Mock/public source
identities remain distinct. A new request for the same derivation writes a duplicate
record linked to the first signal, preserves that request's receipts, and leaves the
first receipts unchanged. Changed decision/arrival times alone do not create a
new derivation. Changed canonical values, rule hashes or geometry under the same
natural ID persist `persisted_signal_derivation_conflict` and cannot replace it.
ORM and PostgreSQL UPDATE/DELETE guards protect history; composite foreign keys
prevent cross-version/tenant duplicate links.

Provenance is explicit: `live_public_rest`, `recorded_public_receipts`, or
`synthetic_fixture`; replay mode cannot relabel mock original envelopes as public.
Only trusted offline code installs recorded replay. HTTP callers cannot import
receipts or backdate decisions.

## Migration coordination and integration

The reserved single revision is `a12trendpulsescreen001` after
`a11experiments001`, following `a10blofinactivity001`. It adds only the screening
table, read index and immutable guards. Historical revisions are unchanged;
existing head expectations advance with ancestry and data-preservation assertions
retained. PostgreSQL is required for this producer's cross-process lock and SQL
history guarantees. Non-PostgreSQL migration inspection does not install PG guards.

Reservation was posted to the integration owner on
[PR237](https://github.com/Fejjii/AlphaTrade-AI/pull/237#issuecomment-6101578637).
The inspected future integration head
`1ebf85357bc33c1f966e06f892a1a9a02adefafb` contains a11 and no competing a12.
PR237 remains `3600ec89e48a6f3cf2f29b4468ab124d1219f627`; this work is excluded.
Acknowledgment/merge sequencing is still an integration prerequisite. No shared
or runtime database migration was applied. Disposable UUID-schema round trips
verify real ORM screening writes, original receipts on repeated upgrade, and
preservation of prior experiment/native history. Downgrading a12 removes its own
screening history; an operational rollback should disable the producer and retain
its additive schema rather than destroy receipt history.

## Authority and remaining runtime dependencies

Every record and signal states `execution_authorized=false`,
`management_authority=false`, `sample_eligible=false`, and `performance=null`.
The geometry remains a structural stop, conservative rounded entry and gross1R
full target; no runner, discretionary model entry, quantity or native outcome.
Research can inspect an experiment draft/paused/completed version without approval;
this does not authorize autonomous execution. Promotion can study the same event
under a fresh version, but creates no Validation sample or carried performance.

`TRENDPULSE_SCREENING_ENABLED` defaults false. Public production additionally
requires the existing explicit market-data enablement,
`PERPETUAL_EVIDENCE_SOURCE=binance_usdm` and secondary `none`. No operator value was changed and no public
exchange request was made during this task. Do not enable it merely to validate
this PR; mock/raw-response and disposable database verification are provided.

Before public research activation: integration approval for a12 sequencing,
restricted provider network access, real confirmed receipt-envelope validation,
shared IP budgets and reviewed operator request cadence are needed. Automatic
scanning, streaming ingestion, retention and Dashboard/Journal display are absent.

Before any trading or performance claim: reviewed compiler/evaluation registration
and canonical Candidate/TradePlan bridge; verified fresh BloFin demo UID and
execution-account evidence; native rules/quote/basis checks; atomic deterministic
sizing, exposure and loss reservations (including manual native positions without
management authority); approval/kill-switch/final dispatch; protected native order
lifecycle and reconciled outcomes; separate trusted native/simulator sample
resolvers. BloFin owns demo execution and replenishment. This screening API supplies
research evidence only and does not replace those authorities or change Nested/SFP.
