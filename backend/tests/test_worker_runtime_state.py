"""Worker runtime flags and the owner paper ceiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.persistence.worker_runtime_state import (
    WorkerRuntimeWrite,
    org_paper_effective,
    project_worker_effective,
    publish_worker_runtime_state,
)
from app.services.autonomous_paper_switch import resolve_worker_autonomous_mode
from app.services.autonomous_trading import AutonomousMode, resolve_autonomous_mode
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import PolicyKind, profile_with
from app.workers.watcher_paper import WatcherPaperRuntime, WatcherPaperScanReport


def _view(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "execution_mode": "paper",
        "exchange_mode": "paper_internal",
        "enable_real_trading": False,
        "real_trading_enabled": False,
        "autonomous_internal_paper_enabled": False,
        "autonomous_blofin_demo_enabled": False,
        "environment": "local",
        "global_kill_switch_active": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _factory() -> sessionmaker[Session]:
    from app.db.worker_runtime_state import WorkerRuntimeStateRow

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkerRuntimeStateRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _write(**overrides: object) -> WorkerRuntimeWrite:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "worker_id": "paper-worker",
        "heartbeat_at": now,
        "autonomous_internal_paper_env_ceiling": True,
        "watcher_orchestration_enabled": True,
        "watcher_paper_staging_activation": True,
        "telegram_paper_activation_armed": True,
        "telegram_interaction_enabled": True,
        "telegram_alerts_enabled": True,
        "automatic_telegram_delivery_enabled": True,
        "preserve_cycle": False,
        "autonomy_mode": "internal_paper",
        "autonomous_internal_paper_db_enabled": True,
        "last_cycle_at": now,
        "watchlist": ("BTCUSDT", "bad symbol", "ETHUSDT"),
        "organization_id": uuid4(),
    }
    values.update(overrides)
    return WorkerRuntimeWrite(**values)  # type: ignore[arg-type]


def test_publish_projects_worker_flags_and_staleness() -> None:
    factory = _factory()
    publish_worker_runtime_state(factory, _write())
    now = datetime(2026, 10, 22, 12, 0, 30, tzinfo=UTC)
    with factory() as session:
        from app.db.worker_runtime_state import WORKER_EFFECTIVE_COMPONENT, WorkerRuntimeStateRow

        row = session.get(WorkerRuntimeStateRow, WORKER_EFFECTIVE_COMPONENT)
    fresh = project_worker_effective(row, now=now, stale_after_seconds=90)
    assert fresh.source == "worker"
    assert fresh.health_state == "RUNNING"
    assert fresh.stale is False
    assert fresh.watcher_orchestration_enabled is True
    assert fresh.telegram_paper_activation_armed is True
    assert fresh.watchlist == ["BTCUSDT", "ETHUSDT"]
    assert fresh.autonomous_internal_paper_effective is True
    assert fresh.real_trading_enabled is False

    stale = project_worker_effective(
        row,
        now=now + timedelta(seconds=120),
        stale_after_seconds=90,
    )
    assert stale.health_state == "STALE"
    assert stale.stale is True
    assert org_paper_effective(stale, True) is False
    assert org_paper_effective(fresh, True) is True
    assert org_paper_effective(fresh, False) is False


def test_heartbeat_preserves_the_last_cycle() -> None:
    factory = _factory()
    publish_worker_runtime_state(factory, _write())
    later = datetime(2026, 10, 22, 12, 1, tzinfo=UTC)
    publish_worker_runtime_state(
        factory,
        _write(
            heartbeat_at=later,
            preserve_cycle=True,
            autonomy_mode="off",
            autonomous_internal_paper_db_enabled=False,
            watchlist=(),
            watcher_orchestration_enabled=False,
        ),
    )
    with factory() as session:
        from app.db.worker_runtime_state import WORKER_EFFECTIVE_COMPONENT, WorkerRuntimeStateRow

        row = session.get(WorkerRuntimeStateRow, WORKER_EFFECTIVE_COMPONENT)
    projected = project_worker_effective(row, now=later, stale_after_seconds=90)
    assert projected.watcher_orchestration_enabled is False
    assert projected.autonomy_mode == "internal_paper"
    assert projected.watchlist == ["BTCUSDT", "ETHUSDT"]
    assert projected.autonomous_internal_paper_db_enabled is True


def test_env_false_wins_over_the_database_switch() -> None:
    class Boom:
        def get(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("env false must not read the switch")

    resolution = resolve_worker_autonomous_mode(
        _view(autonomous_internal_paper_enabled=False),
        Boom(),  # type: ignore[arg-type]
        uuid4(),
    )
    assert resolution.mode is AutonomousMode.OFF
    assert resolution.refusal is None

    blocked = resolve_autonomous_mode(
        _view(
            autonomous_internal_paper_enabled=True,
            enable_real_trading=True,
            real_trading_enabled=True,
        ),
        internal_paper_db_enabled=True,
    )
    assert blocked.mode is AutonomousMode.OFF
    assert blocked.refusal == "real_trading_blocked"

    real_worker = resolve_worker_autonomous_mode(
        _view(
            autonomous_internal_paper_enabled=True,
            enable_real_trading=True,
            real_trading_enabled=True,
        ),
        Boom(),  # type: ignore[arg-type]
        uuid4(),
    )
    assert real_worker.refusal == "real_trading_blocked"


def _armed_settings() -> Settings:
    return Settings(_env_file=None, autonomous_internal_paper_enabled=True)


def test_database_false_disarms_internal_paper_for_the_worker_cycle() -> None:
    class Missing:
        def get(self, *_args: object, **_kwargs: object) -> None:
            return None

    settings = _armed_settings()
    resolution = resolve_worker_autonomous_mode(settings, Missing(), uuid4())  # type: ignore[arg-type]
    assert resolution.mode is AutonomousMode.OFF
    assert resolution.refusal is None

    candidate_id = uuid4()
    report = WatcherPaperScanReport(
        organization_id=uuid4(),
        scan_scope="scope",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="fixture",
        replayed=False,
        published=True,
        candidate_ids=(candidate_id,),
        kill_switch_active=False,
        discussion=SimpleNamespace(  # type: ignore[arg-type]
            candidate=SimpleNamespace(candidate_id=candidate_id),
            assessment=None,
            window=None,
        ),
    )
    runtime = SimpleNamespace(
        _canonical_runtime=object(),
        _settings=settings,
        _eval_clock=SimpleNamespace(now=lambda: None),
        _clock=SimpleNamespace(now=lambda: None),
    )
    continued = WatcherPaperRuntime._continue_paper_loop(
        runtime,  # type: ignore[arg-type]
        Missing(),  # type: ignore[arg-type]
        SimpleNamespace(organization_id=report.organization_id, scan_scope="scope"),  # type: ignore[arg-type]
        report,
        SimpleNamespace(last_assembly=lambda: (object(), object())),  # type: ignore[arg-type]
    )
    assert continued.paper_loop_stage == "blocked"
    assert continued.paper_loop_reason == "autonomous_mode_disarmed"


def test_unreadable_switch_is_unverified() -> None:
    class Broken:
        def get(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("db down")

    resolution = resolve_worker_autonomous_mode(
        _armed_settings(),
        Broken(),  # type: ignore[arg-type]
        uuid4(),
    )
    assert resolution.refusal == "autonomous_paper_toggle_unverified"


def test_risk_profile_still_rejects_an_autonomous_flag() -> None:
    with pytest.raises(RiskPolicyRejected) as exc:
        profile_with(PolicyKind.CONSERVATIVE, autonomous_internal_paper_enabled=True)
    assert exc.value.args[0] == "autonomous_flags_must_stay_off"


def test_health_reports_worker_flags_and_keeps_real_trading_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat = datetime.now(UTC) - timedelta(seconds=10)
    row = SimpleNamespace(
        heartbeat_at=heartbeat,
        last_cycle_at=heartbeat,
        autonomy_mode="internal_paper",
        autonomous_internal_paper_env_ceiling=True,
        autonomous_internal_paper_db_enabled=True,
        watcher_orchestration_enabled=True,
        watcher_paper_staging_activation=True,
        telegram_paper_activation_armed=True,
        telegram_interaction_enabled=True,
        telegram_alerts_enabled=True,
        automatic_telegram_delivery_enabled=True,
        watchlist=["BTCUSDT"],
        organization_id=None,
    )
    monkeypatch.setattr("app.api.routes.health.load_worker_runtime_row", lambda: row)
    body = client.get("/health").json()
    assert body["real_trading_enabled"] is False
    assert body["execution_mode"] == "paper"
    assert body["worker_flags_source"] == "worker"
    assert body["worker_flags_stale"] is False
    assert body["watcher_orchestration_enabled"] is True
    assert body["telegram_paper_activation_armed"] is True
    assert body["worker_effective"]["real_trading_enabled"] is False
    assert body["worker_effective"]["watchlist"] == ["BTCUSDT"]

    stale_row = SimpleNamespace(**{**row.__dict__, "heartbeat_at": heartbeat - timedelta(hours=1)})
    monkeypatch.setattr("app.api.routes.health.load_worker_runtime_row", lambda: stale_row)
    stale = client.get("/health").json()
    assert stale["worker_flags_stale"] is True
    assert stale["watcher_orchestration_enabled"] is True
    assert stale["real_trading_enabled"] is False
