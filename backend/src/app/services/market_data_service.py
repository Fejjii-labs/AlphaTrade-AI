"""Market data orchestration: provider selection, caching, fallback, indicators."""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from app.market_contracts.errors import RateLimitedError, UpstreamBanError
from app.providers.market_data import (
    MarketDataEnvelope,
    MarketDataProvider,
    OHLCVBar,
    OHLCVData,
    TickerData,
)
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.market import (
    MarketAnalyzeRequest,
    MarketAnalyzeResponse,
    MarketDataMeta,
    MarketSnapshotResponse,
    OHLCVBarSchema,
    OHLCVResponse,
    StrategySignalSummary,
    TickerResponse,
)
from app.services.indicator_service import IndicatorService
from app.services.market_cache import MarketDataCache
from app.services.strategy_service import StrategyService
from app.strategies.base import StrategyEvaluationInput
from app.strategies.confidence import adjust_confidence_for_data_quality, data_quality_label


class MarketDataService:
    """Fetch market data with caching, freshness marking, and safe mock fallback."""

    TICKER_TTL_SECONDS = 15
    OHLCV_TTL_BY_TIMEFRAME: ClassVar[dict[Timeframe, int]] = {
        Timeframe.M1: 30,
        Timeframe.M5: 60,
        Timeframe.M15: 120,
        Timeframe.H1: 300,
        Timeframe.H4: 900,
        Timeframe.D1: 3600,
    }

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        cache: MarketDataCache | None = None,
        indicator_service: IndicatorService | None = None,
        strategy_service: StrategyService | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._indicators = indicator_service or IndicatorService()
        self._strategies = strategy_service

    def get_ticker(self, symbol: str, *, exchange: str = "binance") -> TickerResponse:
        cache_hit = False
        if self._cache is not None:
            key = self._cache._cache_key(
                provider=self._provider.name,
                exchange=exchange,
                symbol=symbol,
                data_type="ticker",
            )
            cached = self._cache.get(key)
            if cached.hit and cached.value:
                cache_hit = True
                payload = {**cached.value, "meta": {**cached.value["meta"], "cache_hit": True}}
                return TickerResponse.model_validate(payload)

        data = self._provider.get_ticker(symbol, exchange=exchange)
        response = self._ticker_to_schema(data, cache_hit=cache_hit)
        if self._cache is not None and not cache_hit:
            key = self._cache._cache_key(
                provider=self._provider.name,
                exchange=exchange,
                symbol=symbol,
                data_type="ticker",
            )
            self._cache.set(
                key,
                response.model_dump(mode="json"),
                ttl_seconds=self.TICKER_TTL_SECONDS,
            )
        return response

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        exchange: str = "binance",
        limit: int = 100,
    ) -> OHLCVResponse:
        cache_hit = False
        if self._cache is not None:
            key = self._cache._cache_key(
                provider=self._provider.name,
                exchange=exchange,
                symbol=symbol,
                data_type="ohlcv",
                timeframe=timeframe.value,
            )
            cached = self._cache.get(key)
            if cached.hit and cached.value:
                cache_hit = True
                payload = {**cached.value, "meta": {**cached.value["meta"], "cache_hit": True}}
                return OHLCVResponse.model_validate(payload)

        data = self._provider.get_ohlcv(symbol, timeframe, exchange=exchange, limit=limit)
        response = self._ohlcv_to_schema(data, cache_hit=cache_hit)
        if self._cache is not None and not cache_hit:
            ttl = self.OHLCV_TTL_BY_TIMEFRAME.get(timeframe, 300)
            key = self._cache._cache_key(
                provider=self._provider.name,
                exchange=exchange,
                symbol=symbol,
                data_type="ohlcv",
                timeframe=timeframe.value,
            )
            self._cache.set(key, response.model_dump(mode="json"), ttl_seconds=ttl)
        return response

    def _funding_for(
        self, symbol: str, *, exchange: str, degrade_on_rate_limit: bool
    ) -> tuple[Decimal | None, bool, bool, str | None]:
        """Return (funding_rate, fallback_used, available, unavailable_reason).

        During a futures rate limit / IP ban the provider raises
        ``RateLimitedError``. API callers let it propagate (mapped to 503);
        agent graph steps pass ``degrade_on_rate_limit=True`` and get
        ``funding_rate=None`` flagged unavailable instead of a crash.
        A non-ban mock fallback value is returned but flagged via the
        snapshot's ``funding_fallback`` (not ``meta.fallback_used``, which
        stays spot-only) so it never masquerades as live data.
        """
        try:
            funding = self._provider.get_funding_rate(symbol, exchange=exchange)
        except RateLimitedError as exc:
            if not degrade_on_rate_limit:
                raise
            banned = isinstance(exc, UpstreamBanError)
            reason = "futures_upstream_ban" if banned else "futures_rate_limited"
            return None, False, False, reason
        if funding is None:
            return None, False, True, None
        return funding.funding_rate, bool(funding.envelope.fallback_used), True, None

    def get_snapshot(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        exchange: str = "binance",
        degrade_on_rate_limit: bool = False,
    ) -> MarketSnapshotResponse:
        ticker = self.get_ticker(symbol, exchange=exchange)
        ohlcv = self.get_ohlcv(symbol, timeframe, exchange=exchange)
        funding_rate, funding_fallback, funding_ok, reason = self._funding_for(
            symbol, exchange=exchange, degrade_on_rate_limit=degrade_on_rate_limit
        )
        latest = ohlcv.bars[-1] if ohlcv.bars else None
        indicators = None
        if ohlcv.bars:
            bars = [
                OHLCVBar(
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    timestamp=b.timestamp,
                )
                for b in ohlcv.bars
            ]
            indicators = self._indicators.calculate(
                symbol=symbol,
                timeframe=timeframe,
                bars=bars,
                funding_rate=funding_rate,
            )
        meta = ohlcv.meta.model_copy(
            update={
                "is_live": ticker.meta.is_live and ohlcv.meta.is_live,
                "is_stale": ticker.meta.is_stale or ohlcv.meta.is_stale,
                "fallback_used": ticker.meta.fallback_used or ohlcv.meta.fallback_used,
                "provider_name": ohlcv.meta.provider_name,
                "cache_hit": ticker.meta.cache_hit or ohlcv.meta.cache_hit,
            }
        )
        return MarketSnapshotResponse(
            meta=meta,
            ticker=ticker,
            latest_bar=latest,
            indicators=indicators,
            funding_rate=funding_rate,
            funding_rate_available=funding_ok,
            funding_fallback=funding_fallback,
            # Open interest shares the futures cluster; unavailable whenever funding is.
            open_interest_available=funding_ok,
            unavailable_reason=reason,
        )

    def analyze(self, request: MarketAnalyzeRequest) -> MarketAnalyzeResponse:
        snapshot = self.get_snapshot(
            request.symbol,
            request.timeframe,
            exchange=request.exchange,
        )
        ohlcv = self.get_ohlcv(
            request.symbol,
            request.timeframe,
            exchange=request.exchange,
        )
        bars = [
            OHLCVBar(
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                timestamp=b.timestamp,
            )
            for b in ohlcv.bars
        ]
        # Reuse the snapshot's funding read: one futures call.
        funding_rate = snapshot.funding_rate
        indicators = self._indicators.calculate(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            funding_rate=funding_rate,
        )
        quality = data_quality_label(snapshot.meta)
        penalty = snapshot.meta.fallback_used or snapshot.meta.is_stale or not snapshot.meta.is_live
        signals: list[StrategySignalSummary] = []
        if self._strategies is not None and bars:
            close = bars[-1].close
            volume = bars[-1].volume
            eval_input = StrategyEvaluationInput(
                symbol=request.symbol,
                timeframe=request.timeframe,
                close=close,
                volume=volume,
                funding_rate=funding_rate,
                rsi=indicators.rsi,
                ema_fast=indicators.ema_fast,
                ema_slow=indicators.ema_slow,
                htf_trend=TradeDirection.LONG,
                data_is_live=snapshot.meta.is_live,
                data_is_stale=snapshot.meta.is_stale,
                data_fallback_used=snapshot.meta.fallback_used,
            )
            for strategy_id in request.strategy_ids:
                raw = self._strategies.evaluate(strategy_id, eval_input)
                if raw is None:
                    continue
                adjusted = adjust_confidence_for_data_quality(raw.confidence, eval_input)
                note = None
                if penalty:
                    note = f"Confidence reduced — data quality: {quality}"
                signals.append(
                    StrategySignalSummary(
                        strategy_id=strategy_id,
                        direction=raw.direction.value,
                        confidence=adjusted,
                        evidence=raw.evidence,
                        data_quality_note=note,
                    )
                )
        return MarketAnalyzeResponse(
            snapshot=snapshot,
            indicators=indicators,
            strategy_signals=signals,
            data_quality=quality,
            confidence_penalty_applied=penalty,
        )

    def provider_status(self):
        return self._provider.status()

    @staticmethod
    def _envelope_to_meta(env: MarketDataEnvelope, *, cache_hit: bool = False) -> MarketDataMeta:
        return MarketDataMeta(
            symbol=env.symbol,
            exchange=env.exchange,
            timeframe=env.timeframe,
            timestamp=env.timestamp,
            source=env.source,
            is_live=env.is_live,
            is_stale=env.is_stale,
            stale_reason=env.stale_reason,
            provider_name=env.provider_name,
            fallback_used=env.fallback_used,
            retrieved_at=env.retrieved_at,
            cache_hit=cache_hit,
        )

    def _ticker_to_schema(self, data: TickerData, *, cache_hit: bool = False) -> TickerResponse:
        return TickerResponse(
            meta=self._envelope_to_meta(data.envelope, cache_hit=cache_hit),
            last_price=data.last_price,
            bid=data.bid,
            ask=data.ask,
            volume_24h=data.volume_24h,
            change_24h_pct=data.change_24h_pct,
        )

    def _ohlcv_to_schema(self, data: OHLCVData, *, cache_hit: bool = False) -> OHLCVResponse:
        return OHLCVResponse(
            meta=self._envelope_to_meta(data.envelope, cache_hit=cache_hit),
            bars=[
                OHLCVBarSchema(
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    timestamp=b.timestamp,
                )
                for b in data.bars
            ],
        )
