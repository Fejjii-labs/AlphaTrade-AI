"""Bybit USDT perpetual evidence and Binance primary failover. No network. No arming."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.config import Settings
from app.evidence_pipeline.current_price import quote_current_price
from app.evidence_pipeline.watcher_port import _reconcile_monitor_and_assembled
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.coverage import build_complete_trade_window_coverage
from app.market_contracts.cvd import accumulate_signed_quote
from app.market_contracts.enums import SourceFamily, VenueId
from app.market_contracts.errors import (
    DuplicateDataError,
    EvidenceSourceSwitchRequiredError,
    FormingCandleError,
    GapDetectedError,
    IncompleteTradeWindowError,
    RateLimitedError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    UnapprovedEvidenceHostError,
    UpstreamBanError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.freshness import evaluate_freshness, first_slice_freshness_policy
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import (
    AGGRESSOR_CONVENTION,
    BINANCE_REST_ADAPTER_VERSION,
    InstrumentIdentity,
    binance_usdm_btcusdt,
    bybit_usdt_perpetual_btcusdt,
)
from app.market_contracts.trades import OrderedTradeBatch, build_trade_event, order_trades
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from app.watcher.errors import WatcherEvidenceUnavailableError

T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _identity(instrument: InstrumentIdentity):
    return first_slice_identity(
        timeframe=Timeframe.M15,
        replay=False,
        is_live=True,
        instrument=instrument,
    )


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _trade(
    seq: int,
    moment: datetime,
    *,
    side: str = "Buy",
    size: str = "0.02",
    symbol: str = "BTCUSDT",
    cross_seq: str | None = None,
) -> dict[str, str | int]:
    return {
        "execId": f"exec-{seq}",
        "symbol": symbol,
        "price": "100",
        "size": size,
        "side": side,
        "time": str(_ms(moment)),
        "seq": cross_seq if cross_seq is not None else str(seq * 17 + 3),
    }


def _kline(start: datetime, *, base: str = "1.25", quote: str = "125000.5") -> list[str]:
    return [str(_ms(start)), "100", "110", "90", "105", base, quote]


def _ok(result: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": result})


def _source(handler: object) -> BybitUsdtPerpetualSource:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return BybitUsdtPerpetualSource(
        base_url="https://api.bybit.com",
        timeout_seconds=2.0,
        transport=transport,
        max_retries=0,
    )


def _bybit_trades(rows: list[dict[str, str | int]]) -> dict[str, object]:
    return {"category": "linear", "symbol": "BTCUSDT", "list": rows}


class _Primary:
    """Binance stand-in. A configured failure never returns a payload."""

    name = "binance-usdm-perpetual"
    kind = ProviderKind.MARKET_DATA

    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.trade_fetches = 0

    def fetch_closed_ohlcv(self, **_kwargs: object) -> object:
        if self._error is not None:
            raise self._error
        raise FormingCandleError("stub has no closed Binance candles")

    def fetch_ordered_trades(
        self,
        *,
        identity: object,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> OrderedTradeBatch:
        self.trade_fetches += 1
        if self._error is not None:
            raise self._error
        trade = build_trade_event(
            instrument=instrument,
            venue_trade_id="binance-1",
            sequence=1,
            price=Decimal("100"),
            quantity=Decimal("0.01"),
            buyer_is_maker=False,
            event_timestamp=start + timedelta(seconds=1),
            receive_timestamp=receive_at,
            source_connection_id=source_connection_id,
            adapter_version=BINANCE_REST_ADAPTER_VERSION,
            aggressor_convention=AGGRESSOR_CONVENTION,
        )
        ordered = order_trades([trade])
        coverage = build_complete_trade_window_coverage(
            identity=identity,  # type: ignore[arg-type]
            lineage_id=source_connection_id,
            requested_start=start,
            requested_end=end,
            trades=ordered,
        )
        return with_content_hash(
            OrderedTradeBatch(
                identity=identity,  # type: ignore[arg-type]
                trades=ordered,
                source_connection_id=source_connection_id,
                coverage=coverage,
                content_hash="0" * 64,
            )
        )

    def status(self) -> ProviderStatus:
        healthy = self._error is None
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY if healthy else ProviderHealth.DEGRADED,
            using_fallback=False,
            is_mock=False,
            detail="primary",
        )


def test_ohlcv_volume_and_provenance_exclude_forming_candles() -> None:
    closed = T0 - timedelta(minutes=30)
    instrument = bybit_usdt_perpetual_btcusdt()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.bybit.com"
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["end"] == str(int(T0.timestamp() * 1000))
        assert "api-key" not in {name.lower() for name in request.headers}
        return _ok(
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "list": [_kline(T0), _kline(closed)],
            }
        )

    source = _source(handler)
    series = source.fetch_closed_ohlcv(
        identity=_identity(instrument),
        instrument=instrument,
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=T0,
    )
    source.close()
    assert len(series.bars) == 1
    assert series.bars[0].base_volume == Decimal("1.25")
    assert series.bars[0].quote_volume == Decimal("125000.5")
    assert series.identity.venue is VenueId.BYBIT
    assert series.identity.source.family is SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
    assert series.identity.provenance.fallback_used is False
    assert series.identity.provenance.is_live is True
    assert "binance" not in series.identity.instrument.instrument_id


def test_cvd_uses_taker_side_and_base_coin_size() -> None:
    start = T0 - timedelta(seconds=5)
    instrument = bybit_usdt_perpetual_btcusdt()
    rows = [
        _trade(11, start + timedelta(seconds=2), side="Sell", size="0.01"),
        _trade(10, start + timedelta(seconds=1), side="Buy", size="0.02"),
        _trade(9, start - timedelta(seconds=1), side="Buy", size="9"),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_bybit_trades(rows))

    source = _source(handler)
    batch = source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    source.close()
    signed, total = accumulate_signed_quote(batch.trades)
    assert signed == Decimal("1")
    assert total == Decimal("3")
    assert batch.identity.provenance.provider_name == "bybit-usdt-perpetual"
    assert batch.identity.venue is VenueId.BYBIT
    assert batch.identity.instrument.contract_multiplier == Decimal("1")


def test_gap_and_unproven_window_do_not_fabricate() -> None:
    start = T0 - timedelta(seconds=5)
    instrument = bybit_usdt_perpetual_btcusdt()
    connection = uuid4()
    pages = {"n": 0}

    def gapped(_request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        if pages["n"] == 1:
            return _ok(
                _bybit_trades(
                    [
                        _trade(12, start + timedelta(milliseconds=1500), cross_seq="900"),
                        _trade(10, start + timedelta(milliseconds=500), cross_seq="100"),
                        _trade(9, start - timedelta(seconds=1), cross_seq="50"),
                    ]
                )
            )
        return _ok(
            _bybit_trades(
                [
                    _trade(14, start + timedelta(seconds=3), cross_seq="2000"),
                ]
            )
        )

    source = _source(gapped)
    first = source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=connection,
        receive_at=T0,
    )
    assert [trade.sequence for trade in first.trades] == [2, 3]
    assert [trade.venue_trade_id for trade in first.trades] == ["exec-10", "exec-12"]
    with pytest.raises(GapDetectedError):
        source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=connection,
            receive_at=T0,
        )
    source.close()

    def short(_request: httpx.Request) -> httpx.Response:
        return _ok(_bybit_trades([_trade(3, start + timedelta(seconds=1))]))

    short_source = _source(short)
    with pytest.raises(IncompleteTradeWindowError):
        short_source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    short_source.close()


def test_forward_tail_covers_ten_reads_after_the_buffer_rolls() -> None:
    """Later pages overlap the watermark and do not rebuild the dropped prefix."""

    instrument = bybit_usdt_perpetual_btcusdt()
    connection = uuid4()
    origin = T0 - timedelta(seconds=4)
    prefix = _trade(1, origin - timedelta(seconds=1))
    tape = [
        _trade(index, origin + timedelta(milliseconds=100 * index), side="Buy", size="0.01")
        for index in range(2, 13)
    ]
    pages = [[prefix, tape[0], tape[1]]]
    for index in range(1, 10):
        pages.append([tape[index], tape[index + 1]])
    cursor = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        page = pages[cursor["n"]]
        cursor["n"] += 1
        return _ok(_bybit_trades(page))

    source = _source(handler)
    signed = Decimal("0")
    previous_ids: list[str] = []
    for read in range(10):
        batch = source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=origin,
            end=T0,
            source_connection_id=connection,
            receive_at=T0,
        )
        assert batch.identity.provenance.fallback_used is False
        assert [trade.venue_trade_id for trade in batch.trades] == [
            f"exec-{index}" for index in range(2, 4 + read)
        ]
        assert [trade.sequence for trade in batch.trades] == list(range(2, 4 + read))
        delta, _total = accumulate_signed_quote(
            [trade for trade in batch.trades if trade.venue_trade_id not in previous_ids]
        )
        signed += delta
        previous_ids = [trade.venue_trade_id for trade in batch.trades]
    source.close()
    assert cursor["n"] == 10
    assert signed == Decimal("11")
    assert previous_ids[-1] == "exec-12"


def test_duplicate_exec_ids_do_not_double_count_or_conflict() -> None:
    instrument = bybit_usdt_perpetual_btcusdt()
    connection = uuid4()
    start = T0 - timedelta(seconds=4)
    first = _trade(2, start + timedelta(milliseconds=100), size="0.02")
    proof = _trade(1, start - timedelta(seconds=1))
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok(_bybit_trades([proof, first, dict(first)]))
        if calls["n"] == 2:
            return _ok(_bybit_trades([first]))
        changed = dict(first)
        changed["price"] = "101"
        return _ok(_bybit_trades([changed]))

    source = _source(handler)
    batch = source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=connection,
        receive_at=T0,
    )
    assert [trade.venue_trade_id for trade in batch.trades] == ["exec-2"]
    assert accumulate_signed_quote(batch.trades)[0] == Decimal("2")
    quiet = source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=connection,
        receive_at=T0,
    )
    assert [trade.venue_trade_id for trade in quiet.trades] == ["exec-2"]
    with pytest.raises(DuplicateDataError):
        source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=connection,
            receive_at=T0,
        )
    source.close()


def test_restart_requires_a_new_prefix_and_stale_tail_fails_closed() -> None:
    instrument = bybit_usdt_perpetual_btcusdt()
    start = T0 - timedelta(seconds=4)
    fresh = _trade(4, T0 - timedelta(seconds=1))
    proof = _trade(3, start - timedelta(seconds=1))
    stale = _trade(5, T0 - timedelta(seconds=30))
    stale_proof = _trade(6, T0 - timedelta(seconds=31))

    def fresh_page(_request: httpx.Request) -> httpx.Response:
        if fresh_page.calls == 0:
            fresh_page.calls += 1
            return _ok(_bybit_trades([proof, fresh]))
        return _ok(_bybit_trades([fresh]))

    fresh_page.calls = 0
    source = _source(fresh_page)
    connection = uuid4()
    source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=connection,
        receive_at=T0,
    )
    with pytest.raises(IncompleteTradeWindowError):
        source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    source.close()

    def stale_page(_request: httpx.Request) -> httpx.Response:
        return _ok(_bybit_trades([stale_proof, stale]))

    stale_source = _source(stale_page)
    batch = stale_source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=T0 - timedelta(seconds=31),
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    stale_source.close()
    with pytest.raises(StaleEvidenceError):
        evaluate_freshness(
            source_time=batch.trades[-1].event_timestamp,
            evaluated_at=T0,
            policy=first_slice_freshness_policy(),
            require_fresh=True,
        )


def test_spot_and_inverse_books_are_rejected() -> None:
    start = T0 - timedelta(seconds=5)
    instrument = bybit_usdt_perpetual_btcusdt()

    def spot(_request: httpx.Request) -> httpx.Response:
        rows = [
            _trade(4, start + timedelta(seconds=1)),
            _trade(3, start - timedelta(seconds=1)),
        ]
        return _ok({"category": "spot", "symbol": "BTCUSDT", "list": rows})

    source = _source(spot)
    with pytest.raises(SpotFallbackRejectedError):
        source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    source.close()

    def inverse(_request: httpx.Request) -> httpx.Response:
        return _ok({"category": "inverse", "symbol": "BTCUSD", "list": []})

    inverse_source = _source(inverse)
    with pytest.raises(WrongMarketError, match="not linear"):
        inverse_source.fetch_ordered_trades(
            identity=_identity(instrument),
            instrument=instrument,
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    inverse_source.close()

    with pytest.raises(SpotFallbackRejectedError):
        BybitUsdtPerpetualSource(base_url="https://api.binance.com", max_retries=0)
    with pytest.raises(UnapprovedEvidenceHostError):
        BybitUsdtPerpetualSource(base_url="http://api.bybit.com", max_retries=0)


def test_outage_rate_limit_and_stale_evidence_fail_closed() -> None:
    instrument = bybit_usdt_perpetual_btcusdt()

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    source = _source(down)
    outage = source.status()
    source.close()
    assert outage.health is ProviderHealth.UNAVAILABLE
    assert outage.using_fallback is False
    assert outage.is_mock is False

    def limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"retCode": 10006})

    limited_status = _source(limited).status()
    assert limited_status.using_fallback is False
    assert limited_status.health is ProviderHealth.UNAVAILABLE

    def coded(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"retCode": 10006, "result": {}})

    coded_source = _source(coded)
    with pytest.raises(RateLimitedError):
        coded_source.fetch_closed_ohlcv(
            identity=_identity(instrument),
            instrument=instrument,
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=T0,
        )
    coded_source.close()

    start = T0 - timedelta(seconds=5)
    fresh = T0 - timedelta(seconds=1)
    proof = T0 - timedelta(seconds=15)

    def fresh_handler(_request: httpx.Request) -> httpx.Response:
        return _ok(_bybit_trades([_trade(8, fresh), _trade(7, proof)]))

    fresh_source = _source(fresh_handler)
    batch = fresh_source.fetch_ordered_trades(
        identity=_identity(instrument),
        instrument=instrument,
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    fresh_source.close()
    with pytest.raises(StaleEvidenceError):
        evaluate_freshness(
            source_time=batch.trades[-1].event_timestamp,
            evaluated_at=T0 + timedelta(seconds=30),
            policy=first_slice_freshness_policy(),
            require_fresh=True,
        )
    quote = quote_current_price(
        _source(fresh_handler),
        identity=_identity(instrument),
        instrument=instrument,
        evaluated_at=T0,
        connection_id=uuid4(),
        replay=False,
    )
    assert quote.usable_as_current_market_price is True
    assert quote.fallback_used is False
    assert quote.source_family is SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC


def test_binance_healthy_does_not_call_bybit() -> None:
    called = {"bybit": 0}

    def bybit_handler(_request: httpx.Request) -> httpx.Response:
        called["bybit"] += 1
        return httpx.Response(500, json={"retCode": 10001})

    start = T0 - timedelta(seconds=4)
    router = FailoverPerpetualSource(
        _Primary(None),  # type: ignore[arg-type]
        _source(bybit_handler),
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    batch = router.fetch_ordered_trades(
        identity=_identity(binance_usdm_btcusdt()),
        instrument=binance_usdm_btcusdt(),
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    assert batch.identity.venue is VenueId.BINANCE
    assert batch.identity.source.family is SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
    assert called["bybit"] == 0
    assert router.using_secondary is False
    assert router.kind is router.active_source.kind
    assert router.kind is ProviderKind.MARKET_DATA


@pytest.mark.parametrize("kind", ["418", "429", "outage"])
def test_binance_http_failure_switches_entire_window_to_bybit(kind: str) -> None:
    seen = {"binance": 0, "bybit": 0}
    start = T0 - timedelta(seconds=4)

    def binance_handler(request: httpx.Request) -> httpx.Response:
        seen["binance"] += 1
        assert request.url.host == "fapi.binance.com"
        if kind == "418":
            return httpx.Response(418, json={"msg": "banned"})
        if kind == "429":
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"msg": "limit"})
        raise httpx.ConnectError("down")

    def bybit_handler(request: httpx.Request) -> httpx.Response:
        seen["bybit"] += 1
        assert request.url.host == "api.bybit.com"
        assert request.url.params.get("category") == "linear"
        assert "X-BAPI-API-KEY" not in request.headers
        return _ok(
            _bybit_trades(
                [
                    _trade(5, start + timedelta(seconds=1)),
                    _trade(4, start - timedelta(seconds=1)),
                ]
            )
        )

    primary = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(binance_handler),
        max_retries=0,
    )
    secondary = _source(bybit_handler)
    router = FailoverPerpetualSource(
        primary,
        secondary,
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError) as switched:
        router.fetch_ordered_trades(
            identity=_identity(binance_usdm_btcusdt()),
            instrument=binance_usdm_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    assert switched.value.instrument == bybit_usdt_perpetual_btcusdt()
    assert seen["bybit"] == 0
    assert seen["binance"] == 1
    assert router.kind is secondary.kind
    assert router.kind is ProviderKind.MARKET_DATA
    batch = router.fetch_ordered_trades(
        identity=_identity(bybit_usdt_perpetual_btcusdt()),
        instrument=bybit_usdt_perpetual_btcusdt(),
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    assert batch.identity.venue is VenueId.BYBIT
    assert batch.identity.source.family is SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
    assert "binance" not in batch.identity.instrument.instrument_id
    assert seen["bybit"] == 1
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        router.fetch_ordered_trades(
            identity=_identity(binance_usdm_btcusdt()),
            instrument=binance_usdm_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    primary.close()
    secondary.close()


def test_bybit_outage_after_switch_does_not_fabricate() -> None:
    start = T0 - timedelta(seconds=4)

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bybit-down")

    router = FailoverPerpetualSource(
        _Primary(UpstreamBanError("banned", retry_after_seconds=90)),  # type: ignore[arg-type]
        _source(down),
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        router.fetch_ordered_trades(
            identity=_identity(binance_usdm_btcusdt()),
            instrument=binance_usdm_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    with pytest.raises(RegionalProviderFailureError):
        router.fetch_ordered_trades(
            identity=_identity(bybit_usdt_perpetual_btcusdt()),
            instrument=bybit_usdt_perpetual_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    assert router.using_secondary is True


def test_provider_recovers_and_switches_back_to_binance() -> None:
    primary = _Primary(UpstreamBanError("banned", retry_after_seconds=90))
    start = T0 - timedelta(seconds=4)
    calls = {"bybit": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["bybit"] += 1
        return _ok(
            _bybit_trades(
                [
                    _trade(5, start + timedelta(seconds=1)),
                    _trade(4, start - timedelta(seconds=1)),
                ]
            )
        )

    secondary = _source(handler)
    router = FailoverPerpetualSource(
        primary,  # type: ignore[arg-type]
        secondary,
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        router.fetch_ordered_trades(
            identity=_identity(binance_usdm_btcusdt()),
            instrument=binance_usdm_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    bybit_batch = router.fetch_ordered_trades(
        identity=_identity(bybit_usdt_perpetual_btcusdt()),
        instrument=bybit_usdt_perpetual_btcusdt(),
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    assert bybit_batch.identity.venue is VenueId.BYBIT
    assert calls["bybit"] == 1
    assert router.try_recover_primary() is None
    primary._error = None
    recovered = router.try_recover_primary()
    assert recovered == binance_usdm_btcusdt()
    assert router.using_secondary is False
    assert router.name == "binance-usdm-perpetual"
    assert router.kind is primary.kind
    assert router.kind is ProviderKind.MARKET_DATA
    with pytest.raises(WrongSourceError, match="active perpetual source"):
        router.fetch_ordered_trades(
            identity=_identity(bybit_usdt_perpetual_btcusdt()),
            instrument=bybit_usdt_perpetual_btcusdt(),
            start=start,
            end=T0,
            source_connection_id=uuid4(),
            receive_at=T0,
        )
    binance_batch = router.fetch_ordered_trades(
        identity=_identity(binance_usdm_btcusdt()),
        instrument=binance_usdm_btcusdt(),
        start=start,
        end=T0,
        source_connection_id=uuid4(),
        receive_at=T0,
    )
    assert binance_batch.identity.venue is VenueId.BINANCE
    assert calls["bybit"] == 1
    secondary.close()


def test_monitor_switch_publishes_bybit_then_returns_to_binance() -> None:
    primary = _Primary(UpstreamBanError("banned", retry_after_seconds=90))
    inside = T0 - timedelta(seconds=2)
    proof = T0 - timedelta(seconds=11)
    closed = T0 - timedelta(hours=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/kline"):
            return _ok(
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "list": [_kline(T0), _kline(closed)],
                }
            )
        return _ok(
            _bybit_trades(
                [
                    _trade(6, inside, side="Buy", size="0.02"),
                    _trade(5, proof, side="Buy", size="0.02"),
                ]
            )
        )

    router = FailoverPerpetualSource(
        primary,  # type: ignore[arg-type]
        _source(handler),
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    monitor = PerpetualMarketMonitor(
        router,  # type: ignore[arg-type]
        replay=False,
        backoff=BackoffPolicy(initial_seconds=0.01, max_seconds=1.0),
        clock=lambda: T0,
        poll_seconds=0.01,
    )
    snapshot = monitor.tick("BTCUSDT", now=T0)
    assert snapshot.source_family is SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
    assert snapshot.instrument_id == bybit_usdt_perpetual_btcusdt().instrument_id
    assert snapshot.fallback_used is False
    assert snapshot.current_price is not None
    assert snapshot.current_price.provider_name == "bybit-usdt-perpetual"
    assert snapshot.cvd.available is True
    assert snapshot.cvd.signed_quote_delta is not None
    assert Decimal(snapshot.cvd.signed_quote_delta) == Decimal("2")
    assert primary.trade_fetches == 1
    bybit_epoch = snapshot.stream.connection_identity
    _reconcile_monitor_and_assembled(snapshot, False, SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC)
    with pytest.raises(WatcherEvidenceUnavailableError) as exc:
        _reconcile_monitor_and_assembled(snapshot, False, SourceFamily.BINANCE_USDM_FUTURES_PUBLIC)
    assert exc.value.reason_code == "wrong_source"

    primary._error = None
    returned = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=1))
    assert returned.source_family is SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
    assert returned.instrument_id == binance_usdm_btcusdt().instrument_id
    assert returned.current_price is not None
    assert returned.current_price.provider_name == "binance-usdm-perpetual"
    assert returned.stream.connection_identity != bybit_epoch
    assert returned.fallback_used is False


def test_factory_selects_bybit_secondary_without_enabling_trading() -> None:
    settings = Settings(
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        perpetual_evidence_source="binance_usdm",
        perpetual_evidence_secondary_source="bybit_usdt_perpetual",
        market_data_futures_base_url="https://fapi.binance.com",
        bybit_perpetual_base_url="https://api.bybit.com",
    )
    source = resolve_perpetual_evidence_source(settings)
    assert isinstance(source, FailoverPerpetualSource)
    assert source.active_instrument().venue is VenueId.BINANCE
    assert settings.enable_real_trading is False
    direct = resolve_perpetual_evidence_source(
        settings.model_copy(
            update={
                "perpetual_evidence_source": "bybit_usdt_perpetual",
                "perpetual_evidence_secondary_source": "none",
            }
        )
    )
    assert isinstance(direct, BybitUsdtPerpetualSource)
    with pytest.raises(ValueError, match="spot"):
        Settings(
            environment="local",
            perpetual_evidence_source="spot",
            jwt_secret="x" * 40,
        )
    with pytest.raises(ValueError, match="bybit_usdt_perpetual"):
        Settings(
            environment="local",
            perpetual_evidence_secondary_source="okx_usdt_swap",
            jwt_secret="x" * 40,
        )


def test_failover_refuses_a_pair_that_is_not_market_data() -> None:
    class _ExchangeShaped:
        name = "not-market-data"
        kind = ProviderKind.EXCHANGE

        def status(self) -> ProviderStatus:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.HEALTHY,
                using_fallback=False,
                is_mock=True,
            )

    with pytest.raises(WrongSourceError, match="market_data"):
        FailoverPerpetualSource(
            _Primary(None),  # type: ignore[arg-type]
            _ExchangeShaped(),  # type: ignore[arg-type]
            primary_instrument=binance_usdm_btcusdt(),
            secondary_instrument=bybit_usdt_perpetual_btcusdt(),
        )


def test_staging_provider_registry_starts_with_bybit_secondary() -> None:
    """The Render startup path registers the Binance/Bybit failover."""

    from app.main import create_app
    from app.providers.registry import build_default_registry

    settings = Settings(
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
        market_watcher_enabled=False,
        telegram_alerts_enabled=False,
        perpetual_evidence_source="binance_usdm",
        perpetual_evidence_secondary_source="bybit_usdt_perpetual",
        market_data_futures_base_url="https://fapi.binance.com",
        bybit_perpetual_base_url="https://api.bybit.com",
    )
    registry = build_default_registry(settings)
    app = create_app(settings)
    source = resolve_perpetual_evidence_source(settings)
    assert isinstance(source, FailoverPerpetualSource)
    assert source.kind is source.active_source.kind
    assert source.kind is ProviderKind.MARKET_DATA
    assert source.using_secondary is False
    registered = registry.get("binance-usdm-perpetual")
    assert registered is not None
    assert registered.kind is ProviderKind.MARKET_DATA
    started = app.state.provider_registry.get("binance-usdm-perpetual")
    assert started is not None
    assert started.kind is ProviderKind.MARKET_DATA
    assert settings.enable_real_trading is False
    assert settings.execution_mode.value == "paper"
    assert settings.exchange_mode.value == "paper_internal"
