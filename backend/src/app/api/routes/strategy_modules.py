"""Deterministic strategy module evaluation API."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import StrategyServiceDep
from app.schemas.common import StrategyId
from app.schemas.strategy import StrategyEvaluateRequest, StrategyEvaluateResponse
from app.security.rbac import ReaderDep, TraderDep
from app.strategies.base import StrategyEvaluationInput

router = APIRouter(prefix="/strategies", tags=["strategy-modules"])


@router.get("/modules", summary="List registered strategy modules")
def list_strategy_modules(
    service: StrategyServiceDep,
    _tenant: ReaderDep,
) -> list[StrategyId]:
    return service.list_strategy_ids()


@router.post(
    "/evaluate",
    response_model=StrategyEvaluateResponse,
    summary="Research-only strategy module evaluation",
)
def evaluate_strategy(
    body: StrategyEvaluateRequest,
    service: StrategyServiceDep,
    _tenant: TraderDep,
) -> StrategyEvaluateResponse:
    """Evaluate a registered strategy *module* against supplied indicators.

    Research-only. This endpoint is not compiled-policy evaluation, does not
    mint Watcher Candidates, and does not open paper or live trades.
    Automated paper and Watcher evaluation require approved compiled lineage.
    """

    data = StrategyEvaluationInput(
        symbol=body.symbol,
        timeframe=body.timeframe,
        close=body.close,
        volume=body.volume,
        funding_rate=body.funding_rate,
        rsi=body.rsi,
        ema_fast=body.ema_fast,
        ema_slow=body.ema_slow,
        htf_trend=body.htf_trend,
        liquidity_sweep_detected=body.liquidity_sweep_detected,
        momentum_exhausted=body.momentum_exhausted,
        at_confluence_level=body.at_confluence_level,
        green_day_active=body.green_day_active,
        stress_score=body.stress_score,
    )
    signal = service.evaluate(body.strategy_id, data)
    return StrategyEvaluateResponse(strategy_id=body.strategy_id, signal=signal)
