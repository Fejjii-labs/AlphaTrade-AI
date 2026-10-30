"""Paper-only continuous Watcher runtime: leases, fail-closed evidence, Candidates."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import KillSwitchState, Membership, Organization, User, UserStrategy
from app.evidence_pipeline.watcher_port import (
    AssemblingWatcherScanEvidence,
    resolve_watcher_scan_policy,
)
from app.main import create_app
from app.market_contracts.errors import RegionalProviderFailureError, StaleEvidenceError
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.schemas.common import MembershipRole, StrategyId, StrategyLifecycleState
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate
from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.compiled_setup_service import CompiledSetupService
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService
from app.signal_fusion.enums import SetupAssessmentState
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.watcher.contracts import EvaluationCommand, WatcherRuntimeConfig
from app.watcher.errors import WatcherEvidenceUnavailableError
from app.watcher.fusion_evaluation import (
    BoundEvaluationClock,
    ExecutablePolicyAuthority,
    WatcherCanonicalScanEvidence,
    WatcherScanEvidencePort,
)
from app.watcher.memory import FakeClock, InMemoryWatcherStore, SideEffectProbe
from app.watcher.ports import WatcherStore
from app.workers.watcher_paper import (
    WatcherPaperCycleReport,
    WatcherPaperRuntime,
    default_paper_evidence_factory,
    last_closed_interval_end,
    paper_runtime_enabled,
)
from app.workers.watcher_paper_targets import (
    FIRST_SLICE_SYMBOL,
    PaperScanTarget,
    list_paper_scan_targets,
    normalize_paper_symbols,
)
from tests.support.phase6_evaluator import EvaluatorWorld, make_world, subsequent_bars

ORG = UUID("00000000-0000-0000-0000-00000000a069")
ORG_B = UUID("00000000-0000-0000-0000-00000000b069")
USER = UUID("00000000-0000-0000-0000-00000000c069")
USER_B = UUID("00000000-0000-0000-0000-00000000d069")


def _settings(**updates: object) -> Settings:
    payload: dict[str, object] = {
        "environment": "local",
        "log_json": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "database_url": "sqlite+pysqlite:///:memory:",
        "jwt_secret": "watcher-paper-runtime-secret-32b-min",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "provider_mode": "mock",
        "market_data_provider": "mock",
        "watcher_orchestration_enabled": False,
        "perpetual_evidence_source": "replay",
    }
    payload.update(updates)
    return Settings(**payload)  # type: ignore[arg-type]


def _sqlite_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    install_persistence_firewall()
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    factory = _sqlite_factory()
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG, name="AT069 Org A"),
                Organization(id=ORG_B, name="AT069 Org B"),
                User(id=USER, email="at069-a@test.example", hashed_password="not-a-real-hash"),
                User(id=USER_B, email="at069-b@test.example", hashed_password="not-a-real-hash"),
            ]
        )
        db.flush()
        db.add_all(
            [
                Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER),
                Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.TRADER),
            ]
        )
        db.commit()
    yield factory


def _card() -> StrategyCard:
    spec = canonical_first_slice_authored_spec()
    return StrategyCard.model_validate(
        {
            "strategy_name": spec.name,
            "market_type": "crypto_perp",
            "asset_universe": ["BTCUSDT"],
            "timeframes": ["15m", "4h"],
            "entry_conditions": ["Bearish liquidity sweep at 4h resistance"],
            "confirmation_conditions": ["CVD divergence"],
            "invalidation": ["Close back above sweep high"],
            "stop_loss": ["Above sweep high"],
            "take_profit_plan": ["TP1 at 1R"],
            "runner_plan": [],
            "position_sizing": ["1%"],
            "add_rules": [],
            "no_trade_rules": [],
            "backtest_rules": [],
            "success_criteria": [],
            "validation_status": "draft",
        }
    )


def _create_strategy(session: Session, *, org: UUID = ORG, user: UUID = USER) -> UserStrategy:
    return StrategyLibraryService(session).create(
        UserStrategyCreate(
            organization_id=org,
            user_id=user,
            name=f"AT069 Sweep {uuid4().hex[:8]}",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            card=_card(),
        )
    )


def _attach_spec(session: Session, strategy_id: UUID, *, user_id: UUID = USER) -> object:
    from app.schemas.common import StrategyChangeSource

    strategy = session.get(UserStrategy, strategy_id)
    assert strategy is not None
    versioning = StrategyVersioningService(session)
    parent = versioning.selected_version(strategy)
    assert parent is not None
    return versioning.fork_semantic_update(
        strategy,
        parent=parent,
        card=parent.card,
        structured_rules=parent.structured_rules,
        lesson_source_metadata=parent.lesson_source_metadata,
        actor_user_id=user_id,
        source=StrategyChangeSource.PATTERN_SPEC,
        reason="attach pattern spec",
        pattern_spec=canonical_first_slice_authored_spec().model_dump(mode="json"),
    )


def _approve(session: Session, strategy: UserStrategy, version_id: UUID, *, user_id: UUID) -> None:
    StrategyVersioningService(session).append_lifecycle(
        organization_id=strategy.organization_id,
        strategy_id=strategy.id,
        strategy_version_id=version_id,
        new_state=StrategyLifecycleState.APPROVED,
        actor_user_id=user_id,
        reason="approve compiled first-slice policy",
    )


def _seed_approved_compiled(
    session: Session, *, org: UUID = ORG, user: UUID = USER
) -> tuple[UserStrategy, UUID]:
    created = _create_strategy(session, org=org, user=user)
    version = _attach_spec(session, created.id, user_id=user)
    session.flush()
    compiled = CompiledSetupService(session).compile_version(
        version.id, organization_id=org, user_id=user
    )
    assert compiled.compiled is not None
    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    strategy.paper_eligible = True
    _approve(session, strategy, version.id, user_id=user)
    session.commit()
    return strategy, version.id


def _snapshot_for_executable(
    world: EvaluatorWorld, executable: object
) -> WatcherCanonicalScanEvidence:
    command = world.command.model_copy(
        update={
            "organization_id": executable.organization_id,
            "strategy_version_id": executable.strategy_version_id,
            "executable_setup": executable.fusion_policy.executable_setup,
        }
    )
    return WatcherCanonicalScanEvidence(
        organization_id=executable.organization_id,
        policy=executable.fusion_policy,
        executable_policy=executable,  # type: ignore[arg-type]
        assessment_command=command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
        policy_authority=ExecutablePolicyAuthority.PERSISTED_APPROVED_COMPILED,
    )


class _WorldEvidencePort:
    def __init__(
        self,
        session: Session | None,
        store: WatcherStore,
        world: EvaluatorWorld,
        *,
        error: Exception | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._world = world
        self._error = error

    def load(self, command: EvaluationCommand) -> WatcherCanonicalScanEvidence | None:
        if self._error is not None:
            raise self._error
        if self._session is None:
            return None
        executable = resolve_watcher_scan_policy(self._session, command, store=self._store)
        if executable is None:
            return None
        return _snapshot_for_executable(self._world, executable)


class _FrozenEvidencePort:
    """Thread-safe snapshot port. Avoids sharing a SQLite session across workers."""

    def __init__(self, snapshot: WatcherCanonicalScanEvidence) -> None:
        self._snapshot = snapshot

    def load(self, command: EvaluationCommand) -> WatcherCanonicalScanEvidence | None:
        if command.request.organization_id != self._snapshot.organization_id:
            return None
        return self._snapshot


def _world_factory(world: EvaluatorWorld, *, error: Exception | None = None) -> object:
    def factory(
        session: Session | None, store: WatcherStore, _symbol: str
    ) -> WatcherScanEvidencePort:
        return _WorldEvidencePort(session, store, world, error=error)

    return factory


def _runtime(
    session_factory: sessionmaker[Session],
    *,
    world: EvaluatorWorld | None = None,
    error: Exception | None = None,
    enabled: bool = True,
    worker_id: str = "watcher-paper-a",
    store: WatcherStore | None = None,
    lifecycle: CandidateLifecycleService | None = None,
    clock: FakeClock | None = None,
    lease_ttl_seconds: int = 30,
    kill_switch_probe: object | None = None,
    target_loader: object | None = None,
    evidence_factory: object | None = None,
    bind_session: bool = True,
    scan_notification_hook: object | None = None,
    evaluation_observer_factory: object | None = None,
) -> tuple[WatcherPaperRuntime, FakeClock, SideEffectProbe, InMemoryWatcherStore]:
    resolved_clock = clock if clock is not None else FakeClock()
    resolved_store = store if store is not None else InMemoryWatcherStore()
    eval_clock = BoundEvaluationClock()
    if lifecycle is None:
        lifecycle = CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=eval_clock,
        )
    else:
        existing = getattr(lifecycle, "_clock", None)
        if isinstance(existing, BoundEvaluationClock):
            eval_clock = existing
    probe = SideEffectProbe()
    evidence_world = world if world is not None else make_world()
    resolved_evidence = evidence_factory or _world_factory(evidence_world, error=error)
    runtime = WatcherPaperRuntime(
        store=resolved_store,
        lifecycle=lifecycle,
        clock=resolved_clock,
        enabled=enabled,
        worker_id=worker_id,
        session_factory=session_factory if bind_session else None,
        evidence_factory=resolved_evidence,  # type: ignore[arg-type]
        kill_switch_probe=kill_switch_probe,  # type: ignore[arg-type]
        target_loader=target_loader,  # type: ignore[arg-type]
        side_effects=probe,
        lease_ttl_seconds=lease_ttl_seconds,
        poll_interval_seconds=0.05,
        settings=_settings(),
        evaluation_clock=eval_clock,
        scan_notification_hook=scan_notification_hook,  # type: ignore[arg-type]
        evaluation_observer_factory=evaluation_observer_factory,  # type: ignore[arg-type]
    )
    return runtime, resolved_clock, probe, resolved_store


def test_live_monitor_continues_polling_between_15m_evaluations(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from app.market_monitor.monitor import PerpetualMarketMonitor
    from app.market_monitor.watcher_gate import watcher_evidence_error_for_monitor
    from app.workers.watcher_market import SymbolMarketFactory
    from tests.support.live_market_monitor import ScriptedPerpetualSource
    from tests.test_live_market_monitor import T0, _trade

    with session_factory() as session:
        _seed_approved_compiled(session)
    clock = FakeClock(T0)
    world = make_world(include_resistance=False)
    tape = [_trade(i + 1, when=T0 + timedelta(seconds=i - 10)) for i in range(911)]

    class AvailableSource(ScriptedPerpetualSource):
        def fetch_ordered_trades(self, **kwargs):
            self.enqueue(tape)
            return super().fetch_ordered_trades(**kwargs)

    source = AvailableSource()
    monitor = PerpetualMarketMonitor(source, replay=False, clock=clock.now)

    class GatedPort(_WorldEvidencePort):
        def load(self, command):
            refusal = watcher_evidence_error_for_monitor(monitor.latest())
            if refusal is not None:
                raise refusal
            return super().load(command)

    class Factory(SymbolMarketFactory):
        def __call__(self, session, store, symbol):
            return GatedPort(session, store, world)

    factory = Factory(_settings(perpetual_evidence_source="binance_usdm"))
    monkeypatch.setattr(factory, "composition", lambda _symbol: (source, None, monitor, []))
    runtime, _, side_effects, _ = _runtime(
        session_factory, clock=clock, world=world, evidence_factory=factory
    )
    first = runtime.run_cycle()
    assert first.scans[0].reason_code == "watch"
    epoch = monitor._runtimes["BTCUSDT"]._assembler.connection_identity
    for minute in range(1, 16):
        clock.advance(60)
        report = runtime.run_cycle()
        assert report.scans[0].status == "succeeded", report.scans[0].reason_code
        assert report.scans[0].candidate_ids == ()
        if minute < 15:
            assert report.scans[0].replayed is True
        else:
            assert report.scans[0].reason_code == "watch"
    assert source.fetch_count >= 16
    cursor = monitor._runtimes["BTCUSDT"]._assembler
    assert cursor.connection_identity == epoch
    assert len(cursor.accepted_trades()) == 1
    assert side_effects.unused


@pytest.mark.parametrize("failure", ["gap", "outage"])
def test_between_evaluation_monitor_polling_preserves_fail_closed_errors(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from datetime import timedelta

    from app.market_monitor.monitor import PerpetualMarketMonitor
    from app.market_monitor.watcher_gate import watcher_evidence_error_for_monitor
    from app.workers.watcher_market import SymbolMarketFactory
    from tests.support.live_market_monitor import ScriptedPerpetualSource
    from tests.test_live_market_monitor import T0, _trade

    clock = FakeClock(T0)
    source = ScriptedPerpetualSource()
    source.enqueue([_trade(10, when=T0 - timedelta(seconds=1))])
    monitor = PerpetualMarketMonitor(source, replay=False, clock=clock.now)
    factory = SymbolMarketFactory(_settings(perpetual_evidence_source="binance_usdm"))
    monkeypatch.setattr(factory, "composition", lambda _symbol: (source, None, monitor, []))
    factory.poll_monitor("BTCUSDT")
    clock.advance(2)
    source.enqueue(
        [_trade(12, when=T0 + timedelta(seconds=1))]
        if failure == "gap"
        else RegionalProviderFailureError("provider unavailable")
    )
    factory.poll_monitor("BTCUSDT")
    snapshot = monitor.snapshot("BTCUSDT")
    refusal = watcher_evidence_error_for_monitor(snapshot)
    assert refusal is not None
    assert refusal.reason_code == (
        "canonical_evidence_unavailable" if failure == "gap" else "provider_outage"
    )
    assert snapshot.current_price is None
    assert source.fetch_count == 2


def test_replay_factory_does_not_poll_a_live_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.watcher_market import SymbolMarketFactory

    factory = SymbolMarketFactory(_settings())

    def unexpected_composition(_symbol):
        raise AssertionError("Replay upkeep must not acquire live evidence")

    monkeypatch.setattr(factory, "composition", unexpected_composition)
    factory.poll_monitor("BTCUSDT")


@pytest.mark.parametrize("scope", ["kill_switch", "nested", "sfp"])
def test_monitor_upkeep_respects_existing_safety_and_strategy_gates(
    session_factory: sessionmaker[Session], scope: str
) -> None:
    from dataclasses import replace

    from app.schemas.nested_continuation import NESTED_KIND
    from app.strategy_brain.sfp.contracts import SFP_KIND

    with session_factory() as session:
        _seed_approved_compiled(session)
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
    if scope != "kill_switch":
        targets = tuple(
            replace(t, fusion_policy_version=NESTED_KIND if scope == "nested" else SFP_KIND)
            for t in targets
        )
    world = make_world(include_resistance=False)
    factory = _world_factory(world)
    polled = []
    factory.poll_monitor = lambda symbol: polled.append(symbol)
    runtime, _, side_effects, _ = _runtime(
        session_factory,
        world=world,
        evidence_factory=factory,
        target_loader=lambda _session: targets,
        kill_switch_probe=lambda _org: scope == "kill_switch",
    )
    report = runtime.run_cycle()
    assert report.scans[0].candidate_ids == ()
    if scope == "kill_switch":
        assert report.scans[0].reason_code == "kill_switch_active"
        assert polled == []
    else:
        # The API reads worker-persisted monitor state, so non-default strategy
        # scopes still publish exactly one monitor tick per symbol per cycle.
        assert polled == ["BTCUSDT"]
    assert side_effects.unused


def test_monitor_publish_failure_is_logged_not_raised(
    session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    import structlog

    import app.workers.watcher_paper as watcher_paper_module

    # configure_logging caches the module logger on first use, so in the full
    # suite capture_logs would only see this event on stdout. Re-bind a fresh
    # lazy logger so capture_logs' processors apply (same pattern as
    # test_paper_exit_monitor.py).
    monkeypatch.setattr(
        watcher_paper_module, "logger", structlog.get_logger("workers.watcher_paper")
    )

    from app.strategy_brain.sfp.contracts import SFP_KIND

    with session_factory() as session:
        _seed_approved_compiled(session)
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
    targets = tuple(replace(t, fusion_policy_version=SFP_KIND) for t in targets)
    world = make_world(include_resistance=False)
    factory = _world_factory(world)
    calls: list[str] = []

    def failing_poll(symbol: str) -> None:
        calls.append(symbol)
        raise RuntimeError("exchange down")

    factory.poll_monitor = failing_poll
    runtime, _, _, _ = _runtime(
        session_factory,
        world=world,
        evidence_factory=factory,
        target_loader=lambda _session: targets,
    )
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        report = runtime.run_cycle()
    assert calls == ["BTCUSDT"]
    assert report.reason_code == "completed"
    events = [entry for entry in logs if entry["event"] == "market_monitor_snapshot_poll_failed"]
    assert events and events[0]["log_level"] == "warning"


def test_defaults_keep_paper_runtime_disabled() -> None:
    settings = _settings()
    assert settings.watcher_orchestration_enabled is False
    assert settings.watcher_paper_symbols == ["BTCUSDT"]
    assert paper_runtime_enabled(settings) is False
    assert paper_runtime_enabled(_settings(watcher_orchestration_enabled=True)) is True
    assert (
        paper_runtime_enabled(_settings(watcher_orchestration_enabled=True, execution_mode="paper"))
        is True
    )
    with pytest.raises(ValidationError, match="watcher_orchestration_enabled"):
        Settings(
            environment="staging",
            jwt_secret="x" * 32,
            database_url="postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
            redis_url="redis://redis.example.com:6379/0",
            qdrant_url="https://qdrant.example.com",
            openai_api_key="sk-test-not-a-real-key",
            cors_origins="https://app.example.com",
            auth_refresh_cookie_enabled=True,
            auth_cookie_secure=True,
            auth_cookie_samesite="none",
            enable_real_trading=False,
            execution_mode="paper",
            provider_mode="fallback",
            rate_limit_use_redis=True,
            rate_limit_allow_in_memory_fallback=False,
            trusted_proxy_hops=1,
            debug=False,
            watcher_orchestration_enabled=True,
        )


def test_btc_usdt_is_always_first_symbol() -> None:
    assert normalize_paper_symbols([]) == (FIRST_SLICE_SYMBOL,)
    assert normalize_paper_symbols(["ethusdt", "BTCUSDT"]) == ("BTCUSDT", "ETHUSDT")
    assert normalize_paper_symbols(["ETHUSDT"])[0] == "BTCUSDT"


def test_disabled_runtime_does_not_scan(session_factory: sessionmaker[Session]) -> None:
    runtime, _clock, probe, _store = _runtime(session_factory, enabled=False)
    report = runtime.run_cycle()
    assert report.reason_code == "watcher_disabled"
    assert report.scans == ()
    assert report.candidates_created == 0
    assert probe.unused is True


def test_confirmed_setup_creates_candidate(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
    runtime, _clock, probe, _store = _runtime(session_factory, world=world)
    report = runtime.run_cycle()
    assert len(report.scans) == 1
    scan = report.scans[0]
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value
    assert len(scan.candidate_ids) == 1
    assert report.candidates_created == 1
    assert probe.unused is True
    assert probe.execution == []


def test_watch_and_no_setup_create_no_candidate(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _seed_approved_compiled(session)
    watch_world = make_world(pattern_bars=False)
    runtime, _clock, probe, _store = _runtime(session_factory, world=watch_world)
    watch = runtime.run_cycle()
    assert watch.scans[0].reason_code == SetupAssessmentState.WATCH.value
    assert watch.scans[0].candidate_ids == ()
    no_setup_world = make_world(bar_15m_count=10, pattern_bars=True, include_snapshot=False)
    runtime_b, _clock_b, probe_b, _store_b = _runtime(
        session_factory, world=no_setup_world, worker_id="watcher-paper-b"
    )
    missing = runtime_b.run_cycle()
    assert missing.scans[0].reason_code == SetupAssessmentState.NO_SETUP.value
    assert missing.scans[0].candidate_ids == ()
    assert probe.unused and probe_b.unused


def test_duplicate_scan_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
    repo = InMemoryCandidateRepository()
    clock = FakeClock()
    lifecycle = CandidateLifecycleService(repository=repo, clock=BoundEvaluationClock())
    runtime, _clock, probe, _store = _runtime(
        session_factory, world=world, lifecycle=lifecycle, clock=clock
    )
    first = runtime.run_cycle()
    second = runtime.run_cycle()
    assert len(first.scans[0].candidate_ids) == 1
    created_id = first.scans[0].candidate_ids[0]
    assert second.scans[0].replayed is True
    assert second.scans[0].reason_code == "replay"
    assert second.scans[0].candidate_ids == ()
    assert repo.get_by_id(ORG, created_id) is not None
    assert probe.unused is True


def test_restart_recovers_same_candidate(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
    repo = InMemoryCandidateRepository()
    clock = FakeClock()
    store = InMemoryWatcherStore()
    lifecycle = CandidateLifecycleService(repository=repo, clock=BoundEvaluationClock())
    first, _c, probe, _s = _runtime(
        session_factory,
        world=world,
        lifecycle=lifecycle,
        clock=clock,
        store=store,
        worker_id="watcher-paper-a",
    )
    created = first.run_cycle()
    assert len(created.scans[0].candidate_ids) == 1
    created_id = created.scans[0].candidate_ids[0]
    clock.advance(31)
    restarted, _c2, probe2, _s2 = _runtime(
        session_factory,
        world=world,
        lifecycle=lifecycle,
        clock=clock,
        store=store,
        worker_id="watcher-paper-a",
    )
    recovered = restarted.run_cycle()
    assert recovered.scans[0].replayed is True
    assert recovered.scans[0].reason_code == "replay"
    assert repo.get_by_id(ORG, created_id) is not None
    assert probe.unused and probe2.unused


def test_concurrent_workers_single_lease(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _strategy, version_id = _seed_approved_compiled(session)
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
        assert targets
        executable = resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=version_id
        )
        assert executable is not None
        snapshot = _snapshot_for_executable(world, executable)

    def _factory(
        _session: Session | None, _store: WatcherStore, _symbol: str
    ) -> WatcherScanEvidencePort:
        return _FrozenEvidencePort(snapshot)

    store = InMemoryWatcherStore()
    clock = FakeClock()
    repo = InMemoryCandidateRepository()
    lifecycle = CandidateLifecycleService(repository=repo, clock=BoundEvaluationClock())
    shared = {
        "store": store,
        "clock": clock,
        "lifecycle": lifecycle,
        "target_loader": lambda _session: targets,
        "evidence_factory": _factory,
        "bind_session": False,
        "world": world,
    }
    left, _c, probe_a, _s = _runtime(session_factory, worker_id="worker-left", **shared)
    right, _c2, probe_b, _s2 = _runtime(session_factory, worker_id="worker-right", **shared)
    barrier = threading.Barrier(2)
    results: list[WatcherPaperCycleReport] = []
    lock = threading.Lock()

    def _run(runtime: WatcherPaperRuntime) -> None:
        barrier.wait(timeout=10)
        report = runtime.run_cycle()
        with lock:
            results.append(report)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run, left), pool.submit(_run, right)]
        for future in futures:
            future.result(timeout=30)
    assert len(results) == 2
    statuses = sorted(item.scans[0].status for item in results)
    reasons = {item.scans[0].reason_code for item in results}
    assert "succeeded" in statuses or SetupAssessmentState.CONFIRMED_SETUP.value in reasons
    assert "skipped" in statuses or "lease_held" in reasons
    candidate_ids = [item.scans[0].candidate_ids for item in results]
    created = [ids for ids in candidate_ids if ids]
    assert len(created) <= 1
    assert probe_a.unused and probe_b.unused


def test_lease_takeover_after_ttl(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
    store = InMemoryWatcherStore()
    clock = FakeClock()
    first, clock, probe, store = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="stale-owner",
        lease_ttl_seconds=1,
    )
    first.run_cycle()
    clock.advance(2)
    successor, _c, probe2, _s = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="takeover",
        lease_ttl_seconds=1,
    )
    report = successor.run_cycle()
    assert report.scans[0].status in {"succeeded", "skipped"}
    lease = store.get_lease(ORG, report.scans[0].scan_scope)
    assert lease is not None
    assert lease.owner_id == "takeover"
    assert probe.unused and probe2.unused


def test_stale_evidence_creates_no_candidate(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _seed_approved_compiled(session)
    runtime, _clock, probe, _store = _runtime(
        session_factory,
        error=WatcherEvidenceUnavailableError("stale", reason_code="stale_evidence"),
    )
    report = runtime.run_cycle()
    assert report.scans[0].reason_code == "stale_evidence"
    assert report.scans[0].candidate_ids == ()
    assert probe.unused is True


def test_provider_outage_creates_no_candidate(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _seed_approved_compiled(session)
    runtime, _clock, probe, _store = _runtime(
        session_factory,
        error=WatcherEvidenceUnavailableError("outage", reason_code="provider_outage"),
    )
    report = runtime.run_cycle()
    assert report.scans[0].reason_code == "provider_outage"
    assert report.scans[0].candidate_ids == ()
    assert probe.unused is True


def test_wrong_tenant_creates_no_candidate(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
        assert targets
        foreign = PaperScanTarget(
            organization_id=ORG_B,
            user_id=USER_B,
            strategy_id=targets[0].strategy_id,
            strategy_version_id=targets[0].strategy_version_id,
            compiled_setup_definition_id=targets[0].compiled_setup_definition_id,
            compiled_content_hash=targets[0].compiled_content_hash,
            fusion_policy_version=targets[0].fusion_policy_version,
            symbol=targets[0].symbol,
        )

    runtime, _clock, probe, _store = _runtime(
        session_factory, world=world, target_loader=lambda _session: (foreign,)
    )
    report = runtime.run_cycle()
    assert report.scans[0].candidate_ids == ()
    assert report.scans[0].reason_code in {
        "missing_canonical_evidence",
        "organization_mismatch",
        "strategy_not_approved",
    }
    assert probe.unused is True


def test_wrong_strategy_lineage_creates_no_candidate(
    session_factory: sessionmaker[Session],
) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
        draft = _create_strategy(session)
        session.commit()
        draft_id = draft.id

    def _draft_loader(_session: Session | None) -> tuple[PaperScanTarget, ...]:
        return (
            PaperScanTarget(
                organization_id=ORG,
                user_id=USER,
                strategy_id=draft_id,
                strategy_version_id=uuid4(),
                compiled_setup_definition_id=uuid4(),
                compiled_content_hash="ab" * 32,
                fusion_policy_version="first-slice-fusion/v1",
                symbol="BTCUSDT",
            ),
        )

    runtime, _clock, probe, _store = _runtime(
        session_factory, world=world, target_loader=_draft_loader
    )
    report = runtime.run_cycle()
    assert report.scans[0].candidate_ids == ()
    assert report.scans[0].reason_code in {
        "missing_canonical_evidence",
        "strategy_not_approved",
        "draft_not_executable",
    }
    assert probe.unused is True


def test_expiry_creates_no_candidate(session_factory: sessionmaker[Session]) -> None:
    base = make_world()
    later = subsequent_bars(base.trigger, count=2, high=base.trigger.high)
    world = EvaluatorWorld(
        policy=base.policy,
        command=base.command,
        evidence=base.evidence.model_copy(update={"subsequent_final_15m": tuple(later)}),
        evaluated_at=base.evaluated_at,
        bars_15m=base.bars_15m,
        bars_4h=base.bars_4h,
        snapshot=base.snapshot,
        trigger=base.trigger,
    )
    with session_factory() as session:
        _seed_approved_compiled(session)
    runtime, _clock, probe, _store = _runtime(session_factory, world=world)
    report = runtime.run_cycle()
    assert report.scans[0].reason_code == SetupAssessmentState.EXPIRED.value
    assert report.scans[0].candidate_ids == ()
    assert probe.unused is True


def test_kill_switch_does_not_place_orders(session_factory: sessionmaker[Session]) -> None:
    world = make_world()
    with session_factory() as session:
        _seed_approved_compiled(session)
        session.add(KillSwitchState(organization_id=ORG, active=True, version=1, reason="halt"))
        session.commit()
    runtime, _clock, probe, _store = _runtime(session_factory, world=world)
    report = runtime.run_cycle()
    assert report.kill_switch_active is True
    assert report.scans[0].kill_switch_active is True
    assert report.scans[0].reason_code == "kill_switch_active"
    assert report.scans[0].candidate_ids == ()
    assert report.candidates_created == 0
    assert probe.unused is True
    assert probe.execution == []
    assert probe.telegram == []


def test_graceful_shutdown_stops_loop(session_factory: sessionmaker[Session]) -> None:
    runtime, _clock, _probe, _store = _runtime(session_factory, enabled=True)
    thread = runtime.start_background_thread()
    runtime.stop()
    thread.join(timeout=2)
    assert thread.is_alive() is False
    assert runtime.snapshot().running is False


def test_status_endpoint_requires_auth_and_stays_disabled() -> None:
    settings = _settings()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.core.config import get_settings
    from app.db.session import get_session

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        denied = client.get("/watcher/paper-runtime/status")
        assert denied.status_code == 401
        registered = client.post(
            "/auth/register",
            json={
                "email": "watcher-paper@test.example",
                "password": "secure-password-1",
                "organization_name": "Watcher Paper Org",
            },
        )
        assert registered.status_code == 201, registered.text
        token = registered.json()["tokens"]["access_token"]
        body = client.get(
            "/watcher/paper-runtime/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert body.status_code == 200, body.text
        payload = body.json()
        assert payload["enabled"] is False
        assert payload["running"] is False
        assert payload["paper_only"] is True
        assert payload["real_trading_enabled"] is False
        assert payload["telegram_enabled"] is False
        assert payload["symbols"][0] == "BTCUSDT"
        assert payload["remaining_activation_requirements"]
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_last_closed_interval_is_deterministic() -> None:
    moment = datetime.fromisoformat("2026-09-21T16:07:33+00:00")
    closed = last_closed_interval_end(moment)
    assert closed.minute == 0
    assert closed.hour == 16


def test_orchestrator_config_default_stays_disabled() -> None:
    config = WatcherRuntimeConfig()
    assert config.enabled is False


def test_stale_and_outage_errors_are_classified() -> None:
    assert isinstance(StaleEvidenceError("x"), Exception)
    assert isinstance(RegionalProviderFailureError("x"), Exception)
    assert WatcherEvidenceUnavailableError("x", reason_code="stale_evidence").reason_code == (
        "stale_evidence"
    )


def test_list_targets_skip_drafts_and_foreign_tenants(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_approved_compiled(session)
        _create_strategy(session)
        session.commit()
        own = list_paper_scan_targets(session, symbols=["BTCUSDT"], organization_id=ORG, limit=20)
        foreign = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG_B, limit=20
        )
    assert len(own) == 1
    assert own[0].organization_id == ORG
    assert own[0].symbol == FIRST_SLICE_SYMBOL
    assert foreign == ()


def test_default_evidence_factory_uses_canonical_port() -> None:
    factory = default_paper_evidence_factory(_settings())
    port = factory(None, InMemoryWatcherStore(), FIRST_SLICE_SYMBOL)
    assert isinstance(port, AssemblingWatcherScanEvidence)
    assert isinstance(port._monitor, MarketMonitorWatcherPort)


def _publish_scan(
    symbol: str,
    *,
    org: UUID = ORG,
    scope: str = "s",
    killed: bool = False,
    owner: str | None = "self",
):
    from app.workers.watcher_paper import WatcherPaperScanReport

    return WatcherPaperScanReport(
        organization_id=org,
        scan_scope=f"{scope}:15m",
        symbol=symbol,
        status="kill_switch_active" if killed else "succeeded",
        reason_code="kill_switch_active" if killed else "replay",
        replayed=not killed,
        published=False,
        candidate_ids=(),
        kill_switch_active=killed,
        lease_owner=owner,
    )


def _publish_runtime(session_factory: sessionmaker[Session]):
    world = make_world(include_resistance=False)
    factory = _world_factory(world)
    polled: list[str] = []
    factory.poll_monitor = lambda symbol: polled.append(symbol)
    runtime, _, _, _ = _runtime(session_factory, world=world, evidence_factory=factory)
    return runtime, factory, polled


def _resolve(runtime, *scans):
    return [replace_owner(s, runtime._worker_id) if s.lease_owner == "self" else s for s in scans]


def replace_owner(scan, owner):
    from dataclasses import replace

    return replace(scan, lease_owner=owner)


def test_monitor_publish_many_scopes_one_symbol_polls_once(
    session_factory: sessionmaker[Session],
) -> None:
    runtime, _, polled = _publish_runtime(session_factory)
    runtime._publish_monitor_snapshots(
        _resolve(runtime, *(_publish_scan("BTCUSDT", scope=f"s{i}") for i in range(3)))
    )
    assert polled == ["BTCUSDT"]


def test_monitor_publish_mixed_killed_and_live_orgs(
    session_factory: sessionmaker[Session],
) -> None:
    runtime, _, polled = _publish_runtime(session_factory)
    runtime._publish_monitor_snapshots(
        _resolve(
            runtime,
            _publish_scan("BTCUSDT", org=ORG, killed=True),
            _publish_scan("BTCUSDT", org=ORG_B, scope="b"),
            _publish_scan("ETHUSDT", org=ORG, killed=True, scope="c"),
        )
    )
    assert polled == ["BTCUSDT"]


def test_monitor_publish_all_orgs_killed_polls_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    runtime, _, polled = _publish_runtime(session_factory)
    runtime._publish_monitor_snapshots(
        _resolve(
            runtime,
            _publish_scan("BTCUSDT", org=ORG, killed=True),
            _publish_scan("BTCUSDT", org=ORG_B, killed=True, scope="b"),
        )
    )
    assert polled == []


def test_monitor_publish_two_symbols_each_once(
    session_factory: sessionmaker[Session],
) -> None:
    runtime, _, polled = _publish_runtime(session_factory)
    runtime._publish_monitor_snapshots(
        _resolve(
            runtime,
            _publish_scan("BTCUSDT"),
            _publish_scan("ETHUSDT", scope="e"),
            _publish_scan("BTCUSDT", scope="b2"),
            _publish_scan("ETHUSDT", scope="e2"),
        )
    )
    assert polled == ["BTCUSDT", "ETHUSDT"]


def test_monitor_publish_skips_scopes_leased_by_another_worker(
    session_factory: sessionmaker[Session],
) -> None:
    runtime, _, polled = _publish_runtime(session_factory)
    runtime._publish_monitor_snapshots(
        _resolve(
            runtime,
            _publish_scan("BTCUSDT", owner="other-worker"),
            _publish_scan("ETHUSDT", owner=None, scope="e"),
        )
    )
    assert polled == []


def test_default_policy_scope_polls_once_per_cycle(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _seed_approved_compiled(session)
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
    world = make_world(include_resistance=False)
    factory = _world_factory(world)
    polled: list[str] = []
    factory.poll_monitor = lambda symbol: polled.append(symbol)
    runtime, _, _, _ = _runtime(
        session_factory,
        world=world,
        evidence_factory=factory,
        target_loader=lambda _session: targets,
    )
    runtime.run_cycle()
    assert polled == ["BTCUSDT"]


def test_monitor_poll_failure_warning_is_rate_limited(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.workers.watcher_paper as wp

    logs: list[dict[str, object]] = []

    class _Log:
        def warning(self, event: str, **kw: object) -> None:
            logs.append({"event": event, "log_level": "warning", **kw})

        def info(self, event: str, **kw: object) -> None:
            logs.append({"event": event, "log_level": "info", **kw})

    runtime, factory, _ = _publish_runtime(session_factory)

    def failing(_symbol: str) -> None:
        raise RuntimeError("exchange down")

    factory.poll_monitor = failing
    clock = {"t": 1000.0}
    monkeypatch.setattr(wp.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(wp, "logger", _Log())
    scans = _resolve(runtime, _publish_scan("BTCUSDT"))
    for _ in range(5):
        runtime._publish_monitor_snapshots(scans)
        clock["t"] += 10
    clock["t"] += wp.MONITOR_POLL_FAILURE_SUMMARY_SECONDS
    runtime._publish_monitor_snapshots(scans)
    factory.poll_monitor = lambda _symbol: None
    runtime._publish_monitor_snapshots(scans)
    names = [e["event"] for e in logs]
    assert names.count("market_monitor_snapshot_poll_failed") == 1
    first = next(e for e in logs if e["event"] == "market_monitor_snapshot_poll_failed")
    assert first["exc_info"] is True
    summary = next(e for e in logs if e["event"] == "market_monitor_snapshot_poll_failing")
    assert names.count("market_monitor_snapshot_poll_failing") == 1
    assert summary["suppressed_failures"] == 5
    assert summary["error"] == "RuntimeError"
    assert "exc_info" not in summary
    assert names.count("market_monitor_snapshot_poll_recovered") == 1
