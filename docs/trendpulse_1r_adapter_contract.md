# TrendPulse1R pure adapter — research v1

Separate feature branch `codex/trendpulse-1r-adapter`, based on corrected PR241
`6ca6a76201547524275266dfa22fdf12f5932b14`. This is outside PR237's release candidate.
No migration, worker, settings, order dispatch, model caller or performance runtime
is added. Nested/SFP detectors, targets, compilers and execution meaning are preserved.

Authoritative code: `app.strategy_brain.trendpulse_1r`. Authored family
`trendpulse_1r/v1`, parameter version `trendpulse-1r-research/v1`, corrected adapter
version `trendpulse-1r-research/v2`, paper-only
and provisional. Rules are fixed; overriding a v1 threshold is rejected. A reviewed
new rule version and immutable strategy version are required to change them.
[Published JSON Schema](contracts/trendpulse_1r.v1.schema.json) describes the pure
request/result and separate experiment attribution interface. It is not OpenAPI
for a new endpoint. Shared API drift remains checked against PR241's full artifacts.

## Pure interface and causal evidence

`evaluate_trendpulse(spec, *, trend_bars, trend_observations, entry_bars,
entry_observations, instrument_rules, trigger_end, evaluated_at, seen_signal_ids)`
returns `TrendPulseResult`. No network, database, clock read or mutation is performed.
`TrendPulseRequest` is the serializable input contract; callers can pass its fields
to the function. An explicit aware `trigger_end` identifies one closed 5m event;
this is not an implicit scan for the latest candle.

- Trend: exactly the latest 250 contiguous FINAL, provider-complete UTC-aligned 15m
  candles ending at the last 15m boundary at/before the entry candle's **opening**.
- Entry: exactly 60 contiguous FINAL, provider-complete aligned 5m candles including
  the separate trigger candle. All 310 selected bars must have been available
  at/before the actual supplied **post-close decision time**, `evaluated_at`.
- Availability is `max(observed_at, receive_time)`. Every candle binds its existing
  canonical payload/envelope hash, identity, source, revision and observation UUID.
  Latest expected context missing/late, any hole, forming row or missing envelope
  yields unavailable. There is no fallback to an older context or another source.
  A 15m close simultaneous with a 5m trigger close cannot become that trigger's
  prior context. Future paired evidence and history outside the fixed windows are
  excluded. Each input stream is bounded to 1,024 pairs.
- Both streams have identical complete venue/perpetual/instrument/source/provenance
  identities except timeframe, matching adapter version and fresh canonical receipt
  status. Supported public sources are the existing Binance USD-M/Bybit contracts
  and explicitly identified replay fixtures. Replay is never claimed to be live.
- Identical duplicate rows converge in any input order, choosing earliest available
  receipt. Conflicting same-interval/event revisions refuse implicit replay. A
  qualified signal has a stable natural UUID bound to adapter version, spec, instrument and trigger
  event, plus a full derivation hash. Caller-supplied previously seen UUIDs produce
  duplicate with no new signal. Cross-process durable dedupe remains a runtime task.
- Evaluation before trigger close is unavailable; at/after close + 60 seconds is
  expired. The output records `decision_at` and the maximum original receipt time
  `known_at` separately. A later decision inside that 60-second envelope can use
  newly arrived history; those receipts cannot change an earlier as-of evaluation.

### Receipt timing correction (adapter v2)

The provisional v1 adapter required prior receipts by trigger opening. That made
the immediately preceding 5m bar unusable with any positive delivery delay; a 15m
close at the same opening boundary failed too. V2 changes receipt eligibility to
the decision after trigger close. Candle selection stays explicit: prior trend
ends at/before trigger opening, entry ends at trigger close. This conservative
prior-trend convention does not include a 15m close simultaneous with trigger close.
Neither candle values nor observed/received/recorded arrival times are rewritten.
Future receipts are excluded even if their candle is historical; future candle
intervals remain excluded even if an envelope claims an early arrival. The stable
signal UUID includes the corrected adapter version; decision time is in the full
derivation hash, rather than the natural ID, so repeated evaluations deduplicate.
Thresholds, authored parameter v1, structural stops and rounded gross 1R are unchanged.

## Fixed conservative v1 definitions

EMA arithmetic uses explicit 80-digit Decimal/ROUND_HALF_EVEN context, no floats.
Each EMA is seeded by the SMA of its first `period` closes in the fixed window,
then `EMA[t] = EMA[t-1] + 2/(period+1) × (close[t] − EMA[t-1])`. Extra ancient history
cannot alter the seed/window. 15m EMA20/EMA50 use 250 bars; 5m EMA20 uses 60 bars.

| Rule | Long | Short |
| --- | --- | --- |
| Trend price | Latest closed 15m price and 5m trigger close strictly above trend EMA50 | Both strictly below |
| EMA alignment | EMA20 strictly above EMA50 | EMA20 strictly below EMA50 |
| EMA50 slope | `(EMA50[t] − EMA50[t−3]) / EMA50[t−3] ≥ 0.001` (at least +0.1% over 45m) | Same ratio ≤ −0.001 |
| Structure | In last 32 trend bars, strict pivots with two closed bars on each wing. Last two highs and lows are higher; chronological H1 < L1 < H2 < L2 | Last two lows/highs lower; L1 < H1 < L2 < H2 |
| Pivot ties | Equal wing extremes establish no pivot; unclosed right wings establish no pivot | Same |
| Prior impulse | 5m candle immediately before the three-candle pullback closes > its EMA20 + 0.1% | Closes < EMA20 − 0.1% |
| Pullback | Exactly three prior 5m candles, net decreasing closes and at least one bearish body. Every close ≥ its EMA20 − 0.1%; no low below EMA20 − 0.3%. At least one low ≤ EMA20 + 0.1% | Net increasing, at least one bullish body; every close ≤ EMA20 + 0.1%; no high above EMA20 + 0.3%; at least one high ≥ EMA20 − 0.1% |
| Continuation | Separate bullish closed trigger strictly above all three pullback highs and its 5m EMA20 | Bearish closed trigger strictly below all three lows and its EMA20 |
| Gap | Absolute trigger open/prior close gap ≤ 0.1% | Same |

These thresholds are conservative research definitions, not established edge,
performance, win-rate or tradeability claims. There is no discretionary/model entry,
runner, trailing stop, partial target or intra-bar wick confirmation.

## Structural risk and precision

Long structural extreme = minimum low of all three pullback bars; short = maximum
high. Place the stop one tick beyond that extreme, rounding further outward (floor
long, ceiling short). Research entry is the trigger close rounded conservatively
(ceiling long, floor short). Compute distance **after both prices are rounded**.
Target = rounded entry + distance for long, − distance for short: one full gross 1R
before fees/funding/slippage. Tick-aligned entry/stop/target, positive prices, at
least two ticks distance, rounded risk/entry between 0.1% and 2%, and entry rounding
movement at most 0.1% are required. Otherwise refuse; do not shrink structural risk
or rewrite the target. The chosen stop remains strictly outside the entire pullback.

Instrument rules must be LINEAR USDT, with exact evidence instrument base, quote,
settlement and multiplier. Numeric inputs have at most 24 coefficient digits,
12 fractional places and 24 integer places. Unsupported precision/units fail closed.
Currency/multiplier consistency is not proof of venue rules provenance or freshness.
The supplied rule hash is a research binding; future dispatch must resolve verified
current execution rules and cross-venue basis through canonical authorities.
No quantity, leverage, position management or autonomous admission is produced.

For example, synthetic long entry 125.60, pullback low 124.55, tick 0.01 yields
stop 124.54 and target 126.66. The inverse short at entry 174.40, high 175.45 yields
stop 175.46 and target 173.34. These are fixture geometry, not exchange orders.

## Experiment-config/v1 connection

The experiment service recognizes immutable authored `TrendPulseSpec` and exact
v1 parameters from an organization/user-owned stored strategy version. Both 15m
and 5m must appear in the exact variant universe. Research configurations require
`setup_observation`; `closed_trade` is refused because there is no authorized
TradePlan/execution adapter. Strategy library/UI authoring and compiler registration
remain a later integration responsibility; this batch does not relabel a Nested/SFP
strategy or add a new executable StrategyId. PostgreSQL tests use a dedicated new
MANUAL_REVIEW research library record containing the immutable authored spec.

`bind_experiment_signal(version, *, variant_key, spec, strategy_content_hash, signal)`
is a pure attribution helper for a trusted immutable strategy lookup. It rechecks
configuration hash, variant/version/content hash, exact parameters, family/universe
and signal derivation hash. Output preserves organization, experiment/version/hash,
variant, strategy/version, sample group and **declared** execution account/source/UID.
Changing these fields without resealing the experiment refuses; new approved domain
versions have distinct binding/sample identities. Advisory model policy cannot alter
the detector's rules or geometry.

These tags are not native performance or `ExperimentSourceProof`: account_verified_here,
sample_eligible, native_execution and runtime_activated are always false; performance
is null. A draft can be studied without granting approval. Rebinding an Exploration
signal to a later Validation version does not make it a fresh sample. The trusted
source resolver must separately verify opening inside that version's approved
running interval and reject historical/reused records. No samples are created here.
Default HTTP sample ingestion still returns 503 without the trusted resolver, and
`preview_admission` still refuses TrendPulse with authorized_execution_adapter_missing.
Internal simulation remains separate from BloFin demo, never native performance.

## Runtime dependencies and verification handoff

Before any execution or performance claim, owners must provide reviewed strategy
library/compiler/evaluation/Candidate/TradePlan integration; bounded public evidence
acquisition and durable replay/dedupe; verified fresh BloFin demo UID/connection and
execution-rule/basis/quote proofs; deterministic sizing/lot precision and atomic
account-wide exposure/reservation/loss budgets including manual positions; existing
kill-switch, approval and final dispatch gates; native entry/protection/exit lifecycle
and independent realized-outcome reconciliation; separately trusted simulator/native
source resolvers with fresh Validation attribution. BloFin owns demo execution and
replenishment. Approval alone never dispatches an order or grants manual management.

Focused commands/results and the exact feature SHA are published in the draft PR
and [verification handoff](trendpulse_1r_verification.md). No full backend CI, manual
CI dispatch, deployment, orders, credentials or operator setting changes, Telegram
messages or runtime activation occur in this batch.
