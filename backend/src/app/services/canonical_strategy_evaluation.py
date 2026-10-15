"""Resolve approved compiled strategy versions into executable evaluation policy.

Paper validation and Watcher must call this boundary (or
``evaluate_canonical_strategy`` after a resolved policy). This module does not
mint Candidates, enable Watcher, or call an LLM.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.analysis.wilder_atr_v1 import FINALITY_POLICY_VERSION
from app.core.errors import NotFoundError
from app.db.models import CompiledSetupDefinition, PaperValidationRun, UserStrategyVersion
from app.evidence_pipeline.canonical import MANDATORY_ROLES
from app.market_contracts.freshness import FIRST_SLICE_FRESHNESS_POLICY_VERSION
from app.schemas.common import SetupCompileStatus, StrategyLifecycleState
from app.schemas.nested_continuation import NESTED_KIND, NestedContinuationSpec
from app.schemas.strategy_library import StrategyCard
from app.schemas.strategy_pattern_spec import FirstSliceAuthoredPatternSpec
from app.schemas.structured_rules import StructuredRules
from app.services.setup_ast_compiler import compile_from_authored
from app.services.strategy_versioning import CrossTenantStrategyError, StrategyVersioningService
from app.signal_fusion.adapters import AssessmentCommand
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.enums import EvidenceRole, SetupIdentityKind
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.policy import (
    DEFAULT_FUSION_POLICY_VERSION,
    FusionPolicy,
    FusionThresholds,
    build_fusion_policy,
)
from app.signal_fusion.strategy_evaluation_policy import (
    ExecutableStrategyPolicy,
    build_executable_strategy_policy,
    evaluate_canonical_strategy,
)
from app.signal_fusion.types import ExecutableSetupRef, RoleTimeframeBinding, RuleWeight
from app.strategy_brain.sfp.contracts import SFP_KIND, SfpSpec
from app.strategy_brain.trendpulse_1r.contracts import TRENDPULSE_KIND, TrendPulseSpec


def resolve_executable_strategy_policy(
    session: Session,
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    user_id: UUID | None = None,
    paper_validation_run_id: UUID | None = None,
) -> ExecutableStrategyPolicy:
    """Load one approved compiled version as executable evaluation policy."""

    versioning = StrategyVersioningService(session)
    version = session.get(UserStrategyVersion, strategy_version_id)
    if version is None:
        raise NotFoundError("Strategy version not found.")
    try:
        strategy = versioning.assert_tenant_version(version, organization_id=organization_id)
    except CrossTenantStrategyError as exc:
        raise StrategyEvaluationPolicyError(
            "Strategy version does not belong to this organization.",
            reason_code="organization_mismatch",
        ) from exc
    if user_id is not None and strategy.user_id != user_id:
        raise NotFoundError("Strategy version not found.")

    lifecycle = versioning.latest_lifecycle_event_for_version(version.id)
    state = lifecycle.new_state if lifecycle is not None else StrategyLifecycleState.DRAFT
    if state is StrategyLifecycleState.DRAFT or lifecycle is None:
        raise StrategyEvaluationPolicyError(
            "Draft strategy versions cannot be evaluated as trading authority.",
            reason_code="draft_not_executable",
        )
    validation_scope = False
    if state is StrategyLifecycleState.PAPER_VALIDATING and paper_validation_run_id is not None:
        run = session.get(PaperValidationRun, paper_validation_run_id)
        if (
            run is not None
            and run.organization_id == organization_id
            and run.user_id == user_id
            and run.strategy_version_id == version.id
            and lifecycle.evidence_snapshot.get("paper_validation_run_id") == str(run.id)
        ):
            from app.services.strategy_promotion import StrategyPromotionService

            StrategyPromotionService(session).require_candidate_replay(
                version.id,
                organization_id=organization_id,
                user_id=user_id,
            )
            validation_scope = True
    if not validation_scope and state not in {
        StrategyLifecycleState.APPROVED,
        StrategyLifecycleState.ACTIVE,
    }:
        raise StrategyEvaluationPolicyError(
            "Only approved or active strategy versions may become evaluation policy.",
            reason_code="strategy_not_approved",
        )

    compiled = versioning.compiled_for_version(version.id, organization_id=organization_id)
    if compiled is None:
        raise StrategyEvaluationPolicyError(
            "Approved strategy version has no CompiledSetupDefinition.",
            reason_code="compiled_definition_missing",
        )
    if compiled.organization_id != organization_id:
        raise StrategyEvaluationPolicyError(
            "Compiled setup does not belong to this organization.",
            reason_code="organization_mismatch",
        )
    if compiled.compile_status is not SetupCompileStatus.EXECUTABLE:
        raise StrategyEvaluationPolicyError(
            "Compiled setup is not executable.",
            reason_code="unsupported_strategy_rule",
        )
    if compiled.strategy_version_id != version.id:
        raise StrategyEvaluationPolicyError(
            "Compiled setup is not bound to this strategy version.",
            reason_code="compiled_definition_mismatch",
        )

    spec = _parse_authored_spec(version)
    recompiled = compile_from_authored(
        card=StrategyCard.model_validate(version.card),
        rules=(
            StructuredRules.model_validate(version.structured_rules)
            if version.structured_rules
            else None
        ),
        pattern_spec=spec.model_dump(mode="json"),
        strategy_version_id=version.id,
        organization_id=organization_id,
    )
    if recompiled.document is None:
        raise StrategyEvaluationPolicyError(
            "Stored strategy rules cannot compile into an executable definition.",
            reason_code="unsupported_strategy_rule",
        )
    if recompiled.document.content_hash != compiled.content_hash:
        raise StrategyEvaluationPolicyError(
            "Stored CompiledSetupDefinition does not match the version pattern_spec.",
            reason_code="compiled_definition_mismatch",
        )

    fusion_policy = _fusion_policy_for_compiled(
        organization_id=organization_id,
        strategy_version_id=version.id,
        compiled=compiled,
        spec=spec,
    )
    return build_executable_strategy_policy(
        organization_id=organization_id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        strategy_version_content_hash=version.content_hash,
        lifecycle_state=state,
        compiled_setup_definition_id=compiled.id,
        compiled_content_hash=compiled.content_hash,
        compiler_version=compiled.compiler_version,
        grammar_version=compiled.grammar_version,
        fusion_policy=fusion_policy,
        authored_spec=spec,
        execution_scope="paper_validation" if validation_scope else "paper",
    )


def evaluate_canonical_strategy_for_version(
    session: Session,
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    previous_assessment: SetupAssessment | None = None,
    user_id: UUID | None = None,
) -> SetupAssessment:
    """Resolve approved compiled policy then emit SetupAssessment."""

    policy = resolve_executable_strategy_policy(
        session,
        organization_id=organization_id,
        strategy_version_id=strategy_version_id,
        user_id=user_id,
    )
    return evaluate_canonical_strategy(
        executable_policy=policy,
        command=command,
        evidence=evidence,
        evaluated_at=evaluated_at,
        previous_assessment=previous_assessment,
        account_context=None,
    )


def _parse_authored_spec(
    version: UserStrategyVersion,
) -> FirstSliceAuthoredPatternSpec | NestedContinuationSpec | SfpSpec | TrendPulseSpec:
    if version.pattern_spec is None:
        raise StrategyEvaluationPolicyError(
            "Executable evaluation requires a stored pattern_spec on the version.",
            reason_code="unsupported_strategy_rule",
        )
    try:
        if version.pattern_spec.get("kind") == NESTED_KIND:
            return NestedContinuationSpec.model_validate(version.pattern_spec)
        if version.pattern_spec.get("kind") == TRENDPULSE_KIND:
            return TrendPulseSpec.model_validate(version.pattern_spec)
        if version.pattern_spec.get("kind") == SFP_KIND:
            return SfpSpec.model_validate(version.pattern_spec)
        return FirstSliceAuthoredPatternSpec.model_validate(version.pattern_spec)
    except ValidationError as exc:
        raise StrategyEvaluationPolicyError(
            "Stored pattern_spec is not a supported first-slice authored spec.",
            reason_code="unsupported_strategy_rule",
        ) from exc


def _fusion_policy_for_compiled(
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    compiled: CompiledSetupDefinition,
    spec: FirstSliceAuthoredPatternSpec | NestedContinuationSpec | SfpSpec | TrendPulseSpec,
) -> FusionPolicy:
    setup = ExecutableSetupRef(
        setup_definition_id=compiled.id,
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash=compiled.content_hash,
    )
    nested = isinstance(spec, (NestedContinuationSpec, SfpSpec))
    if isinstance(spec, TrendPulseSpec):
        return build_fusion_policy(
            policy_version=spec.kind,
            organization_id=organization_id,
            strategy_version_id=strategy_version_id,
            executable_setup=setup,
            required_roles=(EvidenceRole.TRIGGER_OHLCV, EvidenceRole.CONTEXT_OHLCV),
            role_timeframes=(
                RoleTimeframeBinding(
                    role=EvidenceRole.TRIGGER_OHLCV, timeframe=spec.trigger_timeframe
                ),
                RoleTimeframeBinding(
                    role=EvidenceRole.CONTEXT_OHLCV, timeframe=spec.trend_timeframe
                ),
            ),
            thresholds=FusionThresholds(
                confirmation_score=Decimal("1.0"),
                weights=(RuleWeight(rule_id="mandatory_evidence", weight=Decimal("1.0")),),
            ),
            freshness_policy_version=FIRST_SLICE_FRESHNESS_POLICY_VERSION,
            finality_policy_version=FINALITY_POLICY_VERSION,
        )
    return build_fusion_policy(
        role_timeframes=(
            RoleTimeframeBinding(role=EvidenceRole.TRIGGER_OHLCV, timeframe=spec.trigger_timeframe),
            RoleTimeframeBinding(role=EvidenceRole.STRUCTURE, timeframe=spec.trigger_timeframe),
        )
        if nested
        else None,
        policy_version=spec.kind if nested else DEFAULT_FUSION_POLICY_VERSION,
        organization_id=organization_id,
        strategy_version_id=strategy_version_id,
        executable_setup=setup,
        required_roles=(EvidenceRole.TRIGGER_OHLCV, EvidenceRole.STRUCTURE)
        if nested
        else MANDATORY_ROLES,
        thresholds=FusionThresholds(
            confirmation_score=Decimal("1.0"),
            weights=(RuleWeight(rule_id="mandatory_evidence", weight=Decimal("1.0")),),
        ),
        freshness_policy_version=FIRST_SLICE_FRESHNESS_POLICY_VERSION,
        finality_policy_version=FINALITY_POLICY_VERSION,
    )


__all__ = [
    "evaluate_canonical_strategy_for_version",
    "resolve_executable_strategy_policy",
]
