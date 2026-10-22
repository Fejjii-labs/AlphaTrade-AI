"""Unresolved voice reservations fence ordinary model quotas after restart."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent_voice.config import MinuteRealtimePrice, VoiceLimits, minute_session_cost
from app.core.config import Settings
from app.db.voice_realtime import VoiceSessionReservation
from app.schemas.usage import OrganizationQuotaUpdate
from app.services.quota_service import QuotaService
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401


def reserve(world, *, status, accounted=None, age=0):
    now = datetime.now(UTC) - timedelta(days=age)
    row = VoiceSessionReservation(
        id=uuid4(),
        organization_id=world.tenant.organization_id,
        user_id=world.tenant.user_id,
        conversation_id=None,
        budget_reference="synthetic-verified-budget",
        model="synthetic-protocol-model",
        status=status,
        reserved_cost=Decimal("1"),
        reserved_tokens=100,
        accounted_cost=accounted,
        response_count=1,
        ledger={"responses": [{"state": "unresolved"}]},
        created_at=now,
        updated_at=now,
    )
    world.session.add(row)
    return row


@pytest.mark.parametrize("status", ["pending", "active", "uncertain", "closed"])
def test_ordinary_request_is_blocked_by_unresolved_reservation_after_fresh_session(
    experiment_engine,
    status,
):
    with Session(experiment_engine) as session:
        world = World(session)
        org = world.tenant.organization_id
        QuotaService(session).update_quota(
            org,
            OrganizationQuotaUpdate(
                monthly_token_limit=100,
                monthly_cost_limit=Decimal("1"),
            ),
        )
        reserve(world, status=status)
        session.commit()
    with Session(experiment_engine) as fresh:
        quota = QuotaService(fresh)
        result = quota.check_feature(org, "agent_chat")
        assert result.hard_blocked and not result.allowed
        usage = quota.get_status(org).usage
        assert usage.monthly_tokens_used == 0 and usage.monthly_cost_used == 0
        assert usage.monthly_tokens_reserved == 100
        assert usage.monthly_cost_reserved == Decimal("1")


def test_uncertain_reservation_survives_month_boundary_and_does_not_affect_other_tenant(
    experiment_engine,
):
    with Session(experiment_engine) as session:
        world, other = World(session), World(session)
        org, other_org = world.tenant.organization_id, other.tenant.organization_id
        reserve(world, status="uncertain", age=40)
        session.commit()
    with Session(experiment_engine) as fresh:
        own = QuotaService(fresh).get_status(org).usage
        other = QuotaService(fresh).get_status(other_org).usage
        assert own.monthly_tokens_reserved == 100 and own.monthly_cost_reserved == 1
        assert other.monthly_tokens_reserved == 0 and other.monthly_cost_reserved == 0


def test_partial_accounting_cannot_release_an_active_reservation(experiment_engine):
    with Session(experiment_engine) as session:
        world = World(session)
        org = world.tenant.organization_id
        reserve(world, status="active", accounted=Decimal("0.25"))
        session.commit()
    with Session(experiment_engine) as fresh:
        usage = QuotaService(fresh).get_status(org).usage
        assert usage.monthly_cost_reserved == 1 and usage.monthly_tokens_reserved == 100


def minute_limits(*, seconds: int) -> VoiceLimits:
    now = datetime.now(UTC)
    receipt = {
        "model": Settings(_env_file=None).agent_reasoning_model,
        "source": "https://example.invalid/fixture-only",
        "valid_until": now + timedelta(hours=1),
        "maximum_input_tokens": 20000,
        "input_usd_per_million": "1",
        "output_usd_per_million": "2",
    }
    return VoiceLimits.model_validate(
        {
            "organization_id": str(uuid4()),
            "budget_reference": "synthetic-minute-budget",
            "budget_as_of": now - timedelta(seconds=1),
            "valid_until": now + timedelta(hours=1),
            "remaining_budget_usd": "1",
            "session_ceiling_usd": "1",
            "maximum_seconds": seconds,
            "maximum_turns": 2,
            "maximum_responses": 4,
            "realtime": {
                "model": "grok-voice-3.0",
                "source": "https://docs.x.ai/developers/models/speech-to-speech",
                "valid_until": now + timedelta(hours=1),
                "usd_per_minute": "0.08",
                "usd_per_text_input": "0.004",
            },
            "reasoning": receipt,
        }
    )


def test_minute_meter_rounds_partial_minutes_up_and_prices_text_inputs():
    twelve = minute_limits(seconds=12 * 60)
    price = twelve.realtime
    assert isinstance(price, MinuteRealtimePrice)
    assert minute_session_cost(price, 12 * 60, 0) == Decimal("0.96")
    assert minute_session_cost(price, 13 * 60, 0) == Decimal("1.04")
    # 12 minutes + 1 second rounds up to 13 billable minutes.
    assert minute_session_cost(price, 12 * 60 + 1, 0) == Decimal("1.04")
    assert minute_session_cost(price, 12 * 60, 2) == Decimal("0.968")


def test_twelve_minute_xai_session_reserves_tool_cost_and_exceeds_one_dollar():
    """Default reasoning limits make 12 minutes refuse the $1 ceiling.

    The receipt prices 20,000 input tokens at $1/M and output at $2/M, with two
    turns and four attempts each. ``agent_max_output_tokens`` defaults to 25000,
    so the tool hold is $0.56 and 12 minutes ($0.96) total $1.52.

    With ``AGENT_MAX_OUTPUT_TOKENS=4000`` the tool hold is $0.224. Nine minutes
    cost $0.72 and the total is $0.944. The next second bills a tenth minute
    ($1.024). The largest ``maximum_seconds`` that fits $1 is 540.
    Twelve minutes at 4000 output tokens is still $1.184 and stays refused.
    """
    settings = Settings(_env_file=None)
    assert settings.agent_max_output_tokens == 25000
    twelve = minute_limits(seconds=12 * 60)
    assert twelve.reservation(settings) == Decimal("1.52")
    assert twelve.reservation(settings) > twelve.session_ceiling_usd
    # Lowering the clock admits. 300s is five minutes ($0.40) plus the $0.56 tool hold.
    admitted = minute_limits(seconds=300)
    assert admitted.reservation(settings) == Decimal("0.96")
    assert admitted.reservation(settings) <= admitted.session_ceiling_usd
    assert minute_limits(seconds=301).reservation(settings) > Decimal("1")

    capped = settings.model_copy(update={"agent_max_output_tokens": 4000})
    assert twelve.reservation(capped) == Decimal("1.184")
    assert twelve.reservation(capped) > twelve.session_ceiling_usd
    fitted = minute_limits(seconds=540)
    assert fitted.reservation(capped) == Decimal("0.944")
    assert fitted.reservation(capped) <= fitted.session_ceiling_usd
    assert minute_limits(seconds=541).reservation(capped) == Decimal("1.024")
    payload = admitted.model_dump(mode="json")
    payload["embedding"] = {
        "model": "text-embedding-3-small",
        "source": "https://example.invalid/fixture-only",
        "valid_until": payload["valid_until"],
        "maximum_input_tokens": 1000,
        "input_usd_per_million": "1",
    }
    # Two turns times 1,000 tokens at $1/M is $0.002 on top of the $0.96 hold.
    assert VoiceLimits.model_validate(payload).reservation(settings) == Decimal("0.962")


def test_unknown_minute_hold_stays_reserved_and_settled_minutes_do_not(experiment_engine):
    with Session(experiment_engine) as session:
        unknown, settled = World(session), World(session)
        unknown_org = unknown.tenant.organization_id
        settled_org = settled.tenant.organization_id
        for world, status, reserved, accounted in (
            (unknown, "uncertain", Decimal("0.96"), None),
            (settled, "closed", Decimal("0"), Decimal("0.08")),
        ):
            QuotaService(session).update_quota(
                world.tenant.organization_id,
                OrganizationQuotaUpdate(
                    monthly_token_limit=100,
                    monthly_cost_limit=Decimal("1"),
                ),
            )
            row = reserve(world, status=status, accounted=accounted)
            row.reserved_cost = reserved
        session.commit()
    with Session(experiment_engine) as fresh:
        unknown_usage = QuotaService(fresh).get_status(unknown_org).usage
        settled_usage = QuotaService(fresh).get_status(settled_org).usage
        assert unknown_usage.monthly_cost_reserved == Decimal("0.96")
        assert settled_usage.monthly_cost_reserved == 0
        assert unknown_usage.monthly_cost_used == 0
        assert settled_usage.monthly_cost_used == 0


def test_verified_closed_reservation_releases_hold_without_claiming_measured_usage(
    experiment_engine,
):
    with Session(experiment_engine) as session:
        world = World(session)
        org = world.tenant.organization_id
        reserve(world, status="closed", accounted=Decimal("0.25"))
        session.commit()
    with Session(experiment_engine) as fresh:
        usage = QuotaService(fresh).get_status(org).usage
        assert usage.monthly_cost_reserved == 0 and usage.monthly_tokens_reserved == 0
        # Actual provider usage is independently written by the server ledger,
        # never inferred from a client or from releasing a promise.
        assert usage.monthly_cost_used == 0 and usage.monthly_tokens_used == 0
