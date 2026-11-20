"""Market watcher API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request

from app.core.auth import TenantDep
from app.core.dependencies import (
    HistoricalCandleServiceDep,
    MarketDataServiceDep,
    MarketServiceDep,
    SessionDep,
    UsageServiceDep,
)
from app.schemas.common import Timeframe
from app.schemas.historical_candles import (
    HistoricalCandleList,
    HistoricalIngestRequest,
    HistoricalIngestResult,
)
from app.schemas.market import (
    MarketAnalyzeRequest,
    MarketAnalyzeResponse,
    MarketSnapshotResponse,
    OHLCVResponse,
    TickerResponse,
    WatchlistItem,
    WatchlistItemCreate,
    WatchlistItemUpdate,
)
from app.security.quota_enforcement import require_quota
from app.security.rbac import TraderDep

router = APIRouter(prefix="/market", tags=["market"])

_MARKET_ANALYZE_QUOTA = require_quota("market_analyze")


@router.get("/ticker", response_model=TickerResponse, summary="Get ticker price")
def get_ticker(
    tenant: TenantDep,
    market_data: MarketDataServiceDep,
    symbol: str = Query(default="BTCUSDT", min_length=2, max_length=30),
    exchange: str = Query(default="binance", min_length=2, max_length=40),
) -> TickerResponse:
    _ = tenant
    return market_data.get_ticker(symbol, exchange=exchange)


@router.get("/ohlcv", response_model=OHLCVResponse, summary="Get OHLCV candles")
def get_ohlcv(
    tenant: TenantDep,
    market_data: MarketDataServiceDep,
    symbol: str = Query(default="BTCUSDT", min_length=2, max_length=30),
    exchange: str = Query(default="binance", min_length=2, max_length=40),
    timeframe: Timeframe = Query(default=Timeframe.H1),
    limit: int = Query(default=100, ge=1, le=500),
) -> OHLCVResponse:
    _ = tenant
    return market_data.get_ohlcv(symbol, timeframe, exchange=exchange, limit=limit)


@router.get("/snapshots", response_model=MarketSnapshotResponse, summary="Market snapshot")
def get_snapshot(
    tenant: TenantDep,
    market_data: MarketDataServiceDep,
    symbol: str = Query(default="BTCUSDT", min_length=2, max_length=30),
    exchange: str = Query(default="binance", min_length=2, max_length=40),
    timeframe: Timeframe = Query(default=Timeframe.H1),
) -> MarketSnapshotResponse:
    _ = tenant
    return market_data.get_snapshot(symbol, timeframe, exchange=exchange)


@router.post(
    "/analyze",
    response_model=MarketAnalyzeResponse,
    summary="Analyze market context",
    dependencies=[_MARKET_ANALYZE_QUOTA],
)
def analyze_market(
    body: MarketAnalyzeRequest,
    tenant: TraderDep,
    market_data: MarketDataServiceDep,
    usage_service: UsageServiceDep,
    session: SessionDep,
    request: Request,
) -> MarketAnalyzeResponse:
    import uuid

    from app.schemas.usage import UsageEventCreate

    result = market_data.analyze(body)
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    usage_service.record(
        UsageEventCreate(
            request_id=request_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            feature="market_analyze",
            provider="market-data",
            input_tokens=0,
            output_tokens=0,
            provider_metadata={"cost_source": "unavailable"},
        )
    )
    session.commit()
    return result


@router.post("/watchlist", response_model=WatchlistItem, summary="Add watchlist item")
def create_watchlist_item(
    body: WatchlistItemCreate,
    tenant: TraderDep,
    market_service: MarketServiceDep,
    session: SessionDep,
) -> WatchlistItem:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    result = market_service.create(payload)
    session.commit()
    return result


@router.get("/watchlist", response_model=list[WatchlistItem], summary="List watchlist")
def list_watchlist(
    tenant: TenantDep,
    market_service: MarketServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[WatchlistItem]:
    items, _total = market_service.list_items(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return items


@router.patch(
    "/watchlist/{watchlist_item_id}",
    response_model=WatchlistItem,
    summary="Update watchlist item",
)
def update_watchlist_item(
    watchlist_item_id: uuid.UUID,
    body: WatchlistItemUpdate,
    tenant: TraderDep,
    market_service: MarketServiceDep,
    session: SessionDep,
) -> WatchlistItem:
    result = market_service.update(
        watchlist_item_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        data=body,
    )
    session.commit()
    return result


@router.delete(
    "/watchlist/{watchlist_item_id}",
    status_code=204,
    summary="Delete watchlist item",
)
def delete_watchlist_item(
    watchlist_item_id: uuid.UUID,
    tenant: TraderDep,
    market_service: MarketServiceDep,
    session: SessionDep,
) -> None:
    market_service.delete(
        watchlist_item_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()


@router.post(
    "/history/ingest",
    response_model=HistoricalIngestResult,
    summary="Ingest historical OHLCV candles for backtests",
)
def ingest_historical_candles(
    body: HistoricalIngestRequest,
    tenant: TraderDep,
    service: HistoricalCandleServiceDep,
    session: SessionDep,
) -> HistoricalIngestResult:
    _ = tenant
    result = service.ingest(body)
    session.commit()
    return result


@router.get(
    "/history/candles",
    response_model=HistoricalCandleList,
    summary="List stored historical candles (debug)",
)
def list_historical_candles(
    tenant: TenantDep,
    service: HistoricalCandleServiceDep,
    symbol: str = Query(default="BTCUSDT", min_length=2, max_length=30),
    exchange: str = Query(default="binance", min_length=1, max_length=40),
    timeframe: Timeframe = Query(default=Timeframe.H4),
    limit: int = Query(default=200, ge=1, le=5000),
) -> HistoricalCandleList:
    _ = tenant
    return service.get_candles(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        limit=limit,
    )
