"""Every model call records the SHA-256 of its system prompt (review item 5)."""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.persistence_firewall import install_persistence_firewall
from app.core.prompt_registry import load_prompt_record
from app.db.base import Base
from app.db.models import ModelCallAttempt as ModelCallAttemptRow
from app.db.models import Organization, User
from app.providers.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    LLMMessage,
    MockLLMProvider,
    OpenAILLMProvider,
)
from app.schemas.model_routing import (
    ModelContextScope,
    ModelResourceType,
    ModelRoutingPurpose,
    ModelTaskRequest,
)
from app.services.model_call_telemetry import ModelCallTelemetryService
from app.services.model_router import ModelRouter

ORG = uuid.UUID("00000000-0000-0000-0000-00000000d001")
USER = uuid.UUID("00000000-0000-0000-0000-00000000d002")
MODEL = "gpt-6-luna"
# interactive_agent_system v2 body ([Kn] citation instruction); v1 was 26d09c9b...
INTERACTIVE_V2_SHA256 = "d3435fa4d1eef3e770700c4182f03117aa1c274dd357be48e0735626123f4112"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: Any, _record: object) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    install_persistence_firewall()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Prompt Org"),
                User(id=USER, email="prompt@example.com", hashed_password="x"),
            ]
        )
        db.commit()
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


class _CapturingLLM:
    name = "capturing-llm"
    kind = MockLLMProvider().kind

    def __init__(self) -> None:
        self.requests: list[LLMCompletionRequest] = []

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        self.requests.append(request)
        return LLMCompletionResult(
            content='{"summary":"ok"}',
            model=request.model,
            provider=self.name,
            input_tokens=10,
            output_tokens=2,
            latency_ms=1.0,
        )


def _request(**prompt: Any) -> ModelTaskRequest:
    purpose = ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS
    return ModelTaskRequest(
        purpose=purpose,
        context=ModelContextScope(
            organization_id=ORG,
            user_id=USER,
            purpose=purpose,
            resource_type=ModelResourceType.CONVERSATION,
        ),
        correlation_id="prompt-hash",
        caller_organization_id=ORG,
        caller_user_id=USER,
        **prompt,
    )


def _router(llm: Any, session: Session | None = None) -> ModelRouter:
    return ModelRouter(
        llm,
        tier_a_model=MODEL,
        tier_b_model=MODEL,
        fail_closed=True,
        telemetry=ModelCallTelemetryService(session),
    )


def test_hash_matches_system_text_passed_to_provider_and_is_persisted(session: Session) -> None:
    llm = _CapturingLLM()
    prompt = load_prompt_record("interactive_agent_system")
    result = _router(llm, session).complete(
        _request(prompt_id=prompt.id, prompt_version=prompt.version),
        [LLMMessage(role="system", content=prompt.text), LLMMessage(role="user", content="hi")],
    )
    sent = [m.content for m in llm.requests[0].messages if m.role == "system"]
    assert sent == [prompt.text]
    attempt = result.attempts[0]
    assert attempt.system_prompt_sha256 == _sha(sent[0]) == prompt.sha256
    assert attempt.system_prompt_sha256 == INTERACTIVE_V2_SHA256
    assert (attempt.prompt_id, attempt.prompt_version) == ("interactive_agent_system", 2)
    row = session.scalars(select(ModelCallAttemptRow)).one()
    assert row.system_prompt_sha256 == _sha(sent[0])
    assert (row.prompt_id, row.prompt_version) == ("interactive_agent_system", 2)


def test_no_system_prompt_records_none(session: Session) -> None:
    result = _router(_CapturingLLM(), session).complete(
        _request(), [LLMMessage(role="user", content="hi")]
    )
    attempt = result.attempts[0]
    assert (attempt.system_prompt_sha256, attempt.prompt_id, attempt.prompt_version) == (
        None,
        None,
        None,
    )
    row = session.scalars(select(ModelCallAttemptRow)).one()
    assert (row.system_prompt_sha256, row.prompt_id, row.prompt_version) == (None, None, None)


def test_hash_equals_instructions_in_openai_responses_body() -> None:
    """The hash covers the exact ``instructions`` the Responses transport would send."""
    bodies: list[dict[str, Any]] = []

    def capture(_self: object, _url: str, body: dict[str, Any], **_kw: object) -> dict[str, Any]:
        bodies.append(body)
        raise RuntimeError("no network in tests")

    provider = OpenAILLMProvider(
        api_key="test-key", base_url="https://x.invalid", model=MODEL, fail_closed=True
    )
    messages = [
        LLMMessage(role="system", content="Rendered system text.\n"),
        LLMMessage(role="user", content="hi"),
    ]
    with patch.object(OpenAILLMProvider, "_post_json", capture):
        result = _router(provider).complete(_request(), messages)
    assert bodies and bodies[0]["instructions"] == "Rendered system text.\n"
    # Failed attempts carry the hash too: the prompt reached the transport.
    assert result.attempts and not result.attempts[0].success
    assert result.attempts[0].system_prompt_sha256 == _sha(bodies[0]["instructions"])


def test_conversation_and_capture_callers_send_their_prompt_ids() -> None:
    from app.agent_capture.model import CaptureModel
    from app.core.config import Settings
    from app.interactive_agent.conversation import ModelConversationalResponder

    settings = Settings(execution_mode="paper", enable_real_trading=False, log_json=False)
    seen: list[ModelTaskRequest] = []
    original = ModelRouter.complete

    def spy(self: ModelRouter, request: ModelTaskRequest, messages: Any) -> Any:
        seen.append(request)
        return original(self, request, messages)

    llm = _CapturingLLM()
    with patch.object(ModelRouter, "complete", spy):
        ModelConversationalResponder(None, settings, llm_provider=llm).compose(  # type: ignore[arg-type]
            organization_id=ORG,
            user_id=USER,
            conversation_id=uuid.uuid4(),
            message="hello",
            factual_context="none",
        )
        with contextlib.suppress(Exception):
            CaptureModel(settings, llm_provider=llm).plan(  # type: ignore[arg-type]
                organization_id=ORG, user_id=USER, conversation_id=uuid.uuid4(), content={}
            )
    assert [(r.prompt_id, r.prompt_version) for r in seen] == [
        ("interactive_agent_system", 2),
        ("agent_capture_system", 1),
    ]
    prompt_ids = ("interactive_agent_system", "agent_capture_system")
    for request, prompt_id in zip(llm.requests, prompt_ids, strict=True):
        system = next(m.content for m in request.messages if m.role == "system")
        assert _sha(system) == load_prompt_record(prompt_id).sha256


def test_narrative_router_path_records_prompt_file_and_rendered_hash() -> None:
    from app.schemas.agent import AgentState
    from app.schemas.analysis import TradingAnalysisDetail
    from app.schemas.common import RiskSeverity
    from app.services.narrative_service import NarrativeService

    class _Recording(MockLLMProvider):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[LLMCompletionRequest] = []

        def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
            self.requests.append(request)
            return super().complete(request)

    llm = _Recording()
    router = ModelRouter(llm, tier_a_model=MODEL, tier_b_model=MODEL)
    service = NarrativeService(llm_provider=llm, llm_model=MODEL, model_router=router)
    analysis = TradingAnalysisDetail.model_validate(
        {
            "summary": "No trade.",
            "risk_level": RiskSeverity.MEDIUM,
            "stop_loss_or_no_trade_reason": "No trade.",
            "approval_status": "not_required",
            "market_data_quality": "mock",
        }
    )
    agent = AgentState(
        request_id="narr-hash", message="analyze btc", organization_id=ORG, user_id=USER
    )
    service.enhance(agent, analysis)
    attempt = router.attempts_for("narr-hash")[0]
    system = next(m.content for m in llm.requests[0].messages if m.role == "system")
    assert (attempt.prompt_id, attempt.prompt_version) == ("trading_analysis_narrative", 1)
    # Rendered (context-filled) text, not the template, is what gets hashed.
    assert attempt.system_prompt_sha256 == _sha(system)
    assert attempt.system_prompt_sha256 != load_prompt_record("trading_analysis_narrative").sha256


@pytest.mark.parametrize(
    ("url", "bounded"),
    [("postgresql+psycopg://offline@localhost/offline", True), ("sqlite:///:memory:", False)],
)
@pytest.mark.parametrize(
    ("direction", "revisions"),
    [
        ("upgrade", "a27obsretention001:a28promptsha001"),
        ("downgrade", "a28promptsha001:a27obsretention001"),
    ],
)
def test_prompt_hash_migration_bounds_its_lock_wait_on_postgres(
    monkeypatch: pytest.MonkeyPatch, url: str, bounded: bool, direction: str, revisions: str
) -> None:
    import io
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    buffer = io.StringIO()
    # No ini file: env.py then skips fileConfig, so test logging is untouched.
    config = Config(output_buffer=buffer)
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/app/db/migrations")
    )
    getattr(command, direction)(config, revisions, sql=True)
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    ddl = [i for i, line in enumerate(lines) if line.startswith("ALTER TABLE model_call_attempts")]
    assert len(ddl) == 3
    if not bounded:
        assert not any("lock_timeout" in line for line in lines)
        return
    assert lines[ddl[0] - 1] == "SET LOCAL lock_timeout = '5s';"
    assert lines[ddl[-1] + 1] == "SET LOCAL lock_timeout = DEFAULT;"
