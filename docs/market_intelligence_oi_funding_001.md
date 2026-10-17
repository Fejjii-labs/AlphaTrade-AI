# Read-only OI and funding evidence

Branch: `codex/market_intelligence_oi_funding_001`  
Exact implementation base: `78635e60e4f745fd50d6dc181b555a6948562077`

`GET /canonical/evidence` now includes `market_intelligence`, using the same
process-owned perpetual source, GET-only HTTP client, request budget, catalog,
contract verification, and whole-venue failover as canonical OHLCV/trade evidence.
This read does not mint candidates, grant execution authority, activate Watcher,
submit orders, or deploy anything. No database migration or separate data service
is introduced. Legacy spot/mock market-data APIs are not a source for this evidence.

Each hashed observation contains a metric, full existing `EvidenceMarketIdentity`
(instrument, venue, market type, provider, provenance, native timeframe), provider
event time, caller-supplied observation time, freshness evaluation, units,
calculation method, availability state, decimal value, and reason. Absent event
times and freshness remain null; observation time never substitutes for event time.
Decimal strings retain precision. Zero is accepted only when explicitly reported.
The payload hash binds the freshness policy version and provider fact; observation
clock and consumer age metadata do not create a new fact or evidence-window hash.

| Source | OI | Funding |
| --- | --- | --- |
| Binance USD-M USDT linear perpetual | `/fapi/v1/openInterest`, `openInterest`, `time`; base-asset quantity; snapshot with no candle timeframe | `/fapi/v1/fundingRate`, `fundingRate`, `fundingTime`; latest settled record at or before observation time |
| Bybit USDT linear perpetual | `/v5/market/open-interest`, `openInterest`, `timestamp`; native `5min` / `5m`; base-asset quantity reported as the sum of both sides | `/v5/market/funding/history`, `fundingRate`, `fundingRateTimestamp`; latest settled record at or before observation time |

Both providers verify the exact trading USDT perpetual contract using existing
exchangeInfo/instruments-info contract parsers before reading the metric. Delivery,
inverse, spot, wrong-symbol and wrong-provider evidence cannot satisfy this contract.
OI retains each provider's quantity convention; it is not summed, divided, or
compared across venues. Funding uses `ratio_per_settlement` and
`provider_reported_settled_rate`. A negative rate stays negative. No percentage,
basis-point, annualized rate, default 8h interval, forecast, notional OI, OI change,
CVD or order-flow calculation is inferred from these endpoints.

## Availability and consumer freshness

| State | Meaning |
| --- | --- |
| `AVAILABLE` | Complete verified provider record, usable under its freshness policy |
| `MISSING` | Empty supported history or explicit transport/region/HTTP/rate-limit failure |
| `STALE` | Valid provider record older than the consumer age limit; retained for inspection |
| `UNSUPPORTED` | Provider contract cannot be verified as the supported product, or replay/source has no OI/funding contract |
| `INCOMPLETE` | Malformed JSON/record, absent required fields, invalid decimals/timestamps, future event time, or incompatible result identity |

Versioned age limits are 60 seconds for Binance OI, 600 seconds for Bybit's native
5-minute OI, and 24 hours for **last settled funding history**. The funding limit is
an explicit consumer policy, not a venue funding schedule or a claim that the rate
is current/predicted. The exact age boundary is inclusive. The current v2 freshness
policy rejects any event after observation/evaluation; clock-skew allowance is zero.
Future events fail closed with unknown freshness. These policies reuse the existing
freshness evaluator; existing 10-second trade/quote policy constants stay unchanged.

OI/funding are optional market intelligence in canonical reads. Optional data is
outside the existing setup evidence hash and cannot silently strengthen a strategy.
A `FusionPolicy` that requires `OPEN_INTEREST` or `FUNDING` makes first-slice assembly
acquire and validate the input, bind its public observation/payload hash into the
existing evidence window, and carry the payload in the existing evidence bundle.
The evaluator revalidates identity, hash, availability, observation causality,
event age and command/payload binding at consumer time. Every unavailable state
blocks a required input. This adds no strategy threshold or trading permission;
unsupported strategy grammar/families retain their existing refusals.

On an eligible primary provider failure, `FailoverPerpetualSource` signals the
existing explicit identity switch. All facts collected before the switch are
discarded. Canonical reads restart quote, setup and OI/funding together under the
active venue, preventing a Bybit fact from being labeled Binance. Stale, incomplete
or unsupported primary data is not silently filled with secondary data. Secondary
failure remains explicit. Replay reports unsupported OI/funding without invented
fixtures. CVD continues to require the existing proven trades, never candle volume.

## Official contracts checked

Checked on 2026-10-01 using the official Binance connector and official Bybit docs:

- [Binance present OI](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest)
- [Binance settled funding history](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)
- [Binance official connector market methods](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py)
- [Bybit OI contract and quantity convention](https://github.com/bybit-exchange/docs/blob/master/docs/v5/market/open-interest.mdx)
- [Bybit settled funding history](https://github.com/bybit-exchange/docs/blob/master/docs/v5/market/history-fund-rate.mdx)

Validation uses deterministic `httpx.MockTransport` responses with injected
observation times, including recorded candle/trade fixtures for pipeline checks.
No live venue connectivity or live values are claimed by these tests.

Current receipt-time capture, explicit single-record coverage, cache and Agent
consumer contract: [Public market evidence foundation](market_evidence_foundation.md).
