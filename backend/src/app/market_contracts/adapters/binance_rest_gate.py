"""Process-wide gate for every Binance USD-M REST read.

On 2026-10-27 the staging worker's egress IP was banned (HTTP 418) for about an
hour after boot-time bursts of ``/fapi/v1/aggTrades``. Per-client pauses did
not stop other clients in the same process from calling during the ban, and
each call during a ban lengthens it. This gate is the one shared answer to
"may this process call Binance REST right now?":

* HTTP 418 or 429 closes the gate for every client in the process until
  ``max(Retry-After, exponential backoff)`` plus jitter has passed.
  Consecutive failures double the backoff (capped at 15 min). A success resets
  it. A deadline the server sends (Retry-After or a ``banned until`` body) is
  honoured up to 3 days, because repeat offenders get bans longer than the
  computed cap. A 418 without a usable deadline waits at least 2 minutes,
  Binance's shortest ban.
* ``X-MBX-USED-WEIGHT-1M`` at or above the soft limit closes the gate until
  the next minute window, before Binance has to answer 429.
* aggTrades requests are spaced out, more widely during the first minutes
  after process start, so a boot cannot fire a burst of weight-20 reads.

A closed gate raises before any request is sent. Callers fail closed: no data
means no paper decision, never a fabricated one.

Every 418/429 logs ``binance_rest_throttled`` (WARNING), and a used weight above
``USED_WEIGHT_WARN_THRESHOLD`` logs ``binance_used_weight_high`` at most once per
warn interval, so a climb toward a ban is visible before it happens.

On 2026-11-04 a deploy overlapped three worker instances behind one egress IP;
each had its own gate. ``binance_gate_coordination`` shares the ban deadline
and a startup lease through PostgreSQL. This module stays database-free: it
exposes a failure listener, ``adopt_deadline`` and a standby ``hold``.

Websocket market streams would remove most aggTrades REST polling; that is a
separate follow-up and out of scope here.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import structlog

AGGTRADES_PATH = "/fapi/v1/aggTrades"
# Binance USD-M IP limit is 2400 weight per minute. Pause before reaching it.
USED_WEIGHT_SOFT_LIMIT = 2000
# Warn (rate-limited) above this used weight, well before the soft limit pauses.
USED_WEIGHT_WARN_THRESHOLD = 1500
USED_WEIGHT_WARN_INTERVAL_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 5.0
# Cap for the computed exponential backoff only.
BACKOFF_CAP_SECONDS = 900.0
# Server deadlines are honoured up to this bound. A ban once lasted 40 minutes.
MAX_SERVER_PAUSE_SECONDS = 3 * 86_400.0
# Binance's shortest IP ban. Used when a 418 carries no usable deadline.
BAN_FLOOR_SECONDS = 120.0
JITTER_FRACTION = 0.25
AGGTRADES_MIN_INTERVAL_SECONDS = 0.25
BOOT_RAMP_SECONDS = 120.0
BOOT_AGGTRADES_MIN_INTERVAL_SECONDS = 1.0
_USED_WEIGHT_HEADER = "x-mbx-used-weight-1m"

logger = structlog.get_logger(__name__)

# ``(kind, until_epoch)`` after a 418/429 closed the gate. Must not raise or block.
FailureListener = Callable[[str, float], None]


@dataclass(frozen=True, slots=True)
class RestGateState:
    """Read-only view for health. Epoch seconds; no request data."""

    banned: bool
    closed_kind: str | None
    until_epoch: float | None
    last_status_code: int | None
    ban_started_epoch: float | None
    strikes: int


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
        used_weight_warn_threshold: int = USED_WEIGHT_WARN_THRESHOLD,
        used_weight_warn_interval_seconds: float = USED_WEIGHT_WARN_INTERVAL_SECONDS,
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
        self._last_status: int | None = None
        self._ban_started: float | None = None
        self._warn_threshold = used_weight_warn_threshold
        self._warn_interval = used_weight_warn_interval_seconds
        self._next_weight_warn_at: float | None = None
        self._failure_listener: FailureListener | None = None
        self._hold_until: float | None = None

    def state(self) -> RestGateState:
        """Current gate posture. Reading it never opens or closes the gate."""

        with self._lock:
            closed = self._remaining_locked()
            return RestGateState(
                banned=closed is not None and closed[0] == "ban",
                closed_kind=None if closed is None else closed[0],
                until_epoch=None if closed is None else self._wall_clock() + closed[1],
                last_status_code=self._last_status,
                ban_started_epoch=self._ban_started,
                strikes=self._strikes,
            )

    def remaining(self) -> tuple[str, float] | None:
        """``(ban|rate_limit, seconds_left)`` while the gate is closed."""

        with self._lock:
            return self._remaining_locked()

    def _remaining_locked(self) -> tuple[str, float] | None:
        """``remaining`` body. The caller holds ``self._lock``."""

        now = self._clock()
        if self._until is not None and self._kind is not None:
            left = self._until - now
            if left > 0:
                return self._kind, left
            self._until = None
            self._kind = None
        if self._hold_until is not None:
            # Startup standby reads like a rate-limit pause: callers fail closed.
            left = self._hold_until - now
            if left > 0:
                return "rate_limit", left
            self._hold_until = None
        return None

    def _close(self, kind: str, seconds: float) -> None:
        """Extend the closed window. A shorter new pause never shortens it."""

        with self._lock:
            self._close_locked(kind, seconds)

    def _close_locked(self, kind: str, seconds: float) -> None:
        """``_close`` body. The caller holds ``self._lock``."""

        until = self._clock() + max(seconds, 0.0)
        if self._until is None or until >= self._until:
            self._until = until
            if self._kind != "ban" or kind == "ban":
                self._kind = kind

    def note_failure(self, kind: str, retry_after_seconds: float | None) -> float:
        """Close the gate after a 418 (``ban``) or 429 (``rate_limit``).

        Returns the pause length. It is never shorter than Retry-After.
        """

        server = sanitize_retry_after(retry_after_seconds)
        if server is None and kind == "ban":
            server = BAN_FLOOR_SECONDS
        # The ban check, the ban start and the close share one lock, so two
        # concurrent 418s cannot both see "not banned" and move the ban start.
        with self._lock:
            already_banned = (self._remaining_locked() or ("",))[0] == "ban"
            self._strikes += 1
            self._last_status = 418 if kind == "ban" else 429
            if kind == "ban" and not already_banned:
                self._ban_started = self._wall_clock()
            backoff = min(self._base * 2.0 ** (self._strikes - 1), self._cap)
            base = max(server or 0.0, backoff)
            # Jitter is bounded by the backoff cap so a 3-day ban is not stretched by hours.
            seconds = base + min(base, self._cap) * self._jitter * self._rng()
            self._close_locked(kind, seconds)
            strikes = self._strikes
            listener = self._failure_listener
            until_epoch = 0.0 if listener is None else self._wall_clock() + seconds
        # Every 418/429 is logged; they are rare and each one matters.
        logger.warning(
            "binance_rest_throttled",
            status_code=418 if kind == "ban" else 429,
            kind=kind,
            retry_after_seconds=None if server is None else round(server, 1),
            pause_seconds=round(seconds, 1),
            strikes=strikes,
            used_weight_1m=self.last_used_weight,
        )
        if listener is not None:
            try:
                listener(kind, until_epoch)
            except Exception:
                logger.warning("binance_rest_gate_listener_failed")
        if seconds > self._cap:
            logger.error(
                "binance_rest_gate_long_pause",
                kind=kind,
                pause_seconds=round(seconds, 1),
                server_deadline_seconds=None if server is None else round(server, 1),
                strikes=strikes,
            )
        return seconds

    def note_success(self, headers: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._strikes = 0
        used = _used_weight(headers)
        if used is None:
            return
        self._record_used_weight(used)
        if used >= self._soft_limit:
            into_minute = self._wall_clock() % 60.0
            seconds = (60.0 - into_minute) + 1.0 + self._jitter * self._rng()
            self._close("rate_limit", seconds)

    def note_used_weight(self, headers: Mapping[str, str] | None) -> None:
        used = _used_weight(headers)
        if used is not None:
            self._record_used_weight(used)

    def _record_used_weight(self, used: int) -> None:
        """Remember the header and warn above the threshold, once per warn interval."""

        self.last_used_weight = used
        if used <= self._warn_threshold:
            return
        with self._lock:
            now = self._clock()
            if self._next_weight_warn_at is not None and now < self._next_weight_warn_at:
                return
            self._next_weight_warn_at = now + self._warn_interval
        logger.warning(
            "binance_used_weight_high",
            used_weight_1m=used,
            warn_threshold=self._warn_threshold,
            soft_limit=self._soft_limit,
        )

    def set_failure_listener(self, listener: FailureListener | None) -> None:
        """Called after each 418/429 with the new deadline (wall clock)."""

        with self._lock:
            self._failure_listener = listener

    def adopt_deadline(self, kind: str, until_epoch: float) -> float:
        """Close the gate until a deadline another instance recorded.

        Keeps the later deadline, counts no strike and calls no listener.
        Returns the seconds adopted (0 when the deadline already passed).
        """

        if kind not in ("ban", "rate_limit"):
            return 0.0
        seconds = sanitize_retry_after(until_epoch - self._wall_clock())
        if not seconds:
            return 0.0
        self._close(kind, seconds)
        return seconds

    def hold(self, seconds: float) -> None:
        """Start a standby: the gate reads closed for ``seconds`` unless released."""

        with self._lock:
            self._hold_until = self._clock() + max(seconds, 0.0)

    def release_hold(self) -> None:
        with self._lock:
            self._hold_until = None

    @property
    def holding(self) -> bool:
        with self._lock:
            return self._hold_until is not None and self._hold_until > self._clock()

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


def sanitize_retry_after(
    value: float | None, *, cap: float = MAX_SERVER_PAUSE_SECONDS
) -> float | None:
    """Usable server deadline in seconds, or None so the caller falls back to backoff.

    Non-finite (inf, nan) and negative values are rejected. Everything else is
    clamped to ``cap`` (3 days) so one bad header cannot close the gate for longer.
    """

    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return min(parsed, cap)


def _used_weight(headers: Mapping[str, str] | None) -> int | None:
    """Strict parse: ASCII digits only, so "12abc", "-1", "1.5" are ignored."""

    if not headers:
        return None
    raw = headers.get(_USED_WEIGHT_HEADER)
    if raw is None:
        raw = headers.get("X-MBX-USED-WEIGHT-1M")
    if raw is None:
        return None
    token = str(raw).strip()
    if not token or not token.isascii() or not token.isdigit():
        return None
    return int(token)


_PROCESS_GATE: BinanceRestGate | None = None
_PROCESS_GATE_GUARD = threading.Lock()


def process_binance_rest_gate() -> BinanceRestGate:
    global _PROCESS_GATE
    with _PROCESS_GATE_GUARD:
        if _PROCESS_GATE is None:
            _PROCESS_GATE = BinanceRestGate(**_gate_settings())
        return _PROCESS_GATE


def _gate_settings() -> dict[str, float]:
    """Warn settings from config. Unreadable settings keep the module defaults."""

    try:
        from app.core.config import get_settings

        settings = get_settings()
        return {
            "used_weight_warn_threshold": settings.binance_used_weight_warn_threshold,
            "used_weight_warn_interval_seconds": (
                settings.binance_used_weight_warn_interval_seconds
            ),
        }
    except Exception:
        return {}


def process_binance_rest_gate_state() -> RestGateState | None:
    """State of the process gate, or None when no Binance client created it."""

    with _PROCESS_GATE_GUARD:
        gate = _PROCESS_GATE
    return None if gate is None else gate.state()


def reset_process_binance_rest_gate() -> None:
    """Tests use this to simulate a fresh process."""

    global _PROCESS_GATE
    with _PROCESS_GATE_GUARD:
        _PROCESS_GATE = None
