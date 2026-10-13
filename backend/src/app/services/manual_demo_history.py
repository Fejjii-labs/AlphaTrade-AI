"""Authenticated durable manual command reads; no venue IO or execution authority."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import (
    AuditLog,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionProjection,
    ExecutionReceipt,
    JournalTrade,
    ManualDemoLifecycleResolution,
    RiskReservation,
    TradePlanRevision,
    VenueSubmitEffect,
)
from app.schemas.common import AuditEventType
from app.schemas.execution_protocol import ExecutionCommandOutcome, VenueSubmitEffectState
from app.schemas.manual_demo import (
    ManualDemoAttempt,
    ManualDemoHistory,
    ManualDemoHistoryFilter,
    ManualDemoStatus,
)
from app.schemas.trade_plan import TradePlanRevisionSemantic
from app.security.tenant import TenantContext
from app.services.canonical_serialization import canonical_sha256
from app.services.manual_demo_plan import MANUAL_DEMO_ORIGIN
from app.services.manual_demo_recovery import recorded_exit_facts
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema


def current_native_receipt(session: Session, command: ExecutionCommand) -> AuditLog | None:
    """Latest observed receipt, including a return to an already recorded snapshot."""
    scope = (
        AuditLog.organization_id == command.organization_id,
        AuditLog.user_id == command.user_id,
        AuditLog.resource_type == "manual_demo_test",
        AuditLog.resource_id == str(command.id),
    )
    digest = session.scalar(
        select(AuditLog.redacted_metadata["native_receipt_hash"].as_string())
        .where(
            *scope,
            AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_lifecycle_observed",
            AuditLog.redacted_metadata["native_receipt_hash"].as_string().is_not(None),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    query = select(AuditLog).where(
        *scope,
        AuditLog.redacted_metadata["operation"].as_string() == "manual_demo_native_order_receipt",
    )
    if digest:
        query = query.where(AuditLog.redacted_metadata["receipt_hash"].as_string() == digest)
    return session.scalar(query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1))


def command_query(
    tenant: TenantContext, filters: ManualDemoHistoryFilter
) -> Select[tuple[ExecutionCommand]]:
    query = (
        select(ExecutionCommand)
        .join(TradePlanRevision, TradePlanRevision.id == ExecutionCommand.revision_id)
        .join(ExecutionAccount, ExecutionAccount.id == ExecutionCommand.account_id)
        .outerjoin(VenueSubmitEffect, VenueSubmitEffect.command_id == ExecutionCommand.id)
        .outerjoin(ExecutionReceipt, ExecutionReceipt.command_id == ExecutionCommand.id)
        .outerjoin(ExecutionProjection, ExecutionProjection.receipt_id == ExecutionReceipt.id)
        .where(
            ExecutionCommand.organization_id == tenant.organization_id,
            ExecutionCommand.user_id == tenant.user_id,
            TradePlanRevision.organization_id == tenant.organization_id,
            TradePlanRevision.user_id == tenant.user_id,
            TradePlanRevision.account_id == ExecutionCommand.account_id,
            TradePlanRevision.schema_version == "ManualDemoTradePlanV1",
            TradePlanRevision.plan_authority == MANUAL_DEMO_ORIGIN,
            TradePlanRevision.execution_venue == "BLOFIN_DEMO",
            ExecutionAccount.organization_id == tenant.organization_id,
            ExecutionAccount.user_id == tenant.user_id,
        )
    )
    if filters.command_id:
        query = query.where(ExecutionCommand.id == filters.command_id)
    if filters.account_id:
        query = query.where(ExecutionCommand.account_id == filters.account_id)
    if filters.symbol:
        normalized = filters.symbol.upper().replace("-", "").replace("/", "")
        query = query.where(
            func.replace(TradePlanRevision.execution_instrument, "-", "") == normalized
        )
    if filters.side:
        query = query.where(
            TradePlanRevision.semantic_payload["side"].as_string() == filters.side.value
        )
    if filters.requested_quantity is not None:
        query = query.where(
            TradePlanRevision.semantic_payload["quantity"]["value"].as_string().cast(Numeric)
            == filters.requested_quantity
        )
    # Dispatch authorization is the durable submission start, not fill/Journal time.
    at = func.coalesce(VenueSubmitEffect.dispatch_authorized_at, ExecutionCommand.created_at)
    if filters.since:
        query = query.where(at >= filters.since)
    if filters.until:
        query = query.where(at <= filters.until)
    if filters.submission_status == "blocked":
        query = query.where(
            (ExecutionCommand.outcome == ExecutionCommandOutcome.BLOCKED)
            | (VenueSubmitEffect.state == VenueSubmitEffectState.PROVEN_UNSENT)
        )
    elif filters.submission_status == "submitted":
        query = query.where(
            VenueSubmitEffect.state == VenueSubmitEffectState.SEND_ATTEMPTED,
            VenueSubmitEffect.uncertainty.is_(False),
            VenueSubmitEffect.reconciliation_disposition != "REJECTED",
        )
    elif filters.submission_status == "uncertain":
        query = query.where(VenueSubmitEffect.uncertainty.is_(True))
    elif filters.submission_status == "filled":
        query = query.where(ExecutionProjection.filled_quantity > 0)
    return query


class ManualDemoHistoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, tenant: TenantContext, filters: ManualDemoHistoryFilter) -> ManualDemoHistory:
        query = command_query(tenant, filters)
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        commands = self.session.scalars(
            query.order_by(
                func.coalesce(
                    VenueSubmitEffect.dispatch_authorized_at, ExecutionCommand.created_at
                ).desc(),
                ExecutionCommand.id.desc(),
            )
            .offset(filters.offset)
            .limit(filters.limit)
        )
        return ManualDemoHistory(
            items=tuple(self.project(tenant, command) for command in commands),
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )

    def get(self, tenant: TenantContext, command_id: UUID) -> ManualDemoAttempt:
        command = self.session.scalar(
            command_query(tenant, ManualDemoHistoryFilter(command_id=command_id))
        )
        if command is None:
            raise NotFoundError("Manual demo command not found in your account scope.")
        return self.project(tenant, command)

    def project(self, tenant: TenantContext, command: ExecutionCommand) -> ManualDemoAttempt:
        row = self.session.get(TradePlanRevision, command.revision_id)
        account = self.session.get(ExecutionAccount, command.account_id)
        assert row is not None and account is not None
        plan = trade_plan_revision_to_schema(row)
        if (
            canonical_sha256(TradePlanRevisionSemantic.model_validate(row.semantic_payload))
            != command.plan_content_hash
            or plan.content_hash != command.plan_content_hash
        ):
            raise NotFoundError("Manual demo immutable plan integrity failed.")
        effect = self.session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command.id)
        )
        receipt = self.session.scalar(
            select(ExecutionReceipt).where(ExecutionReceipt.command_id == command.id)
        )
        projection = (
            self.session.scalar(
                select(ExecutionProjection).where(ExecutionProjection.receipt_id == receipt.id)
            )
            if receipt
            else None
        )
        trade = self.session.scalar(
            select(JournalTrade).where(
                JournalTrade.organization_id == tenant.organization_id,
                JournalTrade.user_id == tenant.user_id,
                JournalTrade.execution_lifecycle_id == command.id,
            )
        )
        claims = tuple(
            self.session.scalars(
                command_query(tenant, ManualDemoHistoryFilter(account_id=command.account_id))
                .where(
                    ExecutionCommand.outcome == ExecutionCommandOutcome.ALLOW,
                    ~select(ManualDemoLifecycleResolution.id)
                    .where(ManualDemoLifecycleResolution.command_id == ExecutionCommand.id)
                    .exists(),
                )
                .with_only_columns(ExecutionCommand.id)
                .limit(50)
            )
        )
        reservation = self.session.scalar(
            select(RiskReservation).where(RiskReservation.command_id == command.id)
        )
        resolution = self.session.scalar(
            select(ManualDemoLifecycleResolution).where(
                ManualDemoLifecycleResolution.command_id == command.id
            )
        )

        def audit(operation: str) -> AuditLog | None:
            return self.session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.organization_id == tenant.organization_id,
                    AuditLog.user_id == tenant.user_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == str(command.id),
                    AuditLog.redacted_metadata["operation"].as_string() == operation,
                )
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .limit(1)
            )

        native = current_native_receipt(self.session, command)
        lifecycle = audit("manual_demo_lifecycle_observed")
        failure = audit("manual_demo_reconciliation_failed")
        facts = dict(native.redacted_metadata) if native else {}
        if native:
            digest = facts.pop("receipt_hash", None)
            facts.pop("operation", None)
            if (
                canonical_sha256(facts) != digest
                or facts.get("plan_content_hash") != command.plan_content_hash
            ):
                raise NotFoundError("Manual demo native receipt integrity failed.")
        entry_fee_rows = list(
            self.session.scalars(
                select(AuditLog).where(
                    AuditLog.organization_id == tenant.organization_id,
                    AuditLog.user_id == tenant.user_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == str(command.id),
                    AuditLog.action == AuditEventType.POSITION_UPDATED,
                )
            )
        )
        entry_fees_by_id = {
            a.redacted_metadata["fill_identity"]: Decimal(a.redacted_metadata["fee"])
            for a in entry_fee_rows
            if "fill_identity" in a.redacted_metadata
            and "fee" in a.redacted_metadata
            and a.redacted_metadata.get("fee_evidence_contract") == "manual-demo-native-fee/v2"
            and a.redacted_metadata.get("fee_currency") == plan.instrument_rules.settlement_currency
        }
        entry_fees = (
            sum(entry_fees_by_id.values(), Decimal("0"))
            if entry_fees_by_id and set(entry_fees_by_id) == set(facts.get("fill_identities", []))
            else None
        )
        life = lifecycle.redacted_metadata if lifecycle else {}
        exit_facts = recorded_exit_facts(self.session, command)
        exit_quantity = sum((Decimal(f["quantity"]) for f in exit_facts), Decimal("0"))
        exit_price = (
            sum((Decimal(f["quantity"]) * Decimal(f["price"]) for f in exit_facts), Decimal("0"))
            / exit_quantity
            if exit_quantity
            else None
        )
        exit_fees = (
            sum((Decimal(f["fee"]) for f in exit_facts), Decimal("0"))
            if exit_facts
            and all(
                f.get("fee") is not None
                and f.get("fee_currency") == plan.instrument_rules.settlement_currency
                for f in exit_facts
            )
            else None
        )
        reported_pnl = (
            sum((Decimal(f["fillPnl"]) for f in exit_facts), Decimal("0"))
            if exit_facts and all(f["fillPnl"] is not None for f in exit_facts)
            else None
        )
        observations = [a.created_at for a in (native, lifecycle) if a]
        recorded_at = max(observations) if observations else None
        observed_at = (
            datetime.fromisoformat(life["observed_at"]) if life.get("observed_at") else recorded_at
        )
        failed = failure is not None and (recorded_at is None or failure.created_at >= recorded_at)
        diagnostics = facts.get("diagnostics", []) + life.get("diagnostics", [])
        if failed:
            assert failure is not None
            diagnostics = [failure.redacted_metadata["diagnostic"]]
        filled = projection.filled_quantity if projection else Decimal("0")
        closed_proof = (
            self.session.scalar(
                select(AuditLog.id)
                .where(
                    AuditLog.organization_id == command.organization_id,
                    AuditLog.user_id == command.user_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == str(command.id),
                    AuditLog.redacted_metadata["operation"].as_string()
                    == "manual_demo_lifecycle_observed",
                    AuditLog.redacted_metadata["position_status"].as_string() == "closed_verified",
                    AuditLog.redacted_metadata["plan_content_hash"].as_string()
                    == command.plan_content_hash,
                )
                .limit(1)
            )
            is not None
        )
        remaining = projection.remaining_quantity if projection else plan.quantity.value
        unsent = command.outcome == ExecutionCommandOutcome.BLOCKED or (
            effect is not None and effect.state == VenueSubmitEffectState.PROVEN_UNSENT
        )
        native_state = facts.get("native_state")
        terminal = native_state in {"canceled", "cancelled", "expired", "rejected"}
        execution = (
            "blocked_before_submission"
            if unsent
            else "closed"
            if (resolution and resolution.reason == "verified_exit")
            or (
                trade is not None
                and closed_proof
                and trade.exit_price is not None
                and exit_quantity == filled
                and filled > 0
            )
            else "canceled"
            if native_state in {"canceled", "cancelled", "expired"}
            else "rejected"
            if native_state == "rejected"
            else "filled"
            if filled == plan.quantity.value
            else "partially_filled"
            if filled
            else "submitted"
            if effect
            and effect.state == VenueSubmitEffectState.SEND_ATTEMPTED
            and not effect.uncertainty
            and effect.reconciliation_disposition != "REJECTED"
            else "rejected"
            if effect and effect.reconciliation_disposition == "REJECTED"
            else "submission_uncertain"
        )
        protection = (
            "not_required_closed"
            if execution == "closed"
            else "unverified"
            if failed
            else str(facts.get("protection_status", "unverified"))
        )
        missing = []
        if filled and entry_fees is None:
            differing = any(
                a.redacted_metadata.get("fee_evidence_contract") == "manual-demo-native-fee/v2"
                and a.redacted_metadata.get("fee_currency")
                not in (None, plan.instrument_rules.settlement_currency)
                for a in entry_fee_rows
            )
            missing.append(
                "Native entry fee currency differs from settlement currency; "
                "no conversion is verified."
                if differing
                else "Historical entry fee currency is unverified; fee totals are unavailable."
            )
        if exit_facts and exit_fees is None:
            if any(f.get("fee") is None for f in exit_facts):
                missing.append("Native exit fee is missing; fee totals are unavailable.")
            if any(f.get("fee_currency") is None for f in exit_facts):
                missing.append(
                    "Historical exit fee currency is unverified; fee totals are unavailable."
                )
            if any(
                f.get("fee_currency") not in (None, plan.instrument_rules.settlement_currency)
                for f in exit_facts
            ):
                missing.append(
                    "Native exit fee currency differs from settlement currency; "
                    "no conversion is verified."
                )
        if filled:
            missing.append("Native fee sign convention is unverified.")
        if exit_facts:
            missing.append(
                "Native fillPnl fee/funding basis is unverified; gross/net PnL are unknown."
            )
        if failed:
            missing.append(
                "Latest venue read failed; previously verified facts are retained."
                " Refresh this exact command; do not resubmit."
            )
        if not filled and not unsent and not terminal:
            missing.append(
                "No actual venue fill is verified; submission does not prove a position."
            )
        recovery_reason = str(
            life.get(
                "recovery_reason",
                "Fresh account state and complete native terminal evidence are required.",
            )
        )
        if not resolution:
            missing.append(recovery_reason)
        else:
            recovery_reason = (
                "Only this command's reservation and account claim were resolved. "
                "Global kill switch and unrelated holds are unchanged."
            )
        position = (
            "unknown"
            if failed
            else str(
                life.get(
                    "position_status", "closed_verified" if execution == "closed" else "unknown"
                )
            )
        )
        status = (
            "resolved_" + resolution.reason
            if resolution
            else "reconciliation_unavailable_operator_hold"
            if failed
            else "protection_failed_operator_hold"
            if filled and protection != "verified" and position == "account_position_present"
            else "filled_protected"
            if execution == "filled" and protection == "verified"
            else command.blocked_reason_code or execution
        )
        can_resolve = bool(
            not resolution
            and not failed
            and not life.get("diagnostics")
            and life.get("resolution_eligible")
        )
        result = ManualDemoStatus(
            revision_id=plan.revision_id,
            command_id=command.id,
            client_order_id=effect.client_order_id if effect else "",
            venue_order_id=facts.get("venue_order_id"),
            protection_order_ids=tuple(facts.get("protection_order_ids", [])),
            status=status,
            filled_quantity=filled,
            remaining_quantity=remaining,
            average_fill_price=projection.weighted_price if projection else None,
            fees=entry_fees + (exit_fees or Decimal("0"))
            if entry_fees is not None and (not exit_facts or exit_fees is not None)
            else None,
            protection=protection,
            historical_protection="configured"
            if facts.get("protection_configured")
            else "unverified",
            triggered_protection=str(life.get("triggered_protection", "unverified")),
            protection_diagnostics=tuple(life.get("protection_diagnostics", [])),
            entry_fees=entry_fees,
            gross_pnl=None,
            funding=None,
            net_pnl=None,
            journal_trade_id=trade.id if trade else None,
            missing_evidence=tuple(missing),
            reconciliation_diagnostics=tuple(diagnostics),
            execution_status=execution,
            position_status="closed_verified" if execution == "closed" else position,
            account_status="unknown"
            if failed or not life.get("account_verified", not life.get("diagnostics"))
            else "flat"
            if life.get("account_flat")
            else "positions_present"
            if life.get("position_status") == "account_position_present"
            else "unknown",
            protection_history=tuple(life.get("protection_history", [])),
            exit_fills=tuple(exit_facts),
            observed_at=observed_at,
            reconciliation_freshness="latest_read_failed"
            if failed or life.get("diagnostics")
            else "recorded_observation"
            if observed_at
            else "never_observed",
            exit_quantity=exit_quantity,
            exit_price=exit_price,
            exit_fees=exit_fees,
            venue_reported_fill_pnl=reported_pnl,
            recovery_status="resolved"
            if resolution
            else "eligible"
            if can_resolve
            else "unresolved",
            recovery_reason=recovery_reason,
            account_claim_command_ids=claims,
            reservation_status=reservation.release_state.value
            if reservation
            else "none_blocked"
            if unsent
            else "missing",
            can_reconcile=True,
            can_cancel=bool(
                not unsent
                and not terminal
                and native_state in {"live", "partially_filled"}
                and not failed
                and remaining > 0
            ),
            can_resolve=can_resolve,
        )
        return ManualDemoAttempt(
            command_id=command.id,
            account_id=command.account_id,
            account_name=account.name,
            attempted_at=command.created_at,
            submitted_at=effect.dispatch_authorized_at if effect and not unsent else None,
            symbol=plan.execution_instrument,
            side=plan.side,
            requested_contracts=plan.quantity.value,
            base_quantity=plan.quantity.value * plan.instrument_rules.contract_multiplier,
            stop=plan.risk_and_exits.stop.value,
            target=plan.risk_and_exits.targets[0].price.value,
            content_hash=plan.content_hash,
            submission_outcome=command.outcome.value,
            blocked_reason=command.blocked_reason_code,
            detail_url=f"/execution/manual-demo/{command.id}",
            evidence=result,
        )
