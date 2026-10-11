"""Staging paper Watcher activation stays disarmed and fails closed."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.main import _maybe_start_watcher_paper_runtime
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.watcher.fusion_evaluation import BoundEvaluationClock
from app.watcher.memory import FakeClock, InMemoryWatcherStore
from app.workers.watcher_activation import (
    ActivationDecision,
    ActivationObservations,
    WatcherPaperActivationConfig,
    _architecture_pins,
    evaluate_activation,
    evaluate_post_activation_smoke,
    execute_rollback_process,
    expected_migration_head,
    provider_state_for,
    read_migration_revision,
    rollback_plan_for,
    run_staging_activation,
    runtime_health_decision,
    start_if_allowed,
    worker_identity_is_unique,
)
from app.workers.watcher_paper import (
    WatcherPaperRuntime,
    new_worker_instance_id,
    paper_runtime_enabled,
    resolve_runtime_enabled,
)
from app.workers.watcher_paper_targets import PaperScanTarget, lineage_targets_are_valid

_STAGING = {
    "environment": "staging",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
    "perpetual_evidence_source": "binance_usdm",
    "watcher_orchestration_enabled": True,
    "watcher_paper_staging_activation": True,
}


def _config(**updates: object) -> WatcherPaperActivationConfig:
    base = WatcherPaperActivationConfig(
        environment="staging",
        armed=True,
        orchestration_enabled=True,
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
        telegram_alerts_enabled=False,
        telegram_interaction_enabled=False,
        automatic_telegram_delivery_enabled=False,
        market_watcher_enabled=False,
        market_watcher_bridge_enabled=False,
        market_watcher_bridge_auto_tick=False,
        evidence_source="binance_usdm",
        exchange_mode="paper_internal",
        configured_worker_id="watcher-paper-1",
    )
    return replace(base, **updates)  # type: ignore[arg-type]


def _observations(worker_id: str, **updates: object) -> ActivationObservations:
    head = expected_migration_head()
    assert head is not None
    base = ActivationObservations(
        migration_revision=head,
        expected_migration_revision=head,
        provider_state="available",
        lineage_valid=True,
        lease_backend="postgres",
        worker_instance_id=worker_id,
        fencing_enabled=True,
        restart_recovery_enabled=True,
        idempotency_enabled=True,
        approved_compiled_only=True,
        canonical_live_evidence=True,
        freshness_fail_closed=True,
        confirmed_setup_only=True,
        risk_block_final=True,
        paper_execution_only=True,
        kill_switch_preserved=True,
    )
    return replace(base, **updates)  # type: ignore[arg-type]


def _worker() -> str:
    return new_worker_instance_id("watcher-paper-1")


def _refuses(
    reason: str, config: WatcherPaperActivationConfig, observations: ActivationObservations
) -> None:
    started = False

    def start() -> None:
        nonlocal started
        started = True

    decision = start_if_allowed(config, observations, start)
    assert decision.allowed is False
    assert decision.primary_reason == reason
    assert reason in decision.reason_codes
    assert started is False
    plan = execute_rollback_process(decision=decision, stop=start)
    assert plan.triggered is True
    assert plan.modifies_environment is False
    assert plan.deploys is False
    assert plan.activates_telegram is False
    assert plan.enables_real_trading is False
    assert any("Do not deploy" in command for command in plan.commands)


def test_healthy_preflight_can_start() -> None:
    worker_id = _worker()
    started = False

    def start() -> None:
        nonlocal started
        started = True

    decision = start_if_allowed(_config(), _observations(worker_id), start)
    assert decision.allowed is True
    assert decision.primary_reason == "cleared"
    assert started is True
    assert rollback_plan_for(decision).triggered is False


def test_replay_evidence_cannot_start() -> None:
    _refuses(
        "replay_evidence_while_live_required",
        _config(evidence_source="replay"),
        _observations(_worker(), provider_state="replay"),
    )


def test_provider_unavailable_cannot_start() -> None:
    _refuses(
        "provider_unavailable",
        _config(),
        _observations(_worker(), provider_state="unavailable"),
    )


def test_invalid_lineage_cannot_start() -> None:
    _refuses(
        "strategy_lineage_invalid",
        _config(),
        _observations(_worker(), lineage_valid=False),
    )


def test_real_trading_flag_cannot_start() -> None:
    _refuses(
        "real_trading_enabled",
        _config(enable_real_trading=True),
        _observations(_worker()),
    )
    _refuses(
        "real_trading_enabled",
        _config(real_trading_enabled=True),
        _observations(_worker()),
    )


def test_unhealthy_migration_cannot_start() -> None:
    worker_id = _worker()
    _refuses(
        "migration_unhealthy",
        _config(),
        _observations(worker_id, migration_revision=None),
    )
    _refuses(
        "migration_unhealthy",
        _config(),
        _observations(worker_id, migration_revision="not-a-head"),
    )
    _refuses(
        "migration_unhealthy",
        _config(),
        _observations(worker_id, expected_migration_revision=None),
    )
    _refuses(
        "migration_unhealthy",
        _config(),
        _observations(worker_id, migration_revision="b6f2d9a10e73"),
    )
    _refuses(
        "migration_unhealthy",
        _config(),
        _observations(worker_id, migration_revision="a6manualdemo001"),
    )


def test_settings_cannot_construct_real_trading_or_armed_replay() -> None:
    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING"):
        Settings(**{**_STAGING, "enable_real_trading": True})
    with pytest.raises(ValidationError, match="replay evidence"):
        Settings(**{**_STAGING, "perpetual_evidence_source": "replay"})


def test_staging_entrypoint_refuses_the_probe_failures() -> None:
    settings = Settings(**_STAGING)
    worker_id = new_worker_instance_id(settings.watcher_paper_worker_id)
    cases = (
        ("provider_unavailable", {"provider_state": "unavailable"}),
        ("strategy_lineage_invalid", {"lineage_valid": False}),
        ("migration_unhealthy", {"migration_revision": "not-a-head"}),
    )
    for reason, updates in cases:
        started = False

        def start() -> None:
            nonlocal started
            started = True

        decision = run_staging_activation(
            settings,
            observations=_observations(worker_id, **updates),
            start=start,
            worker_instance_id=worker_id,
        )
        assert decision.primary_reason == reason
        assert started is False


def test_default_settings_stay_disarmed_and_api_does_not_autostart() -> None:
    settings = Settings()
    assert settings.watcher_paper_staging_activation is False
    assert settings.watcher_orchestration_enabled is False
    assert paper_runtime_enabled(settings) is False
    assert _maybe_start_watcher_paper_runtime(settings) is None

    armed = Settings(**_STAGING)
    assert paper_runtime_enabled(armed) is False
    assert resolve_runtime_enabled(armed, enabled=True, activation_cleared=False) is False
    assert resolve_runtime_enabled(armed, enabled=True, activation_cleared=True) is True
    assert _maybe_start_watcher_paper_runtime(armed) is None


def test_architecture_pins_match_the_validated_watcher() -> None:
    pins = _architecture_pins()
    assert pins.postgres_leases is True
    assert pins.fencing_enabled is True
    assert pins.restart_recovery_enabled is True
    assert pins.idempotency_enabled is True
    assert pins.approved_compiled_only is True
    assert pins.canonical_assembler is True
    assert pins.freshness_fail_closed is True
    assert pins.confirmed_setup_only is True
    assert pins.risk_block_final is True
    assert pins.kill_switch_preserved is True


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("fencing_enabled", "fencing_required"),
        ("restart_recovery_enabled", "restart_recovery_required"),
        ("idempotency_enabled", "idempotency_required"),
        ("approved_compiled_only", "approved_compiled_required"),
        ("canonical_live_evidence", "canonical_live_evidence_required"),
        ("freshness_fail_closed", "freshness_fail_closed"),
        ("confirmed_setup_only", "candidate_authority_not_confirmed_setup"),
        ("risk_block_final", "risk_block_not_final"),
        ("kill_switch_preserved", "kill_switch_not_preserved"),
        ("paper_execution_only", "paper_execution_required"),
    ],
)
def test_each_architecture_pin_can_block_start(field: str, reason: str) -> None:
    _refuses(reason, _config(), _observations(_worker(), **{field: False}))


def test_memory_leases_and_bare_worker_id_cannot_start() -> None:
    worker_id = _worker()
    _refuses(
        "postgres_lease_required",
        _config(),
        _observations(worker_id, lease_backend="memory"),
    )
    bare = _config().configured_worker_id
    assert worker_identity_is_unique(bare, bare) is False
    _refuses(
        "worker_identity_not_unique",
        _config(),
        _observations(bare),
    )


def test_telegram_and_production_cannot_start() -> None:
    worker_id = _worker()
    _refuses(
        "telegram_forbidden",
        _config(telegram_interaction_enabled=True),
        _observations(worker_id),
    )
    _refuses(
        "production_forbidden",
        _config(environment="production"),
        _observations(worker_id),
    )
    decision = evaluate_activation(_config(armed=False), _observations(worker_id))
    assert decision.allowed is False
    assert decision.primary_reason == "activation_disarmed"


def test_stale_evidence_and_stale_heartbeat_fail_closed() -> None:
    worker_id = _worker()
    stale = evaluate_activation(
        _config(),
        _observations(worker_id, provider_state="stale"),
    )
    assert stale.primary_reason == "freshness_fail_closed"
    heartbeat = runtime_health_decision(
        _config(),
        _observations(
            worker_id, runtime_heartbeat_age_seconds=120, runtime_heartbeat_limit_seconds=90
        ),
    )
    assert heartbeat.allowed is False
    assert heartbeat.primary_reason == "runtime_heartbeat_stale"
    fresh = runtime_health_decision(
        _config(),
        _observations(
            worker_id, runtime_heartbeat_age_seconds=5, runtime_heartbeat_limit_seconds=90
        ),
    )
    assert fresh.allowed is True


def test_provider_state_separates_outage_replay_and_fresh() -> None:
    assert provider_state_for(
        availability="unavailable", reason="provider_unavailable", is_live=True
    ) == ("unavailable")
    assert (
        provider_state_for(availability="replay", reason="replay_fixture", is_live=False)
        == "replay"
    )
    assert provider_state_for(availability="fresh", reason="ok", is_live=False) == "replay"
    assert provider_state_for(availability="stale", reason="stale_stream", is_live=True) == "stale"
    assert provider_state_for(availability="fresh", reason="ok", is_live=True) == "available"


def test_lineage_requires_a_hashed_compiled_target() -> None:
    assert lineage_targets_are_valid(()) is False
    target = PaperScanTarget(
        organization_id=uuid4(),
        user_id=uuid4(),
        strategy_id=uuid4(),
        strategy_version_id=uuid4(),
        compiled_setup_definition_id=uuid4(),
        compiled_content_hash="ab" * 32,
        fusion_policy_version="fusion/v1",
        symbol="BTCUSDT",
    )
    assert lineage_targets_are_valid((target,)) is True
    broken = replace(target, compiled_content_hash="not-a-hash")
    assert lineage_targets_are_valid((broken,)) is False


def test_migration_reader_fails_closed_without_one_revision() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        assert read_migration_revision(session) is None
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        assert read_migration_revision(session) is None
        session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('d9e0f1a2b3c4')"))
        session.commit()
        assert read_migration_revision(session) == "d9e0f1a2b3c4"
        session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('other')"))
        session.commit()
        assert read_migration_revision(session) is None
    assert expected_migration_head() == "a13voicemonitor001"


@pytest.mark.parametrize(
    ("heads", "expected"),
    [([], None), (["a1brain001", "b6f2d9a10e73"], None), (["future-head"], "future-head")],
)
def test_expected_migration_head_requires_one_alembic_head(
    monkeypatch: pytest.MonkeyPatch, heads: list[str], expected: str | None
) -> None:
    from alembic.script import ScriptDirectory

    monkeypatch.setattr(ScriptDirectory, "get_heads", lambda _self: heads)
    assert expected_migration_head() == expected


def test_runtime_gate_stops_before_scan() -> None:
    def evidence(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("scan evidence was requested")

    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=BoundEvaluationClock(),
        ),
        clock=FakeClock(),
        enabled=True,
        evidence_factory=evidence,  # type: ignore[arg-type]
        activation_gate=lambda: ActivationDecision(
            allowed=False,
            reason_codes=("provider_unavailable",),
            primary_reason="provider_unavailable",
            phase="runtime",
        ),
    )
    report = runtime.run_cycle()
    assert report.reason_code == "provider_unavailable"
    assert report.scans == ()
    assert report.candidates_created == 0
    assert runtime.snapshot().stopping is False
    again = runtime.run_cycle()
    assert again.reason_code == "provider_unavailable"
    assert again.scans == ()


def test_post_activation_smoke_rejects_the_five_failures() -> None:
    worker_id = _worker()
    healthy = {
        "paper_only": True,
        "real_trading_enabled": False,
        "telegram_enabled": False,
        "evidence_source": "live",
        "migration_healthy": True,
        "provider_available": True,
        "lineage_valid": True,
        "lease_backend": "postgres",
        "fencing_enabled": True,
        "restart_recovery_enabled": True,
        "idempotency_enabled": True,
        "confirmed_setup_only": True,
        "risk_block_final": True,
        "kill_switch_preserved": True,
        "freshness_fail_closed": True,
        "running": True,
        "activation_armed": True,
        "configured_worker_id": "watcher-paper-1",
        "worker_id": worker_id,
    }
    assert evaluate_post_activation_smoke(healthy).passed is True
    assert (
        "evidence_source"
        in evaluate_post_activation_smoke({**healthy, "evidence_source": "replay"}).failures
    )
    assert (
        "provider_unavailable"
        in evaluate_post_activation_smoke({**healthy, "provider_available": False}).failures
    )
    assert (
        "strategy_lineage_invalid"
        in evaluate_post_activation_smoke({**healthy, "lineage_valid": False}).failures
    )
    assert (
        "real_trading_enabled"
        in evaluate_post_activation_smoke({**healthy, "real_trading_enabled": True}).failures
    )
    assert (
        "migration_unhealthy"
        in evaluate_post_activation_smoke({**healthy, "migration_healthy": False}).failures
    )


def test_read_migration_revision_types_session() -> None:
    """Keep the reader callable against a Session, not only a connection."""

    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        assert isinstance(session, Session)
        assert read_migration_revision(session) is None


def test_expected_migration_head_is_independent_of_process_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert expected_migration_head() == "a13voicemonitor001"
