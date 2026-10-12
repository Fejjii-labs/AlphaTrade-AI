"""Owner-confirmed manual demo origin using existing claim, fences and venue facts.

No strategy approval or synthetic Candidate. An explicit audited recovery can
resolve only this command's verified completed or definitively unsent lifecycle.
Incomplete evidence retains its account claim; global safety remains unchanged.
"""

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, TradingPolicyError, ValidationAppError
from app.core.execution_credentials import blofin_execution_authorized, manual_demo_access_requested
from app.db.models import (
    AuditLog,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionProjection,
    JournalTrade,
    ManualDemoLifecycleResolution,
    RiskReservation,
    TradeProposal,
    VenueSubmitEffect,
)
from app.db.models import TradePlanRevision as PlanRow
from app.providers.exchange.demo_preflight import (
    failure_diagnostics,
    preflight_category,
    preflight_message,
)
from app.providers.exchange.demo_reconciliation import ORDER, diagnostic_for
from app.providers.exchange.factory import build_blofin_client
from app.providers.exchange.governed_blofin import (
    DemoFill,
    DemoVenueSnapshot,
    GovernedBloFinDemoProvider,
)
from app.providers.exchange.manual_demo_lifecycle import (
    ManualLifecycleObservation,
    observe_lifecycle,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    ProposalStatus,
    RiskSeverity,
    TradeDirection,
)
from app.schemas.execution_protocol import (
    ExecutePaperPlanRequest,
    ExecutionCommandOutcome,
    VenueSubmitEffectState,
)
from app.schemas.manual_demo import (
    DemoReconciliationDiagnostic,
    ManualDemoConfirmation,
    ManualDemoInstrument,
    ManualDemoPreview,
    ManualDemoPreviewRequest,
    ManualDemoStatus,
)
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import (
    AuthorizationChannel,
    AuthorizationDecision,
    EntrySide,
    TradePlanRevision,
    TradePlanRevisionSemantic,
)
from app.security.tenant import TenantContext
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import journal_planned_targets
from app.services.canonical_serialization import canonical_sha256
from app.services.execution_account_service import ExecutionAccountService
from app.services.execution_claim import ExecutionClaimHooks, PaperPlanClaimService
from app.services.execution_fills import fill_content_hash
from app.services.manual_demo_history import ManualDemoHistoryService
from app.services.manual_demo_plan import MANUAL_DEMO_ORIGIN, build_manual_plan
from app.services.manual_demo_policy import validate_manual_demo
from app.services.manual_demo_recovery import (
    project_verified_exits,
    record_lifecycle,
    recorded_exit_facts,
    resolve_manual_lifecycle,
)
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema
from app.services.planned_reward_risk import PlannedRewardRiskError, execution_reward_risk
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.settings_service import RiskSettingsService
from app.services.safety_epoch import SafetyEpochService
from app.services.venue_submit_dispatcher import VenueSubmitDispatcher

logger = structlog.get_logger(__name__)


class ManualDemoService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        provider: GovernedBloFinDemoProvider | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session, self.settings, self.clock = session, settings, clock
        self.provider = provider
        self.audit = AuditService(session, strict_mode=True)
        self.risk_settings = RiskSettingsService(session, self.audit)
        self.epochs = SafetyEpochService(session, settings, self.risk_settings)

    def _scope(self, tenant: TenantContext) -> ExecutionAccount:
        if tenant.membership_role is not MembershipRole.OWNER:
            raise ForbiddenError("Only the authenticated owner can run a manual demo test.")
        if not manual_demo_access_requested(self.settings) or not blofin_execution_authorized(
            self.settings
        ):
            raise TradingPolicyError(
                "Manual demo capability is disarmed or credentials are sealed."
            )
        account = ExecutionAccountService(self.session).status(tenant).account
        if account is None or (
            str(tenant.organization_id),
            str(tenant.user_id),
            str(account.id),
        ) != (
            self.settings.governed_blofin_demo_organization_id,
            self.settings.governed_blofin_demo_user_id,
            self.settings.governed_blofin_demo_account_id,
        ):
            raise TradingPolicyError(
                "Manual demo account scope does not match the configured pins."
            )
        row = self.session.get(ExecutionAccount, account.id)
        assert row is not None
        return row

    def _provider(self) -> GovernedBloFinDemoProvider:
        if self.provider is None:
            self.provider = GovernedBloFinDemoProvider(
                build_blofin_client(self.settings), clock=self.clock
            )
        return self.provider

    def _plan(self, tenant: TenantContext, revision_id: UUID) -> TradePlanRevision:
        account = self._scope(tenant)
        row = self.session.scalar(
            select(PlanRow).where(
                PlanRow.id == revision_id,
                PlanRow.organization_id == tenant.organization_id,
                PlanRow.user_id == tenant.user_id,
                PlanRow.account_id == account.id,
                PlanRow.plan_authority == MANUAL_DEMO_ORIGIN,
            )
        )
        if row is None:
            raise NotFoundError("Manual demo plan not found in your account.")
        plan = trade_plan_revision_to_schema(row)
        semantic = TradePlanRevisionSemantic.model_validate(row.semantic_payload)
        if (
            plan.schema_version != "ManualDemoTradePlanV1"
            or canonical_sha256(semantic) != plan.content_hash
        ):
            raise TradingPolicyError("Manual demo plan integrity failed.")
        return plan

    def _risk(
        self, tenant: TenantContext, plan: TradePlanRevisionSemantic, snapshot: DemoVenueSnapshot
    ) -> None:
        # The venue must prove a flat account. Pending/uncertain local demo
        # reservations still consume capacity, even if the venue has no order yet.
        reserved, loss = self.session.execute(
            select(
                func.coalesce(func.sum(RiskReservation.remaining_reserved_notional), 0),
                func.coalesce(func.sum(RiskReservation.daily_loss_allocation), 0),
            )
            .join(ExecutionCommand, ExecutionCommand.id == RiskReservation.command_id)
            .join(PlanRow, PlanRow.id == ExecutionCommand.revision_id)
            .where(
                RiskReservation.organization_id == tenant.organization_id,
                RiskReservation.account_id == plan.account_id,
                PlanRow.execution_venue == "BLOFIN_DEMO",
                RiskReservation.release_state != "RELEASED",
            )
        ).one()
        if self.epochs.organization_kill_active(tenant.organization_id):
            raise TradingPolicyError(
                "Manual demo is held by the kill switch. Review the hold before continuing.",
                details={"reason": "safety_epoch_blocking", "category": "manual_demo_limits"},
            )
        validate_manual_demo(
            plan, snapshot, now=self.clock(), reserved_notional=reserved, reserved_loss=loss
        )

    def _snapshot(self, *, side: EntrySide, quantity: Decimal | None = None) -> DemoVenueSnapshot:
        try:
            return self._provider().snapshot(
                symbol="BTCUSDT", now=self.clock(), side=side, quantity=quantity
            )
        except Exception as exc:
            diagnostics = failure_diagnostics(exc, stage="snapshot")
            raise TradingPolicyError(
                preflight_message(diagnostics),
                details={"preflight": diagnostics, "category": preflight_category(diagnostics)},
            ) from exc

    def instrument(self, tenant: TenantContext) -> ManualDemoInstrument:
        account_id = self._scope(tenant).id
        self.session.commit()
        snapshot = self._snapshot(side=EntrySide.BUY)
        return ManualDemoInstrument(
            account_id=account_id,
            instrument=snapshot.instrument,
            minimum_quantity=snapshot.minimum,
            maximum_quantity=snapshot.maximum,
            lot_increment=snapshot.lot,
            tick_size=snapshot.tick,
            contract_multiplier=snapshot.multiplier,
            reference_price=snapshot.price,
            observed_at=snapshot.observed_at,
        )

    def preview(
        self, tenant: TenantContext, request: ManualDemoPreviewRequest
    ) -> ManualDemoPreview:
        account_id = self._scope(tenant).id
        self.session.commit()  # No DB lock/transaction across venue reads.
        stage = "provider_initialization"
        try:
            provider = self._provider()
            stage = "snapshot"
            snapshot = provider.snapshot(
                symbol=request.symbol,
                now=self.clock(),
                side=request.side,
                quantity=request.quantity,
            )
        except Exception as exc:
            diagnostics = failure_diagnostics(exc, stage=stage)
            logger.warning(
                "manual_demo_preflight_failed",
                organization_id=str(tenant.organization_id),
                user_id=str(tenant.user_id),
                account_id=str(account_id),
                **diagnostics,
            )
            raise TradingPolicyError(
                preflight_message(diagnostics),
                details={"preflight": diagnostics, "category": preflight_category(diagnostics)},
            ) from exc
        try:
            semantic = build_manual_plan(
                request,
                snapshot,
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                account_id=account_id,
                now=self.clock(),
            )
        except PlannedRewardRiskError as exc:
            raise TradingPolicyError(
                f"Manual demo geometry: {exc}",
                details={"reason": exc.reason, "category": "manual_demo_geometry"},
            ) from exc
        except ValueError as exc:
            raise TradingPolicyError(
                str(exc), details={"category": "exchange_constraints"}
            ) from exc
        reward_risk = execution_reward_risk(semantic)
        try:
            self._risk(tenant, semantic, snapshot)
        except TradingPolicyError as exc:
            raise TradingPolicyError(
                exc.message,
                details={**exc.details, "gross_reward_risk": str(reward_risk.ratio)},
            ) from exc
        root = TradeProposal(
            id=semantic.plan_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            strategy_id=None,
            symbol=request.symbol,
            timeframe="manual",
            plan_root_kind=MANUAL_DEMO_ORIGIN,
            direction=TradeDirection.LONG if request.side.value == "BUY" else TradeDirection.SHORT,
            entry_price=snapshot.price,
            entry_low=semantic.entry_zone.lower,
            entry_high=semantic.entry_zone.upper,
            position_size=request.quantity * snapshot.multiplier,
            leverage=Decimal("1"),
            stop_loss=request.stop,
            take_profits=[str(request.target)],
            invalidation="Explicit owner stop",
            confidence=0,
            risk_level=RiskSeverity.LOW,
            rationale="Manual demo test; no detected strategy",
            status=ProposalStatus.DRAFT,
            approval_required=True,
        )
        self.session.add(root)
        self.session.flush()
        row = PlanRow(
            id=semantic.revision_id,
            plan_id=semantic.plan_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            account_id=account_id,
            exchange_account_id=None,
            schema_version=semantic.schema_version,
            operation=semantic.operation,
            strategy_version_id=None,
            setup_definition_id=None,
            candidate_id=None,
            canonical_candidate_id=None,
            compiled_setup_definition_id=None,
            plan_authority=MANUAL_DEMO_ORIGIN,
            expected_account_mode=semantic.expected_account_mode,
            permission_attestation_id=semantic.permission_attestation_id,
            permission_attestation_version=semantic.permission_attestation_version,
            execution_venue=semantic.execution_venue,
            execution_instrument=semantic.execution_instrument,
            execution_policy_version=semantic.execution_policy_version,
            valid_from=semantic.valid_from,
            valid_until=semantic.valid_until,
            semantic_payload=semantic.model_dump(mode="json"),
            correlation_id=uuid4(),
            content_hash=canonical_sha256(semantic),
            presentation_metadata={"display_title": "Manual demo test"},
        )
        self.session.add(row)
        self.session.flush()
        root.latest_plan_revision_id = row.id
        self._audit(
            tenant,
            AuditEventType.TRADE_PROPOSAL_CREATED,
            row.id,
            "manual_demo_preview",
            {
                "plan_content_hash": row.content_hash,
                "origin": MANUAL_DEMO_ORIGIN,
                "venue": "BLOFIN_DEMO",
            },
        )
        self.session.commit()
        return ManualDemoPreview(
            account_id=account_id,
            revision_id=row.id,
            content_hash=row.content_hash,
            instrument=semantic.execution_instrument,
            side=request.side,
            quantity=request.quantity,
            base_quantity=root.position_size,
            reference_price=snapshot.price,
            entry_lower=semantic.entry_zone.lower,
            entry_upper=semantic.entry_zone.upper,
            stop=request.stop,
            target=request.target,
            maximum_planned_loss=semantic.risk_and_exits.maximum_loss.value,
            gross_reward_risk=reward_risk.ratio,
            valid_until=semantic.valid_until,
        )

    def confirm(
        self, tenant: TenantContext, confirmation: ManualDemoConfirmation
    ) -> ManualDemoStatus:
        plan = self._plan(tenant, confirmation.revision_id)
        if confirmation.confirm is not True or confirmation.label != "manual demo test":
            raise TradingPolicyError("Explicit manual demo test confirmation is required.")
        if confirmation.content_hash != plan.content_hash:
            raise TradingPolicyError("Confirmation must match the exact preview hash.")
        existing_query = select(ExecutionCommand).where(
            ExecutionCommand.organization_id == tenant.organization_id,
            ExecutionCommand.user_id == tenant.user_id,
            ExecutionCommand.account_id == plan.account_id,
            ExecutionCommand.revision_id == plan.revision_id,
        )
        existing = self.session.scalar(existing_query)
        if existing is not None:
            command_id = existing.id
            self.session.commit()
            return self.reconcile(tenant, command_id)
        self.session.commit()
        if self.clock() >= plan.valid_until:
            raise ValidationAppError(
                "Manual demo plan expired. Create and confirm a fresh preview.",
                details={"submission": "not_started"},
            )
        try:
            snapshot = self._snapshot(side=plan.side, quantity=plan.quantity.value)
        except TradingPolicyError as exc:
            # A concurrent exact confirmation may have submitted while we read.
            existing = self.session.scalar(existing_query)
            if existing is None:
                raise TradingPolicyError(
                    exc.message, details={**exc.details, "submission": "not_started"}
                ) from exc
            command_id = existing.id
            self.session.commit()
            return self.reconcile(tenant, command_id)
        # Serialize duplicate owner confirmations on this immutable revision.
        # The shared claim service then locks idempotency → account epoch → risk,
        # retaining the same lock order as automatic strategy claims.
        self.session.scalar(
            select(PlanRow.id)
            .where(
                PlanRow.id == plan.revision_id,
                PlanRow.organization_id == tenant.organization_id,
                PlanRow.user_id == tenant.user_id,
            )
            .with_for_update()
        )
        existing = self.session.scalar(
            select(ExecutionCommand).where(
                ExecutionCommand.organization_id == tenant.organization_id,
                ExecutionCommand.user_id == tenant.user_id,
                ExecutionCommand.account_id == plan.account_id,
                ExecutionCommand.revision_id == plan.revision_id,
            )
        )
        if existing is not None:
            command_id = existing.id
            self.session.commit()
            return self.reconcile(tenant, command_id)  # Never resend a replay/restarted command.
        try:
            self._risk(tenant, plan, snapshot)
        except TradingPolicyError as exc:
            raise TradingPolicyError(
                exc.message, details={**exc.details, "submission": "not_started"}
            ) from exc
        approval = ApprovalService(self.session, self.audit, clock=self.clock)
        pending = approval.create_for_plan_revision(
            revision_id=plan.revision_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            approval_reason="Owner confirmed manual demo test exact preview",
        )
        authorization = approval.issue_authorization(
            approval_id=pending.id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            channel=AuthorizationChannel.WEB,
            manual_demo_confirmation_hash=confirmation.content_hash,
        )
        self._audit(
            tenant,
            AuditEventType.APPROVAL_DECISION,
            plan.revision_id,
            "manual_demo_confirmed",
            {
                "origin": MANUAL_DEMO_ORIGIN,
                "plan_content_hash": plan.content_hash,
                "authorization_id": str(authorization.authorization_id),
            },
        )
        result = PaperPlanClaimService(
            self.session,
            self.settings,
            self.epochs,
            clock=self.clock,
            hooks=ExecutionClaimHooks(
                manual_demo_capacity=lambda **kw: self._revalidate(tenant, kw["plan"], snapshot)
            ),
        ).claim(
            ExecutePaperPlanRequest(
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                account_id=plan.account_id,
                revision_id=plan.revision_id,
                authorization_id=authorization.authorization_id,
                idempotency_key=f"manual-demo:{plan.revision_id}",
            )
        )
        command_id = result.command_id
        if snapshot.native_account_uid is not None:
            account_evidence = {
                "native_account_uid": snapshot.native_account_uid,
                "execution_account_id": str(plan.account_id),
                "environment": "demo",
                "plan_content_hash": plan.content_hash,
            }
            self._audit(
                tenant,
                AuditEventType.APPROVAL_DECISION,
                command_id,
                "manual_demo_execution_account_verified",
                {**account_evidence, "evidence_hash": canonical_sha256(account_evidence)},
            )
        self.session.commit()  # Durable exact authority and reservation before provider IO.
        if result.outcome is ExecutionCommandOutcome.ALLOW:
            dispatch = self._dispatcher()
            owner = f"manual-demo:{command_id}"
            effect = dispatch.lease_effect(command_id=command_id, owner=owner)
            fence = int(effect.fencing_token)
            effect = dispatch.authorize_dispatch(
                command_id=command_id, owner=owner, fencing_token=fence
            )
            if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
                dispatch.attempt_governed_demo_send(
                    command_id=command_id,
                    owner=owner,
                    fencing_token=fence,
                    provider=self._provider(),
                    settings=self.settings,
                    plan=plan,
                )
            self.session.commit()
        return self.reconcile(tenant, command_id)

    def _revalidate(
        self, tenant: TenantContext, plan: TradePlanRevision, snapshot: DemoVenueSnapshot
    ) -> str | None:
        try:
            self._scope(tenant)
            self._risk(tenant, plan, snapshot)
        except TradingPolicyError as exc:
            return str(exc.details.get("reason", "manual_demo_risk_or_scope_refused"))
        return None

    def _dispatcher(self) -> VenueSubmitDispatcher:
        return VenueSubmitDispatcher(self.session, self.epochs, clock=self.clock)

    def _command(
        self, tenant: TenantContext, command_id: UUID
    ) -> tuple[ExecutionCommand, TradePlanRevision, VenueSubmitEffect | None]:
        self._scope(tenant)
        command = self.session.scalar(
            select(ExecutionCommand).where(
                ExecutionCommand.id == command_id,
                ExecutionCommand.organization_id == tenant.organization_id,
                ExecutionCommand.user_id == tenant.user_id,
            )
        )
        if command is None:
            raise NotFoundError("Manual demo command not found.")
        plan = self._plan(tenant, command.revision_id)
        if command.account_id != plan.account_id or command.plan_content_hash != plan.content_hash:
            raise TradingPolicyError("Manual demo command lineage failed.")
        effect = self.session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command.id)
        )
        return command, plan, effect

    def reconcile(self, tenant: TenantContext, command_id: UUID) -> ManualDemoStatus:
        command, plan, effect = self._command(tenant, command_id)
        resolved = self.session.scalar(
            select(ManualDemoLifecycleResolution).where(
                ManualDemoLifecycleResolution.command_id == command_id
            )
        )
        status = command.blocked_reason_code or "not_sent"
        protection = "unverified"
        evidence = None
        diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
        if effect is not None and effect.state not in {
            VenueSubmitEffectState.CREATED,
            VenueSubmitEffectState.LEASED,
            VenueSubmitEffectState.PROVEN_UNSENT,
        }:
            client_id = effect.client_order_id
            self.session.commit()
            try:
                evidence = self._provider().reconcile(plan=plan, client_order_id=client_id)
            except Exception as exc:
                status = "reconciliation_unavailable_operator_hold"
                diagnostic = diagnostic_for(exc, stage="order_lookup", endpoint=ORDER)
                diagnostics = (diagnostic,)
                logger.warning(
                    "manual_demo_reconciliation_failed",
                    organization_id=str(tenant.organization_id),
                    account_id=str(plan.account_id),
                    command_id=str(command_id),
                    **diagnostic.model_dump(exclude_none=True),
                )
                self._audit(
                    tenant,
                    AuditEventType.TOOL_FAILED,
                    command_id,
                    "manual_demo_reconciliation_failed",
                    {
                        "origin": MANUAL_DEMO_ORIGIN,
                        "diagnostic": diagnostic.model_dump(exclude_none=True),
                    },
                )
                self.session.commit()
            else:
                status = "ambiguous_operator_hold" if evidence is None else evidence.status
        locally_unsent = command.outcome.value == "BLOCKED" or (
            effect is not None
            and effect.state
            in {
                VenueSubmitEffectState.CREATED,
                VenueSubmitEffectState.LEASED,
                VenueSubmitEffectState.PROVEN_UNSENT,
            }
            and effect.dispatch_authorized_at is None
            and not effect.uncertainty
        )
        observation = None
        prior_configuration = False
        if not diagnostics:
            self.session.commit()
            prior_protection_ids: set[str] = set()
            if evidence is not None:
                receipts = self.session.scalars(
                    select(AuditLog).where(
                        AuditLog.organization_id == tenant.organization_id,
                        AuditLog.user_id == tenant.user_id,
                        AuditLog.resource_type == "manual_demo_test",
                        AuditLog.resource_id == str(command_id),
                        AuditLog.redacted_metadata["venue_order_id"].as_string()
                        == evidence.order_id,
                        AuditLog.redacted_metadata["operation"].as_string()
                        == "manual_demo_native_order_receipt",
                    )
                )
                for prior in receipts:
                    prior_facts = dict(prior.redacted_metadata)
                    prior_digest = prior_facts.pop("receipt_hash", None)
                    prior_facts.pop("operation", None)
                    if (
                        canonical_sha256(prior_facts) != prior_digest
                        or prior_facts.get("plan_content_hash") != plan.content_hash
                        or prior_facts.get("client_order_id") != evidence.client_order_id
                        or prior_facts.get("instrument") != plan.execution_instrument
                    ):
                        raise TradingPolicyError("Stored protection receipt lineage failed.")
                    prior_configuration |= (
                        prior.redacted_metadata.get("protection_status") == "verified"
                        or prior.redacted_metadata.get("protection_configured") is True
                    )
                    prior_protection_ids.update(
                        prior.redacted_metadata.get("protection_order_ids", [])
                    )
                    if prior.redacted_metadata.get("native_tpsl_id"):
                        prior_protection_ids.add(prior.redacted_metadata["native_tpsl_id"])
            observation = observe_lifecycle(
                self._provider(),
                plan=plan,
                entry=evidence,
                proven_unsent=locally_unsent,
                known_protection_ids=tuple(sorted(prior_protection_ids)),
            )
            self.epochs.lock_epoch(
                organization_id=tenant.organization_id, account_id=plan.account_id
            )
        if evidence is not None:
            diagnostics = evidence.diagnostics
            self.epochs.lock_epoch(
                organization_id=tenant.organization_id, account_id=plan.account_id
            )
            prior_order_ids = set(
                self.session.scalars(
                    select(AuditLog.redacted_metadata["venue_order_id"].as_string()).where(
                        AuditLog.organization_id == tenant.organization_id,
                        AuditLog.user_id == tenant.user_id,
                        AuditLog.resource_type == "manual_demo_test",
                        AuditLog.resource_id == str(command_id),
                        AuditLog.action == AuditEventType.EXCHANGE_DEMO_ORDER_CREATED,
                    )
                )
            ) - {None}
            prior_fill_ids = set(
                self.session.scalars(
                    select(ExecutionFillFact.source_fill_identity).where(
                        ExecutionFillFact.organization_id == tenant.organization_id,
                        ExecutionFillFact.command_id == command_id,
                    )
                )
            )
            if (
                prior_order_ids and prior_order_ids != {evidence.order_id}
            ) or not prior_fill_ids.issubset({f.identity for f in evidence.fills}):
                raise TradingPolicyError(
                    "Native reconciliation conflicts with this command's stored order/fills; "
                    "operator review required. No identities are merged.",
                    details={
                        "reason": "native_order_or_fill_history_conflict",
                        "submission": "already_started",
                    },
                )
            self._dispatcher().record_demo_order(
                command_id=command_id, rejected=evidence.status == "rejected" and not evidence.fills
            )
            receipt = {
                "origin": MANUAL_DEMO_ORIGIN,
                "venue_order_id": evidence.order_id,
                "client_order_id": evidence.client_order_id,
                "native_state": evidence.status,
                "fill_identities": [f.identity for f in evidence.fills],
                "protection_status": evidence.protection_status,
                "protection_configured": evidence.protection_configured
                or evidence.protected
                or prior_configuration,
                "protection_order_ids": list(evidence.protection_order_ids),
                "instrument": plan.execution_instrument,
                "planned_stop": str(plan.risk_and_exits.stop.value),
                "planned_target": str(plan.risk_and_exits.targets[0].price.value),
                "filled_quantity": str(sum((f.quantity for f in evidence.fills), Decimal("0"))),
                "plan_content_hash": plan.content_hash,
                "native_tpsl_id": evidence.native_tpsl_id,
                "diagnostics": [d.model_dump(exclude_none=True) for d in diagnostics],
            }
            if evidence.native_account_uid is not None:
                receipt["native_account_uid"] = evidence.native_account_uid
                receipt["execution_account_id"] = str(command.account_id)
                receipt["environment"] = "demo"
            receipt_hash = canonical_sha256(receipt)
            prior_receipt = self.session.scalar(
                select(AuditLog.id).where(
                    AuditLog.organization_id == tenant.organization_id,
                    AuditLog.user_id == tenant.user_id,
                    AuditLog.resource_id == str(command_id),
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.redacted_metadata["receipt_hash"].as_string() == receipt_hash,
                )
            )
            if prior_receipt is None:
                self._audit(
                    tenant,
                    AuditEventType.EXCHANGE_DEMO_ORDER_CREATED,
                    command_id,
                    "manual_demo_native_order_receipt",
                    {"receipt_hash": receipt_hash, **receipt},
                )
            if observation:
                record_lifecycle(
                    self.session,
                    command=command,
                    plan=plan,
                    observation=observation,
                    audit=self.audit,
                    native_receipt_hash=receipt_hash,
                )
            fees = (
                sum((f.fee for f in evidence.fills), Decimal("0"))
                if all(
                    f.fee_currency == plan.instrument_rules.settlement_currency
                    for f in evidence.fills
                )
                else None
            )
            for fact in evidence.fills:
                quantity, price = self._replay_representation(tenant, command_id, fact)
                fill = self._dispatcher().apply_unique_fill(
                    command_id=command_id,
                    fill_quantity=quantity,
                    fill_price=price,
                    source_identity=fact.identity,
                    occurred_at=fact.occurred_at,
                    venue_source="blofin_demo",
                )
                prior_fee = None
                if fill.replayed:
                    prior_fee = self.session.scalar(
                        select(AuditLog)
                        .where(
                            AuditLog.organization_id == tenant.organization_id,
                            AuditLog.user_id == tenant.user_id,
                            AuditLog.resource_type == "manual_demo_test",
                            AuditLog.resource_id == str(command_id),
                            AuditLog.action == AuditEventType.POSITION_UPDATED,
                            AuditLog.redacted_metadata["fill_identity"].as_string()
                            == fact.identity,
                        )
                        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                        .limit(1)
                    )
                    if prior_fee is None or Decimal(prior_fee.redacted_metadata["fee"]) != fact.fee:
                        raise TradingPolicyError(
                            "Verified manual demo fill fee conflicts with its stored receipt; "
                            "operator review required."
                        )
                    if (
                        prior_fee.redacted_metadata.get("fee_evidence_contract")
                        == "manual-demo-native-fee/v2"
                        and prior_fee.redacted_metadata.get("fee_currency") != fact.fee_currency
                    ):
                        raise TradingPolicyError(
                            "Native manual demo fee currency conflicts with its recorded receipt; "
                            "operator review required."
                        )
                if not fill.replayed or (
                    prior_fee is not None
                    and (
                        "quantity" not in prior_fee.redacted_metadata
                        or prior_fee.redacted_metadata.get("fee_evidence_contract")
                        != "manual-demo-native-fee/v2"
                    )
                ):
                    self._audit(
                        tenant,
                        AuditEventType.POSITION_UPDATED,
                        command_id,
                        "manual_demo_fill_representation_verified"
                        if fill.replayed
                        else "manual_demo_actual_fill",
                        {
                            "fill_identity": fact.identity,
                            "fee": str(fact.fee),
                            "quantity": str(fact.quantity),
                            "price": str(fact.price),
                            "fee_currency": fact.fee_currency,
                            "fee_evidence_contract": "manual-demo-native-fee/v2",
                            "protection": evidence.protection_status,
                        },
                    )
            protection = evidence.protection_status
            self._journal(tenant, command, plan, evidence.fills, fees, protection)
            if observation and evidence.fills:
                project_verified_exits(
                    self.session,
                    command=command,
                    plan=plan,
                    observation=observation,
                    entry_fees=fees,
                )
            if evidence.status in {"canceled", "cancelled", "expired"}:
                self._dispatcher().record_demo_terminal(command_id=command_id)
            recorded_closed = (
                ManualDemoHistoryService(self.session)
                .get(tenant, command_id)
                .evidence.execution_status
                == "closed"
            )
            if (
                evidence.fills
                and not evidence.protected
                and not resolved
                and not recorded_closed
                and observation is not None
                and observation.account_verified
                and not observation.account_flat
            ):
                if not self.epochs.organization_kill_active(tenant.organization_id):
                    KillSwitchService(self.session, self.audit, self.settings).activate(
                        organization_id=tenant.organization_id,
                        actor_user_id=tenant.user_id,
                        payload=KillSwitchMutationRequest(
                            confirm=True,
                            reason=(
                                "Manual demo protection is not verified; operator action required"
                            ),
                        ),
                    )
                status = "protection_failed_operator_hold"
            elif evidence.fills and evidence.protected:
                status = (
                    "filled_protected"
                    if sum((f.quantity for f in evidence.fills), Decimal("0"))
                    == plan.quantity.value
                    else "partial_fill_protected_operator_hold"
                )
            elif evidence.fills:
                status = (
                    "closed_verified" if recorded_closed else "filled_exit_unverified_operator_hold"
                )
            if not resolved and any(
                not plan.entry_zone.lower <= f.price <= plan.entry_zone.upper
                for f in evidence.fills
            ):
                if (
                    observation is not None
                    and observation.account_verified
                    and not observation.account_flat
                    and not self.epochs.organization_kill_active(tenant.organization_id)
                ):
                    KillSwitchService(self.session, self.audit, self.settings).activate(
                        organization_id=tenant.organization_id,
                        actor_user_id=tenant.user_id,
                        payload=KillSwitchMutationRequest(
                            confirm=True,
                            reason="Manual demo fill exceeded the entry range; operator review",
                        ),
                    )
                status = "actual_fill_outside_plan_operator_hold"
            self.session.commit()
        elif observation:
            record_lifecycle(
                self.session, command=command, plan=plan, observation=observation, audit=self.audit
            )
        projection = self.session.scalar(
            select(ExecutionProjection)
            .join(VenueSubmitEffect, VenueSubmitEffect.receipt_id == ExecutionProjection.receipt_id)
            .where(VenueSubmitEffect.command_id == command_id)
        )
        trade = self.session.scalar(
            select(JournalTrade).where(
                JournalTrade.organization_id == tenant.organization_id,
                JournalTrade.user_id == tenant.user_id,
                JournalTrade.execution_lifecycle_id == command_id,
            )
        )
        self.session.commit()
        durable = ManualDemoHistoryService(self.session).get(tenant, command_id).evidence
        missing = []
        if durable.recovery_status != "resolved":
            missing.append(
                "Account claim remains held until explicit evidence-backed "
                "recovery; global safety is unchanged."
            )
        cancel_requested = self.session.scalar(
            select(AuditLog.id).where(
                AuditLog.organization_id == tenant.organization_id,
                AuditLog.user_id == tenant.user_id,
                AuditLog.resource_id == str(command_id),
                AuditLog.action == AuditEventType.MANUAL_DEMO_CANCEL_REQUESTED,
            )
        )
        if cancel_requested and (
            evidence is None
            or evidence.status not in {"canceled", "cancelled", "expired", "filled"}
        ):
            missing.append(
                "Cancellation requested; terminal cancellation is not verified. Do not retry."
            )
        if evidence is None and projection is not None and projection.filled_quantity:
            missing.append(
                "Previously verified fills are retained; the current native order and protection "
                "read is unavailable. Reconcile the same command."
            )
        elif evidence is None or not evidence.fills:
            missing.append("No actual venue fill has been verified.")
        if durable.protection not in {"verified", "not_required_closed"}:
            missing.append("Stop and target protection are not verified.")
        if diagnostics:
            missing.append(
                "Native demo evidence read failed. Check the reported stage, endpoint and reason "
                "against this order in BloFin demo, then refresh this SAME command. "
                "Do not resubmit or clear the operator hold."
            )
        return ManualDemoStatus(
            revision_id=plan.revision_id,
            command_id=command_id,
            client_order_id=effect.client_order_id if effect else "",
            venue_order_id=evidence.order_id if evidence else None,
            protection_order_ids=evidence.protection_order_ids if evidence else (),
            status=durable.status if resolved else status,
            filled_quantity=projection.filled_quantity if projection else Decimal("0"),
            remaining_quantity=projection.remaining_quantity if projection else plan.quantity.value,
            average_fill_price=projection.weighted_price if projection else None,
            fees=durable.fees,
            protection=durable.protection,
            historical_protection=durable.historical_protection,
            triggered_protection=durable.triggered_protection,
            protection_diagnostics=durable.protection_diagnostics,
            entry_fees=durable.entry_fees,
            gross_pnl=durable.gross_pnl,
            funding=durable.funding,
            net_pnl=durable.net_pnl,
            journal_trade_id=trade.id if trade else None,
            missing_evidence=tuple(dict.fromkeys([*missing, *durable.missing_evidence])),
            reconciliation_diagnostics=diagnostics
            + (observation.diagnostics if observation else ()),
            execution_status=durable.execution_status,
            position_status=durable.position_status,
            account_status=durable.account_status,
            protection_history=durable.protection_history,
            exit_fills=durable.exit_fills,
            observed_at=durable.observed_at,
            reconciliation_freshness=durable.reconciliation_freshness,
            exit_quantity=durable.exit_quantity,
            exit_price=durable.exit_price,
            exit_fees=durable.exit_fees,
            venue_reported_fill_pnl=durable.venue_reported_fill_pnl,
            recovery_status=durable.recovery_status,
            recovery_reason=durable.recovery_reason,
            account_claim_command_ids=durable.account_claim_command_ids,
            reservation_status=durable.reservation_status,
            can_reconcile=durable.can_reconcile,
            can_cancel=durable.can_cancel,
            can_resolve=durable.can_resolve,
        )

    def resolve(self, tenant: TenantContext, command_id: UUID) -> ManualDemoStatus:
        """Explicit audited local recovery; refresh proof, never change venue state."""
        command, plan, effect = self._command(tenant, command_id)
        existing = self.session.scalar(
            select(ManualDemoLifecycleResolution).where(
                ManualDemoLifecycleResolution.command_id == command_id
            )
        )
        if existing:
            return ManualDemoHistoryService(self.session).get(tenant, command_id).evidence
        if effect and effect.state in {
            VenueSubmitEffectState.CREATED,
            VenueSubmitEffectState.LEASED,
            VenueSubmitEffectState.PROVEN_UNSENT,
        }:
            self._dispatcher().fence_proven_unsent(command_id=command_id)
            self.session.commit()
        current = self.reconcile(tenant, command_id)
        if not current.can_resolve:
            raise TradingPolicyError(
                current.recovery_reason or "Complete terminal evidence is required.",
                details={"reason": "manual_demo_resolution_evidence_incomplete"},
            )
        # Use the just-read full precision observation, not rounded Journal values
        # or client-supplied proof. The epoch serializes release with new claims.
        latest = self.session.scalar(
            select(AuditLog)
            .where(
                AuditLog.organization_id == tenant.organization_id,
                AuditLog.user_id == tenant.user_id,
                AuditLog.resource_id == str(command_id),
                AuditLog.redacted_metadata["operation"].as_string()
                == "manual_demo_lifecycle_observed",
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        assert latest is not None
        facts = latest.redacted_metadata
        observation = ManualLifecycleObservation(
            observed_at=datetime.fromisoformat(facts["observed_at"]),
            position_status=facts["position_status"],
            protection_history=tuple(facts["protection_history"]),
            exit_fills=tuple(facts["exit_fills"]),
            account_flat=facts["account_flat"],
            account_idle=facts["account_idle"],
            resolution_reason=facts["resolution_reason"],
            recovery_reason=facts["recovery_reason"],
            account_verified=facts.get("account_verified", False),
            triggered_protection=facts.get("triggered_protection", "unverified"),
            protection_diagnostics=tuple(
                DemoReconciliationDiagnostic.model_validate(d)
                for d in facts.get("protection_diagnostics", [])
            ),
        )
        resolve_manual_lifecycle(
            self.session,
            command=command,
            plan=plan,
            observation=observation,
            epochs=self.epochs,
            audit=self.audit,
        )
        self.session.commit()
        return ManualDemoHistoryService(self.session).get(tenant, command_id).evidence

    def _replay_representation(
        self, tenant: TenantContext, command_id: UUID, fact: DemoFill
    ) -> tuple[Decimal, Decimal]:
        """Reuse a proven lexical representation only for exactly equal native facts.

        Shared immutable fill hashes include decimal strings. Do not rewrite those
        hashes or compare rounded database amounts to establish equality. The
        original full-precision audit amounts must recreate the immutable hash.
        """
        prior = self.session.scalar(
            select(AuditLog)
            .where(
                AuditLog.organization_id == tenant.organization_id,
                AuditLog.user_id == tenant.user_id,
                AuditLog.resource_type == "manual_demo_test",
                AuditLog.resource_id == str(command_id),
                AuditLog.action == AuditEventType.POSITION_UPDATED,
                AuditLog.redacted_metadata["fill_identity"].as_string() == fact.identity,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        if prior is None or "quantity" not in prior.redacted_metadata:
            return fact.quantity, fact.price
        quantity = Decimal(prior.redacted_metadata["quantity"])
        price = Decimal(prior.redacted_metadata["price"])
        if quantity != fact.quantity or price != fact.price:
            return (
                fact.quantity,
                fact.price,
            )  # Shared identity conflict check remains final authority.
        immutable = self.session.scalar(
            select(ExecutionFillFact).where(
                ExecutionFillFact.organization_id == tenant.organization_id,
                ExecutionFillFact.command_id == command_id,
                ExecutionFillFact.source_fill_identity == fact.identity,
            )
        )
        if (
            immutable is not None
            and fill_content_hash(
                receipt_id=str(immutable.receipt_id),
                command_id=str(command_id),
                venue_source=immutable.venue_source,
                source_fill_identity=fact.identity,
                quantity=quantity,
                price=price,
                unit=immutable.unit,
                occurred_at=immutable.occurred_at,
            )
            == immutable.content_hash
        ):
            return quantity, price
        return fact.quantity, fact.price

    def _journal(
        self,
        tenant: TenantContext,
        command: ExecutionCommand,
        plan: TradePlanRevision,
        fills: tuple[DemoFill, ...],
        fees: Decimal | None,
        protection: str,
    ) -> None:
        if not fills:
            return
        trade = self.session.scalar(
            select(JournalTrade).where(
                JournalTrade.organization_id == tenant.organization_id,
                JournalTrade.execution_lifecycle_id == command.id,
            )
        )
        if trade is None:
            trade = JournalTrade(
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                source=JournalTradeSource.MANUAL_DEMO_TEST,
                entry_method=JournalEntryMethod.MANUAL,
                symbol="BTCUSDT",
                exchange="BLOFIN_DEMO",
                timeframe="manual",
                direction=TradeDirection.LONG if plan.side.value == "BUY" else TradeDirection.SHORT,
                strategy_label="Manual demo test — excluded from strategy validation",
                thesis="Supervised manual demo test; no detected or approved strategy claimed",
                planned_entry_price=plan.basis_policy.execution_price.value,
                planned_stop_price=plan.risk_and_exits.stop.value,
                planned_targets=journal_planned_targets(plan),
                planned_risk_amount=plan.risk_and_exits.maximum_loss.value,
                linked_proposal_id=plan.plan_id,
                execution_lifecycle_id=command.id,
                account_id=plan.account_id,
                trade_plan_revision_id=plan.revision_id,
                leverage=Decimal("1"),
                tags=["manual demo test", "excluded from strategy validation"],
            )
            self.session.add(trade)
        elif (
            trade.user_id != tenant.user_id
            or trade.account_id != plan.account_id
            or trade.source != JournalTradeSource.MANUAL_DEMO_TEST
            or trade.exchange != "BLOFIN_DEMO"
            or trade.trade_plan_revision_id not in {None, plan.revision_id}
            or trade.linked_proposal_id != plan.plan_id
            or trade.symbol != "BTCUSDT"
            or trade.direction
            != (TradeDirection.LONG if plan.side.value == "BUY" else TradeDirection.SHORT)
        ):
            raise TradingPolicyError(
                "Manual demo Journal identity conflicts; operator review required."
            )
        # Repair only this proven lifecycle's missing pointer/target representation.
        trade.trade_plan_revision_id = plan.revision_id
        trade.planned_targets = journal_planned_targets(plan)
        total = sum((f.quantity for f in fills), Decimal("0"))
        trade.entry_price = sum((f.quantity * f.price for f in fills), Decimal("0")) / total
        trade.size = total * plan.instrument_rules.contract_multiplier
        trade.entry_time = min(f.occurred_at for f in fills)
        exits = recorded_exit_facts(self.session, command)
        trade.fees = (
            fees + sum((Decimal(f["fee"]) for f in exits), Decimal("0"))
            if fees is not None
            and all(
                f.get("fee") is not None
                and f.get("fee_currency") == plan.instrument_rules.settlement_currency
                for f in exits
            )
            else None
        )
        if trade.exit_price is None:
            trade.status = JournalTradeStatus.OPEN
        trade.entry_plan = (
            f"Actual BloFin demo fills; protection {protection}; plan {plan.revision_id}"
        )
        self.session.flush()

    def cancel(self, tenant: TenantContext, command_id: UUID) -> ManualDemoStatus:
        _command, plan, effect = self._command(tenant, command_id)
        if effect is None:
            raise TradingPolicyError("No venue order identity to cancel.")
        client_id = effect.client_order_id
        self.session.commit()
        # Reconcile first; never cancel protection or guess a venue order identity.
        evidence = self._provider().reconcile(plan=plan, client_order_id=client_id)
        if evidence is None or evidence.status not in {"live", "partially_filled"}:
            return self.reconcile(tenant, command_id)
        self.epochs.lock_epoch(organization_id=tenant.organization_id, account_id=plan.account_id)
        prior = self.session.scalar(
            select(AuditLog.id).where(
                AuditLog.organization_id == tenant.organization_id,
                AuditLog.user_id == tenant.user_id,
                AuditLog.resource_id == str(command_id),
                AuditLog.action == AuditEventType.MANUAL_DEMO_CANCEL_REQUESTED,
            )
        )
        if prior is None:
            self._audit(
                tenant,
                AuditEventType.MANUAL_DEMO_CANCEL_REQUESTED,
                command_id,
                "manual_demo_cancel_requested",
                {
                    "order_id": evidence.order_id,
                    "client_order_id": client_id,
                    "origin": MANUAL_DEMO_ORIGIN,
                },
            )
        self.session.commit()  # Durable cancellation intent before one POST; never retry.
        if prior is None:
            # Any uncertain cancellation is followed by reads, never another POST.
            with suppress(Exception):
                self._provider().cancel_entry(plan=plan, evidence=evidence)
        return self.reconcile(tenant, command_id)

    def _audit(
        self,
        tenant: TenantContext,
        event: AuditEventType,
        resource: UUID,
        action: str,
        metadata: dict[str, object],
    ) -> None:
        self.audit.record(
            AuditRecordCreate(
                request_id=str(resource),
                trace_id=str(resource),
                event_type=event,
                resource_type="manual_demo_test",
                resource_id=str(resource),
                actor_type=ActorType.USER,
                action=action,
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                metadata={"operation": action, **metadata},
            )
        )
