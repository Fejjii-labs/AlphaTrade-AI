"""Closed-candle kline cache: one Binance read per (symbol, interval) per closed bar.

The 10 second public read cache refetched klines on every Watcher tick (15 s
poll, 10 s monitor publish). Closed candles do not change until the next close,
so a payload is reused until the interval boundary moves. Any caller asking for
at most the cached row count is served the tail of that payload, so the monitor,
every strategy and every scope on one symbol/interval share a single read.

A payload is stored only when its last row is the candle forming after the
boundary. That proves Binance already rolled past the previous close, so the
cache never pins a closed candle that the provider had not finalized.
Loader failures are not stored.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

from app.market_contracts.adapters.request_budget import record_cache_hit

# Epoch-aligned floors match Binance boundaries up to 1d. Weekly candles open on
# Monday, not on the epoch Thursday, so 1w and longer are never cached here.
MAX_CACHEABLE_INTERVAL_MS = 86_400_000


class ClosedKlineCache:
    """Bounded per-key payload cache, invalidated by the next interval close."""

    def __init__(self, *, max_entries: int = 64) -> None:
        if max_entries < 1:
            raise ValueError("closed kline cache must keep at least one entry.")
        self._max_entries = max_entries
        self._entries: OrderedDict[Hashable, tuple[int, list[Any]]] = OrderedDict()
        self._locks: dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()

    def get_or_load(
        self,
        key: Hashable,
        *,
        end_ms: int,
        interval_ms: int,
        limit: int,
        loader: Callable[[], list[Any]],
    ) -> list[Any]:
        """Return the newest ``limit`` rows for the boundary containing ``end_ms``."""

        if not 0 < interval_ms <= MAX_CACHEABLE_INTERVAL_MS:
            return loader()
        boundary = (end_ms // interval_ms) * interval_ms
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._guard:
                entry = self._entries.get(key)
                if entry is not None and entry[0] == boundary and len(entry[1]) >= limit:
                    self._entries.move_to_end(key)
                    record_cache_hit()
                    return list(entry[1][-limit:])
            rows = loader()
            if _forming_row_opens_at(rows, boundary):
                with self._guard:
                    self._entries[key] = (boundary, list(rows))
                    self._entries.move_to_end(key)
                    while len(self._entries) > self._max_entries:
                        old_key, _ = self._entries.popitem(last=False)
                        self._locks.pop(old_key, None)
            return rows

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()


def _forming_row_opens_at(rows: object, boundary: int) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    last = rows[-1]
    return isinstance(last, list) and bool(last) and last[0] == boundary
