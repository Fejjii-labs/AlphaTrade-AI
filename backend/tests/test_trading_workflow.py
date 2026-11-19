"""Synthetic receipts and disposable PG; never provider/live execution proof."""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.db.models import CompiledSetupDefinition
from app.schemas.common import StrategyLifecycleState
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.setup_ast import PatternAst
from app.services.canonical_strategy_evaluation import _fusion_policy_for_compiled
from app.services.setup_ast_compiler import compile_from_authored
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import SetupAssessmentState
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.lifecycle import CandidateCreationCommand, CandidateLifecycleService
from app.signal_fusion.memory import FrozenClock, InMemoryCandidateRepository
from app.signal_fusion.strategy_evaluation_policy import (
    build_executable_strategy_policy,
    evaluate_canonical_strategy,
)
from app.strategy_brain.trendpulse_1r.canonical import command_from_receipts
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseRequest
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401
from tests.support.experiment_fixtures import experiment_world as _experiment_world  # noqa: F401
from tests.test_at067_canonical_strategy_evaluation_policy import _card
from tests.test_trendpulse_1r_adapter import END, path, spec


def policy(short=False):
    authored = spec(short)
    org, version, setup = uuid4(), uuid4(), uuid4()
    result = compile_from_authored(card=_card(), rules=None, pattern_spec=authored.model_dump())
    assert result.status == "executable" and result.document is not None
    compiled = CompiledSetupDefinition(id=setup, content_hash=result.document.content_hash)
    fusion = _fusion_policy_for_compiled(
        organization_id=org, strategy_version_id=version, compiled=compiled, spec=authored
    )
    return build_executable_strategy_policy(
        organization_id=org,
        strategy_id=uuid4(),
        strategy_version_id=version,
        strategy_version_content_hash="a" * 64,
        lifecycle_state=StrategyLifecycleState.APPROVED,
        compiled_setup_definition_id=setup,
        compiled_content_hash=result.document.content_hash,
        compiler_version=result.document.compiler_version,
        grammar_version=result.document.grammar_version,
        fusion_policy=fusion,
        authored_spec=authored,
    )


@pytest.mark.parametrize("short", [False, True])
def test_canonical_rules_candidate_dedupe_and_expiration(short):
    p = policy(short)
    request = TrendPulseRequest(spec=p.authored_spec, **path(short))
    command = command_from_receipts(p.fusion_policy, request)
    assessment = evaluate_canonical_strategy(
        executable_policy=p,
        command=command,
        evidence=FirstSliceEvidenceBundle(trendpulse=request),
        evaluated_at=request.evaluated_at,
    )
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    window = evidence_window_from_assessment_command(command)
    service = CandidateLifecycleService(
        repository=InMemoryCandidateRepository(), clock=FrozenClock(request.evaluated_at)
    )
    create = CandidateCreationCommand(
        assessment=assessment,
        evidence_window=window,
        executable_setup=command.executable_setup,
        evidence_identity=command.evidence_identity,
        idempotency_key="synthetic-workflow",
        correlation_id=assessment.correlation_id,
    )
    assert service.create_from_confirmed_setup(create) == service.create_from_confirmed_setup(
        create
    )
    expired = evaluate_canonical_strategy(
        executable_policy=p,
        command=command,
        evidence=FirstSliceEvidenceBundle(trendpulse=request),
        evaluated_at=END + timedelta(seconds=60),
    )
    assert expired.state is SetupAssessmentState.EXPIRED
    assert request.entry_observations[-1].receive_time == END + timedelta(seconds=3)


def test_changed_rules_and_command_fail_closed():
    p = policy()
    request = TrendPulseRequest(spec=p.authored_spec, **path())
    command = command_from_receipts(p.fusion_policy, request)
    with pytest.raises(ValueError, match="command_mismatch"):
        evaluate_canonical_strategy(
            executable_policy=p,
            command=command.model_copy(
                update={"public_observations": command.public_observations[:-1]}
            ),
            evidence=FirstSliceEvidenceBundle(trendpulse=request),
            evaluated_at=request.evaluated_at,
        )
    invalid = p.authored_spec.model_dump()
    invalid["parameters"]["ema50_slope_min_ratio"] = "0.0001"
    result = compile_from_authored(card=_card(), rules=None, pattern_spec=invalid)
    assert result.status == "non_executable" and result.document is None


def test_legacy_serialization_and_exclusive_family():
    from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec

    result = compile_from_authored(
        card=_card(), rules=None, pattern_spec=canonical_first_slice_authored_spec()
    )
    assert "trendpulse_spec" not in result.document.pattern.model_dump()
    with pytest.raises(ValueError, match="one family"):
        PatternAst.model_validate(
            {
                **result.document.pattern.model_dump(),
                "nested_spec": NestedContinuationSpec(symbol="BTCUSDT").model_dump(),
                "trendpulse_spec": spec().model_dump(),
            }
        )


def test_legacy_watcher_refuses_trendpulse_with_precise_receipt_requirement():
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.watcher.errors import WatcherEvidenceUnavailableError
    from tests.test_live_evidence_pipeline import _evaluation_command

    p = policy()
    port = AssemblingWatcherScanEvidence(None, executable_resolver=lambda _command: p)
    with pytest.raises(WatcherEvidenceUnavailableError) as error:
        port.load(_evaluation_command(p.organization_id))
    assert error.value.reason_code == "trendpulse_screening_receipts_required"
