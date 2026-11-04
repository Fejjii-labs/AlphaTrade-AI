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
| `PAPER_WORKER_MEMORY_WATCHDOG_STATE_PATH` | Backoff state file. Default `/tmp/alphatrade-paper-worker-memory-watchdog.json`; empty disables it. |

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
   3600, 3600 s. A trip later than grace + backoff max after start is a normal
   leak and starts a new streak of one. A missing, stale, corrupt or unwritable
   file falls back to the plain grace and never blocks the trip.

The state lives in `/tmp`. If Render restarts the service in a fresh
container, the file is gone, and the plain grace alone limits the restart
rate to one per grace period plus boot time (not one per 30 s check). In that
case a limit below the real baseline still restarts the worker about every
five minutes; `paper_worker_memory_watchdog_tripped` logs `uptime_seconds` and
`consecutive_trips`, and `paper_worker_memory_watchdog_armed` logs the grace in
effect, so the loop is visible. Raise the limit or disable the watchdog if it
shows up.

`--once` rounds do not check memory. This change does not set these variables
in Render.
