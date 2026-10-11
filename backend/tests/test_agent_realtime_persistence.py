"""Disposable PostgreSQL evidence; provider outputs are synthetic, never live quality."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.grounding import read_grounding
from app.agent_voice.service import shared_turn
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AgentSavedEntry,
    ConversationMessage,
    Membership,
    Organization,
    OrganizationQuota,
    TradeJournal,
    UsageEvent,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.voice_realtime import VoiceSessionReservation
from app.providers.base import ProviderKind
from app.providers.llm import LLMCompletionResult
from app.schemas.common import (
    CostSource,
    MembershipRole,
    StrategyId,
    StrategyLifecycleState,
    TradeDirection,
)
from app.schemas.conversation import ConversationCreate
from app.schemas.strategy_library import StrategyCard
from app.security.tokens import create_access_token
from app.services.conversation_service import ConversationService
from app.services.strategy_versioning import StrategyVersioningService
from tests.test_agent_realtime_protocol import voice_limits  # noqa: F401


@pytest.fixture
def voice_database(request):
    receipt = request.getfixturevalue("voice_limits")
    url = os.environ.get("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("EXPERIMENT_TEST_POSTGRES_URL must identify disposable PostgreSQL")
    schema = "voice_evidence_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, poolclass=NullPool, connect_args={"options": f"-csearch_path={schema}"}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        provider_mode="mock",
        market_data_provider="mock",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    organization_id, user_id = uuid4(), uuid4()
    with sessions() as session:
        session.add_all(
            [
                Organization(id=organization_id, name="Synthetic voice owner"),
                User(
                    id=user_id,
                    email=f"{user_id}@example.invalid",
                    hashed_password="synthetic-unused",
                    email_verified=True,
                ),
            ]
        )
        session.flush()
        session.add(
            Membership(organization_id=organization_id, user_id=user_id, role=MembershipRole.OWNER)
        )
        session.add(OrganizationQuota(organization_id=organization_id))
        conversation = ConversationService(session).create(
            ConversationCreate(title="Synthetic realtime context"),
            organization_id=organization_id,
            user_id=user_id,
        )
        conversation_id = conversation.id
        session.commit()
    token, _ = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        email="voice@example.invalid",
        settings=settings,
    )
    limits = receipt.model_copy(update={"organization_id": str(organization_id)})
    fixture = SimpleNamespace(
        sessions=sessions,
        settings=settings,
        limits=limits,
        token=token,
        conversation_id=conversation_id,
        organization_id=organization_id,
        user_id=user_id,
        schema=schema,
    )
    yield fixture
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def ledger_for(database):
    return VoiceLedger(
        database.sessions,
        database.settings,
        database.limits,
        database.token,
        database.conversation_id,
    )


def test_budget_is_committed_before_transport_and_survives_new_session(voice_database):
    ledger = ledger_for(voice_database)
    ledger.reserve()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        assert row.status == "pending"
        assert row.reserved_cost == ledger.limits.reservation(ledger.settings)
        assert row.reserved_tokens > 0
        assert row.accounted_cost is None
        assert "access_token" not in json.dumps(row.ledger)


def test_session_cannot_outlive_approved_price_or_budget_receipt(voice_database):
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    ledger = ledger_for(voice_database)
    ledger.limits = ledger.limits.model_copy(update={"valid_until": deadline})
    ledger.reserve()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        assert datetime.fromisoformat(row.ledger["expires_at"]) == deadline


def test_competing_sessions_cannot_double_spend_existing_receipt(voice_database):
    barrier = Barrier(2)

    def reserve():
        ledger = ledger_for(voice_database)
        barrier.wait(timeout=3)
        try:
            ledger.reserve()
            return "reserved"
        except VoiceUnavailableError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: reserve(), range(2)))
    assert sorted(results) == ["reserved", "voice_approved_budget_exhausted"]
    with voice_database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(VoiceSessionReservation)) == 1


@pytest.mark.parametrize(
    "field,reason",
    [
        ("daily_request_limit", "daily_request"),
        ("limit_agent_chat", "agent_feature"),
        ("monthly_token_limit", "monthly_token"),
    ],
)
def test_existing_disabled_or_exhausted_quota_blocks_before_provider(voice_database, field, reason):
    with voice_database.sessions() as session:
        row = session.scalar(select(OrganizationQuota))
        setattr(row, field, 0)
        session.commit()
    with pytest.raises(VoiceUnavailableError, match=reason):
        ledger_for(voice_database).reserve()


def test_provider_reported_cost_debits_approved_balance(voice_database):
    with voice_database.sessions() as session:
        session.add(
            UsageEvent(
                organization_id=voice_database.organization_id,
                user_id=voice_database.user_id,
                feature="agent_chat",
                event_at=datetime.now(UTC),
                estimated_cost=Decimal(0),
                provider_reported_cost=Decimal("0.9"),
                cost_source=CostSource.PROVIDER_REPORTED,
            )
        )
        session.commit()
    with pytest.raises(VoiceUnavailableError, match="approved_budget"):
        ledger_for(voice_database).reserve()


def test_fresh_membership_and_private_conversation_are_revalidated(voice_database):
    ledger = ledger_for(voice_database)
    ledger.reserve()
    with voice_database.sessions() as session:
        session.scalar(select(Membership)).role = MembershipRole.VIEWER
        session.commit()
    with pytest.raises(VoiceUnavailableError, match="permission"):
        ledger.response()
    ledger.close()
    with voice_database.sessions() as session:
        assert session.get(VoiceSessionReservation, ledger.id).response_count == 0


def test_missing_usage_retains_reservation_and_duplicate_done_counts_once(voice_database):
    ledger = ledger_for(voice_database)
    ledger.reserve()
    ledger.activate()
    ledger.response()
    ledger.response_started("server-response")
    response = {"id": "server-response", "status": "completed", "usage": None}
    ledger.response_done(response)
    ledger.response_done(response)
    ledger.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        assert row.status == "uncertain" and row.accounted_cost is None
        assert row.reserved_cost == ledger.limits.reservation(ledger.settings)
        events = session.scalars(
            select(UsageEvent).where(UsageEvent.feature == "agent_voice_realtime")
        ).all()
        assert len(events) == 1 and events[0].cost_source == CostSource.UNAVAILABLE


def test_server_usage_is_durable_and_audio_without_reply_retains_full_hold(voice_database):
    ledger = ledger_for(voice_database)
    ledger.reserve()
    ledger.activate()
    ledger.audio(48000)
    ledger.response()
    ledger.response_started("verified-server")
    response = {
        "id": "verified-server",
        "status": "completed",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "input_token_details": {"audio_tokens": 40, "text_tokens": 60},
        },
    }
    ledger.response_done(response)
    ledger.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, ledger.id)
        event = session.scalar(
            select(UsageEvent).where(UsageEvent.feature == "agent_voice_realtime")
        )
        assert event.total_tokens == 150 and event.cost_source == CostSource.STATIC_ESTIMATED
        assert row.ledger["responses"][0]["usage"] == response["usage"]
        assert row.status == "uncertain" and row.accounted_cost is None


def test_server_audio_cap_is_checked_before_any_further_forwarding(voice_database):
    ledger = ledger_for(voice_database)
    ledger.reserve()
    ledger.activate()
    ledger.audio(48000 * ledger.limits.maximum_audio_seconds)
    with pytest.raises(VoiceUnavailableError, match="audio_duration"):
        ledger.audio(2)
    with voice_database.sessions() as session:
        assert (
            session.get(VoiceSessionReservation, ledger.id).ledger["audio_bytes"]
            == 48000 * ledger.limits.maximum_audio_seconds
        )


@pytest.mark.parametrize("mode", ["voice", "text"])
def test_shared_pipeline_reply_history_capture_and_restart_exact_recovery(
    voice_database, monkeypatch, mode
):
    calls = []
    spoken_note = "My earlier BTC risk rule may be outdated."

    class SyntheticLLM:
        name, kind = "synthetic-unmeasured", ProviderKind.LLM

        def complete(self, request):
            calls.append(request)
            if request.response_format:
                content = json.dumps(
                    {
                        "entries": [
                            {
                                "category": "rules",
                                "title": "Provisional BTC risk rule",
                                "summary": spoken_note,
                                "evidence_quotes": [spoken_note],
                                "tags": ["BTC"],
                                "confidence": 0.8,
                                "target_entry_id": None,
                                "draft": None,
                            }
                        ],
                        "clarification": None,
                    }
                )
            else:
                content = "My earlier rule is uncertain. Current native fees/PnL are unavailable."
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
    ledger = ledger_for(voice_database)
    ledger.reserve()
    ledger.activate()
    body = {
        "message": spoken_note,
        "conversation_id": str(voice_database.conversation_id),
    }
    key, _ = ledger.tool("server-call", body)
    if mode == "voice":

        def invoke():
            return shared_turn(ledger, key, body)
    else:
        from uuid import UUID

        from app.interactive_agent.contracts import AgentTurnRequest
        from app.interactive_agent.turn_runtime import run_interactive
        from app.services.turn_coordinator import TurnCoordinator

        coordinator = TurnCoordinator(voice_database.sessions)

        def invoke():
            return coordinator.run(
                channel="interactive",
                key=UUID(key),
                body=body,
                organization_id=voice_database.organization_id,
                user_id=voice_database.user_id,
                conversation_id=voice_database.conversation_id,
                work=lambda reservation: run_interactive(
                    coordinator,
                    voice_database.settings,
                    reservation,
                    AgentTurnRequest.model_validate(body),
                    llm_provider=SyntheticLLM(),
                    external_retrieval=False,
                ),
            )

    result = invoke()
    ledger.tool_done("server-call", result)
    count = len(calls)
    replay = invoke()
    assert replay == result and len(calls) == count
    assert result["conversation_id"] == body["conversation_id"]
    assert result["authority_mutated"] is False and result["execution_attempted"] is False
    assert result["reply"].startswith("My earlier rule is uncertain")
    assert "native_blofin_fills" in result["recorded_evidence"]
    assert result["capture_status"] == "saved"
    assert len(result["saved_entries"]) == 1
    assert result["saved_entries"][0]["summary"] == spoken_note
    with voice_database.sessions() as session:
        rows = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == voice_database.conversation_id
            )
        ).all()
        assert len([row for row in rows if row.role.value == "user"]) == 1
        assert len([row for row in rows if row.role.value == "assistant"]) == 1
        saved = session.scalars(select(AgentSavedEntry)).all()
        assert len(saved) == 1 and saved[0].summary == spoken_note
        assert saved[0].original_text == spoken_note and saved[0].category == "rules"
        assert saved[0].user_id == voice_database.user_id
        assert saved[0].organization_id == voice_database.organization_id
        pending = session.get(VoiceSessionReservation, ledger.id).ledger["tools"]["server-call"]
        assert pending["body"] == body and pending["key"] == key


def test_saved_grounding_preserves_uncertainty_and_owner_isolation(voice_database):
    with voice_database.sessions() as session:
        other = User(email=f"{uuid4()}@example.invalid", hashed_password="synthetic-unused")
        session.add(other)
        session.flush()
        for user_id, summary in [
            (voice_database.user_id, "My BTC rule may be outdated"),
            (other.id, "PRIVATE NEVER EXPOSED BTC rule"),
        ]:
            session.add(
                AgentSavedEntry(
                    organization_id=voice_database.organization_id,
                    user_id=user_id,
                    conversation_id=voice_database.conversation_id,
                    category="rules",
                    title="BTC rule",
                    summary=summary,
                    original_text=summary,
                    sources={"message_ids": []},
                )
            )
        session.commit()
    with voice_database.sessions() as session:
        facts = read_grounding(
            session,
            voice_database.settings,
            voice_database.organization_id,
            voice_database.user_id,
            "my BTC rules",
        )
    assert "may be outdated" in facts
    assert "PRIVATE NEVER EXPOSED" not in facts
    assert '"availability": "unavailable"' in facts
    assert "Incomplete coverage cannot establish portfolio returns" in facts


def test_grounding_reads_selected_strategy_rules_and_journal_without_foreign_or_invalid_card(
    voice_database,
):
    with voice_database.sessions() as session:
        other = User(email=f"{uuid4()}@example.invalid", hashed_password="synthetic-unused")
        session.add(other)
        session.flush()
        for actor, name in [
            (voice_database.user_id, "Owned saved strategy"),
            (other.id, "PRIVATE NEVER EXPOSED"),
        ]:
            strategy = UserStrategy(
                organization_id=voice_database.organization_id,
                user_id=actor,
                name=name,
                setup_type=StrategyId.SFP,
                current_version=1,
            )
            session.add(strategy)
            session.flush()
            card = StrategyCard(
                strategy_name=name,
                entry_conditions=["Observed prior sweep"],
                invalidation=["Level no longer valid"],
                stop_loss=["Beyond invalidation"],
                no_trade_rules=["Do not act without confirmation"],
            )
            selected = UserStrategyVersion(
                strategy_id=strategy.id,
                version=1,
                card=card.model_dump(mode="json"),
                structured_rules={"rule": "Saved bounded risk rule"},
            )
            session.add(selected)
            session.flush()
            StrategyVersioningService(session).append_lifecycle(
                organization_id=voice_database.organization_id,
                strategy_id=strategy.id,
                strategy_version_id=selected.id,
                new_state=StrategyLifecycleState.DRAFT,
                actor_user_id=actor,
                reason="synthetic owned draft",
            )
            session.add(
                UserStrategyVersion(
                    strategy_id=strategy.id,
                    version=2,
                    card={"invalid": "Never presented as approved card"},
                )
            )
            session.add(
                TradeJournal(
                    organization_id=voice_database.organization_id,
                    user_id=actor,
                    symbol="BTCUSDT",
                    timeframe="1h",
                    direction=TradeDirection.LONG,
                    entry_rationale=name,
                    lessons="My interpretation may be wrong",
                    improvement_rule="Keep this provisional",
                )
            )
        corrupt = UserStrategy(
            organization_id=voice_database.organization_id,
            user_id=voice_database.user_id,
            name="Unavailable card",
            setup_type=StrategyId.SFP,
        )
        session.add(corrupt)
        session.flush()
        session.add(UserStrategyVersion(strategy_id=corrupt.id, version=1, card={"invalid": True}))
        session.commit()
    with voice_database.sessions() as session:
        facts = read_grounding(
            session,
            voice_database.settings,
            voice_database.organization_id,
            voice_database.user_id,
            "my saved strategies and lessons",
        )
    payload = json.loads(facts.split("\n", 1)[1])
    assert "PRIVATE NEVER EXPOSED" not in facts
    assert "Never presented as approved card" not in facts
    owned = next(
        row
        for row in payload["selected_strategy_versions_and_rules"]
        if row["name"] == "Owned saved strategy"
    )
    assert owned["selected_version"] == 1 and owned["lifecycle"] == "draft"
    assert owned["no_trade_rules"] == ["Do not act without confirmation"]
    assert "Saved bounded risk rule" in owned["structured_rules_excerpt"]
    assert owned["execution_active"] == "not established by this read"
    unavailable = next(
        row
        for row in payload["selected_strategy_versions_and_rules"]
        if row["name"] == "Unavailable card"
    )
    assert unavailable["card_availability"].startswith("unavailable")
    assert unavailable["entry_conditions"] is None
    assert payload["saved_journal"][0]["lessons"] == "My interpretation may be wrong"
    assert payload["read_at"] and owned["version_recorded_at"]


def test_grounding_unicode_overflow_keeps_valid_bounded_json_and_limitations(voice_database):
    with voice_database.sessions() as session:
        for number in range(3):
            session.add(
                AgentSavedEntry(
                    organization_id=voice_database.organization_id,
                    user_id=voice_database.user_id,
                    conversation_id=voice_database.conversation_id,
                    category="rules",
                    title="BTC rule " + str(number),
                    summary="BTC " + "界" * 10000,
                    original_text="BTC " + "界" * 10000,
                    sources={"message_ids": []},
                )
            )
        session.commit()
    with voice_database.sessions() as session:
        facts = read_grounding(
            session,
            voice_database.settings,
            voice_database.organization_id,
            voice_database.user_id,
            "BTC rules",
        )
    assert len(facts.encode()) <= 6000
    payload = json.loads(facts.split("\n", 1)[1])
    assert payload["native_blofin_fills"]["availability"] == "unavailable"
    assert payload["read_at"] and payload["limitations"]
    assert "[excerpt; remainder unavailable]" in facts
