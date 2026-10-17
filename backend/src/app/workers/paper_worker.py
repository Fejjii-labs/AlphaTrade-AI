"""Supervised paper worker. Hosts Watcher and Telegram; real trading stays disabled.

One process supervises the two runtimes. Each has its own thread, health
record, and failure counter. A cycle runs outside the other component's lock.
Settings given to each runtime are a copy, so a Watcher failure cannot change
Telegram authority and a Telegram failure cannot rewrite Watcher state.

PostgreSQL scan leases and the Telegram runtime lease stay inside the existing
runtimes. This module does not scan twice and does not deliver twice. It does
not start the API and it does not start the Slice 59 worker entrypoint.

Disarmed boot does not open PostgreSQL, Redis, or provider clients. Armed boot
keeps the existing dependency and secret validation.
"""

from __future__ import annotations

import os
import selectors
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import FrameType
from typing import Protocol

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Environment, Settings
from app.observability.process_memory import read_process_memory, release_allocator_memory
from app.observability.worker_memory import CycleMemorySampler, WorkerMemoryDiagnostics
from app.workers.watcher_paper import (
    WatcherPaperCycleReport,
    WatcherPaperRuntime,
    paper_runtime_enabled,
)

logger = structlog.get_logger("workers.paper_worker")

_AUTHORITY_FIELDS = (
    "enable_real_trading",
    "real_trading_enabled",
    "execution_mode",
    "exchange_mode",
    "blofin_demo_enabled",
    "governed_blofin_demo_enabled",
    "governed_blofin_demo_organization_id",
    "governed_blofin_demo_user_id",
    "governed_blofin_demo_account_id",
    "watcher_orchestration_enabled",
    "watcher_paper_staging_activation",
    "market_watcher_enabled",
    "market_watcher_bridge_enabled",
    "market_watcher_bridge_auto_tick",
    "telegram_alerts_enabled",
    "telegram_interaction_enabled",
    "automatic_telegram_delivery_enabled",
    "telegram_paper_activation_armed",
    "telegram_inbound_mode",
    "telegram_network_permitted",
)

AuthorityProbe = Callable[[], tuple[object, ...]]
AuthorityRestore = Callable[[], None]


class AuthorityDriftError(RuntimeError):
    """A component changed a paper-authority flag during its cycle."""

    def __init__(self, component: str) -> None:
        super().__init__(f"authority_drift:{component}")
        self.component = component


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    """One supervised cycle. Exceptions are failures. Outcome errors are not."""

    status: str
    error: str = ""
    record_scan: bool = False
    delivered: int = 0
    kill_switch_active: bool = False


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Observed state of one supervised runtime."""

    component: str
    status: str
    last_heartbeat_at: datetime | None
    last_scan_at: datetime | None
    last_delivery_at: datetime | None
    last_error: str
    restart_count: int
    cycles_completed: int
    kill_switch_active: bool


@dataclass(frozen=True, slots=True)
class PaperWorkerHealth:
    """Watcher and Telegram health. Neither field is the other's state."""

    watcher: ComponentHealth
    telegram: ComponentHealth
    stopping: bool
    authority_intact: bool
    indexing: ComponentHealth | None = None
    activity: ComponentHealth | None = None


@dataclass(frozen=True, slots=True)
class _AuthorityGuard:
    probe: AuthorityProbe
    restore: AuthorityRestore
    baseline: tuple[object, ...]

    def drifted(self) -> bool:
        return self.probe() != self.baseline

    def revert(self) -> None:
        self.restore()


class _Component:
    """Private health for one runtime. The other runtime never receives this."""

    def __init__(
        self,
        name: str,
        cycle: Callable[[], CycleOutcome],
        guard: _AuthorityGuard | None,
        poll_seconds: float | None = None,
    ) -> None:
        self.name = name
        self.cycle = cycle
        self.guard = guard
        self.poll_seconds = poll_seconds
        self.lock = threading.Lock()
        self.status = "idle"
        self.last_heartbeat_at: datetime | None = None
        self.last_scan_at: datetime | None = None
        self.last_delivery_at: datetime | None = None
        self.last_error = ""
        self.restart_count = 0
        self.cycles_completed = 0
        self.kill_switch_active = False
        self.thread: threading.Thread | None = None

    def health(self) -> ComponentHealth:
        with self.lock:
            return ComponentHealth(
                component=self.name,
                status=self.status,
                last_heartbeat_at=self.last_heartbeat_at,
                last_scan_at=self.last_scan_at,
                last_delivery_at=self.last_delivery_at,
                last_error=self.last_error,
                restart_count=self.restart_count,
                cycles_completed=self.cycles_completed,
                kill_switch_active=self.kill_switch_active,
            )


class PaperWorkerSupervisor:
    """Run Watcher and Telegram until asked to stop. Failures stay local."""

    def __init__(
        self,
        *,
        watcher_cycle: Callable[[], CycleOutcome],
        telegram_cycle: Callable[[], CycleOutcome],
        poll_seconds: float = 15.0,
        join_timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        watcher_authority: tuple[AuthorityProbe, AuthorityRestore] | None = None,
        telegram_authority: tuple[AuthorityProbe, AuthorityRestore] | None = None,
        on_stop: Callable[[], None] | None = None,
        memory_diagnostics_enabled: bool = False,
    ) -> None:
        self._poll_seconds = poll_seconds
        self._join_timeout_seconds = join_timeout_seconds
        self._clock = clock if clock is not None else _utc_now
        self._on_stop = on_stop
        self._stop = threading.Event()
        self._signaled = False
        self._start_lock = threading.Lock()
        self._closed = False
        self._authority_intact = True
        self._watcher = _Component("watcher", watcher_cycle, _guard(watcher_authority))
        self._telegram = _Component("telegram", telegram_cycle, _guard(telegram_authority))
        self._indexing: _Component | None = None
        self._indexing_stop: Callable[[], None] | None = None
        self._activity: _Component | None = None
        self._activity_stop: Callable[[], None] | None = None
        self._memory_diagnostics = WorkerMemoryDiagnostics() if memory_diagnostics_enabled else None

    def attach_indexing(
        self,
        cycle: Callable[[], CycleOutcome],
        *,
        close: Callable[[], None],
        request_stop: Callable[[], None] | None = None,
    ) -> None:
        """Attach an independently supervised knowledge loop before process start."""
        self._indexing = _Component("indexing", cycle, None)
        self._indexing_stop = request_stop
        previous = self._on_stop

        def stop() -> None:
            try:
                if previous:
                    previous()
            finally:
                close()

        self._on_stop = stop

    def attach_activity(
        self,
        cycle: Callable[[], CycleOutcome],
        *,
        poll_seconds: float,
        request_stop: Callable[[], None],
    ) -> None:
        """One bounded read-only component in this worker; no separate service."""
        self._activity = _Component("activity", cycle, None, poll_seconds=poll_seconds)
        self._activity_stop = request_stop

    def _components(self) -> tuple[_Component, ...]:
        return (
            (self._watcher, self._telegram)
            + ((self._indexing,) if self._indexing else ())
            + ((self._activity,) if self._activity else ())
        )

    def snapshot(self) -> PaperWorkerHealth:
        """Copy both health records. Callers cannot mutate the supervisor."""

        return PaperWorkerHealth(
            watcher=self._watcher.health(),
            telegram=self._telegram.health(),
            stopping=self._stop.is_set(),
            authority_intact=self._authority_intact,
            indexing=self._indexing.health() if self._indexing else None,
            activity=self._activity.health() if self._activity else None,
        )

    def run_round(self) -> PaperWorkerHealth:
        """Run each component once, on this thread, Watcher then Telegram."""

        for component in self._components():
            self._step(component)
        self._log_health()
        return self.snapshot()

    def start(self) -> None:
        """Start one thread per component. A second call does not add threads."""

        with self._start_lock:
            for component in self._components():
                self._ensure_thread(component)

    def request_stop(self) -> None:
        """Ask both loops to finish the current cycle and not start another."""

        self._stop.set()
        if self._indexing_stop:
            self._indexing_stop()
        if self._activity_stop:
            self._activity_stop()

    def join(self) -> None:
        for component in self._components():
            thread = component.thread
            if thread is not None:
                thread.join(self._join_timeout_seconds)
                if component in (self._indexing, self._activity) and thread.is_alive():
                    thread.join()

    def close(self) -> None:
        """Release runtime resources once. Safe to call more than once."""

        if self._closed:
            return
        if self._activity is not None:
            self.request_stop()
            self.join()
        self._closed = True
        hook = self._on_stop
        if hook is None:
            return
        try:
            hook()
        except Exception:
            logger.warning("paper_worker_stop_hook_failed")

    def serve_until_signal(self, *, ready: Callable[[], None] | None = None) -> None:
        """Block on the main thread until SIGINT or SIGTERM, then join.

        ``ready`` runs after the signal handlers are installed, so a caller
        can observe that SIGTERM will stop the process instead of killing it.
        """

        def _handle(_signum: int, _frame: FrameType | None) -> None:
            # A flag only. Event.set from a handler can deadlock with wait.
            self._signaled = True

        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        previous_wakeup = signal.set_wakeup_fd(write_fd)
        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        selector = selectors.DefaultSelector()
        selector.register(read_fd, selectors.EVENT_READ)
        try:
            self.start()
            if ready is not None:
                ready()
            logger.warning(
                "paper_worker_serving",
                paper_only=True,
                real_trading_enabled=False,
            )
            while not self._signaled:
                selector.select(timeout=self._poll_seconds if self._poll_seconds > 0 else 0.05)
                _drain_wakeup(read_fd)
        finally:
            selector.close()
            signal.set_wakeup_fd(previous_wakeup)
            os.close(read_fd)
            os.close(write_fd)
        self.request_stop()
        self.join()
        for component in self._components():
            self._mark_stopped(component)
        self.close()
        self._log_health()

    def _ensure_thread(self, component: _Component) -> None:
        if component.thread is not None and component.thread.is_alive():
            return
        component.thread = threading.Thread(
            target=self._loop,
            args=(component,),
            name=f"paper-worker-{component.name}",
            daemon=False,
        )
        component.thread.start()

    def _loop(self, component: _Component) -> None:
        while not self._stop.is_set():
            self._step(component)
            interval = (
                component.poll_seconds if component.poll_seconds is not None else self._poll_seconds
            )
            if self._stop.wait(interval if interval > 0 else 0.05):
                break
        self._mark_stopped(component)

    def _step(self, component: _Component) -> None:
        sampler = CycleMemorySampler() if self._memory_diagnostics is not None else None
        try:
            if self._stop.is_set():
                self._mark_stopped(component)
                return
            try:
                outcome = component.cycle()
            except Exception as exc:
                self._revert(component)
                self._fail(component, exc)
                return
            if component.guard is not None and component.guard.drifted():
                self._revert(component)
                self._fail(component, AuthorityDriftError(component.name))
                return
            self._succeed(component, outcome)
        finally:
            if sampler is not None:
                sampler.stop()
            after = release_allocator_memory()
            if sampler is not None:
                assert self._memory_diagnostics is not None
                self._memory_diagnostics.finish(component.name, sampler, after)

    def _revert(self, component: _Component) -> None:
        guard = component.guard
        if guard is None:
            return
        try:
            guard.revert()
        except Exception:
            logger.warning("paper_worker_authority_restore_failed", component=component.name)

    def _fail(self, component: _Component, exc: BaseException) -> None:
        message = _safe_error(exc)
        if "authority_drift" in message:
            self._authority_intact = False
        now = self._clock()
        with component.lock:
            component.status = "failed"
            component.last_heartbeat_at = now
            component.last_error = message
            component.restart_count += 1
        logger.error(
            "paper_worker_component_failed",
            component=component.name,
            error=message,
            restart_count=component.restart_count,
            paper_only=True,
        )

    def _succeed(self, component: _Component, outcome: CycleOutcome) -> None:
        now = self._clock()
        with component.lock:
            component.status = outcome.status
            component.last_heartbeat_at = now
            component.last_error = outcome.error[:120]
            component.kill_switch_active = outcome.kill_switch_active
            component.cycles_completed += 1
            if outcome.record_scan:
                component.last_scan_at = now
            if outcome.delivered > 0:
                component.last_delivery_at = now

    def _mark_stopped(self, component: _Component) -> None:
        with component.lock:
            if component.status != "stopped":
                component.status = "stopped"
                component.last_heartbeat_at = self._clock()

    def _log_health(self) -> None:
        health = self.snapshot()
        memory = read_process_memory()
        logger.info(
            "paper_worker_health",
            rss_bytes=memory.rss_bytes,
            rss_peak_bytes=memory.peak_rss_bytes,
            watcher_status=health.watcher.status,
            watcher_error=health.watcher.last_error,
            watcher_heartbeat=_iso(health.watcher.last_heartbeat_at),
            watcher_last_scan=_iso(health.watcher.last_scan_at),
            telegram_status=health.telegram.status,
            telegram_error=health.telegram.last_error,
            telegram_heartbeat=_iso(health.telegram.last_heartbeat_at),
            telegram_last_delivery=_iso(health.telegram.last_delivery_at),
            indexing_status=health.indexing.status if health.indexing else "disabled",
            indexing_error=health.indexing.last_error if health.indexing else "",
            activity_status=health.activity.status if health.activity else "disabled",
            activity_error=health.activity.last_error if health.activity else "",
            stopping=health.stopping,
            authority_intact=health.authority_intact,
            paper_only=True,
            real_trading_enabled=False,
        )


def isolate_runtime_settings(settings: Settings) -> tuple[Settings, Settings]:
    """Return independent Watcher and Telegram copies of ``settings``."""

    return settings.model_copy(deep=True), settings.model_copy(deep=True)


def authority_binding(settings: Settings) -> tuple[AuthorityProbe, AuthorityRestore]:
    """Probe and restore the paper-authority fields of one settings object."""

    baseline = {name: getattr(settings, name) for name in _AUTHORITY_FIELDS}

    def probe() -> tuple[object, ...]:
        return tuple(getattr(settings, name) for name in _AUTHORITY_FIELDS)

    def restore() -> None:
        for name, value in baseline.items():
            if getattr(settings, name) != value:
                setattr(settings, name, value)

    return probe, restore


def outcome_from_watcher_report(report: WatcherPaperCycleReport) -> CycleOutcome:
    """Map a Watcher cycle onto supervisor health. Does not deliver Telegram."""

    error = ""
    record_scan = False
    for scan in report.scans:
        record_scan = True
        if scan.status == "failed" and scan.reason_code:
            error = scan.reason_code
    if report.kill_switch_active:
        status = "monitoring"
    elif report.enabled and error:
        status = "degraded"
    elif report.enabled:
        status = "running"
    elif report.reason_code == "watcher_disabled":
        status = "disarmed"
    else:
        status = "refused"
        error = error or report.reason_code
    return CycleOutcome(
        status=status,
        error=error,
        record_scan=record_scan,
        kill_switch_active=report.kill_switch_active,
    )


def outcome_from_telegram_cycle(cycle: object) -> CycleOutcome:
    """Map a Telegram cycle. A missed lease is reported, not restarted here."""

    posture = str(getattr(cycle, "posture", "disarmed"))
    delivered = int(getattr(cycle, "delivered", 0))
    killed = bool(getattr(cycle, "kill_switch_active", False))
    held = bool(getattr(cycle, "lease_held", False))
    error = str(getattr(cycle, "last_error_code", ""))
    if killed:
        status = "monitoring"
    elif not held:
        status = "refused"
    elif posture == "disarmed":
        status = "disarmed"
    elif error:
        status = "degraded"
    else:
        status = "running"
    return CycleOutcome(
        status=status,
        error=error,
        delivered=delivered,
        kill_switch_active=killed,
    )


def build_paper_worker_supervisor(settings: Settings) -> PaperWorkerSupervisor:
    """Wire both runtimes, or idle both when the process is disarmed."""

    from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker

    watcher_settings, telegram_settings = isolate_runtime_settings(settings)
    if settings_are_disarmed_paper_worker(settings):
        supervisor = PaperWorkerSupervisor(
            watcher_cycle=_static_cycle("disarmed"),
            telegram_cycle=_static_cycle("disarmed"),
            poll_seconds=float(settings.watcher_paper_poll_interval_seconds),
            watcher_authority=authority_binding(watcher_settings),
            telegram_authority=authority_binding(telegram_settings),
            memory_diagnostics_enabled=settings.paper_worker_memory_diagnostics_enabled,
        )
    else:
        supervisor = _build_armed_supervisor(watcher_settings, telegram_settings)
    if settings.knowledge_indexing_enabled:
        from app.workers.knowledge_indexing import KnowledgeIndexingCycle

        cycle = KnowledgeIndexingCycle(settings.model_copy(deep=True))
        supervisor.attach_indexing(cycle, close=cycle.close, request_stop=cycle.request_stop)
    from app.services.blofin_activity_config import get_activity_settings

    activity_config = get_activity_settings()
    if activity_config.enabled:
        from app.workers.blofin_activity import BloFinActivityCycle

        activity = BloFinActivityCycle(settings.model_copy(deep=True), activity_config)
        supervisor.attach_activity(
            activity, poll_seconds=activity_config.poll_seconds, request_stop=activity.request_stop
        )
    return supervisor


def run_paper_worker_process(*, once: bool = False) -> str:
    """Start the supervised paper worker, or idle once when disarmed.

    Disarmed startup constructs Settings and returns ``disarmed`` without
    opening PostgreSQL, Redis, or provider clients. Armed startup does not
    bind the disarmed role, so missing operational dependencies fail closed.
    """

    from app.core.disarmed_worker_boot import (
        WorkerBootRole,
        load_worker_process_settings,
        settings_are_disarmed_paper_worker,
    )
    from app.core.logging import configure_logging
    from app.core.paper_safety import assert_execution_capable_composition_root

    settings = load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    configure_logging(log_level=settings.log_level, json_logs=settings.log_json)
    assert_execution_capable_composition_root(settings)
    disarmed = settings_are_disarmed_paper_worker(settings)
    supervisor = build_paper_worker_supervisor(settings)
    logger.warning(
        "paper_worker_boot",
        posture="disarmed" if disarmed else "armed",
        paper_only=True,
        enable_real_trading=settings.enable_real_trading,
        real_trading_enabled=settings.real_trading_enabled,
    )
    try:
        if once:
            supervisor.run_round()
        else:
            supervisor.serve_until_signal()
    finally:
        supervisor.close()
    return "disarmed" if disarmed else "armed"


def main() -> None:
    run_paper_worker_process(once=False)


def _build_armed_supervisor(
    watcher_settings: Settings,
    telegram_settings: Settings,
) -> PaperWorkerSupervisor:
    from app.db.session import get_session_factory
    from app.signal_fusion.memory import UtcClock
    from app.telegram_activation.errors import TelegramActivationError
    from app.telegram_activation.runtime import build_telegram_runtime
    from app.workers.watcher_activation import (
        activation_config_from_settings,
        open_staging_watcher_session,
        run_staging_activation,
    )
    from app.workers.watcher_paper import (
        build_watcher_paper_runtime,
        new_worker_instance_id,
    )

    factory = get_session_factory()
    closers: list[Callable[[], None]] = []
    stops: list[Callable[[], None]] = []
    clock = UtcClock()
    instance_id = new_worker_instance_id(watcher_settings.watcher_paper_worker_id)

    watcher_cycle: Callable[[], CycleOutcome]
    if watcher_settings.environment is Environment.STAGING:
        decision = run_staging_activation(
            watcher_settings,
            start=lambda: None,
            worker_instance_id=instance_id,
        )
        if not decision.allowed:
            watcher_cycle = _idle_watcher_cycle(
                watcher_settings,
                factory,
                worker_id=instance_id,
                reason=decision.primary_reason,
                clock=clock,
            )
        else:
            config = activation_config_from_settings(watcher_settings)
            try:
                session = open_staging_watcher_session(
                    watcher_settings,
                    config,
                    instance_id,
                    factory,
                )
            except TelegramActivationError as exc:
                watcher_cycle = _refused_watcher_cycle(exc.reason)
            else:
                closers.append(session.close)
                stops.append(session.runtime.stop)
                watcher_cycle = _scanning_watcher_cycle(session.runtime)
    elif paper_runtime_enabled(watcher_settings):
        runtime = build_watcher_paper_runtime(
            watcher_settings,
            factory,
            worker_id=instance_id,
        )
        stops.append(runtime.stop)
        watcher_cycle = _scanning_watcher_cycle(runtime)
    else:
        watcher_cycle = _static_cycle("disarmed", error="watcher_disabled")

    telegram_runtime = build_telegram_runtime(telegram_settings, factory)
    stops.append(telegram_runtime.stop)

    def telegram_cycle() -> CycleOutcome:
        return outcome_from_telegram_cycle(telegram_runtime.run_cycle())

    existing_watcher_cycle = watcher_cycle

    def research_watcher_cycle() -> CycleOutcome:
        outcome = existing_watcher_cycle()
        # Reuse this component, its opt-in, and its authority guard. Domain start
        # never arms execution; this tick acquires bounded research receipts only.
        if (
            outcome.status in {"running", "degraded"}
            and watcher_settings.trendpulse_screening_enabled
        ):
            from app.experiments.scheduling import tick_existing_worker

            with factory() as research_session:
                tick_existing_worker(research_session, watcher_settings)
                research_session.commit()
        return outcome

    def on_stop() -> None:
        for stop in stops:
            stop()
        for close in closers:
            close()

    return PaperWorkerSupervisor(
        watcher_cycle=research_watcher_cycle,
        telegram_cycle=telegram_cycle,
        poll_seconds=float(watcher_settings.watcher_paper_poll_interval_seconds),
        watcher_authority=authority_binding(watcher_settings),
        telegram_authority=authority_binding(telegram_settings),
        on_stop=on_stop,
        memory_diagnostics_enabled=watcher_settings.paper_worker_memory_diagnostics_enabled,
    )


class _NowClock(Protocol):
    def now(self) -> datetime: ...


def _static_cycle(status: str, *, error: str = "") -> Callable[[], CycleOutcome]:
    def cycle() -> CycleOutcome:
        return CycleOutcome(status=status, error=error)

    return cycle


def _refused_watcher_cycle(reason: str) -> Callable[[], CycleOutcome]:
    def cycle() -> CycleOutcome:
        return CycleOutcome(status="refused", error=reason)

    return cycle


def _scanning_watcher_cycle(runtime: WatcherPaperRuntime) -> Callable[[], CycleOutcome]:
    def cycle() -> CycleOutcome:
        return outcome_from_watcher_report(runtime.run_cycle())

    return cycle


def _idle_watcher_cycle(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    reason: str,
    clock: _NowClock,
) -> Callable[[], CycleOutcome]:
    from app.workers.watcher_paper import publish_idle_watcher_status

    def cycle() -> CycleOutcome:
        publish_idle_watcher_status(
            settings,
            session_factory,
            worker_id=worker_id,
            reason=reason,
            now=clock.now(),
        )
        state = "disarmed" if reason == "activation_disarmed" else "refused"
        return CycleOutcome(status=state, error=reason)

    return cycle


def _guard(
    binding: tuple[AuthorityProbe, AuthorityRestore] | None,
) -> _AuthorityGuard | None:
    if binding is None:
        return None
    probe, restore = binding
    return _AuthorityGuard(probe=probe, restore=restore, baseline=probe())


def _drain_wakeup(read_fd: int) -> None:
    while True:
        try:
            chunk = os.read(read_fd, 1024)
        except BlockingIOError:
            return
        if not chunk:
            return


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.astimezone(UTC).isoformat()


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:120]


if __name__ == "__main__":
    main()
