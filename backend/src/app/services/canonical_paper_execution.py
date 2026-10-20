"""Canonical PAPER execution application layer.

EXECUTE_PAPER_PLAN for ``plan_authority=canonical`` must bind the exact approved
immutable TradePlanRevision and re-verify Candidate, ActionEligibility, plan,
approval, and account lineage immediately before the claim transaction.

The claim predicate inside PaperPlanClaimService is final for the kill switch,
daily lock, total exposure, daily loss, trade slots, and symbol exposure.
``RiskEngine.evaluate()`` is not that gate. A kill switch that becomes active
after approval and before claim still blocks the order. Missing Candidate,
eligibility, or required lineage after ALLOW fails closed rather than skipping
learning evidence.
This module never calls an exchange.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from app.services.risk_policy.profile import PolicyKind

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError, ValidationAppError
from app.core.paper_safety import assert_paper_execution_allowed
from app.db.canonical_trade_plans import PLAN_AUTHORITY_CANONICAL
from app.db.models import ApprovalAuthorization as ApprovalAuthorizationRow
from app.db.models import ExecutionCommand, ExecutionFillFact, JournalLifecycleEvent, JournalTrade
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

logger = structlog.get_logger(__name__)

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
            command = self._session.get(ExecutionCommand, command_id)
            _attach_approval_channel(
                self._session,
                payload,
                None if command is None else command.authorization_id,
            )
            # Phase 1 paper claims do not create legacy Order rows. Command
            # identity is execution_lifecycle_id; linked_order_id stays unset.
            payload["lineage"] = _lineage_payload(envelope, command_id, self._runtime).model_dump(
                mode="json", exclude_none=True
            )
            if (
                envelope.plan.risk_and_exits.margin_assumption_id == "paper-profile-isolated"
                and fill.weighted_price is not None
            ):
                _journal_paper_liquidation(payload, envelope.plan, fill.weighted_price)
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
            # Two cycles can both observe OPEN before either commits. The row
            # lock makes the second wait, then re-read. A matching close key
            # replays. Any other key finds the trade already closed.
            self._projector.lock_execution_lifecycle(request.organization_id, request.command_id)
            refresh = (
                select(JournalTrade)
                .where(JournalTrade.id == trade.id)
                .execution_options(populate_existing=True)
            )
            bind = self._session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                refresh = refresh.with_for_update()
            locked = self._session.scalar(refresh)
            if locked is None or locked.user_id != request.user_id:
                raise ValidationAppError(
                    "Filled paper journal trade is missing; refusing to invent an outcome.",
                    details={"reason": "missing_open_trade"},
                )
            trade = locked
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
            payload: dict[str, object] = {
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
            payload.update(paper_exit_audit_fields(request))
            payload.update(
                _closed_risk_facts(
                    self._session,
                    organization_id=request.organization_id,
                    candidate_id=trade.candidate_id,
                    net_pnl=economics.net_pnl,
                )
            )
            command = self._session.get(ExecutionCommand, request.command_id)
            _attach_approval_channel(
                self._session,
                payload,
                None if command is None else command.authorization_id,
            )
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
            # A live profile close feeds the consecutive streak and the daily
            # lock. A labelled backfill does not. No calibration sample is
            # written: a backfill is ineligible, and a live close has no
            # walk-forward fold on this path.
            if profile_close_should_note(
                request,
                replayed=projection.replayed,
                margin_assumption_id=envelope.plan.risk_and_exits.margin_assumption_id,
                net_pnl=trade.net_pnl,
            ):
                assert trade.net_pnl is not None
                _consecutive, refused_new_trades = _note_profile_close(
                    self._session,
                    organization_id=request.organization_id,
                    user_id=request.user_id,
                    account_id=request.account_id,
                    net_pnl=trade.net_pnl,
                    now=request.occurred_at,
                )
                if refused_new_trades:
                    logger.info(
                        "profile_close_refuses_new_trades",
                        organization_id=str(request.organization_id),
                        account_id=str(request.account_id),
                        consecutive_losses=_consecutive,
                    )
            elif _backfill_loss_needs_protection(
                request,
                replayed=projection.replayed,
                net_pnl=trade.net_pnl,
            ):
                assert trade.net_pnl is not None
                _protect_backfill_loss(
                    self._session,
                    request=request,
                    net_pnl=trade.net_pnl,
                    settings=self._settings,
                )
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
        _attach_approval_channel(self._session, payload, request.authorization_id)
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


def paper_exit_audit_fields(request: ClosePaperPlanRequest) -> dict[str, object]:
    """Journal fields that mark an automated exit. Absent on a human close.

    Keys are added only when the caller set them, so an HTTP close payload
    keeps the same shape and content hash as before this monitor existed.
    """

    fields: dict[str, object] = {}
    if request.exit_channel is not None:
        fields["exit_channel"] = request.exit_channel
    if request.exit_actor is not None:
        fields["exit_actor"] = request.exit_actor
    if request.exit_evidence is not None:
        fields["exit_evidence"] = dict(request.exit_evidence)
        origin = request.exit_evidence.get("exit_origin")
        if origin is not None:
            fields["exit_origin"] = origin
        sample = request.exit_evidence.get("calibration_sample")
        if sample is not None:
            fields["calibration_sample"] = sample
    return fields


def profile_close_should_note(
    request: ClosePaperPlanRequest,
    *,
    replayed: bool,
    margin_assumption_id: str,
    net_pnl: Decimal | None,
) -> bool:
    """Profile closes update the breaker. A labelled backfill does not.

    ``_note_profile_close`` is what advances the consecutive-loss streak and
    syncs the daily lock. Skipping it leaves both untouched. A human close
    has no monitor label and still notes. A cash-margin plan does not.
    A losing backfill is not noted here. The close path refuses new entries
    for that loss without writing the streak or the daily lock.
    """

    if replayed or net_pnl is None:
        return False
    if margin_assumption_id != "paper-profile-isolated":
        return False
    from app.services.paper_exit_monitor import exit_monitor_backfill

    return not exit_monitor_backfill(request)


def _backfill_loss_needs_protection(
    request: ClosePaperPlanRequest,
    *,
    replayed: bool,
    net_pnl: Decimal | None,
) -> bool:
    from app.services.paper_exit_monitor import backfill_loss_needs_protection

    return backfill_loss_needs_protection(request, replayed=replayed, net_pnl=net_pnl)


def _protect_backfill_loss(
    session: Session,
    *,
    request: ClosePaperPlanRequest,
    net_pnl: Decimal,
    settings: Settings,
) -> None:
    from app.services.paper_exit_protection import protect_account_from_backfill_loss

    protect_account_from_backfill_loss(
        session,
        request=request,
        net_pnl=net_pnl,
        settings=settings,
    )


def _journal_paper_liquidation(
    payload: dict[str, object], plan: TradePlanRevision, fill_price: Decimal
) -> None:
    """Record a post-fill liquidation-versus-stop verdict. Does not close or write."""

    from app.services.risk_policy.decision import post_fill_recheck
    from app.services.risk_policy.errors import RiskPolicyRejected
    from app.services.risk_policy.liquidation import isolated_liquidation_price
    from app.services.risk_policy.plan_sizing import (
        PAPER_CONSERVATIVE_MAINTENANCE_RATE,
        PAPER_CONSERVATIVE_MAINTENANCE_TIERS,
    )

    direction = "long" if plan.side is EntrySide.BUY else "short"
    try:
        liquidation = isolated_liquidation_price(
            entry=fill_price,
            leverage=plan.risk_and_exits.leverage,
            maintenance_rate=PAPER_CONSERVATIVE_MAINTENANCE_RATE,
            direction=direction,
        )
    except RiskPolicyRejected:
        payload["post_fill_action"] = "hold"
        payload["post_fill_reason"] = "unreadable_liquidation"
        payload["exchange_write_attempted"] = False
        return
    outcome = post_fill_recheck(
        fill_price=fill_price,
        stop_distance=abs(fill_price - plan.risk_and_exits.stop.value),
        atr=None,
        leverage=plan.risk_and_exits.leverage,
        notional=fill_price,
        margin_mode="isolated",
        tiers=PAPER_CONSERVATIVE_MAINTENANCE_TIERS,
        direction=direction,
        stop_price=plan.risk_and_exits.stop.value,
        observed_liquidation_price=liquidation,
    )
    if outcome.accepted or outcome.exchange_write_attempted:
        return
    payload["post_fill_action"] = outcome.action
    payload["post_fill_reason"] = outcome.reason
    payload["exchange_write_attempted"] = False


def _note_profile_close(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    net_pnl: Decimal,
    now: datetime,
    kind: PolicyKind | None = None,
) -> tuple[int | None, bool]:
    """Advance consecutive losses, then persist the breaker verdict. No reset.

    Returns the streak and whether new trades are refused. ``kind`` selects
    the active profile and the breaker row is the account's row. Paper uses
    the internal-paper profile. Demo uses the demo profile. A drawdown latch
    is not cleared. A refusal is final for the next entry; this close stays.
    """

    from app.services.audit_service import AuditService
    from app.services.automated_paper_loop import _load_active_profile
    from app.services.risk_policy.breaker_trip import (
        _as_utc,
        persist_profile_breakers,
        profile_close_consecutive,
        streak_after_expiry,
    )
    from app.services.risk_policy.persistence import RiskPolicyService
    from app.services.risk_policy.profile import PolicyKind as StoredPolicyKind

    selected = StoredPolicyKind.INTERNAL_PAPER if kind is None else kind
    active = _load_active_profile(session, organization_id, selected.value, now)
    if active is None:
        return None, False
    service = RiskPolicyService(session, AuditService(session))
    row = service.load_breaker(organization_id=organization_id, account_id=account_id)
    current = 0 if row is None else int(row.consecutive_losses)
    paused_until = None if row is None else getattr(row, "consecutive_paused_until", None)
    current = streak_after_expiry(current, paused_until, now)
    pause_in_force = paused_until is not None and _as_utc(paused_until) > _as_utc(now)
    consecutive = profile_close_consecutive(current, net_pnl, pause_in_force=pause_in_force)
    refused = persist_profile_breakers(
        session,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        profile=active.profile,
        now=now,
        consecutive_losses=consecutive,
    )
    return consecutive, refused


def _attach_approval_channel(
    session: Session,
    payload: dict[str, object],
    authorization_id: UUID | None,
) -> None:
    """Copy the authorization channel onto a new journal payload.

    Replay replaces this payload with the recorded event, so a legacy event
    that has no channel stays a human approval. A missing authorization is
    left unset for the same reason.
    """

    if authorization_id is None or "approval_channel" in payload:
        return
    row = session.get(ApprovalAuthorizationRow, authorization_id)
    if row is None:
        return
    channel = row.channel
    payload["approval_channel"] = channel.value if hasattr(channel, "value") else str(channel)


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


def _closed_risk_facts(
    session: Session,
    *,
    organization_id: UUID,
    candidate_id: UUID | None,
    net_pnl: Decimal,
) -> dict[str, str]:
    """Copy grade facts onto the close payload. The trade row stays append-only."""

    if candidate_id is None:
        return {}
    from app.db.risk_policy import RiskPolicyTradeRecord

    row = session.scalar(
        select(RiskPolicyTradeRecord).where(
            RiskPolicyTradeRecord.organization_id == organization_id,
            RiskPolicyTradeRecord.trade_id == str(candidate_id),
        )
    )
    if row is None:
        return {}
    calculation = row.calculation if isinstance(row.calculation, dict) else {}
    facts = {"grade": str(row.grade), "outcome": "closed"}
    for key in ("effective_risk_percent", "leverage", "entry", "stop", "target", "planned_r"):
        if key in calculation:
            name = "risk_percent" if key == "effective_risk_percent" else key
            facts[name] = str(calculation[key])
    raw_risk = calculation.get("risk_amount")
    if raw_risk is not None:
        amount = Decimal(str(raw_risk))
        if amount > 0:
            facts["r_multiple"] = format(
                (net_pnl / amount).quantize(Decimal("0.00000001")),
                "f",
            )
    return facts
