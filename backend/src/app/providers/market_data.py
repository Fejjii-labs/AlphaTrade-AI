"""Read-only market data provider abstraction (mock + Binance public API)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from app.market_contracts.adapters.binance_rest_gate import (
    BinanceRestGate,
    process_binance_rest_gate,
    sanitize_retry_after,
)
from app.market_contracts.errors import RateLimitedError, UpstreamBanError
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe

logger = structlog.get_logger(__name__)

TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M3: 180,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H2: 7200,
    Timeframe.H4: 14400,
    Timeframe.H6: 21600,
    Timeframe.H12: 43200,
    Timeframe.D1: 86400,
    Timeframe.D3: 259200,
    Timeframe.W1: 604800,
}

TICKER_STALE_SECONDS = 60


@dataclass(frozen=True)
class MarketDataEnvelope:
    """Common metadata attached to every market data response."""

    symbol: str
    exchange: str
    timeframe: Timeframe | None
    timestamp: datetime
    source: str
    is_live: bool
    is_stale: bool
    stale_reason: str | None
    provider_name: str
    fallback_used: bool
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class TickerData:
    envelope: MarketDataEnvelope
    last_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume_24h: Decimal | None = None
    change_24h_pct: float | None = None


@dataclass(frozen=True)
class OHLCVBar:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class OHLCVData:
    envelope: MarketDataEnvelope
    bars: list[OHLCVBar]


@dataclass(frozen=True)
class FundingRateData:
    envelope: MarketDataEnvelope
    funding_rate: Decimal


@dataclass(frozen=True)
class OpenInterestData:
    envelope: MarketDataEnvelope
    open_interest: Decimal


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBookSnapshot:
    envelope: MarketDataEnvelope
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


def normalize_symbol(symbol: str) -> str:
    """Normalize trading symbol for exchange APIs (e.g. BTC/USDT -> BTCUSDT)."""
    cleaned = symbol.strip().upper().replace("/", "").replace("-", "")
    return cleaned


def _mock_price(symbol: str) -> Decimal:
    digest = hashlib.sha256(symbol.encode()).hexdigest()
    base = 20_000 + (int(digest[:8], 16) % 50_000)
    return Decimal(str(base))


def _is_stale(
    data_time: datetime,
    *,
    max_age_seconds: float,
    reason: str,
) -> tuple[bool, str | None]:
    age = (datetime.now(UTC) - data_time).total_seconds()
    if age > max_age_seconds:
        return True, reason
    return False, None


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    kind: ProviderKind

    def get_ticker(self, symbol: str, *, exchange: str = "binance") -> TickerData: ...

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        exchange: str = "binance",
        limit: int = 100,
    ) -> OHLCVData: ...

    def get_funding_rate(
        self, symbol: str, *, exchange: str = "binance"
    ) -> FundingRateData | None: ...

    def get_open_interest(
        self, symbol: str, *, exchange: str = "binance"
    ) -> OpenInterestData | None: ...

    def get_order_book_snapshot(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        depth: int = 10,
    ) -> OrderBookSnapshot | None: ...

    def status(self) -> ProviderStatus: ...


class MockMarketDataProvider:
    """Deterministic mock market data — always flagged as non-live."""

    name = "mock-market-data"
    kind = ProviderKind.MARKET_DATA

    def get_ticker(self, symbol: str, *, exchange: str = "mock") -> TickerData:
        sym = normalize_symbol(symbol)
        price = _mock_price(sym)
        now = datetime.now(UTC)
        return TickerData(
            envelope=MarketDataEnvelope(
                symbol=sym,
                exchange=exchange,
                timeframe=None,
                timestamp=now,
                source="mock",
                is_live=False,
                is_stale=False,
                stale_reason=None,
                provider_name=self.name,
                fallback_used=True,
            ),
            last_price=price,
            bid=price * Decimal("0.999"),
            ask=price * Decimal("1.001"),
            volume_24h=Decimal("1000000"),
            change_24h_pct=0.0,
        )

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        exchange: str = "mock",
        limit: int = 100,
    ) -> OHLCVData:
        sym = normalize_symbol(symbol)
        close = _mock_price(sym)
        now = datetime.now(UTC)
        bars: list[OHLCVBar] = []
        step = TIMEFRAME_SECONDS.get(timeframe, 3600)
        for i in range(limit):
            ts = now - timedelta(seconds=step * (limit - i))
            c = close * Decimal(str(1 + (i - limit // 2) * 0.001))
            bars.append(
                OHLCVBar(
                    open=c * Decimal("0.998"),
                    high=c * Decimal("1.005"),
                    low=c * Decimal("0.995"),
                    close=c,
                    volume=Decimal("500000") + Decimal(str(i * 1000)),
                    timestamp=ts,
                )
            )
        return OHLCVData(
            envelope=MarketDataEnvelope(
                symbol=sym,
                exchange=exchange,
                timeframe=timeframe,
                timestamp=bars[-1].timestamp if bars else now,
                source="mock",
                is_live=False,
                is_stale=False,
                stale_reason=None,
                provider_name=self.name,
                fallback_used=True,
            ),
            bars=bars,
        )

    def get_funding_rate(self, symbol: str, *, exchange: str = "mock") -> FundingRateData | None:
        sym = normalize_symbol(symbol)
        now = datetime.now(UTC)
        return FundingRateData(
            envelope=MarketDataEnvelope(
                symbol=sym,
                exchange=exchange,
                timeframe=None,
                timestamp=now,
                source="mock",
                is_live=False,
                is_stale=False,
                stale_reason=None,
                provider_name=self.name,
                fallback_used=True,
            ),
            funding_rate=Decimal("0.0001"),
        )

    def get_open_interest(self, symbol: str, *, exchange: str = "mock") -> OpenInterestData | None:
        sym = normalize_symbol(symbol)
        now = datetime.now(UTC)
        return OpenInterestData(
            envelope=MarketDataEnvelope(
                symbol=sym,
                exchange=exchange,
                timeframe=None,
                timestamp=now,
                source="mock",
                is_live=False,
                is_stale=False,
                stale_reason=None,
                provider_name=self.name,
                fallback_used=True,
            ),
            open_interest=Decimal("100000"),
        )

    def get_order_book_snapshot(
        self,
        symbol: str,
        *,
        exchange: str = "mock",
        depth: int = 10,
    ) -> OrderBookSnapshot | None:
        sym = normalize_symbol(symbol)
        price = _mock_price(sym)
        now = datetime.now(UTC)
        bids = [
            OrderBookLevel(price=price * Decimal(str(1 - 0.001 * (i + 1))), size=Decimal("1"))
            for i in range(depth)
        ]
        asks = [
            OrderBookLevel(price=price * Decimal(str(1 + 0.001 * (i + 1))), size=Decimal("1"))
            for i in range(depth)
        ]
        return OrderBookSnapshot(
            envelope=MarketDataEnvelope(
                symbol=sym,
                exchange=exchange,
                timeframe=None,
                timestamp=now,
                source="mock",
                is_live=False,
                is_stale=False,
                stale_reason=None,
                provider_name=self.name,
                fallback_used=True,
            ),
            bids=bids,
            asks=asks,
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY,
            using_fallback=False,
            is_mock=True,
            detail="Deterministic mock market data — not live prices.",
        )


class BinancePublicMarketDataProvider:
    """Read-only Binance public REST endpoints — no API key required."""

    name = "binance-public"
    kind = ProviderKind.MARKET_DATA

    def __init__(
        self,
        *,
        spot_base_url: str = "https://api.binance.com",
        futures_base_url: str = "https://fapi.binance.com",
        fallback: MockMarketDataProvider | None = None,
        timeout_seconds: float = 10.0,
        enabled: bool = True,
        rest_gate: BinanceRestGate | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._spot_base = spot_base_url.rstrip("/")
        self._transport = transport
        # USD-M futures reads share the IP weight budget with aggTrades, so they
        # go through the same process-wide gate (418/429/weight).
        self._rest_gate = rest_gate
        self._futures_base = futures_base_url.rstrip("/")
        self._fallback = fallback or MockMarketDataProvider()
        self._timeout = timeout_seconds
        self._enabled = enabled
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self._using_fallback = False

    def _gate_for(self, url: str) -> BinanceRestGate | None:
        if not url.startswith(self._futures_base + "/"):
            return None
        if self._rest_gate is None:
            self._rest_gate = process_binance_rest_gate()
        return self._rest_gate

    def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        gate = self._gate_for(url)
        if gate is not None:
            closed = gate.remaining()
            if closed is not None:
                kind, left = closed
                if kind == "ban":
                    raise UpstreamBanError(
                        "Binance USD-M temporarily banned this process (HTTP 418).",
                        retry_after_seconds=left,
                    )
                raise RateLimitedError(
                    "Binance USD-M is in a rate-limit backoff for this process.",
                    retry_after_seconds=left,
                )
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            response = client.get(url, params=params)
        if gate is not None:
            if response.status_code in (418, 429):
                kind = "ban" if response.status_code == 418 else "rate_limit"
                retry_after = _retry_after_header(response)
                gate.note_used_weight(response.headers)
                gate.note_failure(kind, retry_after)
                if kind == "ban":
                    raise UpstreamBanError(
                        "Binance USD-M temporarily banned this process (HTTP 418).",
                        retry_after_seconds=retry_after,
                    )
                raise RateLimitedError(
                    "Binance USD-M rate-limited this process (HTTP 429).",
                    retry_after_seconds=retry_after,
                )
            response.raise_for_status()
            gate.note_success(response.headers)
            return response.json()
        response.raise_for_status()
        return response.json()

    def _envelope(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: Timeframe | None,
        timestamp: datetime,
        fallback_used: bool,
        is_stale: bool = False,
        stale_reason: str | None = None,
    ) -> MarketDataEnvelope:
        return MarketDataEnvelope(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            timestamp=timestamp,
            source="binance-public" if not fallback_used else "mock",
            is_live=not fallback_used,
            is_stale=is_stale,
            stale_reason=stale_reason,
            provider_name=self.name if not fallback_used else self._fallback.name,
            fallback_used=fallback_used,
        )

    def get_ticker(self, symbol: str, *, exchange: str = "binance") -> TickerData:
        if not self._enabled:
            data = self._fallback.get_ticker(symbol, exchange=exchange)
            self._using_fallback = True
            return data

        sym = normalize_symbol(symbol)
        started = time.perf_counter()
        try:
            payload = self._request(f"{self._spot_base}/api/v3/ticker/24hr", {"symbol": sym})
            self._last_success_at = datetime.now(UTC)
            self._last_error = None
            self._using_fallback = False
            ts = datetime.now(UTC)
            is_stale, stale_reason = _is_stale(
                ts, max_age_seconds=TICKER_STALE_SECONDS, reason="Ticker older than 60s"
            )
            return TickerData(
                envelope=self._envelope(
                    symbol=sym,
                    exchange=exchange,
                    timeframe=None,
                    timestamp=ts,
                    fallback_used=False,
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                ),
                last_price=Decimal(str(payload["lastPrice"])),
                bid=Decimal(str(payload.get("bidPrice", payload["lastPrice"]))),
                ask=Decimal(str(payload.get("askPrice", payload["lastPrice"]))),
                volume_24h=Decimal(str(payload.get("volume", "0"))),
                change_24h_pct=float(payload.get("priceChangePercent", 0)),
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "binance_ticker_failed",
                symbol=sym,
                error=str(exc),
                latency_ms=latency_ms,
            )
            self._last_error = str(exc)[:200]
            self._using_fallback = True
            return self._fallback.get_ticker(symbol, exchange=exchange)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        exchange: str = "binance",
        limit: int = 100,
    ) -> OHLCVData:
        if not self._enabled:
            return self._fallback.get_ohlcv(symbol, timeframe, exchange=exchange, limit=limit)

        sym = normalize_symbol(symbol)
        try:
            payload = self._request(
                f"{self._spot_base}/api/v3/klines",
                {"symbol": sym, "interval": timeframe.value, "limit": min(limit, 1000)},
            )
            self._last_success_at = datetime.now(UTC)
            self._last_error = None
            self._using_fallback = False
            bars: list[OHLCVBar] = []
            for row in payload:
                ts = datetime.fromtimestamp(row[0] / 1000, tz=UTC)
                bars.append(
                    OHLCVBar(
                        open=Decimal(str(row[1])),
                        high=Decimal(str(row[2])),
                        low=Decimal(str(row[3])),
                        close=Decimal(str(row[4])),
                        volume=Decimal(str(row[5])),
                        timestamp=ts,
                    )
                )
            latest_ts = bars[-1].timestamp if bars else datetime.now(UTC)
            max_age = TIMEFRAME_SECONDS.get(timeframe, 3600) * 2
            is_stale, stale_reason = _is_stale(
                latest_ts,
                max_age_seconds=max_age,
                reason=f"Latest candle older than {max_age}s",
            )
            return OHLCVData(
                envelope=self._envelope(
                    symbol=sym,
                    exchange=exchange,
                    timeframe=timeframe,
                    timestamp=latest_ts,
                    fallback_used=False,
                    is_stale=is_stale,
                    stale_reason=stale_reason,
                ),
                bars=bars,
            )
        except Exception as exc:
            logger.warning("binance_ohlcv_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            self._using_fallback = True
            return self._fallback.get_ohlcv(symbol, timeframe, exchange=exchange, limit=limit)

    def get_funding_rate(self, symbol: str, *, exchange: str = "binance") -> FundingRateData | None:
        if not self._enabled:
            return self._fallback.get_funding_rate(symbol, exchange=exchange)

        sym = normalize_symbol(symbol)
        try:
            payload = self._request(f"{self._futures_base}/fapi/v1/premiumIndex", {"symbol": sym})
            self._last_success_at = datetime.now(UTC)
            ts = datetime.now(UTC)
            return FundingRateData(
                envelope=self._envelope(
                    symbol=sym,
                    exchange=exchange,
                    timeframe=None,
                    timestamp=ts,
                    fallback_used=False,
                ),
                funding_rate=Decimal(str(payload.get("lastFundingRate", "0"))),
            )
        except RateLimitedError as exc:
            # Fail closed during a ban/backoff: no mock fallback value.
            logger.warning("binance_funding_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            raise
        except Exception as exc:
            logger.warning("binance_funding_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            return self._fallback.get_funding_rate(symbol, exchange=exchange)

    def get_open_interest(
        self, symbol: str, *, exchange: str = "binance"
    ) -> OpenInterestData | None:
        if not self._enabled:
            return self._fallback.get_open_interest(symbol, exchange=exchange)

        sym = normalize_symbol(symbol)
        try:
            payload = self._request(f"{self._futures_base}/fapi/v1/openInterest", {"symbol": sym})
            self._last_success_at = datetime.now(UTC)
            ts = datetime.now(UTC)
            return OpenInterestData(
                envelope=self._envelope(
                    symbol=sym,
                    exchange=exchange,
                    timeframe=None,
                    timestamp=ts,
                    fallback_used=False,
                ),
                open_interest=Decimal(str(payload.get("openInterest", "0"))),
            )
        except RateLimitedError as exc:
            # Fail closed during a ban/backoff: no mock fallback value.
            logger.warning("binance_oi_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            raise
        except Exception as exc:
            logger.warning("binance_oi_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            return self._fallback.get_open_interest(symbol, exchange=exchange)

    def get_order_book_snapshot(
        self,
        symbol: str,
        *,
        exchange: str = "binance",
        depth: int = 10,
    ) -> OrderBookSnapshot | None:
        if not self._enabled:
            return self._fallback.get_order_book_snapshot(symbol, exchange=exchange, depth=depth)

        sym = normalize_symbol(symbol)
        try:
            payload = self._request(
                f"{self._spot_base}/api/v3/depth",
                {"symbol": sym, "limit": min(depth, 100)},
            )
            self._last_success_at = datetime.now(UTC)
            ts = datetime.now(UTC)
            bids = [
                OrderBookLevel(price=Decimal(str(p)), size=Decimal(str(q)))
                for p, q in payload.get("bids", [])[:depth]
            ]
            asks = [
                OrderBookLevel(price=Decimal(str(p)), size=Decimal(str(q)))
                for p, q in payload.get("asks", [])[:depth]
            ]
            return OrderBookSnapshot(
                envelope=self._envelope(
                    symbol=sym,
                    exchange=exchange,
                    timeframe=None,
                    timestamp=ts,
                    fallback_used=False,
                ),
                bids=bids,
                asks=asks,
            )
        except Exception as exc:
            logger.warning("binance_orderbook_failed", symbol=sym, error=str(exc))
            self._last_error = str(exc)[:200]
            return self._fallback.get_order_book_snapshot(symbol, exchange=exchange, depth=depth)

    def status(self) -> ProviderStatus:
        if not self._enabled:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.HEALTHY,
                using_fallback=True,
                is_mock=True,
                detail="Live market data disabled — using mock fallback.",
                last_success_at=self._last_success_at,
                error_message=_redact_error(self._last_error),
            )
        if self._using_fallback and self._last_error:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.DEGRADED,
                using_fallback=True,
                is_mock=False,
                detail="Binance unavailable — mock fallback active.",
                last_success_at=self._last_success_at,
                error_message=_redact_error(self._last_error),
            )
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY,
            using_fallback=False,
            is_mock=False,
            detail="Binance public REST (read-only, no API key).",
            last_success_at=self._last_success_at,
            error_message=_redact_error(self._last_error),
        )


def _retry_after_header(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None or not raw.strip():
        return None
    try:
        return sanitize_retry_after(float(raw.strip()))
    except ValueError:
        return None


def _redact_error(message: str | None) -> str | None:
    if not message:
        return None
    redacted = message.replace("api_key", "[redacted]").replace("secret", "[redacted]")
    return redacted[:200]
