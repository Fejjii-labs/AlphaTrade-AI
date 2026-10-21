"""Watcher scans must not evaluate forming M15 candles at bar boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.errors import FormingCandleError
from app.watcher.contracts import EvaluationOutcome, EvaluationStatus, WorkerCycleStatus
from app.watcher.errors import WatcherEvidenceUnavailableError
from app.workers.watcher_market import SymbolMarketFactory, _wait_past_configured_boundary
from app.workers.watcher_paper import WatcherPaperRuntime, last_closed_interval_end
from app.workers.watcher_paper_targets import PaperScanTarget

ORG = UUID("00000000-0000-0000-0000-00000000a069")
USER = UUID("00000000-0000-0000-0000-00000000c069")
STRATEGY = UUID("00000000-0000-0000-0000-00000000d069")
VERSION = UUID("00000000-0000-0000-0000-00000000e069")
COMPILED = UUID("00000000-0000-0000-0000-00000000f069")
COMPILED_HASH = "a" * 64


def _settings(**updates: object) -> Settings:
    payload: dict[str, object] = {
        "_env_file": None,
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "database_url": "sqlite+pysqlite:///:memory:",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "watcher_orchestration_enabled": False,
        "perpetual_evidence_source": "binance_usdm",
        "binance_request_max_retries": 0,
    }
    payload.update(updates)
    return Settings(**payload)  # type: ignore[arg-type]


def _frozen(moment: datetime):
    sleeps: list[float] = []

    def clock() -> datetime:
        return moment if not sleeps else moment + timedelta(seconds=sum(sleeps))

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return clock, sleeper, sleeps


def test_configured_boundary_wait_sleeps_only_inside_delay_window() -> None:
    inside = datetime(2026, 10, 21, 18, 15, 2, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(inside)
    delay = timedelta(seconds=5)
    assert _wait_past_configured_boundary(inside, delay, now=clock, sleeper=sleeper) is True
    assert sleeps == [3.0]

    settled = datetime(2026, 10, 21, 18, 15, 6, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(settled)
    assert _wait_past_configured_boundary(settled, delay, now=clock, sleeper=sleeper) is False
    assert sleeps == []


def test_ensure_bar_close_ready_waits_for_binance_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moment = datetime(2026, 10, 21, 18, 15, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.workers.watcher_market.sleep",
        lambda _seconds: pytest.fail("injected clock must not call time.sleep"),
    )
    clock, sleeper, sleeps = _frozen(moment)
    factory = SymbolMarketFactory(_settings(), clock=clock, sleeper=sleeper)
    monkeypatch.setattr(
        factory,
        "composition",
        lambda _symbol: (BinanceUsdmPerpetualSource(), object(), None, ()),
    )
    assert factory.ensure_bar_close_ready("BTCUSDT") is True
    assert sleeps == [5.0]
    assert clock() == moment + timedelta(seconds=5)


def test_ensure_bar_close_ready_honors_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 10, 21, 18, 15, 1, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(moment)
    factory = SymbolMarketFactory(
        _settings(watcher_paper_bar_close_delay_seconds=6.0),
        clock=clock,
        sleeper=sleeper,
    )
    monkeypatch.setattr(
        factory,
        "composition",
        lambda _symbol: (object(), object(), None, ()),
    )
    assert factory.ensure_bar_close_ready("BTCUSDT") is True
    assert sleeps == [5.0]


def test_forming_candle_maps_to_distinct_reason_code() -> None:
    with pytest.raises(WatcherEvidenceUnavailableError) as failure:
        AssemblingWatcherScanEvidence._raise_undiagnosed(
            FormingCandleError("Binance REST candle is inside the post-close settlement guard.")
        )
    assert failure.value.reason_code == "forming_candle"


def test_last_closed_interval_end_4h_stable_within_period() -> None:
    interval = 4 * 3600
    early = datetime(2026, 10, 21, 13, 30, tzinfo=UTC)
    later = datetime(2026, 10, 21, 14, 45, tzinfo=UTC)
    assert last_closed_interval_end(early, interval_seconds=interval) == datetime(
        2026, 10, 21, 12, 0, tzinfo=UTC
    )
    assert last_closed_interval_end(later, interval_seconds=interval) == datetime(
        2026, 10, 21, 12, 0, tzinfo=UTC
    )


def test_last_closed_interval_end_1d_stable_within_utc_day() -> None:
    interval = 24 * 3600
    morning = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    evening = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    expected = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    assert last_closed_interval_end(morning, interval_seconds=interval) == expected
    assert last_closed_interval_end(evening, interval_seconds=interval) == expected


def test_idempotency_key_15m_matches_legacy_epoch_floor() -> None:
    from app.signal_fusion.lifecycle import CandidateLifecycleService
    from app.signal_fusion.memory import InMemoryCandidateRepository
    from app.watcher.fusion_evaluation import BoundEvaluationClock
    from app.watcher.memory import FakeClock, InMemoryWatcherStore

    interval = 900
    for moment in (
        datetime(2026, 10, 21, 18, 14, 59, tzinfo=UTC),
        datetime(2026, 10, 21, 18, 15, 0, tzinfo=UTC),
        datetime(2026, 10, 21, 18, 15, 7, tzinfo=UTC),
    ):
        clock = FakeClock(moment)
        runtime = WatcherPaperRuntime(
            store=InMemoryWatcherStore(),
            lifecycle=CandidateLifecycleService(
                repository=InMemoryCandidateRepository(),
                clock=BoundEvaluationClock(),
            ),
            clock=clock,
            enabled=False,
            evidence_factory=lambda *_args: pytest.fail("unused"),
        )
        target = PaperScanTarget(
            organization_id=ORG,
            user_id=USER,
            strategy_id=STRATEGY,
            strategy_version_id=VERSION,
            compiled_setup_definition_id=COMPILED,
            compiled_content_hash=COMPILED_HASH,
            fusion_policy_version="first_slice_v1",
            symbol="BTCUSDT",
            timeframe="15m",
        )
        legacy = datetime.fromtimestamp(int(moment.timestamp()) // interval * interval, UTC)
        assert runtime._idempotency_key(target).endswith(legacy.strftime("%Y%m%dT%H%M%SZ"))


def test_idempotency_key_uses_last_closed_interval_end() -> None:
    from app.signal_fusion.lifecycle import CandidateLifecycleService
    from app.signal_fusion.memory import InMemoryCandidateRepository
    from app.watcher.fusion_evaluation import BoundEvaluationClock
    from app.watcher.memory import FakeClock, InMemoryWatcherStore

    moment = datetime(2026, 10, 21, 18, 14, 59, tzinfo=UTC)
    clock = FakeClock(moment)
    eval_clock = BoundEvaluationClock()
    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=eval_clock,
        ),
        clock=clock,
        enabled=False,
        evidence_factory=lambda *_args: pytest.fail("unused"),
    )
    target = PaperScanTarget(
        organization_id=ORG,
        user_id=USER,
        strategy_id=STRATEGY,
        strategy_version_id=VERSION,
        compiled_setup_definition_id=COMPILED,
        compiled_content_hash=COMPILED_HASH,
        fusion_policy_version="first_slice_v1",
        symbol="BTCUSDT",
        timeframe="15m",
    )
    closed = last_closed_interval_end(moment, interval_seconds=900)
    assert runtime._idempotency_key(target).endswith(closed.strftime("%Y%m%dT%H%M%SZ"))


def test_wait_for_closed_bar_delegates_to_factory() -> None:
    from app.signal_fusion.lifecycle import CandidateLifecycleService
    from app.signal_fusion.memory import InMemoryCandidateRepository, UtcClock
    from app.watcher.fusion_evaluation import BoundEvaluationClock
    from app.watcher.memory import InMemoryWatcherStore

    calls: list[str] = []

    class _Factory:
        def ensure_bar_close_ready(self, symbol: str) -> bool:
            calls.append(symbol)
            return True

        def __call__(self, *_args: object) -> object:
            raise AssertionError("not used")

    eval_clock = BoundEvaluationClock()
    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=eval_clock,
        ),
        clock=UtcClock(),
        enabled=False,
        evidence_factory=_Factory(),
    )
    assert runtime._wait_for_closed_bar("ETHUSDT") is True
    assert calls == ["ETHUSDT"]


def _paper_target() -> PaperScanTarget:
    return PaperScanTarget(
        organization_id=ORG,
        user_id=USER,
        strategy_id=STRATEGY,
        strategy_version_id=VERSION,
        compiled_setup_definition_id=COMPILED,
        compiled_content_hash=COMPILED_HASH,
        fusion_policy_version="first_slice_v1",
        symbol="BTCUSDT",
        timeframe="15m",
    )


def _forming_cycle_result() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        status=WorkerCycleStatus.FAILED,
        outcome=EvaluationOutcome(
            command_id=uuid4(),
            request_hash="a" * 64,
            evaluation_input_hash="b" * 64,
            status=EvaluationStatus.FAILED,
            reason_code="forming_candle",
            candidate_ids=(),
        ),
        reason_code="forming_candle",
    )


def _ok_cycle_result() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        status=WorkerCycleStatus.SUCCEEDED,
        outcome=EvaluationOutcome(
            command_id=uuid4(),
            request_hash="a" * 64,
            evaluation_input_hash="b" * 64,
            status=EvaluationStatus.SUCCEEDED,
            reason_code="no_setup",
            candidate_ids=(),
        ),
        reason_code="no_setup",
    )


def _runtime_for_retry_tests() -> WatcherPaperRuntime:
    from app.signal_fusion.lifecycle import CandidateLifecycleService
    from app.signal_fusion.memory import InMemoryCandidateRepository, UtcClock
    from app.watcher.fusion_evaluation import BoundEvaluationClock
    from app.watcher.memory import InMemoryWatcherStore

    waits: list[str] = []

    class _Factory:
        def ensure_bar_close_ready(self, symbol: str) -> bool:
            waits.append(symbol)
            return True

        def __call__(self, *_args: object) -> object:
            raise AssertionError("not used")

    factory = _Factory()
    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=BoundEvaluationClock(),
        ),
        clock=UtcClock(),
        enabled=False,
        evidence_factory=factory,
        worker_id="watcher-test",
    )
    runtime._retry_test_waits = waits  # type: ignore[attr-defined]
    return runtime


class _RecordingOrchestrator:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def run_worker(self, _request: object, *, worker_id: str, held_fencing_token: object) -> object:
        self.calls += 1
        return self._outcomes[self.calls - 1]


def test_forming_candle_triggers_exactly_one_retry() -> None:
    from app.watcher.contracts import ScanRequest

    runtime = _runtime_for_retry_tests()
    orchestrator = _RecordingOrchestrator([_forming_cycle_result(), _ok_cycle_result()])
    request = ScanRequest(
        organization_id=ORG,
        principal_id=None,
        scan_scope="scope",
        policy_id=uuid4(),
        policy_version=1,
        policy_content_hash="c" * 64,
        watchlist_item_ids=(uuid4(),),
        timeframe="15m",
        idempotency_key="test-key",
    )
    result = runtime._run_worker_with_forming_retry(
        orchestrator,
        request=request,
        target=_paper_target(),
    )
    assert orchestrator.calls == 2
    assert result.reason_code == "no_setup"


def test_second_forming_candle_stays_failed_without_third_attempt() -> None:
    from app.watcher.contracts import ScanRequest

    runtime = _runtime_for_retry_tests()
    orchestrator = _RecordingOrchestrator(
        [_forming_cycle_result(), _forming_cycle_result()],
    )
    request = ScanRequest(
        organization_id=ORG,
        principal_id=None,
        scan_scope="scope",
        policy_id=uuid4(),
        policy_version=1,
        policy_content_hash="c" * 64,
        watchlist_item_ids=(uuid4(),),
        timeframe="15m",
        idempotency_key="test-key-2",
    )
    result = runtime._run_worker_with_forming_retry(
        orchestrator,
        request=request,
        target=_paper_target(),
    )
    assert orchestrator.calls == 2
    assert result.reason_code == "forming_candle"
