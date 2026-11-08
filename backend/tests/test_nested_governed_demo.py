"""Approved provisional Nested long/short use actual demo fill/exit authorities."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    ExecutionAccount,
    GovernedDemoLifecycleResolution,
    JournalTrade,
    Organization,
    User,
)
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.common import JournalTradeStatus, TradeDirection
from app.schemas.nested_continuation import NestedContinuationSpec
from app.services import demo_leverage_config
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from app.signal_fusion.memory import FrozenClock
from tests import test_strategy_brain_nested as nested
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_governed_blofin_demo import demo_settings, seed_band_ledger_headroom
from tests.test_governed_demo_lifecycle import ClosingVenue
from tests.test_watcher_paper_runtime import ORG, USER

pytestmark = requires_postgres


@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
def test_approved_nested_produces_protected_actual_demo_entry_and_verified_exit(
    monkeypatch, direction
):
    factory = phase7_plan_session_factory()
    with factory() as session:
        session.add_all(
            [
                Organization(id=ORG, name="Nested demo"),
                User(id=USER, email="nested-demo@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        original_approved, original_bars = nested.approved, nested.bars

        def approve_requested(db, org, user, spec=None):
            return original_approved(
                db, org, user, spec or NestedContinuationSpec(symbol="BTCUSDT", direction=direction)
            )

        def requested_bars(*args, **kwargs):
            return original_bars(*args, **{**kwargs, "bearish": direction is TradeDirection.SHORT})

        monkeypatch.setattr(nested, "approved", approve_requested)
        monkeypatch.setattr(nested, "bars", requested_bars)
        runtime, _held, template, now = nested.nested_runtime_world(
            (session, ORG, USER), monkeypatch
        )
        account = session.scalar(select(ExecutionAccount))
        settings = demo_settings(str(account.id))
        # demo_settings() arms autonomous BloFin demo mode (M1 requires it for
        # worker entries); that path refuses without a verifiable risk ledger.
        seed_band_ledger_headroom(session, account.id)
        session.commit()
        # The Nested fixture stop is wide (~10%); 10x would correctly refuse with
        # demo_liquidation_inside_stop_buffer, so the owner configures 2x isolated.
        monkeypatch.setattr(
            demo_leverage_config,
            "load_demo_leverage_config",
            lambda *_a, **_k: demo_leverage_config.DemoLeverageConfig(2, "isolated", "account"),
        )
        price = Decimal(186 if direction is TradeDirection.SHORT else 114)
        venue = ClosingVenue(price, category="tp", quote_ts=str(int(now.timestamp() * 1000)))
        client = BloFinClient(
            base_url=settings.blofin_demo_rest_base_url,
            api_key="simulated-key",
            api_secret="simulated-secret",
            api_passphrase="simulated-pass",
            transport=httpx.MockTransport(venue.handle),
            sleeper=lambda _: None,
        )
        provider = GovernedBloFinDemoProvider(client, clock=lambda: now + timedelta(seconds=2))
        monkeypatch.setattr(GovernedBloFinDemoLoop, "_get_provider", lambda _: provider)
        runtime._settings = settings
        report = runtime.run_cycle()
        assert report.scans and venue.post_count == 1, report
        assert venue.account_writes[-1][1]["leverage"] == "2"
        assert venue.order["marginMode"] == "isolated"
        assert venue.order["side"] == ("sell" if direction is TradeDirection.SHORT else "buy")
        assert venue.order["slOrderPrice"] == venue.order["tpOrderPrice"] == "-1"
        assert session.scalar(select(JournalTrade)).strategy_version_id == template["version_id"]
        venue.exited = True
        loop = GovernedBloFinDemoLoop(
            runtime._canonical_runtime,
            settings,
            FrozenClock(now + timedelta(seconds=2)),
            provider=provider,
        )
        assert loop.reconcile_pending(session) == ("demo_closed_reconciled",)
        session.expire_all()
        trade = session.scalar(select(JournalTrade))
        assert trade.status is JournalTradeStatus.CLOSED and trade.direction is direction
        assert trade.fees == Decimal("0.05") and trade.net_pnl is None
        assert session.scalar(select(LearningAttributionRecordRow)).closed
        assert session.scalar(select(func.count()).select_from(ExecutionAccount)) == 1
        assert session.scalar(select(GovernedDemoLifecycleResolution)) is not None
