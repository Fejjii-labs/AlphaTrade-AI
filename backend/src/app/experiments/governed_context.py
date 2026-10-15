"""Explicit experiment context for the existing governed canonical coordinator.

This is an internal server boundary, not an HTTP execution API or a scheduler.
Selection is explicit; no running experiment is discovered by strategy similarity.
Lifecycle locks share the domain's root/version order. Existing dispatch fences
remain authoritative, including for calls that bypass the coordinator.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ForbiddenError
from app.db.models import AuditLog, ExecutionCommand, Membership
from app.experiments.blofin_source import refuse
from app.experiments.identity import utc
from app.experiments.native_lineage import NativeEntryBinding
from app.experiments.native_producer import ENTRY, ORIGIN, POSITION, BloFinNativeOutcomeProducer
from app.experiments.repository import ExperimentRepository
from app.experiments.service import ExperimentService
from app.schemas.common import MembershipRole
from app.schemas.experiments import ExperimentSource, ExperimentVersion
from app.security.tenant import TenantContext


def _current_trader(session: Session, organization_id: UUID, user_id: UUID) -> Membership:
    membership = session.scalar(
        select(Membership)
        .where(Membership.organization_id == organization_id, Membership.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if membership is None or membership.role not in {MembershipRole.OWNER, MembershipRole.TRADER}:
        raise ForbiddenError("Experiment execution requires current Trader membership.")
    return membership


@dataclass(frozen=True, slots=True)
class GovernedExperimentContext:
    tenant: TenantContext
    experiment_id: UUID
    version_id: UUID
    variant_key: str

    def producer(
        self, session: Session, settings: Settings, now: datetime
    ) -> BloFinNativeOutcomeProducer:
        return BloFinNativeOutcomeProducer(session, settings, self.tenant, clock=lambda: now)

    def require_active(
        self,
        session: Session,
        settings: Settings,
        now: datetime,
        *,
        revision_id: UUID | None = None,
    ) -> ExperimentVersion:
        # Re-read membership and frozen configuration from the scoped database.
        # The supplied IDs never grant authority or select a similar experiment.
        _current_trader(session, self.tenant.organization_id, self.tenant.user_id)
        service = ExperimentService(session, settings, clock=lambda: now)
        service._mutation(self.tenant)
        row = ExperimentRepository(session, self.tenant).version(
            self.experiment_id, self.version_id, lock=True
        )
        version = service.view(row)
        producer = self.producer(session, settings, now)
        if (
            version.configuration.account.source is not ExperimentSource.BLOFIN_DEMO
            or version.state != "running"
            or version.started_at is None
            or version.authorized_until is None
            or not utc(version.started_at) <= now < utc(version.authorized_until)
        ):
            refuse("experiment_sample_window")
        producer.resolver._version(version, now)
        variant = next(
            (item for item in version.configuration.variants if item.key == self.variant_key), None
        )
        if variant is None:
            refuse("experiment_variant_unknown")
        if revision_id is not None:
            plan = producer._canonical_plan(
                version, self.variant_key, revision_id, require_current_validity=False
            ).plan
            limits = version.configuration.risk_limits
            notional = (
                plan.quantity.value
                * plan.instrument_rules.contract_multiplier
                * plan.entry_zone.upper
            )
            if (
                plan.risk_and_exits.maximum_loss.value > limits.max_risk_per_trade
                or notional > limits.max_position_notional
                or notional > limits.max_total_exposure
                or plan.risk_and_exits.leverage > limits.max_leverage
                or plan.risk_and_exits.fee_allowance.value
                + plan.risk_and_exits.slippage_allowance.value
                + plan.risk_and_exits.funding_allowance.value
                > limits.cost_allowance
            ):
                refuse("experiment_plan_risk_envelope")
        return version


def require_registered_experiment_dispatch(
    session: Session, settings: Settings, command: ExecutionCommand, *, now: datetime
) -> None:
    """Enforce durable experiment receipts without changing unowned demo paths.

    Called before dispatch authorization and again immediately before POST, after
    venue preflight. Neither lookup, context validation nor receipt probe does IO.
    Caller owns commit/rollback and the existing send linearization boundary.
    """
    origin_row = session.scalar(
        select(AuditLog).where(
            AuditLog.organization_id == command.organization_id,
            AuditLog.user_id == command.user_id,
            AuditLog.resource_type == "experiment_execution",
            AuditLog.resource_id == str(command.revision_id),
            AuditLog.redacted_metadata["operation"].as_string() == ORIGIN,
        )
    )
    if origin_row is None:
        return
    membership = _current_trader(session, command.organization_id, command.user_id)
    tenant = TenantContext(
        organization_id=command.organization_id,
        user_id=command.user_id,
        email="",
        membership_role=membership.role,
    )
    producer = BloFinNativeOutcomeProducer(session, settings, tenant, clock=lambda: now)
    origin_audit, origin = producer._origin(command)
    context = GovernedExperimentContext(
        tenant, origin.experiment_id, origin.version_id, origin.variant_key
    )
    context.require_active(session, settings, now, revision_id=command.revision_id)
    producer._position(command, origin_audit)
    # Flushed entry evidence is insufficient. Dispatch must observe committed
    # position AND binding receipts even with a Connection-bound caller session.
    receipts = [producer._audit(command.id, operation) for operation in (POSITION, ENTRY)]
    binding_row = receipts[1]
    if binding_row is None or NativeEntryBinding.model_validate(
        producer._body(binding_row)
    ) != producer._binding(origin, command):
        refuse("experiment_entry_binding_invalid")
    with Session(session.get_bind().engine) as probe:
        for receipt in receipts:
            if receipt is None:
                refuse("experiment_entry_binding_not_committed")
            committed = probe.get(AuditLog, receipt.id)
            if committed is None or committed.payload_hash != receipt.payload_hash:
                refuse("experiment_entry_binding_not_committed")
