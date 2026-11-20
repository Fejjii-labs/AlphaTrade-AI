# D-line detection research v1

D-line is a **draft, unapproved detection family** (`d_line/v1` / `d_line`).
There is no executable AST, watcher registration, Telegram dispatch, paper/demo
plan adapter, order, fill, partial exit, or stop-movement implementation here.
`POST /strategy-brain/templates/dline` creates an ordinary immutable library
version in draft. Compilation explicitly returns `dline_draft_only`; approval
and executable policy resolution refuse it. An entry below is a research price
reference, never a claim that a next-open entry or retest was filled.

## Using the detector and recording research

`strategy_brain.dline.detector.detect_dline` accepts canonical OHLCV bars with
matching observation envelopes, a `DlineSpec`, an aware evaluation clock, tick
size, optional canonical HTF history and optional explicitly sourced levels.
It returns typed would-be candidates. Long and short coordinate mirrors use
the same Decimal arithmetic. BTCUSDT on 5m, 15m and 1h is the only v1 universe;
other symbols/timeframes are refused, and no watchlist is enlarged.

`strategy_brain.dline.records.record_dline_scan` is an explicit offline caller
boundary. It binds a scan to an owned immutable **draft** version and uses the
existing tenant-scoped Brain setup/event tables. No schema migration is needed.
It does not commit the caller's transaction, create a governed Candidate, or
call notification/execution services. Read these records through the existing
Brain overview/details APIs and the Strategies family/detail screens. Restart
and duplicate-scan tests use an isolated temporary SQLite database only.
There are no production/shared database writes in this implementation task.

Example for a future explicitly authorized offline research caller (not a
worker registration or executable policy):

```python
scan = detect_dline(
    bars, observations, spec, evaluated_at=clock, tick_size=tick_size,
    htf_bars=htf_bars, htf_observations=htf_observations, levels=sourced_levels,
)
setup_ids = record_dline_scan(
    session, organization_id=organization_id, user_id=user_id,
    version_id=draft_version_id, scan=scan,
)
```

Supply the complete causal episode history on replay/rescan. Attempt ordinals
are recomputed from that history; idempotent frozen event IDs survive restart.
A truncated/revised history that changes an existing frozen record is refused,
not silently rewritten. Attempts count qualifying research crossings, not
executed trades or confirmed stop-outs. Durable execution attempt reservations
belong to later actionability work.

## Causality and geometry

A strict pivot needs `pivot_width` closed bars on both sides; equal plateaus
are excluded. Pair-fitted descending highs form long D-lines; ascending lows
form the short mirror. Rank by touches, span, residual and stable anchor order.
Every intervening close must stay on the correct side. Wicks may cross without
triggering. Two touches are accepted at lower ordinal quality; three are
preferred. The slope is ATR per trigger bar, not a geometric chart angle.
ATR is Wilder ATR, frozen from the second anchor's confirmation window.

The previous close must be on/below the long line and the trigger close strictly
above its projection plus the configured buffer (short reverses). Freeze line,
ATR, anchors, evidence and plan at that trigger. Prefix replay tests compare the
entire candidate after future trigger/HTF bars are appended. Forming, incomplete,
future, conflicting, cross-instrument or gapped evidence cannot confirm; prior
receipts cannot become available after a trigger and backdate it.

Breakout records the closed trigger price and next-open **intent only**. Retest
records a fixed break projection only when a subsequent closed bar spans it
within the window, with close-based invalidation. This is an OHLCV research
observation; it cannot establish intrabar order/fills. Later execution must
validate fresh quotes, ordering, slippage and structural validity independently.

HTF bias is close versus EMA with signed EMA slope over closed bars. A causal
rising parent support (short: falling resistance) uses the latest two strict
confirmed HTF pivots. A broken supplied parent blocks continuation, even if EMA
agrees. Parent identity and continuation number are stored. No suitable parent
is recorded as unavailable. Nested confluence calls the existing Nested detector;
only its latest intact confirmed pullback pivot may override the recent minor
swing stop. No Nested trade approval is inherited.

Fib 0.9 confluence uses a declared latest confirmed low/high swing pair and an
authored orientation. The default is deep retracement `B - .9*(B-A)` in long
coordinates; the alternative is `A + .9*(B-A)`. This cannot select an orientation
retrospectively to improve quality. Evidence availability is distinct from false.
The quality score is ordinal, with components/proofs stored; it is not a win rate.
Manual emotional questions are displayed for reflection and do not gate detection.

## Levels, stops and would-be targets

**v1 has no automated OB or POC provider.** Do not infer volume-at-price or order
blocks from these screenshots or relabel OHLCV volume as POC. Optional manual/
replay `SourcedLevel` snapshots require exact instrument/venue binding, source,
evidence reference, timeframe, known-at, expiry, and consumption state. A zone
snapshot supplies its near opposing edge. Wrong-side, expired, future, consumed
or wrong-instrument levels are ignored. Candidates explicitly list automated
OB/POC providers and missing optional confluences as unavailable and retain
source identifiers on targets.

The stop is the latest confirmed pre-break swing, overridden by a valid larger
Nested 2. Apply the larger of ATR buffer and tick buffer, rounding away from
entry. Target Fib is pattern extreme -> first line-anchor extreme, at .618 and
1.0. Merge favorable sourced levels and Fib levels, cluster exact tick prices,
keep up to four (nearest three and farthest). Front-run opposing OB edges by one
tick. On 5m the nearest opposing 15m-or-higher OB caps targets and disables the
runner by default; no remote level can rescue insufficient R:R through that cap.

Trending requires two distinct targets, original Fib TP2 >= minimum R:R, and
final scheduled target >= minimum R:R. Fib-only plans use TP1/default residual
and no runner. With sourced levels, assign TP1, split the remaining scheduled
fraction equally among other targets and leave the authored runner fraction.
Fractions sum exactly to one; these are recorded concepts only. No BE-plus-fees
or partial-exit behavior is implemented. Gross final R:R, estimated cost-adjusted
R:R and weighted scheduled R are stored separately; runner assumed reward is zero.
Estimated round-trip costs are a configurable research assumption, not actual fees.

Ranging uses the configurable close-path efficiency ratio (absolute net move /
sum of absolute close moves), not ADX. This is a v1 proxy permitted by the source's
configurable ranging indicator, rather than the plan's proposed Wilder ADX.
Insufficient range warmup is explicitly unavailable. A ranging plan records a
full exit at the first target, no runner, and requires that target itself to meet
the authored ranging R:R threshold. It never borrows a distant TP2's R:R.

## Parameters and defaults

Every number below is a research hypothesis. Parameters and their hash are bound
to each immutable draft version and candidate. Tune by authoring a new draft spec;
never mutate prior records or promote a version to observe signals.

<!-- Parameter table generated from DlineParameters below. -->

| Parameter | Default |
| --- | --- |
| `version` | `dline-research/v1` |
| `lookback` | `96` |
| `pivot_width` | `2` |
| `minimum_touches` | `2` |
| `preferred_touches` | `3` |
| `minimum_separation` | `2` |
| `touch_atr` | `0.15` |
| `slope_min_atr` | `0.01` |
| `slope_max_atr` | `0.8` |
| `atr_period` | `14` |
| `break_buffer_atr` | `0` |
| `stop_buffer_atr` | `0.1` |
| `minimum_stop_ticks` | `1` |
| `minimum_rr` | `2` |
| `risk_fraction` | `0.01` |
| `entry_mode` | `breakout` |
| `retest_window` | `6` |
| `retest_tolerance_atr` | `0.15` |
| `retest_policy` | `frozen_break_projection` |
| `attempt_cap` | `2` |
| `tp1_fraction` | `0.5` |
| `runner_fraction` | `0.2` |
| `five_minute_ob_policy` | `cap` |
| `ranging_first_target_min_rr` | `2` |
| `range_lookback` | `14` |
| `range_efficiency_threshold` | `0.2` |
| `htf_required` | `True` |
| `five_minute_htf` | `1h` |
| `fifteen_minute_htf` | `4h` |
| `hourly_htf` | `1d` |
| `ema_period` | `50` |
| `ema_slope_bars` | `3` |
| `htf_max_age_bars` | `2` |
| `fib_09_orientation` | `deep_retracement` |
| `fib_09_tolerance_atr` | `0.15` |
| `round_trip_cost_fraction` | `0.001` |


Pending Sofien's refinement: TP1 fraction **50%**, attempt cap **2**, normalized
slope band **0.01–0.8 ATR/bar**, 5m EMA map **1h**, Fib .9 orientation
**deep_retracement**, retest window **6 bars** and policy **frozen_break_projection**,
5m OB policy **cap** (runner zero), and ranging first-target R:R **2**. Each has a
named parameter and the corresponding code comment. No values are calibrated
from screenshots. The provisional efficiency regime proxy and equal residual
ladder allocation also need review before future actionability.

## TODO: walk-forward / out-of-sample gate (documentation only)

Before any approval/arming, pre-register a BTCUSDT evaluation on 5m/15m/1h with
chronologically separated development, validation and untouched test windows.
Freeze the spec hash, periods, level sources, cost/slippage assumptions and
retest ambiguity policy before inspecting test results. Proposed sample-size
floor pending Sofien: **100 qualifying setups per timeframe, with at least 30
per direction** in held-out data. Below that floor, report insufficient evidence
and collect more data; do not lower it retrospectively. Report counts, missing
level/HTF coverage, hit rate, R distribution, MAE, time-in-trade, drawdown and
maximum adverse move after TP1, broken down by direction, two/three touches and
regime. Chart fixtures establish behavior only and are not evidence of returns.
No evaluation engine, backtest performance claim or execution code is added now.
