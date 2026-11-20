"""Pre-trade analysis API (Slice 33)."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import PreTradeAnalysisServiceDep
from app.schemas.pretrade import (
    PreTradeAnalyzeBody,
    PreTradeAnalyzeRequest,
    PreTradeAnalyzeResponse,
)
from app.security.rbac import TraderDep

router = APIRouter(prefix="/pretrade", tags=["pretrade"])


@router.post("/analyze", response_model=PreTradeAnalyzeResponse, summary="Run pre-trade analysis")
def analyze_pretrade(
    body: PreTradeAnalyzeBody,
    tenant: TraderDep,
    service: PreTradeAnalysisServiceDep,
) -> PreTradeAnalyzeResponse:
    payload = PreTradeAnalyzeRequest(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        **body.model_dump(),
    )
    return service.analyze(payload)
