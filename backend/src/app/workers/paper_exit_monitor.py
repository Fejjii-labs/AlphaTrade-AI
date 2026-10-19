"""Paper-worker component that closes canonical internal paper trades at SL/TP.

Disabled unless ``PAPER_EXIT_MONITOR_ENABLED`` is true. ``PAPER_EXIT_MONITOR_DRY_RUN``
logs the closes it would record and does not call the paper close service.
The write path is :meth:`ExecutionService.close_canonical_paper_plan`, the
same method as ``POST /paper-plan/close``. No exchange write is added.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.guardrails.redaction import redact_text
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.execution_protocol import ClosePaperPlanRequest, ClosePaperPlanResult
from app.services.paper_exit_candles import PaperExitCandleLoader
from app.services.paper_exit_monitor import (
    FetchedBars,
    MonitorReport,
    OpenInternalPaperTrade,
    PaperExitCloser,
    load_open_internal_paper_trades,
    run_paper_exit_monitor,
)
from app.workers.paper_worker import CycleOutcome

logger = structlog.get_logger(__name__)


class ExecutionServicePaperExitCloser(PaperExitCloser):
    """One session per close. Commit only after the shared close service returns."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._runtime: ProductionCanonicalRuntime | None = None

    def close(self, request: ClosePaperPlanRequest) -> ClosePaperPlanResult:
        from app.runtime.canonical import build_production_canonical_runtime
        from app.services.audit_service import AuditService
        from app.services.execution_service import ExecutionService

        if self._runtime is None:
            self._runtime = build_production_canonical_runtime(
                self._session_factory,
                settings=self._settings,
            )
        session = self._session_factory()
        try:
            result = ExecutionService(
                session,
                self._settings,
                AuditService(session),
                canonical_runtime=self._runtime,
            ).close_canonical_paper_plan(request)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def run_configured_exit_monitor(
    settings: Settings,
    *,
    shutdown: Callable[[], bool] | None = None,
    session_factory: sessionmaker[Session] | None = None,
    closer: PaperExitCloser | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MonitorReport:
    """One monitor pass. The flag off returns before any database or market read."""

    if not settings.paper_exit_monitor_enabled:
        return MonitorReport(status="disarmed")
    if shutdown is not None and shutdown():
        return MonitorReport(status="stopped")
    factory = session_factory or _session_factory()
    evaluated_at = clock() if clock is not None else datetime.now(UTC)
    session = factory()
    try:
        trades, rejected = load_open_internal_paper_trades(session)
        session.rollback()
    finally:
        session.close()
    loader = PaperExitCandleLoader(settings)

    def candles(trade: OpenInternalPaperTrade) -> FetchedBars:
        return loader(trade, evaluated_at=evaluated_at)

    exit_closer = closer
    if exit_closer is None and not settings.paper_exit_monitor_dry_run:
        exit_closer = ExecutionServicePaperExitCloser(factory, settings)
    if exit_closer is None:
        exit_closer = _RefusingCloser()
    return run_paper_exit_monitor(
        enabled=True,
        dry_run=settings.paper_exit_monitor_dry_run,
        trades=trades,
        candles=candles,
        closer=exit_closer,
        evaluated_at=evaluated_at,
        rejected=rejected,
        shutdown=shutdown,
    )


class PaperExitMonitorCycle:
    """One supervised component of the existing paper-worker loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings.model_copy(deep=True)
        self._stop = Event()

    def __call__(self) -> CycleOutcome:
        if self._stop.is_set():
            return CycleOutcome("stopped")
        if not self._settings.paper_exit_monitor_enabled:
            return CycleOutcome("disarmed")
        try:
            report = run_configured_exit_monitor(
                self._settings,
                shutdown=self._stop.is_set,
            )
        except Exception as exc:
            logger.warning(
                "paper_exit_monitor_cycle_failed",
                error=redact_text(str(exc))[:200],
            )
            return CycleOutcome("degraded", error="paper_exit_monitor_failed")
        if report.status == "stopped":
            return CycleOutcome("stopped")
        if report.status == "degraded":
            return CycleOutcome("degraded", error=report.error or "paper_exit_monitor_failed")
        return CycleOutcome("running")

    def request_stop(self) -> None:
        self._stop.set()


class _RefusingCloser(PaperExitCloser):
    """Dry-run never calls this. A call is a programming error, not a close."""

    def close(self, request: ClosePaperPlanRequest) -> ClosePaperPlanResult:
        raise RuntimeError("paper exit monitor dry-run attempted a close")


def _session_factory() -> sessionmaker[Session]:
    from app.db.session import get_session_factory

    return get_session_factory()
