"""Exact sealed experiment enrollment for an existing approved Watcher target.

No new targets, approvals, versions or runtime activation. The demo arm and the
canonical coordinator still recheck current identity, role, risk and dispatch.
"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.execution_credentials import governed_demo_worker_access_requested
from app.db.models import Membership
from app.experiments.governed_context import GovernedExperimentContext
from app.experiments.identity import utc
from app.experiments.models import ExperimentVersionRow
from app.experiments.service import ExperimentService
from app.schemas.common import MembershipRole
from app.schemas.experiments import ExperimentSource
from app.security.tenant import TenantContext
from app.workers.watcher_paper_targets import PaperScanTarget


def select_execution_context(
    session: Session, settings: Settings, target: PaperScanTarget, now: datetime
) -> tuple[GovernedExperimentContext | None, str | None]:
    if not governed_demo_worker_access_requested(settings):
        return None, None
    rows = list(
        session.scalars(
            select(ExperimentVersionRow)
            .where(
                ExperimentVersionRow.organization_id == target.organization_id,
                ExperimentVersionRow.user_id == target.user_id,
                ExperimentVersionRow.execution_account_id
                == UUID(settings.governed_blofin_demo_account_id),
            )
            .limit(33)
        )
    )
    if len(rows) > 32:
        return None, "experiment_execution_selection_budget"
    tenant = TenantContext(
        organization_id=target.organization_id,
        user_id=target.user_id,
        email="",
        membership_role=MembershipRole.VIEWER,
    )
    service = ExperimentService(session, settings, clock=lambda: now)
    enrolled, active = [], []
    for row in rows:
        version = service.view(row)
        config = version.configuration
        if (
            config.account.source is not ExperimentSource.BLOFIN_DEMO
            or config.strategy_id != target.strategy_id
            or target.symbol not in config.symbols
            or target.timeframe not in config.timeframes
            or version.started_at is None
        ):
            continue
        for variant in config.variants:
            if variant.strategy_version_id != target.strategy_version_id:
                continue
            enrolled.append((version, variant))
            if (
                version.state == "running"
                and version.authorized_until is not None
                and utc(version.started_at) <= now < utc(version.authorized_until)
            ):
                active.append((version, variant))
    if not enrolled:
        return None, None  # Ordinary Watcher execution remains separately governed.
    if len(active) != 1:
        return None, "experiment_execution_ambiguous" if active else "experiment_execution_inactive"
    membership = session.scalar(
        select(Membership).where(
            Membership.organization_id == target.organization_id,
            Membership.user_id == target.user_id,
        )
    )
    if membership is None or membership.role not in {MembershipRole.OWNER, MembershipRole.TRADER}:
        return None, "experiment_execution_membership_required"
    version, variant = active[0]
    tenant = replace(tenant, membership_role=membership.role)
    return GovernedExperimentContext(tenant, version.experiment_id, version.id, variant.key), None
