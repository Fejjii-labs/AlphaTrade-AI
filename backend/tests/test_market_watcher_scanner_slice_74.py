"""Tests for market watcher setup detector integration (Slice 74)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.analysis.types import (
    AnalysisResult,
    ConfidenceScore,
    Indicators,
    MarketStructure,
    SetupDetection,
)
from app.core.config import ExecutionMode, Settings, get_settings
from app.db.base import Base
from app.db.models import Membership, Organization, PaperValidationAlert, User
from app.db.session import get_session
from app.main import create_app
from app.providers.market_data import MockMarketDataProvider, OHLCVBar
from app.schemas.common import (
    AlertDeliveryChannel,
    AlertDeliveryStatus,
    MembershipRole,
)
from app.schemas.market_watcher import (
    CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
    SCAN_CONFIRM_PHRASE,
    MarketWatcherScanRequest,
)
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter
from app.services.market_data_service import MarketDataService
from app.services.market_watcher_scanner import detect_candidates
from app.services.market_watcher_service import MarketWatcherService
from app.services.market_watcher_setup_detectors import (
    SETUP_DETECTOR_VERSIONS,
    detect_setup_candidates,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000007401")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000007402")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-000000007403")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "slice74-scanner-secret-minimum-32",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def slice74_db() -> Iterator[sessionmaker[Session]]:
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
    settings = Settings(**_BASE)
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice74 Org")
        owner = User(
            id=USER_ID,
            email="owner74@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        trader = User(
            id=OTHER_USER,
            email="trader74@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        session.add(org)
        session.add(owner)
        session.add(trader)
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.add(
            Membership(user_id=OTHER_USER, organization_id=ORG_ID, role=MembershipRole.TRADER)
        )
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _client(
    slice74_db: sessionmaker[Session],
    *,
    settings_overrides: dict | None = None,
    role: MembershipRole = MembershipRole.OWNER,
) -> TestClient:
    overrides = dict(_BASE)
    if settings_overrides:
        overrides.update(settings_overrides)
    settings = Settings(**overrides)
    get_settings.cache_clear()
    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with slice74_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    email = "owner74@test.example" if role == MembershipRole.OWNER else "trader74@test.example"
    login = client.post("/auth/login", json={"email": email, "password": "TestPassword123!"})
    token = login.json()["tokens"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _flat_bars(count: int = 40, *, price: float = 100.0) -> list[OHLCVBar]:
    now = datetime.now(UTC)
    return [
        OHLCVBar(
            open=price,
            high=price * 1.001,
            low=price * 0.999,
            close=price,
            volume=1000.0,
            timestamp=now - timedelta(minutes=15 * (count - i)),
        )
        for i in range(count)
    ]


def _analysis_result(
    *,
    detections: tuple[SetupDetection, ...],
    confidence: float = 72.5,
) -> AnalysisResult:
    return AnalysisResult(
        symbol="BTCUSDT",
        timeframe="15m",
        bar_count=40,
        indicators=Indicators(
            sma_fast=100.0,
            sma_slow=99.0,
            ema_fast=100.0,
            ema_slow=95.0,
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            macd_hist=0.0,
            atr=2.0,
            vwap=100.0,
            volume=1000.0,
            volume_avg=900.0,
            volume_ratio=1.1,
            volatility=1.0,
            funding_rate=None,
        ),
        structure=MarketStructure(trend="uptrend", last_label="HH", swing_points=()),
        support_levels=(),
        resistance_levels=(),
        fibonacci=None,
        detections=detections,
        no_trade_filters=(),
        no_trade=False,
        confidence=ConfidenceScore(score=confidence, factors=()),
    )


def test_liquidity_sweep_candidate_maps_correctly() -> None:
    detection = SetupDetection(
        name="liquidity_sweep",
        detected=True,
        direction="long",
        reason="Swept liquidity below prior swing low and closed back above it.",
        metrics={"swept_level": 98.0, "low": 97.5, "atr": 2.0},
    )
    bars = _flat_bars()
    bars[-1] = OHLCVBar(
        open=99.0,
        high=100.0,
        low=97.5,
        close=99.5,
        volume=1000.0,
        timestamp=bars[-1].timestamp,
    )
    result = _analysis_result(detections=(detection,))
    candidates = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.condition == "liquidity_sweep"
    assert candidate.direction == "long"
    assert candidate.trigger_level == 98.0
    assert candidate.invalidation_level == 97.5
    assert candidate.reason == detection.reason
    assert candidate.confidence == 72.5
    assert candidate.source == "market_watcher"
    assert candidate.detector_version == SETUP_DETECTOR_VERSIONS["liquidity_sweep"]
    assert candidate.metrics["source"] == "market_watcher"


def test_sfp_candidate_maps_correctly() -> None:
    detection = SetupDetection(
        name="sfp",
        detected=True,
        direction="short",
        reason="Bearish swing failure above prior swing high.",
        metrics={"failed_level": 201.0, "close": 199.0},
    )
    bars = _flat_bars(price=200.0)
    bars[-1] = OHLCVBar(
        open=200.5,
        high=201.5,
        low=198.5,
        close=199.0,
        volume=1000.0,
        timestamp=bars[-1].timestamp,
    )
    result = _analysis_result(detections=(detection,))
    candidates = detect_setup_candidates(
        symbol="ETHUSDT",
        timeframe="1h",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.condition == "sfp"
    assert candidate.direction == "short"
    assert candidate.trigger_level == 201.0
    assert candidate.invalidation_level == 201.5
    assert candidate.detector_version == SETUP_DETECTOR_VERSIONS["sfp"]


def test_trend_pullback_candidate_maps_correctly() -> None:
    detection = SetupDetection(
        name="trend_pullback",
        detected=True,
        direction="long",
        reason="Uptrend pullback to the fast EMA.",
        metrics={"close": 99.0, "ema_fast": 100.0, "ema_slow": 90.0},
    )
    bars = _flat_bars(price=99.0)
    result = _analysis_result(detections=(detection,))
    candidates = detect_setup_candidates(
        symbol="SOLUSDT",
        timeframe="15m",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.condition == "trend_pullback"
    assert candidate.direction == "long"
    assert candidate.trigger_level == 100.0
    assert candidate.invalidation_level == 90.0
    assert candidate.detector_version == SETUP_DETECTOR_VERSIONS["trend_pullback"]


def test_detector_error_degrades_without_crashing_scan() -> None:
    bars = _flat_bars()
    bad = SetupDetection(
        name="liquidity_sweep",
        detected=True,
        direction="long",
        reason="bad metrics",
        metrics={"swept_level": "not-a-number"},  # type: ignore[arg-type]
    )
    good = SetupDetection(
        name="sfp",
        detected=True,
        direction="short",
        reason="Bearish swing failure above prior swing high.",
        metrics={"failed_level": 101.0, "close": 99.0},
    )
    bars[-1] = OHLCVBar(
        open=100.5,
        high=101.5,
        low=99.0,
        close=99.5,
        volume=1000.0,
        timestamp=bars[-1].timestamp,
    )
    result = _analysis_result(detections=(bad, good))
    candidates = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    assert candidates[0].condition == "sfp"


def test_non_dry_run_creates_in_app_alerts_only(slice74_db: sessionmaker[Session]) -> None:
    detection = SetupDetection(
        name="trend_pullback",
        detected=True,
        direction="long",
        reason="Uptrend pullback to the fast EMA.",
        metrics={"close": 99.0, "ema_fast": 100.0, "ema_slow": 90.0},
    )
    with slice74_db() as session:
        settings = Settings(**_BASE)
        provider = MockMarketDataProvider()
        market_data = MarketDataService(provider)
        svc = MarketWatcherService(session, settings, market_data=market_data)
        with patch(
            "app.services.market_watcher_scanner.detect_setup_candidates",
            return_value=[
                detect_setup_candidates(
                    symbol="BTCUSDT",
                    timeframe="15m",
                    bars=_flat_bars(),
                    result=_analysis_result(detections=(detection,)),
                )[0]
            ],
        ):
            result = svc.scan(
                organization_id=ORG_ID,
                user_id=USER_ID,
                request=MarketWatcherScanRequest(
                    confirm=SCAN_CONFIRM_PHRASE,
                    create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
                    symbols=["BTCUSDT"],
                    timeframes=["15m"],
                    dry_run=False,
                ),
            )
        session.commit()
        assert result.alerts_created >= 1
        rows = list(session.scalars(select(PaperValidationAlert)).all())
        for row in rows:
            assert row.delivery_channel == AlertDeliveryChannel.IN_APP
            assert row.delivery_status in (
                AlertDeliveryStatus.DISABLED,
                AlertDeliveryStatus.PENDING,
                AlertDeliveryStatus.SKIPPED,
            )
            assert row.metadata_json.get("source") == "market_watcher"
            assert row.metadata_json.get("condition") == "trend_pullback"


def test_dedupe_prevents_repeated_setup_detector_alerts(slice74_db: sessionmaker[Session]) -> None:
    detection = SetupDetection(
        name="liquidity_sweep",
        detected=True,
        direction="long",
        reason="Swept liquidity below prior swing low and closed back above it.",
        metrics={"swept_level": 98.0, "low": 97.5, "atr": 2.0},
    )
    bars = _flat_bars()
    bars[-1] = OHLCVBar(
        open=99.0,
        high=100.0,
        low=97.5,
        close=99.5,
        volume=1000.0,
        timestamp=bars[-1].timestamp,
    )
    stub = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=bars,
        result=_analysis_result(detections=(detection,)),
    )
    with slice74_db() as session:
        settings = Settings(**_BASE)
        provider = MockMarketDataProvider()
        market_data = MarketDataService(provider)
        svc = MarketWatcherService(session, settings, market_data=market_data)
        body = MarketWatcherScanRequest(
            confirm=SCAN_CONFIRM_PHRASE,
            create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            dry_run=False,
        )
        with patch(
            "app.services.market_watcher_scanner.detect_setup_candidates",
            return_value=stub,
        ):
            first = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
            second = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
        assert first.alerts_created == 1
        assert second.alerts_deduped >= 1
        assert second.alerts_created == 0


def test_no_telegram_service_called(slice74_db: sessionmaker[Session]) -> None:
    with slice74_db() as session:
        settings = Settings(**_BASE)
        provider = MockMarketDataProvider()
        market_data = MarketDataService(provider)
        svc = MarketWatcherService(session, settings, market_data=market_data)
        with patch(
            "app.services.telegram_alert_delivery_service.TelegramAlertDeliveryService.deliver_alert"
        ) as deliver:
            svc.scan(
                organization_id=ORG_ID,
                user_id=USER_ID,
                request=MarketWatcherScanRequest(
                    confirm=SCAN_CONFIRM_PHRASE,
                    create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
                    symbols=["BTCUSDT"],
                    timeframes=["15m"],
                    dry_run=False,
                ),
            )
        deliver.assert_not_called()


def test_no_execution_service_called(slice74_db: sessionmaker[Session]) -> None:
    with slice74_db() as session:
        settings = Settings(**_BASE)
        provider = MockMarketDataProvider()
        market_data = MarketDataService(provider)
        svc = MarketWatcherService(session, settings, market_data=market_data)
        with patch(
            "app.services.execution_service.ExecutionService.place_paper_order",
        ) as place_order:
            svc.scan(
                organization_id=ORG_ID,
                user_id=USER_ID,
                request=MarketWatcherScanRequest(
                    confirm=SCAN_CONFIRM_PHRASE,
                    symbols=["BTCUSDT"],
                    timeframes=["15m"],
                    dry_run=True,
                ),
            )
        place_order.assert_not_called()


def test_real_trading_enabled_blocks_scan(slice74_db: sessionmaker[Session]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING=true"):
        _client(
            slice74_db,
            settings_overrides={
                "execution_mode": ExecutionMode.TRADE.value,
                "enable_real_trading": True,
            },
        )


def test_non_paper_execution_blocks_scan(slice74_db: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="execution-capable process"):
        _client(
            slice74_db,
            settings_overrides={"execution_mode": ExecutionMode.READ_ONLY.value},
        )


def test_detect_candidates_includes_setup_detectors_when_fired() -> None:
    detection = SetupDetection(
        name="trend_pullback",
        detected=True,
        direction="long",
        reason="Uptrend pullback to the fast EMA.",
        metrics={"close": 99.0, "ema_fast": 100.0, "ema_slow": 90.0},
    )
    bars = _flat_bars(count=60, price=99.0)
    with patch(
        "app.services.market_watcher_scanner.analyze",
        return_value=_analysis_result(detections=(detection,)),
    ):
        candidates = detect_candidates(symbol="BTCUSDT", timeframe="15m", bars=bars)
    conditions = {c.condition for c in candidates}
    assert "trend_pullback" in conditions


def test_provider_failure_returns_degraded(slice74_db: sessionmaker[Session]) -> None:
    provider = MagicMock()
    provider.name = "mock"
    provider.get_ohlcv.side_effect = RuntimeError("provider unavailable")
    market_data = MarketDataService(provider)
    with slice74_db() as session:
        settings = Settings(**_BASE)
        svc = MarketWatcherService(session, settings, market_data=market_data)
        result = svc.scan(
            organization_id=ORG_ID,
            user_id=USER_ID,
            request=MarketWatcherScanRequest(
                confirm=SCAN_CONFIRM_PHRASE,
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                dry_run=True,
            ),
        )
        assert result.status == "degraded"
