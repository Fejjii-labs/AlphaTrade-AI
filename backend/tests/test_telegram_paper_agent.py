"""Paper-mode Telegram interaction layer.

Telegram stays disabled by default. Messages are never trading authority.
Mutating paper actions require identity-bound confirmation.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.deployment_safety import deployment_posture
from app.main import create_app
from app.signal_fusion.enums import CandidateState
from app.telegram_paper_agent.confirmation import format_paper_confirmation
from app.telegram_paper_agent.contracts import (
    DiscussionIntent,
    JournalOutcomeView,
    LearningSummaryView,
    PaperTradeStatusView,
    StrategyDraftView,
    WatcherScanNotice,
)
from app.telegram_paper_agent.errors import PaperTelegramDisabledError, PaperTelegramTenantError
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_paper_agent.memory import InMemoryPaperContext
from app.telegram_security.actions import (
    APPROVE_EXECUTES,
    CLOSE_AVAILABLE,
    TELEGRAM_EXECUTION_ENTRY_PATHS,
    TelegramRemoteAction,
)
from app.telegram_security.contracts import ActionReceiptState, OutboxState
from app.telegram_security.errors import TelegramSecurityError, TelegramSecurityReason
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.rate_limit import RateLimitPolicy
from app.watcher.contracts import EvaluationMode, ScanRequest, WatcherRuntimeConfig
from app.watcher.memory import (
    FakeClock,
    InMemoryWatcherStore,
    ScriptedEvaluationBoundary,
    SideEffectProbe,
)
from app.watcher.orchestrator import WatcherOrchestrator
from tests.support.phase6_fusion import ORG_ID
from tests.support.telegram_paper_agent import (
    PaperAgentWorld,
    assert_no_internal_ids,
    enabled_paper_agent_world,
)
from tests.support.telegram_security import (
    OTHER_ORG,
    OTHER_USER,
    callback_identity,
    inbound_callback,
    inbound_message,
    message_identity,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src/app/telegram_paper_agent"
FORBIDDEN_SNIPPETS = (
    "execute_paper_plan",
    "place_paper_order",
    "ExecutionService",
    "enable_real_trading = True",
    "real_trading_enabled = True",
)


def test_package_has_no_execution_path() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text


def test_telegram_remains_disabled_by_default() -> None:
    settings = Settings()
    assert settings.telegram_interaction_enabled is False
    assert settings.telegram_alerts_enabled is False
    posture = deployment_posture(settings)
    assert posture["telegram_interaction_enabled"] is False
    assert APPROVE_EXECUTES is False
    assert CLOSE_AVAILABLE is False
    assert TELEGRAM_EXECUTION_ENTRY_PATHS == ()


def test_create_app_does_not_mount_telegram_webhook() -> None:
    app = create_app(Settings())
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("telegram" in path and "webhook" in path for path in paths)
    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["telegram_interaction_enabled"] is False


def test_disabled_agent_refuses_projection() -> None:
    world = enabled_paper_agent_world()
    agent = TelegramPaperAgent.in_memory(enabled=False)
    with pytest.raises(PaperTelegramDisabledError):
        agent.project_candidate_alert(
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
            recipient=world.recipient,
        )


def _confirmed_notice(world: PaperAgentWorld) -> WatcherScanNotice:
    return WatcherScanNotice(
        organization_id=world.recipient.organization_id,
        user_id=world.recipient.user_id,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="confirmed_setup",
        published=True,
        candidate_ids=(world.candidate.candidate_id,),
        request_hash="b" * 64,
        lineage_id=uuid4(),
    )


def test_watcher_confirmed_setup_dedupes_and_allows_discussion() -> None:
    world = enabled_paper_agent_world()
    notice = _confirmed_notice(world)
    first = world.agent.project_watcher_notice(
        notice=notice,
        recipient=world.recipient,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert first is not None
    assert first.converged is False
    second = world.agent.project_watcher_notice(
        notice=notice,
        recipient=world.recipient,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert second is not None
    assert second.converged is True
    assert second.outbox.outbox_id == first.outbox.outbox_id
    # One message per setup: the alert itself. No footer, no identity block.
    assert first.candidate_alert is not None
    assert first.outbox.outbox_id == first.candidate_alert.outbox.outbox_id
    assert first.confirmations == ()
    attempts = world.protocol.deliver_pending()
    assert [item.accepted for item in attempts] == [True]
    sent = attempts[0].outbox.text
    assert_no_internal_ids(sent)
    for jargon in ("CONFIRMED_SETUP", "candidate_id", "canonical", "minted", "I confirm"):
        assert jargon not in sent
    discussed = world.agent.handle_inbound_message(
        identity=message_identity(update_id=40, message_id="ask-1"),
        inbound=inbound_message(),
        text="Explain the candidate evidence and risk",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert discussed.executed is False
    assert discussed.candidate_minted is False
    assert discussed.reply_outbox is not None
    # A reply to the setup alert discusses the same candidate thread.
    assert discussed.thread.thread_id == first.thread.thread_id
    reply = discussed.reply_outbox.text.lower()
    assert (
        "telegram cannot" in reply
        or "candidate_id" in reply
        or "riskengine" in reply
        or "evidence_window_hash" in reply
        or "assessment_state" in reply
    )


def test_empty_watcher_scan_is_not_an_alert() -> None:
    world = enabled_paper_agent_world()
    notice = WatcherScanNotice(
        organization_id=world.recipient.organization_id,
        user_id=world.recipient.user_id,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="no_setup",
        published=False,
        candidate_ids=(),
        request_hash="c" * 64,
    )
    assert world.agent.project_watcher_notice(notice=notice, recipient=world.recipient) is None


def test_watcher_blocked_alert_is_durable() -> None:
    world = enabled_paper_agent_world()
    notice = WatcherScanNotice(
        organization_id=world.recipient.organization_id,
        user_id=world.recipient.user_id,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="blocked",
        reason_code="stale_evidence",
        published=False,
        request_hash="d" * 64,
        lineage_id=uuid4(),
    )
    first = world.agent.project_watcher_notice(notice=notice, recipient=world.recipient)
    assert first is not None
    assert first.outbox.text == (
        "BTCUSDT scan paused\n"
        "\u2022 Reason: market data is out of date\n"
        "\u2022 No setup was created and no trade was placed."
    )
    second = world.agent.project_watcher_notice(notice=notice, recipient=world.recipient)
    assert second is not None and second.converged is True


def test_identity_bound_reject_confirmation_does_not_execute() -> None:
    world = enabled_paper_agent_world()
    projection = world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    reject = next(row for row in projection.confirmations if row.action.value == "REJECT")
    text = f"I confirm reject\n{format_paper_confirmation(reject)}"
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=50, message_id="confirm-reject"),
        inbound=inbound_message(),
        text=text,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.executed is False
    assert result.execution_attempted is False
    assert result.candidate_result is not None
    assert result.candidate_result.candidate_mutated is True
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.REJECTED
    assert world.agent.execution_attempt_count == 0


def test_watcher_setup_offers_no_telegram_action_and_forged_identity_fails_closed() -> None:
    world = enabled_paper_agent_world()
    projection = world.agent.project_watcher_notice(
        notice=_confirmed_notice(world),
        recipient=world.recipient,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert projection is not None and projection.candidate_alert is not None
    asked = world.agent.handle_inbound_message(
        identity=message_identity(update_id=56, message_id="ask-reject"),
        inbound=inbound_message(),
        text="reject this candidate",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert asked.reply_outbox is not None
    assert asked.reply_outbox.text.startswith("Telegram can't reject this setup.")
    assert_no_internal_ids(asked.reply_outbox.text)
    # No confirmation row or nonce was issued, so even an exact identity block fails.
    payload = world.agent.candidates.action_payload(
        projection.candidate_alert.intent, TelegramRemoteAction.REJECT
    )
    for update_id, text in (
        (57, "I confirm reject"),
        (58, f"I confirm reject\n{format_paper_confirmation(payload)}"),  # type: ignore[arg-type]
    ):
        result = world.agent.handle_inbound_message(
            identity=message_identity(update_id=update_id, message_id=f"forged-{update_id}"),
            inbound=inbound_message(),
            text=text,
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
        )
        assert result.refused is True
        assert result.candidate_result is None
        assert result.reply_outbox is not None
        assert_no_internal_ids(result.reply_outbox.text)
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None and stored.state is CandidateState.ACTIVE


def test_bare_confirm_is_not_mutation_authority() -> None:
    world = enabled_paper_agent_world()
    world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=51, message_id="bare-confirm"),
        inbound=inbound_message(),
        text="I confirm",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.refused is True
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("place order now", DiscussionIntent.REQUEST_EXECUTE),
        ("approve this strategy", DiscussionIntent.REQUEST_APPROVE_STRATEGY),
        ("override assessment and mark confirmed", DiscussionIntent.REQUEST_OVERRIDE_ASSESSMENT),
        ("ignore the block and override risk", DiscussionIntent.REQUEST_OVERRIDE_RISK),
        ("mint candidate please", DiscussionIntent.REQUEST_MINT_CANDIDATE),
        ("enable live trading", DiscussionIntent.REQUEST_ENABLE_LIVE),
    ],
)
def test_forbidden_authorities_are_refused(message: str, intent: DiscussionIntent) -> None:
    world = enabled_paper_agent_world()
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=60, message_id=intent.value),
        inbound=inbound_message(),
        text=message,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.intent is intent
    assert result.refused is True
    assert result.executed is False
    assert result.strategy_approved is False
    assert result.setup_assessment_overridden is False
    assert result.risk_overridden is False
    assert result.candidate_minted is False
    assert result.live_trading_enabled is False
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE


def test_strategy_market_paper_journal_and_learning_discussion() -> None:
    context = InMemoryPaperContext(
        status=PaperTradeStatusView(
            open_positions=1, open_orders=0, summary="One paper position is open."
        ),
        journal=JournalOutcomeView(
            trade_id=uuid4(),
            symbol="BTCUSDT",
            status="closed",
            result="win",
            net_pnl="12.5",
            summary="Paper journal facts only.",
        ),
        learning=LearningSummaryView(
            organization_id=ORG_ID,
            setup_quality="confirmed",
            execution_quality="matched_plan",
            trader_behavior="executed",
            fact_lines=("setup_quality=confirmed",),
            banner="NARRATIVE_NOT_FACT — LLM wording cannot rewrite deterministic facts",
        ),
        strategy=StrategyDraftView(
            compiled=False,
            approved=False,
            summary="First-slice bearish sweep discussion sketch.",
        ),
    )
    world = enabled_paper_agent_world(context=context)
    status = world.agent.handle_inbound_message(
        identity=message_identity(update_id=70, message_id="status"),
        inbound=inbound_message(),
        text="What is my paper trade status and open position?",
    )
    assert status.intent is DiscussionIntent.PAPER_TRADE_STATUS
    assert status.reply_outbox is not None
    assert "\u2022 Live trading: off" in status.reply_outbox.text
    journal = world.agent.handle_inbound_message(
        identity=message_identity(update_id=71, message_id="journal"),
        inbound=inbound_message(),
        text="Show the journal outcome",
    )
    assert journal.intent is DiscussionIntent.JOURNAL_OUTCOME
    assert journal.reply_outbox is not None
    assert journal.reply_outbox.text.startswith(
        "BTCUSDT paper trade closed\n\u2022 Result: Win\n\u2022 Net P&L: 12.5"
    )
    learning = world.agent.handle_inbound_message(
        identity=message_identity(update_id=72, message_id="learn"),
        inbound=inbound_message(),
        text="Give me a learning summary",
    )
    assert learning.intent is DiscussionIntent.LEARNING_SUMMARY
    assert learning.reply_outbox is not None
    assert "\u2022 Setup quality: Confirmed" in learning.reply_outbox.text
    assert "LLM wording cannot rewrite deterministic facts." in learning.reply_outbox.text
    for reply in (status, journal, learning):
        assert reply.reply_outbox is not None
        assert_no_internal_ids(reply.reply_outbox.text)
        assert "_" not in reply.reply_outbox.text
    market = world.agent.handle_inbound_message(
        identity=message_identity(update_id=73, message_id="mkt"),
        inbound=inbound_message(),
        text="What is the market context?",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert market.intent is DiscussionIntent.MARKET_CONTEXT
    strategy = world.agent.handle_inbound_message(
        identity=message_identity(update_id=74, message_id="strat"),
        inbound=inbound_message(),
        text="Let's discuss this strategy idea",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert strategy.intent is DiscussionIntent.STRATEGY_DISCUSSION
    assert strategy.strategy_approved is False


def test_exact_replay_converges_and_conflict_fails_closed() -> None:
    world = enabled_paper_agent_world()
    identity = message_identity(update_id=80, message_id="replay")
    first = world.agent.handle_inbound_message(
        identity=identity,
        inbound=inbound_message(),
        text="paper status please",
    )
    second = world.agent.handle_inbound_message(
        identity=identity,
        inbound=inbound_message(),
        text="paper status please",
    )
    assert second.telegram_outcome.replayed is True
    assert second.telegram_outcome.receipt.receipt_id == first.telegram_outcome.receipt.receipt_id
    with pytest.raises(TelegramSecurityError) as exc:
        world.agent.handle_inbound_message(
            identity=identity,
            inbound=inbound_message(),
            text="place order now",
        )
    assert exc.value.reason is TelegramSecurityReason.REPLAY_CONFLICT


def test_cross_tenant_recipient_is_rejected() -> None:
    world = enabled_paper_agent_world()
    other = world.recipient.model_copy(update={"organization_id": OTHER_ORG, "user_id": OTHER_USER})
    with pytest.raises(PaperTelegramTenantError):
        world.agent.project_candidate_alert(
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
            recipient=other,
        )


def test_delivery_retry_and_ack() -> None:
    world = enabled_paper_agent_world()
    world.transport.fail_next(1)
    projection = world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    first = world.protocol.deliver_pending(limit=10)
    assert any(not item.accepted and item.retryable for item in first)
    second = world.protocol.deliver_pending(limit=10)
    accepted = [item for item in second if item.accepted]
    assert accepted
    acked = world.protocol.acknowledge_delivery(
        outbox_id=accepted[0].outbox.outbox_id,
        transport_message_id=accepted[0].transport_message_id or "tg-1",
    )
    assert acked.state is OutboxState.ACKNOWLEDGED
    assert projection.outbox.idempotency_key.startswith("paper-thread:")


def test_inbound_rate_limit() -> None:
    world = enabled_paper_agent_world()
    world.protocol._rate.policy = RateLimitPolicy(callback_per_user=2, callback_per_chat=2)
    world.agent.handle_inbound_message(
        identity=message_identity(update_id=90, message_id="rl-1"),
        inbound=inbound_message(),
        text="paper status",
    )
    with pytest.raises(TelegramSecurityError) as exc:
        world.agent.handle_inbound_message(
            identity=message_identity(update_id=91, message_id="rl-2"),
            inbound=inbound_message(),
            text="paper status again",
        )
    assert exc.value.reason is TelegramSecurityReason.RATE_LIMITED


def test_callback_approve_is_authorization_intent_only() -> None:
    world = enabled_paper_agent_world()
    projection = world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    approve = next(row for row in projection.confirmations if row.action.value == "APPROVE")
    issued = world.agent.candidates.issue_action_nonce(
        binding_id=world.binding_id,
        intent=projection.candidate_alert.intent,  # type: ignore[union-attr]
        action=approve.action,
    )
    result = world.agent.handle_callback(
        identity=callback_identity(update_id=100, callback_query_id="cb-approve"),
        nonce_token=issued.token,
        presented_payload=world.agent.candidates.action_payload(
            projection.candidate_alert.intent,
            approve.action,  # type: ignore[union-attr]
        ),
        inbound=inbound_callback(),
    )
    assert result.candidate_result is not None
    assert result.candidate_result.candidate_mutated is False
    assert result.telegram_outcome.authorization_intent is not None
    assert result.telegram_outcome.authorization_intent.executes is False
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE


def test_watcher_persist_and_notify_stays_blocked() -> None:
    orchestrator = WatcherOrchestrator(
        store=InMemoryWatcherStore(),
        evaluator=ScriptedEvaluationBoundary(),
        clock=FakeClock(),
        config=WatcherRuntimeConfig(
            enabled=True, lease_ttl_seconds=30, heartbeat_stale_after_seconds=90
        ),
        side_effects=SideEffectProbe(),
    )
    request = ScanRequest(
        organization_id=ORG_ID,
        principal_id=None,
        scan_scope="org:btcusdt",
        policy_id=uuid4(),
        policy_version=1,
        policy_content_hash="e" * 64,
        watchlist_item_ids=(uuid4(),),
        timeframe="15m",
        idempotency_key="notify-disabled",
    )
    result = orchestrator.evaluate_manual(request, mode=EvaluationMode.PERSIST_AND_NOTIFY)
    assert result.reason_code == "notify_disabled"
    assert result.persisted is False


def test_protocol_private_message_requires_binding() -> None:
    protocol = TelegramSecurityProtocol.in_memory(enabled=True)
    outcome = protocol.receive_private_message(
        identity=message_identity(),
        inbound=inbound_message(),
        text="hello",
    )
    assert outcome.receipt.state is ActionReceiptState.REJECTED
    assert outcome.reason_code == TelegramSecurityReason.BINDING_NOT_FOUND.value


def test_named_reject_confirmation_without_footer() -> None:
    world = enabled_paper_agent_world()
    world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=52, message_id="named-reject"),
        inbound=inbound_message(),
        text="I confirm reject",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.executed is False
    assert result.candidate_result is not None
    assert result.candidate_result.candidate_mutated is True
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.REJECTED


def test_approve_via_text_is_authorization_intent_only() -> None:
    world = enabled_paper_agent_world()
    projection = world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    approve = next(row for row in projection.confirmations if row.action.value == "APPROVE")
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=53, message_id="confirm-approve"),
        inbound=inbound_message(),
        text=f"I confirm approve\n{format_paper_confirmation(approve)}",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.executed is False
    assert result.candidate_result is not None
    assert result.candidate_result.candidate_mutated is False
    assert result.telegram_outcome.authorization_intent is not None
    assert result.telegram_outcome.authorization_intent.executes is False
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE


def test_reject_confirmation_replay_does_not_reapply() -> None:
    world = enabled_paper_agent_world()
    projection = world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    reject = next(row for row in projection.confirmations if row.action.value == "REJECT")
    text = f"I confirm reject\n{format_paper_confirmation(reject)}"
    identity = message_identity(update_id=54, message_id="reject-replay")
    first = world.agent.handle_inbound_message(
        identity=identity,
        inbound=inbound_message(),
        text=text,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    second = world.agent.handle_inbound_message(
        identity=identity,
        inbound=inbound_message(),
        text=text,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert first.candidate_result is not None
    assert first.candidate_result.candidate_mutated is True
    assert second.telegram_outcome.replayed is True
    assert second.candidate_result is None
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.REJECTED


def test_request_skip_represents_identity() -> None:
    world = enabled_paper_agent_world()
    world.agent.project_candidate_alert(
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
        recipient=world.recipient,
    )
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=55, message_id="ask-skip"),
        inbound=inbound_message(),
        text="skip this candidate",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert result.intent is DiscussionIntent.REQUEST_SKIP
    assert result.executed is False
    assert result.reply_outbox is not None
    assert "confirmation-gated" in result.reply_outbox.text
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None
    assert stored.state is CandidateState.ACTIVE


def test_journal_outcome_alert_dedupes() -> None:
    world = enabled_paper_agent_world()
    view = JournalOutcomeView(
        trade_id=uuid4(),
        symbol="BTCUSDT",
        status="closed",
        result="win",
        net_pnl="3.25",
        summary="Paper journal facts only.",
    )
    first = world.agent.project_journal_outcome(view=view, recipient=world.recipient)
    second = world.agent.project_journal_outcome(view=view, recipient=world.recipient)
    assert second.converged is True
    assert second.outbox.outbox_id == first.outbox.outbox_id
    assert second.outbox.idempotency_key.startswith("paper-notify:")
