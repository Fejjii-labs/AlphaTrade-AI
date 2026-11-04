# Configurable BloFin demo leverage plan

Branch: `claude/configurable-leverage` (PR #412), re-ported onto main as
`claude/demo-configured-leverage-v2` (see "Port onto main" below). BloFin **demo** and
internal paper only. The owner
asked for this directly, so implementation proceeds with this plan as the record.

## Problem

The governed BloFin demo loop never wrote leverage. With no active risk-policy profile it
required the demo account to already read back **1x cross**, so a demo account at any other
leverage blocked every setup (`demo_leverage_not_one`). With a profile it required an
isolated account whose leverage already matched the sized value, and still never wrote it.
A human had to set leverage by hand in the BloFin demo UI.

## Design

### 1. Setting (per org owner)
- New nullable columns on `autonomous_trading_settings` (migration `a24demoleverage001`,
  single head): `demo_leverage` (int, CHECK 1–20), `demo_margin_mode`
  (`isolated|cross`, CHECK), `demo_leverage_overrides` (JSON map
  `{"BTCUSDT": {"leverage": 5, "margin_mode": "isolated"}}`).
- NULL means the code default: **10x isolated**. Hard ceiling **20x**
  (`DEMO_LEVERAGE_CEILING = min(20, HARD_MAX_LEVERAGE)`), enforced by the service, the DB
  CHECK, the provider, and re-validated when a stored row is read (bad stored data refuses,
  never clamps).
- API: the existing `GET`/`PATCH /autonomous/settings` (owner-only writes, audited) gain
  `demo_leverage`, `demo_margin_mode`, `demo_leverage_overrides`,
  `clear_demo_leverage_overrides`; the view adds `demo_leverage_using_default` and
  `max_demo_leverage_ceiling`. Validation failures return 422 with stable codes
  (`demo_leverage_exceeds_ceiling`, `demo_leverage_below_one`, `demo_leverage_not_whole`,
  `demo_margin_mode_invalid`, `demo_leverage_override_symbol_invalid`, …).
- The row is keyed by (organization, user), like the rest of the autonomous settings. The
  governed demo loop runs for exactly one pinned org/user pair, so this is the per-org value
  in practice.
- UI: the **Leverage** card on the Autonomous Trading page (`LeverageSettingsSlot.tsx`,
  `#leverage`) gets leverage, margin mode and a per-coin override editor (see "Port onto
  main" for why it moved out of `BotSettings.tsx`). Risk per trade is the existing field; position size is still
  derived by the existing sizer. No new sizing semantics.

### 2. Pre-trade liquidation gate
`demo_liquidation_refusal` (in `services/demo_leverage_config.py`) estimates the isolated
liquidation price at the configured leverage with the venue's maintenance tier for the
planned notional, from the adverse (worst book) entry. It refuses with:
- `demo_liquidation_inside_stop` – liquidation at or through the stop;
- `demo_liquidation_inside_stop_buffer` – liquidation closer than 2× the stop distance;
- `demo_maintenance_tiers_unavailable` / `demo_liquidation_estimate_invalid` – fail closed.
Cross margin uses the same isolated estimate (free equity only moves a cross liquidation
further away, so this is conservative). It runs for both profile and non-profile plans,
after quantity is known and before the plan is created. The existing profile liquidation
buffer (`liquidation_inside_buffer`) still applies on top.

### 3. App-set leverage on BloFin DEMO
`GovernedBloFinDemoProvider.apply_configured_leverage` is the **only** leverage or
margin-mode write in the codebase. In `GovernedBloFinDemoLoop._bound`, after the kill switch,
posture, autonomous pre-checks and prior-entry reconciliation have passed, and after a fresh
kill-switch re-check, it:
1. refuses unless the client `base_url` is an allowlisted demo host and not a production
   host (`leverage_write_host_refused`); the `BloFinClient` constructor and every request
   also assert the demo host;
2. refuses an out-of-range request (`leverage_request_invalid`);
3. verifies read/trade-only permissions, NET position mode, and a **flat** account
   (positions, pending orders, pending TPSL) – a margin-mode change cannot apply to an open
   position, so that blocks as `account_not_flat` instead of crashing;
4. `POST /api/v1/account/set-margin-mode {marginMode}` only when the account mode differs
   (`margin_mode_set_failed`, `margin_mode_set_unconfirmed`);
5. `POST /api/v1/account/set-leverage {instId, leverage, marginMode, positionSide: "net"}`
   (`leverage_set_failed`, `leverage_set_unconfirmed`);
6. reads back `GET /account/margin-mode` and `GET /account/leverage-info` and requires an
   exact match (`margin_mode_readback_mismatch`, `leverage_readback_mismatch`).
POSTs are never retried (client policy). Every refusal is surfaced as
`demo_<reason>` on the loop proof; no order is sent. The subsequent snapshot reads leverage
again with `required_leverage = configured`, and `submit()` re-reads it immediately before
the order POST, so drift between planning and dispatch also refuses.

Plans sized without a profile now carry `margin_assumption_id = "configured-demo-leverage"`,
the configured `leverage` and `margin_mode`, and the order body sends that margin mode.
Sizing for those plans is unchanged (1% risk, 10% notional of equity). Profile plans pass the
configured leverage to `size_for_profile`; a configured leverage above the profile's
`max_leverage`, or a cross mode with a profile (isolated-only buffer), refuses before any
write. Manual demo plans keep 1x cross and never trigger a write.

### 4. Unchanged
Real-money/live blocks, `ENABLE_REAL_TRADING`, `EXCHANGE_MODE` checks, kill switch, both
trade caps, exposure and daily-loss limits, the risk engine, profile sizing and the
post-fill liquidation hold are untouched. No leverage write exists outside the governed
demo `_bound` path.

## Acceptance criteria
- GET returns 10 / isolated / `{}` when nothing is saved; PATCH accepts 1–20 and rejects
  0, 21, non-whole values, unknown modes and malformed per-coin overrides with 422 codes.
- On the demo host the loop sends set-margin-mode (if needed) and set-leverage with the
  configured values before entry, then requires an exact read-back.
- A live host, a set error, a read-back mismatch, an open position, or a liquidation estimate
  inside the stop (or its buffer) blocks with a distinct reason and sends no order.
- The happy path enters with the configured leverage and margin mode in the order.

## Risks for review
- This intentionally relaxes the old "no account-setting mutation" rule for **demo only**
  (`20-trading-safety.mdc` forbids automatic leverage changes; read as live). Owner request.
- BloFin endpoint shapes (set-leverage response `data` echo, set-margin-mode response) are
  from the public API docs; verify on the demo host once before enabling the worker. A
  missing echo fails closed as `*_set_unconfirmed`.
- The write happens before the liquidation gate (the stop is only known after the snapshot),
  so a blocked setup can still leave the flat demo account at the configured leverage. No
  position is affected.
- The leverage write runs on every scan that reaches `_bound` (idempotent value, flat account
  only). Rate limits are the client's 5 req/s throttle.
- Non-profile configured plans do not get the profile post-fill liquidation hold (kept out of
  scope to avoid stalling reconciliation on venues that omit a cross liquidation price).
- The OpenAPI contract (`frontend/src/lib/api/generated/`) was regenerated with
  `npm run api:generate`; `npm run api:check` passes.

## Griller GO WITH CONDITIONS follow-up (2026-11-03)
- **C1 (staging only):** writes need `GOVERNED_DEMO_AUTO_LEVERAGE_WRITE_ENABLED=true` and
  `ENVIRONMENT` staging/local (`demo_leverage_write_allowed`). Production, including a
  production demo profile, never writes; the snapshot still requires the configured
  leverage, so a mismatched account blocks (`demo_leverage_readback_mismatch`-style refusal).
  Staging must set the flag for the app to set leverage.
- **C2 (post-fill hold, all configured plans):** after a fill the venue position liqPrice is
  read. Through the stop -> `demo_post_fill_liquidation_inside_stop`; for configured plans,
  closer than 2x the stop distance -> `demo_post_fill_liquidation_inside_buffer`. Either one:
  epoch lock, one `POST /api/v1/trade/close-position` on the demo host (never retried),
  kill switch armed, critical alert carrying `flatten_outcome`. Profile plans now also
  flatten on a through-stop breach. An unreadable liqPrice on a non-profile plan is logged
  only (cross positions may omit it); profile plans keep the existing hold.
- **C3 (journal):** each confirmed write records audit `demo_leverage_applied` with
  inst_id, previous/new margin mode and leverage.
- **Shapes/docs:** leverage-info accepts an object or a list with exactly one row for the
  instrument; doc URLs cited in `apply_configured_leverage` / `flatten_position`.
- **Not done (optional):** gate-before-write ordering; the stop is only known after the
  snapshot, which needs the leverage already set. Left as is (flat-account writes only).
- **Safety rule:** `20-trading-safety.mdc` amended with a demo-host-only, owner-configured
  exception, **pending Sofien's explicit approval**.

## Deviations after Griller re-review (2026-11-03, @ 7b38902f)

- **C-c (endpoint):** the leverage read now uses `GET /api/v1/account/batch-leverage-info`
  ([GET Multiple Leverage](https://docs.blofin.com/index.html#get-multiple-leverage)) instead
  of the deprecated `GET /api/v1/account/leverage-info`
  ([GET Leverage (Deprecated)](https://docs.blofin.com/index.html#get-leverage-deprecated)).
  The response must be a list with exactly one row whose `instId` matches; an object, no
  match, duplicates or a row without `instId` read as unconfirmed (leverage 0), so the caller
  fails closed. `#get-leverage` is the copy-trading endpoint and is no longer cited. This
  replaces the "object or single-row list" shape note above.

## Port onto main (2026-11-04, `claude/demo-configured-leverage-v2`)

PR #412 was based on `e7962d8a`; main has since landed #411, #413, #414, #416, #417, #423,
#425, #426 and #428. The port is the #412 diff re-applied with these adjustments:

- **UI:** #413 split the old Bot limits form into `TradingLimitsCard` and left
  `LeverageSettingsSlot` as the integration point for this work. The leverage editor now
  lives there, loads `GET /autonomous/settings` itself and PATCHes only the demo leverage
  fields (owner-only, same 422 codes). `BotSettings.tsx` is unchanged from main.
- **Action-time quote refresh (#426):** the configured leverage and margin mode are stored
  in the loop's `_snapshot_request`, so the action-time re-read also requires them.
- **Migration:** `a24demoleverage001` still follows `a23marketmonitor001`, which is still
  the single head on main.
- **Tests:** two existing fixtures that build a `GovernedBloFinDemoLoop` directly
  (`test_grade_risk_appetite`, `test_demo_order_book`) now set the configured leverage the
  loop requires; they failed on #412's own head for the same reason.
- **#424 (demo trading unblock) is not on main** at the time of this port (it only exists on
  `claude/demo-trading-unblock`). This port does not include it. Whichever lands second must
  reconcile the non-profile sizing path in `GovernedBloFinDemoLoop._plan_terms` (#412: exact
  configured-leverage read-back, 1% risk / 10% notional; #424: existing leverage, 20x
  ceiling, 1-5% stop-loss band), the `margin_assumption_id` values, the `snapshot()`
  defaults in `governed_blofin.py`, and the plan valid-until window (10s here, 60s in #424).
