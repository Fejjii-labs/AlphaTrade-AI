"""Focused synthetic Live/Agent evidence: no provider, trading or hosted services."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.contracts import VoiceStart
from app.agent_voice.live_accounting import LiveLedger
from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS, LiveLimits, load_live_limits
from app.agent_voice.live_protocol import context_parts
from app.agent_voice.live_service import LiveSession
from app.agent_voice.service import shared_turn
from app.api.routes.agent_voice import voice_config
from app.core.config import Settings
from app.db.models import ConversationMessage, Membership, OrganizationQuota, UsageEvent, User
from app.db.voice_realtime import VoiceSessionReservation
from app.providers.base import ProviderKind
from app.providers.llm import LLMCompletionResult
from app.schemas.common import MembershipRole
from app.schemas.conversation import ConversationCreate
from app.security.tokens import create_access_token
from app.services.conversation_service import ConversationService
from tests.test_workflows import ORG_ID, USER_ID


@pytest.fixture
def live_limits():
    now = datetime.now(UTC)
    return LiveLimits(
        organization_id=str(ORG_ID),
        budget_reference="synthetic-existing-budget",
        budget_as_of=now - timedelta(seconds=1),
        valid_until=now + timedelta(hours=1),
        remaining_budget_usd="1",
        session_ceiling_usd="1",
        maximum_seconds=30,
        maximum_audio_seconds=30,
        maximum_turns=2,
        live={
            "source": "https://example.invalid/fixture-only",
            "valid_until": now + timedelta(hours=1),
            "usd_per_second": "0.0008",
        },
        reasoning={
            "model": Settings(_env_file=None).agent_reasoning_model,
            "source": "https://example.invalid/fixture-only",
            "valid_until": now + timedelta(hours=1),
            "maximum_input_tokens": 20000,
            "input_usd_per_million": "1",
            "output_usd_per_million": "2",
        },
    )


@pytest.fixture
def live_ledger(workflow_db, live_limits):
    sessions, settings = workflow_db
    settings = settings.model_copy(
        update={
            "provider_mode": "mock",
            "market_data_provider": "mock",
            "openai_api_key": "",
            "jwt_secret": "synthetic-live-fixture-key-at-least-32-characters",
        }
    )
    with sessions() as session:
        session.get(User, USER_ID).email_verified = True
        session.add(OrganizationQuota(organization_id=ORG_ID))
        conversation = ConversationService(session).create(
            ConversationCreate(), organization_id=ORG_ID, user_id=USER_ID
        )
        conversation_id = conversation.id
        session.commit()
    token, _ = create_access_token(
        user_id=USER_ID, organization_id=ORG_ID, email="wf@test.example", settings=settings
    )
    ledger = LiveLedger(sessions, settings, live_limits, token, conversation_id)
    ledger.reserve()
    ledger.started("live_fixture")
    yield ledger
    ledger.close()


def test_activation_is_disabled_and_live_uses_existing_budget_routes(live_limits):
    settings = Settings(_env_file=None, openai_api_key="", provider_mode="mock")
    assert not settings.agent_live_enabled and not settings.agent_realtime_enabled
    with pytest.raises(VoiceUnavailableError, match="gpt_live_not_enabled"):
        load_live_limits(settings)
    config = voice_config(SimpleNamespace(organization_id=ORG_ID), settings)
    assert config.available is False and config.provider == "realtime"
    config = voice_config(
        SimpleNamespace(organization_id=ORG_ID),
        settings.model_copy(update={"agent_live_enabled": True}),
    )
    assert config.available is False and config.provider == "gpt_live"
    expected = (
        Decimal(30 + CLOSE_SECONDS + CLEANUP_SECONDS) * Decimal("0.0008")
        + Decimal(2 * 4 * (20000 + settings.agent_max_output_tokens * 2)) / 1000000
    )
    assert live_limits.reservation(settings) == expected
    assert live_limits.reserved_tokens(settings) == 2 * 4 * (
        20000 + settings.agent_max_output_tokens
    )


def test_verified_receipts_required_even_with_live_flag(tmp_path, live_limits):
    path = tmp_path / "live-limits.json"
    path.write_text(live_limits.model_dump_json())
    settings = Settings(_env_file=None, provider_mode="mock", openai_api_key="fixture")
    settings = settings.model_copy(
        update={
            "agent_live_enabled": True,
            "provider_mode": "live",
            "agent_live_limits_file": str(path),
        }
    )
    assert load_live_limits(settings) == live_limits
    path.write_text(
        live_limits.model_copy(
            update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)}
        ).model_dump_json()
    )
    with pytest.raises(VoiceUnavailableError, match="expired"):
        load_live_limits(settings)


def test_cumulative_usage_once_finalized_and_caption_replay_once(live_ledger):
    ledger = live_ledger
    fragment = {"event_id": "f1", "delta": "I may be wrong", "start_ms": 100, "end_ms": 400}
    ledger.transcript("user", fragment)
    ledger.transcript("user", fragment)
    for seconds in [12, 15, 15]:
        ledger.usage({"usage": {"seconds": seconds}})
    final = {
        "session": {"id": "live_fixture"},
        "usage": {"seconds": 15},
        "reason": "close_requested",
    }
    ledger.usage(final, final=True)
    ledger.usage(final, final=True)
    ledger.close()
    with ledger.sessions() as session:
        events = session.scalars(
            select(UsageEvent).where(UsageEvent.feature == "agent_voice_live")
        ).all()
        row = session.get(VoiceSessionReservation, ledger.id)
        assert len(events) == 1 and events[0].estimated_cost == Decimal("0.012")
        assert events[0].total_tokens == 0
        assert len(row.ledger["transcripts"]) == 1
        assert row.status == "closed" and row.accounted_cost == Decimal("0.012")
        assert row.ledger["live"]["finalized"] is True


@pytest.mark.parametrize("seconds", [True, -1, float("nan"), float("inf"), "1"])
def test_invalid_usage_never_releases_reserved_budget(live_ledger, seconds):
    with pytest.raises(VoiceUnavailableError, match="invalid_live_usage"):
        live_ledger.usage({"usage": {"seconds": seconds}})
    live_ledger.close()
    with live_ledger.sessions() as session:
        row = session.get(VoiceSessionReservation, live_ledger.id)
        assert row.status == "uncertain" and row.accounted_cost is None


def test_regression_foreign_final_and_missing_finalization_keep_hold(live_ledger):
    ledger = live_ledger
    ledger.usage({"usage": {"seconds": 15}})
    with pytest.raises(VoiceUnavailableError, match="regressed"):
        ledger.usage({"usage": {"seconds": 12}})
    with pytest.raises(VoiceUnavailableError, match="mismatch"):
        ledger.usage({"usage": {"seconds": 15}, "session": {"id": "foreign"}}, final=True)
    ledger.close()
    with ledger.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        assert row.status == "uncertain" and row.accounted_cost is None
        assert row.ledger["live"]["seconds"] == "15" and not row.ledger["live"]["finalized"]


def test_existing_quota_and_current_membership_block_live(live_ledger):
    ledger = live_ledger
    with ledger.sessions() as session:
        session.scalar(select(OrganizationQuota)).monthly_cost_limit = Decimal("0.01")
        session.commit()
    other = LiveLedger(
        ledger.sessions,
        ledger.settings,
        ledger.limits.model_copy(update={"remaining_budget_usd": Decimal("5")}),
        ledger.token,
        ledger.conversation_id,
    )
    with pytest.raises(VoiceUnavailableError, match="monthly_cost"):
        other.reserve()
    with ledger.sessions() as session:
        session.scalar(select(Membership)).role = MembershipRole.VIEWER
        session.commit()
    with pytest.raises(VoiceUnavailableError, match="permission"):
        ledger.audio(2)
    ledger.usage(
        {"usage": {"seconds": 2}, "session": {"id": "live_fixture"}, "reason": "connection_lost"},
        final=True,
    )
    ledger.close()  # Final accounting/cleanup works after authorization is revoked.


def test_shared_agent_grounding_storage_exact_replay_and_secondary_usage(live_ledger, monkeypatch):
    ledger = live_ledger
    requests = []

    class SyntheticLLM:
        name, kind = "synthetic", ProviderKind.LLM

        def complete(self, request):
            requests.append(request)
            content = (
                json.dumps({"entries": [], "clarification": None})
                if request.response_format
                else "Saved rules may be outdated. Native account fees are unavailable."
            )
            return LLMCompletionResult(
                content=content,
                model=request.model,
                provider=self.name,
                input_tokens=100,
                output_tokens=50,
                fallback_used=False,
            )

    monkeypatch.setattr(
        "app.agent_voice.service.resolve_providers", lambda _: SimpleNamespace(llm=SyntheticLLM())
    )
    for number, message in enumerate(
        ["My BTC risk rule may be outdated.", "What did I say about my BTC rule?"]
    ):
        body = {"message": message, "conversation_id": str(ledger.conversation_id)}
        key, _ = ledger.tool(f"delegation{number}", body)
        result = shared_turn(ledger, key, body)
        ledger.tool_done(f"delegation{number}", result)
        count = len(requests)
        assert shared_turn(ledger, key, body) == result and len(requests) == count
        assert result["authority_mutated"] is False and result["execution_attempted"] is False
        assert "native_blofin_fills" in result["recorded_evidence"]
    with ledger.sessions() as session:
        rows = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == ledger.conversation_id
            )
        ).all()
        assert len([r for r in rows if r.role.value == "user"]) == 2
        assert len([r for r in rows if r.role.value == "assistant"]) == 2
        assert session.scalars(select(UsageEvent).where(UsageEvent.feature == "agent_chat")).all()
    assert any(
        "No provider event proves utterance completeness" in m.content
        for r in requests
        for m in r.messages
    )
    assert len(ledger.history()) == 4


class FakeLedger:
    def __init__(self, limits):
        self.limits, self.id, self.conversation_id = limits, uuid4(), uuid4()
        self.keys, self.events = {}, []

    def history(self):
        return []

    def started(self, identifier):
        self.events.append(("started", identifier))

    def audio(self, length):
        self.events.append(("audio", length))

    def transcript(self, role, fragment):
        self.events.append((role, fragment))

    def usage(self, event, final=False):
        self.events.append(("usage", event, final))

    def tool(self, identifier, body):
        self.keys[identifier] = str(uuid4())
        self.events.append(("pending", body))
        return self.keys[identifier], True

    def tool_done(self, identifier, result):
        self.events.append(("acknowledged", identifier, result))

    def close(self):
        self.events.append("closed")


def live_session(limits, turn=None):
    ledger, sent, upstream = FakeLedger(limits), [], []

    async def send(event):
        sent.append(event)

    async def provider_send(event):
        upstream.append(event)

    start = VoiceStart(
        type="session.start", access_token="fixture", conversation_id=ledger.conversation_id
    )
    session = LiveSession(
        ledger,
        start,
        SimpleNamespace(send=provider_send),
        send,
        turn=turn
        or (
            lambda _ledger, _key, body: {
                "reply": "Fees unavailable; risk rule provisional.",
                "conversation_id": body["conversation_id"],
            }
        ),
    )
    return session, ledger, sent, upstream


async def ready(session):
    await session.start_session()
    await session.provider(
        {
            "type": "session.started",
            "session": {
                "id": "live_fixture",
                "model": "gpt-live-1",
                "audio": {"format": {"type": "audio/pcm", "rate": 24000}},
                "delegation": {"type": "client"},
                "store": False,
            },
        }
    )


async def fragment(session, identifier, text, offset):
    await session.provider(
        {
            "type": "session.input_transcript.delta",
            "event_id": identifier,
            "delta": text,
            "start_ms": offset - 100,
            "end_ms": offset,
        }
    )


async def delegate(session, sent, identifier, offset):
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": offset,
            "delegation": {"id": identifier, "target": "client"},
        }
    )
    for _ in range(100):
        pending = [
            e for e in sent if e["type"] == "turn.pending" and e["request_key"] in session.accepts
        ]
        if pending:
            break
        await asyncio.sleep(0.002)
    assert pending
    await session.client({"type": "turn.accept", "request_key": pending[-1]["request_key"]})
    await asyncio.wait_for(session.tool_task, timeout=1)


@pytest.mark.asyncio
async def test_continuous_delegation_pending_receipt_two_canonical_results(live_limits):
    session, ledger, sent, upstream = live_session(live_limits)
    await ready(session)
    assert upstream[0]["session"]["delegation"] == {"type": "client"}
    for number in range(2):
        await fragment(session, f"f{number}", f"My request {number}", (number + 1) * 1000)
        await delegate(session, sent, f"d{number}", (number + 1) * 1000)
    results = [e for e in sent if e["type"] == "turn.result"]
    assert len(results) == 2 and len({e["request_key"] for e in results}) == 2
    assert results[0]["result"]["conversation_id"] == results[1]["result"]["conversation_id"]
    assert (
        upstream[-1]["type"] == "session.commentary.append"
        and upstream[-1]["delegation_id"] == "d1"
    )
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": 2000,
            "delegation": {"id": "d1", "target": "client"},
        }
    )
    assert len(session.tasks) == 2
    await session.stop()
    assert ledger.events[-1] == "closed"


@pytest.mark.asyncio
async def test_audio_caps_arbitrary_controls_and_manual_interrupt_resume(live_limits):
    session, ledger, sent, upstream = live_session(live_limits)
    with pytest.raises(VoiceUnavailableError, match="not_ready"):
        await session.client({"type": "audio.append", "audio": base64.b64encode(b"xx").decode()})
    await ready(session)
    audio = base64.b64encode(b"xx" * 50).decode()
    await session.client({"type": "audio.append", "audio": audio})
    assert (
        ledger.events[-1] == ("audio", 100) and upstream[-1]["type"] == "session.input_audio.append"
    )
    for event in [
        {"type": "response.create"},
        {"type": "session.usage.updated", "usage": {"seconds": 0}},
        {"type": "playback.truncate", "item_id": "x"},
        {"type": "response.interrupt", "audio_end_ms": 0},
    ]:
        with pytest.raises(VoiceUnavailableError):
            await session.client(event)
    await session.client({"type": "response.interrupt"})
    assert sent[-1] == {"type": "playback.clear", "muted": True}
    assert upstream[-1]["type"] == "session.instructions.append"
    await session.provider({"type": "session.output_audio.delta", "delta": audio})
    assert not any(e["type"] == "audio.delta" for e in sent)
    await session.provider(
        {"type": "session.instructions.appended", "client_event_id": upstream[-1]["event_id"]}
    )
    assert session.output_muted
    await session.client({"type": "playback.resume"})
    await session.provider({"type": "session.output_audio.delta", "delta": audio})
    assert sent[-1]["type"] == "audio.delta"
    await session.stop()


@pytest.mark.asyncio
async def test_late_correction_suppresses_obsolete_spoken_result(live_limits):

    def turn(_ledger, _key, body):

        assert release.wait(timeout=2)
        return {"reply": "Provisional earlier reply.", "conversation_id": body["conversation_id"]}

    import threading

    release = threading.Event()
    session, _ledger, sent, upstream = live_session(live_limits, turn)
    await ready(session)
    await fragment(session, "f1", "Thursday", 1000)
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": 1000,
            "delegation": {"id": "d1", "target": "client"},
        }
    )
    for _ in range(100):
        if session.accepts:
            break
        await asyncio.sleep(0.002)
    await session.client({"type": "turn.accept", "request_key": next(iter(session.accepts))})
    await fragment(session, "f2", "Actually Friday", 1500)
    release.set()
    await asyncio.wait_for(session.tool_task, timeout=1)
    assert not any(
        e["type"] in {"session.thinking.append", "session.commentary.append"} for e in upstream
    )
    assert any(e["type"] == "turn.result" for e in sent)
    await session.stop()


@pytest.mark.asyncio
async def test_missing_transcript_and_finalization_do_not_invent_a_turn(live_limits):
    session, ledger, sent, upstream = live_session(live_limits)
    await ready(session)
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": 1000,
            "delegation": {"id": "d1", "target": "client"},
        }
    )
    await asyncio.wait_for(session.tool_task, timeout=3)
    assert not any(e["type"] == "turn.pending" for e in sent)
    assert "unavailable" in upstream[-1]["content"]
    await session.request_close()
    assert upstream[-1]["type"] == "session.close" and not session.finalized.is_set()
    await session.provider(
        {
            "type": "session.closed",
            "session": {"id": "live_fixture"},
            "usage": {"seconds": 1},
            "reason": "close_requested",
        }
    )
    assert session.finalized.is_set() and ledger.events[-1][2] is True
    await session.stop()


def test_context_append_chunks_preserve_unicode_and_all_limitations():
    text = "Saved interpretation is uncertain. " + "界" * 900
    parts = context_parts(text)
    assert "".join(parts) == text and all(len(part.encode()) <= 480 for part in parts)


@pytest.mark.asyncio
async def test_queued_delegation_is_fenced_by_correction(live_limits):
    session, _ledger, sent, upstream = live_session(live_limits)
    await ready(session)
    blocker = asyncio.create_task(asyncio.sleep(0.04))
    session.tool_task = blocker
    await fragment(session, "f1", "Thursday", 1000)
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": 1000,
            "delegation": {"id": "d1", "target": "client"},
        }
    )
    await fragment(session, "f2", "Actually Friday", 1500)
    await session.tool_task
    assert not any(e["type"] == "turn.pending" for e in sent)
    assert not any(e["type"] == "session.commentary.append" for e in upstream)
    await session.stop()


@pytest.mark.asyncio
async def test_delayed_matching_transcript_can_supply_pending_delegation(live_limits):
    session, _ledger, sent, _upstream = live_session(live_limits)
    await ready(session)
    await session.provider(
        {
            "type": "session.delegation.created",
            "offset_ms": 1000,
            "delegation": {"id": "d1", "target": "client"},
        }
    )
    await asyncio.sleep(0.01)
    await fragment(session, "late", "My rule may be outdated", 1000)
    for _ in range(100):
        if session.accepts:
            break
        await asyncio.sleep(0.002)
    await session.client({"type": "turn.accept", "request_key": next(iter(session.accepts))})
    await session.tool_task
    assert (
        next(e for e in sent if e["type"] == "turn.pending")["body"]["message"]
        == "My rule may be outdated"
    )
    await session.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["end", "disconnect", "authorization", "provider_error", "missing_final"]
)
async def test_socket_drains_final_usage_after_end_disconnect_auth_or_provider_error(
    monkeypatch, live_limits, mode
):
    from fastapi import WebSocketDisconnect

    from app.agent_voice import live_socket
    from app.core.config import Settings

    ledger = FakeLedger(live_limits)
    ledger.reserve = lambda: ledger.events.append("reserved")

    def update(_callback):
        if mode == "authorization":
            raise VoiceUnavailableError("voice_permission_denied")

    ledger.update = update
    monkeypatch.setattr(live_socket, "LiveLedger", lambda *args: ledger)
    monkeypatch.setattr(live_socket, "get_session_factory", lambda: object())
    monkeypatch.setattr(live_socket, "load_live_limits", lambda _: live_limits)
    monkeypatch.setattr(live_socket, "CLOSE_SECONDS", 0.08)
    settings = Settings(
        _env_file=None,
        cors_origins=["https://fixture.invalid"],
        openai_api_key="fixture",
        provider_mode="mock",
    )
    monkeypatch.setattr(live_socket, "get_settings", lambda: settings)
    queue = asyncio.Queue()
    started = asyncio.Event()
    upstream_sent = []

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            upstream_sent.append({"type": "transport.closed"})

        def __aiter__(self):
            return self

        async def __anext__(self):
            item = await queue.get()
            return SimpleNamespace(model_dump=lambda **kwargs: item)

        async def send(self, event):
            upstream_sent.append(event)
            if event["type"] == "session.start":
                await queue.put(
                    {
                        "type": "session.started",
                        "session": {
                            "id": "live_fixture",
                            "model": "gpt-live-1",
                            "audio": {"format": {"type": "audio/pcm", "rate": 24000}},
                            "delegation": {"type": "client"},
                            "store": False,
                        },
                    }
                )
                if mode == "provider_error":
                    await queue.put(
                        {"type": "error", "error": {"message": "NEVER EXPOSE PROVIDER SECRET"}}
                    )
            if event["type"] == "session.close" and mode != "missing_final":
                # Caption work must not invalidate the final-receipt reader after
                # authorization revocation or session expiry has begun cleanup.
                await queue.put(
                    {
                        "type": "session.input_transcript.delta",
                        "event_id": "late-drain",
                        "delta": "late",
                        "start_ms": 100,
                        "end_ms": 200,
                    }
                )
                await queue.put(
                    {
                        "type": "session.closed",
                        "session": {"id": "live_fixture"},
                        "usage": {"seconds": 2},
                        "reason": "close_requested",
                    }
                )

    class Client:
        def __init__(self, **kwargs):
            self.live = SimpleNamespace(connect=lambda **kwargs: Upstream())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(live_socket, "AsyncOpenAI", Client)

    class Browser:
        def __init__(self):
            self.headers = {"origin": "https://fixture.invalid"}
            self.sent = []
            self.first = True

        async def accept(self):
            pass

        async def close(self, **kwargs):
            self.sent.append({"type": "browser.closed"})

        async def send_json(self, event):
            self.sent.append(event)
            if event["type"] == "session.ready":
                started.set()

        async def receive_text(self):
            if self.first:
                self.first = False
                return json.dumps(
                    {
                        "type": "session.start",
                        "access_token": "fixture",
                        "conversation_id": str(ledger.conversation_id),
                    }
                )
            await started.wait()
            if mode in {"authorization", "provider_error"}:
                await asyncio.Event().wait()
            if mode == "disconnect":
                raise WebSocketDisconnect()
            return json.dumps({"type": "session.end"})

    browser = Browser()
    await asyncio.wait_for(live_socket.live_voice(browser), timeout=2)
    finalized = any(e[0] == "usage" and e[2] is True for e in ledger.events if isinstance(e, tuple))
    assert finalized is (mode != "missing_final")
    assert not any(e[0] == "user" for e in ledger.events if isinstance(e, tuple))
    assert ledger.events[0] == "reserved" and ledger.events[-1] == "closed"
    assert sum(e["type"] == "session.close" for e in upstream_sent) == 1
    assert upstream_sent[-1]["type"] == "transport.closed"
    assert "NEVER EXPOSE" not in json.dumps(browser.sent)
    assert next(e for e in browser.sent if e["type"] == "session.closed")["finalized"] is finalized
