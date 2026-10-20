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

For each open trade the monitor loads closed candles with
`fetch_closed_ohlcv`, the same call the strategies use. The adapter is chosen
from the trade, not from a separate monitor setting:

- Live Binance evidence (`evidence_is_live` and venue `binance`, or source
  family `binance_usdm_futures_public`) reads Binance USD-M public candles,
  including `BTCUSDT` `15m`. This is the source the live watcher uses.
- Live Bybit evidence reads the Bybit USDT perpetual adapter.
- Replay or mock evidence stays on the replay fixture. Tests and backtests
  use that path.
- The process default `perpetual_evidence_source=replay` does not hide a live
  trade. On staging, a trade with no stored identity also uses Binance USD-M.
  A process that is already configured for a live source, including failover,
  keeps that source when the trade has no stored identity.

It walks bars that start at or after the entry boundary. The first bar whose
range touches a level is the exit.

- Long: stop when low is at or below the stop; target when high is at or above
  the target.
- Short: stop when high is at or above the stop; target when low is at or
  below the target.
- Both levels in one bar: exit at the stop. The journal evidence records
  `same_bar_assumption=stop_first`.
- Fill price is that level, or the open when the bar opens through the stop.
  Costs are the plan's fee, funding, and slippage
  allowances. The internal-paper plan records those allowances as zero, so the
  existing exact-price model is unchanged.
- Exit time is the bar's `interval_end`, the time the closed bar made the
  touch knowable.
- A trade opened at bar close has `entry_time` equal to that bar's
  `interval_end`. The journal stores that bar end, not the watcher clock.
  A stored time a few seconds after the boundary is treated as the boundary.
  The fill bar's `interval_start` is earlier, so it is not eligible. The next
  bar, whose `interval_start` equals the entry boundary, is the first bar
  that can exit the trade. A touch inside the fill bar does not close the
  trade. A fill recorded deeper inside a bar still skips that bar, because
  its range is not known to have printed after the fill.
- A bar that opens through the stop exits at that open
  (`same_bar_assumption=gap_through_open`). A touch that does not gap fills
  at the stop or the target. Both levels in one bar, without that gap, still
  exit at the stop.
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
- `exit_origin`: `live` when the crossing bar ended less than 24 hours before
  evaluation, including a bar processed after UTC midnight. `backfill` when
  the bar ended 24 hours or more earlier, which is the October 6
  reconstruction, or when a timestamp is naive.
- `calibration_sample`: `eligible` on a live close and `excluded` on a backfill

A live close of a `paper-profile-isolated` plan uses the same profile-close
note as any other profile close. The note is called with the bar's end, so
it advances the consecutive-loss streak and syncs the daily lock for the day
the bar closed. A close processed just after midnight still does that for
the previous UTC day. A backfill is journaled and does not call that note,
so it does not move the streak or the daily lock, and it is not an ADR-109
calibration sample. This path does not insert a calibration row for a live
close either: the walk-forward fold is not known here.

A human close leaves those fields unset.
