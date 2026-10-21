"""Canonical SFP journal -> existing gateway/policy/outbox. Fake transport only."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.candidate_alerts.contracts import CandidateAlertRecipient
from app.candidate_alerts.errors import CandidateAlertTenantError
from app.candidate_alerts.gateway import CandidateAlertGateway
from app.candidate_alerts.sfp import SEVERITIES
from app.paper_interaction.bridge import project_scan_report
from app.schemas.nested_continuation import EvidenceAvailability
from app.schemas.telegram_policy import NotificationEventType as EventType
from app.schemas.telegram_policy import TelegramNotificationPolicyV2
from app.signal_fusion.lifecycle import in_memory_candidate_lifecycle
from app.strategy_brain.records import attach_paper_result
from app.strategy_brain.sfp_runtime.notifications import sfp_notification_summaries
from app.strategy_brain.sfp_runtime.records import record_sfp_availability
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_security.clock import FrozenClock
from app.telegram_security.contracts import OutboxState
from app.telegram_security.errors import TelegramInteractionDisabledError
from app.telegram_security.memory import InMemoryTelegramSecurityStore
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramTransport
from app.workers.watcher_paper import WatcherPaperScanReport
from tests.support.telegram_security import BOT, CHAT, enroll
from tests.test_sfp_detector import BEAR, BULL, evidence, spec
from tests.test_sfp_strategy_brain_runtime import approve, persist, runtime_world
from tests.test_sfp_strategy_brain_runtime import postgres_store as postgres_store
from tests.test_sfp_strategy_brain_runtime import store as store


class World:
    def __init__(self, org, user, now, security_store=None):
        self.clock = FrozenClock(now)
        self.transport = FakeTelegramTransport()
        self.policy = TelegramNotificationPolicyV2()
        self.protocol = TelegramSecurityProtocol(
            store=security_store or InMemoryTelegramSecurityStore(),
            enabled=True,
            clock=self.clock,
            transport=self.transport,
            notification_policy_loader=lambda org, user: self.policy,
        )
        _, binding_id = enroll(self.protocol, organization_id=org, user_id=user)
        self.recipient = CandidateAlertRecipient(
            organization_id=org,
            user_id=user,
            account_id=uuid4(),
            binding_id=binding_id,
            bot_id=BOT,
            chat_id=CHAT,
        )
        self.gateway = self.restart()

    def restart(self):
        self.protocol = TelegramSecurityProtocol(
            store=self.protocol.store,
            transport=self.transport,
            clock=self.clock,
            enabled=True,
            notification_policy_loader=lambda org, user: self.policy,
        )
        self.gateway = CandidateAlertGateway(
            lifecycle=in_memory_candidate_lifecycle(now=self.clock.now()),
            protocol=self.protocol,
            clock=self.clock,
        )
        return self.gateway

    def project(self, summary):
        return self.gateway.project_sfp_event(summary=summary, recipient=self.recipient)


def confirmed(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, observations = evidence()
    _, setup_id = persist(session, policy, bars, observations)
    now = observations[-1].observed_at
    return session, policy, setup_id, now, World(org, user, now)


def summaries(session, policy, now):
    return sfp_notification_summaries(
        session,
        organization_id=policy.organization_id,
        strategy_version_id=policy.strategy_version_id,
        now=now,
    )


@pytest.mark.parametrize("bearish", [False, True])
def test_closed_reclaim_projects_sweep_forming_and_confirmation_without_actions(store, bearish):
    session, org, user, _ = store
    policy = approve(session, org, user, spec(bearish=bearish))
    path = BEAR if bearish else BULL
    bars, observations = evidence(path[:5])
    _, setup_id = persist(session, policy, bars, observations)
    now = observations[-1].observed_at
    world = World(org, user, now)
    facts = summaries(session, policy, now)
    assert {s.event_type for s in facts} == {
        EventType.SFP_SWEEP_DETECTED,
        EventType.SFP_RECLAIM_FORMING,
    }
    agent = TelegramPaperAgent(
        protocol=world.protocol,
        candidates=world.gateway,
        clock=world.clock,
        enabled=True,
    )
    report = WatcherPaperScanReport(
        organization_id=policy.organization_id,
        user_id=world.recipient.user_id,
        scan_scope="sfp",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="confirmed",
        published=True,
        replayed=False,
        candidate_ids=(),
        kill_switch_active=False,
        sfp_strategy=True,
        sfp_alerts=facts,
    )
    recipient = PaperAlertRecipient(**world.recipient.model_dump())
    assert project_scan_report(agent, report, recipient=recipient) is None
    bars, observations = evidence(path)
    persist(session, policy, bars, observations)
    now = observations[-1].observed_at
    world.clock.advance(now - world.clock.now())
    from dataclasses import replace

    assert (
        project_scan_report(
            agent, replace(report, sfp_alerts=summaries(session, policy, now)), recipient=recipient
        )
        is None
    )
    rows = world.protocol.store.list_outbox(organization_id=policy.organization_id)
    assert len(rows) == 3
    for row in rows:
        assert row.notification_event.quality is None
        assert row.notification_event.severity == SEVERITIES[row.notification_event.event_type]
        for text in (
            "PAPER MODE",
            "BTCUSDT",
            "15m",
            "Structural level:",
            "Sweep extreme:",
            "Reclaim state:",
            str(policy.strategy_version_id),
            str(setup_id),
            "Evidence timestamp:",
            "Quality coverage:",
            "Missing evidence:",
            "Risk state:",
        ):
            assert text in row.text
    confirmed_text = next(
        row.text for row in rows if row.notification_event.event_type is EventType.SFP_CONFIRMED
    )
    confirmed_fact = next(
        fact
        for fact in summaries(session, policy, now)
        if fact.event_type is EventType.SFP_CONFIRMED
    )
    assert confirmed_fact.entry and confirmed_fact.stop and confirmed_fact.take_profit
    for label, price in (
        ("Entry:", confirmed_fact.entry),
        ("Stop:", confirmed_fact.stop),
        ("Take-profit:", confirmed_fact.take_profit),
    ):
        assert f"{label} {price}" in confirmed_text
    assert "Real trading is not authorized." in confirmed_text
    assert all(
        "Entry:" not in row.text
        for row in rows
        if row.notification_event.event_type is not EventType.SFP_CONFIRMED
    )
    assert world.transport.sent == []
    assert (
        world.gateway.store.get_by_identity_hash(rows[0].notification_event.duplicate_key) is None
    )
    assert world.protocol.execution_attempt_count == world.gateway.execution_attempt_count == 0


@pytest.mark.parametrize("tail", [(101, 102, 99, "99.5"), (101, 102, 97, 99)])
def test_invalidation_uses_stored_terminal_fact(store, tail):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, observations = evidence([*BULL[:5], tail])
    persist(session, policy, bars, observations)
    facts = summaries(session, policy, observations[-1].observed_at)
    assert EventType.SFP_INVALIDATED in {s.event_type for s in facts}


def test_clock_expiry_keeps_original_evidence_timestamp(store):
    session, org, user, _ = store
    policy = approve(session, org, user)
    bars, observations = evidence(BULL[:5])
    persist(session, policy, bars, observations)
    now = observations[-1].observed_at + timedelta(minutes=15 * spec().parameters.expiry_bars)
    record_sfp_availability(
        session,
        executable=policy,
        availability=EvidenceAvailability.MISSING,
        reason_codes=("provider_missing",),
        evaluated_at=now,
        evidence_hash="missing",
    )
    session.commit()
    (fact,) = summaries(session, policy, now)
    assert fact.event_type == EventType.SFP_EXPIRED
    assert fact.evidence_at == observations[-1].observed_at < fact.occurred_at
    assert "required_evidence:MISSING" in fact.missing_evidence


def test_existing_risk_decision_is_filterable_and_never_mandatory(store):
    session, policy, setup_id, now, world = confirmed(store)
    attach_paper_result(
        session,
        organization_id=policy.organization_id,
        setup_id=setup_id,
        candidate_id=uuid4(),
        assessment_id=uuid4(),
        now=now,
        proof=SimpleNamespace(
            eligibility_id=None,
            eligibility_state="blocked_daily_loss",
            paper_loop_reason="risk_block",
            paper_loop_stage="blocked",
            journal_trade_id=None,
            execution_command_id=None,
            trade_plan_revision_id=None,
            paper_fill_id=None,
        ),
    )
    session.commit()
    (fact,) = (
        s for s in summaries(session, policy, now) if s.event_type == EventType.SFP_BLOCKED_BY_RISK
    )
    assert fact.risk_state == "blocked_daily_loss"
    assert not fact.notification_event().mandatory_risk
    world.policy = TelegramNotificationPolicyV2(risk_alerts=False)
    row = world.project(fact)
    assert row.state == OutboxState.SUPPRESSED and row.last_error == "POLICY_EVENT_DISABLED"


@pytest.mark.parametrize(
    "policy,reason",
    [
        ({"strategy_subscriptions": []}, "POLICY_STRATEGY_SUBSCRIPTIONS"),
        ({"symbol_subscriptions": ["ETHUSDT"]}, "POLICY_SYMBOL_SUBSCRIPTIONS"),
        ({"severities": ["CRITICAL"]}, "POLICY_SEVERITIES"),
        ({"event_types": ["SFP_EXPIRED"]}, "POLICY_EVENT_TYPES"),
        ({"minimum_quality": "0"}, "POLICY_QUALITY_MISSING"),
        ({"confirmed_alerts": False}, "POLICY_CONFIRMED_DISABLED"),
        ({"quiet_hours": {"start": "00:00", "end": "23:59"}}, "POLICY_QUIET_HOURS"),
    ],
)
def test_sfp_filters_use_existing_admission_policy(store, policy, reason):
    session, strategy, _, now, world = confirmed(store)
    fact = next(
        s for s in summaries(session, strategy, now) if s.event_type == EventType.SFP_CONFIRMED
    )
    world.policy = TelegramNotificationPolicyV2(**policy)
    row = world.project(fact)
    assert row.state == OutboxState.SUPPRESSED and row.last_error == reason
    assert world.transport.sent == []


def test_restart_semantic_dedup_cooldown_and_delivery_recheck(store):
    session, policy, _, now, world = confirmed(store)
    fact = next(
        s for s in summaries(session, policy, now) if s.event_type == EventType.SFP_CONFIRMED
    )
    row = world.project(fact)
    engine = create_engine(store[3])
    with Session(engine) as restarted:
        world.restart()
        persisted = next(
            s for s in summaries(restarted, policy, now) if s.event_type == EventType.SFP_CONFIRMED
        )
        assert world.project(persisted).outbox_id == row.outbox_id
    engine.dispose()
    world.policy = TelegramNotificationPolicyV2(cooldown_seconds=120)
    new = world.project(fact.model_copy(update={"setup_id": uuid4()}))
    assert new.state == OutboxState.SUPPRESSED and new.last_error == "POLICY_COOLDOWN"
    world.policy = TelegramNotificationPolicyV2(event_types=())
    results = world.protocol.deliver_pending()
    assert results[0].outbox.state == OutboxState.SUPPRESSED
    assert results[0].outbox.attempt == 0 and world.transport.sent == []


def test_scope_freshness_binding_and_disarmed_gates(store):
    session, policy, _, now, world = confirmed(store)
    facts = summaries(session, policy, now)
    assert (
        sfp_notification_summaries(
            session,
            organization_id=uuid4(),
            strategy_version_id=policy.strategy_version_id,
            now=now,
        )
        == ()
    )
    assert summaries(session, policy, now + timedelta(days=1)) == ()
    with pytest.raises(CandidateAlertTenantError):
        world.project(facts[0].model_copy(update={"organization_id": uuid4()}))
    wrong = world.recipient.model_copy(update={"user_id": uuid4()})
    with pytest.raises(CandidateAlertTenantError):
        world.gateway.project_sfp_event(summary=facts[0], recipient=wrong)
    protocol = TelegramSecurityProtocol(
        store=world.protocol.store,
        clock=world.clock,
        transport=world.transport,
        enabled=False,
    )
    disabled = CandidateAlertGateway.in_memory(enabled=False, now=now, protocol=protocol)
    with pytest.raises(TelegramInteractionDisabledError):
        disabled.project_sfp_event(summary=facts[0], recipient=world.recipient)


@pytest.mark.parametrize(
    "forming,risk_block,expected",
    [
        (True, False, {EventType.SFP_SWEEP_DETECTED, EventType.SFP_RECLAIM_FORMING}),
        (False, False, {EventType.SFP_CONFIRMED}),
        (False, True, {EventType.SFP_CONFIRMED, EventType.SFP_BLOCKED_BY_RISK}),
    ],
)
def test_watcher_projects_canonical_sfp_history_after_risk_and_replays_without_actions(
    postgres_store,
    forming,
    risk_block,
    expected,
):
    runtime, restart, _, now = runtime_world(
        postgres_store,
        forming=forming,
        risk_block=risk_block,
    )
    report = runtime.run_cycle()
    assert report.scans and report.scans[0].sfp_strategy, report
    scan = report.scans[0]
    assert {s.event_type for s in scan.sfp_alerts} == expected
    if risk_block:
        blocked = next(s for s in scan.sfp_alerts if s.event_type == EventType.SFP_BLOCKED_BY_RISK)
        assert "blocked_daily_loss" in blocked.risk_reasons
    from app.persistence.composition import build_postgres_telegram_security_store

    world = World(
        scan.organization_id,
        scan.user_id,
        now,
        security_store=build_postgres_telegram_security_store(postgres_store[3]),
    )
    agent = TelegramPaperAgent(
        protocol=world.protocol,
        candidates=world.gateway,
        clock=world.clock,
        enabled=True,
    )
    recipient = PaperAlertRecipient(**world.recipient.model_dump())
    assert project_scan_report(agent, scan, recipient=recipient) is None
    first = world.protocol.store.list_outbox(organization_id=scan.organization_id)
    assert len(first) == len(expected)
    world.restart()
    agent = TelegramPaperAgent(
        protocol=world.protocol,
        candidates=world.gateway,
        clock=world.clock,
        enabled=True,
    )
    replay = restart().run_cycle()
    assert project_scan_report(agent, replay.scans[0], recipient=recipient) is None
    assert world.protocol.store.list_outbox(organization_id=scan.organization_id) == first
    assert world.transport.sent == []
    assert world.protocol.execution_attempt_count == 0
