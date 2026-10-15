# Generic experiment domain — interface v1

Future batch based on refreshed PR237 `619c15fe63ffc23288c2866781e08f9cb44a2ab9`.
PR239/240 are preserved. The draft targets a separate frozen experiment base;
this feature does not enter the current release candidate. Backend/migration/API
ownership only. Frontend, execution adapters and analytics presentation stay with
their owners. Authoritative request/response models: `app.schemas.experiments`.

## Interface published before implementation

| Method/path | Contract | Access |
| --- | --- | --- |
| POST /experiments | ExperimentCreate → ExperimentVersion | Trader/Owner |
| GET /experiments | paginated ExperimentPage | Reader, own tenant/user |
| GET /experiments/{id} | ExperimentDetail | Reader, own tenant/user |
| POST /experiments/{id}/versions | ExperimentVersionCreate → ExperimentVersion | Trader/Owner |
| POST /experiments/{id}/versions/{version_id}/transition | ExperimentTransition → ExperimentVersion | Trader/Owner |
| POST /experiments/{id}/versions/{version_id}/approve | ExperimentApproval → ExperimentVersion | Owner |
| POST /experiments/{id}/versions/{version_id}/promote | ExperimentPromotion → new ExperimentVersion | Trader/Owner |
| POST /experiments/{id}/versions/{version_id}/samples | ExperimentSampleCreate → ExperimentSample | Trader/Owner, trusted source resolver required |

Draft → pending_approval → approved → running ↔ paused → completed → promoted.
Completion may retain an insufficient sample; promotion requires the selected
variant's declared minimum. Every configuration is immutable from creation;
changes create a new draft version with fresh sample identity and approval.
Transitions use expected_revision, row locks and append-only lifecycle events.
Promotion narrows completed Exploration to one variant in a new Validation draft;
no approval, timestamps, samples or outcomes carry across. It is not a strategy
approval or an improvement claim. Running is a domain state, not worker activation.

Configuration hashes bind tenant, account/source, immutable strategy content hashes,
experiment/version/sample identities, variants, exact parameters, model policy,
universe, risk and targets. Decimal inputs use strings/integers, never floats.
Models can only provide bounded advisory text; they cannot modify deterministic risk,
size, strategy approval, execution or account authority.
Monetary risk limits accept at most 24 digits and 12 decimal places. Admission
previews refuse instrument/account amounts beyond 32 digits, 18 fractional places
or 31 integer places; arithmetic uses an explicit 80-digit context and rechecks
rounded loss, exposure and lot alignment against the approved envelope.

BloFin demo requires the existing verified native UID record and hash-verified
execution-account identity audit. Current stored read-only credential binding must
match; neither organization nor client order echoes prove UID identity. Approval
is an explicit bounded envelope (expiry at most 30 days, finite trade/exposure/loss
and model budgets), not a venue dispatch authorization. A future executor must
verify its current execution connection against the approved UID before dispatch,
including after credential rotation. No external identity request is made here.
Internal simulation has a distinct source and no native UID. Source/account/family
cannot be switched inside an experiment; create a different experiment.

Source adapters resolve immutable records server-side. Sample requests cannot
provide PnL, native/source claims, quantities or approval evidence. Proof must bind
organization, execution account/UID, source, version/hash, variant and sample group,
and establish that the observation/trade began in an approved running interval.
Pre-existing, replayed and Exploration records cannot become a fresh Validation
sample. Without a trusted resolver ingestion fails closed. Performance is null:
this domain does not fabricate native returns or merge them with simulator returns.
Fill/order completion alone does not prove a closed trade or realized performance.

## Canonical adapters and risk boundary

Nested uses `operational_nested_continuation/v1`, the existing immutable authored
parameters and canonical structural detector/evaluation. Do not invent wave rules,
change provisional parameters or replace measured structural targets with 1R.
SFP uses `swing_failure_pattern/v1` and its canonical evidence/structural confirmation;
it currently has no authorized automatic entry/stop/target plan. Setup observations
remain research evidence, not executed trade performance. Parameter changes must
reference a different immutable strategy version, not an experiment override.

The separate deterministic TrendPulse1R research adapter (`trendpulse_1r/v1`)
now has a fixed authored closed 15m trend/5m entry spec, structural stop and rounded
gross 1R geometry: [adapter contract](trendpulse_1r_adapter_contract.md). The domain
recognizes immutable authored parameters with both timeframes for setup observations
only. Compiler/evaluation/Candidate/TradePlan/approval/dispatch integration remains
absent; research geometry cannot grant execution authority. TrendPulse closed-trade
samples and execution admission still refuse. Nested/SFP meanings stay unchanged.

Risk preview composes the existing floor-rounded BASE sizing helper with LINEAR
contract conversion and quote exposure. All native account positions, including
manual positions and reservations, count toward exposure. Only exact experiment
execution lineage can grant management authority; exposure observation never does.
Inverse/unsupported units, off-tick structural prices, stale/mismatched account data,
canonical risk BLOCK, kill switch, loss caps and insufficient capacity fail closed.
BloFin owns demo fills, balance and replenishment; no local balance reset/top-up API.

## Migration and owner handoff

Reserve one additive revision `a11experiments001` directly after current head
`a10blofinactivity001`, on this future feature branch only. No historical migration
changes. Integration owner must sequence/rebase it if another future migration is
reserved first; one head and a10 data preservation are required before adoption.
Current-head expectations advance to a11 on this feature branch while retaining
the historical ancestry assertions. The separate release branch remains at a10.

Initial interface publication: `401b4eb576319d9dd8dde4801f9f7f500a8b28f2` in draft
PR241. Final scoped OpenAPI: [experiments_v1.openapi.json](contracts/experiments_v1.openapi.json).
Focused fixture evidence and reproduction commands:
[experiment_domain_verification.md](experiment_domain_verification.md).

The default API has no source resolver. Its sample endpoint returns 503 until the
source owner installs a trusted adapter. Native adapters must verify complete
experiment-owned entry/exit lineage and account identity; simulator adapters must
resolve independent immutable simulator records. An ordinary manual command is
identity evidence only and never experiment management or sample authority.
The tests use a trusted fixture resolver and HTTP MockTransport, not native exchange
performance. This batch implements neither native closed-trade reconciliation nor
performance calculation, and installs no execution, scheduling or replenishment
runtime. These are explicit adoption prerequisites, alongside reviewed canonical
strategy adapters, fresh native execution UID verification, atomic account-wide
reservations/loss counters and existing final dispatch authorities.

No full backend CI, deployment, external orders, credential changes or activation.
Shared API artifacts are regenerated in the integration correction; presentation
owners can use the published schema and API. Dashboard and Journal remain outside
this PR. Integration should adopt the
migration and feature only in a later batch, preserving one Alembic head.
