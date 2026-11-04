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

`--once` rounds do not check memory. This change does not set these variables
in Render.
