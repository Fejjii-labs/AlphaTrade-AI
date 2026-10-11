"""Actual authenticated WS/SQL composition with a synthetic SDK, never live voice."""

import asyncio
import base64
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.agent_voice.config import load_limits
from app.api.routes import agent_voice
from app.core.config import get_settings
from app.db.models import Chunk, ConversationMessage, Document, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.providers.base import ProviderKind
from app.providers.llm import LLMCompletionResult
from app.schemas.common import DocumentSourceType
from tests.test_agent_realtime_persistence import voice_database as voice_database
from tests.test_agent_realtime_protocol import voice_limits as voice_limits


class SyntheticConnection:
    """Speaks pinned official event shapes; performs no provider/network call."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.messages = []
        self.response = 0
        self.utterance = 0
        self.tool_message = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.queue.get()
        return SimpleNamespace(model_dump=lambda **_: event)

    async def emit(self, event):
        await self.queue.put(event)

    async def send(self, event):
        self.messages.append(event)
        kind = event["type"]
        if kind == "session.update":
            await self.emit({"type": "session.updated", "session": event["session"]})
        elif kind == "response.create":
            self.response += 1
            response_id = f"synthetic-response-{self.response}"
            await self.emit({"type": "response.created", "response": {"id": response_id}})
            if event["response"]["tool_choice"] != "none":
                self.utterance += 1
                await self.emit(
                    {
                        "type": "response.function_call_arguments.done",
                        "response_id": response_id,
                        "call_id": f"synthetic-tool-{self.utterance}",
                        "name": "alpha_trade_turn",
                        "arguments": json.dumps(
                            {
                                "message": self.tool_message
                                or f"Synthetic spoken request {self.utterance}: "
                                "challenge my unsupported BTC assumption"
                            }
                        ),
                    }
                )
                await self.done(response_id)
            else:
                # Keep spoken output active until explicit interruption. This is
                # synthetic PCM, not microphone or measured provider acceptance.
                await self.emit(
                    {
                        "type": "response.output_audio.delta",
                        "item_id": response_id,
                        "delta": base64.b64encode(b"\0\0" * 240).decode(),
                    }
                )
        elif kind == "response.cancel":
            await self.done(f"synthetic-response-{self.response}", status="cancelled")

    async def done(self, response_id, *, status="completed"):
        await self.emit(
            {
                "type": "response.done",
                "response": {
                    "id": response_id,
                    "status": status,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "input_token_details": {"text_tokens": 100, "audio_tokens": 0},
                    },
                },
            }
        )


@pytest.fixture
def voice_client(voice_database, monkeypatch):
    database = voice_database
    connection = SyntheticConnection()
    creations = []
    calls = []

    class SyntheticSDK:
        def __init__(self, **kwargs):
            creations.append(kwargs)
            self.realtime = SimpleNamespace(connect=lambda **_: connection)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class SyntheticLLM:
        name, kind = "synthetic-unmeasured", ProviderKind.LLM

        def complete(self, request):
            calls.append(request)
            content = (
                '{"entries":[],"clarification":null}'
                if request.response_format
                else "Synthetic grounded reply: native fees and current performance "
                "remain unavailable."
            )
            return LLMCompletionResult(
                content=content,
                model=request.model,
                provider=self.name,
                input_tokens=100,
                output_tokens=50,
                fallback_used=False,
            )

    monkeypatch.setattr(agent_voice, "AsyncOpenAI", SyntheticSDK)
    monkeypatch.setattr(agent_voice, "load_limits", lambda _: database.limits)
    monkeypatch.setattr(agent_voice, "get_settings", lambda: database.settings)
    monkeypatch.setattr(agent_voice, "get_session_factory", lambda: database.sessions)
    monkeypatch.setattr(
        "app.agent_voice.service.resolve_providers", lambda _: SimpleNamespace(llm=SyntheticLLM())
    )
    app = create_app(database.settings)
    app.dependency_overrides[get_settings] = lambda: database.settings

    def session():
        with database.sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session
    client = TestClient(app)
    try:
        yield SimpleNamespace(
            client=client, db=database, upstream=connection, creations=creations, calls=calls
        )
    finally:
        client.close()


def test_actual_voice_config_requires_auth_and_reports_unbound_provider(voice_client, monkeypatch):
    assert voice_client.client.get("/agent/voice/config").status_code == 401
    monkeypatch.setattr(agent_voice, "load_limits", load_limits)
    response = voice_client.client.get(
        "/agent/voice/config", headers={"Authorization": "Bearer " + voice_client.db.token}
    )
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "realtime_not_enabled"
    assert voice_client.creations == []


def test_ws_origin_and_invalid_token_fail_before_upstream(voice_client):
    with (
        pytest.raises(WebSocketDisconnect) as denial,
        voice_client.client.websocket_connect(
            "/agent/voice/realtime", headers={"origin": "https://untrusted.invalid"}
        ),
    ):
        pass
    assert denial.value.code == 1008
    with voice_client.client.websocket_connect(
        "/agent/voice/realtime", headers={"origin": voice_client.db.settings.cors_origins[0]}
    ) as websocket:
        websocket.send_json(
            {
                "type": "session.start",
                "access_token": "invalid-local-token",
                "conversation_id": str(voice_client.db.conversation_id),
            }
        )
        assert websocket.receive_json()["type"] == "session.error"
    assert voice_client.creations == []


def receive_type(websocket, target):
    for _ in range(12):
        event = websocket.receive_json()
        assert event["type"] not in {"session.error", "turn.uncertain"}, event
        if event["type"] == target:
            return event
    raise AssertionError(f"No {target} event within bounded event count")


def test_actual_ws_delegates_two_turns_interrupts_and_retains_shared_sql_context(voice_client):
    fixture = voice_client
    keys = []
    with fixture.client.websocket_connect(
        "/agent/voice/realtime", headers={"origin": fixture.db.settings.cors_origins[0]}
    ) as websocket:
        websocket.send_json(
            {
                "type": "session.start",
                "access_token": fixture.db.token,
                "conversation_id": str(fixture.db.conversation_id),
            }
        )
        receive_type(websocket, "session.ready")
        for number in (1, 2):
            websocket.send_json(
                {"type": "audio.append", "audio": base64.b64encode(b"\0\0" * 240).decode()}
            )
            websocket.send_json({"type": "audio.commit"})
            pending = receive_type(websocket, "turn.pending")
            assert pending["body"]["message"].startswith(f"Synthetic spoken request {number}")
            keys.append(pending["request_key"])
            websocket.send_json({"type": "turn.accept", "request_key": keys[-1]})
            result = receive_type(websocket, "turn.result")["result"]
            assert result["conversation_id"] == str(fixture.db.conversation_id)
            assert result["execution_attempted"] is False
            assert "native_blofin_fills" in result["recorded_evidence"]
            audio = receive_type(websocket, "audio.delta")
            websocket.send_json(
                {"type": "response.interrupt", "item_id": audio["item_id"], "audio_end_ms": 0}
            )
            receive_type(websocket, "playback.clear")
            receive_type(websocket, "response.done")
        websocket.send_json({"type": "session.end"})
    assert len(set(keys)) == 2
    assert len(fixture.creations) == 1
    assert fixture.creations[0]["max_retries"] == 0
    assert (
        sum(event["type"] == "conversation.item.truncate" for event in fixture.upstream.messages)
        == 2
    )
    narrative = [call for call in fixture.calls if not call.response_format]
    assert len(narrative) == 2
    assert any("Synthetic spoken request 1" in message.content for message in narrative[1].messages)
    with fixture.db.sessions() as session:
        messages = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == fixture.db.conversation_id
            )
        ).all()
        assert len([row for row in messages if row.role.value == "user"]) == 2
        assert len([row for row in messages if row.role.value == "assistant"]) == 2
        # The shared coordinator also persists one system reservation per turn.
        assert len([row for row in messages if row.role.value == "system"]) == 2


def test_authenticated_ws_uses_owned_knowledge_and_reports_unfunded_vector_limit(voice_client):
    """Actual route/SQL proof; the SDK and model remain synthetic and unmeasured."""
    fixture = voice_client
    peer_id, foreign_org_id = uuid4(), uuid4()
    expected_documents = set()
    with fixture.db.sessions() as session:
        session.add_all(
            [
                User(
                    id=peer_id,
                    email=f"{peer_id}@example.invalid",
                    hashed_password="synthetic-unused",
                ),
                Organization(id=foreign_org_id, name="Synthetic foreign knowledge"),
            ]
        )
        session.flush()
        for organization_id, user_id, title, content in (
            (
                fixture.db.organization_id,
                fixture.db.user_id,
                "Owned observation",
                "Liquidity patience: OWNED_REFERENCE says wait for confirmed evidence.",
            ),
            (
                fixture.db.organization_id,
                None,
                "Shared observation",
                "Liquidity patience: SHARED_REFERENCE keeps monitoring separate from orders.",
            ),
            (
                fixture.db.organization_id,
                peer_id,
                "Peer private observation",
                "Liquidity patience: PEER_PRIVATE_SECRET must not enter this conversation.",
            ),
            (
                foreign_org_id,
                fixture.db.user_id,
                "Foreign tenant observation",
                "Liquidity patience: FOREIGN_TENANT_SECRET must not enter this conversation.",
            ),
        ):
            document = Document(
                id=uuid4(),
                organization_id=organization_id,
                user_id=user_id,
                title=title,
                source_type=DocumentSourceType.GENERAL_NOTE,
            )
            session.add(document)
            session.flush()
            session.add(
                Chunk(
                    id=uuid4(),
                    document_id=document.id,
                    organization_id=organization_id,
                    user_id=user_id,
                    ordinal=0,
                    content=content,
                )
            )
            if organization_id == fixture.db.organization_id and user_id in (
                fixture.db.user_id,
                None,
            ):
                expected_documents.add(str(document.id))
        session.commit()
    fixture.upstream.tool_message = "Search my knowledge for liquidity patience"
    with fixture.client.websocket_connect(
        "/agent/voice/realtime", headers={"origin": fixture.db.settings.cors_origins[0]}
    ) as websocket:
        websocket.send_json(
            {
                "type": "session.start",
                "access_token": fixture.db.token,
                "conversation_id": str(fixture.db.conversation_id),
            }
        )
        receive_type(websocket, "session.ready")
        websocket.send_json(
            {"type": "audio.append", "audio": base64.b64encode(b"\0\0" * 240).decode()}
        )
        websocket.send_json({"type": "audio.commit"})
        pending = receive_type(websocket, "turn.pending")
        websocket.send_json({"type": "turn.accept", "request_key": pending["request_key"]})
        result = receive_type(websocket, "turn.result")["result"]
        assert {hit["document_id"] for hit in result["knowledge"]} == expected_documents
        assert {hit["retrieval_mode"] for hit in result["knowledge"]} == {"lexical_store"}
        assert any(
            "embedding" in note.lower() and "unavailable" in note.lower()
            for note in result["limitations"]
        )
        assert result["execution_attempted"] is False
        websocket.send_json({"type": "session.end"})
    narrative = [call for call in fixture.calls if not call.response_format]
    assert len(narrative) == 1
    prompt = "\n".join(message.content for message in narrative[0].messages)
    assert "OWNED_REFERENCE" in prompt and "SHARED_REFERENCE" in prompt
    assert "PEER_PRIVATE_SECRET" not in prompt and "FOREIGN_TENANT_SECRET" not in prompt
