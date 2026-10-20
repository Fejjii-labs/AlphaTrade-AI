"""Focused SFP canonical binding, persistence and governed Candidate integration."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes.strategy_brain import propose_sfp, read_overview, read_setup
from app.core.errors import NotFoundError
from app.db.base import Base
from app.db.models import Organization, User, UserStrategyVersion
from app.db.public_market_observations import PublicMarketObservationRow
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.market_contracts.enums import FreshnessState
from app.market_contracts.errors import DuplicateDataError, MarketContractError
from app.market_contracts.hashing import with_content_hash
from app.persistence.public_market_observations import remember_observation
from app.schemas.nested_continuation import EvidenceAvailability
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.compiled_setup_service import CompiledSetupService
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceAdapterKind, EvidenceRole, SetupAssessmentState
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
from app.signal_fusion.types import HalfOpenInterval, TriggerIdentity
from app.strategy_brain.agent import read_brain
from app.strategy_brain.records import scoped_setup_id
from app.strategy_brain.service import create_template, details, overview
from app.strategy_brain.sfp.detector import detect_sfp
from app.strategy_brain.sfp_runtime.assembly import assemble_sfp
from app.strategy_brain.sfp_runtime.assessment import select_detection
from app.strategy_brain.sfp_runtime.records import record_sfp_availability, record_sfp_scan
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.test_sfp_detector import BEAR, BULL, START, evidence, spec


@pytest.fixture
def store(tmp_path):
    # File-backed storage with independent engines proves an actual process-cache restart.
    url = f"sqlite+pysqlite:///{tmp_path / 'sfp.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    org, user = uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all(
            [
                Organization(id=org, name="SFP"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        yield session, org, user, url
    engine.dispose()


def approve(session, org, user, configuration=None):
    template = create_template(
        session, organization_id=org, user_id=user, spec=configuration or spec()
    )
    compiler = CompiledSetupService(session)
    compiler.compile_version(template["version_id"], organization_id=org, user_id=user)
    compiler.approve_version(
        template["version_id"], organization_id=org, user_id=user, confirm_message="I confirm"
    )
    session.commit()
    return resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )


def command_for(policy, bars, observations):
    return AssessmentCommand(
        organization_id=policy.organization_id,
        strategy_version_id=policy.strategy_version_id,
        executable_setup=policy.fusion_policy.executable_setup,
        fusion_policy_version=policy.fusion_policy.policy_version,
        finality_policy_version=policy.fusion_policy.finality_policy_version,
        freshness_policy_version=policy.fusion_policy.freshness_policy_version,
        direction=policy.authored_spec.direction,
        evidence_identity=observations[-1].identity,
        interval=HalfOpenInterval(start=bars[0].interval_start, end=bars[-1].interval_end),
        trigger=TriggerIdentity(
            natural_event_id=bars[-1].source_event_id, revision=bars[-1].revision
        ),
        mandatory_evidence_roles=policy.fusion_policy.required_roles,
        public_observations=(observations[-1], *observations),
        selected_roles=(
            EvidenceRole.TRIGGER_OHLCV,
            *(EvidenceRole.STRUCTURE for _ in observations),
        ),
        source_set=(),
        adapter_kind=EvidenceAdapterKind.WATCHER,
        role_timeframes=policy.fusion_policy.role_timeframes,
    )


def evaluate(policy, bars, observations, *, now=None, command=None):
    return evaluate_canonical_strategy(
        executable_policy=policy,
        command=command or command_for(policy, bars, observations),
        evidence=FirstSliceEvidenceBundle(bars_15m=bars),
        evaluated_at=now or bars[-1].interval_end,
    )


def persist(session, policy, bars, observations, *, now=None):
    now = now or observations[-1].observed_at
    scan = detect_sfp(bars, observations, policy.authored_spec, evaluated_at=now)
    window = evidence_window_from_assessment_command(command_for(policy, bars, observations))
    record_sfp_scan(
        session, executable=policy, scan=scan, evidence_hash=window.content_hash, evaluated_at=now
    )
    session.commit()
    selected = select_detection(scan, bars)
    return scan, scoped_setup_id(
        policy.organization_id, policy.strategy_version_id, selected.setup_id
    )


@pytest.mark.parametrize("bearish", [False, True])
def test_registration_immutable_approval_and_tenant_binding(store, bearish):
    session, org, user, _ = store
    configuration = spec(bearish=bearish, expiry_bars=2000)
    draft = asyncio.run(
        propose_sfp(configuration, SimpleNamespace(organization_id=org, user_id=user), session)
    )
    duplicate = create_template(session, organization_id=org, user_id=user, spec=configuration)
    assert draft["version_id"] == duplicate["version_id"]
    assert draft["execution_permission"] == "none"
    with pytest.raises(StrategyEvaluationPolicyError, match="Draft"):
        resolve_executable_strategy_policy(
            session, organization_id=org, strategy_version_id=draft["version_id"]
        )
    policy = approve(session, org, user, configuration)
    assert policy.adapter_id == "swing_failure_pattern/v1"
    assert policy.evaluation_params == configuration.parameters
    version = session.get(UserStrategyVersion, policy.strategy_version_id)
    assert version.card["brain"]["family"] == "sfp"
    assert version.card["brain"]["alert_rules"] == []
    assert version.pattern_spec == configuration.model_dump(mode="json")
    with pytest.raises(StrategyEvaluationPolicyError, match="organization"):
        resolve_executable_strategy_policy(
            session, organization_id=uuid4(), strategy_version_id=version.id
        )
    altered = approve(session, org, user, spec(bearish=bearish, expiry_bars=1999))
    assert altered.strategy_version_id != policy.strategy_version_id
    assert altered.compiled_content_hash != policy.compiled_content_hash


@pytest.mark.parametrize("bearish", [False, True])
def test_canonical_assessment_forming_confirmed_and_old_confirmation(store, bearish):
    session, org, user, _ = store
    policy = approve(session, org, user, spec(bearish=bearish))
    path = BEAR if bearish else BULL
    bars, obs = evidence(path)
    forming = evaluate(policy, bars[:5], obs[:5])
    assert forming.state is SetupAssessmentState.PARTIAL_MATCH
    confirmed = evaluate(policy, bars, obs)
    assert confirmed.state is SetupAssessmentState.CONFIRMED_SETUP
    assert confirmed.strategy_version_id == policy.strategy_version_id
    assert set(confirmed.observation_ids) == {o.observation_id for o in obs}
    assert (
        confirmed.evidence_window_hash
        == evidence_window_from_assessment_command(command_for(policy, bars, obs)).content_hash
    )
    later = (96, 98, 95, 97) if bearish else (104, 105, 103, 104)
    extended, extended_obs = evidence([*path, later])
    assert (
        evaluate(policy, extended, extended_obs).state is not SetupAssessmentState.CONFIRMED_SETUP
    )
    with pytest.raises(StrategyEvaluationPolicyError, match="immutable"):
        evaluate(
            policy.model_copy(update={"evaluation_params": spec(expiry_bars=7).parameters}),
            bars,
            obs,
        )
    foreign = command_for(policy, bars, obs).model_copy(update={"organization_id": uuid4()})
    with pytest.raises(StrategyEvaluationPolicyError):
        evaluate(policy, bars, obs, command=foreign)


@pytest.mark.parametrize("bearish", [False, True])
@pytest.mark.parametrize("role", [EvidenceRole.CVD_5M, EvidenceRole.ORDER_FLOW_5M])
def test_sfp_dispatch_requires_bound_print_evidence(store, bearish, role):
    session, org, user, _ = store
    policy = approve(session, org, user, spec(bearish=bearish))
    bars, observations = evidence(BEAR if bearish else BULL)
    assert evaluate(policy, bars, observations).state is SetupAssessmentState.CONFIRMED_SETUP
    fusion = policy.fusion_policy.model_copy(
        update={"required_roles": (*policy.fusion_policy.required_roles, role)}
    )
    original = command_for(policy, bars, observations)
    # A candle selected as a flow role cannot substitute for bound trade prints.
    command = original.model_copy(
        update={
            "mandatory_evidence_roles": fusion.required_roles,
            "public_observations": (*original.public_observations, observations[-1]),
            "selected_roles": (*original.selected_roles, role),
        }
    )
    result = evaluate_setup(
        policy=fusion,
        command=command,
        evidence=FirstSliceEvidenceBundle(bars_15m=bars),
        evaluated_at=bars[-1].interval_end,
        sfp_spec=policy.authored_spec,
    )
    assert result.state is SetupAssessmentState.NO_SETUP
    assert not next(
        rule for rule in result.rule_results if rule.rule_id == "required_order_flow_binding"
    ).passed


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_stale_required_observations_never_confirm(store, missing):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, obs = evidence()
    if missing:
        command = command_for(policy, bars, obs).model_copy(
            update={
                "public_observations": (obs[-1],),
                "selected_roles": (EvidenceRole.TRIGGER_OHLCV,),
            }
        )
        with pytest.raises(Exception, match="Mandatory evidence roles missing"):
            evaluate(policy, bars, obs, command=command)
    else:
        stale = tuple(
            with_content_hash(o.model_copy(update={"freshness_state": FreshnessState.STALE}))
            for o in obs
        )
        result = evaluate(policy, bars, stale)
        assert result.state is SetupAssessmentState.NO_SETUP
        assert not next(
            r for r in result.rule_results if r.rule_id == "required_sfp_evidence"
        ).passed


def test_provisional_and_future_closed_candles_cannot_confirm(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, obs = evidence(forming=True)
    with pytest.raises(ValidationError, match="cannot select a forming observation"):
        evaluate(policy, bars, obs, now=obs[-1].observed_at)
    bars, obs = evidence()
    result = evaluate(policy, bars, obs, now=bars[-1].interval_start)
    assert result.state is not SetupAssessmentState.CONFIRMED_SETUP
    tampered = command_for(policy, bars, obs).model_copy(
        update={"trigger": TriggerIdentity(natural_event_id="wrong", revision=1)}
    )
    assert evaluate(policy, bars, obs, command=tampered).state is SetupAssessmentState.NO_SETUP


def test_persistence_restart_replay_and_grounded_canonical_reads(store):
    session, org, user, url = store
    policy = approve(session, org, user)
    bars, obs = evidence()
    _, setup_id = persist(session, policy, bars[:5], obs[:5])
    assert session.get(BrainSetupRow, setup_id).state == "FORMING"
    _, setup_id = persist(session, policy, bars, obs)
    count = session.scalar(select(func.count()).select_from(BrainSetupEventRow))
    engine = create_engine(url)
    with Session(engine) as restarted:
        reloaded = resolve_executable_strategy_policy(
            restarted, organization_id=org, strategy_version_id=policy.strategy_version_id
        )
        persist(restarted, reloaded, bars, obs)
        # Older replay cannot regress the latest confirmed projection.
        persist(restarted, reloaded, bars[:5], obs[:5])
        assert restarted.scalar(select(func.count()).select_from(BrainSetupEventRow)) == count
        row = restarted.get(BrainSetupRow, setup_id)
        assert row.state == "CONFIRMED" and row.candidate_id is None
        tenant = SimpleNamespace(organization_id=org)
        view = asyncio.run(read_setup(setup_id, tenant, restarted))
        assert view["family"] == "sfp"
        assert UUID(view["strategy_version_id"]) == policy.strategy_version_id
        assert view["strategy_version_content_hash"] == policy.strategy_version_content_hash
        assert view["sweep"]["reference_level"]["level_id"]
        assert view["confirmation_observation_id"] == str(obs[-1].observation_id)
        assert view["canonical_observation"]["observed_at"] == obs[
            -1
        ].observed_at.isoformat().replace("+00:00", "Z")
        assert {e["payload"]["state"] for e in view["history"]} >= {"FORMING", "CONFIRMED"}
        assert all(
            e["record_id"] and e["payload"]["evidence_at"] and e["payload"]["strategy_version_id"]
            for e in view["history"]
        )
        assert view["quality"]["higher_timeframe_alignment"]["availability"] == "MISSING"
        assert view["quality"]["available_target_space"]["value"] is not None
        for name in ("cvd", "order_flow", "open_interest"):
            assert view["quality"][name]["availability"] == "UNSUPPORTED"
            assert view["quality"][name]["value"] is None
        summary = asyncio.run(read_overview(tenant, restarted))
        assert summary["strategies"][0]["spec"]["kind"] == policy.authored_spec.kind
        with pytest.raises(NotFoundError):
            asyncio.run(read_setup(setup_id, SimpleNamespace(organization_id=uuid4()), restarted))
        assert overview(restarted, organization_id=uuid4())["setups"] == []
        text, refs, _ = read_brain(restarted, organization_id=org, message=f"SFP {setup_id}")
        assert str(setup_id) in text and "confirmed_sfp" in text and refs
    engine.dispose()


@pytest.mark.parametrize(
    "tail,condition",
    [
        ((101, 102, 99, "99.5"), "failed_reclaim"),
        ((101, 102, 97, 99), "invalidated_sfp"),
    ],
)
def test_terminal_invalidation_persists_and_cannot_revive(store, tail, condition):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, obs = evidence([*BULL[:5], tail])
    _, setup_id = persist(session, policy, bars, obs)
    row = session.get(BrainSetupRow, setup_id)
    assert row.state == "INVALIDATED" and row.payload["condition"] == condition
    bars, obs = evidence()
    persist(session, policy, bars, obs)
    assert row.state == "INVALIDATED" and row.candidate_id is None


def test_missing_evidence_and_clock_expiry_survive_replay(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, obs = evidence(BULL[:5])
    _, setup_id = persist(session, policy, bars, obs)
    row = session.get(BrainSetupRow, setup_id)
    original_evidence_time = row.observed_at
    missing_at = obs[-1].observed_at + timedelta(seconds=1)
    args = {
        "executable": policy,
        "availability": EvidenceAvailability.MISSING,
        "reason_codes": ("required_sfp_evidence_unavailable",),
        "evidence_hash": "unavailable",
    }
    record_sfp_availability(session, **args, evaluated_at=missing_at)
    session.commit()
    assert (
        overview(session, organization_id=org, now=missing_at)["setups"][0]["data_quality"]
        == "MISSING"
    )
    assert row.observed_at == original_evidence_time
    expire_at = obs[-1].observed_at + timedelta(
        minutes=15 * policy.authored_spec.parameters.expiry_bars
    )
    record_sfp_availability(session, **args, evaluated_at=expire_at)
    session.commit()
    count = session.scalar(select(func.count()).select_from(BrainSetupEventRow))
    record_sfp_availability(session, **args, evaluated_at=expire_at)
    persist(session, policy, bars, obs)
    assert row.state == "EXPIRED"
    assert session.scalar(select(func.count()).select_from(BrainSetupEventRow)) == count
    assert any(
        e["kind"] == "setup_expired"
        for e in details(session, organization_id=org, setup_id=setup_id)["history"]
    )


def test_public_receipt_is_durable_and_conflicting_revision_is_not_rewritten(store):
    session, _, _, url = store
    _, observations = evidence()
    original = remember_observation(session, observations[-1])
    session.commit()
    engine = create_engine(url)
    with Session(engine) as restarted:
        later = with_content_hash(
            original.model_copy(
                update={
                    "observed_at": original.observed_at + timedelta(hours=1),
                    "receive_time": original.receive_time + timedelta(hours=1),
                }
            )
        )
        assert remember_observation(restarted, later) == original
        assert restarted.scalar(select(func.count()).select_from(PublicMarketObservationRow)) == 1
        conflict = with_content_hash(original.model_copy(update={"payload_content_hash": "f" * 64}))
        with pytest.raises(DuplicateDataError):
            remember_observation(restarted, conflict)
    engine.dispose()


def padded_evidence(path=BULL, *, start=START):
    return evidence(
        [*([(102, 104, 101, 103)] * 256), *path], start=start - timedelta(minutes=256 * 15)
    )


def assemble_prefix(session, policy, bars, *, now=None, replay=True):
    source = ScriptedPerpetualSource(replay=replay, bars_15m=list(bars))
    assembler = FirstSliceEvidenceAssembler(
        source, replay=replay, clock=lambda: now or bars[-1].interval_end
    )
    return assemble_sfp(
        assembler, executable=policy, organization_id=policy.organization_id, session=session
    )


def test_late_download_cannot_invent_historical_sfp(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, _ = padded_evidence()
    downloaded = assemble_prefix(session, policy, bars)
    assert (
        evaluate_canonical_strategy(
            executable_policy=policy,
            command=downloaded.assessment_command,
            evidence=downloaded.bundle,
            evaluated_at=downloaded.evaluated_at,
        ).state
        is not SetupAssessmentState.CONFIRMED_SETUP
    )
    assert session.scalar(select(func.count()).select_from(BrainSetupRow)) == 0


def test_incremental_scan_survives_restart(store):
    session, org, user, url = store
    policy = approve(session, org, user)
    bars, _ = padded_evidence()
    # A real warm-up scan sees the structural level before the future sweep starts.
    assemble_prefix(session, policy, bars[:260])
    session.commit()
    assemble_prefix(session, policy, bars[:261])
    session.commit()
    assert any(r.state == "FORMING" for r in session.scalars(select(BrainSetupRow)))
    engine = create_engine(url)
    with Session(engine) as restarted:
        confirmed = assemble_prefix(restarted, policy, bars)
        restarted.commit()
        result = evaluate_canonical_strategy(
            executable_policy=policy,
            command=confirmed.assessment_command,
            evidence=confirmed.bundle,
            evaluated_at=confirmed.evaluated_at,
        )
        assert result.state is SetupAssessmentState.CONFIRMED_SETUP
        replay = assemble_prefix(
            restarted, policy, bars, now=bars[-1].interval_end + timedelta(seconds=5)
        )
        assert replay.evidence_window_hash == confirmed.evidence_window_hash
    engine.dispose()


@pytest.fixture
def postgres_store():
    from tests.support.postgres_persistence import phase7_plan_session_factory, postgres_available

    if not postgres_available():
        pytest.skip("SFP governed runtime persistence requires local PostgreSQL")
    factory = phase7_plan_session_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="SFP paper scope"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        yield session, org, user, factory


def runtime_world(postgres_store, *, bearish=False, risk_block=False, forming=False, start=START):
    from app.db.models import DailyRiskState, ExecutionAccount, Membership
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.schemas.common import MembershipRole
    from app.schemas.trade_plan import AccountMode, ExecutionMode
    from app.signal_fusion.memory import FrozenClock
    from app.workers.watcher_paper import build_watcher_paper_runtime
    from tests.support.phase5_market import trade
    from tests.test_watcher_paper_runtime import _settings

    session, org, user, factory = postgres_store
    policy = approve(session, org, user, spec(bearish=bearish))
    session.add(Membership(organization_id=org, user_id=user, role=MembershipRole.OWNER))
    session.add(
        ExecutionAccount(
            id=uuid4(),
            organization_id=org,
            user_id=user,
            name="SFP paper",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
            enabled=True,
        )
    )
    path = BEAR if bearish else BULL
    bars, _ = padded_evidence(path, start=start)
    # Actual observations are acquired in chronological scans; no historical backdating.
    assemble_prefix(session, policy, bars[:260], replay=False)
    session.commit()
    if not forming:
        assemble_prefix(session, policy, bars[:261], replay=False)
        session.commit()
    selected = bars[:261] if forming else bars
    now = selected[-1].interval_end + timedelta(seconds=5)
    if risk_block:
        session.add(
            DailyRiskState(
                organization_id=org,
                user_id=user,
                day=now.date(),
                realized_pnl=0,
                unrealized_pnl=0,
                locked=True,
            )
        )
    session.commit()
    source = ScriptedPerpetualSource(replay=False, bars_15m=list(selected))
    for _ in range(8):
        source.enqueue(
            [
                trade(
                    sequence=1,
                    price=str(selected[-1].close),
                    quantity="1",
                    buyer_is_maker=False,
                    event_time=now - timedelta(seconds=1),
                    receive_at=now,
                )
            ]
        )

    def evidence_factory(db, watcher_store, symbol):
        return AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(source, replay=False, clock=lambda: now),
            session=db,
            watcher_store=watcher_store,
            symbol=symbol,
            monitor=SimpleNamespace(
                latest=lambda *args: pytest.fail("SFP must not require fabricated trade flow")
            ),
        )

    settings = _settings(
        watcher_orchestration_enabled=True,
        exchange_mode="paper_internal",
        autonomous_internal_paper_enabled=True,
    )
    runtime = build_watcher_paper_runtime(
        settings, factory, evidence_factory=evidence_factory, clock=FrozenClock(now), enabled=True
    )
    return (
        runtime,
        lambda: build_watcher_paper_runtime(
            settings,
            factory,
            evidence_factory=evidence_factory,
            clock=FrozenClock(now),
            enabled=True,
        ),
        policy,
        now,
    )


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_candidate_restart_replay_uses_existing_authority_and_never_executes(
    postgres_store, bearish, monkeypatch
):
    from app.services.execution_service import ExecutionService
    from app.signal_fusion.lifecycle import CandidateLifecycleService

    calls = []
    create = CandidateLifecycleService.create_from_confirmed_setup

    def canonical_create(self, command):
        calls.append(command)
        return create(self, command)

    monkeypatch.setattr(CandidateLifecycleService, "create_from_confirmed_setup", canonical_create)
    monkeypatch.setattr(
        ExecutionService,
        "execute_paper_plan",
        lambda *args, **kwargs: pytest.fail("SFP must not execute"),
    )
    from app.db.canonical_candidates import CanonicalCandidateRow
    from app.db.canonical_eligibility import ActionEligibilityEvaluationRow
    from app.db.models import ExecutionFillFact, JournalTrade, TradePlanRevision

    runtime, restart, policy, now = runtime_world(postgres_store, bearish=bearish)
    report = runtime.run_cycle()
    assert report.scans and report.scans[0].candidate_ids, report
    assert report.scans[0].nested_alert is None
    scan = report.scans[0]
    assert len(calls) == 1 and calls[0].assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    assert scan.paper_loop_reason == "sfp_execution_plan_not_authorized", scan
    assert scan.eligibility_id and scan.eligibility_state == "eligible", scan
    session, org, _, factory = postgres_store
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(CanonicalCandidateRow)) == 1
    for table in (TradePlanRevision, ExecutionFillFact, JournalTrade):
        assert session.scalar(select(func.count()).select_from(table)) == 0
    candidate_id = scan.candidate_ids[0]
    candidate = session.get(CanonicalCandidateRow, candidate_id)
    assert candidate.idempotency_key.startswith("watcher:sfp:")
    assert candidate.strategy_version_id == policy.strategy_version_id
    assert session.get(ActionEligibilityEvaluationRow, scan.eligibility_id).organization_id == org
    linked = [
        s for s in overview(session, organization_id=org, now=now)["setups"] if s["candidate_id"]
    ]
    assert len(linked) == 1 and linked[0]["candidate_id"] == str(candidate_id)
    assert linked[0]["risk_state"] == "eligible"
    assert linked[0]["condition"] == "confirmed_sfp"
    # Fresh runtime and fresh SQL sessions use durable receipts and episode identity.
    replay = restart().run_cycle()
    assert replay.scans and (replay.scans[0].candidate_ids in ((), (candidate_id,)))
    with factory() as restarted:
        assert restarted.scalar(select(func.count()).select_from(CanonicalCandidateRow)) == 1
        assert restarted.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0
        assert overview(restarted, organization_id=uuid4())["setups"] == []


@pytest.mark.parametrize("bearish", [False, True])
@pytest.mark.parametrize(
    "start",
    [
        datetime(2024, 2, 29, 10, tzinfo=UTC),
        START,
        datetime(2026, 10, 2, 23, tzinfo=UTC),
        datetime(2030, 1, 1, 10, tzinfo=UTC),
    ],
    ids=["leap_day", "release_day", "utc_midnight", "future_year"],
)
def test_existing_daily_risk_lock_is_authoritative_for_sfp(postgres_store, bearish, start):
    from app.db.canonical_candidates import CanonicalCandidateRow
    from app.db.models import DailyRiskState, ExecutionFillFact, TradePlanRevision

    runtime, _, _, now = runtime_world(
        postgres_store, bearish=bearish, risk_block=True, start=start
    )
    runtime._settings.paper_worker_memory_diagnostics_enabled = True
    report = runtime.run_cycle()
    assert report.scans and report.scans[0].candidate_ids, report
    scan = report.scans[0]
    assert scan.paper_loop_reason == "risk_block" and scan.paper_loop_stage == "blocked", scan
    session, org, _, _ = postgres_store
    session.expire_all()
    linked = [
        s for s in overview(session, organization_id=org, now=now)["setups"] if s["candidate_id"]
    ]
    assert linked[0]["state"] == "BLOCKED_BY_RISK"
    assert "blocked_daily_loss" in linked[0]["risk_reason_codes"]
    assert session.scalar(select(func.count()).select_from(CanonicalCandidateRow)) == 1
    risk_rows = session.scalars(select(DailyRiskState)).all()
    assert len(risk_rows) == 1 and risk_rows[0].day == now.date() and risk_rows[0].locked
    assert session.scalar(select(func.count()).select_from(TradePlanRevision)) == 0
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0


def test_forming_sfp_runtime_cannot_mint_candidate(postgres_store):
    from app.db.canonical_candidates import CanonicalCandidateRow
    from app.db.models import ExecutionFillFact

    runtime, _, _, now = runtime_world(postgres_store, forming=True)
    report = runtime.run_cycle()
    assert report.scans and not report.scans[0].candidate_ids, report
    session, org, _, _ = postgres_store
    assert session.scalar(select(func.count()).select_from(CanonicalCandidateRow)) == 0
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0
    assert any(
        s["state"] == "FORMING" for s in overview(session, organization_id=org, now=now)["setups"]
    )


def test_tenants_persist_distinct_episodes_for_the_same_public_market_event(store):
    session, org, user, _ = store
    first = approve(session, org, user)
    other_org, other_user = uuid4(), uuid4()
    session.add_all(
        [
            Organization(id=other_org, name="Other SFP tenant"),
            User(id=other_user, email=f"{other_user}@example.com", hashed_password="unused"),
        ]
    )
    session.commit()
    second = approve(session, other_org, other_user)
    bars, observations = evidence()
    _, first_id = persist(session, first, bars, observations)
    _, second_id = persist(session, second, bars, observations)
    assert first_id != second_id
    assert session.get(BrainSetupRow, first_id).organization_id == org
    assert session.get(BrainSetupRow, second_id).organization_id == other_org
    for tenant, permitted, foreign in (
        (org, first_id, second_id),
        (other_org, second_id, first_id),
    ):
        assert {s["setup_id"] for s in overview(session, organization_id=tenant)["setups"]} == {
            str(permitted)
        }
        with pytest.raises(NotFoundError):
            details(session, organization_id=tenant, setup_id=foreign)


def test_provider_failure_records_missing_evidence_and_durable_expiry(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, observations = evidence(BULL[:5])
    _, setup_id = persist(session, policy, bars, observations)
    source = ScriptedPerpetualSource(replay=True)

    def unavailable(**kwargs):
        raise MarketContractError("Synthetic provider outage")

    source.fetch_closed_ohlcv = unavailable
    now = observations[-1].observed_at + timedelta(seconds=5)
    assembler = FirstSliceEvidenceAssembler(source, replay=True, clock=lambda: now)
    with pytest.raises(MarketContractError, match="Synthetic provider outage"):
        assemble_sfp(assembler, executable=policy, organization_id=org, session=session)
    session.commit()
    assert (
        overview(session, organization_id=org, now=now)["setups"][0]["required_evidence"]
        == "MISSING"
    )
    now = observations[-1].observed_at + timedelta(
        minutes=15 * policy.authored_spec.parameters.expiry_bars
    )
    with pytest.raises(MarketContractError):
        assemble_sfp(assembler, executable=policy, organization_id=org, session=session)
    session.commit()
    assert session.get(BrainSetupRow, setup_id).state == "EXPIRED"


def test_canonical_price_history_survives_rolling_provider_windows(store):
    from app.persistence.public_market_observations import observed_ohlcv_history

    session, org, user, _ = store
    policy = approve(session, org, user, spec(expiry_bars=300))
    bars, _ = padded_evidence()
    assemble_prefix(session, policy, bars[:260])
    session.commit()
    confirmed = assemble_prefix(session, policy, bars)
    session.commit()
    observations = confirmed.assessment_command.public_observations[1:]
    original = select_detection(
        detect_sfp(
            confirmed.bundle.bars_15m,
            observations,
            policy.authored_spec,
            evaluated_at=confirmed.evaluated_at,
        ),
        bars,
    )
    # A later fetch can omit the swept anchor. Canonical receipts retain original prices and clocks.
    extended, _ = padded_evidence([*BULL, *([(104, 105, 103, 104)] * 256)])
    assembler = assemble_prefix(session, policy, extended[-256:])
    assert len(assembler.bundle.bars_15m) > 256
    assert bars[257] in assembler.bundle.bars_15m
    retained = observed_ohlcv_history(
        session,
        identity=confirmed.identity,
        since=START,
        evaluated_at=confirmed.evaluated_at,
        limit=20,
    )
    assert retained and all(
        max(o.observed_at, o.receive_time) <= confirmed.evaluated_at for _, o in retained
    )
    events = detect_sfp(
        assembler.bundle.bars_15m,
        assembler.assessment_command.public_observations[1:],
        policy.authored_spec,
        evaluated_at=assembler.evaluated_at,
    ).events
    assert any(e.setup_id == original.setup_id and e.state.value == "CONFIRMED" for e in events)


def test_receipt_migration_upgrade_and_downgrade():
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    migration = importlib.import_module(
        "app.db.migrations.versions.a2sfp002_public_market_receipts"
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
        assert "public_market_observations" in inspect(conn).get_table_names()
        columns = {c["name"] for c in inspect(conn).get_columns("public_market_observations")}
        assert columns == {"identity_hash", "observation_id", "payload", "bar_start", "ohlcv"}
        migration.downgrade()
        assert "public_market_observations" not in inspect(conn).get_table_names()
    engine.dispose()
