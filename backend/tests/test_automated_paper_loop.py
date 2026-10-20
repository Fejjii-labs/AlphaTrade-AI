"""Automatic paper continuation after a genuine Watcher CONFIRMED_SETUP.

Paper internal only. Real trading, BloFin, exchange mutation, and Telegram
execution stay out of this path. Risk BLOCK and the kill switch create no fill.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exchange_safety import BLOFIN_DEMO_HOST_ALLOWLIST
from app.core.persistence_firewall import install_persistence_firewall
from app.db.models import (
    AuditLog,
    DailyRiskState,
    ExecutionAccount,
    ExecutionFillFact,
    JournalTrade,
    KillSwitchState,
    Membership,
    Organization,
    TradePlanRevision,
    User,
)
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import SetupLifetimeStore
from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.errors import RegionalProviderFailureError
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.types import MarketAvailability
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.runtime.canonical import ProductionCanonicalRuntime, build_production_canonical_runtime
from app.schemas.common import (
    AuditEventType,
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
)
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.services.automated_paper_loop import AutomatedPaperLoop, paper_loop_posture_refusal
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import ActionEligibilityState, SetupAssessmentState
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.memory import FrozenClock
from app.signal_fusion.strategy_evaluation_policy import ExecutableStrategyPolicy
from app.workers.watcher_paper import (
    WatcherPaperRuntime,
    WatcherPaperScanReport,
    build_watcher_paper_runtime,
)
from app.workers.watcher_paper_targets import PaperScanTarget, list_paper_scan_targets
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_evaluator import (
    build_context_4h_bars,
    build_pattern_15m_bars,
    build_slice_trades,
)
from tests.support.postgres_persistence import (
    POSTGRES_URL,
    phase7_plan_session_factory,
    requires_postgres,
)
from tests.test_watcher_paper_runtime import ORG, USER, _seed_approved_compiled, _settings
from tests.test_watcher_product_proof_postgres import _confirming_source, _persist_resistance

_ENQUEUES = 16


@dataclass(frozen=True)
class _Confirmed:
    factory: sessionmaker[Session]
    runtime: WatcherPaperRuntime
    scan: WatcherPaperScanReport
    target: PaperScanTarget
    assembled: AssembledCanonicalEvidence
    policy: ExecutableStrategyPolicy


def _paper_settings() -> Settings:
    return _settings(
        watcher_orchestration_enabled=True,
        database_url=POSTGRES_URL,
        exchange_mode="paper_internal",
        execution_mode="paper",
        enable_real_trading=False,
        autonomous_internal_paper_enabled=True,
    )


def _seed(session: Session) -> None:
    session.add_all(
        [
            Organization(id=ORG, name="Automated paper loop"),
            User(
                id=USER,
                email="automated-paper-loop@test.example",
                hashed_password="not-a-real-hash",
            ),
        ]
    )
    session.flush()
    session.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.OWNER))
    session.add(
        ExecutionAccount(
            id=uuid4(),
            organization_id=ORG,
            user_id=USER,
            name="Internal paper account",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
            enabled=True,
        )
    )
    session.commit()
    _seed_approved_compiled(session)
    _persist_resistance(session)


def _live_source(*, copies: int = _ENQUEUES) -> ScriptedPerpetualSource:
    bars_15m = build_pattern_15m_bars()
    trades = build_slice_trades(bars_15m)
    source = ScriptedPerpetualSource(
        replay=False,
        bars_15m=bars_15m,
        bars_4h=build_context_4h_bars(),
    )
    for _ in range(copies):
        source.enqueue(list(trades))
    return source


def _monitor(
    source: object,
    *,
    replay: bool,
    clock: Callable[[], datetime] | None = None,
) -> PerpetualMarketMonitor:
    return PerpetualMarketMonitor(
        source,  # type: ignore[arg-type]
        replay=replay,
        backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=4.0),
        poll_seconds=0.01,
        clock=clock,
    )


def _runtime_for(
    factory: sessionmaker[Session],
    source: object,
    monitor: PerpetualMarketMonitor,
    *,
    replay: bool,
    held: list[AssemblingWatcherScanEvidence],
    clock: Callable[[], datetime] | None = None,
) -> WatcherPaperRuntime:
    moment = clock or (lambda: EVALUATED_AT)

    def evidence_factory(db: object, store: object, symbol: str) -> AssemblingWatcherScanEvidence:
        lifetime = SqlAlchemySetupLifetimeStore(db) if db is not None else SetupLifetimeStore()
        evidence = AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(
                source,  # type: ignore[arg-type]
                replay=replay,
                lifetime=lifetime,
                clock=moment,
            ),
            session=db,  # type: ignore[arg-type]
            watcher_store=store,  # type: ignore[arg-type]
            symbol=symbol,
            monitor=MarketMonitorWatcherPort(monitor),
        )
        held.append(evidence)
        return evidence

    return build_watcher_paper_runtime(
        _paper_settings(),
        factory,
        evidence_factory=evidence_factory,
        clock=FrozenClock(EVALUATED_AT),
        monitor=monitor,
    )


def _count(session: Session, model: type[object]) -> int:
    value = session.scalar(select(func.count()).select_from(model))
    return int(value or 0)


def _load_target(factory: sessionmaker[Session]) -> PaperScanTarget:
    with factory() as session:
        targets = list_paper_scan_targets(
            session,
            symbols=["BTCUSDT"],
            organization_id=ORG,
            limit=1,
        )
    assert len(targets) == 1
    return targets[0]


def _confirm() -> _Confirmed:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    held: list[AssemblingWatcherScanEvidence] = []
    runtime = _runtime_for(factory, source, monitor, replay=False, held=held)
    cycle = runtime.run_cycle()
    assert cycle.scans, "approved strategy did not become a scan target"
    loaded = held[0].last_assembly()
    assert loaded is not None
    assembled, policy = loaded
    return _Confirmed(
        factory=factory,
        runtime=runtime,
        scan=cycle.scans[0],
        target=_load_target(factory),
        assembled=assembled,
        policy=policy,
    )


def _loop(factory: sessionmaker[Session]) -> AutomatedPaperLoop:
    canonical: ProductionCanonicalRuntime = build_production_canonical_runtime(
        factory,
        settings=_paper_settings(),
        clock=FrozenClock(EVALUATED_AT),
    )
    return AutomatedPaperLoop(canonical, _paper_settings(), FrozenClock(EVALUATED_AT))


def _continue(
    loop: AutomatedPaperLoop,
    session: Session,
    confirmed: _Confirmed,
    *,
    target: PaperScanTarget | None = None,
    candidate: Candidate | None = None,
    assessment: object | None = None,
    window: CanonicalEvidenceWindowV1 | None = None,
    assembled: AssembledCanonicalEvidence | None = None,
    kill_switch_active: bool = False,
) -> object:
    discussion = confirmed.scan.discussion
    assert discussion is not None
    return loop.continue_confirmed_setup(
        session,
        target=confirmed.target if target is None else target,
        candidate=discussion.candidate if candidate is None else candidate,
        assessment=discussion.assessment if assessment is None else assessment,  # type: ignore[arg-type]
        window=discussion.window if window is None else window,
        assembled=confirmed.assembled if assembled is None else assembled,
        policy=confirmed.policy,
        kill_switch_active=kill_switch_active,
    )


def test_paper_posture_refuses_real_trading_and_non_internal_exchange() -> None:
    paper = _paper_settings()
    assert paper_loop_posture_refusal(paper) is None
    assert paper.real_trading_enabled is False
    demo_host = next(iter(BLOFIN_DEMO_HOST_ALLOWLIST))
    demo = _settings(
        watcher_orchestration_enabled=True,
        database_url=POSTGRES_URL,
        exchange_mode="paper_exchange_demo",
        execution_mode="paper",
        enable_real_trading=False,
        blofin_demo_enabled=True,
        blofin_api_key="demo-key",
        blofin_api_secret="demo-secret",
        blofin_api_passphrase="demo-pass",
        blofin_demo_rest_base_url=f"https://{demo_host}",
        blofin_demo_ws_url=f"wss://{demo_host}/ws/public",
    )
    assert paper_loop_posture_refusal(demo) == "exchange_mode_refused"
    read_only = _settings(
        database_url=POSTGRES_URL,
        execution_mode="read_only",
        enable_real_trading=False,
    )
    assert paper_loop_posture_refusal(read_only) == "execution_mode_refused"
    with pytest.raises(ValueError, match="ENABLE_REAL_TRADING"):
        _settings(database_url=POSTGRES_URL, enable_real_trading=True)


@requires_postgres
def test_confirmed_setup_opens_one_journal_and_replays_cleanly() -> None:
    confirmed = _confirm()
    scan = confirmed.scan
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value
    assert scan.paper_loop_stage == "filled"
    assert scan.paper_loop_reason == "open_journal"
    assert scan.paper_loop_replayed is False
    assert scan.eligibility_state == ActionEligibilityState.ELIGIBLE.value
    assert scan.eligibility_id is not None
    assert scan.trade_plan_revision_id is not None
    assert scan.execution_command_id is not None
    assert scan.paper_fill_id is not None
    assert scan.journal_trade_id is not None
    assert scan.journal_status == JournalTradeStatus.OPEN.value
    assert len(scan.candidate_ids) == 1
    effects = confirmed.runtime.side_effects
    assert effects.execution == []  # type: ignore[attr-defined]
    assert effects.journal == []  # type: ignore[attr-defined]
    assert effects.telegram == []  # type: ignore[attr-defined]

    with confirmed.factory() as session:
        trade = session.get(JournalTrade, scan.journal_trade_id)
        assert trade is not None
        assert trade.organization_id == ORG
        assert trade.user_id == USER
        assert trade.candidate_id == scan.candidate_ids[0]
        assert trade.strategy_version_id == confirmed.target.strategy_version_id
        status = trade.status.value if hasattr(trade.status, "value") else trade.status
        assert status == JournalTradeStatus.OPEN.value
        assert trade.source is JournalTradeSource.PAPER_EXECUTION
        assert trade.entry_method is JournalEntryMethod.AUTO
        assert trade.entry_price is not None
        assert trade.size is not None
        plan = session.get(TradePlanRevision, scan.trade_plan_revision_id)
        assert plan is not None
        assert plan.execution_venue == "PAPER_INTERNAL"
        assert plan.exchange_account_id is None
        assert plan.candidate_id == scan.candidate_ids[0]
        fill = session.get(ExecutionFillFact, scan.paper_fill_id)
        assert fill is not None
        assert fill.venue_source == "paper_internal"
        assert "blofin" not in fill.venue_source
        assert _count(session, JournalTrade) == 1
        assert _count(session, TradePlanRevision) == 1
        assert _count(session, ExecutionFillFact) == 1
        actions = set(
            session.scalars(select(AuditLog.action).where(AuditLog.organization_id == ORG))
        )
        assert AuditEventType.JOURNAL_TRADE_CREATED in actions

    duplicate = confirmed.runtime.run_cycle()
    assert duplicate.scans[0].replayed is True
    assert duplicate.scans[0].candidate_ids == ()
    assert duplicate.scans[0].paper_loop_stage == "not_applicable"

    loop = _loop(confirmed.factory)
    with confirmed.factory() as session:
        proof = _continue(loop, session, confirmed)
        session.commit()
        assert proof.replayed is True  # type: ignore[attr-defined]
        assert proof.stage == "filled"  # type: ignore[attr-defined]
        assert proof.journal_trade_id == scan.journal_trade_id  # type: ignore[attr-defined]
        assert proof.eligibility_id == scan.eligibility_id  # type: ignore[attr-defined]
        assert proof.trade_plan_revision_id == scan.trade_plan_revision_id  # type: ignore[attr-defined]
        assert proof.paper_fill_id == scan.paper_fill_id  # type: ignore[attr-defined]
        assert _count(session, JournalTrade) == 1
        assert _count(session, TradePlanRevision) == 1
        assert _count(session, ExecutionFillFact) == 1

        discussion = scan.discussion
        assert discussion is not None
        watch = discussion.assessment.model_copy(update={"state": SetupAssessmentState.WATCH})
        missing = discussion.assessment.model_copy(update={"state": SetupAssessmentState.NO_SETUP})
        quote = confirmed.assembled.current_price
        assert quote is not None
        stale_quote = quote.model_copy(update={"source_time": EVALUATED_AT - timedelta(seconds=30)})
        stale_assembled = confirmed.assembled.model_copy(update={"current_price": stale_quote})
        dark = confirmed.assembled.model_copy(update={"current_price": None})
        refusals = {
            "watch": _continue(loop, session, confirmed, assessment=watch),
            "no_setup": _continue(loop, session, confirmed, assessment=missing),
            "tenant": _continue(
                loop,
                session,
                confirmed,
                target=replace(confirmed.target, organization_id=uuid4()),
            ),
            "lineage": _continue(
                loop,
                session,
                confirmed,
                target=replace(confirmed.target, strategy_version_id=uuid4()),
            ),
            "kill": _continue(loop, session, confirmed, kill_switch_active=True),
            "stale": _continue(loop, session, confirmed, assembled=stale_assembled),
            "outage": _continue(loop, session, confirmed, assembled=dark),
        }
        session.commit()
        assert refusals["watch"].reason_code == "setup_not_confirmed"  # type: ignore[attr-defined]
        assert refusals["no_setup"].reason_code == "setup_not_confirmed"  # type: ignore[attr-defined]
        assert refusals["tenant"].reason_code == "wrong_tenant"  # type: ignore[attr-defined]
        assert refusals["lineage"].reason_code == "wrong_strategy_lineage"  # type: ignore[attr-defined]
        assert refusals["kill"].reason_code == "kill_switch_active"  # type: ignore[attr-defined]
        assert refusals["stale"].reason_code == "stale_evidence"  # type: ignore[attr-defined]
        assert refusals["outage"].reason_code == "evidence_unavailable"  # type: ignore[attr-defined]
        assert _count(session, JournalTrade) == 1
        assert _count(session, TradePlanRevision) == 1
        assert _count(session, ExecutionFillFact) == 1


@requires_postgres
def test_risk_block_creates_no_plan_or_fill() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        session.add(
            DailyRiskState(
                organization_id=ORG,
                user_id=USER,
                day=EVALUATED_AT.date(),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                locked=True,
            )
        )
        session.commit()
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    runtime = _runtime_for(factory, source, monitor, replay=False, held=[])
    scan = runtime.run_cycle().scans[0]
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value
    assert scan.paper_loop_stage == "blocked"
    assert scan.paper_loop_reason == "risk_block"
    assert scan.eligibility_state == ActionEligibilityState.BLOCKED.value
    assert scan.trade_plan_revision_id is None
    assert scan.paper_fill_id is None
    assert scan.journal_trade_id is None
    with factory() as session:
        assert _count(session, JournalTrade) == 0
        assert _count(session, TradePlanRevision) == 0
        assert _count(session, ExecutionFillFact) == 0
        actions = set(
            session.scalars(select(AuditLog.action).where(AuditLog.organization_id == ORG))
        )
        assert AuditEventType.RISK_BLOCK in actions


@requires_postgres
def test_kill_switch_blocks_execution_and_keeps_monitoring() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        session.add(KillSwitchState(organization_id=ORG, active=True, version=1, reason="halt"))
        session.commit()
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    runtime = _runtime_for(factory, source, monitor, replay=False, held=[])
    cycle = runtime.run_cycle()
    scan = cycle.scans[0]
    assert scan.status == "blocked"
    assert scan.reason_code == "kill_switch_active"
    assert scan.candidate_ids == ()
    assert scan.paper_loop_stage == "not_applicable"
    status = runtime.snapshot()
    assert status.cycles_completed == 1
    assert status.scans_blocked == 1
    assert status.kill_switch_active is True
    assert status.candidates_created == 0
    with factory() as session:
        assert _count(session, JournalTrade) == 0
        assert _count(session, TradePlanRevision) == 0


@requires_postgres
def test_stale_monitor_and_provider_outage_create_nothing() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
    moment = {"at": EVALUATED_AT}
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: moment["at"])
    fresh = monitor.tick("BTCUSDT")
    assert fresh.availability is MarketAvailability.FRESH
    moment["at"] = EVALUATED_AT + timedelta(seconds=30)
    runtime = _runtime_for(
        factory,
        source,
        monitor,
        replay=False,
        held=[],
        clock=lambda: moment["at"],
    )
    stale = runtime.run_cycle().scans[0]
    assert stale.reason_code == "stale_evidence"
    assert stale.candidate_ids == ()
    assert stale.paper_loop_stage == "not_applicable"

    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
    outage_source = _live_source(copies=0)
    outage_source.enqueue(RegionalProviderFailureError("perpetual provider unavailable"))
    outage_monitor = _monitor(outage_source, replay=False, clock=lambda: EVALUATED_AT)
    outage_runtime = _runtime_for(factory, outage_source, outage_monitor, replay=False, held=[])
    outage = outage_runtime.run_cycle().scans[0]
    assert outage.reason_code == "provider_outage"
    assert outage.candidate_ids == ()
    with factory() as session:
        assert _count(session, JournalTrade) == 0
        assert _count(session, TradePlanRevision) == 0


@requires_postgres
def test_replay_confirmed_setup_does_not_open_a_journal() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
    source: ReplayPerpetualSource = _confirming_source()
    monitor = _monitor(source, replay=True)
    runtime = _runtime_for(factory, source, monitor, replay=True, held=[])
    scan = runtime.run_cycle().scans[0]
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value
    assert len(scan.candidate_ids) == 1
    assert scan.paper_loop_stage == "skipped"
    assert scan.paper_loop_reason == "evidence_not_live"
    assert scan.trade_plan_revision_id is None
    assert scan.journal_trade_id is None
    assert runtime.side_effects.execution == []  # type: ignore[attr-defined]
    with factory() as session:
        assert _count(session, JournalTrade) == 0
        assert _count(session, TradePlanRevision) == 0
        assert _count(session, ExecutionFillFact) == 0
