"""Conversation adapter to canonical paper authorities. Caller owns the transaction.

No Candidate mint, detector, venue submission, or journal writes occur here.
The transcript binds what was presented; immutable plans, approval issuance,
claim idempotency and journal projection remain the existing authorities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError
from app.core.operation_policy import operation_scope
from app.db.models import Conversation, ConversationMessage, ExecutionAccount
from app.evidence_pipeline.http_schemas import CanonicalEvidenceRead
from app.repositories.journal_trades import JournalTradeRepository
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.agent import Intent, IntentDecision, OperationClass, PrincipalRef, RequestedAction
from app.schemas.agent_paper import AgentPaperConfirmation, AgentPaperResult, AgentPaperTradeIntent
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.canonical_trade_plan import CanonicalTradePlanCommand
from app.schemas.common import ApprovalAction, ConversationMessageRole, RiskAction, TradeDirection
from app.schemas.execution_protocol import ExecutePaperPlanRequest, ExecutionCommandOutcome
from app.schemas.position_sizing import PaperPositionSizingRequest
from app.schemas.risk import RiskCheckRequest, RiskCheckResult
from app.schemas.trade_plan import AuthorizationChannel, EntrySide, TradePlanRevisionCreate
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import paper_loop_posture_refusal
from app.services.execution_service import ExecutionService
from app.services.internal_paper_plan_terms import (
    INTERNAL_PAPER_LOT_SIZE,
    INTERNAL_PAPER_MIN_NOTIONAL,
    INTERNAL_PAPER_TICK_SIZE,
    build_internal_paper_terms,
)
from app.services.position_sizing_service import PositionSizingService
from app.services.pretrade_analysis_service import PreTradeAnalysisService
from app.services.quota_service import QuotaService
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.paper_cooldown import paper_loss_cooldown_active
from app.services.risk.rules import RiskEvaluationContext, default_is_weekend
from app.services.risk.settings_service import RiskSettingsService
from app.services.risk_service import RiskService
from app.signal_fusion.enums import CandidateState


class CanonicalPaperEvidencePort(Protocol):
    def read(self, *, organization_id: UUID, symbol: str) -> CanonicalEvidenceRead: ...


@dataclass(frozen=True)
class PaperQuote:
    price: Decimal
    source_time: datetime
    valid_until: datetime


class AgentPaperExecutionService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        runtime: ProductionCanonicalRuntime,
        evidence: CanonicalPaperEvidencePort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._runtime = runtime
        self._evidence = evidence
        self._audit = AuditService(session)
        self._risk = RiskService()
        self._risk_settings = RiskSettingsService(session, self._audit)
        self._daily = DailyRiskAccounting(session, self._risk_settings)
        self._approval = ApprovalService(
            session, self._audit, plans=runtime.plans, clock=runtime.clock.now
        )
        self._execution = ExecutionService(
            session, settings, self._audit, canonical_runtime=runtime
        )

    def _lock_conversation(
        self, organization_id: UUID, user_id: UUID, conversation_id: UUID
    ) -> None:
        refusal = paper_loop_posture_refusal(self._settings)
        if refusal:
            raise TradingPolicyError(
                "Internal paper execution is unavailable.", details={"reason": refusal}
            )
        row = self._session.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.organization_id == organization_id,
                Conversation.user_id == user_id,
            )
            .with_for_update(key_share=True)
        )
        if row is None:
            raise NotFoundError("Conversation not found.")

    def prepare(
        self,
        intent: AgentPaperTradeIntent,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
    ) -> AgentPaperResult:
        self._lock_conversation(organization_id, user_id, conversation_id)
        now = self._runtime.clock.now()
        with self._runtime.bind_session(self._session):
            candidate = self._runtime.lifecycle.get_by_candidate_id(
                organization_id, intent.candidate_id
            )
            if candidate is None:
                raise NotFoundError("Canonical Candidate not found.")
            # Eligibility and the execution account bind the user, not an Agent-provided tenant.
            account = self._session.scalar(
                select(ExecutionAccount).where(
                    ExecutionAccount.id == intent.account_id,
                    ExecutionAccount.organization_id == organization_id,
                    ExecutionAccount.user_id == user_id,
                    ExecutionAccount.enabled.is_(True),
                )
            )
            if account is None:
                raise NotFoundError("Paper execution account not found.")
            if account.execution_mode.value != "PAPER" or account.account_mode.value != "NET":
                raise TradingPolicyError("Paper NET account required.")
            identity = (
                candidate.evidence_identity.instrument.provider_symbol,
                candidate.evidence_venue.value,
                candidate.evidence_market.name,
                candidate.timeframe,
                candidate.direction,
            )
            requested = (
                intent.symbol,
                intent.venue,
                intent.market.value,
                intent.timeframe,
                intent.direction,
            )
            if identity != requested or intent.market.value != "PERPETUAL":
                raise TradingPolicyError(
                    "Trade market identity must match the canonical Candidate."
                )
            history = self._runtime.eligibility.history(
                organization_id=organization_id,
                account_id=intent.account_id,
                candidate_id=intent.candidate_id,
            )
            if not history:
                raise TradingPolicyError("Current canonical ActionEligibility is required.")
            evaluation = history[-1]
            if (
                evaluation.eligibility.user_id != user_id
                or not evaluation.currently_paper_actionable(now)
            ):
                raise TradingPolicyError("Canonical ActionEligibility is unavailable or blocked.")
            if candidate.state is not CandidateState.ACTIVE or candidate.valid_until <= now:
                raise TradingPolicyError("An ACTIVE, current canonical Candidate is required.")
            quote = self._current_quote(
                intent.symbol, intent.venue, organization_id, candidate.evidence_instrument
            )
            pretrade = PreTradeAnalysisService.analyze_paper_trade(
                intent, current_price=quote.price
            )
            for level in (intent.entry, intent.stop, *intent.targets):
                if level % INTERNAL_PAPER_TICK_SIZE != 0:
                    raise TradingPolicyError(
                        "Trade levels must respect the internal paper tick precision."
                    )
            snapshot = self._daily.sync_from_portfolio(
                organization_id=organization_id, user_id=user_id
            )
            settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
            approved_cash = (
                snapshot.account_equity * settings.max_risk_per_trade_percent / Decimal("100")
            )
            notional_cap = (
                snapshot.account_equity
                * self._risk.limits.max_position_pct_of_equity
                / Decimal("100")
            )
            sizing = PositionSizingService().calculate_paper(
                PaperPositionSizingRequest(
                    approved_risk_amount=approved_cash,
                    maximum_notional=max(
                        Decimal("0"), notional_cap - snapshot.open_exposure_notional
                    ),
                    entry=intent.entry,
                    stop=intent.stop,
                    lot_size=INTERNAL_PAPER_LOT_SIZE,
                    minimum_quantity=INTERNAL_PAPER_LOT_SIZE,
                    minimum_notional=INTERNAL_PAPER_MIN_NOTIONAL,
                )
            )
            risk = self._check_risk(
                organization_id=organization_id,
                user_id=user_id,
                symbol=intent.symbol,
                direction=intent.direction,
                entry=intent.entry,
                stop=intent.stop,
                quantity=sizing.quantity,
                maximum_loss=sizing.maximum_loss,
            )
            valid_until = min(
                candidate.valid_until,
                evaluation.eligibility.valid_until,
                quote.valid_until,
            )
            terms = build_internal_paper_terms(
                candidate=candidate,
                account_id=intent.account_id,
                entry=intent.entry,
                stop=intent.stop,
                target=intent.targets[0],
                quantity=sizing.quantity,
                observed=quote.source_time,
                now=now,
                valid_until=valid_until,
                freshness=10,
                symbol=intent.symbol,
                tick_size=INTERNAL_PAPER_TICK_SIZE,
                target_formula="explicit-user-target",
                presentation={"channel": "API", "display_title": "Agent paper execution proposal"},
            )
            payload = terms.model_dump(mode="python")
            fraction = (Decimal("1") / len(intent.targets)).quantize(Decimal("0.00000001"))
            payload["risk_and_exits"]["targets"] = [
                {
                    "order": i + 1,
                    "price": {"value": target, "unit": "USDT"},
                    "quantity_fraction": fraction
                    if i < len(intent.targets) - 1
                    else Decimal("1") - fraction * i,
                    "derivation": {"formula_id": "explicit-user-target", "formula_version": "1"},
                }
                for i, target in enumerate(intent.targets)
            ]
            payload["calculation_inputs"][0].update(
                input_value=sizing.raw_quantity,
                result_value=sizing.quantity,
                conservative_remainder=sizing.conservative_remainder,
            )
            # Reuse the exact existing internal paper policy: zero fees/funding/slippage.
            # Its allowances, precision and quantity units are preserved in the hashed plan.
            terms = TradePlanRevisionCreate.model_validate(payload)
            envelope = self._runtime.plans.create(
                CanonicalTradePlanCommand(
                    organization_id=organization_id,
                    user_id=user_id,
                    account_id=intent.account_id,
                    candidate_id=candidate.candidate_id,
                    eligibility_id=evaluation.eligibility.eligibility_id,
                    terms=terms,
                    idempotency_key=f"agent-paper:{candidate.candidate_id}",
                    correlation_id=uuid4(),
                )
            )
            approval = self._approval.create_for_plan_revision(
                revision_id=envelope.plan.revision_id,
                organization_id=organization_id,
                user_id=user_id,
                approval_reason="Explicit confirmation required for this exact revision and hash.",
            )
            command = (
                f"Confirm paper execution revision={envelope.plan.revision_id} "
                f"hash={envelope.plan.content_hash}"
            )
            return AgentPaperResult(
                stage="proposed",
                candidate_id=candidate.candidate_id,
                eligibility_id=evaluation.eligibility.eligibility_id,
                plan=envelope.plan,
                approval_id=approval.id,
                confirmation_message=command,
                risk_result=risk,
                pretrade=pretrade,
            )

    def confirm(
        self,
        confirmation: AgentPaperConfirmation,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
    ) -> AgentPaperResult:
        self._lock_conversation(organization_id, user_id, conversation_id)
        # Only a server-written assistant payload can bind confirmation. New proposals
        # (including invalid ones) supersede previous proposals in this conversation.
        messages = self._session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == ConversationMessageRole.ASSISTANT,
                ConversationMessage.intent.in_(
                    [Intent.PREPARE_PAPER_TRADE.value, Intent.CONFIRM_PAPER_EXECUTION.value]
                ),
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        )
        latest = next(
            (
                message
                for message in messages
                if message.intent == Intent.PREPARE_PAPER_TRADE.value
                or (message.payload and message.payload.get("paper_execution") is not None)
            ),
            None,
        )
        raw = (
            latest.payload.get("paper_execution") if latest is not None and latest.payload else None
        )
        if raw is None:
            raise TradingPolicyError(
                "No current execution proposal was presented in this conversation."
            )
        proposed = AgentPaperResult.model_validate(raw)
        return self.confirm_presented(
            proposed,
            confirmation,
            organization_id=organization_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    def confirm_presented(
        self,
        proposed: AgentPaperResult,
        confirmation: AgentPaperConfirmation,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
    ) -> AgentPaperResult:
        """Apply a server-presented proposal after its transcript confirmation gate.

        Interactive Agent callers must first hold its existing proposal lock and
        validate the immutable content hash and explicit confirmation statement.
        This adapter still rechecks canonical lineage, risk and execution authority.
        """
        self._lock_conversation(organization_id, user_id, conversation_id)
        if proposed.stage == "blocked" or (
            proposed.plan.revision_id != confirmation.revision_id
            or proposed.plan.content_hash != confirmation.plan_content_hash
        ):
            raise TradingPolicyError(
                "Stale confirmation: exact presented revision and hash required."
            )
        with self._runtime.bind_session(self._session):
            envelope = self._runtime.plans.assert_approval_preserves_semantics(
                organization_id=organization_id,
                user_id=user_id,
                revision_id=confirmation.revision_id,
                plan_content_hash=confirmation.plan_content_hash,
            )
            plan = envelope.plan
            approval = self._approval.get_scoped(
                proposed.approval_id, organization_id=organization_id, user_id=user_id
            )
            # Completed confirmations return the gateway's durable replay, never a new order.
            if proposed.stage != "executed":
                if plan.valid_until <= self._runtime.clock.now():
                    raise TradingPolicyError("Stale confirmation: plan validity elapsed.")
                candidate = self._runtime.lifecycle.get_by_candidate_id(
                    organization_id, proposed.candidate_id
                )
                history = self._runtime.eligibility.history(
                    organization_id=organization_id,
                    account_id=plan.account_id,
                    candidate_id=proposed.candidate_id,
                )
                if (
                    candidate is None
                    or candidate.state is not CandidateState.PLAN_CREATED
                    or candidate.valid_until <= self._runtime.clock.now()
                    or not history
                    or history[-1].eligibility.eligibility_id != envelope.lineage.eligibility_id
                    or not history[-1].currently_paper_actionable(self._runtime.clock.now())
                ):
                    raise TradingPolicyError(
                        "Stale confirmation: canonical proposal lineage changed."
                    )
                quote = self._current_quote(
                    plan.execution_instrument,
                    plan.evidence_venue,
                    organization_id,
                    plan.evidence_instrument,
                )
                if quote.price != plan.basis_policy.execution_price.value:
                    raise TradingPolicyError("Stale confirmation: canonical entry quote changed.")
                current_risk = self._check_risk(
                    organization_id=organization_id,
                    user_id=user_id,
                    symbol=plan.execution_instrument,
                    direction=TradeDirection.LONG
                    if plan.side is EntrySide.BUY
                    else TradeDirection.SHORT,
                    entry=plan.basis_policy.execution_price.value,
                    stop=plan.risk_and_exits.stop.value,
                    quantity=plan.quantity.value,
                    maximum_loss=plan.risk_and_exits.maximum_loss.value,
                )
                quota = QuotaService(self._session, audit_service=self._audit).check_feature(
                    organization_id,
                    "paper_execution",
                    user_id=user_id,
                    request_id=f"agent-paper-execute:{plan.revision_id}",
                )
                if quota.hard_blocked:
                    raise TradingPolicyError(quota.message)
                with operation_scope(
                    _decision(
                        Intent.APPROVE,
                        RequestedAction.APPROVE,
                        OperationClass.APPROVAL,
                        organization_id,
                        user_id,
                    )
                ):
                    approval = self._approval.decide(
                        approval.id,
                        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
                        principal_organization_id=organization_id,
                        principal_user_id=user_id,
                        channel=AuthorizationChannel.API,
                    )
            if proposed.stage == "executed":
                current_risk = proposed.risk_result
            if approval.authorization is None:
                raise TradingPolicyError("Exact plan authorization is missing.")
            from app.services.approval_service import assert_human_may_execute

            assert_human_may_execute(approval.authorization)
            with operation_scope(
                _decision(
                    Intent.EXECUTE_PAPER_PLAN,
                    RequestedAction.EXECUTE_PAPER_PLAN,
                    OperationClass.EXECUTION,
                    organization_id,
                    user_id,
                )
            ):
                result = self._execution.execute_paper_plan(
                    ExecutePaperPlanRequest(
                        organization_id=organization_id,
                        user_id=user_id,
                        account_id=plan.account_id,
                        authorization_id=approval.authorization.authorization_id,
                        revision_id=plan.revision_id,
                        idempotency_key=f"agent-paper-execute:{plan.revision_id}",
                    ),
                    clock=self._runtime.clock.now,
                )
                # The canonical gateway owns claim, fill idempotency and lifecycle projection.
                if result.outcome is ExecutionCommandOutcome.ALLOW and not result.replayed:
                    self._execution.apply_paper_plan_fill(
                        command_id=result.command_id,
                        fill_quantity=plan.quantity.value,
                        fill_price=plan.basis_policy.execution_price.value,
                        source_identity=f"agent-paper-fill:{plan.revision_id}",
                        occurred_at=self._runtime.clock.now(),
                        venue_source="paper_internal",
                    )
            if result.outcome is ExecutionCommandOutcome.ALLOW and not result.replayed:
                QuotaService(self._session).record_request_usage(
                    organization_id=organization_id,
                    user_id=user_id,
                    request_id=f"agent-paper-execute:{plan.revision_id}",
                    feature="paper_execution",
                    provider="paper-engine",
                )
            journal = JournalTradeRepository(self._session).find_by_execution_lifecycle(
                organization_id=organization_id,
                execution_lifecycle_id=result.command_id,
            )
            return proposed.model_copy(
                update={
                    "stage": "executed"
                    if result.outcome is ExecutionCommandOutcome.ALLOW
                    else "blocked",
                    "authorization_id": approval.authorization.authorization_id,
                    "paper_action_id": result.command_id,
                    "receipt_id": result.receipt.receipt_id,
                    "journal_trade_id": journal.id if journal else None,
                    "replayed": result.replayed,
                    "reason_code": result.blocked_reason_code,
                    "risk_result": current_risk,
                }
            )

    def _current_quote(
        self, symbol: str, venue: str, organization_id: UUID, instrument_id: str
    ) -> PaperQuote:
        read = self._evidence.read(organization_id=organization_id, symbol=symbol)
        quote = read.current_price
        now = self._runtime.clock.now()
        if (
            read.source.instrument_id != instrument_id
            or read.source.venue != venue
            or read.source.provider_symbol != symbol
            or read.source.market_type != "perpetual"
            or not read.source.is_live
            or read.source.is_mock
            or not quote.usable_as_current_market_price
            or not quote.is_live
            or quote.is_mock
            or quote.fallback_used
            or quote.price is None
            or quote.source_time is None
            or quote.source_time > now
            or quote.freshness.valid_until is None
            or quote.freshness.valid_until <= now
        ):
            raise TradingPolicyError(
                "Fresh canonical perpetual evidence is required; no fallback price."
            )
        return PaperQuote(
            price=Decimal(quote.price),
            source_time=quote.source_time,
            valid_until=quote.freshness.valid_until,
        )

    def _check_risk(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        symbol: str,
        direction: TradeDirection,
        entry: Decimal,
        stop: Decimal,
        quantity: Decimal,
        maximum_loss: Decimal,
    ) -> RiskCheckResult:
        snapshot = self._daily.sync_from_portfolio(organization_id=organization_id, user_id=user_id)
        if paper_loss_cooldown_active(
            self._session,
            organization_id=organization_id,
            user_id=user_id,
            seconds=self._settings.paper_signal_cooldown_after_loss_seconds,
            now=self._runtime.clock.now(),
        ):
            raise TradingPolicyError(
                "Paper cooldown active.", details={"reason": "cooldown_active"}
            )
        settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
        approved_cash = (
            snapshot.account_equity * settings.max_risk_per_trade_percent / Decimal("100")
        )
        if maximum_loss > approved_cash:
            raise TradingPolicyError("Current approved monetary risk no longer covers this plan.")
        kill = KillSwitchService(self._session, self._audit, self._settings).evaluate(
            organization_id=organization_id
        )
        risk = self._risk.check(
            RiskCheckRequest(
                symbol=symbol,
                direction=direction,
                entry_price=entry,
                stop_loss=stop,
                position_size=quantity,
                leverage=Decimal("1"),
                account_equity=snapshot.account_equity,
            ),
            context=RiskEvaluationContext(
                daily_locked=snapshot.daily_locked,
                realized_pnl_today=snapshot.realized_pnl,
                daily_loss_limit=snapshot.daily_loss_limit,
                trades_today=snapshot.trade_count,
                kill_switch_active=kill.blocked,
                open_exposure_notional=snapshot.open_exposure_notional,
                overtrading=settings.overtrading_guard_enabled
                and snapshot.trade_count >= settings.max_trades_per_day,
                protect_green_day=settings.green_day_protection_enabled
                and settings.daily_target is not None
                and snapshot.realized_pnl >= settings.daily_target,
                is_weekend=default_is_weekend(),
            ),
        )
        if risk.action is RiskAction.BLOCK:
            raise TradingPolicyError(
                "Risk BLOCK is final.", details={"risk_result": risk.model_dump(mode="json")}
            )
        return risk


def _decision(
    intent: Intent,
    action: RequestedAction,
    operation: OperationClass,
    organization_id: UUID,
    user_id: UUID,
) -> IntentDecision:
    return IntentDecision(
        intent=intent,
        requested_action=action,
        operation_class=operation,
        organization_id=organization_id,
        principal=PrincipalRef(user_id=user_id),
        explicit_confirmation=True,
    )
