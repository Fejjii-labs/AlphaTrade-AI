"""Deterministic official-protocol evidence, never actual voice/model acceptance."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openai.types.realtime.conversation_item_create_event_param import (
    ConversationItemCreateEventParam,
)
from openai.types.realtime.conversation_item_truncate_event_param import (
    ConversationItemTruncateEventParam,
)
from openai.types.realtime.response_create_event_param import ResponseCreateEventParam
from openai.types.realtime.session_update_event_param import SessionUpdateEventParam
from pydantic import TypeAdapter, ValidationError

from app.agent_voice.config import VoiceLimits, VoiceUnavailableError, load_limits
from app.agent_voice.contracts import VoiceStart
from app.agent_voice.protocol import checked_audio, create_response, session_update
from app.agent_voice.service import RealtimeSession
from app.core.config import Settings


@pytest.fixture
def voice_limits():
    now = datetime.now(UTC)
    price = {
        "model": "gpt-realtime",
        "source": "https://example.invalid/fixture-only",
        "valid_until": now + timedelta(hours=1),
        "maximum_input_tokens": 20000,
        "input_usd_per_million": "1",
        "output_usd_per_million": "2",
    }
    # No fixture rate is a verified price or supported-model live measurement.
    return VoiceLimits(
        organization_id=str(uuid4()),
        budget_reference="synthetic-existing-budget",
        budget_as_of=now - timedelta(seconds=1),
        valid_until=now + timedelta(hours=1),
        remaining_budget_usd="1",
        session_ceiling_usd="1",
        maximum_turns=2,
        maximum_responses=4,
        maximum_seconds=30,
        maximum_audio_seconds=5,
        realtime=price
        | {
            "audio_input_usd_per_million": "3",
            "audio_output_usd_per_million": "4",
            "maximum_audio_input_tokens_per_second": "100",
        },
        reasoning=price | {"model": Settings(_env_file=None).agent_reasoning_model},
    )


def test_pinned_official_sdk_validates_session_and_response_transport(voice_limits):
    TypeAdapter(SessionUpdateEventParam).validate_python(session_update(voice_limits))
    TypeAdapter(ResponseCreateEventParam).validate_python(create_response(voice_limits))
    TypeAdapter(ResponseCreateEventParam).validate_python(
        create_response(voice_limits, grounded=True)
    )
    TypeAdapter(ConversationItemCreateEventParam).validate_python(
        {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": "call1",
                "output": "canonical scoped reply",
            },
        }
    )
    TypeAdapter(ConversationItemTruncateEventParam).validate_python(
        {
            "type": "conversation.item.truncate",
            "item_id": "audio1",
            "content_index": 0,
            "audio_end_ms": 120,
        }
    )
    session = session_update(voice_limits)["session"]
    assert "transcription" not in session["audio"]["input"]
    assert session["audio"]["input"]["turn_detection"]["create_response"] is False
    assert session["truncation"] == "disabled"
    assert [tool["name"] for tool in session["tools"]] == ["alpha_trade_turn"]


@pytest.mark.parametrize(
    "change",
    [
        {"remaining_budget_usd": "NaN"},
        {"session_ceiling_usd": "1.01"},
        {"maximum_responses": 1},
        {"valid_until": datetime.now()},
    ],
)
def test_receipts_reject_nonfinite_overbudget_or_undated_inputs(voice_limits, change):
    with pytest.raises(ValidationError):
        VoiceLimits.model_validate(voice_limits.model_dump() | change)


def test_full_context_orphan_audio_and_shared_router_retries_are_reserved(voice_limits):
    settings = Settings(_env_file=None)
    expected = (
        Decimal(
            4 * (20000 * 3 + 1024 * 4)
            + 5 * 100 * 3
            + 2 * 4 * (20000 + settings.agent_max_output_tokens * 2)
        )
        / 1000000
    )
    assert voice_limits.reservation(settings) == expected


@pytest.mark.parametrize(
    "change,reason",
    [
        ({}, "realtime_not_enabled"),
        ({"agent_realtime_enabled": True}, "provider_credential_unbound"),
        (
            {"agent_realtime_enabled": True, "openai_api_key": "fixture"},
            "approved_live_route_unavailable",
        ),
        (
            {"agent_realtime_enabled": True, "openai_api_key": "fixture", "provider_mode": "live"},
            "verified_realtime_limits_unbound",
        ),
    ],
)
def test_missing_bindings_fail_closed_before_provider(tmp_path, change, reason):
    with pytest.raises(VoiceUnavailableError, match=reason):
        load_limits(Settings(_env_file=None).model_copy(update=change))


@pytest.mark.parametrize(
    "encoded,remaining",
    [
        ("!", 100),
        (base64.b64encode(b"x").decode(), 100),
        (base64.b64encode(b"xx").decode(), 1),
        (base64.b64encode(b"xx" * 8193).decode(), 20000),
    ],
)
def test_audio_format_and_server_limit_refuse_before_transport(encoded, remaining):
    with pytest.raises(VoiceUnavailableError):
        checked_audio(encoded, remaining)


class FakeLedger:
    def __init__(self, limits):
        self.limits = limits
        self.id, self.conversation_id = uuid4(), uuid4()
        self.settings = Settings(_env_file=None)
        self.events = []
        self.keys = {}

    def response(self):
        self.events.append("admitted")

    def response_started(self, key):
        self.events.append(("started", key))

    def response_done(self, result):
        self.events.append(("done", result["id"]))

    def activate(self):
        self.events.append("active")

    def audio(self, length):
        self.events.append(("audio_admitted", length))

    def close(self):
        self.events.append("closed")

    def tool(self, call_id, body):
        key = self.keys.setdefault(call_id, str(uuid4()))
        self.events.append(("pending", key, body))
        return key, True

    def tool_done(self, call_id, result):
        self.events.append(("committed", call_id, result))


def session_for(limits, turn=None):
    ledger = FakeLedger(limits)
    sent, upstream = [], []

    async def send(event):
        sent.append(event)

    async def provider_send(event):
        upstream.append(event)

    start = VoiceStart(
        type="session.start", access_token="fixture-only", conversation_id=ledger.conversation_id
    )
    session = RealtimeSession(ledger, start, SimpleNamespace(send=provider_send), send, turn=turn)
    session.ready = True
    return session, ledger, sent, upstream


@pytest.mark.asyncio
async def test_effective_provider_session_confirms_caps_before_audio_readiness(voice_limits):
    session, ledger, sent, _ = session_for(voice_limits)
    session.ready = False
    await session.provider(
        {"type": "session.updated", "session": session_update(voice_limits)["session"]}
    )
    assert session.ready and ledger.events == ["active"]
    assert sent == [{"type": "session.ready", "session_id": str(ledger.id)}]


@pytest.mark.asyncio
async def test_effective_provider_auto_response_or_asr_refused_before_audio(voice_limits):
    for enabled in ("create_response", "interrupt_response", "transcription"):
        session, ledger, sent, _ = session_for(voice_limits)
        session.ready = False
        effective = session_update(voice_limits)["session"]
        if enabled == "transcription":
            effective["audio"]["input"][enabled] = {"model": "unapproved-transcription"}
        else:
            effective["audio"]["input"]["turn_detection"][enabled] = True
        with pytest.raises(VoiceUnavailableError, match="audio_limits_not_confirmed"):
            await session.provider({"type": "session.updated", "session": effective})
        assert not session.ready and not ledger.events and not sent


@pytest.mark.asyncio
async def test_bounded_large_provider_pcm_chunk_is_playable_while_browser_upload_cap_remains(
    voice_limits,
):
    session, _ledger, sent, _ = session_for(voice_limits)
    session.response_grounded = True
    encoded = base64.b64encode(b"xx" * 16000).decode()
    await session.provider(
        {"type": "response.output_audio.delta", "delta": encoded, "item_id": "server-audio"}
    )
    assert sent == [{"type": "audio.delta", "audio": encoded, "item_id": "server-audio"}]
    with pytest.raises(VoiceUnavailableError, match="audio_limit"):
        checked_audio(encoded, 65536)


@pytest.mark.asyncio
async def test_audio_admission_precedes_forwarding_and_arbitrary_provider_controls_refused(
    voice_limits,
):
    session, ledger, _, upstream = session_for(voice_limits)
    await session.client({"type": "audio.append", "audio": base64.b64encode(b"xx").decode()})
    assert ledger.events == [("audio_admitted", 2)]
    assert upstream[0]["type"] == "input_audio_buffer.append"
    with pytest.raises(VoiceUnavailableError, match="unsupported_client"):
        await session.client({"type": "session.update", "session": {"model": "unapproved"}})
    with pytest.raises(ValidationError):
        await session.client({"type": "audio.append", "audio": "eHg=", "usage": {"cost": 0}})


@pytest.mark.asyncio
async def test_ungrounded_audio_never_reaches_browser(voice_limits):
    session, _, sent, _ = session_for(voice_limits)
    with pytest.raises(VoiceUnavailableError, match="ungrounded"):
        await session.provider(
            {"type": "response.output_audio.delta", "delta": "eHg=", "item_id": "unsafe"}
        )
    assert not sent


@pytest.mark.asyncio
async def test_consecutive_grounded_turns_ack_before_shared_work_and_reply_before_audio(
    voice_limits,
):
    calls = []

    def turn(_ledger, key, body):
        calls.append((key, body))
        return {
            "reply": "Canonical stale quote; unknown fees.",
            "conversation_id": body["conversation_id"],
        }

    session, _ledger, sent, upstream = session_for(voice_limits, turn)
    for number in range(2):
        if session.response_active:
            await session.provider(
                {"type": "response.created", "response": {"id": f"spoken{number}"}}
            )
            await session.provider(
                {
                    "type": "response.done",
                    "response": {"id": f"spoken{number}", "status": "completed", "usage": None},
                }
            )
        await session._respond()
        response_id = f"tool{number}"
        await session.provider({"type": "response.created", "response": {"id": response_id}})
        await session.provider(
            {
                "type": "response.function_call_arguments.done",
                "response_id": response_id,
                "name": "alpha_trade_turn",
                "call_id": f"call{number}",
                "arguments": json.dumps({"message": f"Request {number} and my prior context"}),
            }
        )
        await session.provider(
            {
                "type": "response.done",
                "response": {"id": response_id, "status": "completed", "usage": None},
            }
        )
        for _ in range(200):
            if len([e for e in sent if e["type"] == "turn.pending"]) == number + 1:
                break
            await asyncio.sleep(0.001)
        assert len(calls) == number
        pending = [e for e in sent if e["type"] == "turn.pending"][-1]
        await session.client({"type": "turn.accept", "request_key": pending["request_key"]})
        await session.tool_task
        assert calls[-1] == (pending["request_key"], pending["body"])
        assert sent[-1]["type"] == "turn.result"
        assert upstream[-1]["response"]["tool_choice"] == "none"
    assert calls[0][1]["conversation_id"] == calls[1][1]["conversation_id"]
    assert len({key for key, _ in calls}) == 2


@pytest.mark.asyncio
async def test_cancelled_tool_arguments_do_not_capture_partial_utterance(voice_limits):
    session, ledger, sent, _ = session_for(
        voice_limits, lambda *a: pytest.fail("cancelled utterance captured")
    )
    await session._respond()
    await session.provider({"type": "response.created", "response": {"id": "cancelled"}})
    await session.provider(
        {
            "type": "response.function_call_arguments.done",
            "response_id": "cancelled",
            "name": "alpha_trade_turn",
            "call_id": "partial",
            "arguments": '{"message":"save this partial"}',
        }
    )
    await session.provider(
        {"type": "response.done", "response": {"id": "cancelled", "status": "cancelled"}}
    )
    await session.tool_task
    assert sent[-1]["type"] == "input.interrupted"
    assert not ledger.keys


@pytest.mark.asyncio
async def test_interrupt_truncates_actual_item_without_blocking_tool_thread(voice_limits):
    session, _, sent, upstream = session_for(voice_limits)
    session.response_active, session.audio_item, session.audio_bytes = True, "audio1", 48000
    await session.client({"type": "response.interrupt", "item_id": "audio1", "audio_end_ms": 250})
    assert [event["type"] for event in upstream] == [
        "response.cancel",
        "conversation.item.truncate",
    ]
    assert sent[-1]["type"] == "playback.clear"
    with pytest.raises(VoiceUnavailableError, match="invalid_playback"):
        await session.client(
            {"type": "playback.truncate", "item_id": "foreign", "audio_end_ms": 250}
        )


@pytest.mark.asyncio
async def test_reader_remains_responsive_while_cancel_ack_is_pending(voice_limits):
    session, _, _, upstream = session_for(voice_limits)
    await session._respond()
    await session.provider({"type": "response.created", "response": {"id": "old"}})
    await session.provider({"type": "input_audio_buffer.speech_started"})
    await asyncio.wait_for(session.provider({"type": "input_audio_buffer.speech_stopped"}), 0.2)
    assert not session.response_request.done()
    await session.provider(
        {"type": "response.done", "response": {"id": "old", "status": "cancelled"}}
    )
    await session.response_request
    assert upstream[-1]["type"] == "response.create"
    await session.stop()
