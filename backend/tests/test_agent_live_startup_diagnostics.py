"""GPT Live startup failures: sanitized diagnostics and the provider-contact boundary.

The durable ``provider_contact`` mark must be committed before any connection
attempt; only failures before that commit leave the hold releasable at $0.

Drives the real ``live_voice`` route and ``LiveSession`` against a scripted
provider socket and an in-memory ledger. No provider, network or database.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.agent_voice import live_socket

ORIGIN = "https://staging.example"
SECRET_TEXT = "sk-live-fixture-secret provider says quota for user@example.invalid"
PROVIDER_ID = "live_fixture_session"


class Browser:
    def __init__(self, end_after_ready: bool = False) -> None:
        self.headers = {"origin": ORIGIN}
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._started = False
        self._end_after_ready = end_after_ready
        self.ready = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._started:
            self._started = True
            return json.dumps(
                {"access_token": "fixture-bearer-token", "conversation_id": "c", "context": None}
            )
        if self._end_after_ready:
            await self.ready.wait()
            return json.dumps({"type": "session.end"})
        await asyncio.sleep(3600)
        return ""

    async def send_json(self, event: dict[str, Any]) -> None:
        self.sent.append(event)
        if event.get("type") == "session.ready":
            self.ready.set()

    async def close(self, code: int | None = None) -> None:
        self.closed = True

    def errors(self) -> list[str]:
        return [e["reason"] for e in self.sent if e.get("type") == "session.error"]


class Ledger:
    instances: ClassVar[list[Ledger]] = []
    # Ordered record of ledger and provider calls shared with the fake client.
    calls: ClassVar[list[str]] = []
    reserve_hangs = False
    contact_error: Exception | None = None

    def __init__(self, *_args: Any) -> None:
        self.id = "00000000-0000-4000-8000-000000000001"
        self.limits = SimpleNamespace(voice_model="gpt-live-1", maximum_turns=2)
        self.boundary_marked = False
        self.started_with: tuple[str, str | None] | None = None
        self.final_receipts: list[dict[str, Any]] = []
        self.closed_with: list[dict[str, Any] | None] = []
        Ledger.instances.append(self)

    def reserve(self) -> None:
        Ledger.calls.append("reserve")
        if Ledger.reserve_hangs:
            import time

            time.sleep(0.5)

    def history(self) -> list[dict[str, Any]]:
        return []

    def provider_contact_may_start(self) -> None:
        if Ledger.contact_error is not None:
            raise Ledger.contact_error
        Ledger.calls.append("provider_contact")
        self.boundary_marked = True

    def started(self, provider_id: str, *, resolved_model: str | None = None) -> None:
        self.started_with = (provider_id, resolved_model)

    def update(self, *_args: Any) -> None:
        return None

    def usage(self, event: dict[str, Any], *, final: bool = False) -> None:
        if final:
            self.final_receipts.append(event)

    def close(self, failure: dict[str, Any] | None = None) -> None:
        self.closed_with.append(failure)


class Event:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self.data


class Provider:
    """Scripted Live socket. ``session.close`` yields ``closing`` events, if any."""

    def __init__(self, script: list[dict[str, Any]], closing: list[dict[str, Any]]) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        for item in script:
            self.queue.put_nowait(item)
        self.closing = closing
        self.sent: list[dict[str, Any]] = []

    async def send(self, event: dict[str, Any]) -> None:
        self.sent.append(event)
        if event.get("type") == "session.close":
            for item in self.closing:
                self.queue.put_nowait(item)
            self.queue.put_nowait(None)

    def __aiter__(self) -> Provider:
        return self

    async def __anext__(self) -> Event:
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return Event(item)


class Manager:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour

    async def __aenter__(self) -> Any:
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        if self.behaviour == "hang":
            await asyncio.sleep(3600)
        return self.behaviour

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def started(model: str = "gpt-live-1") -> dict[str, Any]:
    return {
        "type": "session.started",
        "event_id": "evt_started",
        "session": {
            "id": PROVIDER_ID,
            "model": model,
            "status": "active",
            "expires_at": 0,
            "delegation": {"type": "client"},
            "audio": {"format": {"type": "audio/pcm", "rate": 24000}},
            "store": False,
        },
    }


CLOSED = {
    "type": "session.closed",
    "session": {"id": PROVIDER_ID},
    "usage": {"seconds": 3},
    "reason": "close_requested",
}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch):
    Ledger.instances = []
    Ledger.calls = []
    Ledger.reserve_hangs = False
    Ledger.contact_error = None
    warnings: list[tuple[str, dict[str, Any]]] = []
    behaviour: dict[str, Any] = {}
    settings = SimpleNamespace(
        cors_origins=[ORIGIN],
        agent_live_enabled=True,
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="sk-fixture-api-key-never-logged",
    )

    class Client:
        def __init__(self, **_kwargs: Any) -> None:
            self.live = SimpleNamespace(connect=self.connect)

        def connect(self, **_kwargs: Any) -> Manager:
            Ledger.calls.append("connect")
            return Manager(behaviour["connect"])

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(live_socket, "get_settings", lambda: settings)
    monkeypatch.setattr(
        live_socket, "load_live_limits", lambda _s: SimpleNamespace(maximum_seconds=30)
    )
    monkeypatch.setattr(
        live_socket,
        "VoiceStart",
        SimpleNamespace(model_validate=lambda data: SimpleNamespace(**data)),
    )
    monkeypatch.setattr(live_socket, "get_session_factory", lambda: None)
    monkeypatch.setattr(live_socket, "LiveLedger", Ledger)
    monkeypatch.setattr(live_socket, "AsyncOpenAI", Client)
    monkeypatch.setattr(live_socket, "CLOSE_SECONDS", 0.3)
    monkeypatch.setattr(
        live_socket,
        "logger",
        SimpleNamespace(warning=lambda event, **kw: warnings.append((event, kw))),
    )
    return SimpleNamespace(warnings=warnings, behaviour=behaviour)


def failures(harness: Any) -> list[dict[str, Any]]:
    return [kw for event, kw in harness.warnings if event == "live_voice_failed"]


def assert_no_sensitive_text(harness: Any) -> None:
    rendered = repr(harness.warnings) + repr([ledger.closed_with for ledger in Ledger.instances])
    for fragment in ("sk-", "fixture-bearer-token", "user@example.invalid", "provider says"):
        assert fragment not in rendered


def rate_limited_upgrade() -> InvalidStatus:
    body = SECRET_TEXT.encode()
    return InvalidStatus(Response(429, "Too Many Requests", Headers(), body))


@pytest.mark.asyncio
async def test_refused_upgrade_is_after_contact_and_logs_status_without_text(harness) -> None:
    harness.behaviour["connect"] = rate_limited_upgrade()
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    expected = {
        "reservation_id": Ledger.instances[0].id,
        "stage": "provider_connect",
        "error_class": "InvalidStatus",
        "http_status": 429,
    }
    assert failures(harness) == [expected]
    # Conservative accounting was committed before the credential left the host.
    assert Ledger.calls == ["reserve", "provider_contact", "connect"]
    ledger = Ledger.instances[0]
    assert ledger.closed_with == [{k: v for k, v in expected.items() if k != "reservation_id"}]
    assert browser.errors() == ["voice_provider_unavailable"]
    assert_no_sensitive_text(harness)


@pytest.mark.asyncio
async def test_connection_refused_is_after_contact_boundary(harness) -> None:
    harness.behaviour["connect"] = ConnectionRefusedError(111, "refused " + SECRET_TEXT)
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    assert Ledger.calls == ["reserve", "provider_contact", "connect"]
    assert Ledger.instances[0].closed_with == [
        {"stage": "provider_connect", "error_class": "ConnectionRefusedError"}
    ]
    assert browser.errors() == ["voice_provider_unavailable"]
    assert_no_sensitive_text(harness)


@pytest.mark.asyncio
async def test_connect_timeout_is_after_contact_boundary(harness, monkeypatch) -> None:
    monkeypatch.setattr(live_socket, "PROVIDER_CONNECT_SECONDS", 0.1)
    harness.behaviour["connect"] = "hang"
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    assert Ledger.calls == ["reserve", "provider_contact", "connect"]
    assert Ledger.instances[0].closed_with == [
        {"stage": "provider_connect", "error_class": "TimeoutError"}
    ]
    assert browser.errors() == ["voice_session_unavailable"]


@pytest.mark.asyncio
async def test_cancellation_during_connect_is_after_contact_boundary(harness) -> None:
    harness.behaviour["connect"] = "hang"
    task = asyncio.create_task(live_socket.live_voice(Browser()))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)

    assert Ledger.calls == ["reserve", "provider_contact", "connect"]
    assert Ledger.instances[0].closed_with == [
        {"stage": "provider_connect", "error_class": "CancelledError"}
    ]


@pytest.mark.asyncio
async def test_cancellation_before_contact_never_connects(harness) -> None:
    Ledger.reserve_hangs = True
    harness.behaviour["connect"] = "hang"
    task = asyncio.create_task(live_socket.live_voice(Browser()))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)

    # The provider was never contacted, so the ledger may release this hold.
    assert "provider_contact" not in Ledger.calls and "connect" not in Ledger.calls
    assert Ledger.instances[0].closed_with == [
        {"stage": "admission", "error_class": "CancelledError"}
    ]


@pytest.mark.asyncio
async def test_refused_contact_boundary_never_connects(harness) -> None:
    from app.agent_voice.config import VoiceUnavailableError

    Ledger.contact_error = VoiceUnavailableError("voice_session_closed")
    harness.behaviour["connect"] = "hang"
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    assert Ledger.calls == ["reserve"]
    assert Ledger.instances[0].closed_with == [
        {
            "stage": "contact_boundary",
            "error_class": "VoiceUnavailableError",
            "reason": "voice_session_closed",
        }
    ]
    assert browser.errors() == ["voice_session_closed"]


@pytest.mark.asyncio
async def test_provider_error_before_start_keeps_codes_but_not_message(harness) -> None:
    error = {
        "type": "error",
        "event_id": "evt_error",
        "error": {
            "type": "invalid_request_error",
            "code": "insufficient_quota",
            "param": "session.model",
            "message": SECRET_TEXT,
        },
    }
    harness.behaviour["connect"] = Provider([error], closing=[])
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    ledger = Ledger.instances[0]
    assert ledger.boundary_marked is True  # provider contact preceded the error
    record = ledger.closed_with[0]
    assert record == {
        "stage": "awaiting_session_started",
        "error_class": "VoiceUnavailableError",
        "reason": "live_provider_event_unavailable",
        "provider_error_type": "invalid_request_error",
        "provider_error_code": "insufficient_quota",
        "provider_error_param": "session.model",
        "provider_event": "error",
        "provider_event_reason": "live_provider_error",
    }
    assert failures(harness)[0]["provider_error_code"] == "insufficient_quota"
    assert browser.errors() == ["live_provider_event_unavailable"]
    assert_no_sensitive_text(harness)


@pytest.mark.asyncio
async def test_missing_session_started_is_a_reported_startup_timeout(harness, monkeypatch) -> None:
    monkeypatch.setattr(live_socket, "STARTUP_SECONDS", 0.1)
    harness.behaviour["connect"] = Provider([], closing=[])
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    ledger = Ledger.instances[0]
    assert ledger.boundary_marked is True
    assert ledger.closed_with[0] == {
        "stage": "awaiting_session_started",
        "error_class": "VoiceUnavailableError",
        "reason": "live_session_start_timeout",
    }
    assert browser.errors() == ["live_session_start_timeout"]


@pytest.mark.asyncio
async def test_refused_config_still_binds_identity_and_keeps_final_receipt(harness) -> None:
    harness.behaviour["connect"] = Provider([started(model="gpt-live-1-preview")], [CLOSED])
    browser = Browser()

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    ledger = Ledger.instances[0]
    # Identity was bound before the refusal, so the provider's billed seconds
    # are recorded instead of rejected as "usage before start".
    assert ledger.started_with == (PROVIDER_ID, "gpt-live-1-preview")
    assert ledger.final_receipts == [CLOSED]
    record = ledger.closed_with[0]
    assert record["config_mismatch"] == ["model"]
    assert record["resolved_model"] == "gpt-live-1-preview"
    assert record["provider_event_reason"] == "live_session_config_mismatch"
    assert "session.ready" not in [e.get("type") for e in browser.sent]


@pytest.mark.asyncio
async def test_successful_session_reaches_ready_and_closes_without_failure(harness) -> None:
    provider = Provider([started()], [CLOSED])
    harness.behaviour["connect"] = provider
    browser = Browser(end_after_ready=True)

    await asyncio.wait_for(live_socket.live_voice(browser), timeout=10)

    ledger = Ledger.instances[0]
    assert ledger.boundary_marked is True
    assert ledger.started_with == (PROVIDER_ID, "gpt-live-1")
    assert ledger.final_receipts == [CLOSED]
    assert ledger.closed_with == [None]
    assert failures(harness) == []
    assert [e["type"] for e in browser.sent] == ["session.ready", "session.closed"]
    assert browser.sent[-1] == {"type": "session.closed", "finalized": True}
    assert [e["type"] for e in provider.sent] == ["session.start", "session.close"]


def test_http_status_follows_wrapped_causes() -> None:
    try:
        try:
            raise rate_limited_upgrade()
        except InvalidStatus as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert live_socket.failure_diagnostics(outer, "provider_connect", None) == {
            "stage": "provider_connect",
            "error_class": "RuntimeError",
            "http_status": 429,
        }
