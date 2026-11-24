"""PR122 measurement and PR124 Telegram discussion on one paper Watcher scan path.

Telegram stays disabled unless a test supplies a hook. Evaluation cannot activate
a strategy. Narrative text is excluded from fact lines and from content hashes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.deployment_safety import deployment_posture
from app.learning_attribution.contracts import LearningVenueMode, RiskAdherence
from app.learning_attribution.memory import InMemoryAttributionStore
from app.main import create_app
from app.paper_evaluation.contracts import PaperEvaluationStage
from app.paper_evaluation.errors import RefinementActivationForbiddenError
from app.paper_evaluation.memory import InMemoryPaperEvaluationStore
from app.paper_evaluation.ports import JournalTradeMeasurement
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.paper_evaluation.recorder import PaperEvaluationRecorder
from app.paper_evaluation.refinement import refuse_activation
from app.paper_evaluation.watcher_observer import WatcherPaperEvaluationObserver
from app.paper_interaction.bridge import (
    ConfirmedScanEvidence,
    EvaluationLearningContext,
    evaluation_fact_lines,
    notice_from_scan_report,
    project_scan_report,
    telegram_scan_hook,
)
from app.schemas.common import TradeResult
from app.schemas.journal_statistics import TradeRuleCompliance
from app.signal_fusion.enums import CandidateState
from app.signal_fusion.lifecycle import in_memory_candidate_lifecycle
from app.telegram_paper_agent.contracts import JournalOutcomeView, PaperAlertRecipient
from app.telegram_paper_agent.errors import PaperTelegramDisabledError, PaperTelegramTenantError
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_paper_agent.memory import InMemoryPaperContext
from app.telegram_security.clock import FrozenClock
from app.watcher.memory import FakeClock, InMemoryWatcherStore
from app.workers.watcher_paper import (
    WatcherPaperRuntime,
    WatcherPaperScanReport,
    paper_runtime_enabled,
)
from tests.support.paper_evaluation import (
    make_attribution_record,
    make_eval_command,
    make_eval_outcome,
)
from tests.support.phase6_fusion import ACCOUNT_ID
from tests.support.telegram_paper_agent import assert_no_internal_ids, enabled_paper_agent_world
from tests.support.telegram_security import (
    OTHER_ORG,
    inbound_message,
    message_identity,
)
from tests.test_watcher_paper_runtime import (
    ORG,
    USER,
    _runtime,
    _seed_approved_compiled,
)

PACKAGE = Path(__file__).resolve().parents[1] / "src/app/paper_interaction"
NARRATIVE = "NARRATIVE_SENTINEL_purple_elephant"
WHEN = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


class _Journal:
    def __init__(self, organization_id: UUID, row: JournalTradeMeasurement) -> None:
        self._organization_id = organization_id
        self._row = row

    def facts_for(
        self, *, organization_id: UUID, journal_trade_ids: tuple[UUID, ...]
    ) -> dict[UUID, JournalTradeMeasurement]:
        if organization_id != self._organization_id:
            return {}
        if self._row.journal_trade_id not in journal_trade_ids:
            return {}
        return {self._row.journal_trade_id: self._row}


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    from app.db.models import Membership, Organization, User
    from app.schemas.common import MembershipRole
    from tests.test_watcher_paper_runtime import ORG_B, USER_B, _sqlite_factory

    factory = _sqlite_factory()
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG, name="AT069 Org A"),
                Organization(id=ORG_B, name="AT069 Org B"),
                User(id=USER, email="at069-a@test.example", hashed_password="not-a-real-hash"),
                User(id=USER_B, email="at069-b@test.example", hashed_password="not-a-real-hash"),
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
    yield factory


def test_product_flags_and_default_hook_stay_off() -> None:
    settings = Settings()
    assert settings.telegram_interaction_enabled is False
    assert settings.watcher_orchestration_enabled is False
    assert settings.market_watcher_enabled is False
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert paper_runtime_enabled(settings) is False
    posture = deployment_posture(settings)
    assert posture["telegram_interaction_enabled"] is False
    assert posture["watcher_orchestration_enabled"] is False
    assert posture["real_trading_enabled"] is False
    app = create_app(settings)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("telegram" in path and "webhook" in path for path in paths)
    health = TestClient(app).get("/health")
    assert health.status_code == 200
    assert health.json()["telegram_interaction_enabled"] is False
    source = "\n".join(path.read_text() for path in PACKAGE.glob("*.py"))
    assert "ExecutionService" not in source
    assert "place_order" not in source
    assert "enable_real_trading = True" not in source


def test_disabled_hook_and_failures_do_not_alert_or_raise() -> None:
    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=in_memory_candidate_lifecycle(now=WHEN),
        clock=FakeClock(WHEN),
        enabled=False,
    )
    assert runtime._scan_notification_hook is None
    agent = TelegramPaperAgent.in_memory(enabled=False, now=WHEN)
    recipient = PaperAlertRecipient(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT_ID,
        binding_id=uuid4(),
        bot_id="bot",
        chat_id="chat",
    )
    report = WatcherPaperScanReport(
        organization_id=ORG,
        user_id=USER,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="blocked",
        reason_code="stale_evidence",
        replayed=False,
        published=False,
        candidate_ids=(),
        kill_switch_active=False,
    )
    armed = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=in_memory_candidate_lifecycle(now=WHEN),
        clock=FakeClock(WHEN),
        enabled=False,
        scan_notification_hook=telegram_scan_hook(agent, recipient),
    )
    armed._notify_scan(report)
    with pytest.raises(PaperTelegramDisabledError):
        agent.deliver_pending()
    with pytest.raises(PaperTelegramDisabledError):
        project_scan_report(agent, report, recipient=recipient)

    def explode(_report: WatcherPaperScanReport) -> None:
        raise RuntimeError("transport down")

    fragile = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=in_memory_candidate_lifecycle(now=WHEN),
        clock=FakeClock(WHEN),
        enabled=False,
        scan_notification_hook=explode,
    )
    fragile._notify_scan(report)
    assert (
        notice_from_scan_report(
            WatcherPaperScanReport(
                organization_id=ORG,
                scan_scope="org:btcusdt",
                symbol="BTCUSDT",
                status="blocked",
                reason_code="stale_evidence",
                replayed=False,
                published=False,
                candidate_ids=(),
                kill_switch_active=False,
            )
        )
        is None
    )


def test_journal_attribution_learning_keeps_facts_apart_from_narrative() -> None:
    world = enabled_paper_agent_world()
    org = world.recipient.organization_id
    trade_id = uuid4()
    evaluation_store = InMemoryPaperEvaluationStore()
    attributions = InMemoryAttributionStore()
    recorder = PaperEvaluationRecorder(evaluation_store)
    observer = WatcherPaperEvaluationObserver(recorder)
    command = make_eval_command(organization_id=org)
    outcome = make_eval_outcome(command, candidate_ids=(world.candidate.candidate_id,))
    observer.observe_evaluation(command, outcome)
    before = len(evaluation_store.list_for_organization(org))
    observer.observe_evaluation(command, outcome)
    assert len(evaluation_store.list_for_organization(org)) == before
    record = make_attribution_record(
        organization_id=org,
        candidate_id=world.candidate.candidate_id,
        journal_trade_id=trade_id,
        confirmed=True,
        closed=True,
        executed=True,
        loss=True,
        net_pnl="-1.25",
        result=TradeResult.LOSS,
        risk_adherence=RiskAdherence.STOP_VIOLATION,
        narrative=NARRATIVE,
    )
    attributions.put(record)
    recorder.record_attribution(record)
    journal = _Journal(
        org,
        JournalTradeMeasurement(
            journal_trade_id=trade_id,
            mfe_amount=Decimal("2.50"),
            mae_amount=Decimal("1.25"),
            net_pnl=Decimal("-1.25"),
            result=TradeResult.LOSS,
            rule_compliance=TradeRuleCompliance.VIOLATED,
            closed_at=WHEN,
        ),
    )
    query = PaperEvaluationQueryService(
        evaluation_store,
        attribution_store=attributions,
        journal=journal,
    )
    plain = query.summary(organization_id=org, narrative=None)
    told = query.summary(organization_id=org, narrative=NARRATIVE)
    assert plain.facts.content_hash == told.facts.content_hash
    assert told.narrative is not None
    assert NARRATIVE in told.narrative.text
    assert plain.facts.missed_opportunities.counterfactual_pnl is None
    assert plain.facts.live_executable is False
    assert plain.facts.strategy_overall.average_mfe is not None
    assert plain.refinements
    suggestion = plain.refinements[0]
    assert suggestion.activate is False
    assert suggestion.auto_activate is False
    with pytest.raises(RefinementActivationForbiddenError):
        refuse_activation(suggestion)
    lines = evaluation_fact_lines(plain)
    assert lines is not None
    assert all(NARRATIVE not in line for line in lines)
    assert "refinement:rule_adherence:activate=false:auto_activate=false" in lines
    context = EvaluationLearningContext(
        InMemoryPaperContext(
            journal=JournalOutcomeView(
                trade_id=trade_id,
                symbol="BTCUSDT",
                status="closed",
                result="loss",
                net_pnl="-1.25",
                summary="Recorded paper journal close. Not a live fill.",
            )
        ),
        query,
    )
    agent = TelegramPaperAgent(
        protocol=world.protocol,
        candidates=world.agent.candidates,
        clock=FrozenClock(world.candidate.created_at),
        store=world.agent.store,
        context=context,
        enabled=True,
    )
    notice_report = WatcherPaperScanReport(
        organization_id=org,
        user_id=world.recipient.user_id,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="confirmed_setup",
        replayed=False,
        published=True,
        candidate_ids=(world.candidate.candidate_id,),
        kill_switch_active=False,
        request_hash="ab" * 32,
        lineage_id=uuid4(),
    )
    evidence = ConfirmedScanEvidence(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    window_before = world.window.model_dump()
    candidate_hash = world.candidate.content_hash
    first = project_scan_report(agent, notice_report, recipient=world.recipient, evidence=evidence)
    second = project_scan_report(agent, notice_report, recipient=world.recipient, evidence=evidence)
    assert first is not None and second is not None
    assert second.converged is True
    assert second.outbox.outbox_id == first.outbox.outbox_id
    assert world.window.model_dump() == window_before
    assert world.candidate.content_hash == candidate_hash
    assert world.candidate.state is CandidateState.ACTIVE
    delivered = agent.deliver_pending()
    assert delivered and delivered[0].accepted is True
    learning = agent.handle_inbound_message(
        identity=message_identity(update_id=70, message_id="learn-1"),
        inbound=inbound_message(),
        text="show learning attribution",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert learning.executed is False
    assert learning.reply_outbox is not None
    reply = learning.reply_outbox.text
    assert NARRATIVE not in reply
    assert_no_internal_ids(reply)
    assert plain.facts.content_hash not in reply
    assert "(not active)" in reply
    assert "\u2022 Counterfactual P&L: None" in reply
    assert f"\u2022 Average MFE: {plain.facts.strategy_overall.average_mfe}" in reply
    journal_reply = agent.handle_inbound_message(
        identity=message_identity(update_id=71, message_id="journal-1"),
        inbound=inbound_message(),
        text="show the journal outcome",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert journal_reply.reply_outbox is not None
    assert_no_internal_ids(journal_reply.reply_outbox.text)
    assert "-1.25" in journal_reply.reply_outbox.text
    for update_id, text, needle in (
        (72, "place order", "cannot place an order"),
        (73, "override risk", "BLOCK remains final"),
        (74, "activate strategy", "cannot approve or activate"),
        (75, "enable live trading", "cannot enable live trading"),
    ):
        refused = agent.handle_inbound_message(
            identity=message_identity(update_id=update_id, message_id=f"no-{update_id}"),
            inbound=inbound_message(),
            text=text,
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
        )
        assert refused.refused is True
        assert refused.executed is False
        assert refused.candidate_minted is False
        assert refused.risk_overridden is False
        assert refused.strategy_approved is False
        assert refused.live_trading_enabled is False
        assert refused.reply_outbox is not None
        assert needle in refused.reply_outbox.text
    assert world.candidate.state is CandidateState.ACTIVE
    assert world.window.model_dump() == window_before
    assert len(evaluation_store.list_for_organization(org)) == before + 1
    stages = {item.stage for item in evaluation_store.list_for_organization(org)}
    assert PaperEvaluationStage.WATCHER_SCAN in stages
    assert PaperEvaluationStage.ATTRIBUTION in stages
    assert evaluation_fact_lines(query.summary(organization_id=OTHER_ORG)) is None
    other = world.recipient.model_copy(update={"organization_id": OTHER_ORG})
    with pytest.raises(PaperTelegramTenantError):
        project_scan_report(agent, notice_report, recipient=other, evidence=evidence)
    restored = TelegramPaperAgent(
        protocol=agent.protocol,
        candidates=agent.candidates,
        clock=FrozenClock(world.candidate.created_at),
        store=agent.store,
        context=context,
        enabled=True,
    )
    replay = project_scan_report(
        restored, notice_report, recipient=world.recipient, evidence=evidence
    )
    assert replay is not None and replay.converged is True
    assert restored.store.get_notification_by_hash(first.intent.identity_hash) is not None
    assert (
        query.summary(
            organization_id=org, learning_venue_mode=LearningVenueMode.PAPER_INTERNAL
        ).facts.live_executable
        is False
    )


def test_watcher_scan_records_evaluation_and_durable_notification(
    session_factory: sessionmaker[Session],
) -> None:
    from app.candidate_alerts.gateway import CandidateAlertGateway
    from app.signal_fusion.lifecycle import CandidateLifecycleService
    from app.signal_fusion.memory import InMemoryCandidateRepository
    from app.telegram_security.protocol import TelegramSecurityProtocol
    from app.telegram_security.transport import FakeTelegramTransport
    from app.watcher.fusion_evaluation import BoundEvaluationClock
    from tests.support.phase6_evaluator import make_world
    from tests.support.telegram_security import BOT, CHAT, TokenSeq, enroll

    factory = session_factory
    with factory() as session:
        strategy, version_id = _seed_approved_compiled(session)
        strategy_id = strategy.id
        assert version_id is not None
    evaluation_store = InMemoryPaperEvaluationStore()

    def observer_factory(session: Session | None, target: object) -> object:
        if session is None:
            return None
        recorder = PaperEvaluationRecorder(evaluation_store)
        return WatcherPaperEvaluationObserver(
            recorder,
            strategy_version_id=getattr(target, "strategy_version_id", None),
            setup_definition_id=getattr(target, "compiled_setup_definition_id", None),
        )

    repo = InMemoryCandidateRepository()
    lifecycle = CandidateLifecycleService(repository=repo, clock=BoundEvaluationClock())
    clock = FrozenClock(WHEN)
    transport = FakeTelegramTransport()
    protocol = TelegramSecurityProtocol.in_memory(
        enabled=True, clock=clock, transport=transport, token_factory=TokenSeq()
    )
    _token, binding_id = enroll(protocol, organization_id=ORG, user_id=USER)
    recipient = PaperAlertRecipient(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT_ID,
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    query = PaperEvaluationQueryService(evaluation_store)
    context = EvaluationLearningContext(InMemoryPaperContext(), query)
    gateway = CandidateAlertGateway(
        lifecycle=lifecycle,
        protocol=protocol,
        clock=clock,
    )
    agent = TelegramPaperAgent(
        protocol=protocol,
        candidates=gateway,
        clock=clock,
        context=context,
        enabled=True,
    )
    reports: list[WatcherPaperScanReport] = []
    inner = telegram_scan_hook(agent, recipient)

    def hook(report: WatcherPaperScanReport) -> None:
        reports.append(report)
        inner(report)

    runtime, _clock, probe, _store = _runtime(
        factory,
        world=make_world(),
        lifecycle=lifecycle,
        scan_notification_hook=hook,
        evaluation_observer_factory=observer_factory,
    )
    cycle = runtime.run_cycle()
    assert len(cycle.scans) == 1
    scan = cycle.scans[0]
    assert scan.published is True
    assert scan.user_id == USER
    assert scan.discussion is not None
    assert scan.candidate_ids == (scan.discussion.candidate.candidate_id,)
    assert reports and reports[0].discussion is not None
    observations = evaluation_store.list_for_organization(ORG)
    stages = {item.stage for item in observations}
    assert PaperEvaluationStage.WATCHER_SCAN in stages
    assert PaperEvaluationStage.CANDIDATE in stages
    delivered = agent.deliver_pending()
    assert delivered and delivered[0].accepted is True
    discussed = agent.handle_inbound_message(
        identity=message_identity(update_id=80, message_id="scan-discuss"),
        inbound=inbound_message(),
        text="Explain the candidate evidence and risk",
        candidate=scan.discussion.candidate,
        assessment=scan.discussion.assessment,
        window=scan.discussion.window,
    )
    assert discussed.executed is False
    assert discussed.candidate_minted is False
    assert discussed.reply_outbox is not None
    learning = agent.handle_inbound_message(
        identity=message_identity(update_id=81, message_id="scan-learn"),
        inbound=inbound_message(),
        text="show learning attribution",
        candidate=scan.discussion.candidate,
        assessment=scan.discussion.assessment,
        window=scan.discussion.window,
    )
    assert learning.reply_outbox is not None
    assert_no_internal_ids(learning.reply_outbox.text)
    assert "\u2022 Live executable: No" in learning.reply_outbox.text
    summary = query.summary(organization_id=ORG)
    assert summary.facts.watcher.scan_count >= 1
    assert summary.facts.watcher_orchestration_enabled is False
    assert summary.refinements == () or all(
        item.activate is False and item.auto_activate is False for item in summary.refinements
    )
    assert agent.deliver_pending()
    replay = runtime.run_cycle()
    assert replay.scans[0].replayed is True
    assert replay.scans[0].discussion is None
    assert agent.deliver_pending() == []
    again = project_scan_report(agent, reports[0], recipient=recipient)
    assert again is not None and again.converged is True
    restored = TelegramPaperAgent(
        protocol=protocol,
        candidates=gateway,
        clock=clock,
        store=agent.store,
        context=context,
        enabled=True,
    )
    recovered = project_scan_report(restored, reports[0], recipient=recipient)
    assert recovered is not None and recovered.converged is True
    persisted = repo.get_by_id(ORG, scan.candidate_ids[0])
    assert persisted is not None
    assert persisted.state is CandidateState.ACTIVE
    assert probe.unused is True
    with factory() as session:
        from app.db.models import UserStrategy

        row = session.get(UserStrategy, strategy_id)
        assert row is not None
        assert row.paper_eligible is True
    other = recipient.model_copy(update={"organization_id": uuid4()})
    with pytest.raises(PaperTelegramTenantError):
        project_scan_report(agent, reports[0], recipient=other)
