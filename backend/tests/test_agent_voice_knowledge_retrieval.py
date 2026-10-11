"""Synthetic provider evidence for bounded voice Knowledge; never live parity."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from app.agent_voice.config import EmbeddingPrice, VoiceLimits, VoiceUnavailableError
from app.agent_voice.retrieval import VoiceEmbeddingsProvider, embedding_limitation
from app.agent_voice.service import shared_turn
from app.core.config import Environment
from app.core.errors import ServiceUnavailableError
from app.db.models import Membership, Organization, OrganizationQuota, UsageEvent, User
from app.db.voice_realtime import VoiceSessionReservation
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.turn_runtime import run_interactive
from app.interactive_agent.vector_adapter import AgentVectorAdapter
from app.providers.base import ProviderKind
from app.providers.embeddings import EmbeddingResult, OpenAIEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore, VectorPoint
from app.repositories.usage import UsageRepository
from app.schemas.common import CostSource, MembershipRole, UsageStatus
from app.services.rag_service import RAG_COLLECTION
from app.services.turn_context import turn_context
from app.services.turn_coordinator import TurnCoordinator
from tests.test_agent_realtime_persistence import ledger_for
from tests.test_agent_realtime_persistence import voice_database as voice_database
from tests.test_agent_realtime_protocol import voice_limits as voice_limits
from tests.test_interactive_agent_foundation import _add_chunk


def price_for(database, **changes):
    return EmbeddingPrice(
        model=database.settings.embeddings_model,
        source="https://example.invalid/synthetic-price-not-live-evidence",
        valid_until=datetime.now(UTC) + timedelta(minutes=20),
        maximum_input_tokens=4000,
        input_usd_per_million=Decimal("0.1"),
    ).model_copy(update=changes)


class SyntheticLLM:
    name, kind = "synthetic-unmeasured", ProviderKind.LLM

    def __init__(self):
        self.calls = []

    def complete(self, request):
        from app.providers.llm import LLMCompletionResult

        self.calls.append(request)
        return LLMCompletionResult(
            content='{"entries":[],"clarification":null}'
            if request.response_format
            else "Synthetic reply grounded in available documents; uncertainty remains.",
            model=request.model,
            provider=self.name,
            input_tokens=100,
            output_tokens=50,
        )


class SyntheticEmbeddings:
    name, kind, dimensions = "openai-embeddings", ProviderKind.EMBEDDINGS, 2

    def __init__(self, database, *, change=None, fail=False):
        self.database, self.change, self.fail = database, change or {}, fail
        self.calls = []
        self.ledger = None
        self.key = None

    def embed_with_metadata(self, texts):
        self.calls.append(texts)
        # Provider work observes an already committed admission and full reservation.
        if self.ledger:
            with self.database.sessions() as session:
                row = session.get(VoiceSessionReservation, self.ledger.id)
                assert row.ledger["embedding_attempts"][self.key]["state"] == "transport_may_start"
                assert row.reserved_cost == self.ledger.limits.reservation(self.ledger.settings)
        if self.fail:
            raise TimeoutError("synthetic lost embedding reply")
        return replace(
            EmbeddingResult(
                vectors=[[1.0, 0.0]],
                dimensions=2,
                model=self.database.settings.embeddings_model,
                provider=self.name,
                input_tokens=12,
            ),
            **self.change,
        )


def world(database, monkeypatch, *, receipt=True, credential=True, environment=None, **changes):
    database.settings = database.settings.model_copy(
        update={
            "provider_mode": "live",
            "openai_api_key": "synthetic-not-a-credential" if credential else "",
            **({"environment": environment} if environment else {}),
        }
    )
    if receipt:
        database.limits = database.limits.model_copy(update={"embedding": price_for(database)})
    store, llm, embeddings = (
        InMemoryVectorStore(),
        SyntheticLLM(),
        SyntheticEmbeddings(database, **changes),
    )
    bundle = SimpleNamespace(llm=llm, embeddings=embeddings, vector_store=store)
    monkeypatch.setattr("app.agent_voice.service.resolve_providers", lambda _: bundle)
    monkeypatch.setattr("app.interactive_agent.vector_adapter.resolve_providers", lambda _: bundle)
    vector_hits = []
    original_search = AgentVectorAdapter.search

    def observed_search(adapter, **kwargs):
        hits = original_search(adapter, **kwargs)
        vector_hits.append(hits)
        return hits

    monkeypatch.setattr(AgentVectorAdapter, "search", observed_search)
    peer_id, foreign_id, foreign_org = uuid4(), uuid4(), uuid4()
    with database.sessions() as session:
        session.add_all(
            [
                User(id=peer_id, email=f"{peer_id}@example.invalid", hashed_password="unused"),
                User(
                    id=foreign_id, email=f"{foreign_id}@example.invalid", hashed_password="unused"
                ),
                Organization(id=foreign_org, name="Foreign knowledge tenant"),
            ]
        )
        session.flush()
        rows = [
            _add_chunk(
                session,
                organization_id=org,
                user_id=user,
                title=title,
                content=f"quasar funding playbook {detail}",
            )
            for org, user, title, detail in [
                (database.organization_id, database.user_id, "Owned playbook", "OWNED PASSAGE"),
                (database.organization_id, None, "Shared playbook", "SHARED PASSAGE"),
                (database.organization_id, peer_id, "Private peer", "PEER SECRET"),
                (foreign_org, foreign_id, "Foreign playbook", "FOREIGN SECRET"),
            ]
        ]
        for row in rows:
            row.chunk_metadata = {"title": "Synthetic playbook", "source_type": "trading_playbook"}
        session.commit()
        ids = [r.id for r in rows]
        store.upsert(
            RAG_COLLECTION,
            [
                VectorPoint(
                    str(r.id),
                    [1.0, 0.0],
                    {
                        "organization_id": str(r.organization_id),
                        "user_id": str(r.user_id) if r.user_id else None,
                        "source_type": "trading_playbook",
                    },
                )
                for r in rows
            ],
        )
    ledger = ledger_for(database)
    ledger.reserve()
    ledger.activate()
    body = {
        "message": "What does the quasar funding guidance say? Do not save this request.",
        "conversation_id": str(database.conversation_id),
    }
    key, _ = ledger.tool("original-call", body)
    embeddings.ledger, embeddings.key = ledger, key
    return SimpleNamespace(
        ledger=ledger,
        key=key,
        body=body,
        embeddings=embeddings,
        llm=llm,
        ids=ids,
        store=store,
        vector_hits=vector_hits,
    )


def assert_scope(result, scenario, *, mode):
    assert {h["chunk_id"] for h in result["knowledge"]} == {str(i) for i in scenario.ids[:2]}
    # The passage builder truthfully relabels displayed SQL passages lexical_store,
    # even after vector selection. Prove vector selection at the actual adapter.
    assert all(h["retrieval_mode"] == "lexical_store" for h in result["knowledge"])
    if mode == "vector":
        assert len(scenario.vector_hits) == 1
        assert {h.chunk_id for h in scenario.vector_hits[0]} == set(scenario.ids[:2])
        assert all(h.retrieval_mode == "vector" for h in scenario.vector_hits[0])
    else:
        assert not scenario.vector_hits
    assert "OWNED PASSAGE" in result["recorded_evidence"]
    assert "SHARED PASSAGE" in result["recorded_evidence"]
    prompts = "\n".join(m.content for r in scenario.llm.calls for m in r.messages)
    assert "OWNED PASSAGE" in prompts and "SHARED PASSAGE" in prompts
    assert "PEER SECRET" not in json.dumps(result) + prompts
    assert "FOREIGN SECRET" not in json.dumps(result) + prompts
    assert result["authority_mutated"] is False and result["execution_attempted"] is False


def test_shared_voice_vector_scope_verified_cost_and_restart_exact_recovery(
    voice_database, monkeypatch
):
    scenario = world(voice_database, monkeypatch)
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert_scope(result, scenario, mode="vector")
    scenario.ledger.tool_done("original-call", result)
    replay_ledger = ledger_for(voice_database)
    replay_ledger.id = scenario.ledger.id
    replay_ledger.organization_id, replay_ledger.user_id = (
        scenario.ledger.organization_id,
        scenario.ledger.user_id,
    )
    assert shared_turn(replay_ledger, scenario.key, scenario.body) == result
    assert len(scenario.embeddings.calls) == 1
    with voice_database.sessions() as session:
        events = session.scalars(select(UsageEvent).where(UsageEvent.feature == "rag_search")).all()
        assert len(events) == 1
        event = events[0]
        assert event.id == uuid5(UUID(scenario.key), "voice-embedding:query")
        admission = session.scalar(
            select(UsageEvent).where(UsageEvent.provider == "agent_turn_admission")
        )
        assert event.request_id == admission.request_id
        assert event.user_id == voice_database.user_id
        assert event.total_tokens == 12 and event.estimated_cost == Decimal("0.00000120")
        assert event.cost_source == CostSource.STATIC_ESTIMATED
        assert event.provider_reported_cost is None and not event.cost_is_placeholder
        usage = UsageRepository(session)
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.provider == "agent_turn_admission")
            )
            == 1
        )
        assert (
            usage.count_requests_since(
                organization_id=voice_database.organization_id,
                since=voice_database.limits.budget_as_of,
                feature="rag_search",
            )
            == 0
        )
        # Existing reply/capture suffix events remain conservative admissions.
        # Voice embedding adds none, matching the same text workflow below.
        assert (
            usage.count_requests_since(
                organization_id=voice_database.organization_id,
                since=voice_database.limits.budget_as_of,
            )
            == 3
        )
        summary = usage.summarize(
            organization_id=voice_database.organization_id, since=voice_database.limits.budget_as_of
        )
        assert summary.total_tokens == sum(
            r.total_tokens for r in session.scalars(select(UsageEvent))
        )
        assert summary.total_cost >= event.estimated_cost


def test_text_shared_pipeline_keeps_default_vector_retrieval(voice_database, monkeypatch):
    scenario = world(voice_database, monkeypatch)
    scenario.embeddings.ledger = None
    coordinator = TurnCoordinator(voice_database.sessions)
    result = coordinator.run(
        channel="interactive",
        key=UUID(scenario.key),
        body=scenario.body,
        organization_id=voice_database.organization_id,
        user_id=voice_database.user_id,
        conversation_id=voice_database.conversation_id,
        work=lambda reservation: run_interactive(
            coordinator,
            voice_database.settings,
            reservation,
            AgentTurnRequest.model_validate(scenario.body),
            llm_provider=scenario.llm,
        ),
    )
    assert_scope(result, scenario, mode="vector")
    assert len(scenario.embeddings.calls) == 1
    with voice_database.sessions() as session:
        usage = UsageRepository(session)
        assert (
            usage.count_requests_since(
                organization_id=voice_database.organization_id,
                since=voice_database.limits.budget_as_of,
            )
            == 3
        )
        assert (
            usage.count_requests_since(
                organization_id=voice_database.organization_id,
                since=voice_database.limits.budget_as_of,
                feature="rag_search",
            )
            == 0
        )


@pytest.mark.parametrize("missing", ["receipt", "credential"])
@pytest.mark.parametrize("strict", [False, True])
def test_unavailable_embedding_is_explicit_and_sql_follows_same_policy(
    voice_database, monkeypatch, missing, strict
):
    scenario = world(
        voice_database,
        monkeypatch,
        receipt=missing != "receipt",
        credential=missing != "credential",
        environment=Environment.STAGING if strict else None,
    )
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert not scenario.embeddings.calls
    assert any("embedding unavailable" in note.lower() for note in result["limitations"])
    if strict:
        assert result["knowledge"] == []
        assert any("refuses" in note for note in result["limitations"])
    else:
        assert_scope(result, scenario, mode="lexical_store")
    with voice_database.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.feature == "rag_search")
            )
            == 0
        )


@pytest.mark.parametrize(
    "change",
    [
        {"input_tokens": 0},
        {"input_tokens": 4001},
        {"input_tokens": True},
        {"input_tokens": None},
        {"model": "unverified-route"},
        {"model": None},
        {"fallback_used": True},
        {"vectors": None},
        {"vectors": [[float("nan"), 0.0]]},
    ],
)
def test_unverified_embedding_result_never_claims_vector_or_refunds(
    voice_database, monkeypatch, change
):
    scenario = world(voice_database, monkeypatch, change=change)
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert_scope(result, scenario, mode="lexical_store")
    assert any("Vector retrieval unavailable" in note for note in result["limitations"])
    assert shared_turn(scenario.ledger, scenario.key, scenario.body) == result
    assert len(scenario.embeddings.calls) == 1
    scenario.ledger.tool_done("original-call", result)
    scenario.ledger.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, scenario.ledger.id)
        assert row.status == "uncertain" and row.accounted_cost is None
        event = session.get(UsageEvent, uuid5(UUID(scenario.key), "voice-embedding:query"))
        assert event.cost_source == CostSource.UNAVAILABLE and event.cost_is_placeholder
        assert event.status == UsageStatus.FAILURE and event.input_tokens == 0


def test_lost_embedding_reply_persists_unknown_without_retry(voice_database, monkeypatch):
    scenario = world(voice_database, monkeypatch, fail=True)
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert_scope(result, scenario, mode="lexical_store")
    assert shared_turn(scenario.ledger, scenario.key, scenario.body) == result
    assert len(scenario.embeddings.calls) == 1
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, scenario.ledger.id)
        assert row.ledger["embedding_attempts"][scenario.key]["state"] == "uncertain"
        turn_id = UUID(row.ledger["embedding_attempts"][scenario.key]["request_id"])
        assert session.get(
            UsageEvent, uuid5(UUID(scenario.key), "voice-embedding:query")
        ).cost_is_placeholder
    with (
        turn_context(turn_id, voice_database.sessions),
        pytest.raises(VoiceUnavailableError, match="voice_embedding_already_admitted"),
    ):
        scenario.ledger.embedding_attempt(scenario.key, scenario.body["message"])


@pytest.mark.parametrize("refusal", ["quota", "body", "cap", "auth", "reservation"])
def test_embedding_admission_refuses_before_transport(voice_database, monkeypatch, refusal):
    scenario = world(voice_database, monkeypatch)
    reservation, _ = TurnCoordinator(voice_database.sessions).reserve(
        channel="interactive",
        key=scenario.key,
        body=scenario.body,
        organization_id=voice_database.organization_id,
        user_id=voice_database.user_id,
        conversation_id=voice_database.conversation_id,
    )
    query = scenario.body["message"]
    if refusal == "quota":
        with voice_database.sessions() as session:
            quota = session.scalar(select(OrganizationQuota))
            quota.monthly_token_limit = 0
            session.commit()
    elif refusal == "body":
        query = "substituted body"
    elif refusal == "cap":
        query = "界" * 2000
    elif refusal == "auth":
        with voice_database.sessions() as session:
            session.scalar(select(Membership)).role = MembershipRole.VIEWER
            session.commit()
    else:
        scenario.ledger.limits = scenario.ledger.limits.model_copy(
            update={"embedding": price_for(voice_database, input_usd_per_million=Decimal("0.2"))}
        )
    guarded = VoiceEmbeddingsProvider(scenario.embeddings, scenario.ledger, scenario.key)
    reason = {
        "quota": "voice_embedding_quota_exhausted",
        "body": "voice_embedding_turn_identity_conflict",
        "cap": "voice_embedding_input_cap",
        "auth": "voice_permission_denied",
        "reservation": "voice_embedding_reservation_mismatch",
    }[refusal]
    with (
        turn_context(reservation.turn_id, voice_database.sessions),
        pytest.raises(VoiceUnavailableError, match=reason),
    ):
        guarded.embed_with_metadata([query])
    assert not scenario.embeddings.calls
    with voice_database.sessions() as session:
        assert not session.get(VoiceSessionReservation, scenario.ledger.id).ledger[
            "embedding_attempts"
        ]


def test_optional_embedding_reservation_token_cost_and_receipt_expiry(voice_database):
    ledger = ledger_for(voice_database)
    old = ledger.limits.reservation(ledger.settings)
    deadline = datetime.now(UTC) + timedelta(seconds=20)
    price = price_for(voice_database, valid_until=deadline)
    ledger.limits = ledger.limits.model_copy(update={"embedding": price})
    assert ledger.limits.reservation(ledger.settings) == old + Decimal("0.0008")
    ledger.reserve()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        assert datetime.fromisoformat(row.ledger["expires_at"]) == deadline
        assert row.reserved_tokens >= price.maximum_input_tokens * ledger.limits.maximum_turns
    # No receipt means an honest limitation; configured receipt/model never guessed.
    ledger.limits = ledger.limits.model_copy(update={"embedding": None})
    assert "no verified embedding spending receipt" in embedding_limitation(ledger)


def test_embedding_reservation_cannot_exceed_approved_balance(voice_database):
    ledger = ledger_for(voice_database)
    ledger.limits = ledger.limits.model_copy(
        update={"embedding": price_for(voice_database, input_usd_per_million=Decimal("1000"))}
    )
    with pytest.raises(VoiceUnavailableError, match="approved_budget_exhausted"):
        ledger.reserve()
    with voice_database.sessions() as session:
        assert session.get(VoiceSessionReservation, ledger.id) is None


def test_embedding_without_canonical_turn_context_refuses_before_transport(
    voice_database, monkeypatch
):
    scenario = world(voice_database, monkeypatch)
    guarded = VoiceEmbeddingsProvider(scenario.embeddings, scenario.ledger, scenario.key)
    with pytest.raises(VoiceUnavailableError, match="voice_embedding_canonical_turn_required"):
        guarded.embed_with_metadata([scenario.body["message"]])
    assert not scenario.embeddings.calls


def test_adapter_setup_failure_has_honest_policy_fallback_without_embedding(
    voice_database, monkeypatch
):
    scenario = world(voice_database, monkeypatch)

    def unavailable(_settings):
        raise RuntimeError("synthetic unavailable vector configuration")

    monkeypatch.setattr("app.interactive_agent.vector_adapter.resolve_providers", unavailable)
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert_scope(result, scenario, mode="lexical_store")
    assert any("Vector retrieval unavailable" in note for note in result["limitations"])
    assert not scenario.embeddings.calls


@pytest.mark.parametrize("invalid", ["expired", "model", "naive"])
def test_optional_embedding_receipt_failure_is_explicit(voice_database, invalid):
    ledger = ledger_for(voice_database)
    price = price_for(voice_database)
    if invalid == "expired":
        price = price.model_copy(update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)})
    elif invalid == "model":
        price = price.model_copy(update={"model": "unverified-route"})
    else:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="timezone"):
            VoiceLimits.model_validate(
                ledger.limits.model_dump()
                | {"embedding": price.model_dump() | {"valid_until": datetime.now()}}
            )
        return
    ledger.limits = ledger.limits.model_copy(update={"embedding": price})
    assert "embedding unavailable" in embedding_limitation(ledger).lower()


def official_sdk_payload(model, *, change=None):
    return {
        "model": model,
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
        "data": [{"index": 0, "embedding": [1.0, 0.0]}],
        **(change or {}),
    }


def synthetic_official_sdk(monkeypatch, payload, *, scenario=None, transport_failure=False):
    calls, clients = [], []

    class SyntheticSDK:
        def __init__(self, **kwargs):
            clients.append(kwargs)
            self.embeddings = SimpleNamespace(create=self.create)

        def create(self, **body):
            if scenario:
                with scenario.ledger.sessions() as session:
                    row = session.get(VoiceSessionReservation, scenario.ledger.id)
                    assert (
                        row.ledger["embedding_attempts"][scenario.key]["state"]
                        == "transport_may_start"
                    )
            calls.append(body)
            if transport_failure:
                raise TimeoutError("synthetic lost raw SDK reply")
            return SimpleNamespace(model_dump=lambda **_: payload)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr("app.providers.embeddings.OpenAI", SyntheticSDK)
    return calls, clients


@pytest.mark.parametrize(
    "change",
    [
        {"model": None},
        {"model": ""},
        {"model": "unverified-route"},
        {"usage": None},
        {"usage": {"total_tokens": 12}},
        {"usage": {"prompt_tokens": True}},
        {"usage": {"prompt_tokens": 0}},
        {"usage": {"prompt_tokens": -1}},
        {"usage": {"prompt_tokens": "12"}},
        {"usage": {"prompt_tokens": 12.5}},
    ],
)
def test_strict_official_embedding_receipt_refuses_raw_missing_or_coerced_metadata(
    monkeypatch, change
):
    model = "configured-synthetic-route-not-model-support-evidence"
    calls, clients = synthetic_official_sdk(monkeypatch, official_sdk_payload(model, change=change))
    provider = OpenAIEmbeddingsProvider(
        model=model,
        api_key="synthetic-not-a-credential",
        base_url="https://example.invalid",
        dimensions=2,
        fail_closed=True,
        require_usage_receipt=True,
    )
    with pytest.raises(ServiceUnavailableError, match="Embeddings provider is unavailable"):
        provider.embed_with_metadata(["quasar"])
    assert len(calls) == len(clients) == 1
    assert clients[0]["max_retries"] == 0
    assert clients[0]["timeout"].read == 30 and clients[0]["timeout"].connect == 5


def test_ordinary_embedding_defaults_are_unchanged_while_raw_strict_receipt_is_optional(
    monkeypatch,
):
    model = "configured-synthetic-route-not-model-support-evidence"
    calls, _clients = synthetic_official_sdk(
        monkeypatch,
        official_sdk_payload(model, change={"model": None, "usage": {"prompt_tokens": "12"}}),
    )
    result = OpenAIEmbeddingsProvider(
        model=model,
        api_key="synthetic-not-a-credential",
        base_url="https://example.invalid",
        dimensions=2,
        fail_closed=True,
    ).embed_with_metadata(["quasar"])
    assert result.model == model and result.input_tokens == 12
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [None, "model", "tokens", "transport"])
def test_guarded_official_voice_embedding_raw_receipt_accounting_and_recovery(
    voice_database, monkeypatch, failure
):
    scenario = world(voice_database, monkeypatch)
    payload = official_sdk_payload(voice_database.settings.embeddings_model)
    if failure == "model":
        payload.pop("model")
    elif failure == "tokens":
        payload["usage"] = {"prompt_tokens": True}
    calls, clients = synthetic_official_sdk(
        monkeypatch, payload, scenario=scenario, transport_failure=failure == "transport"
    )
    provider = OpenAIEmbeddingsProvider(
        model=voice_database.settings.embeddings_model,
        api_key=voice_database.settings.openai_api_key,
        base_url=voice_database.settings.openai_base_url,
        dimensions=2,
    )
    bundle = SimpleNamespace(llm=scenario.llm, embeddings=provider, vector_store=scenario.store)
    monkeypatch.setattr("app.agent_voice.service.resolve_providers", lambda _: bundle)
    monkeypatch.setattr("app.interactive_agent.vector_adapter.resolve_providers", lambda _: bundle)
    result = shared_turn(scenario.ledger, scenario.key, scenario.body)
    assert_scope(result, scenario, mode="vector" if failure is None else "lexical_store")
    scenario.ledger.tool_done("original-call", result)
    replay_ledger = ledger_for(voice_database)
    replay_ledger.id = scenario.ledger.id
    replay_ledger.organization_id, replay_ledger.user_id = (
        scenario.ledger.organization_id,
        scenario.ledger.user_id,
    )
    assert shared_turn(replay_ledger, scenario.key, scenario.body) == result
    assert len(calls) == len(clients) == 1
    assert clients[0]["max_retries"] == 0 and clients[0]["timeout"].read == 30
    scenario.ledger.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, scenario.ledger.id)
        assert row.status == "uncertain" and row.accounted_cost is None
        event = session.get(UsageEvent, uuid5(UUID(scenario.key), "voice-embedding:query"))
        admission = session.scalar(
            select(UsageEvent).where(UsageEvent.provider == "agent_turn_admission")
        )
        assert event.request_id == admission.request_id
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.feature == "rag_search")
            )
            == 1
        )
        if failure is None:
            assert event.estimated_cost == Decimal("0.00000120") and event.total_tokens == 12
            assert (
                event.cost_source == CostSource.STATIC_ESTIMATED and not event.cost_is_placeholder
            )
        else:
            assert event.cost_source == CostSource.UNAVAILABLE and event.cost_is_placeholder
            assert event.status == UsageStatus.FAILURE
