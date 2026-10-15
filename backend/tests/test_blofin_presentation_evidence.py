"""Read-only account and Agent presentation from native-shaped fixture evidence."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.db.models import JournalTrade
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.service import InteractiveAgentService
from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeDirection
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.tenant import TenantContext
from app.services.dashboard.demo_performance import attach_demo_performance
from tests.support.postgres_persistence import requires_postgres
from tests.test_manual_blofin_demo import world as world
from tests.test_manual_demo_lifecycle_workflow import set_exit
from tests.test_manual_demo_reconciliation import _service, _submit
from tests.test_manual_demo_reconciliation import native as native

__all__ = ["native", "world"]
pytestmark = requires_postgres


def account_view(world, session, *, tenant=None, account_id=None):
    return attach_demo_performance(
        DashboardDemoAccount(
            status="ok",
            account_id=account_id or UUID(world[2].governed_blofin_demo_account_id),
            total_equity_usd=Decimal("999.9"),
            message="Fixture account",
        ),
        session=session,
        settings=world[2],
        tenant=tenant or world[1],
    )


def test_account_performance_requires_verified_exits_and_never_uses_balance_delta(native):
    world, _ = native
    _, first = _submit(native)
    with world[0]() as session:
        trade = session.get(JournalTrade, first.journal_trade_id)
        trade.status = JournalTradeStatus.CLOSED
        trade.gross_pnl = 100
        trade.net_pnl = 100
        session.commit()
        read = account_view(world, session)
        assert read.performance.status == "unavailable"
        assert read.performance.gross_pnl is None and read.performance.net_pnl is None
        assert read.performance.unresolved_trades == 1
        assert read.total_equity_usd == Decimal("999.9")


def test_account_performance_uses_exact_closed_lifecycle_and_retains_unknown_funding(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    with world[0]() as session:
        _service(world, session).reconcile(world[1], first.command_id)
        performance = account_view(world, session).performance
        assert performance.status == "partial" and performance.currency == "USDT"
        assert performance.gross_pnl is None
        assert performance.fees == Decimal("0.00497364") + Decimal("-0.00498")
        assert performance.funding is None and performance.net_pnl is None
        assert performance.verified_closed_trades == 1 and performance.manual_test_trades == 1
        assert performance.strategy_closed_trades is None and performance.unresolved_trades == 0
        assert "outside venue history" in performance.coverage


@pytest.mark.parametrize("scope", ["user", "tenant", "account", "unbound"])
def test_account_performance_is_exactly_scoped(native, scope):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    with world[0]() as session:
        _service(world, session).reconcile(world[1], first.command_id)
        tenant = TenantContext(
            uuid4() if scope == "user" else world[1].user_id,
            uuid4() if scope == "tenant" else world[1].organization_id,
            "fixture",
            world[1].membership_role,
        )
        data = account_view(
            world, session, tenant=tenant, account_id=uuid4() if scope == "account" else None
        )
        if scope == "unbound":
            data.account_id = None
            data = attach_demo_performance(data, session=session, settings=world[2], tenant=tenant)
        assert data.performance.status == "unavailable"
        assert data.performance.gross_pnl is None and data.performance.net_pnl is None
        assert data.performance.verified_closed_trades == 0


def test_simulator_and_other_venue_records_are_excluded_from_account_performance(native):
    world, _ = native
    _submit(native)
    with world[0]() as session:
        session.add(
            JournalTrade(
                organization_id=world[1].organization_id,
                user_id=world[1].user_id,
                account_id=UUID(world[2].governed_blofin_demo_account_id),
                source=JournalTradeSource.MANUAL,
                exchange="PAPER_INTERNAL",
                symbol="BTCUSDT",
                timeframe="1m",
                direction=TradeDirection.LONG,
                status=JournalTradeStatus.CLOSED,
                net_pnl=Decimal("9999"),
            )
        )
        session.commit()
        view = account_view(world, session)
        assert view.performance.net_pnl is None
        assert view.performance.unresolved_trades == 1
        assert view.performance.manual_test_trades == 1


@pytest.mark.parametrize("closed", [False, True])
def test_manual_agent_has_one_concise_reply_and_separate_exact_evidence(native, closed):
    world, _ = native
    _, first = _submit(native)
    if closed:
        set_exit(native, first)
    with world[0]() as session:
        if closed:
            _service(world, session).reconcile(world[1], first.command_id)
        response = InteractiveAgentService(session, settings=world[2]).handle_turn(
            AgentTurnRequest(message=f"Explain command {first.command_id}"),
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
        )
        assert len(response.reply) < 750
        assert "BloFin demo" in response.reply and "Entry filled:" in response.reply
        assert "Recorded facts" not in response.reply and "Why it was allowed" not in response.reply
        assert str(first.command_id) not in response.reply
        assert str(first.command_id) in response.recorded_evidence
        assert any(ref.record_id == str(first.command_id) for ref in response.connections)
        assert "0E-" not in response.reply
        if closed:
            assert (
                "Closure verified" in response.reply
                and "net PnL remain unverified" in response.reply
            )
            assert "Reported closing PnL: 0.0106" in response.reply
            assert "fee/funding basis unverified" in response.reply
            assert "Gross trading PnL:" not in response.reply
        else:
            assert "was flat at the snapshot" in response.reply
            assert "exit remains unverified" in response.reply
            assert "currently open position" not in response.reply
