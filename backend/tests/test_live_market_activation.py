"""Controlled staging activation for read-only Binance USD-M evidence."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import ExecutionMode, Settings
from app.evidence_pipeline.types import CurrentPricePresentation
from app.main import create_app
from app.market_activation.profile import (
    activation_state,
    live_market_activation_violations,
)
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.enums import SourceFamily
from app.market_contracts.errors import NetworkMutationForbiddenError, WrongInstrumentError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.identity import (
    binance_usdm_btcusdt,
    binance_usdm_perpetual,
    interval_timedelta,
)
from app.market_monitor.factory import build_perpetual_market_monitor
from app.market_monitor.service import PerpetualMarketMonitorService, status_from_snapshot
from app.market_monitor.types import MarketAvailability, MarketMode, MonitorReason
from app.schemas.common import Timeframe
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.support.phase5_market import trade

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)

_STAGING: dict[str, object] = {
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
    "exchange_mode": "paper_internal",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
    "perpetual_evidence_source": "binance_usdm",
    "market_data_futures_base_url": "https://fapi.binance.com",
}


def _local(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "provider_mode": "mock",
        "rate_limit_use_redis": False,
        "market_data_cache_use_redis": False,
        "access_token_denylist_use_redis": False,
        "perpetual_evidence_source": "binance_usdm",
        "market_data_futures_base_url": "https://fapi.binance.com",
        "perpetual_monitor_backoff_initial_seconds": 0.05,
        "perpetual_monitor_backoff_max_seconds": 2.0,
        "perpetual_monitor_poll_seconds": 0.25,
    }
    base.update(overrides)
    return Settings(**base)


def _kline(open_time: datetime, timeframe: Timeframe) -> list[object]:
    end = open_time + interval_timedelta(timeframe)
    return [
        int(open_time.timestamp() * 1000),
        "100000",
        "100010",
        "99990",
        "100002",
        "10",
        int(end.timestamp() * 1000) - 1,
        "1000000",
        4,
        "0",
        "0",
        "0",
    ]


class _PublicUsdM:
    """Scripted public REST. Records methods and never accepts credentials."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.trade_id = 1
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "fapi.binance.com"
        lowered = {key.lower() for key in request.headers}
        assert "x-mbx-apikey" not in lowered
        assert "authorization" not in lowered
        path = request.url.path
        if path == "/fapi/v1/ping":
            return httpx.Response(200, json={})
        if path == "/fapi/v1/klines":
            assert request.url.params["symbol"] == "BTCUSDT"
            return httpx.Response(200, json=self._klines(request.url.params["interval"]))
        if path == "/fapi/v1/aggTrades":
            assert request.url.params["symbol"] == "BTCUSDT"
            if self.mode == "down":
                raise httpx.ConnectError("usd-m disconnected")
            if self.mode == "limit":
                return httpx.Response(429, headers={"Retry-After": "2"}, json={"code": -1003})
            if self.mode == "gap":
                return httpx.Response(200, json=[self._trade(1, request), self._trade(3, request)])
            return httpx.Response(200, json=[self._trade(self.trade_id, request)])
        return httpx.Response(404, json={"msg": "not allowlisted"})

    def _klines(self, interval: str) -> list[list[object]]:
        if interval == "15m":
            step = interval_timedelta(Timeframe.M15)
            closed = [
                _kline(NOW - step * 3, Timeframe.M15),
                _kline(NOW - step * 2, Timeframe.M15),
            ]
            forming = _kline(NOW, Timeframe.M15)
            return [*closed, forming]
        step = interval_timedelta(Timeframe.H4)
        return [
            _kline(NOW - step * 3, Timeframe.H4),
            _kline(NOW - step * 2, Timeframe.H4),
        ]

    def _trade(self, agg_id: int, request: httpx.Request) -> dict[str, object]:
        start = int(request.url.params["startTime"])
        end = int(request.url.params["endTime"])
        event_ms = end if end >= start else start
        return {"a": agg_id, "p": "101234.7", "q": "0.02", "m": False, "T": event_ms}


def _live_monitor(exchange: _PublicUsdM, settings: Settings | None = None):
    resolved = settings or _local()
    source = resolve_perpetual_evidence_source(
        resolved,
        transport=httpx.MockTransport(exchange.handler),
    )
    assert isinstance(source, BinanceUsdmPerpetualSource)
    return build_perpetual_market_monitor(resolved, source=source), source


def test_staging_activation_profile_is_active() -> None:
    settings = Settings(**_STAGING)
    assert settings.perpetual_evidence_source == "binance_usdm"
    assert activation_state(settings) == "active"
    assert live_market_activation_violations(settings) == []
    assert settings.real_trading_enabled is False
    assert settings.enable_real_trading is False
    assert first_slice_freshness_policy().trade_max_age_seconds == 10


def test_source_aliases_canonicalize_without_spot() -> None:
    assert _local(perpetual_evidence_source="usdm").perpetual_evidence_source == "binance_usdm"
    assert _local(perpetual_evidence_source="binance-usdm").perpetual_evidence_source == (
        "binance_usdm"
    )
    replay = _local(perpetual_evidence_source="fixture")
    assert replay.perpetual_evidence_source == "replay"
    assert activation_state(replay) == "inactive"
    with pytest.raises(ValidationError, match="spot fallback"):
        _local(perpetual_evidence_source="spot")


def test_spot_coinm_and_insecure_hosts_fail_closed() -> None:
    for url in (
        "https://api.binance.com",
        "https://dapi.binance.com",
        "http://fapi.binance.com",
        "https://user:secret@fapi.binance.com",
        "https://fapi.binance.com/fapi/v1/klines",
    ):
        with pytest.raises(ValidationError, match="market_data_futures_base_url"):
            _local(market_data_futures_base_url=url)


def test_market_credentials_and_exchange_credentials_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "not-a-real-key")
    with pytest.raises(ValidationError, match="BINANCE_API_KEY") as exc:
        _local()
    assert "not-a-real-key" not in str(exc.value)
    monkeypatch.delenv("BINANCE_API_KEY")
    stored = _local(
        blofin_api_key="demo-key",
        blofin_api_secret="demo-secret",
        blofin_api_passphrase="demo-pass",
    )
    assert stored.execution_mode is ExecutionMode.PAPER
    assert stored.enable_real_trading is False
    assert "demo-secret" not in repr(stored)
    with pytest.raises(ValidationError, match="paper_internal") as demo:
        _local(
            exchange_mode="paper_exchange_demo",
            blofin_demo_enabled=True,
            blofin_api_key="demo-key",
            blofin_api_secret="demo-secret",
            blofin_api_passphrase="demo-pass",
            blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        )
    assert "demo-secret" not in str(demo.value)


def test_staging_paper_watcher_arm_may_select_live_evidence() -> None:
    settings = Settings(
        **{
            **_STAGING,
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
        }
    )
    assert live_market_activation_violations(settings) == []
    assert activation_state(settings) == "active"
    assert settings.telegram_alerts_enabled is False


def test_staging_controlled_package_selects_live_evidence() -> None:
    settings = Settings(
        **{
            **_STAGING,
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
            "telegram_interaction_enabled": True,
            "telegram_paper_activation_armed": True,
            "telegram_inbound_mode": "polling",
            "telegram_bot_id": "bot-100",
            "telegram_chat_id": "tg-chat-1",
            "telegram_bot_token": "123456789:AAHtestTokenValueForStagingPackage",
            "telegram_network_permitted": True,
        }
    )
    assert live_market_activation_violations(settings) == []
    assert activation_state(settings) == "active"
    assert isinstance(resolve_perpetual_evidence_source(settings), BinanceUsdmPerpetualSource)


def test_live_source_rejects_watcher_telegram_and_real_trading() -> None:
    with pytest.raises(ValidationError, match="watcher_orchestration_enabled"):
        _local(watcher_orchestration_enabled=True)
    with pytest.raises(ValidationError, match="telegram_interaction_enabled"):
        _local(telegram_interaction_enabled=True)
    settings = _local()
    settings.enable_real_trading = True
    settings.execution_mode = ExecutionMode.TRADE
    violations = live_market_activation_violations(settings)
    assert any("real trading" in item for item in violations)
    assert any("execution_mode=paper" in item for item in violations)
    assert activation_state(settings) == "refused"


def test_production_stays_on_replay() -> None:
    with pytest.raises(ValidationError, match="staging-only"):
        Settings(**{**_STAGING, "environment": "production"})
    production = Settings(
        **{**_STAGING, "environment": "production", "perpetual_evidence_source": "replay"}
    )
    assert activation_state(production) == "inactive"
    assert production.perpetual_evidence_source == "replay"


def test_rollback_restores_replay_and_separates_it_from_live() -> None:
    live = _local()
    assert activation_state(live) == "active"
    rolled = _local(perpetual_evidence_source="replay")
    assert activation_state(rolled) == "inactive"
    assert isinstance(resolve_perpetual_evidence_source(rolled), ReplayPerpetualSource)
    monitor = build_perpetual_market_monitor(rolled)
    snapshot = monitor.tick("BTCUSDT")
    assert snapshot.mode is MarketMode.REPLAY
    assert snapshot.availability is MarketAvailability.REPLAY
    assert snapshot.source_family is SourceFamily.REPLAY_FIXTURE
    assert snapshot.current_price is not None
    assert snapshot.current_price.presentation is CurrentPricePresentation.REPLAY_FIXTURE
    assert snapshot.current_price.usable_as_current_market_price is False
    assert snapshot.watcher_activated is False
    assert snapshot.live_executable is False
    assert snapshot.fallback_used is False
    body = PerpetualMarketMonitorService(monitor, settings=rolled).read(symbol="BTCUSDT")
    assert body.activation.state == "inactive"
    assert body.activation.configured_source == "replay"
    assert body.current_price.presentation == "replay_fixture"


def test_live_provider_contract_identity_freshness_and_canonical_fields() -> None:
    exchange = _PublicUsdM()
    settings = _local()
    monitor, source = _live_monitor(exchange, settings)
    with pytest.raises(NetworkMutationForbiddenError):
        source._http.request_json("POST", "/fapi/v1/order")
    snapshot = monitor.tick("BTCUSDT", now=NOW)
    assert snapshot.mode is MarketMode.LIVE_PERPETUAL
    assert snapshot.availability is MarketAvailability.FRESH
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.source_family is SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
    assert snapshot.provider_name == "binance-usdm-perpetual"
    assert snapshot.is_live is True
    assert snapshot.is_mock is False
    assert snapshot.fallback_used is False
    assert snapshot.current_price is not None
    assert snapshot.current_price.usable_as_current_market_price is True
    assert snapshot.current_price.presentation is CurrentPricePresentation.LIVE_MARK
    assert str(snapshot.current_price.price) == "101234.7"
    assert snapshot.current_price.freshness.policy_version == (
        "first-slice-btc-usdt-usdm-freshness/v1"
    )
    assert snapshot.cvd.available is True
    assert snapshot.cvd.signed_quote_delta == "2024.694"
    assert snapshot.ohlcv.available is True
    assert snapshot.ohlcv.latest_15m_final is True
    assert snapshot.coverage.completeness.value == "complete"
    assert snapshot.coverage.gap_state.value == "none"
    assert {request.method for request in exchange.requests} == {"GET"}
    identity = first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True)
    series = source.fetch_closed_ohlcv(
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=NOW,
    )
    assert all(bar.interval_end <= NOW for bar in series.bars)
    body = status_from_snapshot(snapshot, settings=settings)
    assert body.activation.state == "active"
    assert body.activation.configured_source == "binance_usdm"
    assert body.activation.exchange_credentials_used is False
    assert body.activation.spot_fallback_permitted is False
    assert body.activation.trade_freshness_seconds == 10
    assert body.source.fallback_used is False
    assert body.watcher_activated is False
    assert body.live_executable is False


def test_stale_live_quote_fails_closed_at_ten_seconds() -> None:
    settings = _local()
    source = ScriptedPerpetualSource(replay=False)
    source.enqueue(
        [
            trade(
                sequence=10,
                price="101000",
                quantity="0.01",
                buyer_is_maker=False,
                event_time=NOW - timedelta(seconds=1),
                receive_at=NOW,
            )
        ]
    )
    monitor = build_perpetual_market_monitor(settings, source=source)
    fresh = monitor.tick("BTCUSDT", now=NOW)
    assert fresh.current_price is not None
    assert fresh.current_price.usable_as_current_market_price is True
    stale = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=12))
    assert stale.availability is MarketAvailability.STALE
    assert stale.current_price is None
    assert stale.fallback_used is False


def test_disconnect_and_reconnect_fail_closed_then_recover() -> None:
    exchange = _PublicUsdM()
    monitor, _source = _live_monitor(exchange)
    connected = monitor.tick("BTCUSDT", now=NOW)
    epoch = connected.stream.connection_identity
    exchange.mode = "down"
    dropped = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=1))
    assert dropped.availability is MarketAvailability.UNAVAILABLE
    assert dropped.reason is MonitorReason.PROVIDER_UNAVAILABLE
    assert dropped.current_price is None
    assert dropped.stream.reconnect_state.value == "reconnecting"
    assert dropped.fallback_used is False
    exchange.mode = "ok"
    exchange.trade_id = 2
    held = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=1, milliseconds=20))
    assert held.backoff.active is True
    recovered = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=2))
    assert recovered.stream.connection_identity != epoch
    assert recovered.current_price is not None
    assert recovered.current_price.usable_as_current_market_price is True
    assert recovered.fallback_used is False


def test_rate_limit_does_not_fabricate_a_fallback() -> None:
    exchange = _PublicUsdM()
    monitor, _source = _live_monitor(exchange)
    fresh = monitor.tick("BTCUSDT", now=NOW)
    assert fresh.availability is MarketAvailability.FRESH
    exchange.mode = "limit"
    limited = monitor.tick("BTCUSDT", now=NOW + timedelta(milliseconds=50))
    assert limited.availability is MarketAvailability.DEGRADED
    assert limited.reason is MonitorReason.RATE_LIMITED
    assert limited.backoff.active is True
    assert limited.provider.using_fallback is False
    exchange.mode = "ok"
    exchange.trade_id = 2
    held = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=1))
    assert held.backoff.active is True
    recovered = monitor.tick("BTCUSDT", now=NOW + timedelta(seconds=3))
    assert recovered.availability is MarketAvailability.FRESH
    assert recovered.current_price is not None
    assert recovered.fallback_used is False


def test_aggtrade_gap_fails_closed() -> None:
    exchange = _PublicUsdM()
    exchange.mode = "gap"
    monitor, _source = _live_monitor(exchange)
    snapshot = monitor.tick("BTCUSDT", now=NOW)
    assert snapshot.availability in {MarketAvailability.DEGRADED, MarketAvailability.UNAVAILABLE}
    assert snapshot.reason in {MonitorReason.GAP, MonitorReason.UNRECOVERABLE_GAP}
    price = snapshot.current_price
    assert price is None or price.usable_as_current_market_price is False
    assert snapshot.fallback_used is False
    assert snapshot.cvd.available is False


def test_wrong_symbol_fails_closed() -> None:
    exchange = _PublicUsdM()
    monitor, source = _live_monitor(exchange)
    with pytest.raises(Exception, match="ETHUSDT") as exc:
        monitor.tick("ETHUSDT", now=NOW)
    assert "unknown_perpetual_instrument" in getattr(exc.value, "code", "")
    eth = binance_usdm_perpetual("ETHUSDT")
    with pytest.raises(WrongInstrumentError):
        source.fetch_closed_ohlcv(
            identity=first_slice_identity(
                timeframe=Timeframe.M15,
                replay=False,
                is_live=True,
                instrument=eth,
            ),
            instrument=eth,
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=NOW,
        )


def test_wrong_source_replay_adapter_cannot_satisfy_live_activation() -> None:
    settings = _local()
    monitor = build_perpetual_market_monitor(settings, source=ReplayPerpetualSource())
    snapshot = monitor.tick("BTCUSDT", now=NOW)
    assert snapshot.availability is MarketAvailability.UNAVAILABLE
    assert snapshot.reason is MonitorReason.WRONG_SOURCE
    price = snapshot.current_price
    assert price is None or price.usable_as_current_market_price is False
    assert snapshot.fallback_used is False


def test_health_shows_active_read_only_posture() -> None:
    app = create_app(_local())
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["execution_mode"] == "paper"
    assert body["real_trading_enabled"] is False
    assert body["perpetual_evidence_source"] == "binance_usdm"
    assert body["perpetual_evidence_activation"] == "active"
    assert body["live_quote_freshness_seconds"] == 10
    assert body["first_perpetual_symbol"] == "BTCUSDT"
    assert body["exchange_credentials_used_for_market_evidence"] is False
    assert body["spot_fallback_permitted"] is False
    assert body["fabricated_fallback_permitted"] is False
    assert body["live_market_read_only"] is True
    assert body["market_watcher_enabled"] is False
    assert body["watcher_orchestration_enabled"] is False
    assert body["telegram_alerts_enabled"] is False
    assert body["telegram_interaction_enabled"] is False
    assert body["automatic_telegram_delivery_enabled"] is False


def test_declared_config_files_match_the_activation_contract() -> None:
    staging = (ROOT / ".env.staging.example").read_text(encoding="utf-8")
    production = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    local = (ROOT / ".env.example").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "\nPERPETUAL_EVIDENCE_SOURCE=binance_usdm\n" in staging
    assert "\nPERPETUAL_EVIDENCE_SECONDARY_SOURCE=bybit_usdt_perpetual\n" in staging
    assert "\nBYBIT_PERPETUAL_BASE_URL=https://api.bybit.com\n" in staging
    assert "okx_usdt_swap" not in staging
    assert "\nMARKET_DATA_FUTURES_BASE_URL=https://fapi.binance.com\n" in staging
    assert "\nPERPETUAL_EVIDENCE_SOURCE=replay\n" in production
    assert "\nPERPETUAL_EVIDENCE_SOURCE=replay\n" in local
    assert "BINANCE_API_KEY=" not in staging
    # Sources are operator-owned (sync: false) on both services; hosts stay literal.
    assert "value: binance_usdm" not in render
    assert "value: bybit_usdt_perpetual" not in render
    assert render.count("key: PERPETUAL_EVIDENCE_SOURCE\n        sync: false") == 2
    assert render.count("key: PERPETUAL_EVIDENCE_SECONDARY_SOURCE\n        sync: false") == 2
    assert render.count("value: https://fapi.binance.com") == 2
    assert render.count("value: https://api.bybit.com") == 2
    assert "www.okx.com" not in render
    assert (ROOT / "docs/live_market_staging_activation.md").is_file()


def test_validation_script_self_check() -> None:
    script = ROOT / "scripts/validate-live-market-staging.sh"
    result = subprocess.run(
        [str(script), "--self-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "self-check passed" in result.stdout
