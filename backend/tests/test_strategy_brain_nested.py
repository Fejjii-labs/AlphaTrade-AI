"""Focused Nested Continuation causality, lifecycle and governed-loop regressions."""

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import Organization, User
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.market_contracts.enums import Finality
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import interval_timedelta
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.nested_continuation import BrainSetupState, NestedContinuationSpec
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.compiled_setup_service import CompiledSetupService
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.strategy_brain.agent import read_brain
from app.strategy_brain.detector import detect_nested
from app.strategy_brain.records import record_detections, scoped_setup_id
from app.strategy_brain.service import create_template, details, overview
from tests.support.phase5_market import closed_bar
from tests.test_watcher_paper_runtime import _sqlite_factory

PRICES = [
    103,
    102,
    100,
    102,
    104,
    108,
    112,
    110,
    109,
    107,
    108,
    110,
    114,
    118,
    116,
    114,
    111,
    113,
    116,
    120,
    124,
    122,
    120,
    117,
    119,
    122,
    126,
    130,
    128,
    126,
    123,
    125,
    128,
    132,
]
START = datetime(2026, 9, 1, tzinfo=UTC)


def bars(prices=PRICES, *, bearish=False, timeframe=Timeframe.M15):
    result = []
    step = interval_timedelta(timeframe)
    for i, number in enumerate(prices):
        value = Decimal(300 - number if bearish else number)
        start = START + step * i
        bar = closed_bar(open_time=start, timeframe=timeframe, index=i, evaluated_at=start + step)
        result.append(
            with_content_hash(
                bar.model_copy(
                    update={
                        "open": value,
                        "close": value,
                        "high": value + 1,
                        "low": value - 1,
                        "base_volume": Decimal(100),
                        "quote_volume": value * 100,
                    }
                )
            )
        )
    return tuple(result)


def detection(series, *, bearish=False, **parameters):
    spec = NestedContinuationSpec(
        symbol="BTCUSDT",
        direction=TradeDirection.SHORT if bearish else TradeDirection.LONG,
        trigger_timeframe=series[0].timeframe,
        parameters=parameters,
    )
    return detect_nested(series, spec, evaluated_at=series[-1].interval_end + timedelta(seconds=5))


@pytest.mark.parametrize("bearish", [False, True])
def test_mirrored_four_stage_progression_is_not_higher_high_counting(bearish):
    series = bars(bearish=bearish)
    confirmed = [
        event
        for event in detection(series, bearish=bearish)
        if event.state is BrainSetupState.CONFIRMED
    ]
    assert [event.stage for event in confirmed] == ["N1", "N2", "N3", "N4_PLUS"]
    assert [event.completed_continuations for event in confirmed] == [1, 2, 3, 4]
    assert len({event.sequence_id for event in confirmed}) == 1
    assert len({event.setup_id for event in confirmed}) == 4
    assert all(
        event.stop > event.entry if bearish else event.stop < event.entry for event in confirmed
    )


def test_forming_then_causal_confirmation_and_optional_missing():
    forming = detection(bars(PRICES[:12]))[-1]
    assert forming.state is BrainSetupState.FORMING
    confirmed = detection(bars(PRICES[:13]))[-1]
    assert confirmed.state is BrainSetupState.CONFIRMED
    assert confirmed.confirmed_index == 12
    assert confirmed.evidence["higher_timeframe"] == "MISSING"
    assert confirmed.evidence["order_flow"] == "UNSUPPORTED"
    assert confirmed.history_expectancy == "insufficient_history"
    assert forming.setup_id == confirmed.setup_id


def test_future_or_forming_candles_cannot_confirm():
    series = bars(PRICES[:13])
    spec = NestedContinuationSpec(symbol="BTCUSDT")
    assert detect_nested(series, spec, evaluated_at=series[-1].interval_start) == ()
    changed = (*series[:-1], series[-1].model_copy(update={"finality": Finality.FORMING}))
    assert detection(changed) == ()
    for length in range(5, len(series)):
        events = detection(series[:length])
        assert not any(e.state is BrainSetupState.CONFIRMED for e in events)


def test_invalidation_resets_to_a_distinct_episode():
    series = bars([*PRICES[:12], 95, 97, 98, 99, 101, 107, 109, 107, 105, 103, 105, 107, 111])
    events = detection(series)
    assert any(e.state is BrainSetupState.INVALIDATED for e in events)
    confirmations = [e for e in events if e.state is BrainSetupState.CONFIRMED]
    assert confirmations
    first = events[0]
    assert confirmations[-1].sequence_id != first.sequence_id
    assert confirmations[-1].stage == "N1"


def test_expired_formation_cannot_be_confirmed_later():
    series = bars([*PRICES[:12], *([109] * 12), 120])
    events = detection(series, confirmation_window=8)
    assert any(event.state is BrainSetupState.EXPIRED for event in events)
    assert not any(event.state is BrainSetupState.CONFIRMED for event in events)


def test_replay_does_not_change_past_decisions_with_later_pivots():
    full = bars()
    for length in (10, 12, 13, 20, 27):
        prefix = detection(full[:length])
        assert prefix == tuple(e for e in detection(full) if e.event_index < length)


@pytest.fixture
def tenant_store():
    factory = _sqlite_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="Brain"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        yield session, org, user


def approved(session, org, user, spec=None):
    template = create_template(
        session,
        organization_id=org,
        user_id=user,
        spec=spec or NestedContinuationSpec(symbol="BTCUSDT"),
    )
    service = CompiledSetupService(session)
    service.compile_version(template["version_id"], organization_id=org, user_id=user)
    service.approve_version(
        template["version_id"], organization_id=org, user_id=user, confirm_message="I confirm"
    )
    session.commit()
    return template


def test_library_version_binding_and_draft_not_executable(tenant_store):
    session, org, user = tenant_store
    draft = create_template(
        session, organization_id=org, user_id=user, spec=NestedContinuationSpec(symbol="BTCUSDT")
    )
    with pytest.raises(StrategyEvaluationPolicyError):
        resolve_executable_strategy_policy(
            session, organization_id=org, strategy_version_id=draft["version_id"]
        )
    from app.db.models import UserStrategyVersion

    version = session.get(UserStrategyVersion, draft["version_id"])
    assert version.pattern_spec["parameters"]["provisional"] is True
    assert version.card["brain"]["family"] == "nested_continuation"
    service = CompiledSetupService(session)
    result = service.compile_version(version.id, organization_id=org, user_id=user)
    assert result.status.value == "executable"
    service.approve_version(
        version.id, organization_id=org, user_id=user, confirm_message="I confirm"
    )
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=version.id
    )
    assert policy.evaluation_params == policy.authored_spec.parameters
    with pytest.raises(StrategyEvaluationPolicyError):
        resolve_executable_strategy_policy(
            session, organization_id=uuid4(), strategy_version_id=version.id
        )


def test_restart_replay_setup_idempotency_and_grounded_brain(tenant_store):
    session, org, user = tenant_store
    template = approved(session, org, user)
    spec = NestedContinuationSpec(symbol="BTCUSDT")
    series = bars(PRICES[:13])
    events = detection(series)
    args = {
        "organization_id": org,
        "strategy_id": template["strategy_id"],
        "version_id": template["version_id"],
        "spec": spec,
        "bars": series,
        "events": events,
        "evidence_hash": "a" * 64,
        "evaluated_at": series[-1].interval_end,
    }
    record_detections(session, **args)
    session.commit()
    setup_count = session.scalar(select(func.count()).select_from(BrainSetupRow))
    event_count = session.scalar(select(func.count()).select_from(BrainSetupEventRow))
    session.expire_all()
    record_detections(session, **args)
    session.commit()
    assert session.scalar(select(func.count()).select_from(BrainSetupRow)) == setup_count
    assert session.scalar(select(func.count()).select_from(BrainSetupEventRow)) == event_count
    summary, refs, limitations = read_brain(
        session, organization_id=org, message="Which Nested setups are forming?"
    )
    assert str(template["version_id"]) in summary
    assert "EXPIRED" in summary and "N1" in summary
    assert "STALE" in summary and "Insufficient history" in summary
    assert "order_flow=UNSUPPORTED" in summary
    assert refs and limitations
    foreign = overview(session, organization_id=uuid4())
    assert foreign["setups"] == []
    identity = scoped_setup_id(org, template["version_id"], events[-1].setup_id)
    with pytest.raises(Exception, match="Setup not found"):
        details(session, organization_id=uuid4(), setup_id=identity)


def test_brain_no_data_and_rule_change_is_proposal(tenant_store):
    session, org, _ = tenant_store
    summary, refs, _ = read_brain(session, organization_id=org, message="What are you watching?")
    assert "No stored Nested Continuation setup exists" in summary
    assert "unknown" in summary and not refs
    from app.interactive_agent.classify import classify_turn

    assert classify_turn("What are you watching?").capability.value == "strategy_brain"
    assert classify_turn("Change Nested Continuation rules").operation.value == "propose"


def nested_runtime_world(tenant_store, monkeypatch, *, forming=False, risk_block=False):
    from app.db.models import ExecutionAccount, Membership
    from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.schemas.common import MembershipRole
    from app.schemas.trade_plan import AccountMode, ExecutionMode
    from app.signal_fusion.memory import FrozenClock
    from app.workers.watcher_paper import build_watcher_paper_runtime
    from tests.support.live_market_monitor import ScriptedPerpetualSource
    from tests.support.phase5_market import trade
    from tests.test_watcher_paper_runtime import _settings

    session, org, user = tenant_store
    template = approved(session, org, user)
    session.add(Membership(organization_id=org, user_id=user, role=MembershipRole.OWNER))
    session.add(
        ExecutionAccount(
            id=uuid4(),
            organization_id=org,
            user_id=user,
            name="Brain paper",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
            enabled=True,
        )
    )
    session.commit()
    # 256 closed candles from the same contracted instrument; no synthetic live provider call.
    series = bars([*([105] * (244 if forming else 243)), *PRICES[: 12 if forming else 13]])
    now = series[-1].interval_end + timedelta(seconds=5)
    source = ScriptedPerpetualSource(replay=False, bars_15m=list(series))
    for _ in range(5):
        source.enqueue(
            [
                trade(
                    sequence=1,
                    price=str(series[-1].close),
                    quantity="1",
                    buyer_is_maker=False,
                    event_time=now - timedelta(seconds=1),
                    receive_at=now,
                )
            ]
        )
    held = []

    def evidence_factory(db, store, symbol):
        port = AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(source, replay=False, clock=lambda: now),
            session=db,
            watcher_store=store,
            symbol=symbol,
            monitor=SimpleNamespace(
                latest=lambda *a: pytest.fail(
                    "Nested detection must not require the trade/CVD monitor"
                )
            ),
        )
        held.append(port)
        return port

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=session.bind, expire_on_commit=False)
    settings = _settings(
        watcher_orchestration_enabled=True,
        exchange_mode="paper_internal",
        autonomous_internal_paper_enabled=True,
    )
    if risk_block:
        # Use the existing daily loss gate; do not invent a Nested risk engine.
        from app.db.models import DailyRiskState

        session.add(
            DailyRiskState(
                organization_id=org,
                user_id=user,
                day=now.date(),
                realized_pnl=Decimal(0),
                unrealized_pnl=Decimal(0),
                locked=True,
            )
        )
        session.commit()
    runtime = build_watcher_paper_runtime(
        settings, factory, evidence_factory=evidence_factory, clock=FrozenClock(now), enabled=True
    )
    return runtime, held, template, now


def test_watcher_confirmed_candidate_existing_paper_path_and_replay(
    postgres_tenant_store, monkeypatch
):
    tenant_store = postgres_tenant_store
    from app.db.models import ExecutionFillFact, JournalTrade

    runtime, _held, template, now = nested_runtime_world(tenant_store, monkeypatch)
    # Exercise the family assembly directly so failures retain their precise contract error.
    from app.strategy_brain.assembly import assemble_nested

    session, org, _ = tenant_store

    # The runtime evidence factory will load the same source under the persistence fence.
    port = runtime._evidence_factory(session, runtime.store, "BTCUSDT")
    executable = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    assembled = assemble_nested(
        port._assembler, executable=executable, organization_id=org, session=session
    )
    session.commit()
    from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy

    assessment = evaluate_canonical_strategy(
        executable_policy=executable,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=assembled.evaluated_at,
    )
    assert assessment.state.value == "confirmed_setup"
    report = runtime.run_cycle()
    assert report.scans, report
    scan = report.scans[0]
    assert scan.candidate_ids, scan
    assert scan.paper_loop_stage == "filled", scan
    session, org, _ = tenant_store
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
    view = overview(session, organization_id=org, now=now)
    setup = view["setups"][0]
    assert setup["state"] == "TRADE_CANDIDATE"
    assert setup["candidate_id"] == str(scan.candidate_ids[0])
    assert setup["journal"]["status"] == "open"
    assert setup["strategy_version_id"] == str(template["version_id"])
    assert setup["decision_id"] == str(scan.eligibility_id)
    assert setup["assessment_id"] == str(scan.discussion.assessment.assessment_id)
    # A restarted worker sees the durable setup/Candidate and cannot add another fill.
    from app.signal_fusion.memory import FrozenClock
    from app.workers.watcher_paper import build_watcher_paper_runtime

    restarted = build_watcher_paper_runtime(
        runtime._settings,
        runtime._session_factory,
        evidence_factory=runtime._evidence_factory,
        clock=FrozenClock(now + timedelta(minutes=5)),
        enabled=True,
    )
    duplicate = restarted.run_cycle()
    assert not duplicate.scans[0].candidate_ids
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
    # An actual close event on the existing lifecycle produces an outcome link.
    from app.schemas.common import JournalLifecycleEventType
    from app.schemas.journal_lifecycle import JournalLifecycleEventInput
    from app.services.audit_service import AuditService
    from app.services.journal_lifecycle_projector import JournalLifecycleProjector

    journal = session.get(JournalTrade, scan.journal_trade_id)
    close = JournalLifecycleEventInput(
        event_type=JournalLifecycleEventType.CLOSE,
        execution_lifecycle_id=journal.execution_lifecycle_id,
        account_id=journal.account_id,
        source_system="paper_internal",
        source_aggregate="fixture-close",
        source_event_id="close-1",
        source_event_version=1,
        payload={"exit_time": now.isoformat(), "net_pnl": "12.34"},
    )
    projector = JournalLifecycleProjector(session, AuditService(session))
    projector.project(close, organization_id=org, user_id=journal.user_id)
    session.commit()
    row = overview(session, organization_id=org, now=now)["setups"][0]
    assert row["state"] == "COMPLETED"
    assert row["journal"]["net_pnl"] == "12.34"
    outcome_events = list(
        session.scalars(
            select(BrainSetupEventRow).where(BrainSetupEventRow.kind == "paper_trade_closed")
        )
    )
    assert len(outcome_events) == 1
    assert outcome_events[0].payload["provenance"] == "actual_outcome"
    projector.project(close, organization_id=org, user_id=journal.user_id)
    session.commit()
    assert (
        session.scalar(
            select(func.count())
            .select_from(BrainSetupEventRow)
            .where(BrainSetupEventRow.kind == "paper_trade_closed")
        )
        == 1
    )


def test_watcher_forming_cannot_mint_candidate(postgres_tenant_store, monkeypatch):
    tenant_store = postgres_tenant_store
    runtime, _, _, _ = nested_runtime_world(tenant_store, monkeypatch, forming=True)
    report = runtime.run_cycle()
    assert report.scans and not report.scans[0].candidate_ids
    assert report.candidates_created == 0


@pytest.fixture
def postgres_tenant_store():
    from tests.support.postgres_persistence import phase7_plan_session_factory, postgres_available

    if not postgres_available():
        pytest.skip("Governed paper-path integration requires local PostgreSQL")
    factory = phase7_plan_session_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="Brain paper path"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        yield session, org, user


def test_existing_daily_risk_lock_blocks_nested_paper_execution(postgres_tenant_store, monkeypatch):
    from app.db.models import ExecutionFillFact, JournalTrade, TradePlanRevision

    runtime, _, _, now = nested_runtime_world(postgres_tenant_store, monkeypatch, risk_block=True)
    report = runtime.run_cycle()
    scan = report.scans[0]
    assert scan.candidate_ids
    assert scan.paper_loop_reason == "risk_block", scan
    assert scan.paper_loop_stage == "blocked"
    session, org, _ = postgres_tenant_store
    view = overview(session, organization_id=org, now=now)["setups"][0]
    assert view["state"] == "BLOCKED_BY_RISK"
    assert "blocked_daily_loss" in view["risk_reason_codes"]
    assert view["decision_id"] == str(scan.eligibility_id)
    for table in (ExecutionFillFact, JournalTrade, TradePlanRevision):
        assert session.scalar(select(func.count()).select_from(table)) == 0


def test_confirmed_sequence_failure_is_recorded_without_waiting_for_next_impulse():
    events = detection(bars([*PRICES[:13], 102]))
    assert events[-2].state is BrainSetupState.CONFIRMED
    assert events[-1].state is BrainSetupState.INVALIDATED
    assert events[-2].setup_id == events[-1].setup_id


def test_causal_event_identity_survives_irrelevant_rolling_history():
    from app.strategy_brain.detector import detection_hash

    full = bars([*([105] * 30), *PRICES[:13]])
    clipped = full[10:]
    first, second = detection(full)[-1], detection(clipped)[-1]
    assert first.setup_id == second.setup_id
    assert detection_hash(first, full) == detection_hash(second, clipped)


def test_duplicate_template_does_not_create_another_version(tenant_store):
    session, org, user = tenant_store
    spec = NestedContinuationSpec(symbol="BTCUSDT")
    first = create_template(session, organization_id=org, user_id=user, spec=spec)
    second = create_template(session, organization_id=org, user_id=user, spec=spec)
    assert first["strategy_id"] == second["strategy_id"]
    assert first["version_id"] == second["version_id"]


def test_required_evidence_staleness_missing_and_version_binding(tenant_store, monkeypatch):
    from app.market_contracts.enums import FreshnessState
    from app.signal_fusion.errors import SignalFusionContractError
    from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
    from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
    from app.strategy_brain.assembly import assemble_nested

    runtime, _, template, now = nested_runtime_world(tenant_store, monkeypatch)
    session, org, _ = tenant_store
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    port = runtime._evidence_factory(session, runtime.store, "BTCUSDT")
    assembled = assemble_nested(
        port._assembler, executable=policy, organization_id=org, session=session
    )

    def evaluate(
        command=assembled.assessment_command, bundle=assembled.bundle, at=now, executable=policy
    ):
        return evaluate_canonical_strategy(
            executable_policy=executable, command=command, evidence=bundle, evaluated_at=at
        )

    assert evaluate().state.value == "confirmed_setup"
    assert evaluate(at=now + timedelta(minutes=15)).state.value == "no_setup"
    assert evaluate(bundle=FirstSliceEvidenceBundle()).state.value == "no_setup"
    observations = tuple(
        with_content_hash(o.model_copy(update={"freshness_state": FreshnessState.STALE}))
        for o in assembled.assessment_command.public_observations
    )
    stale_command = assembled.assessment_command.model_copy(
        update={"public_observations": observations}
    )
    with suppress(SignalFusionContractError):
        assert evaluate(command=stale_command).state.value != "confirmed_setup"
    wrong_version = assembled.assessment_command.model_copy(update={"strategy_version_id": uuid4()})
    with pytest.raises(StrategyEvaluationPolicyError):
        evaluate(command=wrong_version)
    changed = policy.model_copy(
        update={
            "evaluation_params": policy.authored_spec.parameters.model_copy(
                update={"minimum_impulse": Decimal("0.03")}
            )
        }
    )
    with pytest.raises(StrategyEvaluationPolicyError):
        evaluate(executable=changed)


def test_user_observation_is_idempotent_tenant_scoped_and_cannot_change_rules(tenant_store):
    import asyncio

    from app.api.routes.strategy_brain import ObservationRequest, user_observation
    from app.core.errors import NotFoundError, ValidationAppError

    session, org, user = tenant_store
    spec = NestedContinuationSpec(symbol="BTCUSDT")
    template = approved(session, org, user, spec)
    series = bars(PRICES[:12])
    record_detections(
        session,
        organization_id=org,
        strategy_id=template["strategy_id"],
        version_id=template["version_id"],
        spec=spec,
        bars=series,
        events=detection(series),
        evidence_hash="a" * 64,
        evaluated_at=series[-1].interval_end,
    )
    session.commit()
    identity = scoped_setup_id(org, template["version_id"], detection(series)[-1].setup_id)
    tenant = SimpleNamespace(organization_id=org, user_id=user)
    body = ObservationRequest(idempotency_key=uuid4(), observation="Research idea, not an outcome")
    first = asyncio.run(user_observation(identity, body, tenant, session))
    second = asyncio.run(user_observation(identity, body, tenant, session))
    assert first["record_id"] == second["record_id"]
    assert first["provenance"] == "user_belief" and first["changes_active_rules"] is False
    with pytest.raises(ValidationAppError):
        asyncio.run(
            user_observation(
                identity, body.model_copy(update={"observation": "Changed"}), tenant, session
            )
        )
    with pytest.raises(NotFoundError):
        asyncio.run(
            user_observation(
                identity, body, SimpleNamespace(organization_id=uuid4(), user_id=user), session
            )
        )
    assert (
        resolve_executable_strategy_policy(
            session, organization_id=org, strategy_version_id=template["version_id"]
        ).authored_spec
        == spec
    )


@pytest.mark.parametrize(
    "question",
    [
        "What are you watching?",
        "Which Nested setups are forming?",
        "Which symbols currently have confirmed setups?",
        "Is this N1, N2, N3 or N4 plus?",
        "Why is this setup still forming?",
        "Why is this setup blocked?",
        "What evidence supports this setup?",
        "What data is missing?",
        "What happened to the previous setup on this market?",
        "Which strategy version generated it?",
        "What paper trade or journal entry is connected to it?",
    ],
)
def test_brain_required_read_questions_use_stored_authorities(question):
    from app.interactive_agent.classify import classify_turn

    classification = classify_turn(question)
    assert classification.capability.value == "strategy_brain"
    assert classification.operation.value == "read"


def test_zero_volume_break_cannot_confirm_required_volume():
    changed = tuple(
        with_content_hash(
            b.model_copy(update={"base_volume": Decimal(0), "quote_volume": Decimal(0)})
        )
        for b in bars(PRICES[:13])
    )
    assert not any(e.state is BrainSetupState.CONFIRMED for e in detection(changed))


def test_new_family_fields_preserve_existing_compiled_hash_and_card_payload():
    from app.schemas.setup_ast import PatternAst
    from app.schemas.strategy_library import StrategyCard
    from app.services.canonical_serialization import canonical_sha256
    from app.services.setup_ast_compiler import compile_pattern, first_slice_bearish_sweep_pattern

    document = compile_pattern(first_slice_bearish_sweep_pattern()).document
    legacy = document.pattern.model_dump(mode="json")
    assert "nested_spec" not in legacy
    assert PatternAst.model_validate(legacy).model_dump(mode="json") == legacy
    assert canonical_sha256(legacy) == document.content_hash
    card = StrategyCard(
        strategy_name="Legacy",
        entry_conditions=["entry"],
        invalidation=["invalid"],
        stop_loss=["stop"],
    )
    assert "brain" not in card.model_dump(mode="json")
