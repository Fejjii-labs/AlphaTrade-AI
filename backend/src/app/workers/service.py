"""Worker orchestration: one safe scan cycle with locking and dead-lettering."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import MarketScanRun
from app.guardrails.redaction import redact_text
from app.workers.lock import WorkerLock
from app.workers.repository import MarketScanRunRepository, WorkerHeartbeatRepository

if TYPE_CHECKING:
    from app.runtime.canonical import ProductionCanonicalRuntime

logger = structlog.get_logger(__name__)

# A scanner inspects market data and returns (symbols_scanned, setups_detected).
Scanner = Callable[[Session], tuple[int, int]]


def _noop_scanner(_session: Session) -> tuple[int, int]:
    return 0, 0


@dataclass(frozen=True)
class ScanResult:
    """Outcome of a single worker cycle."""

    status: str  # "success" | "skipped" | "failed"
    symbols_scanned: int = 0
    setups_detected: int = 0
    reason: str | None = None
    error: str | None = None


class WorkerService:
    """Runs scan cycles under a single-runner lock and records observability rows."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        lock: WorkerLock,
        *,
        worker_name: str,
        scanner: Scanner | None = None,
        on_cycle: Callable[[ScanResult], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        canonical_runtime: ProductionCanonicalRuntime | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lock = lock
        self._worker_name = worker_name
        self._scanner = scanner or _noop_scanner
        self._on_cycle = on_cycle
        self._clock = clock
        self.canonical_runtime = canonical_runtime

    def set_paused(self, paused: bool) -> None:
        """Manually pause or resume scanning (persisted on the heartbeat)."""
        with self._session_factory() as session:
            repo = WorkerHeartbeatRepository(session)
            repo.set_paused(
                worker_name=self._worker_name,
                paused=paused,
            )
            session.commit()

    def is_paused(self) -> bool:
        with self._session_factory() as session:
            row = WorkerHeartbeatRepository(session).get_by_name(self._worker_name)
            return bool(row and row.paused)

    def run_cycle(self) -> ScanResult:
        """Execute one cycle: skip if paused/locked, else scan and record."""
        if self.is_paused():
            self._beat(status="paused")
            return ScanResult(status="skipped", reason="paused")

        token = self._lock.try_acquire()
        if token is None:
            return ScanResult(status="skipped", reason="lock_not_acquired")

        started = self._clock()
        perf_start = time.perf_counter()
        try:
            with self._session_factory() as session:
                symbols, setups = self._scanner(session)
                session.commit()
            latency_ms = (time.perf_counter() - perf_start) * 1000
            self._record_run(
                status="success",
                started_at=started,
                symbols=symbols,
                setups=setups,
                latency_ms=latency_ms,
            )
            self._beat(status="running", increment=True)
            # Bounded backtest drain: at most one QUEUED run per successful cycle.
            self._drain_one_backtest()
            result = ScanResult(status="success", symbols_scanned=symbols, setups_detected=setups)
            self._emit(result)
            return result
        except Exception as exc:  # dead-letter; the loop must keep running
            error = redact_text(str(exc))[:500]
            logger.error("worker_cycle_failed", worker=self._worker_name, error=error)
            latency_ms = (time.perf_counter() - perf_start) * 1000
            self._record_run(
                status="failed",
                started_at=started,
                symbols=0,
                setups=0,
                latency_ms=latency_ms,
                error=error,
            )
            self._beat(status="error", detail=error[:255])
            result = ScanResult(status="failed", error=error)
            self._emit(result)
            return result
        finally:
            self._lock.release(token)

    def _drain_one_backtest(self) -> None:
        """Execute at most one QUEUED backtest; never raise into the scan cycle."""
        try:
            with self._session_factory() as session:
                from app.repositories.backtest import BacktestRunRepository
                from app.services.backtest_service import BacktestService

                queued = BacktestRunRepository(session).next_queued()
                if queued is None:
                    return
                BacktestService(session).execute_run(
                    queued.id, organization_id=queued.organization_id
                )
                session.commit()
        except Exception as exc:
            logger.warning(
                "backtest_drain_failed",
                worker=self._worker_name,
                error=redact_text(str(exc))[:200],
            )

    def _emit(self, result: ScanResult) -> None:
        if self._on_cycle is None:
            return
        try:
            self._on_cycle(result)
        except Exception:  # notifications must never break the worker loop
            logger.warning("worker_on_cycle_hook_failed", worker=self._worker_name)

    def _record_run(
        self,
        *,
        status: str,
        started_at: datetime,
        symbols: int,
        setups: int,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            MarketScanRunRepository(session).add(
                MarketScanRun(
                    worker_name=self._worker_name,
                    status=status,
                    symbols_scanned=symbols,
                    setups_detected=setups,
                    started_at=started_at,
                    finished_at=self._clock(),
                    latency_ms=latency_ms,
                    error=error,
                )
            )
            session.commit()

    def _beat(self, *, status: str, increment: bool = False, detail: str | None = None) -> None:
        with self._session_factory() as session:
            repo = WorkerHeartbeatRepository(session)
            current = repo.get_by_name(self._worker_name)
            cycle_count = (current.cycle_count if current else 0) + (1 if increment else 0)
            repo.upsert(
                worker_name=self._worker_name,
                status=status,
                last_beat_at=self._clock(),
                cycle_count=cycle_count,
                detail=detail,
            )
            session.commit()
