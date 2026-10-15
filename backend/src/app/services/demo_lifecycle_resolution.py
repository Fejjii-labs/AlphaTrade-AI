"""Actual demo close projection and audited, idempotent account exposure release.

Called only by governed venue reconciliation; no caller-supplied close API. Daily
loss allocation and consumed trade counts remain conservative, not replenished.
"""

from decimal import ROUND_FLOOR, Decimal
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.core.operation_policy import PersistenceKind, assert_write_allowed
from app.db.models import (
    ApprovalAuthorization,
    ExecutionCommand,
    ExecutionFillFact,
    GovernedDemoLifecycleResolution,
    JournalLifecycleEvent,
    JournalTrade,
    RiskReservation,
    VenueSubmitEffect,
)
from app.providers.exchange.governed_blofin_exit import VerifiedDemoExit
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.audit import AuditRecordCreate
from app.schemas.canonical_trade_plan import CanonicalTradePlanRevision
from app.schemas.common import (
    AuditEventType,
    JournalLifecycleEventType,
    JournalTradeStatus,
    TradeResult,
)
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
)
from app.schemas.journal_lifecycle import JournalLifecycleEventInput
from app.schemas.trade_plan import AuthorizationState
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import (
    CANONICAL_EXECUTION_SOURCE_SYSTEM,
    project_canonical_execution_event,
)
from app.services.canonical_execution_learning import attribute_canonical_paper_event
from app.services.canonical_paper_execution import _lineage_payload
from app.services.canonical_serialization import canonical_sha256
from app.services.execution_fills import adjust_symbol_map
from app.services.journal_lifecycle_projector import JournalLifecycleProjector
from app.services.safety_epoch import SafetyEpochService


def resolve_verified_demo_exit(
    session: Session,
    *,
    runtime: ProductionCanonicalRuntime,
    settings: Settings,
    epochs: SafetyEpochService,
    command: ExecutionCommand,
    envelope: CanonicalTradePlanRevision,
    evidence: VerifiedDemoExit,
) -> GovernedDemoLifecycleResolution:
    assert_write_allowed(PersistenceKind.EXECUTION)
    plan = envelope.plan
    authorization = session.get(ApprovalAuthorization, command.authorization_id)
    if (
        command.outcome is not ExecutionCommandOutcome.ALLOW
        or authorization is None
        or authorization.state is not AuthorizationState.CONSUMED
        or authorization.consumed_by_execution_command_id != command.id
        or authorization.plan_content_hash != plan.content_hash
        or (
            authorization.organization_id,
            authorization.user_id,
            authorization.account_id,
            authorization.revision_id,
            authorization.execution_venue,
            authorization.execution_instrument,
        )
        != (
            command.organization_id,
            command.user_id,
            command.account_id,
            command.revision_id,
            plan.execution_venue,
            plan.execution_instrument,
        )
    ):
        raise ValidationAppError("Demo exit requires the exact consumed plan authorization.")
    if (
        (
            command.organization_id,
            command.user_id,
            command.account_id,
            command.revision_id,
            command.plan_content_hash,
        )
        != (
            plan.organization_id,
            plan.user_id,
            plan.account_id,
            plan.revision_id,
            plan.content_hash,
        )
        or plan.execution_venue != "BLOFIN_DEMO"
        or plan.execution_policy_version != "governed-blofin-demo/v1"
    ):
        raise ValidationAppError("Demo exit plan lineage mismatch.")
    payload = {
        **evidence.facts(),
        "command_id": str(command.id),
        "revision_id": str(plan.revision_id),
        "plan_content_hash": plan.content_hash,
        "authorization_id": str(command.authorization_id),
    }
    entry_fee_events = list(
        session.scalars(
            select(JournalLifecycleEvent)
            .where(
                JournalLifecycleEvent.organization_id == command.organization_id,
                JournalLifecycleEvent.user_id == command.user_id,
                JournalLifecycleEvent.account_id == command.account_id,
                JournalLifecycleEvent.execution_lifecycle_id == command.id,
                JournalLifecycleEvent.event_type == JournalLifecycleEventType.FILL,
                JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                JournalLifecycleEvent.source_aggregate == "execution-command",
            )
            .limit(101)
        )
    )
    payload["recorded_entry_protection_ids"] = sorted(
        {
            identity
            for event in entry_fee_events
            for identities in [event.payload.get("demo_protection_order_ids")]
            if isinstance(identities, list)
            for identity in identities
            if isinstance(identity, str) and identity
        }
    )
    digest = canonical_sha256(payload)
    epochs.lock_epoch(organization_id=command.organization_id, account_id=command.account_id)
    existing = session.scalar(
        select(GovernedDemoLifecycleResolution).where(
            GovernedDemoLifecycleResolution.command_id == command.id
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise ValidationAppError("Demo exit conflicts with immutable lifecycle resolution.")
        return existing
    effect = session.scalar(
        select(VenueSubmitEffect)
        .where(VenueSubmitEffect.command_id == command.id)
        .with_for_update()
    )
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == command.id).with_for_update()
    )
    fills = list(
        session.scalars(
            select(ExecutionFillFact)
            .where(
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.organization_id == command.organization_id,
                ExecutionFillFact.venue_source == "blofin_demo",
            )
            .limit(101)
        )
    )
    if (
        effect is None
        or effect.uncertainty
        or effect.client_order_id != evidence.entry_client_order_id
        or reservation is None
        or (reservation.organization_id, reservation.account_id)
        != (command.organization_id, command.account_id)
        or reservation.instrument != plan.execution_instrument
        or reservation.contract_multiplier != plan.instrument_rules.contract_multiplier
        or reservation.converted_trade_slots != reservation.daily_trade_allocation
        or not fills
        or len(fills) > 100
        or sorted(f.source_fill_identity for f in fills) != sorted(evidence.entry_fill_identities)
        or sum((f.quantity for f in fills), Decimal("0")) != evidence.entry_quantity
        or sum((f.quantity for f in evidence.fills), Decimal("0")) != evidence.entry_quantity
    ):
        raise ValidationAppError(
            "Demo exit requires exact recorded entry fills and resolved order identity."
        )
    trade = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == command.organization_id,
            JournalTrade.user_id == command.user_id,
            JournalTrade.account_id == command.account_id,
            JournalTrade.execution_lifecycle_id == command.id,
        )
    )
    if trade is None or trade.status is not JournalTradeStatus.OPEN:
        raise ValidationAppError("Demo exit requires its open recorded Journal trade.")
    accounting = epochs.lock_risk_accounting(
        organization_id=command.organization_id,
        account_id=command.account_id,
        user_id=command.user_id,
        exposure_unit=reservation.exposure_unit,
    )
    # Keep any sub-unit rounding remainder charged; never subtract another trade's exposure.
    exposure = sum(
        (
            (f.quantity * f.price * plan.instrument_rules.contract_multiplier).quantize(
                Decimal("0.00000001"), rounding=ROUND_FLOOR
            )
            for f in fills
        ),
        Decimal("0"),
    )
    unused = reservation.remaining_reserved_notional
    if (
        accounting.actual_notional < exposure
        or accounting.reserved_notional < unused
        or Decimal(str(accounting.symbol_actual.get(reservation.instrument, "0"))) < exposure
        or Decimal(str(accounting.symbol_reserved.get(reservation.instrument, "0"))) < unused
    ):
        raise ValidationAppError("Demo exit risk accounting is inconsistent; release refused.")
    close_time = max(f.occurred_at for f in evidence.fills)
    exit_price = (
        sum((f.quantity * f.price for f in evidence.fills), Decimal("0")) / evidence.entry_quantity
    )
    reported_pnl = (
        sum((f.reported_pnl for f in evidence.fills if f.reported_pnl is not None), Decimal("0"))
        if all(f.reported_pnl is not None for f in evidence.fills)
        else None
    )
    # Closure/exposure release is independent of monetary availability. Retain
    # individual reported signed amounts; aggregate only explicitly matching
    # currencies. Never turn absent currency into settlement-currency evidence.
    currency = plan.instrument_rules.settlement_currency
    fee_currency_verified = (
        len(entry_fee_events) == len(fills)
        and {event.source_event_id for event in entry_fee_events} == {str(f.id) for f in fills}
        and all(
            event.payload.get("venue_fill_fee_currency") == currency for event in entry_fee_events
        )
        and all(f.fee is not None and f.fee_currency == currency for f in evidence.fills)
    )
    fees = None
    if fee_currency_verified:
        entry_fees = [Decimal(str(event.payload["venue_fill_fee"])) for event in entry_fee_events]
        if not all(fee.is_finite() for fee in entry_fees):
            raise ValidationAppError("Demo entry fee receipt is invalid.")
        # Editable Journal totals are a projection, not the exchange's fee source.
        fees = sum(entry_fees, Decimal("0")) + sum(
            (f.fee for f in evidence.fills if f.fee is not None), Decimal("0")
        )
    source_id = f"verified-demo-exit:{command.id}"
    close_payload = {
        "exit_time": close_time.isoformat(),
        "exit_price": str(exit_price),
        "exit_reason": "verified_blofin_demo_"
        + "_".join(sorted({f.category for f in evidence.fills})),
        "fees": str(fees) if fees is not None else None,
        "venue_reported_fee_currency": currency if fees is not None else None,
        "fee_convention": None,
        # The wire contract calls fillPnl "profit and loss" without declaring
        # gross/net or funding semantics. Preserve it with its original name.
        "venue_reported_fill_pnl": str(reported_pnl) if reported_pnl is not None else None,
        "gross_pnl": None,
        "funding": None,
        "slippage": None,
        "net_pnl": None,
        "result": TradeResult.OPEN.value,
        "demo_exit_evidence": payload,
        "demo_exit_content_hash": digest,
        "missing_outcome_fields": [
            *(["fees", "fee_currency"] if fees is None else []),
            "fee_convention",
            "fill_pnl_fee_basis",
            "gross_pnl",
            "funding",
            "net_pnl",
            "result",
        ],
        "lineage": _lineage_payload(envelope, command.id, runtime).model_dump(
            mode="json", exclude_none=True
        ),
    }
    event = JournalLifecycleEventInput(
        event_type=JournalLifecycleEventType.CLOSE,
        execution_lifecycle_id=command.id,
        source_system=CANONICAL_EXECUTION_SOURCE_SYSTEM,
        source_aggregate="execution-command",
        source_event_id=source_id,
        source_event_version=1,
        account_id=command.account_id,
        payload=close_payload,
        correlation_id=str(command.correlation_id),
    )
    audit = AuditService(session, strict_mode=True)
    projector = JournalLifecycleProjector(session, audit)
    projection = project_canonical_execution_event(
        projector, event=event, organization_id=command.organization_id, user_id=command.user_id
    )
    attribute_canonical_paper_event(
        session=session,
        projector=projector,
        runtime=runtime,
        settings=settings,
        envelope=envelope,
        event=event,
        projection=projection,
        organization_id=command.organization_id,
        user_id=command.user_id,
    )
    close_event = session.scalar(
        select(JournalLifecycleEvent).where(
            JournalLifecycleEvent.organization_id == command.organization_id,
            JournalLifecycleEvent.execution_lifecycle_id == command.id,
            JournalLifecycleEvent.source_event_id == source_id,
            JournalLifecycleEvent.event_type == JournalLifecycleEventType.CLOSE,
        )
    )
    if close_event is None:
        raise ValidationAppError("Verified demo close event missing.")
    accounting.actual_notional -= exposure
    accounting.reserved_notional -= unused
    accounting.symbol_actual = adjust_symbol_map(
        accounting.symbol_actual, reservation.instrument, -exposure
    )
    accounting.symbol_reserved = adjust_symbol_map(
        accounting.symbol_reserved, reservation.instrument, -unused
    )
    accounting.version = int(accounting.version) + 1
    reservation.remaining_reserved_notional = Decimal("0")
    reservation.release_state = RiskReservationReleaseState.RELEASED
    reservation.release_reason = RiskReservationReleaseReason.VERIFIED_DEMO_EXIT
    reservation.updated_at = evidence.observed_at
    identity = uuid5(command.id, "verified-demo-lifecycle/v1")
    recorded = audit.record(
        AuditRecordCreate(
            request_id=str(identity),
            trace_id=str(command.correlation_id),
            event_type=AuditEventType.DEMO_LIFECYCLE_RECONCILED,
            resource_type="execution_command",
            resource_id=str(command.id),
            organization_id=command.organization_id,
            user_id=command.user_id,
            timestamp=evidence.observed_at,
            metadata={
                "resolution_id": str(identity),
                "evidence_hash": digest,
                "released_notional": str(exposure),
                "released_unused_reservation": str(unused),
                "daily_trade_count_retained": accounting.actual_trade_count,
                "daily_loss_budget": "retained_conservatively",
                "journal_close_event_id": str(close_event.id),
            },
        )
    )
    if recorded is None:
        raise ValidationAppError("Verified demo release audit missing.")
    resolution = GovernedDemoLifecycleResolution(
        id=identity,
        organization_id=command.organization_id,
        user_id=command.user_id,
        account_id=command.account_id,
        command_id=command.id,
        revision_id=command.revision_id,
        journal_close_event_id=close_event.id,
        audit_event_id=recorded.event_id,
        content_hash=digest,
        evidence_payload=payload,
        observed_at=evidence.observed_at,
        released_notional=exposure,
        risk_accounting_version=accounting.version,
    )
    session.add(resolution)
    effect.reconciliation_disposition = "DEMO_CLOSED_RECONCILED"
    session.flush()
    return resolution
