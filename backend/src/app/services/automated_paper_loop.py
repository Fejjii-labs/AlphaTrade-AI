"""Paper-only continuation after a Watcher CONFIRMED_SETUP Candidate.

The Watcher remains the setup authority. This module does not detect setups,
does not mint Candidates, and does not override risk. It sequences the existing
ActionEligibility, CanonicalTradePlanService, and internal paper fill services.

Paper internal only. Real trading, BloFin, exchange mutation, and Telegram are
outside this path. Duplicate calls converge on the same plan, fill, and journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.db.models import AccountSafetyEpoch, ExecutionAccount, ExecutionFillFact, JournalTrade
from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.audit import AuditRecordCreate
from app.schemas.canonical_trade_plan import CanonicalTradePlanCommand
from app.schemas.common import (
    ActorType,
    ApprovalAction,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    JournalTradeStatus,
    TradeDirection,
)
from app.schemas.execution_protocol import ExecutePaperPlanRequest, ExecutionCommandOutcome
from app.schemas.trade_plan import (
    AuthorizationChannel,
    TradePlanRevisionCreate,
)
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.canonical_trade_plan import CanonicalTradePlanService
from app.services.execution_service import ExecutionService
from app.services.internal_paper_plan_terms import (
    INTERNAL_PAPER_LOT_SIZE,
    INTERNAL_PAPER_MIN_NOTIONAL,
    INTERNAL_PAPER_TICK_SIZE,
    build_internal_paper_terms,
)
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.engine import RiskEngine
from app.services.risk.settings_service import RiskSettingsService
from app.signal_fusion.action_eligibility import (
    AccountIdentity,
    ActionEligibilityCommand,
    ActionEligibilityService,
    MarketActionEvidence,
    PaperExecutionConfiguration,
    PortfolioState,
    RiskStateSnapshot,
    SafetyStateSnapshot,
)
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import ActionEligibilityState, SetupAssessmentState
from app.signal_fusion.evaluator import first_slice_short_invalidation_price
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.ports import Clock
from app.signal_fusion.strategy_evaluation_policy import ExecutableStrategyPolicy
from app.workers.watcher_paper_targets import PaperScanTarget

_NAMESPACE = UUID("c44ea7de-0008-4000-8000-a070e1100001")
_POLICY_VERSION = "paper-internal-entry/v1"
_QUOTE_FRESH_SECONDS = 10
_LOT = INTERNAL_PAPER_LOT_SIZE
_TICK = INTERNAL_PAPER_TICK_SIZE
_MIN_NOTIONAL = INTERNAL_PAPER_MIN_NOTIONAL


@dataclass(frozen=True, slots=True)
class AutomatedPaperLoopProof:
    """Runtime proof of how far the paper continuation went. Empty ids mean no write."""

    stage: str
    reason_code: str
    replayed: bool = False
    candidate_id: UUID | None = None
    eligibility_id: UUID | None = None
    eligibility_state: str | None = None
    trade_plan_revision_id: UUID | None = None
    execution_command_id: UUID | None = None
    paper_fill_id: UUID | None = None
    journal_trade_id: UUID | None = None
    journal_status: str | None = None


def _global_kill_switch_active(settings: object) -> bool:
    """True only for an explicit boolean global kill switch."""

    return getattr(settings, "global_kill_switch_active", None) is True


def paper_loop_posture_refusal(settings: Settings) -> str | None:
    """Return a refusal code when this process may not place an internal paper fill."""

    if settings.execution_mode is not ExecutionMode.PAPER:
        return "execution_mode_refused"
    if settings.enable_real_trading or settings.real_trading_enabled:
        return "real_trading_refused"
    if settings.exchange_mode is not ExchangeMode.PAPER_INTERNAL:
        return "exchange_mode_refused"
    return None


class AutomatedPaperLoop:
    """Sequence existing paper authorities after one confirmed Candidate."""

    def __init__(
        self,
        runtime: ProductionCanonicalRuntime,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._runtime = runtime
        self._settings = settings
        self._clock = clock

    def continue_confirmed_setup(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        kill_switch_active: bool,
    ) -> AutomatedPaperLoopProof:
        """Open one internal paper journal trade, or write nothing when a gate fails."""

        self._observed_kill_switch = kill_switch_active
        stopped = self._preface(
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            policy=policy,
            kill_switch_active=kill_switch_active,
        )
        if stopped is not None:
            return stopped
        account = self._account(session, target)
        if account is None:
            return _proof(candidate, "skipped", "execution_account_missing")
        with self._runtime.bind_session(session):
            return self._bound(
                session,
                target=target,
                candidate=candidate,
                assessment=assessment,
                window=window,
                assembled=assembled,
                policy=policy,
                account=account,
            )

    def _preface(
        self,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        kill_switch_active: bool,
    ) -> AutomatedPaperLoopProof | None:
        if kill_switch_active or _global_kill_switch_active(self._settings):
            return _proof(candidate, "blocked", "kill_switch_active")
        if not isinstance(getattr(self._settings, "global_kill_switch_active", None), bool):
            return _proof(candidate, "blocked", "kill_switch_unverified")
        refusal = self._posture_refusal()
        if refusal is not None:
            return _proof(candidate, "blocked", refusal)
        if assessment.state is not SetupAssessmentState.CONFIRMED_SETUP:
            return _proof(candidate, "skipped", "setup_not_confirmed")
        lineage = _lineage_refusal(
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            policy=policy,
        )
        if lineage is not None:
            return _proof(candidate, "blocked", lineage)
        evidence = _evidence_refusal(assembled, now=self._clock.now())
        if evidence is not None:
            return _proof(candidate, "skipped", evidence)
        from app.schemas.nested_continuation import NestedContinuationSpec
        from app.strategy_brain.sfp.contracts import SfpSpec

        if candidate.direction is not TradeDirection.SHORT and not isinstance(
            policy.authored_spec, (NestedContinuationSpec, SfpSpec)
        ):
            return _proof(candidate, "skipped", "direction_not_paper_executable")
        return None

    def _bound(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        account: ExecutionAccount,
    ) -> AutomatedPaperLoopProof:
        existing = self._runtime.plan_store.get_by_candidate_scope(
            organization_id=target.organization_id,
            user_id=target.user_id,
            account_id=account.id,
            candidate_id=candidate.candidate_id,
        )
        if existing is not None:
            if existing.plan.execution_policy_version != self._policy_version():
                return _proof(candidate, "blocked", "existing_plan_not_paper_loop")
            return self._execute_existing(
                session,
                target=target,
                candidate=candidate,
                revision_id=existing.plan.revision_id,
                account_id=account.id,
            )
        eligibility = self._eligibility_service().evaluate(
            self._eligibility_command(
                session,
                settings=self._settings,
                target=target,
                candidate=candidate,
                assessment=assessment,
                window=window,
                assembled=assembled,
                account=account,
                now=self._clock.now(),
            )
        )
        state = eligibility.eligibility.state
        if state is not ActionEligibilityState.ELIGIBLE or not eligibility.paper_actionable:
            self._audit(
                session,
                target=target,
                candidate=candidate,
                event_type=AuditEventType.RISK_BLOCK,
                result=AuditResult.BLOCKED,
                metadata={
                    "candidate_id": str(candidate.candidate_id),
                    "eligibility_id": str(eligibility.eligibility.eligibility_id),
                    "eligibility_state": state.value,
                    "trade_plan_revision_id": None,
                    "paper_fill_id": None,
                    "journal_trade_id": None,
                },
            )
            return AutomatedPaperLoopProof(
                stage="blocked",
                reason_code="risk_block" if _is_risk_block(state) else state.value,
                candidate_id=candidate.candidate_id,
                eligibility_id=eligibility.eligibility.eligibility_id,
                eligibility_state=state.value,
            )
        terms = self._plan_terms(
            candidate=candidate,
            assembled=assembled,
            policy=policy,
            account_id=account.id,
            equity=_equity(session, target, now=self._clock.now()),
            now=self._clock.now(),
            eligibility_valid_until=eligibility.eligibility.valid_until,
            eligibility_id=eligibility.eligibility.eligibility_id,
        )
        if isinstance(terms, str):
            return AutomatedPaperLoopProof(
                stage="skipped",
                reason_code=terms,
                candidate_id=candidate.candidate_id,
                eligibility_id=eligibility.eligibility.eligibility_id,
                eligibility_state=state.value,
            )
        envelope = self._plan_service().create(
            _plan_command(
                target=target,
                candidate=candidate,
                account_id=account.id,
                eligibility_id=eligibility.eligibility.eligibility_id,
                terms=terms,
            )
        )
        return self._execute_existing(
            session,
            target=target,
            candidate=candidate,
            revision_id=envelope.plan.revision_id,
            account_id=account.id,
            eligibility_id=eligibility.eligibility.eligibility_id,
            eligibility_state=state.value,
        )

    def _execute_existing(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        revision_id: UUID,
        account_id: UUID,
        eligibility_id: UUID | None = None,
        eligibility_state: str | None = None,
    ) -> AutomatedPaperLoopProof:
        plans = self._plan_service()
        envelope = plans.get_scoped(
            revision_id,
            organization_id=target.organization_id,
            user_id=target.user_id,
        )
        if eligibility_id is None:
            eligibility_id = envelope.lineage.eligibility_id
        if eligibility_state is None:
            eligibility_state = envelope.lineage.eligibility_state.value
        blocked = self._autonomous_approval_refusal(session, target.organization_id)
        if blocked is not None:
            return _proof(candidate, "blocked", blocked, revision_id=revision_id)
        from app.services.autonomous_trading import (
            AUTONOMOUS_ACTOR_TYPE,
            AutonomousMode,
            autonomous_decision_metadata,
        )

        approval = ApprovalService(
            session,
            AuditService(session),
            clock=self._clock.now,
            plans=plans,
        )
        pending = approval.create_for_plan_revision(
            revision_id=revision_id,
            organization_id=target.organization_id,
            user_id=target.user_id,
            approval_reason="paper-internal hash binding; no semantic change",
        )
        decided = approval.decide(
            pending.id,
            ApprovalDecisionRequest(action=_approve_action(), modified_fields=None),
            principal_organization_id=target.organization_id,
            principal_user_id=target.user_id,
            channel=AuthorizationChannel.AUTONOMOUS,
            actor_type=AUTONOMOUS_ACTOR_TYPE,
            audit_metadata=autonomous_decision_metadata(
                mode=AutonomousMode.INTERNAL_PAPER,
                settings=self._settings,
                limits=None,
                limits_note=("account ledger limits are enforced at claim, not at this approval"),
            ),
        )
        if decided.authorization is None:
            return _proof(candidate, "blocked", "authorization_missing", revision_id=revision_id)
        blocked = self._autonomous_approval_refusal(session, target.organization_id)
        if blocked is not None:
            approval.revoke_authorization(
                decided.authorization.authorization_id,
                organization_id=target.organization_id,
                user_id=target.user_id,
                reason="post_approval_recheck",
            )
            return _proof(candidate, "blocked", blocked, revision_id=revision_id)
        execution = ExecutionService(
            session,
            self._settings,
            AuditService(session),
            canonical_runtime=self._runtime,
        )
        result = execution.execute_paper_plan(
            _execute_request(
                target=target,
                account_id=account_id,
                revision_id=revision_id,
                authorization_id=decided.authorization.authorization_id,
                candidate_id=candidate.candidate_id,
            ),
            clock=self._clock.now,
        )
        if result.outcome is ExecutionCommandOutcome.ALLOW and result.command_id is not None:
            already_open = _find_open_journal(
                session,
                organization_id=target.organization_id,
                command_id=result.command_id,
                candidate_id=candidate.candidate_id,
            )
            if already_open is not None:
                return AutomatedPaperLoopProof(
                    stage="filled",
                    reason_code="open_journal",
                    replayed=True,
                    candidate_id=candidate.candidate_id,
                    eligibility_id=eligibility_id,
                    eligibility_state=eligibility_state,
                    trade_plan_revision_id=revision_id,
                    execution_command_id=result.command_id,
                    paper_fill_id=_fill_id(session, result.command_id),
                    journal_trade_id=already_open.id,
                    journal_status=_journal_status_value(already_open),
                )
        if result.outcome is not ExecutionCommandOutcome.ALLOW or result.command_id is None:
            self._audit(
                session,
                target=target,
                candidate=candidate,
                event_type=AuditEventType.PAPER_ORDER_REJECTED,
                result=AuditResult.BLOCKED,
                metadata=_audit_ids(
                    candidate=candidate,
                    eligibility_id=eligibility_id,
                    revision_id=revision_id,
                    command_id=result.command_id,
                    fill_id=None,
                    journal_id=None,
                    extra={"blocked_reason_code": result.blocked_reason_code},
                ),
            )
            return AutomatedPaperLoopProof(
                stage="blocked",
                reason_code=result.blocked_reason_code or "execution_blocked",
                replayed=result.replayed,
                candidate_id=candidate.candidate_id,
                eligibility_id=eligibility_id,
                eligibility_state=eligibility_state,
                trade_plan_revision_id=revision_id,
                execution_command_id=result.command_id,
            )
        filled = execution.apply_paper_plan_fill(
            command_id=result.command_id,
            fill_quantity=envelope.plan.quantity.value,
            fill_price=envelope.plan.basis_policy.execution_price.value,
            source_identity=f"paper-internal:{candidate.candidate_id}",
            occurred_at=self._clock.now(),
            venue_source="paper_internal",
        )
        trade = _open_journal(
            session,
            organization_id=target.organization_id,
            command_id=result.command_id,
            candidate_id=candidate.candidate_id,
        )
        self._audit(
            session,
            target=target,
            candidate=candidate,
            event_type=AuditEventType.JOURNAL_TRADE_CREATED,
            result=AuditResult.SUCCESS,
            metadata=_audit_ids(
                candidate=candidate,
                eligibility_id=eligibility_id,
                revision_id=revision_id,
                command_id=result.command_id,
                fill_id=filled.fill_id,
                journal_id=trade.id,
            ),
        )
        return AutomatedPaperLoopProof(
            stage="filled",
            reason_code="open_journal",
            replayed=result.replayed or filled.replayed,
            candidate_id=candidate.candidate_id,
            eligibility_id=eligibility_id,
            eligibility_state=eligibility_state,
            trade_plan_revision_id=revision_id,
            execution_command_id=result.command_id,
            paper_fill_id=filled.fill_id,
            journal_trade_id=trade.id,
            journal_status=trade.status.value
            if isinstance(trade.status, JournalTradeStatus)
            else str(trade.status),
        )

    def _autonomous_approval_refusal(self, session: Session, organization_id: UUID) -> str | None:
        """Re-check mode and kill switch immediately before auto-approval."""

        from app.services.autonomous_trading import (
            AutonomousMode,
            continuation_refusal,
            kill_switch_refusal,
        )

        refusal = continuation_refusal(self._settings, expected=AutonomousMode.INTERNAL_PAPER)
        if refusal is not None:
            return refusal
        observed = getattr(self, "_observed_kill_switch", None)
        if observed is not None and not isinstance(observed, bool):
            return "kill_switch_unverified"
        return kill_switch_refusal(
            self._settings,
            session,
            organization_id,
            observed=observed if isinstance(observed, bool) else None,
        )

    def _posture_refusal(self) -> str | None:
        refusal = paper_loop_posture_refusal(self._settings)
        if refusal is not None:
            return refusal
        from app.services.autonomous_trading import AutonomousMode, continuation_refusal

        return continuation_refusal(self._settings, expected=AutonomousMode.INTERNAL_PAPER)

    def _policy_version(self) -> str:
        return _POLICY_VERSION

    def _account(self, session: Session, target: PaperScanTarget) -> ExecutionAccount | None:
        return _paper_account(session, target)

    def _eligibility_command(
        self,
        session: Session,
        *,
        settings: Settings,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        account: ExecutionAccount,
        now: datetime,
    ) -> ActionEligibilityCommand:
        return _eligibility_command(
            session,
            settings=settings,
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            account=account,
            now=now,
        )

    def _plan_terms(
        self,
        *,
        candidate: Candidate,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        account_id: UUID,
        equity: Decimal,
        now: datetime,
        eligibility_valid_until: datetime,
        eligibility_id: UUID,
    ) -> TradePlanRevisionCreate | str:
        return _plan_terms(
            candidate=candidate,
            assembled=assembled,
            policy=policy,
            account_id=account_id,
            equity=equity,
            now=now,
            eligibility_valid_until=eligibility_valid_until,
            eligibility_id=eligibility_id,
        )

    def _eligibility_service(self) -> ActionEligibilityService:
        return ActionEligibilityService(store=self._runtime.eligibility_store, clock=self._clock)

    def _plan_service(self) -> CanonicalTradePlanService:
        return CanonicalTradePlanService(
            store=self._runtime.plan_store,
            lifecycle=self._runtime.lifecycle,
            eligibility=self._eligibility_service(),
            clock=self._clock,
        )

    def _audit(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        event_type: AuditEventType,
        result: AuditResult,
        metadata: dict[str, object],
    ) -> None:
        AuditService(session).record(
            AuditRecordCreate(
                request_id=f"paper-loop:{candidate.candidate_id}",
                trace_id=f"paper-loop:{target.scan_scope}",
                event_type=event_type,
                resource_type="automated_paper_loop",
                actor_type=ActorType.SYSTEM,
                action=event_type.value,
                result=result,
                severity=AuditSeverity.INFO
                if result is AuditResult.SUCCESS
                else AuditSeverity.HIGH,
                user_id=target.user_id,
                organization_id=target.organization_id,
                resource_id=str(candidate.candidate_id),
                metadata=metadata,
            )
        )


def _proof(
    candidate: Candidate,
    stage: str,
    reason_code: str,
    *,
    revision_id: UUID | None = None,
) -> AutomatedPaperLoopProof:
    return AutomatedPaperLoopProof(
        stage=stage,
        reason_code=reason_code,
        candidate_id=candidate.candidate_id,
        trade_plan_revision_id=revision_id,
    )


def _lineage_refusal(
    *,
    target: PaperScanTarget,
    candidate: Candidate,
    assessment: SetupAssessment,
    window: CanonicalEvidenceWindowV1,
    assembled: AssembledCanonicalEvidence,
    policy: ExecutableStrategyPolicy,
) -> str | None:
    orgs = {
        target.organization_id,
        candidate.organization_id,
        assessment.organization_id,
        window.organization_id,
        assembled.organization_id,
        policy.organization_id,
    }
    if len(orgs) != 1:
        return "wrong_tenant"
    versions = {
        target.strategy_version_id,
        candidate.strategy_version_id,
        assessment.strategy_version_id,
        window.strategy_version_id,
        policy.strategy_version_id,
    }
    setups = {
        target.compiled_setup_definition_id,
        candidate.setup_definition_id,
        assessment.setup_definition_id,
        window.compiled_setup_definition_id,
    }
    hashes = {
        target.compiled_content_hash,
        candidate.executable_setup.content_hash,
        window.compiled_setup_content_hash,
        policy.compiled_content_hash,
    }
    if len(versions) != 1 or len(setups) != 1 or len(hashes) != 1:
        return "wrong_strategy_lineage"
    if candidate.evidence_window_hash != window.content_hash:
        return "wrong_strategy_lineage"
    if assembled.evidence_window_hash != window.content_hash:
        return "wrong_strategy_lineage"
    if policy.fusion_policy.policy_version != candidate.fusion_policy_version:
        return "wrong_strategy_lineage"
    return None


def _evidence_refusal(assembled: AssembledCanonicalEvidence, *, now: datetime) -> str | None:
    if assembled.replay:
        return "evidence_not_live"
    quote = assembled.current_price
    if quote is None:
        return "evidence_unavailable"
    if quote.fallback_used or quote.is_mock or not quote.is_live:
        return "evidence_not_live"
    if not quote.usable_as_current_market_price:
        return "stale_evidence"
    age = (now - quote.source_time).total_seconds()
    if age < 0 or age > _QUOTE_FRESH_SECONDS:
        return "stale_evidence"
    if quote.freshness.valid_until <= now:
        return "stale_evidence"
    clocks = assembled.clocks
    if not clocks.closed_evidence_valid or not clocks.quote_fresh:
        return "stale_evidence"
    if assembled.trigger_bar.finality.value != "final":
        return "stale_evidence"
    return None


def _paper_account(session: Session, target: PaperScanTarget) -> ExecutionAccount | None:
    return session.scalar(
        select(ExecutionAccount)
        .where(
            ExecutionAccount.organization_id == target.organization_id,
            ExecutionAccount.user_id == target.user_id,
            ExecutionAccount.enabled.is_(True),
        )
        .order_by(ExecutionAccount.created_at.asc(), ExecutionAccount.id.asc())
        .limit(1)
    )


def _equity(session: Session, target: PaperScanTarget, *, now: datetime) -> Decimal:
    accounting = DailyRiskAccounting(session, _risk_settings(session), clock=lambda: now)
    snapshot = accounting.sync_from_portfolio(
        organization_id=target.organization_id,
        user_id=target.user_id,
    )
    return snapshot.account_equity


def _risk_settings(session: Session) -> RiskSettingsService:
    return RiskSettingsService(session, AuditService(session))


def _eligibility_command(
    session: Session,
    *,
    settings: Settings,
    target: PaperScanTarget,
    candidate: Candidate,
    assessment: SetupAssessment,
    window: CanonicalEvidenceWindowV1,
    assembled: AssembledCanonicalEvidence,
    account: ExecutionAccount,
    now: datetime,
) -> ActionEligibilityCommand:
    quote = assembled.current_price
    if quote is None:
        raise ValueError("Paper loop eligibility requires a current price.")
    accounting = DailyRiskAccounting(session, _risk_settings(session), clock=lambda: now)
    snapshot = accounting.sync_from_portfolio(
        organization_id=target.organization_id,
        user_id=target.user_id,
    )
    epoch_row = session.scalar(
        select(AccountSafetyEpoch).where(
            AccountSafetyEpoch.organization_id == target.organization_id,
            AccountSafetyEpoch.account_id == account.id,
        )
    )
    epoch = 1 if epoch_row is None else int(epoch_row.epoch)
    risk_id = uuid5(
        _NAMESPACE,
        f"risk:{account.id}:{snapshot.day.isoformat()}:{snapshot.daily_locked}:"
        f"{snapshot.realized_pnl}:{snapshot.trade_count}",
    )
    venue_state_id = uuid5(
        _NAMESPACE,
        f"venue:{window.content_hash}:{quote.price}:{quote.source_time.isoformat()}",
    )
    return ActionEligibilityCommand(
        candidate=candidate,
        assessment=assessment,
        evidence_window=window,
        account=AccountIdentity(
            organization_id=target.organization_id,
            user_id=target.user_id,
            account_id=account.id,
            account_active=bool(account.enabled),
        ),
        portfolio=PortfolioState(
            organization_id=target.organization_id,
            account_id=account.id,
            account_equity=snapshot.account_equity,
            open_exposure_notional=snapshot.open_exposure_notional,
        ),
        risk=RiskStateSnapshot(
            risk_snapshot_id=risk_id,
            organization_id=target.organization_id,
            user_id=target.user_id,
            account_id=account.id,
            daily_locked=snapshot.daily_locked,
            daily_loss_limit=snapshot.daily_loss_limit,
            realized_pnl_today=snapshot.realized_pnl,
            trades_today=snapshot.trade_count,
        ),
        safety=SafetyStateSnapshot(
            organization_id=target.organization_id,
            account_id=account.id,
            safety_epoch=epoch,
            kill_switch_active=False,
            global_kill_switch_active=bool(settings.global_kill_switch_active),
            kill_switch_unavailable=False,
        ),
        market_action=MarketActionEvidence(
            venue_state_id=venue_state_id,
            evidence_venue=candidate.evidence_venue,
            execution_venue=candidate.evidence_venue,
            evidence_price=quote.price,
            execution_price=quote.price,
            required_action_evidence_fresh=True,
            action_evidence_valid_until=quote.freshness.valid_until,
            basis_fresh=True,
            symbol=target.symbol,
        ),
        configuration=PaperExecutionConfiguration(
            execution_mode=ExecutionMode.PAPER,
            enable_real_trading=False,
            exchange_mode=ExchangeMode.PAPER_INTERNAL,
        ),
        correlation_id=candidate.correlation_id,
    )


def _plan_terms(
    *,
    candidate: Candidate,
    assembled: AssembledCanonicalEvidence,
    policy: ExecutableStrategyPolicy,
    account_id: UUID,
    equity: Decimal,
    now: datetime,
    eligibility_valid_until: datetime,
    eligibility_id: UUID,
) -> TradePlanRevisionCreate | str:
    from app.strategy_brain.sfp.contracts import SfpSpec

    if isinstance(policy.authored_spec, SfpSpec):
        return "sfp_execution_plan_not_authorized"
    quote = assembled.current_price
    if quote is None:
        return "evidence_unavailable"
    entry = quote.price
    from app.schemas.nested_continuation import NestedContinuationSpec
    from app.strategy_brain.detector import detect_nested
    from app.strategy_brain.records import scoped_setup_id

    nested = isinstance(policy.authored_spec, NestedContinuationSpec)
    nested_target = None
    if isinstance(policy.authored_spec, NestedContinuationSpec):
        events = detect_nested(
            assembled.bundle.bars_15m, policy.authored_spec, evaluated_at=assembled.evaluated_at
        )
        if not events or events[-1].state.value != "CONFIRMED":
            return "setup_not_confirmed"
        stop = events[-1].stop
        nested_target = events[-1].targets[0] if events[-1].targets else None
    else:
        from app.signal_fusion.first_slice_adapter import FirstSliceEvaluationParams

        if not isinstance(policy.evaluation_params, FirstSliceEvaluationParams):
            return "execution_policy_unsupported"
        stop = first_slice_short_invalidation_price(
            trigger=assembled.trigger_bar,
            bars_15m=assembled.bundle.bars_15m,
            tick_size=assembled.bundle.tick_size,
            params=policy.evaluation_params,
        )
    if stop is None:
        return "invalidation_unavailable"
    is_long = candidate.direction is TradeDirection.LONG
    stop = (
        _floor_to_lot(stop, assembled.bundle.tick_size)
        if is_long
        else _ceil_to_tick(stop, assembled.bundle.tick_size)
    )
    if (is_long and stop >= entry) or (not is_long and stop <= entry):
        return "invalidated_by_price"
    distance = abs(stop - entry)
    quantity = _quantity(equity=equity, entry=entry, distance=distance)
    if quantity is None:
        return "size_below_minimum"
    target = nested_target if nested else entry - distance
    if (
        target is None
        or target <= 0
        or (is_long and target <= entry)
        or (not is_long and target >= entry)
    ):
        return "invalidation_unavailable"
    observed = quote.source_time
    if observed > now:
        return "stale_evidence"
    age = (now - observed).total_seconds()
    freshness = max(_QUOTE_FRESH_SECONDS + 1, int(age) + 1)
    valid_until = min(candidate.valid_until, eligibility_valid_until)
    if valid_until <= now:
        return "candidate_expired"
    return build_internal_paper_terms(
        candidate=candidate,
        account_id=account_id,
        entry=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        observed=observed,
        now=now,
        valid_until=valid_until,
        freshness=freshness,
        symbol=quote.provider_symbol,
        tick_size=assembled.bundle.tick_size,
        target_formula="nested-measured-impulse" if nested else "paper-internal-1r",
        presentation={
            "channel": AuthorizationChannel.API.value,
            "display_title": "Internal paper confirmed setup",
            "strategy_id": str(policy.strategy_id),
            "strategy_version_id": str(candidate.strategy_version_id),
            "setup_id": str(
                scoped_setup_id(
                    policy.organization_id, policy.strategy_version_id, events[-1].setup_id
                )
            )
            if nested
            else str(candidate.setup_definition_id),
            "evidence_reference": candidate.evidence_window_hash,
            "decision_reference": str(eligibility_id),
        },
    )


def _quantity(*, equity: Decimal, entry: Decimal, distance: Decimal) -> Decimal | None:
    limits = RiskEngine().limits
    risk_pct = limits.max_position_pct_of_equity
    notional_cap = equity * risk_pct / Decimal("100")
    if notional_cap < _MIN_NOTIONAL or distance <= 0 or entry <= 0:
        return None
    risk_cash = equity * Decimal("1") / Decimal("100")
    raw = min(risk_cash / distance, notional_cap / entry)
    quantity = _floor_to_lot(raw, _LOT)
    if quantity < _LOT or quantity * entry < _MIN_NOTIONAL:
        return None
    if quantity * entry > notional_cap:
        return None
    return quantity


def _floor_to_lot(quantity: Decimal, lot: Decimal) -> Decimal:
    steps = (quantity / lot).to_integral_value(rounding=ROUND_FLOOR)
    return steps * lot


def _ceil_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    steps = (value / tick).to_integral_value(rounding=ROUND_CEILING)
    return steps * tick


def _plan_command(
    *,
    target: PaperScanTarget,
    candidate: Candidate,
    account_id: UUID,
    eligibility_id: UUID,
    terms: TradePlanRevisionCreate,
) -> CanonicalTradePlanCommand:
    return CanonicalTradePlanCommand(
        organization_id=target.organization_id,
        user_id=target.user_id,
        account_id=account_id,
        candidate_id=candidate.candidate_id,
        eligibility_id=eligibility_id,
        terms=terms,
        idempotency_key=f"paper-loop:{candidate.candidate_id}",
        correlation_id=candidate.correlation_id,
    )


def _execute_request(
    *,
    target: PaperScanTarget,
    account_id: UUID,
    revision_id: UUID,
    authorization_id: UUID,
    candidate_id: UUID,
) -> ExecutePaperPlanRequest:
    return ExecutePaperPlanRequest(
        organization_id=target.organization_id,
        user_id=target.user_id,
        account_id=account_id,
        authorization_id=authorization_id,
        revision_id=revision_id,
        idempotency_key=f"paper-exec:{candidate_id}",
    )


def _approve_action() -> ApprovalAction:
    return ApprovalAction.APPROVE


def _find_open_journal(
    session: Session,
    *,
    organization_id: UUID,
    command_id: UUID,
    candidate_id: UUID,
) -> JournalTrade | None:
    trade = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.execution_lifecycle_id == command_id,
        )
    )
    if trade is None or trade.candidate_id != candidate_id:
        return None
    if _journal_status(trade) is not JournalTradeStatus.OPEN:
        return None
    return trade


def _fill_id(session: Session, command_id: UUID) -> UUID | None:
    return session.scalar(
        select(ExecutionFillFact.id).where(ExecutionFillFact.command_id == command_id)
    )


def _journal_status(trade: JournalTrade) -> JournalTradeStatus:
    if isinstance(trade.status, JournalTradeStatus):
        return trade.status
    return JournalTradeStatus(str(trade.status))


def _journal_status_value(trade: JournalTrade) -> str:
    status = trade.status
    if isinstance(status, JournalTradeStatus):
        return status.value
    return str(status)


def _open_journal(
    session: Session,
    *,
    organization_id: UUID,
    command_id: UUID,
    candidate_id: UUID,
) -> JournalTrade:
    trade = session.scalar(
        select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.execution_lifecycle_id == command_id,
        )
    )
    if trade is None or trade.candidate_id != candidate_id:
        raise ValueError("Internal paper fill did not open the candidate journal trade.")
    if _journal_status(trade) is not JournalTradeStatus.OPEN:
        raise ValueError("Internal paper fill did not leave the journal trade open.")
    return trade


def _is_risk_block(state: ActionEligibilityState) -> bool:
    return state is ActionEligibilityState.BLOCKED


def _audit_ids(
    *,
    candidate: Candidate,
    eligibility_id: UUID | None,
    revision_id: UUID | None,
    command_id: UUID | None,
    fill_id: UUID | None,
    journal_id: UUID | None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": str(candidate.candidate_id),
        "eligibility_id": None if eligibility_id is None else str(eligibility_id),
        "trade_plan_revision_id": None if revision_id is None else str(revision_id),
        "execution_command_id": None if command_id is None else str(command_id),
        "paper_fill_id": None if fill_id is None else str(fill_id),
        "journal_trade_id": None if journal_id is None else str(journal_id),
        "exchange_mode": ExchangeMode.PAPER_INTERNAL.value,
        "execution_venue": "PAPER_INTERNAL",
    }
    if extra:
        payload.update(extra)
    return payload
