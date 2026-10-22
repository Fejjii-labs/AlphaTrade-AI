"""xAI Grok Voice selection, per-minute settlement, and absent realtime events.

No live socket, credential, or provider call. Fixture keys are not secrets.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from openai.types.realtime.session_update_event_param import SessionUpdateEventParam
from pydantic import TypeAdapter
from sqlalchemy import select

from app.agent_voice.config import (
    GROK_VOICE_MODEL,
    XAI_REALTIME_ORIGIN,
    VoiceUnavailableError,
    load_limits,
    realtime_endpoint,
)
from app.agent_voice.protocol import (
    billable_text_input,
    provider_event_optional,
    session_update,
)
from app.core.config import Settings
from app.db.models import UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from tests.test_agent_realtime_persistence import ledger_for
from tests.test_agent_realtime_persistence import voice_database as voice_database
from tests.test_agent_realtime_protocol import session_for
from tests.test_agent_realtime_protocol import voice_limits as voice_limits
from tests.test_voice_reservations_quota import minute_limits


def test_openai_stays_the_default_and_both_origins_are_exact():
    assert Settings.model_fields["agent_realtime_provider"].default == "openai"
    assert Settings.model_fields["xai_api_key"].default == ""
    assert Settings.model_fields["xai_realtime_base_url"].default == XAI_REALTIME_ORIGIN
    assert Settings.model_fields["xai_api_key"].repr is False
    settings = Settings(_env_file=None).model_copy(
        update={
            "agent_realtime_provider": "xai",
            "xai_api_key": "fixture-xai-key",
            "xai_realtime_base_url": "https://api.x.ai/v1/",
        }
    )
    provider, key, origin = realtime_endpoint(settings)
    assert provider == "xai" and origin == XAI_REALTIME_ORIGIN and key == "fixture-xai-key"
    openai = Settings(_env_file=None)
    assert realtime_endpoint(openai)[0] == "openai"
    assert realtime_endpoint(openai)[2] == "https://api.openai.com/v1"
    for update, reason in (
        ({"xai_realtime_base_url": "https://api.x.ai.evil/v1"}, "approved_xai_origin"),
        ({"xai_realtime_base_url": "wss://api.x.ai/v1"}, "approved_xai_origin"),
        ({"xai_realtime_base_url": "https://api.x.ai/v1/realtime"}, "approved_xai_origin"),
        (
            {"agent_realtime_provider": "openai", "openai_base_url": "https://example.invalid/v1"},
            "approved_openai_origin",
        ),
    ):
        with pytest.raises(VoiceUnavailableError, match=reason):
            realtime_endpoint(settings.model_copy(update=update))


def test_xai_limits_accept_twelve_minutes_and_refuse_a_bad_origin_or_model(tmp_path):
    twelve = minute_limits(seconds=12 * 60)
    thirteen = minute_limits(seconds=13 * 60)
    path = tmp_path / "limits.json"
    path.write_text(twelve.model_dump_json())
    settings = Settings(_env_file=None).model_copy(
        update={
            "agent_realtime_enabled": True,
            "agent_realtime_provider": "xai",
            "xai_api_key": "fixture-xai-key",
            "xai_realtime_base_url": XAI_REALTIME_ORIGIN,
            "provider_mode": "live",
            "agent_realtime_limits_file": str(path),
        }
    )
    loaded = load_limits(settings)
    assert loaded.realtime.model == GROK_VOICE_MODEL
    assert loaded.reservation(settings) == Decimal("0.96")
    path.write_text(thirteen.model_dump_json())
    with pytest.raises(VoiceUnavailableError, match="verified_cap_cannot_cover_session"):
        load_limits(settings)
    path.write_text(twelve.model_dump_json())
    with pytest.raises(VoiceUnavailableError, match="approved_xai_origin"):
        load_limits(
            settings.model_copy(update={"xai_realtime_base_url": "https://realtime.x.ai/v1"})
        )
    token = json.loads(twelve.model_dump_json())
    token["realtime"] = {
        "model": "gpt-realtime-2.1",
        "source": "https://example.invalid/fixture-only",
        "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "maximum_input_tokens": 1000,
        "input_usd_per_million": "1",
        "output_usd_per_million": "1",
        "audio_input_usd_per_million": "1",
        "audio_output_usd_per_million": "1",
        "maximum_audio_input_tokens_per_second": "1",
    }
    path.write_text(json.dumps(token))
    with pytest.raises(VoiceUnavailableError, match="verified_realtime_route_mismatch"):
        load_limits(settings)
    with pytest.raises(VoiceUnavailableError, match="provider_credential_unbound"):
        load_limits(settings.model_copy(update={"xai_api_key": ""}))


def test_openai_session_update_is_unchanged_and_xai_omits_unsupported_events():
    limits = minute_limits(seconds=180)
    openai = session_update(limits)
    TypeAdapter(SessionUpdateEventParam).validate_python(openai)
    assert openai["session"]["audio"]["output"]["voice"] == "marin"
    assert "voice" not in openai["session"]
    assert openai["type"] != "conversation.item.retrieve"
    xai = session_update(limits, provider="xai")
    assert xai["session"]["voice"] == "eve"
    assert xai["session"]["model"] == GROK_VOICE_MODEL
    assert xai["session"]["reasoning"] == {"effort": "none"}
    assert provider_event_optional("rate_limits.updated")
    assert provider_event_optional("conversation.item.retrieve")
    assert provider_event_optional("conversation.item.retrieved")
    assert not provider_event_optional("session.updated")
    assert billable_text_input({"type": "function_call_output"}) is False
    assert billable_text_input({"type": "message"}) is True
    assert billable_text_input({"type": "mystery"}) is None


@pytest.mark.asyncio
async def test_xai_session_becomes_ready_without_rate_limits_or_item_retrieve():
    limits = minute_limits(seconds=180)
    session, ledger, sent, upstream = session_for(limits)
    session.ready = False
    ledger.settings = ledger.settings.model_copy(update={"agent_realtime_provider": "xai"})
    effective = session_update(limits, provider="xai")["session"]
    # Grok Voice may omit these OpenAI echoes. Readiness must not wait on them.
    effective.pop("model")
    effective.pop("max_output_tokens")
    effective.pop("truncation")
    effective["audio"]["input"]["turn_detection"].pop("create_response")
    effective["audio"]["input"]["turn_detection"].pop("interrupt_response")
    await session.provider({"type": "session.updated", "session": effective})
    await session.provider({"type": "rate_limits.updated", "rate_limits": []})
    await session.provider({"type": "conversation.item.retrieved", "item": {"id": "absent"}})
    assert session.ready and ledger.events == ["active"]
    assert sent == [{"type": "session.ready", "session_id": str(ledger.id)}]
    with pytest.raises(VoiceUnavailableError, match="unsupported_client_event"):
        await session._provider_send({"type": "conversation.item.retrieve", "item_id": "item"})
    assert upstream == []
    effective["audio"]["input"]["turn_detection"]["create_response"] = True
    session.ready = False
    with pytest.raises(VoiceUnavailableError, match="audio_limits_not_confirmed"):
        await session.provider({"type": "session.updated", "session": effective})


def test_known_minutes_settle_and_unknown_text_is_not_refunded(voice_database):
    voice_database.limits = minute_limits(seconds=12 * 60).model_copy(
        update={"organization_id": str(voice_database.organization_id)}
    )
    voice_database.settings = voice_database.settings.model_copy(
        update={
            "agent_realtime_provider": "xai",
            "xai_api_key": "fixture-xai-key",
            "xai_realtime_base_url": XAI_REALTIME_ORIGIN,
        }
    )
    known = ledger_for(voice_database)
    known.reserve()
    known.mark_upstream_open()
    known.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, known.id)
        assert row is not None
        assert row.status == "closed"
        assert row.accounted_cost == Decimal("0.08")
        assert row.reserved_cost == Decimal("0")
        assert row.ledger["settled_minutes"] == 1
        assert Decimal(row.ledger["opening_reserved_cost"]) == Decimal("0.96")
        event = session.scalar(
            select(UsageEvent).where(UsageEvent.feature == "agent_voice_realtime")
        )
        assert event is not None
        assert event.provider == "xai"
        assert event.model == GROK_VOICE_MODEL
        assert event.estimated_cost == Decimal("0.08")
        assert event.cost_is_placeholder is False
        assert event.total_tokens == 0
    shorter = minute_limits(seconds=60).model_copy(
        update={"organization_id": str(voice_database.organization_id)}
    )
    voice_database.limits = shorter
    follow = ledger_for(voice_database)
    follow.reserve()
    voice_database.limits = minute_limits(seconds=12 * 60).model_copy(
        update={"organization_id": str(voice_database.organization_id)}
    )
    with pytest.raises(VoiceUnavailableError, match="approved_budget"):
        ledger_for(voice_database).reserve()

    # The follow-up hold is on the first budget. Unknown usage uses a fresh one.
    voice_database.limits = voice_database.limits.model_copy(
        update={
            "budget_reference": "synthetic-unknown-minute-budget",
            # The known session's usage event is an organization debit. This
            # separate receipt still has room for its own twelve-minute hold.
            "remaining_budget_usd": Decimal("2"),
        }
    )
    unknown = ledger_for(voice_database)
    unknown.reserve()
    unknown.mark_upstream_open()

    def forget_text(_row: VoiceSessionReservation, data: dict[str, object]) -> None:
        data["text_inputs"] = None

    unknown.update(forget_text)
    unknown.close()
    with voice_database.sessions() as session:
        row = session.get(VoiceSessionReservation, unknown.id)
        assert row is not None
        assert row.status == "uncertain"
        assert row.accounted_cost is None
        assert row.reserved_cost == Decimal("0.96")
        events = session.scalars(
            select(UsageEvent).where(UsageEvent.request_id == str(unknown.id))
        ).all()
        assert events == []
