"""Frontier AT-080 P0/P1 regressions.

These tests rehearse the paper Telegram process, Binance request budget, lease
heartbeats, kill switch, and activation order. They do not enable live trading.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.candidate_alerts.gateway import CandidateAlertGateway
from app.controlled_activation.profile import (
    controlled_telegram_projection,
    package_disarmed,
    telegram_enrollment_runtime,
)
from app.controlled_activation.rollback import plan_package_rollback
from app.core.config import Settings
from app.core.logging import (
    RedactingFormatter,
    SecretRedactionFilter,
    _redact_sensitive,
    configure_logging,
)
from app.db.models import KillSwitchState, Organization
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.guardrails.redaction import redact_text
from app.main import create_app
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.request_budget import (
    SlidingWeightBudget,
    market_request_metrics,
    request_weight,
    sleep_with_progress,
)
from app.market_contracts.errors import (
    RateLimitedError,
    RegionalProviderFailureError,
    UpstreamBanError,
)
from app.market_contracts.request_progress import (
    bind_market_request_progress,
    notify_market_request_progress,
    reset_market_request_progress,
)
from app.persistence.composition import build_postgres_telegram_security_protocol
from app.persistence.runtime_status import TELEGRAM_COMPONENT, load_runtime_rows
from app.persistence.telegram_activation import (
    PostgresActivationCursorStore,
    advance_bot_cursor,
)
from app.runtime_safety.paper_actions import (
    read_kill_switch_active,
    read_process_kill_switch,
)
from app.schemas.health import HealthResponse
from app.signal_fusion.lifecycle import in_memory_candidate_lifecycle
from app.telegram_activation.controller import TelegramPaperActivation
from app.telegram_activation.cursor import enrollment_cursor_owner
from app.telegram_activation.intake import ParsedTelegramUpdate, RecordedUpdateSource
from app.telegram_activation.policy import PAPER_ACTIVATION_BACKOFF
from app.telegram_activation.runtime import TelegramPaperRuntime, build_telegram_runtime
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_security.clock import FrozenClock
from app.telegram_security.contracts import ChatType, OutboxState
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramBehavior, FakeTelegramTransport
from app.watcher.contracts import (
    EvaluationCommand,
    EvaluationMode,
    ScanRequest,
    ScanTrigger,
    WatcherRuntimeConfig,
)
from app.watcher.memory import (
    FakeClock,
    InMemoryWatcherStore,
    ScriptedEvaluationBoundary,
    SideEffectProbe,
)
from app.watcher.orchestrator import WatcherOrchestrator
from app.workers.watcher_paper import paper_lease_ttl_seconds
from tests.support.phase6_fusion import ORG_ID
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.support.telegram_security import (
    ACCOUNT,
    BOT,
    CHAT,
    ORG,
    OTHER_CHAT,
    OTHER_ORG,
    USER,
    TokenSeq,
    enroll,
    message_identity,
)

TOKEN = "123456789:AAHtestTokenValueForStagingPackage"
ROOT = Path(__file__).resolve().parents[2]

_STAGING = {
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
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
}


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


class _CountingStore(InMemoryWatcherStore):
    def __init__(self) -> None:
        super().__init__()
        self.renewals = 0
        self.fail_next_renewal = False
        self.heartbeat_details: list[str | None] = []

    def renew_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        ttl_seconds: int,
        now: datetime,
    ) -> bool:
        self.renewals += 1
        if self.fail_next_renewal:
            self.fail_next_renewal = False
            return False
        return super().renew_lease(
            organization_id=organization_id,
            scan_scope=scan_scope,
            owner_id=owner_id,
            fencing_token=fencing_token,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def record_heartbeat(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        lease_epoch: int,
        fencing_token: int,
        now: datetime,
        detail: str | None = None,
    ) -> object:
        self.heartbeat_details.append(detail)
        return super().record_heartbeat(
            organization_id=organization_id,
            scan_scope=scan_scope,
            owner_id=owner_id,
            lease_epoch=lease_epoch,
            fencing_token=fencing_token,
            now=now,
            detail=detail,
        )


class _ProgressBoundary(ScriptedEvaluationBoundary):
    def evaluate(self, command: EvaluationCommand) -> object:
        notify_market_request_progress()
        return super().evaluate(command)


class _BrokenSession:
    def scalar(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("kill switch unreadable")


def _scan_request() -> ScanRequest:
    return ScanRequest(
        organization_id=ORG_ID,
        principal_id=None,
        scan_scope="org:btcusdt",
        policy_id=uuid4(),
        policy_version=1,
        policy_content_hash="e" * 64,
        watchlist_item_ids=(uuid4(),),
        timeframe="15m",
        idempotency_key=f"frontier-{uuid4().hex}",
    )


def _command() -> EvaluationCommand:
    request = _scan_request()
    return EvaluationCommand(
        command_id=uuid4(),
        request=request,
        request_hash="a" * 64,
        evaluation_input_hash="b" * 64,
        mode=EvaluationMode.PERSIST_EVIDENCE,
        trigger=ScanTrigger.WORKER,
        lineage_id=uuid4(),
        attempt_id=uuid4(),
        lease_epoch=1,
        fencing_token=1,
        worker_id="frontier",
        correlation_id=uuid4(),
    )


def _run_worker(store: _CountingStore, boundary: _ProgressBoundary) -> object:
    orchestrator = WatcherOrchestrator(
        store=store,
        evaluator=boundary,
        clock=FakeClock(),
        config=WatcherRuntimeConfig(
            enabled=True,
            lease_ttl_seconds=30,
            heartbeat_stale_after_seconds=90,
        ),
        side_effects=SideEffectProbe(),
    )
    return orchestrator.run_worker(_scan_request(), worker_id="frontier-worker")


def _local_projection_settings() -> Settings:
    return Settings(
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        telegram_interaction_enabled=True,
        telegram_paper_activation_armed=True,
        telegram_inbound_mode="polling",
        telegram_alerts_enabled=False,
        automatic_telegram_delivery_enabled=False,
        telegram_network_permitted=False,
    )


@dataclass(frozen=True, slots=True)
class _Projection:
    runtime: TelegramPaperRuntime
    protocol: TelegramSecurityProtocol
    transport: FakeTelegramTransport
    clock: FrozenClock
    controller: TelegramPaperActivation
    binding_id: UUID


def _projection_runtime(
    factory: sessionmaker[Session],
    *,
    updates: tuple[ParsedTelegramUpdate, ...] = (),
    transport: FakeTelegramTransport | None = None,
    clock: FrozenClock | None = None,
    worker_id: str = "tg-paper",
    update_id: int = 1,
) -> _Projection:
    resolved_clock = clock or FrozenClock()
    resolved_transport = transport or FakeTelegramTransport()
    protocol = build_postgres_telegram_security_protocol(
        factory,
        enabled=True,
        clock=resolved_clock,
        transport=resolved_transport,
        token_factory=TokenSeq(),
        retry_backoff=PAPER_ACTIVATION_BACKOFF,
    )
    _, binding_id = enroll(
        protocol,
        identity=message_identity(update_id=update_id, message_id=f"enroll-{update_id}"),
    )
    agent = TelegramPaperAgent(
        protocol=protocol,
        candidates=CandidateAlertGateway(
            lifecycle=in_memory_candidate_lifecycle(now=resolved_clock.now()),
            protocol=protocol,
            clock=resolved_clock,
        ),
        clock=resolved_clock,
        enabled=True,
    )
    controller = TelegramPaperActivation(
        settings=_local_projection_settings(),
        agent=agent,
        recipient=PaperAlertRecipient(
            organization_id=ORG,
            user_id=USER,
            account_id=ACCOUNT,
            binding_id=binding_id,
            bot_id=BOT,
            chat_id=CHAT,
        ),
        clock=resolved_clock,
        outbound_ready=True,
        update_source=RecordedUpdateSource(updates),
        cursor_store=PostgresActivationCursorStore(factory),
    )
    controller.arm()
    runtime = TelegramPaperRuntime(
        settings=_local_projection_settings(),
        session_factory=factory,
        clock=resolved_clock,
        worker_id=worker_id,
        posture="projection",
        controller=controller,
    )
    return _Projection(
        runtime=runtime,
        protocol=protocol,
        transport=resolved_transport,
        clock=resolved_clock,
        controller=controller,
        binding_id=binding_id,
    )


def _message(*, update_id: int, chat_id: str, text: str) -> ParsedTelegramUpdate:
    return ParsedTelegramUpdate(
        update_id=update_id,
        body_size=len(text.encode()),
        kind="message",
        chat_type=ChatType.PRIVATE,
        chat_id=chat_id,
        telegram_user_id="tg-user-1",
        message_id=f"msg-{update_id}",
        text=text,
    )


def test_telegram_token_cannot_appear_in_logs_exceptions_or_health(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    assert TOKEN not in redact_text(url)
    assert TOKEN not in redact_text(f"token={TOKEN}")
    configure_logging(log_level="INFO", json_logs=True)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger().level == logging.INFO
    handler = _ListHandler()
    handler.setFormatter(RedactingFormatter("%(message)s"))
    handler.addFilter(SecretRedactionFilter())
    library = logging.getLogger("httpx")
    # Earlier in-process Alembic probes disable existing loggers.
    monkeypatch.setattr(library, "disabled", False)
    library.addHandler(handler)
    library.setLevel(logging.INFO)
    try:
        library.info("HTTP Request: GET %s", url)
    finally:
        library.removeHandler(handler)
        library.setLevel(logging.WARNING)
    assert handler.lines
    assert TOKEN not in handler.lines[0]
    assert "REDACTED" in handler.lines[0]
    try:
        raise RuntimeError(url)
    except RuntimeError:
        rendered = RedactingFormatter().formatException(sys.exc_info())
    assert TOKEN not in rendered
    event = _redact_sensitive(None, "error", {"event": "transport", "exc_info": RuntimeError(url)})
    assert "exc_info" not in event
    assert TOKEN not in str(event["exception"])
    from app.core.logging import get_logger

    get_logger("frontier").info("watcher_heartbeat", component="watcher")
    captured = capsys.readouterr().out
    assert "watcher_heartbeat" in captured
    assert TOKEN not in captured
    assert "telegram_bot_token" not in HealthResponse.model_fields
    from fastapi.testclient import TestClient

    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "worker_runtime" in body
    assert TOKEN not in response.text
    assert "telegram_bot_token" not in response.text


def test_binance_429_retries_and_418_does_not_ignore_a_long_ban() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def limited(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "12"}, json={"msg": "slow"})
        return httpx.Response(200, json={"ok": True})

    client = ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(limited),
        max_retries=1,
        max_backoff_seconds=30.0,
        sleeper=sleeps.append,
    )
    assert client.get_json("/fapi/v1/aggTrades", {"symbol": "BTCUSDT"}) == {"ok": True}
    client.close()
    assert calls["n"] == 2
    assert sleeps == [5.0, 5.0, 2.0]

    regional = {"n": 0}

    def teapot(_request: httpx.Request) -> httpx.Response:
        regional["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "120"}, json={"msg": "banned"})

    blocked = ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(teapot),
        max_retries=3,
        max_backoff_seconds=30.0,
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("418 must not retry early")),
    )
    with pytest.raises(UpstreamBanError) as banned:
        blocked.get_json("/fapi/v1/ping")
    blocked.close()
    assert regional["n"] == 1
    assert banned.value.retry_after_seconds == 120.0


def test_binance_outage_retries_and_weight_budget_fails_closed() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def down(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("usd-m disconnected")

    client = ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(down),
        max_retries=1,
        max_backoff_seconds=30.0,
        sleeper=sleeps.append,
    )
    with pytest.raises(RegionalProviderFailureError):
        client.get_json("/fapi/v1/time")
    client.close()
    assert calls["n"] == 2
    assert sleeps == [1.0]
    assert request_weight("/fapi/v1/aggTrades", None) == 20
    assert request_weight("/fapi/v1/klines", {"limit": 1000}) == 5
    now = {"t": 0.0}
    budget = SlidingWeightBudget(
        limit=25,
        window_seconds=60.0,
        max_wait_seconds=1.0,
        _clock=lambda: now["t"],
    )
    budget.acquire(20, sleeper=lambda _seconds: None)
    with pytest.raises(RateLimitedError):
        budget.acquire(20, sleeper=lambda _seconds: None)
    now["t"] = 61.0
    budget.acquire(20, sleeper=lambda _seconds: None)
    assert budget.used() == 20
    oversized = SlidingWeightBudget(limit=10, _clock=lambda: 0.0)
    with pytest.raises(RateLimitedError):
        oversized.acquire(11, sleeper=lambda _seconds: None)


def test_backoff_notifies_progress_and_closed_agg_window_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats = {"n": 0}
    sleeps: list[float] = []
    token = bind_market_request_progress(lambda: beats.__setitem__("n", beats["n"] + 1))
    try:
        sleep_with_progress(12, sleeper=sleeps.append)
    finally:
        reset_market_request_progress(token)
    assert sleeps == [5.0, 5.0, 2.0]
    assert beats["n"] == 3
    fetches: list[object] = []

    def fake_fetch(**kwargs: object) -> list[dict[str, int]]:
        fetches.append(kwargs)
        return [{"a": 1, "p": "1", "q": "1", "T": 1, "m": False}]

    monkeypatch.setattr(
        "app.market_contracts.adapters.binance_usdm.fetch_complete_agg_trade_rows",
        fake_fetch,
    )
    before = market_request_metrics().cache_hits
    source = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[])),
        trade_cache_entries=2,
    )
    start = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=8)
    first = source._cached_agg_trades(symbol="BTCUSDT", start=start, end=end)
    second = source._cached_agg_trades(symbol="BTCUSDT", start=start, end=end)
    source.close()
    assert first == second
    assert len(fetches) == 1
    assert market_request_metrics().cache_hits == before + 1


def test_evidence_port_reuses_one_snapshot_and_does_not_cache_errors() -> None:
    port = AssemblingWatcherScanEvidence(
        assembler=object(),  # type: ignore[arg-type]
    )
    command = _command()
    state = {"n": 0}

    def flaky(_command: EvaluationCommand) -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("assembler failed")
        return None

    port._load_uncached = flaky  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="assembler failed"):
        port.load(command)
    assert port.load(command) is None
    assert port.load(command) is None
    assert state["n"] == 2
    other = command.model_copy(update={"evaluation_input_hash": "c" * 64})
    assert port.load(other) is None
    assert state["n"] == 3


def test_lease_heartbeat_during_evaluation_and_lost_lease_before_persist() -> None:
    store = _CountingStore()
    result = _run_worker(store, _ProgressBoundary())
    assert store.renewals >= 2
    assert "lease_heartbeat" in store.heartbeat_details
    assert result.published is True
    assert result.reason_code == "scripted"
    lost = _CountingStore()
    lost.fail_next_renewal = True
    rejected = _run_worker(lost, _ProgressBoundary())
    assert rejected.reason_code == "lease_renewal_lost"
    assert rejected.published is False
    assert rejected.outcome is not None
    assert rejected.outcome.candidate_ids == ()
    assert lost.renewals == 1


def test_paper_lease_ttl_covers_binance_backoff_and_replay_stays_configured() -> None:
    replay = Settings(watcher_lease_ttl_seconds=30)
    assert paper_lease_ttl_seconds(replay) == 30
    live = Settings(**{**_STAGING, "perpetual_evidence_source": "binance_usdm"})
    assert paper_lease_ttl_seconds(live) == 85
    widest = Settings(
        **{
            **_STAGING,
            "perpetual_evidence_source": "binance_usdm",
            "perpetual_evidence_timeout_seconds": 30,
            "binance_request_max_backoff_seconds": 120,
        }
    )
    assert paper_lease_ttl_seconds(widest) == 255
    raw = widest.model_dump()
    raw["perpetual_evidence_timeout_seconds"] = 1000
    capped = Settings.model_construct(**raw)
    assert paper_lease_ttl_seconds(capped) == 3600


def test_activation_sequence_has_no_armed_network_gap_and_webhook_is_not_staging() -> None:
    enrollment = Settings(
        **{
            **_STAGING,
            "perpetual_evidence_source": "binance_usdm",
            "telegram_interaction_enabled": True,
            "telegram_inbound_mode": "polling",
            "telegram_network_permitted": True,
            "telegram_bot_id": BOT,
            "telegram_bot_token": TOKEN,
        }
    )
    assert telegram_enrollment_runtime(enrollment) is True
    assert controlled_telegram_projection(enrollment) is False
    projection = Settings(
        **{
            **enrollment.model_dump(),
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
            "telegram_paper_activation_armed": True,
            "telegram_chat_id": CHAT,
        }
    )
    assert controlled_telegram_projection(projection) is True
    with pytest.raises(ValidationError, match="telegram_paper_activation_armed"):
        Settings(
            **{
                **enrollment.model_dump(),
                "watcher_orchestration_enabled": True,
                "watcher_paper_staging_activation": True,
                "telegram_paper_activation_armed": True,
                "telegram_chat_id": CHAT,
                "telegram_network_permitted": False,
            }
        )
    with pytest.raises(ValidationError, match="telegram_inbound_mode"):
        Settings(
            **{
                **enrollment.model_dump(),
                "watcher_orchestration_enabled": True,
                "watcher_paper_staging_activation": True,
                "telegram_paper_activation_armed": True,
                "telegram_chat_id": CHAT,
                "telegram_inbound_mode": "webhook",
                "telegram_webhook_secret": "w" * 32,
            }
        )


def test_partial_deployment_and_rollback_order_keep_live_trading_impossible() -> None:
    with pytest.raises(ValidationError, match="replay evidence"):
        Settings(
            **{
                **_STAGING,
                "watcher_orchestration_enabled": True,
                "watcher_paper_staging_activation": True,
                "perpetual_evidence_source": "replay",
            }
        )
    disarmed = Settings(**{**_STAGING, "perpetual_evidence_source": "replay"})
    assert package_disarmed(disarmed) is True
    assert disarmed.enable_real_trading is False
    assert disarmed.real_trading_enabled is False
    actions = [step.action for step in plan_package_rollback()]
    telegram = next(
        i for i, action in enumerate(actions) if "TELEGRAM_PAPER_ACTIVATION_ARMED=false" in action
    )
    watcher = next(
        i for i, action in enumerate(actions) if "WATCHER_ORCHESTRATION_ENABLED=false" in action
    )
    replay = next(
        i for i, action in enumerate(actions) if "PERPETUAL_EVIDENCE_SOURCE=replay" in action
    )
    assert telegram < watcher < replay
    assert any("only after both Watcher flags are false" in action for action in actions)
    assert any("Leave the kill switch unchanged" in action for action in actions)
    assert not any("KILL_SWITCH" in action and "false" in action for action in actions)
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" not in blueprint
    for name, command in (
        ("alphatrade-paper-worker-staging", "python -m app.workers.paper_worker"),
    ):
        start = blueprint.index(f"name: {name}")
        block = blueprint[start : start + 4000]
        assert f"dockerCommand: {command}" in block
        assert "ENABLE_REAL_TRADING" in block
        assert "value: false" in block
        assert "WATCHER_ORCHESTRATION_ENABLED" in block
        assert "TELEGRAM_NETWORK_PERMITTED" in block
        assert 'value: "off"' in block
        assert "AUTH_REFRESH_COOKIE_ENABLED" in block
        assert "CORS_ORIGINS" in block
        assert "AUTH_COOKIE_SECURE" in block
    paths = [getattr(route, "path", "") for route in create_app().routes]
    assert not any("telegram" in path and "webhook" in path for path in paths)


def test_unreadable_kill_switch_blocks_automated_paper_actions() -> None:
    broken = cast(Session, _BrokenSession())
    assert read_kill_switch_active(broken, Settings(), ORG) is True
    assert read_process_kill_switch(broken, Settings()) is True
    assert read_kill_switch_active(None, Settings(global_kill_switch_active=True), ORG) is True
    assert read_kill_switch_active(None, Settings(), ORG) is False


@requires_postgres
def test_postgres_runtime_retries_duplicate_delivery_and_survives_restart() -> None:
    factory = phase7_plan_session_factory()
    transport = FakeTelegramTransport()
    transport.fail_next(1)
    world = _projection_runtime(factory, transport=transport)
    runtime = world.runtime
    protocol = world.protocol
    transport = world.transport
    clock = world.clock
    protocol.enqueue_outbound(
        organization_id=ORG,
        user_id=USER,
        binding_id=world.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="paper notice",
        idempotency_key="frontier-duplicate",
    )
    first = runtime.run_cycle()
    assert first.delivered == 0
    assert first.last_error_code == "TRANSPORT_FAILURE"
    assert transport.send_count == 0
    assert runtime.run_cycle().delivered == 0
    clock.advance(timedelta(seconds=5))
    second = runtime.run_cycle()
    assert second.delivered == 1
    assert transport.send_count == 1
    assert runtime.run_cycle().delivered == 0
    assert transport.send_count == 1
    restarted = TelegramPaperRuntime(
        settings=_local_projection_settings(),
        session_factory=factory,
        clock=clock,
        worker_id="tg-paper-restart",
        posture="projection",
        controller=world.controller,
    )
    clock.advance(timedelta(seconds=31))
    assert restarted.run_cycle().delivered == 0
    assert transport.send_count == 1
    with factory() as session:
        row = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert row.last_delivery_at == clock.now() - timedelta(seconds=31)


@requires_postgres
def test_postgres_runtime_recovers_after_network_partition() -> None:
    factory = phase7_plan_session_factory()
    partitioned = FakeTelegramTransport(behavior=FakeTelegramBehavior.FAIL)
    world = _projection_runtime(factory, transport=partitioned, worker_id="tg-partition")
    world.protocol.enqueue_outbound(
        organization_id=ORG,
        user_id=USER,
        binding_id=world.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="partition",
        idempotency_key="frontier-partition",
    )
    blocked = world.runtime.run_cycle()
    assert blocked.delivered == 0
    assert blocked.last_error_code == "TRANSPORT_FAILURE"
    partitioned.behavior = FakeTelegramBehavior.ACCEPT
    world.clock.advance(timedelta(seconds=5))
    assert world.runtime.run_cycle().delivered == 1
    assert partitioned.send_count == 1


@requires_postgres
def test_postgres_runtime_rejects_wrong_recipient_and_keeps_cursor() -> None:
    factory = phase7_plan_session_factory()
    update = _message(update_id=7, chat_id=OTHER_CHAT, text="not enrolled")
    world = _projection_runtime(factory, updates=(update,))
    runtime = world.runtime
    transport = world.transport
    cycle = runtime.run_cycle()
    assert cycle.poll_rejected == 1
    assert cycle.poll_applied == 0
    assert transport.send_count == 0
    cursor = PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT)
    assert cursor is not None
    assert cursor.last_update_id == 7
    assert cursor.organization_id == ORG
    again = runtime.run_cycle()
    assert again.poll_rejected == 0
    assert again.poll_applied == 0
    advance_bot_cursor(
        factory,
        bot_id=BOT,
        organization_id=OTHER_ORG,
        update_id=8,
        now=world.clock.now(),
    )
    stored = PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT)
    assert stored is not None
    assert stored.organization_id == ORG
    assert stored.last_update_id == 8


@requires_postgres
def test_postgres_kill_switch_stops_delivery_and_cursor_and_keeps_heartbeat() -> None:
    factory = phase7_plan_session_factory()
    update = _message(update_id=3, chat_id=CHAT, text="status")
    world = _projection_runtime(factory, updates=(update,))
    runtime = world.runtime
    protocol = world.protocol
    transport = world.transport
    protocol.enqueue_outbound(
        organization_id=ORG,
        user_id=USER,
        binding_id=world.binding_id,
        bot_id=BOT,
        chat_id=CHAT,
        text="queued while switch activates",
        idempotency_key="frontier-killed",
    )
    with factory() as session, session.begin():
        session.add(Organization(id=ORG, name="Frontier kill switch"))
        session.flush()
        session.add(KillSwitchState(organization_id=ORG, active=True, reason="frontier"))
    cycle = runtime.run_cycle()
    assert cycle.kill_switch_active is True
    assert cycle.delivered == 0
    assert cycle.poll_applied == 0
    assert transport.send_count == 0
    assert PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT) is None
    pending = protocol.store.list_outbox(organization_id=ORG)
    assert any(row.state is not OutboxState.ACKNOWLEDGED for row in pending)
    with factory() as session:
        row = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert row.activation_state == "monitoring"
    assert row.kill_switch_active is True
    assert row.heartbeat_at is not None
    assert row.fence_held is True
    assert row.lease_epoch >= 1
    assert TOKEN not in row.last_error_code


@requires_postgres
def test_postgres_lease_restart_and_enrollment_polling() -> None:
    factory = phase7_plan_session_factory()
    clock = FrozenClock()
    settings = Settings()
    first = TelegramPaperRuntime(
        settings=settings,
        session_factory=factory,
        clock=clock,
        worker_id="lease-a",
        posture="disarmed",
    )
    second = TelegramPaperRuntime(
        settings=settings,
        session_factory=factory,
        clock=clock,
        worker_id="lease-b",
        posture="disarmed",
    )
    assert first.run_cycle().lease_held is True
    blocked = second.run_cycle()
    assert blocked.lease_held is False
    assert blocked.delivered == 0
    with factory() as session:
        held = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert held.lease_owner == "lease-a"
    assert held.worker_id == "lease-a"
    assert held.lease_epoch >= 1
    clock.advance(timedelta(seconds=31))
    assert second.run_cycle().lease_held is True
    with factory() as session:
        renewed = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert renewed.lease_owner == "lease-b"
    assert renewed.lease_epoch > held.lease_epoch

    enrollment_settings = Settings(
        **{
            **_STAGING,
            "perpetual_evidence_source": "binance_usdm",
            "telegram_interaction_enabled": True,
            "telegram_inbound_mode": "polling",
            "telegram_network_permitted": True,
            "telegram_bot_id": BOT,
            "telegram_bot_token": TOKEN,
        }
    )
    starter = build_postgres_telegram_security_protocol(
        factory,
        enabled=True,
        clock=clock,
        transport=FakeTelegramTransport(),
        token_factory=TokenSeq(),
    )
    started = starter.start_enrollment(organization_id=ORG, user_id=USER, bot_id=BOT)
    source = RecordedUpdateSource((_message(update_id=11, chat_id=CHAT, text=started.token),))
    enrolled = build_telegram_runtime(
        enrollment_settings,
        factory,
        transport=FakeTelegramTransport(),
        update_source=source,
        clock=clock,
        worker_id="enroll-1",
    )
    assert enrolled.posture == "enrollment"
    clock.advance(timedelta(seconds=31))
    completed = enrolled.run_cycle()
    assert completed.poll_applied == 1
    assert completed.delivered == 0
    assert started.token not in completed.last_error_code
    binding = starter.store.get_active_binding_for_chat(bot_id=BOT, chat_id=CHAT)
    assert binding is not None
    assert binding.organization_id == ORG
    assert enrolled.run_cycle().poll_applied == 0
    cursor = PostgresActivationCursorStore(factory).get_cursor(bot_id=BOT)
    assert cursor is not None
    assert cursor.last_update_id == 11
    assert cursor.organization_id == ORG
    assert enrollment_cursor_owner(BOT) != ORG


@requires_postgres
def test_postgres_projection_without_binding_idles_instead_of_exiting() -> None:
    factory = phase7_plan_session_factory()
    settings = Settings(
        **{
            **_STAGING,
            "perpetual_evidence_source": "binance_usdm",
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
            "telegram_interaction_enabled": True,
            "telegram_paper_activation_armed": True,
            "telegram_inbound_mode": "polling",
            "telegram_network_permitted": True,
            "telegram_bot_id": BOT,
            "telegram_chat_id": CHAT,
            "telegram_bot_token": TOKEN,
        }
    )
    runtime = build_telegram_runtime(
        settings,
        factory,
        clock=FrozenClock(),
        worker_id="missing-binding",
    )
    assert runtime.posture == "projection"
    cycle = runtime.run_cycle()
    assert cycle.delivered == 0
    assert cycle.poll_applied == 0
    assert cycle.last_error_code == "recipient_binding_missing"
    with factory() as session:
        row = load_runtime_rows(session)[TELEGRAM_COMPONENT]
    assert row.activation_state == "refused"
    assert row.heartbeat_at is not None
    assert TOKEN not in row.last_error_code
