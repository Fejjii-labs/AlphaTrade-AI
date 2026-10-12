"""Canonical strategy evaluation policy boundary.

Product evaluation authority is:

    approved immutable UserStrategyVersion
    → CompiledSetupDefinition
    → canonical market evidence
    → first-slice compatibility adapter
    → evaluate_setup
    → SetupAssessment

``evaluate_setup`` remains the sole SetupAssessment function (AT-ADR-025).
This module decides whether a stored strategy may occupy that function.
Draft conversational proposals and unsupported rules fail closed. No LLM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.market_contracts.models import CanonicalModel
from app.schemas.common import StrategyLifecycleState
from app.schemas.nested_continuation import NestedContinuationSpec, NestedParameters
from app.schemas.setup_ast import COMPILER_VERSION, GRAMMAR_VERSION
from app.schemas.strategy_pattern_spec import FirstSliceAuthoredPatternSpec
from app.signal_fusion.adapters import AssessmentCommand
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.first_slice_adapter import (
    FIRST_SLICE_ADAPTER_ID,
    FirstSliceEvaluationParams,
    bind_first_slice_compatibility_adapter,
)
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.policy import FusionPolicy
from app.signal_fusion.types import Sha256Hex, hashed_model
from app.strategy_brain.sfp.contracts import SfpParameters, SfpSpec
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseParameters, TrendPulseSpec

EXECUTABLE_STRATEGY_POLICY_SCHEMA = "ExecutableStrategyPolicy/v1"
EXECUTABLE_LIFECYCLE_STATES = frozenset(
    {StrategyLifecycleState.APPROVED, StrategyLifecycleState.ACTIVE}
)


class ExecutableStrategyPolicy(CanonicalModel):
    """Resolved, immutable evaluation policy for one approved compiled version."""

    schema_version: str = EXECUTABLE_STRATEGY_POLICY_SCHEMA
    organization_id: UUID
    strategy_id: UUID
    strategy_version_id: UUID
    strategy_version_content_hash: Sha256Hex
    lifecycle_state: StrategyLifecycleState
    execution_scope: Literal["paper", "paper_validation"] = "paper"
    compiled_setup_definition_id: UUID
    compiled_content_hash: Sha256Hex
    compiler_version: str
    grammar_version: str
    fusion_policy: FusionPolicy
    adapter_id: Literal[
        "first_slice_compatibility/v1",
        "operational_nested_continuation/v1",
        "swing_failure_pattern/v1",
        "trendpulse_1r/v1",
    ] = FIRST_SLICE_ADAPTER_ID
    authored_spec: FirstSliceAuthoredPatternSpec | NestedContinuationSpec | SfpSpec | TrendPulseSpec
    evaluation_params: (
        FirstSliceEvaluationParams | NestedParameters | SfpParameters | TrendPulseParameters
    )
    content_hash: Sha256Hex


def build_executable_strategy_policy(
    *,
    organization_id: UUID,
    strategy_id: UUID,
    strategy_version_id: UUID,
    strategy_version_content_hash: str,
    lifecycle_state: StrategyLifecycleState,
    compiled_setup_definition_id: UUID,
    compiled_content_hash: str,
    compiler_version: str,
    grammar_version: str,
    fusion_policy: FusionPolicy,
    authored_spec: FirstSliceAuthoredPatternSpec
    | NestedContinuationSpec
    | SfpSpec
    | TrendPulseSpec,
    execution_scope: Literal["paper", "paper_validation"] = "paper",
) -> ExecutableStrategyPolicy:
    """Bind an approved compiled version onto the first-slice adapter."""

    _assert_executable_lifecycle(lifecycle_state, execution_scope)
    _assert_fusion_lineage(
        fusion_policy,
        organization_id=organization_id,
        strategy_version_id=strategy_version_id,
        compiled_setup_definition_id=compiled_setup_definition_id,
        compiled_content_hash=compiled_content_hash,
    )
    spec = authored_spec
    params = (
        spec.parameters
        if isinstance(spec, (NestedContinuationSpec, SfpSpec, TrendPulseSpec))
        else bind_first_slice_compatibility_adapter(spec)
    )
    draft = ExecutableStrategyPolicy(
        organization_id=organization_id,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        strategy_version_content_hash=strategy_version_content_hash,
        lifecycle_state=lifecycle_state,
        execution_scope=execution_scope,
        compiled_setup_definition_id=compiled_setup_definition_id,
        compiled_content_hash=compiled_content_hash,
        compiler_version=compiler_version,
        grammar_version=grammar_version,
        fusion_policy=fusion_policy,
        adapter_id=spec.kind
        if isinstance(spec, (NestedContinuationSpec, SfpSpec, TrendPulseSpec))
        else FIRST_SLICE_ADAPTER_ID,
        authored_spec=spec,
        evaluation_params=params,
        content_hash="0" * 64,
    )
    return hashed_model(draft)


def executable_policy_from_fusion_policy(
    fusion_policy: FusionPolicy,
    *,
    strategy_id: UUID,
    strategy_version_content_hash: str,
    authored_spec: FirstSliceAuthoredPatternSpec
    | NestedContinuationSpec
    | SfpSpec
    | TrendPulseSpec,
    lifecycle_state: StrategyLifecycleState = StrategyLifecycleState.APPROVED,
    compiler_version: str = COMPILER_VERSION,
    grammar_version: str = GRAMMAR_VERSION,
) -> ExecutableStrategyPolicy:
    """In-memory policy for Watcher/evaluator tests. Does not persist rows."""

    return build_executable_strategy_policy(
        organization_id=fusion_policy.organization_id,
        strategy_id=strategy_id,
        strategy_version_id=fusion_policy.strategy_version_id,
        strategy_version_content_hash=strategy_version_content_hash,
        lifecycle_state=lifecycle_state,
        compiled_setup_definition_id=fusion_policy.executable_setup.setup_definition_id,
        compiled_content_hash=fusion_policy.executable_setup.content_hash,
        compiler_version=compiler_version,
        grammar_version=grammar_version,
        fusion_policy=fusion_policy,
        authored_spec=authored_spec,
    )


def evaluate_canonical_strategy(
    *,
    executable_policy: ExecutableStrategyPolicy,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    previous_assessment: SetupAssessment | None = None,
    account_context: object | None = None,
) -> SetupAssessment:
    """Evaluate setup truth from an approved compiled strategy policy.

    Account, risk, and LLM context cannot change the assessment.
    """

    del account_context
    _assert_executable_lifecycle(
        executable_policy.lifecycle_state, executable_policy.execution_scope
    )
    _assert_command_matches_policy(executable_policy, command)
    if isinstance(executable_policy.authored_spec, TrendPulseSpec):
        if (
            executable_policy.evaluation_params != executable_policy.authored_spec.parameters
            or executable_policy.adapter_id != executable_policy.authored_spec.kind
            or hashed_model(executable_policy).content_hash != executable_policy.content_hash
        ):
            raise StrategyEvaluationPolicyError(
                "TrendPulse parameters do not match immutable policy.",
                reason_code="unsupported_strategy_rule",
            )
        return evaluate_setup(
            policy=executable_policy.fusion_policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            trendpulse_spec=executable_policy.authored_spec,
        )
    if isinstance(executable_policy.authored_spec, NestedContinuationSpec):
        if executable_policy.evaluation_params != executable_policy.authored_spec.parameters:
            raise StrategyEvaluationPolicyError(
                "Nested parameters do not match immutable policy.",
                reason_code="unsupported_strategy_rule",
            )
        return evaluate_setup(
            policy=executable_policy.fusion_policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            previous_assessment=previous_assessment,
            nested_spec=executable_policy.authored_spec,
        )
    if isinstance(executable_policy.authored_spec, SfpSpec):
        if (
            executable_policy.evaluation_params != executable_policy.authored_spec.parameters
            or executable_policy.adapter_id != executable_policy.authored_spec.kind
            or hashed_model(executable_policy).content_hash != executable_policy.content_hash
        ):
            raise StrategyEvaluationPolicyError(
                "SFP parameters do not match immutable policy.",
                reason_code="unsupported_strategy_rule",
            )
        return evaluate_setup(
            policy=executable_policy.fusion_policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            previous_assessment=previous_assessment,
            sfp_spec=executable_policy.authored_spec,
        )
    bound = bind_first_slice_compatibility_adapter(executable_policy.authored_spec)
    if bound != executable_policy.evaluation_params:
        raise StrategyEvaluationPolicyError(
            "Executable policy adapter params do not match the compiled spec.",
            reason_code="unsupported_strategy_rule",
        )
    return evaluate_setup(
        policy=executable_policy.fusion_policy,
        command=command,
        evidence=evidence,
        evaluated_at=evaluated_at,
        previous_assessment=previous_assessment,
        account_context=None,
        evaluation_params=bound,
        market_symbol=executable_policy.authored_spec.symbol,
    )


def _assert_executable_lifecycle(
    state: StrategyLifecycleState, execution_scope: str = "paper"
) -> None:
    if execution_scope == "paper_validation" and state is StrategyLifecycleState.PAPER_VALIDATING:
        return
    if state in EXECUTABLE_LIFECYCLE_STATES:
        return
    if state is StrategyLifecycleState.DRAFT:
        raise StrategyEvaluationPolicyError(
            "Draft strategy versions cannot be evaluated as trading authority.",
            reason_code="draft_not_executable",
        )
    raise StrategyEvaluationPolicyError(
        "Only approved or active strategy versions may become evaluation policy.",
        reason_code="strategy_not_approved",
    )


def _assert_fusion_lineage(
    fusion_policy: FusionPolicy,
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    compiled_setup_definition_id: UUID,
    compiled_content_hash: str,
) -> None:
    if fusion_policy.organization_id != organization_id:
        raise StrategyEvaluationPolicyError(
            "Fusion policy organization_id does not match the strategy tenant.",
            reason_code="organization_mismatch",
        )
    if fusion_policy.strategy_version_id != strategy_version_id:
        raise StrategyEvaluationPolicyError(
            "Fusion policy strategy_version_id does not match the compiled version.",
            reason_code="strategy_evidence_mismatch",
        )
    setup = fusion_policy.executable_setup
    if setup.setup_definition_id != compiled_setup_definition_id:
        raise StrategyEvaluationPolicyError(
            "Fusion policy compiled setup id does not match CompiledSetupDefinition.",
            reason_code="strategy_evidence_mismatch",
        )
    if setup.content_hash != compiled_content_hash:
        raise StrategyEvaluationPolicyError(
            "Fusion policy compiled setup hash does not match CompiledSetupDefinition.",
            reason_code="compiled_definition_mismatch",
        )


def _assert_command_matches_policy(
    executable_policy: ExecutableStrategyPolicy, command: AssessmentCommand
) -> None:
    if command.organization_id != executable_policy.organization_id:
        raise StrategyEvaluationPolicyError(
            "Assessment command organization_id does not match executable policy.",
            reason_code="organization_mismatch",
        )
    if command.strategy_version_id != executable_policy.strategy_version_id:
        raise StrategyEvaluationPolicyError(
            "Assessment command strategy_version_id does not match executable policy.",
            reason_code="strategy_evidence_mismatch",
        )
    setup = command.executable_setup
    if setup.setup_definition_id != executable_policy.compiled_setup_definition_id:
        raise StrategyEvaluationPolicyError(
            "Assessment command compiled setup id does not match executable policy.",
            reason_code="strategy_evidence_mismatch",
        )
    if setup.content_hash != executable_policy.compiled_content_hash:
        raise StrategyEvaluationPolicyError(
            "Assessment command compiled setup hash does not match executable policy.",
            reason_code="compiled_definition_mismatch",
        )
    if command.fusion_policy_version != executable_policy.fusion_policy.policy_version:
        raise StrategyEvaluationPolicyError(
            "Assessment command fusion policy version does not match executable policy.",
            reason_code="strategy_evidence_mismatch",
        )


__all__ = [
    "EXECUTABLE_LIFECYCLE_STATES",
    "EXECUTABLE_STRATEGY_POLICY_SCHEMA",
    "ExecutableStrategyPolicy",
    "build_executable_strategy_policy",
    "evaluate_canonical_strategy",
    "executable_policy_from_fusion_policy",
]
