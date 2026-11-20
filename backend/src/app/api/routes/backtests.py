"""Backtest API (Slice 35 / AT-034 WS2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Query
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.dependencies import (
    BacktestJournalServiceDep,
    BacktestServiceDep,
    SessionDep,
    SettingsDep,
)
from app.schemas.backtest import (
    BacktestJournalRequest,
    BacktestJournalResult,
    BacktestRun,
    BacktestVerifyResult,
    PaginatedBacktestTrades,
)
from app.schemas.common import BacktestRunStatus
from app.schemas.strategy_replay import (
    ReplayComparison,
    ReplayComparisonRequest,
    StrategyReplayCreate,
)
from app.security.rbac import TraderDep
from app.services.backtest_service import BacktestService
from app.services.strategy_replay_service import StrategyReplayService

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _execute_backtest_background(
    bind: Engine | Connection,
    run_id: uuid.UUID,
    organization_id: uuid.UUID,
    settings: Settings,
) -> None:
    """Open an independent session and execute one QUEUED run (worker-disabled fallback)."""
    factory = sessionmaker(bind=bind, expire_on_commit=False)
    with factory() as session:
        service = BacktestService(session, settings)
        service.execute_run(run_id, organization_id=organization_id)
        session.commit()


@router.post("/replays", response_model=BacktestRun, status_code=201)
def create_strategy_replay(
    body: StrategyReplayCreate,
    tenant: TraderDep,
    service: BacktestServiceDep,
    session: SessionDep,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
) -> BacktestRun:
    result = StrategyReplayService(session, service).create(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    enqueue_backtest_if_needed(
        result=result,
        session=session,
        settings=settings,
        background_tasks=background_tasks,
    )
    return result


@router.post("/replays/compare", response_model=ReplayComparison)
def compare_strategy_replays(
    body: ReplayComparisonRequest,
    tenant: TraderDep,
    service: BacktestServiceDep,
    session: SessionDep,
) -> ReplayComparison:
    result = StrategyReplayService(session, service).compare(
        body, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    session.commit()
    return result


@router.get("/{backtest_id}", response_model=BacktestRun, summary="Get backtest run with metrics")
def get_backtest(
    backtest_id: uuid.UUID,
    tenant: TraderDep,
    service: BacktestServiceDep,
) -> BacktestRun:
    return service.get(backtest_id, organization_id=tenant.organization_id)


@router.get(
    "/{backtest_id}/trades",
    response_model=PaginatedBacktestTrades,
    summary="List simulated trades for backtest run",
)
def list_backtest_trades(
    backtest_id: uuid.UUID,
    tenant: TraderDep,
    service: BacktestServiceDep,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedBacktestTrades:
    return service.list_trades(
        backtest_id,
        organization_id=tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{backtest_id}/cancel",
    response_model=BacktestRun,
    summary="Cancel a queued or running backtest",
)
def cancel_backtest(
    backtest_id: uuid.UUID,
    tenant: TraderDep,
    service: BacktestServiceDep,
    session: SessionDep,
) -> BacktestRun:
    result = service.cancel(
        backtest_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{backtest_id}/verify",
    response_model=BacktestVerifyResult,
    summary="Re-run frozen config against stored dataset (no mutation)",
)
def verify_backtest(
    backtest_id: uuid.UUID,
    tenant: TraderDep,
    service: BacktestServiceDep,
    session: SessionDep,
) -> BacktestVerifyResult:
    result = service.verify(
        backtest_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{backtest_id}/journal-trades",
    response_model=BacktestJournalResult,
    summary="Bulk-create journal trades from a completed backtest",
)
def journal_backtest_trades(
    backtest_id: uuid.UUID,
    body: BacktestJournalRequest,
    tenant: TraderDep,
    service: BacktestJournalServiceDep,
    session: SessionDep,
) -> BacktestJournalResult:
    result = service.journal_from_request(
        backtest_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


def enqueue_backtest_if_needed(
    *,
    result: BacktestRun,
    session: Session,
    settings: Settings,
    background_tasks: BackgroundTasks,
) -> None:
    """Schedule BackgroundTasks drain when worker is disabled and run stayed QUEUED."""
    if result.status != BacktestRunStatus.QUEUED:
        return
    if settings.worker_enabled:
        return
    background_tasks.add_task(
        _execute_backtest_background,
        session.get_bind(),
        result.id,
        result.organization_id,
        settings,
    )
