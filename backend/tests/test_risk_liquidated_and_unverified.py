"""L2: liquidated legacy losses count toward realized PnL. L3: unknown kill switch is not "off"."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Organization, Position, TradeProposal, User
from app.schemas.common import PositionStatus, RiskAction, TradeDirection
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.rules import RiskEvaluationContext, check_kill_switch


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    for model in (Organization, User, TradeProposal, Position):
        model.__table__.create(engine)  # type: ignore[attr-defined]
    with Session(engine) as db:
        yield db
    engine.dispose()


def _position(org: uuid.UUID, user: uuid.UUID, status: PositionStatus, pnl: str) -> Position:
    moment = datetime(2026, 11, 7, 12, tzinfo=UTC)
    return Position(
        organization_id=org,
        user_id=user,
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        size=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=Decimal("1"),
        realized_pnl=Decimal(pnl),
        status=status,
        opened_at=moment,
        closed_at=moment if status is not PositionStatus.OPEN else None,
    )


def test_liquidated_loss_counts_toward_realized_pnl(session) -> None:
    org, user = uuid.uuid4(), uuid.uuid4()
    session.add(Organization(id=org, name="l2"))
    session.add(User(id=user, email="l2@example.test", hashed_password="x"))
    session.flush()
    session.add_all(
        [
            _position(org, user, PositionStatus.CLOSED, "-10"),
            _position(org, user, PositionStatus.LIQUIDATED, "-90"),
            _position(org, user, PositionStatus.OPEN, "-999"),
        ]
    )
    session.flush()
    accounting = DailyRiskAccounting(session, risk_settings=None)  # type: ignore[arg-type]
    total = accounting._sum_realized_closed(
        organization_id=org,
        user_id=user,
        start_utc=datetime(2026, 11, 7, tzinfo=UTC),
        end_utc=datetime(2026, 11, 8, tzinfo=UTC),
    )
    assert total == Decimal("-100")


def test_unverified_kill_switch_warns_and_active_still_blocks() -> None:
    request = object()
    unverified = check_kill_switch(
        request,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        RiskEvaluationContext(kill_switch_unverified=True),
    )
    assert unverified is not None and unverified.action is RiskAction.WARN
    assert "could not be verified" in unverified.message
    active = check_kill_switch(
        request,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        RiskEvaluationContext(kill_switch_active=True, kill_switch_unverified=True),
    )
    assert active is not None and active.action is RiskAction.BLOCK
    assert check_kill_switch(request, object(), RiskEvaluationContext()) is None  # type: ignore[arg-type]


def test_agent_resolver_returns_none_without_session_or_org() -> None:
    from types import SimpleNamespace

    from app.agents.nodes import _resolve_kill_switch_active

    runtime = SimpleNamespace(
        settings=SimpleNamespace(global_kill_switch_active=False), session=None
    )
    assert _resolve_kill_switch_active(runtime, uuid.uuid4()) is None  # type: ignore[arg-type]
    assert _resolve_kill_switch_active(runtime, None) is None  # type: ignore[arg-type]
    runtime.settings.global_kill_switch_active = True
    assert _resolve_kill_switch_active(runtime, None) is True  # type: ignore[arg-type]
