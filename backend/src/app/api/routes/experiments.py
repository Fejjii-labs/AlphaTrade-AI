"""Tenant-scoped experiment API. Domain state changes never start runtime execution."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import SessionDep, SettingsDep
from app.experiments.outcome_contract import ExperimentPerformance, NativeOutcome
from app.experiments.outcomes import BloFinExperimentReads
from app.experiments.service import ExperimentService
from app.schemas.experiments import (
    ExperimentApproval,
    ExperimentCreate,
    ExperimentDetail,
    ExperimentPage,
    ExperimentPromotion,
    ExperimentSample,
    ExperimentSampleCreate,
    ExperimentTransition,
    ExperimentVersion,
    ExperimentVersionCreate,
)
from app.schemas.trendpulse_screening import ExperimentResearchTick, TrendPulseScreeningRecord
from app.security.rbac import OwnerDep, ReaderDep, TraderDep

router = APIRouter(prefix="/experiments", tags=["experiments"])


def get_experiment_service(session: SessionDep, settings: SettingsDep) -> ExperimentService:
    # Deliberately no source resolver/worker/exchange client installed by HTTP.
    return ExperimentService(session, settings)


ServiceDep = Annotated[ExperimentService, Depends(get_experiment_service)]


def get_native_reads(
    session: SessionDep, settings: SettingsDep, tenant: ReaderDep
) -> BloFinExperimentReads:
    # Stored, scoped reads only. No producer, provider, dispatcher or sample
    # adapter is installed into the HTTP mutation service above.
    return BloFinExperimentReads(session, settings, tenant)


NativeReadsDep = Annotated[BloFinExperimentReads, Depends(get_native_reads)]


@router.post("/research/tick", response_model=ExperimentResearchTick)
def research_tick(
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=2)] = 2,
) -> ExperimentResearchTick:
    from app.experiments.scheduling import ExperimentResearchScheduler

    result = ExperimentResearchScheduler(session, settings).tick(tenant, limit=limit)
    session.commit()
    return ExperimentResearchTick(
        screenings=[
            TrendPulseScreeningRecord.model_validate(
                item.model_dump(include=set(TrendPulseScreeningRecord.model_fields))
            )
            for item in result
        ]
    )


@router.post("", response_model=ExperimentVersion, status_code=201)
def create(
    body: ExperimentCreate, tenant: TraderDep, service: ServiceDep, session: SessionDep
) -> ExperimentVersion:
    result = service.create(tenant, body)
    session.commit()
    return result


@router.get("", response_model=ExperimentPage)
def listing(
    tenant: ReaderDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExperimentPage:
    return service.list(tenant, limit=limit, offset=offset)


@router.get("/{experiment_id}", response_model=ExperimentDetail)
def detail(experiment_id: UUID, tenant: ReaderDep, service: ServiceDep) -> ExperimentDetail:
    return service.detail(tenant, experiment_id)


@router.post("/{experiment_id}/versions", response_model=ExperimentVersion, status_code=201)
def fork(
    experiment_id: UUID,
    body: ExperimentVersionCreate,
    tenant: TraderDep,
    service: ServiceDep,
    session: SessionDep,
) -> ExperimentVersion:
    result = service.fork(tenant, experiment_id, body)
    session.commit()
    return result


@router.post("/{experiment_id}/versions/{version_id}/transition", response_model=ExperimentVersion)
def transition(
    experiment_id: UUID,
    version_id: UUID,
    body: ExperimentTransition,
    tenant: TraderDep,
    service: ServiceDep,
    session: SessionDep,
) -> ExperimentVersion:
    result = service.transition(tenant, experiment_id, version_id, body)
    session.commit()
    return result


@router.post("/{experiment_id}/versions/{version_id}/approve", response_model=ExperimentVersion)
def approve(
    experiment_id: UUID,
    version_id: UUID,
    body: ExperimentApproval,
    tenant: OwnerDep,
    service: ServiceDep,
    session: SessionDep,
) -> ExperimentVersion:
    result = service.approve(tenant, experiment_id, version_id, body)
    session.commit()
    return result


@router.post(
    "/{experiment_id}/versions/{version_id}/promote",
    response_model=ExperimentVersion,
    status_code=201,
)
def promote(
    experiment_id: UUID,
    version_id: UUID,
    body: ExperimentPromotion,
    tenant: TraderDep,
    service: ServiceDep,
    session: SessionDep,
) -> ExperimentVersion:
    result = service.promote(tenant, experiment_id, version_id, body)
    session.commit()
    return result


@router.post(
    "/{experiment_id}/versions/{version_id}/samples",
    response_model=ExperimentSample,
    status_code=201,
)
def sample(
    experiment_id: UUID,
    version_id: UUID,
    body: ExperimentSampleCreate,
    tenant: TraderDep,
    service: ServiceDep,
    session: SessionDep,
) -> ExperimentSample:
    result = service.record_sample(tenant, experiment_id, version_id, body)
    session.commit()
    return result


@router.get(
    "/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}",
    response_model=NativeOutcome,
)
def native_outcome(
    experiment_id: UUID,
    version_id: UUID,
    source_record_id: str,
    reads: NativeReadsDep,
    variant_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{0,39}$")] = "baseline",
) -> NativeOutcome:
    return reads.outcome(experiment_id, version_id, variant_key, source_record_id)


@router.get(
    "/{experiment_id}/versions/{version_id}/performance", response_model=ExperimentPerformance
)
def native_performance(
    experiment_id: UUID,
    version_id: UUID,
    reads: NativeReadsDep,
    variant_key: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_-]{0,39}$")] = "baseline",
) -> ExperimentPerformance:
    return reads.performance(experiment_id, version_id, variant_key)
