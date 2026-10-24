"""Live socket cleanup: cancelled children and bounded shielded close (#320 N6)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.agent_voice import live_socket

ORIGIN = "https://staging.example"


class FakeWebSocket:
    def __init__(self, browser_cancel: bool) -> None:
        self.headers = {"origin": ORIGIN}
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._started = False
        self._browser_cancel = browser_cancel

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._started:
            self._started = True
            return json.dumps({"access_token": "t", "conversation_id": "c"})
        if self._browser_cancel:
            raise asyncio.CancelledError()
        await asyncio.sleep(3600)
        return ""

    async def send_json(self, event: dict[str, Any]) -> None:
        self.sent.append(event)

    async def close(self, code: int | None = None) -> None:
        self.closed = True


class FakeLedger:
    def __init__(self, *args: Any) -> None:
        self.closed = False

    def reserve(self) -> None:
        return None

    def update(self, *_args: Any) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeSession:
    instances: ClassVar[list[FakeSession]] = []
    stop_hangs = False

    def __init__(self, ledger: Any, start: Any, upstream: Any, to_browser: Any) -> None:
        self.finalized = asyncio.Event()
        self.failure = None
        self.closing = False
        self.ready = True
        self.tasks: list[asyncio.Task[None]] = []
        self.close_started = None
        self.stopped = False
        upstream.session = self
        FakeSession.instances.append(self)

    async def start_session(self) -> None:
        return None

    async def client(self, _event: dict[str, Any]) -> bool:
        return True

    async def provider(self, _event: dict[str, Any]) -> None:
        return None

    async def request_close(self) -> None:
        self.closing = True
        self.finalized.set()

    async def stop(self) -> None:
        if FakeSession.stop_hangs:
            await asyncio.sleep(3600)
        self.stopped = True


class FakeUpstream:
    session: FakeSession

    def __aiter__(self) -> FakeUpstream:
        return self

    async def __anext__(self) -> Any:
        await self.session.finalized.wait()
        raise StopAsyncIteration


class _CM:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class FakeClient(_CM):
    def __init__(self, **_kwargs: Any) -> None:
        super().__init__(self)
        self.live = SimpleNamespace(connect=lambda **_k: _CM(FakeUpstream()))


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    FakeSession.instances = []
    FakeSession.stop_hangs = False
    settings = SimpleNamespace(
        cors_origins=[ORIGIN],
        agent_live_enabled=True,
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="test-key",
    )
    monkeypatch.setattr(live_socket, "get_settings", lambda: settings)
    monkeypatch.setattr(
        live_socket, "load_live_limits", lambda _s: SimpleNamespace(maximum_seconds=5)
    )
    monkeypatch.setattr(
        live_socket,
        "VoiceStart",
        SimpleNamespace(model_validate=lambda data: SimpleNamespace(**data)),
    )
    monkeypatch.setattr(live_socket, "get_session_factory", lambda: None)
    monkeypatch.setattr(live_socket, "LiveLedger", FakeLedger)
    monkeypatch.setattr(live_socket, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(live_socket, "LiveSession", FakeSession)
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        live_socket,
        "logger",
        SimpleNamespace(warning=lambda event, **kw: warnings.append((event, kw))),
    )
    return warnings


@pytest.mark.asyncio
async def test_cancelled_browser_reader_is_not_a_session_failure(patched) -> None:
    websocket = FakeWebSocket(browser_cancel=True)

    await asyncio.wait_for(live_socket.live_voice(websocket), timeout=10)  # must not raise

    session = FakeSession.instances[0]
    assert session.stopped is True
    assert websocket.closed is True
    assert not [e for e in websocket.sent if e.get("type") == "session.error"]
    assert patched == []


@pytest.mark.asyncio
async def test_shielded_close_is_bounded_and_logged(patched, monkeypatch) -> None:
    FakeSession.stop_hangs = True
    monkeypatch.setattr(live_socket, "SHIELDED_CLOSE_SECONDS", 0.2)
    websocket = FakeWebSocket(browser_cancel=True)

    await asyncio.wait_for(live_socket.live_voice(websocket), timeout=10)

    assert patched == [("live_voice_shielded_close_timeout", {"seconds": 0.2})]


def test_shielded_close_default_is_thirty_seconds() -> None:
    assert live_socket.SHIELDED_CLOSE_SECONDS == 30
