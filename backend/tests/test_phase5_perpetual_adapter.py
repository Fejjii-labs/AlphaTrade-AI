"""Phase 5 perpetual adapter safety, replay, freshness, and health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.core.config import Settings
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.enums import MarketType
from app.market_contracts.errors import (
    FormingCandleError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    UnapprovedEvidenceHostError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import (
    FIRST_SLICE_MIN_FINAL_4H,
    FIRST_SLICE_MIN_FINAL_15M,
    first_slice_identity,
)
from app.market_contracts.freshness import evaluate_freshness, first_slice_freshness_policy
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import binance_usdm_btcusdt, interval_timedelta
from app.market_contracts.replay_fixtures import canonical_first_slice_fixture
from app.providers.base import ProviderHealth
from app.providers.registry import build_default_registry
from app.schemas.common import Timeframe
from tests.support.phase5_market import (
    CONNECTION,
    EVALUATED_AT,
    RECORDED_FIXTURE_GRACE,
    TRIGGER_OPEN,
    eth_instrument,
    identity,
    spot_identity,
    trade,
)


def _kline_row(
    open_time: datetime,
    *,
    timeframe: Timeframe = Timeframe.M15,
    index: int = 0,
) -> list[object]:
    open_ = Decimal("100000") + Decimal(index)
    close = open_ + Decimal("2")
    high = close + Decimal("1")
    low = open_ - Decimal("1")
    end = open_time + interval_timedelta(timeframe)
    return [
        int(open_time.timestamp() * 1000),
        str(open_),
        str(high),
        str(low),
        str(close),
        "10",
        int(end.timestamp() * 1000) - 1,
        "1000000",
        4,
        "0",
        "0",
        "0",
    ]


def _usdm_handler(
    *,
    klines: list[list[object]] | None = None,
    trades: list[dict[str, object]] | None = None,
    ping_status: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"msg": "method not allowed"})
        path = request.url.path
        if path.endswith("/fapi/v1/ping"):
            return httpx.Response(ping_status, json={})
        if path.endswith("/fapi/v1/klines"):
            return httpx.Response(200, json=klines or [])
        if path.endswith("/fapi/v1/aggTrades"):
            return httpx.Response(200, json=trades or [])
        return httpx.Response(404, json={"msg": "missing"})

    return httpx.MockTransport(handler)


def test_replay_determinism() -> None:
    left = ReplayPerpetualSource()
    right = ReplayPerpetualSource()
    series_a = left.fetch_closed_ohlcv(
        identity=identity(),
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=FIRST_SLICE_MIN_FINAL_15M,
        evaluated_at=EVALUATED_AT,
    )
    series_b = right.fetch_closed_ohlcv(
        identity=identity(Timeframe.M15),
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=FIRST_SLICE_MIN_FINAL_15M,
        evaluated_at=EVALUATED_AT,
    )
    assert len(series_a.bars) == FIRST_SLICE_MIN_FINAL_15M
    assert series_a.content_hash == series_b.content_hash
    context = left.fetch_closed_ohlcv(
        identity=identity(Timeframe.H4),
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.H4,
        min_final_bars=FIRST_SLICE_MIN_FINAL_4H,
        evaluated_at=EVALUATED_AT,
    )
    assert len(context.bars) == FIRST_SLICE_MIN_FINAL_4H
    trades_a = left.fetch_ordered_trades(
        identity=identity(),
        instrument=binance_usdm_btcusdt(),
        start=series_a.bars[0].interval_start,
        end=series_a.bars[-1].interval_end,
        source_connection_id=CONNECTION,
        receive_at=EVALUATED_AT,
    )
    trades_b = right.fetch_ordered_trades(
        identity=identity(),
        instrument=binance_usdm_btcusdt(),
        start=series_a.bars[0].interval_start,
        end=series_a.bars[-1].interval_end,
        source_connection_id=CONNECTION,
        receive_at=EVALUATED_AT,
    )
    assert trades_a.content_hash == trades_b.content_hash
    assert trades_a.coverage.content_hash == trades_b.coverage.content_hash
    assert trades_a.coverage.requested_start == series_a.bars[0].interval_start
    assert trades_a.coverage.requested_end == series_a.bars[-1].interval_end
    assert trades_a.trades
    assert trades_a.trades[0].content_hash == trades_b.trades[0].content_hash


def test_replay_rejects_wrong_instrument() -> None:
    source = ReplayPerpetualSource()
    with pytest.raises(WrongInstrumentError):
        source.fetch_closed_ohlcv(
            identity=identity(),
            instrument=eth_instrument(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_replay_boundary_rejects_trade_from_wrong_instrument() -> None:
    wrong_trade = trade(
        sequence=1,
        price="100",
        quantity="1",
        buyer_is_maker=False,
        event_time=TRIGGER_OPEN + timedelta(seconds=1),
        instrument=eth_instrument(),
    )
    source = ReplayPerpetualSource(trades=[wrong_trade])
    with pytest.raises(WrongInstrumentError):
        source.fetch_ordered_trades(
            identity=identity(),
            instrument=binance_usdm_btcusdt(),
            start=TRIGGER_OPEN,
            end=TRIGGER_OPEN + timedelta(minutes=15),
            source_connection_id=CONNECTION,
            receive_at=EVALUATED_AT,
        )


def test_replay_boundary_rejects_trade_from_wrong_market() -> None:
    perpetual_trade = trade(
        sequence=1,
        price="100",
        quantity="1",
        buyer_is_maker=False,
        event_time=TRIGGER_OPEN + timedelta(seconds=1),
    )
    wrong_market_trade = with_content_hash(
        perpetual_trade.model_copy(update={"market_type": MarketType.SPOT})
    )
    source = ReplayPerpetualSource(trades=[wrong_market_trade])
    with pytest.raises(WrongMarketError):
        source.fetch_ordered_trades(
            identity=identity(),
            instrument=binance_usdm_btcusdt(),
            start=TRIGGER_OPEN,
            end=TRIGGER_OPEN + timedelta(minutes=15),
            source_connection_id=CONNECTION,
            receive_at=EVALUATED_AT,
        )


def test_replay_boundary_rejects_live_provider_source_identity() -> None:
    source = ReplayPerpetualSource()
    with pytest.raises(WrongSourceError):
        source.fetch_ordered_trades(
            identity=first_slice_identity(
                timeframe=Timeframe.M15,
                replay=False,
                is_live=True,
            ),
            instrument=binance_usdm_btcusdt(),
            start=TRIGGER_OPEN,
            end=TRIGGER_OPEN + timedelta(minutes=15),
            source_connection_id=CONNECTION,
            receive_at=EVALUATED_AT,
        )


def test_freshness_and_stale_evidence() -> None:
    policy = first_slice_freshness_policy()
    source_time = EVALUATED_AT - timedelta(seconds=4)
    fresh = evaluate_freshness(
        source_time=source_time,
        evaluated_at=EVALUATED_AT,
        policy=policy,
    )
    assert fresh.state.value in {"fresh", "aging"}
    with pytest.raises(StaleEvidenceError):
        evaluate_freshness(
            source_time=EVALUATED_AT - timedelta(seconds=11),
            evaluated_at=EVALUATED_AT,
            policy=policy,
        )
    stale = evaluate_freshness(
        source_time=EVALUATED_AT - timedelta(seconds=11),
        evaluated_at=EVALUATED_AT,
        policy=policy,
        require_fresh=False,
    )
    assert stale.state.value == "stale"


def test_no_spot_fallback_on_spot_host() -> None:
    with pytest.raises(SpotFallbackRejectedError):
        ReadOnlyHttpGetClient(base_url="https://api.binance.com", timeout_seconds=5.0)


def test_no_spot_fallback_on_spot_path() -> None:
    client = ReadOnlyHttpGetClient(base_url="https://fapi.binance.com", timeout_seconds=5.0)
    with pytest.raises(SpotFallbackRejectedError):
        client.request_json("GET", "/api/v3/klines")
    client.close()


def test_unapproved_host_and_plain_http_rejected() -> None:
    with pytest.raises(UnapprovedEvidenceHostError):
        ReadOnlyHttpGetClient(base_url="https://not-binance.example", timeout_seconds=5.0)
    with pytest.raises(UnapprovedEvidenceHostError, match="HTTPS"):
        ReadOnlyHttpGetClient(base_url="http://fapi.binance.com", timeout_seconds=5.0)


def test_regional_provider_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"msg": "unavailable for legal reasons"})

    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
    )
    status = source.status()
    assert status.health is ProviderHealth.UNAVAILABLE
    assert status.using_fallback is False
    assert "spot fallback" in (status.detail or "").lower()
    with pytest.raises(RegionalProviderFailureError):
        source.fetch_closed_ohlcv(
            identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
            instrument=binance_usdm_btcusdt(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_live_adapter_maps_closed_klines() -> None:
    delta = interval_timedelta(Timeframe.M15)
    first_open = TRIGGER_OPEN - (delta * 99)
    rows = [_kline_row(first_open + (delta * index), index=index) for index in range(100)]
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_usdm_handler(klines=rows),
        settlement_grace=RECORDED_FIXTURE_GRACE,
    )
    live_identity = first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True)
    series = source.fetch_closed_ohlcv(
        identity=live_identity,
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=100,
        evaluated_at=EVALUATED_AT,
    )
    assert len(series.bars) == 100
    assert series.bars[-1].interval_start == TRIGGER_OPEN


def test_live_adapter_rejects_forming_last_candle_as_confirmation() -> None:
    forming_open = datetime(2026, 1, 15, 16, 15, tzinfo=UTC)
    rows = [_kline_row(forming_open, index=0)]
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_usdm_handler(klines=rows),
    )
    with pytest.raises(FormingCandleError):
        source.fetch_closed_ohlcv(
            identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
            instrument=binance_usdm_btcusdt(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_live_adapter_rejects_spot_identity() -> None:
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_usdm_handler(klines=[]),
    )
    with pytest.raises(WrongMarketError):
        source.fetch_closed_ohlcv(
            identity=spot_identity(),
            instrument=spot_identity().instrument,
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_source_health_registered_as_replay_by_default() -> None:
    settings = Settings(
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        provider_mode="mock",
        market_data_provider="mock",
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
        perpetual_evidence_source="replay",
    )
    registry = build_default_registry(settings)
    names = {provider.name for provider in registry.all()}
    assert "binance-usdm-perpetual-replay" in names
    status = next(
        item for item in registry.statuses() if item.name == "binance-usdm-perpetual-replay"
    )
    assert status.health is ProviderHealth.HEALTHY
    assert status.is_mock is True
    assert status.using_fallback is False
    source = resolve_perpetual_evidence_source(settings)
    assert isinstance(source, ReplayPerpetualSource)


def test_live_trading_remains_disabled() -> None:
    settings = Settings(
        environment="local",
        provider_mode="mock",
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.execution_mode.value == "paper"


def test_canonical_fixture_counts() -> None:
    fixture = canonical_first_slice_fixture()
    assert len(fixture["bars_15m"]) == 100
    assert len(fixture["bars_4h"]) == 30
    assert all(bar.finality.value == "final" for bar in fixture["bars_15m"])
    assert all(bar.finality.value == "final" for bar in fixture["bars_4h"])
    assert fixture["trades"]
    assert fixture["evaluated_at"] == EVALUATED_AT
