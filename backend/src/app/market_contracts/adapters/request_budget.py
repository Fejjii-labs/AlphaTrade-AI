"""Sliding request-weight budget for public Binance USD-M reads.

Weights follow the USD-M REQUEST_WEIGHT schedule used by the allowlisted
paths. The budget fails closed when a wait would exceed the configured bound.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.market_contracts.errors import RateLimitedError
from app.market_contracts.request_progress import notify_market_request_progress

AGGTRADE_REQUEST_WEIGHT = 20
_PROGRESS_CHUNK_SECONDS = 5.0


def request_weight(path: str, params: Mapping[str, str | int] | None) -> int:
    """Return the USD-M request weight for one allowlisted GET."""

    if path == "/fapi/v1/aggTrades":
        return AGGTRADE_REQUEST_WEIGHT
    if path == "/fapi/v1/depth":
        limit = int((params or {}).get("limit", 500))
        if limit not in {5, 10, 20, 50, 100, 500, 1000}:
            raise ValueError("Uncontracted Binance depth limit.")
        return 2 if limit <= 50 else 5 if limit == 100 else 10 if limit == 500 else 20
    if path == "/fapi/v1/klines":
        raw_limit = (params or {}).get("limit", 500)
        limit = int(raw_limit)
        if limit < 100:
            return 1
        if limit < 500:
            return 2
        if limit <= 1000:
            return 5
        return 10
    return 1


@dataclass
class RequestMetrics:
    """Process-local counters. They do not include URLs or secrets."""

    requests: int = 0
    weight_used: int = 0
    retries: int = 0
    rate_limited: int = 0
    cache_hits: int = 0


@dataclass
class MarketRequestSnapshot:
    """Copy of the counters safe to publish on a status row."""

    requests: int
    weight_used: int
    retries: int
    rate_limited: int
    cache_hits: int


_METRICS = RequestMetrics()
_METRICS_LOCK = threading.Lock()


def market_request_metrics() -> MarketRequestSnapshot:
    with _METRICS_LOCK:
        return MarketRequestSnapshot(
            requests=_METRICS.requests,
            weight_used=_METRICS.weight_used,
            retries=_METRICS.retries,
            rate_limited=_METRICS.rate_limited,
            cache_hits=_METRICS.cache_hits,
        )


def record_cache_hit() -> None:
    with _METRICS_LOCK:
        _METRICS.cache_hits += 1


def record_request(*, weight: int, rate_limited: bool = False, retry: bool = False) -> None:
    with _METRICS_LOCK:
        _METRICS.requests += 1
        _METRICS.weight_used += weight
        if rate_limited:
            _METRICS.rate_limited += 1
        if retry:
            _METRICS.retries += 1


@dataclass
class SlidingWeightBudget:
    """At most ``limit`` weight inside ``window_seconds``. Clock is monotonic."""

    limit: int
    window_seconds: float = 60.0
    max_wait_seconds: float = 60.0
    _events: deque[tuple[float, int]] = field(default_factory=deque)
    _clock: Callable[[], float] = time.monotonic
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def used(self, now: float | None = None) -> int:
        with self._lock:
            return self._used_unlocked(now)

    def acquire(
        self,
        weight: int,
        *,
        sleeper: Callable[[float], None],
    ) -> None:
        """Reserve ``weight`` or raise once the bounded wait is exhausted."""

        if weight < 1:
            raise ValueError("request weight must be >= 1")
        if weight > self.limit:
            raise RateLimitedError(
                "A single read exceeds the Binance request-weight budget.",
                retry_after_seconds=self.window_seconds,
            )
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                self._evict(now)
                if self._used_unlocked(now) + weight <= self.limit:
                    self._events.append((now, weight))
                    return
                delay = self._wait_step(now)
            if waited + delay > self.max_wait_seconds:
                raise RateLimitedError(
                    "Binance request-weight budget is exhausted.",
                    retry_after_seconds=max(delay, 0.0),
                )
            sleep_with_progress(delay, sleeper=sleeper)
            waited += delay

    def _used_unlocked(self, now: float | None = None) -> int:
        current = self._clock() if now is None else now
        self._evict(current)
        return sum(weight for _stamp, weight in self._events)

    def _wait_step(self, now: float) -> float:
        if not self._events:
            return _PROGRESS_CHUNK_SECONDS
        oldest = self._events[0][0]
        remaining = self.window_seconds - (now - oldest)
        if remaining <= 0:
            return 0.05
        return min(max(remaining, 0.05), _PROGRESS_CHUNK_SECONDS)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()


def bounded_backoff_seconds(
    *,
    attempt: int,
    retry_after_seconds: float | None,
    max_backoff_seconds: float,
) -> float:
    """Honor Retry-After when present, otherwise a short exponential delay."""

    cap = max(max_backoff_seconds, 0.0)
    if retry_after_seconds is not None and retry_after_seconds >= 0:
        return min(retry_after_seconds, cap)
    growth = min(2**attempt, 8)
    return min(float(growth), cap)


def sleep_with_progress(seconds: float, *, sleeper: Callable[[float], None]) -> None:
    """Sleep in short slices so a lease heartbeat can run during backoff."""

    remaining = max(seconds, 0.0)
    if remaining == 0:
        notify_market_request_progress()
        return
    while remaining > 0:
        step = min(_PROGRESS_CHUNK_SECONDS, remaining)
        notify_market_request_progress()
        sleeper(step)
        remaining -= step
