"""Share the Binance REST gate across paper-worker instances through PostgreSQL.

On 2026-11-04 a Render deploy overlapped up to three worker instances behind
one egress IP. Each process gate (``binance_rest_gate``) only knew its own
traffic, so together they earned an 18-minute HTTP 418 ban. This coordinator
adds two cross-instance facts on top of the process gate:

* Shared deadline. A 418/429 deadline is written to ``binance_rest_coordination``
  (the later deadline wins). At start and on every heartbeat each instance
  adopts the stored deadline, so a new instance does not call during a ban
  another instance earned.
* Startup lease ("binance market reads"). A new instance that finds a live
  lease of another instance holds its gate closed (a standby that reads as a
  rate-limit pause, so callers fail closed) until the lease is released or
  expires, at most ``max_wait_seconds``. The holder renews it every heartbeat
  and releases it on clean shutdown (SIGTERM). After max wait the instance
  proceeds and keeps trying to take the lease.

Scope: only the paper worker starts this. The API process (agent chat reads)
keeps its own process gate and is never held by the lease; a chat read is a
few weighted requests on user demand, while the worker owns the sustained
market polling that causes bans.

Every database error is logged and ignored: the process gate keeps working
alone (fail open to the in-process gate). Nothing here raises into the worker.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.persistence.binance_rest_coordination import (
    load_gate_deadline,
    record_gate_deadline,
    release_market_lease,
    try_acquire_market_lease,
)

logger = structlog.get_logger(__name__)

# While in standby, look for the lease more often than the heartbeat.
_STANDBY_POLL_SECONDS = 5.0


class BinanceGateCoordinator:
    """Heartbeat thread for the shared deadline and the market-read lease."""

    def __init__(
        self,
        gate: BinanceRestGate,
        session_factory: sessionmaker[Session],
        *,
        owner: str,
        persistence_enabled: bool = True,
        lease_enabled: bool = True,
        lease_ttl_seconds: float = 90.0,
        heartbeat_seconds: float = 20.0,
        max_wait_seconds: float = 240.0,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gate = gate
        self._factory = session_factory
        self.owner = owner[:80]
        self._persist = persistence_enabled
        self._lease = lease_enabled
        self._ttl = float(lease_ttl_seconds)
        # A heartbeat at or above the TTL would let the lease lapse between renewals.
        self._heartbeat = min(float(heartbeat_seconds), self._ttl / 3.0)
        self._max_wait = max(float(max_wait_seconds), 0.0)
        self._now = now if now is not None else _utc_now
        # Lease times come from the database clock unless a test injects ``now``.
        self._lease_now = now
        self._monotonic = monotonic
        self._pending: tuple[float, str] | None = None
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._standby_until: float | None = None
        self._standby_started: float | None = None
        self._adopted_until: float | None = None
        self.lease_held = False

    def start(self) -> None:
        """Adopt the shared deadline and claim the lease before any market read."""

        if self._persist:
            self._gate.set_failure_listener(self._on_gate_failure)
            self._adopt_shared_deadline()
        if self._lease:
            self._step_lease(starting=True)
        self._thread = threading.Thread(
            target=self._run, name="binance-gate-coordination", daemon=True
        )
        self._thread.start()

    def stop(self, *, join_timeout_seconds: float = 5.0) -> None:
        """Flush a pending deadline for the successor, then release the lease."""

        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_seconds)
        if self._persist:
            self._gate.set_failure_listener(None)
            self._flush_pending()
        if self._lease and self.lease_held:
            try:
                released = release_market_lease(self._factory, owner=self.owner)
            except Exception as exc:
                logger.warning("binance_market_lease_release_failed", error_type=type(exc).__name__)
            else:
                self.lease_held = False
                logger.info("binance_market_lease_released", owner=self.owner, released=released)

    def run_once(self) -> None:
        """One heartbeat. Public for tests; the thread calls it."""

        if self._persist:
            self._flush_pending()
            self._adopt_shared_deadline()
        if self._lease:
            self._step_lease(starting=False)

    def _run(self) -> None:
        while not self._stop.is_set():
            interval = self._heartbeat
            if self._in_standby():
                interval = min(interval, _STANDBY_POLL_SECONDS)
            self._wake.wait(interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.run_once()
            except Exception as exc:  # pragma: no cover - each step already guards
                logger.warning("binance_gate_coordination_failed", error_type=type(exc).__name__)

    def _on_gate_failure(self, kind: str, until_epoch: float) -> None:
        """Gate listener: remember the latest deadline and wake the thread to write it."""

        with self._pending_lock:
            current = self._pending
            if current is None or until_epoch > current[0]:
                kind = "ban" if current is not None and current[1] == "ban" else kind
                self._pending = (until_epoch, kind)
            elif kind == "ban":
                self._pending = (current[0], "ban")
        self._wake.set()

    def _flush_pending(self) -> None:
        with self._pending_lock:
            pending = self._pending
        if pending is None:
            return
        until_epoch, kind = pending
        now = self._now()
        try:
            stored = record_gate_deadline(
                self._factory,
                kind=kind,
                until=datetime.fromtimestamp(until_epoch, tz=UTC),
                writer=self.owner,
                now=now,
            )
        except Exception as exc:
            # Keep it pending; the next heartbeat retries.
            logger.warning("binance_gate_deadline_write_failed", error_type=type(exc).__name__)
            return
        with self._pending_lock:
            if self._pending == pending:
                self._pending = None
        logger.info(
            "binance_gate_deadline_shared",
            kind=stored.kind,
            until=stored.until.isoformat(),
        )

    def _adopt_shared_deadline(self) -> None:
        try:
            shared = load_gate_deadline(self._factory, now=self._now())
        except Exception as exc:
            logger.warning("binance_gate_deadline_read_failed", error_type=type(exc).__name__)
            return
        if shared is None:
            return
        until_epoch = shared.until.timestamp()
        seconds = self._gate.adopt_deadline(shared.kind, until_epoch)
        if seconds > 0 and until_epoch != self._adopted_until:
            self._adopted_until = until_epoch
            logger.warning(
                "binance_gate_shared_deadline_adopted",
                kind=shared.kind,
                until=shared.until.isoformat(),
                seconds_left=round(seconds, 1),
            )

    def _step_lease(self, *, starting: bool) -> None:
        try:
            claim = try_acquire_market_lease(
                self._factory,
                owner=self.owner,
                ttl_seconds=self._ttl,
                now=None if self._lease_now is None else self._lease_now(),
            )
        except Exception as exc:
            # Fail open: without the database the process gate alone applies.
            logger.warning("binance_market_lease_unavailable", error_type=type(exc).__name__)
            if self._in_standby():
                self._end_standby(reason="lease_store_unavailable")
            return
        if claim.acquired:
            if not self.lease_held:
                logger.info("binance_market_lease_acquired", owner=self.owner)
            self.lease_held = True
            if self._standby_until is not None:
                self._end_standby(reason="lease_acquired")
            return
        if self.lease_held:
            logger.warning("binance_market_lease_lost", holder=claim.holder)
        self.lease_held = False
        if starting and self._max_wait > 0:
            self._standby_started = self._monotonic()
            self._standby_until = self._standby_started + self._max_wait
            self._gate.hold(self._max_wait)
            logger.warning(
                "binance_market_reads_standby",
                holder=claim.holder,
                holder_expires_at=None
                if claim.expires_at is None
                else claim.expires_at.isoformat(),
                max_wait_seconds=self._max_wait,
            )
        elif self._standby_until is not None and not self._in_standby():
            # The gate hold already ran out; reads go ahead without the lease.
            self._standby_until = None
            logger.warning(
                "binance_market_lease_wait_expired",
                holder=claim.holder,
                max_wait_seconds=self._max_wait,
            )

    def _in_standby(self) -> bool:
        return self._standby_until is not None and self._monotonic() < self._standby_until

    def _end_standby(self, *, reason: str) -> None:
        waited = (
            None
            if self._standby_started is None
            else round(self._monotonic() - self._standby_started, 1)
        )
        self._standby_until = None
        self._gate.release_hold()
        logger.info("binance_market_reads_resumed", reason=reason, waited_seconds=waited)


def start_binance_gate_coordination(
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
) -> BinanceGateCoordinator | None:
    """Start coordination for a live Binance worker. Never raises; None when off."""

    from app.market_contracts.adapters.factory import perpetual_source_is_replay

    persist = settings.binance_gate_persistence_enabled
    lease = settings.binance_market_lease_enabled
    if not (persist or lease) or perpetual_source_is_replay(settings):
        return None
    try:
        from app.db.session import get_session_factory
        from app.market_contracts.adapters.binance_rest_gate import process_binance_rest_gate

        coordinator = BinanceGateCoordinator(
            process_binance_rest_gate(),
            session_factory if session_factory is not None else get_session_factory(),
            owner=_instance_owner(),
            persistence_enabled=persist,
            lease_enabled=lease,
            lease_ttl_seconds=settings.binance_market_lease_ttl_seconds,
            heartbeat_seconds=settings.binance_market_lease_heartbeat_seconds,
            max_wait_seconds=settings.binance_market_lease_max_wait_seconds,
        )
        coordinator.start()
    except Exception as exc:
        logger.warning("binance_gate_coordination_unavailable", error_type=type(exc).__name__)
        return None
    return coordinator


def _instance_owner() -> str:
    """Unique per process: Render instance (or host), pid and a random suffix."""

    host = os.environ.get("RENDER_INSTANCE_ID") or socket.gethostname()
    return f"{host[:48]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _utc_now() -> datetime:
    return datetime.now(UTC)
