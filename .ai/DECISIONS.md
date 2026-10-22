# AlphaTrade AI — Decisions (ADR log)

Durable, append-only architecture/workflow decisions. IDs: `AT-ADR-XXX`.

## AT-ADR-146 — GPT Live reasoning is voice-scoped

- Date: 2026-10-22
- Decision: The disabled GPT Live voice path delegates backend reasoning to
  `AGENT_VOICE_REASONING_MODEL` (default `gpt-6.1-sol`) at
  `AGENT_VOICE_REASONING_EFFORT` (default `low`). The text agent keeps
  `AGENT_REASONING_MODEL` and `AGENT_REASONING_EFFORT`. Voice budget receipts
  compare `limits.reasoning.model` to the voice model, not the text model.
- Admission reserves gpt-live-1 at USD 0.05 per minute, billed per second,
  plus the reasoning and embedding holds. The sum must fit the $1 session
  ceiling or the session is refused. Realtime and GPT Live stay disabled.
  No Render environment change.
- Reference: `docs/gpt_live_integration.md`,
  `docs/gpt_live_limits.example.json`.

## AT-ADR-145 — Last-trade paper fills use a per-asset haircut, and the alert stop uses the ATR floor

- Date: 2026-10-22
- Decision: Both live quote producers (`quote_current_price` and
  `quote_from_terminal_trade`) publish the last contracted trade and do not
  set bid or ask. The book is a separate optional observation and is not on
  `PerpetualMarketSource`, so it is not attached to that quote and is not
  invented from the last trade. When a caller already supplies a usable book,
  the fill still lifts the ask or hits the bid. Otherwise the mid moves
  against the trader by a per-asset haircut. BTC and ETH use
  `PAPER_FILL_MAJOR_SPREAD_HAIRCUT` (default `0.0001`, 1bp). Every other
  base, including names that merely start with those letters such as ETHFI,
  uses `PAPER_FILL_SPREAD_HAIRCUT` (default `0.0005`, 5bp). A missing symbol
  uses the 5bp default. Supersedes only the single `0.0001` haircut sentence
  of AT-ADR-144.
- The SFP ATR floor is applied when the brain scan records the setup, which
  is the plan Telegram copies. It is the same `SFP_MIN_STOP_ATR_MULTIPLE`
  the internal paper loop already applies (default `0.5`). The floor is still
  not hashed into `setup_id`. The research replay adapter does not apply it.
  BloFin demo still does not opt into the haircut or the floor.
- Side-aware exit fills with slippage are not part of this decision.
- Neither setting arms the watcher, demo execution, or real trading.
- Reference: PR #311 Griller conditions. `app/services/paper_fill.py`,
  `app/strategy_brain/assembly.py`.

## AT-ADR-144 — Paper fills are side-aware and the SFP stop has an ATR floor

- Date: 2026-10-22
- Decision: Internal paper fills a long at the live ask and a short at the
  live bid. When the quote only has a mid (`CurrentPriceQuote.price`, the
  last trade), the fill moves against the trader by
  `PAPER_FILL_SPREAD_HAIRCUT` (default `0.0001`, half of a 2bp spread).
  A missing quote, a crossed book, or a non-positive result refuses the
  plan with `evidence_unavailable`, the same fail-closed code as a missing
  quote. BloFin demo does not opt in: it still replaces the entry from its
  own book.
- The SFP paper stop stays `max(0.1% of the swept reference, N ticks, k*ATR)`.
  `k` defaults to `0` on stored `sfp-research/v1` and `sfp-research/v2` so
  those rows and their setup ids do not change. `sfp-research/v3` sets `k`
  to `0.5`. Internal paper applies `max(authored k, SFP_MIN_STOP_ATR_MULTIPLE)`
  with that setting defaulting to `0.5`. The floor is not hashed into
  `setup_id`. A positive `k` with no Wilder ATR still refuses the plan.
  Setting the multiple to `0` restores the authored multiple, including zero.
- Neither setting arms the watcher, demo execution, or real trading.
- Reference: PR #309 Griller follow-ups. `app/services/paper_fill.py`,
  `app/strategy_brain/sfp/contracts.py`.

## AT-ADR-143 — Internal-read mirror runs the #304 pin before account reads

- Date: 2026-10-21
- Decision: When `blofin_paper_internal_main_key_selected` is true and the
  manual mirror is enabled, `BloFinDemoManualMirror.apply` runs the AT-ADR-141
  organization pin and the AT-ADR-140 UID compare before instruments,
  positions, or history. A missing pin raises before any venue call. The UID
  compare's only request is `GET /api/v1/user/query-apikey`. A mismatch or an
  unverified UID raises before the other reads. The check lives in the mirror
  so the fresh-snapshot path, which skips `BloFinSyncService.sync`, cannot
  read without it. A startup refusal of `BLOFIN_DEMO_MANUAL_MIRROR_ENABLED`
  together with the internal-read flag was not used. `paper_exchange_demo`
  and the dedicated read-only key are unchanged.
- The selector property was renamed from
  `blofin_paper_internal_snapshot_read_requested` to
  `blofin_paper_internal_main_key_selected`. It means the main `BLOFIN_API_*`
  key is selected for the paper_internal GET snapshot, before the host
  allowlist. It does not by itself make the snapshot ready.
- Reference: `docs/blofin_demo_manual_mirror.md`. Supplements AT-ADR-142.

## AT-ADR-142 — Paper-internal demo snapshot is a GET opt-in, not demo execution

- Date: 2026-10-21
- Decision: The read-only BloFin demo snapshot may run while
  `EXCHANGE_MODE=paper_internal`. The opt-in is
  `BLOFIN_DEMO_SNAPSHOT_INTERNAL_READ_ENABLED`, default false, together with
  `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED`. It is not `BLOFIN_DEMO_ENABLED`.
  That flag is the execution axis: setting it clears paper credential
  isolation and fails live USD-M evidence, which the Telegram staging
  projection requires (`exchange_mode=paper_internal`). Reusing it would
  crash-loop the armed worker or force `paper_exchange_demo`.
- The internal path runs only when execution is paper, real trading is off,
  the three `BLOFIN_API_*` values are set, `BLOFIN_DEMO_REST_BASE_URL` is the
  allowlisted demo host, and the governed organization and user pins are
  UUIDs. A non-demo host is refused. A missing gate skips the cycle. The
  client stays GET-only. `blofin_execution_authorized` and the exchange
  order adapter stay closed. Paper orders stay internal.
- The activity worker still requires the dedicated `BLOFIN_READONLY_*` key.
  The activity organization and expected UID pin still fail closed for the
  demo-key read. The identity log is `blofin_snapshot_identity` with
  `result=verified` or `result=mismatch` and does not include the UID or
  key. Supersedes only the `paper_internal` inactivity sentence of
  AT-ADR-139 for this opt-in. `paper_exchange_demo` behavior is unchanged.
- Reference: `docs/blofin_demo_snapshot_worker.md`.

## AT-ADR-141 — Fallback demo key fails closed without the activity pin

- Date: 2026-10-21
- Decision: The main demo-key fallback refuses to sync unless
  `BLOFIN_ACTIVITY_ORGANIZATION_ID` is set and equals the sync organization,
  and `BLOFIN_ACTIVITY_EXPECTED_UID` is set. A mismatch or a missing pin is
  logged as `blofin_fallback_sync_refused` with a reason token and no
  credential or UID value. The refusal happens before a venue call. The
  dedicated read-only key keeps its previous pin: it compares the native UID
  only when that pin is present for the same organization. Activity sync
  still requires a venue read-only key. `BLOFIN_ACTIVITY_ENABLED` is not
  required for this pin.
- Reference: `docs/blofin_demo_snapshot_worker.md`. Supplements AT-ADR-140.

## AT-ADR-140 — Fallback demo key uses the same native account pin

- Date: 2026-10-21
- Decision: The main demo-key fallback is subject to the same native account
  pin as the dedicated read-only key. When `BLOFIN_ACTIVITY_EXPECTED_UID` is
  set for `BLOFIN_ACTIVITY_ORGANIZATION_ID`, sync refuses a key whose native
  UID is a different demo account. `latest()` applies the same activity-account
  snapshot proof. The connection binding stays the main demo key
  (`readonly=False`). A dedicated key that can trade is still refused. The
  fallback key is not refused only because `readOnly` is 0. The GET-only
  client, `assert_demo_host`, and the partial-set fail-closed rule are unchanged.
- Reference: `docs/blofin_demo_snapshot_worker.md`. Supplements AT-ADR-139.

## AT-ADR-139 — Demo snapshot and read sync may use the main demo key for GET only

- Date: 2026-10-21
- Decision: Staging keeps one Read+Trade demo key (`BLOFIN_API_KEY`,
  `BLOFIN_API_SECRET`, `BLOFIN_API_PASSPHRASE`). Snapshot sync and read sync
  use that key when the dedicated `BLOFIN_READONLY_*` set is absent. The
  client stays `BloFinReadOnlyClient`: GET and the existing path allowlist
  only. `assert_demo_host` and the demo-host allowlist stay in force. A
  partial read-only set does not fall back. A complete read-only set is still
  preferred, and a dedicated key that can trade is still refused.
- `demo_account_active` accepts `paper_exchange_demo` as well as the existing
  `paper_internal` read-only path, so snapshot sync, the manual mirror, and
  autonomous demo orders can run in one mode. `paper_internal` without the
  read-only set stays inactive.
- Safety: `real_trading_enabled` stays false. Production refusals in
  `deployment_safety.py` are unchanged, including `paper_exchange_demo` and
  both autonomous flags. Setting both autonomous flags still resolves to
  `autonomous_mode_ambiguous`. Activity identity still requires a venue
  read-only key. This decision does not enable the snapshot flag, activity
  sync, or real-money trading.
- Supersedes the credential sentence of AT-ADR-115 only. The snapshot flag
  still defaults false, and a disarmed worker still opens no client.
- Reference: `docs/blofin_demo_snapshot_worker.md`.

## AT-ADR-138 — One exit-monitor candle series is one venue

- Date: 2026-10-21
- Decision: A paper exit candle walk never stitches two venues into one
  series. When the primary source requires a switch, bars already collected
  are discarded and the walk restarts on the secondary venue alone. A second
  switch, or a page whose bars are not that venue, returns no bars and the
  trade stays open. The switch is logged with the journal trade id, the from
  venue, the to venue, and the timeframe. The venue that supplied the series
  is stored on the journal close payload as `exit_evidence.candle_venue`.
  That payload is the close record. No new column is required. Closes that
  use Bybit fallback candles stay enabled. Staging remains dry-run unless an
  operator sets the flag.
- Safety: `PAPER_EXIT_MONITOR_ENABLED` stays default false.
  `PAPER_EXIT_MONITOR_DRY_RUN` stays default true. Real-money trading stays
  rejected. No Render, environment, or workflow change.
- Reference: AT-ADR-137; Griller conditions on PR #303.

## AT-ADR-137 — Exit-monitor empty candles are a failed fetch, not a loose freshness check

- Date: 2026-10-21
- Decision: Staging `candles_unusable` / `leading_gap` with an empty
  `series_start` was a failed candle fetch. Live Binance paper trades were
  read with the Bybit secondary stripped, and
  `EvidenceSourceSwitchRequiredError` was treated as a page-size miss, so
  every size failed and the series stayed empty. Bybit `fetch_closed_ohlcv`
  also omitted the kline `end` parameter, so a cursor could not move behind
  the latest window. The loader now keeps a configured Bybit secondary for a
  live Binance trade and rebinds once to that identity. Bybit kline requests
  pass `end` equal to `evaluated_at`. `candle_series_block` thresholds are
  unchanged. A same-version 31-hex or legacy kill-switch row is marked sent
  once the full-hex row of that version is sent. A same-version unsent row
  on the only remaining slot is still retried (AT-ADR-136).
- Safety: `PAPER_EXIT_MONITOR_ENABLED` stays default false.
  `PAPER_EXIT_MONITOR_DRY_RUN` stays default true. An injected closer is
  replaced by the refusing closer while dry-run is on. Production refusal
  is unchanged. Real-money trading stays rejected. No Render, environment,
  or workflow change.
- Reference: AT-ADR-135; AT-ADR-136; staging worker
  `srv-daqej7o473hc73fsc1k0` at `b031f740`.

## AT-ADR-136 — Stale kill-switch rows do not keep the health retry hot

- Date: 2026-10-20
- Decision: The first write of an organization's full-hex kill-switch slot
  marks that organization's 31-hex slot and its legacy `paper_kill_switch`
  row sent when those rows are still unsent. `GET /health/paper-worker`
  does not schedule a retry for an unsent row whose version is older than
  the current activation. A later full-hex row that is no longer active
  also supersedes those older unsent rows. A same-version unsent row on
  the only remaining slot is still retried.
- Migration `a21killswitchkey001` is already applied on staging. Its file
  is not edited to add `SET LOCAL lock_timeout`. A downgrade still refuses
  while any component is longer than 32 characters, so the full-hex rows
  have to be removed first. See `docs/deploy_rollback_runbook.md` §5.6.
- Bybit's closed-candle post-close grace is 0 seconds
  (`FIRST_SLICE_OHLCV_GRACE_SECONDS`). It is not longer than the 5-second
  Binance settlement guard, so the exit-monitor paging cursor stays one
  value for every source.
- Safety: `PAPER_EXIT_MONITOR_ENABLED` stays default false. Production
  refusal is unchanged. Real-money trading stays rejected. No Render or
  workflow change.
- Reference: AT-ADR-134; AT-ADR-135.

## AT-ADR-135 — Exit-monitor dry-run is the default

- Date: 2026-10-20
- Decision: `paper_exit_monitor_dry_run` (`PAPER_EXIT_MONITOR_DRY_RUN`)
  defaults true. Removing the variable must not start internal-paper
  closes. Live closes require an explicit `false` and the enabled flag.
  `paper_exit_monitor_enabled` stays default false. Production still
  refuses the enabled flag.
- Safety: Real-money trading stays rejected. The example template records
  the safe default. No Render blueprint or workflow change.
- Reference: AT-ADR-116; Griller follow-up on PR #299.

## AT-ADR-134 — Kill-switch alert rows use the full organization hex

- Date: 2026-10-20
- Decision: `controlled_runtime_status.component` is widened from 32 to 64
  characters. A new pending kill-switch alert is keyed `k` plus the full
  organization UUID hex (33 characters). The previous `k` plus 31 hex
  digits, and the single `paper_kill_switch` row, are still read and can
  still be marked sent. Downgrade of the widen refuses while any component
  is longer than 32 characters. `GET /health/paper-worker` starts the
  Telegram retry thread only when an unsent page is visible and that
  page's 60 second limit is open. A failed runtime read still schedules
  the thread.
- Safety: Flags stay default false. Production refusal is unchanged.
  Real-money trading stays rejected. No environment, Render, or workflow
  change.
- Reference: AT-ADR-133; Griller comment 6297076089 conditions C3 and C4.

## AT-ADR-133 — Kill-switch bookkeeping cannot undo the activation

- Date: 2026-10-20
- Decision: The `paper_kill_switch` posture write runs inside a savepoint
  and uses an upsert. A failed bookkeeping write, including two concurrent
  first inserts of the same slot, leaves the kill-switch activation
  committed. Each organization has its own pending-alert component: `k`
  plus the first 31 hex digits of the organization id, because the column
  is 32 characters. The legacy `paper_kill_switch` row is still read.
  `GET /health/paper-worker` schedules the Telegram retry after the
  response, in a worker thread. A version is attempted at most once per
  60 seconds, including a failed attempt. Worker boot may still retry
  directly under that same limit.
- Safety: Flags stay default false. Production refusal is unchanged.
  Real-money trading stays rejected. No environment, Render, or workflow
  change.
- Reference: AT-ADR-132; Griller pre-arming conditions on the exit-monitor
  follow-up.

## AT-ADR-132 — A disarmed reboot clears stale arming rows

- Date: 2026-10-20
- Decision: A worker that boots disarmed deletes leftover `paper_arming`
  and `paper_exit_monitor` rows. An old armed row must not page as stale,
  and an old refused row must not stay on `GET /health`. The delete does
  not attach the monitor and does not write a new posture. If the database
  cannot be opened, the failure is logged and the process still returns
  disarmed.
- Kill switch: a committed activation records its page as unsent, keyed by
  the row version, in the same transaction. A failed Telegram send or a
  crash before the send leaves that marker. `GET /health/paper-worker` and
  the next worker boot retry it. A version already sent is not sent again.
  The HTTP send timeout stays at most 5 seconds.
- Safety: Flags stay default false. Production refusal is unchanged.
  Real-money trading stays rejected. No environment, Render, or workflow
  change.
- Reference: AT-ADR-131; Griller second pass on the exit-monitor follow-up.

## AT-ADR-131 — An attached exit monitor stays visible when the loop refuses

- Date: 2026-10-20
- Decision: Exit-monitor liveness follows whether the monitor thread is
  attached. An autonomous-loop boot refusal still marks `paper_arming` as
  `refused`. It does not delete the `paper_exit_monitor` heartbeat and it
  does not report that attached thread as `disabled`. A stale heartbeat on
  the attached thread is `stale` on `GET /health/paper-worker` and raises
  the existing deduped alert.
- Kill switch: `GET /health/paper-worker` includes `kill_switch` with
  `active`, `source`, and `since`. The free-text reason, secrets, and P&L
  amounts are not included. A committed activation whose Telegram send
  fails is logged at error. Global-setting and fail-closed pages from
  `evaluate()` are sent after any open transaction ends, including when
  that transaction rolls back.
- Telegram: the HTTP send timeout is at most 5 seconds. The stored setting
  may still be higher so an older environment value boots. Sends clamp it.
- Safety: Flags stay default false. Production refusal is unchanged.
  Real-money trading stays rejected. No environment, Render, or workflow
  change.
- Reference: AT-ADR-130; PR #296 review comment 6294327165.

## AT-ADR-130 — A refused arming and a dead exit-monitor thread are visible

- Date: 2026-10-20
- Decision: When the exit monitor or an autonomous loop refuses to arm at
  boot (`consecutive_loss_cooldown_invalid` or
  `cycle_interval_exceeds_timeframe`), the worker writes a `paper_arming`
  runtime-status row. `GET /health` and `GET /health/paper-worker` show that
  reason. One deduplicated Telegram alert uses the existing safety-alert
  path. The exit-monitor thread writes a separate `paper_exit_monitor`
  heartbeat. A dead or hung thread is `stale` or `missing` on
  `GET /health/paper-worker` and alerts. A monitor that was not armed is
  `disabled` and does not alert.
- Kill switch: activations from the manual route, the global setting, the
  drawdown breaker, the other breaker and demo-protection paths, and a
  fail-closed read use the same redacted deduplicated Telegram path. The
  message has no secret and no P&L amount. The caller's free-text reason is
  not copied. A rolled-back activation is logged and not sent.
- Database outage: a queued safety alert discarded with a rollback is logged
  at error outside the transaction. An observed exit-monitor alert (repeated
  degradation or clock) is still sent from memory. A kill-switch activation
  that did not commit is not sent.
- Safety: Flags stay default false. Production still refuses them. This
  record does not arm them, change an environment, or change a blueprint.
  Real-money trading stays rejected.
- Reference: AT-ADR-129; issue #291; `docs/paper_exit_monitor.md`.

## AT-ADR-129 — Arming refuses a disabled loss cooldown, a slow cycle, and a silent alert

- Date: 2026-10-20
- Decision: The paper exit monitor and both autonomous loops refuse to arm
  unless `paper_signal_cooldown_after_loss_seconds` is a finite number
  greater than zero. The reason code is `consecutive_loss_cooldown_invalid`.
  They also refuse when `watcher_paper_poll_interval_seconds` is longer than
  the smallest configured strategy timeframe, or when that list cannot be
  read. The reason code is `cycle_interval_exceeds_timeframe`. Equality with
  the candle is still allowed. The exit-monitor call returns `refused`
  before a database or candle read. A disarmed flag stays disarmed.
- Heartbeat: the paper worker writes a `paper_worker` runtime-status
  heartbeat on each watcher cycle. A disarmed worker does not open the
  database and does not write it. `GET /health/paper-worker` reports
  `fresh`, `stale`, or `missing`. Stale and missing raise one deduped
  Telegram alert. `GET /health` reports the same row and does not send.
- Alerts: backfill loss, stale live loss, kill-switch arming, repeated
  degraded or refused cycles, clock skew, and the heartbeat alert go through
  the existing Telegram delivery provider. The bot token and chat id stay
  `telegram_bot_token` and `telegram_chat_id`. No new secret is added. The
  message has no secret and no P&L amount. A dedup key inside its cooldown
  does not send again. The send runs after the database commit.
- Counter: the degraded-cycle streak is the `paper_exit_degradation` runtime
  status row. A restart loads it. A healthy cycle deletes it.
- Safety: Both monitor flags and both autonomous flags stay default false.
  Production still refuses them. This record does not arm them, change an
  environment, or change a blueprint. Real-money trading stays rejected.
- Reference: AT-ADR-127; AT-ADR-128; issue #291;
  `docs/paper_exit_monitor.md`.

## AT-ADR-128 — A stale live paper-exit loss arms the kill switch

- Date: 2026-10-20
- Decision: A paper-exit close that is labelled live, has a net loss, and
  whose bar end is strictly older than the paper loss cooldown or strictly
  older than one candle of the trade timeframe, arms the organization kill
  switch. It does not advance the consecutive-loss streak and it does not
  sync the daily lock. The close stays journaled with `exit_origin=live`.
  The journal `exit_time` stays the bar end. Calibration eligibility is
  unchanged. A replay does not ask again. A win, a zero, and a missing PnL
  do not arm the switch. A non-finite PnL on a stale close fails closed and
  does. A missing database clock, or an unreadable timeframe, fails closed
  and arms the switch for a live monitor loss.
- Bounds: The pause window is `paper_signal_cooldown_after_loss_seconds`
  (default 3600). A non-positive window does not by itself mark a close
  stale. Equality with the pause window or with one candle stays on the
  live note, so a loss on the bar that just closed still writes the streak
  and the daily lock from that bar. The `>=24h` backfill path is unchanged:
  `downtime_is_historical_backfill` and `backfill_loss_needs_protection`
  still own that label, that alert, and that kill-switch reason.
- Why the kill switch, not the current database time: AT-ADR-127 already
  refused to fold a delayed loss into the live streak. Stamping the breakers
  with database `now()` would make every loss from one outage look like it
  happened now, including several closes in one pass. A stale win is still
  noted at the bar time, and that win could reset or fail to reset a streak
  that had just been moved to the current clock. The paper loss cooldown
  keys off journal `exit_time`, which has to stay the bar end, so restamping
  only the breaker clock would not revive a cooldown that has already
  expired. The daily lock written from the bar's UTC date is yesterday when
  the close crosses midnight. The kill switch blocks new entries until a
  human deactivates it, without rewriting either figure.
- Audit: Exit-monitor kill-switch arming, for this path and for the
  backfill path, is recorded as the system. `activated_by` is null and the
  audit actor is `system`. A human activation stays a user action.
- Not in this record: The degraded-cycle counter still lives in the worker
  process. Persisting it from audit rows needs a row on each degraded cycle
  and a rule for clearing it across processes. That is a follow-up.
- Safety: Both monitor flags stay default false. This record does not arm
  them, change an environment, or change a blueprint. Real-money trading
  stays rejected.
- Reference: AT-ADR-127; AT-ADR-118; `docs/paper_exit_monitor.md`.

## AT-ADR-127 — A losing backfill alerts and refuses; it does not move the live breakers

- Date: 2026-10-20
- Decision: A paper-exit close that `downtime_is_historical_backfill` labels
  as a backfill (24 hours or more, or a naive timestamp) and that has a net
  loss still protects the account. It does not advance the consecutive-loss
  streak and it does not sync the daily lock. It raises a structured alert
  and arms the organization kill switch, which refuses new entries until a
  human deactivates it. The close stays journaled. A winning or zero backfill
  does neither. A replay does not ask again. AT-ADR-118 and AT-ADR-124 still
  keep the backfill out of ADR-109 calibration.
- Why this and not the live breakers: `_note_profile_close` is called with
  the crossing bar's end. That instant is already 24 hours or more behind
  evaluation. The daily lock it would write is for a finished UTC day, so it
  does not block today's entries. The consecutive pause it would write is
  dated from that same past instant, and the next scan's `streak_after_expiry`
  clears a pause that is already over. Moving the breakers with the bar time
  does not protect the account. Noting the loss with the evaluation clock
  instead would lock today and increment today's streak for a reconstruction,
  including several October losses in one pass, and a later backfill win must
  not reset that streak. The live book would no longer match the live
  sequence. The kill switch is the existing refusal that blocks new entries
  without rewriting either figure. The PnL amount is not stored on the alert.
- Repeated cycles: degraded or refused exit-monitor cycles raise a structured
  paper alert, not only a log line, after they have repeated for
  `paper_exit_monitor_degraded_alert_after_seconds`. The default is 900
  seconds (15 minutes). The field cannot be set above 3600 seconds, so the
  wait stays well under the 24-hour backfill horizon. One cycle does not
  alert. A healthy cycle clears the streak.
- Candles: a series whose earliest closed bar starts after the trade entry
  is a leading gap. The trade stays open. The pass counts `refused`, logs
  `paper_exit_monitor_candles_unusable` with reason `leading_gap`, and the
  report is `degraded`. The monitor does not invent the missing stretch.
- Clock: before any candle read, the worker compares its wall clock with
  database `now()`. An exchange server time, when the caller supplies one,
  must agree as well. A gap larger than
  `paper_exit_monitor_max_clock_skew_seconds` (default 15, maximum 120) or a
  missing trusted time refuses the cycle and raises a structured alert.
  Equality with the threshold is still accepted.
- Safety: Both monitor flags stay default false. This record does not arm
  them, change an environment, or change a blueprint. Real-money trading
  stays rejected.
- Reference: AT-ADR-118; AT-ADR-124; AT-ADR-125;
  `docs/paper_exit_monitor.md`.

## AT-ADR-124 — Paper exit monitor stays closed until a close is exactly once

- Date: 2026-10-20
- Decision: The internal-paper exit monitor may not be dry-run on staging or
  switched on until a close is exactly once, unusable candles are refused,
  a degraded cycle is warned, the 24-hour downtime rule is explicit, and a
  disarmed worker ignores the enabled flag. Both flags stay default false.
  This record does not arm them.
- Exactly once: `close_filled_plan` holds `lock_execution_lifecycle` (advisory
  key, then the journal row on PostgreSQL) and re-reads the trade before the
  status check. The monitor key `paper-exit-monitor:{command_id}` replays.
  A different key on an already closed trade raises `already_closed` and
  writes no second close.
- Candles: `candle_series_block` refuses a stop or target read when closed
  bars do not meet, a bar is not one timeframe long, or the newest closed
  bar ended at least one timeframe before evaluation. The trade stays open.
  The pass counts `refused` and logs `paper_exit_monitor_candles_unusable`.
- Degraded: a raised cycle or a `degraded` report logs
  `paper_exit_monitor_cycle_degraded`.
- Downtime: `downtime_is_historical_backfill` is the rule. Exactly 24 hours
  from the crossing bar's end to evaluation is a backfill. Less than 24
  hours is live, including a previous UTC date processed after midnight. A
  naive timestamp is a backfill. The close is journaled either way.
  AT-ADR-118 still keeps a backfill out of the breakers and out of ADR-109
  calibration.
- Disarmed: the monitor is attached only after the disarmed-worker check,
  and only when the flag is true. A disarmed worker does not open PostgreSQL
  or public candles when `PAPER_EXIT_MONITOR_ENABLED` is true. The flag is
  not added to the disarmed-flag list, because that would treat the worker
  as armed.
- Safety: Real-money trading stays rejected. No environment, blueprint, or
  deploy change.
- Reference: AT-ADR-116; AT-ADR-117; AT-ADR-118;
  `docs/paper_exit_monitor.md`.

## AT-ADR-125 — Ruined equity still latches the profile breaker

- Date: 2026-10-20
- Decision: `persist_profile_breakers` writes the drawdown latch and the
  loss streak when account equity is not a finite number greater than zero.
  It arms the organization kill switch on the trip that sets the latch. It
  does not return before that write. A stored positive equity peak is kept.
  A new row uses peak `1` only so the column can be written; the latch, not
  that peak, blocks new trades. The scan returns `breaker_active` when the
  persist call refuses new trades, before sizing. A profile close that
  refuses new trades is logged as `profile_close_refuses_new_trades`. The
  close itself stands.
- Safety: Real-money trading stays rejected. This does not clear a latch and
  does not deactivate a kill switch.
- Reference: AT-ADR-109; AT-ADR-110;
  `backend/src/app/services/risk_policy/breaker_trip.py`.

## AT-ADR-126 — A missing stored plan revokes leftover autonomous authority

- Date: 2026-10-20
- Decision: When the execution clock is present and the stored plan is
  missing, both the internal-paper loop and the governed BloFin demo loop
  call `revoke_leftover_autonomous_authorizations` with reason
  `stored_plan_limits_uncomputed` before returning that reason. A missing
  clock returns the same reason and does not revoke, because there is no
  moment at which those rows can be revoked.
- Safety: Real-money trading stays rejected. Flags stay default false.
- Reference: AT-ADR-119; AT-ADR-120.

## AT-ADR-121 — Non-pull_request runs cannot cancel or replace each other

- Date: 2026-10-19
- Id: Renumbered from AT-ADR-107 during the 2026-10-19 integration batch.
  AT-ADR-107 is the grade-based risk policy (PR #278, policy `at-adr-107/v1`).
  The decision text is unchanged.
- Decision: Every push and every `workflow_dispatch` run, including
  `full_backend`, has its own concurrency group: `github.workflow` plus
  `github.run_id`. `cancel-in-progress` stays true only for `pull_request`.
  Pull requests share `pr-<number>`, so a newer review event cancels the older
  run of that PR. A `pull_request` `edited` event that does not change the base
  branch also uses `github.run_id`, and that run does the heavy work instead of
  publishing a skip, so a title or body edit cannot cancel, replace, or paint a
  green skip over the current head. A base-branch change, including a retarget
  to `main`, joins the PR group, cancels the stale run, and classifies the diff
  against the new base. That retarget runs the heavy jobs even when the pull
  request is still a draft, unless the new diff is docs-only. `reopened` and
  `ready_for_review` also run the heavy jobs on code when the draft flag is
  set. A missing head SHA, including a fork pull request whose commits are not
  local yet, fails open and runs the work. The docs-only skip remains
  pull_request-only. It does not apply to push, `workflow_dispatch`, or a
  non-base edit.
- Limit: Supersedes the concurrency grouping in AT-ADR-122. Draft `opened` and
  `synchronize` still skip heavy work unless the exact label `run-ci` is already
  present. A label whose name only contains that text does not count. The scope
  self-test ignores the calling `CI_SCOPE_*`, `GITHUB_*`, and `GIT_*` environment.
  No
  schedule, no job-level `if`, and `full_backend` stays manual. A green check
  after a draft or docs skip is still not release acceptance (AT-ADR-084).
- Safety: Real trading stays disabled. No workflow is dispatched by this change.
- Reference: `.github/workflows/ci.yml`; `.github/workflows/ci-scope.sh`.

## AT-ADR-122 — Ordinary CI cancels stale runs; full backend is manual per batch

- Date: 2026-10-19
- Id: Renumbered from AT-ADR-106 during the 2026-10-19 integration batch.
  AT-ADR-106 is the autonomous-mode decision (PR #258, policy `at-adr-106/v1`).
  The decision text is unchanged.
- Decision: Only pull_request CI cancels the previous run of that pull request.
  Concurrency grouping is superseded by AT-ADR-121: a push or
  `workflow_dispatch` uses its own `github.run_id` group, so it is not queued,
  cancelled, or replaced while pending. The 02:00 UTC
  schedule is removed. The complete
  backend suite runs only when someone dispatches `ci.yml` with
  `full_backend=true`. Pull requests and pushes run the focused selection only.
  A batch stacks onto an integration branch such as `integration/<date>`, gets
  one full dispatch, and merges in order only after that run. Draft `opened`
  and `synchronize` events do not run backend, evaluation, Docker build, or
  Playwright smoke unless they already carry `run-ci`. AT-ADR-121 runs that
  heavy work for `reopened`, `ready_for_review`, and a base retarget even when
  the pull request is still a draft. The `labeled` activity is
  not a workflow trigger. `deployment-safety` still runs on drafts and runs
  the CI scope self-test. Frontend still runs on draft code changes. A rename
  is not docs-only unless every old and new path is under `docs/**`, `.ai/**`,
  or root markdown. That docs-only skip applies to pull requests only. A push
  to `main` or `master` always runs the heavy jobs. On a pull request, a change
  limited to `docs/**`, `.ai/**`, or root markdown skips backend, frontend, and
  Docker, and also evaluation and browser work that depend on them. The six job names
  stay, with no job-level `if`, and a skipped heavy path exits success so a
  required check is not left pending.
- Limit: The `full_backend` input and the complete pytest command are unchanged.
  A green check after a draft or docs skip is not release acceptance
  (AT-ADR-084). This supersedes the nightly schedule in AT-ADR-103. No branch
  protection, secrets, or trading mode changes. The Postgres service on the
  backend job still starts when heavy steps are skipped, because a job-level
  `if` would drop the required check name.
- Safety: Real trading stays disabled. No workflow is dispatched by this change.
- Reference: `.github/workflows/ci.yml`; `.github/workflows/ci-scope.sh`;
  `.ai/RELEASE.md`.
## AT-ADR-118 — Backfilled paper exits stay out of the breakers

- Date: 2026-10-19
- Decision: An exit-monitor close is a historical backfill only when the
  crossing bar ended at least 24 hours before evaluation, or a timestamp is
  naive. That is a close from before the monitor tracked the trade. The open
  October 6 short stop and long target, reconstructed on a later day, are
  that case. The journal still records the close, with `exit_origin=backfill`
  and `calibration_sample=excluded`. That close does not advance the
  consecutive-loss breaker, does not sync the daily lock, and is not an
  AT-ADR-109 calibration sample. A crossing processed within 24 hours is
  `exit_origin=live`, including when the bar ended on the previous UTC date
  and the worker runs after midnight. A live close of a
  `paper-profile-isolated` plan uses the same profile-close note as any
  other profile close, with the bar's end as the note time, so the result
  counts toward the consecutive streak and the daily lock for the day the
  bar closed. A human close is unchanged. A live close does not insert a
  calibration row on this path, because the walk-forward fold is not known
  here. Both monitor flags stay false. This record does not arm them.
  Real-money trading stays rejected.
- Reference: AT-ADR-109; AT-ADR-110; AT-ADR-116;
  `docs/paper_exit_monitor.md`.

## AT-ADR-117 — Paper exit candles follow the trade's live evidence

- Date: 2026-10-18
- Decision: The internal-paper exit monitor reads closed candles from the
  trade's stored evidence identity. Live Binance evidence uses the Binance
  USD-M public adapter (the live watcher source for BTCUSDT 15m and the
  other watched perpetuals). Live Bybit evidence uses the Bybit public
  adapter. Replay and mock evidence stay on the replay fixture, which is
  for tests and backtests. `perpetual_evidence_source=replay` remains the
  process default and does not select candles for a live trade. Staging
  trades with no stored identity use Binance USD-M. A process already
  configured on a live source keeps that source, including failover, only
  when the trade has no stored identity.
- Entry bar: live internal-paper fills are taken at a closed-bar boundary.
  The journal `entry_time` is that bar's `interval_end`, not the watcher
  clock. A stored time within 90 seconds after the boundary is the same
  boundary. The fill bar is not eligible. The next bar, whose
  `interval_start` equals that boundary, is the first bar that can exit.
  A touch inside the fill bar does not close the trade. A fill deeper
  inside a bar still skips that bar. A bar that opens through the stop
  exits at that open.
- Flags and scope are unchanged from AT-ADR-116. Both monitor flags default
  false. This decision does not arm them, change deploy config, or
  authorize a merge. Real-money trading stays rejected.
- Id: drafted as AT-ADR-108 on the paper exit-monitor branch. The grade-risk
  branch had already published AT-ADR-107 and AT-ADR-108, so the merge
  recorded this as AT-ADR-115. The batch already uses every id through
  AT-ADR-115, so this record is AT-ADR-117. The candle rule is unchanged.
- Reference: `docs/paper_exit_monitor.md`;
  `backend/src/app/services/paper_exit_candles.py`.

## AT-ADR-116 — Internal paper trades exit at the stop or target

- Date: 2026-10-18
- Decision: Open canonical internal paper trades are closed by a paper-worker
  component when a closed candle from the strategy evidence source touches the
  stop or the single full-size target. The fill price is that level, except
  when the bar opens through the stop: that fill is the open. Costs are
  the plan's fee, funding, and slippage allowances (zero on
  `paper-internal-entry/v1`). If one bar touches both levels, the stop wins and
  the journal evidence records `same_bar_assumption=stop_first`. The exit time
  is the crossing bar's `interval_end`.
- Writer: `ExecutionService.close_canonical_paper_plan`, the same method as
  `POST /paper-plan/close`. The monitor does not update journal rows itself
  and does not call an exchange. Idempotency key
  `paper-exit-monitor:{command_id}`. A rerun replays that close.
- Journal identity: `exit_reason` is `stop_hit` or `target_hit`. The CLOSE
  lifecycle payload adds `exit_channel=PAPER_EXIT_MONITOR` and
  `exit_actor=paper_exit_monitor` plus bar evidence. The HTTP close body
  cannot set those fields, so a human close stays distinct.
- Scope: `plan_authority=canonical`, `execution_venue=PAPER_INTERNAL`,
  `execution_policy_version=paper-internal-entry/v1`, runner disabled, one
  target at fraction 1. Manual BloFin demo trades and governed BloFin demo
  plans are not read for exit. A history window that does not reach the entry
  does not close the trade.
- Flags: `paper_exit_monitor_enabled` (`PAPER_EXIT_MONITOR_ENABLED`) and
  `paper_exit_monitor_dry_run` (`PAPER_EXIT_MONITOR_DRY_RUN`). Both default
  false. Production refuses the enabled flag. Dry-run logs
  `paper_exit_monitor_would_close` and does not write. This decision does not
  arm either flag, change deploy config, or authorize a merge. Real-money
  trading stays rejected.
- Id: drafted as AT-ADR-107 on the paper exit-monitor branch. The grade-risk
  branch had already used AT-ADR-107, so the merge recorded this as
  AT-ADR-114. The batch already uses every id through AT-ADR-115, so this
  record is AT-ADR-116. The exit rule is unchanged.
- Reference: `docs/paper_exit_monitor.md`;
  `backend/src/app/services/paper_exit_monitor.py`.

## AT-ADR-120 — Stored-plan grade cap, server drawdown reset, unverified fill funding

- Date: 2026-10-19
- Id: Renumbered from AT-ADR-113. PR 280 owns AT-ADR-113. PR 281 uses
  AT-ADR-116 through AT-ADR-118.
- Decision: This amends AT-ADR-119. A stored plan is capped at the grade
  written on it when it was sized (`profile_sizing_grade`). The cap is the
  tighter of that stored percent and the same grade on the profile in force.
  The widest grade on the profile is not used. A plan with no readable grade
  is refused with `stored_plan_limits_uncomputed`. `reset_drawdown_latch`
  reads equity for the user stored on `ExecutionAccount` and reads the
  drawdown threshold as the tighter of the internal-paper and demo profiles
  in force. The caller does not pass either value. The proof owner stays the
  configured risk-policy owner, the same value `apply` uses.
- Fill funding: official `GET /api/v1/trade/fills-history`
  (https://docs.blofin.com/index.html) lists `fillPnl` and `fee` and does
  not list a funding field or its sign. `GET /api/v1/account/funding-fees`
  has `fundingFee` ("a positive value means received; a negative value means
  paid") on a separate bill, not on the fill. Fill funding stays unverified,
  so the demo close streak stays negative. A TODO marks the fill parser.
- What stays: real-money trading stays blocked. Both autonomous flags stay
  false. No live order is added.
- Reference: Grok review 5575648400 on `cf67b559`.

## AT-ADR-119 — Stored plans, open risk, and drawdown reset stay fail-closed

- Date: 2026-10-19
- Id: Renumbered from AT-ADR-112. PR 282 owns AT-ADR-112.
- Decision: A stored paper or demo plan is refused before approval and again
  before execution when `breaker_active` is true, including a daily or
  consecutive pause written after sizing and a drawdown latch that remains
  after `KillSwitchService.deactivate`. A missing execution clock time, or a
  missing stored plan, refuses with `stored_plan_limits_uncomputed` and does
  not raise. When a profile is active, the stored quantity is priced again
  against the current open book and is not rewritten.
  Open-risk notional uses only open positions and open lifecycles. Closed
  rows are excluded from both sides of that comparison, which allows a
  difference of at most `0.00000001`. `reset_drawdown_latch` accepts only an
  authenticated human, requires the same step-up proof as a profile apply,
  and reads equity from server portfolio accounting. A verified demo close
  passes funding cash into the streak only when every closing fill carried a
  finite figure; otherwise the streak stays negative. An unreadable paper
  liquidation price is recorded as `unreadable_liquidation`. An unreadable
  demo liquidation price logs a warning and is not labeled through the stop.
- What stays: real-money trading stays blocked. Both autonomous flags stay
  false. The risk engine `BLOCK` remains final. No live order, withdrawal,
  transfer, or leverage change is added.
- Reference: Griller re-check of `e2f488f3`; AT-ADR-110; AT-ADR-111.

## AT-ADR-111 — Paper and demo may collect grades above 1% without a promotion report

- Date: 2026-10-18
- Decision: Sofien decided at 17:20 CEST on 2026-10-18 (15:20 UTC), in chat
  with his AI Nerd agent, to allow grade risk up to 5% (A+) on internal
  paper and BloFin demo now, and to skip the AT-ADR-109 (c) promotion gate
  for those kinds only, so paper and demo can collect data. This amends
  AT-ADR-109 (c). It does not replace (a), (b), (d), or (e).
- Scope: the skip applies only when the stored profile kind is the
  server-set `internal_paper` or `demo` kind (`PAPER_DEMO_KINDS`, the same
  set as the 5% ceiling) and `paper_demo_data_collection` is true on that
  stored profile. `conservative`, `standard`, and `aggressive` still require
  a stored eligible promotion report before a tradable grade goes above 1%,
  and they cannot set `paper_demo_data_collection`. A caller does not pass
  an eligibility flag into sizing. `promote()` still refuses. The field is
  not a live-trading or arming exemption.
- What stays: the PR 278 human proof and the loosening cooldown still apply
  before a profile version is applied. Breakers, the daily-loss limit, and
  the open-risk limit still refuse a new trade. Real-money trading stays
  blocked. Both autonomous flags stay false. The per-trade ceiling stays 5%
  for paper and demo and 2% for every other kind.
- Rationale: paper and demo need observed outcomes for grades above the 1%
  placeholder before a server promotion report can exist. Collecting those
  outcomes does not satisfy (c) for any other kind, for live trading, or
  for arming either flag.
- Reference: Sofien, 2026-10-18 17:20 CEST; AT-ADR-109 (c); AT-ADR-110.

## AT-ADR-110 — Paper and demo per-trade ceiling of 5%

- Date: 2026-10-18
- Decision: Sofien instructed on 2026-10-18 that paper and demo risk at the
  stop may be chosen by setup grade, normally 1–3% and up to 5% for an A+
  high-confidence setup. A selectable profile `sofien_aggressive_paper`
  (and `sofien_aggressive_demo`) uses A+ 5%, A 3%, B+ 2%, B 1%, C 0%,
  default leverage 10x, and maximum leverage 20x. It is not applied
  automatically. The conservative placeholder stays the default, and a
  profile is active only when a version row exists.
- Ceilings: `PolicyKind.INTERNAL_PAPER` and `PolicyKind.DEMO` may store up
  to 5% per trade and 2000% of equity as notional. `conservative`,
  `standard`, and `aggressive` stay at 2% per trade and 100% notional.
  There is no live policy kind. Real-money trading stays blocked. Both
  autonomous flags stay false and a profile that sets either flag is rejected.
- Calibration: AT-ADR-111 amends AT-ADR-109 (c). An applied
  `internal_paper` or `demo` profile with `paper_demo_data_collection` may
  size a grade above 1%, up to the 5% ceiling, without a promotion report.
  Turning the field on is a loosening and is audited. `promote()` still
  refuses. Every other kind still requires the report. The field does not
  satisfy (c) for live trading or for arming either flag. The human proof,
  loosening cooldown, breakers, daily and open-risk limits, and the
  real-money block stay in force. The profile sizer sets
  `paper_or_demo_channel` from the execution channel. Internal paper and
  BloFin demo use that 5% branch. Any other channel keeps
  `risk_ceiling_for` and is refused above 2%.
- Drawdown kill switch: the profile scan that newly sets the drawdown
  latch calls `KillSwitchService.activate` for the organization, with an
  audited reason. A later scan does not deactivate it and does not call
  activate again while that latch remains, including after equity is back
  inside the threshold and after a human deactivation. Only
  `KillSwitchService.deactivate` turns it off. A new trip after
  `reset_drawdown_latch` can arm it again. A repeat scan does not write
  another activation audit row.
- Open stop risk: canonical open paper and demo lifecycle rows are priced
  at their planned stop together with Position rows. If a lifecycle row
  or the snapshot notional cannot be priced, the plan is refused with
  `open_risk_uncomputed` and the reason is logged.
- Leverage sets margin only. Loss at the stop, the exit-leg taker fee,
  funding, and on demo the 0.2% book buffer, must fit inside the grade
  cash. Costs above the profile share of R refuse with `costs_exceed_r`.
  Funding is the verified live rate when a demo
  `GET /api/v1/market/funding-rate` returns a finite rate, and otherwise
  a floor of 0.0001 per 8 hours. The sized rate is never below that floor
  (`max(abs(live), 0.0001)`). Periods are the expected hold divided by 8,
  and at least one. The wired path has no hold forecast, so the expected
  hold is 8 hours (one window). A caller that supplies a longer hold
  scales the periods. A failed or missing GET is not replaced with the
  mock funding fallback. Paper has no venue funding read and uses the
  floor. A 0.5% stop can refuse at this floor; the 20% cost-share ceiling
  is unchanged. Those same costs are counted in open risk at existing
  stops and in the `maximum_loss` reserved as daily loss. The shared
  organization-and-account ledger row is not rewritten. The wired paper
  and demo path refuses when liquidation is inside the buffer. It does
  not silently lower leverage. A library helper can still compute a lower
  whole-number leverage and never raises it; that helper is not applied
  on the wired path. If 1x still fails, or margin would exceed equity, the
  trade is refused and quantity is not shrunk to fit. Paper maintenance
  uses a conservative 2% rate because there is no venue tier feed. Demo
  reads public position tiers and refuses when they are missing. BloFin's
  GET /api/v1/market/position-tiers
  (https://docs.blofin.com/index.html) describes `minSize` as "Minimum
  position size" and does not say contracts. Order `minSize` on GET
  Instruments is in contracts. The public BTC-USDT isolated ladder
  (0, 2500, 5000, … USDT, with `contractValue` 0.001) matches quote
  notional. The tier floor is that number unchanged. Scaling it by
  contract value and price would keep the lowest maintenance rate.
  BloFin
  demo does not send a leverage or margin-mode write. Account margin mode
  is read from `GET /api/v1/account/margin-mode`. Leverage-info counts
  only when its payload includes `marginMode` and that mode equals the
  account read. A missing mode is not replaced with the requested mode.
  A leverage mismatch blocks with `demo_account_leverage_mismatch`. A
  demo account that is not isolated blocks with
  `demo_margin_mode_not_isolated`. An active demo profile does not treat
  the unprofiled 1% `size_below_minimum` result as the plan result; that
  plan is sized with `size_for_profile`. After a profile demo fill, the
  existing kill switch runs before a liquidation or unreadable-liquidation
  result, including when the observed liquidation price is through the
  stop and protection is reported attached. When that is the trigger, the
  kill-switch reason says liquidation is through the stop, and a critical
  alert is recorded on the existing notification path. Telegram stays off
  unless it was already enabled. An unreadable read keeps the
  protection-missing reason and is not labeled as a price through the
  stop. That path does not close or reduce the position and does not
  write leverage or margin mode. A verified demo profile close advances
  that account's consecutive-loss count. The sign subtracts verified fees
  and funding, so a trade that is flat or green on price and loses after
  fees is a loss. Missing fees do not reset the streak. Missing or
  non-finite demo funding cash does not reset it either. The 0.0001 per
  8h figure stays a sizing cost estimate and is not verified funding for
  the streak sign. Venue fill PnL stays ignored. The consecutive
  pause is set once, to the later of the next UTC midnight and the profile
  cooldown (`consecutive_loss_cooldown_hours`, default 24, range 1 to 168).
  Later scans do not push that expiry forward. When the expiry passes, the
  streak clears. While that pause is still in the future, a paper or demo
  close cannot zero the streak or clear the pause. A loss on that close
  may still increment the count. Ending the pause early stays on the
  human consecutive-loss reset. A manual reset requires the same human proof as a
  loosening apply, writes an audit row, and does not clear a drawdown
  latch. A Telegram proof without `iat` is rejected on that reset, the
  same as on apply. `decode_apply_proof` stays bound to a proposal UUID
  and is not widened for `consecutive-reset:<account_id>`. A future
  reset route needs its own strict decoder that accepts only a human
  actor. Shortening the cooldown is a loosening. The next demo entry stays
  refused while that pause is active. Drawdown reset stays human-only.
  The daily pause is stored on `daily_paused_until` as the next UTC
  midnight when the daily loss first trips. A later scan on that same UTC
  day keeps the stored timestamp after realized and unrealized PnL are
  back inside the limit, and `breaker_active` stays true. That timestamp
  is cleared only once it is no longer in the future. There is no early
  reset for the daily pause. A scan cannot clear a drawdown latch.
  `reset_drawdown_latch` remains the human path, and it still refuses
  while the live drawdown is outside the threshold. `KillSwitchService.deactivate`
  is the only path that turns the organization kill switch off. A
  loosening `effective_at` is stored once and a later scan does not move
  it earlier. SFP approach cooldowns and alert cooldowns are not risk
  pauses, and a breaker scan does not clear them.
- Claim ceilings for a profile plan are a per-claim view. They are not
  written onto the shared organization-and-account ledger row, so a later
  manual claim keeps the stored cash ceilings.
- Notional: when a profile is active, a cap that would shrink the grade
  risk refuses with `notional_cap_shrinks_grade_risk`. The previous fixed
  1% risk and 5% notional path remains only when no profile version row
  is active.
- Reference: Sofien, 2026-10-18; AT-ADR-111; `docs/risk_policy_arming_checklist.md` (c);
  `docs/sfp_level_approach.md`.

## AT-ADR-109 — Hard prerequisites before arming the grade-based risk policy

- Date: 2026-10-18
- Decision: Griller's verdict on PR #278 at
  `bc9041f09fef55517056a0da8238797ec9119cb8` is MERGE WITH BOTH FLAGS OFF.
  `autonomous_internal_paper_enabled` and `autonomous_blofin_demo_enabled`
  stay false. Real-money trading stays blocked. This record does not enable
  either flag, does not change deploy config, and does not merge the pull
  request. The checklist is `docs/risk_policy_arming_checklist.md`.
- The items below are HARD PREREQUISITES. Each one names the trigger that
  makes it binding. Writing them down does not satisfy them.

### (a) Trigger: before ANY proof issuer or apply use

Before a server issues an apply proof, and before apply is used, including
a paper or demo profile apply:

- Proofs are issued by the server and are single-use. Single-use means a
  stored nonce. The TTL is 5 minutes or less.
- The server checks a real verification: a TOTP or WebAuthn step-up, or a
  Telegram confirmation from the pinned chat id.
- The proof uses a signing key that is separate from `jwt_secret`.
- `proposal_id` is required and matches the proposal being applied.
- Current state on `bc9041f0`: the proof only fails closed. A missing or
  invalid proof is rejected. There is no issuer, no stored nonce, and no
  separate signing key. `decode_apply_proof` verifies with `jwt_secret`.
  A token that omits `proposal_id` is filled with the request's proposal
  id. `actor=user` is a label on the access token. It is not a TOTP,
  WebAuthn, or Telegram check.
- Follow-up to Grok review 5568860205: `decode_apply_proof` now rejects a
  missing `proposal_id` and a `proposal_id` that does not match the
  proposal. It does not copy the request id. A `telegram_owner` proof is
  fresh only when its `iat` is not in the future and is no older than 5
  minutes, even when `exp` is later. There is still no issuer, stored
  nonce, or separate signing key. `actor=user` is still a label.
- Follow-up to Grok review 5569113368: a step-up proof is rejected when
  `auth_time` or `step_up_at` is later than now. `decode_apply_proof` and
  `_require_fresh_step_up` both enforce that. The 15-minute maximum age
  remains. There is no clock-skew allowance on this path. A non-numeric
  or non-finite time claim is rejected instead of being parsed. `exp` does
  not extend either freshness window. Blank or non-string `sub`,
  `content_hash`, and `proposal_id` claims are rejected. An access token
  cannot be used as an apply proof, and an apply proof cannot be used as
  an access token. A human access token must include `exp`, `iat`, `sub`,
  and `org_id`, and the risk-policy route checks those against the tenant.
  Replaying a proof against another proposal is rejected. Applying the
  same proposal twice is `version_conflict`. There is still no issuer,
  stored nonce, or separate signing key. `actor=user` is still a label.

### (b) Trigger: before exposing drawdown reset

Before a route or any other caller can reset a drawdown latch:

- The reset requires the same proof as apply.
- Legacy tokens with no `actor` claim are refused.

### (c) Trigger: before ANY grade goes above 1%

Amended 2026-10-18 by AT-ADR-111. The promotion report is not required for
a stored `internal_paper` or `demo` profile whose
`paper_demo_data_collection` field is true. Every other kind still requires
the report. This amendment does not change (a), (b), (d), or (e), and it
does not cover live trading or arming.

Before a tradable grade risk is allowed above the 1% placeholder, on apply
or on sizing:

- The promotion report is computed by the server, per grade, from stored
  samples.
- The caller supplies no eligibility flags.
- One report does not cover the whole organization.

### (d) Trigger: before arming

Before either autonomous flag is set true, in any environment:

- Database triggers make breaker intervals, cancellations, promotion
  reports, and breaker resets append-only.
- A cancellation commits only its own work.
- A breaker that opens and closes between apply and `effective_at` also
  cancels that change.
- On `bc9041f0`, profile versions have a Postgres immutability trigger.
  Intervals, cancellations, promotion reports, and resets are refused by
  ORM listeners. Those four tables do not have the database triggers this
  prerequisite requires. `_cancel_version` commits the whole session. A
  persisted window cancels a loosening when the window covers
  `effective_at`. A window that has already closed before `effective_at`
  does not.

### (e) Follow-ups

These follow-ups are required. They do not replace (a)–(d).

- Trigger: before a downgrade of `a17riskpolicy002` is relied on. That
  downgrade refuses while cancellation, promotion-report, or breaker-reset
  rows exist. `a16` and `a18` already refuse when their guarded rows exist.
- Trigger: before a client uses `/risk/policy/*`. Route tests cover those
  routes.
- Trigger: the next edit of `backend/src/app/core/config.py`. Correct the
  stale comment at lines 205–209. This record does not edit that file.

### Wiring pull request

These stay in a later wiring pull request. This decision does not authorize
that work:

- an atomic budget reservation
- server-side open-risk computation
- the isolated-versus-cross margin mismatch on the demo path

`ExchangeMarginWriter` stays unwired.

- Reference: AT-ADR-107; AT-ADR-108; `docs/risk_policy_arming_checklist.md`;
  Griller review of PR #278 at `bc9041f0`, 2026-10-18.

## AT-ADR-108 — Griller follow-up on the grade-based risk policy

- Date: 2026-10-18
- Decision: The follow-up review of the grade-based risk policy (PR #278 at
  `895ca71f`) is accepted as changes, not as a merge. Real-money trading
  stays blocked. Both autonomous flags stay off. No BloFin write path is
  added. Alembic `a17riskpolicy002` revises `a16riskpolicy001`.
- `risk_percent` in the rule engine applies only when the caller sets
  `autonomous_sizing`. Human paper execution still copies
  `max_risk_per_trade_percent` (the settings API allows up to 10%) and
  strategy replays still pass 2-5%. Those paths do not opt in, so they
  behave as they did before the policy tables existed.
- Apply is not "any owner role" and a missing `actor` claim is not a human.
  Apply requires the configured `risk_policy_owner_user_id` plus a separate
  proof: a fresh interactive session with step-up or re-auth (15 minutes),
  or a confirmation from the pinned Telegram owner chat
  (`telegram_chat_id`). Service, agent, and bot tokens are refused. The
  apply body has no `confirm` field. The stored version channel remains
  `human` because the proof is what makes the apply human.
- Loosening becomes effective at `max(next UTC midnight, apply time + 24h)`.
  A proposal older than 72 hours cannot be applied. If a breaker is active
  when a scheduled loosening comes due, that version is cancelled and does
  not activate after the breaker clears.
- A tradable grade above the 1% placeholder is refused, on apply and on
  sizing, unless a stored promotion report for `deterministic-grade/v1`
  meets the existing calibration criteria. `promote()` still always refuses.
- R includes the exit-leg fee at the stop price. Funding is expected
  holding periods times the funding rate. Quantity is rounded down to the
  instrument lot step, refused below the minimum quantity, and R is
  recomputed after that rounding. A non-positive stop is refused. The
  remaining daily budget used for sizing is
  `min(profile daily, 6% ceiling, remaining daily)`.
- Postgres refuses UPDATE and DELETE of `risk_policy_versions` with a
  trigger. Downgrade of `a16riskpolicy001` refuses when trade records or
  breaker-state rows exist. Breaker pause, loss count, realized P&L, and
  unrealized P&L are persisted. A drawdown reset is an append-only row
  carrying the actor. The daily breaker uses realized plus unrealized P&L.
- Grok review of `38450d26` (2026-10-18) required one further correction.
  A loosening is cancelled when its `effective_at` falls inside a persisted
  breaker window, even if the first profile read happens after that window
  has closed. The window is the append-only open and close history in
  `risk_policy_breaker_intervals` (`a18riskpolicy003`), not the live latch.
  A breaker that opens only after `effective_at` does not cancel that
  version. `a18riskpolicy003` revises `a17riskpolicy002`. `a16` still
  revises `a15reissueauth001`. Real-money trading stays blocked.
- Grok review of `f06614c3` (2026-10-18) required four corrections, accepted
  on the same branch. The first stored profile is classified against the
  live placeholder: a looser result is a loosening, so the cooldown and the
  human proof apply, and an unchanged placeholder stays an initial apply.
  Breaker cancellation of a due loosening is committed, so closing the read
  session cannot roll it back, and that version stays cancelled after the
  breaker clears. A new proposal is required. Human access tokens are
  stamped `actor=user` at issuance. Agent, bot, and service tokens keep
  their own actor. An undeclared or missing actor is rejected before a
  proposal insert. A persisted drawdown reset is refused while equity is
  still outside the drawdown threshold.
- Left for a later wiring pull request, not this change: an atomic budget
  reservation, server-side open-risk computation, and the isolated-versus-cross
  margin mismatch on the demo path. Connecting `ExchangeMarginWriter` still
  needs its own pull request, a Griller review, and a further ADR.
- Reference: AT-ADR-107; Griller review of PR #278, 2026-10-18.
- Addendum 2026-10-18: Griller's verdict on `bc9041f0` is MERGE WITH BOTH
  FLAGS OFF. The hard prerequisites and their triggers are AT-ADR-109 and
  `docs/risk_policy_arming_checklist.md`. This addendum does not arm either
  flag.

## AT-ADR-107 — Grade-based risk policy is versioned, and ceilings fail closed

- Date: 2026-10-18
- Decision: Owner Sofien's grade-based risk policy is infrastructure only.
  Every risk number is a profile field. The shipped values are conservative
  placeholders: 1% risk on A+, A, B+, and B; C and an ungraded setup do not
  trade; leverage defaults to 1x; `autonomous_internal_paper_enabled` and
  `autonomous_blofin_demo_enabled` stay false and a profile that sets either
  to true is rejected. Real-money trading stays permanently blocked. This
  decision does not enable either autonomous flag, change deploy config, or
  authorize a merge.
- Ceilings, in code, not in the profile: 2% per trade, 6% daily loss, 6%
  aggregate open risk (sum of risk-at-stop), 20x leverage, and 25% of R as
  the maximum cost share. A profile outside a ceiling is rejected at
  validation and again when a version row is inserted. The loader does not
  clamp. Separate profile kinds (conservative, standard, aggressive,
  internal paper, demo) exist so paper and demo can diverge later. They
  start from the same placeholder.
- Governance: proposals may come from an authenticated human, agent, or bot
  token. Only an authenticated human access token can apply, and the version
  row's `actor_channel` check is `human`. Tightening takes effect immediately.
  Loosening needs `confirm=true`, takes effect at the next UTC midnight, and
  is refused while a daily-loss, drawdown, or consecutive-loss breaker is
  active. The active version is the highest version number whose
  `effective_at` is already due, so a later tightening is not overwritten by
  an earlier scheduled loosening. Versions are immutable. A change applies
  to new trades only; the trade row keeps the version it was sized with.
- Grading: `deterministic-grade/v1` uses only the trend and momentum scores
  from `app.analysis.confidence`. The first grade bound to a setup stays
  bound. AI text may explain that grade and cannot raise it.
- Sizing: effective risk is the minimum of the adjusted grade risk, the
  profile allowance, the 2% ceiling, and the room left in both the daily
  budget and the aggregate open-risk cap after open risk-at-stop. Quantity
  comes from the adverse stop distance (stop plus any gap past the stop)
  with fees, absolute funding, and slippage inside R. Costs above the
  profile's share of R refuse the trade. Notional caps can only reduce
  quantity. Leverage sets isolated margin and does not change quantity.
  A missing adjuster input or a missing maintenance tier refuses the trade.
- Breakers: daily loss and four consecutive losses pause new trades until
  the next UTC day. A 10% drawdown from the equity peak trips the kill
  switch and stays latched until an authenticated human resets it, and the
  reset does not stick while the drawdown is still outside the threshold.
- Liquidation: isolated margin only. The buffer is the larger of twice the
  stop distance and the stop distance plus 1.5 ATR, using the maintenance
  tier for the position notional. The post-fill check can say
  `close_or_reduce`. It does not call an exchange.
- Exchange writes: this decision does **not** add a BloFin endpoint that
  sets leverage or margin mode. `ExchangeMarginWriter` in
  `app.services.risk_policy.exchange_margin` is unwired and raises if
  called. Connecting that write needs its own pull request, a Griller
  review, and a further ADR that covers the allowlist, the demo host, the
  pinned account, and a read-back that matches. Until then the check is
  local.
- Ledger: PR 258's unverified fallback stores `max_symbol_notional` equal
  to `max_notional`, and demo auto-approval already refuses that row. A
  bound profile produces a symbol cap from per-trade notional that is
  strictly below aggregate notional, and a daily-loss amount that is also
  strictly below aggregate notional. The risk engine's `risk_percent` rule
  blocks a stated risk above the ceiling or the active profile cap, and
  blocks a ledger whose symbol cap is not independent of total notional.
  Callers that omit both the field and the ledger are unchanged.
- Calibration: the promotion report needs at least 100 out-of-sample trades
  per tradable grade on one frozen grader version, strictly ordered
  expectancy, a lower 90% bound above zero for A+ and A, walk-forward folds
  that keep A+ and A positive, and the same results after the slippage
  haircut. A full sample that then fails is flagged for auto-demotion on
  the report. `promote()` always refuses. Promotion is manual.
- Reference: Griller review 2026-10-18; PR #258; AT-ADR-106. Alembic
  `a16riskpolicy001` revises `a15reissueauth001` and downgrades by dropping
  the new tables, refusing while a trade record exists.

## AT-ADR-106 — Two autonomous modes; demo auto-approval stays inside risk limits

- Date: 2026-10-17
- Decision: Owner Sofien decided (2026-10-17 22:49 CEST) that there are two
  autonomous trading modes, (1) autonomous on internal paper and (2) autonomous
  on the BloFin DEMO account, and BloFin demo trades may be auto-approved
  without per-trade human approval, but only within the configured risk limits,
  with the kill switch always honoured. Real-money trading stays permanently
  blocked in code. Both modes ship behind feature flags that default OFF.
- Flags: `autonomous_internal_paper_enabled` and
  `autonomous_blofin_demo_enabled`. Both default false. Both true, a flag that
  disagrees with `execution_mode` / `exchange_mode`, or unreadable settings
  fail closed. No flag means that mode is disarmed.
- Demo auto-approval: allowed only when the demo flag resolves cleanly, the
  kill switch is verified inactive, and the order fits the configured account
  limits that already exist (max position / symbol notional, total exposure,
  daily loss, and the stored trade-slot cap). Unreadable limits fail closed.
  The existing claim predicate remains the execution authority.
- Safety: `execution_mode=trade`, `exchange_mode=trade_live`, and
  `enable_real_trading=true` stay permanently rejected. This decision does not
  enable either flag, change deploy config, or authorize a merge. It does not
  reopen live trading. AT-ADR-020's rejection of an autonomous live path
  remains in force.
- Risk gate, stated honestly (2026-10-17 review): `RiskEngine.evaluate()` does
  not gate the demo order. Eligibility runs only some of its rules (daily loss,
  weekly loss, cooldown, portfolio conflict, and open exposure against the 5%
  equity cap) in `backend/src/app/signal_fusion/action_eligibility.py`. Demo
  sizing may use up to 10% of equity as notional and 1% as loss
  (`governed_blofin_demo.py`), which is above that 5% per-order cap. The claim
  predicate remains the execution authority for the kill switch, daily lock,
  total exposure, daily loss, trade slots, and symbol notional. The numeric
  fallbacks used when no ledger row exists are unchanged. Demo auto-approval
  refuses with `risk_limits_unverified` when that fallback has no
  `daily_loss_limit`, and when an existing ledger row has `max_daily_loss` or
  `max_symbol_notional` greater than or equal to `max_notional` (the 100%
  fallback a first claim would persist). A later grade-based risk-per-trade
  policy may loosen that guard.
- Approval identity: an autonomous auto-approval is actor type `SYSTEM` on
  channel `AUTONOMOUS`, not the plan user on the API channel. `actor_id` stays
  the tenant user because that column is a user foreign key. The approval
  audit row records the mode, both flag values, policy version
  `at-adr-106/v1`, and the limit snapshot used. Demo records the account ledger
  snapshot that passed. Paper records that the account ledger is enforced at
  claim, not at this approval. Journal events carry `approval_channel`.
  Learning attribution maps `AUTONOMOUS` to `autonomous_approval` and does not
  set `plan_approved`, so those trades are not human approvals and do not
  enter human-approved win/loss counts. The verified demo close copies
  `approval_channel` from the authorization.   Once a record is autonomous,
  that flag is sticky: a later fill or close with no channel cannot flip
  `plan_approved` back to true. `autonomous_approval: false` is omitted from
  the canonical facts hash so records stored before the field still load.
  A true value stays in the hash. An autonomous approval's trader-behavior
  axis is `autonomous_approval`, not `approved_plan`. A missing channel on a
  legacy event that was never autonomous stays a human approval. If the
  kill switch (or the same re-check) blocks after the authorization is
  issued, that authorization is revoked and an `approval_decision` audit
  row records `reason=post_approval_recheck`. A later pre-check that finds
  an AVAILABLE AUTONOMOUS authorization for that revision revokes it with
  `reason=autonomous_precheck_blocked`. A claim that blocks after issuance
  revokes that authorization with `reason=claim_blocked`. Both write the
  same approval-decision audit. A revoke that changes nothing writes no
  second audit row. A contradictory autonomous gate refusal also revokes
  leftover AVAILABLE AUTONOMOUS authorizations before it returns.
  A REVOKED or EXPIRED authorization is not reused. A later cycle whose
  pre-check passes issues a new authorization on the same approval, with a
  new binding and a new approval-decision audit that names the new
  authorization id. A CONSUMED row is reused, so a revision still has one
  ALLOW. An AVAILABLE row is reused only when its channel and actor match
  the issuance being requested. A leftover API, WEB, or TELEGRAM row is
  revoked with an approval-decision audit before an autonomous authorization
  is inserted, and a human issuance does the same to a leftover autonomous
  row. Two workers still cannot hold two active authorizations for one
  approval. The claim remains
  one entry execution per plan revision. An autonomous execution idempotency
  key is ``paper-exec:{candidate_id}:{authorization_id}``. The human and API
  path keeps ``paper-exec:{candidate_id}``. A committed blocked command for a
  revoked authorization therefore does not conflict with the reissued
  authorization, and a second ALLOW for that revision is still refused. The
  kill switch, unverified risk
  limits, and production refusal are unchanged. Downgrade of a15 refuses on
  every dialect when one approval has more than one authorization row.
- Existing governed demo flow: with both autonomous flags off, the governed
  BloFin demo worker keeps its pre-existing API-channel approval. That path
  is not an AUTONOMOUS approval and is not subject to the unverified-ledger
  refusal. The refusal applies only when the demo flag resolves to the
  autonomous BloFin mode. A contradictory autonomous configuration still
  fails closed and does not fall through to the API approval. The
  `approval_authorizations.channel` CHECK includes `AUTONOMOUS` so an
  Alembic-migrated database can store that channel. Fresh metadata
  (`create_all`) installs the same predicate as an explicit table CHECK.
  Other portable enums stay VARCHAR without a database CHECK. Downgrade
  restores the WEB/API/TELEGRAM check and refuses while an AUTONOMOUS row
  exists. That row check runs on every dialect. Replacing the CHECK uses
  the PostgreSQL catalog because PostgreSQL is the migrated production
  database. Experiment price PnL aggregates are written only when the
  verified closures share one confirmed currency. Mixed or missing
  currencies leave those fields unknown.
- Environment: `environment=production` refuses both modes at startup in
  `deployment_safety.py` and again when the mode is resolved. Staging may
  still arm a flag. BloFin demo execution stays staging-only through the
  existing governed demo posture.
- Kill switch: it stops new entries only. It does not close open demo
  positions. An operator closes those on the BloFin demo account by hand and
  confirms the account is flat. See
  `docs/autonomous_blofin_demo_execution.md`.
- Reference: PR #258; `backend/src/app/services/autonomous_trading.py`.

## AT-ADR-105 — Analytics filters on a mutation turn return 422

- Date: 2026-10-16
- Decision: Owner Sofien decided that `analytics_filters` on a non-read turn
  must return HTTP 422. `handle_turn` raises `ValidationAppError` when those
  filters are present and `classify_turn` is not `TurnOperation.READ`, before
  ordinary capture can rewrite a proposal or refusal into a read. A proposal
  such as "Create a strategy", a paper-trade request, and a real-trading
  refusal all stay rejected. No strategy-analytics reply is produced.
- Correction: Commit `df0a12d3` wrongly changed
  `test_analytics_filters_cannot_route_mutations` so "Create a strategy" with
  `analytics_filters` expected 200. That expectation is restored to 422. This
  ID is not the 2026-10-10 AT-ADR-099 and not the colliding AT-ADR-099 text on
  `fix/strategy-analytics-422` (`4d1c7349`).
- Safety: Fail closed. No order, mode change, or analytics reply is produced
  for that input. Real trading stays disabled.
- Reference: `backend/src/app/interactive_agent/service.py`;
  `backend/tests/test_agent_strategy_analytics.py`; ported from
  `4d1c734930081be2cc74259773c6193cba0182d5`.

## AT-ADR-104 — Full backend report lists failures, not every pass

- Date: 2026-10-15
- Decision: The complete backend gate keeps the 300s thread timeout, the 50
  slowest durations, and the junit/log artifacts. It reports failures and errors
  (`-rfE`) instead of every passed test (`-rA`). Run 38765481172 reached 100%
  of 4848 collected tests in about 70 minutes, then the all-pass captured-output
  report was still running when the 150 minute job was cancelled, so no junit
  artifact was uploaded.
- Limit: This does not skip tests and does not change assertions. The known
  analytics-filter 422 failure stays a real failure. No merge or dispatch is
  authorized by this decision. This ID is not the 2026-10-10 AT-ADR-099.
- Reference: `.github/workflows/ci.yml`; run 38765481172.

## AT-ADR-103 — Full backend gate keeps diagnostics and a nightly run

- Date: 2026-10-15
- Decision: Owner Sofien approved hardening only the complete backend gate.
  Focused push/PR selection stays as it is. `workflow_dispatch` with
  `full_backend=true`, and the default-branch schedule at 02:00 UTC, run the
  full suite with a 300s thread timeout, `-rfE`, the 50 slowest durations, junit
  and pytest log artifacts, and a 150 minute backend job timeout.
- Limit: The nightly schedule does not run until this workflow is on the default
  branch. It is not release acceptance by itself. No merge, deploy, secret,
  Render, or branch-protection change is authorized by this decision. This ID
  is not the 2026-10-10 AT-ADR-098.
- Reference: `.github/workflows/ci.yml`; approved 2026-10-15 15:51 CEST.

## AT-ADR-097 — Separate native closure from protection attribution

- Date: 2026-10-09
- Decision: Verify exact native entry/reduce-only exit sequence and quantity
  independently from optional TP/SL ancestry. Preserve entry/exit facts while
  retaining explicit uncertainty about historical, active and triggered protection.
- Safety: Flatness is insufficient closure proof. New protection/outside-range
  kill activation requires positively observed open exposure; existing kill and
  unrelated holds persist. Recovery remains scoped, audited and idempotent.
- Presentation: Default to one configured native account; bind recorded performance
  to saved account provenance and verified lifecycle evidence. Unknown funding/net
  and unsupported strategy outcomes stay unknown. Exact Journal detail reuses
  immutable execution evidence and separate personal observations.
- Limits: Fixtures do not verify native account mapping or the user's live exits.
- Reference: `docs/blofin_reconciliation_presentation_repair.md`; AT-117.

## AT-ADR-094 — Past qualification selects immutable trade lineage

- Date: 2026-10-06
- Decision: Latest-position past qualification routes to scoped recorded Journal
  lineage even without a literaltrade noun. Same-plan followups reuse only a
  revalidated persisted selection; explicit current setup requests retain their path.
- Evidence: Locate the exact immutable candidate/assessment/plan setup decision,
  independent of mutable projection identity/state. Owner/version isolation remains.
  Recorded conditions and bounded candle hashes are references, not reconstructed
  historical OHLCV or current evidence. Material candle-value gaps stay visible.
- Policy: Read current minimum/version and compare original plan terms through
  existing deterministic application authority; preserve its visible conclusion.
  Knowledge prose never sets policy; historical approval is not current eligibility.
- Limits: Read-only Agent repair; no strategy thresholds, history or execution-flag
  change. Focused/mocked tests do not establish live conversational acceptance.
- Reference: `docs/agent_historical_setup_explanation.md`; AT-109.

## AT-ADR-093 — Prospective allocation-weighted gross 1R entry floor

- Date: 2026-10-06
- Decision: Require exact gross allocation-weighted reward/risk ≥1 at the worst
  allowed entry-zone price for new linear canonical plans, authorization, claim
  and governed demo dispatch. Unpriced runner reward is zero; allocations sum to
  one. Existing maximum-loss allowances retain costs; this is no net-R guarantee.
- History: Preserve semantic schemas, hashes, target levels and existing replays.
  Reads/reconciliation do not revoke history. Below-minimum unsent entry is refused.
  Strategy exit changes require existing explicit new-version governance.
- Evidence: Scoped compiled definition, exact immutable detector decision and
  integrity-checked persisted assessment summary are read-only reference data.
  Distinguish absence from inaccessible stores; retain unique material warnings.
- Reference: `docs/minimum_planned_reward_risk_release.md`; AT-108. No deployment,
  live repair, exchange order or full CI from implementation.

## AT-ADR-092 — Migration readiness uses the deployed package path

- Date: 2026-10-06
- Decision: Resolve Watcher migration scripts relative to its located backend
  package/alembic.ini, independent of the process working directory. Require one
  expected head and one matching stored revision; unreadable or split history
  remains a refusal. Preserve the literal migration chain in focused regressions.
- Diagnostics: A startup refusal remains idle until a reviewed worker restart;
  later DB recovery or a process heartbeat does not prove scanning. Reproduce this
  behavior and provide fresh same-container preflight, posture, lease, scheduling
  and successful evaluation checks. Do not automatically retry activation.
- Limits: The declared Docker working directory normally resolves the old path;
  the observed staging cause requires live evidence. No SFP tolerance, receipt,
  strategy, execution, risk or deployment-authority change.
- Reference: `docs/pr215_release_blockers.md`; AT-107 / PR215.

## AT-ADR-091 — Conversation selections are scoped references, never authority

- Date: 2026-10-06
- Decision: Store one server-selected Journal/account reference in the existing
  assistant transcript payload. Followups revalidate conversation, tenant, owner
  and account and re-read current immutable lineage. Explicit selectors override;
  failed/ambiguous selection invalidates earlier context. New chats remain separate.
- Context: Bounded user/assistant history is conversational input only, distinct
  from fresh stored facts. No parsing assistant prose into identity or authorization.
- Presentation: Preserve bounded complete explanation, evidence and source references
  in Stored evidence; clean sentence/citation endings, display-only readable numbers,
  and mandatory material missing-evidence warnings. No economic record mutation.
- Import: Agent reuses Knowledge preview and explicit save. Importing documents
  grants no strategy, execution, risk or Journal authority; screenshot controls hide.
- Limits: No reference backfill from old prose; bare currencies need stored plan
  metadata. Live model/browser/import quality requires supervised acceptance.
- Reference: `docs/agent_conversation_continuity.md`; AT-107.

## AT-ADR-090 — Only immutable audited actual-exit proof releases a demo lifecycle

- Date: 2026-10-06
- Decision: Reuse the same demo account after exact native closing-fill, parent
  order/protection and account-wide flat/no-pending evidence. Persist one immutable
  scoped resolution with canonical Journal CLOSE and strict audit; release only
  owned exposure/reservation under the account epoch. Mutable Journal status and
  acknowledgments confer no release authority. Unresolved ALLOW continues blocking.
- Safety: Preserve daily loss/trade accounting, historical clocks/hashes, fresh
  Candidate/plan authorization, fencing, uncertain entry holds, one concurrent
  position and one execution venue. Partial cancellation retains filled exposure's
  loss/trade budget. Concurrent/restarted/late reconciliations cannot double-release.
- Outcome: Actual fees and native fillPnl are recorded; undocumented gross/net,
  funding and win/loss semantics remain missing. No assumed economic profit credit.
- Limits: Bounded exclusive NET/cross account history; actual staging TPSL linkage
  unverified. Unfilled, ambiguous, unsupported or incomplete history stays held.
  PostgreSQL/ORM protection preserves resolution history; nonempty downgrade refused.
- Reference: `docs/governed_demo_lifecycle.md`; independent PR211/212/213 remain intact.

## AT-ADR-088 — Demo first-entry readiness precedes repeat lifecycle authority

- Date: 2026-10-06
- Decision: First-entry readiness is an independent PR after Agent grounding PR211.
  Immutable plan venue determines whether historical ALLOW consumes the demo slot;
  internal-paper exposure, reservations and daily risk accounting remain authoritative.
  Every historical demo ALLOW continues to block until a separate reviewed lifecycle
  implements actual exit proof and audited accounting release. History is never reset.
- Safety: One concurrent demo position across all five markets. Before planning and
  send, prove account-wide flat positions and no normal/TPSL pending orders with
  strict bounded reads. The existing safety epoch serializes competing claims.
  Reject existing plans with a different venue/policy before creating authorization.
  Preserve uncertain POST holds, exact plan hashes, risk, fences, TTL and SFP refusal.
- Limits: Simulated instrument contracts are not current venue availability. No
  strategy creation/approval, deployment, activation or exchange orders here.
  Final acceptance remains one consolidated exact-SHA full release CI followed by
  supervised live instrument preflight and a natural actual protected demo fill.
- Reference: `docs/five_market_demo_readiness.md`.

## AT-ADR-087 — Recorded trade explanations read historical execution authorities

- Date: 2026-10-06
- Decision: Natural recorded-trade intent selects canonical Journal within authenticated
  tenant/owner/account scope and reads its exact immutable plan and historical lineage.
  Strategy/Knowledge lookup cannot replace execution evidence. Optional Agent Risk
  capture enriches the read but is not required to discover an existing canonical trade.
- Truthfulness: Exact authorization, eligibility, risk reservation and actual fills are
  separate facts. Missing narrative/receipt/lineage remains missing; contradictory
  lineage refuses. Planned demo venue is not exchange fill proof. Empty Journal targets
  may be explained from the linked plan without projection repair or history mutation.
- Authority: Read-only. Historical facts and document proposals do not grant approval,
  execution, strategy or risk changes. Existing explicit capture-gated reader is retained.
- Acceptance: Focused local proofs establish selection/context, not live model quality;
  supervising deployment and repeat of the original question remain required.
- Reference: `docs/agent_recorded_trade_grounding.md`; AT-104.

---

## AT-ADR-089 — Baseline preview cannot transfer strategy approval

- Date: 2026-10-06
- Decision: An authenticated owner selects an immutable provisional Nested 15m
  baseline to preview ten exact market/direction specs. Shared deterministic template
  naming makes preview agree with existing explicit idempotent draft creation.
  Source/version and proposed-spec hashes bind the preview; no provider or authority
  mutation occurs. Existing selected versions are reused only when their specs match;
  changed selected versions produce visible conflicts.
- Safety: Each new independent version must compile and receive its own explicit
  approval. Existing source approval never transfers; no watchlist enablement,
  strategy permission, risk setting, demo arm or execution capability changes here.
  Live availability remains unverified until supervising provider preflight.
- Reference: `docs/nested_subscription_preview.md`; first-entry readiness PR212 and
  repeat lifecycle remain separate.

## AT-ADR-086 — Live REST finalization policy has a new immutable evidence identity

- Date: 2026-10-06
- Decision: Live Binance REST v2 validates native close/count metadata, excludes
  a five-second post-close settlement interval and requires two matching closed
  payload reads pinned to the same evaluation window. Replay v1 and Bybit remain
  unchanged; same-policy immutable conflicts still refuse.
- Evidence: Supplied final/revision1 receipt at close+614 ms has changed volume/count
  values. Zero-grace local-clock admission and repeated-read instability reproduce.
  Exact upstream late-aggregation versus correction attribution remains unconfirmed.
- History: An actual adapter policy change receives a new source/provenance version
  and identity hash. Reobserve into that namespace with current receipt clocks;
  preserve original full-key rows and all historical references. Never fabricate
  provider revisions, silently merge policies, overwrite receipts or backdate warm-up.
- Limits: Five seconds and stable REST reads are conservative admission conditions,
  not a provider finality guarantee. The boundary requires supervising release review,
  captured public comparison and fresh both-scope scheduled acceptance.
- Reference: `docs/sfp_candle_finalization_recovery.md`; AT-103.

---

## AT-ADR-085 — Journal target repair preserves immutable execution history

- Date: 2026-10-06
- Decision: New canonical execution events include approved targets with exact
  decimal strings; Journal projection converts them to its existing target schema.
  Keep semantic order and allocation, without redistributing reserved runner size.
- Replay: Reuse stored approved-plan and fill payloads so producer fixes cannot
  change historical event preimages/hashes. Do not repair projections implicitly.
- Repair: A dry-run-first one-trade CLI delegates mutation to the Journal projector.
  Require exact tenant/owner/account, canonical plan/envelope hashes, consumed PAPER
  authorization, ALLOW command and every recorded canonical lifecycle event lineage.
  Empty targets only; existing lifecycle/row locking, revalidation and strict audit
  make application atomic and idempotent. No immutable history or authority change.
- Acceptance: Reviewed deployment and supervising dry-run/apply/known-trade check
  are required; focused local tests do not establish live repair or release acceptance.
- Reference: `docs/journal_plan_target_repair.md`; AT-102.

---

## AT-ADR-084 — Focused development CI and explicit complete release acceptance

- Date: 2026-10-06
- Decision: Ordinary push/PR backend tests use a documented focused selection:
  config/deployment/workflow baseline, changed test files, direct module matches
  and explicit SFP receipt/logging neighbors. Full lint and the other five CI
  jobs, including evaluation/browser dependencies, keep their identities and checks.
- Release gate: Explicit `workflow_dispatch` with `full_backend=true` (manual default)
  runs the unfiltered backend suite with PostgreSQL and every existing CI job.
  Require the exact reviewed release SHA and all six jobs successful; skipped
  evaluation/browser checks or focused backend success do not establish acceptance.
- Limit: Focused selection is not exhaustive dependency analysis. Unmapped
  cross-module behavior needs explicit focused coverage and final complete CI.
- Safety: No protection-rule, credentials, deployment or activation changes.
  No full run is launched by this implementation task. Real trading stays disabled.
- Reference: `.ai/RELEASE.md`; AT-101; PR207 follow-up.

---

## AT-ADR-083 — Paper account setup preserves canonical execution identity

- Date: 2026-10-05
- Decision: Owner-only authenticated setup derives tenant/user from the principal;
  a strict empty body cannot supply account identity, modes or activation fields.
  PostgreSQL setup locks the existing unique tenant membership through the route's
  commit, then checks the owner's accounts. READ COMMITTED requests converge on
  one UUID without imposing a new constraint on historical account data.
- Reuse: Return a single enabled PAPER/NET account unchanged. Refuse disabled,
  invalid or multiple accounts; never create a replacement to reset governed
  execution history. All registration callers must use this service/lock.
- Audit: Commit creation with a strict `execution_account_registered` audit event.
  Repeated setup is a read/reuse and produces no extra creation audit.
- Authority: Registration establishes identity only. It does not grant execution
  permissions, approve strategies, authorize a plan, alter risk/credentials, or
  arm demo or real trading. UUID pinning and venue acceptance remain supervised.
- Reference: `docs/governed_blofin_demo_execution.md`; AT-098.

---

## AT-ADR-081 — Display snippets do not bound substantive document evidence

- Date: 2026-10-05
- Decision: Keep short display snippets, but independently resolve named tenant/user
  documents and read relevant full passages plus neighboring context for model synthesis.
  Balance requested topics before filling a 12,000-character source budget; label
  truncated legacy passages and omitted context. Preserve durable source citations.
- Authority: Document passages, including proposed discipline and unresolved decisions,
  are reference data. They do not establish approved settings or route execution actions.
  Keep this boundary in the model prompt and in a visible deterministic notice.
- Safety: Organization and user/shared boundaries apply to both document metadata and
  chunk content. No new database, provider call, strategy approval or risk mutation.
- Acceptance: Use the already stored staging Master Playbook; do not reimport it.
  Local coverage tests prove supplied passages and isolation, not live model quality.
- Reference: `docs/agent_presentation_grounding.md`.

---

## AT-ADR-079 — Conversational evidence and selected lifecycle remain explicit

- Date: 2026-10-05
- Decision: Reserve conversational prose space and persist bounded stored evidence
  independently in the assistant payload; expose it through native collapsed details.
  Preserve old transcript delimiters and keep deterministic evidence warnings visible.
- Authority: Read the latest canonical lifecycle event for the selected version.
  Research validation, setup confirmation and execution eligibility remain distinct.
  Include both Nested and SFP stored definitions and scopes in comparison context.
- Demo reads: Explain durable actual venue fill/Journal/reconciliation facts without
  inventing a manual conversation capture. A command authorization is not a fill;
  recorded protection observations are not current market claims.
- Safety: These changes read and explain; proposals and execution retain their
  existing deterministic authority and explicit controls. Real trading remains disabled.
- Reference: `docs/agent_presentation_grounding.md`.

---

## AT-ADR-082 — File preview confirms bounded canonical Knowledge ingestion

- Date: 2026-10-05
- Status: Accepted for AT-096; staging acceptance pending.
- Decision: Extract TXT/Markdown, DOCX body/table text and selectable-text PDF in
  resource-limited child processes. Preview performs no SQL/vector writes. A
  ten-minute signed receipt binds the authenticated organization/user, filename,
  file bytes, extracted text, title, category and parser version to explicit save.
  Save re-extracts and calls the existing Document/Chunk ingestion service.
- Provenance: Nullable `documents.ingestion_metadata` JSON records filename,
  SHA-256 of raw bytes and extracted text, sizes, parser version and confirmation
  time, plus SQL chunk count and observed vector upsert/backend/fallback status.
  Raw binaries are not stored; legacy metadata remains null/unverified.
- Ownership: Duplicate hashes and lookup include organization and exact user;
  same-owner legacy pasted imports remain compatible. Same-category identical
  files converge for that principal even when renamed; other owners/categories
  remain independent. SQL ownership is rechecked when projecting vector hits.
- Safety: No document database replacement, OCR, strategy/risk/execution mutation,
  Agent edits, deployment or exchange orders. Import does not claim live hosted
  indexing readiness. The already stored Master Playbook is not reimported.
- Reference: `docs/knowledge_file_import.md`; migration `a4knowledge001`.

---

## AT-ADR-078 — Advisory attention is a read-only record projection

- Date: 2026-10-02
- Status: Accepted for proactive attention queue 001.
- Decision: Derive tenant/user attention through SELECT-only adapters and a pure
  reducer with an explicit clock. Preserve source provenance, stable semantic
  identity, fixed expiry, existing acknowledgement states and deterministic
  risk-first ordering. Missing records confer no facts or action authority.
- Ownership: Market/setup/Watcher records are organization-shared; journal,
  risk, research, proposals and delivery remain user-scoped except explicitly
  organization-owned alerts. Exclude global operational rows without ownership.
- Safety: No queue persistence, acknowledgement writes, providers, delivery,
  approval, execution, risk evaluation or worker activation. All next actions
  are human review recommendations. Agent integration is deferred to avoid
  shared Agent implementation changes. Existing authorities remain final.
- Reference: `docs/proactive_attention_queue_001.md`; exact base
  `5f4f0a467ce8f68f9c7a82d3b32307d559a9f092`.

---

## AT-ADR-001 — Adopt private `.ai/` collaboration + iCloud handoff workflow
- **Date:** 2026-07-19
- **Status:** Accepted
- **Context:** Standardize the ChatGPT ↔ Cursor workflow already used for OnePilot AI.
- **Decision:** Add a version-controlled `.ai/` layer and Cursor project rules, plus
  per-session `HANDOFF.md` + `CHANGELOG_SESSION.md` (gitignored) and a content-aware macOS
  iCloud sync (script + LaunchAgent) that mirrors only those generated handoff docs.
- **Consequences:** Consistent, clone-portable handoffs; no application-code or Git-history
  changes; generated handoff artifacts never committed.

## AT-ADR-002 — Version-control governance; keep generated handoffs private
- **Date:** 2026-07-19
- **Status:** Accepted
- **Context:** Durable governance (`.ai/`, `.cursor/rules/`) must reach every clone, but
  per-session handoffs contain evolving state and should not pollute Git history.
- **Decision:** Track `.ai/` and `.cursor/rules/` in Git. Keep `HANDOFF.md`,
  `CHANGELOG_SESSION.md`, and `*.local.md` gitignored. The generated handoffs are
  mirrored only to iCloud via `sync-alphatrade-ai-handoff.sh` (two lightweight docs).
- **Consequences:** A fresh clone receives the AI instructions and Cursor rules; the
  repo working tree is the source of truth for handoffs and iCloud is a verified mirror.

## AT-ADR-003 — Preserve paper-only trading posture as an invariant
- **Date:** 2026-07-19
- **Status:** Accepted (pre-existing, reaffirmed)
- **Context:** Safety-critical trading system.
- **Decision:** `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`,
  `EXCHANGE_MODE=paper_internal`, `PROVIDER_MODE=fallback` (staging) are invariants.
  Any change requires a separate, explicitly authorized task.
- **Consequences:** Enforced in `deployment_safety.py` / `exchange_safety.py` and CI.

## AT-ADR-004 — Adopt Master Workflow v2.0 as the authoritative standard
- **Date:** 2026-07-19
- **Status:** Accepted (supersedes the workflow portions of AT-ADR-001/002)
- **Context:** A consolidated v2.0 standard (`ALPHATRADE_AI_MASTER_WORKFLOW.md`) unifies the
  earlier catch-up prompt and mobile-blocker addendum into one governance document.
- **Decision:** Save it as `.ai/MASTER_WORKFLOW.md` and make it authoritative from `.ai/MASTER.md`.
  Adopt the five-status model (`IN_PROGRESS`, `REVIEW_REQUIRED`, `BLOCKED`, `FAILED`, `READY`;
  no `DRAFT`), the Mobile Status block + Schema Version 2.0 metadata, the normalized
  `Source File SHA256` self-hash (hash of the doc with its own hash line removed), mandatory
  sync at every phase/blocker/review/failure, and broker/exchange modes A–D (D disabled).
  Keep `HANDOFF.md`/`CHANGELOG_SESSION.md`/`*.local.md` and `.ai/local//.ai/private/` ignored.
- **Alternatives considered:** Keep the v1 ad-hoc handoff format (rejected: no blocker/review
  states, hardcoded timezone, body-only hash); embed private material in tracked files (rejected:
  use ignored `.ai/private/` / `.ai/local/`).
- **Safety impact:** None to application behavior; strengthens blocker/review/failure handling and
  reaffirms paper-only posture and disabled real execution (mode D).
- **Consequences:** Templates and Cursor rules updated; installation stops at `REVIEW_REQUIRED`
  before any commit until a human authorizes it.
- **Validation:** `bash -n` sync script, `plutil -lint` LaunchAgent, SHA256 + `cmp`, idempotent
  second sync, secret scan of tracked governance, no app-code changes.
- **Reaffirmation (2026-07-22, AT-000B):** Supplied
  `ALPHATRADE_AI_MASTER_WORKFLOW.md` reinstalled byte-identical
  (SHA256 `4255f52c…`) as `.ai/MASTER_WORKFLOW.md`. Governance reconciled
  (`PROJECT_CONTEXT`, `MASTER.md`, trading-safety Mode A/C wording). No app-code changes.

## AT-ADR-005 — Real-money (Mode D) requires phased program; paper Criticals first
- **Date:** 2026-07-21
- **Status:** Accepted
- **Context:** AT-010 readiness audit found paper-MVP/staging readiness with Critical/High
  gaps (unauth tools, soft data degradation, under-wired risk/kill switch). A real-money
  program must not bypass paper hardening.
- **Decision:**
  1. Keep `main` paper-first; short-lived feature branches only; no long-lived live-trading branch.
  2. Close paper Critical findings (AT-011…AT-014, AT-007) before sandbox execution work.
  3. Mode D follows Phases 0–4 in `docs/AT010_real_money_safety_roadmap.md`; Phase 3–4 require
     separate explicit human authorization beyond ordinary implementation tasks.
  4. Never merge changes that weaken `EXECUTION_MODE=paper` / `ENABLE_REAL_TRADING=false` defaults.
- **Alternatives considered:** Long-lived live branch (rejected: drift + accidental merge risk);
  implement sandbox immediately (rejected: Critical paper gaps remain).
- **Safety impact:** Strengthens fail-closed path to any future capital; no live trading enabled now.
- **Consequences:** Backlog AT-011…AT-024 added; next slice is AT-011 authz.
- **Validation:** AT-010 deliverables reviewed; staging verify-safety remains paper-only.

## AT-ADR-006 — Staging/production RAG providers fail closed (AT-013)
- **Date:** 2026-07-22
- **Status:** Accepted (implementation pending review/commit authorization)
- **Context:** Silent mock LLM/embeddings and Qdrant→in-memory substitutes created
  split-brain knowledge behavior and false readiness in non-local environments.
- **Decision:**
  1. `provider_fail_closed` for `ENVIRONMENT` in `{staging, production}`.
  2. Staging/production require configured `OPENAI_API_KEY` and hosted `QDRANT_URL`;
     reject `PROVIDER_MODE=mock`.
  3. OpenAI LLM/embeddings and Qdrant refuse silent mock/memory substitutes when
     fail-closed; ingest/search raise clear `ServiceUnavailableError` (no secrets).
  4. Readiness treats critical LLM/embeddings/vector as not ready when unavailable,
     degraded+fallback, or accidentally mock.
  5. Local (and pytest default local settings) retain explicit mocks/soft fallback.
- **Alternatives considered:** Soft degrade with warnings only (rejected: false healthy);
  ban mocks in all environments (rejected: blocks offline local/dev).
- **Safety impact:** Strengthens knowledge integrity; no trading-mode change;
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false` preserved.
- **Consequences:** Branch `feat/at-013-rag-provider-fail-closed`; stop at
  `REVIEW_REQUIRED` before commit/push/deploy.
- **Validation:** Scoped ruff/mypy + AT-013/provider/RAG/deployment/health tests (see handoff).

## AT-ADR-008 — Audit/usage unit-of-work + gated RED metrics (AT-016)
- **Date:** 2026-07-22
- **Status:** Accepted (implementation pending review/commit authorization)
- **Context:** AT010-H6 / RR-10 — `AuditService.record` and `UsageService.record`
  called `session.commit()` mid-request on the shared FastAPI Session, splitting
  business durability from audit/usage. No scrapeable RED metrics existed.
- **Decision:**
  1. **Caller / UoW owns commit.** Routes (or application services that already
     commit) perform the authoritative `session.commit()` after business mutation,
     audit flush, and usage flush.
  2. **Audit and usage flush only.** `record()` adds + flushes so IDs (e.g.
     `audit_event_id`) are available before commit; no hidden service-level commit.
     Flush failures that must not wipe prior business rows use a nested savepoint
     via `run_in_savepoint_when_active` — nested only when the DBAPI connection
     already has an open transaction. (SQLite: `RELEASE` of a SAVEPOINT that
     *started* the transaction would otherwise commit.)
  3. **No global auto-commit on `get_session()`** — existing explicit commits remain;
     a blanket teardown commit would surprise tool/worker paths.
  4. **Durable rejected/security events** use explicitly named
     `AuditService.record_durable_isolated()` (dedicated short-lived session +
     commit) after or outside the business transaction — rate-limit, quota block,
     paper reject, kill-switch trigger, auth security events.
  5. **Usage persistence** stays fail-open unless `observability_strict_mode`;
     strict audit flush failures raise and prevent the caller commit.
  6. **RED metrics** via `prometheus-client`: `http_requests_total`,
     `http_request_duration_seconds`, `http_requests_in_progress` with labels
     `method` / `route` (template) / `status_class` only. `METRICS_ENABLED=false`
     by default; outside local, `METRICS_SCRAPE_TOKEN` is required. `/metrics` is
     not observed recursively. Health/ready stay separate.
- **Alternatives considered:** Global commit-on-success in `get_session()` (rejected
  for this slice — too many existing commits); unrestricted public `/metrics`
  (rejected — Render scrape surface); embedding org/user labels (rejected — cardinality
  + privacy).
- **Safety impact:** Stronger atomicity for paper execution/approvals; durable
  security audits preserved; no trading-mode change.
- **Consequences:** Branch `feat/at-016-audit-uow-metrics`; stop at `REVIEW_REQUIRED`.
- **Validation:** `tests/test_at016_audit_uow_metrics.py` + audit/usage/execution/
  approval/risk/auth regressions.
- **Amendment (2026-07-23 — idempotent usage metering):** Route meters
  `paper_execution` only when `PaperOrderPlacementResult.created_new` is true.
  Sequential idempotent replay returns the existing order without a second usage
  row or creation audit. Concurrent first-writers may still race past the lookup
  and hit unique constraints; current contract is unique-conflict + client retry
  (proven in `test_concurrent_identical_requests_remain_safe`). Server-side
  Postgres convergence is AT-028 — not part of this amendment.

## AT-ADR-007 — Honor PROVIDER_MODE + narrative quota + search opacity (AT-015)
- **Date:** 2026-07-22
- **Status:** Accepted
- **Context:** AT-010 H5/H10 — factory ignored `PROVIDER_MODE=mock` for LLM/embeddings
  when a key was set; `limit_agent_narrative` was unused; search opacity needed UI/tests.
- **Decision:**
  1. Local `PROVIDER_MODE=mock` forces mock LLM/embeddings (and mock dims) even with key.
  2. Staging/production continue to reject `PROVIDER_MODE=mock` (AT-ADR-006 unchanged).
  3. Narrative polish checks `agent_narrative` quota before LLM; hard block → deterministic
     fallback (chat analysis still succeeds; no narrative LLM spend).
  4. Search continues to return `degraded`/`fallback_used`/`vector_backend`; frontend surfaces them.
- **Alternatives considered:** Hard-429 the entire chat on narrative quota (rejected: optional
  polish must not block deterministic analysis); allow mock in staging (rejected: AT-013).
- **Safety impact:** Reduces unexpected OpenAI spend in mock mode; cost control for narrative;
  no trading-mode change.
- **Consequences:** Branch `feat/at-015-provider-mode-quotas`; stop at `REVIEW_REQUIRED`.
- **Validation:** `tests/test_at015_provider_mode_quotas.py` + provider/embedding/AT-013 regressions.

## AT-ADR-009 — Proxy trust, Redis-required rate limits, fail-closed denylist (AT-018)
- **Date:** 2026-07-23
- **Status:** Accepted (implementation pending review/commit authorization)
- **Context:** AT010-H8 / RR-12 — `client_ip()` trusted the leftmost (client-supplied)
  `X-Forwarded-For` entry and uvicorn ran with `--forwarded-allow-ips="*"`, so rate-limit
  identity was spoofable. Staging allowed silent in-memory rate-limit fallback, and the
  token denylist could silently fall back to a process-local store (no cross-instance
  revocation).
- **Decision:**
  1. **Rightmost-hops proxy trust.** New `TRUSTED_PROXY_HOPS` setting (default 0). Only the
     rightmost N `X-Forwarded-For` entries — appended by our own reverse proxies — are
     trusted; entry `[-N]` is the client. 0 ignores the header entirely. Malformed or
     too-short header data falls back to the socket peer address. Staging/production
     require `>= 1` (Render sits behind exactly one proxy); local defaults to 0.
  2. **Uvicorn no longer trusts `*`.** `--forwarded-allow-ips` defaults to loopback and is
     overridable via `FORWARDED_ALLOW_IPS`; client-IP resolution happens in-app.
  3. **Redis-required rate limits outside local.** Staging/production reject
     `RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK=true` at startup. Runtime Redis errors without
     fallback keep failing closed (HTTP 429), and startup fails fast when Redis is
     unreachable.
  4. **Fail-closed denylist.** Staging/production require the denylist enabled, on Redis,
     and `ACCESS_TOKEN_DENYLIST_FAIL_CLOSED=true`. Outside local, denylist construction
     failure raises (no silent in-memory substitute), revocation writes that cannot be
     persisted raise `TokenDenylistUnavailableError` (HTTP 503), and revocation checks on
     Redis error continue to treat tokens as revoked. Local keeps developer-friendly
     fallback.
- **Alternatives considered:** CIDR allowlist for proxies (rejected: Render proxy IPs are
  not stable/published; hop count is deterministic); trusting uvicorn `--proxy-headers`
  resolution (rejected: with `*` it takes the spoofable leftmost entry); swallowing
  denylist write failures (rejected: a revoked token would silently stay valid).
- **Safety impact:** Rate-limit identity is no longer client-controlled; revocation is
  enforced or explicitly unavailable. No trading-mode change; paper posture preserved.
- **Consequences:** Branch `feat/at-018-proxy-trust-redis`; `render.yaml` staging sets
  `RATE_LIMIT_ALLOW_IN_MEMORY_FALLBACK=false`, `TRUSTED_PROXY_HOPS=1`,
  `ACCESS_TOKEN_DENYLIST_FAIL_CLOSED=true` (staging Redis must be reachable at deploy);
  stop at `REVIEW_REQUIRED`.
- **Validation:** `tests/test_rate_limit.py` (proxy trust + spoof regression),
  `tests/test_token_denylist.py`, `tests/test_deployment_safety.py` (AT-018 invariants),
  full backend suite + scoped strict mypy + ruff.

## AT-ADR-010 — Backup/restore RPO/RTO targets for paper staging (AT-019)
- **Date:** 2026-07-23
- **Status:** Accepted
- **Context:** AT010-H9 / RR-13 — backup/restore RPO/RTO was UNKNOWN; no verified restore
  drill. Postgres is the system of record; Redis is ephemeral; Qdrant is rebuildable.
- **Decision:**
  1. **Postgres RPO ≤ 24h** (stretch ≤ 1h if platform PITR enabled); **RTO ≤ 4h** for
     scratch restore + validation + cutover on staging/paper-MVP.
  2. **Qdrant RPO ≤ 24h or rebuild-from-SoR**; **RTO ≤ 4h** via snapshot or re-ingest.
  3. **Redis:** no logical backup; **RTO ≤ 15m** recreate empty instance.
  4. Local Compose drills are the default verification path; managed/staging restores
     require explicit human approval and prefer scratch DB over in-place overwrite.
  5. Evidence in git must be sanitized (sizes, hashes, durations, pass/fail only).
  6. AT-005 (deploy rollback + smoke gate) remains a separate concern — not duplicated.
- **Alternatives considered:** Require staging restore before closing AT-019 (deferred:
  approval-gated); treat Redis as SoR (rejected: intentionally ephemeral).
- **Safety impact:** Improves recovery preparedness; no trading-mode change; no live
  execution; scripts refuse non-local targets.
- **Consequences:** Runbook + inventory + drill docs under `docs/`; local helpers under
  `scripts/*postgres-local*` / `drill-backup-restore-local.sh`; dumps in `.ai/local/`.
- **Validation:** Tier A local drill passed 2026-07-23; see
  `docs/backup_restore_drill_evidence.md`.

## AT-ADR-011 — Post-deploy smoke gate + deploy rollback procedure (AT-005)
- **Date:** 2026-07-24
- **Status:** Accepted (merged PR #15 → `main` @ `f145599`)
- **Context:** Rollback was informal checklist rows; no automated fail-closed post-deploy
  gate. AT-019 covers data restore; app revision rollback and smoke gating were still open.
- **Decision:**
  1. Mandatory post-deploy command is `scripts/post-deploy-smoke-gate.sh`, which always
     runs `verify-safety.sh` and (default `GATE_PROFILE=standard`) `staging-smoke.sh`.
  2. Gate exit codes: `0` pass, `1` rollback trigger, `2` misconfiguration.
  3. Document exact triggers/steps/verification/failure handling in
     `docs/deploy_rollback_runbook.md`; wire into staging checklist/runbook/`RELEASE.md`.
  4. CI `deployment-safety` job asserts the gate is executable and `--self-check` passes
     (no network / no staging deploy from CI).
  5. The gate never deploys, never enables real trading, and never mutates platform services.
- **Alternatives considered:** Rely on manual `verify-safety` only (rejected: easy to skip);
  auto-rollback via Render API from CI (rejected: requires credentials + deploy authority
  outside ordinary impl tasks).
- **Safety impact:** Stronger fail-closed deploy acceptance; paper-only invariants unchanged.
- **Consequences:** Operators must treat gate exit `1` as a hard rollback trigger; data
  restore remains AT-019.
- **Validation:** `post-deploy-smoke-gate.sh --self-check`; unit tests in
  `tests/test_deployment_scripts.py`; docs present and cross-linked.

## AT-ADR-012 — Canonical journal trade domain links existing records (AT-030)
- **Date:** 2026-07-24
- **Status:** Accepted (merged via PR #16)
- **Context:** Trade data is fragmented across proposal-flow positions, paper-validation
  trades, backtest trades, and manual session records; the legacy `journals` table is
  reflection-only and typed to the built-in `StrategyId` enum. There was no canonical
  trade identity, no first-class MFE/MAE or available-vs-realized profit, and no
  structured rule-compliance or behavioral-observation records.
- **Decision:**
  1. **One canonical entity, `journal_trades`**, tenant-scoped, covering all sources
     (`manual`, `paper_execution`, `paper_validation`, `backtest`, `imported`, `system`)
     with plan fields (thesis, trigger, entry plan, invalidation, stop, targets, runner),
     execution fields (entry/exit, size, leverage, fees, funding, slippage, PnL), market
     regime, and excursion metrics (MFE/MAE, available vs realized).
  2. **Link, never copy.** FKs to positions, paper trades, proposals, orders, backtest
     trades, paper validation runs, and legacy journal entries; setup/strategy provenance
     via existing immutable `SetupDefinition` (name+version) and `UserStrategyVersion`.
     All links validated against the caller's organization; mismatches return 404
     (fail closed, no existence leak).
  3. **Child tables** for evidence (`journal_trade_evidence`), rule compliance
     (`journal_trade_rule_checks`), and behavioral observations
     (`journal_trade_observations`) instead of free-text lists.
  4. **Record-only.** No execution authority: never read by the engine, scheduler, or
     risk gates; excursion metrics accept deterministic inputs only (manual now, candle
     replay later) — no live market I/O in the journal path.
  5. **Legacy `/journal/entries` API and RAG sync stay unchanged**; canonical trades
     mount on the same router under `/journal/trades`.
- **Alternatives considered:** Extending `TradeJournal` in place (rejected: schema is
  reflection-shaped, enum-typed to legacy strategies, and widely consumed); a separate
  standalone journal service/app (rejected: would disconnect from tenancy, audit, RBAC,
  and existing records); computing MFE/MAE live at read time (rejected: nondeterministic,
  provider-dependent, violates freshness/conservatism rules).
- **Safety impact:** No trading-mode change; paper posture preserved; all mutations
  audited (`JOURNAL_TRADE_*` events); no new secrets or providers.
- **Consequences:** Alembic head moves to `i5d6e7f8a9b0`; follow-up slices (statistics,
  replay, human-vs-system endpoint, backtest integration, import/backfill) build on this
  domain — see `docs/journal_intelligence_foundation.md`.
- **Validation:** Migration upgrade/downgrade/upgrade on Postgres 16; 13 new API tests;
  full backend suite exit 0; ruff clean; strict mypy clean on all new modules.

## AT-ADR-013 — Journal statistics: deterministic aggregates over recorded values (AT-031)
- **Date:** 2026-07-24
- **Status:** Accepted (merged via PR #17)
- **Context:** AT-030 established canonical `journal_trades` but there were no statistics
  over it. Statistics must be trustworthy on small, partially populated samples: paper
  tenants have few trades, and MFE/MAE, planned risk, fees, and available-profit are only
  sometimes recorded.
- **Decision:**
  1. **Extend the AT-030 journal architecture** — statistics queries live on
     `JournalTradeRepository`, computation in a dedicated `JournalStatisticsService`,
     one authorized endpoint `GET /journal/statistics` on the journal router. No separate
     analytics system, no rollup tables in this slice.
  2. **Closed trades only; recorded values only.** SQL selects a bounded narrow
     projection (`journal_stats_max_rows`, default 5000, stable oldest-first ordering
     with truncation flagging); metric arithmetic runs in Python with `Decimal` for
     deterministic, dialect-independent precision (SQLite tests ≙ Postgres prod).
  3. **Per-family sample counts + confidence.** Every metric family (PnL, R, costs,
     MFE/MAE, available-vs-realized) aggregates only trades that recorded those values
     and reports its own sample count; `None` is never silently reported as zero.
     Coarse confidence labels (<5 insufficient, <20 low, <50 moderate, ≥50 high) and
     machine-readable warnings accompany every result.
  4. **Derived dimensions are conservative.** Rule compliance per trade = worst recorded
     assessment (`violated` > `partial` > `compliant`; no checks ⇒ `unassessed`, never
     compliant). Human-vs-system = decision authority mapping from `source`
     (`manual`/`imported`/`paper_execution` ⇒ human; `paper_validation`/`backtest`/
     `system` ⇒ system).
  5. **Win/loss classification**: recorded `result` wins; closed trades left at
     `result=open` fall back to the recorded `net_pnl` sign (the same arithmetic AT-030
     applies at close); win rate = wins / (wins + losses), breakeven excluded.
- **Alternatives considered:** Pure SQL conditional aggregation (rejected: float
  arithmetic on SQLite diverges from Postgres `numeric`, and per-family sample logic
  becomes unreadable); extending `UnifiedTradeLoader` (rejected for this slice: it loads
  positions/paper trades, not canonical journal trades — journal statistics must read the
  canonical table so all sources are covered uniformly); precomputed rollup tables
  (rejected: premature — bounded on-demand scans suffice at current volumes and cannot
  drift from source data).
- **Safety impact:** Read-only endpoint (`ReaderDep`), tenant-scoped (org + user); no
  execution-path changes; no live market I/O; reads not audited, mutations remain audited
  via AT-030; paper posture unchanged.
- **Consequences:** Alembic head moves to `j6e7f8a9b0c1` (index-only migration). Replay
  slice (deterministic MFE/MAE from candles) will raise excursion coverage; backtest
  integration reuses these grouping dimensions.
- **Validation:** Migration upgrade/downgrade/upgrade on Postgres 16 scratch DB; 19 new
  API tests; ruff clean; strict mypy clean on new modules; frontend tsc + eslint + page
  test green.

## AT-ADR-014 — Journal excursion replay from HistoricalCandle (AT-032)
- **Date:** 2026-07-24
- **Status:** Accepted (merged via PR #18)
- **Context:** AT-030 stored excursion columns but values were only manual; AT-031
  aggregates recorded MFE/MAE / available-profit with per-family sample counts, so
  coverage stays low until a deterministic fill path exists. Live market fetches at
  read time were rejected in AT-ADR-012.
- **Decision:**
  1. **Pure calculator + audited replay service.** In-trade MFE/MAE / available profit
     are computed from stored `HistoricalCandle` OHLC overlapping `[entry, exit)`
     (exit exclusive). Long and short use mirrored extremes. Amounts require size;
     capture percent reuses AT-030 arithmetic.
  2. **Read-only market data.** Replay never ingests or calls providers; missing
     candles / invalid windows skip safely with limitations. Gaps and incomplete
     coverage set freshness flags (`excursion_is_stale`, notes) without inventing bars.
  3. **Overwrite policy is explicit and deterministic.** Default `skip_protected`
     writes only when `excursion_source` is empty or already `replay`. `manual` and
     `system` (and any other non-replay source) require `overwrite_policy=force`.
  4. **Provenance columns** on `journal_trades` record data source, staleness, gap
     count, window completeness, and `excursion_computed_at`. Persisted source is
     always `replay`. Mutations audit as `JOURNAL_TRADE_EXCURSION_REPLAYED`.
  5. **Post-exit runner analysis** reuses `RunnerAndMissedProfitAnalyzer` in the
     response only — it does not redefine in-trade `available_profit`.
  6. **AT-031 integration** is automatic: statistics read recorded amounts; replay
     raises sample coverage without changing aggregate semantics.
- **Alternatives considered:** Computing MFE/MAE at statistics read time (rejected:
  nondeterministic if candles change; couples reads to market store); always
  overwriting manual values (rejected: human-entered coaching data must stay
  protected); provider fetch during replay (rejected: violates record-only /
  freshness rules for this slice).
- **Safety impact:** Record-only; `TraderDep` mutations; tenant-scoped; bounded
  candle/batch limits; paper posture unchanged; no live trading.
- **Consequences:** Alembic head moves to `k7f8a9b0c1d2`. Remaining journal slices:
  completion/import, human-vs-system endpoint, backtest bulk journal + full
  deterministic backtesting coverage.
- **Validation:** Migration upgrade/downgrade/upgrade on Postgres 16 scratch DB; 17 new
  API tests; ruff clean; strict mypy clean on new modules; CI run 30105918952 success
  (backend 1259 passed / 1 skipped).

## AT-ADR-015 — Journal completion: import dedup, backfill, auto-journal, attachments (AT-033)
- **Date:** 2026-07-24
- **Status:** Accepted (merged PR #19, merge `ad66dca`, CI run 30114440476 success)
- **Context:** AT-030/031/032 built the canonical journal, statistics, and excursion
  replay, but records only entered via manual API calls. The completion slice needs
  bulk history import, legacy `TradeJournal` migration, automatic journaling on paper
  closes, and a storage answer for screenshots/evidence — with idempotency, tenant
  isolation, audit, and human-vs-system analytics preserved.
- **Decision:**
  1. **DB-enforced dedup via partial unique index.** `(organization_id, external_ref)
     WHERE external_ref IS NOT NULL` on `journal_trades`. App-level checks give
     friendly per-row `duplicate` outcomes; the index is the race-proof backstop.
     Import rows without a ref get a deterministic `fp-sha256:` fingerprint over
     normalized identity fields, so re-imports are idempotent either way.
  2. **All-or-nothing import commit as the recovery model.** `mode=dry_run` previews
     per-row outcomes; `mode=commit` persists nothing when any row is invalid
     (single unit of work). Recovery is "fix and re-run" — duplicates skip safely.
     Committed batches persist to `journal_import_batches` for reconciliation
     history and audit (`JOURNAL_IMPORT_COMPLETED`).
  3. **`entry_method` column for human-vs-system analytics.**
     `manual|auto|import|backfill`, orthogonal to `source` (a human from-position
     record and an auto-hook record share `source=paper_execution` but differ in
     entry_method). First-class AT-031 filter + `group_by` dimension.
  4. **Backfill is a CLI script, dry-run by default.**
     `scripts/backfill_journal_entries.py` maps legacy rows to
     `source=imported`/`entry_method=backfill` with
     `external_ref='legacy-journal:<id>'` + `linked_journal_entry_id`; legacy rows
     are never mutated (link-never-copy, AT-ADR-012 upheld).
  5. **Auto-journal hooks are opt-in global Settings flags, fail-safe for the
     close.** `journal_auto_from_position_close` /
     `journal_auto_from_paper_validation`, both default false. Hooks reuse the
     idempotent `create_from_*` prefills inside a savepoint and swallow all errors
     after a warning log — journaling can never block or roll back a close.
     Paper-validation records attribute to the run owner.
  6. **Attachments are DB-backed behind an interface.** Bytes live in
     `journal_trade_attachments.content` (Postgres) with strict caps (5 MiB, MIME
     whitelist, 20/trade) because no durable object store exists and Render disks
     are ephemeral; existing DB backups cover them. `AttachmentStorage` keeps an
     S3-style swap schema-free. Uploads auto-link `JournalTradeEvidence`
     (`ref='attachment:<id>'`).
- **Alternatives considered:** Per-row partial import commits (rejected: ambiguous
  recovery semantics; idempotent re-run is simpler); per-user auto-journal preference
  (rejected for now: no preferences model exists — would be scope creep; global flags
  documented as deferred work); local-filesystem or S3 attachment storage (rejected:
  ephemeral Render disk loses data / no object store provisioned; interface keeps the
  door open); making `external_ref` globally unique across sources only for
  `imported` (rejected: org-wide partial index gives idempotency to backfill and
  future integrations too).
- **Safety impact:** Record-only throughout; no execution-path change; new flags
  default off; mutations `TraderDep`, reads `ReaderDep`; org-scoped fail-closed
  lookups; attachment validation fail-closed; paper posture unchanged; no live
  trading.
- **Consequences:** Alembic head moves to `l8a9b0c1d2e3`. Deferred: attachment upload
  UI (needs a trades detail page), per-user auto-journal opt-in, persisting
  failed/dry-run batches (enum values reserved).
- **Validation:** Migration upgrade/downgrade/upgrade round-trip on disposable
  Postgres 16 (docker); partial unique index verified on Postgres (duplicate
  rejected, NULLs unconstrained); 44 new backend tests (import 14, backfill 6,
  attachments 12, auto-journal 9, integration 3) plus AT-030/031/032 regression
  green; frontend 267 tests + typecheck + build green; ruff clean. No deploy.

## AT-ADR-016 — Deterministic backtesting v1: snapshot+hash reproducibility, conservative intra-bar rule, evidence tiers, bounded orchestration without new queue infra (AT-034)
- **Date:** 2026-07-24
- **Status:** Accepted (merged via PRs #20–#23 → `main` @ `8f9a84b`)
- **Context:** AT-030–033 established canonical journal trades, statistics, excursion
  replay, and bulk import. Slice 35 introduced a simpler backtest engine. AT-034
  needs reproducible historical simulation that journals into the canonical trade
  store, supports walk-forward evaluation, and surfaces advisory evidence — without
  new async infrastructure, live trading paths, or parameter-optimization loops.
- **Decision:**
  1. **Frozen config + dataset snapshots.** On create, persist `config_snapshot`,
     `config_hash`, and link an immutable `backtest_datasets` row by `dataset_hash`.
     Candles are hash-referenced, not copied per run.
  2. **Pure engine + `result_hash`.** `BacktestEngineService` (v2,
     `ENGINE_VERSION=at034-2.0.0`) is deterministic; `result_hash` is canonical JSON
     SHA-256. `POST /backtests/{id}/verify` re-runs with `persist=False` and
     compares hashes after dataset integrity check.
  3. **Conservative intra-bar rule.** When stop and TP both touch in one bar, stop
     wins.
  4. **Walk-forward v1 only.** Holdout and rolling splits evaluate independent
     segments with per-split and OOS metrics — no parameter optimization across
     windows.
  5. **Bounded orchestration.** Sync path when `total_bars <= backtest_sync_max_bars`;
     otherwise `QUEUED` with existing worker loop (1 run/cycle) plus BackgroundTasks
     fallback when worker disabled. Refuse `total_bars > backtest_max_bars` (no
     truncation). Cancel every 2000 bars; idempotency keys; active-run cap per org.
  6. **Bulk journal + comparison + advisory tiers.** `POST /backtests/{id}/journal-trades`
     creates `source=backtest` rows with dedup `external_ref`. `GET /journal/comparison`
     exposes human/paper_system/backtest cohorts. `GET /journal/setup-evidence` assigns
     tier1/tier2/tier3 from configurable OOS and confirmation thresholds — advisory
     only, never execution authority.
- **Alternatives considered:** Celery/dedicated queue (rejected: existing worker +
  BackgroundTasks suffice for bounded workloads); storing full candle copies per run
  (rejected: immutable hash-referenced datasets); parameter-optimizing walk-forward
  (rejected for v1: windowed evaluation only); using backtest tiers to gate live
  trading (rejected: record-only, risk engine untouched).
- **Safety impact:** Record-only throughout; paper posture unchanged; advisory tiers
  never feed execution or risk; mutations `TraderDep`; tenant-scoped; no live orders.
- **Consequences:** Alembic head moves to `m9b0c1d2e3f4`. Docs rewritten in
  `docs/backtesting.md`. Integration tests in `test_at034_integration.py`.
- **Validation:** Merged PRs #20–#23 (CI runs 30126077064, 30126107532,
  30130869273, 30130870524 all success). Post-merge on `main` @ `8f9a84b`:
  AT-034 + slice-35 pytest 43 passed; frontend page tests 7 passed; ruff clean.
  No deploy.

## AT-ADR-017 — Research validation loop: extend candidate provenance + synthetic alert/draft vs parallel queue (AT-035)
- **Date:** 2026-07-25
- **Status:** Accepted
- **Context:** AT-034 delivers deterministic backtests, OOS metrics, and advisory
  setup evidence tiers. Slice 80 established the paper validation candidate queue
  (alert → draft → candidate). Users need a paper-safe path to promote strong
  backtest evidence into that queue without bypassing FK constraints or creating a
  parallel promotion system.
- **Decision:**
  1. **Reuse existing candidate queue.** Promotion enters `paper_validation_candidates`
     via the same queue model — no separate research queue or execution shortcut.
  2. **Synthetic research-origin alert + draft.** Create
     `PaperAlertType.RESEARCH_VALIDATION_PROMOTION` alert and a `ready_for_validation`
     draft to satisfy non-null `source_alert_id` / `draft_id` FKs; mark
     `promotion_source=research_validation`.
  3. **Eligibility via `SetupEvidenceService`.** Tier1/tier2 eligible; tier3 hard
     blocked. Missing OOS metrics or incomplete runs blocked. Soft warning
     `insufficient_confirm_sample` when confirm trades below tier1 threshold.
  4. **Frozen provenance on candidate.** Persist `backtest_run_id`, strategy/version
     FKs, dataset/config/result hashes, `evidence_tier`, and `evidence_snapshot`
     JSON at promotion time. Legacy alert-draft rows keep nullable provenance.
  5. **Idempotency.** Partial unique index on
     `(organization_id, backtest_run_id)` for active (`queued`/`reviewing`) candidates.
  6. **Confirm phrase.** `PROMOTE_RESEARCH_VALIDATION_CANDIDATE` required on POST.
  7. **Paper-only.** Advisory endpoints; never feed execution or risk; tenant-scoped;
     `ReaderDep` for evidence/status, `TraderDep` for promote.
- **Alternatives considered:** Parallel research-only queue (rejected: duplicates
  review UX and splits paper validation); nullable alert/draft FKs (rejected: breaks
  existing schema invariants); auto-start paper runtime on promote (rejected: exceeds
  advisory scope and weakens human review).
- **Safety impact:** Record-only; paper posture unchanged; no risk/execution module
  changes; promotion does not authorize live trading.
- **Consequences:** Alembic head moves to `n0c1d2e3f4a5`. Docs in
  `docs/research_validation.md`. Tests in `test_at035_research_validation.py`.
- **Validation:** Disposable Postgres 16 migration upgrade/downgrade/upgrade cycle;
  partial unique index verified; duplicate active promotion blocked at DB layer;
  targeted pytest + ruff + frontend research-validation tests; CI on merge PR.

## AT-ADR-018 — Aggregate journal comparison decision quality vs per-trade HumanVsSystemService (AT-036)
- **Date:** 2026-07-25
- **Status:** Accepted
- **Context:** AT-034 delivers three-cohort journal comparison (`human`,
  `paper_system`, `backtest`) over closed canonical trades. Users need aggregate
  decision-quality metrics (entry timing, early exits, missed profit, capture) and
  human-vs-system actor scorecards without invoking per-trade
  `HumanVsSystemService` orchestration on every list request. Slice 36
  `/human-vs-system/{id}` remains the per-trade analyzer surface.
- **Decision:**
  1. **Extend existing endpoint.** Add AT-036 fields to `GET /journal/comparison`
     — backward compatible; preserve AT-034 `cohorts` with three keys.
  2. **Recorded fields only.** Decision quality computed from journal columns
     (`planned_entry_price`, `entry_price`, `direction`, `available_profit`,
     `net_pnl`, `realized_vs_available_pct`) — no live market I/O, no proposal
     linkage required for aggregates.
  3. **Actor scorecards.** `human` = manual + imported + paper_execution;
     `system` = paper_validation + backtest + system (decision-authority mapping
     from AT-031).
  4. **Dimension buckets.** `by_entry_method`, `by_source`, `rule_compliance`
     (worst-assessment), plus capped setup/regime `breakdowns`.
  5. **Warnings.** Reuse AT-031 confidence thresholds; add `PARTIAL_TIMING_DATA`
     and `PARTIAL_MISSED_PROFIT_DATA` when subsamples are partial.
  6. **Frontend paths in `links`.** Echo filters for journal statistics and
     comparison; link to research-validation and paper-validation candidates.
  7. **Paper-only / ReaderDep.** Advisory record-only; never feeds execution or
     risk; tenant-scoped like AT-034.
- **Alternatives considered:** New dedicated endpoint (rejected: fragments
  comparison UX and duplicates filters); invoke `HumanVsSystemService` per trade in
  list (rejected: heavy, needs proposal links, exceeds aggregate scope); replace
  Slice 36 per-trade API (rejected: different granularity and analyzer depth).
- **Safety impact:** Record-only; paper posture unchanged; no risk/execution
  module changes.
- **Consequences:** No migration required (reuses AT-031/034 indexes and journal
  columns). Docs in `docs/journal_intelligence_foundation.md` §7,
  `docs/human_vs_system.md`, `docs/backtesting.md`. Tests in
  `test_at036_journal_comparison.py` and frontend comparison page tests.
- **Validation:** Targeted backend + frontend tests on feature branch; no deploy.

## AT-ADR-019 — TradingView signed intake + BloFin demo read-only sync (AT-037)
- **Date:** 2026-07-25
- **Status:** Accepted
- **Context:** TradingView alerts need a secure multi-tenant intake path into the
  paper-first workflow, and operators need BloFin **demo** account/position
  visibility without any order mutation. Existing paper-validation candidates
  require non-null alert/draft FKs (AT-035 synthetic scaffold pattern). Billing
  webhooks prove HMAC+timestamp+idempotency patterns but lack tenant signal
  lifecycle and public rate limits.
- **Decision:**
  1. **Signed public webhook.** `POST /webhooks/tradingview` with
     `X-AT-Timestamp` + `X-AT-Signature` (HMAC-SHA256 over `{timestamp}.{body}`),
     skew window, fail-closed when disabled/secret missing, public IP rate limit.
  2. **Dedicated `tradingview_signals` table.** Org-scoped lifecycle
     (`received`/`validated`/`rejected`/`duplicate`/`candidate_created`),
     unique `(org, idempotency_key)` and `(org, alert_id)`, redacted payload
     storage, optional links to setup/strategy/journal/backtest/candidate.
  3. **Optional paper candidate only.** Explicit trader confirm
     (`CREATE_TRADINGVIEW_PAPER_CANDIDATE`) creates synthetic alert+draft+queued
     candidate with `promotion_source=tradingview_signal`. Auto-create off by
     default. Never creates live orders or executable proposals.
  4. **BloFin sync is read-only demo.** Persist bounded snapshots via
     `get_demo_account_provider` only; provenance + health + stale marking;
     no execution provider calls; secrets never logged.
  5. **Frontend.** Signal inbox + exchange BloFin sync panel with loading/empty/
     stale/error states.
- **Alternatives considered:** Reuse billing `webhook_events` only (rejected:
  no signal lifecycle/links); make alert FK nullable on candidates (rejected:
  breaks Slice 80 invariants — use synthetic scaffold); auto-queue every alert
  (rejected: opt-in confirm / setting); live BloFin sync (rejected: Mode D /
  safety).
- **Safety impact:** Paper/demo only; real trading stays disabled; no order
  placement/cancel/modify; rate limits/denylist/kill-switch unchanged.
- **Consequences:** Migration `o1d2e3f4a5b6`; docs in
  `docs/tradingview_blofin_sync.md`; tests in `test_at037_tradingview_blofin.py`
  + frontend page/panel tests.
- **Validation:** Targeted backend + frontend tests on feature branch; no deploy.

## AT-ADR-020 — Automated paper-signal orchestration (AT-038)
- **Date:** 2026-07-25
- **Status:** Accepted
- **Context:** AT-037 delivers signed TradingView intake and optional manual
  paper-candidate creation. Operators need a deterministic, reviewable bridge from
  validated signals into paper-validation candidates/run plans and (optionally)
  approval-gated paper proposals — without any live or autonomous execution path.
- **Decision:**
  1. **Dedicated decision table.** `paper_signal_orchestration_decisions` with
     unique `(org, tradingview_signal_id)` idempotency, status state machine,
     eligibility/risk evidence JSON, transition history, and links to signal /
     setup / strategy / journal / backtest / candidate / run plan / proposal.
  2. **Configurable human modes only.** `observe_only` | `candidate_only` |
     `approval_required`. No autonomous live mode. Default disabled + observe_only.
  3. **Reuse existing paper pathways.** Candidates via AT-037
     `_create_candidate_internal`; run plans via Slice 81 confirm pathway;
     paper proposals via `ProposalService.create(approval_required=True)`.
     Never call `place_paper_order` or exchange mutation APIs.
  4. **Fail-closed gates.** Freshness, timeframe, direction, confidence, setup/
     strategy linkage when configured, level consistency, conflicting-signal
     window, market-context (when BloFin demo enabled), kill switch, daily-loss
     lock, cooldown after loss, `execution_mode=paper`, `enable_real_trading=false`.
  5. **Explicit proposal confirm.** `APPROVE_PAPER_SIGNAL_PROPOSAL` required in
     `approval_required` mode; re-validate eligibility before proposal create.
- **Alternatives considered:** Auto-place paper orders from signals (rejected:
  bypasses approval/risk); extend TradingView signal status instead of a decision
  table (rejected: conflates intake lifecycle with orchestration); live mode
  (rejected: Mode D / safety).
- **Safety impact:** Paper-only; real trading stays disabled; kill switch /
  cooldown / daily-loss / approval controls remain authoritative.
- **Consequences:** Migration `p2e3f4a5b6c7`; docs in
  `docs/paper_signal_orchestration.md`; tests in
  `test_at038_paper_signal_orchestration.py` + frontend page tests.
- **Validation:** Targeted backend + frontend tests on feature branch; no deploy.

## AT-ADR-021 — Phase 5 Binance USD-M perpetual evidence contracts (no spot fallback)
- **Date:** 2026-09-16
- **Status:** Accepted (implemented on PR #80, draft; do not merge pending independent review)
- **Context:** The first BTCUSDT vertical slice requires closed perpetual 15m/4h OHLCV,
  ordered USD-M aggregate trades, quote-volume CVD, and signed quote flow. The existing
  `binance-public` adapter uses spot `/api/v3/klines` and may silently fall back to mock
  data. Architecture §17/§25 forbid forming-candle confirmation, spot substitution, and
  unresolved reconnect gaps.
- **Decision:**
  1. **Separate perpetual evidence package** (`app.market_contracts`) with typed venue,
     market, instrument, source, provider, interval, finality, revision, timestamps,
     freshness, content hash, and provenance. Phase 1 execution modules are not modified.
  2. **Preferred source is Binance USD-M public REST** (`fapi.binance.com` klines +
     aggTrades). GET-only path allowlist. No API keys. No order/position/account paths.
  3. **No spot fallback and no mock substitute** for live USD-M evidence. Regional
     unavailability fails closed (`RegionalProviderFailureError`) and reports
     `using_fallback=false`.
  4. **FINAL candles only.** Forming klines are represented but cannot enter a
     `ClosedOhlcvSeries`. First slice requires 100 final 15m and 30 final 4h bars.
  5. **Trade stream cursor** is `INITIAL -> CONTINUOUS -> RECONNECTING -> RECOVERED`.
     Every reconnect starts a new epoch. Unresolved sequence gaps are
     `UNRECOVERABLE` and fail closed. Cross-connection CVD windows are not supported.
  6. **CVD** is unrounded Decimal quote volume: buyer aggressor `+price*qty*multiplier`,
     seller aggressor negative. Baseline is 0 at the open of the 32nd 15m bar before
     trigger T. Signed flow is `signed_quote_delta / total_quote_volume` on the trigger
     bar. Binance aggressor convention is `m=true` ⇒ seller aggressor (`buyer-is-maker/v1`).
  7. **Default runtime source is replay fixtures** (`PERPETUAL_EVIDENCE_SOURCE=replay`)
     so local/CI never require Binance reachability. Replay is explicitly mock, not a
     silent live fallback.
  8. **AggTrades retrieval** chunks `startTime`/`endTime` to < 1 hour and paginates
     further pages with `fromId` only. Mixed time+fromId queries are forbidden.
     Incomplete pages or sequence holes fail closed.
  9. **CVD completeness** is derived from a `TradeStreamSnapshot` plus immutable
     `TradeWindowCoverageProof`. The proof binds exact market/source identity,
     lineage, requested/actual half-open bounds, gap/completeness state, terminal
     trade identities, ordered trade-set hash, and content hash. It must cover the
     entire T-32 through trigger-end window. Count and internal continuity alone
     are insufficient. First-slice action eligibility also requires terminal trade
     freshness ≤10s.
  10. **Live evidence hosts** are explicit approved USD-M HTTPS identities
      (`fapi.binance.com`). Arbitrary hosts and `http://` are rejected.
  11. **End-to-end identity** requires every normalized trade to exactly match the
      stream market/instrument and represented source fields. CVD/flow identity must
      exactly equal the authoritative snapshot identity.
  12. **Signed quote flow** consumes the same proven stream as CVD and requires
      complete trigger-bar coverage, no gap, matching lineage/aggressor convention,
      and fresh terminal evidence. Raw trade lists are not accepted.
- **Alternatives considered:** Relabel the existing spot kline adapter as perpetual
  (rejected: incompatible market); fall back to spot or mock when USD-M is blocked
  (rejected: false evidence); persist observations in this phase (rejected: no migration
  unless unavoidable; later phases own candidate/observation storage).
- **Safety impact:** Read-only market evidence only; no execution path change;
  `ENABLE_REAL_TRADING=false` and `EXECUTION_MODE=paper` unchanged.
- **Consequences:** Docs in `docs/market_source_contracts.md`; tests in
  `test_phase5_*.py`. Pattern/fusion/watcher remain out of scope.
- **Validation:** Targeted Phase 5 pytest, full backend pytest, ruff, scoped mypy.

## AT-ADR-022 — Isolated watcher orchestration foundation (typed persistence)
- **Date:** 2026-09-16
- **Status:** Accepted
- **Context:** Phase 7 watcher work is split across parallel agents. This agent
  owns worker orchestration (leases, fencing, lineage, retry, health, scheduling)
  without market semantics, candidates, Telegram, execution, journal, or ORM
  migrations. Agent 1 later supplies source freshness and evidence validity.
- **Decision:**
  1. New isolated package `app.watcher` with typed persistence ports and a
     deterministic in-memory repository for this wave.
  2. Manual and worker callers share one `WatcherOrchestrator.evaluate` boundary
     (`PREVIEW | PERSIST_EVIDENCE | PERSIST_AND_NOTIFY`). Notify stays blocked.
  3. One fenced worker owns a tenant-scoped scan key `(organization_id, scan_scope)`;
     stale fence holders cannot publish. `scan_scope` strings are not a tenant boundary.
  4. Every scan has an immutable lineage; attempts are append-only; retries are
     idempotent; failures cannot be rewritten as successes.
  5. Health is exclusive `healthy | degraded | blocked | stale`.
  6. `WATCHER_ORCHESTRATION_ENABLED` defaults false and is not wired into the
     live worker loop. No shared ORM model or Alembic changes.
  7. WatcherStore methods take `organization_id` explicitly and reject organization
     mismatch. Lease, fence, heartbeat, health, latest-attempt-by-scope, and
     lineage-by-scope are keyed by `(organization_id, scan_scope)`.
- **Alternatives considered:** Extend `MarketWatcherService` / SQLAlchemy models
  now (rejected: collides with parallel agents and premature PostgreSQL binding);
  process-local locks only (rejected: architecture requires monotonic lease
  epochs on every worker-caused write).
- **Safety impact:** Watcher stays disabled; no automatic trading or candidate
  creation; real trading remains disabled.
- **Consequences:** Later integration review binds the ports to PostgreSQL after
  all three parallel PRs land.
- **Validation:** `backend/tests/test_watcher_orchestration_foundation.py`.

## AT-ADR-023 — Isolated Telegram security protocol; APPROVE never executes (AT-043)
- **Date:** 2026-09-16
- **Status:** Accepted
- **Context:** Agentic redesign §10 requires verified private-chat enrollment,
  opaque action nonces, receipts, replay protection, and a RemoteActionGateway
  that is not a second mutation stack. Agent 3 owns the isolated security
  foundation only. Telegram must remain disabled and must not connect to
  execution. CLOSE stays unavailable. EXECUTE_PAPER_PLAN is out of scope.
- **Decision:**
  1. **Isolated package.** `app.telegram_security` holds enrollment, binding,
     message/callback identity, nonce, receipt, rate-limit, outbox, delivery
     acknowledgement, transport, and the action authorization boundary.
  2. **Persistence interfaces only.** `TelegramSecurityStore` + deterministic
     `InMemoryTelegramSecurityStore`. No shared ORM models, no Alembic.
  3. **Disabled by default.** `telegram_interaction_enabled=false`. No FastAPI
     webhook. Fake transport for tests; no live Telegram API calls.
  4. **Private chat only.** Group/channel chats and chat-id-only enrollment fail.
  5. **Opaque one-time nonces** bind org, user, account, Telegram identity, resource,
     revision/content hash, one action, expiry, and single-use state.
  6. **APPROVE** records a protocol-level authorization intent (`executes=false`).
     It never calls ExecutionService. EXECUTE_PAPER_PLAN is not in the Telegram
     vocabulary.
  7. **CLOSE** is a known name but unavailable (issue and apply both fail).
  8. Duplicate `update_id` / `callback_query_id` / outbox idempotency keys
     converge only when the inbound semantic fingerprint is identical. Same
     transport lookup identity with changed update/message/callback identity,
     Telegram user, chat, chat type, bot, nonce or enrollment-token hash,
     action, organization, AlphaTrade user, account, resource, revision,
     content hash, or payload hash fails closed as `REPLAY_CONFLICT`.
  9. Inbound type and size validation precede receipt lookup. Exact persisted
     replay convergence and conflicting replay rejection precede rate-limit
     charging; only genuinely new semantic inbound actions consume budget.
  10. `MAX_INBOUND_UPDATE_BYTES` is enforced against
     `TelegramInboundUpdate.body_size` (raw Telegram request payload), never
     nonce or enrollment-token length. Webhook wiring remains out of scope.
  11. Delivery is at-least-once via durable claim.
- **Alternatives considered:** Wire inbound webhook now (rejected: later
  integration); persist via Alembic in this slice (rejected: later PostgreSQL
  binding); allow APPROVE to call execution (rejected: CRITICAL-03).
- **Safety impact:** Telegram stays off. Live trading stays disabled. Approval
  cannot execute. CLOSE cannot close.
- **Consequences:** Docs in `docs/telegram_security_protocol.md`. Tests in
  `backend/tests/test_telegram_security_protocol.py`.
- **Validation:** Targeted protocol tests, full backend pytest, ruff, mypy on
  `app.telegram_security`, GitHub CI. No merge in the implementing PR's agent
  instructions.

## AT-ADR-024 — Phase 6 canonical signal-fusion contract freeze
- **Date:** 2026-09-17
- **Status:** Accepted (contracts only; evaluator/persistence not in this slice)
- **Context:** Parallel Phase 6 implementation needs one typed identity and hash
  layer so observations, fusion, assessment, candidates, eligibility, adapters,
  and PostgreSQL persistence cannot invent competing schemas.
- **Decision:**
  1. Canonical package is `app.signal_fusion`. Phase 5
     `PublicMarketObservation` and market identities are reused, not duplicated.
  2. Freeze immutable contracts: `FusionPolicy`, `SetupAssessment`,
     `ActionEligibility`, `Candidate`, `CandidateTransition`, and
     `CanonicalEvidenceWindowV1`.
  3. Setup truth lifecycle is `NO_SETUP -> WATCH -> PARTIAL_MATCH ->
     CONFIRMED_SETUP` or market-only `INVALIDATED`/`EXPIRED`. Risk/account
     state cannot occupy `SetupAssessment`.
  4. Confirmed candidates start `ACTIVE`. Terminal `REJECTED`/`SKIPPED`/
     `EXPIRED`/`INVALIDATED` cannot resurrect to `ACTIVE`.
  5. Candidate uniqueness is the §5 tuple; `setup_definition_id` is a
     tenant-owned `CompiledSetupDefinition` only.
  6. `CanonicalEvidenceWindowV1` hashes the §26 semantic preimage with
     canonical Decimal/datetime serialization and deterministic ordering.
     Transport metadata and presentation evidence are excluded.
  7. This slice does not implement the fusion evaluator, candidate repository,
     Alembic, watcher/Telegram/TradingView adapters, risk evaluation, alerts,
     outbox, execution, or frontend.
- **Alternatives considered:** New observation identity in fusion (rejected:
  Phase 5 already owns public observations); persist candidates now (rejected:
  contract freeze only); let adapters keep source-specific candidate keys
  (rejected: equivalent watcher/detector/TradingView evidence must converge).
- **Safety impact:** Paper mode unchanged. No network, execution, watcher,
  Telegram, or BloFin calls.
- **Consequences:** Later Phase 6 agents implement evaluator, persistence, and
  adapters against this package.
- **Hardening (2026-09-17, PR #84):** Fail-closed selected-observation
  venue/market/instrument matching; role-aware timeframes (trigger vs context
  may differ); tenant assertions enter CanonicalEvidenceWindowV1 only when
  required or explicitly selected by FusionPolicy; duplicate semantic set
  members are canonicalized; Phase 5 `observation_id_for` is revision-aware
  (`source_event_id|finality|revision`) so FORMING, FINAL, and corrected
  revisions are distinct immutable appends. No evaluator, persistence, or
  live trading.


## AT-ADR-025 — Phase 6 deterministic first-slice fusion evaluator
- **Date:** 2026-09-17
- **Status:** Accepted (setup-truth evaluator only)
- **Context:** PR 84 froze identities. Setup truth still had no deterministic
  evaluator. Parallel agents must not invent a second identity or let risk
  rewrite pattern presence.
- **Decision:**
  1. `evaluate_setup(policy, command, evidence, evaluated_at)` is the sole
     first-slice setup-truth function. It reuses PR 84 contracts and Phase 5
     payloads; it does not create `Candidate` or `ActionEligibility`.
  2. Canonical pattern name remains **Bearish Liquidity Sweep with CVD
     Divergence and Aggressive Sell Imbalance at 4h Resistance**. The first
     slice makes no exhaustion claim.
  3. States are only `NO_SETUP`, `WATCH`, `PARTIAL_MATCH`, `CONFIRMED_SETUP`,
     `INVALIDATED`, `EXPIRED`. Risk, account, leverage, balance, portfolio,
     and execution availability cannot alter `SetupAssessment`.
  4. Confirmation requires every mandatory Boolean predicate (equal weight,
     threshold `1.0`): identity, finality, freshness ≤ 10s, no gap, warmup,
     Wilder ATR14, confirmed L2/R2 swing `S`, nearest versioned 4h resistance
     `R` with `abs(S-R) <= 0.50 ATR4h`, sweep/close, volume ≥ 1.50, bearish
     quote-volume CVD divergence, aggressive sell imbalance ≤ -0.10.
  5. Invalidation is `T.high + max(0.10 ATR15m, 2 * tick)`; expiry is two
     additional final 15m bars. Fail closed on wrong market/instrument/venue,
     forming/stale/missing evidence, unresolved gap, wrong perpetual identity,
     incomplete warmup, missing/invalid manual resistance, incompatible policy.
  6. Evidence order and adapter kind (watcher vs detector) must not change
     the assessment. Equivalent semantic evidence hashes identically via
     `CanonicalEvidenceWindowV1`.
- **Alternatives considered:** Interpret a generic AST VM now (rejected: first
  slice is one compiled pattern); persist candidates in this slice (rejected:
  evaluator owns truth only); let account/risk fields veto setup (rejected:
  eligibility is a later contract).
- **Safety impact:** Paper only. No exchange, execution, Telegram, watcher, or
  deployment calls.
- **Consequences:** Candidate persistence and action eligibility remain later
  Phase 6 slices against the same frozen identities.

## AT-ADR-026 — Phase 6 candidate lifecycle service (in-memory authority)
- **Date:** 2026-09-17
- **Status:** Accepted (application service; PostgreSQL/Alembic not in this slice)
- **Context:** PR #84 froze Candidate / CandidateUniquenessTuple / transitions.
  Parallel persistence work must not invent a second candidate authority.
  PaperValidationCandidate remains a downstream queue (§26).
- **Decision:**
  1. Canonical candidate authority is `CandidateLifecycleService`.
  2. Creation requires SetupAssessment `CONFIRMED_SETUP` plus the exact
     `CanonicalEvidenceWindowV1` plus the tenant-owned
     `CompiledSetupDefinition` identity. NO_SETUP / WATCH / PARTIAL_MATCH /
     EXPIRED / INVALIDATED cannot mint ACTIVE candidates.
  3. Uniqueness is exactly `CandidateUniquenessTuple`. Duplicate semantic
     confirmations converge, including concurrent inserts. Distinct org,
     strategy version, compiled setup, fusion policy version, direction,
     venue, market, instrument, timeframe, or evidence-window hash do not
     converge.
  4. Initial state is ACTIVE. Descendants are PLAN_CREATED and terminal
     REJECTED / SKIPPED / EXPIRED / INVALIDATED. Terminal states cannot
     resurrect. History is append-only.
  5. Persistence is a typed `CandidateRepository` port with deterministic
     `InMemoryCandidateRepository` only. No SQLAlchemy models, no Alembic,
     no fusion evaluator, no watcher/Telegram/execution wiring.
  6. Legacy `PaperValidationCandidate` cannot create canonical identity.
  7. **Idempotency (integration hardening):** organization-scoped creation
     `idempotency_key` binds to the canonical uniqueness fingerprint. Exact
     retries converge; same key with a different payload fails closed.
     Transition idempotency is candidate-scoped: exact key+payload replay
     returns the original record; key reuse with a changed payload fails
     closed. A different key requesting an already-applied state converges
     only when the semantic fingerprint matches the original transition.
- **Alternatives considered:** Persist PostgreSQL in this slice (rejected:
  separate durable-persistence agent); derive candidates from PVC or
  TradingView signals (rejected: competing authority).
- **Safety impact:** Paper only. No network, execution, feature flags, or
  live trading.
- **Consequences:** Later Phase 6 agents bind PostgreSQL to the same port
  without changing uniqueness or lifecycle rules.

## AT-ADR-027 — Phase 6 evaluator + candidate runtime foundation integration
- **Date:** 2026-09-17
- **Status:** Accepted (in-memory runtime foundation only)
- **Context:** Main already contains Phase 6 contracts, compatibility audit,
  and the golden first-slice fixture corpus. PR #87 owns setup truth. PR #86
  owns candidate authority. They must share one package without rewriting
  frozen contracts or restoring removed fixture-specific semantic hashing.
- **Decision:**
  1. Integrate evaluator and candidate lifecycle additively onto current
     main. Do not change CanonicalEvidenceWindowV1, SetupAssessment,
     Candidate, or the golden fixture corpus.
  2. Canonical strategy name remains **Bearish Liquidity Sweep with CVD
     Divergence and Aggressive Sell Imbalance at 4h Resistance**.
  3. Evaluator never creates candidates. Combined flow is composition:
     evidence → `evaluate_setup` → CONFIRMED_SETUP → exactly one ACTIVE
     candidate. Duplicate semantic evaluation converges.
  4. Golden fixtures remain the authoritative corpus in
     `backend/tests/fixtures/phase6_first_slice/`. Evaluator/candidate
     tests adapt to current support APIs; they do not overwrite fixtures.
  5. No PostgreSQL, Alembic, watcher, Telegram, eligibility, TradePlan,
     execution, frontend, or live trading.
- **Alternatives considered:** Merge PR 86/87 fixture support over the
  corpus (rejected: corpus on main is authoritative); couple evaluator to
  candidate creation (rejected: separate authorities).
- **Safety impact:** Paper only. No external calls, execution, or deployment.
- **Consequences:** Independent integration review is the next gate. Draft
  PR only; do not merge in the implementing agent instructions.

## AT-ADR-028 — Phase 6 deterministic ActionEligibility service
- **Date:** 2026-09-17
- **Status:** Accepted (in-memory eligibility authority only)
- **Context:** Frozen `ActionEligibility` binds identity and state but does not
  evaluate. SetupAssessment is market truth. A confirmed Candidate still must
  not proceed toward paper TradePlan creation until account, risk, safety,
  configuration, stale action evidence, and first-slice cross-venue basis are
  checked without rewriting setup history.
- **Decision:**
  1. `ActionEligibilityService.evaluate` is the sole first-slice action gate.
     It consumes a canonical Candidate, SetupAssessment lineage, account,
     portfolio, risk snapshot, safety snapshot, market-action evidence, and
     paper execution configuration. It does not create Candidates, mutate
     SetupAssessment, create TradePlanRevision, reserve risk, consume
     approval, or call venues.
  2. Frozen `ActionEligibilityState` remains `ELIGIBLE | BLOCKED | EXPIRED`.
     Finer distinctions are `EligibilityReasonCode` values. Kill switch maps
     to `BLOCKED_KILL_SWITCH` and always dominates. Daily loss and weekly loss
     use Phase 1 `check_daily_loss_lock` / `check_weekly_loss`. Capacity uses
     `RiskEngine.limits.max_position_pct_of_equity`. Account/tenant isolation
     maps to `BLOCKED_ACCOUNT_STATE`. Stale required action evidence maps to
     `BLOCKED_DATA_QUALITY`. Cross-venue basis above 20 bps maps to
     `BLOCKED_BASIS` and never changes SetupAssessment. Live/real-trading
     configuration maps to `BLOCKED_CONFIGURATION`. Non-confirmed setup maps
     to `BLOCKED_SETUP_NOT_CONFIRMED`.
  3. First-slice cross-venue basis threshold is 20 bps (`>` blocks; `<=`
     eligible). Basis is an eligibility gate only.
  4. Identical semantic evaluation converges on one immutable record.
     Changed risk snapshot identity or safety epoch appends a distinct
     evaluation revision without mutating history.
  5. `live_executable` is always false. `paper_actionable` is true only for
     `ELIGIBLE` under paper configuration. Persistence remains in-memory.
- **Alternatives considered:** New ActionEligibilityState values such as
  `BLOCKED_SAFETY` (rejected: frozen contract already has ELIGIBLE/BLOCKED/
  EXPIRED); evaluating eligibility inside `evaluate_setup` (rejected: setup
  truth must stay independent); constructing TradePlan to probe risk size
  (rejected: this slice ends at eligibility truth).
- **Safety impact:** Paper only. Kill switch remains the Phase 1 authority.
  Real trading cannot become executable. No network, Telegram, watcher,
  Alembic, or deployment.
- **Consequences:** Later slices may bind PostgreSQL and feed ELIGIBLE results
  into paper TradePlan creation. Draft PR only; do not merge.

## AT-ADR-029 — Watcher orchestration wires to Phase 6 fusion (first slice)
- **Date:** 2026-09-17
- **Status:** Accepted (orchestration wiring only)
- **Context:** PR #88 integrated `evaluate_setup` and `CandidateLifecycleService`
  on frozen contracts. Watcher orchestration still used a scripted evaluation
  boundary that forbade all candidate IDs. Manual and worker scans must consume
  the same deterministic evidence and evaluator without a second identity.
  Wave B integration assigned this ADR `AT-ADR-029` because PR 90 already
  occupied `AT-ADR-028` for ActionEligibility.
- **Decision:**
  1. One typed service, `WatcherFusionEvaluationService`, implements
     `WatcherEvaluationBoundary`. Watcher does not implement trading predicates.
  2. Flow is scan evidence → `evidence_window_from_assessment_command` →
     `evaluate_setup` → `SetupAssessment` → `CandidateLifecycleService` only
     when state is `CONFIRMED_SETUP` and mode is `PERSIST_EVIDENCE`.
  3. Watcher does not compute a second evidence hash and does not mint
     candidate IDs. CanonicalEvidenceWindowV1 and CandidateLifecycleService
     remain identity authorities.
  4. NO_SETUP, WATCH, PARTIAL_MATCH, INVALIDATED, and EXPIRED do not create
     an ACTIVE candidate. Duplicate semantic scans converge. Organization
     isolation remains `(organization_id, scan_scope)` plus the candidate
     uniqueness tuple.
  5. SetupAssessment stays market truth only. Balance, portfolio, risk,
     leverage, execution availability, and account state are not inputs.
     Action eligibility, TradePlan, Telegram, execution, Alembic, and a
     second WatcherStore adapter are out of scope.
  6. Persistence remains the existing WatcherStore port (in-memory now;
     PostgreSQL owned by PR #85). `WATCHER_ORCHESTRATION_ENABLED` and
     `MARKET_WATCHER_ENABLED` stay false. No scheduler activation.
  7. Orchestrator honesty still rejects unverified candidate IDs. Canonical
     publication requires SUCCEEDED, reason `confirmed_setup`, a 64-hex
     `evidence_validity_token`, and exactly one candidate id.
- **Alternatives considered:** Put predicates in WatcherOrchestrator
  (rejected: second evaluator); mint watcher-scoped candidate IDs (rejected:
  competing identity); create candidates on PREVIEW (rejected: dry-run);
  mix ActionEligibility into SetupAssessment (rejected: setup vs action).
- **Safety impact:** Watcher remains disabled. Paper only. No live trading,
  exchange mutation, Telegram, or deployment.
- **Consequences:** Draft PR only; do not merge. Later work may bind a live
  market-evidence adapter and PostgreSQL WatcherStore without changing this
  evaluation boundary.

## AT-ADR-030 — Phase 6 Candidate Telegram alert foundation
- **Date:** 2026-09-17
- **Status:** Accepted (composition foundation; Telegram remains disabled)
- **Context:** Canonical Candidate authority (AT-ADR-026/027) and the isolated
  Telegram security protocol (AT-ADR-023) both exist. Alerts must not treat
  PaperValidationCandidate, PaperSignal, SetupDetection, or TradingViewSignal
  as independent candidate authorities. APPROVE must never execute. Wave B
  integration assigned this ADR `AT-ADR-030` because `AT-ADR-028` is
  ActionEligibility and `AT-ADR-029` is watcher fusion wiring.
- **Decision:**
  1. New package `app.candidate_alerts` composes Candidate -> deterministic
     `CandidateAlertIntent` -> existing Telegram outbox -> secured actions.
  2. Identity binds organization, user, account, candidate ID, candidate
     content hash, strategy version, compiled setup identity, fusion policy
     version, evidence window hash, lifecycle revision, alert kind, and
     Telegram channel. Duplicate semantic events converge. Revision/content
     changes produce a distinct identity.
  3. Structured alert facts are canonical only. No profitability claims.
     No LLM-authored setup truth.
  4. APPROVE stays authorization intent (`executes=false`). REJECT/SKIP map
     to `CandidateLifecycleService` through the gateway. REDUCE_RISK emits
     typed intent and does not mutate Candidate. EXPLAIN/SHOW_CHART/STATUS
     are read-only. CLOSE remains unavailable. EXECUTE_PAPER_PLAN is absent.
  5. Reuse `TelegramSecurityStore` / in-memory protocol store. No new
     PostgreSQL adapter, no Alembic, no webhook, no Telegram network call.
     PR 85 remains durable Telegram persistence owner.
  6. `TELEGRAM_INTERACTION_ENABLED=false`, `ENABLE_REAL_TRADING=false`,
     `EXECUTION_MODE=paper`.
- **Alternatives considered:** Alert from PaperValidationCandidate or
  TradingViewSignal (rejected: competing authority); extend TelegramSecurityStore
  with alert tables (rejected: second persistence model); let APPROVE call
  execution (rejected: CRITICAL-03).
- **Safety impact:** Telegram stays off. Live trading stays disabled. Approval
  cannot execute. Candidate immutability is preserved for REDUCE_RISK and
  read-only actions.
- **Consequences:** Docs in `docs/phase6_candidate_telegram_alerts.md`. Tests in
  `backend/tests/test_phase6_candidate_telegram_alerts.py`.
- **Validation:** Candidate alert tests, Telegram protocol tests, Phase 6
  Candidate tests, full backend pytest, ruff, mypy `--strict` on the new
  package plus `app.telegram_security` and `app.signal_fusion`, GitHub CI.
  Draft PR only; do not merge in the implementing agent instructions.

## AT-ADR-031 — Phase 6 canonical Candidate PostgreSQL persistence
- **Date:** 2026-09-18
- **Status:** Accepted (PostgreSQL adapter; not wired into staging/production)
- **Context:** AT-ADR-026 froze CandidateLifecycleService as the only Candidate
  authority with an in-memory `CandidateRepository`. Durable uniqueness,
  append-only transitions, and the remaining Watcher persist-after-lease-loss
  race require a PostgreSQL adapter that cannot become a second authority.
- **Decision:**
  1. `PostgresCandidateRepository` implements the existing
     `CandidateRepository` port. `CandidateLifecycleService` remains the only
     Candidate authority. In-memory remains the unit-test default.
  2. Projections persist in `canonical_candidates`. Creation idempotency,
     append-only transitions, and transition-key aliases are separate tables
     with uniqueness constraints matching in-memory semantics.
  3. Worker-originated writes bind Watcher lease identity. The Candidate
     persist transaction `SELECT ... FOR UPDATE` locks
     `watcher_worker_leases (organization_id, scan_scope)` and proves current
     owner, epoch, fencing token, and a non-expired lease before committing
     Candidate authority. A stale worker cannot persist after losing its lease.
  4. One Alembic revision from head `3ec264f9aaa8`. Adapters are constructed
     explicitly and are not imported by FastAPI, workers, or feature flags.
  5. `WATCHER_ORCHESTRATION_ENABLED`, `MARKET_WATCHER_ENABLED`,
     `TELEGRAM_INTERACTION_ENABLED`, and live trading remain disabled.
- **Alternatives considered:** Check fence then persist in a later transaction
  (rejected: TOCTOU); put Candidate identity in WatcherStore (rejected: second
  authority); change TradePlan / ActionEligibility / SetupAssessment (out of
  scope).
- **Safety impact:** Paper only. No Watcher, Telegram, or live-trading
  activation.
- **Consequences:** Tests in `backend/tests/test_phase6_candidate_postgres.py`.
  Migration `4fd8c1a90b27`.

## AT-ADR-032 — Phase 7 canonical TradePlanRevision application layer
- **Date:** 2026-09-18
- **Status:** Accepted (application layer; durable PostgreSQL binding follows)
- **Context:** Phase 6 froze Candidate and ActionEligibility as in-memory
  authorities. Phase 1 `TradePlanRevision` is hash-stable and immutable, but
  `ProposalService.create_revision` is fail-closed and
  `trade_plan_revisions.candidate_id` still foreign-keys
  `paper_validation_candidates`. PR 93 owned the application layer. Source PR
  claimed `AT-ADR-031`, already used by Candidate PostgreSQL, so this ADR is
  `AT-ADR-032`.
- **Decision:**
  1. `CanonicalTradePlanService.create` is the sole first-slice plan authority.
  2. Creation loads Candidate and ActionEligibility from those services. Only
     `ACTIVE` + currently paper-actionable `ELIGIBLE` may insert. Lineage
     (org/user/account, candidate identity/hash/revision, assessment, evidence
     window, strategy/setup, venue/market/instrument/timeframe/side) must match
     exactly. Plan `candidate_id` is the canonical Candidate id.
  3. Phase 1 `TradePlanRevisionSemantic` / `CanonicalTradePlanContentV1` stays
     hash-stable. Canonical binding is `CanonicalTradePlanLineage` beside that
     preimage, not new semantic fields.
  4. Identical semantic requests converge (presentation/correlation first-write
     wins). Conflicting organization-scoped idempotency fails closed. One plan
     per (org, user, account, candidate) in this slice.
  5. `CandidateState.PLAN_CREATED` is applied only after a successful store
     insert; failed creates do not transition. Transition identity is derived
     from plan uniqueness so retries converge.
  6. `PaperValidationCandidate` and `ProposalService.create_revision` cannot
     mint canonical plan authority. Approval may bind revision id + content
     hash only and cannot change executable semantics. Execution is absent.
  7. Application persistence is `CanonicalTradePlanStore`. Source PR used an
     unbound SQLAlchemy adapter because the PVC FK was still in place. Phase 7
     integration owns the durable remap after Candidate migration `4fd8c1a90b27`.
- **Alternatives considered:** Add lineage fields to `TradePlanRevisionSemantic`
  (rejected: would break existing Phase 1 content-hash verification); write
  canonical UUID5 ids into the PVC FK (rejected: competing identity, FK
  violation).
- **Safety impact:** Paper only. Live trading cannot become executable. No
  network, Watcher, Telegram, Journal, execution dispatch, frontend, or
  deployment changes.
- **Consequences:** Docs in `docs/phase7_canonical_trade_plan_binding.md`.
  Tests in `backend/tests/test_phase7_canonical_trade_plan.py`.
- **Validation:** Focused canonical plan tests, Phase 1 planning/approval
  tests, Phase 6 candidate/eligibility tests, full backend pytest, ruff,
  mypy `--strict`, GitHub CI. Draft PR only; do not merge.

## AT-ADR-033 — Phase 7 learning attribution reuses journal projector
- **Date:** 2026-09-18
- **Status:** Accepted (application service; PostgreSQL/Alembic not in this slice)
- **Context:** Phase 4 already converges one execution lifecycle to one
  `JournalTrade`. Phase 6 froze SetupAssessment/Candidate/TradePlan identity.
  Learning analytics still summarize the older paper-validation funnel. This
  wave must connect canonical lifecycle to learning without a second trading
  authority or competing migration. Source PR 95 claimed `AT-ADR-031`, already
  used by Candidate PostgreSQL, so this ADR is `AT-ADR-033`.
- **Decision:**
  1. `JournalLifecycleProjector` remains the only JournalTrade writer.
  2. `LearningAttributionService` / `JournalLifecycleLearningService` are
     record-only consumers. They copy lineage; they do not evaluate setups,
     create candidates, authorize plans, or dispatch execution.
  3. Lineage rides on append-only `payload.lineage`. First-seen values are
     sticky. Duplicate source identity converges. Conflicting identity and
     cross-tenant attribution fail closed.
  4. REJECT/SKIP never create executed trade outcomes. Quality axes are
     planned setup vs execution vs trader behavior. PnL cannot rewrite
     SetupAssessment hashes. LLM narrative is excluded from `facts_hash`.
  5. Lesson/analytics/RAG adapters consume facts. Lessons are suggestions
     only. RAG is a renderer only. No shared schema changes in this wave.
- **Alternatives considered:** New journal trade writer for learning (rejected:
  competing authority); Alembic columns in this wave (rejected: Candidate and
  TradePlan migrations already occupy the Alembic head); auto-persisting
  lessons or RAG ingest (rejected: review workflow and market-truth integrity).
- **Safety impact:** Paper only. No live trading, Watcher, Telegram, frontend,
  or execution-dispatch changes.
- **Consequences:** Docs in `docs/phase7_learning_attribution.md`. Tests in
  `backend/tests/test_learning_attribution.py` and
  `backend/tests/test_journal_lifecycle_lineage.py`.
- **Validation:** Targeted attribution/journal/learning pytest, full backend
  pytest, ruff, mypy `--strict` on the new package plus journal lifecycle
  modules, GitHub CI. Draft PR only; do not merge.

## AT-ADR-034 — Phase 7 canonical TradePlan / ActionEligibility PostgreSQL binding
- **Date:** 2026-09-19
- **Status:** Accepted (PostgreSQL adapter; not wired into staging/production)
- **Context:** AT-ADR-032 froze CanonicalTradePlanService as the only first-slice
  plan authority with an in-memory store. `trade_plan_revisions.candidate_id`
  still foreign-keyed `paper_validation_candidates`. ActionEligibility was
  in-memory only. Candidate PostgreSQL (AT-ADR-031 / Alembic `4fd8c1a90b27`)
  landed first.
- **Decision:**
  1. Next Alembic revision is `c9e2b4a1d078` after `4fd8c1a90b27`.
  2. `PostgresActionEligibilityStore` implements `ActionEligibilityStore`.
     Evaluations are immutable and keyed by uniqueness hash. Identity bindings
     fail closed. Candidate rows are locked so revision history is append-safe.
  3. `trade_plan_revisions` keeps `candidate_id` as an unconstrained UUID for
     legacy PVC-backed rows. Discriminator `plan_authority` is
     `paper_validation` (canonical_candidate_id NULL) or `canonical`
     (canonical_candidate_id = candidate_id, FK canonical_candidates).
     Legacy PVC ids are never reinterpreted as canonical Candidate ids.
  4. Canonical rows bind `compiled_setup_definition_id` to tenant-owned
     `compiled_setup_definitions`. Global SetupDefinition FK is dropped.
  5. `canonical_trade_plan_lineage` stores Candidate/eligibility hashes beside
     unchanged `semantic_payload`. Uniqueness hash is unique. Idempotency keys
     are organization-scoped.
  6. Canonical `plan_id` remains UUID5(org, user, account, candidate_id). A
     compatibility TradeProposal with `plan_root_kind=canonical_plan_root`
     satisfies the existing composite FK without restoring ProposalService as
     plan authority.
  7. Plan insert locks the canonical Candidate and runs `PLAN_CREATED` in the
     same transaction via `on_inserted`. Adapters are not imported by FastAPI,
     workers, or feature flags. Execution remains a later slice.
- **Alternatives considered:** Backfill PVC ids as canonical Candidate ids
  (rejected: competing identity); put lineage fields into
  TradePlanRevisionSemantic (rejected: would break Phase 1 hashes); delete
  canonical rows on Alembic downgrade automatically (rejected: fail closed if
  canonical rows exist).
- **Safety impact:** Paper only. Watcher, Telegram, and live trading stay
  disabled. No exchange mutation. No deployment.
- **Consequences:** Docs in `docs/phase7_canonical_trade_plan_binding.md`.
  Tests in `backend/tests/test_phase7_eligibility_postgres.py` and
  `backend/tests/test_phase7_trade_plan_postgres.py`. Migration `c9e2b4a1d078`.

## AT-ADR-035 — Phase 8 learning attribution PostgreSQL persistence
- **Date:** 2026-09-19
- **Status:** Accepted (PostgreSQL adapter; FastAPI/runtime wiring owned by later Phase 8 slices)
- **Context:** AT-ADR-033 froze record-only learning attribution on
  `JournalLifecycleProjector` with an in-memory store. Canonical trade outcomes
  still could not be queried as durable strategy/pattern intelligence. Agent 1's
  persistence contract lived in `AGENT_1_ATTRIBUTION_INTEGRATION`.
- **Decision:**
  1. Next Alembic revision is `d4f7a2c8e901` after `c9e2b4a1d078`.
  2. `learning_attribution_records` is the candidate-scoped aggregate
     (`UNIQUE(organization_id, candidate_id)`). Partial unique
     `(organization_id, execution_lifecycle_id)` when set. Events are append-only
     and unique on the same source identity as journal projection receipts.
  3. `PostgresAttributionStore` implements `AttributionStore` on the caller's
     session. Duplicate source identity converges. Conflicting identity, sticky
     lineage, and cross-tenant access fail closed.
  4. Quality axes remain separate: setup quality, execution quality, risk
     adherence, trader behavior, and outcome. REJECT/SKIP cannot become executed
     outcomes. PnL cannot rewrite SetupAssessment or evidence-window hashes.
  5. `learning_venue_mode` is `paper_internal` or `paper_exchange_demo` so future
     demo trade learning does not mix with internal paper stats. Live/real is
     not a venue.
  6. `LearningQueryService` is the read API for strategy/pattern stats,
     human-versus-system comparison, lesson suggestions (`persist=false`), and
     RAG evidence documents. Narrative is labeled `NARRATIVE_NOT_FACT` and is
     excluded from `facts_hash`. RagService is not called.
  7. Optional `journal_trades` lineage columns are projector-stamped query
     helpers. The projector remains the only JournalTrade writer. Slice 84
     paper-validation learning analytics is not replaced.
- **Alternatives considered:** Materialized stats table as a second authority
  (rejected: derive stats from records); auto-ingest learning narrative to Qdrant
  (rejected: market-truth and lesson-review integrity); FastAPI/worker wiring
  (rejected: out of scope for this slice).
- **Safety impact:** Paper only. No live trading, Watcher, Telegram, frontend, or
  execution-dispatch changes in this slice.
- **Consequences:** Docs in `docs/phase8_learning_persistence.md`. Tests in
  `backend/tests/test_phase8_learning_persistence.py`. Migration `d4f7a2c8e901`.
- **Validation:** Attribution/idempotency/tenant/RAG-boundary tests, Alembic
  upgrade/downgrade/reupgrade and single head, full backend pytest, ruff, mypy
  `--strict`, GitHub CI. Source PR #97; integrated on
  `cursor/phase8_final_integration`.

## AT-ADR-036 — Phase 8 canonical PAPER runtime execution
- **Date:** 2026-09-19
- **Status:** Accepted (runtime composition; paper execution only)
- **Context:** AT-ADR-034 bound Candidate, ActionEligibility, and canonical
  TradePlanRevision to PostgreSQL without FastAPI or worker wiring. Phase 1
  `EXECUTE_PAPER_PLAN` already claims `paper_validation` plans. Canonical plans
  must not become ProposalService trading authority. Source PR #99 claimed
  `AT-ADR-035`, already used by learning persistence, so this ADR is
  `AT-ADR-036`.
- **Decision:**
  1. `ProductionCanonicalRuntime` is the production composition for PostgreSQL
     Candidate, ActionEligibility, and CanonicalTradePlan adapters.
     `CandidateLifecycleService` remains the only Candidate authority.
  2. FastAPI lifespan stores the runtime on `app.state`. Request handlers bind
     the caller's Session. The worker constructs the same runtime and never
     starts Watcher or Telegram. Flags default false.
  3. `ExecutionService.execute_paper_plan` routes `plan_authority=canonical` to
     `CanonicalPaperExecutionService`. That path re-verifies Candidate,
     eligibility, plan hash, approval, and account lineage immediately before
     the existing `PaperPlanClaimService` claim transaction. Kill switch and
     `evaluate_claim_predicate` remain final. Authorization is not consumed on
     `BLOCKED`.
  4. Canonical execution is PAPER only. `live_executable` stays false. No
     exchange mutation. Duplicate idempotency keys converge across restarts.
     Stale, rejected, expired, mismatched, or modified plans fail closed.
  5. Compatibility `canonical_plan_root` TradeProposal rows remain FK-only.
     ProposalService create always writes `analysis_proposal`. List omits
     canonical roots. Get/update paths raise `TradingPolicyError`.
  6. Journal lifecycle receives deterministic `approved_plan` / `fill` events
     with `source_system=canonical_paper_execution` via
     `JournalLifecycleProjector`. Exact replay converges.
  7. HTTP `POST /execution/paper-plan` accepts only identity + idempotency.
     Executable fields are forbidden. Legacy `POST /execution/paper` stays
     fail-closed and must not mint canonical Candidate or TradePlan authority.
- **Alternatives considered:** Making ProposalService the canonical plan
  authority (rejected: competing authority); enabling Watcher/Telegram with
  this wave (rejected: explicitly out of scope); new Alembic (rejected: Phase 7
  schema plus PR97 learning migration are sufficient).
- **Safety impact:** Paper only. Watcher, Telegram, and live trading stay
  disabled. No real exchange mutation. No deployment.
- **Consequences:** Docs in `docs/phase8_runtime_execution.md`. Tests in
  `backend/tests/test_phase8_runtime_composition.py` and
  `backend/tests/test_phase8_canonical_paper_execution.py`.
- **Validation:** Adversarial execution pytest, full backend pytest, ruff,
  scoped `mypy --strict` on Phase 8 modules, deployment-safety, relevant HTTP
  E2E, GitHub CI. Source PR #99; integrated on
  `cursor/phase8_final_integration`.

## AT-ADR-037 — Canonical paper decision frontend (user-facing workflow)
- **Date:** 2026-09-19
- **Status:** Accepted (frontend composition; no backend authority invented)
- **Context:** Redesign architecture §14/§15 Phase 12 consolidates surfaces.
  Backend Phase 6/7 Candidate, ActionEligibility, TradePlanRevision, and
  ExecutionReceipt exist as services/stores. Source PR #98 claimed
  `AT-ADR-035`, already used by learning persistence, so this ADR is
  `AT-ADR-037`.
- **Decision:**
  1. Plan landing is `/decision`. Legacy `/workspace`, `/proposals`,
     `/approvals`, and `/paper-validation/*` stay reachable.
  2. Market quality and action eligibility are separate cards. Setup quality
     never grants permission to act.
  3. Canonical TradePlan execution binds to `POST /execution/paper-plan`
     with identity + idempotency only. Legacy `POST /execution/paper` remains
     compatibility-only and must not execute a canonical TradePlan.
  4. Canonical reads reuse existing authorities: candidates, setup
     assessment, action eligibility, execution receipt, and
     `LearningQueryService` strategy/pattern statistics. Missing HTTP is
     added as a thin read adapter, not a second domain model.
  5. PaperValidationCandidate is a compatibility projection, not
     CandidateLifecycleService. `canonical_plan_root` stays invisible to
     ProposalService trading authority.
  6. Human approval records authorization only. AI copy cannot claim to
     approve or execute. No live execution control is rendered.
  7. Kill switch and paper/real-trading posture are on every decision screen.
     LLM never becomes deterministic authority.
- **Alternatives considered:** Treat PVC as canonical Candidate (rejected:
  AT-ADR-026); keep `POST /execution/paper` as the decision-hub execute path
  (rejected: AT-ADR-036 fail-closed); hide legacy Plan routes (rejected: keep
  functional until telemetry justifies deprecation).
- **Safety impact:** Paper only. Watcher, Telegram, and live trading stay
  disabled. No real exchange mutation. No deployment.
- **Consequences:** Docs in `docs/redesign/phase8_canonical_frontend.md`.
  Frontend module `frontend/src/lib/canonical-decision/`.

## AT-ADR-038 — Phase 8 release-candidate integration
- **Date:** 2026-09-19
- **Status:** Accepted (integration; draft PR only; do not merge or deploy)
- **Context:** PR97, PR99, and PR98 landed independently on `main@cd9087a`.
  All three claimed AT-ADR-035 / AT-055. Canonical frontend executed through
  legacy `POST /execution/paper`. Runtime learning used in-memory attribution.
- **Decision:**
  1. Integration order is PR97 → PR99 → PR98. Do not merge `main`.
  2. ADR/task IDs remap to AT-ADR-035/AT-055 (learning), AT-ADR-036/AT-056
     (runtime), AT-ADR-037/AT-057 (frontend).
  3. Alembic remains a single head: `d4f7a2c8e901` after `c9e2b4a1d078`.
  4. Canonical frontend execution binds to `POST /execution/paper-plan`.
  5. Thin `/canonical/*` read APIs reuse Candidate, ActionEligibility,
     ExecutionReceipt, and `LearningQueryService`. No duplicate domain models.
  6. Runtime paper execution attributes through `PostgresAttributionStore`.
  7. Paper only. Watcher and Telegram stay disabled. Kill switch and risk
     BLOCK stay final. Human approval is mandatory. LLM is never authority.
- **Alternatives considered:** Merge into `main` in this wave (rejected);
  create another Alembic revision (rejected: not required).
- **Safety impact:** Paper only. No deploy. No real exchange mutation.
- **Consequences:** Branch `cursor/phase8_final_integration`. Tests in
  `backend/tests/test_phase8_canonical_workflow.py`.

## AT-ADR-039 — Final backend hardening fail-closed attribution and deployment pins
- **Date:** 2026-09-19
- **Status:** Accepted
- **Context:** After Phase 8 integration (`main@c39dca6`), ALLOW journal projection
  could silently skip learning when Candidate or ActionEligibility lookup returned
  `None`. `PaperExecutionRiskGate` remains unused on `EXECUTE_PAPER_PLAN`. Staging
  deployment safety logged Telegram flags but did not reject Watcher/Telegram enablement.
- **Decision:**
  1. Missing Candidate, eligibility, or required assessment lineage after ALLOW
     raises `LearningAttributionIncompleteError` and rolls back the claim/journal
     unit of work. Learning evidence is never silently skipped.
  2. Claim-time final risk authority remains `evaluate_claim_predicate` under
     locked safety-epoch and `AccountRiskAccountingState`, plus canonical lineage
     revalidation including persisted ActionEligibility TTL. Do not integrate
     unused `PaperExecutionRiskGate` as a competing claim authority.
  3. `POST /execution/paper-plan` commits replay requests without double-metering
     usage so healing writes persist.
  4. Canonical fill projection fails closed when the command/plan is missing or
     the production canonical runtime is unbound on a canonical plan.
  5. Staging/production reject Watcher and Telegram enablement flags. Accidental
     real trading remains impossible via existing paper/deployment/exchange
     invariants (`real_trading_enabled` is permanently false).
- **Alternatives considered:** Persist a separate attribution-failure row while
  committing ALLOW (rejected for this slice: would split execution from learning);
  wire `PaperExecutionRiskGate` into claim (rejected: second risk authority).
- **Safety impact:** Paper only. Watcher, Telegram, and live trading stay disabled.
  No deploy.
- **Consequences:** Branch `cursor/final_backend_hardening`. Tests in
  `backend/tests/test_phase8_backend_hardening.py`. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/102 (do not merge). Integrated on
  `cursor/final_release_integration-c461`.

## AT-ADR-040 — Staging/production refuse Watcher and Telegram enablement
- **Date:** 2026-09-19
- **Status:** Accepted
- **Context:** Phase 8 canonical architecture is paper-ready. Watcher and Telegram
  defaulted false but were not locked, so a staging env-var mistake could start
  orchestration or outbound alerts. Final synthetic staging validation requires
  those surfaces to stay off. Source PR #103 claimed AT-ADR-039; remapped to
  AT-ADR-040 so backend hardening keeps AT-ADR-039.
- **Decision:**
  1. `deployment_safety` rejects Watcher (scanner, bridge, auto-tick, orchestration)
     and Telegram (alerts, interaction, automatic delivery) when `ENVIRONMENT` is
     staging or production. The same checks are not duplicated: they sit next to
     the paper/`real_trading_enabled` pins.
  2. `/health` exposes those flags plus `exchange_mode` so `verify-safety.sh` can
     assert them without printing secrets.
  3. Real trading remains permanently impossible (`paper_safety`).
  4. Canonical HTTP minting is still out of scope; synthetic smoke seeds via
     existing services or optional operator IDs.
- **Alternatives considered:** Leave flags default-off only (rejected: operator
  misconfig could enable Watcher/Telegram on staging).
- **Safety impact:** Tightens paper staging; does not enable live trading.
- **Consequences:** Branch `cursor/final_staging_readiness`. Tests in
  `backend/tests/test_deployment_safety.py` and
  `backend/tests/test_canonical_staging_smoke.py`. Integrated on
  `cursor/final_release_integration-c461`.

## AT-ADR-041 — Final paper-release candidate integration
- **Date:** 2026-09-19
- **Status:** Accepted (integration; draft PR only; do not merge or deploy)
- **Context:** PR102, PR101, and PR103 landed independently on `main@c39dca6`.
  All three claimed AT-059. PR102 and PR103 both claimed AT-ADR-039.
  `deployment_safety` Watcher/Telegram locks overlapped.
- **Decision:**
  1. Integration order is PR102 → PR101 → PR103. Do not merge `main`. Do not deploy.
  2. Task/ADR IDs remap to AT-059/AT-ADR-039 (backend hardening), AT-060/AT-ADR-037
     (canonical UX), AT-061/AT-ADR-040 (staging readiness), AT-062/AT-ADR-041
     (this integration).
  3. Watcher/Telegram fail-closed checks are kept once beside paper and
     `real_trading_enabled` pins. `/health` posture flags from staging readiness
     remain the operator-visible surface.
  4. Canonical frontend binds GET APIs and `POST /execution/paper-plan` only.
     ProposalService tenant-scopes before `canonical_plan_root` rejection.
  5. Alembic remains a single head: `d4f7a2c8e901`.
  6. Paper only. Watcher and Telegram stay disabled. Kill switch and risk BLOCK
     stay final. Human approval is mandatory. No silent learning loss. Cross-tenant
     lookups fail closed. Duplicate/restart paper-plan requests are idempotent.
- **Alternatives considered:** Last-write-wins on `deployment_safety` (rejected:
  would drop either `real_trading_enabled` pin or `/health` operator surface);
  merge to `main` in this wave (rejected).
- **Safety impact:** Paper only. No deploy. No real exchange mutation.
- **Consequences:** Branch `cursor/final_release_integration-c461`. Draft PR
  https://github.com/Fejjii/AlphaTrade-AI/pull/104. GitHub CI run 35466201940
  success. Report `docs/FINAL_RELEASE_READINESS.md`.

## AT-ADR-042 — Canonical live read-only USD-M evidence pipeline (AT-064)
- **Date:** 2026-09-20
- **Status:** Accepted (source PR #110; remapped on intelligence integration)
- **Context:** Phase 5 USD-M contracts and CanonicalEvidenceWindowV1 existed, but
  canonical reads still lacked a live/read-only assembler. Compatibility
  `POST /market/analyze` snapshots could be presented as current market prices.
- **Decision:**
  1. Assemble first-slice evidence from existing Binance USD-M GET-only contracts
     into CanonicalEvidenceWindowV1. BTCUSDT is the enabled catalog default;
     additional USD-M symbols are registerable without rewriting the assembler.
  2. Current price is the last contracted perpetual trade in the 10s freshness
     window. Missing, stale, or incomplete evidence fails closed. No spot fallback.
     No fabricated prices. Replay fixtures are labeled `replay_fixture` and are
     never `usable_as_current_market_price`.
  3. Canonical identity remains the §26 window preimage. `organization_id` forks
     tenant hashes. Connection ids and receive times stay out of the window hash.
  4. Expose truthful current price + freshness on `GET /canonical/evidence`.
     Canonical UI (`/decision/market`) uses that GET path. Compatibility snapshots
     must not be shown as live marks.
  5. `PERPETUAL_EVIDENCE_SOURCE` stays configurable; production/runtime default
     remains `replay`. Watcher is not activated. `AssemblingWatcherScanEvidence`
     is not wired into the worker.
  6. Paper only: `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`. No exchange
     mutation, Telegram, strategy redesign, or live trading.
- **Alternatives considered:** Reuse `POST /market/analyze` as canonical current
  price (rejected: frozen/mock snapshots); auto-enable Watcher when live source
  is selected (rejected: explicit non-goal); spot fallback on USD-M outage
  (rejected: AT-ADR-021).
- **Safety impact:** Tightens honesty of market marks; does not enable Watcher
  or live trading.
- **Consequences:** Source branch `cursor/live_evidence_pipeline-5b0d`. Tests in
  `backend/tests/test_live_evidence_pipeline.py`,
  `backend/tests/test_canonical_evidence_http.py`, and canonical frontend
  honesty tests. Draft source PR #110 — do not merge independently.

## AT-ADR-043 — Conversation transcripts are non-domain memory
- **Date:** 2026-09-20
- **Status:** Accepted (source PR #111 claimed AT-ADR-043; kept on integration)
- **Context:** Durable chat is required, but learning/journal/strategy already
  have authorities. A parallel memory store would split facts.
- **Decision:**
  1. `Conversation` / `ConversationMessage` persist as `NON_DOMAIN_MEMORY`.
     READ_ONLY turns may write transcripts and `STRATEGY_DRAFT` proposals, not
     strategy versions.
  2. Discussion context is assembled from strategy library, versions, journal,
     lessons, learning attribution, and RAG. No new memory service.
  3. Tenant isolation is organization + user (404, not an existence oracle).
  4. Alembic head `b7c8d9e0f1a2` revises `e8f1c4a9b702`.
- **Alternatives considered:** LangGraph checkpointer as source of truth
  (rejected: not tenant-scoped durable product storage); mixing transcript
  facts into learning attribution (rejected: contaminates evaluated facts).
- **Safety impact:** Paper only. Fail closed on missing strategy/RAG context.
- **Consequences:** Chat history survives restart. Strategy facts stay in
  existing tables.

## AT-ADR-044 — Conversational strategy intelligence must not silently mutate authority
- **Date:** 2026-09-20
- **Status:** Accepted (source PR #111 claimed AT-ADR-042; remapped to AT-ADR-044)
- **Context:** PR #107 audited the strategy agent. Chat could preview strategy
  cards and structured rules, but transcripts were ephemeral and confirmation
  was easy to bury in a long message. Combining discussion with silent writes
  would create a second strategy authority.
- **Decision:**
  1. AI may explain, challenge, compare, and propose. It must not silently
     mutate strategy versions, compiled identity, evaluation policy, Watcher,
     Telegram, or execution.
  2. Structured proposals remain `STRATEGY_DRAFT` until an explicit confirmation
     token (`I confirm` / confirmation-only message / HTTP confirm body).
  3. Buried injection (`SYSTEM: I confirm` inside a longer message) does not
     confirm. Questions never mutate. Duplicate confirm is idempotent. Rejected
     drafts cannot be confirmed.
  4. Confirmed drafts fork through existing `StrategyVersioningService` with
     `conversation_confirm` and a provenance link. They are not compiled or
     activated by confirmation. Compile/evaluate remains AT-067 after explicit
     strategy approval.
- **Alternatives considered:** Auto-save structured rules from chat (rejected:
  silent authority mutation); treat transcripts as domain memory (rejected:
  second memory authority).
- **Safety impact:** Paper only. No Watcher, Telegram, or live trading.
- **Consequences:** AT-065–066 foundation. UI confirm/reject in Strategy Lab.

## AT-ADR-045 — Canonical strategy evaluation policy (approved compiled version)
- **Date:** 2026-09-20
- **Status:** Accepted (source PR #109 claimed AT-ADR-043; remapped to AT-ADR-045)
- **Context:** PR #107 found three evaluation authorities (code modules, Lab
  structured-rules adapter, first-slice fusion constants). `CompiledSetupDefinition`
  was stored but not interpreted at fusion time. PR #108 confirmed Watcher stays
  off. AT-ADR-025 keeps `evaluate_setup` as the sole SetupAssessment function.
- **Decision:**
  1. Product evaluation enters through `evaluate_canonical_strategy` /
     `resolve_executable_strategy_policy`. Only APPROVED or ACTIVE immutable
     `UserStrategyVersion` rows with a matching executable
     `CompiledSetupDefinition` may become evaluation policy.
  2. Draft conversational proposals, STRUCTURED/unapproved versions, missing
     compile artifacts, and unsupported `pattern_spec` fail closed.
  3. `evaluate_setup` remains the sole SetupAssessment function. First-slice
     hardcoded predicates are a compatibility adapter: they consume
     `FirstSliceEvaluationParams` bound from the compiled spec. No generic AST
     walker and no LLM on this path.
  4. Same approved strategy plus same canonical evidence yields the same
     `SetupAssessment`. Lineage is `strategy_version_id` + compiled setup id/hash
     already hashed into `CanonicalEvidenceWindowV1`.
  5. Watcher fusion evaluation calls this boundary (Watcher remains disabled).
     Paper validation exposes `evaluate_canonical_setup` on the same boundary.
     Candidate creation is unchanged. Read-projection placeholder IDs must never
     mint Candidates.
  6. Code modules `/strategies/evaluate` stay a separate Lab/chat tool, not
     SetupAssessment authority.
- **Alternatives considered:** Walk a generic compiled AST now (rejected: first
  slice only; compiler already fail-closes unsupported kinds); require stored
  spec to match constants exactly and ignore compiled thresholds (rejected:
  silent dual policy); enable Watcher to “finish the loop” (rejected: AT-ADR-040).
- **Safety impact:** Paper only. No Watcher/Telegram/live-trading flag change.
  SetupAssessment remains independent of account/risk.
- **Consequences:** Docs `docs/AT067_canonical_strategy_evaluation_policy.md`.
  Tests `backend/tests/test_at067_canonical_strategy_evaluation_policy.py`.

## AT-ADR-046 — Intelligence integration of PR109, PR110, and PR111
- **Date:** 2026-09-20
- **Status:** Accepted (integration; draft PR only; do not merge or deploy)
- **Context:** Three draft PRs landed independently on `main@20d2cac`. PR #110
  and PR #111 both claimed AT-064 / AT-ADR-042. PR #109 and PR #111 both claimed
  AT-ADR-043. Required correctness gaps: evidence identity, Watcher
  `executable_policy`, freshness vs candle close, conversation-to-pattern
  preview, and confirmation safety.
- **Decision:**
  1. Integration order is PR #109 → PR #110 → PR #111. Do not merge `main`.
     Do not deploy. Do not push source branches.
  2. Task/ADR IDs remap to AT-064/AT-ADR-042 (evidence pipeline),
     AT-065/AT-ADR-043 (conversation transcripts), AT-066/AT-ADR-044
     (confirmation-gated drafts), AT-067/AT-ADR-045 (canonical evaluation
     policy), AT-063/AT-ADR-046 (this integration).
  3. Alembic remains a single head: `b7c8d9e0f1a2` revising `e8f1c4a9b702`
     (journal `account_id` from PR #106). Validate with PostgreSQL through
     Alembic, not only `create_all`.
  4. Paper only. Replay remains the deployment default. Watcher and Telegram
     stay disabled. No automatic strategy approval. No exchange mutation.
- **Alternatives considered:** Merge source PRs independently to main (rejected:
  overlapping API clients, governance IDs, and incomplete Watcher/evidence
  contracts); enable Watcher to prove the loop (rejected: AT-ADR-040).
- **Safety impact:** Paper only. No deploy. No real exchange mutation.
- **Consequences:** Branch `cursor/intelligence_integration-1ea1`. Draft
  integration PR only; stop for independent review.

## AT-ADR-047 — Durable setup lifetime and canonical paper-trade authority
- **Date:** 2026-09-21
- **Status:** Accepted (remediation; draft PR only; do not merge or deploy)
- **Context:** Final independent acceptance review left two P1s open. Setup
  lifetime pins lived in process memory, so restart reset the original trigger.
  Paper-validation `scan` used a boolean approved-lineage gate, then
  `PaperBotEngine` plus invented StructuredRules to mint AUTO_PAPER trades.
- **Decision:**
  1. Persist setup-lifetime identity in `setup_lifetime_pins` keyed by tenant,
     symbol, timeframe, strategy version, and compiled setup id/hash. Pins store
     original trigger interval end, trigger bar hash, expiry state, and a
     semantic lineage hash. Transport metadata is excluded. Duplicate writes
     converge. An expired pin cannot resurrect after restart.
  2. Automated paper minting consumes only persisted APPROVED/ACTIVE compiled
     policy → canonical evidence → `evaluate_canonical_strategy` →
     `CONFIRMED_SETUP`. `PaperBotEngine.evaluate_entry` is not an AUTO_PAPER
     authority. Draft, `REVIEW_REQUIRED`, unsupported, incomplete, stale,
     expired, wrong-source, and mismatched-lineage inputs fail closed.
  3. `evaluate_setup` remains the sole SetupAssessment function.
     `CandidateLifecycleService` remains the sole Candidate authority.
     Watcher, Telegram, and live trading stay disabled.
- **Alternatives considered:** Keep in-memory pins and document restart
  (rejected: P1); invent a second paper SetupAssessment (rejected).
- **Safety impact:** Paper only. No Watcher/Telegram/live-trading flag change.
- **Consequences:** Alembic head `c8d9e0f1a2b3`. Draft PR only; no merge or
  deploy.

## AT-ADR-048 — Continuous live read-only USD-M market monitor (AT-069)
- **Date:** 2026-09-21
- **Status:** Accepted (source PR #118; remapped on Watcher integration)
- **Context:** AT-064 assembles first-slice evidence on demand. Watcher still
  needs a continuous, honest perpetual feed. Compatibility `/market` snapshots
  and replay fixtures must never be shown as current live prices.
- **Decision:**
  1. Add `app.market_monitor` on existing Binance USD-M GET-only adapters and
     Phase 5 cursor/CVD/freshness contracts. BTCUSDT is the catalog default.
  2. Current price is the last contracted perpetual trade in the 10s window.
     Provider outage, symbol mismatch, conflicting duplicates, out-of-order
     trades, and unresolved gaps fail closed. No spot substitution.
  3. HTTP 429 is a distinct `RateLimitedError` with bounded Retry-After backoff.
     Reconnect starts a new connection epoch. Transport metadata (connection
     ids, receive times, backoff) is excluded from the semantic hash.
  4. Expose `GET /canonical/market-status` with availability
     `fresh|stale|degraded|unavailable|replay`. Replay remains the default
     source and is never `live_mark`.
  5. Watcher, Telegram, and live trading stay disabled by default. Source PR
     #118 left the monitor port unwired; AT-072 binds it as the Watcher
     current-quote/stream gate without a second evidence authority.
- **Alternatives considered:** Background websocket (rejected for this slice:
  reuse existing REST capabilities); treat 429 as regional outage (rejected:
  recoverable backoff); show last-good price during outage (rejected: fail closed).
- **Safety impact:** Tightens market-mark honesty. Does not enable Watcher or
  live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Source branch `cursor/live-market-monitoring-cc4d`. Tests in
  `backend/tests/test_live_market_monitor.py` and
  `backend/tests/test_live_market_monitor_http.py`. Draft source PR only.

## AT-ADR-049 — Paper-only continuous Watcher runtime (AT-070)
- **Date:** 2026-09-21
- **Status:** Accepted (source PR #120 claimed AT-ADR-048 / AT-069; remapped)
- **Context:** Canonical Watcher evaluation, Postgres leases/fencing, live
  read-only evidence assembly, and CandidateLifecycleService already exist on
  main. The missing piece is a continuous paper worker that polls approved
  compiled strategies without enabling staging/production flags, Telegram, or
  live orders.
- **Decision:**
  1. Dedicated process `python -m app.workers.watcher_paper` is the canonical
     worker. The API may autostart a daemon thread only when
     `paper_runtime_enabled`: local + paper + `WATCHER_ORCHESTRATION_ENABLED`
     + real trading false. Staging/production still reject the flag.
  2. One scan unit is tenant x approved compiled version x symbol. BTCUSDT is
     always first. `resolve_executable_strategy_policy` is the only target
     filter. Drafts, other tenants, and missing compiles never become targets.
  3. Evidence comes from `AssemblingWatcherScanEvidence`. Stale data and
     provider outages fail closed (`stale_evidence` / `provider_outage`). No
     second evaluator: `WatcherFusionEvaluationService` →
     `evaluate_canonical_strategy` → `evaluate_setup`.
  4. Candidate persist requires `ExecutablePolicyAuthority.PERSISTED_APPROVED_COMPILED`
     and the Watcher evaluation clock bound to evidence time. Kill switch does
     not block monitoring or Candidate persist; it must not invoke execution or
     Telegram.
  5. Idempotency key is `watcher-paper:{policy_id}:{symbol}:{closed_15m_end}` so
     subsequent bars can re-evaluate expiry. `WatcherOrchestrator` leases remain
     the single-active-worker fence.
- **Alternatives considered:** Enable staging Watcher flags (rejected: explicit
  do-not); put DB model imports in `app.watcher` (rejected: foundation isolation);
  invent a second setup evaluator (rejected).
- **Safety impact:** Paper monitoring only. Defaults stay disabled. No Telegram,
  no live orders, no real exchange credentials.
- **Consequences:** Status at `GET /watcher/paper-runtime/status`. Prometheus
  counters on the existing registry. Draft source PR only; no merge or deploy.

## AT-ADR-050 — Watcher PAPER MONITORING is runtime evidence, not configuration
- **Date:** 2026-09-21
- **Status:** Accepted (source PR #119 claimed AT-ADR-048 / AT-069; remapped)
- **Context:** Operators needed a truthful Watcher paper-monitoring surface.
  Two stacks exist: the legacy market-watcher scanner/bridge (disabled by
  default) and unused Phase 7 orchestration leases/health. Frontend flags
  must not be treated as RUNNING. No market-data authority or strategy
  evaluation authority belongs in this slice.
- **Decision:**
  1. `GET /market-watcher/monitoring` is a read-only aggregation of existing
     stores. Operator state is `RUNNING` | `STOPPED` | `DEGRADED` | `STALE` |
     `BLOCKED`.
  2. `RUNNING` requires live runtime evidence: a fenced orchestration lease
     with a fresh heartbeat, or a live watcher-worker heartbeat with both
     scanner and worker flags enabled. Scanner-only flags remain `STOPPED`.
     Orchestration enabled without a heartbeat is `STALE`. Kill switch and
     non-paper/real-trading posture are `BLOCKED`. Provider outage overlays
     `DEGRADED` only when runtime evidence already exists.
  3. SetupAssessment shown on the card is lineage from persisted Candidates
     (`CONFIRMED_SETUP`). This slice does not call `evaluate_setup` or mint
     Candidates.
  4. Dashboard, Decision (Plan), Strategy Lab, `/watcher`, and
     `/market-watcher` reuse one monitoring card. No duplicate Watcher
     workflow and no Watcher/Telegram/live-trading enablement.
- **Alternatives considered:** Infer RUNNING from frontend config (rejected:
  fake activity); create a second evaluator for SetupAssessment (rejected:
  AT-ADR-045/047); activate Watcher to populate the card (rejected:
  AT-ADR-040).
- **Safety impact:** Paper only. Watcher and Telegram stay disabled. No
  trades. No live trading. No canonical evaluator change.
- **Consequences:** Source branch `cursor/watcher_monitoring_ux-c026`. Draft
  source PR only; no merge or deploy.

## AT-ADR-051 — Paper Watcher stack has one evidence authority and one evaluator
- **Date:** 2026-09-21
- **Status:** Accepted
- **Context:** PR #118 (live monitor), PR #120 (paper Watcher runtime), and
  PR #119 (monitoring UX) landed as independent slices on overlapping IDs.
  Integration must not merge those source PRs, must not deploy, and must not
  activate Watcher. Dual market fetches and config-as-RUNNING were the
  remaining honesty risks.
- **Decision:**
  1. The live/read-only monitor is the current-quote and trade-stream gate
     (`watcher_evidence_error_for_monitor`). `FirstSliceEvidenceAssembler`
     remains the sole `CanonicalEvidenceWindowV1` producer. Shared source:
     replay monitor with replay assembler, live monitor with live assembler.
     Mode mismatch is `wrong_source`. Replay is allowed for deterministic
     tests and is never a live perpetual mark.
  2. Canonical authority is exactly: persisted APPROVED/ACTIVE strategy →
     executable compiled definition → gated canonical evidence →
     `evaluate_canonical_strategy` → SetupAssessment → `CONFIRMED_SETUP`
     only → Candidate. No second evaluator. In-memory policy authority
     cannot mint.
  3. Freshness clocks stay separate and fail closed: current quote, trade
     stream, closed-candle finality, historical evidence validity, setup
     lifetime. Existing safety thresholds are not weakened. No spot
     fallback. No fabricated prices. Provider outage and stale evidence
     refuse Candidate mint.
  4. Monitoring RUNNING requires fenced lease + fresh heartbeat. Paper poll
     is the next-scan basis when the paper worker is the live evidence.
     Configuration flags never project RUNNING. Replay/demo prices never
     appear as current live perpetual marks.
  5. Watcher, Telegram, and live trading stay off. Staging/production still
     reject Watcher activation flags. Dedicated activation remains a
     separate authorized task.
- **Alternatives considered:** Merge source PRs 118/119/120 (rejected:
  explicit do-not); dual assembler+monitor evidence windows (rejected: two
  authorities); treat config as RUNNING (rejected: AT-ADR-050); enable
  Watcher to populate monitoring (rejected: AT-ADR-040).
- **Safety impact:** Tightens fail-closed wiring. Does not enable Watcher,
  Telegram, or live trading. `EXECUTION_MODE=paper`,
  `ENABLE_REAL_TRADING=false`.
- **Consequences:** Integration branch `cursor/watcher_integration-b74b`.
  Draft integration PR only; no merge, deploy, or Watcher activation.

## AT-ADR-052 — Continuous paper evaluation is measurement, not a trading authority
- **Date:** 2026-09-21
- **Status:** Accepted
- **Context:** After AT-072, Watcher, eligibility, paper execution, Journal, and
  learning attribution exist as separate authorities. Operators need a
  continuous paper evaluation layer (win rate, expectancy, drawdown, MFE/MAE,
  conversion, false signals, blocked trades, human vs system, missed
  opportunities, data quality, strategy-version comparison) without creating
  another setup/Candidate/execution writer.
- **Decision:**
  1. `app.paper_evaluation` copies facts from existing authorities and rolls
     them up at query time. It does not evaluate setups, mint Candidates,
     authorize plans, or dispatch execution.
  2. Deterministic facts and AI narrative are siblings. Narrative is excluded
     from `content_hash`. Missed opportunities never invent counterfactual PnL.
  3. AI may emit `RefinementSuggestion` with `activate=false` and
     `auto_activate=false`. Activation is always forbidden.
  4. Operator summary is `GET /canonical/paper-evaluation/summary`.
     `watcher_activated` and `live_executable` stay false. Watcher, Telegram,
     and live trading stay off.
- **Alternatives considered:** Auto-promote a better strategy version (rejected:
  AT-ADR-026 / learning is review-only); treat Watcher config as RUNNING to
  populate metrics (rejected: AT-ADR-050); invent counterfactual missed PnL
  (rejected: not a recorded fact).
- **Safety impact:** Measurement only. Does not enable Watcher, Telegram, or
  live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Alembic revision `e3f4a5b6c7d8` (parent of the Telegram
  identity revision). Draft PR only; no merge, deploy, or Watcher activation.

## AT-ADR-053 — Telegram paper interaction is discussion, never trading authority
- **Date:** 2026-09-21
- **Status:** Accepted
- **Context:** After AT-072 the paper Watcher can persist CONFIRMED_SETUP
  Candidates, but operators still cannot discuss those events in Telegram
  without a paper-only interaction layer. Telegram security, Candidate alerts,
  and conversation foundations already exist and must be reused. A Telegram
  message must never become trading authority.
- **Decision:**
  1. `app.telegram_paper_agent` composes `TelegramSecurityProtocol` and
     `CandidateAlertGateway`. Watcher `PERSIST_AND_NOTIFY` stays
     `notify_disabled`. The paper worker may take an optional scan hook;
     default is no hook and no Telegram send.
  2. Meaningful alerts are CONFIRMED_SETUP with a persisted Candidate and
     fail-closed blocked scans (`stale_evidence`, `provider_outage`,
     `wrong_source`, `candidate_creation_failed`). Empty successful scans are
     not alerts. Journal outcomes use the same durable identity/outbox path.
  3. Inbound private-chat messages are authorized by
     `receive_private_message`. Discussion is read-only unless an exact issued
     nonce payload is presented. Bare `I confirm` is not mutation authority
     when multiple actions were presented.
  4. Telegram refuses: implicit strategy approval/compile/activate, SetupAssessment
     override, risk override, Candidate mint, live or paper order placement,
     live-trading enablement, `CLOSE`, `EXECUTE_PAPER_PLAN`. `APPROVE` remains
     protocol `AuthorizationIntent` only (`executes=false`).
  5. Dedup uses deterministic identity hashes and existing outbox idempotency
     (`paper-notify:`, `paper-thread:`, `candidate-alert:`). Delivery, retry,
     rate limits, and audit stay on the Telegram security store. Paper identity
     recovers from `telegram_paper_*` (Alembic `d9e0f1a2b3c4`).
  6. `TELEGRAM_INTERACTION_ENABLED` defaults false. Staging/production still
     reject the flag. No FastAPI webhook. No deploy. No merge.
- **Alternatives considered:** Enable Watcher `PERSIST_AND_NOTIFY` (rejected:
  AT-040/051; notify remains blocked); mount a Telegram webhook (rejected:
  HTTP adapter is a later slice); treat chat text as strategy or risk
  authority (rejected: Telegram is never trading authority).
- **Safety impact:** Paper discussion only. Does not enable Telegram, Watcher,
  or live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Alembic revision `d9e0f1a2b3c4` revises `e3f4a5b6c7d8`.
  Source PR #124 claimed AT-073 / AT-ADR-052; remapped because AT-073 is
  continuous paper evaluation. Branch `cursor/telegram_paper_agent-aac1`.
  Draft PR only; no merge, deploy, or Telegram activation.

## AT-ADR-054 — Evaluation and Telegram share one paper chain and no trading authority
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** PR #122 (continuous paper evaluation) and PR #124 (Telegram paper
  interaction) both sit on PR #121 and both claimed AT-073 / AT-ADR-052. Their
  Alembic revisions both revise `c8d9e0f1a2b3`. Operators need one linear
  schema and one path from a Watcher scan to measurement and to discussion.
- **Decision:**
  1. AT-073 / AT-ADR-052 remain continuous paper evaluation. Telegram paper
     interaction is AT-074 / AT-ADR-053. This integration is AT-075 / AT-ADR-054.
  2. Alembic is one chain: `c8d9e0f1a2b3` → `e3f4a5b6c7d8` (paper evaluation
     observations) → `d9e0f1a2b3c4` (paper Telegram identity).
  3. `WatcherPaperRuntime` records evaluation through the existing observer and
     then calls an optional scan hook. `build_paper_runtime` leaves that hook
     unset. `telegram_scan_hook` is the only composer from a scan report to
     `TelegramPaperAgent.project_watcher_notice`.
  4. Confirmed-setup discussion uses the evaluator's `WatcherDiscussionSnapshot`
     (the Candidate, SetupAssessment, and evidence window just persisted). It
     does not re-evaluate setup truth.
  5. `EvaluationLearningContext` reads `PaperEvaluationQueryService` and copies
     deterministic fact lines only. Narrative is excluded. Refinement lines
     keep `activate=false` and `auto_activate=false`.
  6. Telegram still cannot place orders, mint Candidates, override
     SetupAssessment or risk, approve or activate a strategy, or enable live
     trading. Evaluation still cannot activate a refinement. Risk BLOCK stays
     final. Watcher, Telegram, and live trading stay disabled.
- **Alternatives considered:** Two Alembic heads (rejected: deploy cannot pick
  one); install the Telegram hook inside `build_paper_runtime` (rejected:
  Telegram stays off and a recipient binding is not configuration); let
  Telegram text rewrite evaluation hashes (rejected: facts and narrative stay
  siblings).
- **Safety impact:** Measurement and discussion only. Does not enable Watcher,
  Telegram, or live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Draft integration PR only. Do not merge, deploy, or
  activate Watcher or Telegram.

## AT-ADR-055 — Final paper system keeps one authority per decision
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** PR #126 remediates Watcher freshness, product proof, unique
  worker identity, fencing, and live/replay mismatch on top of AT-072. PR #125
  adds paper evaluation and Telegram discussion on the pre-remediation Watcher.
  The final paper loop must use the remediated Watcher as the scan authority
  and attach evaluation and Telegram as advisory layers. Source PRs stay
  unmerged. Watcher and Telegram stay off. Live trading stays off.
- **Decision:**
  1. This composition is AT-076 / AT-ADR-055. Base is PR #126
     `75bee6d74c71edb39a73a5965784b6d388b7772d`. PR #125
     `189d9752ec3daaa512fbde42127ed321f11f5f61` is applied semantically. Neither
     source PR is merged.
  2. One market-evidence authority: the live monitor gates current quote and
     trade-stream freshness; `FirstSliceEvidenceAssembler` is the only
     `CanonicalEvidenceWindowV1` producer. PR #126 freshness contracts remain
     authoritative. Missing monitor is `missing_monitor`. Live/replay mismatch
     is `wrong_source`. Stale evidence and provider outage still fail closed
     before assembly.
  3. One strategy authority: persisted approved compiled policy
     (`ExecutablePolicyAuthority.PERSISTED_APPROVED_COMPILED`). In-memory
     policy cannot mint a Candidate.
  4. One SetupAssessment authority: `evaluate_canonical_strategy`. One
     Candidate authority: `CandidateLifecycleService`, and only on a genuine
     `CONFIRMED_SETUP`. Unique worker instance ids stay intact. The same owner
     cannot renew a lease without the current fencing token. The worker clock
     and the candidate-repository fence clock are the same clock passed to
     `build_production_canonical_runtime`.
  5. One automated paper-decision authority: ActionEligibility, then
     `CanonicalTradePlanService`, then paper execution, Journal, and
     attribution. Risk `BLOCK` is final. `paper_evaluation` reads deterministic
     facts only. AI refinement stays a suggestion (`activate=false`,
     `auto_activate=false`) and `refuse_activation` rejects activation.
  6. Telegram is an advisory interaction layer. `main()` does not install
     `telegram_scan_hook`. Telegram cannot mint a Candidate, override
     SetupAssessment, override risk, activate a strategy, place an order, or
     enable live trading. Learning discussion may load eligibility so the
     Telegram fact hash matches the canonical summary. Default loader is none.
  7. Alembic stays one chain: `c8d9e0f1a2b3` → `e3f4a5b6c7d8` →
     `d9e0f1a2b3c4`. No new revision.
  8. Watcher, Telegram, and live trading remain disabled. No exchange
     mutation, no real credentials, no deployment.
- **Alternatives considered:** Merge PR #125 and PR #126 (rejected: explicit
  do-not); let Telegram or evaluation write Candidates or strategy state
  (rejected: two authorities); weaken `wrong_source` into `stale_evidence`
  (rejected: PR #126 source gate stays authoritative); share a wall clock for
  the fence while the worker uses evidence time (rejected: the lease expires
  before Candidate persist).
- **Safety impact:** Composition only. Does not enable Watcher, Telegram, or
  live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Branch `cursor/final_paper_system_integration`. Draft PR
  only. Do not merge, deploy, or activate Watcher or Telegram.

## AT-ADR-056 — Staging USD-M evidence is public read-only, rollback is replay
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** The Binance USD-M public adapter, 10-second freshness rule, and
  canonical trade/CVD/OHLCV/coverage contracts already exist. Staging still
  intends `PERPETUAL_EVIDENCE_SOURCE=replay`, so operators have no controlled
  path to real read-only market evidence.
- **Decision:**
  1. The process default stays `replay` for local, CI, production, and
     rollback. Staging's intended source is `binance_usdm` on
     `https://fapi.binance.com`, declared in `.env.staging.example` and
     `render.yaml`. Applying it is a human environment change. This task does
     not edit a live platform environment and does not deploy.
  2. Selecting `binance_usdm` fails closed unless the profile holds: public
     USD-M origin only, no Binance or BloFin credentials, `exchange_mode`
     `paper_internal`, Watcher and Telegram flags false, paper execution, and
     not production. Spot, Coin-M, and plain HTTP are rejected. No fabricated
     fallback. BTCUSDT stays the default catalog symbol. Live quote freshness
     stays 10 seconds.
  3. `GET /health` and `GET /canonical/market-status` report the configured
     source, activation (`inactive` replay or `active` live), freshness, and
     that credentials and spot fallback are not used. Replay prices stay
     `replay_fixture`.
  4. Rollback is `PERPETUAL_EVIDENCE_SOURCE=replay` plus a restart.
     `scripts/validate-live-market-staging.sh` checks the profile offline and
     the running health payload. It does not write environment variables.
- **Alternatives considered:** Flip the code default to live (rejected: CI and
  deterministic tests would need the network); allow production to select the
  live source in the same change (rejected: activation is staging-only);
  keep a last price during outage (rejected: fail closed).
- **Safety impact:** Read-only public market data when a human applies the
  staging env change. Does not enable Watcher, Telegram, or live trading.
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Branch `cursor/activation_live_market`. Draft PR only.
  Do not merge, deploy, or activate Watcher or Telegram. Operators follow
  `docs/live_market_staging_activation.md`.

## AT-ADR-057 — Staging Watcher paper activation is a disarmed, fail-closed arm
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** The validated Watcher can scan locally in paper mode. Staging
  and production reject `WATCHER_ORCHESTRATION_ENABLED`. A controlled staging
  path is required for paper monitoring only, without Telegram, without live
  trading, and without turning the worker on in this change.
- **Decision:**
  1. `WATCHER_PAPER_STAGING_ACTIVATION` defaults false. Production rejects it.
     Staging rejects `WATCHER_ORCHESTRATION_ENABLED` unless this arm is set.
     The armed pair still requires live perpetual evidence. Replay, Telegram,
     the legacy scanner, and real trading still fail settings load.
  2. The API process does not autostart staging. Only
     `python -m app.workers.watcher_paper` calls preflight, and it scans only
     when every pin holds: unique worker id, PostgreSQL leases, fencing,
     restart recovery, idempotency, approved compiled lineage, canonical live
     evidence, freshness fail-closed, `CONFIRMED_SETUP` as the only Candidate
     authority, risk `BLOCK` final, paper execution, and the kill switch.
  3. The runtime health gate repeats those pins each cycle and stops the
     process on failure. Rollback prints an operator plan and does not deploy,
     edit environment files, clear the kill switch, or enable Telegram.
  4. This decision does not arm staging and does not modify staging environment
     values. Templates keep Watcher and Telegram false.
- **Alternatives considered:** Flip `WATCHER_ORCHESTRATION_ENABLED` in
  `render.yaml` (rejected: that is activation); allow replay evidence on the
  arm (rejected: live canonical evidence is required); autostart inside the
  API (rejected: the dedicated worker is the only start path).
- **Safety impact:** Paper only. `ENABLE_REAL_TRADING` stays permanently
  rejected. Default staging posture is unchanged until a human sets the arm.
- **Consequences:** Branch `cursor/activation-watcher-paper-5263`. Do not
  deploy or activate from this change. Source PR #130 recorded this decision as
  AT-ADR-056 before the live-market decision took that id. The accepted id
  on the integration branch is AT-ADR-057.

## AT-ADR-058 — Paper Telegram activation is armed only by an explicit local preflight
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** AT-074 and AT-076 can project a Watcher or Candidate event into
  the Telegram outbox and discuss it, but the production-shaped intake,
  backoff, health, and rollback path was still absent. Operators need that
  path before any controlled activation. Watcher and live trading stay off.
  Staging environment variables stay unchanged.
- **Decision:**
  1. `app.telegram_activation` is the only paper activation controller.
     `TelegramPaperActivation.arm` fails closed unless preflight passes.
     Default settings are not armable. `create_app` does not mount a webhook
     and does not start polling.
  2. Inbound mode is `off`, `polling`, or `webhook`, never both. Webhook
     requests must present `X-Telegram-Bot-Api-Secret-Token`. Polling uses an
     injected update source. HTTP clients refuse unless
     `TELEGRAM_NETWORK_PERMITTED=true`.
  3. Recipient binding, tenant isolation, outbox idempotency, send-ledger
     dedupe, retry backoff, expired-lease recovery, inbound and outbound rate
     limits, audit events, and delivery status stay on the existing security
     protocol plus the activation cursor and ledger.
  4. Paper mutations still require an explicit confirmation identity.
     Telegram cannot mint a Candidate, override SetupAssessment, override
     risk, activate a strategy, place an order, or enable live trading.
  5. Staging and production reject `TELEGRAM_PAPER_ACTIVATION_ARMED`,
     non-off `TELEGRAM_INBOUND_MODE`, `TELEGRAM_NETWORK_PERMITTED`, and a
     non-empty webhook secret. `render.yaml` is not modified.
  6. Rollback is a printed human checklist. The rollback command refuses
     `--apply` and does not edit environment files or deploy.
- **Alternatives considered:** Mount the webhook from `create_app` (rejected:
  activation must be explicit); enable Watcher `PERSIST_AND_NOTIFY` (rejected:
  notify stays blocked); send a real Telegram message during implementation
  (rejected: no test recipient was authorized for live delivery).
- **Safety impact:** Machinery only. Does not enable Watcher, Telegram, or
  live trading. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`.
- **Consequences:** Alembic `e0f1a2b3c4d5` revises `d9e0f1a2b3c4`. Branch
  `cursor/activation_telegram_paper-a361`. Draft PR only. Do not deploy or
  activate. Source PR #129 recorded this decision as AT-ADR-056. The
  integration branch assigns AT-ADR-058.
  AT-ADR-059 narrows staging rejection to incomplete arming flags. The
  historical source-PR validation claims are unchanged.

## AT-ADR-059 — One controlled paper activation package
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** PR #128 (live USD-M evidence), PR #129 (Telegram paper
  activation), and PR #130 (Watcher paper activation) were each validated
  separately. Their settings disagree: live evidence refused Watcher and
  Telegram, Watcher required live evidence with Telegram off, and Telegram
  preflight refused every non-local environment. Staging needs one package
  that can be armed in a fixed order and rolled back without deleting rows.
- **Decision:**
  1. Production still refuses `binance_usdm`, the Watcher arm, and every
     Telegram arming flag. Defaults stay disarmed. Replay remains the
     rollback and test source.
  2. Staging may select public `binance_usdm` evidence alone. The Watcher
     pair (`WATCHER_PAPER_STAGING_ACTIVATION` and
     `WATCHER_ORCHESTRATION_ENABLED`) may be set only with that live source,
     paper execution, `paper_internal`, and legacy scanner flags off.
  3. Staging may arm Telegram only as the full projection: the Watcher pair,
     `binance_usdm`, `TELEGRAM_PAPER_ACTIVATION_ARMED`,
     `TELEGRAM_INTERACTION_ENABLED`, inbound polling with an empty webhook
     secret or webhook with a secret of at least 32 characters, and non-empty
     `TELEGRAM_BOT_ID` and `TELEGRAM_CHAT_ID`. Alerts and automatic delivery
     stay false. `TELEGRAM_NETWORK_PERMITTED` is allowed only with that
     package. A partial flag is still rejected.
  4. The dedicated Watcher process installs the projection hook only when
     that package is on and a verified private binding exists. A missing
     binding refuses the start. The API does not autostart the worker.
  5. Telegram still cannot mint a Candidate, override SetupAssessment,
     override risk, activate a strategy, place an order, or enable live
     trading. Risk `BLOCK` and the kill switch stay final.
  6. Rollback is one printed checklist. It returns to paper execution, replay
     evidence, and no automated monitoring. It does not delete database rows,
     downgrade Alembic, edit env, or deploy. `--apply` exits 2.
  7. Alembic stays a single head: `e0f1a2b3c4d5` revises `d9e0f1a2b3c4`.
  8. `render.yaml` keeps the step-3 evidence blueprint and does not arm
     Watcher or Telegram. This decision does not deploy and does not change
     the live staging environment.
- **Alternatives considered:** Keep Telegram local-only (rejected: the
  package must project scans on staging); allow interaction without the
  Watcher arm (rejected: Telegram must not become a second scan authority);
  delete rows on rollback (rejected: audit and journal history stay).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`. `ENABLE_REAL_TRADING`
  stays false. No exchange credentials. No exchange mutation.
- **Consequences:** Operator procedure is `docs/controlled_paper_activation.md`.
  Branch `cursor/controlled_paper_activation_integration`. Do not merge and
  do not deploy from this change.

## AT-ADR-060 — Frontier paper runtime remediation
- **Date:** 2026-09-22
- **Status:** Accepted
- **Context:** The Frontier AT-080 audit on `070169c` found the paper package
  could not run a production-shaped Telegram process, could leak a bot token
  through HTTP client logs, and could repeat the same Binance aggTrade window.
  Staging also lacked a disarmed Watcher service, a single activation order,
  a kill-switch contract that matches Candidate and Telegram behavior, and
  health that reports the worker rather than only API flags.
- **Decision:**
  1. A dedicated process, `python -m app.telegram_activation run`, drains the
     durable outbox and polls. The Watcher only enqueues. Staging inbound is
     polling. Webhook remains for local tests and is not a staging activation
     path. `create_app` does not mount a webhook.
  2. Enrollment is `POST /telegram-paper/enrollment/start` plus a private-chat
     poll. The one-time token is not logged. The polling cursor is durable and
     does not move to another tenant. Restart resumes from the outbox and
     cursor. A Postgres runtime lease stops a second replica from sending.
  3. httpx and httpcore stay at WARNING. A stdlib redaction filter and
     formatter, plus the existing structlog processor, redact Telegram bot
     tokens in log lines, exception text, and trace-bound events. Application
     log level is unchanged.
  4. One closed aggTrade window is reused for evaluate and Candidate
     persistence. Public Binance reads use request weight, a sliding budget,
     Retry-After, bounded backoff, and process-local metrics. 418 and 451 are
     not retried. The Watcher lease TTL for `binance_usdm` is at least
     `timeout * 4 + max_backoff + 15` seconds, capped at 3600, and the lease
     heartbeats during a long read.
  5. `render.yaml` adds disarmed `alphatrade-watcher-paper-staging` and
     `alphatrade-telegram-paper-staging`. Flags stay false. No bot token is
     in the blueprint. This does not deploy.
  6. Armed Telegram projection requires polling, network permission, and a bot
     token together. Enrollment is the prior step and does not arm projection.
     An intermediate armed projection with network off fails Settings
     validation. A refused Watcher gate idles and heartbeats. It does not exit.
  7. For controlled automated paper operation the kill switch stops new
     Candidate mints, Watcher-started paper workflows, new Telegram enqueues,
     and delivery of queued automated messages. Monitoring continues.
     An unreadable switch is active. Rollback does not clear it.
  8. `GET /health` includes `worker_runtime` from `controlled_runtime_status`.
     A failed status read leaves liveness HTTP 200 with `available=false`.
  9. Alembic head `f1a2b3c4d5e6` revises `e0f1a2b3c4d5`. Live trading stays
     impossible.
- **Alternatives considered:** Keep delivery inside the Watcher (rejected:
  the Watcher would become the Telegram authority); use webhook on staging
  (rejected: the API does not mount it); cache evidence across tenants
  (rejected: the snapshot cache key includes organization id).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`. `ENABLE_REAL_TRADING`
  stays false. `EXCHANGE_MODE` stays non-live. Telegram stays advisory.
- **Consequences:** Operator procedure remains
  `docs/controlled_paper_activation.md`. Branch
  `cursor/activation_frontier_remediation`. Do not merge, deploy, or activate.

## AT-ADR-061 — Shared Binance evidence, disarmed worker boot, heartbeat health
- **Date:** 2026-09-23
- **Status:** Accepted
- **Context:** The independent review of PR #132 left three findings open.
  Canonical API reads built a new aggTrade cache and request-weight budget
  per HTTP request. Dedicated staging workers in `render.yaml` did not carry
  the Settings contract required to boot. Worker health treated a persisted
  runtime row as live without considering heartbeat age.
- **Decision:**
  1. Canonical API evidence reads use one process pool per Binance
     configuration: one bounded closed-window aggTrade cache and one sliding
     request-weight budget. The cache key is retrieval policy, symbol, and
     the UTC window. It does not include a tenant. Same-key fetches
     single-flight. A different semantic fingerprint replaces the rows and
     counts as a correction. Age equal to the TTL is still fresh. Age past
     the TTL is a miss and is not served. Failures are not cached, and an
     idle lock from a failed fetch is released. A process restart drops the
     pool. The default `resolve_perpetual_evidence_source` stays isolated;
     sharing is opt-in for the API process.
  2. While a market-request progress hook is bound, a slow GET pulses that
     hook during the HTTP call, including a successful call, a 429, and a
     network timeout. The hook is captured on the caller thread. Backoff
     still uses the existing progress sleep. A Watcher lease can be renewed
     only while its fence is still active.
  3. `alphatrade-watcher-paper-staging` and
     `alphatrade-telegram-paper-staging` include the staging cookie, CORS,
     denylist, rate-limit, and trusted-proxy settings. `TELEGRAM_INBOUND_MODE`
     is the quoted string `off`. Secrets stay `sync: false`. Both workers
     stay disarmed. This blueprint change does not deploy.
  4. Worker component health is `RUNNING` only when the heartbeat age is
     within `watcher_heartbeat_stale_after_seconds` (default 90, inclusive).
     A missing heartbeat is `UNAVAILABLE`. A future, naive, or older
     heartbeat is `STALE`. `available` on the component is true only for
     `RUNNING`. A persisted activation state of `running` is not reported as
     running when the heartbeat is not fresh. The parent
     `worker_runtime.available` flag still means the status read succeeded.
     Post-activation smoke checks `health_state`, age, and the threshold.
  5. No Alembic revision. Head remains `f1a2b3c4d5e6`. Live trading stays
     impossible.
- **Alternatives considered:** Share the cache by default for every
  `resolve_perpetual_evidence_source` call (rejected: existing isolated
  reads and tests must stay isolated); treat any persisted row as RUNNING
  (rejected: that was the finding).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. `EXCHANGE_MODE` stays `paper_internal`
  in the worker blueprint. Neither worker is armed.
- **Consequences:** Branch `cursor/final_three_activation_fixes`. Do not
  merge, deploy, or activate.

## AT-ADR-062 — Disarmed Render workers boot before operational secrets
- **Date:** 2026-09-23
- **Status:** Accepted
- **Context:** AT-082 put the staging Settings contract on
  `alphatrade-watcher-paper-staging` and `alphatrade-telegram-paper-staging`,
  but Settings still required PostgreSQL, Redis, JWT, OpenAI, and Qdrant
  before a disarmed worker could start. Those values are `sync: false` in
  `render.yaml`. Injecting placeholders would be a fake secret. Skipping the
  same checks for every staging process would weaken the API.
- **Decision:**
  1. Watcher and Telegram process startup read the disarmed paper posture
     before Settings applies operational-dependency checks. The role is
     process-local. It is not an environment variable.
  2. While that role is bound and the constructed settings are staging,
     paper, `paper_internal`, with Watcher and Telegram arms off and no bot
     token, Settings may be built without `DATABASE_URL`, `REDIS_URL`,
     `JWT_SECRET`, `OPENAI_API_KEY`, or `QDRANT_URL`. The process then idles
     `disarmed` and does not open those dependencies.
  3. Cookie, CORS, denylist, rate-limit, trusted-proxy, paper-only, and
     `provider_mode!=mock` checks still run. JWT minimum length still runs.
     A short secret is rejected. The API calls `Settings()` with no worker
     role, so a literal API blueprint without secrets still fails.
  4. If either worker is armed, the role is not bound. Missing PostgreSQL,
     Redis, JWT, provider dependencies, or—for Telegram—the bot token still
     fail closed. Real trading stays impossible.
- **Alternatives considered:** Put placeholder secrets in `render.yaml`
  (rejected: fake credentials). Relax `validate_deployment_settings` for
  every disarmed staging process (rejected: the API blueprint is also
  disarmed and must keep failing closed).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. Neither blueprint worker is armed.
  No deploy and no activation.
- **Consequences:** Branch `cursor/final_render_worker_boot_fix`, based on
  `5ff0eb8c4a7d171118dfde921259a6da13b60abb`. Do not merge, deploy, or
  activate.

## AT-ADR-063 — Staging Blueprint omits the Slice 59 worker
- **Date:** 2026-09-23
- **Status:** Accepted
- **Context:** The paper runtime is the Vercel frontend plus three Render
  processes: `alphatrade-api-staging`, `alphatrade-watcher-paper-staging`,
  and `alphatrade-telegram-paper-staging`. `render.yaml` also defined
  `alphatrade-worker-staging` (`python -m app.workers.entrypoint`).
- **Decision:** Remove `alphatrade-worker-staging` from `render.yaml`. The
  application module stays. Nothing in the paper runtime starts that Render
  service.
  1. `app.workers.entrypoint` scans with the legacy setup detector, records
     `SetupDetectionRecord`, drains one queued backtest, and emits
     `WorkerNotifier` alerts. It does not start Watcher or Telegram.
  2. The paper scan authority is `python -m app.workers.watcher_paper`.
     It evaluates the approved compiled strategy and can mint a Candidate.
     Its evaluation observer records paper-evaluation facts. Legacy
     `MARKET_WATCHER_*` flags stay false.
  3. The paper Telegram authority is `python -m app.telegram_activation run`.
     It drains the outbox and polls. It does not mint Candidates.
  4. Paper execution, Journal projection, and learning attribution run
     through `ExecutionService` / `CanonicalPaperExecutionService` and
     `JournalLifecycleProjector`. The Slice 59 entrypoint does not call
     them. Watcher orchestration ports do not invoke execution or journal.
  5. `ENABLE_PAPER_SCHEDULER` stays false. The paper scheduler has no
     background loop. The Slice 59 entrypoint does not tick it.
  6. With `WORKER_ENABLED` false, queued backtests drain in the API via
     FastAPI `BackgroundTasks`. The API blueprint does not enable the worker.
- **Alternatives considered:** Keep the disarmed Slice 59 service in the
  Blueprint (rejected: it has no unique paper-runtime responsibility and
  adds a paid worker). Delete `app.workers.entrypoint` (rejected: this
  change is the Blueprint only).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. Watcher and Telegram stay disarmed.
  No secrets are added. No deploy and no activation.
- **Consequences:** Branch `cursor/render_blueprint_final_cleanup`, based on
  `6314a2021441384b9a1bb20f6c0b82916b15f6bd`. Do not deploy or activate.

## AT-ADR-064 — One supervised paper worker
- **Date:** 2026-09-23
- **Status:** Accepted
- **Context:** The staging blueprint ran Watcher and Telegram as two Render
  workers plus the API. The paper path needs both runtimes, and a third
  compute service is not required for that split.
- **Decision:**
  1. `render.yaml` defines exactly `alphatrade-api-staging` and
     `alphatrade-paper-worker-staging`. Both stay on the Starter plan. The
     paper worker command is `python -m app.workers.paper_worker`. The
     dedicated Watcher service, the dedicated Telegram service, and the
     Slice 59 worker are not Blueprint services.
  2. The paper worker supervises the existing Watcher runtime and the
     existing Telegram runtime. Each has its own thread, health record, and
     failure counter. Settings objects are copied before either runtime is
     built. A Telegram exception does not rewrite Watcher health. A Watcher
     exception or authority-flag change is reverted on that copy and does
     not change the Telegram copy.
  3. Scan leases and the Telegram runtime lease, cursor, and outbox stay in
     the existing runtimes. The supervisor starts one loop of each. It does
     not call `app.workers.entrypoint` and it does not mount the API.
  4. Disarmed boot uses `WorkerBootRole.PAPER_WORKER` and does not open
     PostgreSQL, Redis, or a bot token. Armed boot does not bind that role,
     so the existing dependency and secret checks stay mandatory.
  5. No deploy and no activation. Live trading stays impossible.
- **Alternatives considered:** Keep two worker services (rejected: the cost
  target is two Render compute services). Fold the worker into the API
  (rejected: a worker failure must not take down the API, and the API must
  not gain the worker's process role). Run the two runtimes as subprocesses
  of the supervisor (rejected: health and shutdown stay simpler in one
  process when each runtime already fences itself in PostgreSQL).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. The blueprint worker stays disarmed.
  Render's published Starter price is $7 per month for a web service and $7
  per month for a background worker (512 MB, 0.5 CPU), so the two compute
  services are $14 per month before workspace, PostgreSQL, Redis, and
  bandwidth. Those other charges are not set by this blueprint.
- **Consequences:** Branch `cursor/consolidated-paper-worker-37fe`, based on
  `8a2512c85d8c876576410e9de300310f720b37cb`. Do not deploy or activate.

## AT-ADR-065 — Stored BloFin secrets stay sealed in paper isolation
- **Date:** 2026-09-24
- **Status:** Accepted
- **Context:** Staging Settings validation rejected
  `PERPETUAL_EVIDENCE_SOURCE=binance_usdm` whenever `BLOFIN_API_KEY`,
  `BLOFIN_API_SECRET`, and `BLOFIN_API_PASSPHRASE` were present, even with
  `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`,
  `EXCHANGE_MODE=paper_internal`, and `BLOFIN_DEMO_ENABLED=false`.
- **Decision:** Credential storage, loading, client construction, and
  execution authorization are separate. Stored BloFin secrets may remain
  while paper isolation is active. They are not loaded into an authenticated
  client, are not sent on Binance USD-M evidence requests, and cannot place,
  cancel, or modify positions. The loader opens only for the complete demo
  execution gate (paper execution, real trading off, `paper_exchange_demo`,
  demo enabled, all three secrets, allowlisted demo host, and evidence that
  is not Binance USD-M). Any incomplete gate fails closed. Binance public
  evidence still refuses Binance credentials. Real trading stays disabled.
- **Alternatives considered:** Delete the credential-presence check only
  (rejected: presence would still be treated as access). Wipe Render secrets
  (rejected: storage is not execution authority).
- **Safety impact:** Paper only. No deploy. No live trading. No BloFin demo
  activation.
- **Consequences:** Branch `cursor/execution-credentials-isolation-1e5a`.
  Tests in `backend/tests/test_execution_credential_isolation.py`.

## AT-ADR-066 — Binance HTTP 418 is a temporary ban, not a dead monitor
- **Date:** 2026-09-24
- **Status:** Accepted
- **Context:** From Render Frankfurt, `fapi.binance.com` intermittently returns
  HTTP 418. The read-only client treated 418 like 401/403/451
  (`RegionalProviderFailureError`) and did not retry. A failed or incomplete
  reconnect backfill then set `GapState.UNRECOVERABLE`, and
  `SymbolMonitorRuntime` refused every later fetch until process restart.
- **Decision:**
  1. HTTP 418 is `UpstreamBanError`, a temporary IP ban. Retry only when
     another attempt remains and Retry-After fits `max_backoff_seconds`.
     A longer Retry-After is not followed by another request. The monitor
     waits out Retry-After up to Binance's 3-day ban ceiling. HTTP 401, 403,
     and 451 stay regional failures with no retry. HTTP 429 stays
     `RateLimitedError`. There is no spot or fabricated fallback.
  2. An empty or incomplete backfill stays on the same cursor and connection
     epoch, records the gap, and does not publish a usable price. A later
     attempt runs only after backoff. A complete backfill can recover without
     a process restart. A process restart still opens a new epoch.
  3. No deploy. Watcher and Telegram stay disarmed. Live trading stays off.
     No Alembic revision.
- **Alternatives considered:** Retry 418 on the same schedule as 429 even when
  Retry-After exceeds the cap (rejected: that sends another request during
  the ban). Keep the unrecoverable latch and rely on process restart
  (rejected: one empty backfill then kills the monitor for the life of the
  process).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. Evidence stays fail-closed while
  coverage is incomplete.
- **Consequences:** Branch `cursor/binance-418-recovery-11ce`. Tests in
  `backend/tests/test_binance_usdm_staging_reliability.py`. Do not deploy
  or activate.

## AT-ADR-067 — Telegram paper discussion reads durable facts
- **Date:** 2026-09-25
- **Status:** Accepted
- **Context:** The staging paper worker can enroll, poll, and deliver, but the
  armed projection built an empty in-memory discussion context. Journal and
  learning replies could not see recorded paper facts. Inbound discussion also
  ignored the latest stored Candidate unless the caller passed an assessment.
- **Decision:**
  1. The controlled projection uses PostgreSQL journal, paper-position, and
     strategy reads, plus paper-evaluation facts for learning text. It does
     not invent a SetupAssessment or evidence window.
  2. Inbound discussion may attach the latest stored Candidate for that
     organization. Without an assessment body, the reply cites stored
     Candidate facts only.
  3. A numeric `TELEGRAM_BOT_ID` must match the bot user id prefix of
     `TELEGRAM_BOT_TOKEN`. A mismatch fails closed and is not logged with
     the token. Opaque non-numeric ids stay valid for tests.
  4. Staging activation is the combined paper worker after Watcher health is
     fresh. Do not run a second Telegram process. Do not deploy or arm from
     this change. `render.yaml` stays disarmed.
- **Alternatives considered:** Keep the empty in-memory context and require
  operators to patch it (rejected: journal and learning replies would stay
  empty). Reconstruct SetupAssessment from Candidate hashes (rejected: that
  invents setup truth).
- **Safety impact:** Paper only. Telegram still cannot place an order, mint a
  Candidate, override risk, or enable live trading.
- **Consequences:** Operator steps are in `docs/telegram_paper_mvp_activation.md`.
  Do not deploy or activate.

## AT-ADR-068 — Bybit USDT perpetual is the explicit secondary evidence source
- **Date:** 2026-09-25
- **Status:** Accepted
- **Context:** Render Frankfurt is HTTP 418 banned by Binance USD-M, so paper
  staging cannot depend only on `fapi.binance.com`. The canonical contract
  still needs perpetual price, trades, OHLCV, volume, taker-side CVD, freshness,
  and gap detection, each with its own provenance. An earlier draft of this
  change used OKX; that source is not the MVP secondary.
- **Decision:**
  1. Primary stays `binance_usdm`. Optional secondary is `bybit_usdt_perpetual`
     (public linear `BTCUSDT` only, base-coin size, taker side). Selecting
     `bybit_usdt_perpetual` as the primary is also legal. Replay stays the
     default and the rollback. Spot, inverse, and fabricated rows stay rejected.
  2. A primary 418, 429, or regional failure switches the whole active
     instrument and opens a new connection epoch. The failed primary payload
     is not published. Recovery returns to Binance only after its status is
     healthy, again on a new epoch. The two venues are never merged into one
     CVD window.
  3. Bybit public recent trades that do not reach the requested start fail
     closed. `seq` is a cross sequence and is not used as a per-trade id.
     A later read on the same connection that drops the last proven print
     fails closed. No missing prints are invented. Staging blueprint sets the
     secondary and does not arm Watcher, Telegram, or real trading. This
     decision does not deploy.
- **Alternatives considered:** Keep OKX as the secondary (rejected: the
  required secondary is Bybit). Relabel Bybit prints as Binance (rejected:
  incompatible books). Use spot tickers (rejected).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. Binance GET allowlist is unchanged.
- **Consequences:** Branch `cursor/okx-perp-evidence-failover-98bd`. Tests in
  `backend/tests/test_bybit_usdt_perpetual_evidence.py`. No Bybit credentials.

## AT-ADR-069 — Confirmed Watcher setup continues to one internal paper fill
- **Date:** 2026-09-25
- **Status:** Accepted
- **Context:** The activation runbook expects a genuine Watcher `CONFIRMED_SETUP`
  to reach an open paper Journal trade. The worker stopped after the Candidate.
  Eligibility, the canonical TradePlan, and the paper fill were composed only
  in tests.
- **Decision:**
  1. `AutomatedPaperLoop` sequences the existing ActionEligibility service,
     `CanonicalTradePlanService`, hash-bound API approval, and internal paper
     fill. It is not a second setup or risk authority.
  2. The loop runs only for paper mode, `ENABLE_REAL_TRADING=false`, and
     `EXCHANGE_MODE=paper_internal`. The fill source is `paper_internal`.
     Replay, `WATCH`, `NO_SETUP`, stale evidence, provider outage, a wrong
     tenant, a wrong strategy lineage, risk `BLOCK`, and the kill switch
     create no fill.
  3. One Candidate produces at most one plan and one fill. A duplicate scan
     and a later call with the same Candidate converge on that journal trade.
  4. This change does not arm Watcher, Telegram, or live trading, and does
     not add an Alembic revision.
- **Alternatives considered:** Keep the manual test composition as the
  production path (rejected: the activation runbook cannot observe an open
  journal trade). Invoke Watcher side-effect ports from the orchestrator
  (rejected: those ports stay unused by orchestration).
- **Safety impact:** Paper only. Risk `BLOCK` stays final. No exchange
  mutation and no Telegram execution authority.
- **Consequences:** Focused tests live in
  `backend/tests/test_automated_paper_loop.py`. Do not deploy or activate.

## AT-ADR-071 — Bybit forward trade tail is retained on the connection
- **Date:** 2026-09-25
- **Status:** Accepted
- **Context:** Staging produced one Bybit BTCUSDT observation, then later
  reads raised `IncompleteTradeWindowError`. Each recent-trade response was
  required to reach the original window start. The public buffer only returns
  the latest prints, so a later page can still contain the last proven
  execution id after it has dropped that prefix.
- **Decision:** The first page on a connection must reach the requested start.
  That proven tail is kept for the connection. A later page extends it only
  when it still contains the last proven execution id. The returned window is
  the retained tail clipped to the request. A page that drops the watermark
  fails closed. Trades are not invented. A new connection id proves its prefix
  again.
- **Alternatives considered:** Treat every short page as a complete window
  (rejected: that hides a missing prefix). Fill a dropped watermark with
  synthetic prints (rejected: fabricated evidence).
- **Safety impact:** Paper only. Spot rejection, Binance primary, and Bybit
  secondary are unchanged. Real trading stays false.
- **Consequences:** Tests in `backend/tests/test_bybit_usdt_perpetual_evidence.py`.
  No deploy, no Watcher arm, no Telegram activation.

## AT-ADR-070 — Failover perpetual evidence reports the active market-data kind
- **Date:** 2026-09-25
- **Status:** Accepted
- **Context:** Staging startup registers the perpetual evidence source through
  `_PerpetualEvidenceAdapter`, which copies `inner.kind`. `FailoverPerpetualSource`
  had a venue `name` and no provider kind, so Render raised
  `AttributeError: 'FailoverPerpetualSource' object has no attribute 'kind'`
  while building the default provider registry. Binance USD-M and Bybit USDT
  perpetual sources already report `ProviderKind.MARKET_DATA`.
- **Decision:** `kind` is the provider capability, not the venue. The failover
  wrapper adopts `kind` from the active source and updates it on switch and on
  return to the primary. Construction requires both sources to report
  `ProviderKind.MARKET_DATA`. A hardcoded kind that ignores the sources is not
  the contract. Venue provenance stays on the instrument and source identity.
- **Alternatives considered:** Set `kind = ProviderKind.MARKET_DATA` on the
  wrapper without reading the sources (rejected: that only hides the startup
  error). Make the registry skip a missing kind (rejected: the source must
  satisfy the provider interface).
- **Safety impact:** Paper only. No change to Watcher authority, Telegram,
  spot rejection, or fabricated-evidence rejection.
- **Consequences:** `PerpetualMarketSource` requires `kind`. Regression coverage
  builds `build_default_registry` and `create_app` with
  `PERPETUAL_EVIDENCE_SOURCE=binance_usdm` and
  `PERPETUAL_EVIDENCE_SECONDARY_SOURCE=bybit_usdt_perpetual`.

## AT-ADR-072 — Telegram enrollment ignores unrelated organization kill switches
- **Date:** 2026-09-26
- **Status:** Accepted
- **Context:** Telegram enrollment posture called `read_process_kill_switch()`,
  which is true when any `KillSwitchState` row is active. One tenant's switch
  paused enrollment for every tenant (`telegram_runtime_state=paused`,
  `last_error_code=kill_switch_active`) even after the enrolling organization's
  switch was deactivated. Enrollment does not mint Candidates, start Watcher
  paper workflows, enqueue automated notices, or deliver them.
- **Decision:**
  1. Enrollment pauses only for `Settings.global_kill_switch_active`. A missing
     settings object or a failed read of that flag fails closed.
  2. Organization kill switches stay tenant-scoped for Watcher paper scans,
     automated paper actions, and Telegram delivery bound to that organization.
     `read_process_kill_switch()` remains the any-tenant fail-closed reader and
     is not used for enrollment.
  3. Real trading stays impossible. This change does not arm live orders.
- **Alternatives considered:** Keep the any-tenant pause for enrollment
  (rejected: it blocks unrelated tenants). Also refuse enrollment when the
  token's own organization switch is active (rejected: enrollment is not an
  automated paper action, and delivery still checks the bound tenant at send
  time). Scope `read_process_kill_switch()` itself to one tenant (rejected:
  that function's contract is the process-wide gate).
- **Safety impact:** Paper only. Bound-tenant delivery and Watcher scans still
  stop when that tenant's switch is active or unreadable. The ops global
  switch still pauses enrollment.
- **Consequences:** Regression coverage is
  `backend/tests/test_telegram_enrollment_kill_switch_scope.py`. No deploy.

## AT-ADR-073 — Watcher scans release the trade tape after the proof is bound
- **Date:** 2026-09-28
- **Status:** Accepted
- **Context:** The consolidated paper worker (Watcher plus Telegram) exceeded
  the 512 MB Render limit during Watcher scans and was temporarily moved to
  2 GB. CPU stayed low. The scan fetches about eight hours of aggTrades and
  was retaining every trade as a `TradeEvent`, plus raw cache copies and a
  monitor book that grew for the life of the process.
- **Decision:**
  1. Binance scan retrieval streams aggTrades, binds the same coverage hash
     and closed-bar Decimal sums, and stores `trades=[]` with a
     `ReleasedTradeTape`. Replay fixtures keep the full trade list. CVD,
     signed flow, and bearish-divergence sums read the tape when trades are
     absent. The 32-bar lookback is unchanged.
  2. Raw aggTrade cache entries larger than
     `binance_evidence_cache_max_rows` (default 4096) are not stored. The
     reduced snapshot for a closed window is cached instead.
  3. The market monitor keeps coverage and CVD totals for the latest window
     and drops trades older than `last_event_at`. Reconnect clears the book.
  4. The worker logs RSS, sets Prometheus gauges, and writes nullable
     `process_rss_bytes` / `process_rss_peak_bytes` (`BigInteger`) on
     `controlled_runtime_status`. Alembic head `a8c3e1b94d20` revises
     `f1a2b3c4d5e6`. No secrets are included.
  5. Real trading stays impossible. Render is not changed by this decision.
- **Alternatives considered:** Shorten the CVD lookback (rejected: that
  changes the strategy). Keep the 2 GB tier without a code change (rejected:
  the spike is retention, and a streamed tape stayed flat in measurement).
- **Safety impact:** Paper only. `EXECUTION_MODE=paper`.
  `ENABLE_REAL_TRADING` stays false. Risk `BLOCK` stays final.
- **Consequences:** The source branch recorded this decision as `AT-ADR-072`.
  The release candidate assigns `AT-ADR-073` because `AT-ADR-072` is the
  Telegram enrollment scope decision. Apply `a8c3e1b94d20` before the paper
  worker starts. Staging can retest the 512 MB worker after a human deploy.
  A 10-minute monitor stall can still materialize that short window. Live
  BTCUSDT trade counts were not measured from this environment (HTTP 451).
  This decision does not deploy and does not migrate the staging database.

## AT-ADR-074 — Interactive agent orchestrates existing authorities
- **Date:** 2026-09-28
- **Status:** Accepted
- **Context:** The product is moving toward an Agent surface. Conversation
  transcripts, strategy previews, journal rows, knowledge chunks, portfolio
  reads, and coaching summaries already exist. A second store, or free-form
  chat that writes those records, would split authority.
- **Decision:**
  1. `InteractiveAgentService` is an orchestration layer. It appends turns to
     the existing conversation transcript and reads strategy, knowledge,
     journal, portfolio, performance, coaching, and watcher records.
  2. A turn may store a structured proposal. It does not confirm that proposal.
     Chat text that says "I confirm" does not write domain rows.
  3. Explicit confirm applies only a complete journal proposal, through
     `JournalService`. Strategy, rule, lesson, and trade-decision confirms stay
     `confirmed_unapplied`. Strategy preview rows remain `DRAFT`.
  4. Screenshot analysis and voice input/output are contracts only. No image or
     audio is interpreted.
  5. The agent cannot enable real trading. It does not call execution.
- **Alternatives considered:** Route the new surface through the existing
  LangGraph graph (rejected for this slice: that graph can still plan trades).
  Add a new proposal table (rejected: the transcript and strategy preview
  tables already store these drafts).
- **Safety impact:** Paper only. No Render change, no deploy, no live-trading
  flag. Risk `BLOCK` and execution services are not invoked.
- **Consequences:** The source branch recorded this decision as `AT-ADR-072`.
  The release candidate assigns `AT-ADR-074` because `AT-ADR-072` is Telegram
  enrollment scope and `AT-ADR-073` is the trade-tape release. Tests in
  `backend/tests/test_interactive_agent_foundation.py`. Contract notes in
  `docs/interactive_agent_foundation.md`.

## AT-ADR-075 — Release candidate keeps the conversational model beside proposals
- **Date:** 2026-09-28
- **Status:** Accepted
- **Context:** PR 145's Agent workspace talks to `/chat`. PR 147's agent turns
  reply with deterministic templates and read an injected ticker. Integrating
  those branches by switching the workspace onto the templates would replace
  the conversational model. Three source branches also appended `AT-ADR-072`.
- **Decision:**
  1. `POST /agent/turns` asks the existing `ModelRouter` purpose
     `general_agent_synthesis` for the assistant prose. If that call is
     unavailable, the transcript says the conversational model reply is
     unavailable. Recorded facts are appended and are not a confirmation.
  2. Market answers use `CanonicalEvidenceService`. Unavailable and stale
     reads stay labeled. No price is invented and `fallback_used` stays false.
  3. Strategy and journal proposals are not confirmed by model text or by
     sending a message. Journal rows are written only by the explicit confirm
     route. A confirm that finds the proposal's journal row returns that row
     and does not insert another. A confirm whose payload says applied, but
     whose journal row is missing, writes exactly one row. Rejected and
     unauthorized statements write none. Strategy confirm stays
     `confirmed_unapplied`.
  4. Screenshot and voice routes stay unimplemented.
  5. Decision identifiers on this candidate are `AT-ADR-072` enrollment,
     `AT-ADR-073` trade-tape release, and `AT-ADR-074` agent orchestration.
     Apply Alembic `a8c3e1b94d20` before the paper worker starts. This
     decision does not migrate staging.
- **Alternatives considered:** Point the workspace only at `/chat` (rejected:
  structured proposals would stay disconnected). Replace replies with the
  PR 147 templates (rejected: that removes the conversational model).
- **Safety impact:** Paper only. Real trading stays disabled. No deploy, no
  Render change, no additional symbols, no kill-switch or Telegram arm.
- **Consequences:** `docs/interactive_agent_foundation.md` and
  `docs/controlled_paper_activation.md`. PR 148 remains excluded.

## AT-ADR-076 — Paper Watcher uses five logical symbol slots
- **Date:** 2026-09-30
- **Status:** Accepted
- **Context:** The paper Watcher treated BTCUSDT as the only market identity.
  PR 148 gated extra symbols behind a flag and still forced BTCUSDT into the
  active set. Live Binance and Bybit contract checks were not reachable from
  this environment (HTTP 451 and HTTP 403).
- **Decision:**
  1. One paper Watcher process owns five ordered slots. The default symbols are
     BTCUSDT, ZECUSDT, ETHUSDT, TAOUSDT, and HYPEUSDT. Operators can enable,
     disable, replace, and reorder them through `PUT /watcher/watchlist`
     without a code deployment. The file store is `var/watcher-watchlist.json`
     unless `WATCHER_WATCHLIST_PATH` is set. No Alembic migration is added.
  2. A symbol is scan-eligible only when the active evidence venue has a
     verified linear USDT perpetual contract for that exact symbol. The
     in-repo book verifies BTCUSDT on Binance USD-M and Bybit linear. Other
     symbols stay unavailable. A payload for a different symbol is rejected.
  3. Generic market identity follows the symbol being evaluated. The canonical
     first-slice evaluator still defaults to BTCUSDT when no symbol is passed,
     and compiled BTC strategies still match only BTCUSDT. No new setup rules
     are added.
  4. Symbols are scanned one at a time. One failure does not stop the others.
     Historical trade windows are released after each symbol. Risk BLOCK and
     the kill switch stay in force. Real trading stays disabled.
- **Alternatives considered:** Cherry-pick PR 148 (rejected: it still forced
  BTC as the only active identity). Mark ZEC, ETH, TAO, and HYPE as verified
  without a provider payload (rejected: those listings were not verified here).
- **Safety impact:** Paper only. No Render deploy, no Telegram arm, no live
  orders, no shared database migration.
- **Consequences:** `GET /watcher/watchlist` is configuration.
  `GET /watcher/watchlist/status` is per-symbol runtime status. Settings shows
  the five slots.

## AT-ADR-077 — USD-M contract checks must not treat a blocked host as a delisting
- **Date:** 2026-09-30
- **Status:** Accepted
- **Context:** PR 151 left ETHUSDT, ZECUSDT, TAOUSDT, and HYPEUSDT as
  `contract_unverified` because `fapi.binance.com` returned HTTP 451 and
  `api.bybit.com` returned HTTP 403. `www.binance.com/fapi/v1/exchangeInfo`
  returned a `futuresType=U_MARGINED` book. That host ignores the symbol query
  and returns the full book, whose first row is BTCUSDT.
- **Decision:**
  1. Select the exchangeInfo row with the exact symbol. A different row is not
     a substitute.
  2. The five Binance USD-M perpetual rows proven on 2026-09-30 are the catalog
     the Watcher may scan. A later USD-M payload replaces a row. A missing row
     in a successful book is `unsupported_contract`.
  3. HTTP 451, 403, a redirect, or a transport failure is
     `provider_unreachable`. It does not delete a proven contract and it does
     not invent a Bybit listing.
  4. Live candle reads stay on `https://fapi.binance.com`. This decision does
     not change execution mode, real trading, Telegram, or the kill switch.
- **Safety impact:** Paper only. No deploy.
- **Consequences:** `backend/src/app/market_contracts/contract_discovery.py`.




## AT-ADR-WATCHER-TENANT-20260930 — organization-owned durable Watcher state

Status: accepted by the PR152 remediation contract; implementation under validation.

The five-slot process file could cross tenant boundaries and did not connect API and dedicated worker services. Watcher configuration now belongs to an organization in the existing SQLAlchemy database, with monotonic revisions. Latest per-symbol observations and the legacy runtime summary carry that organization, revision and observation time; stale or old-revision observations are pending, and obsolete symbol rows are removed. The API derives ownership from authenticated membership. Optimistic configuration updates and revision-fenced worker publication prevent stale writers.

The worker visits organizations in deterministic round-robin order, loading one organization per bounded cycle from the shared database. Per-symbol market composition and latest status remain bounded; no tenant strategy data enters shared public-market caches. Configuration changes are observed on the tenant's next turn; delayed/missing worker observations expire visibly. Exact provider contract verification gates acquisition, and no-strategy monitoring reads only a bounded closed-candle sample without producing Candidates. This probe is not current-price or full CVD evidence.

Existing activation, kill-switch, paper execution and Telegram tenant controls remain authoritative. Migration b6f2d9a10e73 is for isolated validation only in this task; shared database rollout, merge and deployment require their existing human gates. Acceptance requires regression tests, exact-revision CI and a fresh independent review.


## AT-ADR-WATCHER-BYBIT-CONTINUITY-20260930 — bounded ranks and lineage proofs

Status: PR152 independent-review correction under validation.

The organization-owned database decision above supersedes AT-ADR-076's historical process-file authority. The legacy path setting remains only for local/test helpers.

Bybit execution ranks now identify observations of one exact symbol/source independently of a caller's retrieval lineage. The persistent monitor and historical canonical assembly keep separate proof tails (at most two recent lineages). A failed historical request cannot reset the monitor's sequence. The execution ledger retains only the current provider page plus those bounded proofs, including signatures to reject changed duplicate executions. Bulk cleanup trims each proof to one provider page, preserving overlap identity and anchors. During reads, each proof has a ten-page and 15-minute bound. Discarded prefixes, including any partially retained millisecond bucket, are unavailable rather than fabricated complete coverage. Gaps, wrong instruments/sources and freshness checks remain authoritative. This bound is not a staging RSS claim.

Contract eligibility refusals cross the strategy evidence boundary as typed errors retaining unsupported versus unreachable reasons, matching the existing read-only probe path. No new acquisition, Candidate, execution or activation authority is added.

## AT-ADR-GOVERNED-DEMO-20261005 — canonical BloFin demo entry slice

- **Date:** 2026-10-05
- **Status:** Implemented; real staging acceptance pending review and consolidated CI.
- **Decision:** A separately armed staging worker may continue a genuinely confirmed,
  approved canonical Candidate through the existing eligibility, risk, immutable plan,
  exact authorization, durable claim/reservation and fenced dispatch authorities to one
  BloFin demo market entry with attached stop/target. Organization, user and execution
  account UUIDs are pinned. Real trading remains disabled and legacy entry paths remain
  tombstoned. SFP keeps `sfp_execution_plan_not_authorized`.
- **Evidence:** Venue-timed quotes and linear contract rules determine size and precision.
  Permission reads must establish read/trade only, existing NET/cross/leverage 1 and a flat
  account. The final safety epoch, lease fence and TTL are freshly read after network
  preflight. A committed ambiguity marker consumes send authority before exactly one
  POST; timeout, crash or absent lookup never permits automatic resend.
- **Projection:** Only identity-validated actual fill history creates immutable execution
  facts, Journal entries and demo learning attribution. Contract facts become base Journal
  quantities; actual fees persist. Missing, unreadable or mismatched linked protection
  preserves actual fills and activates the kill switch. Armed read reconciliation continues
  under that switch and when scanning is refused. Agent explains the latest recorded
  observations without venue reads, execution authority or a fabricated confirmation.
- **Limits:** One ALLOW claim per account's preserved history. No automatic exit-fill close,
  funding/PnL attribution or account reuse; synthetic demo fills and closes are refused.
  Kill-switch activation does not protect or close exposure. The operator must verify
  protection or manually close the demo position under the documented acceptance procedure.
- **Acceptance:** Simulated disposable-PostgreSQL tests are implementation evidence only.
  Real child TPSL/client-ID linkage and a natural venue fill must be proven in staging.
  No merge, deploy, infrastructure mutation or network exchange order occurs in this task.
- **Runbook:** `docs/governed_blofin_demo_execution.md` (activation, evidence and rollback).


## AT-ADR-095 — Explicit manual demo origin reuses execution protocol

Manual demo tests use a distinct immutable schema/authority discriminator with
null strategy/setup/Candidate identities, rather than manufacturing market setup
or approval evidence. Canonical strategy lineage remains mandatory for canonical
plans. Owner confirmation binds the stored revision/hash; generic proposal,
approval and paper-execution APIs cannot bypass this manual confirmation boundary.

Reuse existing authorization hashes, atomic account risk reservation and one
unresolved demo command gate, durable client identity, fencing, kill/TTL guards and
one native POST. A separate staging-only manual API arm does not activate Watcher
or strategy governance. First scope is BTC market entry with one full stop/target.

Persist native fills/fees and verify protection; partial fills and uncertain sends
hold. One durable cancellation intent permits only an entry remainder cancellation;
terminal venue proof controls unused reservation release. Manual Journal source is
excluded from strategy lineage/learning but included in portfolio risk. Exit outcomes
and repeated-entry release are deliberately unsupported until separately governed.

## AT-ADR-096 — Safe preflight stages and canonical Dashboard paper scope

Date: 2026-10-07. Task: AT-115. Base: main e4a7816.

The supervised manual demo provider snapshot must retain the failing read or
validation stage while refusing the preview. Diagnostics allow fixed stage,
endpoint, reason and type labels plus bounded numeric HTTP/venue fields; they
exclude arbitrary exception text, credentials, signatures and private payloads.
They do not infer missing credentials, authorize a trade, retry a POST, mutate an
account setting or weaken a safety predicate. Actual venue root cause remains
unknown without live failure evidence.

Dashboard open positions use the owner-private canonical open paper execution
and validation JournalTrade cohort also used by Attention. Count all matches
independently of the detail limit; keep account and recorded venue visible.
Recent trades use the canonical Journal list across all sources/statuses/dates.
Existing portfolio and daily discipline projections keep their scope and explicit
UI labels. Manual demo tests remain excluded from the Attention/open-paper count;
recent Journal records can include them. Missing current PnL/exposure stays null.
No legacy fallback after a canonical error or historical record rewrite.

## AT-ADR-123 — Private conversational capture has no execution authority

- Id: Filed on 2026-10-09 as a second AT-ADR-097. Renumbered on 2026-10-20.
  AT-ADR-097 remains “Separate native closure from protection attribution”
  (AT-117, BloFin reconciliation). The decision text is unchanged.

2026-10-09. Consolidate navigation into Dashboard, Agent, Journal & Knowledge,
Strategies and Settings while reusing PR232 native account/lifecycle contracts.
Default exchange activity requires the projector-owned execution lifecycle link;
manual venue/source claims and internal simulations cannot qualify. Preserve
account-wide native exposure, incomplete outcome coverage and historical records.

Prose and structured capture use configurable `gpt-6-astra`/high reasoning through
the existing Responses integration, without cheaper/mock prose fallback. Official
model documentation was checked; authorized runtime access and semantic quality
are pending. Preserve server-only secrets and explicit unavailable results.
Private saved notes/drafts have source quotes, scoped deterministic exact-trade
links, durable content deduplication, revision checks and non-destructive Undo.
They never become Journal execution evidence, strategy approval, automation, risk
policy or orders. Uploaded instructions are untrusted data. Existing typed tool
permissions/confirmations/execution fences remain authoritative.

An additive migration follows a7manualrecovery001; no deployed database was
changed. Disposable PostgreSQL migration/concurrency checks pass. Private capture
uses FOR NO KEY UPDATE so unrelated user foreign-key references remain available. Ordinary chat/capture remains available during execution pause. PR232
was accepted and integrated; live checks and the supervised consolidated gate remain separate.
Implementation, fixture verification and presentation guide:
`docs/alphatrade_workspace_redesign.md`.

## AT-ADR-098 — Native activity facts remain separate from execution authority

Date: 2026-10-10. AT-118 stores exact native completed orders/fills under
organization/demo/authenticated UID; credential bindings are selectors, never
account identities. Page facts and checkpoints commit atomically; incompatible
replays stop without overwriting. Command links require verified native UID,
hash-verified original execution-account evidence and a compatible native order receipt;
organization/client echo/instrument/side alone never prove account identity.
Missing proof retains native origin and cannot create execution/strategy/Journal
authority. Count individual native fills once and preserve partial coverage,
unknown currency/funding and observed metadata provenance. The bounded runner
is an optional component of the existing worker and is disarmed by default. Orders
use recurring cursor reconciliation without undocumented time-filter assumptions;
fills retain bounded incremental windows. Existing snapshots require current-binding
verification and UID selection; historical unverified rows are retained but withheld.
Execution authority, frontend and runtime activation remain unchanged.
See docs/blofin_native_activity.md.

## AT-ADR-099 — Experiments approve bounded configurations, not execution authority

Date: 2026-10-10. AT-119 provides generic Exploration/Validation lifecycle over
immutable strategy versions. Each immutable experiment version binds organization,
owner, verified account/source, exact strategy hashes, configuration and independent
sample identity. Owner approval seals the hash with finite budgets and an expiry of
at most 30 days. Running is domain state only. Promotion creates an unapproved
Validation version with no carried samples; records cannot be reused across versions.
Internal simulation and native BloFin have separate identities and provenance.
Manual native exposure consumes capacity without granting management authority.
Trusted adapters, existing canonical strategy/risk/dispatch authorities and fresh
execution UID verification remain prerequisites for future runtime adoption. No
source resolver or runtime is installed. SFP remains observation-only and future
TrendPulse1R needs explicit closed 15m trend/5m entry, structural stop and gross 1R
compiler/evaluation contracts. One additive a11 migration follows a10 on the future
feature branch, outside the release candidate. See docs/experiment_domain_contract.md.

## AT-ADR-100 — TrendPulse1R is deterministic research geometry before runtime

Date: 2026-10-10. AT-120 isolates the pure TrendPulse1R adapter from corrected PR241
6ca6a76 and the current release candidate. Fixed provisional v1: causally known
250 closed 15m bars for EMA20/50, three-bar ±0.1% slow EMA slope, strict confirmed
HH/HL or LL/LH two-wing structure; 60 closed 5m bars, three-candle EMA20 pullback,
closed extreme break and ≤0.1% opening gap. Structural stop is one tick outside
pullback, rounded outward; conservative rounded entry determines one gross 1R
full target. Decimal context/precision, missing receipts, conflicting revisions,
expiry, duplicates and invalid risk fail closed. Immutable experiment variants may
bind this research spec with both timeframes and setup observations only. Pure
attribution tags cannot verify an account or create samples/performance/dispatch.
Existing execution admission still refuses TrendPulse. Nested/SFP meanings remain
unchanged. No compiler/runtime/worker/migration/activation is introduced.
See docs/trendpulse_1r_adapter_contract.md.

## AT-ADR-101 — TrendPulse receipts are eligible at the post-close decision

The prior provisional adapter required historical receipts at trigger opening,
which excluded the immediately preceding closed bar for any positive latency.
Corrected adapter v2 selects the same explicit prior-trend/trigger candle windows
but requires every original receipt to be known at the actual post-close decision.
Later receipts cannot alter an earlier evaluation; new decisions expire at 60s.
Decision time is recorded/hashed; the natural UUID includes the adapter version.
Authored parameter v1, all thresholds, structural stops and rounded gross 1R remain
unchanged. Publish this correction separately before any screening persistence.

## AT-ADR-102 — One-trigger screening persists evidence without execution authority

AT-121 continues from the separately published receipt correction 8b9d1b8 on a
future feature branch. One on-demand authenticated producer acquires at most five
GET-only public requests, evaluates at the actual post-close server decision,
and appends original receipts plus qualified/no-setup/refused/unavailable outcomes.
Nonblocking PG single-flight and process capacity protect the existing worker;
database uniqueness and immutable guards preserve dedupe across restarts. Explicit
public/recorded/synthetic provenance cannot grant native execution/sample authority.
Authenticated bounded GET summaries/detail consume durable records. One reserved
a12 follows a11; reviewed PR24662a20bb is now adopted by next-batch PR244 with
one immutable migration head and retained-schema rollback proof. It remains outside
frozen PR237 and is neither deployed nor full-release accepted.
Synthetic untuned four-regime replay yields192 no-setups/zero signals; genuine
original-arrival market replay remains unverified. Disabled defaults, gross1R,
structural stops, no runner, no quantity/sample/performance or operator changes.
See docs/trendpulse_screening_contract.md and verification handoff.

## AT-ADR-113 — Demo snapshot worker and activity round-trip P&L

- Date: 2026-10-18
- Decision: The paper worker may call the existing read-only `BloFinSyncService.sync`
  every 30–60 seconds when demo gates and the governed owner pin are set. Closed
  gates skip without a database or venue call. Errors back off. A snapshot newer
  than the interval is not fetched again. Real-money trading stays blocked. No
  new BloFin order or write endpoint is added.
- Performance: Win rate and realized P&L for the whole demo account come from
  fills stored by the activity worker. A trade is a closed round-trip per
  position. Partial fills accumulate. A crossing fill is split in proportion.
  Realized P&L is native `fillPnl` plus the original signed fee only when the
  fee currency matches the stored settlement currency. Order `pnl` is not added.
  When `BLOFIN_ACTIVITY_ENABLED` is false, those figures are withheld.
- Method id: `closed-round-trip-fillpnl-signed-fee/v1`.
- Reference: `docs/blofin_demo_snapshot_worker.md`. Enabling the activity worker
  still requires Sofien's approval and is not done by this decision.

## AT-ADR-114 — Demo win rate stays withheld until fee basis and a flat start exist

- Date: 2026-10-18
- Decision: Follow-up to AT-ADR-113 after review of the draft implementation.
  Stored BloFin `fillPnl` is not established as gross or net of fees or funding,
  and fee sign is not independently verified. The signed fee is recorded and is
  not added to the decisive result. A complete activity cursor is not proof
  that a position was flat before the first retained fill. Until both proofs
  exist, the demo account API keeps `win_rate` and realized P&L empty and
  `activity_sync_status` is not `ready`. The structural method id is
  `closed-round-trip-fillpnl-fee-recorded/v1`. Activity sync remains off until
  Sofien approves the worker env names.

## AT-ADR-115 — Demo snapshot worker stays off unless explicitly enabled

- Date: 2026-10-19
- Decision: `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` defaults false. The paper
  worker does not construct the snapshot cycle while the flag is off or while
  the worker is disarmed, so a disarmed staging boot still opens no database
  and no exchange client for this cycle. When the flag is on, the unattended
  sync requires `BLOFIN_READONLY_SYNC_ENABLED` and the three
  `BLOFIN_READONLY_API_*` credentials. It does not fall back to demo trading
  keys. A refusal logs only those two booleans. This does not enable the flag
  or real-money trading.

## AT-ADR-112 — Mirror BloFin demo hand trades from positions history, not fillPnl

- Date: 2026-10-18
- Number: AT-ADR-112. PR 279 already uses AT-ADR-111 for Sofien's paper and
  demo promotion exception, and AT-ADR-110 for the 5% per-trade ceiling.
  PR 280's snapshot decisions are AT-ADR-113, AT-ADR-114, and AT-ADR-115.
  PRs 278, 280, and 281 do not use 112.
- Decision: Sofien's hand trades on the BloFin demo account are mirrored into
  the canonical journal from read-only `GET /api/v1/account/positions-history`,
  funding-fee history, fills (order ids only), and orders history. The flag
  `BLOFIN_DEMO_MANUAL_MIRROR_ENABLED` defaults off and is not set by this
  change. New rows use source `blofin_demo_manual`. Grade is a `grade:` tag.
  No Alembic revision.
- Semantics, read from https://docs.blofin.com/index.html on 2026-10-18:
  the published ETH positions-history row shows `realizedPnl` equal to
  `(close - open) * contracts` and not net of `fee`. `fundingFee` is positive
  when received and negative when paid. Positions history has no funding
  field. `fillPnl` is still not documented as gross or net of fees or
  funding, so AT-ADR-114 stands for the activity path. This mirror ignores
  `fillPnl`.
- A closed row is verified only when direction-aware price P&L times
  `contractValue` equals `realizedPnl` at 1e-8, settlement is USDT, the fee
  is present, and funding for that slice sits inside the seven-day window
  and is not shared. Net is price P&L minus fee minus funding paid
  (`-fundingFee`). A negative fee is stored as a rebate. That sign is
  inferred from the order-history "Fee and rebate" label and the existing
  reconciliation note, not from a positions-history sentence. The published
  BCH `sell` example does not match the short formula, so it stays
  unverified.
- The dashboard publishes win rate, realized P&L, average R, and count from
  closed `blofin_demo_manual` rows only when every one of those rows is
  verified. Otherwise those headlines stay empty and the card says why.
  Method id: `positions-history-price-pnl-fee-funding/v1`.
- Idempotency key is `blofin-demo-history:{historyId}` for a closed slice and
  `blofin-demo-open:{positionId}:{createTime}` for a remainder. An open
  AlphaTrade manual demo row with the same symbol, direction, entry, and
  size is closed in place and keeps its source, including
  `912f2bfd-9061-4196-82bd-a17a0650c14e` when the venue slice matches.
  Ambiguous matches are skipped. A reconciled `manual` or `manual_demo_test`
  row whose external ref starts with `blofin-demo-` can still be tagged.
  Mirrored venue P&L, including that in-place close, does not feed open
  risk, daily realized loss, the daily lock, the paper loss cooldown, or
  promotion. The daily lock does not read tags. The exclusion is source
  `blofin_demo_manual` or the exact server prefix `blofin-demo-history:`
  on `external_ref`. A stuck governed `manual_demo_test` row closed by the
  mirror receives that prefix, so its venue net does not count toward the
  daily lock. Clients cannot set a case or whitespace variant of
  `blofin-demo-`, or a tag that contains a double quote or starts with
  `blofin-`. Candidate selection,
  in-place reconciliation, open-tracker cancellation, the cycle marker,
  and the headline read are limited to the pinned user, not the whole
  organization. A lifecycle-backed row keeps its entry time, so the day's
  trade count still includes the close. A closed row contributes zero open
  exposure. Promotion continues to read paper-validation trades only.
- The mirror runs on the snapshot cadence only when
  `BLOFIN_DEMO_SNAPSHOT_SYNC_ENABLED` is on, the paper worker is armed, and
  `BLOFIN_READONLY_SYNC_ENABLED` with the three `BLOFIN_READONLY_API_*`
  credentials are set. Demo trading keys are not a fallback. It still runs
  when the saved snapshot is inside the refresh interval. A mirror read
  failure does not back off the snapshot cycle. The latest read is stored on a cancelled journal
  marker. A failed read or a page cap withholds the headline. Fills, funding,
  and orders keep paging after positions history has succeeded. A capped
  auxiliary read still stores that history and does not publish a net.
- Headline rows are recomputed from stored entry, exit, size, fee, and
  funding. The journal create route cannot mint `blofin_demo_manual`, and
  the generic update cannot change that source's venue facts or P&L tags.
- Reference: `docs/blofin_demo_manual_mirror.md`.
