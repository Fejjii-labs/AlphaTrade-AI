# SFP REST candle finalization and recovery

## What the evidence establishes

The supervising live report identifies observation
`55121195-4489-5d6e-b733-6c23c2f4e618`: Binance USD-M BTCUSDT 15m,
October 5, 2026, 21:15–21:30 UTC, first received at
21:30:00.614496 UTC, marked final at revision 1. Stored base volume was
`238.970`, quote volume `20494228.80890`, and trade count `8528`. Later
worker reads conflict on those three values and `content_hash`.

This is a genuine same-revision conflict. PR207's decimal-equivalence fix correctly
refuses it. SFP remembers every candle in its overlapping history; both directions
share these public receipts and therefore fail on the same candle. Nested does
not reuse these durable SFP receipts, explaining its different observed outcome.

The old Binance REST adapter supplied no provider completion flag to
`build_ohlcv_bar`. The builder inferred completion from local time crossing the
interval end; the adapter used zero grace and ignored the native close timestamp.
Consequently, the 614 ms receipt was admitted as final without a settlement check.
The focused reproducer matches the exact observation UUID, stored volumes/count,
and all four conflicting fields. OHLC and later numeric increments in the
reproducer are synthetic: the complete original/later live responses were not
independently captured in this task. The application admission defect is confirmed;
upstream late aggregation versus a later provider correction is not independently
established. Neither provider closeTime nor two matching REST reads is a native
finality acknowledgement or an absolute guarantee against later changes.

## Correction and immutable recovery boundary

Live Binance REST adapter **v2** changes the actual acquisition contract:

- Validate the native inclusive close timestamp against the canonical half-open
  interval end minus one millisecond; require a native integer trade count.
- Refuse a just-closed row inside a five-second settlement guard, even when older
  candles could satisfy the requested minimum. Do not substitute an older trigger.
- Pin the request window with `endTime` and use at most two confirmation GETs per
  adapter invocation. Eligible closed rows must match in every typed field; normal
  decimal formatting differences compare equal. Forming rows remain ineligible.
- Mark only guarded, confirmed rows final. The five-second floor excludes the
  reported subsecond race; it does not assert a Binance settlement SLA. Further
  same-policy changes continue to fail strict immutable receipt validation.
- Stamp new SFP receipts using the injected clock after acquisition/confirmation.
  Reuse existing receipt clocks exactly on later scans and restarts.

Source and provenance explicitly identify `binance-usdm-perpetual/v2`; actual
live bar/trade normalization carries the same bundle version. Replay stays at v1,
and Bybit is unchanged. This is an application adapter policy version, **not a
fabricated provider candle revision**. Provider revision remains 1.

The existing receipt primary key is `(identity_hash, observation_id)`. Changing
the real adapter policy produces a distinct identity hash, so v2 reobservations
append beside v1 history without deleting, rewriting or reclassifying it. The
natural candle observation UUID can be identical across these policy identities:
always inspect the complete key. Historical plans, evidence windows and source
references retain their original v1 hashes and clocks.

No receipt repair SQL, revision increment, exception allowlist or automatic
same-policy conflict recovery is introduced. v2 does not merge v1 into its active
history. Its historical download is known only at the new receipt time; causal
warm-up can legitimately produce no setup or unavailable structural levels.
Past confirmation is not backdated or replayed into a new Candidate. Active
strategy versions, permissions, risk and execution activation are not changed.

## Bounded supervised staging procedure

No staging reads, deployment, repair or exchange orders were performed here.

1. Review the PR and this policy/history boundary before deploying. Capture the
   worker's later raw **public** candle values, request interval and receipt times
   in the supervising evidence record. Compare them with the reported stored v1
   row. Preserve the original row and any referencing assessments/plans. If other
   instruments, intervals, prices or provenance differ, investigate those separately.
2. Record the original full receipt key, JSON, content hashes and clocks with this
   bounded read-only query (no credentials in the evidence record):

   ```sql
   SELECT identity_hash, observation_id, payload, ohlcv
   FROM public_market_observations
   WHERE observation_id = '55121195-4489-5d6e-b733-6c23c2f4e618';
   ```

3. Through the supervising release process, deploy the exact reviewed SHA and
   restart/drain workers so a scope has one deployed adapter policy. Keep demo
   disarmed and real trading disabled. No strategy reapproval or credential change
   is needed. Verify new source/provenance reports v2; do not alter stored v1 rows.
4. Allow the existing worker poll to retry after the five-second guard. Failed
   Watcher attempts are retryable; only successful attempts replay. Check both SFP
   scopes within at most **two 15m scheduled boundaries** after worker health is
   restored. A legitimate `no_setup` or causal warm-up result is acceptable;
   `canonical_contract_invalid_contract`, mixed-version errors and repeated missing
   acquisition are not successful evidence acceptance. Verify Nested still evaluates.
5. Confirm new v2 receipts have current post-acquisition clocks, unchanged provider
   revision 1 and correct volume/count values. Re-run the read-only known-UUID query:
   the original v1 row must match its snapshot exactly. If a v2 row for this candle
   is in the bounded provider window, it has a different identity hash and a later
   receipt time. Do not require a new row once the candle ages out of that window.
6. Repeat one scope and restart once to verify receipt/key/hash stability and no
   duplicate canonical result. If v2 conflicts recur, stop acceptance, retain the
   refusal and collect UUID, full key, finite conflicting field names and separately
   reviewed public values. Do not increment revisions, delete evidence or expand
   a grace window until the new conflict is explained and reviewed.

Normal request budgets/backoff still apply. Each adapter invocation adds one
confirmation GET; the existing monitor's bounded partial-history fallback may make
another invocation. Native limits remain capped at 1500 rows per request (normal
SFP request is its existing history bound). Do not force tight manual polling.

Focused tests provide development evidence, including real local PostgreSQL and
mock HTTP confirmation. Live scheduled success remains a supervising acceptance
step. No full backend run is launched here; the existing consolidated exact-SHA
release dispatch with `full_backend=true`, evaluation and browser smoke remains
the final complete release gate.

## Development verification

248 distinct focused cases passed: the core adapter/market contracts, SFP receipt
reuse/runtime/detector, Nested, reliability and freshness batch passed 216 cases.
The extended source/native-metadata, existing adapter, governed-demo and Watcher
port batch passed 66 (34 overlap); the final 26 source cases were repeated after
clock binding and typing cleanup. This includes exact-UUID reproduction and
SQLite/PostgreSQL policy-history and both-scope restart checks. The two admission
regressions fail on untouched main before the fix.

Scoped Ruff/format passes. The three contract modules pass strict mypy with
dependency reports suppressed; shared Brain assembly has the same 36 existing
strict errors as untouched `60fed16`, with no added error after line normalization.
No production conflict guard or SFP version validation was weakened to pass tests.

## 24 November 2026: v2 recurrence and adapter v3 (60 s admission)

**Recurrence (staging, diagnosed 23 Nov).** The v2 five-second guard did not
hold. The Binance USD-M BTCUSDT 15m bar 11:45–12:00 UTC was stored as a final
v2 receipt at 12:00:06 UTC with 18,553 trades. Later worker reads of that bar
returned a different close, volume and trade count, so every later SFP scan on
BTC 15m failed with `canonical_contract_invalid_contract` (same-revision
conflict). The bad receipt stays inside SFP's 256-bar lookback for about 64 h,
and retention is off on staging. ZEC 15m failed the same way. Only SFP was
affected, because only SFP stores durable receipts. **Evidence gap:** Binance's
later values for that bar were not captured; Binance is blocked from the
development machine, so the size of the change and when it happened are not
known. This recurrence therefore shows that 5 s is not enough. It does not show
that 60 s is enough.

**Decision.** Reviewed with the owner (Sofien) on 24 Nov, who chose option B:
raise the live REST admission grace from 5 s to 60 s **and** bump the adapter
policy to `binance-usdm-perpetual/v3`. As in the v2 change above, the new policy
version gives new receipts a different identity hash, so stored v2 rows (including
the stuck BTC/ZEC bars) are not read as v3 history and are not edited or
deleted. The stuck SFP scopes start clean on v3 receipts after deployment.
Provider revision stays 1; no repair SQL, revision bump or allowlist was added.
A same-policy conflict under v3 still fails closed.

**Scope of the 60 s value.** It applies to the Watcher's strategy evidence reads
(the path that stores SFP receipts), the live monitor and symbol probes. To keep
the worker loop free, the worker never sleeps more than 5 s for it. Earlier in
the window a new bar is *not yet due*: the scan's idempotency key stays on the
previous bar, which replays, and the next 15 s poll scans the new bar once it is
admitted. If the previous bar's attempt had failed (as the stuck scopes had at
deploy), that replay can still record a `forming_candle` failure in the first
window. A forming-candle retry whose wait crosses into the new bar's window is
skipped rather than rerun under the previous bar's key. Symbol probes check the
previous settled close inside the window. A provider stuck one bar behind therefore still passes
the probe for up to that minute (a 60 s detection gap).

Two readers keep the v2 five-second floor (`BINANCE_REST_FAST_READ_GRACE`), as
before this change: the paper exit monitor (so stops and targets react as
quickly as before) and TrendPulse screening (whose trigger expires 60 s after
close). TrendPulse screening rows do store the bars they read
(`TrendPulseScreeningRow.evidence`), labelled with the v3 adapter, so a v3 label
covers both a 60 s and a 5 s admission; those rows are screening evidence, not
SFP Watcher receipts. Bars shorter than two minutes are admitted after half a
bar (30 s for 1m), because a 60 s guard would refuse every 1m read. Shared kline
caches are keyed by grace, so a five-second read is never reused as a 60 s
evidence read; the exit monitor and evidence reads therefore no longer share a
closed-kline payload (one extra confirmation pair of Binance reads per symbol,
interval and close).

Trade-offs: new 15m strategy evaluations, including Nested and D-line, start
about 60–75 s after close instead of 5–20 s. Other 60 s readers (the evidence
pipeline API, the `probe_exchange` CLI and paper validation) fail closed for the
first minute after each close; paper validation records that as a blocker on
the run. The live monitor's OHLCV block shows `SettlementGuardError` during that
minute. The v3 identity changes evidence-window hashes, so a Nested or D-line
setup that spans the deploy gets a new candidate id.

**Stronger later fix (option C, not done here).** A time grace is still only a
heuristic. A trade-count consistency check is stronger: before admitting a bar,
compare the kline trade count with an independent count for the bar's window
(for example the first/last trade-id span of its aggTrades). Under
the review rule above, a further v3 conflict means stopping and investigating
again, not raising the grace.
