"""PostgreSQL product loop for the integrated paper system.

Approved compiled strategy → real canonical evidence assembly → Watcher →
genuine CONFIRMED_SETUP → persisted Candidate → paper TradePlan → paper
execution → Journal → attribution → evaluation facts → learning suggestion →
durable Telegram alert and discussion.

Does not insert CONFIRMED_SETUP. Watcher, Telegram, and live trading stay off
unless a test explicitly supplies a hook. Risk BLOCK stays final.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.candidate_alerts.gateway import CandidateAlertGateway
from app.core.config import Settings
from app.core.persistence_firewall import install_persistence_firewall
from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    ExecutionAccount,
    JournalLifecycleEvent,
    JournalTrade,
    Membership,
    Organization,
    User,
)
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import SetupLifetimeStore
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.paper_evaluation.contracts import PaperEvaluationStage
from app.paper_evaluation.errors import RefinementActivationForbiddenError
from app.paper_evaluation.journal_facts import SqlAlchemyJournalExcursionPort
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.paper_evaluation.refinement import refuse_activation
from app.paper_interaction.bridge import (
    EvaluationLearningContext,
    evaluation_fact_lines,
    project_scan_report,
    telegram_scan_hook,
)
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.persistence.eligibility_postgres import latest_evaluations_for_organization
from app.persistence.paper_evaluation_postgres import PostgresPaperEvaluationStore
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.persistence.telegram_paper_agent import PostgresPaperAgentStore
from app.runtime.canonical import build_production_canonical_runtime
from app.schemas.canonical_trade_plan import CanonicalTradePlanCommand
from app.schemas.common import MembershipRole, RuleComplianceStatus
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.journal_trades import JournalTradeRuleCheckCreate
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.services.audit_service import AuditService
from app.services.journal_trade_service import JournalTradeService
from app.signal_fusion.enums import ActionEligibilityState, CandidateState, SetupAssessmentState
from app.signal_fusion.memory import FrozenClock as PlanClock
from app.signal_fusion.memory import UtcClock
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_paper_agent.errors import PaperTelegramTenantError
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_paper_agent.memory import InMemoryPaperContext
from app.telegram_security.clock import FrozenClock as TelegramClock
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramTransport
from app.workers.watcher_paper import (
    WatcherPaperScanReport,
    build_watcher_paper_runtime,
)
from tests.support.phase6_eligibility import (
    account_identity,
    eligibility_command,
    market_action,
    paper_configuration,
    portfolio_state,
    risk_snapshot,
    safety_snapshot,
)
from tests.support.phase6_fusion import CORRELATION_A, VENUE_STATE_ID
from tests.support.phase7_trade_plan import plan_terms
from tests.support.phase8_runtime import (
    EXECUTE_AT,
    authorize_canonical_plan,
    canonical_execute_request,
    canonical_execution_service,
    seed_paper_capacity,
)
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.support.telegram_paper_agent import assert_no_internal_ids
from tests.support.telegram_security import (
    BOT,
    CHAT,
    TokenSeq,
    enroll,
    inbound_message,
    message_identity,
)
from tests.test_watcher_paper_runtime import ORG, USER, _seed_approved_compiled, _settings
from tests.test_watcher_product_proof_postgres import _confirming_source, _persist_resistance

NARRATIVE = "NARRATIVE_SENTINEL_not_a_fact"


def _evidence_factory(source: object, monitor: PerpetualMarketMonitor):
    def evidence_factory(db: object, store: object, symbol: str) -> AssemblingWatcherScanEvidence:
        lifetime = SqlAlchemySetupLifetimeStore(db) if db is not None else SetupLifetimeStore()
        return AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(source, replay=True, lifetime=lifetime),
            session=db,  # type: ignore[arg-type]
            watcher_store=store,  # type: ignore[arg-type]
            symbol=symbol,
            monitor=MarketMonitorWatcherPort(monitor),
        )

    return evidence_factory


def _seed(session: Session, account_id: object) -> None:
    session.add_all(
        [
            Organization(id=ORG, name="Final paper loop"),
            User(id=USER, email="final-paper-loop@test.example", hashed_password="not-a-real-hash"),
        ]
    )
    session.flush()
    session.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.OWNER))
    session.add(
        ExecutionAccount(
            id=account_id,
            organization_id=ORG,
            user_id=USER,
            name="Final paper account",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
        )
    )
    session.commit()
    _seed_approved_compiled(session)
    _persist_resistance(session)


@requires_postgres
def test_postgres_product_loop_reaches_suggestion_and_telegram_without_injection() -> None:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    account_id = uuid4()
    with factory() as session:
        _seed(session, account_id)

    source = _confirming_source()
    monitor = PerpetualMarketMonitor(
        source,
        replay=True,
        backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=4.0),
        poll_seconds=0.01,
    )
    settings = _settings(watcher_orchestration_enabled=True)
    assert settings.market_watcher_enabled is False
    assert settings.enable_real_trading is False
    assert settings.telegram_alerts_enabled is False
    assert settings.telegram_interaction_enabled is False
    assert Settings().watcher_orchestration_enabled is False
    assert Settings().market_watcher_enabled is False
    assert Settings().enable_real_trading is False
    assert Settings().telegram_interaction_enabled is False
    assert build_watcher_paper_runtime(Settings(), factory).snapshot().enabled is False

    from tests.support.phase5_market import EVALUATED_AT

    decision_runtime = build_production_canonical_runtime(
        factory, settings=settings, clock=PlanClock(EVALUATED_AT)
    )
    protocol = TelegramSecurityProtocol.in_memory(
        enabled=True,
        clock=TelegramClock(EVALUATED_AT),
        transport=FakeTelegramTransport(),
        token_factory=TokenSeq(),
    )
    _token, binding_id = enroll(protocol, organization_id=ORG, user_id=USER)
    recipient = PaperAlertRecipient(
        organization_id=ORG,
        user_id=USER,
        account_id=account_id,
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    paper_store = PostgresPaperAgentStore(factory)
    gateway = CandidateAlertGateway(
        lifecycle=decision_runtime.lifecycle,
        protocol=protocol,
        clock=TelegramClock(EVALUATED_AT),
    )
    agent = TelegramPaperAgent(
        protocol=protocol,
        candidates=gateway,
        clock=TelegramClock(EVALUATED_AT),
        store=paper_store,
        context=InMemoryPaperContext(),
        enabled=True,
    )
    reports: list[WatcherPaperScanReport] = []
    hook_errors: list[BaseException] = []
    inner = telegram_scan_hook(agent, recipient)

    def hook(report: WatcherPaperScanReport) -> None:
        reports.append(report)
        try:
            inner(report)
        except Exception as exc:
            hook_errors.append(exc)

    runtime = build_watcher_paper_runtime(
        settings,
        factory,
        evidence_factory=_evidence_factory(source, monitor),
        clock=UtcClock(),
        scan_notification_hook=hook,
        monitor=monitor,
    )
    cycle = runtime.run_cycle()
    assert cycle.scans, "approved strategy did not become a scan target"
    scan = cycle.scans[0]
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value, scan.reason_code
    assert scan.published is True
    assert len(scan.candidate_ids) == 1
    assert scan.discussion is not None
    assert scan.discussion.assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    assert scan.candidate_ids == (scan.discussion.candidate.candidate_id,)
    assert hook_errors == []
    assert reports and reports[0].discussion is not None
    assert runtime.side_effects.execution == []  # type: ignore[attr-defined]
    candidate = scan.discussion.candidate
    assessment = scan.discussion.assessment
    window = scan.discussion.window
    stored = decision_runtime.lifecycle.get_by_candidate_id(ORG, candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE
    assert stored.content_hash == candidate.content_hash
    delivered = agent.deliver_pending()
    assert delivered and delivered[0].accepted is True
    discussed = agent.handle_inbound_message(
        identity=message_identity(update_id=97, message_id="final-discuss"),
        inbound=inbound_message(),
        text="Explain the candidate evidence and risk",
        candidate=candidate,
        assessment=assessment,
        window=window,
    )
    assert discussed.executed is False
    assert discussed.candidate_minted is False
    assert discussed.reply_outbox is not None
    replay = project_scan_report(agent, reports[0], recipient=recipient)
    assert replay is not None and replay.converged is True
    restored = TelegramPaperAgent(
        protocol=protocol,
        candidates=gateway,
        clock=TelegramClock(EVALUATED_AT),
        store=PostgresPaperAgentStore(factory),
        context=InMemoryPaperContext(),
        enabled=True,
    )
    recovered = project_scan_report(restored, reports[0], recipient=recipient)
    assert recovered is not None and recovered.converged is True
    other = recipient.model_copy(update={"organization_id": uuid4()})
    with pytest.raises(PaperTelegramTenantError):
        project_scan_report(agent, reports[0], recipient=other)

    evaluation = decision_runtime.eligibility.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=stored,
            account=account_identity(organization_id=ORG, user_id=USER, account_id=account_id),
            portfolio=portfolio_state(organization_id=ORG, account_id=account_id),
            risk=risk_snapshot(
                organization_id=ORG,
                user_id=USER,
                account_id=account_id,
                risk_snapshot_id=uuid4(),
            ),
            safety=safety_snapshot(organization_id=ORG, account_id=account_id),
            market=market_action(venue_state_id=VENUE_STATE_ID),
            configuration=paper_configuration(),
            correlation_id=CORRELATION_A,
        )
    )
    assert evaluation.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert evaluation.paper_actionable is True
    assert evaluation.live_executable is False
    envelope = decision_runtime.plans.create(
        CanonicalTradePlanCommand(
            organization_id=ORG,
            user_id=USER,
            account_id=account_id,
            candidate_id=candidate.candidate_id,
            eligibility_id=evaluation.eligibility.eligibility_id,
            terms=plan_terms(stored, account_id=account_id),
            idempotency_key="final-paper-loop-plan",
            correlation_id=CORRELATION_A,
        )
    )
    assert envelope.live_executable is False
    assert envelope.paper_actionable is True
    assert envelope.lineage.candidate_id == candidate.candidate_id
    planned = decision_runtime.lifecycle.get_by_candidate_id(ORG, candidate.candidate_id)
    assert planned is not None
    assert planned.state is CandidateState.PLAN_CREATED

    with factory() as session:
        authorization = authorize_canonical_plan(session, envelope, plans=decision_runtime.plans)
        seed_paper_capacity(session, envelope)
        session.commit()
        service = canonical_execution_service(session, decision_runtime)
        result = service.execute_paper_plan(
            canonical_execute_request(envelope, authorization, key="final-paper-loop-exec"),
            clock=lambda: EXECUTE_AT,
        )
        session.commit()
        assert result.outcome is ExecutionCommandOutcome.ALLOW
        assert result.replayed is False
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.candidate_id == candidate.candidate_id
        assert trade.execution_lifecycle_id == result.command_id
        assert session.scalars(select(JournalLifecycleEvent)).one().payload["lineage"][
            "candidate_id"
        ] == str(candidate.candidate_id)
        attribution = session.scalars(select(LearningAttributionRecordRow)).one()
        assert attribution.candidate_id == candidate.candidate_id
        assert attribution.journal_trade_id == trade.id
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
        fill = service.apply_paper_plan_fill(
            command_id=result.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100000"),
            source_identity="final-paper-loop-fill",
            occurred_at=EXECUTE_AT,
        )
        session.commit()
        assert fill.replayed is False
        query = PaperEvaluationQueryService(
            PostgresPaperEvaluationStore(session),
            attribution_store=PostgresAttributionStore(session),
            journal=SqlAlchemyJournalExcursionPort(session),
        )
        eligibility_rows = latest_evaluations_for_organization(session, organization_id=ORG)
        summary = query.summary(organization_id=ORG, eligibility=eligibility_rows, narrative=None)
        told = query.summary(organization_id=ORG, eligibility=eligibility_rows, narrative=NARRATIVE)
        assert summary.facts.live_executable is False
        assert summary.facts.watcher_orchestration_enabled is False
        assert summary.facts.telegram_interaction_enabled is False
        assert summary.facts.conversion.candidates >= 1
        assert summary.facts.conversion.approved >= 1
        assert summary.facts.watcher.scan_count >= 1
        assert summary.facts.rule_adherence.journal_violated_count >= 1
        assert summary.facts.missed_opportunities.counterfactual_pnl is None
        assert told.facts.content_hash == summary.facts.content_hash
        assert told.narrative is not None
        assert NARRATIVE in told.narrative.text
        plain_hash = summary.facts.content_hash
        stages = {
            item.stage for item in PostgresPaperEvaluationStore(session).list_for_organization(ORG)
        }
        assert PaperEvaluationStage.WATCHER_SCAN in stages
        assert PaperEvaluationStage.CANDIDATE in stages
        assert PostgresAttributionStore(session).list_for_organization(ORG)
        assert summary.refinements
        suggestion = next(item for item in summary.refinements if item.category == "rule_adherence")
        assert suggestion.activate is False
        assert suggestion.auto_activate is False
        with pytest.raises(RefinementActivationForbiddenError):
            refuse_activation(suggestion)
        lines = evaluation_fact_lines(summary)
        assert lines is not None
        assert f"facts_hash:{plain_hash}" in lines
        assert any("activate=false" in line for line in lines)
        assert all(NARRATIVE not in line for line in lines)
        context = EvaluationLearningContext(
            InMemoryPaperContext(),
            query,
            eligibility_loader=lambda organization_id: latest_evaluations_for_organization(
                session, organization_id=organization_id
            ),
        )
        discussing = TelegramPaperAgent(
            protocol=protocol,
            candidates=gateway,
            clock=TelegramClock(EVALUATED_AT),
            store=paper_store,
            context=context,
            enabled=True,
        )
        learning = discussing.handle_inbound_message(
            identity=message_identity(update_id=90, message_id="final-learn"),
            inbound=inbound_message(),
            text="show learning attribution",
            candidate=candidate,
            assessment=assessment,
            window=window,
        )
        assert learning.executed is False
        assert learning.candidate_minted is False
        assert learning.reply_outbox is not None
        assert NARRATIVE not in learning.reply_outbox.text
        assert_no_internal_ids(learning.reply_outbox.text)
        assert plain_hash not in learning.reply_outbox.text
        assert "(not active)" in learning.reply_outbox.text
        for update_id, text, needle in (
            (91, "place order", "cannot place an order"),
            (92, "override risk", "A risk block is final"),
            (93, "override assessment", "cannot change whether a setup is confirmed"),
            (94, "activate strategy", "cannot approve or activate"),
            (95, "mint candidate", "cannot create a setup"),
            (96, "enable live trading", "cannot enable live trading"),
        ):
            refused = discussing.handle_inbound_message(
                identity=message_identity(update_id=update_id, message_id=f"final-no-{update_id}"),
                inbound=inbound_message(),
                text=text,
                candidate=candidate,
                assessment=assessment,
                window=window,
            )
            assert refused.refused is True
            assert refused.executed is False
            assert refused.candidate_minted is False
            assert refused.risk_overridden is False
            assert refused.strategy_approved is False
            assert refused.live_trading_enabled is False
            assert refused.reply_outbox is not None
            assert needle in refused.reply_outbox.text
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
        persisted = PostgresPaperAgentStore(factory).get_notification_by_hash(
            recovered.intent.identity_hash
        )
        assert persisted is not None
        assert persisted.candidate_id == candidate.candidate_id

    assert planned is not None and planned.state is CandidateState.PLAN_CREATED
    assert decision_runtime.flags.real_trading_enabled is False


def _candidate_total(factory: object) -> int:
    canonical = build_production_canonical_runtime(factory, settings=_settings())  # type: ignore[arg-type]
    _rows, total = canonical.candidate_repository.list_for_organization(ORG)
    return total


def _run_failing_scan(
    factory: object,
    evidence_factory: object,
    *,
    clock: object,
    **kwargs: object,
):
    runtime = build_watcher_paper_runtime(
        _settings(watcher_orchestration_enabled=True),
        factory,  # type: ignore[arg-type]
        evidence_factory=evidence_factory,  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )
    return runtime.run_cycle()


@requires_postgres
def test_postgres_fail_closed_paths_do_not_mint_or_override_authority() -> None:
    """Stale, outage, tenant, lineage, expiry, source, monitor, policy, and fence."""

    from datetime import UTC, datetime, timedelta

    from app.market_contracts.errors import RegionalProviderFailureError
    from app.persistence.composition import build_postgres_watcher_store
    from app.watcher.fusion_evaluation import ExecutablePolicyAuthority
    from app.watcher.memory import FakeClock
    from app.workers.watcher_paper import new_worker_instance_id
    from app.workers.watcher_paper_targets import PaperScanTarget, list_paper_scan_targets
    from tests.support.live_market_monitor import ScriptedPerpetualSource
    from tests.support.phase5_market import consecutive_bars, trade
    from tests.support.phase6_evaluator import EvaluatorWorld, make_world, subsequent_bars
    from tests.test_watcher_paper_runtime import ORG_B, USER_B, _create_strategy, _world_factory
    from tests.test_watcher_stack_integration import _live_monitor, _stale_live_monitor

    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    account_id = uuid4()
    with factory() as session:
        _seed(session, account_id)
    source = _confirming_source()
    now = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
    scan_clock = FakeClock(now)

    def scan(evidence: object, **kwargs: object):
        return _run_failing_scan(factory, evidence, clock=scan_clock, **kwargs)

    def rescan(evidence: object, **kwargs: object):
        scan_clock.advance(20 * 60)
        return scan(evidence, **kwargs)

    def gated(monitor: object):
        def evidence_factory(
            db: object, store: object, symbol: str
        ) -> AssemblingWatcherScanEvidence:
            return AssemblingWatcherScanEvidence(
                FirstSliceEvidenceAssembler(source, replay=True),
                session=db,  # type: ignore[arg-type]
                watcher_store=store,  # type: ignore[arg-type]
                symbol=symbol,
                monitor=MarketMonitorWatcherPort(monitor),  # type: ignore[arg-type]
            )

        return evidence_factory

    stale = scan(gated(_stale_live_monitor()))
    assert stale.scans[0].reason_code == "stale_evidence"
    assert stale.scans[0].candidate_ids == ()
    assert _candidate_total(factory) == 0

    outage_source = ScriptedPerpetualSource(replay=False)
    outage_source.enqueue(RegionalProviderFailureError("connect timeout"))
    outage_monitor = _live_monitor(outage_source)
    outage_monitor.tick("BTCUSDT", now=now)
    outage = rescan(gated(outage_monitor))
    assert outage.scans[0].reason_code == "provider_outage"
    assert outage.scans[0].candidate_ids == ()

    scan_clock.advance(20 * 60)
    mismatch_at = scan_clock.now()
    fresh_source = ScriptedPerpetualSource(
        replay=False,
        bars_15m=consecutive_bars(2, last_open=mismatch_at - timedelta(minutes=15)),
    )
    fresh_source.enqueue(
        [
            trade(
                sequence=11,
                price="101234.7",
                quantity="0.01",
                buyer_is_maker=False,
                event_time=mismatch_at - timedelta(seconds=1),
                receive_at=mismatch_at - timedelta(seconds=1),
            )
        ]
    )
    fresh = _live_monitor(fresh_source, clock=scan_clock.now)
    mismatch = scan(gated(fresh))
    assert mismatch.scans[0].reason_code == "wrong_source"
    assert mismatch.scans[0].candidate_ids == ()

    missing = rescan(gated_monitor_none(source))
    assert missing.scans[0].reason_code == "missing_monitor"
    assert missing.scans[0].candidate_ids == ()

    world = make_world()
    with factory() as session:
        from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy

        seeded = list_paper_scan_targets(session, symbols=["BTCUSDT"], organization_id=ORG, limit=1)
        assert seeded
        executable = resolve_executable_strategy_policy(
            session,
            organization_id=ORG,
            strategy_version_id=seeded[0].strategy_version_id,
        )

    class _InMemoryPort:
        def load(self, command: object) -> object:
            del command
            from tests.test_watcher_paper_runtime import _snapshot_for_executable

            snapshot = _snapshot_for_executable(world, executable)
            return snapshot.model_copy(
                update={"policy_authority": ExecutablePolicyAuthority.IN_MEMORY_TEST_HELPER}
            )

    memory = rescan(lambda _db, _store, _symbol: _InMemoryPort())
    assert memory.scans[0].candidate_ids == ()
    assert _candidate_total(factory) == 0

    with factory() as session:
        targets = list_paper_scan_targets(
            session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
        )
        assert targets
        foreign = PaperScanTarget(
            organization_id=ORG_B,
            user_id=USER_B,
            strategy_id=targets[0].strategy_id,
            strategy_version_id=targets[0].strategy_version_id,
            compiled_setup_definition_id=targets[0].compiled_setup_definition_id,
            compiled_content_hash=targets[0].compiled_content_hash,
            fusion_policy_version=targets[0].fusion_policy_version,
            symbol=targets[0].symbol,
        )

    def foreign_evidence_must_not_run(*_args: object) -> object:
        pytest.fail("Foreign tenant target reached evidence acquisition")

    wrong_tenant = rescan(foreign_evidence_must_not_run, target_loader=lambda _session: (foreign,))
    # Organization-owned scheduling now rejects this target before a scan,
    # rather than relying on a later evidence/strategy tenant rejection.
    assert wrong_tenant.scans == ()
    assert wrong_tenant.reason_code == "idle"
    assert wrong_tenant.candidates_created == 0
    assert _candidate_total(factory) == 0

    with factory() as session:
        draft = _create_strategy(session)
        session.commit()
        draft_id = draft.id

    def _draft_loader(_session: object) -> tuple[PaperScanTarget, ...]:
        return (
            PaperScanTarget(
                organization_id=ORG,
                user_id=USER,
                strategy_id=draft_id,
                strategy_version_id=uuid4(),
                compiled_setup_definition_id=uuid4(),
                compiled_content_hash="ab" * 32,
                fusion_policy_version="first-slice-fusion/v1",
                symbol="BTCUSDT",
            ),
        )

    wrong_lineage = rescan(_world_factory(world), target_loader=_draft_loader)
    assert wrong_lineage.scans[0].candidate_ids == ()
    assert wrong_lineage.scans[0].reason_code in {
        "missing_canonical_evidence",
        "strategy_not_approved",
        "draft_not_executable",
    }

    base = make_world()
    later = subsequent_bars(base.trigger, count=2, high=base.trigger.high)
    expired_world = EvaluatorWorld(
        policy=base.policy,
        command=base.command,
        evidence=base.evidence.model_copy(update={"subsequent_final_15m": tuple(later)}),
        evaluated_at=base.evaluated_at,
        bars_15m=base.bars_15m,
        bars_4h=base.bars_4h,
        snapshot=base.snapshot,
        trigger=base.trigger,
    )
    expired = rescan(_world_factory(expired_world))
    assert expired.scans[0].reason_code == SetupAssessmentState.EXPIRED.value
    assert expired.scans[0].candidate_ids == ()
    assert _candidate_total(factory) == 0

    replay_monitor = PerpetualMarketMonitor(
        source,
        replay=True,
        backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=4.0),
        poll_seconds=0.01,
    )
    confirming = _evidence_factory(source, replay_monitor)
    first = rescan(confirming, monitor=replay_monitor)
    assert first.scans[0].reason_code == SetupAssessmentState.CONFIRMED_SETUP.value
    second = scan(confirming, monitor=replay_monitor)
    assert second.scans[0].candidate_ids == ()
    assert second.scans[0].reason_code in {"lease_held", "replay"}
    assert _candidate_total(factory) == 1

    store = build_postgres_watcher_store(factory)
    fence_clock = FakeClock()
    scope = "final-paper-fence"
    _acquired, held, _reason = store.claim_lease(
        organization_id=ORG,
        scan_scope=scope,
        owner_id=new_worker_instance_id("watcher-paper-1"),
        ttl_seconds=30,
        now=fence_clock.now(),
    )
    fence_clock.advance(31)
    taken, winner, reason = store.claim_lease(
        organization_id=ORG,
        scan_scope=scope,
        owner_id=new_worker_instance_id("watcher-paper-1"),
        ttl_seconds=30,
        now=fence_clock.now(),
    )
    assert taken is True
    assert reason == "claimed"
    assert (
        store.fence_is_active(
            organization_id=ORG,
            scan_scope=scope,
            owner_id=held.owner_id or "",
            fencing_token=held.fencing_token,
            now=fence_clock.now(),
        )
        is False
    )
    assert winner.fencing_token != held.fencing_token
    same_owner_without_token, _row, renew_reason = store.claim_lease(
        organization_id=ORG,
        scan_scope=scope,
        owner_id=winner.owner_id or "",
        ttl_seconds=30,
        now=fence_clock.now(),
    )
    assert same_owner_without_token is False
    assert renew_reason == "lease_held"


def gated_monitor_none(source: object):
    def evidence_factory(db: object, store: object, symbol: str) -> AssemblingWatcherScanEvidence:
        return AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(source, replay=True),
            session=db,  # type: ignore[arg-type]
            watcher_store=store,  # type: ignore[arg-type]
            symbol=symbol,
            monitor=None,
        )

    return evidence_factory
