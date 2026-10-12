"""Authenticated application producer/read consumer for on-demand research only."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import CanonicalRuntimeDep, SessionDep, SettingsDep
from app.schemas.trendpulse_screening import (
    TrendPulseScreeningCreate,
    TrendPulseScreeningDetail,
    TrendPulseScreeningPage,
)
from app.security.rbac import ReaderDep, TraderDep
from app.signal_fusion.candidate import Candidate
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseStatus
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService

router = APIRouter(tags=["trendpulse-screening"])


def get_screening_service(session: SessionDep, settings: SettingsDep) -> TrendPulseScreeningService:
    return TrendPulseScreeningService(session, settings)


ServiceDep = Annotated[TrendPulseScreeningService, Depends(get_screening_service)]


@router.post(
    "/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings",
    response_model=TrendPulseScreeningDetail,
)
def screen(
    experiment_id: UUID,
    version_id: UUID,
    body: TrendPulseScreeningCreate,
    tenant: TraderDep,
    service: ServiceDep,
    session: SessionDep,
) -> TrendPulseScreeningDetail:
    result = service.screen(tenant, experiment_id, version_id, body)
    session.commit()
    return result


@router.get(
    "/experiments/{experiment_id}/versions/{version_id}/trendpulse-screenings",
    response_model=TrendPulseScreeningPage,
)
def listing(
    experiment_id: UUID,
    version_id: UUID,
    tenant: ReaderDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    status: TrendPulseStatus | None = None,
) -> TrendPulseScreeningPage:
    return service.list(
        tenant, experiment_id, version_id, limit=limit, offset=offset, status=status
    )


@router.get("/trendpulse-screenings/{record_id}", response_model=TrendPulseScreeningDetail)
def detail(record_id: UUID, tenant: ReaderDep, service: ServiceDep) -> TrendPulseScreeningDetail:
    return service.detail(tenant, record_id)


@router.post("/trendpulse-screenings/{record_id}/candidate", response_model=Candidate)
def canonical_candidate(
    record_id: UUID,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    runtime: CanonicalRuntimeDep,
) -> Candidate:
    from app.strategy_brain.trendpulse_screening.pipeline import TrendPulsePipeline

    candidate, _, _, _ = TrendPulsePipeline(session, settings, runtime).candidate(
        tenant,
        record_id,
        now=runtime.clock.now(),
    )
    session.commit()
    return candidate
