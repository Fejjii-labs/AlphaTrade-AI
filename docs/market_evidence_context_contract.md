# MarketEvidenceContext v1 and experiment integration

Stable contract: `public-market-context/v1`.
Python source: `backend/src/app/market_contracts/context.py`.
Published serialization schema: [market_evidence_context.v1.schema.json](contracts/market_evidence_context.v1.schema.json).
Read-only runnable example: [experiment_market_evidence.py](../backend/examples/experiment_market_evidence.py).
Provider coverage, units, CVD/divergence/reset rules and native limitations remain
in [market_evidence_foundation.md](market_evidence_foundation.md).

## Acquisition and evaluation clocks

The required reader and actual assembler use a fixed evidence cutoff and an
independent acquisition completion clock. Neither a provider event timestamp nor
a returned receipt timestamp can advance the evaluation clock.

For event `14:59:58`, scan cutoff `15:00:00` and actual HTTP receipt `15:00:01`,
the required OI/funding observation passes when the independent completion clock
has reached `15:00:01` and the provider fact is otherwise valid/fresh. The receipt
remains `15:00:01`; it is never backdated to the scan cutoff.

| Clock | Meaning and admission rule |
| --- | --- |
| Scan cutoff | Immutable event/candle selection boundary. Derivative `event_time <= cutoff`; closed candles and bounded trade windows remain selected at this original boundary. |
| `collected_at` | Original actual HTTP receipt/acquisition. `event_time <= collected_at <= observed_at`; cached reuse preserves it. |
| Observation | Consumer observation/re-age time; it must not exceed independent completion/evaluation. The adapter initially observes at receipt; assembly may re-age at final evaluation. |
| Completion / `assembled.evaluated_at` | Independently sampled after acquisition; every required fact is revalidated here, including an earlier fact that aged while a later request completed. |

`EvidenceClockReport.evidence_cutoff_at` and `acquisition_completed_at` expose
both boundaries. Consumers must evaluate at `assembled.evaluated_at`, not replace
it with the earlier scan-start time. `PublicMarketObservation.receive_time` uses
original derivative `collected_at`, including on cache hits. Provider fact hashes
remain stable across observation/freshness re-aging; actual receipt provenance is
retained separately in the observation envelope.

Current production assemblies called without `evaluated_at` use the assembler's
current clock. To inject a deterministic current clock, pass `clock=...` to
`FirstSliceEvidenceAssembler`; an explicit `evaluated_at` then sets only the
fixed cutoff. `CanonicalEvidenceService` passes its same clock to its assembler.
An explicit `evaluated_at` without a completion clock remains a frozen as-of read;
replay also stays frozen even when a clock is injected. A response acquired after
that historical cutoff cannot satisfy it. Native current OI is not retroactive OI.

Future event times, receipts after independently sampled completion, a regressing
or naive acquisition clock, wrong source/hash/units, and stale events still fail.
Cache entries acquired after a requested cutoff cannot be reused backward. Funding
and Bybit OI requests keep their original bounded `endTime`; final evaluation does
not slide them forward to a later settlement/sample. Binance current OI has no
historical request parameter, so its returned event must independently satisfy the
cutoff. No tolerance, backdating or unconditional timestamp bypass is introduced.

## Serialization contract

| Field | Contract |
| --- | --- |
| `contract_version` | Literal `public-market-context/v1`. Breaking meaning/unit changes require a new version; schema additions require coordinated updates to strict generated clients. |
| `evaluated_at`, `evidence_cutoff_at` | Aware timestamps; optional cutoff cannot exceed evaluation. Optional context may be evaluated later than canonical qualification and retains its own clock. |
| `anchor_venue`, `anchor_symbol` | Canonical market anchor; exact provider symbols are not silently rewritten. |
| `derivatives` | At most one observation per OI/funding metric; explicit per-observation source, venue, symbol, units, event/collection/observation times, version, coverage, freshness, availability and reason. Decimal values serialize as strings. |
| `order_flow` | Executed-print evidence and signed base CVD with proven bounded coverage. `null` is explicitly missing optional flow, not a neutral/zero value. |
| `order_book` | Current visible resting-depth snapshot, separate from executed flow. `null` is missing optional depth. Even an available snapshot has `historical_coverage=false`. |
| `cross_venue_components` | Exact component names whose venues differ from the anchor; required for every such component. A different symbol is rejected. |
| `qualification_authority` | Literal `false`. Context cannot create qualification, a Candidate or execution authority. |
| `open_interest_change`, `open_interest_notional`, `historical_order_book` | Explicit capability limitations: `availability=UNSUPPORTED`, `value=null`, deterministic reason. These are not fabricated observations and carry no invented provider/event facts. |

An empty derivatives tuple means no derivative observations were supplied. An
included unavailable derivative still carries its provider and reason. Required
missing/stale/incomplete/unsupported evidence blocks qualification; optional absence
stays inspectable. Consumers recheck structure, hash, source, methodology, units,
causal clocks and freshness before using values. Serialized availability alone is
not a previously earned pass. Unknown fields and fabricated capability values are
rejected by the typed model.

The separate unsupported capability fields are intentional: OI quantity is not OI
change/notional, and a current resting snapshot cannot establish past liquidity.
Executed flow/CVD uses the existing two closed UTC five-minute windows and declared
zero reset at the ten-minute start. This does not change configured detection
timeframes. Required flow is aligned to each actual detection trigger close;
detection remains on its configured timeframe.

## Integration example

The example consumes one existing assembly and its policy, checks required
derivatives at final evaluation with the original cutoff, checks required flow
alignment and uses the existing canonical evaluator for command/hash binding. It
returns a persistence-ready record containing canonical evidence hash, cutoff,
evaluation, configured detection timeframe, trigger end, assessment state and
optional typed context. It collects nothing, changes no thresholds and activates
no runtime:

```python
from examples.experiment_market_evidence import experiment_evidence_record

# Use the existing collector/assembler configured for the experiment's policy.
assembled = assembler.assemble(organization_id=organization_id, policy=policy)
record = experiment_evidence_record(assembled, policy, optional_context=context)
# Persist record with the experiment result through its existing repository.
```

Required observations come from the canonical bundle, never from optional context.
An available optional OI cannot replace missing required canonical OI. Required
historical book data remains blocked. Cross-venue optional facts keep their own
identities and labels; they cannot silently satisfy the canonical venue's roles.
The example is not registered in a worker or runtime. The next experiment-owner
step is to review this contract and wire the record into the existing experiment
repository/evaluator using its approved configured timeframe and policy.

## Regression evidence and checks

On baseline `d4b98087d1586a3a27d0882ecca98b70847288e1`, the four single-required-metric
cases (Binance/Bybit × OI/funding) fail in the actual assembler with
`required_<metric>:INCOMPLETE` and `wrong_source` diagnostics. The same cases pass
after correction with event `14:59:58`, cutoff `15:00:00`, and receipt/evaluation
`15:00:01`. `test_required_derivative_acquisition.py` contains 30 deterministic
cases: those four, both-metric delayed cache miss/hit with unchanged hashes and
receipt provenance, stale event, event beyond cutoff/receipt, future receipt,
strict historical receipt rejection, backward cache admission, and final-age
revalidation after the later metric completes.

These cases exercise the actual assembler and actual public derivative adapters,
not only optional Agent presentation. Binance core history uses its actual adapter
over recorded HTTP fixtures. Bybit's public recent tail cannot prove the first
slice's eight-hour core trade window, so Bybit cases supply an independent recorded
core history fixture; its OI/funding acquisition, receipt clock, cache, quote,
candles and all assembler required gates remain real code. No native historical
coverage or successful live qualification is inferred from this fixture.

Schema drift check: from `backend`, run
`.venv/bin/python scripts/export_market_evidence_context.py --check`.
`test_market_evidence_context_contract.py` checks schema equality, JSON round trips,
the experiment example, no second collection, required-missing refusal, explicit
unavailable capabilities, cross-venue labeling and forbidden fabricated values.
Generated OpenAPI/types/validators/hashes are checked together. Affected development
selection: **433 passed, 11 skipped** (durable-pin checks need disposable PostgreSQL).
New timing/contract cases: **30 + 6 passed**, no skips. Scoped strict mypy checks
**13 source files**, including assembler/reader/service/probe and the example;
Ruff passes on **15 changed Python files**. Frontend API drift/typecheck and
**15 focused API contract/client tests** pass. OpenAPI schema SHA256:
`dfb53b36c2e7c2e162231da7f4acb7fa010d55575d614c2e81b24514fa5f52a9`.
Exact published code SHA is recorded in PR242.

No native Binance/Bybit HTTP/WS connectivity, region availability, latency or
reconnect/loss acceptance is verified: enforced environment egress allows GitHub
and package hosts, not exchange hosts. No full backend CI, paid provider,
deployment or runtime activation is part of this correction.
