"""Paper-worker component that closes canonical internal paper trades at SL/TP.

Disabled unless ``PAPER_EXIT_MONITOR_ENABLED`` is true. ``PAPER_EXIT_MONITOR_DRY_RUN``
logs the closes it would record and does not call the paper close service.
The write path is :meth:`ExecutionService.close_canonical_paper_plan`, the
same method as ``POST /paper-plan/close``. No exchange write is added.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.guardrails.redaction import redact_text
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.execution_protocol import ClosePaperPlanRequest, ClosePaperPlanResult
from app.services.paper_exit_candles import PaperExitCandleLoader
from app.services.paper_exit_monitor import (
    DegradationStreak,
    ExitMonitorAlert,
    FetchedBars,
    MonitorReport,
    OpenInternalPaperTrade,
    PaperExitCloser,
    build_clock_alert,
    clock_refusal_reason,
    load_open_internal_paper_trades,
    observe_repeated_degradation,
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


def read_database_now(session: Session) -> datetime | None:
    """Database ``now()``, or None when the session cannot return an aware time."""

    try:
        value = session.scalar(select(func.now()))
    except Exception:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def run_configured_exit_monitor(
    settings: Settings,
    *,
    shutdown: Callable[[], bool] | None = None,
    session_factory: sessionmaker[Session] | None = None,
    closer: PaperExitCloser | None = None,
    clock: Callable[[], datetime] | None = None,
    exchange_time: Callable[[], datetime | None] | None = None,
    alert_sink: Callable[[ExitMonitorAlert], None] | None = None,
) -> MonitorReport:
    """One monitor pass. The flag off returns before any database or market read.

    A disarmed paper worker returns here even when the flag is true. It does
    not open PostgreSQL and it does not read public candles. A wall clock that
    disagrees with database ``now()``, or with an exchange server time when
    that reader is supplied, refuses the cycle before any candle read.
    """

    from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker

    if settings_are_disarmed_paper_worker(settings) or not settings.paper_exit_monitor_enabled:
        return MonitorReport(status="disarmed")
    if shutdown is not None and shutdown():
        return MonitorReport(status="stopped")
    factory = session_factory or _session_factory()
    wall = clock() if clock is not None else datetime.now(UTC)
    max_skew = timedelta(seconds=int(settings.paper_exit_monitor_max_clock_skew_seconds))
    session = factory()
    try:
        trusted, trusted_source = _trusted_times(session, exchange_time)
        reason = clock_refusal_reason(wall, trusted, max_skew=max_skew)
        if reason is not None:
            trades, _rejected = load_open_internal_paper_trades(session)
            alert = build_clock_alert(
                reason=reason,
                wall=wall,
                trusted=trusted,
                max_skew=max_skew,
                trusted_source=trusted_source,
            )
            try:
                _emit_clock_alert(session, alert, trades, alert_sink)
            except Exception as exc:
                session.rollback()
                logger.warning(
                    "paper_exit_monitor_alert_persist_failed",
                    error=redact_text(str(exc))[:200],
                    code=alert.code,
                )
            logger.warning("paper_exit_monitor_alert", **alert.as_log())
            return MonitorReport(
                status="refused",
                error=reason,
                alerts=(alert,),
                organization_ids=tuple(dict.fromkeys(trade.organization_id for trade in trades)),
            )
        evaluated_at = trusted[0]
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

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        alert_sink: Callable[[ExitMonitorAlert], None] | None = None,
    ) -> None:
        self._settings = settings.model_copy(deep=True)
        self._stop = Event()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._alert_sink = alert_sink
        self._streak: DegradationStreak | None = None

    def __call__(self) -> CycleOutcome:
        from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker

        if self._stop.is_set():
            return CycleOutcome("stopped")
        if settings_are_disarmed_paper_worker(self._settings) or (
            not self._settings.paper_exit_monitor_enabled
        ):
            return CycleOutcome("disarmed")
        try:
            report = run_configured_exit_monitor(
                self._settings,
                shutdown=self._stop.is_set,
                clock=self._clock,
            )
        except Exception as exc:
            self._note_repetition("degraded", error="paper_exit_monitor_failed", refused=0)
            logger.warning(
                "paper_exit_monitor_cycle_degraded",
                error=redact_text(str(exc))[:200],
                failed=True,
            )
            return CycleOutcome("degraded", error="paper_exit_monitor_failed")
        if report.status == "stopped":
            return CycleOutcome("stopped")
        if report.status == "refused":
            self._note_repetition(
                "refused",
                error=report.error or "paper_exit_monitor_refused",
                refused=report.refused,
                report=report,
            )
            logger.warning(
                "paper_exit_monitor_cycle_refused",
                error=report.error or "paper_exit_monitor_refused",
                examined=report.examined,
            )
            return CycleOutcome("refused", error=report.error or "paper_exit_monitor_refused")
        if report.status == "degraded":
            self._note_repetition(
                "degraded",
                error=report.error or "paper_exit_monitor_failed",
                refused=report.refused,
                report=report,
            )
            logger.warning(
                "paper_exit_monitor_cycle_degraded",
                error=report.error or "paper_exit_monitor_failed",
                examined=report.examined,
                errors=report.errors,
                refused=report.refused,
                skipped=report.skipped,
                failed=report.errors > 0,
            )
            return CycleOutcome("degraded", error=report.error or "paper_exit_monitor_failed")
        self._note_repetition(report.status, error="", refused=0, report=report)
        return CycleOutcome("running")

    def request_stop(self) -> None:
        self._stop.set()

    def _note_repetition(
        self,
        status: str,
        *,
        error: str,
        refused: int,
        report: MonitorReport | None = None,
    ) -> None:
        seconds = int(self._settings.paper_exit_monitor_degraded_alert_after_seconds)
        threshold = timedelta(seconds=seconds)
        watch = observe_repeated_degradation(
            self._streak,
            status=status,
            observed_at=self._clock(),
            threshold=threshold,
            error=error,
            refused=refused,
        )
        self._streak = watch.streak
        if watch.alert is None:
            return
        logger.warning("paper_exit_monitor_alert", **watch.alert.as_log())
        self._emit_repeat_alert(watch.alert, report)

    def _emit_repeat_alert(
        self,
        alert: ExitMonitorAlert,
        report: MonitorReport | None,
    ) -> None:
        if self._alert_sink is not None:
            self._alert_sink(alert)
            return
        factory = _session_factory()
        session = factory()
        try:
            from app.services.paper_exit_protection import record_exit_monitor_alert

            orgs = () if report is None else report.organization_ids
            if orgs:
                for organization_id in orgs:
                    record_exit_monitor_alert(
                        session,
                        alert,
                        organization_id=organization_id,
                        user_id=None,
                    )
            else:
                record_exit_monitor_alert(
                    session,
                    alert,
                    organization_id=None,
                    user_id=None,
                )
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning(
                "paper_exit_monitor_alert_persist_failed",
                error=redact_text(str(exc))[:200],
                code=alert.code,
            )
        finally:
            session.close()


def _trusted_times(
    session: Session,
    exchange_time: Callable[[], datetime | None] | None,
) -> tuple[tuple[datetime, ...], str]:
    """Database ``now()``, plus exchange server time when the caller supplies it."""

    moments: list[datetime] = []
    labels: list[str] = []
    database_now = read_database_now(session)
    if database_now is not None:
        moments.append(database_now)
        labels.append("database")
    if exchange_time is not None:
        try:
            exchange_now = exchange_time()
        except Exception:
            exchange_now = None
        if isinstance(exchange_now, datetime):
            moments.append(exchange_now)
            labels.append("exchange")
    source = "+".join(labels) if labels else "none"
    return tuple(moments), source


def _emit_clock_alert(
    session: Session,
    alert: ExitMonitorAlert,
    trades: tuple[OpenInternalPaperTrade, ...],
    alert_sink: Callable[[ExitMonitorAlert], None] | None,
) -> None:
    if alert_sink is not None:
        alert_sink(alert)
        session.rollback()
        return
    from app.services.paper_exit_protection import record_exit_monitor_alert

    targets = _alert_targets(trades)
    if not targets:
        record_exit_monitor_alert(
            session,
            alert,
            organization_id=None,
            user_id=None,
        )
    else:
        for organization_id, user_id in targets:
            record_exit_monitor_alert(
                session,
                alert,
                organization_id=organization_id,
                user_id=user_id,
            )
    session.commit()


def _alert_targets(
    trades: tuple[OpenInternalPaperTrade, ...],
) -> tuple[tuple[UUID, UUID], ...]:
    seen: dict[UUID, UUID] = {}
    for trade in trades:
        seen.setdefault(trade.organization_id, trade.user_id)
    return tuple(seen.items())


class _RefusingCloser(PaperExitCloser):
    """Dry-run never calls this. A call is a programming error, not a close."""

    def close(self, request: ClosePaperPlanRequest) -> ClosePaperPlanResult:
        raise RuntimeError("paper exit monitor dry-run attempted a close")


def _session_factory() -> sessionmaker[Session]:
    from app.db.session import get_session_factory

    return get_session_factory()
