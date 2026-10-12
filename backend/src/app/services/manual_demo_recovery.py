"""One audited manual resolution under the account epoch; never clear global safety."""

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, TradingPolicyError
from app.db.models import (
    ApprovalAuthorization,
    AuditLog,
    ExecutionCommand,
    ExecutionFillFact,
    JournalTrade,
    ManualDemoLifecycleResolution,
    RiskReservation,
    VenueSubmitEffect,
)
from app.providers.exchange.manual_demo_lifecycle import ManualLifecycleObservation
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import AuditEventType, JournalTradeSource, JournalTradeStatus
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
)
from app.schemas.trade_plan import AuthorizationState, TradePlanRevision
from app.services.audit_service import AuditService
from app.services.canonical_serialization import canonical_sha256
from app.services.execution_claim import conservative_reservation
from app.services.execution_fills import adjust_symbol_map, fill_content_hash
from app.services.safety_epoch import SafetyEpochService


def recorded_exit_facts(session: Session, command: ExecutionCommand) -> list[dict[str, Any]]:
    """Read each immutable exit once, including additive native currency receipts.

    Legacy facts omitted currency. A later actual read may append that source;
    historical bytes/hashes stay unchanged and never become assumed USDT.
    """
    rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == command.organization_id,
                AuditLog.user_id == command.user_id,
                AuditLog.resource_type == "manual_demo_test",
                AuditLog.resource_id == str(command.id),
                AuditLog.redacted_metadata["operation"]
                .as_string()
                .in_(("manual_demo_exit_fill", "manual_demo_exit_fill_currency_evidence")),
            )
            .limit(2001)
        )
    )
    if len(rows) > 2000:
        raise NotFoundError("Manual demo exit evidence budget exceeded.")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        fact = row.redacted_metadata["fact"]
        if canonical_sha256(fact) != row.redacted_metadata["content_hash"]:
            raise NotFoundError("Manual demo exit fact integrity failed.")
        prior = by_id.get(fact["identity"])
        if prior is not None and (
            {k: v for k, v in prior.items() if k != "fee_currency"}
            != {k: v for k, v in fact.items() if k != "fee_currency"}
            or (
                "fee_currency" in prior
                and "fee_currency" in fact
                and prior["fee_currency"] != fact["fee_currency"]
            )
        ):
            raise NotFoundError(
                "Manual demo exit currency evidence conflicts with its native fact."
            )
        if prior is None or "fee_currency" in fact:
            by_id[fact["identity"]] = fact
    return list(by_id.values())


def verified_entry_notional(
    session: Session, command: ExecutionCommand, plan: TradePlanRevision
) -> Decimal:
    fills = list(
        session.scalars(
            select(ExecutionFillFact).where(
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.organization_id == command.organization_id,
            )
        )
    )
    exact_notional = Decimal("0")
    for fill in fills:
        proof = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.organization_id == command.organization_id,
                AuditLog.user_id == command.user_id,
                AuditLog.resource_type == "manual_demo_test",
                AuditLog.resource_id == str(command.id),
                AuditLog.action == AuditEventType.POSITION_UPDATED,
                AuditLog.redacted_metadata["fill_identity"].as_string()
                == fill.source_fill_identity,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        if proof is None or "quantity" not in proof.redacted_metadata:
            raise TradingPolicyError(
                "Full precision entry proof is required for reservation release."
            )
        full_quantity = Decimal(proof.redacted_metadata["quantity"])
        full_price = Decimal(proof.redacted_metadata["price"])
        if (
            fill_content_hash(
                receipt_id=str(fill.receipt_id),
                command_id=str(command.id),
                venue_source=fill.venue_source,
                source_fill_identity=fill.source_fill_identity,
                quantity=full_quantity,
                price=full_price,
                unit=fill.unit,
                occurred_at=fill.occurred_at,
            )
            != fill.content_hash
        ):
            raise TradingPolicyError(
                "Entry precision proof does not match its immutable fill hash."
            )
        exact_notional += full_quantity * full_price * plan.instrument_rules.contract_multiplier
    return exact_notional


def record_lifecycle(
    session: Session,
    *,
    command: ExecutionCommand,
    plan: TradePlanRevision,
    observation: ManualLifecycleObservation,
    audit: AuditService,
    native_receipt_hash: str | None = None,
) -> None:
    """Append unique full-precision exit facts, retain all prior identities on replay."""
    prior = {f["identity"]: f for f in recorded_exit_facts(session, command)}
    current = {f["identity"]: f for f in observation.exit_fills}
    # A failed read does not replace previously proven exits with absence.
    for identity, fact in prior.items():
        latest = current.get(identity)
        equivalent = latest == fact or (
            "fee_currency" not in fact
            and latest is not None
            and {k: v for k, v in latest.items() if k != "fee_currency"} == fact
        )
        if (identity in current or not observation.diagnostics) and not equivalent:
            raise TradingPolicyError(
                "Native exit history conflicts or regressed; keep the command "
                "held. No exit identities are merged."
            )
    for fact in observation.exit_fills:
        existing = prior.get(fact["identity"])
        if existing is not None and "fee_currency" in existing:
            continue
        audit.record(
            AuditRecordCreate(
                request_id=str(command.id),
                trace_id=str(command.id),
                event_type=AuditEventType.DEMO_LIFECYCLE_RECONCILED,
                resource_type="manual_demo_test",
                resource_id=str(command.id),
                organization_id=command.organization_id,
                user_id=command.user_id,
                metadata={
                    "operation": "manual_demo_exit_fill_currency_evidence"
                    if existing is not None
                    else "manual_demo_exit_fill",
                    "fact": fact,
                    "fee_evidence_contract": "manual-demo-native-fee/v2",
                    "content_hash": canonical_sha256(fact),
                    "plan_content_hash": plan.content_hash,
                },
            )
        )
    facts = {
        **observation.facts(),
        "plan_content_hash": plan.content_hash,
        "command_id": str(command.id),
    }
    # Unlike economic facts, snapshot time matters to a current-account observation.
    audit.record(
        AuditRecordCreate(
            request_id=str(command.id),
            trace_id=str(command.id),
            event_type=AuditEventType.DEMO_LIFECYCLE_RECONCILED,
            resource_type="manual_demo_test",
            resource_id=str(command.id),
            organization_id=command.organization_id,
            user_id=command.user_id,
            metadata={
                "operation": "manual_demo_lifecycle_observed",
                **facts,
                "observed_at": observation.observed_at.isoformat(),
                "native_receipt_hash": native_receipt_hash,
            },
        )
    )


def project_verified_exits(
    session: Session,
    *,
    command: ExecutionCommand,
    plan: TradePlanRevision,
    observation: ManualLifecycleObservation,
    entry_fees: Decimal | None,
) -> None:
    if not observation.exit_fills or observation.diagnostics:
        return
    trade = session.scalar(
        select(JournalTrade).where(
            JournalTrade.execution_lifecycle_id == command.id,
            JournalTrade.organization_id == command.organization_id,
            JournalTrade.user_id == command.user_id,
            JournalTrade.account_id == command.account_id,
            JournalTrade.source == JournalTradeSource.MANUAL_DEMO_TEST,
            JournalTrade.exchange == "BLOFIN_DEMO",
            JournalTrade.trade_plan_revision_id == plan.revision_id,
        )
    )
    if trade is None:
        raise TradingPolicyError("Exact manual Journal entry is required for native exits.")
    facts = observation.facts()
    trade.fees = (
        entry_fees + Decimal(str(facts["exit_fees"]))
        if entry_fees is not None
        and facts["exit_fees"] is not None
        and facts["exit_fee_currency"] == plan.instrument_rules.settlement_currency
        else None
    )
    # Raw fillPnl has no independently verified fee/funding basis here.
    trade.gross_pnl = trade.funding = trade.net_pnl = None
    if observation.position_status == "closed_verified" and not observation.diagnostics:
        trade.status = JournalTradeStatus.CLOSED
        trade.exit_time = max(
            datetime.fromisoformat(f["occurred_at"]) for f in observation.exit_fills
        )
        trade.exit_price = Decimal(str(facts["exit_price"]))
        trade.exit_reason = "verified_blofin_demo_exit"
    # fillPnl does not establish funding/net result; retain unknown outcome fields.


def resolve_manual_lifecycle(
    session: Session,
    *,
    command: ExecutionCommand,
    plan: TradePlanRevision,
    observation: ManualLifecycleObservation,
    epochs: SafetyEpochService,
    audit: AuditService,
) -> ManualDemoLifecycleResolution:
    reason = observation.resolution_reason
    if (
        not reason
        or observation.diagnostics
        or not observation.account_verified
        or not observation.account_flat
        or not observation.account_idle
    ):
        raise TradingPolicyError(
            observation.recovery_reason,
            details={
                "reason": "manual_demo_resolution_evidence_incomplete",
                "submission": "already_started",
            },
        )
    epochs.lock_epoch(organization_id=command.organization_id, account_id=command.account_id)
    existing = session.scalar(
        select(ManualDemoLifecycleResolution).where(
            ManualDemoLifecycleResolution.command_id == command.id
        )
    )
    if existing:
        return existing
    if (
        plan.schema_version != "ManualDemoTradePlanV1"
        or plan.execution_venue != "BLOFIN_DEMO"
        or plan.content_hash != command.plan_content_hash
    ):
        raise TradingPolicyError("Manual lifecycle lineage mismatch.")
    effect = session.scalar(
        select(VenueSubmitEffect)
        .where(VenueSubmitEffect.command_id == command.id)
        .with_for_update()
    )
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == command.id).with_for_update()
    )
    authorization = session.get(ApprovalAuthorization, command.authorization_id)
    if command.outcome == ExecutionCommandOutcome.ALLOW and (
        effect is None
        or effect.uncertainty
        or authorization is None
        or authorization.state != AuthorizationState.CONSUMED
        or authorization.consumed_by_execution_command_id != command.id
    ):
        raise TradingPolicyError(
            "Uncertain dispatch or missing consumed exact authorization; resolution refused."
        )
    fills = list(
        session.scalars(
            select(ExecutionFillFact).where(
                ExecutionFillFact.command_id == command.id,
                ExecutionFillFact.organization_id == command.organization_id,
            )
        )
    )
    entry_quantity = sum((f.quantity for f in fills), Decimal("0"))
    if reason == "verified_exit":
        if (
            not fills
            or any(f.venue_source != "blofin_demo" for f in fills)
            or sum((Decimal(f["quantity"]) for f in observation.exit_fills), Decimal("0"))
            != entry_quantity
        ):
            raise TradingPolicyError(
                "Exit quantities do not match this command's verified native entry fills."
            )
    elif fills or observation.exit_fills:
        raise TradingPolicyError("An unfilled resolution cannot erase verified fills.")
    exposure = Decimal("0")
    unused = Decimal("0")
    if reservation:
        if (reservation.organization_id, reservation.account_id, reservation.plan_revision_id) != (
            command.organization_id,
            command.account_id,
            command.revision_id,
        ):
            raise TradingPolicyError("Reservation identity mismatch.")
        accounting = epochs.lock_risk_accounting(
            organization_id=command.organization_id,
            account_id=command.account_id,
            user_id=command.user_id,
            exposure_unit=reservation.exposure_unit,
        )
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
        if unused:
            exact_notional = verified_entry_notional(session, command, plan)
            exact_unused = max(
                Decimal("0"), conservative_reservation(plan).pending_notional - exact_notional
            )
            if reservation.release_reason in {
                RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_CANCELLATION,
                RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_EXPIRY,
            }:
                exact_unused = Decimal("0")
            if abs(exact_unused - unused) > Decimal("0.00000001"):
                raise TradingPolicyError(
                    "Reservation remainder differs from its immutable plan and fills."
                )
            # Do not release another hold to compensate for database rounding.
            # Sub-unit dust stays conservatively charged and is explicitly audited.
            unused = min(unused, exact_unused.quantize(Decimal("0.00000001"), rounding=ROUND_FLOOR))
        if (
            accounting.actual_notional < exposure
            or accounting.reserved_notional < unused
            or Decimal(str(accounting.symbol_actual.get(reservation.instrument, "0"))) < exposure
            or Decimal(str(accounting.symbol_reserved.get(reservation.instrument, "0"))) < unused
        ):
            raise TradingPolicyError(
                "Account reservation accounting is inconsistent; scoped release refused."
            )
        accounting.actual_notional -= exposure
        accounting.reserved_notional -= unused
        accounting.symbol_actual = adjust_symbol_map(
            accounting.symbol_actual, reservation.instrument, -exposure
        )
        accounting.symbol_reserved = adjust_symbol_map(
            accounting.symbol_reserved, reservation.instrument, -unused
        )
        if not reservation.converted_trade_slots and unused:
            if (
                accounting.reserved_daily_loss < reservation.daily_loss_allocation
                or accounting.reserved_trade_slots < reservation.daily_trade_allocation
            ):
                raise TradingPolicyError(
                    "Unfilled reservation budget is inconsistent; release refused."
                )
            accounting.reserved_daily_loss -= reservation.daily_loss_allocation
            accounting.reserved_trade_slots -= reservation.daily_trade_allocation
        accounting.version += 1
        reservation.remaining_reserved_notional = Decimal("0")
        reservation.release_state = RiskReservationReleaseState.RELEASED
        reservation.release_reason = (
            RiskReservationReleaseReason.VERIFIED_DEMO_EXIT
            if fills
            else RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_PROVEN_UNSENT
            if reason == "proven_unsent"
            else reservation.release_reason
        )
    elif command.outcome == ExecutionCommandOutcome.ALLOW:
        raise TradingPolicyError("Allowed command reservation is missing; resolution refused.")
    facts = {
        "command_id": str(command.id),
        "account_id": str(command.account_id),
        "revision_id": str(plan.revision_id),
        "plan_content_hash": plan.content_hash,
        "reason": reason,
        **observation.facts(),
    }
    if reason == "verified_exit":
        trade = session.scalar(
            select(JournalTrade).where(
                JournalTrade.execution_lifecycle_id == command.id,
                JournalTrade.organization_id == command.organization_id,
                JournalTrade.user_id == command.user_id,
                JournalTrade.account_id == command.account_id,
                JournalTrade.source == JournalTradeSource.MANUAL_DEMO_TEST,
                JournalTrade.exchange == "BLOFIN_DEMO",
                JournalTrade.trade_plan_revision_id == command.revision_id,
            )
        )
        if trade is None:
            raise TradingPolicyError("Exact manual Journal entry is required.")
        if trade.status != JournalTradeStatus.CLOSED:
            raise TradingPolicyError("Verified exit Journal projection is incomplete.")
    recorded = audit.record(
        AuditRecordCreate(
            request_id=str(command.id),
            trace_id=str(command.id),
            event_type=AuditEventType.DEMO_LIFECYCLE_RECONCILED,
            resource_type="manual_demo_test",
            resource_id=str(command.id),
            organization_id=command.organization_id,
            user_id=command.user_id,
            metadata={
                "operation": "manual_demo_lifecycle_resolved",
                **facts,
                "released_notional": str(exposure),
                "released_unused_reservation": str(unused),
                "global_kill_switch": "unchanged",
                "rounding_remainder": "retained_conservatively_below_one_money_unit",
                "unrelated_holds": "unchanged",
            },
        )
    )
    if recorded is None:
        raise TradingPolicyError("Recovery audit could not be persisted; release refused.")
    result = ManualDemoLifecycleResolution(
        id=uuid5(command.id, "manual-demo-resolution/v1"),
        organization_id=command.organization_id,
        user_id=command.user_id,
        account_id=command.account_id,
        command_id=command.id,
        revision_id=command.revision_id,
        audit_event_id=recorded.event_id,
        reason=reason,
        content_hash=canonical_sha256(facts),
        evidence_payload=facts,
        observed_at=observation.observed_at,
        released_notional=exposure + unused,
    )
    session.add(result)
    if effect:
        effect.reconciliation_disposition = "MANUAL_DEMO_RESOLVED"
    session.flush()
    return result
