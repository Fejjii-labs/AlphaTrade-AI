"""Non-vacuous AUTO_PAPER authority: CONFIRMED_SETUP only.

Persisted approved compiled policy → canonical evidence →
evaluate_canonical_strategy → CONFIRMED_SETUP → paper workflow.
In-memory, draft, stale, expired, wrong-tenant, and wrong-hash inputs fail
closed. Watcher, Telegram, and live trading stay disabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import Membership, Organization, PaperSignal, User, UserStrategy
from app.schemas.common import (
    MembershipRole,
    PaperValidationRuntimeMode,
    StrategyId,
    StrategyLifecycleState,
)
from app.schemas.paper_validation import PaperValidationConfig, PaperValidationRunStart
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate
from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.compiled_setup_service import CompiledSetupService
from app.services.paper_validation_runtime_service import (
    CanonicalPaperEvidence,
    PaperValidationRuntimeService,
)
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService
from app.signal_fusion.enums import SetupAssessmentState, SetupIdentityKind
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.strategy_evaluation_policy import (
    evaluate_canonical_strategy,
    executable_policy_from_fusion_policy,
)
from app.signal_fusion.types import ExecutableSetupRef
from tests.support.phase6_evaluator import EvaluatorWorld, make_world, subsequent_bars
from tests.support.phase6_fusion import STRATEGY_ID

ORG = UUID("00000000-0000-0000-0000-00000000a168")
ORG_B = UUID("00000000-0000-0000-0000-00000000b168")
USER = UUID("00000000-0000-0000-0000-00000000c168")
USER_B = UUID("00000000-0000-0000-0000-00000000d168")

_PAPER_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "app"
    / "services"
    / "paper_validation_runtime_service.py"
)


def _settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="canonical-paper-authority-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        watcher_orchestration_enabled=False,
        perpetual_evidence_source="replay",
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
                Organization(id=ORG, name="AT068 Org A"),
                Organization(id=ORG_B, name="AT068 Org B"),
                User(id=USER, email="at068-a@test.example", hashed_password="not-a-real-hash"),
                User(id=USER_B, email="at068-b@test.example", hashed_password="not-a-real-hash"),
            ]
        )
        db.flush()
        db.add_all(
            [
                Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER),
                Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.TRADER),
            ]
        )
        db.commit()
        yield db


def _card() -> StrategyCard:
    spec = canonical_first_slice_authored_spec()
    return StrategyCard.model_validate(
        {
            "strategy_name": spec.name,
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


def _create_strategy(session: Session, *, org: UUID = ORG, user: UUID = USER) -> UserStrategy:
    return StrategyLibraryService(session).create(
        UserStrategyCreate(
            organization_id=org,
            user_id=user,
            name=f"AT068 Sweep {uuid4().hex[:8]}",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            card=_card(),
        )
    )


def _attach_spec(session: Session, strategy_id: UUID, *, user_id: UUID = USER) -> object:
    from app.schemas.common import StrategyChangeSource

    strategy = session.get(UserStrategy, strategy_id)
    assert strategy is not None
    versioning = StrategyVersioningService(session)
    parent = versioning.selected_version(strategy)
    assert parent is not None
    return versioning.fork_semantic_update(
        strategy,
        parent=parent,
        card=parent.card,
        structured_rules=parent.structured_rules,
        lesson_source_metadata=parent.lesson_source_metadata,
        actor_user_id=user_id,
        source=StrategyChangeSource.PATTERN_SPEC,
        reason="attach pattern spec",
        pattern_spec=canonical_first_slice_authored_spec().model_dump(mode="json"),
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


def _seed_approved_compiled(session: Session) -> tuple[UserStrategy, object, object]:
    created = _create_strategy(session)
    version = _attach_spec(session, created.id)
    session.flush()
    compiled = CompiledSetupService(session).compile_version(
        version.id, organization_id=ORG, user_id=USER
    )
    assert compiled.compiled is not None
    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    strategy.paper_eligible = True
    _approve(session, strategy, version.id, user_id=USER)
    session.flush()
    return strategy, version, compiled.compiled


def _bind_world(
    world: EvaluatorWorld, *, setup: ExecutableSetupRef, version_id: UUID, org: UUID
) -> EvaluatorWorld:
    from app.signal_fusion.policy import build_fusion_policy

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


def _world_for_compiled(
    compiled: object, version_id: UUID, world: EvaluatorWorld
) -> EvaluatorWorld:
    setup = ExecutableSetupRef(
        setup_definition_id=compiled.id,
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash=compiled.content_hash,
    )
    return _bind_world(world, setup=setup, version_id=version_id, org=ORG)


def _loader_for(world: EvaluatorWorld):
    def _load(_policy: object) -> CanonicalPaperEvidence:
        return CanonicalPaperEvidence(
            command=world.command,
            evidence=world.evidence,
            evaluated_at=world.evaluated_at,
        )

    return _load


def _start_auto_paper(
    session: Session,
    strategy: UserStrategy,
    *,
    evidence_loader,
) -> tuple[PaperValidationRuntimeService, UUID]:
    runtime = PaperValidationRuntimeService(session, _settings(), evidence_loader=evidence_loader)
    started = runtime.start(
        strategy.id,
        PaperValidationRunStart(
            runtime_mode=PaperValidationRuntimeMode.AUTO_PAPER,
            config=PaperValidationConfig(),
        ),
        organization_id=ORG,
        user_id=USER,
    )
    return runtime, started.id


def test_scan_source_is_canonical_evaluator_not_paper_bot() -> None:
    text = _PAPER_SRC.read_text(encoding="utf-8")
    engine = Path(_PAPER_SRC.parent / "paper_bot_engine.py").read_text(encoding="utf-8")
    assert "evaluate_canonical_strategy(" in text
    assert "evaluate_entry(" not in text
    assert "resolve_backtest_rules" not in text
    assert "Not an AUTO_PAPER minting authority" in engine


def test_persisted_confirmed_setup_mints_auto_paper_trade(session: Session) -> None:
    strategy, version, compiled = _seed_approved_compiled(session)
    world = _world_for_compiled(compiled, version.id, make_world())
    policy = resolve_executable_strategy_policy(
        session, organization_id=ORG, strategy_version_id=version.id, user_id=USER
    )
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP

    runtime, run_id = _start_auto_paper(session, strategy, evidence_loader=_loader_for(world))
    scanned = runtime.scan(run_id, organization_id=ORG, user_id=USER)
    assert scanned.trade_created is True
    assert scanned.signal.status.value == "consumed"
    assert scanned.signal.rule_engine_source == "canonical_setup"
    trades = runtime.list_trades(run_id, organization_id=ORG)
    assert trades.total == 1
    assert trades.items[0].rule_engine_source == "canonical_setup"
    assert trades.items[0].direction.value == "short"


def test_in_memory_policy_cannot_mint_auto_paper(session: Session) -> None:
    strategy, version, compiled = _seed_approved_compiled(session)
    world = _world_for_compiled(compiled, version.id, make_world())
    in_memory = executable_policy_from_fusion_policy(
        world.policy,
        strategy_id=STRATEGY_ID,
        strategy_version_content_hash=world.policy.executable_setup.content_hash,
        authored_spec=canonical_first_slice_authored_spec(),
    )
    assessment = evaluate_canonical_strategy(
        executable_policy=in_memory,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    persisted = resolve_executable_strategy_policy(
        session, organization_id=ORG, strategy_version_id=version.id, user_id=USER
    )
    assert in_memory.content_hash != persisted.content_hash
    runtime = PaperValidationRuntimeService(
        session, _settings(), evidence_loader=_loader_for(world)
    )
    started = runtime.start(
        strategy.id,
        PaperValidationRunStart(runtime_mode=PaperValidationRuntimeMode.SCAN_ONLY),
        organization_id=ORG,
        user_id=USER,
    )
    scanned = runtime.scan(started.id, organization_id=ORG, user_id=USER)
    assert scanned.trade_created is False
    signal_row = session.get(PaperSignal, scanned.signal.id)
    assert signal_row is not None
    minted = runtime._mint_auto_paper_from_confirmed(
        runtime._ensure_run(started.id, organization_id=ORG),
        policy=in_memory,
        assessment=assessment,
        loaded=CanonicalPaperEvidence(
            command=world.command,
            evidence=world.evidence,
            evaluated_at=world.evaluated_at,
        ),
        signal_row=signal_row,
        organization_id=ORG,
        user_id=USER,
        config=PaperValidationConfig(),
        engine_source="canonical_setup",
    )
    assert minted is False
    assert runtime.list_trades(started.id, organization_id=ORG).total == 0


def test_draft_and_review_required_cannot_mint(session: Session) -> None:
    created = _create_strategy(session)
    strategy_row = session.get(UserStrategy, created.id)
    assert strategy_row is not None
    strategy_row.paper_eligible = True
    session.flush()
    version = _attach_spec(session, created.id)
    session.flush()
    runtime = PaperValidationRuntimeService(
        session, _settings(), evidence_loader=_loader_for(make_world())
    )
    with pytest.raises(StrategyEvaluationPolicyError):
        resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=version.id, user_id=USER
        )
    started = runtime.start(
        created.id,
        PaperValidationRunStart(runtime_mode=PaperValidationRuntimeMode.AUTO_PAPER),
        organization_id=ORG,
        user_id=USER,
    )
    scanned = runtime.scan(started.id, organization_id=ORG, user_id=USER)
    assert scanned.trade_created is False
    assert scanned.signal.status.value == "not_testable"
    assert runtime.list_trades(started.id, organization_id=ORG).total == 0

    CompiledSetupService(session).compile_version(version.id, organization_id=ORG, user_id=USER)
    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    StrategyVersioningService(session).append_lifecycle(
        organization_id=ORG,
        strategy_id=created.id,
        strategy_version_id=version.id,
        new_state=StrategyLifecycleState.REVIEW_REQUIRED,
        actor_user_id=USER,
        reason="review required",
    )
    session.flush()
    with pytest.raises(StrategyEvaluationPolicyError) as exc:
        resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=version.id, user_id=USER
        )
    assert exc.value.reason_code == "strategy_not_approved"
    reviewed = runtime.scan(started.id, organization_id=ORG, user_id=USER)
    assert reviewed.trade_created is False
    assert runtime.list_trades(started.id, organization_id=ORG).total == 0


def test_stale_and_expired_evidence_cannot_mint(session: Session) -> None:
    strategy, version, compiled = _seed_approved_compiled(session)
    stale_world = _world_for_compiled(compiled, version.id, make_world(stale_command=True))
    runtime, run_id = _start_auto_paper(session, strategy, evidence_loader=_loader_for(stale_world))
    stale = runtime.scan(run_id, organization_id=ORG, user_id=USER)
    assert stale.trade_created is False
    assert stale.signal.status.value == "not_testable"

    confirmed = _world_for_compiled(compiled, version.id, make_world())
    later = subsequent_bars(confirmed.trigger, count=2, high=confirmed.trigger.high)
    expired_world = EvaluatorWorld(
        policy=confirmed.policy,
        command=confirmed.command,
        evidence=confirmed.evidence.model_copy(update={"subsequent_final_15m": tuple(later)}),
        evaluated_at=confirmed.evaluated_at + timedelta(minutes=30),
        bars_15m=confirmed.bars_15m,
        bars_4h=confirmed.bars_4h,
        snapshot=confirmed.snapshot,
        trigger=confirmed.trigger,
    )
    expired_runtime, expired_run = _start_auto_paper(
        session, strategy, evidence_loader=_loader_for(expired_world)
    )
    expired = expired_runtime.scan(expired_run, organization_id=ORG, user_id=USER)
    assert expired.trade_created is False
    assert expired.signal.status.value == "not_testable"
    assert "expired" in (expired.signal.reason or "").lower() or any(
        "expired" in item.lower() for item in expired.signal.limitations
    )


def test_wrong_tenant_and_wrong_compiled_hash_cannot_mint(session: Session) -> None:
    strategy, version, compiled = _seed_approved_compiled(session)
    world = _world_for_compiled(compiled, version.id, make_world())
    with pytest.raises(NotFoundError, match=r"Strategy version not found\."):
        resolve_executable_strategy_policy(
            session, organization_id=ORG_B, strategy_version_id=version.id, user_id=USER_B
        )

    bad_setup = ExecutableSetupRef(
        setup_definition_id=compiled.id,
        kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
        content_hash="ff" * 32,
    )
    mismatched = world.command.model_copy(update={"executable_setup": bad_setup})
    runtime, run_id = _start_auto_paper(
        session,
        strategy,
        evidence_loader=lambda _policy: CanonicalPaperEvidence(
            command=mismatched,
            evidence=world.evidence,
            evaluated_at=world.evaluated_at,
        ),
    )
    scanned = runtime.scan(run_id, organization_id=ORG, user_id=USER)
    assert scanned.trade_created is False
    assert scanned.signal.status.value == "not_testable"
    assert runtime.list_trades(run_id, organization_id=ORG).total == 0


def test_default_replay_assembly_does_not_vacuously_mint(session: Session) -> None:
    strategy, _version, _compiled = _seed_approved_compiled(session)
    runtime = PaperValidationRuntimeService(session, _settings())
    started = runtime.start(
        strategy.id,
        PaperValidationRunStart(runtime_mode=PaperValidationRuntimeMode.AUTO_PAPER),
        organization_id=ORG,
        user_id=USER,
    )
    scanned = runtime.scan(started.id, organization_id=ORG, user_id=USER)
    assert scanned.trade_created is False
    assert runtime.list_trades(started.id, organization_id=ORG).total == 0
