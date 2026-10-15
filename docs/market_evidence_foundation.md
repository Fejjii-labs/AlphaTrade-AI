# Public market evidence foundation — provider owner contract

Workstream: AT-MARKET-EVIDENCE. Initial refreshed PR237 baseline:
`7fc21db00a0527ee018f3fd1c478da64bcc713c0`; latest refreshed integration head
`619c15fe63ffc23288c2866781e08f9cb44a2ab9` (migration tests/docs only). Separate feature branch:
`codex/market-evidence-foundation`. Public read-only exchange data; no subscriptions
or MindPillar endpoint assumptions. No deployment, paid activation, external
orders, operator setting changes or complete backend CI.

## Contract and actual consumer

This extends existing Binance/Bybit sources, `DerivativeObservation`, proven
trade-window coverage and `OrderFlowObservation`; it does not create a second
market pipeline. `CanonicalEvidenceRead.order_book` is additive. The existing
Agent `CanonicalPerpetualQuoteReader` carries typed `MarketEvidenceContext` in
`MarketQuoteView.evidence_context`; the existing Agent market turn renders OI,
executed flow, CVD/divergence and resting book context. Each optional input retains
its availability, reason and provider identity. The deterministic consumer
rechecks structure, hash, methodology, identity, units and age before showing values.
It has no qualification/execution authority. Tests call `InteractiveAgentService.handle_turn`.

Every fact carries provider/source/adapter provenance, venue, perpetual product,
instrument and provider symbol, event/observation timestamps, decimal units,
versioned methodology, coverage, freshness and availability. OI/book also retain
original `collected_at`; cache reuse advances observation/freshness without
pretending to recollect. Provider book generation time is separate from matching
engine/transaction time. Null values remain null. Test transports/fixtures are
explicitly deterministic tests; they are never installed as live production data.

Live factory sources capture snapshots at receipt time. Optional Agent context
uses a separate final `market_context_evaluated_at`, after collection. Required
current scans keep a fixed event/candle cutoff and independently sample final
acquisition/evaluation time; actual receipt may follow scan start, while events
must satisfy the cutoff and receipts must not exceed completion. Frozen historical
reads still reject later acquisition. See the [stable contract and experiment
example](market_evidence_context_contract.md) for the PR242 timing correction,
regressions and exact clock semantics.

| Evidence | Binance USD-M USDT linear perpetual | Bybit USDT linear perpetual | Claimed coverage |
| --- | --- | --- | --- |
| OI | `GET /fapi/v1/openInterest`; `openInterest`, `time`; base quantity | `GET /v5/market/open-interest`, `category=linear`, `intervalTime=5min`, `limit=1`, bounded `endTime`; `openInterest`, `timestamp`; provider sum of both sides in base units | One provider record. Native Bybit 5m sample is not proof of complete 5m historical coverage. |
| Settled funding (existing) | `/fapi/v1/fundingRate` | `/v5/market/funding/history` | One settled record; ratio per settlement, no inferred funding schedule. |
| Executed flow | `/fapi/v1/aggTrades`, contiguous IDs, complete bounded pagination; `m=true` means buyer maker/sell aggressor | `/v5/market/recent-trade`, `execId`, documented taker `side`; bounded recent tail with prefix/end/overlap proofs | Existing proven half-open trade window. Binance includes aggregated market prints, excluding insurance/ADL, not individual-fill counts. Bybit recent REST is not pageable historical trade coverage. |
| Resting book | `/fapi/v1/depth?limit=20`; `T` transaction, `E` output, `lastUpdateId` | `/v5/market/orderbook?category=linear&limit=20`; `cts` matching engine, `ts` generation, `u`, `seq`, `s` | Independent depth-limited snapshot of returned visible levels; RPI excluded on both venues. No historical or continuous-book claim. |

OI is outstanding quantity, not OI change, OI notional or executed flow. This
foundation explicitly reports OI change/notional unavailable: it does not subtract
unproven samples or multiply by an unrelated price. Binance's separate public
`/futures/data/openInterestHist` has quantity/value fields and a bounded native
history; that endpoint is documented but not collected by this change. Bybit's
`singleOpenInterest` (now documented) is distinct from the existing contracted
`openInterest`; no implicit factor-of-two conversion or cross-venue OI sum is made.

Book base quantity totals and quote notionals refer only to returned resting
levels. Quote notional is the unrounded sum of price × base quantity. Spread is
best ask minus best bid in quote/base units. Resting imbalance is
(bid base − ask base)/(bid base + ask base). It is distinct from taker imbalance.
Duplicate/unordered/crossed/locked levels, nonpositive sizes, absent timestamps,
wrong symbols or future times produce explicit incomplete evidence without values.

## CVD and divergence rules

Existing flow supports two UTC-aligned, closed five-minute intervals over a
half-open ten-minute window. It does not change strategy detection timeframes:
1m, 15m, 1h, 4h and other configured/native timeframes remain intact. A strategy
requiring aligned flow binds its flow end to the actual strategy trigger close.
`coverage_kind=proven_executed_trade_window` requires the existing coverage proof;
missing prefixes, lost overlap, conflicting execution IDs or sequence gaps fail closed.

CVD units are base asset. Signed delta is aggressive buy base − aggressive sell
base; quote delta/CVD are separately labeled unrounded price × base quantities.
CVD starts at zero at the declared ten-minute start and rebuilds on window roll
or venue switch. It is not a provider's absolute/session CVD. Last-window slope
is signed base delta / 300 seconds. Empty fully proven internal intervals can have
zero volume; missing coverage cannot be converted into neutral zero evidence.

The deterministic divergence method remains
`last-vs-prior-5m;directional-delta-and-quote-imbalance;print-close/v1`:

- Bullish: both windows have prints, current terminal print price is strictly lower
  than the prior terminal price, and current signed base delta is strictly positive.
- Bearish: both windows have prints, current terminal price is strictly higher,
  and current signed base delta is strictly negative.
- Equal prices, zero signed delta or either empty window produce no divergence.

These are print-close-to-close conditions, not swing/pivot divergence or probabilities.
Strengthening additionally requires same signed direction, greater absolute base
delta, aligned quote imbalance and greater absolute quote imbalance. All derived
states reconcile to the hashed windows at the consumer boundary.

## Book gaps, deduplication and resynchronization

The active consumer collects REST snapshots. Polls replace whole books; update-ID
jumps between independent snapshots are not labeled missing historical deltas.
`BookSequenceGuard` and each adapter's `observe_order_book_delta` callback handle
future stream integration without opening a socket or activating a worker:

- Binance uses the documented futures first bridge `U <= lastUpdateId <= u`, then
  `pu == previous u`. Exact repeated final updates are ignored; conflicting
  duplicates, missing bridges/predecessors and disconnects require resynchronization.
- Bybit documents ordered `u` and cross-depth ordering `seq`, but no predecessor
  field/contiguous increment guarantee. A delta therefore cannot establish lossless
  continuity here: it is explicitly unproven and requires a fresh snapshot. A new
  snapshot replaces state, including service restart `u=1`.
- Any accepted change invalidates the truncated REST book rather than applying
  zero/deletion updates to a depth-limited book whose deeper levels are unknown.
  A gap callback returns `INCOMPLETE`, `sequence_status=resync_required`, no levels;
  the next normal read acquires/verifies a fresh snapshot before presenting liquidity.

Native WS lifecycle/reconnect/packet-loss checks have not been performed. The
sequence callback is deterministically exercised through the actual adapters;
continuous historical book reconstruction is not claimed.

## Cache, freshness and required evidence

Existing shared Binance request budget, closed aggTrade cache and reduced snapshot
cache remain. A bounded causal observation cache adds one collector for OI,
funding and books: 64 public entries, two-second TTL, exact source/identity/symbol/
method keys. Binance factory instances share the existing process pool; the existing
app process-source factory shares each Bybit source. A cache acquired after a
requested evaluation is never reused backward. Failures are not cached as usable
zero facts. Book invalidation discards the symbol's cached observations.

Production process-shared Bybit recent pages are cached for consumers at the same exact observation clock,
with independent normalized copies and independent lineage/window proofs. Later
polls reacquire. A cached page cannot invent a missing overlap or a historical
prefix. Future prints cannot prove an end boundary.

OI age limits remain Binance 60s / Bybit 600s; funding 24h is a consumer limit for
a last settled record. Derivative freshness methodology is v2 with zero allowed
future skew. Book event age limit is 10s. Existing closed-flow event age is 600s.
These are consumer policies, not exchange latency or funding-schedule guarantees.
Stale records remain inspectable but cannot satisfy a required role.

Existing required OI/funding/flow qualification validates hashed command binding
and fails for missing, stale, unsupported or incomplete evidence. Required historical
`ORDER_BOOK` explicitly fails in both assembly and evaluation because this snapshot
contract cannot establish liquidity at an earlier trigger. Optional observations
remain outside the setup evidence hash and cannot silently strengthen qualification.
Cross-venue components are listed explicitly in `MarketEvidenceContext`; each
observation retains its own venue. Existing failover restarts the entire canonical
read and discards old-venue projections rather than relabeling secondary facts.

## Official semantics verified 2026-10-10

- [Binance USD-M market data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data): order book `T/E`, depth limits/weights, OI, aggTrades field meanings/48h history limit, RPI/insurance/ADL coverage.
- [Binance futures book synchronization](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly): futures bridge/predecessor rules and absolute quantity/deletions.
- [Official Binance connector](https://github.com/binance/binance-futures-connector-python/blob/a6bfbbf10fe2c1b4eb76fc24ffb82eb94bf9df89/binance/um_futures/market.py).
- Bybit official docs at revision `59e3d457dea6b419a14987c19a3aabf4ffb38238`: [OI](https://github.com/bybit-exchange/docs/blob/59e3d457dea6b419a14987c19a3aabf4ffb38238/docs/v5/market/open-interest.mdx), [executed trades](https://github.com/bybit-exchange/docs/blob/59e3d457dea6b419a14987c19a3aabf4ffb38238/docs/v5/market/recent-trade.mdx), [REST book](https://github.com/bybit-exchange/docs/blob/59e3d457dea6b419a14987c19a3aabf4ffb38238/docs/v5/market/orderbook.mdx), [WS book](https://github.com/bybit-exchange/docs/blob/59e3d457dea6b419a14987c19a3aabf4ffb38238/docs/v5/websocket/public/orderbook.mdx).

No native Binance/Bybit API/WS connectivity, host latency, region availability or
real-market sample acceptance is claimed. Current environment egress policy permits
GitHub/package hosts, not exchange API/WS hosts; no policy/operator settings were
changed. Focused HTTP fixtures use sandbox network permission for local AnyIO
TestClient scheduling; they do not call native exchanges.

## Integration handoff

[Schema coordination posted to PR237](https://github.com/Fejjii/AlphaTrade-AI/pull/237#issuecomment-6098506280).
The experiment/integration owner identity and acknowledgment are not yet provided.
Changes are additive backend/Agent contracts; no experiment schema, DB migration,
strategy thresholds, compiler grammar or frontend product-flow edits. Generated
OpenAPI/types/validators/hashes are refreshed together.

Next consumer step: the experiment/integration owner should consume the same
`MarketEvidenceContext` and required-role checks in the experiment evaluator,
bind requested flow context to each configured detection trigger, and persist
provider-specific causal coverage/availability. If historical book qualification or
OI change/notional is required, first add verified native histories/paired causal
samples with declared quantity conventions; do not qualify from current snapshots.
Native public endpoint and sequence/reconnect acceptance remains a separate read-only
check in an exchange-permitted environment, without paid activation or orders.

Foundation verification at `d4b98087` (before the required-timing correction;
development scope, no complete backend acceptance):

- `backend/.venv/bin/pytest` with `test_public_market_evidence_foundation.py`,
  `test_market_intelligence_oi_funding.py`, `test_market_intelligence_cvd_orderflow.py`,
  `test_bybit_usdt_perpetual_evidence.py`, `test_binance_usdm_staging_reliability.py`,
  `test_canonical_evidence_http.py`, `test_interactive_agent_foundation.py`: **300 passed,
  zero skips**, including one actual Agent turn through canonical evidence.
- Scoped Ruff lint/format; scoped strict mypy over 14 provider/normalization/consumer
  source files with `--follow-imports=silent` (not a repository-wide typecheck).
- `npm run api:generate`, `npm run api:check`, `npm run typecheck`; focused
  `generated-contracts.test.ts` and `client.test.ts`: **15 passed / 2 files**.
- No full backend CI, manual workflow dispatch, deployment or native exchange orders.

Exact published feature SHA and final check results are recorded in the draft PR.

PR242 correction checks: **433 passed, 11 skipped** across the affected provider,
assembler, canonical HTTP, Agent, diagnostic, evaluator and lifetime selection.
The skipped durable-pin cases require disposable PostgreSQL; they are not counted
as passing. New timing regressions: **30 passed**; stable-contract/experiment-example
cases: **6 passed**. Scoped strict mypy: **13 source files**; focused frontend API
tests: **15 passed**; Ruff and generated API/schema drift/typecheck passed.
See [market_evidence_context_contract.md](market_evidence_context_contract.md) for
baseline reproduction, clock admission, fixture coverage limits and the example.
