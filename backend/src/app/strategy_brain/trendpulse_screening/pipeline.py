"""Durable screening → canonical Candidate → existing eligibility and plan ports.

This consumer never approves or dispatches. Native execution still needs its
existing verified rules, quote, UID and reservation authorities.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError
from app.experiments.identity import require_account
from app.experiments.repository import ExperimentRepository
from app.experiments.risk import ExperimentRiskPlan, ExperimentRiskState, preview_admission
from app.experiments.service import ExperimentService
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.canonical_trade_plan import CanonicalTradePlanCommand, CanonicalTradePlanRevision
from app.schemas.experiments import ExperimentSource, ExperimentState
from app.schemas.trade_plan import QuantityUnit
from app.security.tenant import TenantContext
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.internal_paper_plan_terms import build_internal_paper_terms
from app.signal_fusion.action_eligibility import ActionEligibilityCommand
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import SetupAssessmentState
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.lifecycle import CandidateCreationCommand
from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
from app.strategy_brain.trendpulse_1r.adapter import evaluate_trendpulse
from app.strategy_brain.trendpulse_1r.canonical import command_from_receipts
from app.strategy_brain.trendpulse_1r.contracts import (
    TrendPulseRequest,
    TrendPulseSignal,
    TrendPulseSpec,
    TrendPulseStatus,
    exact_hash,
)
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService


class TrendPulsePipeline:
    def __init__(self, session: Session, settings: Settings, runtime: ProductionCanonicalRuntime):
        self.session, self.settings, self.runtime = session, settings, runtime

    def candidate(
        self,
        tenant: TenantContext,
        record_id: UUID,
        *,
        now: datetime,
    ) -> tuple[Candidate, SetupAssessment, CanonicalEvidenceWindowV1, TrendPulseSignal]:
        ExperimentService(self.session, self.settings)._mutation(tenant)
        screening = TrendPulseScreeningService(self.session, self.settings).detail(
            tenant, record_id
        )
        if screening.duplicate_of is not None:
            screening = TrendPulseScreeningService(self.session, self.settings).detail(
                tenant,
                screening.duplicate_of,
            )
        if screening.status is not TrendPulseStatus.QUALIFIED or screening.signal is None:
            raise ConflictError(
                "Screening has no qualified signal.", code="screening_not_qualified"
            )
        version = ExperimentService(self.session, self.settings).view(
            ExperimentRepository(self.session, tenant).version(
                screening.experiment_id,
                screening.experiment_version_id,
                lock=True,
            )
        )
        if (
            version.state is not ExperimentState.RUNNING
            or version.started_at is None
            or version.authorized_until is None
            or not version.started_at
            <= screening.acquisition_started_at
            <= now
            < version.authorized_until
            or version.configuration_hash != screening.configuration_hash
        ):
            raise ConflictError(
                "Screening is outside the approved run.", code="experiment_sample_window"
            )
        require_account(self.session, tenant, version.configuration.account, self.settings, now=now)
        policy = resolve_executable_strategy_policy(
            self.session,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            strategy_version_id=screening.strategy_version_id,
        )
        if not isinstance(policy.authored_spec, TrendPulseSpec):
            raise ConflictError("Compiled family differs.", code="screening_family_mismatch")
        if policy.strategy_version_content_hash != screening.strategy_content_hash:
            raise ConflictError("Immutable strategy differs.", code="screening_strategy_mismatch")
        if exact_hash(screening.evidence) != screening.evidence_hash:
            raise ConflictError("Stored receipts differ.", code="screening_evidence_mismatch")
        request = TrendPulseRequest(
            spec=policy.authored_spec,
            **screening.evidence.model_dump(),
            trigger_end=screening.trigger_end,
            evaluated_at=screening.decision_at,
        )
        command = command_from_receipts(policy.fusion_policy, request)
        assessment = evaluate_canonical_strategy(
            executable_policy=policy,
            command=command,
            evidence=FirstSliceEvidenceBundle(trendpulse=request),
            evaluated_at=now,
        )
        if assessment.state is not SetupAssessmentState.CONFIRMED_SETUP:
            raise ConflictError(assessment.explanation, code="screening_no_current_setup")
        signal = evaluate_trendpulse(
            request.spec,
            trend_bars=request.trend_bars,
            trend_observations=request.trend_observations,
            entry_bars=request.entry_bars,
            entry_observations=request.entry_observations,
            instrument_rules=request.instrument_rules,
            trigger_end=request.trigger_end,
            evaluated_at=now,
        ).signal
        assert signal is not None
        # Clocks may advance, but original geometry and rules may not.
        if (signal.entry, signal.structural_stop, signal.target, signal.instrument_rules_hash) != (
            screening.signal.signal.entry,
            screening.signal.signal.structural_stop,
            screening.signal.signal.target,
            screening.signal.signal.instrument_rules_hash,
        ):
            raise ConflictError(
                "Stored signal derivation differs.", code="screening_evidence_mismatch"
            )
        window = evidence_window_from_assessment_command(command)
        with self.runtime.bind_session(self.session):
            candidate = self.runtime.lifecycle.create_from_confirmed_setup(
                CandidateCreationCommand(
                    assessment=assessment,
                    evidence_window=window,
                    executable_setup=command.executable_setup,
                    evidence_identity=command.evidence_identity,
                    idempotency_key=f"trendpulse:{record_id}",
                    correlation_id=assessment.correlation_id,
                )
            )
        return candidate, assessment, window, signal

    def prepare_internal_plan(
        self,
        tenant: TenantContext,
        record_id: UUID,
        *,
        now: datetime,
        eligibility_command: ActionEligibilityCommand,
        risk_state: ExperimentRiskState,
        quote_observed_at: datetime,
    ) -> CanonicalTradePlanRevision:
        """Server-owned account/quote/risk evidence; never HTTP-supplied snapshots.

        Missing trusted state remains an explicit adoption blocker. This method
        composes existing authorities and grants no new automatic send permission.
        """
        candidate, assessment, window, signal = self.candidate(tenant, record_id, now=now)
        screening = TrendPulseScreeningService(self.session, self.settings).detail(
            tenant, record_id
        )
        version = ExperimentService(self.session, self.settings).view(
            ExperimentRepository(self.session, tenant).version(
                screening.experiment_id,
                screening.experiment_version_id,
                lock=True,
            )
        )
        if version.configuration.account.source is not ExperimentSource.INTERNAL_SIMULATION:
            raise ConflictError(
                "Native rules/quote/reservation adapter required.",
                code="experiment_native_plan_adapter_required",
            )
        if (
            candidate.evidence_identity.provenance.is_mock
            or candidate.evidence_identity.provenance.fallback_used
        ):
            raise ConflictError(
                "Synthetic/replay evidence cannot authorize this plan.", code="screening_not_live"
            )
        if not 0 <= (now - quote_observed_at).total_seconds() <= 10:
            raise ConflictError("Fresh source quote required.", code="screening_quote_stale")
        ec = eligibility_command
        if (
            ec.candidate != candidate
            or ec.assessment != assessment
            or ec.evidence_window != window
            or ec.account.account_id != version.configuration.account.execution_account_id
            or ec.account.user_id != tenant.user_id
            or ec.market_action.evidence_price != signal.entry
            or ec.market_action.execution_price != signal.entry
        ):
            raise ConflictError(
                "Current canonical action evidence differs.",
                code="screening_action_binding_mismatch",
            )
        if not (
            self.settings.execution_mode.value == "paper"
            and self.settings.exchange_mode.value == "paper_internal"
            and not self.settings.enable_real_trading
            and not self.settings.real_trading_enabled
        ):
            raise ConflictError(
                "Internal paper execution posture required.", code="exchange_mode_refused"
            )
        # Sizing uses the existing floor-rounded helper and the approved version's exact limits.
        request = screening.evidence
        assert request.instrument_rules is not None
        admission = preview_admission(
            version,
            ExperimentRiskPlan(
                entry=signal.entry,
                structural_stop=signal.structural_stop,
                target=signal.target,
                direction="LONG" if signal.direction.value == "long" else "SHORT",
                leverage=1,
                quantity_unit=QuantityUnit.BASE,
                instrument_rules=request.instrument_rules,
            ),
            risk_state,
            now=now,
        )
        if not admission.admissible or admission.quantity is None:
            raise ConflictError(admission.reason, code="experiment_risk_refused")
        with self.runtime.bind_session(self.session):
            evaluation = self.runtime.eligibility.evaluate(ec)
            terms = build_internal_paper_terms(
                candidate=candidate,
                account_id=ec.account.account_id,
                entry=signal.entry,
                stop=signal.structural_stop,
                target=signal.target,
                quantity=admission.quantity,
                observed=quote_observed_at,
                now=now,
                valid_until=min(signal.expires_at, evaluation.eligibility.valid_until),
                freshness=60,
                symbol=signal.identity.instrument.provider_symbol,
                tick_size=request.instrument_rules.tick_size,
                target_formula="trendpulse-structural-gross-1r",
                presentation={
                    "evidence_reference": candidate.evidence_window_hash,
                    "decision_reference": str(evaluation.eligibility.eligibility_id),
                },
            )
            # Research precision is declared simulator precision, never BloFin precision.
            terms = terms.model_copy(update={"instrument_rules": request.instrument_rules})
            return self.runtime.plans.create(
                CanonicalTradePlanCommand(
                    organization_id=tenant.organization_id,
                    user_id=tenant.user_id,
                    account_id=ec.account.account_id,
                    candidate_id=candidate.candidate_id,
                    eligibility_id=evaluation.eligibility.eligibility_id,
                    terms=terms,
                    idempotency_key=f"trendpulse-plan:{record_id}",
                    correlation_id=assessment.correlation_id,
                )
            )
