"""TradingView signal intake and inbox API (AT-037)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request

from app.core.dependencies import SessionDep, TradingViewSignalServiceDep
from app.schemas.common import TradingViewSignalStatus
from app.schemas.tradingview_signal import (
    TradingViewSignalCreateCandidateRequest,
    TradingViewSignalCreateCandidateResult,
    TradingViewSignalIntakeResult,
    TradingViewSignalItem,
    TradingViewSignalListResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency, tradingview_webhook_rate_limit
from app.security.rbac import ReaderDep, TraderDep

router = APIRouter(tags=["tradingview"])

_WEBHOOK_LIMIT = Depends(tradingview_webhook_rate_limit)
_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("tradingview:read", limit=120, window_seconds=3600)
)
_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("tradingview:write", limit=60, window_seconds=3600)
)


@router.post(
    "/webhooks/tradingview",
    response_model=TradingViewSignalIntakeResult,
    summary="TradingView signed webhook intake",
    dependencies=[_WEBHOOK_LIMIT],
)
async def tradingview_webhook(
    request: Request,
    service: TradingViewSignalServiceDep,
    session: SessionDep,
    x_at_signature: str | None = Header(default=None, alias="X-AT-Signature"),
    x_at_timestamp: str | None = Header(default=None, alias="X-AT-Timestamp"),
) -> TradingViewSignalIntakeResult:
    raw = await request.body()
    result = service.intake_webhook(
        raw,
        signature_header=x_at_signature,
        timestamp_header=x_at_timestamp,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return result


@router.get(
    "/tradingview/signals",
    response_model=TradingViewSignalListResponse,
    summary="List TradingView signals for the current organization",
    dependencies=[_READ_LIMIT],
)
async def list_tradingview_signals(
    tenant: ReaderDep,
    service: TradingViewSignalServiceDep,
    status: TradingViewSignalStatus | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TradingViewSignalListResponse:
    return service.list_signals(
        organization_id=tenant.organization_id,
        status=status,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tradingview/signals/{signal_id}",
    response_model=TradingViewSignalItem,
    summary="TradingView signal detail",
    dependencies=[_READ_LIMIT],
)
async def get_tradingview_signal(
    signal_id: uuid.UUID,
    tenant: ReaderDep,
    service: TradingViewSignalServiceDep,
) -> TradingViewSignalItem:
    return service.get_signal(signal_id, organization_id=tenant.organization_id)


@router.post(
    "/tradingview/signals/{signal_id}/create-candidate",
    response_model=TradingViewSignalCreateCandidateResult,
    summary="Create a paper-validation candidate from a validated TradingView signal",
    dependencies=[_WRITE_LIMIT],
)
async def create_tradingview_candidate(
    signal_id: uuid.UUID,
    payload: TradingViewSignalCreateCandidateRequest,
    tenant: TraderDep,
    service: TradingViewSignalServiceDep,
    session: SessionDep,
    request: Request,
) -> TradingViewSignalCreateCandidateResult:
    result = service.create_candidate(
        signal_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return result
