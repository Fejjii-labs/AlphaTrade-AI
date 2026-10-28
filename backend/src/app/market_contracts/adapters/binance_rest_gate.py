"""Process-wide gate for every Binance USD-M REST read.

On 2026-10-27 the staging worker's egress IP was banned (HTTP 418) for about an
hour after boot-time bursts of ``/fapi/v1/aggTrades``. Per-client pauses did
not stop other clients in the same process from calling during the ban, and
each call during a ban lengthens it. This gate is the one shared answer to
"may this process call Binance REST right now?":

* HTTP 418 or 429 closes the gate for every client in the process until
  ``max(Retry-After, exponential backoff)`` plus jitter has passed.
  Consecutive failures double the backoff (capped). A success resets it.
* ``X-MBX-USED-WEIGHT-1M`` at or above the soft limit closes the gate until
  the next minute window, before Binance has to answer 429.
* aggTrades requests are spaced out, more widely during the first minutes
  after process start, so a boot cannot fire a burst of weight-20 reads.

A closed gate raises before any request is sent. Callers fail closed: no data
means no paper decision, never a fabricated one.

Websocket market streams would remove most aggTrades REST polling; that is a
separate follow-up and out of scope here.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Mapping

AGGTRADES_PATH = "/fapi/v1/aggTrades"
# Binance USD-M IP limit is 2400 weight per minute. Pause before reaching it.
USED_WEIGHT_SOFT_LIMIT = 2000
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 900.0
JITTER_FRACTION = 0.25
AGGTRADES_MIN_INTERVAL_SECONDS = 0.25
BOOT_RAMP_SECONDS = 120.0
BOOT_AGGTRADES_MIN_INTERVAL_SECONDS = 1.0
_USED_WEIGHT_HEADER = "x-mbx-used-weight-1m"


class BinanceRestGate:
    """Shared ban/backoff state plus request spacing for one process."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
        used_weight_soft_limit: int = USED_WEIGHT_SOFT_LIMIT,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        backoff_cap_seconds: float = BACKOFF_CAP_SECONDS,
        jitter_fraction: float = JITTER_FRACTION,
        aggtrades_min_interval_seconds: float = AGGTRADES_MIN_INTERVAL_SECONDS,
        boot_ramp_seconds: float = BOOT_RAMP_SECONDS,
        boot_aggtrades_min_interval_seconds: float = BOOT_AGGTRADES_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._rng = rng
        self._soft_limit = used_weight_soft_limit
        self._base = backoff_base_seconds
        self._cap = backoff_cap_seconds
        self._jitter = jitter_fraction
        self._aggtrades_interval = aggtrades_min_interval_seconds
        self._boot_ramp = boot_ramp_seconds
        self._boot_aggtrades_interval = boot_aggtrades_min_interval_seconds
        self._until: float | None = None
        self._kind: str | None = None
        self._lock = threading.Lock()
        self._strikes = 0
        self._started_at = clock()
        self._next_aggtrades_at = self._started_at
        self.last_used_weight: int | None = None

    def remaining(self) -> tuple[str, float] | None:
        """``(ban|rate_limit, seconds_left)`` while the gate is closed."""

        with self._lock:
            if self._until is None or self._kind is None:
                return None
            left = self._until - self._clock()
            if left <= 0:
                self._until = None
                self._kind = None
                return None
            return self._kind, left

    def _close(self, kind: str, seconds: float) -> None:
        """Extend the closed window. A shorter new pause never shortens it."""

        until = self._clock() + max(seconds, 0.0)
        with self._lock:
            if self._until is None or until >= self._until:
                self._until = until
                if self._kind != "ban" or kind == "ban":
                    self._kind = kind

    def note_failure(self, kind: str, retry_after_seconds: float | None) -> float:
        """Close the gate after a 418 (``ban``) or 429 (``rate_limit``).

        Returns the pause length. It is never shorter than Retry-After.
        """

        with self._lock:
            self._strikes += 1
            backoff = min(self._base * 2.0 ** (self._strikes - 1), self._cap)
        floor = (
            float(retry_after_seconds) if retry_after_seconds and retry_after_seconds > 0 else 0.0
        )
        seconds = max(floor, backoff) * (1.0 + self._jitter * self._rng())
        self._close(kind, seconds)
        return seconds

    def note_success(self, headers: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._strikes = 0
        used = _used_weight(headers)
        if used is None:
            return
        self.last_used_weight = used
        if used >= self._soft_limit:
            into_minute = self._wall_clock() % 60.0
            seconds = (60.0 - into_minute) + 1.0 + self._jitter * self._rng()
            self._close("rate_limit", seconds)

    def note_used_weight(self, headers: Mapping[str, str] | None) -> None:
        used = _used_weight(headers)
        if used is not None:
            self.last_used_weight = used

    def reserve_spacing(self, path: str) -> float:
        """Reserve the next aggTrades slot. Returns seconds to wait first."""

        if path != AGGTRADES_PATH:
            return 0.0
        with self._lock:
            now = self._clock()
            in_boot = now - self._started_at < self._boot_ramp
            interval = self._boot_aggtrades_interval if in_boot else self._aggtrades_interval
            slot = max(now, self._next_aggtrades_at)
            self._next_aggtrades_at = slot + interval
            return slot - now

    @property
    def strikes(self) -> int:
        with self._lock:
            return self._strikes


def _used_weight(headers: Mapping[str, str] | None) -> int | None:
    if not headers:
        return None
    raw = headers.get(_USED_WEIGHT_HEADER)
    if raw is None:
        raw = headers.get("X-MBX-USED-WEIGHT-1M")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


_PROCESS_GATE: BinanceRestGate | None = None
_PROCESS_GATE_GUARD = threading.Lock()


def process_binance_rest_gate() -> BinanceRestGate:
    global _PROCESS_GATE
    with _PROCESS_GATE_GUARD:
        if _PROCESS_GATE is None:
            _PROCESS_GATE = BinanceRestGate()
        return _PROCESS_GATE


def reset_process_binance_rest_gate() -> None:
    """Tests use this to simulate a fresh process."""

    global _PROCESS_GATE
    with _PROCESS_GATE_GUARD:
        _PROCESS_GATE = None
