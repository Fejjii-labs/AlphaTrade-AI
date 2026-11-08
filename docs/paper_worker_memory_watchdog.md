# Paper worker memory guard

The staging paper worker runs on a 512 MB Render plan. Above that limit Render
kills the process without a graceful stop.

## Allocator arenas

`MALLOC_ARENA_MAX=2` is set in the runtime stage of `backend/Dockerfile` and
on `alphatrade-paper-worker-staging` in `render.yaml`. With about eight worker
threads, glibc otherwise keeps one arena per thread, and freed memory is not
returned to the OS.

## Soft RSS watchdog

The supervisor's serving thread reads resident memory (`/proc/self/status`
`VmRSS`, through `read_process_memory`) at most once per check interval. At or
above the limit it logs `paper_worker_memory_watchdog_tripped` with
`rss_bytes` and `limit_bytes`, stops every component the same way SIGTERM does
(finish the current cycle, join, close), and the process exits with code 75 so
Render restarts it. A failed memory read is logged and never trips.

| Name | Role |
| --- | --- |
| `PAPER_WORKER_MEMORY_WATCHDOG_ENABLED` | Default true. False attaches no watchdog. |
| `PAPER_WORKER_MEMORY_WATCHDOG_LIMIT_MB` | Soft limit, 64–65536. Default 440. |
| `PAPER_WORKER_MEMORY_WATCHDOG_CHECK_SECONDS` | Check interval, 1–3600. Default 30. |
| `PAPER_WORKER_MEMORY_WATCHDOG_STARTUP_GRACE_SECONDS` | No check this long after start, 0–3600. Default 300. |
| `PAPER_WORKER_MEMORY_WATCHDOG_BACKOFF_MAX_SECONDS` | Cap on the backed-off grace, 0–86400. Default 3600. |
| `PAPER_WORKER_MEMORY_WATCHDOG_EARLY_TRIP_WINDOW_SECONDS` | A trip this soon after the grace ends continues the streak, 0–86400. Default 900. |
| `PAPER_WORKER_MEMORY_WATCHDOG_CEILING_MB` | Emergency ceiling, 0–65536; 0 = off. Default 480. Raised to the soft limit if set below it. |
| `PAPER_WORKER_MEMORY_WATCHDOG_CEILING_CHECK_SECONDS` | Ceiling check interval, 1–3600. Default 5. |
| `PAPER_WORKER_MEMORY_WATCHDOG_STATE_PATH` | Backoff state file. Default `/tmp/alphatrade-paper-worker-memory-watchdog.json`; empty disables it. |
| `PAPER_WORKER_MEMORY_WATCHDOG_ALERT_AFTER_TRIPS` | Telegram alert once the streak reaches this, 0–100; 0 = off. Default 2. |
| `PAPER_WORKER_RUN_MARKER_PATH` | Unclean-exit marker. Default `/tmp/alphatrade-paper-worker.running`; empty disables it. |

### Emergency ceiling

The soft limit is checked every 30 s and not at all during the startup grace.
A fast spike (for example several symbols catching up aggTrade windows right
after a restart) can go from the steady state to 512 MiB inside that window,
and the kernel then kills the process: no clean stop, leases stay held, and
the next boot can refuse on `provider_unavailable`. The ceiling is read every
`PAPER_WORKER_MEMORY_WATCHDOG_CEILING_CHECK_SECONDS` from the first second,
grace or not. At or above it the watchdog trips exactly like the soft limit
(`trigger="ceiling"` on `paper_worker_memory_watchdog_tripped`, clean stop,
exit 75, the same streak and backoff). Between the soft limit and the ceiling
the grace still holds, so a worker that starts slightly above 440 MB does not
exit-loop. The ceiling is never below the soft limit.

### Start at or above the limit

If the worker's baseline RSS is already at the limit, a plain watchdog would
exit every check interval. Two rules prevent that loop:

1. **Startup grace.** No check runs for the grace period, counted from process
   start (the watchdog's monotonic clock at construction). A worker that starts
   above the limit serves for the full grace before the first check.
2. **Exponential backoff across restarts.** On a trip the watchdog writes
   `{"consecutive_trips": n, "tripped_at": <unix seconds>}` to the state file
   (nothing else). At the next start, a state younger than the backoff cap sets
   the grace to `grace * 2**n`, capped at the backoff max: 300, 600, 1200, 2400,
   3600, 3600 s. Only a trip within the early-trip window (default 900 s)
   after the grace ends continues the streak. A later trip is a normal leak
   and starts a new streak of one, so a leak that trips 1–2 h after start does
   not double the next grace. A missing, stale, corrupt or unwritable
   file falls back to the plain grace and never blocks the trip.

The state lives in `/tmp`. If Render restarts the service in a fresh
container, the file is gone, and the plain grace alone limits the restart
rate to one per grace period plus boot time (not one per 30 s check). In that
case a limit below the real baseline still restarts the worker about every
five minutes; `paper_worker_memory_watchdog_tripped` logs `uptime_seconds` and
`consecutive_trips`, and `paper_worker_memory_watchdog_armed` logs the grace in
effect, so the loop is visible. Raise the limit or disable the watchdog if it
shows up.

## Alerts

Both alerts use the existing safety-alert Telegram path
(`services/safety_alerts.deliver_safety_alert`, `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID`; no new secret). Both codes are in the default Telegram
allowlist and carry counts and times only. Without a token and chat id they
are logged as `safety_alert_telegram_unconfigured` and not sent.

- `worker_memory_watchdog_repeat`: after the clean stop, before exit 75, when
  `consecutive_trips >= PAPER_WORKER_MEMORY_WATCHDOG_ALERT_AFTER_TRIPS`.
- `worker_unclean_exit`: at boot, the worker writes `{"started_at": <unix
  seconds>}` to the run marker and removes it after a clean stop (a SIGTERM or
  SIGINT shutdown, a `--once` round, or a watchdog exit). A marker still there
  at the next boot means the previous run was OOM-killed, SIGKILLed after the
  shutdown deadline, or crashed. The worker logs
  `paper_worker_previous_exit_unclean` at error and sends the alert. A Python
  exception that escapes the run also leaves the marker, on purpose.
  On SIGTERM the worker adds `stopping_at` to the marker before joining its
  threads. If that run is then SIGKILLed when Render's 60 s grace runs out (an
  ordinary deploy whose shutdown overran), the next boot logs
  `paper_worker_previous_exit_after_sigterm` at warning and does **not** page.
  The supervisor joins all threads within one 45 s budget
  (`SHUTDOWN_BUDGET_SECONDS`); a thread still running after it is logged as
  `paper_worker_join_timeout` and the stop hook (lease release, client close)
  is skipped (`paper_worker_stop_hook_skipped`) so nothing is closed under a
  running cycle; its leases expire by TTL.

### Limit: the files may not survive a Render restart

Since the Fable audit L8 fix, an armed worker also keeps the trip streak in the
`controlled_runtime_status` row `paper_worker_memory_watchdog`
(`request_count` = consecutive trips, `heartbeat_at` = trip time). At boot the
watchdog uses whichever of the file and that row remembers more trips, so the
startup-grace backoff survives a container replacement. A disarmed worker
does not open PostgreSQL and keeps file-only state. The run marker is still
file-only (see below).

Render's documentation (<https://render.com/docs/disks>, fetched 2026-11-04)
says: "By default, Render services have an ephemeral filesystem. This means
that without a persistent disk, any changes you make to a service's local
files are lost every time the service redeploys or restarts." This branch does
not attach a disk, and the behavior on staging has not been checked. If `/tmp`
is wiped on every restart:

- the backoff state is lost, so the streak never passes one and the grace
  never grows (the plain grace still limits restarts to one per grace period);
- the repeat alert therefore never fires at the default of 2. Set
  `PAPER_WORKER_MEMORY_WATCHDOG_ALERT_AFTER_TRIPS=1` to page on every trip;
- the unclean-exit marker is lost, so an OOM kill is not reported by the
  worker. Render's own service events still show it.

Pointing both paths at a persistent disk mount would make them survive. That
adds a paid disk and a Render change, so it needs the owner's approval. A
database-backed marker would avoid the disk, but it must not mistake the old
instance of a zero-downtime deploy for an unclean exit.

`--once` rounds do not check memory. This change does not set these variables
in Render.
