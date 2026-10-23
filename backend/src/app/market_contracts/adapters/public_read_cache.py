"""Short TTL cache for public USD-M quotes and klines.

``PUBLIC_READ_TTL_SECONDS`` is 10 seconds. That is inside the 5-15 second
window and matches live-quote freshness. The cache covers repeated scan, chat,
and status reads of confirmed klines (per symbol, interval, limit, and
end-time bucket) and current-price quotes (per instrument). Concurrent
identical misses share one loader. Failures are shared with waiters and are
not stored.

Internal paper SFP fills and the eligibility receipt that records the fill
price do not use this cache. They take a fresh aggTrades read at fill time
and do not write the result back. See ``load_paper_sfp_fill_quote``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

from app.market_contracts.adapters.request_budget import record_cache_hit

# Inside the 5-15s window, and the same length as live quote freshness.
PUBLIC_READ_TTL_SECONDS = 10.0


class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: Exception | None = None


class ShortTtlSingleflight:
    """Bounded TTL cache. Identical concurrent loads share one result."""

    def __init__(
        self,
        *,
        ttl_seconds: float = PUBLIC_READ_TTL_SECONDS,
        max_entries: int = 64,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("public read cache TTL must be positive.")
        if max_entries < 1:
            raise ValueError("public read cache must keep at least one entry.")
        self.ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock or time.monotonic
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._flights: dict[Hashable, _Flight] = {}
        self._guard = threading.Lock()

    def get(self, key: Hashable) -> Any | None:
        """Return a fresh value. Expired entries are a miss."""

        with self._guard:
            return self._get_unlocked(key)

    def invalidate(self, key: Hashable) -> None:
        with self._guard:
            self._entries.pop(key, None)

    def get_or_load(self, key: Hashable, loader: Callable[[], Any]) -> Any:
        """Return the cached value or run ``loader`` once for this key."""

        with self._guard:
            cached = self._get_unlocked(key)
            if cached is not None:
                leader = False
                flight = None
            else:
                flight = self._flights.get(key)
                if flight is None:
                    flight = _Flight()
                    self._flights[key] = flight
                    leader = True
                else:
                    leader = False
        if cached is not None:
            record_cache_hit()
            return cached
        assert flight is not None
        if not leader:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            record_cache_hit()
            return flight.value
        try:
            value = loader()
        except Exception as exc:
            flight.error = exc
            raise
        else:
            with self._guard:
                self._put_unlocked(key, value)
            flight.value = value
            return value
        finally:
            flight.event.set()
            with self._guard:
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)

    def _get_unlocked(self, key: Hashable) -> Any | None:
        item = self._entries.get(key)
        if item is None:
            return None
        stored_at, value = item
        if self._clock() - stored_at > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def _put_unlocked(self, key: Hashable, value: Any) -> None:
        if value is None:
            return
        self._entries[key] = (self._clock(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
