"""Integration-owner wiring: reuse persisted native activity, never collect or execute.

Caller owns the transaction and authentication. The reviewed executor must already
have persisted NativeEntryBinding and NativeExitLineage; this example cannot mint
ownership. Internal simulation needs its own resolver, never this one.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.experiments.outcome_contract import ExperimentPerformance
from app.experiments.outcomes import BloFinExperimentReads
from app.schemas.experiments import ExperimentSample, ExperimentSampleCreate
from app.security.tenant import TenantContext


def admit_native_sample(
    session: Session,
    settings: Settings,
    tenant: TenantContext,
    experiment_id: UUID,
    version_id: UUID,
    request: ExperimentSampleCreate,
    *,
    now: datetime,
) -> ExperimentSample:
    reads = BloFinExperimentReads(session, settings, tenant, clock=lambda: now)
    return reads.record_sample(experiment_id, version_id, request)


def read_native_performance(
    session: Session,
    settings: Settings,
    tenant: TenantContext,
    experiment_id: UUID,
    version_id: UUID,
    variant_key: str,
    *,
    now: datetime,
) -> ExperimentPerformance:
    reads = BloFinExperimentReads(session, settings, tenant, clock=lambda: now)
    return reads.performance(experiment_id, version_id, variant_key)
