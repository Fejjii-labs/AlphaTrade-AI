"""Canonical PAPER execution application layer.

EXECUTE_PAPER_PLAN for ``plan_authority=canonical`` must bind the exact approved
immutable TradePlanRevision and re-verify Candidate, ActionEligibility, plan,
approval, and account lineage immediately before the claim transaction.

RiskEngine BLOCK and the kill switch remain final inside PaperPlanClaimService
via ``evaluate_claim_predicate``. Missing Candidate, eligibility, or required
lineage after ALLOW fails closed rather than skipping learning evidence.
This module never calls an exchange.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError, ValidationAppError
from app.core.paper_safety import assert_paper_execution_allowed
from app.db.canonical_trade_plans import PLAN_AUTHORITY_CANONICAL
from app.db.models import ExecutionCommand, ExecutionFillFact, JournalLifecycleEvent
from app.repositories.journal_trades import JournalTradeRepository
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.approval import ApprovalAuthorization
from app.schemas.canonical_trade_plan import CanonicalTradePlanRevision
from app.schemas.common import (
    JournalLifecycleEventType,
    JournalTradeStatus,
    TradeDirection,
    TradeResult,
)
from app.schemas.execution_protocol import (
    ClosePaperPlanRequest,
    ClosePaperPlanResult,
    ExecutePaperPlanRequest,
    ExecutePaperPlanResult,
    ExecutionCommandOutcome,
    UniqueFillResult,
)
from app.schemas.journal_lifecycle import JournalLifecycleEventInput, JournalLineagePayload
from app.schemas.trade_plan import EntrySide, TradePlanRevision
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import (
    CANONICAL_EXECUTION_SOURCE_SYSTEM,
    canonical_planned_targets,
    project_canonical_execution_event,
)
from app.services.canonical_execution_learning import (
    attribute_canonical_paper_event,
    require_canonical_attribution_lineage,
)
from app.services.canonical_trade_plan_errors import CanonicalTradePlanNotFoundError
from app.services.execution_claim import ExecutionClaimHooks, PaperPlanClaimService
from app.services.journal_lifecycle_projector import JournalLifecycleProjector
from app.services.paper_close_economics import paper_close_pnl
from app.services.safety_epoch import SafetyEpochService
from app.signal_fusion.enums import CandidateState

_TERMINAL_CANDIDATE_REASONS: dict[CandidateState, str] = {
    CandidateState.REJECTED: "candidate_rejected",
    CandidateState.SKIPPED: "candidate_skipped",
    CandidateState.EXPIRED: "candidate_expired",
    CandidateState.INVALIDATED: "candidate_invalidated",
}


class CanonicalPaperExecutionService:
    """Paper-only execution for canonical TradePlanRevision rows."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        audit_service: AuditService,
        runtime: ProductionCanonicalRuntime,
        *,
        safety_epochs: SafetyEpochService,
        clock: Callable[[], datetime],
        hooks: ExecutionClaimHooks | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit_service
        self._runtime = runtime
        self._clock = clock
        self._projector = JournalLifecycleProjector(session, audit_service)
        claim_hooks = hooks or ExecutionClaimHooks()
        self._claim = PaperPlanClaimService(
            session,
            settings,
            safety_epochs,
            clock=clock,
            hooks=ExecutionClaimHooks(
                after_idempotency=claim_hooks.after_idempotency,
                after_epoch_lock=claim_hooks.after_epoch_lock,
                before_return=claim_hooks.before_return,
                revalidate=self._revalidate_canonical_lineage,
            ),
        )

    def execute(self, request: ExecutePaperPlanRequest) -> ExecutePaperPlanResult:
        assert_paper_execution_allowed(self._settings)
        with self._runtime.bind_session(self._session):
            result = self._claim.claim(request)
            if result.outcome is ExecutionCommandOutcome.ALLOW:
                plan = self._runtime.plans.get_scoped(
                    request.revision_id,
                    organization_id=request.organization_id,
                    user_id=request.user_id,
                ).plan
                # Demo authorization is not exchange evidence. Journal begins
                # only when immutable venue fills have been reconciled.
                if plan.execution_policy_version != "governed-blofin-demo/v1":
                    self._project_approved_plan(request, result)
            return result

    def project_fill(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        account_id: UUID,
        command_id: UUID,
        fill: UniqueFillResult,
        revision_id: UUID,
        venue_fill_fee: Decimal | None = None,
        venue_fill_fee_currency: str | None = None,
        cumulative_fees: Decimal | None = None,
        demo_protection: str | None = None,
        demo_protection_order_ids: tuple[str, ...] = (),
        demo_native_tpsl_id: str | None = None,
    ) -> None:
        with self._runtime.bind_session(self._session):
            envelope = self._runtime.plans.get_scoped(
                revision_id,
                organization_id=organization_id,
                user_id=user_id,
            )
            payload = _instrument_payload(envelope.plan)
            if fill.weighted_price is not None:
                payload["entry_price"] = str(fill.weighted_price)
            payload["size"] = str(
                fill.filled_quantity * envelope.plan.instrument_rules.contract_multiplier
                if envelope.plan.execution_venue == "BLOFIN_DEMO"
                and envelope.plan.quantity.unit == "CONTRACTS"
                else fill.filled_quantity
            )
            entry_time = self._session.scalar(
                select(func.min(ExecutionFillFact.occurred_at)).where(
                    ExecutionFillFact.organization_id == organization_id,
                    ExecutionFillFact.command_id == command_id,
                )
            )
            if entry_time is None:
                raise ValidationAppError("Canonical journal fill requires an immutable fill fact.")
            payload["entry_time"] = entry_time.isoformat()
            if demo_protection is not None:
                payload["demo_protection"] = demo_protection
                payload["demo_protection_order_ids"] = list(demo_protection_order_ids)
                payload["demo_native_tpsl_id"] = demo_native_tpsl_id
            if venue_fill_fee is not None:
                payload["venue_fill_fee"] = str(venue_fill_fee)
                payload["fees"] = str(cumulative_fees) if cumulative_fees is not None else None
                if demo_protection is not None:
                    payload["venue_fill_fee_currency"] = venue_fill_fee_currency
                    payload["fee_convention"] = None
            # Phase 1 paper claims do not create legacy Order rows. Command
            # identity is execution_lifecycle_id; linked_order_id stays unset.
            payload["lineage"] = _lineage_payload(envelope, command_id, self._runtime).model_dump(
                mode="json", exclude_none=True
            )
            if fill.replayed:
                # A verified immutable fill may be replayed after PostgreSQL has
                # applied its numeric scale, or after later partial fills have
                # changed cumulative totals. Its original journal event remains
                # the authority for that source identity. When no event exists,
                # reconstruct it normally to preserve crash recovery.
                recorded = self._session.scalar(
                    select(JournalLifecycleEvent).where(
                        JournalLifecycleEvent.organization_id == organization_id,
                        JournalLifecycleEvent.user_id == user_id,
                        JournalLifecycleEvent.account_id == account_id,
                        JournalLifecycleEvent.execution_lifecycle_id == command_id,
                        JournalLifecycleEvent.event_type == JournalLifecycleEventType.FILL,
                        JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                        JournalLifecycleEvent.source_aggregate == "execution-command",
                        JournalLifecycleEvent.source_event_id == str(fill.fill_id),
                        JournalLifecycleEvent.source_event_version == 1,
                        JournalLifecycleEvent.supersession == 0,
                    )
                )
                if recorded is not None:
                    if (
                        venue_fill_fee is not None
                        and Decimal(str(recorded.payload.get("venue_fill_fee"))) != venue_fill_fee
                    ):
                        raise ValidationAppError(
                            "Venue fill fee conflicts with its immutable Journal event."
                        )
                    if (
                        demo_protection is not None
                        and "venue_fill_fee_currency" in recorded.payload
                        and recorded.payload["venue_fill_fee_currency"] != venue_fill_fee_currency
                    ):
                        raise ValidationAppError(
                            "Venue fill fee currency conflicts with its immutable Journal event."
                        )
                    payload = dict(recorded.payload)
            event = JournalLifecycleEventInput(
                event_type=JournalLifecycleEventType.FILL,
                execution_lifecycle_id=command_id,
                source_system=CANONICAL_EXECUTION_SOURCE_SYSTEM,
                source_aggregate="execution-command",
                source_event_id=str(fill.fill_id),
                source_event_version=1,
                account_id=account_id,
                payload=payload,
                correlation_id=str(envelope.plan.correlation_id),
            )
            projection = project_canonical_execution_event(
                self._projector,
                event=event,
                organization_id=organization_id,
                user_id=user_id,
            )
            attribute_canonical_paper_event(
                session=self._session,
                projector=self._projector,
                runtime=self._runtime,
                settings=self._settings,
                envelope=envelope,
                event=event,
                projection=projection,
                organization_id=organization_id,
                user_id=user_id,
            )

    def close_filled_plan(self, request: ClosePaperPlanRequest) -> ClosePaperPlanResult:
        """Project a paper CLOSE for one filled canonical plan.

        The exit price and costs are caller-supplied. This method does not read
        market data, does not call an exchange, and does not open a new order.
        """

        assert_paper_execution_allowed(self._settings)
        if request.occurred_at.tzinfo is None:
            raise ValidationAppError(
                "Paper close time must be timezone-aware.",
                details={"reason": "naive_close_time"},
            )
        with self._runtime.bind_session(self._session):
            envelope = self._load_close_envelope(request)
            if envelope.plan.execution_policy_version == "governed-blofin-demo/v1":
                raise TradingPolicyError(
                    "Demo close requires actual exchange exit evidence; "
                    "synthetic closes are disabled.",
                    details={"reason": "demo_synthetic_close_forbidden"},
                )
            trade = JournalTradeRepository(self._session).find_by_execution_lifecycle(
                organization_id=request.organization_id,
                execution_lifecycle_id=request.command_id,
            )
            if trade is None or trade.user_id != request.user_id:
                raise ValidationAppError(
                    "Filled paper journal trade is missing; refusing to invent an outcome.",
                    details={"reason": "missing_open_trade"},
                )
            self._assert_close_eligible(request, trade_status=trade.status)
            if trade.entry_price is None or trade.size is None:
                raise ValidationAppError(
                    "Paper close requires a recorded entry price and size.",
                    details={"reason": "incomplete_fill"},
                )
            economics = paper_close_pnl(
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=request.exit_price,
                size=trade.size,
                fees=request.fees,
                funding=request.funding,
                slippage=request.slippage,
            )
            exit_reason = request.exit_reason.strip()
            if not exit_reason:
                raise ValidationAppError(
                    "Paper close requires an exit reason.",
                    details={"reason": "missing_exit_reason"},
                )
            payload = {
                "exit_price": str(request.exit_price),
                "exit_time": request.occurred_at.isoformat(),
                "exit_reason": exit_reason,
                "fees": str(request.fees),
                "funding": str(request.funding),
                "slippage": str(request.slippage),
                "gross_pnl": str(economics.gross_pnl),
                "net_pnl": str(economics.net_pnl),
                "result": economics.result.value,
                "lineage": _lineage_payload(envelope, request.command_id, self._runtime).model_dump(
                    mode="json", exclude_none=True
                ),
            }
            event = JournalLifecycleEventInput(
                event_type=JournalLifecycleEventType.CLOSE,
                execution_lifecycle_id=request.command_id,
                source_system=CANONICAL_EXECUTION_SOURCE_SYSTEM,
                source_aggregate="execution-command",
                source_event_id=request.idempotency_key,
                source_event_version=1,
                account_id=request.account_id,
                payload=payload,
                correlation_id=str(envelope.plan.correlation_id),
            )
            projection = project_canonical_execution_event(
                self._projector,
                event=event,
                organization_id=request.organization_id,
                user_id=request.user_id,
            )
            attribute_canonical_paper_event(
                session=self._session,
                projector=self._projector,
                runtime=self._runtime,
                settings=self._settings,
                envelope=envelope,
                event=event,
                projection=projection,
                organization_id=request.organization_id,
                user_id=request.user_id,
            )
            if projection.journal_trade_id is None:
                raise ValidationAppError(
                    "Paper close did not resolve a journal trade.",
                    details={"reason": "missing_journal_trade"},
                )
            self._session.refresh(trade)
            if (
                trade.entry_price is None
                or trade.exit_price is None
                or trade.fees is None
                or trade.gross_pnl is None
                or trade.net_pnl is None
                or trade.exit_reason is None
                or trade.result is TradeResult.OPEN
            ):
                raise ValidationAppError(
                    "Paper close did not record exit, fees, and PnL.",
                    details={"reason": "incomplete_close"},
                )
            return ClosePaperPlanResult(
                replayed=projection.replayed,
                command_id=request.command_id,
                journal_trade_id=projection.journal_trade_id,
                candidate_id=trade.candidate_id,
                strategy_version_id=trade.strategy_version_id,
                symbol=trade.symbol,
                timeframe=trade.timeframe,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                exit_reason=trade.exit_reason,
                fees=trade.fees,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                result=trade.result,
                thesis=trade.thesis,
            )

    def _load_close_envelope(self, request: ClosePaperPlanRequest) -> CanonicalTradePlanRevision:
        command = self._session.get(ExecutionCommand, request.command_id)
        if command is None or command.organization_id != request.organization_id:
            raise NotFoundError("Execution command is unknown in this organization.")
        if command.user_id != request.user_id:
            raise NotFoundError("Execution command is unknown in this organization.")
        if command.account_id != request.account_id or command.revision_id != request.revision_id:
            raise ValidationAppError(
                "Paper close identity does not match the execution command.",
                details={"reason": "close_identity_mismatch"},
            )
        try:
            return self._runtime.plans.get_scoped(
                request.revision_id,
                organization_id=request.organization_id,
                user_id=request.user_id,
            )
        except CanonicalTradePlanNotFoundError as exc:
            raise NotFoundError(
                "Canonical trade plan revision is unknown in this tenant scope."
            ) from exc

    def _assert_close_eligible(
        self,
        request: ClosePaperPlanRequest,
        *,
        trade_status: JournalTradeStatus,
    ) -> None:
        existing = self._session.scalar(
            select(JournalLifecycleEvent).where(
                JournalLifecycleEvent.organization_id == request.organization_id,
                JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                JournalLifecycleEvent.event_type == JournalLifecycleEventType.CLOSE,
                JournalLifecycleEvent.source_event_id == request.idempotency_key,
            )
        )
        if existing is not None and existing.execution_lifecycle_id != request.command_id:
            raise ValidationAppError(
                "Paper close idempotency key is bound to another trade.",
                details={"reason": "idempotency_conflict"},
            )
        if trade_status is JournalTradeStatus.CLOSED and existing is None:
            raise ValidationAppError(
                "Paper trade is already closed.",
                details={"reason": "already_closed"},
            )
        if trade_status is not JournalTradeStatus.OPEN and existing is None:
            raise ValidationAppError(
                "Paper close requires an open fill.",
                details={"reason": "not_open"},
            )

    def _revalidate_canonical_lineage(
        self,
        *,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
        request: ExecutePaperPlanRequest,
        at: datetime,
    ) -> str | None:
        try:
            envelope = self._runtime.plans.get_scoped(
                request.revision_id,
                organization_id=request.organization_id,
                user_id=request.user_id,
            )
        except CanonicalTradePlanNotFoundError as exc:
            raise NotFoundError(
                "Canonical trade plan revision is unknown in this tenant scope."
            ) from exc
        if envelope.plan.content_hash != plan.content_hash:
            return "canonical_plan_modified"
        if envelope.plan.content_hash != authorization.plan_content_hash:
            return "plan_hash_mismatch"
        if envelope.live_executable:
            return "live_execution_forbidden"
        if envelope.plan.account_id != request.account_id:
            return "account_mismatch"
        if envelope.plan.account_id != authorization.account_id:
            return "account_mismatch"
        if envelope.plan.revision_id != authorization.revision_id:
            return "plan_revision_mismatch"
        candidate = self._runtime.lifecycle.get_by_candidate_id(
            request.organization_id, envelope.lineage.candidate_id
        )
        if candidate is None:
            raise NotFoundError("Canonical Candidate is unknown in this organization.")
        if candidate.organization_id != request.organization_id:
            raise TradingPolicyError(
                "Canonical Candidate belongs to a different organization.",
                details={"reason": "tenant_isolation"},
            )
        if candidate.candidate_id != envelope.plan.candidate_id:
            return "canonical_lineage_mismatch"
        terminal = _TERMINAL_CANDIDATE_REASONS.get(candidate.state)
        if terminal is not None:
            return terminal
        if candidate.state is not CandidateState.PLAN_CREATED:
            return "candidate_not_plan_created"
        # PLAN_CREATED appends a transition; lineage identity hashes remain the
        # pre-transition Candidate digest.
        if candidate.valid_until <= at:
            return "candidate_expired"
        evaluation = self._runtime.eligibility.get(envelope.lineage.eligibility_uniqueness_hash)
        if evaluation is None:
            return "eligibility_not_found"
        if evaluation.eligibility.eligibility_id != envelope.lineage.eligibility_id:
            return "eligibility_mismatch"
        if evaluation.content_hash != envelope.lineage.eligibility_content_hash:
            return "canonical_lineage_mismatch"
        if evaluation.eligibility.account_id != request.account_id:
            return "account_mismatch"
        if evaluation.live_executable:
            return "live_execution_forbidden"
        if not evaluation.currently_paper_actionable(at):
            return "eligibility_not_actionable"
        return None

    def _project_approved_plan(
        self, request: ExecutePaperPlanRequest, result: ExecutePaperPlanResult
    ) -> None:
        envelope = self._runtime.plans.get_scoped(
            request.revision_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        payload = _instrument_payload(envelope.plan)
        payload["lineage"] = _lineage_payload(
            envelope, result.command_id, self._runtime
        ).model_dump(mode="json", exclude_none=True)
        if result.replayed:
            # Preserve the original event preimage, including legacy events
            # without targets. Projection repair must never rewrite history.
            recorded = self._session.scalar(
                select(JournalLifecycleEvent).where(
                    JournalLifecycleEvent.organization_id == request.organization_id,
                    JournalLifecycleEvent.user_id == request.user_id,
                    JournalLifecycleEvent.account_id == request.account_id,
                    JournalLifecycleEvent.execution_lifecycle_id == result.command_id,
                    JournalLifecycleEvent.event_type == JournalLifecycleEventType.APPROVED_PLAN,
                    JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                    JournalLifecycleEvent.source_aggregate == "execution-command",
                    JournalLifecycleEvent.source_event_id == str(result.command_id),
                    JournalLifecycleEvent.source_event_version == 1,
                    JournalLifecycleEvent.supersession == 0,
                )
            )
            if recorded is not None:
                payload = dict(recorded.payload)
        event = JournalLifecycleEventInput(
            event_type=JournalLifecycleEventType.APPROVED_PLAN,
            execution_lifecycle_id=result.command_id,
            source_system=CANONICAL_EXECUTION_SOURCE_SYSTEM,
            source_aggregate="execution-command",
            source_event_id=str(result.command_id),
            source_event_version=1,
            account_id=request.account_id,
            payload=payload,
            correlation_id=str(envelope.plan.correlation_id),
        )
        projection = project_canonical_execution_event(
            self._projector,
            event=event,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        attribute_canonical_paper_event(
            session=self._session,
            projector=self._projector,
            runtime=self._runtime,
            settings=self._settings,
            envelope=envelope,
            event=event,
            projection=projection,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )


def is_canonical_plan_authority(plan_authority: str | None) -> bool:
    return plan_authority == PLAN_AUTHORITY_CANONICAL


def _instrument_payload(plan: TradePlanRevision) -> dict[str, object]:
    direction = TradeDirection.LONG if plan.side is EntrySide.BUY else TradeDirection.SHORT
    return {
        "symbol": plan.execution_instrument,
        "timeframe": plan.timeframe,
        "direction": direction.value,
        "thesis": "Canonical paper execution of an approved TradePlanRevision.",
        "planned_entry_price": str(plan.entry_zone.lower),
        "planned_stop_price": str(plan.risk_and_exits.stop.value),
        "planned_targets": canonical_planned_targets(plan),
        "planned_risk_amount": str(plan.risk_and_exits.risk_budget.value),
        "size": str(
            plan.quantity.value * plan.instrument_rules.contract_multiplier
            if plan.execution_venue == "BLOFIN_DEMO" and plan.quantity.unit == "CONTRACTS"
            else plan.quantity.value
        ),
        "leverage": str(plan.risk_and_exits.leverage),
        "linked_proposal_id": str(plan.plan_id),
        "strategy_version_id": str(plan.strategy_version_id),
    }


def _lineage_payload(
    envelope: CanonicalTradePlanRevision,
    execution_lifecycle_id: UUID,
    runtime: ProductionCanonicalRuntime,
) -> JournalLineagePayload:
    lineage = envelope.lineage
    resolved = require_canonical_attribution_lineage(
        runtime=runtime,
        organization_id=envelope.plan.organization_id,
        envelope=envelope,
    )
    return JournalLineagePayload(
        organization_id=envelope.plan.organization_id,
        account_id=envelope.plan.account_id,
        execution_lifecycle_id=execution_lifecycle_id,
        candidate_id=lineage.candidate_id,
        candidate_content_hash=lineage.candidate_content_hash,
        assessment_id=lineage.assessment_id,
        assessment_content_hash=resolved.evaluation.setup_assessment_content_hash,
        evidence_window_hash=lineage.evidence_window_hash,
        trade_plan_revision_id=envelope.plan.revision_id,
        trade_plan_content_hash=envelope.plan.content_hash,
        setup_definition_id=lineage.setup_definition_id,
        strategy_version_id=lineage.strategy_version_id,
        fusion_policy_version=lineage.fusion_policy_version,
        uniqueness_tuple_hash=envelope.uniqueness_hash,
    )
