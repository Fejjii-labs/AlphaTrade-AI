"""PostgreSQL rehearsal of the controlled paper package. No network and no orders.

Live evidence is a scripted USD-M source with live identity. Replay is not the
success path. The Watcher assembles a genuine SetupAssessment, and the paper
loop continues that Candidate through eligibility, one canonical TradePlan, one
internal paper fill, and one open Journal trade. Telegram is the projection
hook and cannot mint, trade, or override risk.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid5

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.controlled_activation.projection import build_controlled_scan_hook
from app.controlled_activation.rollback import plan_package_rollback
from app.core.config import Settings
from app.core.persistence_firewall import install_persistence_firewall
from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.canonical_trade_plans import CanonicalTradePlanLineageRow, CanonicalTradePlanRootRow
from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    ExecutionAccount,
    ExecutionFillFact,
    JournalLifecycleEvent,
    JournalTrade,
    Membership,
    Organization,
    User,
)
from app.db.telegram_security import TelegramOutboxRow
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import SetupLifetimeStore
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.cvd import select_trades_in_window
from app.market_contracts.enums import SourceFamily
from app.market_contracts.errors import RegionalProviderFailureError
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.types import MarketAvailability, MarketMode
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.paper_evaluation.contracts import PaperEvaluationStage
from app.paper_evaluation.errors import RefinementActivationForbiddenError
from app.paper_evaluation.journal_facts import SqlAlchemyJournalExcursionPort
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.paper_evaluation.refinement import refuse_activation
from app.paper_interaction.bridge import (
    EvaluationLearningContext,
    evaluation_fact_lines,
)
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.persistence.composition import build_postgres_telegram_security_store
from app.persistence.eligibility_postgres import latest_evaluations_for_organization
from app.persistence.paper_evaluation_postgres import PostgresPaperEvaluationStore
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.runtime.canonical import build_production_canonical_runtime
from app.schemas.common import JournalTradeStatus, MembershipRole, RuleComplianceStatus
from app.schemas.journal_trades import JournalTradeRuleCheckCreate
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.services.audit_service import AuditService
from app.services.journal_trade_service import JournalTradeService
from app.signal_fusion.enums import ActionEligibilityState, CandidateState, SetupAssessmentState
from app.signal_fusion.memory import FrozenClock as PlanClock
from app.signal_fusion.memory import UtcClock
from app.telegram_activation.errors import TelegramActivationError
from app.telegram_activation.intake import ParsedTelegramUpdate, RecordedUpdateSource
from app.telegram_activation.runtime import TelegramPaperRuntime
from app.telegram_paper_agent.identity import PAPER_NOTIFY_IDENTITY_NAMESPACE
from app.telegram_security.clock import FrozenClock as TelegramClock
from app.telegram_security.contracts import ChatType
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramTransport
from app.workers.watcher_activation import run_staging_activation
from app.workers.watcher_paper import build_watcher_paper_runtime, new_worker_instance_id
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_evaluator import (
    build_context_4h_bars,
    build_pattern_15m_bars,
    build_slice_trades,
)
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.support.telegram_security import (
    BOT,
    CHAT,
    TG_USER,
    TokenSeq,
    enroll,
    message_identity,
)
from tests.test_watcher_paper_activation import _observations
from tests.test_watcher_paper_runtime import ORG, USER, _seed_approved_compiled
from tests.test_watcher_product_proof_postgres import _persist_resistance

NARRATIVE = "NARRATIVE_SENTINEL_not_a_fact"

_STAGING: dict[str, object] = {
    "environment": "staging",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "exchange_mode": "paper_internal",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
    "perpetual_evidence_source": "binance_usdm",
    "market_data_futures_base_url": "https://fapi.binance.com",
    "watcher_orchestration_enabled": True,
    "watcher_paper_staging_activation": True,
    "autonomous_internal_paper_enabled": True,
    "telegram_interaction_enabled": True,
    "telegram_paper_activation_armed": True,
    "telegram_inbound_mode": "polling",
    "telegram_bot_id": BOT,
    "telegram_chat_id": CHAT,
    "telegram_bot_token": "123456789:AAHtestTokenValueForStagingPackage",
    "telegram_alerts_enabled": False,
    "automatic_telegram_delivery_enabled": False,
    "telegram_network_permitted": True,
}


def _package_settings() -> Settings:
    return Settings(**_STAGING)


def _discussion_updates() -> tuple[ParsedTelegramUpdate, ...]:
    rows = (
        (90, "pkg-discuss", "Explain the candidate evidence and risk"),
        (91, "pkg-learn", "show learning attribution"),
        (92, "pkg-no-92", "place order"),
        (93, "pkg-no-93", "override risk"),
        (94, "pkg-no-94", "override assessment"),
        (95, "pkg-no-95", "activate strategy"),
        (96, "pkg-no-96", "mint candidate"),
        (97, "pkg-no-97", "enable live trading"),
    )
    return tuple(
        ParsedTelegramUpdate(
            update_id=update_id,
            body_size=len(text.encode("utf-8")),
            kind="message",
            chat_type=ChatType.PRIVATE,
            chat_id=CHAT,
            telegram_user_id=TG_USER,
            message_id=message_id,
            text=text,
        )
        for update_id, message_id, text in rows
    )


class RepeatingLivePerpetualSource(ScriptedPerpetualSource):
    """Live-identity source. Every fetch returns the fixture window. No network."""

    def __init__(self) -> None:
        bars_15m = build_pattern_15m_bars()
        super().__init__(
            replay=False,
            bars_15m=bars_15m,
            bars_4h=build_context_4h_bars(),
        )
        self._trades = build_slice_trades(bars_15m)

    def fetch_ordered_trades(self, **kwargs: object) -> object:
        start = kwargs["start"]
        end = kwargs["end"]
        assert isinstance(start, datetime)
        assert isinstance(end, datetime)
        self.enqueue(select_trades_in_window(self._trades, start=start, end=end))
        return super().fetch_ordered_trades(**kwargs)  # type: ignore[arg-type]


class OutageLiveSource(RepeatingLivePerpetualSource):
    def fetch_ordered_trades(self, **kwargs: object) -> object:
        raise RegionalProviderFailureError("scripted regional outage")


def _seed_org(session: Session) -> None:
    session.add_all(
        [
            Organization(id=ORG, name="Controlled paper rehearsal"),
            User(id=USER, email="controlled-paper@test.example", hashed_password="not-a-real-hash"),
        ]
    )
    session.flush()
    session.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.OWNER))
    session.commit()
    _seed_approved_compiled(session)
    _persist_resistance(session)


def _account_id_for(binding_id: UUID) -> UUID:
    return uuid5(PAPER_NOTIFY_IDENTITY_NAMESPACE, f"acct:{binding_id}")


def _enroll(factory: sessionmaker[Session]) -> UUID:
    clock = TelegramClock(EVALUATED_AT)
    protocol = TelegramSecurityProtocol(
        store=build_postgres_telegram_security_store(factory),
        transport=FakeTelegramTransport(),
        clock=clock,
        enabled=True,
        token_factory=TokenSeq(),
    )
    _token, binding_id = enroll(
        protocol,
        organization_id=ORG,
        user_id=USER,
        identity=message_identity(bot_id=BOT, chat_id=CHAT),
    )
    return binding_id


def _add_account(factory: sessionmaker[Session], account_id: UUID) -> None:
    with factory() as session:
        session.add(
            ExecutionAccount(
                id=account_id,
                organization_id=ORG,
                user_id=USER,
                name="Controlled paper account",
                execution_mode=ExecutionMode.PAPER,
                account_mode=AccountMode.NET,
            )
        )
        session.commit()


def _monitor(source: ScriptedPerpetualSource, *, now: datetime) -> PerpetualMarketMonitor:
    return PerpetualMarketMonitor(
        source,
        replay=False,
        backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=4.0),
        poll_seconds=0.01,
        clock=lambda: now,
    )


def _evidence_factory(
    source: ScriptedPerpetualSource,
    monitor: PerpetualMarketMonitor,
) -> Callable[..., AssemblingWatcherScanEvidence]:
    def evidence_factory(db: object, store: object, symbol: str) -> AssemblingWatcherScanEvidence:
        lifetime = SqlAlchemySetupLifetimeStore(db) if db is not None else SetupLifetimeStore()
        assembler = FirstSliceEvidenceAssembler(
            source,
            replay=False,
            lifetime=lifetime,
            clock=lambda: EVALUATED_AT,
        )
        return AssemblingWatcherScanEvidence(
            assembler,
            session=db,  # type: ignore[arg-type]
            watcher_store=store,  # type: ignore[arg-type]
            symbol=symbol,
            monitor=MarketMonitorWatcherPort(monitor),
        )

    return evidence_factory


def _count(factory: sessionmaker[Session], model: type[object]) -> int:
    with factory() as session:
        found = session.scalar(select(func.count()).select_from(model))
    return int(found or 0)


@pytest.fixture(autouse=True)
def offline_contract_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    # All rehearsal cases use scripted market acquisition. The outage/stale
    # cases must reach their evidence source rather than external discovery.
    monkeypatch.setattr(
        "app.workers.watcher_paper._live_contract_discoverer", lambda settings: None
    )


@requires_postgres
def test_rehearsal_projects_live_evidence_through_paper_and_telegram() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    settings = _package_settings()
    assert isinstance(resolve_perpetual_evidence_source(settings), BinanceUsdmPerpetualSource)
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    with factory() as session:
        _seed_org(session)
    binding_id = _enroll(factory)
    account_id = _account_id_for(binding_id)
    _add_account(factory, account_id)
    transport = FakeTelegramTransport()
    discussion = RecordedUpdateSource(_discussion_updates())
    projection = build_controlled_scan_hook(
        settings,
        factory,
        transport=transport,
        update_source=discussion,
        clock=TelegramClock(EVALUATED_AT),
    )
    assert projection is not None
    assert isinstance(projection.agent._context, EvaluationLearningContext)
    assert projection.recipient.binding_id == binding_id
    assert projection.recipient.account_id == account_id

    source = RepeatingLivePerpetualSource()
    monitor = _monitor(source, now=EVALUATED_AT)
    first = monitor.tick("BTCUSDT")
    assert first.mode is MarketMode.LIVE_PERPETUAL
    assert first.availability is MarketAvailability.FRESH
    assert first.source_family is SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
    assert first.is_live is True
    assert first.is_mock is False
    assert first.fallback_used is False
    assert first.live_executable is False
    monitor.restart()

    worker_id = new_worker_instance_id(settings.watcher_paper_worker_id)
    assert worker_id != settings.watcher_paper_worker_id
    runtime = build_watcher_paper_runtime(
        settings,
        factory,
        evidence_factory=_evidence_factory(source, monitor),
        clock=UtcClock(),
        scan_notification_hook=projection.hook,
        monitor=monitor,
        worker_id=worker_id,
        enabled=True,
        activation_cleared=True,
    )
    assert runtime.snapshot().enabled is True
    assert runtime.snapshot().worker_id == worker_id
    cycle = runtime.run_cycle()
    assert cycle.scans, "approved strategy did not become a scan target"
    scan = cycle.scans[0]
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value, scan.reason_code
    assert scan.published is True
    assert scan.replayed is False
    assert len(scan.candidate_ids) == 1
    assert scan.discussion is not None
    families = {item.source_family for item in scan.discussion.window.source_set}
    assert SourceFamily.BINANCE_USDM_FUTURES_PUBLIC in families
    assert SourceFamily.REPLAY_FIXTURE not in families
    assert runtime.side_effects.execution == []  # type: ignore[attr-defined]
    candidate = scan.discussion.candidate
    assert scan.paper_loop_stage == "filled"
    assert scan.paper_loop_reason == "open_journal"
    assert scan.paper_loop_replayed is False
    assert scan.eligibility_state == ActionEligibilityState.ELIGIBLE.value
    assert scan.trade_plan_revision_id is not None
    assert scan.execution_command_id is not None
    assert scan.paper_fill_id is not None
    assert scan.journal_trade_id is not None
    assert scan.journal_status == JournalTradeStatus.OPEN.value

    decision_runtime = build_production_canonical_runtime(
        factory, settings=settings, clock=PlanClock(EVALUATED_AT)
    )
    stored = decision_runtime.lifecycle.get_by_candidate_id(ORG, candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.PLAN_CREATED
    envelope = decision_runtime.plans.get_scoped(
        scan.trade_plan_revision_id,
        organization_id=ORG,
        user_id=USER,
    )
    assert envelope.paper_actionable is True
    assert envelope.live_executable is False
    assert envelope.plan.execution_venue == "PAPER_INTERNAL"
    assert envelope.lineage.candidate_id == candidate.candidate_id
    assert decision_runtime.flags.real_trading_enabled is False
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    with factory() as session:
        plans = session.scalars(select(CanonicalTradePlanRootRow)).all()
        assert len(plans) == 1
        assert plans[0].candidate_id == candidate.candidate_id
        lineages = session.scalars(select(CanonicalTradePlanLineageRow)).all()
        assert len(lineages) == 1
        assert lineages[0].revision_id == scan.trade_plan_revision_id
        assert lineages[0].candidate_id == candidate.candidate_id
        fills = session.scalars(select(ExecutionFillFact)).all()
        assert len(fills) == 1
        assert fills[0].id == scan.paper_fill_id
        assert fills[0].command_id == scan.execution_command_id
        assert fills[0].venue_source == "paper_internal"
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.id == scan.journal_trade_id
        assert trade.candidate_id == candidate.candidate_id
        assert trade.execution_lifecycle_id == scan.execution_command_id
        assert trade.status == JournalTradeStatus.OPEN
        events = session.scalars(select(JournalLifecycleEvent)).all()
        assert events
        linked_candidates: set[str] = set()
        for event in events:
            payload = event.payload
            if not isinstance(payload, dict):
                continue
            lineage = payload.get("lineage")
            if isinstance(lineage, dict) and "candidate_id" in lineage:
                linked_candidates.add(str(lineage["candidate_id"]))
        assert linked_candidates == {str(candidate.candidate_id)}
        attribution = session.scalars(select(LearningAttributionRecordRow)).one()
        assert attribution.candidate_id == candidate.candidate_id
        JournalTradeService(session, AuditService(session)).add_rule_check(
            trade.id,
            JournalTradeRuleCheckCreate(
                rule_key="recorded_stop",
                rule_source="journal",
                status=RuleComplianceStatus.VIOLATED,
                notes="Operator recorded a stop violation on the paper journal trade.",
            ),
            organization_id=ORG,
            user_id=USER,
        )
        session.commit()
        query = PaperEvaluationQueryService(
            PostgresPaperEvaluationStore(session),
            attribution_store=PostgresAttributionStore(session),
            journal=SqlAlchemyJournalExcursionPort(session),
        )
        eligibility_rows = latest_evaluations_for_organization(session, organization_id=ORG)
        summary = query.summary(organization_id=ORG, eligibility=eligibility_rows, narrative=None)
        assert summary.facts.live_executable is False
        assert summary.facts.watcher_orchestration_enabled is False
        assert summary.facts.telegram_interaction_enabled is False
        assert summary.facts.conversion.candidates >= 1
        stages = {
            item.stage for item in PostgresPaperEvaluationStore(session).list_for_organization(ORG)
        }
        assert PaperEvaluationStage.WATCHER_SCAN in stages
        assert PaperEvaluationStage.CANDIDATE in stages
        suggestion = next(item for item in summary.refinements if item.category == "rule_adherence")
        assert suggestion.activate is False
        with pytest.raises(RefinementActivationForbiddenError):
            refuse_activation(suggestion)
        lines = evaluation_fact_lines(summary)
        assert lines is not None
        assert "telegram_interaction_enabled:false" in lines
        assert any("activate=false" in line for line in lines)
        candidates_before = _count(factory, CanonicalCandidateRow)
        plans_before = _count(factory, CanonicalTradePlanRootRow)
        fills_before = _count(factory, ExecutionFillFact)
        journals_before = _count(factory, JournalTrade)
        telegram = TelegramPaperRuntime(
            settings=settings,
            session_factory=factory,
            clock=TelegramClock(EVALUATED_AT),
            worker_id="telegram-paper:rehearsal01",
            posture="projection",
            controller=projection.controller,
        )
        telegram_cycle = telegram.run_cycle()
        assert telegram_cycle.delivered >= 1
        assert telegram_cycle.poll_applied >= 8
        assert telegram_cycle.kill_switch_active is False
        assert transport.send_count >= 1
        assert _count(factory, CanonicalCandidateRow) == candidates_before
        assert _count(factory, CanonicalTradePlanRootRow) == plans_before == 1
        assert _count(factory, ExecutionFillFact) == fills_before == 1
        assert _count(factory, JournalTrade) == journals_before == 1
        assert settings.enable_real_trading is False
        assert settings.real_trading_enabled is False
        with factory() as fresh:
            texts = "\n".join(fresh.scalars(select(TelegramOutboxRow.text)).all())
        assert "activate=false" in texts
        for needle in (
            "cannot place an order",
            "BLOCK remains final",
            "cannot override SetupAssessment",
            "cannot approve or activate",
            "cannot mint a Candidate",
            "cannot enable live trading",
        ):
            assert needle in texts

    before = (
        _count(factory, CanonicalCandidateRow),
        _count(factory, JournalTrade),
        _count(factory, TelegramOutboxRow),
    )
    assert before[0] >= 1
    assert before[1] == 1
    assert before[2] >= 1
    plan_package_rollback()
    assert (
        _count(factory, CanonicalCandidateRow),
        _count(factory, JournalTrade),
        _count(factory, TelegramOutboxRow),
    ) == before


@requires_postgres
def test_missing_binding_refuses_the_worker_without_a_scan() -> None:
    factory = phase7_plan_session_factory()
    settings = _package_settings()
    worker_id = new_worker_instance_id(settings.watcher_paper_worker_id)
    with pytest.raises(TelegramActivationError) as captured:
        build_controlled_scan_hook(
            settings,
            factory,
            transport=FakeTelegramTransport(),
            update_source=RecordedUpdateSource(()),
            clock=TelegramClock(EVALUATED_AT),
        )
    assert captured.value.reason == "recipient_binding_missing"
    decision = run_staging_activation(
        settings,
        observations=_observations(worker_id),
        session_factory=factory,
        worker_instance_id=worker_id,
    )
    assert decision.allowed is False
    assert decision.primary_reason == "telegram_projection_refused"


@requires_postgres
def test_provider_outage_does_not_mint() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    settings = _package_settings()
    with factory() as session:
        _seed_org(session)
    outage = OutageLiveSource()
    outage_monitor = _monitor(outage, now=EVALUATED_AT)
    worker_id = new_worker_instance_id(settings.watcher_paper_worker_id)
    runtime = build_watcher_paper_runtime(
        settings,
        factory,
        evidence_factory=_evidence_factory(outage, outage_monitor),
        clock=UtcClock(),
        monitor=outage_monitor,
        worker_id=worker_id,
        enabled=True,
        activation_cleared=True,
    )
    cycle = runtime.run_cycle()
    assert cycle.scans
    assert cycle.scans[0].status == "failed"
    assert cycle.scans[0].candidate_ids == ()
    assert cycle.scans[0].reason_code == "provider_outage"
    assert _count(factory, CanonicalCandidateRow) == 0


@requires_postgres
def test_stale_live_window_does_not_mint() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    settings = _package_settings()
    with factory() as session:
        _seed_org(session)
    source = RepeatingLivePerpetualSource()
    monitor = _monitor(source, now=EVALUATED_AT + timedelta(seconds=30))
    runtime = build_watcher_paper_runtime(
        settings,
        factory,
        evidence_factory=_evidence_factory(source, monitor),
        clock=UtcClock(),
        monitor=monitor,
        worker_id=new_worker_instance_id(settings.watcher_paper_worker_id),
        enabled=True,
        activation_cleared=True,
    )
    cycle = runtime.run_cycle()
    assert cycle.scans
    assert cycle.scans[0].status == "failed"
    assert cycle.scans[0].reason_code != SetupAssessmentState.CONFIRMED_SETUP.value
    assert cycle.scans[0].candidate_ids == ()
    assert _count(factory, CanonicalCandidateRow) == 0
