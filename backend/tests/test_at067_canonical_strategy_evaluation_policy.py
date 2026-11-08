"""Canonical strategy evaluation policy (AT-067).

Approved compiled versions drive SetupAssessment through one boundary.
First-slice predicates are a compatibility adapter, not a second authority.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import NotFoundError
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import Membership, Organization, User, UserStrategy
from app.schemas.common import MembershipRole, StrategyId, StrategyLifecycleState
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate
from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec
from app.services.canonical_strategy_evaluation import (
    evaluate_canonical_strategy_for_version,
    resolve_executable_strategy_policy,
)
from app.services.compiled_setup_service import CompiledSetupService
from app.services.paper_validation_runtime_service import PaperValidationRuntimeService
from app.services.setup_ast_compiler import compile_from_spec
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService
from app.signal_fusion.enums import AssessmentReasonCode, SetupAssessmentState, SetupIdentityKind
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.first_slice_adapter import (
    bind_first_slice_compatibility_adapter,
    default_first_slice_evaluation_params,
)
from app.signal_fusion.policy import build_fusion_policy
from app.signal_fusion.strategy_evaluation_policy import (
    evaluate_canonical_strategy,
    executable_policy_from_fusion_policy,
)
from app.signal_fusion.types import ExecutableSetupRef
from app.watcher.fusion_evaluation import WatcherCanonicalScanEvidence
from tests.support.phase6_evaluator import EvaluatorWorld, make_world
from tests.support.phase6_fusion import (
    STRATEGY_ID,
)

ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000a067")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000b067")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000c067")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000d067")

_POLICY_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "app"
    / "signal_fusion"
    / "strategy_evaluation_policy.py"
)
_ADAPTER_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "app" / "signal_fusion" / "first_slice_adapter.py"
)
_SERVICE_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "app"
    / "services"
    / "canonical_strategy_evaluation.py"
)
_EVALUATOR_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "app" / "signal_fusion" / "evaluator.py"
)
_WATCHER_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "app" / "watcher" / "fusion_evaluation.py"
)
_PAPER_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "app"
    / "services"
    / "paper_validation_runtime_service.py"
)


def _engine() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    install_persistence_firewall()
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session() -> Iterator[Session]:
    factory = _engine()
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG_A, name="AT067 Org A"),
                Organization(id=ORG_B, name="AT067 Org B"),
                User(id=USER_A, email="at067-a@test.example", hashed_password="not-a-real-hash"),
                User(id=USER_B, email="at067-b@test.example", hashed_password="not-a-real-hash"),
            ]
        )
        db.flush()
        db.add_all(
            [
                Membership(organization_id=ORG_A, user_id=USER_A, role=MembershipRole.TRADER),
                Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.TRADER),
            ]
        )
        db.commit()
        yield db


def _card() -> StrategyCard:
    return StrategyCard.model_validate(
        {
            "strategy_name": canonical_first_slice_authored_spec().name,
            "market_type": "crypto_perp",
            "asset_universe": ["BTCUSDT"],
            "timeframes": ["15m", "4h"],
            "entry_conditions": ["Bearish liquidity sweep at 4h resistance"],
            "confirmation_conditions": ["CVD divergence"],
            "invalidation": ["Close back above sweep high"],
            "stop_loss": ["Above sweep high"],
            "take_profit_plan": ["TP1 at 1R"],
            "runner_plan": [],
            "position_sizing": ["1%"],
            "add_rules": [],
            "no_trade_rules": [],
            "backtest_rules": [],
            "success_criteria": [],
            "validation_status": "draft",
        }
    )


def _create_strategy(session: Session, *, org: UUID = ORG_A, user: UUID = USER_A) -> UserStrategy:
    return StrategyLibraryService(session).create(
        UserStrategyCreate(
            organization_id=org,
            user_id=user,
            name="AT067 Sweep",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            card=_card(),
        )
    )


def _attach_spec(
    session: Session,
    strategy_id: UUID,
    *,
    spec: object | None = None,
    user_id: UUID = USER_A,
) -> object:
    from app.schemas.common import StrategyChangeSource

    strategy = session.get(UserStrategy, strategy_id)
    assert strategy is not None
    versioning = StrategyVersioningService(session)
    parent = versioning.selected_version(strategy)
    assert parent is not None
    payload = (
        canonical_first_slice_authored_spec().model_dump(mode="json")
        if spec is None
        else spec
        if isinstance(spec, dict)
        else spec.model_dump(mode="json")  # type: ignore[union-attr]
    )
    return versioning.fork_semantic_update(
        strategy,
        parent=parent,
        card=parent.card,
        structured_rules=parent.structured_rules,
        lesson_source_metadata=parent.lesson_source_metadata,
        actor_user_id=user_id,
        source=StrategyChangeSource.PATTERN_SPEC,
        reason="attach pattern spec",
        pattern_spec=payload,
    )


def _approve(session: Session, strategy: UserStrategy, version_id: UUID, *, user_id: UUID) -> None:
    StrategyVersioningService(session).append_lifecycle(
        organization_id=strategy.organization_id,
        strategy_id=strategy.id,
        strategy_version_id=version_id,
        new_state=StrategyLifecycleState.APPROVED,
        actor_user_id=user_id,
        reason="approve compiled first-slice policy",
    )


def _seed_approved_compiled(
    session: Session,
    *,
    org: UUID = ORG_A,
    user: UUID = USER_A,
    spec: object | None = None,
) -> tuple[UserStrategy, object, object]:
    created = _create_strategy(session, org=org, user=user)
    version = _attach_spec(session, created.id, spec=spec, user_id=user)
    session.flush()
    compiled = CompiledSetupService(session).compile_version(
        version.id, organization_id=org, user_id=user
    )
    assert compiled.compiled is not None
    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    _approve(session, strategy, version.id, user_id=user)
    session.flush()
    return strategy, version, compiled.compiled


def _policy_for_world(
    world: EvaluatorWorld,
    *,
    spec: object | None = None,
    lifecycle: StrategyLifecycleState = StrategyLifecycleState.APPROVED,
):
    from app.schemas.strategy_pattern_spec import FirstSliceAuthoredPatternSpec

    if spec is None:
        authored: FirstSliceAuthoredPatternSpec = canonical_first_slice_authored_spec()
    elif isinstance(spec, FirstSliceAuthoredPatternSpec):
        authored = spec
    else:
        authored = FirstSliceAuthoredPatternSpec.model_validate(spec)
    return executable_policy_from_fusion_policy(
        world.policy,
        strategy_id=STRATEGY_ID,
        strategy_version_content_hash=world.policy.executable_setup.content_hash,
        authored_spec=authored,
        lifecycle_state=lifecycle,
    )


def _bind_world(world: EvaluatorWorld, *, setup: ExecutableSetupRef, version_id: UUID, org: UUID):
    src = world.policy
    policy = build_fusion_policy(
        policy_version=src.policy_version,
        organization_id=org,
        strategy_version_id=version_id,
        executable_setup=setup,
        required_roles=src.required_roles,
        thresholds=src.thresholds,
        freshness_policy_version=src.freshness_policy_version,
        finality_policy_version=src.finality_policy_version,
        optional_roles=src.optional_roles,
        disqualifying_roles=src.disqualifying_roles,
        correction_selection_policy=src.correction_selection_policy,
        role_timeframes=src.role_timeframes,
        required_assertion_roles=src.required_assertion_roles,
        identity_assertion_roles=src.identity_assertion_roles,
    )
    command = world.command.model_copy(
        update={
            "organization_id": org,
            "strategy_version_id": version_id,
            "executable_setup": setup,
        }
    )
    return EvaluatorWorld(
        policy=policy,
        command=command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
        bars_15m=world.bars_15m,
        bars_4h=world.bars_4h,
        snapshot=world.snapshot,
        trigger=world.trigger,
    )


def _no_llm(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "import openai" not in lowered
    assert "from openai" not in lowered
    assert "langgraph" not in lowered
    assert "anthropic" not in lowered
    assert "chatgpt" not in lowered


def test_adapter_parity_with_canonical_spec() -> None:
    bound = bind_first_slice_compatibility_adapter(canonical_first_slice_authored_spec())
    assert bound == default_first_slice_evaluation_params()


def test_determinism_same_strategy_and_evidence() -> None:
    world = make_world()
    policy = _policy_for_world(world)
    first = evaluate_canonical_strategy(
        executable_policy=policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    second = evaluate_canonical_strategy(
        executable_policy=policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert first.state is SetupAssessmentState.CONFIRMED_SETUP
    assert first.assessment_id == second.assessment_id
    assert first.content_hash == second.content_hash
    assert first.strategy_version_id == world.command.strategy_version_id
    assert first.executable_setup == world.command.executable_setup


def test_duplicate_evaluation_is_idempotent() -> None:
    test_determinism_same_strategy_and_evidence()


def test_compatibility_parity_with_evaluate_setup() -> None:
    world = make_world()
    direct = evaluate_setup(
        policy=world.policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    via_policy = evaluate_canonical_strategy(
        executable_policy=_policy_for_world(world),
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert direct.assessment_id == via_policy.assessment_id
    assert direct.content_hash == via_policy.content_hash
    assert direct.state is via_policy.state is SetupAssessmentState.CONFIRMED_SETUP


def test_version_change_changes_assessment() -> None:
    world = make_world()
    canonical = canonical_first_slice_authored_spec()
    tight = canonical.model_copy(update={"volume_ratio_threshold": Decimal("99")})
    compiled_a = compile_from_spec(canonical)
    compiled_b = compile_from_spec(tight)
    assert compiled_a.document is not None
    assert compiled_b.document is not None
    assert compiled_a.document.content_hash != compiled_b.document.content_hash

    setup_a = ExecutableSetupRef(
        setup_definition_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01"),
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash=compiled_a.document.content_hash,
    )
    setup_b = ExecutableSetupRef(
        setup_definition_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa02"),
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash=compiled_b.document.content_hash,
    )
    version_a = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01")
    version_b = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02")
    org = world.policy.organization_id
    world_a = _bind_world(world, setup=setup_a, version_id=version_a, org=org)
    world_b = _bind_world(world, setup=setup_b, version_id=version_b, org=org)
    left = evaluate_canonical_strategy(
        executable_policy=_policy_for_world(world_a, spec=canonical),
        command=world_a.command,
        evidence=world_a.evidence,
        evaluated_at=world_a.evaluated_at,
    )
    right = evaluate_canonical_strategy(
        executable_policy=_policy_for_world(world_b, spec=tight),
        command=world_b.command,
        evidence=world_b.evidence,
        evaluated_at=world_b.evaluated_at,
    )
    assert left.state is SetupAssessmentState.CONFIRMED_SETUP
    assert right.state is not SetupAssessmentState.CONFIRMED_SETUP
    assert left.evidence_window_hash != right.evidence_window_hash
    assert left.strategy_version_id != right.strategy_version_id


def test_unsupported_rule_fail_closed() -> None:
    spec = canonical_first_slice_authored_spec().model_copy(
        update={"requires_cvd_divergence": False}
    )
    with pytest.raises(StrategyEvaluationPolicyError) as exc:
        bind_first_slice_compatibility_adapter(spec)
    assert exc.value.reason_code == "unsupported_strategy_rule"


def test_draft_cannot_become_evaluation_policy() -> None:
    world = make_world()
    with pytest.raises(StrategyEvaluationPolicyError) as exc:
        _policy_for_world(world, lifecycle=StrategyLifecycleState.DRAFT)
    assert exc.value.reason_code == "draft_not_executable"


def test_stale_evidence_fail_closed_through_policy() -> None:
    world = make_world(stale_command=True)
    assessment = evaluate_canonical_strategy(
        executable_policy=_policy_for_world(world),
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert assessment.state is SetupAssessmentState.NO_SETUP
    assert AssessmentReasonCode.REQUIRED_SOURCE_STALE in assessment.reason_codes


def test_strategy_evidence_mismatch_fail_closed() -> None:
    world = make_world()
    policy = _policy_for_world(world)
    command = world.command.model_copy(update={"strategy_version_id": uuid.uuid4()})
    with pytest.raises(StrategyEvaluationPolicyError) as exc:
        evaluate_canonical_strategy(
            executable_policy=policy,
            command=command,
            evidence=world.evidence,
            evaluated_at=world.evaluated_at,
        )
    assert exc.value.reason_code == "strategy_evidence_mismatch"


def test_lineage_is_preserved() -> None:
    world = make_world()
    policy = _policy_for_world(world)
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert assessment.strategy_version_id == policy.strategy_version_id
    assert assessment.executable_setup.setup_definition_id == policy.compiled_setup_definition_id
    assert assessment.executable_setup.content_hash == policy.compiled_content_hash
    assert assessment.organization_id == policy.organization_id


def test_policy_does_not_construct_setup_assessment() -> None:
    text = _POLICY_SRC.read_text(encoding="utf-8")
    assert "build_setup_assessment" not in text
    assert "evaluate_setup(" in text


def test_no_llm_in_deterministic_evaluation() -> None:
    for path in (_POLICY_SRC, _ADAPTER_SRC, _SERVICE_SRC, _EVALUATOR_SRC, _WATCHER_SRC):
        _no_llm(path)


def test_watcher_and_paper_validation_call_the_boundary() -> None:
    watcher = _WATCHER_SRC.read_text(encoding="utf-8")
    paper = _PAPER_SRC.read_text(encoding="utf-8")
    assert "evaluate_canonical_strategy(" in watcher
    assert "evaluate_setup(" not in watcher
    assert "evaluate_canonical_strategy_for_version" in paper
    assert "evaluate_canonical_strategy(" in paper
    assert "evaluate_entry(" not in paper
    assert "WATCHER_ORCHESTRATION_ENABLED" not in watcher


def test_approved_compiled_version_evaluates(session: Session) -> None:
    strategy, version, compiled = _seed_approved_compiled(session)
    setup = ExecutableSetupRef(
        setup_definition_id=compiled.id,
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash=compiled.content_hash,
    )
    world = _bind_world(
        make_world(),
        setup=setup,
        version_id=version.id,
        org=ORG_A,
    )
    assessment = evaluate_canonical_strategy_for_version(
        session,
        organization_id=ORG_A,
        strategy_version_id=version.id,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
        user_id=USER_A,
    )
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    assert assessment.strategy_version_id == version.id
    assert assessment.executable_setup.content_hash == compiled.content_hash
    paper = PaperValidationRuntimeService(session).evaluate_canonical_setup(
        strategy_version_id=version.id,
        organization_id=ORG_A,
        user_id=USER_A,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert paper.assessment_id == assessment.assessment_id
    del strategy


def test_draft_version_resolve_fail_closed(session: Session) -> None:
    created = _create_strategy(session)
    version = _attach_spec(session, created.id)
    session.flush()
    with pytest.raises(StrategyEvaluationPolicyError) as draft_exc:
        resolve_executable_strategy_policy(
            session, organization_id=ORG_A, strategy_version_id=version.id, user_id=USER_A
        )
    assert draft_exc.value.reason_code == "draft_not_executable"
    CompiledSetupService(session).compile_version(version.id, organization_id=ORG_A, user_id=USER_A)
    with pytest.raises(StrategyEvaluationPolicyError) as review_exc:
        resolve_executable_strategy_policy(
            session, organization_id=ORG_A, strategy_version_id=version.id, user_id=USER_A
        )
    assert review_exc.value.reason_code == "strategy_not_approved"


def test_tenant_isolation(session: Session) -> None:
    _strategy, version, _compiled = _seed_approved_compiled(session)
    with pytest.raises(NotFoundError) as cross_org:
        resolve_executable_strategy_policy(
            session, organization_id=ORG_B, strategy_version_id=version.id, user_id=USER_B
        )
    with pytest.raises(NotFoundError) as missing:
        resolve_executable_strategy_policy(
            session, organization_id=ORG_B, strategy_version_id=uuid.uuid4(), user_id=USER_B
        )
    # A foreign version is indistinguishable from a missing one.
    assert type(cross_org.value) is type(missing.value)
    assert str(cross_org.value) == str(missing.value) == "Strategy version not found."


def test_unsupported_stored_spec_fail_closed(session: Session) -> None:
    created = _create_strategy(session)
    version = _attach_spec(session, created.id, spec={"not": "a-pattern"})
    session.flush()
    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    _approve(session, strategy, version.id, user_id=USER_A)
    session.flush()
    with pytest.raises(StrategyEvaluationPolicyError) as exc:
        resolve_executable_strategy_policy(
            session, organization_id=ORG_A, strategy_version_id=version.id, user_id=USER_A
        )
    assert exc.value.reason_code in {"unsupported_strategy_rule", "compiled_definition_missing"}


def test_watcher_snapshot_requires_executable_policy() -> None:
    world = make_world()
    snapshot = WatcherCanonicalScanEvidence.model_validate(
        {
            "organization_id": world.command.organization_id,
            "policy": world.policy,
            "executable_policy": _policy_for_world(world),
            "assessment_command": world.command,
            "evidence": world.evidence,
            "evaluated_at": world.evaluated_at,
        }
    )
    assert snapshot.executable_policy.fusion_policy.content_hash == world.policy.content_hash
