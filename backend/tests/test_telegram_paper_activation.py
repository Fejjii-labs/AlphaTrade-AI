"""Controlled paper Telegram activation. No network delivery and no arming by default."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.candidate_alerts.gateway import CandidateAlertGateway
from app.core.config import Settings, get_settings
from app.main import create_app
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import CandidateState
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.lifecycle import CandidateLifecycleService, in_memory_candidate_lifecycle
from app.telegram_activation import plan_rollback
from app.telegram_activation.__main__ import main as activation_cli
from app.telegram_activation.controller import TelegramPaperActivation
from app.telegram_activation.cursor import InMemoryActivationCursorStore
from app.telegram_activation.errors import TelegramActivationError
from app.telegram_activation.intake import (
    ParsedTelegramUpdate,
    RecordedUpdateSource,
    RefusingTelegramUpdateSource,
    parse_telegram_update,
)
from app.telegram_activation.policy import PAPER_ACTIVATION_BACKOFF
from app.telegram_activation.preflight import defaults_are_safe
from app.telegram_activation.transport import (
    GuardedTelegramTransport,
    HttpTelegramTransport,
    InMemorySendLedger,
    OutboundRateLimiter,
)
from app.telegram_activation.webhook import mount_paper_telegram_webhook
from app.telegram_paper_agent.contracts import PaperAlertRecipient, WatcherScanNotice
from app.telegram_paper_agent.errors import PaperTelegramDisabledError, PaperTelegramTenantError
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_security.backoff import RATE_LIMITED_ERROR
from app.telegram_security.clock import FrozenClock
from app.telegram_security.contracts import ChatType, OutboxState
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramBehavior, FakeTelegramTransport
from app.workers.watcher_paper import main as watcher_main
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_fusion import (
    ACCOUNT_ID,
    ORG_ID,
    USER_ID,
    make_assessment,
    make_creation_command,
    make_evidence_window,
)
from tests.support.telegram_security import BOT, CHAT, OTHER_ORG, OTHER_USER, TokenSeq, enroll

PACKAGE = Path(__file__).resolve().parents[1] / "src/app/telegram_activation"
SECRET = "w" * 32


@dataclass
class ActivationWorld:
    controller: TelegramPaperActivation
    agent: TelegramPaperAgent
    protocol: TelegramSecurityProtocol
    transport: FakeTelegramTransport
    clock: FrozenClock
    lifecycle: CandidateLifecycleService
    candidate: Candidate
    assessment: SetupAssessment
    window: CanonicalEvidenceWindowV1
    recipient: PaperAlertRecipient
    settings: Settings
    source: RecordedUpdateSource | None


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "telegram_interaction_enabled": True,
        "telegram_paper_activation_armed": True,
        "telegram_inbound_mode": "polling",
        "telegram_alerts_enabled": False,
        "automatic_telegram_delivery_enabled": False,
        "telegram_network_permitted": False,
        "watcher_orchestration_enabled": False,
        "market_watcher_enabled": False,
    }
    payload.update(overrides)
    return Settings(**payload)


def _world(
    *,
    settings: Settings | None = None,
    per_chat: int = 20,
    updates: tuple[ParsedTelegramUpdate, ...] = (),
    inbound_mode: str = "polling",
) -> ActivationWorld:
    resolved = settings or _settings(telegram_inbound_mode=inbound_mode)
    clock = FrozenClock(EVALUATED_AT)
    fake = FakeTelegramTransport()
    protocol = TelegramSecurityProtocol.in_memory(
        enabled=True,
        clock=clock,
        transport=GuardedTelegramTransport(
            inner=fake,
            limiter=OutboundRateLimiter(
                clock,
                per_chat=per_chat,
                window=timedelta(seconds=60),
            ),
            ledger=InMemorySendLedger(),
            organization_id=ORG_ID,
            bot_id=BOT,
            chat_id=CHAT,
            clock=clock,
        ),
        token_factory=TokenSeq(),
        retry_backoff=PAPER_ACTIVATION_BACKOFF,
    )
    _, binding_id = enroll(protocol, organization_id=ORG_ID, user_id=USER_ID)
    lifecycle = in_memory_candidate_lifecycle(now=EVALUATED_AT)
    gateway = CandidateAlertGateway(lifecycle=lifecycle, protocol=protocol, clock=clock)
    agent = TelegramPaperAgent(
        protocol=protocol,
        candidates=gateway,
        clock=clock,
        enabled=True,
    )
    window = make_evidence_window()
    assessment = make_assessment(window)
    candidate = lifecycle.create_from_confirmed_setup(
        make_creation_command(window=window, assessment=assessment)
    )
    recipient = PaperAlertRecipient(
        organization_id=ORG_ID,
        user_id=USER_ID,
        account_id=ACCOUNT_ID,
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    source: RecordedUpdateSource | None = None
    if resolved.telegram_inbound_mode.value == "polling":
        source = RecordedUpdateSource(updates)
    controller = TelegramPaperActivation(
        settings=resolved,
        agent=agent,
        recipient=recipient,
        clock=clock,
        outbound_ready=True,
        update_source=source,
    )
    return ActivationWorld(
        controller=controller,
        agent=agent,
        protocol=protocol,
        transport=fake,
        clock=clock,
        lifecycle=lifecycle,
        candidate=candidate,
        assessment=assessment,
        window=window,
        recipient=recipient,
        settings=resolved,
        source=source,
    )


def _notice(candidate: Candidate) -> WatcherScanNotice:
    return WatcherScanNotice(
        organization_id=ORG_ID,
        user_id=USER_ID,
        scan_scope="org:btcusdt",
        symbol="BTCUSDT",
        status="published",
        reason_code="confirmed_setup",
        published=True,
        candidate_ids=(candidate.candidate_id,),
        request_hash="ab" * 32,
        lineage_id=uuid4(),
    )


def _project(world: ActivationWorld):
    return world.agent.project_watcher_notice(
        notice=_notice(world.candidate),
        recipient=world.recipient,
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )


def test_defaults_are_disarmed_and_create_app_has_no_webhook() -> None:
    settings = Settings()
    assert defaults_are_safe(settings)
    assert settings.watcher_orchestration_enabled is False
    app = create_app()
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not any("telegram" in path and "webhook" in path for path in paths)
    assert app.state.telegram_webhook_mounted is False
    assert app.state.telegram_paper_activation is None
    client = TestClient(app)
    body = client.get("/health/telegram-paper-activation").json()
    assert body["verdict"] == "NOT_ARMED"
    assert body["runtime_armable"] is False
    assert body["telegram_network_permitted"] is False


def test_preflight_cli_disabled_and_rollback_refuses_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.telegram_activation.__main__.get_settings",
        lambda: Settings(),
    )
    get_settings.cache_clear()
    assert activation_cli(["preflight", "--expect-disabled"]) == 0
    assert activation_cli(["rollback", "--apply"]) == 2
    assert activation_cli(["rollback"]) == 0
    plan = plan_rollback()
    assert plan.disables_watcher is False
    assert plan.enables_live_trading is False
    text = " ".join(step.action for step in plan.steps)
    assert "TELEGRAM_PAPER_ACTIVATION_ARMED" in text
    assert "ENABLE_REAL_TRADING" in text
    assert all(step.mutates_environment is False for step in plan.steps)
    assert all(step.mutates_deployment is False for step in plan.steps)


def test_watcher_main_does_not_install_telegram_and_package_cannot_trade() -> None:
    source = inspect.getsource(watcher_main)
    assert "telegram_activation" not in source
    assert "telegram_scan_hook" not in source
    package = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
    assert "ExecutionService" not in package
    assert "create_from_confirmed_setup" not in package
    world = _world()
    with pytest.raises(TelegramActivationError) as exc:
        world.controller.paper_scan_hook()
    assert exc.value.reason == "not_armed"
    world.controller.arm()
    hook = world.controller.paper_scan_hook()
    assert callable(hook)
    world.controller.disarm()
    assert world.controller.armed is False
    with pytest.raises(TelegramActivationError):
        world.controller.deliver()


def test_activation_path_is_idempotent_bounded_and_confirmation_gated() -> None:
    world = _world()
    world.controller.arm()
    assert world.controller.preflight().runtime_armable is True
    assert world.controller.preflight().tenant_isolated is True
    assert world.controller.preflight().confirmation_identity_required is True
    first = _project(world)
    second = _project(world)
    assert second.converged is True
    assert first.outbox.idempotency_key == second.outbox.idempotency_key
    world.transport.fail_next(1)
    delivered = world.controller.deliver(limit=10)
    assert any(not item.accepted for item in delivered)
    assert any(item.outbox.state is OutboxState.ACKNOWLEDGED for item in delivered)
    sent_after_failure = world.transport.send_count
    assert world.controller.deliver(limit=10) == []
    assert world.transport.send_count == sent_after_failure
    world.clock.advance(timedelta(seconds=5))
    retried = world.controller.deliver(limit=10)
    assert any(item.accepted and item.outbox.state is OutboxState.ACKNOWLEDGED for item in retried)
    assert world.transport.send_count == sent_after_failure + 1
    again = world.controller.deliver(limit=10)
    assert again == []
    audits = [event.event_type for event in world.protocol.store.list_audits()]
    assert "activation_armed" in audits

    other = world.recipient.model_copy(update={"organization_id": OTHER_ORG, "user_id": OTHER_USER})
    with pytest.raises(PaperTelegramTenantError):
        world.agent.project_candidate_alert(
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
            recipient=other,
        )

    before = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert before is not None and before.state is CandidateState.ACTIVE
    for text in (
        "mint a new candidate",
        "override risk and ignore the block",
        "activate strategy now",
        "place order now",
        "enable live trading",
        "I confirm",
    ):
        world.agent.handle_inbound_message(
            identity=_identity(update_id=len(text), message_id=f"m-{len(text)}"),
            inbound=_inbound(),
            text=text,
            candidate=world.candidate,
            assessment=world.assessment,
            window=world.window,
        )
    stored = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert stored is not None and stored.state is CandidateState.ACTIVE
    assert world.agent.execution_attempt_count == 0
    assert world.agent.candidate_mint_attempt_count == 0
    assert world.agent.live_trading_enable_attempt_count == 0
    approved = world.agent.handle_inbound_message(
        identity=_identity(update_id=40, message_id="approve"),
        inbound=_inbound(),
        text="I confirm approve",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert approved.telegram_outcome.authorization_intent is not None
    assert approved.telegram_outcome.authorization_intent.executes is False
    still_active = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert still_active is not None and still_active.state is CandidateState.ACTIVE
    rejected = world.agent.handle_inbound_message(
        identity=_identity(update_id=41, message_id="reject"),
        inbound=_inbound(),
        text="I confirm reject",
        candidate=world.candidate,
        assessment=world.assessment,
        window=world.window,
    )
    assert rejected.candidate_result is not None
    assert rejected.candidate_result.candidate_mutated is True
    final = world.lifecycle.get_by_candidate_id(ORG_ID, world.candidate.candidate_id)
    assert final is not None and final.state is CandidateState.REJECTED
    discussion_audits = [event.event_type for event in world.protocol.store.list_audits()]
    assert "message_applied" in discussion_audits


def test_backoff_reaches_dead_letter_and_expired_claim_recovers_once() -> None:
    world = _world()
    world.controller.arm()
    _project(world)
    world.transport.behavior = FakeTelegramBehavior.FAIL
    first = world.controller.deliver(limit=10)
    assert first
    assert all(item.outbox.state is OutboxState.RETRYABLE for item in first)
    world.clock.advance(timedelta(seconds=5))
    second = world.controller.deliver(limit=10)
    assert all(item.outbox.state is OutboxState.RETRYABLE for item in second)
    world.clock.advance(timedelta(seconds=30))
    third = world.controller.deliver(limit=10)
    assert third
    assert all(item.outbox.state is OutboxState.DEAD_LETTER for item in third)
    world.clock.advance(timedelta(seconds=120))
    assert world.controller.deliver(limit=10) == []

    recovery = _world()
    recovery.controller.arm()
    queued = recovery.protocol.enqueue_outbound(
        organization_id=ORG_ID,
        user_id=USER_ID,
        binding_id=recovery.recipient.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="recover me",
        idempotency_key="paper-thread:recovery",
    )
    recovery.protocol.store.save_outbox(
        queued.model_copy(
            update={
                "state": OutboxState.CLAIMED,
                "lease_owner": "crashed-worker",
                "lease_until": recovery.clock.now() - timedelta(seconds=1),
            }
        )
    )
    recovered = recovery.controller.deliver(limit=10)
    assert any(item.accepted for item in recovered)
    assert recovery.transport.send_count == 1
    assert recovery.controller.deliver(limit=10) == []
    assert recovery.transport.send_count == 1


def test_outbound_rate_limit_defers_without_a_second_send() -> None:
    world = _world(per_chat=1)
    world.controller.arm()
    _project(world)
    first = world.controller.deliver(limit=10)
    assert world.transport.send_count == 1
    assert any(item.error_code == RATE_LIMITED_ERROR for item in first)
    world.clock.advance(timedelta(seconds=5))
    deferred = world.controller.deliver(limit=10)
    assert any(item.error_code == RATE_LIMITED_ERROR for item in deferred)
    assert world.transport.send_count == 1
    world.clock.advance(timedelta(seconds=61))
    resumed = world.controller.deliver(limit=10)
    assert any(item.accepted for item in resumed)
    assert world.transport.send_count == 2


def test_polling_cursor_survives_restart_and_replay_converges() -> None:
    binding_user = "tg-user-1"
    update = ParsedTelegramUpdate(
        update_id=7,
        body_size=128,
        kind="message",
        chat_type=ChatType.PRIVATE,
        chat_id=CHAT,
        telegram_user_id=binding_user,
        message_id="poll-7",
        text="explain the candidate",
    )
    world = _world(updates=(update,))
    world.controller.arm()
    _project(world)
    first = world.controller.poll_once()
    assert first.applied == 1
    assert world.controller.poll_once().fetched == 0
    restarted = TelegramPaperActivation(
        settings=world.settings,
        agent=world.agent,
        recipient=world.recipient,
        clock=world.clock,
        outbound_ready=True,
        update_source=world.source,
        cursor_store=world.controller.cursor_store,
    )
    restarted.arm()
    assert restarted.poll_once().fetched == 0
    lost_cursor = TelegramPaperActivation(
        settings=world.settings,
        agent=world.agent,
        recipient=world.recipient,
        clock=world.clock,
        outbound_ready=True,
        update_source=world.source,
        cursor_store=InMemoryActivationCursorStore(),
    )
    lost_cursor.arm()
    replay = lost_cursor.poll_once()
    assert replay.replayed == 1
    audits = [event.event_type for event in world.protocol.store.list_audits()]
    assert "inbound_replayed" in audits


def _poll_message(update_id: int) -> ParsedTelegramUpdate:
    return ParsedTelegramUpdate(
        update_id=update_id,
        body_size=64,
        kind="message",
        chat_type=ChatType.PRIVATE,
        chat_id=CHAT,
        telegram_user_id="tg-user-1",
        message_id=f"poll-{update_id}",
        text="explain the candidate",
    )


def test_gateway_tenant_error_is_rejected_and_cursor_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L16: a PaperTelegramError must not escape poll_once and pin the cursor."""

    world = _world(updates=(_poll_message(11),))
    world.controller.arm()

    def _refuse(**_kwargs: object) -> None:
        raise PaperTelegramTenantError("No verified Telegram binding for this chat.")

    monkeypatch.setattr(world.agent, "handle_inbound_message", _refuse)
    result = world.controller.poll_once()
    assert result.rejected == 1
    assert result.applied == 0
    cursor = world.controller.cursor_store.get_cursor(bot_id=BOT)
    assert cursor is not None and cursor.last_update_id == 11
    assert world.controller.poll_once().fetched == 0
    audits = [event.event_type for event in world.protocol.store.list_audits()]
    assert "inbound_rejected" in audits


def test_gateway_disabled_error_keeps_the_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(updates=(_poll_message(12),))
    world.controller.arm()

    def _disabled(**_kwargs: object) -> None:
        raise PaperTelegramDisabledError()

    monkeypatch.setattr(world.agent, "handle_inbound_message", _disabled)
    with pytest.raises(PaperTelegramDisabledError):
        world.controller.poll_once()
    assert world.controller.cursor_store.get_cursor(bot_id=BOT) is None


def test_group_updates_and_cross_chat_messages_are_rejected() -> None:
    parsed = parse_telegram_update(
        {
            "update_id": 3,
            "message": {
                "message_id": 1,
                "text": "hello",
                "chat": {"id": 9, "type": "group"},
                "from": {"id": 4},
            },
        },
        body_size=80,
    )
    assert parsed.rejection == "chat_not_private"
    world = _world(
        updates=(
            ParsedTelegramUpdate(
                update_id=8,
                body_size=40,
                kind="message",
                chat_type=ChatType.PRIVATE,
                chat_id="someone-else",
                telegram_user_id="tg-user-1",
                message_id="other",
                text="explain the candidate",
            ),
        )
    )
    world.controller.arm()
    result = world.controller.poll_once()
    assert result.rejected == 1
    assert result.applied == 0


def test_webhook_requires_secret_and_is_not_mounted_by_default() -> None:
    settings = _settings(telegram_inbound_mode="webhook", telegram_webhook_secret=SECRET)
    world = _world(settings=settings, inbound_mode="webhook")
    assert world.controller.accept_webhook(raw_body=b"{}", secret_header=SECRET).status_code == 503
    world.controller.arm()
    app = FastAPI()
    mount_paper_telegram_webhook(app, world.controller)
    client = TestClient(app)
    missing = client.post("/webhooks/telegram/paper", content=b"{}")
    assert missing.status_code == 401
    body = (
        b'{"update_id":11,"message":{"message_id":3,"text":"paper status",'
        b'"chat":{"id":"tg-chat-1","type":"private"},"from":{"id":"tg-user-1"}}}'
    )
    accepted = client.post(
        "/webhooks/telegram/paper",
        content=body,
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert accepted.status_code == 200
    oversized = client.post(
        "/webhooks/telegram/paper",
        content=b"x" * 70_000,
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert oversized.status_code == 413
    world.controller.disarm()
    assert world.controller.accept_webhook(raw_body=body, secret_header=SECRET).status_code == 503


def test_network_transport_refuses_until_permitted_and_then_dedupes() -> None:
    calls: list[str] = []

    def post(url: str, **kwargs: object) -> object:
        del kwargs
        calls.append(url)

        class Response:
            status_code = 200

            def json(self) -> dict[str, object]:
                return {"ok": True, "result": {"message_id": 15}}

        return Response()

    blocked = HttpTelegramTransport(
        token="test-token-not-real",
        timeout_seconds=1,
        network_permitted=False,
        http_post=post,
    )
    refused = blocked.send_private_message(
        bot_id=BOT,
        chat_id=CHAT,
        text="paper",
        idempotency_key="once",
    )
    assert refused.error_code == "NETWORK_DISABLED"
    assert calls == []
    refusing_source = RefusingTelegramUpdateSource()
    with pytest.raises(TelegramActivationError) as exc:
        refusing_source.fetch(offset=None, limit=1)
    assert exc.value.reason == "network_disabled"
    clock = FrozenClock(EVALUATED_AT)
    guarded = GuardedTelegramTransport(
        inner=HttpTelegramTransport(
            token="test-token-not-real",
            timeout_seconds=1,
            network_permitted=True,
            http_post=post,
        ),
        limiter=OutboundRateLimiter(clock, per_chat=5, window=timedelta(seconds=60)),
        ledger=InMemorySendLedger(),
        organization_id=ORG_ID,
        bot_id=BOT,
        chat_id=CHAT,
        clock=clock,
    )
    first = guarded.send_private_message(
        bot_id=BOT, chat_id=CHAT, text="hello", idempotency_key="same-key"
    )
    second = guarded.send_private_message(
        bot_id=BOT, chat_id=CHAT, text="hello", idempotency_key="same-key"
    )
    assert first.transport_message_id == second.transport_message_id == "15"
    assert len(calls) == 1
    assert "test-token-not-real" in calls[0]


def _identity(*, update_id: int, message_id: str):
    from tests.support.telegram_security import message_identity

    return message_identity(update_id=update_id, message_id=message_id)


def _inbound():
    from tests.support.telegram_security import inbound_message

    return inbound_message()
