"""Slice 19 — live market data provider and workflow tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import Settings
from app.providers.market_data import MockMarketDataProvider, OHLCVBar
from app.providers.registry import build_default_registry
from app.schemas.common import Timeframe
from app.services.agent_service import AgentInvokeContext, AgentService
from app.services.indicator_service import IndicatorService
from app.services.market_data_service import MarketDataService
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.strategies.base import StrategyEvaluationInput
from app.strategies.confidence import adjust_confidence_for_data_quality
from app.strategies.registry import build_default_registry as build_strategy_registry
from app.tools.registry import build_default_registry as build_tools


def _market_data_service() -> MarketDataService:
    provider = MockMarketDataProvider()
    return MarketDataService(
        provider,
        cache=None,
        indicator_service=IndicatorService(),
        strategy_service=StrategyService(registry=build_strategy_registry()),
    )


def _synthetic_bars(count: int = 60) -> list[OHLCVBar]:
    bars: list[OHLCVBar] = []
    price = Decimal("50000")
    for i in range(count):
        delta = Decimal(str(1 + i * 0.002))
        close = price * delta
        bars.append(
            OHLCVBar(
                open=close * Decimal("0.998"),
                high=close * Decimal("1.002"),
                low=close * Decimal("0.996"),
                close=close,
                volume=Decimal(str(1_000_000 + i * 5000)),
                timestamp=datetime.now(UTC),
            )
        )
    return bars


def test_mock_provider_fallback_when_unavailable() -> None:
    provider = MockMarketDataProvider()
    ticker = provider.get_ticker("BTCUSDT")
    assert ticker.envelope.fallback_used is True
    assert ticker.envelope.is_live is False


def test_live_provider_disabled_without_config() -> None:
    from app.providers.factory import resolve_market_data_provider

    provider = resolve_market_data_provider(
        Settings(provider_mode="mock", market_data_provider="mock", log_json=False)
    )
    assert provider.name == "mock-market-data"


def test_stale_data_marking() -> None:
    from app.providers.market_data import _is_stale

    old = datetime.now(UTC).replace(year=2020)
    is_stale, reason = _is_stale(old, max_age_seconds=60, reason="too old")
    assert is_stale is True
    assert reason == "too old"


def test_indicator_calculations_from_synthetic_ohlcv() -> None:
    service = IndicatorService()
    result = service.calculate(symbol="BTCUSDT", timeframe=Timeframe.H1, bars=_synthetic_bars())
    assert result.rsi is not None
    assert result.ema_fast is not None
    assert result.ema_slow is not None
    assert result.macd is not None
    assert result.atr is not None
    assert result.vwap is not None
    assert result.volume_trend is not None


def test_strategy_confidence_lowered_with_mock_data() -> None:
    live = StrategyEvaluationInput(
        symbol="BTCUSDT",
        timeframe=Timeframe.H4,
        close=Decimal("60000"),
        volume=Decimal("1000000"),
        data_is_live=True,
        data_is_stale=False,
        data_fallback_used=False,
    )
    mock = replace(live, data_is_live=False, data_fallback_used=True)
    mock_conf = adjust_confidence_for_data_quality(0.72, mock)
    live_conf = adjust_confidence_for_data_quality(0.72, live)
    assert mock_conf < live_conf


def test_agent_response_labels_mock_data() -> None:
    settings = Settings(log_json=False, provider_mode="mock", market_data_provider="mock")
    strategy_service = StrategyService(registry=build_strategy_registry())
    from app.agents.runtime import AgentRuntime
    from app.providers.factory import resolve_market_data_provider
    from app.services.market_cache import MarketDataCache

    market_data_service = MarketDataService(
        resolve_market_data_provider(settings),
        cache=MarketDataCache(settings),
        indicator_service=IndicatorService(),
        strategy_service=strategy_service,
    )
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=strategy_service,
        tool_registry=build_tools(settings, market_data_service=market_data_service),
        market_data_service=market_data_service,
    )
    response = AgentService(runtime=runtime).run(
        "analyze btc setup",
        AgentInvokeContext(
            request_id="market-quality-test",
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000005"),
            organization_id=uuid.UUID("00000000-0000-0000-0000-000000000006"),
        ),
        symbol="BTCUSDT",
        timeframe="4h",
    )
    assert response.analysis is not None
    assert response.analysis.market_data_quality == "mock"
    assert "mock" in response.reply.lower()


def test_ticker_service_schema() -> None:
    ticker = _market_data_service().get_ticker("BTCUSDT")
    assert ticker.meta.symbol == "BTCUSDT"
    assert ticker.meta.is_live is False
    assert ticker.meta.fallback_used is True


def test_registry_includes_market_provider() -> None:
    registry = build_default_registry(Settings(provider_mode="mock", log_json=False))
    market = next(p for p in registry.statuses() if p.kind.value == "market_data")
    assert market.name in {"mock-market-data", "binance-public"}


def test_no_real_exchange_execution_path() -> None:
    registry = build_default_registry(Settings(provider_mode="mock", log_json=False))
    exchange = next(p for p in registry.statuses() if p.kind.value == "exchange")
    assert exchange.is_mock is True
    assert "real trading disabled" in (exchange.detail or "").lower()
