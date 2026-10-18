# Internal paper stop / target exit monitor

Canonical internal paper trades stay open until something calls
`POST /paper-plan/close`. Open exposure then keeps blocking new Nested
entries once it reaches 5% of equity. This monitor closes those trades when a
later closed candle touches the stop or the target.

## Scope

Included:

- `plan_authority=canonical`
- `execution_venue=PAPER_INTERNAL`
- `execution_policy_version=paper-internal-entry/v1`
- one target with quantity fraction 1 and a disabled runner
- journal status `OPEN` with a recorded entry price and entry time

Excluded, and never updated here:

- manual BloFin demo trades (`manual_demo_test`, `BLOFIN_DEMO`)
- governed BloFin demo trades (`governed-blofin-demo/v1`)
- paper-validation plans and any plan with a runner or more than one target

No exchange write endpoint is added. Real-money trading stays rejected.

## Flags

Both default false. This change does not set them in `render.yaml` or in any
deployed environment.

| Setting | Environment variable | Default |
| --- | --- | --- |
| `paper_exit_monitor_enabled` | `PAPER_EXIT_MONITOR_ENABLED` | `false` |
| `paper_exit_monitor_dry_run` | `PAPER_EXIT_MONITOR_DRY_RUN` | `false` |

`PAPER_EXIT_MONITOR_ENABLED=false` does not read candles and does not write.
Production refuses `paper_exit_monitor_enabled=true` at startup. Staging and
local may arm it. `PAPER_EXIT_MONITOR_DRY_RUN=true` still requires the enabled
flag. It logs `paper_exit_monitor_would_close` and does not call the close
service.

The component is attached to the existing paper worker
(`python -m app.workers.paper_worker`) and uses that worker's poll interval
(`WATCHER_PAPER_POLL_INTERVAL_SECONDS`). It is not a second service.

## Decision

For each open trade the monitor loads closed candles from the same perpetual
evidence source the strategies use (`perpetual_evidence_source` /
`fetch_closed_ohlcv`). It walks bars whose interval starts at or after the
entry time. The first bar whose range touches a level is the exit.

- Long: stop when low is at or below the stop; target when high is at or above
  the target.
- Short: stop when high is at or above the stop; target when low is at or
  below the target.
- Both levels in one bar: exit at the stop. The journal evidence records
  `same_bar_assumption=stop_first`.
- Fill price is that level. Costs are the plan's fee, funding, and slippage
  allowances. The internal-paper plan records those allowances as zero, so the
  existing exact-price model is unchanged.
- Exit time is the bar's `interval_end`, the time the closed bar made the
  touch knowable.
- The bar that contains the fill is ignored, because its high and low are not
  known to have printed after the entry.
- If the fetched history does not reach the entry, the trade is left open.

On each run, a trade whose stop or target was already crossed in an earlier
closed bar is closed at that historical bar. The close is idempotent:
`paper-exit-monitor:{command_id}` is the close key, and a second pass replays
the same journal event.

## Journal

The write is `ExecutionService.close_canonical_paper_plan`, the same method
`POST /paper-plan/close` uses. The HTTP body cannot set the monitor identity.

The trade's `exit_reason` is `stop_hit` or `target_hit`, and `exit_time` is
the crossing bar's end. The CLOSE lifecycle payload also stores:

- `exit_channel`: `PAPER_EXIT_MONITOR`
- `exit_actor`: `paper_exit_monitor`
- `exit_evidence`: bar times, OHLC, source event id, content hash, level, and
  the same-bar assumption

A human close leaves those three fields unset.
