"""Offline checks of AlphaTrade's Live wire contract against the pinned official SDK."""

import base64
import json
from unittest.mock import AsyncMock

import pytest
from openai import AsyncOpenAI
from openai.types.live.client_event_param import ClientEventParam
from openai.types.live.server_event import ServerEvent
from openai.types.live.session_start_event_param import SessionStartEventParam
from pydantic import TypeAdapter, ValidationError

from app.agent_voice.live_protocol import session_start


def session_snapshot():
    # The final snapshot also uses status=active; the event identifies closure.
    return {
        "id": "live_fixture",
        "expires_at": 1893456000,
        "status": "active",
        "model": "gpt-live-1",
    }


def test_startup_uses_live_session_configuration_and_supported_history():
    history = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Explain the selected setup."}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Its evidence is still incomplete."}],
        },
    ]
    payload = session_start(history)
    validated = TypeAdapter(SessionStartEventParam).validate_python(payload)
    # The SDK declares Iterable history/content, so consume Pydantic's lazy
    # validators to validate each nested item as well as the startup envelope.
    validated["session"]["input"] = list(validated["session"]["input"])
    for item in validated["session"]["input"]:
        item["content"] = list(item["content"])
    assert validated == payload
    assert payload["type"] == "session.start"
    config = payload["session"]
    assert config["model"] == "gpt-live-1"
    assert config["audio"] == {
        "format": {"type": "audio/pcm", "rate": 24000},
        "output": {"voice": "marin"},
    }
    assert config["delegation"] == {"type": "client"}
    assert config["store"] is False
    assert config["input"] == history
    assert "delegat" in config["instructions"].lower()
    assert set(config) == {"model", "audio", "delegation", "store", "instructions", "input"}


@pytest.mark.parametrize(
    "command",
    [
        {
            "type": "session.input_audio.append",
            "audio": base64.b64encode(b"\x00\x00" * 240).decode(),
        },
        {"type": "session.input_audio.mute", "event_id": "mute_fixture"},
        {"type": "session.input_audio.unmute", "event_id": "unmute_fixture"},
        {
            "type": "session.thinking.append",
            "event_id": "thinking_fixture",
            "delegation_id": "delegation_fixture",
            "content": "The canonical Agent is checking the selected setup.",
        },
        {
            "type": "session.commentary.append",
            "event_id": "commentary_fixture",
            "delegation_id": "delegation_fixture",
            "content": "The Agent reports that the setup evidence is incomplete.",
        },
        {
            "type": "session.instructions.append",
            "event_id": "interrupt_fixture",
            "delegation_id": None,
            "content": "Stop speaking and listen for the caller's corrected request.",
        },
        {"type": "session.close", "event_id": "close_fixture"},
    ],
    ids=["audio", "mute", "unmute", "thinking", "commentary", "interrupt", "close"],
)
def test_standard_live_commands_match_sdk_without_dropped_fields(command):
    assert TypeAdapter(ClientEventParam).validate_python(command) == command


@pytest.mark.parametrize(
    "command",
    [
        {"type": "input_audio_buffer.commit"},
        {"type": "response.cancel"},
        {"type": "session.delegation.done", "delegation_id": "delegation_fixture"},
        {"type": "session.output_audio.clear"},
        {"type": "session.commentary.append", "content": "Missing delegation ID."},
    ],
)
def test_realtime_only_or_incomplete_commands_are_not_live_commands(command):
    with pytest.raises(ValidationError):
        TypeAdapter(ClientEventParam).validate_python(command)


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "session.started",
            "event_id": "started_fixture",
            "client_event_id": "start_fixture",
            "session": session_snapshot(),
        },
        {
            "type": "session.input_transcript.delta",
            "event_id": "input_fixture",
            "delta": "What about",
            "start_ms": 100,
            "end_ms": 200,
        },
        {
            "type": "session.output_transcript.delta",
            "event_id": "output_fixture",
            "delta": " I will check.",
            "start_ms": 150,
            "end_ms": 300,
        },
        {
            "type": "session.output_audio.delta",
            "delta": base64.b64encode(b"\x00\x00" * 240).decode(),
        },
        {
            "type": "session.delegation.created",
            "event_id": "delegation_created_fixture",
            "offset_ms": 210,
            "delegation": {
                "id": "delegation_fixture",
                "type": "delegation",
                "target": "client",
            },
        },
        {
            "type": "session.commentary.appended",
            "event_id": "commentary_appended_fixture",
            "client_event_id": "commentary_fixture",
            "start_ms": 300,
            "end_ms": 400,
        },
        {
            "type": "session.usage.updated",
            "event_id": "usage_fixture",
            "usage": {"seconds": 12.5},
            "context_window": {"usage_ratio": 0.25},
        },
        {
            "type": "session.closed",
            "event_id": "closed_fixture",
            "client_event_id": "close_fixture",
            "reason": "close_requested",
            "session": session_snapshot(),
            "usage": {"seconds": 15.25},
        },
    ],
    ids=["started", "user", "assistant", "audio", "delegation", "ack", "usage", "closed"],
)
def test_server_events_use_live_fragments_metadata_and_duration_usage(event):
    validated = TypeAdapter(ServerEvent).validate_python(event)
    assert validated.model_dump(exclude_unset=True) == event


async def test_native_sdk_primary_socket_uses_correct_url_and_serializes_commands(monkeypatch):
    socket = AsyncMock()
    socket.recv.side_effect = [
        json.dumps(
            {
                "type": "session.started",
                "event_id": "started_fixture",
                "session": session_snapshot(),
            }
        ),
        json.dumps(
            {
                "type": "session.closed",
                "event_id": "closed_fixture",
                "reason": "close_requested",
                "session": session_snapshot(),
                "usage": {"seconds": 1.5},
            }
        ),
    ]
    connector = AsyncMock(return_value=socket)
    monkeypatch.setattr("openai.lib._websocket._WebSocketConnect", connector)
    async with AsyncOpenAI(api_key="mock-only", max_retries=0) as client:
        async with client.live.connect(
            max_retries=0, websocket_connection_options={"max_size": 131072, "close_timeout": 1}
        ) as connection:
            assert connection._max_retries == 0
            await connection.send(session_start([]))
            started = await connection.recv()
            assert started.type == "session.started"
            await connection.session.input_audio.append(audio="AAA=")
            await connection.session.close(event_id="close_fixture")
            closed = await connection.recv()
            assert closed.type == "session.closed"
            assert closed.usage.seconds == 1.5
        connector.assert_awaited_once()
        assert connector.call_args.args == ("wss://api.openai.com/v1/live/sessions",)
        assert connector.call_args.kwargs["close_timeout"] == 1
        assert connector.call_args.kwargs["additional_headers"]["Authorization"] == (
            "Bearer mock-only"
        )
    sent = [json.loads(call.args[0]) for call in socket.send.await_args_list]
    assert [event["type"] for event in sent] == [
        "session.start",
        "session.input_audio.append",
        "session.close",
    ]
    socket.close.assert_awaited_once()
