"""Synthetic receipts and disposable PG; never provider/live execution proof."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.db.models import CompiledSetupDefinition
from app.experiments.models import ExperimentVersionRow
from app.experiments.risk import ExperimentRiskPlan, preview_admission
from app.experiments.scheduling import ExperimentResearchScheduler, select_variant
from app.runtime.canonical import build_production_canonical_runtime
from app.schemas.common import StrategyLifecycleState
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.setup_ast import PatternAst
from app.schemas.trendpulse_screening import TrendPulseScreeningCreate
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
from app.strategy_brain.trendpulse_screening.pipeline import TrendPulsePipeline
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401
from tests.support.experiment_fixtures import experiment_world as _experiment_world  # noqa: F401
from tests.support.trendpulse_screening import DelayedReplayAcquirer
from tests.test_at067_canonical_strategy_evaluation_policy import _card, _seed_approved_compiled
from tests.test_experiment_risk import inputs
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


def running_world(w, **changes):
    w.now = END - timedelta(seconds=20)
    w.spec = spec()
    w.strategy, w.strategy_version, _ = _seed_approved_compiled(
        w.session, org=w.tenant.organization_id, user=w.tenant.user_id, spec=w.spec
    )
    w.version = w.running(
        w.configuration(
            timeframes=["15m", "5m"],
            sample_target={"kind": "setup_observation", "minimum": 1, "maximum": 2},
            **changes,
        )
    )
    w.now = END + timedelta(seconds=8)
    w.settings = w.settings.model_copy(update={"trendpulse_screening_enabled": True})
    return w


def test_durable_receipt_enters_exact_approved_candidate(experiment_world):
    w = running_world(experiment_world)
    screen = TrendPulseScreeningService(
        w.session, w.settings, acquirer=DelayedReplayAcquirer(), clock=lambda: w.now
    )
    receipt = screen.screen(
        w.tenant,
        w.version.experiment_id,
        w.version.id,
        TrendPulseScreeningCreate(request_id=uuid4(), variant_key="baseline", trigger_end=END),
    )
    runtime = build_production_canonical_runtime(
        sessionmaker(bind=w.session.get_bind()), clock=FrozenClock(w.now)
    )
    pipeline = TrendPulsePipeline(w.session, w.settings, runtime)
    candidate, _assessment, window, signal = pipeline.candidate(w.tenant, receipt.id, now=w.now)
    assert candidate.strategy_version_id == w.strategy_version.id
    assert candidate.evidence_window_hash == window.content_hash
    assert pipeline.candidate(w.tenant, receipt.id, now=w.now)[0] == candidate
    assert signal.target - signal.entry == signal.entry - signal.structural_stop
    assert w.service.view(w.session.get(ExperimentVersionRow, w.version.id)).sample_counts == {
        "baseline": 0
    }
    with pytest.raises(ConflictError, match="trigger_expired"):
        pipeline.candidate(w.tenant, receipt.id, now=END + timedelta(seconds=60))


@pytest.mark.parametrize("offset", [-1, 0, 4, 60, 80])
def test_scheduler_never_backfills_or_acquires_outside_window(experiment_world, offset):
    w = running_world(experiment_world)
    source = DelayedReplayAcquirer()
    screen = TrendPulseScreeningService(w.session, w.settings, acquirer=source, clock=lambda: w.now)
    scheduler = ExperimentResearchScheduler(
        w.session, w.settings, screening=screen, clock=lambda: w.now
    )
    w.now = END + timedelta(seconds=offset)
    assert scheduler.tick(w.tenant) == [] and source.calls == 0


def test_scheduler_durable_cap_is_not_a_sample(experiment_world):
    w = running_world(experiment_world)
    source = DelayedReplayAcquirer()
    screen = TrendPulseScreeningService(w.session, w.settings, acquirer=source, clock=lambda: w.now)
    scheduler = ExperimentResearchScheduler(
        w.session, w.settings, screening=screen, clock=lambda: w.now
    )
    assert len(scheduler.tick(w.tenant)) == 1
    assert scheduler.tick(w.tenant) == [] and source.calls == 1
    assert w.version.sample_counts == {"baseline": 0}
    assert select_variant(w.version, {"baseline": 2}) is None
    w.version = w.transition(w.version, "pause")
    assert scheduler.tick(w.tenant) == []


@pytest.mark.parametrize("mode", ["exploration", "validation"])
def test_modes_keep_independent_sample_groups(experiment_world, mode):
    w = running_world(experiment_world, mode=mode)
    assert select_variant(w.version, {}) == "baseline"
    assert w.version.configuration.mode.value == mode
    other = w.create(w.version.configuration)
    assert other.sample_group_id != w.version.sample_group_id
    assert other.sample_counts == {"baseline": 0}


def test_trendpulse_preview_uses_existing_limits_and_exact_1r(experiment_world):
    w = running_world(experiment_world)
    _, state = inputs(w)
    plan = ExperimentRiskPlan(
        entry="125.60",
        structural_stop="124.54",
        target="126.66",
        direction="LONG",
        leverage=1,
        quantity_unit="BASE",
        instrument_rules=path()["instrument_rules"],
    )
    admission = preview_admission(w.version, plan, state, now=w.now)
    assert admission.admissible and admission.maximum_loss <= Decimal("10")
    assert not admission.management_authority
    assert not preview_admission(
        w.version, plan, state.model_copy(update={"kill_switch_active": True}), now=w.now
    ).admissible
    assert (
        preview_admission(
            w.version, plan.model_copy(update={"target": Decimal("127.66")}), state, now=w.now
        ).reason
        == "trendpulse_source_or_1r_geometry_mismatch"
    )


def test_public_shape_fixture_uses_existing_eligibility_plan_and_approval_boundary(
    experiment_world,
):
    from app.schemas.approval import ApprovalDecisionRequest
    from app.schemas.common import ApprovalAction
    from app.schemas.trade_plan import AuthorizationChannel
    from app.services.approval_service import ApprovalService
    from app.services.audit_service import AuditService
    from app.services.planned_reward_risk import planned_reward_risk
    from app.strategy_brain.trendpulse_screening.acquisition import BinanceScreeningAcquirer
    from tests.support.phase6_eligibility import (
        account_identity,
        eligibility_command,
        market_action,
        portfolio_state,
        risk_snapshot,
        safety_snapshot,
    )
    from tests.support.trendpulse_screening import BinanceTransport

    w = running_world(experiment_world)
    w.settings = w.settings.model_copy(
        update={
            "market_data_enabled": True,
            "perpetual_evidence_source": "binance_usdm",
            "perpetual_evidence_secondary_source": "none",
        }
    )
    transport = BinanceTransport()
    screen = TrendPulseScreeningService(
        w.session,
        w.settings,
        acquirer=BinanceScreeningAcquirer(transport=transport.transport, clock=lambda: w.now),
        clock=lambda: w.now,
    )
    receipt = screen.screen(
        w.tenant,
        w.version.experiment_id,
        w.version.id,
        TrendPulseScreeningCreate(request_id=uuid4(), variant_key="baseline", trigger_end=END),
    )
    runtime = build_production_canonical_runtime(
        sessionmaker(bind=w.session.get_bind()), clock=FrozenClock(w.now)
    )
    pipeline = TrendPulsePipeline(w.session, w.settings, runtime)
    candidate, assessment, window, signal = pipeline.candidate(w.tenant, receipt.id, now=w.now)
    account = {"organization_id": w.tenant.organization_id, "account_id": w.account.id}
    ec = eligibility_command(
        candidate=candidate,
        assessment=assessment,
        window=window,
        account=account_identity(**account, user_id=w.tenant.user_id),
        portfolio=portfolio_state(**account),
        risk=risk_snapshot(**account, user_id=w.tenant.user_id),
        safety=safety_snapshot(**account),
        market=market_action(
            evidence_price=signal.entry,
            execution_price=signal.entry,
            action_evidence_valid_until=signal.expires_at,
        ),
    )
    _, state = inputs(w)
    envelope = pipeline.prepare_internal_plan(
        w.tenant,
        receipt.id,
        now=w.now,
        eligibility_command=ec,
        risk_state=state,
        quote_observed_at=w.now,
    )
    plan = envelope.plan
    assert plan.risk_and_exits.stop.value == signal.structural_stop
    assert plan.risk_and_exits.targets[0].price.value == signal.target
    assert plan.risk_and_exits.targets[0].quantity_fraction == 1
    assert planned_reward_risk(plan).ratio == 1
    assert not plan.risk_and_exits.runner.enabled
    assert plan.account_id == w.version.configuration.account.execution_account_id
    assert envelope.live_executable is False and len(transport.calls) == 5
    # Ordinary approval service binds this exact revision; no dispatcher call occurs.
    approvals = ApprovalService(
        w.session, AuditService(w.session), clock=lambda: w.now, plans=runtime.plans
    )
    approval = approvals.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=w.tenant.organization_id,
        user_id=w.tenant.user_id,
    )
    with runtime.bind_session(w.session):
        decision = approvals.decide(
            approval.id,
            ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
            principal_organization_id=w.tenant.organization_id,
            principal_user_id=w.tenant.user_id,
            channel=AuthorizationChannel.API,
        )
    assert decision.authorization is not None
    from app.schemas.execution_protocol import ExecutePaperPlanRequest, ExecutionCommandOutcome
    from app.services.canonical_paper_execution import CanonicalPaperExecutionService
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.safety_epoch import SafetyEpochService

    audit = AuditService(w.session)
    execution = CanonicalPaperExecutionService(
        w.session,
        w.settings,
        audit,
        runtime,
        safety_epochs=SafetyEpochService(
            w.session, w.settings, RiskSettingsService(w.session, audit)
        ),
        clock=lambda: w.now,
    )
    result = execution.execute(
        ExecutePaperPlanRequest(
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            account_id=w.account.id,
            authorization_id=decision.authorization.authorization_id,
            revision_id=plan.revision_id,
            idempotency_key="fixture-trendpulse-canonical-execution",
        )
    )
    assert result.outcome is ExecutionCommandOutcome.ALLOW
    # This is the established internal simulator claim, never an exchange POST.
    assert len(transport.calls) == 5


def test_exploration_selects_distinct_immutable_variants_without_validation_leakage(
    experiment_world,
):
    from app.schemas.experiments import ExperimentVariant

    w = running_world(experiment_world)
    first = w.version.configuration.variants[0]
    second = ExperimentVariant(
        key="alternate", strategy_version_id=first.strategy_version_id, parameters=first.parameters
    )
    version = w.version.model_copy(
        update={
            "configuration": w.version.configuration.model_copy(
                update={"variants": (first, second)}
            )
        }
    )
    assert select_variant(version, {"baseline": 1, "alternate": 0}) == "alternate"
    assert select_variant(version, {"baseline": 1, "alternate": 2}) == "baseline"
    assert select_variant(version, {"baseline": 2, "alternate": 2}) is None


def test_scheduler_uses_new_sample_group_and_current_membership(experiment_world):
    from app.schemas.common import MembershipRole
    from app.schemas.experiments import ExperimentConfiguration

    w = running_world(experiment_world)
    source = DelayedReplayAcquirer()
    screen = TrendPulseScreeningService(w.session, w.settings, acquirer=source, clock=lambda: w.now)
    scheduler = ExperimentResearchScheduler(
        w.session, w.settings, screening=screen, clock=lambda: w.now
    )
    w.membership.role = MembershipRole.VIEWER
    w.session.flush()
    assert scheduler.tick(w.tenant) == [] and source.calls == 0
    w.membership.role = MembershipRole.OWNER
    w.session.flush()
    first = scheduler.tick(w.tenant)[0]
    # A fresh Validation draft retains zero samples and cannot acquire before a new approval/start.
    config = ExperimentConfiguration.model_validate(
        {**w.version.configuration.model_dump(), "mode": "validation"}
    )
    fresh = w.create(config)
    assert fresh.sample_group_id != first.signal.sample_group_id
    assert fresh.sample_counts == {"baseline": 0}
    assert scheduler.tick(w.tenant) == [] and source.calls == 1


def test_http_candidate_review_requires_role_and_exact_tenant(experiment_world):
    from dataclasses import replace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes.trendpulse_screening import router
    from app.core.auth import get_current_tenant
    from app.core.config import get_settings
    from app.core.dependencies import get_canonical_runtime
    from app.core.errors import register_exception_handlers
    from app.db.session import get_session
    from app.schemas.common import MembershipRole

    w = running_world(experiment_world)
    screen = TrendPulseScreeningService(
        w.session, w.settings, acquirer=DelayedReplayAcquirer(), clock=lambda: w.now
    )
    receipt = screen.screen(
        w.tenant,
        w.version.experiment_id,
        w.version.id,
        TrendPulseScreeningCreate(request_id=uuid4(), variant_key="baseline", trigger_end=END),
    )
    runtime = build_production_canonical_runtime(
        sessionmaker(bind=w.session.get_bind()), clock=FrozenClock(w.now)
    )
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    actor = [w.tenant]
    app.dependency_overrides[get_current_tenant] = lambda: actor[0]
    app.dependency_overrides[get_settings] = lambda: w.settings
    app.dependency_overrides[get_session] = lambda: w.session
    app.dependency_overrides[get_canonical_runtime] = lambda: runtime
    with TestClient(app) as client:
        target = f"/trendpulse-screenings/{receipt.id}/candidate"
        actor[0] = replace(w.tenant, membership_role=MembershipRole.VIEWER)
        assert client.post(target).status_code == 403
        actor[0] = w.tenant
        first = client.post(target)
        assert first.status_code == 200, first.text
        assert client.post(target).json() == first.json()
        actor[0] = replace(w.tenant, organization_id=uuid4())
        assert client.post(target).status_code == 403
        actor[0] = w.tenant
        assert client.post(f"/trendpulse-screenings/{uuid4()}/candidate").status_code == 404


def test_http_research_tick_has_bounded_effects_and_keeps_disabled_setting(experiment_world):
    from dataclasses import replace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes.experiments import router
    from app.core.auth import get_current_tenant
    from app.core.config import get_settings
    from app.core.errors import register_exception_handlers
    from app.db.session import get_session
    from app.schemas.common import MembershipRole

    w = running_world(experiment_world)
    w.settings = w.settings.model_copy(update={"trendpulse_screening_enabled": False})
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    actor = [replace(w.tenant, membership_role=MembershipRole.VIEWER)]
    app.dependency_overrides[get_current_tenant] = lambda: actor[0]
    app.dependency_overrides[get_settings] = lambda: w.settings
    app.dependency_overrides[get_session] = lambda: w.session
    with TestClient(app) as client:
        assert client.post("/experiments/research/tick").status_code == 403
        actor[0] = w.tenant
        result = client.post("/experiments/research/tick")
        assert result.json() == {
            "screenings": [],
            "execution_requested": False,
            "samples_admitted": 0,
        }
        assert not w.settings.trendpulse_screening_enabled
        for limit in (0, 3):
            assert client.post(f"/experiments/research/tick?limit={limit}").status_code == 422


def test_legacy_watcher_refuses_trendpulse_with_precise_receipt_requirement():
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.watcher.errors import WatcherEvidenceUnavailableError
    from tests.test_live_evidence_pipeline import _evaluation_command

    p = policy()
    port = AssemblingWatcherScanEvidence(None, executable_resolver=lambda _command: p)
    with pytest.raises(WatcherEvidenceUnavailableError) as error:
        port.load(_evaluation_command(p.organization_id))
    assert error.value.reason_code == "trendpulse_screening_receipts_required"
