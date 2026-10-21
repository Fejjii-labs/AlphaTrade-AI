"""Focused Nested informational alerts; fake transport only."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.candidate_alerts.contracts import CandidateAlertKind, CandidateAlertRecipient
from app.candidate_alerts.errors import CandidateAlertTenantError
from app.candidate_alerts.gateway import CandidateAlertGateway
from app.candidate_alerts.nested import NestedAlertSummary
from app.schemas.nested_continuation import NESTED_KIND
from app.signal_fusion.enums import CandidateReasonCode, CandidateState, SetupAssessmentState
from app.signal_fusion.lifecycle import in_memory_candidate_lifecycle
from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
from app.strategy_brain.assembly import assemble_nested, record_paper_link
from app.telegram_security.clock import FrozenClock
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramTransport
from tests.support.phase6_fusion import make_creation_command
from tests.support.telegram_security import BOT, CHAT, TokenSeq, enroll
from tests.test_strategy_brain_nested import nested_runtime_world, tenant_store  # noqa: F401


@pytest.fixture
def nested_world(tenant_store, monkeypatch):  # noqa: F811
    session, org, user = tenant_store
    runtime, _, template, now = nested_runtime_world(tenant_store, monkeypatch)
    from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy

    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    port = runtime._evidence_factory(session, runtime.store, "BTCUSDT")
    assembled = assemble_nested(
        port._assembler, executable=policy, organization_id=org, session=session
    )
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=now,
    )
    lifecycle = in_memory_candidate_lifecycle(now=now)
    candidate = lifecycle.create_from_confirmed_setup(
        make_creation_command(
            window=assembled.evidence_window,
            assessment=assessment,
            setup=assessment.executable_setup,
            evidence_identity=assembled.identity,
        )
    )
    transport = FakeTelegramTransport()
    clock = FrozenClock(now)
    protocol = TelegramSecurityProtocol.in_memory(
        enabled=True,
        clock=clock,
        transport=transport,
        token_factory=TokenSeq(),
    )
    _, binding = enroll(protocol, organization_id=org, user_id=user)
    gateway = CandidateAlertGateway(lifecycle=lifecycle, protocol=protocol, clock=clock)
    report = SimpleNamespace(
        discussion=SimpleNamespace(candidate=candidate, assessment=assessment),
        paper_loop_reason="risk_block",
        eligibility_state="blocked",
    )
    summary = record_paper_link(
        None,
        target=SimpleNamespace(organization_id=org, strategy_version_id=template["version_id"]),
        report=report,
        evidence=SimpleNamespace(last_assembly=lambda: (assembled, policy)),
        now=now,
    )
    assert isinstance(summary, NestedAlertSummary)
    recipient = CandidateAlertRecipient(
        organization_id=org,
        user_id=user,
        account_id=uuid4(),
        binding_id=binding,
        bot_id=BOT,
        chat_id=CHAT,
    )
    return SimpleNamespace(
        gateway=gateway,
        protocol=protocol,
        transport=transport,
        clock=clock,
        candidate=candidate,
        assessment=assessment,
        window=assembled.evidence_window,
        summary=summary,
        recipient=recipient,
    )


def project(world, **changes):
    args = {
        "candidate": world.candidate,
        "assessment": world.assessment,
        "window": world.window,
        "recipient": world.recipient,
        "nested": world.summary,
    }
    return world.gateway.project_canonical_candidate(**(args | changes))


def test_confirmed_paper_event_content_and_duplicate_episode(nested_world):
    w = nested_world
    first = project(w)
    assert first.intent.alert_kind is CandidateAlertKind.NESTED_CONFIRMED
    text = first.outbox.text
    for value in (
        "PAPER",
        "BTCUSDT",
        w.window.evidence_venue.value,
        w.window.timeframe.value,
        w.summary.stage,
        w.candidate.direction.value,
        str(w.candidate.strategy_version_id),
        str(w.summary.setup_id),
        w.summary.evidence_at.isoformat(),
        "AVAILABLE",
        "risk_block",
        "blocked",
    ):
        assert value in text
    assert "Reason summary:" in text
    assert w.summary.entry is not None and w.summary.stop is not None
    assert w.summary.target is not None
    assert format(w.summary.entry, "f") in text
    assert format(w.summary.stop, "f") in text
    assert format(w.summary.target, "f") in text
    assert "Entry:" in text and "Stop:" in text and "Target:" in text
    # Risk/decision changes and a later canonical candidate revision cannot re-alert the episode.
    updated = w.gateway.lifecycle.transition(
        organization_id=w.candidate.organization_id,
        candidate_id=w.candidate.candidate_id,
        new_state=CandidateState.PLAN_CREATED,
        reason_codes=(CandidateReasonCode.PLAN_CREATED,),
        idempotency_key="nested-plan-created",
        correlation_id=uuid4(),
    )
    duplicate = project(
        w, candidate=updated, nested=w.summary.model_copy(update={"risk_state": "allowed"})
    )
    assert duplicate.converged
    assert duplicate.outbox.outbox_id == first.outbox.outbox_id
    assert duplicate.intent.intent_id == first.intent.intent_id
    # Restart the gateway's transient intent store while keeping the existing outbox.
    w.gateway = CandidateAlertGateway(
        lifecycle=w.gateway.lifecycle, protocol=w.protocol, clock=w.clock
    )
    restarted = project(
        w, candidate=updated, nested=w.summary.model_copy(update={"risk_state": "allowed"})
    )
    assert restarted.outbox == first.outbox
    assert restarted.converged
    assert w.transport.sent == []
    assert w.gateway.execution_attempt_count == 0


@pytest.mark.parametrize("state", list(SetupAssessmentState))
def test_only_confirmed_assessments_emit(nested_world, state):
    w = nested_world
    assessment = w.assessment.model_copy(update={"state": state})
    result = project(w, assessment=assessment)
    assert (result is not None) == (state is SetupAssessmentState.CONFIRMED_SETUP)


@pytest.mark.parametrize("rule", ["fresh_closed_ohlcv", "required_observation_binding"])
def test_stale_required_evidence_suppresses_event(nested_world, rule):
    w = nested_world
    assessment = w.assessment.model_copy(
        update={
            "rule_results": tuple(
                r.model_copy(update={"passed": False}) if r.rule_id == rule else r
                for r in w.assessment.rule_results
            )
        }
    )
    assert project(w, assessment=assessment) is None
    assert w.transport.sent == []


def test_clock_staleness_and_missing_summary_suppress(nested_world):
    w = nested_world
    assert project(w, nested=None) is None
    w.clock.advance(timedelta(minutes=15))
    assert project(w) is None


def test_recipient_and_summary_tenant_isolation(nested_world):
    w = nested_world
    with pytest.raises(CandidateAlertTenantError):
        project(w, recipient=w.recipient.model_copy(update={"organization_id": uuid4()}))
    with pytest.raises(ValueError, match="does not match"):
        project(w, nested=w.summary.model_copy(update={"organization_id": uuid4()}))
    with pytest.raises(CandidateAlertTenantError):
        project(w, recipient=w.recipient.model_copy(update={"user_id": uuid4()}))


def test_paper_scan_bridge_emits_one_informational_event_and_no_actions(nested_world):
    from app.candidate_alerts.sfp import SfpAlertSummary
    from app.paper_interaction.bridge import ConfirmedScanEvidence, project_scan_report
    from app.schemas.telegram_policy import NotificationEventType
    from app.telegram_paper_agent.contracts import PaperAlertRecipient
    from app.telegram_paper_agent.gateway import TelegramPaperAgent
    from app.workers.watcher_paper import WatcherPaperScanReport

    w = nested_world
    agent = TelegramPaperAgent(
        protocol=w.protocol,
        candidates=w.gateway,
        clock=w.clock,
        enabled=True,
    )
    recipient = PaperAlertRecipient.model_validate(w.recipient.model_dump())
    report = WatcherPaperScanReport(
        organization_id=w.candidate.organization_id,
        user_id=w.recipient.user_id,
        scan_scope="nested-test",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="published",
        replayed=False,
        published=True,
        candidate_ids=(w.candidate.candidate_id,),
        kill_switch_active=False,
        nested_alert=w.summary,
        nested_strategy=True,
        discussion=SimpleNamespace(candidate=w.candidate, assessment=w.assessment, window=w.window),
    )
    # Committing the paper loop advances storage beyond the scan's old snapshot.
    current = w.gateway.lifecycle.transition(
        organization_id=w.candidate.organization_id,
        candidate_id=w.candidate.candidate_id,
        new_state=CandidateState.PLAN_CREATED,
        reason_codes=(CandidateReasonCode.PLAN_CREATED,),
        idempotency_key="bridge-paper-plan",
        correlation_id=uuid4(),
    )
    first = project_scan_report(agent, report, recipient=recipient)
    second = project_scan_report(agent, report, recipient=recipient)
    assert first.candidate_alert is not None
    assert first.candidate_alert.intent.candidate_content_hash == current.content_hash
    assert first.candidate_alert.intent.content.candidate_state is CandidateState.PLAN_CREATED
    assert first.confirmations == ()
    assert first.outbox.outbox_id == first.candidate_alert.outbox.outbox_id
    assert second.converged
    assert second.outbox.outbox_id == first.outbox.outbox_id
    assert w.transport.sent == []
    with pytest.raises(CandidateAlertTenantError, match="content hash"):
        project_scan_report(
            agent,
            report,
            recipient=recipient,
            evidence=ConfirmedScanEvidence(w.candidate, w.assessment, w.window),
        )
    assert (
        project_scan_report(
            agent,
            replace(
                report,
                published=False,
                discussion=None,
                nested_alert=None,
                status="blocked",
                reason_code="required_source_stale",
            ),
            recipient=recipient,
        )
        is None
    )
    assert w.candidate.fusion_policy_version == NESTED_KIND

    sfp = SfpAlertSummary(
        organization_id=recipient.organization_id,
        strategy_version_id=uuid4(),
        setup_id=uuid4(),
        event_type=NotificationEventType.SFP_CONFIRMED,
        occurred_at=w.clock.now(),
        evidence_at=w.clock.now(),
        symbol="BTCUSDT",
        venue=w.window.evidence_venue.value,
        timeframe=w.window.timeframe.value,
        direction=w.candidate.direction.value,
        level_type="swing_high",
        structural_level="100",
        sweep_extreme="101",
        reclaim_state="confirmed_sfp",
        quality_coverage=(),
        missing_evidence=("quality:MISSING",),
        risk_state="not_evaluated",
    )
    sfp_report = replace(
        report,
        published=False,
        candidate_ids=(),
        discussion=None,
        nested_alert=None,
        nested_strategy=False,
        sfp_strategy=True,
        sfp_alerts=(sfp,),
    )
    assert project_scan_report(agent, sfp_report, recipient=recipient) is None
    assert project_scan_report(agent, sfp_report, recipient=recipient) is None
    rows = w.protocol.store.list_outbox(organization_id=recipient.organization_id)
    assert len(rows) == 2
    assert {r.notification_event.event_type for r in rows} == {
        NotificationEventType.SETUP,
        NotificationEventType.SFP_CONFIRMED,
    }
    assert w.transport.sent == []


def test_new_episode_is_distinct_and_informational_actions_are_unavailable(nested_world):
    from app.telegram_security.actions import TelegramRemoteAction

    w = nested_world
    first = project(w)
    second = project(w, nested=w.summary.model_copy(update={"setup_id": uuid4()}))
    assert second.outbox.outbox_id != first.outbox.outbox_id
    assert not second.converged
    with pytest.raises(ValueError, match="do not offer actions"):
        w.gateway.issue_action_nonce(
            binding_id=w.recipient.binding_id,
            intent=first.intent,
            action=TelegramRemoteAction.APPROVE,
        )


def test_invalidated_candidate_does_not_emit(nested_world):
    w = nested_world
    invalidated = w.gateway.lifecycle.transition(
        organization_id=w.candidate.organization_id,
        candidate_id=w.candidate.candidate_id,
        new_state=CandidateState.INVALIDATED,
        reason_codes=(CandidateReasonCode.INVALIDATED,),
        idempotency_key="nested-invalidated",
        correlation_id=uuid4(),
    )
    assert project(w, candidate=invalidated) is None


def test_legacy_candidate_alert_content_keeps_its_serialized_shape():
    from tests.support.candidate_alerts import enabled_alert_world

    w = enabled_alert_world()
    alert = w.gateway.project_canonical_candidate(
        candidate=w.candidate,
        assessment=w.assessment,
        window=w.window,
        recipient=w.recipient,
    )
    assert "nested" not in alert.intent.content.model_dump(mode="json")
    assert "nested" not in alert.intent.model_dump(mode="json")["content"]


@pytest.mark.parametrize(
    "changes,blocked",
    [
        ({"setup_stages": ["N2"]}, True),
        ({"setup_stages": ["N1"]}, False),
        ({"minimum_quality": 0}, True),
        ({"confirmed_alerts": False}, True),
        ({"forming_alerts": False}, False),
        ({"symbol_subscriptions": ["ETHUSDT"]}, True),
        ({"strategy_subscriptions": []}, True),
    ],
)
def test_nested_uses_versioned_recipient_policy(nested_world, changes, blocked):
    from app.schemas.telegram_policy import TelegramNotificationPolicyV2
    from app.telegram_security.contracts import OutboxState

    w = nested_world
    assert w.summary.stage == "N1"
    original = w.candidate.model_dump(mode="json")
    policy = TelegramNotificationPolicyV2(**changes)
    protocol = TelegramSecurityProtocol(
        store=w.protocol.store,
        transport=w.transport,
        clock=w.clock,
        enabled=True,
        notification_policy_loader=lambda org, user: policy,
    )
    w.gateway = CandidateAlertGateway(
        lifecycle=w.gateway.lifecycle, protocol=protocol, clock=w.clock
    )
    result = project(w)
    assert (result is None) == blocked
    # Replaying a filtered episode must not revive a notification or add actions.
    assert (project(w) is None) == blocked
    rows = protocol.store.list_outbox(organization_id=w.recipient.organization_id)
    assert rows[-1].state == (OutboxState.SUPPRESSED if blocked else OutboxState.PENDING)
    assert rows[-1].notification_event.quality is None
    assert rows[-1].notification_event.strategy_id == w.candidate.strategy_version_id
    assert w.candidate.model_dump(mode="json") == original
    assert w.transport.sent == []
