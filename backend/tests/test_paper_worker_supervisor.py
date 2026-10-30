"""Supervised paper worker. Paper only. No deploy and no activation."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings, get_settings
from app.core.disarmed_worker_boot import (
    WorkerBootRole,
    bind_worker_boot_role,
    reset_disarmed_worker_boot,
)
from app.persistence.composition import build_postgres_watcher_store
from app.telegram_activation.runtime import TelegramPaperRuntime, TelegramRuntimeCycle
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    authority_binding,
    build_paper_worker_supervisor,
    isolate_runtime_settings,
    outcome_from_telegram_cycle,
    outcome_from_watcher_report,
)
from app.workers.watcher_activation import ActivationDecision
from app.workers.watcher_paper import WatcherPaperCycleReport, WatcherPaperScanReport
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_disarmed_render_worker_boot import _ARMED_WATCHER, _CONTRACT, _PRESENT_SECRETS

ROOT = Path(__file__).resolve().parents[2]


class _Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


def _scan(status: str = "succeeded", reason: str = "completed") -> WatcherPaperScanReport:
    return WatcherPaperScanReport(
        organization_id=uuid4(),
        scan_scope="BTCUSDT",
        symbol="BTCUSDT",
        status=status,
        reason_code=reason,
        replayed=False,
        published=False,
        candidate_ids=(),
        kill_switch_active=False,
    )


def _idle() -> CycleOutcome:
    return CycleOutcome(status="disarmed")


def _supervisor(
    watcher: object,
    telegram: object,
    *,
    clock: _Clock | None = None,
    poll_seconds: float = 0.0,
) -> PaperWorkerSupervisor:
    return PaperWorkerSupervisor(
        watcher_cycle=watcher,  # type: ignore[arg-type]
        telegram_cycle=telegram,  # type: ignore[arg-type]
        poll_seconds=poll_seconds,
        clock=clock,
    )


def test_disarmed_boot_heartbeats_both_components_without_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_disarmed_worker_boot()
    get_settings.cache_clear()
    bind_worker_boot_role(WorkerBootRole.PAPER_WORKER)

    def _forbidden() -> object:
        raise AssertionError("operational dependency opened during disarmed boot")

    monkeypatch.setattr("app.db.session.get_session_factory", _forbidden)
    try:
        settings = Settings(**_CONTRACT)
        supervisor = build_paper_worker_supervisor(settings)
        health = supervisor.run_round()
    finally:
        reset_disarmed_worker_boot()
        get_settings.cache_clear()
    assert health.watcher.status == "disarmed"
    assert health.telegram.status == "disarmed"
    assert health.watcher.last_heartbeat_at is not None
    assert health.telegram.last_heartbeat_at is not None
    assert health.watcher.last_scan_at is None
    assert health.telegram.last_delivery_at is None
    assert health.watcher.last_error == ""
    assert health.telegram.last_error == ""
    assert health.authority_intact is True
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False


def test_settings_copies_do_not_share_authority_flags() -> None:
    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    watcher_settings, telegram_settings = isolate_runtime_settings(settings)
    watcher_settings.telegram_alerts_enabled = True
    watcher_settings.telegram_paper_activation_armed = True
    assert telegram_settings.telegram_alerts_enabled is False
    assert telegram_settings.telegram_paper_activation_armed is False
    assert settings.telegram_alerts_enabled is False


def test_watcher_only_activation_scans_without_telegram_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    watcher_calls = {"n": 0}
    telegram_calls = {"n": 0}

    class _Watcher:
        def run_cycle(self) -> WatcherPaperCycleReport:
            watcher_calls["n"] += 1
            return WatcherPaperCycleReport(
                reason_code="completed",
                enabled=True,
                scans=(_scan(),),
                candidates_created=0,
                kill_switch_active=False,
            )

        def stop(self) -> None:
            return None

    class _Telegram:
        def run_cycle(self) -> TelegramRuntimeCycle:
            telegram_calls["n"] += 1
            return TelegramRuntimeCycle(
                posture="disarmed",
                delivered=0,
                poll_applied=0,
                poll_rejected=0,
                kill_switch_active=False,
                lease_held=True,
                last_error_code="",
            )

        def stop(self) -> None:
            return None

    class _Session:
        def __init__(self) -> None:
            self.runtime = _Watcher()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    session = _Session()
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(
        "app.workers.watcher_activation.run_staging_activation",
        lambda *_args, **_kwargs: ActivationDecision(
            allowed=True,
            reason_codes=("cleared",),
            primary_reason="cleared",
            phase="preflight",
        ),
    )
    monkeypatch.setattr(
        "app.workers.watcher_activation.open_staging_watcher_session",
        lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime",
        lambda *_args, **_kwargs: _Telegram(),
    )
    supervisor = build_paper_worker_supervisor(settings)
    health = supervisor.run_round()
    supervisor.close()
    assert watcher_calls["n"] == 1
    assert telegram_calls["n"] == 1
    assert health.watcher.status == "running"
    assert health.watcher.last_scan_at is not None
    assert health.telegram.status == "disarmed"
    assert health.telegram.last_delivery_at is None
    assert health.telegram.last_error == ""
    assert session.closed is True
    assert settings.telegram_alerts_enabled is False
    assert settings.real_trading_enabled is False


def test_telegram_projection_refusal_does_not_stop_telegram_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.telegram_activation.errors import TelegramActivationError

    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    beats = {"n": 0}

    class _Telegram:
        def run_cycle(self) -> TelegramRuntimeCycle:
            beats["n"] += 1
            return TelegramRuntimeCycle(
                posture="disarmed",
                delivered=0,
                poll_applied=0,
                poll_rejected=0,
                kill_switch_active=False,
                lease_held=True,
                last_error_code="",
            )

        def stop(self) -> None:
            return None

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(
        "app.workers.watcher_activation.run_staging_activation",
        lambda *_args, **_kwargs: ActivationDecision(
            allowed=True,
            reason_codes=("cleared",),
            primary_reason="cleared",
            phase="preflight",
        ),
    )

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise TelegramActivationError("binding missing", reason="recipient_binding_missing")

    monkeypatch.setattr("app.workers.watcher_activation.open_staging_watcher_session", _refuse)
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime",
        lambda *_args, **_kwargs: _Telegram(),
    )
    health = build_paper_worker_supervisor(settings).run_round()
    assert health.watcher.status == "refused"
    assert health.watcher.last_error == "recipient_binding_missing"
    assert health.watcher.last_scan_at is None
    assert beats["n"] == 1
    assert health.telegram.status == "disarmed"
    assert health.telegram.last_delivery_at is None


def test_startup_migration_refusal_keeps_heartbeating_without_retrying_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy process after DB recovery does not establish scheduled evaluation."""
    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    ready = False
    preflight_calls: list[bool] = []
    published: list[dict[str, object]] = []
    telegram_calls: list[bool] = []

    def preflight(*_args: object, **_kwargs: object) -> ActivationDecision:
        preflight_calls.append(ready)
        reason = "cleared" if ready else "migration_unhealthy"
        return ActivationDecision(
            allowed=ready,
            reason_codes=(reason,),
            primary_reason=reason,
            phase="preflight",
        )

    def unexpected_start(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("startup refusal must not open a scanning runtime")

    class Telegram:
        def run_cycle(self) -> TelegramRuntimeCycle:
            telegram_calls.append(True)
            return TelegramRuntimeCycle(
                posture="disarmed",
                delivered=0,
                poll_applied=0,
                poll_rejected=0,
                kill_switch_active=False,
                lease_held=True,
                last_error_code="",
            )

        def stop(self) -> None:
            return None

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr("app.workers.watcher_activation.run_staging_activation", preflight)
    monkeypatch.setattr(
        "app.workers.watcher_activation.open_staging_watcher_session", unexpected_start
    )
    monkeypatch.setattr(
        "app.workers.watcher_paper.publish_idle_watcher_status",
        lambda *_args, **kwargs: published.append(kwargs),
    )
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime",
        lambda *_args, **_kwargs: Telegram(),
    )
    supervisor = build_paper_worker_supervisor(settings)
    try:
        first = supervisor.run_round()
        ready = True  # The external migration condition recovers after construction.
        second = supervisor.run_round()
    finally:
        supervisor.close()

    assert preflight_calls == [False]
    assert len(published) == len(telegram_calls) == 2
    assert all(status["reason"] == "migration_unhealthy" for status in published)
    assert published[1]["worker_id"] == published[0]["worker_id"]
    for health in (first, second):
        assert health.watcher.status == "refused"
        assert health.watcher.last_error == "migration_unhealthy"
        assert health.watcher.last_heartbeat_at is not None
        assert health.watcher.last_scan_at is None
        assert health.telegram.status == "disarmed"
        assert health.authority_intact is True
    assert second.watcher.cycles_completed == 2
    assert settings.enable_real_trading is settings.real_trading_enabled is False
    assert settings.telegram_alerts_enabled is False


def test_telegram_failure_does_not_corrupt_watcher_state() -> None:
    clock = _Clock()

    def watcher() -> CycleOutcome:
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        raise RuntimeError("telegram_down")

    supervisor = _supervisor(watcher, telegram, clock=clock)
    first = supervisor.run_round()
    scan = first.watcher.last_scan_at
    assert first.watcher.status == "running"
    assert first.watcher.last_error == ""
    assert first.telegram.status == "failed"
    assert "telegram_down" in first.telegram.last_error
    assert first.telegram.restart_count == 1
    clock.advance(5)
    second = supervisor.run_round()
    assert second.watcher.cycles_completed == 2
    assert second.watcher.last_error == ""
    assert second.watcher.last_scan_at == clock()
    assert scan is not None
    assert second.watcher.last_scan_at != scan
    assert second.telegram.restart_count == 2
    assert second.telegram.last_delivery_at is None
    assert second.authority_intact is True


def test_watcher_failure_does_not_grant_telegram_authority() -> None:
    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS})
    watcher_settings, telegram_settings = isolate_runtime_settings(settings)
    seen: list[bool] = []

    def watcher() -> CycleOutcome:
        watcher_settings.telegram_alerts_enabled = True
        watcher_settings.telegram_paper_activation_armed = True
        watcher_settings.enable_real_trading = True
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        seen.append(telegram_settings.telegram_alerts_enabled)
        seen.append(telegram_settings.telegram_paper_activation_armed)
        seen.append(telegram_settings.enable_real_trading)
        return CycleOutcome(status="disarmed")

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=watcher,
        telegram_cycle=telegram,
        watcher_authority=authority_binding(watcher_settings),
        telegram_authority=authority_binding(telegram_settings),
    )
    health = supervisor.run_round()
    assert health.watcher.status == "failed"
    assert "authority_drift" in health.watcher.last_error
    assert health.watcher.last_scan_at is None
    assert health.watcher.cycles_completed == 0
    assert health.telegram.status == "disarmed"
    assert health.telegram.last_error == ""
    assert seen == [False, False, False]
    assert watcher_settings.telegram_alerts_enabled is False
    assert watcher_settings.telegram_paper_activation_armed is False
    assert watcher_settings.enable_real_trading is False
    assert health.authority_intact is False
    assert settings.real_trading_enabled is False


def test_both_components_active_record_scan_and_delivery() -> None:
    clock = _Clock()

    def watcher() -> CycleOutcome:
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        return CycleOutcome(status="running", delivered=1)

    health = _supervisor(watcher, telegram, clock=clock).run_round()
    assert health.watcher.status == "running"
    assert health.telegram.status == "running"
    assert health.watcher.last_scan_at == clock()
    assert health.telegram.last_delivery_at == clock()
    assert health.watcher.last_heartbeat_at == clock()
    assert health.telegram.last_heartbeat_at == clock()
    assert health.watcher.last_error == ""
    assert health.telegram.last_error == ""


def test_failed_component_restarts_without_restarting_the_other() -> None:
    state = {"fail": True}
    watcher_runs = {"n": 0}

    def watcher() -> CycleOutcome:
        watcher_runs["n"] += 1
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("telegram_down")
        return CycleOutcome(status="running", delivered=1)

    supervisor = _supervisor(watcher, telegram)
    failed = supervisor.run_round()
    assert failed.telegram.status == "failed"
    assert failed.telegram.restart_count == 1
    assert failed.watcher.cycles_completed == 1
    recovered = supervisor.run_round()
    assert recovered.telegram.status == "running"
    assert recovered.telegram.restart_count == 1
    assert recovered.telegram.cycles_completed == 1
    assert recovered.telegram.last_delivery_at is not None
    assert recovered.watcher.cycles_completed == 2
    assert watcher_runs["n"] == 2


def test_supervisor_starts_one_loop_per_component() -> None:
    counts = {"watcher": 0, "telegram": 0}
    started = threading.Event()
    release = threading.Event()

    def watcher() -> CycleOutcome:
        counts["watcher"] += 1
        started.set()
        release.wait(2)
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        counts["telegram"] += 1
        return CycleOutcome(status="running")

    supervisor = _supervisor(watcher, telegram, poll_seconds=30)
    supervisor.start()
    watcher_thread = supervisor._watcher.thread
    supervisor.start()
    assert supervisor._watcher.thread is watcher_thread
    assert started.wait(2)
    assert counts["watcher"] == 1
    release.set()
    supervisor.request_stop()
    supervisor.join()
    assert counts["watcher"] == 1
    assert counts["telegram"] == 1
    health = supervisor.snapshot()
    assert health.stopping is True


def test_sigterm_stops_both_components() -> None:
    script = """
import sys
from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor

def watcher():
    return CycleOutcome(status="running", record_scan=True)

def telegram():
    return CycleOutcome(status="running", delivered=1)

supervisor = PaperWorkerSupervisor(
    watcher_cycle=watcher,
    telegram_cycle=telegram,
    poll_seconds=0.05,
    join_timeout_seconds=2,
)
def ready() -> None:
    print("SERVING", flush=True)

supervisor.serve_until_signal(ready=ready)
health = supervisor.snapshot()
print(
    "STOPPED",
    health.watcher.status,
    health.telegram.status,
    str(health.stopping).lower(),
    flush=True,
)
sys.stdout.flush()
"""
    completed = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd="/tmp",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "backend" / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert completed.stdout is not None
    ready, _, _ = select.select([completed.stdout], [], [], 20)
    assert ready, "paper worker did not report that it was serving"
    serving = completed.stdout.readline()
    assert serving.startswith("SERVING"), serving
    completed.send_signal(signal.SIGTERM)
    stdout, stderr = completed.communicate(timeout=15)
    assert completed.returncode == 0, stderr
    assert "STOPPED stopped stopped true" in stdout


def test_provider_outage_stays_on_the_watcher() -> None:
    sends = {"n": 0}

    def watcher() -> CycleOutcome:
        return CycleOutcome(
            status="degraded",
            error="provider_unavailable",
            record_scan=True,
        )

    def telegram() -> CycleOutcome:
        sends["n"] += 1
        return CycleOutcome(status="running", delivered=0)

    health = _supervisor(watcher, telegram).run_round()
    assert health.watcher.status == "degraded"
    assert health.watcher.last_error == "provider_unavailable"
    assert health.watcher.restart_count == 0
    assert health.telegram.status == "running"
    assert health.telegram.last_error == ""
    assert health.telegram.last_delivery_at is None
    assert sends["n"] == 1
    assert health.authority_intact is True
    report = WatcherPaperCycleReport(
        reason_code="completed",
        enabled=True,
        scans=(_scan(status="failed", reason="provider_unavailable"),),
        candidates_created=0,
        kill_switch_active=False,
    )
    mapped = outcome_from_watcher_report(report)
    assert mapped.status == "degraded"
    assert mapped.error == "provider_unavailable"
    assert mapped.delivered == 0


def test_kill_switch_pauses_actions_and_keeps_heartbeats() -> None:
    def watcher() -> CycleOutcome:
        return CycleOutcome(status="monitoring", kill_switch_active=True)

    def telegram() -> CycleOutcome:
        return CycleOutcome(status="monitoring", kill_switch_active=True, delivered=0)

    health = _supervisor(watcher, telegram).run_round()
    assert health.watcher.kill_switch_active is True
    assert health.telegram.kill_switch_active is True
    assert health.watcher.status == "monitoring"
    assert health.telegram.status == "monitoring"
    assert health.telegram.last_delivery_at is None
    assert health.watcher.last_heartbeat_at is not None
    assert health.telegram.last_heartbeat_at is not None
    assert health.watcher.restart_count == 0
    cycle = TelegramRuntimeCycle(
        posture="projection",
        delivered=0,
        poll_applied=0,
        poll_rejected=0,
        kill_switch_active=True,
        lease_held=True,
        last_error_code="kill_switch_active",
    )
    mapped = outcome_from_telegram_cycle(cycle)
    assert mapped.status == "monitoring"
    assert mapped.delivered == 0
    assert mapped.kill_switch_active is True


def test_run_round_invokes_each_cycle_once() -> None:
    counts = {"watcher": 0, "telegram": 0}

    def watcher() -> CycleOutcome:
        counts["watcher"] += 1
        return CycleOutcome(status="running", record_scan=True)

    def telegram() -> CycleOutcome:
        counts["telegram"] += 1
        return CycleOutcome(status="disarmed")

    supervisor = _supervisor(watcher, telegram)
    supervisor.run_round()
    supervisor.run_round()
    supervisor.run_round()
    assert counts == {"watcher": 3, "telegram": 3}


@requires_postgres
def test_postgres_watcher_lease_fences_a_second_owner() -> None:
    factory = phase7_plan_session_factory()
    store = build_postgres_watcher_store(factory)
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    organization_id = uuid4()
    scans = {"n": 0}

    def _cycle(owner: str):
        def cycle() -> CycleOutcome:
            held, _lease, _reason = store.claim_lease(
                organization_id=organization_id,
                scan_scope="BTCUSDT",
                owner_id=owner,
                ttl_seconds=30,
                now=now,
            )
            if not held:
                return CycleOutcome(status="refused", error="lease_held_elsewhere")
            scans["n"] += 1
            return CycleOutcome(status="running", record_scan=True)

        return cycle

    first = PaperWorkerSupervisor(watcher_cycle=_cycle("owner-a"), telegram_cycle=_idle)
    second = PaperWorkerSupervisor(watcher_cycle=_cycle("owner-b"), telegram_cycle=_idle)
    held = first.run_round()
    blocked = second.run_round()
    assert held.watcher.status == "running"
    assert held.watcher.last_scan_at is not None
    assert blocked.watcher.status == "refused"
    assert blocked.watcher.last_error == "lease_held_elsewhere"
    assert blocked.watcher.last_scan_at is None
    assert blocked.telegram.status == "disarmed"
    assert scans["n"] == 1


@requires_postgres
def test_postgres_telegram_lease_delivery_and_restart_stay_idempotent() -> None:
    from app.db.models import KillSwitchState, Organization
    from app.persistence.runtime_status import TELEGRAM_COMPONENT, load_runtime_rows
    from app.persistence.telegram_activation import PostgresActivationCursorStore
    from app.telegram_security.clock import FrozenClock
    from app.telegram_security.contracts import OutboxState
    from app.telegram_security.transport import FakeTelegramTransport
    from tests.support.telegram_security import BOT, CHAT, ORG, USER
    from tests.test_frontier_remediation import _local_projection_settings, _projection_runtime

    factory = phase7_plan_session_factory()
    clock = FrozenClock()
    holder = TelegramPaperRuntime(
        settings=Settings(),
        session_factory=factory,
        clock=clock,
        worker_id="lease-a",
        posture="disarmed",
    )
    rival = TelegramPaperRuntime(
        settings=Settings(),
        session_factory=factory,
        clock=clock,
        worker_id="lease-b",
        posture="disarmed",
    )
    first = PaperWorkerSupervisor(
        watcher_cycle=_idle,
        telegram_cycle=lambda: outcome_from_telegram_cycle(holder.run_cycle()),
    )
    second = PaperWorkerSupervisor(
        watcher_cycle=_idle,
        telegram_cycle=lambda: outcome_from_telegram_cycle(rival.run_cycle()),
    )
    assert first.run_round().telegram.status == "disarmed"
    blocked = second.run_round()
    assert blocked.telegram.status == "refused"
    assert blocked.telegram.last_error == "lease_held_elsewhere"
    assert blocked.telegram.last_delivery_at is None
    assert blocked.watcher.status == "disarmed"
    with factory() as session:
        row = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert row.lease_owner == "lease-a"

    transport = FakeTelegramTransport()
    transport.fail_next(1)
    world = _projection_runtime(factory, transport=transport, clock=clock, worker_id="tg-paper")
    world.protocol.enqueue_outbound(
        organization_id=ORG,
        user_id=USER,
        binding_id=world.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="paper notice",
        idempotency_key="paper-worker-duplicate",
    )
    supervised = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running", record_scan=True),
        telegram_cycle=lambda: outcome_from_telegram_cycle(world.runtime.run_cycle()),
        clock=lambda: world.clock.now(),
    )
    clock.advance(timedelta(seconds=31))
    failed = supervised.run_round()
    assert failed.telegram.last_error == "TRANSPORT_FAILURE"
    assert failed.telegram.last_delivery_at is None
    assert failed.watcher.status == "running"
    assert transport.send_count == 0
    clock.advance(timedelta(seconds=5))
    delivered = supervised.run_round()
    assert delivered.telegram.status == "running"
    assert delivered.telegram.last_delivery_at == clock.now()
    assert transport.send_count == 1
    again = supervised.run_round()
    assert again.telegram.last_delivery_at == clock.now()
    assert transport.send_count == 1

    projection_settings = _local_projection_settings()
    restarted_runtime = TelegramPaperRuntime(
        settings=projection_settings,
        session_factory=factory,
        clock=clock,
        worker_id="tg-paper-restart",
        posture="projection",
        controller=world.controller,
    )
    restarted = PaperWorkerSupervisor(
        watcher_cycle=_idle,
        telegram_cycle=lambda: outcome_from_telegram_cycle(restarted_runtime.run_cycle()),
    )
    clock.advance(timedelta(seconds=31))
    restarted_health = restarted.run_round()
    assert transport.send_count == 1
    assert restarted_health.telegram.last_delivery_at is None

    world.protocol.enqueue_outbound(
        organization_id=ORG,
        user_id=USER,
        binding_id=world.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="queued while switch activates",
        idempotency_key="paper-worker-killed",
    )
    with factory() as session, session.begin():
        session.add(Organization(id=ORG, name="Paper worker kill switch"))
        session.flush()
        session.add(KillSwitchState(organization_id=ORG, active=True, reason="paper-worker"))
    killed_runtime = TelegramPaperRuntime(
        settings=projection_settings,
        session_factory=factory,
        clock=clock,
        worker_id="tg-killed",
        posture="projection",
        controller=world.controller,
    )
    killed = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="monitoring", record_scan=True),
        telegram_cycle=lambda: outcome_from_telegram_cycle(killed_runtime.run_cycle()),
    )
    before = PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT)
    clock.advance(timedelta(seconds=31))
    paused = killed.run_round()
    after = PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT)
    before_id = None if before is None else before.last_update_id
    after_id = None if after is None else after.last_update_id
    assert paused.telegram.kill_switch_active is True
    assert paused.telegram.status == "monitoring"
    assert paused.watcher.status == "monitoring"
    assert paused.watcher.last_scan_at is not None
    assert transport.send_count == 1
    assert after_id == before_id
    pending = world.protocol.store.list_outbox(organization_id=ORG)
    assert any(row.state is not OutboxState.ACKNOWLEDGED for row in pending)


def test_staging_composition_wires_the_watcher_monitor_idle_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging drives run_cycle() from the supervisor; the inter-cycle sleep must
    still re-publish the market monitor snapshot (f626a5c2 regression)."""

    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    waits: list[float] = []

    class _Watcher:
        def run_cycle(self) -> WatcherPaperCycleReport:
            return WatcherPaperCycleReport(
                reason_code="completed",
                enabled=True,
                scans=(_scan(),),
                candidates_created=0,
                kill_switch_active=False,
            )

        def wait_publishing_monitor(self, wait: float, stop: threading.Event) -> bool:
            waits.append(wait)
            return stop.is_set()

        def stop(self) -> None:
            return None

    class _Telegram:
        def run_cycle(self) -> TelegramRuntimeCycle:
            return TelegramRuntimeCycle(
                posture="disarmed",
                delivered=0,
                poll_applied=0,
                poll_rejected=0,
                kill_switch_active=False,
                lease_held=True,
                last_error_code="",
            )

        def stop(self) -> None:
            return None

    class _Session:
        runtime = _Watcher()

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(
        "app.workers.watcher_activation.run_staging_activation",
        lambda *_a, **_k: ActivationDecision(
            allowed=True, reason_codes=("cleared",), primary_reason="cleared", phase="preflight"
        ),
    )
    monkeypatch.setattr(
        "app.workers.watcher_activation.open_staging_watcher_session",
        lambda *_a, **_k: _Session(),
    )
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime",
        lambda *_a, **_k: _Telegram(),
    )
    supervisor = build_paper_worker_supervisor(settings)
    try:
        assert supervisor._watcher.idle_wait is not None
        assert supervisor._telegram.idle_wait is None
        assert supervisor._idle(supervisor._watcher, 15.0) is False
        assert waits == [15.0]
    finally:
        supervisor.close()


def test_failing_idle_wait_falls_back_to_a_plain_wait() -> None:
    def broken(_wait: float, _stop: threading.Event) -> bool:
        raise RuntimeError("db down")

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=_idle,
        telegram_cycle=_idle,
        poll_seconds=0.0,
        watcher_idle_wait=broken,
    )
    assert supervisor._idle(supervisor._watcher, 0.01) is False
    supervisor._stop.set()
    assert supervisor._idle(supervisor._watcher, 0.01) is True


def test_watcher_loop_uses_the_idle_wait_between_cycles() -> None:
    calls = {"cycles": 0, "idle": 0}
    supervisor: PaperWorkerSupervisor

    def cycle() -> CycleOutcome:
        calls["cycles"] += 1
        return CycleOutcome(status="running")

    def idle(_wait: float, stop: threading.Event) -> bool:
        calls["idle"] += 1
        if calls["idle"] >= 2:
            stop.set()
        return stop.is_set()

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=cycle, telegram_cycle=_idle, poll_seconds=15.0, watcher_idle_wait=idle
    )
    supervisor._loop(supervisor._watcher)
    assert calls == {"cycles": 2, "idle": 2}


def test_watcher_loop_stops_when_the_idle_hook_reports_stopped() -> None:
    """X8: a True from the hook ends the loop even if the supervisor event is unset."""

    calls = {"cycles": 0}

    def cycle() -> CycleOutcome:
        calls["cycles"] += 1
        return CycleOutcome(status="running")

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=cycle,
        telegram_cycle=_idle,
        poll_seconds=15.0,
        watcher_idle_wait=lambda _wait, _stop: True,
    )
    worker = threading.Thread(target=supervisor._loop, args=(supervisor._watcher,))
    worker.start()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert calls["cycles"] == 1
    assert supervisor._stop.is_set() is False


def test_local_composition_wires_the_watcher_monitor_idle_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X2: the local paper branch connects the same hook as staging."""

    settings = get_settings().model_copy(deep=True)
    waits: list[float] = []

    class _Runtime:
        def run_cycle(self) -> WatcherPaperCycleReport:
            raise AssertionError("not called")

        def wait_publishing_monitor(self, wait: float, stop: threading.Event) -> bool:
            waits.append(wait)
            return stop.is_set()

        def stop(self) -> None:
            return None

    class _Telegram:
        def run_cycle(self) -> TelegramRuntimeCycle:
            raise AssertionError("not called")

        def stop(self) -> None:
            return None

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(
        "app.workers.paper_worker.paper_runtime_enabled", lambda _s: True, raising=False
    )
    monkeypatch.setattr("app.workers.watcher_paper.paper_runtime_enabled", lambda _s: True)
    monkeypatch.setattr(
        "app.workers.watcher_paper.build_watcher_paper_runtime", lambda *_a, **_k: _Runtime()
    )
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime", lambda *_a, **_k: _Telegram()
    )
    from app.core.config import Environment

    settings.environment = Environment.LOCAL
    supervisor = build_paper_worker_supervisor(settings)
    try:
        assert supervisor._watcher.idle_wait is not None
        assert supervisor._idle(supervisor._watcher, 15.0) is False
        assert waits == [15.0]
    finally:
        supervisor.close()
