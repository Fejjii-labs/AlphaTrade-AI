"""Automated paper-signal orchestration (AT-038).

Deterministic eligibility → paper-validation candidate/run-plan → optional
approval-gated paper proposal. Never places live or paper orders.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ExecutionMode, Settings, get_settings
from app.core.errors import NotFoundError, ServiceUnavailableError, ValidationAppError
from app.db.models import BloFinDemoSyncSnapshot
from app.db.models import PaperSignalOrchestrationDecision as DecisionModel
from app.db.models import TradingViewSignal as SignalModel
from app.repositories.paper_signal_orchestration import PaperSignalOrchestrationRepository
from app.repositories.paper_validation_candidate import PaperValidationCandidateRepository
from app.repositories.paper_validation_run_plan import PaperValidationRunPlanRepository
from app.repositories.tradingview_signal import TradingViewSignalRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    PaperSignalOrchestrationMode,
    PaperSignalOrchestrationStatus,
    PaperValidationCandidateStatus,
    RiskSeverity,
    StrategyId,
    Timeframe,
    TradeDirection,
    TradingViewSignalStatus,
)
from app.schemas.paper_signal_orchestration import (
    APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM,
    EligibilityCheck,
    PaperSignalOrchestrationApproveRequest,
    PaperSignalOrchestrationApproveResult,
    PaperSignalOrchestrationDecisionItem,
    PaperSignalOrchestrationEvaluateResult,
    PaperSignalOrchestrationLinks,
    PaperSignalOrchestrationListResponse,
    PaperSignalOrchestrationTransition,
)
from app.schemas.paper_validation_candidate import PaperValidationCandidateStatusUpdate
from app.schemas.paper_validation_run_plan import (
    CREATE_PAPER_VALIDATION_RUN_PLAN_CONFIRM,
    PaperValidationRunPlanCreateRequest,
)
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.services.audit_service import AuditService
from app.services.paper_validation_candidate_service import PaperValidationCandidateService
from app.services.paper_validation_run_plan_service import PaperValidationRunPlanService
from app.services.proposal_service import ProposalService
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.settings_service import RiskSettingsService
from app.services.tradingview_signal_service import TradingViewSignalService

logger = structlog.get_logger(__name__)

_TERMINAL = {
    PaperSignalOrchestrationStatus.PAPER_PROPOSAL_CREATED.value,
    PaperSignalOrchestrationStatus.REJECTED.value,
    PaperSignalOrchestrationStatus.EXPIRED.value,
}


class PaperSignalOrchestrationService:
    """Evaluate TradingView signals and orchestrate paper-only follow-up."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        kill_switch: KillSwitchService | None = None,
        proposal_service: ProposalService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._decisions = PaperSignalOrchestrationRepository(session)
        self._signals = TradingViewSignalRepository(session)
        self._candidates = PaperValidationCandidateRepository(session)
        self._plans = PaperValidationRunPlanRepository(session)
        self._tv = TradingViewSignalService(session, self._settings)
        self._candidate_svc = PaperValidationCandidateService(session)
        self._plan_svc = PaperValidationRunPlanService(session)
        self._audit = audit_service or AuditService(session)
        self._kill_switch = kill_switch or KillSwitchService(session, self._audit, self._settings)
        self._proposals = proposal_service or ProposalService(session, self._audit)
        self._daily_risk = DailyRiskAccounting(session, RiskSettingsService(session, self._audit))

    def list_decisions(
        self,
        *,
        organization_id: uuid.UUID,
        status: PaperSignalOrchestrationStatus | None = None,
        symbol: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaperSignalOrchestrationListResponse:
        rows, total = self._decisions.list_for_org(
            organization_id=organization_id,
            status=status,
            symbol=symbol,
            limit=limit,
            offset=offset,
        )
        return PaperSignalOrchestrationListResponse(
            items=[self._to_item(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
            mode=self._resolve_mode(),
            enabled=self._settings.paper_signal_orchestration_enabled,
        )

    def get_decision(
        self, decision_id: uuid.UUID, *, organization_id: uuid.UUID
    ) -> PaperSignalOrchestrationDecisionItem:
        row = self._decisions.get_for_org(decision_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("Orchestration decision not found.")
        return self._to_item(row)

    def evaluate(
        self,
        signal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
        advance: bool = False,
    ) -> PaperSignalOrchestrationEvaluateResult:
        self._assert_enabled()
        signal = self._signals.get_for_org(
            signal_id, organization_id=organization_id, for_update=True
        )
        if signal is None:
            raise NotFoundError("TradingView signal not found.")

        existing = self._decisions.get_by_signal(
            organization_id=organization_id, tradingview_signal_id=signal_id
        )
        if existing is not None and existing.status in _TERMINAL:
            return PaperSignalOrchestrationEvaluateResult(
                decision=self._to_item(existing), already_exists=True
            )

        eligibility, risk, status, reason_codes, summary = self._evaluate_signal(
            signal, organization_id=organization_id, user_id=user_id
        )
        mode = self._resolve_mode()
        now = datetime.now(UTC)
        idempotency_key = f"pso:{signal.id}"

        if existing is None:
            row = DecisionModel(
                organization_id=organization_id,
                tradingview_signal_id=signal.id,
                idempotency_key=idempotency_key,
                status=status.value,
                mode=mode.value,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                direction=signal.direction,
                reason_codes=reason_codes,
                reason_summary=summary,
                eligibility_evidence={"checks": [c.model_dump() for c in eligibility]},
                risk_evidence={"checks": [c.model_dump() for c in risk]},
                transitions=[
                    {
                        "at": now.isoformat(),
                        "from_status": None,
                        "to_status": status.value,
                        "reason": summary or status.value,
                        "actor_user_id": str(user_id),
                    }
                ],
                setup_definition_id=signal.setup_definition_id,
                strategy_id=signal.strategy_id,
                strategy_version_id=signal.strategy_version_id,
                journal_trade_id=signal.journal_trade_id,
                backtest_run_id=signal.backtest_run_id,
                candidate_id=signal.candidate_id,
                decided_by=user_id,
                decided_at=now,
                expired_at=now if status == PaperSignalOrchestrationStatus.EXPIRED else None,
            )
            self._decisions.add(row)
            already = False
        else:
            row = existing
            self._refresh_decision_fields(
                row,
                signal=signal,
                status=status,
                mode=mode,
                reason_codes=reason_codes,
                summary=summary,
                eligibility=eligibility,
                risk=risk,
                user_id=user_id,
                now=now,
            )
            already = True

        self._record_audit(
            AuditEventType.PAPER_SIGNAL_ORCHESTRATION_EVALUATED,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            request_id=request_id,
            metadata={
                "signal_id": str(signal.id),
                "status": row.status,
                "mode": row.mode,
                "reason_codes": reason_codes[:20],
                "advance": advance,
            },
        )

        if advance and status == PaperSignalOrchestrationStatus.ELIGIBLE:
            self._advance_by_mode(
                row,
                signal=signal,
                organization_id=organization_id,
                user_id=user_id,
                request_id=request_id,
            )

        self._session.flush()
        return PaperSignalOrchestrationEvaluateResult(
            decision=self._to_item(row), already_exists=already
        )

    def orchestrate(
        self,
        signal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PaperSignalOrchestrationEvaluateResult:
        return self.evaluate(
            signal_id,
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
            advance=True,
        )

    def approve_paper_proposal(
        self,
        decision_id: uuid.UUID,
        payload: PaperSignalOrchestrationApproveRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PaperSignalOrchestrationApproveResult:
        self._assert_enabled()
        if payload.confirm != APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM:
            raise ValidationAppError(
                "Exact confirmation required to approve a paper signal proposal.",
                details={"required_confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
            )
        if self._resolve_mode() != PaperSignalOrchestrationMode.APPROVAL_REQUIRED:
            raise ValidationAppError(
                "Paper proposal approval is only available in approval_required mode.",
                details={"mode": self._resolve_mode().value},
            )

        row = self._decisions.get_for_org(decision_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("Orchestration decision not found.")
        signal = self._signals.get_for_org(
            row.tradingview_signal_id, organization_id=organization_id, for_update=True
        )
        if signal is None:
            raise NotFoundError("Linked TradingView signal not found.")
        # Use the same signal lock as orchestration, then refresh concurrent approval state.
        self._session.refresh(row)
        if row.proposal_id is not None:
            return PaperSignalOrchestrationApproveResult(
                decision=self._to_item(row),
                proposal_id=row.proposal_id,
                already_exists=True,
            )
        if row.status != PaperSignalOrchestrationStatus.AWAITING_REVIEW.value:
            raise ValidationAppError(
                "Only awaiting_review decisions can be approved for paper proposals.",
                details={"status": row.status},
            )

        # Re-check risk/eligibility fail-closed before creating a proposal.
        eligibility, risk, status, reason_codes, summary = self._evaluate_signal(
            signal, organization_id=organization_id, user_id=user_id
        )
        if status != PaperSignalOrchestrationStatus.ELIGIBLE:
            self._transition(
                row,
                to=status,
                reason=summary or "revalidation_failed",
                user_id=user_id,
            )
            row.reason_codes = reason_codes
            row.reason_summary = summary
            row.eligibility_evidence = {"checks": [c.model_dump() for c in eligibility]}
            row.risk_evidence = {"checks": [c.model_dump() for c in risk]}
            self._record_audit(
                AuditEventType.PAPER_SIGNAL_ORCHESTRATION_BLOCKED,
                organization_id=organization_id,
                user_id=user_id,
                resource_id=str(row.id),
                request_id=request_id,
                metadata={"reason_codes": reason_codes[:20]},
                result=AuditResult.FAILURE,
            )
            raise ValidationAppError(
                "Signal is no longer eligible for a paper proposal.",
                details={"status": status.value, "reason_codes": reason_codes},
            )

        candidate = (
            self._candidates.get_for_org(row.candidate_id, organization_id=organization_id)
            if row.candidate_id is not None
            else None
        )
        if (
            candidate is None
            or signal.candidate_id != candidate.id
            or candidate.candidate_status == PaperValidationCandidateStatus.ARCHIVED.value
        ):
            raise ValidationAppError("An active linked paper candidate is required for approval.")

        proposal = self._create_paper_proposal(
            signal, organization_id=organization_id, user_id=user_id
        )
        assert proposal.id is not None
        row.proposal_id = proposal.id
        row.approved_by = user_id
        row.approved_at = datetime.now(UTC)
        self._transition(
            row,
            to=PaperSignalOrchestrationStatus.PAPER_PROPOSAL_CREATED,
            reason="human_approved_paper_proposal",
            user_id=user_id,
        )
        self._record_audit(
            AuditEventType.PAPER_SIGNAL_ORCHESTRATION_PROPOSAL,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            request_id=request_id,
            metadata={"proposal_id": str(proposal.id), "signal_id": str(signal.id)},
        )
        self._session.flush()
        return PaperSignalOrchestrationApproveResult(
            decision=self._to_item(row),
            proposal_id=proposal.id,
            already_exists=False,
        )

    def _advance_by_mode(
        self,
        row: DecisionModel,
        *,
        signal: SignalModel,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        request_id: str | None,
    ) -> None:
        mode = PaperSignalOrchestrationMode(row.mode)
        if mode == PaperSignalOrchestrationMode.OBSERVE_ONLY:
            return

        candidate = self._tv._create_candidate_internal(
            signal,
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
        )
        row.candidate_id = candidate.id
        self._record_audit(
            AuditEventType.PAPER_SIGNAL_ORCHESTRATION_CANDIDATE,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            request_id=request_id,
            metadata={"candidate_id": str(candidate.id)},
        )

        if self._settings.paper_signal_create_run_plan:
            plan_id = self._ensure_run_plan(
                candidate.id,
                signal=signal,
                organization_id=organization_id,
                user_id=user_id,
            )
            if plan_id is not None:
                row.run_plan_id = plan_id

        if mode == PaperSignalOrchestrationMode.CANDIDATE_ONLY:
            self._transition(
                row,
                to=PaperSignalOrchestrationStatus.PAPER_CANDIDATE_CREATED,
                reason="candidate_only_mode",
                user_id=user_id,
            )
            return

        # approval_required: candidate ready, await explicit proposal approval
        self._transition(
            row,
            to=PaperSignalOrchestrationStatus.AWAITING_REVIEW,
            reason="approval_required_mode",
            user_id=user_id,
        )

    def _ensure_run_plan(
        self,
        candidate_id: uuid.UUID,
        *,
        signal: SignalModel,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> uuid.UUID | None:
        existing = self._plans.get_active_for_candidate(organization_id, candidate_id)
        if existing is not None:
            return existing.id

        candidate = self._candidates.get_for_org(candidate_id, organization_id=organization_id)
        if candidate is None:
            return None
        if candidate.candidate_status == PaperValidationCandidateStatus.QUEUED.value:
            self._candidate_svc.update_status(
                candidate_id,
                PaperValidationCandidateStatusUpdate(
                    candidate_status=PaperValidationCandidateStatus.REVIEWING
                ),
                organization_id=organization_id,
                user_id=user_id,
            )

        result = self._plan_svc.create_from_candidate(
            candidate_id,
            PaperValidationRunPlanCreateRequest(
                confirm=CREATE_PAPER_VALIDATION_RUN_PLAN_CONFIRM,
                validation_window="1d",
                observation_timeframe=signal.timeframe,
                max_duration_minutes=1440,
                planned_entry_rule=(
                    f"Paper validate TradingView {signal.direction} entry near "
                    f"{signal.trigger_level or 'signal trigger'}."
                ),
                planned_invalidation_rule=(
                    "Invalidate beyond stop/invalidation level or on stale/degraded data."
                ),
                planned_success_criteria=(
                    "Thesis holds through observation window without rule breaks."
                ),
                planned_failure_criteria=(
                    "Stop/invalidation hit, contradictory structure, or risk block."
                ),
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        return result.plan.plan_id

    def _create_paper_proposal(
        self,
        signal: SignalModel,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Any:
        entry = signal.trigger_level
        stop = signal.stop_loss_level or signal.invalidation_level
        take_profit = signal.take_profit_level
        if entry is None or stop is None or take_profit is None:
            raise ValidationAppError(
                "Paper proposal requires trigger, stop/invalidation, and take-profit levels.",
                details={
                    "trigger_level": entry,
                    "stop_loss_level": stop,
                    "take_profit_level": take_profit,
                },
            )
        try:
            timeframe = Timeframe(signal.timeframe)
        except ValueError as exc:
            raise ValidationAppError(
                "Unsupported timeframe for paper proposal.",
                details={"timeframe": signal.timeframe},
            ) from exc

        confidence = float(signal.confidence) if signal.confidence is not None else 0.5
        data = TradeProposalCreate(
            organization_id=organization_id,
            user_id=user_id,
            signal_id=None,
            strategy_id=StrategyId.MANUAL_REVIEW,
            symbol=signal.symbol,
            timeframe=timeframe,
            direction=TradeDirection(signal.direction),
            entry_price=Decimal(str(entry)),
            position_size=Decimal(str(self._settings.paper_signal_default_position_size)),
            leverage=Decimal(str(self._settings.paper_signal_default_leverage)),
            exit=ExitCriteria(
                invalidation=(
                    f"Close beyond invalidation/stop at {signal.invalidation_level or stop}."
                ),
                stop_loss=Decimal(str(stop)),
                take_profits=[TakeProfitLevel(price=Decimal(str(take_profit)), size_fraction=1.0)],
            ),
            confidence=confidence,
            risk_level=RiskSeverity.MEDIUM,
            rationale=(
                f"Paper proposal from TradingView signal {signal.external_alert_id}. "
                "Approval-gated; does not place orders."
            ),
            approval_required=True,
            user_strategy_id=signal.strategy_id,
        )
        return self._proposals.create(data)

    def _evaluate_signal(
        self,
        signal: SignalModel,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> tuple[
        list[EligibilityCheck],
        list[EligibilityCheck],
        PaperSignalOrchestrationStatus,
        list[str],
        str,
    ]:
        eligibility: list[EligibilityCheck] = []
        risk: list[EligibilityCheck] = []
        reject_codes: list[str] = []
        block_codes: list[str] = []
        expired = False

        # --- Signal quality / eligibility ---
        validated_ok = signal.status in {
            TradingViewSignalStatus.VALIDATED.value,
            TradingViewSignalStatus.CANDIDATE_CREATED.value,
        }
        if signal.status == TradingViewSignalStatus.REJECTED.value:
            eligibility.append(
                EligibilityCheck(
                    code="signal_not_rejected",
                    passed=False,
                    detail=signal.rejection_reason or "Signal was rejected at intake.",
                )
            )
            reject_codes.append("signal_rejected")
        elif signal.status == TradingViewSignalStatus.DUPLICATE.value:
            eligibility.append(
                EligibilityCheck(
                    code="signal_not_duplicate",
                    passed=False,
                    detail="Duplicate signals are not orchestrated.",
                )
            )
            reject_codes.append("signal_duplicate")
        else:
            eligibility.append(
                EligibilityCheck(
                    code="signal_validated",
                    passed=validated_ok,
                    detail=(
                        "Signal status is validated/candidate_created."
                        if validated_ok
                        else f"Signal status is {signal.status}."
                    ),
                )
            )
            if not validated_ok:
                reject_codes.append("signal_not_validated")

        ref_time = signal.occurred_at or signal.received_at
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=UTC)
        age = datetime.now(UTC) - ref_time.astimezone(UTC)
        max_age = timedelta(seconds=self._settings.paper_signal_max_age_seconds)
        future = age < timedelta(seconds=-2)
        fresh = not future and age <= max_age
        eligibility.append(
            EligibilityCheck(
                code="signal_fresh",
                passed=fresh,
                detail=f"Age {int(age.total_seconds())}s vs max {int(max_age.total_seconds())}s.",
            )
        )
        if future:
            reject_codes.append("signal_future_time")
        elif not fresh:
            expired = True
            reject_codes.append("signal_stale")

        allowed_tf = {tf.lower() for tf in self._settings.paper_signal_allowed_timeframes}
        tf_ok = signal.timeframe.lower() in allowed_tf
        eligibility.append(
            EligibilityCheck(
                code="timeframe_allowed",
                passed=tf_ok,
                detail=(
                    f"Timeframe {signal.timeframe} allowed."
                    if tf_ok
                    else f"Timeframe {signal.timeframe} not in allow-list."
                ),
            )
        )
        if not tf_ok:
            reject_codes.append("timeframe_not_allowed")

        direction_ok = signal.direction in {"long", "short"}
        eligibility.append(
            EligibilityCheck(
                code="direction_valid",
                passed=direction_ok,
                detail=f"Direction={signal.direction}.",
            )
        )
        if not direction_ok:
            reject_codes.append("direction_invalid")

        min_conf = self._settings.paper_signal_min_confidence
        if signal.confidence is None:
            conf_ok = min_conf <= 0.0
            conf_detail = "Confidence missing; allowed because min_confidence is 0."
        else:
            conf_ok = signal.confidence >= min_conf
            conf_detail = f"Confidence {signal.confidence} vs min {min_conf}."
        eligibility.append(
            EligibilityCheck(code="confidence_ok", passed=conf_ok, detail=conf_detail)
        )
        if not conf_ok:
            reject_codes.append("confidence_below_min")

        setup_ok, setup_detail = self._check_setup_link(signal)
        eligibility.append(
            EligibilityCheck(code="setup_link_ok", passed=setup_ok, detail=setup_detail)
        )
        if not setup_ok:
            reject_codes.append("setup_link_required")

        strategy_ok, strategy_detail = self._check_strategy_link(signal)
        eligibility.append(
            EligibilityCheck(code="strategy_link_ok", passed=strategy_ok, detail=strategy_detail)
        )
        if not strategy_ok:
            reject_codes.append("strategy_link_invalid")

        levels_ok, levels_detail = self._check_levels(signal)
        eligibility.append(
            EligibilityCheck(code="levels_consistent", passed=levels_ok, detail=levels_detail)
        )
        if not levels_ok:
            reject_codes.append("levels_contradictory")

        conflict_ok, conflict_detail = self._check_conflicting_signal(
            signal, organization_id=organization_id
        )
        eligibility.append(
            EligibilityCheck(
                code="no_conflicting_signal",
                passed=conflict_ok,
                detail=conflict_detail,
            )
        )
        if not conflict_ok:
            block_codes.append("conflicting_signal")

        market_ok, market_detail = self._check_market_context(organization_id=organization_id)
        eligibility.append(
            EligibilityCheck(code="market_context_ok", passed=market_ok, detail=market_detail)
        )
        if not market_ok:
            block_codes.append("market_context_unavailable")

        # --- Risk / safety ---
        paper_ok = self._settings.execution_mode == ExecutionMode.PAPER
        risk.append(
            EligibilityCheck(
                code="execution_mode_paper",
                passed=paper_ok,
                detail=f"execution_mode={self._settings.execution_mode.value}.",
            )
        )
        if not paper_ok:
            block_codes.append("execution_mode_not_paper")

        real_off = not self._settings.enable_real_trading
        risk.append(
            EligibilityCheck(
                code="real_trading_disabled",
                passed=real_off,
                detail=f"enable_real_trading={self._settings.enable_real_trading}.",
            )
        )
        if not real_off:
            block_codes.append("real_trading_enabled")

        kill_blocked = self._kill_switch.is_execution_blocked(organization_id=organization_id)
        risk.append(
            EligibilityCheck(
                code="kill_switch_clear",
                passed=not kill_blocked,
                detail="Kill switch active." if kill_blocked else "Kill switch clear.",
            )
        )
        if kill_blocked:
            block_codes.append("kill_switch")

        daily_ok, daily_detail, cooldown_ok, cooldown_detail = self._check_daily_and_cooldown(
            organization_id=organization_id, user_id=user_id
        )
        risk.append(EligibilityCheck(code="daily_loss_clear", passed=daily_ok, detail=daily_detail))
        if not daily_ok:
            block_codes.append("daily_loss_lock")
        risk.append(
            EligibilityCheck(code="cooldown_clear", passed=cooldown_ok, detail=cooldown_detail)
        )
        if not cooldown_ok:
            block_codes.append("cooldown_active")

        reason_codes = reject_codes + block_codes
        if expired and "signal_stale" in reject_codes:
            status = PaperSignalOrchestrationStatus.EXPIRED
            summary = "Signal expired (stale)."
        elif reject_codes:
            status = PaperSignalOrchestrationStatus.REJECTED
            summary = reject_codes[0]
        elif block_codes:
            status = PaperSignalOrchestrationStatus.BLOCKED
            summary = block_codes[0]
        else:
            status = PaperSignalOrchestrationStatus.ELIGIBLE
            summary = "Signal eligible for paper orchestration."

        return eligibility, risk, status, reason_codes, summary

    def _check_setup_link(self, signal: SignalModel) -> tuple[bool, str]:
        named = signal.setup_name is not None and signal.setup_version is not None
        if not named:
            return True, "No setup name/version provided; link not required."
        if signal.setup_definition_id is not None:
            return True, f"Linked setup_definition_id={signal.setup_definition_id}."
        if self._settings.paper_signal_require_setup_when_named:
            return False, "Setup name/version provided but setup definition was not resolved."
        return True, "Setup unresolved but requirement disabled."

    def _check_strategy_link(self, signal: SignalModel) -> tuple[bool, str]:
        if signal.strategy_id is None and signal.strategy_version_id is None:
            return True, "No strategy link provided."
        if signal.strategy_id is None and signal.strategy_version_id is not None:
            return False, "strategy_version_id requires strategy_id."
        if (
            self._settings.paper_signal_require_strategy_when_provided
            and signal.strategy_id is not None
            and signal.strategy_version_id is None
        ):
            return False, "strategy_id provided without strategy_version_id."
        return True, "Strategy linkage present and consistent."

    def _check_levels(self, signal: SignalModel) -> tuple[bool, str]:
        entry = signal.trigger_level
        stop = signal.stop_loss_level or signal.invalidation_level
        tp = signal.take_profit_level
        if entry is None and stop is None and tp is None:
            return True, "No trade levels provided; candidate-only path still allowed."
        if entry is None or stop is None:
            return False, "Partial levels are contradictory; need trigger and stop/invalidation."
        if signal.direction == "long":
            if stop >= entry:
                return False, "Long signal requires stop/invalidation below trigger."
            if tp is not None and tp <= entry:
                return False, "Long signal requires take-profit above trigger."
        elif signal.direction == "short":
            if stop <= entry:
                return False, "Short signal requires stop/invalidation above trigger."
            if tp is not None and tp >= entry:
                return False, "Short signal requires take-profit below trigger."
        return True, "Levels are directionally consistent."

    def _check_conflicting_signal(
        self, signal: SignalModel, *, organization_id: uuid.UUID
    ) -> tuple[bool, str]:
        window = timedelta(seconds=self._settings.paper_signal_conflict_window_seconds)
        since = datetime.now(UTC) - window
        opposite = "short" if signal.direction == "long" else "long"
        rows = self._session.scalars(
            select(SignalModel).where(
                SignalModel.organization_id == organization_id,
                SignalModel.symbol == signal.symbol,
                SignalModel.timeframe == signal.timeframe,
                SignalModel.direction == opposite,
                SignalModel.id != signal.id,
                SignalModel.status.in_(
                    [
                        TradingViewSignalStatus.VALIDATED.value,
                        TradingViewSignalStatus.CANDIDATE_CREATED.value,
                    ]
                ),
                SignalModel.received_at >= since,
            )
        ).all()
        if rows:
            return False, f"Conflicting {opposite} signal found within conflict window."
        return True, "No conflicting validated signal in window."

    def _check_market_context(self, *, organization_id: uuid.UUID) -> tuple[bool, str]:
        readonly_expected = (
            self._settings.blofin_readonly_sync_enabled
            and self._settings.blofin_readonly_configured
        )
        demo_expected = (
            self._settings.exchange_demo_active
            and self._settings.blofin_demo_enabled
            and self._settings.blofin_demo_configured
        )
        if not (
            (readonly_expected or demo_expected)
            and self._settings.blofin_demo_rest_base_url.strip()
        ):
            # Optional account sync cannot gate internal paper signals, including
            # when a previous disabled/unconfigured sync left an unusable snapshot.
            return True, "No BloFin snapshot required (account sync not configured)."
        snap = self._session.scalars(
            select(BloFinDemoSyncSnapshot)
            .where(BloFinDemoSyncSnapshot.organization_id == organization_id)
            .order_by(BloFinDemoSyncSnapshot.synced_at.desc())
            .limit(1)
        ).first()
        if snap is None:
            return False, "BloFin demo sync required but no snapshot available."
        synced_at = snap.synced_at
        if synced_at.tzinfo is None:
            synced_at = synced_at.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - synced_at).total_seconds()
        if age < -2 or age > self._settings.blofin_sync_stale_after_seconds:
            return False, "Market context timestamp is outside the freshness window."
        if snap.is_stale or snap.health_status != "ok":
            return False, snap.stale_reason or f"Market context health={snap.health_status}."
        return True, f"Market context health={snap.health_status}."

    def _check_daily_and_cooldown(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[bool, str, bool, str]:
        try:
            snap = self._daily_risk.sync_from_portfolio(
                organization_id=organization_id, user_id=user_id
            )
        except Exception as exc:
            logger.warning("paper_signal_daily_risk_unavailable", error=str(exc))
            return False, "Daily risk state unavailable (fail closed).", False, "Cooldown unknown."

        # The daily loss lock is display only (AT-ADR-152); the 10% account
        # equity stop arms the kill switch instead.
        daily_ok = True
        daily_detail = (
            f"Daily loss limit reached (display only); realized PnL={snap.realized_pnl}."
            if snap.daily_locked
            else f"Daily realized PnL={snap.realized_pnl}."
        )

        cooldown_seconds = self._settings.paper_signal_cooldown_after_loss_seconds
        if cooldown_seconds <= 0:
            return daily_ok, daily_detail, True, "Cooldown disabled."

        from app.services.risk.paper_cooldown import paper_loss_cooldown_active

        if paper_loss_cooldown_active(
            self._session,
            organization_id=organization_id,
            user_id=user_id,
            seconds=cooldown_seconds,
            now=datetime.now(UTC),
        ):
            return (
                daily_ok,
                daily_detail,
                False,
                f"Cooldown active after loss within {cooldown_seconds}s.",
            )
        return daily_ok, daily_detail, True, "No recent losing close in cooldown window."

    def _refresh_decision_fields(
        self,
        row: DecisionModel,
        *,
        signal: SignalModel,
        status: PaperSignalOrchestrationStatus,
        mode: PaperSignalOrchestrationMode,
        reason_codes: list[str],
        summary: str,
        eligibility: list[EligibilityCheck],
        risk: list[EligibilityCheck],
        user_id: uuid.UUID,
        now: datetime,
    ) -> None:
        # Do not overwrite advanced terminal/progress statuses with a bare re-eval
        # unless eligibility failed.
        progressive = {
            PaperSignalOrchestrationStatus.AWAITING_REVIEW.value,
            PaperSignalOrchestrationStatus.PAPER_CANDIDATE_CREATED.value,
            PaperSignalOrchestrationStatus.PAPER_PROPOSAL_CREATED.value,
        }
        if row.status in progressive and status == PaperSignalOrchestrationStatus.ELIGIBLE:
            target = PaperSignalOrchestrationStatus(row.status)
        else:
            target = status
            if row.status != target.value:
                self._transition(row, to=target, reason=summary, user_id=user_id, at=now)

        row.mode = mode.value
        row.reason_codes = reason_codes
        row.reason_summary = summary
        row.eligibility_evidence = {"checks": [c.model_dump() for c in eligibility]}
        row.risk_evidence = {"checks": [c.model_dump() for c in risk]}
        row.setup_definition_id = signal.setup_definition_id
        row.strategy_id = signal.strategy_id
        row.strategy_version_id = signal.strategy_version_id
        row.journal_trade_id = signal.journal_trade_id
        row.backtest_run_id = signal.backtest_run_id
        if signal.candidate_id is not None:
            row.candidate_id = signal.candidate_id
        row.decided_at = now
        if target == PaperSignalOrchestrationStatus.EXPIRED:
            row.expired_at = now

    def _transition(
        self,
        row: DecisionModel,
        *,
        to: PaperSignalOrchestrationStatus,
        reason: str,
        user_id: uuid.UUID | None,
        at: datetime | None = None,
    ) -> None:
        now = at or datetime.now(UTC)
        from_status = row.status
        row.status = to.value
        transitions = list(row.transitions or [])
        transitions.append(
            {
                "at": now.isoformat(),
                "from_status": from_status,
                "to_status": to.value,
                "reason": reason,
                "actor_user_id": str(user_id) if user_id else None,
            }
        )
        row.transitions = transitions
        self._record_audit(
            AuditEventType.PAPER_SIGNAL_ORCHESTRATION_TRANSITION,
            organization_id=row.organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            metadata={
                "from_status": from_status,
                "to_status": to.value,
                "reason": reason,
            },
        )

    def _assert_enabled(self) -> None:
        if not self._settings.paper_signal_orchestration_enabled:
            raise ServiceUnavailableError(
                "Paper-signal orchestration is disabled.",
                code="paper_signal_orchestration_disabled",
            )

    def _resolve_mode(self) -> PaperSignalOrchestrationMode:
        raw = (self._settings.paper_signal_orchestration_mode or "").strip().lower()
        try:
            mode = PaperSignalOrchestrationMode(raw)
        except ValueError as exc:
            raise ServiceUnavailableError(
                "Invalid paper_signal_orchestration_mode; fail closed.",
                code="paper_signal_orchestration_mode_invalid",
            ) from exc
        return mode

    def _to_item(self, row: DecisionModel) -> PaperSignalOrchestrationDecisionItem:
        eligibility_raw = (row.eligibility_evidence or {}).get("checks") or []
        risk_raw = (row.risk_evidence or {}).get("checks") or []
        transitions_raw = row.transitions or []
        links = PaperSignalOrchestrationLinks(
            tradingview_signal_id=row.tradingview_signal_id,
            setup_definition_id=row.setup_definition_id,
            strategy_id=row.strategy_id,
            strategy_version_id=row.strategy_version_id,
            journal_trade_id=row.journal_trade_id,
            backtest_run_id=row.backtest_run_id,
            candidate_id=row.candidate_id,
            run_plan_id=row.run_plan_id,
            proposal_id=row.proposal_id,
            signal_path=f"/tradingview-signals?id={row.tradingview_signal_id}",
            candidate_path=(
                f"/paper-validation/candidates/{row.candidate_id}"
                if row.candidate_id is not None
                else None
            ),
            run_plan_path=(
                f"/paper-validation/run-plans/{row.run_plan_id}"
                if row.run_plan_id is not None
                else None
            ),
            proposal_path=(
                f"/proposals/{row.proposal_id}" if row.proposal_id is not None else None
            ),
            journal_path=(
                f"/journal/trades/{row.journal_trade_id}"
                if row.journal_trade_id is not None
                else None
            ),
        )
        return PaperSignalOrchestrationDecisionItem(
            id=row.id,
            organization_id=row.organization_id,
            tradingview_signal_id=row.tradingview_signal_id,
            idempotency_key=row.idempotency_key,
            status=PaperSignalOrchestrationStatus(row.status),
            mode=PaperSignalOrchestrationMode(row.mode),
            symbol=row.symbol,
            timeframe=row.timeframe,
            direction=row.direction,
            reason_codes=list(row.reason_codes or []),
            reason_summary=row.reason_summary,
            eligibility_checks=[EligibilityCheck.model_validate(c) for c in eligibility_raw],
            risk_checks=[EligibilityCheck.model_validate(c) for c in risk_raw],
            transitions=[
                PaperSignalOrchestrationTransition(
                    at=datetime.fromisoformat(t["at"]),
                    from_status=t.get("from_status"),
                    to_status=t["to_status"],
                    reason=t.get("reason") or "",
                    actor_user_id=(
                        uuid.UUID(t["actor_user_id"]) if t.get("actor_user_id") else None
                    ),
                )
                for t in transitions_raw
            ],
            links=links,
            decided_by=row.decided_by,
            approved_by=row.approved_by,
            decided_at=row.decided_at,
            expired_at=row.expired_at,
            approved_at=row.approved_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _record_audit(
        self,
        event_type: AuditEventType,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        resource_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        result: AuditResult = AuditResult.SUCCESS,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=request_id or f"pso-{resource_id or organization_id}",
                trace_id=str(uuid.uuid4()),
                event_type=event_type,
                severity=AuditSeverity.INFO
                if result == AuditResult.SUCCESS
                else AuditSeverity.MEDIUM,
                actor_type=ActorType.USER if user_id else ActorType.SYSTEM,
                organization_id=organization_id,
                user_id=user_id,
                resource_type="paper_signal_orchestration_decision",
                resource_id=resource_id,
                result=result,
                metadata=metadata or {},
            )
        )
