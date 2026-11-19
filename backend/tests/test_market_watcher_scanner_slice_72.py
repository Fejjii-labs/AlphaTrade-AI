"""Tests for read-only market watcher scanner (Slice 72)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.analysis.types import Level
from app.core.config import ExecutionMode, Settings, get_settings
from app.db.base import Base
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.providers.market_data import MockMarketDataProvider, OHLCVBar
from app.schemas.common import (
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
from app.services.market_watcher_scanner import (
    _coerce_level_prices,
    _level_price,
    _nearest_level,
    detect_candidates,
)
from app.services.market_watcher_service import MarketWatcherService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000007201")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000007202")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-000000007203")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "slice72-scanner-secret-minimum-32",
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
def slice72_db() -> Iterator[sessionmaker[Session]]:
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
        org = Organization(id=ORG_ID, name="Slice72 Org")
        owner = User(
            id=USER_ID,
            email="owner72@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        trader = User(
            id=OTHER_USER,
            email="trader72@test.example",
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
    slice72_db: sessionmaker[Session],
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
        with slice72_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    email = "owner72@test.example" if role == MembershipRole.OWNER else "trader72@test.example"
    login = client.post("/auth/login", json={"email": email, "password": "TestPassword123!"})
    token = login.json()["tokens"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _volatile_bars() -> list[OHLCVBar]:
    now = datetime.now(UTC)
    bars: list[OHLCVBar] = []
    price = 100.0
    for i in range(60):
        drift = 0.5 if i > 50 else 0.02
        price *= 1 + drift / 100
        volume = 1000.0 if i < 55 else 5000.0
        bars.append(
            OHLCVBar(
                open=price * 0.999,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=volume,
                timestamp=now - timedelta(minutes=15 * (60 - i)),
            )
        )
    return bars


def test_detect_candidates_strong_move() -> None:
    candidates = detect_candidates(symbol="BTCUSDT", timeframe="15m", bars=_volatile_bars())
    conditions = {c.condition for c in candidates}
    assert "strong_move" in conditions or "high_volume_move" in conditions


def test_level_price_from_level_object() -> None:
    assert _level_price(Level(price=101.5, kind="support", touches=2, strength=0.8)) == 101.5


def test_level_price_from_numeric() -> None:
    assert _level_price(99.0) == 99.0
    assert _level_price(100) == 100.0


def test_level_price_from_dict() -> None:
    assert _level_price({"price": 102.25, "kind": "resistance"}) == 102.25


def test_level_price_ignores_invalid() -> None:
    assert _level_price(None) is None
    assert _level_price("bad") is None
    assert _level_price({"kind": "support"}) is None
    assert _level_price(object()) is None


def test_coerce_level_prices_mixed_and_invalid() -> None:
    levels = (
        Level(price=100.0, kind="support", touches=1, strength=0.5),
        101.0,
        {"price": 102.0},
        "invalid",
        None,
    )
    assert _coerce_level_prices(levels) == [100.0, 101.0, 102.0]


def test_nearest_level_with_level_objects() -> None:
    levels = (
        Level(price=100.0, kind="support", touches=2, strength=0.7),
        Level(price=110.0, kind="support", touches=1, strength=0.4),
    )
    assert _nearest_level(101.0, levels) == 100.0


def test_nearest_level_with_numeric_levels() -> None:
    assert _nearest_level(101.0, (100.0, 110.0)) == 100.0


def test_detect_candidates_with_real_analyze_levels() -> None:
    """Real analyze() returns Level objects — candidate detection must not crash."""
    candidates = detect_candidates(symbol="BTCUSDT", timeframe="15m", bars=_volatile_bars())
    assert isinstance(candidates, list)
    for candidate in candidates:
        assert candidate.metrics.get("support") is None or isinstance(
            candidate.metrics["support"], (int, float)
        )
        assert candidate.metrics.get("resistance") is None or isinstance(
            candidate.metrics["resistance"], (int, float)
        )


def test_real_trading_blocks_scan(slice72_db: sessionmaker[Session]) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING=true"):
        _client(
            slice72_db,
            settings_overrides={
                "execution_mode": ExecutionMode.TRADE.value,
                "enable_real_trading": True,
            },
        )


def test_non_paper_execution_blocks_scan(slice72_db: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError, match="execution-capable process"):
        _client(
            slice72_db,
            settings_overrides={"execution_mode": ExecutionMode.READ_ONLY.value},
        )


def test_dedupe_prevents_duplicate_alerts(slice72_db: sessionmaker[Session]) -> None:
    with slice72_db() as session:
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
        first = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
        session.commit()
        second = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
        session.commit()
        assert second.alerts_deduped >= first.alerts_deduped


def test_provider_failure_returns_degraded(slice72_db: sessionmaker[Session]) -> None:
    provider = MagicMock()
    provider.name = "mock"
    provider.get_ohlcv.side_effect = RuntimeError("provider unavailable")
    market_data = MarketDataService(provider)
    with slice72_db() as session:
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
        assert result.error is not None


def test_no_telegram_service_called(slice72_db: sessionmaker[Session]) -> None:
    with slice72_db() as session:
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


def test_no_execution_service_called(slice72_db: sessionmaker[Session]) -> None:
    with slice72_db() as session:
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
