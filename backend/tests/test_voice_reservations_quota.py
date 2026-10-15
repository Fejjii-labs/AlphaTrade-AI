"""Unresolved voice reservations fence ordinary model quotas after restart."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

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
