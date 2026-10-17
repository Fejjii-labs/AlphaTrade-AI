"""Opt-in worker cycle diagnostics. Retain numbers only, never scan objects."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from time import monotonic

import structlog

from app.observability.process_memory import ProcessMemory, read_process_memory

logger = structlog.get_logger("observability.worker_memory")


class WorkerMemoryDiagnostics:
    """One bounded post-cleanup series per component; process RSS is shared."""

    def __init__(self) -> None:
        # Components (watcher, telegram, indexing, activity, ...) register lazily.
        self._after: dict[str, deque[tuple[float, int]]] = {}
        self._cycles: dict[str, int] = {}
        memory = read_process_memory()
        logger.info(
            "paper_worker_memory_startup",
            rss_bytes=memory.rss_bytes,
            rss_peak_bytes=memory.peak_rss_bytes,
            threads=threading.active_count(),
            **container_memory_fields(),
        )

    def finish(self, component: str, sampler: CycleMemorySampler, after: ProcessMemory) -> None:
        history = self._after.setdefault(component, deque(maxlen=32))
        history.append((monotonic(), after.rss_bytes))
        self._cycles[component] = self._cycles.get(component, 0) + 1
        elapsed = history[-1][0] - history[0][0]
        growth = history[-1][1] - history[0][1]
        logger.info(
            "paper_worker_memory_cycle",
            component=component,
            cycle=self._cycles[component],
            rss_start_bytes=sampler.start.rss_bytes,
            rss_sampled_peak_bytes=sampler.sampled_peak,
            rss_before_cleanup_bytes=sampler.before.rss_bytes,
            rss_after_cleanup_bytes=after.rss_bytes,
            rss_peak_bytes=after.peak_rss_bytes,
            growth_window_cycles=len(history),
            growth_bytes_per_cycle=growth / max(1, len(history) - 1),
            growth_bytes_per_hour=growth * 3600 / elapsed if elapsed > 0 else 0.0,
            threads=threading.active_count(),
            **container_memory_fields(),
        )


def container_memory_fields() -> dict[str, int | None]:
    """Read cgroup usage/limit separately from process RSS, when available."""

    for current, limit in (
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
        (
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
        ),
    ):
        try:
            usage = int(Path(current).read_text().strip())
            raw_limit = Path(limit).read_text().strip()
            maximum = None if raw_limit == "max" else int(raw_limit)
        except (OSError, ValueError):
            continue
        # v1 represents an unlimited budget with a near-int64 maximum.
        if maximum is not None and maximum >= 2**60:
            maximum = None
        return {"container_memory_bytes": usage, "container_memory_limit_bytes": maximum}
    return {"container_memory_bytes": None, "container_memory_limit_bytes": None}


class CycleMemorySampler:
    """Sample current RSS at 250 ms; this is a lower bound on the cycle peak.

    The sampler owns no market, database or callback objects. Stop it before
    allocator cleanup, including when a component raises.
    """

    def __init__(self, *, read: Callable[[], ProcessMemory] = read_process_memory) -> None:
        self._read = read
        self.start = self.before = read()
        self.sampled_peak = self.start.rss_bytes
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample, name="paper-memory-sampler", daemon=True
        )
        self._thread.start()

    def _sample(self) -> None:
        while not self._stop.wait(0.25):
            self.sampled_peak = max(self.sampled_peak, self._read().rss_bytes)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        self.before = self._read()
        self.sampled_peak = max(self.sampled_peak, self.before.rss_bytes)
