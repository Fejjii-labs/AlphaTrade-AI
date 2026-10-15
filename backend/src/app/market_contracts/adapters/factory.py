"""Resolve the read-only perpetual evidence source from settings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.market_activation.profile import LIVE_MODES, REPLAY_MODES
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.evidence_pool import shared_binance_evidence_pool
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.catalog import PerpetualInstrumentCatalog
from app.market_contracts.errors import FallbackForbiddenError
from app.market_contracts.identity import binance_usdm_perpetual, bybit_usdt_perpetual

PerpetualEvidenceSource = (
    ReplayPerpetualSource
    | BinanceUsdmPerpetualSource
    | BybitUsdtPerpetualSource
    | FailoverPerpetualSource
)


def perpetual_source_is_replay(settings: Settings) -> bool:
    return settings.perpetual_evidence_source.strip().lower() in REPLAY_MODES


def canonical_evidence_source_for_process(
    state: Any,
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    catalog: PerpetualInstrumentCatalog | None = None,
) -> PerpetualEvidenceSource:
    """Return the process source stored on ``state``. One HTTP app shares it."""

    current = getattr(state, "canonical_perpetual_evidence_source", None)
    if isinstance(
        current,
        ReplayPerpetualSource
        | BinanceUsdmPerpetualSource
        | BybitUsdtPerpetualSource
        | FailoverPerpetualSource,
    ):
        return current
    source = resolve_perpetual_evidence_source(
        settings,
        transport=transport,
        catalog=catalog,
        shared=True,
    )
    state.canonical_perpetual_evidence_source = source
    return source


def resolve_perpetual_evidence_source(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    catalog: PerpetualInstrumentCatalog | None = None,
    shared: bool = False,
    symbol: str = "BTCUSDT",
) -> PerpetualEvidenceSource:
    mode = settings.perpetual_evidence_source.strip().lower()
    secondary = settings.perpetual_evidence_secondary_source.strip().lower()
    if catalog is None and symbol != "BTCUSDT":
        from app.market_contracts.catalog import catalog_for_symbols
        from app.market_contracts.enums import VenueId

        catalog = catalog_for_symbols(
            [symbol], venue=VenueId.BYBIT if mode == "bybit_usdt_perpetual" else VenueId.BINANCE
        )
    if mode in REPLAY_MODES:
        return ReplayPerpetualSource()
    if mode == "bybit_usdt_perpetual":
        return _bybit_source(settings, transport=transport, symbol=symbol, shared=shared)
    if mode in LIVE_MODES and secondary == "bybit_usdt_perpetual":
        return FailoverPerpetualSource(
            _binance_source(settings, transport=transport, catalog=catalog, shared=shared),
            _bybit_source(settings, transport=transport, symbol=symbol, shared=shared),
            primary_instrument=binance_usdm_perpetual(symbol),
            secondary_instrument=bybit_usdt_perpetual(symbol),
        )
    if mode in LIVE_MODES:
        return _binance_source(settings, transport=transport, catalog=catalog, shared=shared)
    raise FallbackForbiddenError(
        f"Unknown perpetual_evidence_source={mode}; refusing silent fallback."
    )


def _binance_source(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None,
    catalog: PerpetualInstrumentCatalog | None,
    shared: bool,
) -> BinanceUsdmPerpetualSource:
    pool = shared_binance_evidence_pool(settings) if shared else None
    return BinanceUsdmPerpetualSource(
        base_url=settings.market_data_futures_base_url,
        timeout_seconds=settings.perpetual_evidence_timeout_seconds,
        transport=transport,
        catalog=catalog,
        max_retries=settings.binance_request_max_retries,
        weight_per_minute=settings.binance_request_weight_per_minute,
        max_backoff_seconds=settings.binance_request_max_backoff_seconds,
        trade_cache_entries=settings.binance_evidence_cache_entries,
        cache_ttl_seconds=settings.binance_evidence_cache_ttl_seconds,
        max_cached_rows=settings.binance_evidence_cache_max_rows,
        trade_cache=None if pool is None else pool.cache,
        reduced_cache=None if pool is None else pool.reduced,
        observation_cache=None if pool is None else pool.observations,
        observation_clock=(lambda: datetime.now(UTC)) if transport is None else None,
        budget=None if pool is None else pool.budget,
    )


def _bybit_source(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None,
    symbol: str = "BTCUSDT",
    shared: bool = False,
) -> BybitUsdtPerpetualSource:
    return BybitUsdtPerpetualSource(
        reuse_recent_pages=shared,
        observation_clock=(lambda: datetime.now(UTC)) if transport is None else None,
        instrument=bybit_usdt_perpetual(symbol),
        base_url=settings.bybit_perpetual_base_url,
        timeout_seconds=settings.perpetual_evidence_timeout_seconds,
        transport=transport,
        max_retries=settings.binance_request_max_retries,
        max_backoff_seconds=settings.binance_request_max_backoff_seconds,
    )
