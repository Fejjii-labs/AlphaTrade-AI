# D-line strategy implementation plan

Date: 2026-11-16. Status: proposed, implementation approval pending.
Repository: Fejjii-labs/AlphaTrade-AI. Baseline: `b319d4fbc02fb7e539bf95d37d45fe3147be1e08`.
Requested worktree/branch: `/workspace/at-dline`, `claude/dline-strategy`.
Agent/claim: Codex; strategy-brain planning track; this plan only inside the repository, plus `/workspace/at-notes/dline/plan-report.md`. No issue number supplied, so no issue claim comment can be posted.

This task produces a local documentation commit only. The explicitly requested existing `claude/` branch is an exception to the usual agent prefix convention. No push or draft PR is authorized now. When publishing is authorized, open `docs: dline strategy plan (for review)` using the PR template, and obtain Griller GO plus Sofien's own plan approval before code. This plan grants no scanner activation, strategy approval, arming, deployment, shared-database migration, environment change, or exchange action.

## Source interpretation

Read `/workspace/at-notes/dline/prompt.md`, including its overriding addendum, and visually inspected all seven supplied images. The current request is PLAN ONLY; implementation instructions and test commands in that source describe future work.

| Source | Planned behavior and interpretation |
| --- | --- |
| `checklist.jpg` | Closed body break; preferably three touches; normalized slope; bigger trend; minimum 2R; risk ceiling 3%; recent structural stop; manual emotional checklist. |
| `example-btc-15m.png` | Small descending entry line inside a larger coil; Fib 0 at 79,458.4 and 1 at 80,817.9 gives 0.618 = 80,298.5. The screenshot entry label is about 79,882.9 versus the prose's approximate 79,950; neither becomes a hardcoded price. |
| `example2-btc-15m-htf-target.png` | Stop near 73,351; opposing 2H OB/daily/weekly nPOC cluster near 75,800–75,900 supplies structural targets. |
| `example3-btc-1h-trade-series.png` | Multiple attempts, TP1 fee protection, up to TP4, 15–20% runners, and 0.9 Fib retest confluence. Two attempts is a conservative configurable initial limit, not a claim to reproduce every illustrated trade. |
| `example4-btc-5m-12-structure.png` | Existing 1-2 higher-low context supplies invalidation around 82,462.9, even when the small line's latest low is higher. Distinguish the larger confirmed “2” from the small projected “2.” |
| `example5-btc-5m-range-poc.png` | Entry about 83,517.8, stop 83,356, intermediate range POC 83,977.8, opposing 15m OB near 84,466. Do not target the farther 85,820.7 POC through that OB by default. |
| `example6-btc-5m-nested-dlines.png` | Successive distinct descending lines within a rising parent support trend; preserve parent-trend identity and continuation number. Daily 60,849.7 and nPOC 61,762.7 are context, not constants. |

Screenshots are qualitative references, not historical OHLCV or evidence of profitability. Reconstructed fixtures must be labeled synthetic. Short behavior mirrors long behavior. Initial strategy scope is BTCUSDT, 5m/15m/1h, with both directions supported and individually selectable.

## Existing architecture and concrete file map

Nested uses `schemas/nested_continuation.py`, `strategy_brain/detector.py`, `assessment.py`, `records.py`, and `assembly.py`. SFP separates pure rules in `strategy_brain/sfp/` from integration in `strategy_brain/sfp_runtime/`. Follow the SFP family split and the shared `BrainSetupState` lifecycle; do not build a second scanner, approval service, candidate store, journal, or notification transport.

Paths below are relative to `backend/src/app/` unless prefixed otherwise. New filenames are proposed; existing names were inspected.

| Add/change | Purpose |
| --- | --- |
| Add `strategy_brain/dline/{__init__,contracts,pivots,trendlines,detector,confluences,levels,quality,trade_plan}.py` | Typed versioned spec, Decimal math, causal fitting, pure lifecycle observations, target evidence adapters, explainable quality, stop/ladder derivation. Reuse canonical OHLCV contracts and proven helpers where possible; avoid changing SFP numerical semantics. |
| Add `strategy_brain/dline_runtime/{__init__,assembly,assessment,records,notifications}.py` | Bind canonical trigger/HTF/level evidence; family-specific evaluation; tenant-scoped setup projections/events; durable line/attempt operations; persisted alert summaries. Mirror `sfp_runtime`, reuse shared transitions and candidate/assessment ports. |
| Change `schemas/common.py`, `schemas/setup_ast.py`, `schemas/strategy_library.py` | Add strategy/family discriminator and typed authored/compiled spec slot; maintain exclusive family validation and omission of absent fields from old hash preimages. Confirm all family literals in these surfaces during implementation. |
| Change `services/setup_ast_compiler.py`, `signal_fusion/strategy_evaluation_policy.py`, `signal_fusion/evaluator.py` | Compile D-line, bind immutable parameter/spec hashes, dispatch its assessment adapter, bind role timeframes and required evidence. Extend every relevant family allowlist rather than falling into Nested's `else`. |
| Change `strategy_brain/service.py`, `api/routes/strategy_brain.py`, `strategy_brain/agent.py` | Draft template creation and strategy card, family overview/details, candidate explanation; expose confluences, plan availability, checklist, execution permission and reasons. Add `/templates/dline`; preserve current routes. |
| Change `strategy_brain/assembly.py`, `evidence_pipeline/watcher_port.py`, `watcher/fusion_evaluation.py` | Explicit family dispatch for live/replay acquisition and assessment; version-symbol/timeframe scope intersected with enabled tenant watchlist; no scan widening. Preserve observation receipts and evidence availability. |
| Add `candidate_alerts/dline.py`; change `candidate_alerts/{contracts,content,projection}.py` and watcher alert dispatch as needed | Existing gateway/dedup/authority/Telegram delivery, candidate deep link, ladder and runner, fees, line attempt and observed quality. Alerts never authorize execution. |
| Change `services/automated_paper_loop.py`, `services/internal_paper_plan_terms.py` | Canonical D-line plan binding, current-price validation, entry mode, effective risk limits, eligibility and existing approvals. Never reuse a generic single-target fallback for an unsupported D-line plan. |
| Change `services/paper_exit_monitor.py`, `services/paper_close_economics.py`, `schemas/execution_protocol.py`, `services/canonical_paper_execution.py` as necessary | Add governed partial reductions, remaining quantity, per-fill fees/PnL, effective stop changes and runner exit. Preserve existing full-close contracts for Nested/SFP. This is a separate reviewable dependency, not assumed current capability. |
| Reuse `schemas/trade_plan.py`, canonical plan repositories and journal lineage | `RiskAndExits` already expresses multiple `ExitTarget`s and `RunnerRules`; preserve stored V1 hash semantics. Extend with a new explicit contract version only if current derivations cannot bind required rules. |
| Add `db/dline.py` and one autogenerated Alembic revision if needed | Durable line lineage/attempt reservation plus paper partial-exit progress; transactionally unique fill/event IDs. Prefer existing JSON setup/event payloads for detection fields. No blanket rewrite of old setups. |
| Change `services/strategy_replay_adapter.py`, `services/strategy_replay_service.py` and relevant replay request schemas | Family adapter on existing replay path, typed causal HTF/level inputs; missing historical level provenance remains unavailable. Do not label research-only scans as filled trades. |
| Change `frontend/src/lib/api/brain-types.ts`, existing strategies family/detail components and `components/journal/JournalTradeScreen.tsx` | D-line family, parameters/confluence explanations, targets/runner/protection, manual-only emotional checklist and journal linkage. Add focused component tests; retain routes/deep links. Follow repository design direction for any affected redesign phase. |
| Add `backend/tests/test_dline_*.py` and synthetic fixture helpers | Behavior and persistence tests listed below; extend existing compiler/version/candidate/watchlist/exit integration tests. |

Implementation discovery must confirm exact authoring unions and alert dispatch symbols before editing. `services/strategy_versioning.py` remains the lifecycle authority; template creation already returns `draft`/permission `none`. `strategy_brain/service.py` has a conservative SFP permission label; adding D-line must not accidentally claim SFP support or relax any gate.

Important verified gap: `paper_exit_monitor.py` currently accepts one full-size target and a disabled runner; its `_levels` rejects multi-target/runner plans. Schema support alone is insufficient. D-line stays observable but non-actionable until partial exits, protection and runners are integrated end to end and tested.

## Deterministic detection and evidence

1. Validate instrument, venue, market type, timeframe, contiguous canonical final/provider-complete candles, positive tick size, hashes and original observation availability. Evaluate only data whose candle end and observation known-at/received-at are no later than the scan clock. Reject gaps, stale required data, insufficient ATR/EMA/ADX warmup, conflicting revisions or cross-instrument evidence with explicit reason codes.
2. Confirm a strict pivot at index `i` only after `pivot_width` right-hand candles close. Equal highs/lows use a versioned deterministic tie policy (earliest plateau representative). Store pivot time and confirmation time separately. Candidate chronology never moves back to the pivot time.
3. For long entries, enumerate separated confirmed descending high pairs within the lookback; short entries use ascending lows. Fit `L(k) = price_a + slope * (k-k_a)` through two anchors. Initial implementation uses pair fits, not a moving least-squares fit: collect additional distinct confirmed pivot touches satisfying `abs(pivot_price-L(k)) <= touch_atr * ATR_at_pivot`. A touch must be on a separate pivot at least the configured bar separation away. Rank by touch count, span, smaller normalized residual, then stable anchor IDs. Freeze the selected line when armed; later candles do not refit its past signal.
4. Require at least two touches; three earns preferred quality. Reject wrong-sign slope, excessive age, inadequate span, or pre-break closes beyond the line on the breakout side. Wicks are allowed within the configured excursion budget and never count as breaks. Check every candle between anchors/touches and up to the first eligible break. Use close-based penetration, not a rule requiring the entire candle body beyond the line.
5. Normalize `abs(slope)/ATR_at_line_arming` in price per trigger bar, using the frozen ATR snapshot for that line. Reject outside the authored band. This is an operational steepness proxy, not a literal 45-degree geometric conversion. Retain signed slope, ATR and normalized slope for review.
6. Long trigger: previous closed candle is on/below its projected line and current candle closes strictly above its projected line plus configured break buffer; short reverses this. A wick-only pierce, equality, or historical already-crossed line cannot trigger. Once triggered, the line can remain reusable as described below, but does not produce a candidate on every candle above it.
7. Breakout mode records a confirmed setup at the break close, with next-open entry intent. Replay fills no earlier than the next candle's open. Runtime uses the first eligible fresh executable quote after confirmation, with explicit maximum entry delay and slippage tolerance; a closed-candle-only provider must not invent a next-open fill or backdate a later quote. Recompute geometry/R:R and sizing at actual fill. Expired/gapped-away entries are refused.
8. Retest mode arms after that break and fixes a limit at the line projection at break time (default); record this policy explicitly rather than silently chasing a sloped moving limit. Only subsequent bars can fill within tolerance/expiry. Require price to return to the band from the breakout side; invalidate on structural failure or a closed loss of the line beyond tolerance. A live paper limit needs canonical quote/trade evidence; OHLCV replay with ambiguous stop/entry ordering takes the conservative outcome. New moving-line-limit behavior would require a new parameterized policy/version.
9. Lifecycle uses `NO_SETUP -> WATCH -> FORMING -> CONFIRMED -> TRADE_CANDIDATE` with existing `BLOCKED_BY_RISK`, `INVALIDATED`, `EXPIRED`, `COMPLETED`. Retest waiting and attempt phases are typed payload substates/events, not a parallel setup lifecycle. Observation-only confirmation does not confer trade permission.

## Confluences and target provenance

HTF defaults: 5m -> 1h (proposed), 15m -> 4h, 1h -> 1d. Fetch only completed HTF bars available at the trigger clock; do not use the developing higher candle. Default EMA bias requires long HTF close above EMA50 with positive EMA slope over three closed HTF bars; mirror short. Alternative versioned `structure` bias uses confirmed higher highs/higher lows. Missing required HTF data blocks confirmation/actionability rather than silently removing the filter.

Also fit causal rising HTF support for longs (falling resistance for shorts) with the same anchor-proof discipline. When a valid parent line is present, default requires the latest closed HTF price and current trigger close to hold its projected support/resistance within tolerance; a broken parent blocks continuation even if EMA bias agrees. Record parent ID, last hold, touches and projected price. An authored `htf_trendline_required` switch may require it; absent optional parent evidence stays unavailable. Successive fresh D-lines have distinct IDs and continuation ordinals under the same parent.

Reuse `detect_nested`'s causal anchor/impulse/pullback output for 1-2 context on the applicable timeframe, rather than inventing a competing 1-2 definition. Bind to the latest intact, confirmed pullback “2” known before the D-line trigger. Record sequence/setup IDs and proof bars; if present, choose that “2” as stop anchor, even if it is farther away. No nested trade approval is inherited. A simple screenshot structure that does not meet Nested rules is unavailable confluence, not fabricated alignment.

Fib 0.9 retest quality requires a declared causal swing pair: for a long pullback, impulse low `A`, impulse high `B`, deep retracement `B - 0.9*(B-A)`; shorts mirror. Retest price must lie within tolerance of that price. Store anchors, orientation and formula. This is separate from target Fib with 0 at pattern low and 1 at trend-start high; screenshots do not settle the 0.9 orientation, so this proposal requires review. Do not retrospectively select whichever Fib orientation fits best.

Build typed level inputs: price/zone bounds, kind (range POC, session/daily/weekly naked POC, opposing 15m/2h OB, daily/weekly level), timeframe, instrument identity, source, anchor/window, known-at/expiry, freshness and evidence references. Reuse existing contracted intelligence/level evidence only if it actually provides those proofs. The inspected SFP levels expose swing/range/HTF references, not a complete OB/volume-profile provider.

For v1, support explicitly sourced manual/replay level snapshots through existing evidence envelopes and available deterministic references. Never relabel an OHLCV volume approximation as exchange volume-at-price POC. Automated POC/OB derivation is a separately validated dependency: it needs a versioned range/session definition, binning or source profile, causal OB confirmation/mitigation and naked-level consumption rules. Missing optional levels reduces available targets/quality; it must not synthesize a target from a screenshot. If required by the authored target policy, missing levels refuses the plan.

Quality is ordinal and explainable: proposed points = touches capped at 3, HTF bias aligned +1, intact HTF support/resistance line +1, nested 1-2 +1, 0.9 retest +1, sourced structural target +1. Store every component's availability, value and proof; missing is distinct from false. Two-touch setups are lower quality; no score implies probability. No execution gate on emotional state: show the four human questions on manual candidate/journal review, keep answers user-authored and optional.

## Stops, target ladder, fees and sizing

- Base stop is most recent confirmed pre-break swing low for long (high for short), overridden by a valid nested “2” anchor. Place outside by `max(stop_atr_buffer * ATR_at_break, min_stop_ticks * tick_size)`; round away from entry. Missing valid anchor or zero/wrong-side distance refuses the plan. Never tighten solely to manufacture 2R.
- Target Fib long: `F(r) = pattern_low + r*(trend_start_high-pattern_low)`; short: `pattern_high - r*(pattern_high-trend_start_low)`. Freeze anchors at trigger; 0.618 is the initial Fib partial and 1.0 the original trend-start swing. A line can start from a later lower high; its own start swing is the target anchor, not necessarily the chart's global extreme.
- Merge favorable-side Fib 0.618/1.0 and sourced POC/OB/daily/weekly levels; reject stale/consumed/wrong-side levels. For zones use the near opposing edge with one-tick front-running. Sort by distance in trade direction and cluster within target tolerance, retaining all provenance. Choose up to four ordered distinct levels; keep the nearest obstacle and the final structural cap, selecting intermediate levels by stable source priority (POC, HTF level, Fib) then distance when capacity is exceeded.
- On 5m, nearest opposing 15m-or-higher OB is the default cap: no scheduled target or runner continues through it. Default runner disabled under this cap and its fraction moves to final target. Without that cap, final selected target must be structurally justified. With only Fib evidence, preserve the original two-target form; no fabricated TP3/TP4.
- Require at least two valid ordered targets in trending mode. Minimum gross R:R to original Fib TP2 and to the final scheduled exit is 2.0; both checks apply when Fib TP2 exists, and no final target can be chosen beyond a nearer hard OB cap to rescue a failed ratio. Record gross, net-cost-adjusted final R:R and weighted scheduled payoff separately. Runners contribute zero assumed reward to eligibility. If only structural levels are available, require final target >=2R and record `fib_tp2_unavailable`.
- Default four-target fractions of initial size: 40%, 20%, 10%, 10%, runner 20%. Three targets: 40%, 20%, 20%, runner 20%; two targets: 50%, 30%, runner 20%. Runner configurable to 15–20% (or zero); distribute the changed residual to final target. Fib-only compatibility preset: 50%/50%, no runner. Fractions plus runner must equal 100%; never duplicate a level to fill a slot.
- Ranging regime uses causal Wilder ADX14 <20 (proposed). Override all ladder/runner presets: first valid favorable target gets 100%, runner zero, `aggressive_tp=true`. Require that actual full-exit target >=2R; a remote TP2 cannot justify a near, sub-2R full exit.
- After TP1 is actually filled, move remaining stop to breakeven plus fees/slippage and already accrued funding using shared close-economics helpers. Solve for zero net PnL on remaining entry/exit costs, conservatively ignoring prior partial profits; round long protection upward, short downward. Persist exact inputs, effective time and protection event. Never widen an already tighter stop. If price already crossed the protective stop, close conservatively rather than storing impossible protection. No same-candle retroactive protection credit.
- Runner activates after last scheduled TP and trails the last confirmed trigger-timeframe swing with ATR buffer, monotonically tightening. Stop or maximum holding age closes it; parent-line failure may close on the next eligible quote when that policy is enabled. All exits flow through existing paper execution/journal/audit interfaces and have idempotent quantity/PnL updates.
- Sizing default 1% of current equity, strategy hard ceiling 3%. Reject authored values above 3%; effective risk is the minimum of strategy, account, bot and existing platform limits. Reserve costs and slippage in maximum loss and round quantity down to instrument lot/minimum rules. Keep separately labeled account and bot trade caps; no cap or loss breaker is relaxed to meet this strategy's maximum.

## Stable identity, re-entry and persistence

Line identity uses venue/instrument/trigger timeframe/direction plus original confirmed anchor natural-event IDs and a versioned geometry policy; bar array indexes and sliding-window origin are excluded. Freeze anchor lineage across scans. Additional touches or small fit revisions cannot reset attempt counters. Overlapping fits sharing the same origin/anchor lineage are aliases of the existing line, not a new quota. Maintain lineage across strategy parameter versions for account attempt enforcement, so a new approved version does not reset an exhausted line.

Default two filled entries per line (initial + one re-entry), scoped to tenant/account and line lineage. Candidate refreshes, alert retries, rejected risk checks and unfilled expired intents do not consume a filled-entry slot. Atomically reserve a slot before the entry command; pending reservations block competing workers, successful fills consume it, failed/expired commands release it. Reconcile reservations after restart using the canonical command/fill record. One open position per account/line, including a runner. Re-entry requires prior stop-out (including fee-protected stop), a later closed return to the non-breakout side and a fresh body-close crossing or separately configured retest event, all original evidence and HTF/geometry/risk gates passing. No re-entry on the same break candle. Structural invalidation, line maximum age or broken required parent ends the line. New later-origin lines may be continuation entries but remain under account/bot caps and existing cooldowns.

Persist in existing tenant-scoped `BrainSetupRow.payload` and append-only `BrainSetupEventRow.payload`, with canonical candidate/assessment/plan references:

- Family/spec/parameter/schema/quality-policy versions and hashes; strategy/version/setup/candidate IDs; organization, instrument identity, timeframe/direction; observed/known/expiry clocks; source event IDs, revisions, hashes and bar/observation proofs.
- Stable line ID/lineage/alias IDs; immutable anchor IDs, prices/times/confirmation times; line coefficients, fit residuals, touch IDs/count, slope/normalized slope, ATR and warmup window; arm/break/retest times and reason codes.
- Entry mode/intended and actual entry, fixed retest-limit policy, band/expiry, attempt/reservation IDs, prior attempt/exit link, attempts consumed/remaining; parent trend ID/continuation ordinal.
- Each confluence component (including unavailable), HTF map/EMA or structure values, HTF line hold/failure, nested sequence and “2” anchor, Fib pairs/orientations and 0.9 proximity, regime/ADX/aggressive-TP flag.
- Stop anchor/buffer/original stop; ordered target prices/fractions/source level IDs/zone edges and merge decisions; runner rules/fraction; gross/net R:R, weighted payoff, equity/risk inputs/cost assumptions, effective limits and precision rules.
- Required evidence availability, refusal reasons, current lifecycle/risk state; approvals/eligibility/plan revision/journal linkage; optional human checklist reference. Never store secrets or invented approval state.

Execution progress is durable, separate from immutable detection facts: remaining quantity, per-target fills, paid fees/funding, effective stop revision and activation time, runner state, last processed candle/quote/event and unique reduction IDs. Use transactional row locks/unique constraints; JSON counters alone are insufficient for cross-worker safety. Generate a reversible migration only for missing durable structures; test upgrade/downgrade on isolated Postgres and coordinate the single migration owner. No migration or data write occurs in this planning task.

## Test-first implementation list

Write each behavior test failing before implementation. Use synthetic canonical OHLCV/observation fixtures with explicit clocks, ticks and receipt times, fake providers and isolated Postgres where required.

1. Three-touch descending line yields one long candidate with exact entry intent, anchors, stop, Fib and ladder; two touches emits lower quality; short mirror has symmetric geometry.
2. Wick-only/equal-line close gives no signal; earlier intervening body-close violation rejects the original line; adjacent same-pivot touches do not inflate count; wrong-sign/flat/steep/out-of-band slopes reject, including exact boundaries.
3. Prefix replay invariance/no lookahead: delayed pivot confirmation, HTF candle finality, late receipt, future 1-2/Fib/OB/POC evidence, revised candles and insufficient warmup; adding future bars never changes an earlier emitted event.
4. Fixed-anchor slope and line identity survive rolling windows/restarts and extra touches; deterministic fit tie-breaks; aliased/near-duplicate fits and new strategy versions cannot reset attempt limits.
5. Breakout enters only after confirmation on next eligible event; next-open replay gap, live quote delay/slippage and changed fill R:R are refused correctly. No invented next-open price.
6. Retest only after break, correct fixed projected limit/tolerance, no-touch expiry, structural loss cancellation, ambiguous entry/stop candle and duplicate retest scan behavior.
7. HTF EMA bias/structure modes, missing required data, higher support hold versus break, parent and successive-line continuation IDs; no long while required rising support is lost.
8. Nested 1-2 known before break boosts quality and sets stop below its larger “2”; absent/unconfirmed/wrong-direction nested context gives no invented confluence. Test 0.9 orientation, anchors, tolerance, unavailable inputs and short mirror.
9. Ladder includes intermediate range POC then nearest opposing 15m OB; HTF 2H/daily/weekly/nPOC cluster dedups without losing provenance. Stale/consumed/wrong-side levels disappear; cap prevents targeting through OB. Two/three/four targets, fractions sum to one, 15%/20% runner, Fib fallback and unavailable required levels.
10. Stop rounding/buffer, recent-low versus “2” anchor, zero/negative geometry, TP2/final R:R <2 and boundary 2; ranging full-exit target must itself clear 2R. Risk 1% default, rejection above 3%, tighter existing limits and distinct account/bot caps.
11. TP1 partial fill moves remaining stop to BE-plus-fees only afterward; positive/zero fees, funding/slippage, short rounding, prior tighter stop, already-crossed protection, residual dust/lot size and same-candle stop/target ambiguity.
12. Up to TP4 reductions and runner trailing/expiry are idempotent across retries/crashes. Exact remaining quantities, cost attribution and total journal PnL reconcile; unchanged legacy one-target Nested/SFP closes retain behavior.
13. Two filled attempts allowed, third blocked; blocked/expired unfilled candidate not counted; concurrent reservations admit one fill; restart recovers pending reservation; open runner blocks re-entry; new distinct line continuation keeps parent attribution and account cooldowns.
14. Draft/unapproved version cannot be selected for paper decisions; approved-but-unarmed, suspended, stale evidence, kill switch, failed eligibility, account/bot cap and unsupported-exit capability refuse action. Tenant isolation and watchlist intersection exclude ETHUSDT and disabled BTCUSDT; only 5m/15m/1h versions scan by default.
15. Existing family spec hashes/compiler identities and Nested/SFP notification tests remain unchanged; alerts carry persisted target/stop and deep links, dedup per attempt, no send without existing notification authorization. Manual checklist does not gate automated detection.
16. Reconstructed fixtures for all six chart examples: base Fib geometry; HTF target cluster; stopped/re-entered trade series; larger “2” stop; range POC -> 15m OB cap; parent-line continuation breaks. Assert behavior rather than exact screenshot candle history or profitability.
17. Isolated Postgres migration upgrade/downgrade, transaction rollback, duplicate partial exits and tenant/account reservation constraints. Frontend tests cover family details, unavailable confluences, ordered targets/runner and manual checklist.

During iteration run only affected modules. Requested future regression command from `backend/`:

```bash
env -u CI uv run --extra dev python -m pytest tests -k "dline or strategy_brain or sfp or nested" -q
```

Add focused paper-exit/close-economics, compiler/versioning, candidate-alert and watchlist modules when changed; the supplied expression alone does not cover all new safety surfaces. Record exact command, exit code, pass/fail/skip counts; do not invent counts. At each merge-ready implementation batch run once, from `backend/`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest` against isolated Postgres when persistence applies. Frontend changes require its lint/typecheck/tests/build gate. No `workflow_dispatch`; iterative commits carry `Full-Backend: false`, merge-ready PR records full-gate evidence. Re-run the full local backend gate after any push following review or an authorized main integration.

## Review sequence and rollout

Each implementation PR remains one concern, roughly <=400 changed lines excluding generated migrations/lockfiles; split phases further as needed. No code before Griller GO and Sofien plan approval.

1. Pure contracts/fitting/detection with failing synthetic tests first; no runtime registration capable of execution.
2. Confluences and target planning with evidence contracts; replay coverage and unavailable-level behavior. Keep automated OB/profile derivation separate if it needs new data acquisition.
3. Compiler/version/template/brain projection, persisted line identity and atomic attempts. Draft-only, BTCUSDT scoped, isolated migration tests; no shared DB application.
4. Governed internal-paper partial reductions/protection/runner support through existing execution and journal services, separated into small PRs. Capability remains off for D-line until all end-to-end tests pass.
5. Watcher family dispatch, candidate alerts and UI/manual journal checklist; replay first, then explicitly approved observation-only BTCUSDT scans under existing worker/notification permissions.
6. Approval/arming integration and full gate; Griller review on final head of every PR, satisfy GO WITH CONDITIONS before merge. Any push invalidates previous head verdict. Sofien reviews candidates and thresholds before separately approving an immutable version and arming paper decisions.

Start versions in `draft`, execution permission `none`, `paper_only=true`; create separate immutable spec instances per symbol/timeframe/direction to match the current single-symbol authored-spec shape. BTCUSDT only via version asset universe and watchlist intersection. Other symbols require a newly reviewed version, never silent watchlist expansion. Tuning parameters creates a new immutable version requiring existing approval/selection flow; old candidates retain their exact spec and plans. Do not mutate approved defaults in place.

Observation, alerts and paper actionability are independent capabilities. Unsupported partial exits or missing required confluences leave visible candidates with explicit refusal; they do not fall back to trading a simpler plan. Rollback deselects/disarms D-line versions and new scans/entries; keep monitoring already-open paper positions through durable exit rules, preserve event history, and do not drop active progress tables. No live execution path, BloFin safety change or new environment flag activation is part of this scope.

## Proposed parameter defaults and review risks

All research thresholds, periods, tolerances, scoring weights and exit policies below belong in the immutable versioned spec (`dline/v1`, parameter policy `dline-research/v1`), with validated bounds and units. These are proposed operational defaults, not calibrated trading recommendations. Safety ceilings remain stricter platform constraints.

| Parameter | Proposed default |
| --- | --- |
| Symbols / trigger TFs / directions | BTCUSDT / 5m, 15m, 1h / long and short selectable |
| Lookback / pivot width / minimum pivot separation | 192 trigger bars / 2 each side / 3 bars |
| Accepted / preferred touches; minimum line span | 2 / 3; 6 bars |
| ATR period / touch tolerance / maximum wick excursion | Wilder 14 / 0.20 ATR / 1.0 ATR |
| Normalized absolute slope band | 0.02–0.50 ATR per trigger bar, inclusive |
| Break buffer / required closes | max(1 tick, 0.02 ATR) / 1 |
| Line maximum age / break-to-entry delay | 192 bars / 1 trigger interval |
| Entry mode / maximum adverse entry slippage | breakout / 0.10 ATR versus intended next-event price |
| Retest tolerance / expiry / limit policy | 0.20 ATR / 6 bars after break / frozen line price at break |
| HTF map / bias / EMA slope span | 5m:1h, 15m:4h, 1h:1d / EMA50 / 3 HTF bars |
| HTF parent line required / hold tolerance | false when absent; enforce hold when present / 0.20 HTF ATR |
| HTF required freshness | most recent completed HTF bar at trigger time; original receipt binding |
| 1-2 context maximum age | 48 trigger bars, intact latest confirmed pullback |
| Fib retest ratio / tolerance | 0.9 retracement / 0.20 trigger ATR |
| Ranging ADX / period | <20 / 14 |
| Stop buffer / minimum stop ticks | 0.20 ATR / 1 tick beyond anchor |
| Minimum TP2/final R:R | 2.0; ranged full-exit first target also >=2.0 |
| Fib targets / level clustering | 0.618, 1.0 / max(1 tick, 0.10 ATR) |
| Target limit / 5m opposing OB cap | 4 / enabled, 15m or higher |
| Runner default / allowed tuning / maximum hold | 20% / 0% or 15–20% / 96 trigger bars |
| TP fractions / Fib-only / range | 4: 40/20/10/10+20 runner; 3: 40/20/20+20; 2: 50/30+20 / 50/50 / 100% at first target |
| Runner trail buffer | 0.20 ATR outside latest confirmed swing; never loosen |
| Entry risk / hard ceiling | 1% / 3%, subordinated to all tighter existing limits |
| Filled attempts per line / simultaneous positions | 2 total / 1 including runner |
| Evidence age for trigger | latest completed bar, no more than one trigger interval old |
| Optional level age/consumption | source-declared validity; reject missing validity, future knowledge or mitigated/consumed levels |

Review before implementation: the slope band is an uncalibrated proxy for chart angle; two-touch false positives need replay review. HTF EMA mapping for 5m, parent-line hold policy, deep-Fib orientation, fixed retest price, re-entry reset and conservative 2R interpretation are explicit assumptions. Nearest OB cap disables runners there; Sofien should confirm this versus runner examples. Images sometimes show an unclosed candle, which never overrides closed-candle requirements.

Ranked implementation risks: (1) partial-exit accounting/protection integrity; (2) false historical knowledge in pivots/HTF/OB/POC; (3) duplicate attempts across workers, sliding windows and versions; (4) insufficient causal level data/provider budget; (5) screenshot ambiguity/overfitting and stop width versus 2R; (6) accidental family fallthrough or scope/approval bypass. Mitigate with capability refusal, original receipt proofs, transactional persistence, deterministic fake-provider tests, conservative fills and final-head safety regression review. No expected return is asserted.

Planning validation: source/code and all supplied images reviewed; application code and tests unchanged. Backend tests, ruff, migrations, workers, network market acquisition and trading were not run for this documentation-only task. Next action: review this local plan, then authorize publication/plan approval before implementation. Record deviations here after approval rather than silently changing the accepted plan.
