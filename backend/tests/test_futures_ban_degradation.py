"""Futures rate limit / IP ban handling outside paper fills.

- API: RateLimitedError / UpstreamBanError map to 503 + Retry-After (not 500).
- Agent chat: graph steps degrade (funding/OI unavailable) and state the gap.
- Non-ban errors still fall back to mock funding, and the snapshot flags it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.market_contracts.errors import RateLimitedError, UpstreamBanError
from app.providers.market_data import (
    BinancePublicMarketDataProvider,
    FundingRateData,
    MockMarketDataProvider,
    OHLCVData,
    TickerData,
)
from app.schemas.common import Timeframe
from app.schemas.market import MarketAnalyzeRequest
from app.services.agent_service import AgentInvokeContext, AgentService
from app.services.indicator_service import IndicatorService
from app.services.market_data_service import MarketDataService
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.strategies.registry import build_default_registry as build_strategy_registry
from app.tools.registry import build_default_registry as build_tools


def _ban(*_a: object, **_k: object) -> FundingRateData:
    raise UpstreamBanError("binance 418 ban", retry_after_seconds=120.4)


class _LiveSpotProvider(MockMarketDataProvider):
    """Mock spot data relabelled live so fallback flags come only from funding."""

    def get_ticker(self, symbol: str, *, exchange: str = "binance") -> TickerData:
        t = super().get_ticker(symbol, exchange=exchange)
        return replace(t, envelope=replace(t.envelope, is_live=True, fallback_used=False))

    def get_ohlcv(self, *a: object, **k: object) -> OHLCVData:  # type: ignore[override]
        o = super().get_ohlcv(*a, **k)  # type: ignore[arg-type]
        return replace(o, envelope=replace(o.envelope, is_live=True, fallback_used=False))


def _service(provider: MockMarketDataProvider) -> MarketDataService:
    return MarketDataService(
        provider,
        cache=None,
        indicator_service=IndicatorService(),
        strategy_service=StrategyService(registry=build_strategy_registry()),
    )


# --- API mapping -----------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    settings = Settings(
        log_json=False,
        provider_mode="mock",
        market_data_provider="mock",
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="futures-ban-test-secret-32-bytes-minimum",
    )
    monkeypatch.setattr(MockMarketDataProvider, "get_funding_rate", _ban)
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    reg = client.post(
        "/auth/register",
        json={
            "email": f"ban-{uuid.uuid4()}@example.com",
            "password": "securepassword123",
            "organization_name": "Ban Test Org",
        },
    )
    assert reg.status_code in {200, 201}, reg.text
    return {"Authorization": f"Bearer {reg.json()['tokens']['access_token']}"}


def test_snapshot_endpoint_returns_503_with_retry_after_during_ban(
    client: TestClient, headers: dict[str, str]
) -> None:
    r = client.get("/market/snapshots?symbol=BTCUSDT", headers=headers)
    assert r.status_code == 503, r.text
    assert r.headers["Retry-After"] == "121"
    assert r.json()["error"]["code"] == "upstream_banned"


def test_analyze_endpoint_returns_503_during_ban(
    client: TestClient, headers: dict[str, str]
) -> None:
    r = client.post(
        "/market/analyze",
        json={"symbol": "BTCUSDT", "timeframe": "4h"},
        headers=headers,
    )
    assert r.status_code == 503, r.text
    assert int(r.headers["Retry-After"]) >= 1


@pytest.mark.parametrize(
    ("exc", "code", "retry_after"),
    [
        (RateLimitedError("429", retry_after_seconds=None), "upstream_rate_limited", "60"),
        (RateLimitedError("429", retry_after_seconds=float("inf")), "upstream_rate_limited", "60"),
        (UpstreamBanError("418", retry_after_seconds=1e12), "upstream_banned", "900"),
        (UpstreamBanError("418", retry_after_seconds=0.2), "upstream_banned", "1"),
    ],
)
def test_rate_limit_handler_maps_to_503(exc: Exception, code: str, retry_after: str) -> None:
    # Covers /pretrade and any other route: the mapping is a global handler.
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 503
    assert r.headers["Retry-After"] == retry_after
    assert r.json()["error"]["code"] == code


# --- Agent graph degradation -------------------------------------------------


def test_snapshot_degrades_when_requested_and_raises_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MockMarketDataProvider, "get_funding_rate", _ban)
    svc = _service(MockMarketDataProvider())
    with pytest.raises(UpstreamBanError):
        svc.get_snapshot("BTCUSDT", Timeframe.H4)
    snap = svc.get_snapshot("BTCUSDT", Timeframe.H4, degrade_on_rate_limit=True)
    assert snap.funding_rate is None
    assert snap.funding_rate_available is False
    assert snap.open_interest_available is False
    assert snap.unavailable_reason == "futures_upstream_ban"
    assert snap.indicators is not None
    assert snap.indicators.funding_rate is None


def test_agent_chat_survives_futures_ban_and_states_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MockMarketDataProvider, "get_funding_rate", _ban)
    settings = Settings(log_json=False, provider_mode="mock", market_data_provider="mock")
    strategy_service = StrategyService(registry=build_strategy_registry())
    from app.agents.runtime import AgentRuntime

    svc = _service(MockMarketDataProvider())
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=strategy_service,
        tool_registry=build_tools(settings, market_data_service=svc),
        market_data_service=svc,
    )
    response = AgentService(runtime=runtime).run(
        "analyze btc setup",
        AgentInvokeContext(
            request_id="futures-ban-chat",
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000005"),
            organization_id=uuid.UUID("00000000-0000-0000-0000-000000000006"),
        ),
        symbol="BTCUSDT",
        timeframe="4h",
    )
    assert response.reply
    assert "data gap" in response.reply.lower()
    assert "funding rate" in response.reply.lower()


# --- Non-ban fallback --------------------------------------------------------


def _binance_500() -> BinancePublicMarketDataProvider:
    transport = httpx.MockTransport(lambda _req: httpx.Response(500, json={}))
    gate = BinanceRestGate(rng=lambda: 0.0, boot_ramp_seconds=0.0)
    return BinancePublicMarketDataProvider(rest_gate=gate, transport=transport)


def test_non_ban_error_still_falls_back_to_flagged_mock_funding() -> None:
    funding = _binance_500().get_funding_rate("BTCUSDT")
    assert funding is not None
    assert funding.envelope.fallback_used is True
    assert funding.funding_rate == Decimal("0.0001")


def test_snapshot_flags_mock_funding_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    binance = _binance_500()
    monkeypatch.setattr(
        _LiveSpotProvider,
        "get_funding_rate",
        lambda self, symbol, exchange="binance": binance.get_funding_rate(symbol),
    )
    svc = _service(_LiveSpotProvider())
    snap = svc.get_snapshot("BTCUSDT", Timeframe.H4)
    assert snap.funding_rate_available is True
    assert snap.meta.fallback_used is True

    analysis = svc.analyze(MarketAnalyzeRequest(symbol="BTCUSDT", timeframe=Timeframe.H4))
    assert analysis.snapshot.meta.fallback_used is True
    assert analysis.confidence_penalty_applied is True


def test_snapshot_not_flagged_when_funding_live(monkeypatch: pytest.MonkeyPatch) -> None:
    def _live(self: object, symbol: str, exchange: str = "binance") -> FundingRateData:
        f = MockMarketDataProvider().get_funding_rate(symbol)
        assert f is not None
        return replace(f, envelope=replace(f.envelope, fallback_used=False, is_live=True))

    monkeypatch.setattr(_LiveSpotProvider, "get_funding_rate", _live)
    snap = _service(_LiveSpotProvider()).get_snapshot("BTCUSDT", Timeframe.H4)
    assert snap.meta.fallback_used is False
