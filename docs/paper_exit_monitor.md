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

Attachment happens only after the disarmed-worker check, and only when the
enabled flag is true. A disarmed staging worker does not construct the
monitor when `PAPER_EXIT_MONITOR_ENABLED` is true. `run_configured_exit_monitor`
and the cycle return `disarmed` before opening a session or loading candles
in that case, and also when the flag is false. The flag stays default false.
This record does not arm it.

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
  That missing stretch at the start of the series is a refusal
  (`leading_gap`), not a stop or target evaluation.

Closed bars are not evaluated when the series is gapped, stale, or missing
its start. `candle_series_block` is the check. A leading gap means the
earliest closed bar starts after the trade's entry, so the candles do not
reach back to the fill. An internal gap is a closed bar whose length is
not exactly one timeframe, or a later closed bar whose `interval_start` is
not the previous bar's `interval_end`. Stale means there is no closed bar,
or the newest closed bar ended at least one full timeframe before
evaluation. The monitor does not invent the missing bars. The trade stays
open. The pass counts `refused`, logs `paper_exit_monitor_candles_unusable`
with reason `leading_gap`, `gap`, or `stale`, and the report status is
`degraded` with error `candles_unusable` when that is the only failure.
A loader that reports `covers_entry` false is the same leading-gap refusal.

On each run, a trade whose stop or target was already crossed in an earlier
closed bar is closed at that historical bar. The close is idempotent:
`paper-exit-monitor:{command_id}` is the close key, and a second pass replays
the same journal event. Two overlapping cycles, including two processes,
take the lifecycle lock before the status check (`lock_execution_lifecycle`:
the advisory key, then `SELECT … FOR UPDATE` on PostgreSQL). The waiter
re-reads the row. The same key replays. A different key on a trade that is
already closed raises `already_closed` and does not write a second close.

## Journal

The write is `ExecutionService.close_canonical_paper_plan`, the same method
`POST /paper-plan/close` uses. The HTTP body cannot set the monitor identity.

The trade's `exit_reason` is `stop_hit` or `target_hit`, and `exit_time` is
the crossing bar's end. The CLOSE lifecycle payload also stores:

- `exit_channel`: `PAPER_EXIT_MONITOR`
- `exit_actor`: `paper_exit_monitor`
- `exit_evidence`: bar times, OHLC, source event id, content hash, level, and
  the same-bar assumption
- `exit_origin`: `live` or `backfill`, from
  `downtime_is_historical_backfill`. Downtime is the gap from the crossing
  bar's end to this evaluation. Exactly 24 hours
  (`HISTORICAL_BACKFILL_AFTER`) is a backfill. A shorter gap is labelled
  live, including a bar that ended on the previous UTC date and is processed
  after midnight. A naive timestamp is a backfill. The October 6
  reconstruction is the 24-hour case: the close is journaled, and it does
  not move the breakers.
- `calibration_sample`: `eligible` on a live close and `excluded` on a backfill

A fresh live close of a `paper-profile-isolated` plan uses the same
profile-close note as any other profile close. The note is called with the
bar's end, so it advances the consecutive-loss streak and syncs the daily
lock for the day the bar closed. Fresh means the bar is not older than the
paper loss cooldown (`paper_signal_cooldown_after_loss_seconds`, default
3600 seconds) and not older than one candle of the trade's timeframe.
Equality with either bound still notes. A winning close still notes at the
bar's end, including one processed after midnight.

A losing close that is labelled live, and that is older than the cooldown
or older than one candle, does not call that note. The bar clock would
write a pause that has already expired, or a daily lock on the previous
UTC day. The close stays journaled with `exit_origin=live`. The path raises
`paper_exit_stale_loss` and arms the organization kill switch (AT-ADR-128).
Writing the breakers at the current database time was the other option and
is not used. A replay does not ask again. A zero result does not arm the
switch.

A backfill is journaled and does not call that note, so it does not move
the streak or the daily lock, and it is not an ADR-109 calibration sample.
A backfill with a net loss still protects the account: the close path raises
the structured alert `paper_exit_backfill_loss` and arms the organization
kill switch, which refuses new entries until a human deactivates it
(AT-ADR-127). A winning or zero backfill does not. Noting the loss onto the
live streak or the daily lock was the other option and is not used, because
those figures are dated from the old bar and do not block today's entries.
This path does not insert a calibration row for a live close either: the
walk-forward fold is not known here. Both kill-switch armings are audited
as the system. The trade owner is not stored as the person who armed it.

A human close leaves those fields unset.

A cycle that raises, or that returns `degraded` (candle failure, close
failure, or a refused gapped, stale, or leading-gap series), logs the
structured warning `paper_exit_monitor_cycle_degraded`. The warning carries
`failed` when the cycle raised or the report counted errors. A refused
series sets `refused` and does not set `failed`. The worker stays up. The
flag is not turned on by that warning.

Before candles are read, the worker compares its wall clock with database
`now()`. An exchange server time supplied by the caller must agree as well.
A missing trusted time, or a gap larger than
`paper_exit_monitor_max_clock_skew_seconds` (default 15), refuses the cycle
with status `refused` and raises the structured alert `paper_exit_clock_skew`.
No close is attempted.

Degraded or refused cycles that continue for
`paper_exit_monitor_degraded_alert_after_seconds` (default 900, maximum
3600) raise the structured alert `paper_exit_degraded_repeat`. One cycle
does not. A healthy cycle clears the count. The count is stored on the
`controlled_runtime_status` row `paper_exit_degradation`, so a restart
continues the same streak instead of starting from zero. The alert is a
paper-alert row, an audit row, and a Telegram message, not only the log
line `paper_exit_monitor_alert`.

| Setting | Environment variable | Default |
| --- | --- | --- |
| `paper_exit_monitor_degraded_alert_after_seconds` | `PAPER_EXIT_MONITOR_DEGRADED_ALERT_AFTER_SECONDS` | `900` |
| `paper_exit_monitor_max_clock_skew_seconds` | `PAPER_EXIT_MONITOR_MAX_CLOCK_SKEW_SECONDS` | `15` |

Neither value is set in `render.yaml` or in a deployed environment. The
monitor flags stay default false.

## Arming gates

The monitor is not attached, and `run_configured_exit_monitor` returns
`refused` before any database or candle read, unless both of these hold:

- `paper_signal_cooldown_after_loss_seconds` is a finite number greater
  than zero. Zero, a negative number, and a non-finite number refuse with
  `consecutive_loss_cooldown_invalid`.
- `watcher_paper_poll_interval_seconds` is not longer than the smallest
  entry in `paper_signal_allowed_timeframes`. A longer interval, or a
  timeframe list that cannot be read, refuses with
  `cycle_interval_exceeds_timeframe`. An interval equal to that candle is
  still allowed.

The same two checks refuse `resolve_autonomous_mode` when either autonomous
flag is set. Production still refuses those flags, and both monitor flags
stay default false. These checks do not arm anything.

A refusal at boot is written to the `paper_arming` runtime-status row.
`GET /health` and `GET /health/paper-worker` both show `arming_refusal`
(`refused`, `reason`, `source`). One deduplicated Telegram alert uses the
code `arming_refused`. A disarmed reboot deletes leftover `paper_arming`
and `paper_exit_monitor` rows, so an old armed heartbeat cannot page as
stale and an old refusal cannot stay on `GET /health`. That delete is the
only database write on the disarmed path. It does not attach the monitor
and it does not write a new arming row. A database failure is logged and
the process still returns disarmed.

## Heartbeat and Telegram

Each watcher cycle of the paper worker writes a `paper_worker` heartbeat on
`controlled_runtime_status`. A disarmed worker does not open the database,
so it does not write that row. `GET /health` reports the row.
`GET /health/paper-worker` is the external check: it reports `fresh`,
`stale`, or `missing`, and a stale or missing heartbeat raises one deduped
Telegram alert.

The exit-monitor thread writes a separate `paper_exit_monitor` heartbeat.
`GET /health/paper-worker` reports that thread as `fresh`, `stale`,
`missing`, or `disabled`. `disabled` means the thread is not attached, and
it does not alert. `stale` or `missing` means the thread is attached and
then died or hung, and that raises its own deduped Telegram alert. An
autonomous-loop boot refusal still marks `paper_arming` as `refused`. It
does not delete the exit-monitor heartbeat and it does not report that
attached thread as `disabled`. The watcher heartbeat can stay fresh while
this one does not.

`GET /health/paper-worker` also includes `kill_switch` with `active`,
`source`, and `since`. The free-text reason is not included. No secret and
no P&L amount are included. Telegram HTTP sends wait at most 5 seconds.

Backfill-loss, stale-loss, kill-switch arming, repeated degraded or refused
cycles, and clock-skew alerts use the same Telegram path. The message uses
the existing `telegram_bot_token` and `telegram_chat_id`. It does not include
a secret or a P&L amount. A repeated dedup key inside the alert cooldown
does not send again. Telegram is attempted only after the database commit.
If that commit rolls back, the dropped page is logged at error. A repeated
degraded or clock alert is still sent from memory, because the condition
already happened. A kill-switch page is not sent when its activation rolls
back.

Kill-switch activations from the manual route, the global setting, the
drawdown breaker, the other breaker and demo-protection paths, and a
fail-closed read use that same redacted, deduplicated Telegram path. The
free-text reason is not copied. No P&L amount is included. A committed
activation is marked unsent in the same transaction, in a savepoint, one
row per organization. A failed bookkeeping write, including two concurrent
first inserts of the same slot, does not roll the activation back. The
component key is `k` plus the first 31 hex digits of the organization id,
because the column is 32 characters. An older single `paper_kill_switch`
row is still read. If Telegram fails, or the process stops before the
send, the next worker boot retries that version. `GET /health/paper-worker`
schedules the same retry after the response, in a worker thread, at most
once per 60 seconds per version. A poll of that route does not send during
the request. A version already sent is not sent again.
